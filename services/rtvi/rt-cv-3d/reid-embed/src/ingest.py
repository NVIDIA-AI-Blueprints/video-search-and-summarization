# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Broker ingestion subsystem (Kafka or Redis Streams).

Architecture
------------
The consumer lives in a dedicated subprocess (started under the
default ``fork`` start method on Linux). The subprocess instantiates
:class:`KafkaIngestor`, which owns everything it needs:

* Its own per-subprocess Milvus connection (gRPC channels aren't fork-safe).
* The accumulating batch buffer + lock + last-flush timestamp.
* (Future / step 3) the background writer thread pool + periodic flush thread.

Dependencies are injected via the constructor as frozen dataclasses or
shared multiprocessing primitives,

Public surface
--------------
``main.py`` only touches a handful of things from this module:

* :class:`KafkaConfig`, :class:`RedisConfig`, :class:`MilvusConfig` -- frozen configs.
* :func:`create_kafka_consumer` -- parent-side factory that spawns the subprocess.
* :func:`insert_embedding` -- tiny stateless helper used by the HTTP ``/insert`` route.

Broker selection
----------------
``MESSAGE_BROKER`` picks the read loop: ``kafka`` (default) consumes with
``KafkaConsumer``, ``redis`` consumes a Redis Stream with ``XREADGROUP``.
The split is deliberately a single branch in :meth:`KafkaIngestor.run`
rather than a transport abstraction -- both loops hand raw payload bytes
to :meth:`KafkaIngestor._process_message` and everything downstream
(parsing, batching, the writer pool, the tracking store) is shared
verbatim.

Async logging
-------------
We rely on ``fork`` inheritance: under fork the subprocess inherits the
root logger configuration set up in ``main.py`` (a ``QueueHandler`` ->
shared ``_log_queue`` when ``LOG_ASYNC=true``, or a plain
``StreamHandler`` otherwise). The subprocess just rebinds its
module-level ``logger`` to a process-distinguishing name. If the
codebase ever switches the start method to ``spawn``, this needs to be
revisited -- the child gets a fresh interpreter and would have to set up
logging from scratch (along with explicitly receiving the log queue).
"""

from __future__ import annotations

import cProfile  # noqa: F401 -- referenced by the commented-out profiler block in run()
import json
import logging
import multiprocessing
import queue
import threading
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, List, Optional

import numpy as np
from kafka import KafkaConsumer
from pymilvus import Collection, connections

# Optional: only needed when MESSAGE_BROKER=redis. Guarded so a Kafka-only
# deployment with an older venv keeps importing this module cleanly.
try:
    import redis as redis_lib
except ImportError:  # pragma: no cover - exercised only without the dep
    redis_lib = None

import proto_utils.ds_schema_pb2 as schema_pb2
import google.protobuf.json_format as json_format

from config import (
    DEEPSTREAM_SCHEMA,
    DIMENSION,
    ENABLE_SECONDARY_EMBEDDING,
    MESSAGE_BROKER,
    INSERT_BATCH_SIZE,
    INSERT_BATCH_TIMEOUT_SEC,
    INSERT_EMBEDDINGS_IN_BATCHES,
    INSERT_QUEUE_MAX,
    INSERT_QUEUE_PUT_TIMEOUT_SEC,
    INSERT_WORKER_COUNT,
    KAFKA_CONSUMER_CONNECT_RETRY_SEC,
    PROFILE_LOG_INTERVAL_SEC,
    REDIS_CONSUMER_BATCH_COUNT,
    REDIS_CONSUMER_BLOCK_MS,
    REDIS_CONSUMER_CONNECT_RETRY_SEC,
    REDIS_CONSUMER_LAG_LOG_INTERVAL_SEC,
    TRACKING_DB_STORE_ALL_ENTRIES,
)
from profiling import RateProfiler
from tracking_store import TrackedObject, TrackingStore


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KafkaConfig:
    server: str
    topic: str
    group_id: str


@dataclass(frozen=True)
class RedisConfig:
    """Connection + consumer-group settings for the Redis Streams backend.

    ``stream`` and ``payload_key`` must match DeepStream's ``cfg_redis.txt``
    (its ``topic`` property and ``payloadkey`` respectively); ``group`` is
    owned entirely by this service.

    ``stream`` is populated from ``REDIS_CONSUMER_TOPIC`` -- the env var is
    named for symmetry with ``KAFKA_CONSUMER_TOPIC`` and with DeepStream's
    ``topic`` property, while the field keeps Redis's own word because it is
    handed straight to redis-py calls that spell it ``streams=``.
    """

    host: str
    port: int
    stream: str
    group: str
    consumer_name: str
    payload_key: str
    start_id: str
    password: Optional[str] = None


@dataclass(frozen=True)
class MilvusConfig:
    host: str
    port: int
    collection_name: str


# ---------------------------------------------------------------------------
# Stateless insert helper (shared by HTTP /insert path and KafkaIngestor)
# ---------------------------------------------------------------------------


def insert_embedding(
    coll: Collection,
    vector,
    object_id: str,
    sensor_id: str = "Unknown",
    frame_id: int = -1,
) -> bool:
    """Insert a single embedding row into ``coll``.

    Returns ``True`` on success, ``False`` if ``len(vector) != DIMENSION``.
    The caller is responsible for any cross-process signaling (e.g.
    ``_new_data_event.set()``); we keep this helper pure so it can be
    reused from the HTTP route, the :class:`KafkaIngestor` non-batched
    path, and future call sites.
    """
    if len(vector) != DIMENSION:
        return False
    coll.insert(
        [
            {
                "object_id": object_id,
                "sensor_id": sensor_id,
                "frame_id": frame_id,
                "vector": vector,
            }
        ]
    )
    return True


# ---------------------------------------------------------------------------
# KafkaIngestor: owns subprocess-local state and the consumer loop
# ---------------------------------------------------------------------------


class KafkaIngestor:
    """Subprocess-resident broker consumer + Milvus inserter.

    Handles both Kafka and Redis Streams; ``run()`` branches once on
    ``MESSAGE_BROKER`` and the two read loops converge on
    :meth:`_process_message`. (The class name predates Redis support and
    is kept to avoid churn at the call sites.)

    Constructed inside the Kafka consumer subprocess, AFTER fork. Opens
    its own Milvus connection so the inherited (non-fork-safe) gRPC
    channel from the parent is never reused. All state lives on the
    instance; there are no module-level mutable globals.

    Pipeline
    --------
    The consumer loop NEVER does Milvus gRPC directly. It only parses
    Kafka messages and hands work (a list of row dicts) to a bounded
    ``queue.Queue``. A background pool of writer threads drains the
    queue and performs the actual ``Collection.insert(...)``. Because
    pymilvus releases the GIL during gRPC, multiple writers genuinely
    run in parallel.

    A separate periodic-flush thread enforces a wall-clock upper bound
    on how long the trailing batch (in batched mode) can sit unflushed
    when the Kafka stream stops or slows down. Without it, the last
    partial batch waits until the *next* message arrives

    Both ``INSERT_EMBEDDINGS_IN_BATCHES=true`` and ``=false`` go through
    the same writer pool: the only difference is the size of the lists
    enqueued (one row per message vs. up to ``INSERT_BATCH_SIZE`` rows
    per flush). Batched mode is the recommended production setting; the
    non-batched path is retained for low-volume / debug scenarios.
    """

    def __init__(
        self,
        *,
        kafka_config: KafkaConfig,
        milvus_config: MilvusConfig,
        tracking_store: TrackingStore,
        new_data_event: Any,  # multiprocessing.synchronize.Event
        redis_config: Optional[RedisConfig] = None,
    ) -> None:
        self._kafka_config = kafka_config
        self._redis_config = redis_config
        self._tracking_store = tracking_store
        self._new_data_event = new_data_event
        self._last_lag_log_ts = time.monotonic()

        # Per-subprocess Milvus connection. MUST happen post-fork (i.e. here
        # in __init__, AFTER the subprocess has started), not in the parent.
        connections.connect(
            alias="consumer",
            host=milvus_config.host,
            port=milvus_config.port,
        )
        self._insert_collection = Collection(milvus_config.collection_name, using="consumer")
        self._insert_collection.load()

        # Batching state (batched mode). Lock is non-reentrant; private
        # helpers below document which ones assume the lock is held.
        self._pending: list = []
        self._pending_lock = Lock()
        self._last_flush_ts = time.monotonic()

        # Writer pool: bounded queue + N worker threads.
        # The consumer thread (`run` loop) only ever PUTs to this queue;
        # the workers GET and perform the gRPC. This decouples Kafka
        # throughput from Milvus insert latency.
        self._insert_work_queue: "queue.Queue[List[dict]]" = queue.Queue(maxsize=INSERT_QUEUE_MAX)
        self._shutdown_event = threading.Event()
        self._writer_threads: List[threading.Thread] = []
        for worker_id in range(INSERT_WORKER_COUNT):
            t = threading.Thread(
                target=self._writer_loop,
                args=(worker_id,),
                name=f"milvus-insert-{worker_id}",
                daemon=True,
            )
            t.start()
            self._writer_threads.append(t)

        # Periodic-flush thread: wall-clock guarantee on trailing-batch
        # latency in batched mode. Harmless in non-batched mode -- the
        # pending list stays empty so the loop just sleeps.
        self._periodic_flush_thread = threading.Thread(
            target=self._periodic_flush_loop,
            name="periodic-flush",
            daemon=True,
        )
        self._periodic_flush_thread.start()

        # Embeddings-added rate counter. Constructed here (post-fork) so
        # its background reporter thread is owned by this subprocess --
        # threads aren't inherited across fork(), so a parent-side
        # instance would be useless here. The reported rate is cumulative
        # from the first insert that lands in this subprocess.
        self._insert_profiler = RateProfiler(
            "kafka_inserts",
            unit="embeddings",
            log_interval_sec=PROFILE_LOG_INTERVAL_SEC,
            logger=logger,
        )

        logger.info(
            f"KafkaIngestor: started {INSERT_WORKER_COUNT} writer thread(s), "
            f"periodic-flush thread; queue_max={INSERT_QUEUE_MAX}, "
            f"batch_size={INSERT_BATCH_SIZE}, "
            f"batch_timeout_sec={INSERT_BATCH_TIMEOUT_SEC}, "
            f"batched_mode={INSERT_EMBEDDINGS_IN_BATCHES}"
        )

        try:
            import google.protobuf as _pb
            from google.protobuf.internal import api_implementation as _ai

            logger.info(f"protobuf: version={_pb.__version__} impl={_ai.Type()}")
        except Exception as _e:
            logger.warning(f"could not introspect protobuf impl: {_e}")

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    def _create_consumer(self) -> KafkaConsumer:
        """Open a ``KafkaConsumer``. Raises on broker/bootstrap failure."""
        logger.info(f"Attempting to connect to Kafka at {self._kafka_config.server}")
        logger.info(f"Topic: {self._kafka_config.topic}, Group ID: {self._kafka_config.group_id}")
        return KafkaConsumer(
            self._kafka_config.topic,
            bootstrap_servers=self._kafka_config.server,
            group_id=self._kafka_config.group_id,
            auto_offset_reset="latest",
            enable_auto_commit=True,
            request_timeout_ms=30000,  # 30 seconds
            session_timeout_ms=10000,  # 10 seconds
            heartbeat_interval_ms=3000,  # 3 seconds
        )

    def run(self, ready_event: Any = None) -> None:
        """Consume from the configured broker until the loop exits.

        Single branch on ``MESSAGE_BROKER``; both loops signal
        ``ready_event`` only after their first successful connect, so
        ``/health/ready`` reports 503 until the broker is reachable.
        """
        if MESSAGE_BROKER == "redis":
            self._run_redis_loop(ready_event=ready_event)
        else:
            self._run_kafka_loop(ready_event=ready_event)

    def _run_kafka_loop(self, ready_event: Any = None) -> None:
        """Connect to Kafka (retrying), signal ready, then consume until error.

        First connect is required for ``/health/ready``: this process stays
        alive and retries so the parent reports 503 until the broker accepts
        the consumer. After a successful first connect, a consume-loop error
        still ends this process (unchanged).
        """
        consumer = None
        while consumer is None:
            try:
                consumer = self._create_consumer()
            except Exception as e:
                logger.error("=" * 50)
                logger.error("KAFKA CONNECTION FAILED!")
                logger.error(f"Error: {str(e)}")
                logger.error("=" * 50)
                logger.error(f"Kafka is configured as: {self._kafka_config.server}")
                logger.error("If running in Docker, make sure Kafka is accessible at this address")
                logger.warning(
                    f"Retrying Kafka connect in {KAFKA_CONSUMER_CONNECT_RETRY_SEC}s; "
                    "/health/ready stays not-ready until connect succeeds"
                )
                time.sleep(KAFKA_CONSUMER_CONNECT_RETRY_SEC)

        logger.info("Successfully connected to Kafka!")
        logger.info(
            f"Started Kafka consumer for topic: {self._kafka_config.topic}, " f"Group ID: {self._kafka_config.group_id}"
        )
        if ready_event is not None:
            ready_event.set()

        try:
            for message in consumer:
                self._process_message(message.value)
                """
                # add profiler
                with cProfile.Profile() as pr:
                    self._process_message(message.value)
                    import pstats
                    stats = pstats.Stats(pr)
                    stats.sort_stats('cumulative').print_stats(5)
                """
        except Exception as e:
            logger.error("=" * 50)
            logger.error("KAFKA CONSUME LOOP EXITED!")
            logger.error(f"Error: {str(e)}")
            logger.error("=" * 50)
            logger.warning("kafka_consumer process will stop; /health/ready will fail until restart")

    # ------------------------------------------------------------------
    # Redis Streams backend
    # ------------------------------------------------------------------

    def _create_redis_client(self):
        """Open + ping a Redis client. Raises on connect failure.

        ``decode_responses`` stays at its ``False`` default on purpose:
        DeepStream publishes serialized protobuf, which is binary and
        contains NUL bytes, so payloads must come back as raw ``bytes``.
        """
        if redis_lib is None:
            raise RuntimeError(
                "MESSAGE_BROKER=redis but the 'redis' package is not installed. "
                "Install it (pip install redis) or set MESSAGE_BROKER=kafka."
            )
        cfg = self._redis_config
        if cfg is None:
            raise RuntimeError("MESSAGE_BROKER=redis but no RedisConfig was passed to KafkaIngestor.")
        logger.info(f"Attempting to connect to Redis at {cfg.host}:{cfg.port}")
        logger.info(
            f"Stream: {cfg.stream}, Group: {cfg.group}, Consumer: {cfg.consumer_name}, "
            f"Payload key: {cfg.payload_key}"
        )
        client = redis_lib.Redis(
            host=cfg.host,
            port=cfg.port,
            password=cfg.password,
            socket_keepalive=True,
            # Must exceed BLOCK or every idle XREADGROUP would time out.
            socket_timeout=(REDIS_CONSUMER_BLOCK_MS / 1000.0) + 15.0,
            socket_connect_timeout=10.0,
        )
        client.ping()
        return client

    def _ensure_group(self, client) -> None:
        """Create the consumer group if absent, tolerating a concurrent create.

        ``mkstream=True`` lets us attach before DeepStream has published
        anything. ``start_id`` only matters on first creation; an existing
        group keeps its own cursor.
        """
        cfg = self._redis_config
        try:
            client.xgroup_create(
                name=cfg.stream,
                groupname=cfg.group,
                id=cfg.start_id,
                mkstream=True,
            )
            logger.info(
                f"Created Redis consumer group {cfg.group!r} on stream " f"{cfg.stream!r} starting at {cfg.start_id!r}"
            )
        except redis_lib.exceptions.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
            logger.info(
                f"Redis consumer group {cfg.group!r} already exists on stream "
                f"{cfg.stream!r}; resuming from its stored cursor"
            )

    def _run_redis_loop(self, ready_event: Any = None) -> None:
        """Connect to Redis (retrying), signal ready, then XREADGROUP forever.

        Acking policy: entries are XACKed once handed to the writer pool,
        NOT after Milvus confirms the insert. That matches the Kafka path's
        ``enable_auto_commit=True`` semantics -- a batch dropped by
        :meth:`_enqueue_batch` under backpressure is lost either way. Moving
        the XACK behind the insert would be a genuine at-least-once upgrade,
        but it needs stream IDs threaded through the pending buffer.
        """
        cfg = self._redis_config
        client = None
        while client is None:
            try:
                client = self._create_redis_client()
                self._ensure_group(client)
            except Exception as e:
                logger.error("=" * 50)
                logger.error("REDIS CONNECTION FAILED!")
                logger.error(f"Error: {str(e)}")
                logger.error("=" * 50)
                logger.error(f"Redis is configured as: " f"{cfg.host if cfg else '?'}:{cfg.port if cfg else '?'}")
                logger.error("If running in Docker, make sure Redis is accessible at this address")
                logger.warning(
                    f"Retrying Redis connect in {REDIS_CONSUMER_CONNECT_RETRY_SEC}s; "
                    "/health/ready stays not-ready until connect succeeds"
                )
                client = None
                time.sleep(REDIS_CONSUMER_CONNECT_RETRY_SEC)

        logger.info("Successfully connected to Redis!")
        logger.info(f"Started Redis Streams consumer for stream: {cfg.stream}, " f"Group: {cfg.group}")
        if ready_event is not None:
            ready_event.set()

        payload_key = cfg.payload_key.encode("utf-8")
        try:
            while True:
                try:
                    response = client.xreadgroup(
                        groupname=cfg.group,
                        consumername=cfg.consumer_name,
                        streams={cfg.stream: ">"},
                        count=REDIS_CONSUMER_BATCH_COUNT,
                        block=REDIS_CONSUMER_BLOCK_MS,
                    )
                except (
                    redis_lib.exceptions.ConnectionError,
                    redis_lib.exceptions.TimeoutError,
                ) as e:
                    # Unlike kafka-python, redis-py surfaces transient socket
                    # drops to the caller but reconnects on the next command,
                    # so these must not kill the process.
                    logger.warning(f"Redis read failed transiently, retrying: {e}")
                    time.sleep(1.0)
                    continue

                if not response:
                    # BLOCK expired with nothing new.
                    self._maybe_log_lag(client)
                    continue

                for _stream_name, entries in response:
                    ack_ids = []
                    for entry_id, fields in entries:
                        payload = fields.get(payload_key)
                        if payload is None:
                            logger.error(
                                f"Redis entry {entry_id!r} has no field "
                                f"{cfg.payload_key!r} (present: "
                                f"{[k.decode('utf-8', 'replace') for k in fields]}). "
                                f"Check `payloadkey` in DeepStream's cfg_redis.txt "
                                f"against REDIS_CONSUMER_PAYLOAD_KEY."
                            )
                        else:
                            self._process_message(payload)
                        ack_ids.append(entry_id)
                    if ack_ids:
                        client.xack(cfg.stream, cfg.group, *ack_ids)

                self._maybe_log_lag(client)
        except Exception as e:
            logger.error("=" * 50)
            logger.error("REDIS CONSUME LOOP EXITED!")
            logger.error(f"Error: {str(e)}")
            logger.error("=" * 50)
            logger.warning("kafka_consumer process will stop; /health/ready will fail until restart")

    def _maybe_log_lag(self, client) -> None:
        """Periodically log consumer-group lag for the ingest stream.

        DeepStream's adapter publishes with ``MAXLEN ~ <streamsize>``, so if
        this consumer falls further behind than that window, entries are
        trimmed away while still unread and the data is gone with no other
        symptom. This log is the only warning you get.
        """
        if REDIS_CONSUMER_LAG_LOG_INTERVAL_SEC <= 0:
            return
        now = time.monotonic()
        if now - self._last_lag_log_ts < REDIS_CONSUMER_LAG_LOG_INTERVAL_SEC:
            return
        self._last_lag_log_ts = now
        cfg = self._redis_config
        try:
            group_name = cfg.group.encode("utf-8")
            for info in client.xinfo_groups(cfg.stream):
                if info.get("name", info.get(b"name")) not in (cfg.group, group_name):
                    continue
                # `lag` and `entries-read` require Redis >= 7.0 and read as
                # None on older servers or after a stream trim.
                logger.info(
                    f"Redis stream {cfg.stream!r} group {cfg.group!r}: "
                    f"lag={info.get('lag', info.get(b'lag'))} "
                    f"pending={info.get('pending', info.get(b'pending'))} "
                    f"stream_len={client.xlen(cfg.stream)}"
                )
        except Exception as e:
            logger.debug(f"Could not read Redis consumer-group lag: {e}")

    # ------------------------------------------------------------------
    # Internal: per-message parsing + dispatch
    # ------------------------------------------------------------------

    def _process_message(self, payload: bytes) -> None:
        """Parse one raw broker payload and dispatch its rows downstream.

        Transport-agnostic: ``payload`` is the message body, whatever
        delivered it (``message.value`` from Kafka, the ``payloadkey``
        field of a Redis stream entry).
        """
        try:
            embedding_batch = []
            tracked_obj_batch = []

            if DEEPSTREAM_SCHEMA == "deepstream":
                data = json.loads(payload.decode("utf-8"))
                # Validate the data structure for PAYLOAD_DEEPSTREAM - Deepstream schema payload
                if "object" not in data or "id" not in data["object"]:
                    logger.error(f"Invalid message format: {data}")
                    return

                if "embedding" in data["object"]:
                    embedding_length = data["object"]["embedding"]["embedding_length"]
                    embedding_vector = data["object"]["embedding"]["embedding_vector"]
                    object_id = str(data["object"]["id"])
                    if embedding_length != DIMENSION:
                        logger.error(f"Invalid vector dimension: {embedding_length}")
                        return
                    if not INSERT_EMBEDDINGS_IN_BATCHES:
                        self._insert_one(embedding_vector, object_id)
                    else:
                        embedding_batch.append(
                            {"object_id": object_id, "sensor_id": "Unknown", "frame_id": -1, "vector": embedding_vector}
                        )

            elif DEEPSTREAM_SCHEMA == "minimal":
                data = json.loads(payload.decode("utf-8"))
                sensor_id = str(data["sensor"]) if "sensor" in data else "Unknown"
                frame_id = int(data["id"]) if "id" in data else -1
                if "objects" not in data:
                    logger.error(f"Invalid message format: {data}")
                    return
                logger.info(f"Message received for sensor_id: {sensor_id}, frame_id: {frame_id}")
                for object_str in data["objects"]:
                    fields = object_str.split("|")
                    if "embedding" not in fields:
                        logger.info("Embedding not found in object. Continue.")
                        continue
                    object_id = str(fields[0])
                    embedding_index = fields.index("embedding") + 1
                    if embedding_index >= len(fields):
                        logger.info("Embedding index out of bounds. Continue.")
                        continue
                    embedding_vector = [float(x) for x in fields[embedding_index].split(",")]
                    if len(embedding_vector) != DIMENSION:
                        logger.error(
                            f"Invalid vector dimension for object_id={object_id}: "
                            f"{len(embedding_vector)}, expected {DIMENSION}. Skipping object."
                        )
                        continue
                    if not INSERT_EMBEDDINGS_IN_BATCHES:
                        self._insert_one(embedding_vector, object_id, sensor_id, frame_id)
                    else:
                        embedding_batch.append(
                            {
                                "object_id": object_id,
                                "sensor_id": sensor_id,
                                "frame_id": frame_id,
                                "vector": embedding_vector,
                            }
                        )

            elif DEEPSTREAM_SCHEMA == "protobuf":
                frame = schema_pb2.Frame().FromString(payload)
                sensor_id = str(frame.sensorId) if frame.sensorId else "Unknown"
                frame_id = int(frame.id) if frame.id else -1
                if frame.HasField("timestamp"):
                    timestamp = frame.timestamp.ToJsonString()
                else:
                    timestamp = 0
                version = frame.version or None

                if not frame.objects:
                    logger.info(
                        f"No objects found in message for sensor_id: {sensor_id}, " f"frame_id: {frame_id}. Continue."
                    )
                    return
                for obj in frame.objects:
                    if not obj.id:
                        logger.debug(
                            f"No id found in object for sensor_id: {sensor_id}, " f"frame_id: {frame_id}. Continue."
                        )
                        continue
                    object_id = str(obj.id)
                    logger.debug(f"object_id: {object_id}")
                    has_embedding = False
                    if obj.HasField("embedding") and len(obj.embedding.vector) > 0:
                        embedding_vector = np.array(obj.embedding.vector, dtype=np.float32)
                        if len(embedding_vector) != DIMENSION:
                            logger.error(
                                f"Invalid vector dimension for object_id={object_id}: "
                                f"{len(embedding_vector)}, expected {DIMENSION}. Skipping object."
                            )
                            continue
                        has_embedding = True
                        if not INSERT_EMBEDDINGS_IN_BATCHES:
                            self._insert_one(embedding_vector, object_id, sensor_id, frame_id)
                        else:
                            embedding_batch.append(
                                {
                                    "object_id": object_id,
                                    "sensor_id": sensor_id,
                                    "frame_id": frame_id,
                                    "vector": embedding_vector,
                                }
                            )
                    # Saving tracking db entry only if TRACKING_DB_STORE_ALL_ENTRIES is enabled
                    # OR embeddings are present
                    save_tracking_entry = obj.HasField("bbox") and (TRACKING_DB_STORE_ALL_ENTRIES or has_embedding)
                    if save_tracking_entry:
                        bbox = obj.bbox
                        has_rightX = bbox.rightX != 0.0
                        has_bottomY = bbox.bottomY != 0.0
                        has_confidence = obj.confidence != 0.0

                        if has_rightX and has_bottomY and has_confidence:
                            kafka_raw = None
                            if has_embedding and ENABLE_SECONDARY_EMBEDDING:
                                obj.ClearField("embedding")
                                kafka_raw = json_format.MessageToDict(obj)
                            if "visibility" in obj.info:
                                visibility = float(obj.info["visibility"])
                            else:
                                visibility = 1.0  # default visibility is 1.0
                            tracked_obj = TrackedObject(
                                object_id=object_id,
                                stream_id=sensor_id,
                                frame_id=frame_id,
                                timestamp=timestamp,
                                leftX=float(bbox.leftX),
                                topY=float(bbox.topY),
                                rightX=float(bbox.rightX),
                                bottomY=float(bbox.bottomY),
                                confidence=float(obj.confidence),
                                visibility=visibility,
                                version=version,
                                kafka_raw=kafka_raw,
                            )
                            tracked_obj_batch.append(tracked_obj)
                        elif not has_confidence:
                            continue
                        else:
                            logger.warning(
                                f"Invalid bbox for object_id={object_id} "
                                f"frame_id={frame_id} "
                                f"(rightX={bbox.rightX}, bottomY={bbox.bottomY}, "
                                f"confidence={obj.confidence})"
                            )
                            continue
            else:
                logger.error(f"Invalid schema type: {DEEPSTREAM_SCHEMA}")
                return

            if tracked_obj_batch:
                self._tracking_store.insert_many(tracked_obj_batch)
            self._accumulate(embedding_batch)
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")

    # ------------------------------------------------------------------
    # Internal: Milvus inserts (single + batched)
    # ------------------------------------------------------------------

    def _insert_one(self, vector, object_id: str, sensor_id: str = "Unknown", frame_id: int = -1) -> None:
        """Per-message enqueue for the non-batched path. Builds a
        single-row batch and hands it to the writer pool."""
        if len(vector) != DIMENSION:
            return
        self._enqueue_batch(
            [
                {
                    "object_id": object_id,
                    "sensor_id": sensor_id,
                    "frame_id": frame_id,
                    "vector": vector,
                }
            ]
        )
        logger.info(
            f"Successfully queued message for sensor_id: {sensor_id}, " f"frame_id: {frame_id}, object_id: {object_id}"
        )

    def _accumulate(self, batch) -> None:
        """Add embeddings to the pending buffer; if the count or time
        threshold is reached, move the buffer onto the writer queue."""
        if not batch:
            return
        with self._pending_lock:
            self._pending.extend(batch)
            elapsed = time.monotonic() - self._last_flush_ts
            if len(self._pending) >= INSERT_BATCH_SIZE or elapsed >= INSERT_BATCH_TIMEOUT_SEC:
                self._flush_locked()

    def _flush_locked(self) -> None:
        """Move the pending buffer onto the writer queue. Caller MUST
        hold :attr:`_pending_lock`.
        """
        if not self._pending:
            return
        batch = self._pending
        self._pending = []
        self._last_flush_ts = time.monotonic()
        self._enqueue_batch(batch)

    def _enqueue_batch(self, batch: List[dict]) -> None:
        """Hand a batch to the writer pool.

        Bounded queue with backpressure: blocks up to
        ``INSERT_QUEUE_PUT_TIMEOUT_SEC`` waiting for space. If the queue
        is *still* full, we DROP the batch with a loud warning rather
        than let unbounded memory growth crash the subprocess. If you
        see this log fire, the fix is one of:
          - raise ``INSERT_WORKER_COUNT`` (Milvus can probably absorb more
            parallel writers and we're just not feeding it fast enough);
          - raise ``INSERT_QUEUE_MAX`` (more headroom to ride out bursts);
          - or accept that Milvus is the bottleneck and the producer
            needs to slow down.
        """
        try:
            self._insert_work_queue.put(batch, timeout=INSERT_QUEUE_PUT_TIMEOUT_SEC)
        except queue.Full:
            logger.error(
                f"Insert work queue full (qsize={self._insert_work_queue.qsize()}, "
                f"max={INSERT_QUEUE_MAX}). Milvus is too slow; DROPPING batch of "
                f"{len(batch)} embeddings to avoid OOM. Tune INSERT_WORKER_COUNT "
                f"or INSERT_QUEUE_MAX."
            )

    # ------------------------------------------------------------------
    # Background worker threads
    # ------------------------------------------------------------------

    def _writer_loop(self, worker_id: int) -> None:
        """Background thread: drain :attr:`_insert_work_queue` and write
        to Milvus. Multiple instances run in parallel — pymilvus releases
        the GIL during gRPC, so the threads genuinely overlap on I/O."""
        log = logging.getLogger(f"milvus_insert_worker_{worker_id}")
        while not self._shutdown_event.is_set():
            try:
                # Short timeout so the loop notices shutdown promptly
                # without burning CPU when the queue is idle.
                batch = self._insert_work_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                t0 = time.monotonic()
                self._insert_collection.insert(batch)
                dt_ms = (time.monotonic() - t0) * 1000.0
                log.info(
                    f"Inserted {len(batch)} embeddings in {dt_ms:.1f} ms " f"(qsize={self._insert_work_queue.qsize()})"
                )
                self._new_data_event.set()
                # Count embeddings (not batches) so the reported rate is
                # comparable to the producer-side rate even when batch
                # sizes vary.
                self._insert_profiler.record(count=len(batch))
            except Exception:
                log.exception(f"Milvus insert failed for batch of {len(batch)}")
            finally:
                self._insert_work_queue.task_done()

    def _periodic_flush_loop(self) -> None:
        """Background thread: time-based flush of the pending buffer in
        batched mode so the trailing partial batch never sits indefinitely
        when the Kafka stream stops or slows down.

        Polls at half the configured timeout for tight wall-clock latency
        without burning CPU. Worst-case latency for a trailing batch is
        ``~1.5 * INSERT_BATCH_TIMEOUT_SEC``.
        """
        poll_interval = max(0.05, INSERT_BATCH_TIMEOUT_SEC / 2)
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(poll_interval)
            with self._pending_lock:
                elapsed = time.monotonic() - self._last_flush_ts
                if self._pending and elapsed >= INSERT_BATCH_TIMEOUT_SEC:
                    self._flush_locked()

    # ------------------------------------------------------------------
    # Graceful shutdown (not called by the daemon subprocess path today;
    # provided for tests and any future supervised-stop path).
    # ------------------------------------------------------------------

    def shutdown(self, timeout: float = 5.0) -> None:
        """Drain the pending buffer + work queue, then stop the worker
        threads. Best-effort: bounded by ``timeout`` seconds total."""
        deadline = time.monotonic() + timeout

        # Final flush of any pending rows so they make it into the queue.
        with self._pending_lock:
            if self._pending:
                self._flush_locked()

        # Wait for workers to drain the queue (up to deadline).
        while not self._insert_work_queue.empty() and time.monotonic() < deadline:
            time.sleep(0.05)

        self._shutdown_event.set()
        remaining = max(0.0, deadline - time.monotonic())
        for t in self._writer_threads:
            t.join(timeout=remaining)
            remaining = max(0.0, deadline - time.monotonic())
        self._periodic_flush_thread.join(timeout=remaining)


# ---------------------------------------------------------------------------
# Subprocess entrypoint + parent-side factory
# ---------------------------------------------------------------------------


def _kafka_consumer_subprocess(
    *,
    kafka_config: KafkaConfig,
    milvus_config: MilvusConfig,
    tracking_store: TrackingStore,
    new_data_event: Any,
    ready_event: Any,
    ignore_sigint: Callable[[], None],
    redis_config: Optional[RedisConfig] = None,
) -> None:
    """Target for the broker consumer subprocess.

    Runs the post-fork bootstrap (ignore SIGINT, attach tracking store,
    rebind logger name) and then hands control off to a
    :class:`KafkaIngestor` instance.
    """
    ignore_sigint()
    # Phase 1: no-op for InMemoryTrackingStore. Phase 2 (SQLite) /
    # Phase 3 (Redis) backends open per-process resources here.
    tracking_store.attach_post_fork()

    # Process-distinguishing logger name. Under LOG_ASYNC=true the
    # inherited root logger already has a QueueHandler -> shared
    # _log_queue, so records emitted here flow through the same listener
    # process. Under fork, no explicit log_queue plumbing is needed.
    global logger
    logger = logging.getLogger("kafka_consumer")

    ingestor = KafkaIngestor(
        kafka_config=kafka_config,
        milvus_config=milvus_config,
        tracking_store=tracking_store,
        new_data_event=new_data_event,
        redis_config=redis_config,
    )
    logger.info(f"Consumer process started (broker={MESSAGE_BROKER}) with dedicated " f"Milvus connection")

    # Ready is signalled inside run() only after the first broker connect
    # succeeds. Until then /health/ready reports proc:kafka_consumer as
    # alive-but-not-ready.
    ingestor.run(ready_event=ready_event)


def create_kafka_consumer(
    *,
    kafka_config: KafkaConfig,
    milvus_config: MilvusConfig,
    tracking_store: TrackingStore,
    new_data_event: Any,
    register_child_proc: Callable[[str, "multiprocessing.Process", Any], None],
    ignore_sigint: Callable[[], None],
    redis_config: Optional[RedisConfig] = None,
) -> "multiprocessing.Process":
    """Parent-side factory: spawn the broker consumer subprocess and
    register it with the health probe registry.

    Which of ``kafka_config`` / ``redis_config`` is actually used is decided
    inside the subprocess by ``MESSAGE_BROKER``; both are passed so the
    child needs no extra plumbing to switch. The process is registered as
    ``kafka_consumer`` for either backend to keep the health-probe key and
    shutdown ordering stable.

    Must be called from the parent process before subprocess-dependent
    drainer threads start.
    """
    ready_event = multiprocessing.Event()
    proc = multiprocessing.Process(
        target=_kafka_consumer_subprocess,
        kwargs=dict(
            kafka_config=kafka_config,
            milvus_config=milvus_config,
            tracking_store=tracking_store,
            new_data_event=new_data_event,
            ready_event=ready_event,
            ignore_sigint=ignore_sigint,
            redis_config=redis_config,
        ),
        daemon=True,
    )
    proc.start()
    register_child_proc("kafka_consumer", proc, ready_event)
    return proc


__all__ = [
    "KafkaConfig",
    "RedisConfig",
    "MilvusConfig",
    "insert_embedding",
    "create_kafka_consumer",
]
