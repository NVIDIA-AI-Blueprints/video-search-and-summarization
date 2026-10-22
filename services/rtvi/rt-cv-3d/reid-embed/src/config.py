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

"""Centralized environment-variable configuration for the ReID service.

This module is imported by ``main.py`` *after* the root logger has been
configured. Every ``logger.info`` line below runs at import time and
announces the resolved configuration to whatever handler the root logger
has installed (a plain ``StreamHandler`` or the ``LOG_ASYNC``
``QueueHandler``).

``LOG_LEVEL`` and ``LOG_ASYNC`` deliberately stay in ``main.py`` because
they must be read *before* the logger is configured -- they can't live
here without an awkward chicken-and-egg setup.
"""

from __future__ import annotations

import logging
import os


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message broker selection
# ---------------------------------------------------------------------------
# Which broker this service talks to, in BOTH directions: the ingest
# subprocess consumes from it and the secondary-embedding generator publishes
# enriched frames back to it. Named after DeepStream's own vocabulary -- its
# adapter config section is `[message-broker]` -- so this lines up with
# whatever `msg-broker-proto-lib` the pipeline is configured with.
#
# Everything between the two ends (parsing, batching, the writer pool, Milvus
# inserts, the tracking store) is identical either way; only the read loop and
# the publish call differ.
MESSAGE_BROKER = os.getenv("MESSAGE_BROKER", "kafka").strip().lower()
_VALID_MESSAGE_BROKERS = ("kafka", "redis")
if MESSAGE_BROKER not in _VALID_MESSAGE_BROKERS:
    raise ValueError(f"Invalid MESSAGE_BROKER={MESSAGE_BROKER!r}; " f"must be one of {_VALID_MESSAGE_BROKERS}")

logger.info("=" * 50)
logger.info(f"MESSAGE_BROKER: {MESSAGE_BROKER}")
logger.info("=" * 50)


# ---------------------------------------------------------------------------
# Kafka consumer
# ---------------------------------------------------------------------------
KAFKA_CONSUMER_SERVER = os.getenv("KAFKA_CONSUMER_SERVER", "localhost:9092")
KAFKA_CONSUMER_TOPIC = os.getenv("KAFKA_CONSUMER_TOPIC", "mv3dt")
KAFKA_CONSUMER_GROUP_ID = os.getenv("KAFKA_CONSUMER_GROUP_ID", "vector-search-group")
# Pause between failed first-connect attempts. The kafka_consumer child stays
# alive and does not set its ready event until KafkaConsumer() succeeds, so
# /health/ready stays 503 until the broker is reachable.
KAFKA_CONSUMER_CONNECT_RETRY_SEC = float(os.getenv("KAFKA_CONSUMER_CONNECT_RETRY_SEC", "5"))
if KAFKA_CONSUMER_CONNECT_RETRY_SEC <= 0:
    raise ValueError(f"Invalid KAFKA_CONSUMER_CONNECT_RETRY_SEC={KAFKA_CONSUMER_CONNECT_RETRY_SEC!r}; " f"must be > 0")

logger.info("=" * 50)
logger.info("KAFKA CONSUMER CONFIGURATION")
logger.info(f"KAFKA_CONSUMER_SERVER: {KAFKA_CONSUMER_SERVER}")
logger.info(f"KAFKA_CONSUMER_TOPIC: {KAFKA_CONSUMER_TOPIC}")
logger.info(f"KAFKA_CONSUMER_GROUP_ID: {KAFKA_CONSUMER_GROUP_ID}")
logger.info(f"KAFKA_CONSUMER_CONNECT_RETRY_SEC: {KAFKA_CONSUMER_CONNECT_RETRY_SEC}")
logger.info("=" * 50)


# ---------------------------------------------------------------------------
# Redis Streams consumer (MESSAGE_BROKER=redis)
# ---------------------------------------------------------------------------
# DeepStream's Redis protocol adapter (libnvds_redis_proto.so) publishes with
#   XADD <stream> MAXLEN ~ <streamsize> * <payloadkey> <payload>
# so the settings below must line up with its cfg_redis.txt:
#   REDIS_CONSUMER_TOPIC       <-> nvmsgbroker's `topic` property, which on
#                                  the Redis path holds the stream name
#   REDIS_CONSUMER_PAYLOAD_KEY <-> `payloadkey` (adapter convention: metadata)
# REDIS_CONSUMER_GROUP is ours alone -- deliberately NOT the adapter's
# `consumergroup` default (`mygroup`), so we never share a group with anything
# DeepStream creates on its own subscribe path.
#
# REDIS_CONSUMER_SERVER is a 'host:port' pair for symmetry with
# KAFKA_CONSUMER_SERVER. kafka-python accepts that string as-is, redis-py
# wants host= and port= separately, so it is split here rather than letting
# the client library's signature dictate the config shape.
REDIS_CONSUMER_SERVER = os.getenv("REDIS_CONSUMER_SERVER", "localhost:6379")


def split_host_port(value: str, var_name: str) -> tuple:
    """Split a ``host:port`` pair, tolerating bracketed IPv6 (``[::1]:6379``).

    Shared by the Redis consumer settings below and by the Redis producer
    settings in ``secondary_embedding_generation``; ``var_name`` only shapes
    the error message.
    """
    host, sep, port = value.strip().rpartition(":")
    if not sep or not host:
        raise ValueError(f"Invalid {var_name}={value!r}; expected 'host:port' (e.g. 'localhost:6379')")
    # rpartition already isolated the port, so brackets are only decoration.
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return host, int(port)
    except ValueError:
        raise ValueError(f"Invalid port in {var_name}={value!r}; {port!r} is not an integer") from None


# Derived from REDIS_CONSUMER_SERVER; these are what get handed to redis-py.
REDIS_CONSUMER_HOST, REDIS_CONSUMER_PORT = split_host_port(REDIS_CONSUMER_SERVER, "REDIS_CONSUMER_SERVER")
REDIS_CONSUMER_PASSWORD = os.getenv("REDIS_CONSUMER_PASSWORD") or None
REDIS_CONSUMER_TOPIC = os.getenv("REDIS_CONSUMER_TOPIC", "mv3dt")
REDIS_CONSUMER_GROUP = os.getenv("REDIS_CONSUMER_GROUP", "reid-service-group")
REDIS_CONSUMER_NAME = os.getenv("REDIS_CONSUMER_NAME", "reid-consumer-1")
REDIS_CONSUMER_PAYLOAD_KEY = os.getenv("REDIS_CONSUMER_PAYLOAD_KEY", "metadata")
# Max entries returned per XREADGROUP call.
REDIS_CONSUMER_BATCH_COUNT = int(os.getenv("REDIS_CONSUMER_BATCH_COUNT", "64"))
# XREADGROUP BLOCK timeout. Bounds how long a read parks before the loop gets
# control back to log lag / notice shutdown; it is not a latency floor.
REDIS_CONSUMER_BLOCK_MS = int(os.getenv("REDIS_CONSUMER_BLOCK_MS", "2000"))
# Where a freshly created consumer group starts. '$' = only entries published
# after startup (matches the Kafka path's auto_offset_reset='latest'); '0'
# replays everything still in the stream. Ignored once the group exists.
REDIS_CONSUMER_START_ID = os.getenv("REDIS_CONSUMER_START_ID", "$")
REDIS_CONSUMER_CONNECT_RETRY_SEC = float(os.getenv("REDIS_CONSUMER_CONNECT_RETRY_SEC", "5"))
if REDIS_CONSUMER_CONNECT_RETRY_SEC <= 0:
    raise ValueError(f"Invalid REDIS_CONSUMER_CONNECT_RETRY_SEC={REDIS_CONSUMER_CONNECT_RETRY_SEC!r}; " f"must be > 0")
# How often to log consumer-group lag. DeepStream trims with MAXLEN ~ on every
# publish, so falling further behind than its `streamsize` silently drops
# entries -- this log is the only signal that it happened. 0 disables.
REDIS_CONSUMER_LAG_LOG_INTERVAL_SEC = float(os.getenv("REDIS_CONSUMER_LAG_LOG_INTERVAL_SEC", "30"))

if MESSAGE_BROKER == "redis":
    logger.info("=" * 50)
    logger.info("REDIS CONSUMER CONFIGURATION")
    logger.info(f"REDIS_CONSUMER_SERVER: {REDIS_CONSUMER_SERVER}")
    logger.info(f"REDIS_CONSUMER_PASSWORD: {'<set>' if REDIS_CONSUMER_PASSWORD else '<unset>'}")
    logger.info(f"REDIS_CONSUMER_TOPIC: {REDIS_CONSUMER_TOPIC}")
    logger.info(f"REDIS_CONSUMER_GROUP: {REDIS_CONSUMER_GROUP}")
    logger.info(f"REDIS_CONSUMER_NAME: {REDIS_CONSUMER_NAME}")
    logger.info(f"REDIS_CONSUMER_PAYLOAD_KEY: {REDIS_CONSUMER_PAYLOAD_KEY}")
    logger.info(f"REDIS_CONSUMER_BATCH_COUNT: {REDIS_CONSUMER_BATCH_COUNT}")
    logger.info(f"REDIS_CONSUMER_BLOCK_MS: {REDIS_CONSUMER_BLOCK_MS}")
    logger.info(f"REDIS_CONSUMER_START_ID: {REDIS_CONSUMER_START_ID}")
    logger.info(f"REDIS_CONSUMER_CONNECT_RETRY_SEC: {REDIS_CONSUMER_CONNECT_RETRY_SEC}")
    logger.info(f"REDIS_CONSUMER_LAG_LOG_INTERVAL_SEC: {REDIS_CONSUMER_LAG_LOG_INTERVAL_SEC}")
    logger.info("=" * 50)


# ---------------------------------------------------------------------------
# Milvus connections + collections
# ---------------------------------------------------------------------------
COLLECTION_NAME = "embeddings"
COMPRESSED_COLLECTION_NAME = "compressed_embeddings"
DIMENSION = int(os.getenv("DIMENSION", "1280"))
COLLECTION_MAX_ENTITIES = 16384
ITERATOR_BATCH_SIZE = COLLECTION_MAX_ENTITIES
CUSTOM_GROUP_SEARCH = os.getenv("CUSTOM_GROUP_SEARCH", "true").lower() in ("true", "1", "yes")

MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", "19530"))
MILVUS_COMPRESSED_HOST = os.getenv("MILVUS_COMPRESSED_HOST", MILVUS_HOST)
MILVUS_COMPRESSED_PORT = int(os.getenv("MILVUS_COMPRESSED_PORT", MILVUS_PORT))

CLEAN_RESTART = os.getenv("CLEAN_RESTART", "true").lower() in ("true", "1", "yes")

logger.info(f"MILVUS_HOST: {MILVUS_HOST}")
logger.info(f"MILVUS_PORT: {MILVUS_PORT}")
logger.info(f"MILVUS_COMPRESSED_HOST: {MILVUS_COMPRESSED_HOST}")
logger.info(f"MILVUS_COMPRESSED_PORT: {MILVUS_COMPRESSED_PORT}")
logger.info(f"CLEAN_RESTART: {CLEAN_RESTART} (set CLEAN_RESTART=false to keep existing data)")


# ---------------------------------------------------------------------------
# Milvus index / search parameters
# ---------------------------------------------------------------------------
# MILVUS_INDEX_TYPE selects how vectors are indexed in BOTH the main and the
# compressed collections. Currently supported values:
#
#   FLAT      - Brute-force exact KNN. Recall = 1.0 but scales poorly under
#               concurrency because every query scans every vector.
#   IVF_FLAT  - Inverted-file with full vectors per cell. ~10-50x faster than
#               FLAT under load at ~99% recall when nlist/nprobe are tuned.
#
# To add a new index type (HNSW, IVF_PQ, GPU_CAGRA, ...) extend the two
# _resolve_* helpers below; everything else picks the new params up via
# the MILVUS_INDEX_PARAMS / MILVUS_SEARCH_PARAMS module globals that
# main.py imports via `from config import *`.
#
# Note: Milvus does NOT auto-rebuild an existing index when this changes.
# Set CLEAN_RESTART=true to drop and recreate the collections with the new
# index, or drop the index manually before restart.
MILVUS_INDEX_TYPE = os.getenv("MILVUS_INDEX_TYPE", "FLAT").upper()
MILVUS_METRIC_TYPE = os.getenv("MILVUS_METRIC_TYPE", "L2").upper()

# IVF-family parameters (ignored when MILVUS_INDEX_TYPE doesn't use them).
#   MILVUS_IVF_NLIST  - number of Voronoi cells. Build-time only. Rule of
#                       thumb: 4*sqrt(N) with a floor of ~128 for small
#                       collections. Re-creating the index is the only way
#                       to change this after build.
#   MILVUS_IVF_NPROBE - cells to scan per query. Search-time. Higher = better
#                       recall, more CPU. ~3% of nlist is a sensible start;
#                       lean higher when top_k requests grow because the
#                       custom_group_search path fetches top_k*top_k*2 hits.
MILVUS_IVF_NLIST = int(os.getenv("MILVUS_IVF_NLIST", "1024"))
MILVUS_IVF_NPROBE = int(os.getenv("MILVUS_IVF_NPROBE", "32"))

# Number of search-worker subprocesses spawned PER collection (main +
# compressed). The MAX_CONCURRENT_SEARCHES pipe pool is partitioned as
# evenly as possible across these subprocesses, so each one runs roughly
# MAX_CONCURRENT_SEARCHES / N pipe-handler threads.
#
# Why this exists: all pipe-handler threads inside one subprocess share a
# single Python GIL, so the per-call Python overhead (pickle, pymilvus
# wrappers, hit-dict construction) serializes regardless of how many
# threads or gRPC channels you add. Splitting across N independent
# interpreters gives N concurrent Python execution streams.
#
# Each subprocess opens exactly one persistent gRPC channel to Milvus,
# so total Milvus connections per collection = N.
#
# N=1 reproduces the pre-change single-subprocess behaviour.
MILVUS_SEARCH_WORKER_PROCESSES = int(os.getenv("MILVUS_SEARCH_WORKER_PROCESSES", "4"))


def _resolve_milvus_index_params() -> dict:
    """Build the dict passed to ``Collection.create_index`` for both collections."""
    base = {"metric_type": MILVUS_METRIC_TYPE, "index_type": MILVUS_INDEX_TYPE}
    if MILVUS_INDEX_TYPE == "FLAT":
        return base
    if MILVUS_INDEX_TYPE == "IVF_FLAT":
        return {**base, "params": {"nlist": MILVUS_IVF_NLIST}}
    raise ValueError(f"Unsupported MILVUS_INDEX_TYPE={MILVUS_INDEX_TYPE!r}. " "Supported values: FLAT, IVF_FLAT.")


def _resolve_milvus_search_params() -> dict:
    """Build the dict passed as ``coll.search(param=...)``."""
    base = {"metric_type": MILVUS_METRIC_TYPE}
    if MILVUS_INDEX_TYPE == "FLAT":
        return {**base, "params": {}}
    if MILVUS_INDEX_TYPE == "IVF_FLAT":
        return {**base, "params": {"nprobe": MILVUS_IVF_NPROBE}}
    raise ValueError(f"Unsupported MILVUS_INDEX_TYPE={MILVUS_INDEX_TYPE!r}. " "Supported values: FLAT, IVF_FLAT.")


MILVUS_INDEX_PARAMS = _resolve_milvus_index_params()
MILVUS_SEARCH_PARAMS = _resolve_milvus_search_params()

logger.info(
    f"MILVUS_INDEX_TYPE: {MILVUS_INDEX_TYPE} (metric={MILVUS_METRIC_TYPE}) "
    f"index_params={MILVUS_INDEX_PARAMS} search_params={MILVUS_SEARCH_PARAMS}"
)
logger.info(f"MILVUS_SEARCH_WORKER_PROCESSES: {MILVUS_SEARCH_WORKER_PROCESSES}")


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------
OUTLIER_STD_DEV_THRESHOLD = 3.0
COSINE_SIMILARITY_THRESHOLD = 0.9995
MIN_COMPRESSED_SAMPLES = 3
MAX_COMPRESSED_SAMPLES = int(os.getenv("MAX_COMPRESSED_SAMPLES", "7"))
# Recompression only folds a representative into a group when its mean
# distance to that group's members is at most this, as squared L2 over
# unit-norm embeddings -- the same scale Milvus reports, so 0.3 means a cosine
# similarity of 0.85 (D^2 = 2 - 2S). A representative no group will accept is
# kept instead of merged, so a strict sample budget can never fuse two
# unrelated appearance modes into a summary that describes neither. Retained
# rows per object are therefore bounded by how many distinct modes the object
# actually has, not by MAX_COMPRESSED_SAMPLES. Measured on this deployment's
# embeddings, 0.3 leaves 71% of same-identity pairs mergeable while admitting
# only 8% of cross-identity pairs; loosening to 0.4 gives 84% / 14% and packs
# each object into fewer rows. The knob is monotonic: lower is stricter and 0
# merges only exact duplicates. Set it to 4.0 -- the largest squared L2 between two
# unit vectors -- to switch the gate off and restore the pre-gate behaviour of
# assigning each representative to its nearest sample; that bound makes the
# off switch exact rather than a magic value. In practice the gate goes inert
# earlier, because these embeddings occupy a narrow cone: across 2450 objects
# no pair of representatives exceeded 0.95 and cosine similarity never fell
# below 0.52, so anything from 1.0 up already admits every merge. The useful
# range is therefore roughly 0.2 to 0.6; re-measure it if the ReID model
# changes, since the cone is a property of the model.
COMPRESSION_MERGE_MAX_DISTANCE = float(os.getenv("COMPRESSION_MERGE_MAX_DISTANCE", "0.15"))
# Compression triggers. Current maintenance-loop behavior is:
#   * COMPRESSION_INTERVAL_SEC > 0: time-based compression fires after that
#     many seconds once collection_count >= COMPRESSION_MIN_BATCH.
#   * COMPRESSION_INTERVAL_SEC == 0: size-based compression fires when
#     collection_count >= COMPRESSION_BATCH_LIMIT.
# Set COMPRESSION_INTERVAL_SEC=0 to disable the time trigger and rely on size.
COMPRESSION_BATCH_LIMIT = int(os.getenv("COMPRESSION_BATCH_LIMIT", "5000"))
COMPRESSION_INTERVAL_SEC = float(os.getenv("COMPRESSION_INTERVAL_SEC", "1"))
COMPRESSION_MIN_BATCH = int(os.getenv("COMPRESSION_MIN_BATCH", "100"))

# ---------------------------------------------------------------------------
# Online clustering
# ---------------------------------------------------------------------------
# Maintenance loop fires clustering once the un-clustered row count reaches
# TRIGGER_BATCH_LIMIT (the window size; set to the notebook's ONLINE_BATCH_SIZE,
# e.g. 100, in deterministic mode). FIRST_TRIGGER_MIN_BATCH gates the very first
# tick so clustering doesn't run on a near-empty collection.
TRIGGER_BATCH_LIMIT = int(os.getenv("TRIGGER_BATCH_LIMIT", "5000"))
FIRST_TRIGGER_MIN_BATCH = int(os.getenv("FIRST_TRIGGER_MIN_BATCH", "100"))
# Time-based trigger (shared by clustering+compression when clustering is on):
# fire every TRIGGER_INTERVAL_SEC seconds once collection_count >= TRIGGER_MIN_BATCH.
# Set TRIGGER_INTERVAL_SEC=0 to disable time-based firing and rely purely on size.
TRIGGER_INTERVAL_SEC = float(os.getenv("TRIGGER_INTERVAL_SEC", "1"))
TRIGGER_MIN_BATCH = int(os.getenv("TRIGGER_MIN_BATCH", "100"))
# Deterministic windowing: partition by DATA (frame watermark) rather than
# wall-clock / Milvus insert timing. Only window rows settled below
# (frontier_frame - CLUSTER_WATERMARK_LAG); require a full TRIGGER_BATCH_LIMIT
# window before firing. Repeats within a tick until no full window remains.
CLUSTER_DETERMINISTIC = os.getenv("CLUSTER_DETERMINISTIC", "false").lower() in ("true", "1", "yes")
CLUSTER_WATERMARK_LAG = int(os.getenv("CLUSTER_WATERMARK_LAG", "64"))
CLUSTER_MAX_WINDOWS_PER_TICK = int(os.getenv("CLUSTER_MAX_WINDOWS_PER_TICK", "100000"))
# Tracking-coverage gate for a clustering batch. If more than this FRACTION of
# the batch's embeddings can't be joined to a tracking-DB row, the batch waits
# for the tracking DB to catch up: it re-checks up to CLUSTERING_UNTRACKED_MAX_RETRIES
# times, sleeping CLUSTERING_UNTRACKED_RETRY_DELAY seconds between attempts, and
# is skipped (with a warning) only if coverage is still short after that.
CLUSTERING_MAX_UNTRACKED_EMBEDDINGS_FRACTION = float(os.getenv("CLUSTERING_MAX_UNTRACKED_EMBEDDINGS_FRACTION", "0.1"))
CLUSTERING_UNTRACKED_MAX_RETRIES = int(os.getenv("CLUSTERING_UNTRACKED_MAX_RETRIES", "0"))
CLUSTERING_UNTRACKED_RETRY_DELAY = float(os.getenv("CLUSTERING_UNTRACKED_RETRY_DELAY", "1"))
# Cluster DB snapshot cadence: 'off' | 'every_batch' | 'final_only'. Snapshots
# dump main / tracking / compressed state as JSON under
# CLUSTER_ASSIGNMENTS_DIR/batch_NNN/.
CLUSTER_DB_SNAPSHOT_MODE = os.getenv("CLUSTER_DB_SNAPSHOT_MODE", "final_only").lower()
CLUSTER_DB_SNAPSHOT_IDLE_TICKS = int(os.getenv("CLUSTER_DB_SNAPSHOT_IDLE_TICKS", "5"))
if CLUSTER_DB_SNAPSHOT_MODE not in ("off", "every_batch", "final_only"):
    CLUSTER_DB_SNAPSHOT_MODE = "off"
ENABLE_CLUSTER_DB_SNAPSHOTS = CLUSTER_DB_SNAPSHOT_MODE != "off"
CLUSTER_ASSIGNMENTS_DIR = os.getenv("CLUSTER_ASSIGNMENTS_DIR", "/app/cluster_assignments")
CLUSTER_ID_START = int(os.getenv("CLUSTER_ID_START", "50000"))
LOAD_DELETE_BATCH_SIZE = int(os.getenv("LOAD_DELETE_BATCH_SIZE", "2000"))

# Consistency level used by ALL maintenance-worker read queries against
# Milvus (count(*), query_iterator, and the compressed-collection lookup
# inside _perform_compression).
# Valid pymilvus values: Strong, Bounded, Session, Eventually.
_VALID_CONSISTENCY_LEVELS = ("Strong", "Bounded", "Session", "Eventually")
MAINTENANCE_READ_CONSISTENCY_LEVEL = os.getenv("MAINTENANCE_READ_CONSISTENCY_LEVEL", "Eventually")
if MAINTENANCE_READ_CONSISTENCY_LEVEL not in _VALID_CONSISTENCY_LEVELS:
    raise ValueError(
        f"Invalid MAINTENANCE_READ_CONSISTENCY_LEVEL="
        f"{MAINTENANCE_READ_CONSISTENCY_LEVEL!r}; "
        f"must be one of {_VALID_CONSISTENCY_LEVELS}"
    )
logger.info(
    f"MAINTENANCE_READ_CONSISTENCY_LEVEL: {MAINTENANCE_READ_CONSISTENCY_LEVEL} "
    f"(applies to maintenance-worker reads only; /search uses its own default)"
)


# ---------------------------------------------------------------------------
# Insert / batching
# ---------------------------------------------------------------------------
INSERT_BATCH_SIZE = int(os.getenv("INSERT_BATCH_SIZE", "256"))
INSERT_BATCH_TIMEOUT_SEC = float(os.getenv("INSERT_BATCH_TIMEOUT_SEC", "1.0"))
INSERT_EMBEDDINGS_IN_BATCHES = os.getenv("INSERT_EMBEDDINGS_IN_BATCHES", "true").lower() in ("true", "1", "yes")

# Background writer pool used by the Kafka consumer (see ingest.KafkaIngestor).
# The consumer loop hands work off via a bounded queue; N worker threads
# drain it and perform the actual Milvus gRPC inserts. Decouples Kafka
# reading from Milvus writing so the consumer never blocks on insert latency.
#
#   INSERT_WORKER_COUNT       -- worker threads draining the queue (gRPC
#                                releases the GIL, so they overlap for real).
#                                Raise if the qsize logged by the workers
#                                stays consistently > 0; lower if Milvus is
#                                idle and you want to free a thread slot.
#   INSERT_QUEUE_MAX          -- backpressure ceiling. When full, producers
#                                block up to INSERT_QUEUE_PUT_TIMEOUT_SEC
#                                and then drop with a loud warning (better
#                                than OOM).
#   INSERT_QUEUE_PUT_TIMEOUT_SEC -- how long the consumer blocks waiting for
#                                queue space before dropping a batch.
INSERT_WORKER_COUNT = int(os.getenv("INSERT_WORKER_COUNT", "2"))
INSERT_QUEUE_MAX = int(os.getenv("INSERT_QUEUE_MAX", "64"))
INSERT_QUEUE_PUT_TIMEOUT_SEC = float(os.getenv("INSERT_QUEUE_PUT_TIMEOUT_SEC", "5.0"))

logger.info(
    f"INSERT pipeline: batches={INSERT_EMBEDDINGS_IN_BATCHES} "
    f"size={INSERT_BATCH_SIZE} timeout_sec={INSERT_BATCH_TIMEOUT_SEC} "
    f"workers={INSERT_WORKER_COUNT} queue_max={INSERT_QUEUE_MAX} "
    f"put_timeout_sec={INSERT_QUEUE_PUT_TIMEOUT_SEC}"
)


# ---------------------------------------------------------------------------
# Feature toggles
# ---------------------------------------------------------------------------
ENABLE_COMPRESSION = os.getenv("ENABLE_COMPRESSION", "false").lower() in ("true", "1", "yes")
ENABLE_CLUSTERING = os.getenv("ENABLE_CLUSTERING", "false").lower() in ("true", "1", "yes")
ENABLE_SECONDARY_EMBEDDING = os.getenv("ENABLE_SECONDARY_EMBEDDING", "false").lower() in ("true", "1", "yes")
# Secondary embedding requires compression; coerce ENABLE_COMPRESSION on.
if ENABLE_SECONDARY_EMBEDDING and not ENABLE_COMPRESSION:
    ENABLE_COMPRESSION = True
    logger.info("Secondary embedding is enabled, compression will be enabled")
SECONDARY_EMBEDDING_MODEL = os.getenv("SECONDARY_EMBEDDING_MODEL", "siglip2").lower()
SECONDARY_EMBEDDING_ONNX_MODEL_PATH = os.getenv("SECONDARY_EMBEDDING_ONNX_MODEL_PATH", None)

# ENABLE_CLT_ESTIMATION changes what a compressed-collection search hit
# contributes to a match. Off, the hit contributes one distance (to its
# representative) carrying the whole group's weight, which the scoring code
# then discounts as log2(2*weight). On, the hit's stored moments are expanded
# via cluster_stats.clt into estimated distances to the nearest vectors the
# group actually holds, each counted as one vector.
#
# Only compressed rows carry moments, so this requires ENABLE_COMPRESSION,
# and it assumes L2 over unit-norm embeddings (the similarity-to-distance
# conversion the estimator inverts).
#
# Cost: enabling it adds `mean` to the compressed search's output fields, one
# vector-width column per hit. The column is stored base64-encoded in a VARCHAR
# so that cost is the bytes themselves rather than pymilvus building a Python
# list of DIMENSION floats per row, which is what it was under ARRAY<FLOAT>.
ENABLE_CLT_ESTIMATION = os.getenv("ENABLE_CLT_ESTIMATION", "true").lower() in ("true", "1", "yes")
if ENABLE_CLT_ESTIMATION and not ENABLE_COMPRESSION:
    ENABLE_CLT_ESTIMATION = False
    logger.warning("ENABLE_CLT_ESTIMATION requires ENABLE_COMPRESSION; disabling it")
if ENABLE_CLT_ESTIMATION and MILVUS_METRIC_TYPE != "L2":
    ENABLE_CLT_ESTIMATION = False
    logger.warning(f"ENABLE_CLT_ESTIMATION requires MILVUS_METRIC_TYPE=L2, " f"got {MILVUS_METRIC_TYPE}; disabling it")

# Similarity spread per unit of a cluster's total variance: the estimator reads
# a row's spread out of its mean and scales it by this (cluster_stats.clt).
#
# The default sits below the 1/3 that both the eigenvalue geometry
# (1 / sqrt(r), r ~ 6-9) and a direct fit point at: pooling keeps the k smallest
# estimates, so it selects on the low tail and too large a scale costs more than
# too small a one. It describes the embedding model's geometry rather than the
# deployment, so re-fit it when changing ReID model -- cluster_stats/clt.py's
# docstring says how.
CLT_SIGMA_SCALE = float(os.getenv("CLT_SIGMA_SCALE", "").strip() or 1.0 / 4.5)


# ---------------------------------------------------------------------------
# Search-side concurrency
# ---------------------------------------------------------------------------
MAX_CONCURRENT_SEARCHES = int(os.getenv("MAX_CONCURRENT_SEARCHES", "30"))


# ---------------------------------------------------------------------------
# HTTP server (uvicorn bind address)
# ---------------------------------------------------------------------------
# REID_SERVICE_HOST selects which interfaces uvicorn listens on, not the
# address clients use: 0.0.0.0 accepts traffic on every interface, 127.0.0.1
# restricts it to processes on the same host. Under the bridge-network compose
# file, 127.0.0.1 binds inside the container's namespace and makes the
# published port mapping unreachable, so keep 0.0.0.0 there.
REID_SERVICE_HOST = os.getenv("REID_SERVICE_HOST", "0.0.0.0")
REID_SERVICE_PORT = int(os.getenv("REID_SERVICE_PORT", "8000"))
logger.info(f"REID_SERVICE_HOST: {REID_SERVICE_HOST}")
logger.info(f"REID_SERVICE_PORT: {REID_SERVICE_PORT}")


# ---------------------------------------------------------------------------
# Debug endpoints
# ---------------------------------------------------------------------------
ENABLE_DEBUG_ENDPOINTS = os.getenv("ENABLE_DEBUG_ENDPOINTS", "false").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Kafka payload schema (options: deepstream, minimal, protobuf)
# ---------------------------------------------------------------------------
DEEPSTREAM_SCHEMA = os.getenv("DEEPSTREAM_SCHEMA", "protobuf")
logger.info(f"Using deepstream schema type: {DEEPSTREAM_SCHEMA}")


# ---------------------------------------------------------------------------
# Maintenance worker cadence
# ---------------------------------------------------------------------------
CLUSTER_STATS_INTERVAL = int(os.getenv("CLUSTER_STATS_INTERVAL", "20"))
logger.info(f"CLUSTER_STATS_INTERVAL: {CLUSTER_STATS_INTERVAL}")

# Per-process thread cap applied by ``_maintenance_set_max_threads`` to BLAS
# backends (OpenBLAS, MKL), FAISS, PyTorch, NumExpr, and the OMP_NUM_THREADS
# family of env vars. Keeps the maintenance subprocess from fanning numeric
# kernels across every core and starving the search workers.
MAINTENANCE_MAX_THREADS = max(1, int(os.getenv("MAINTENANCE_MAX_THREADS", "4")))
logger.info(f"MAINTENANCE_MAX_THREADS: {MAINTENANCE_MAX_THREADS}")

# Parallel object compression inside ``_perform_compression_impl``. Pool size;
# each worker gets max(1, MAINTENANCE_MAX_THREADS // pool_workers) BLAS/FAISS
# threads so total native parallelism stays near MAINTENANCE_MAX_THREADS.
# Set to 1 to keep serial per-object processing in the parent process.
MAINTENANCE_COMPRESSION_POOL_WORKERS = max(
    1, int(os.getenv("MAINTENANCE_COMPRESSION_POOL_WORKERS", str(MAINTENANCE_MAX_THREADS)))
)
logger.info(f"MAINTENANCE_COMPRESSION_POOL_WORKERS: {MAINTENANCE_COMPRESSION_POOL_WORKERS}")


# ---------------------------------------------------------------------------
# Profiling
# ---------------------------------------------------------------------------
# this env var only
# controls how often the running average is emitted to the log.
PROFILE_LOG_INTERVAL_SEC = float(os.getenv("PROFILE_LOG_INTERVAL_SEC", "1"))
logger.info(f"PROFILE_LOG_INTERVAL_SEC: {PROFILE_LOG_INTERVAL_SEC}")


# ---------------------------------------------------------------------------
# Tracking DB
# ---------------------------------------------------------------------------
TRACKING_DB_STORE_ALL_ENTRIES = os.getenv("TRACKING_DB_STORE_ALL_ENTRIES", "false").lower() in ("true", "1", "yes")
TRACKING_DB_CLEANUP = os.getenv("TRACKING_DB_CLEANUP", "true").lower() in ("true", "1", "yes")

logger.info(f"TRACKING_DB_STORE_ALL_ENTRIES: {TRACKING_DB_STORE_ALL_ENTRIES}")
logger.info(f"TRACKING_DB_CLEANUP: {TRACKING_DB_CLEANUP}")

__all__ = [
    # Message broker
    "MESSAGE_BROKER",
    "split_host_port",
    # Kafka
    "KAFKA_CONSUMER_SERVER",
    "KAFKA_CONSUMER_TOPIC",
    "KAFKA_CONSUMER_GROUP_ID",
    "KAFKA_CONSUMER_CONNECT_RETRY_SEC",
    # Redis Streams
    "REDIS_CONSUMER_SERVER",
    "REDIS_CONSUMER_HOST",  # derived from REDIS_CONSUMER_SERVER
    "REDIS_CONSUMER_PORT",  # derived from REDIS_CONSUMER_SERVER
    "REDIS_CONSUMER_PASSWORD",
    "REDIS_CONSUMER_TOPIC",
    "REDIS_CONSUMER_GROUP",
    "REDIS_CONSUMER_NAME",
    "REDIS_CONSUMER_PAYLOAD_KEY",
    "REDIS_CONSUMER_BATCH_COUNT",
    "REDIS_CONSUMER_BLOCK_MS",
    "REDIS_CONSUMER_START_ID",
    "REDIS_CONSUMER_CONNECT_RETRY_SEC",
    "REDIS_CONSUMER_LAG_LOG_INTERVAL_SEC",
    # Milvus
    "COLLECTION_NAME",
    "COMPRESSED_COLLECTION_NAME",
    "DIMENSION",
    "COLLECTION_MAX_ENTITIES",
    "ITERATOR_BATCH_SIZE",
    "CUSTOM_GROUP_SEARCH",
    "MILVUS_HOST",
    "MILVUS_PORT",
    "MILVUS_COMPRESSED_HOST",
    "MILVUS_COMPRESSED_PORT",
    "CLEAN_RESTART",
    # Milvus index / search params
    "MILVUS_INDEX_TYPE",
    "MILVUS_METRIC_TYPE",
    "MILVUS_IVF_NLIST",
    "MILVUS_IVF_NPROBE",
    "MILVUS_INDEX_PARAMS",
    "MILVUS_SEARCH_PARAMS",
    "MILVUS_SEARCH_WORKER_PROCESSES",
    # Compression
    "OUTLIER_STD_DEV_THRESHOLD",
    "COSINE_SIMILARITY_THRESHOLD",
    "MIN_COMPRESSED_SAMPLES",
    "MAX_COMPRESSED_SAMPLES",
    "COMPRESSION_MERGE_MAX_DISTANCE",
    "COMPRESSION_BATCH_LIMIT",
    "COMPRESSION_INTERVAL_SEC",
    "COMPRESSION_MIN_BATCH",
    "LOAD_DELETE_BATCH_SIZE",
    "MAINTENANCE_READ_CONSISTENCY_LEVEL",
    # Online clustering
    "TRIGGER_BATCH_LIMIT",
    "FIRST_TRIGGER_MIN_BATCH",
    "TRIGGER_INTERVAL_SEC",
    "TRIGGER_MIN_BATCH",
    "CLUSTER_DETERMINISTIC",
    "CLUSTER_WATERMARK_LAG",
    "CLUSTER_MAX_WINDOWS_PER_TICK",
    "CLUSTERING_MAX_UNTRACKED_EMBEDDINGS_FRACTION",
    "CLUSTERING_UNTRACKED_MAX_RETRIES",
    "CLUSTERING_UNTRACKED_RETRY_DELAY",
    "CLUSTER_DB_SNAPSHOT_MODE",
    "CLUSTER_DB_SNAPSHOT_IDLE_TICKS",
    "ENABLE_CLUSTER_DB_SNAPSHOTS",
    "CLUSTER_ASSIGNMENTS_DIR",
    "CLUSTER_ID_START",
    # Insert / batching
    "INSERT_BATCH_SIZE",
    "INSERT_BATCH_TIMEOUT_SEC",
    "INSERT_EMBEDDINGS_IN_BATCHES",
    "INSERT_WORKER_COUNT",
    "INSERT_QUEUE_MAX",
    "INSERT_QUEUE_PUT_TIMEOUT_SEC",
    # Feature toggles
    "ENABLE_COMPRESSION",
    "ENABLE_CLUSTERING",
    "ENABLE_CLT_ESTIMATION",
    "CLT_SIGMA_SCALE",
    "ENABLE_SECONDARY_EMBEDDING",
    "SECONDARY_EMBEDDING_MODEL",
    "SECONDARY_EMBEDDING_ONNX_MODEL_PATH",
    # Search
    "MAX_CONCURRENT_SEARCHES",
    # HTTP server
    "REID_SERVICE_HOST",
    "REID_SERVICE_PORT",
    # Debug
    "ENABLE_DEBUG_ENDPOINTS",
    # Kafka schema + maintenance
    "DEEPSTREAM_SCHEMA",
    "CLUSTER_STATS_INTERVAL",
    "MAINTENANCE_MAX_THREADS",
    "MAINTENANCE_COMPRESSION_POOL_WORKERS",
    # Profiling
    "PROFILE_LOG_INTERVAL_SEC",
    # Tracking DB
    "TRACKING_DB_STORE_ALL_ENTRIES",
    "TRACKING_DB_CLEANUP",
]
