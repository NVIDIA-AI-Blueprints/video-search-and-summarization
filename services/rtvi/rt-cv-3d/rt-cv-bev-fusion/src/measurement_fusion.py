# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
MV3DT Measurement Fusion Service

Consumes per-sensor Frame messages from mdx-mv3dt-raw and aggregates all frames
sharing the same timestamp bucket into a single fused BEV Frame, then publishes to mdx-bev.

Fusion logic:
  - Buffer messages per timestamp bucket (BUCKET_MS window); flush when all expected
    sensors arrive or on timeout. Timestamp bucketing is required for multi-container
    deployments where each DeepStream instance maintains an independent frame counter
    — frame IDs diverge across containers but wall-clock timestamps stay aligned.
  - For each unique object ID: element-wise average of bbox3d coordinates and confidence
  - Object type resolved by majority vote across sensors (ties broken by total confidence)
  - Fused Frame timestamp: arithmetic mean of all sensor timestamps
  - Per-sensor timestamps stored in Frame.info (key = sensorId, value = ISO timestamp)
  - Output sensorId = "bev-sensor-1"

Broker support:
  - BROKER_TYPE=kafka (default): Confluent Kafka
  - BROKER_TYPE=redis: Redis Streams (XADD/XREAD, payloadkey=sensor.id for mdx-bev, value for mdx-mv3dt-raw)
"""

import logging
import os
import signal
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from google.protobuf import timestamp_pb2

import schema_pb2

# ---------------------------------------------------------------------------
# Configuration (all overridable via environment variables)
# ---------------------------------------------------------------------------

# --- Broker / topics --------------------------------------------------------
BROKER_TYPE          = os.environ.get("BROKER_TYPE",     "kafka")           # "kafka" or "redis"
KAFKA_BOOTSTRAP      = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")  # Kafka bootstrap servers
REDIS_HOST           = os.environ.get("REDIS_HOST",      "localhost")       # Redis host (BROKER_TYPE=redis)
REDIS_PORT           = int(os.environ.get("REDIS_PORT",  "6379"))           # Redis port
RAW_TOPIC            = os.environ.get("RAW_TOPIC",       "mdx-raw")         # Input: per-sensor Frames
FUSED_TOPIC          = os.environ.get("FUSED_TOPIC",     "mdx-bev")         # Output: fused BEV Frames
CONSUMER_POLL_MS     = float(os.environ.get("CONSUMER_POLL_MS", "10"))      # Broker poll/block timeout per iter

# --- Pipeline information ---------------------------------------------------
# Number of distinct sensors expected per timestamp bucket. Once a bucket has
# this many sensors, the fast-path "all here" flush fires immediately.
MAX_EXPECTED_SENSORS = int(os.environ.get("MAX_EXPECTED_SENSORS", "4"))
# sensorId stamped on every fused Frame published to FUSED_TOPIC.
FUSED_SENSOR_ID      = "bev-sensor-1"

# --- Fusion timing ----------------------------------------------------------
# Max time a bucket waits for missing sensors before flushing with whatever it has.
# Applied to BOTH event-time lag (watermark trigger in _buffer_frame) and arrival-time
# age (sweep trigger in _sweep_loop) — whichever crosses the threshold first wins.
SENSOR_TIMEOUT_MS    = float(os.environ.get("SENSOR_TIMEOUT_MS",  "100"))
# Width of one timestamp bucket. Frames whose POSIX timestamps round to the same slot
# are fused together. Default 17 ms = half a 30-FPS frame: narrow enough to never lump
# adjacent frames, wide enough to absorb ~8 ms of inter-container clock skew. The empty
# "guard" bucket between consecutive frames also prevents cross-frame contamination
# if a sensor's clock drifts more than half a frame.
BUCKET_MS            = float(os.environ.get("BUCKET_MS",          "17"))

# --- Background sweep / memory bounds ---------------------------------------
# Sweep thread cadence. Bounds how stale a bucket can get before the arrival-time
# branch (flush or stale-drop) notices it.
SWEEP_INTERVAL_S     = float(os.environ.get("SWEEP_INTERVAL_S",   "0.02"))  # 20 ms
# Hard upper bound on bucket age. Older buckets are *dropped* (not flushed) as a
# safety net for pathological conditions (sweep starvation, broker back-pressure).
# In healthy operation no bucket comes close to this.
BUFFER_DURATION_S    = float(os.environ.get("BUFFER_DURATION_S",  "1.0"))
# How long to remember already-flushed bucket keys. Rejects late stragglers so the
# same fused Frame.id is never republished — without this the BEV display "blinks".
# This is purely a memory bound; the rejection check itself is mandatory.
CLOSED_BUCKET_RETENTION_MS = float(os.environ.get("CLOSED_BUCKET_RETENTION_MS", "1000"))

# --- Logging ----------------------------------------------------------------
# Set LOG_LEVEL=DEBUG to enable verbose per-frame tracing (RECV, TRIG-*, PUBLISH, ...).
LOG_LEVEL            = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_proto_timestamp(ts_msg) -> float:
    """Convert google.protobuf.Timestamp to POSIX seconds (float)."""
    return ts_msg.seconds + ts_msg.nanos / 1e9


def _posix_to_proto_timestamp(posix: float) -> timestamp_pb2.Timestamp:
    ts = timestamp_pb2.Timestamp()
    ts.seconds = int(posix)
    ts.nanos   = int((posix - int(posix)) * 1e9)
    return ts


def _posix_to_rfc3339(posix: float) -> str:
    dt = datetime.fromtimestamp(posix, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _ts_bucket_key(posix_ts: float) -> int:
    """Quantize a POSIX timestamp into a BUCKET_MS-wide bucket index.

    This is the fusion key, used in place of frame_id. Frames from different
    DeepStream containers have unrelated frame_id counters, but their POSIX
    timestamps stay aligned — so rounding to a shared time grid groups same-
    instant frames across containers.
    """
    return round(posix_ts * 1000 / BUCKET_MS)


def _element_wise_mean(arrays: list[list[float]]) -> list[float]:
    if not arrays:
        return []
    n = len(arrays[0])
    result = [0.0] * n
    for arr in arrays:
        for i, v in enumerate(arr):
            result[i] += v
    count = len(arrays)
    return [x / count for x in result]


def _majority_type(instances: list) -> str:
    """Resolve type disagreement across sensors by majority vote.

    Each sensor instance casts one vote for its reported type. Ties are
    broken by total confidence (sum across the tied type's instances), and
    further ties fall back to first-seen order via Python's stable max().
    """
    type_counts: dict[str, int]   = defaultdict(int)
    type_conf:   dict[str, float] = defaultdict(float)
    for inst in instances:
        type_counts[inst.type] += 1
        type_conf[inst.type]   += inst.confidence
    return max(type_counts, key=lambda t: (type_counts[t], type_conf[t]))


# ---------------------------------------------------------------------------
# Fusion logic
# ---------------------------------------------------------------------------
def fuse_frames(bucket_key: int, sensor_frames: dict) -> schema_pb2.Frame:
    """
    Build a single fused Frame from a dict of {sensorId: Frame protobuf message}.
    """
    # --- average timestamps ---
    timestamps = [_parse_proto_timestamp(f.timestamp) for f in sensor_frames.values()]
    avg_ts_posix = sum(timestamps) / len(timestamps)

    # --- per-sensor timestamp info ---
    frame_info = {sid: _posix_to_rfc3339(_parse_proto_timestamp(f.timestamp))
                  for sid, f in sensor_frames.items()}

    # --- aggregate objects by ID ---
    objects_by_id: dict[str, list[schema_pb2.Object]] = defaultdict(list)
    for frame in sensor_frames.values():
        for obj in frame.objects:
            objects_by_id[obj.id].append(obj)

    fused_objects = []
    for obj_id, instances in objects_by_id.items():
        coord_arrays = []
        for inst in instances:
            if inst.HasField("bbox3d") and len(inst.bbox3d.coordinates) == 12:
                coord_arrays.append(list(inst.bbox3d.coordinates))

        avg_coords = _element_wise_mean(coord_arrays) if coord_arrays else [0.0] * 12
        avg_conf = sum(inst.confidence for inst in instances) / len(instances)

        fused_obj = schema_pb2.Object()
        fused_obj.id         = obj_id
        fused_obj.type       = _majority_type(instances)
        fused_obj.confidence = avg_conf
        fused_obj.bbox3d.coordinates[:] = avg_coords
        fused_obj.bbox3d.confidence     = avg_conf
        fused_objects.append(fused_obj)

    # --- build fused Frame ---
    fused_frame = schema_pb2.Frame()
    fused_frame.version  = "4.0"
    fused_frame.id       = str(bucket_key)
    fused_frame.sensorId = FUSED_SENSOR_ID
    fused_frame.timestamp.CopyFrom(_posix_to_proto_timestamp(avg_ts_posix))
    fused_frame.objects.extend(fused_objects)
    fused_frame.info.update(frame_info)

    return fused_frame


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------
class MeasurementFusionService:
    def __init__(self):
        # Open buckets pending fusion. Keyed by bucket_key (= round(posix_ms / BUCKET_MS))
        # so frames from different DeepStream containers — which have independent frame
        # counters — are grouped by wall-clock time. Each entry:
        #   'frames':        {sensorId: Frame}   — sensors that have arrived so far
        #   'first_arrival': time.monotonic()    — wall-clock when the bucket was opened
        #   'first_posix':   earliest event-time seen in this bucket
        #   'last_posix':    latest   event-time seen in this bucket
        self._buffer: dict[int, dict] = {}
        # Recently-flushed bucket keys → monotonic close time. Rejects late stragglers
        # so the same fused Frame.id is never published twice (the "blinking" failure).
        self._closed_buckets: dict[int, float] = {}
        # Highest event-time ever observed, used as the watermark reference for the
        # event-time flush trigger. Monotonic; only ever moves forward.
        self._latest_seen_posix = 0.0

        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._flush_timeout_ms = SENSOR_TIMEOUT_MS

        # Counters surfaced in periodic INFO logs and final shutdown summary.
        self._published = 0
        self._received  = 0
        self._late_dropped = 0

    def _mark_bucket_closed(self, bucket: int, now_mono: float | None = None):
        self._closed_buckets[bucket] = now_mono if now_mono is not None else time.monotonic()

    # --- Buffer management ---

    def _buffer_frame(self, frame: schema_pb2.Frame):
        posix = _parse_proto_timestamp(frame.timestamp)
        bucket = _ts_bucket_key(posix)
        sid = frame.sensorId
        now = time.monotonic()
        to_flush: set[int] = set()

        with self._lock:
            if bucket in self._closed_buckets:
                self._late_dropped += 1
                closed_age_ms = (now - self._closed_buckets[bucket]) * 1000.0
                logger.debug(
                    "[DBG] LATE        bucket=%d sensor=%s ts=%.3f "
                    "(closed %.0fms ago, total_late=%d)",
                    bucket, sid, posix, closed_age_ms, self._late_dropped,
                )
                if self._late_dropped % 100 == 0:
                    logger.info(
                        "Dropped %d late raw frames for already-closed buckets "
                        "(latest bucket=%d)",
                        self._late_dropped, bucket,
                    )
                return

            is_new_bucket = bucket not in self._buffer
            if is_new_bucket:
                self._buffer[bucket] = {
                    "frames": {},
                    "first_arrival": now,
                    "first_posix": posix,
                    "last_posix": posix,
                }
                logger.debug(
                    "[DBG] NEW_BUCKET  bucket=%d sensor=%s ts=%.3f "
                    "(open_buckets=%d)",
                    bucket, sid, posix, len(self._buffer),
                )
            entry = self._buffer[bucket]
            entry["frames"][sid] = frame
            entry["first_posix"] = min(entry["first_posix"], posix)
            entry["last_posix"] = max(entry["last_posix"], posix)
            self._latest_seen_posix = max(self._latest_seen_posix, posix)

            logger.debug(
                "[DBG] RECV        bucket=%d sensor=%s ts=%.3f "
                "→ %d/%d sensors %s",
                bucket, sid, posix,
                len(entry["frames"]), MAX_EXPECTED_SENSORS,
                sorted(entry["frames"].keys()),
            )

            ready = len(entry["frames"]) >= MAX_EXPECTED_SENSORS
            if ready:
                to_flush.add(bucket)
                logger.debug(
                    "[DBG] TRIG-ALL    bucket=%d sensors=%s",
                    bucket, sorted(entry["frames"].keys()),
                )

            # Event-time watermark flush. If the stream has already advanced
            # SENSOR_TIMEOUT_MS past a bucket's latest event-time, no more sensors
            # are realistically going to land in that bucket — flush it now.
            for queued_bucket, queued_entry in self._buffer.items():
                lag_ms = (self._latest_seen_posix - queued_entry["last_posix"]) * 1000.0
                if lag_ms >= self._flush_timeout_ms:
                    to_flush.add(queued_bucket)
                    if queued_bucket != bucket or not ready:
                        logger.debug(
                            "[DBG] TRIG-WMARK  bucket=%d lag_ms=%.1f "
                            "latest_seen=%.3f sensors=%s (missing %d)",
                            queued_bucket, lag_ms, self._latest_seen_posix,
                            sorted(queued_entry["frames"].keys()),
                            MAX_EXPECTED_SENSORS - len(queued_entry["frames"]),
                        )

        for flush_bucket in sorted(to_flush):
            self._flush_frame(flush_bucket)

    def _flush_frame(self, bucket: int):
        with self._lock:
            entry = self._buffer.pop(bucket, None)
            # Always mark closed (even on a no-op pop) so any late straggler for
            # this bucket key is rejected instead of resurrecting the bucket.
            self._mark_bucket_closed(bucket)
        if entry is None:
            logger.debug("[DBG] FLUSH-NOOP  bucket=%d (already popped)", bucket)
            return
        sensor_frames = entry["frames"]
        if not sensor_frames:
            logger.debug("[DBG] FLUSH-EMPTY bucket=%d (no sensors)", bucket)
            return
        try:
            age_ms = (time.monotonic() - entry["first_arrival"]) * 1000.0
            fused = fuse_frames(bucket, sensor_frames)
            self._publish(fused.SerializeToString())
            self._published += 1
            logger.debug(
                "[DBG] PUBLISH     bucket=%d sensors=%s (%d/%d) "
                "age=%.0fms event_ts=%.3f objects=%d",
                bucket, sorted(sensor_frames.keys()),
                len(sensor_frames), MAX_EXPECTED_SENSORS,
                age_ms,
                _parse_proto_timestamp(fused.timestamp),
                len(fused.objects),
            )
            if self._published % 100 == 0:
                logger.info(
                    "Published %d fused frames (received %d raw frames, "
                    "last bucket=%d, sensors=%s)",
                    self._published, self._received,
                    bucket, list(sensor_frames.keys()),
                )
        except Exception as exc:
            logger.error("Failed to fuse/publish bucket=%d: %s", bucket, exc)

    # --- Background sweep thread ---

    def _sweep_loop(self):
        stale_s = BUFFER_DURATION_S
        closed_retention_s = CLOSED_BUCKET_RETENTION_MS / 1000.0

        while not self._shutdown.is_set():
            now_mono = time.monotonic()
            to_flush = []
            to_drop  = []

            drop_info: list[tuple[int, float, list]] = []
            flush_info: list[tuple[int, float, list]] = []

            with self._lock:
                for bucket, entry in list(self._buffer.items()):
                    age_arrival = now_mono - entry["first_arrival"]

                    if age_arrival >= stale_s:
                        # Pathological case: bucket has somehow lived past
                        # BUFFER_DURATION_S. Drop to bound memory; the lost
                        # frame is preferable to unbounded growth.
                        to_drop.append(bucket)
                        drop_info.append((
                            bucket, age_arrival * 1000.0,
                            sorted(entry["frames"].keys()),
                        ))
                    # Arrival-time fallback for when event-time does not advance
                    # (entire stream silent, broker stall, sparse sensors). Without
                    # this, buckets in those cases would never flush at all.
                    elif age_arrival * 1000.0 >= self._flush_timeout_ms:
                        to_flush.append(bucket)
                        flush_info.append((
                            bucket, age_arrival * 1000.0,
                            sorted(entry["frames"].keys()),
                        ))

                # Only remove stale entries here; flush entries are popped below
                for bucket in to_drop:
                    self._buffer.pop(bucket, None)
                    self._mark_bucket_closed(bucket, now_mono)

                # Bound _closed_buckets memory: drop entries older than the
                # retention window. The bucket key itself is no longer reachable
                # in event time by that point, so further rejection is unneeded.
                if self._closed_buckets:
                    stale_closed = [
                        b for b, closed_at in self._closed_buckets.items()
                        if (now_mono - closed_at) >= closed_retention_s
                    ]
                    for b in stale_closed:
                        self._closed_buckets.pop(b, None)
                    if stale_closed:
                        logger.debug(
                            "[DBG] PRUNE       %d closed buckets pruned (retention=%.0fms)",
                            len(stale_closed), closed_retention_s * 1000,
                        )

            for b, age_ms, sensors in drop_info:
                logger.debug(
                    "[DBG] DROP-STALE  bucket=%d age=%.0fms sensors=%s "
                    "(exceeded BUFFER_DURATION_S=%.1fs)",
                    b, age_ms, sensors, stale_s,
                )

            for b, age_ms, sensors in flush_info:
                logger.debug(
                    "[DBG] TRIG-SWEEP  bucket=%d age=%.0fms sensors=%s "
                    "(missing %d)",
                    b, age_ms, sensors,
                    MAX_EXPECTED_SENSORS - len(sensors),
                )

            for bucket in to_flush:
                self._flush_frame(bucket)

            self._shutdown.wait(SWEEP_INTERVAL_S)

    # --- Broker abstractions ---

    def _publish(self, data: bytes):
        if BROKER_TYPE == "redis":
            self._redis.xadd(FUSED_TOPIC, {"key": FUSED_SENSOR_ID, "value": data, "headers": "{}"})
        else:
            self._producer.produce(FUSED_TOPIC, value=data)
            self._producer.poll(0)

    def _run_kafka(self):
        from confluent_kafka import Consumer, Producer, KafkaException

        self._producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "linger.ms":         5,
            "acks":              "1",
        })
        group_id = f"mdx-measurement-fusion-{uuid.uuid4().hex[:8]}"
        consumer = Consumer({
            "bootstrap.servers":  KAFKA_BOOTSTRAP,
            "group.id":           group_id,
            "auto.offset.reset":  "latest",
            "enable.auto.commit": True,
        })

        logger.info(
            "Measurement fusion (kafka): %s → %s  "
            "(timeout=%.0fms, max_sensors=%d, buffer=%.1fs, bucket=%.0fms)",
            RAW_TOPIC, FUSED_TOPIC, self._flush_timeout_ms, MAX_EXPECTED_SENSORS, BUFFER_DURATION_S, BUCKET_MS,
        )

        try:
            consumer.subscribe([RAW_TOPIC])
            logger.info("Subscribed to %s @ %s", RAW_TOPIC, KAFKA_BOOTSTRAP)
            with open("/tmp/fusion_ready", "w") as _f:
                _f.write("ready\n")

            while not self._shutdown.is_set():
                msg = consumer.poll(timeout=CONSUMER_POLL_MS / 1000.0)
                if msg is None:
                    continue
                if msg.error():
                    logger.warning("Consumer error: %s", msg.error())
                    continue
                try:
                    frame = schema_pb2.Frame()
                    frame.ParseFromString(msg.value())
                    self._received += 1
                    self._buffer_frame(frame)
                except Exception as exc:
                    logger.error("Failed to parse message: %s", exc)

        except KafkaException as exc:
            logger.error("Kafka exception: %s", exc)
        finally:
            self._shutdown.set()
            try:
                consumer.close()
            except Exception:
                pass
            try:
                self._producer.flush(timeout=5)
            except Exception:
                pass

    def _run_redis(self):
        import redis as redis_lib

        self._redis = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT)
        self._redis.ping()

        logger.info(
            "Measurement fusion (redis): %s → %s  "
            "(timeout=%.0fms, max_sensors=%d, buffer=%.1fs, bucket=%.0fms)",
            RAW_TOPIC, FUSED_TOPIC, self._flush_timeout_ms, MAX_EXPECTED_SENSORS, BUFFER_DURATION_S, BUCKET_MS,
        )

        # Start from the tip of the stream (only new messages)
        last_id = "$"
        with open("/tmp/fusion_ready", "w") as _f:
            _f.write("ready\n")
        logger.info("Subscribed to Redis stream %s @ %s:%d", RAW_TOPIC, REDIS_HOST, REDIS_PORT)

        while not self._shutdown.is_set():
            try:
                results = self._redis.xread(
                    {RAW_TOPIC: last_id},
                    count=100,
                    block=int(CONSUMER_POLL_MS),
                )
                if not results:
                    continue
                for _stream, messages in results:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        data = fields.get(b"value")
                        if data is None:
                            continue
                        try:
                            frame = schema_pb2.Frame()
                            frame.ParseFromString(data)
                            self._received += 1
                            self._buffer_frame(frame)
                        except Exception as exc:
                            logger.error("Failed to parse Redis message: %s", exc)
            except Exception as exc:
                if not self._shutdown.is_set():
                    logger.error("Redis read error: %s", exc)
                    time.sleep(1.0)

    # --- Main loop ---

    def run(self):
        def _on_signal(signum, _frame):
            logger.info("Received signal %d — shutting down", signum)
            self._shutdown.set()

        signal.signal(signal.SIGINT,  _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)

        sweep_thread = threading.Thread(target=self._sweep_loop, daemon=True, name="sweep")
        sweep_thread.start()

        try:
            if BROKER_TYPE == "redis":
                self._run_redis()
            else:
                self._run_kafka()
        finally:
            self._shutdown.set()
            sweep_thread.join(timeout=2.0)
            logger.info(
                "Shutdown complete. Received=%d  Published=%d  LateDropped=%d",
                self._received, self._published, self._late_dropped,
            )


if __name__ == "__main__":
    MeasurementFusionService().run()
