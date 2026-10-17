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

from fastapi import FastAPI, HTTPException, APIRouter, Query
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union
import uvicorn
from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType, utility
import numpy as np
import base64
import json
import multiprocessing
import threading
import logging
import logging.handlers
import queue
import re
import heapq
import math
import signal
import time
import os
from argparse import Namespace
from datetime import datetime
from threading import Lock
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager, nullcontext
import asyncio
from tracking_store import (
    SensorIdToMaxFrameIdMap,
    TrackedObject,
    TrackingStore,
    make_store,
)
from cluster_stats import (
    CachedStats,
    ClusterStats,
    ClusterStatsStore,
    make_cluster_stats_store,
)
from cluster_stats.clt import estimate_knn_normal_clt


def _ignore_sigint_in_worker():
    """Child processes inherit SIGINT from the terminal process group. Ignore
    it so only the parent handles Ctrl+C; the parent's shutdown logic then
    terminates each child explicitly. Avoids noisy ``KeyboardInterrupt``
    tracebacks from blocking calls (``time.sleep``, ``Queue.get``, etc.).
    """
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        # Only callable on the main thread of the worker; safe to skip otherwise.
        pass


# ---------------------------------------------------------------------------
# Logging configuration.
#
# Configured at module load time -- BEFORE any other top-level code can
# emit a log line -- so module-level INFO/DEBUG messages (e.g.
# ``make_store()`` announcing the chosen TrackingStore backend, or any
# future ``logger.info(...)`` in a downstream import) aren't silently
# dropped by Python's default WARNING-level root logger. Anything that
# logs at import time, anywhere in the module or in a transitively
# imported package, gets a configured root logger to write into.
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_ASYNC = os.getenv("LOG_ASYNC", "true").lower() in ("true", "1", "yes")
_LOG_FORMAT = "%(levelname)s:%(name)s:%(message)s"


def _log_listener_process(log_queue):
    """Runs in a dedicated process — reads log records from the queue and writes to stderr."""
    _ignore_sigint_in_worker()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    while True:
        try:
            record = log_queue.get()
            if record is None:
                break
            handler.handle(record)
        except Exception:
            pass


_log_queue = None
_log_process = None
if LOG_ASYNC:
    _log_queue = multiprocessing.Queue(-1)
    _log_process = multiprocessing.Process(target=_log_listener_process, args=(_log_queue,), daemon=True)
    _log_process.start()

# Configure the ROOT logger so all modules (cluster_sampling, etc.) route
# through the log process automatically via propagation.
_root_logger = logging.getLogger()
_root_logger.setLevel(LOG_LEVEL)
if LOG_ASYNC and _log_queue is not None:
    _root_logger.addHandler(logging.handlers.QueueHandler(_log_queue))
else:
    _root_handler = logging.StreamHandler()
    _root_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    _root_logger.addHandler(_root_handler)

logger = logging.getLogger(__name__)

# Env-derived constants + Pydantic API models live in dedicated modules.
# These imports MUST come after the root logger is configured above so that
# config.py's module-level ``logger.info(...)`` lines (announcing the resolved
# Kafka/Milvus/compression settings) reach the configured handlers.
# flake8: noqa: F405
from config import *  # noqa: E402,F401,F403 -- intentional re-export
from models import *  # noqa: E402,F401,F403 -- intentional re-export

# Kafka ingestion pipeline (parse, batch, insert, subprocess lifecycle).
import ingest  # noqa: E402

# Online-clustering engine (self-contained; no dependency on inline plumbing).
# Placed after logging setup so any import-time logging is captured.
from cluster_sampling.sample_and_extract_patches import (  # noqa: E402
    process_single_object,
    recompress_single_object,
)
from cluster_manager import ClusterManager, ClusterParams, EmbeddingRecord  # noqa: E402
from cluster_manager.compression_labeling import (  # noqa: E402
    add_surviving_rep_transitions,
    cluster_id_to_object_id,
    retired_to_surviving_from_groups,
)
from cluster_manager.coverage import tracking_coverage  # noqa: E402
from cluster_manager.serialization import write_batch_assignments  # noqa: E402
from cluster_manager.splitting import split_batch_into_subtracklets  # noqa: E402
from cluster_manager.tracking_relabel import (  # noqa: E402
    build_tracking_updates_from_rep_map,
)
from service_adapters.cluster_service import (  # noqa: E402
    delete_filtered_from_main,
    relabel_main_clusters,
    relabel_retired_main_clusters,
    write_db_snapshots,
)
from service_adapters.compressed_relabel import relabel_compressed_clusters  # noqa: E402


from profiling import RateProfiler  # noqa: E402


# ---------------------------------------------------------------------------
# Health-check registries (populated during __main__ startup).
#
# Each child process registers itself with (name, Process, ready_event).
# The worker sets ``ready_event`` once *its own* setup has finished — after
# Milvus connection + collection.load() and any internal threads have been
# started. This lets /health/ready answer "is the worker actually serving?"
# rather than just "did the OS create the process?".
#
# In-process daemon threads (drainers running in the FastAPI process) are
# tracked separately so the readiness probe can also assert they are alive.
# ---------------------------------------------------------------------------
_health_registry_lock = threading.Lock()
_required_child_procs: List[Tuple[str, "multiprocessing.Process", "multiprocessing.synchronize.Event"]] = []
_required_main_threads: List[Tuple[str, "threading.Thread"]] = []


def _register_child_proc(name, proc, ready_event):
    with _health_registry_lock:
        _required_child_procs.append((name, proc, ready_event))


def _register_main_thread(name, thread):
    with _health_registry_lock:
        _required_main_threads.append((name, thread))


# ---------------------------------------------------------------------------
# Cluster-stats stores.
#
# One store instance per Milvus collection (main + compressed), built at
# module import time so they're inherited (via fork) by every child
# process the parent later spawns. The backend is selected by the
# ``CLUSTER_STATS_BACKEND`` env var (default ``sqlite`` -- the
# cross-process SQLite-WAL backend that a multi-process search tier
# needs; set ``memory`` to fall back to the legacy in-process cache).
#
# Writes go through ``store.replace(stats)`` from any process: the
# in-memory backend applies owner writes inline and fans non-owner writes
# in over its own queue + drainer; the SQLite backend writes straight to
# the shared DB. Lifecycle (attach_post_fork / start_drainers / shutdown)
# mirrors ``tracking_store``.
# ---------------------------------------------------------------------------
main_stats_cache: ClusterStatsStore = make_cluster_stats_store(label="main")
compressed_stats_cache: ClusterStatsStore = make_cluster_stats_store(label="compressed")

_new_data_event = multiprocessing.Event()
# Carries resolved ``TrackedObject.model_dump()`` payloads from the
# compression producer (running in either the FastAPI parent or the
# maintenance worker) to the secondary-embedding generator process.
_compressed_embeddings_object_metadata_queue: multiprocessing.Queue = multiprocessing.Queue()

# Tracking database. Backend is selected by ``TRACKING_STORE_BACKEND``
# env var; defaults to :class:`InMemoryTrackingStore` (Phase 1) which
# preserves the legacy dict-and-queue behaviour exactly. Set
# ``TRACKING_STORE_BACKEND=sqlite`` for the Phase 2 SQLite-WAL backend.
tracking_store: TrackingStore = make_store()

# ---------------------------------------------------------------------------
# Online cluster manager (gated by ENABLE_CLUSTERING)
# ---------------------------------------------------------------------------
# Algorithm-tuning knobs are read from env directly into ClusterParams (kept in
# main.py since this constructs a cluster_manager object). The coarse gates
# (ENABLE_CLUSTERING, TRIGGER_BATCH_LIMIT, CLUSTER_DETERMINISTIC, ...) live in
# config.py and arrive via ``from config import *``.
_cluster_params = ClusterParams(
    cos_threshold=float(os.getenv("CLUSTER_COS_THRESHOLD", "0.95")),
    min_embs_threshold=int(os.getenv("CLUSTER_MIN_EMBS", "50")),
    coexist_frame_gap=int(os.getenv("CLUSTER_COEXIST_FRAME_GAP", "30")),
    coexist_min_pairs=int(os.getenv("CLUSTER_COEXIST_MIN_PAIRS", "5")),
    coexist_max_iou=float(os.getenv("CLUSTER_COEXIST_MAX_IOU", "0.1")),
    coexist_persist=os.getenv("CLUSTER_COEXIST_PERSIST", "0") == "1",
    coexist_persist_min_hits=int(os.getenv("CLUSTER_COEXIST_PERSIST_MIN_HITS", "20")),
    lambda_blend=float(os.getenv("CLUSTER_LAMBDA", "0.95")),
    cos_neighbor_threshold=float(os.getenv("CLUSTER_COS_NEIGHBOR", "0.8")),
    rerank_k1_max=int(os.getenv("CLUSTER_RERANK_K1MAX", "20")),
    rerank_k2=int(os.getenv("CLUSTER_RERANK_K2", "1")),
    merge_threshold=float(os.getenv("CLUSTER_MERGE_THRESHOLD", "0.95")),
    # Cross-camera merge incentive. Unset = disabled (default path). When set
    # (e.g. 0.83), disjoint-sensor cluster pairs that are mutually
    # reciprocal-nearest may merge at this lower bar.
    cross_merge_threshold=(
        float(os.environ["CLUSTER_MERGE_THRESHOLD_CROSS"]) if os.getenv("CLUSTER_MERGE_THRESHOLD_CROSS") else None
    ),
    # Variance/purity gate at the merge step (complete-linkage floor). Unset = off.
    merge_min_affinity=(
        float(os.environ["CLUSTER_MERGE_MIN_AFFINITY"]) if os.getenv("CLUSTER_MERGE_MIN_AFFINITY") else None
    ),
    blend_strategy=os.getenv("CLUSTER_BLEND_STRATEGY", "count"),
    ema_alpha=float(os.getenv("CLUSTER_EMA_ALPHA", "0.3")),
    # Multi-prototype cluster reps. 1 = single-centroid (legacy, exact). >1 keeps
    # k appearance modes per cluster and matches on the nearest (MAX-over-protos
    # affinity) -- the lever for cross-camera/hard-pose association.
    n_prototypes=int(os.getenv("CLUSTER_N_PROTOTYPES", "1")),
    proto_spawn_threshold=float(os.getenv("CLUSTER_PROTO_SPAWN", "0.85")),
    sub_proto_spawn_threshold=float(os.getenv("CLUSTER_SUB_PROTO_SPAWN", "0.95")),
    # Decoupled sub-tracklet prototype count (0 = inherit CLUSTER_N_PROTOTYPES).
    sub_n_prototypes=int(os.getenv("CLUSTER_SUB_N_PROTOTYPES", "0")),
)

_cluster_manager: Optional[ClusterManager] = ClusterManager(_cluster_params)
_cluster_manager_lock = Lock()

# Sub-tracklet accumulator (notebook's "Option B: pending-list buffer"). Holds
# embeddings whose sub-tracklets are below min_embs_threshold so they carry
# into the next tick instead of being dropped by Stage 2A. Guarded by an age
# cap (frame distance) and a hard row cap. Accessed only from the
# single-threaded maintenance loop, so no extra lock.
CLUSTER_MAX_FRAME_AGE = int(os.getenv("CLUSTER_MAX_FRAME_AGE", "2500"))
CLUSTER_MAX_PENDING_ROWS = int(os.getenv("CLUSTER_MAX_PENDING_ROWS", "5000"))
_pending_records: List[EmbeddingRecord] = []

# Deterministic-mode in-memory window buffer. Rows are pulled from Milvus ONCE
# via the entry_id cursor, accumulated here, then sliced into fixed
# frame-sorted windows in memory -- keeps the read O(n) while making the batch
# partition a pure function of the data.
_det_window_buffer: List[EmbeddingRecord] = []

# Server-side cursor for the unclustered-rows read in the clustering tick.
# Tracks the max entry_id already pulled so subsequent query_iterator calls can
# push ``entry_id > _max_entry_id_seen`` into Milvus. Committed only AFTER the
# min-batch gate passes, so skipped ticks leave it unchanged.
_max_entry_id_seen: int = 0

collection_load_lock = Lock()
collection_loaded = False

compressed_collection_load_lock = Lock()
compressed_collection_loaded = False

# Semaphore to limit concurrent searches.
# Sized by MAX_CONCURRENT_SEARCHES (see config.py). Initialized in lifespan
# once the asyncio event loop is available.
search_semaphore = None

debug_router = APIRouter()
# Use FastAPI to implement the REST API
openapi_tags = [
    {
        "name": "Run Time Operations",
        "description": "Operations that are used during runtime i.e. during interaction of "
        "ReID service with DS tracking instance.",
    },
    {
        "name": "Offline Operations",
        "description": "Operations that can be used for monitoring / resetting the service.",
    },
    {
        "name": "Health",
        "description": "Liveness and readiness probes for orchestrators "
        "(Kubernetes, Docker healthchecks, load balancers).",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize async resources on startup; nothing to tear down on shutdown.

    Replaces the legacy ``@app.on_event("startup")`` hook (deprecated in
    FastAPI 0.109+, removed in a future release).
    """
    global search_semaphore
    search_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(
        ThreadPoolExecutor(max_workers=MAX_CONCURRENT_SEARCHES, thread_name_prefix="uvicorn-worker")
    )
    logger.info(f"Initialized search semaphore and thread pool with limit: {MAX_CONCURRENT_SEARCHES}")
    yield


app = FastAPI(
    title="ReID Service",
    openapi_tags=openapi_tags,
    description="NVIDIA ReID Service API",
    version="v1",
    contact={"name": "NVIDIA", "url": "https://www.nvidia.com"},
    lifespan=lifespan,
)


connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)
connections.connect(alias="compressed", host=MILVUS_COMPRESSED_HOST, port=MILVUS_COMPRESSED_PORT)

# Drop existing collection if it exists (only when CLEAN_RESTART is enabled)
if CLEAN_RESTART and utility.has_collection(COLLECTION_NAME):
    logger.warning("CLEAN_RESTART is enabled - dropping existing collection (all data will be lost!)")
    coll = Collection(COLLECTION_NAME)
    coll.release()
    utility.drop_collection(COLLECTION_NAME)

# Schema for the main (live) collection - entry_id auto-generated
fields = [
    FieldSchema(name="entry_id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="object_id", dtype=DataType.VARCHAR, max_length=32),
    FieldSchema(name="sensor_id", dtype=DataType.VARCHAR, max_length=255),
    FieldSchema(name="frame_id", dtype=DataType.INT64),
    # Clustering flag: rows enter is_clustered=False; relabel_main_clusters flips
    # them to True once assigned to a cluster rep. Filtered server-side by the
    # clustering tick (== false to skip already-clustered rows), search/stats
    # (== true to surface only cluster reps), and compression (== true sampling
    # targets). default_value lets the row-dict insert paths omit it.
    FieldSchema(name="is_clustered", dtype=DataType.BOOL, default_value=False),
    FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=DIMENSION),
]
schema = CollectionSchema(fields=fields, description="Embeddings collection")

# The mean travels as base64 of its raw little-endian float32 bytes in a
# VARCHAR, not as the ARRAY<FLOAT> it used to be. Every candidate storage type
# is the same number of bytes on the wire (to within base64's 4/3), so what
# separates them is what pymilvus does with those bytes on arrival:
#
#   ARRAY<FLOAT>   eagerly decoded into a Python list of DIMENSION floats,
#                  per row, before /search ever sees it. Measured at ~25ms of
#                  the search path -- the reason this column was the single
#                  largest cost of ENABLE_CLT_ESTIMATION.
#   FLOAT_VECTOR   also materialized as a Python list, and Milvus refuses to
#                  load a collection holding an unindexed vector field, so it
#                  would need an ANN index over moments that nothing searches.
#   FLOAT16_VECTOR arrives as raw bytes (fast) but is still a vector field, so
#                  it carries the same index requirement, plus fp16 rounding.
#   VARCHAR        arrives as one str, untouched. Scalar, so no index needed.
#
# Decoding is then np.frombuffer over a single b64decode: no per-element Python
# work, and the search path's downstream cast to float64 becomes a C loop over
# a typed buffer instead of a walk over DIMENSION boxed floats.
#
# This is a schema change, so a compressed collection built by an older build
# still declares mean as ARRAY<FLOAT> and will reject these writes: switching
# to this build needs CLEAN_RESTART=true once (it is the default).
_MEAN_DTYPE = "<f4"
_MEAN_ITEMSIZE = np.dtype(_MEAN_DTYPE).itemsize


def _mean_b64_max_length(dim: int) -> int:
    """VARCHAR capacity a base64-encoded ``dim``-wide mean needs.

    Base64 emits 4 characters per 3 input bytes, padding the last group, so the
    exact length is ``4 * ceil(bytes / 3)``. Derived from DIMENSION rather than
    fixed so re-pointing the service at a model of another width stays a config
    change; Milvus caps VARCHAR at 65535, which this reaches around dim 12287.
    """
    return 4 * ((dim * _MEAN_ITEMSIZE + 2) // 3)


MEAN_B64_MAX_LENGTH = _mean_b64_max_length(DIMENSION)


def _encode_mean(mean) -> str:
    """Serialize one mean for the compressed collection's ``mean`` column."""
    return base64.b64encode(np.asarray(mean, dtype=_MEAN_DTYPE).tobytes()).decode("ascii")


def _decode_mean(encoded: str) -> np.ndarray:
    """Read one ``mean`` column value back as a ``[DIMENSION]`` float32 array.

    The result is a read-only view over the decoded bytes -- callers that need
    to write, or need another dtype, copy via ``np.asarray(..., dtype=...)``.
    """
    return np.frombuffer(base64.b64decode(encoded), dtype=_MEAN_DTYPE)


# Compressed collection: same schema but entry_id not auto-generated so we can
# store original main collection ids. Each row also carries the summary of the
# raw vectors it stands for: weight is the group size and mean is their
# per-dimension mean, mergeable in O(d) as rows are recompressed (see
# cluster_stats.moments).
#
# ENABLE_CLT_ESTIMATION is the only consumer of the mean, so with it off the
# column is dropped outright rather than written and ignored: that halves the
# compressed row width. Because COMPRESSED_FIELD_ORDER below is derived from
# this list, dropping it here also removes it from every insert, upsert and
# maintenance query. Flipping the flag therefore changes the schema, which an
# existing collection will not adopt -- it needs CLEAN_RESTART=true.
compressed_fields = [
    FieldSchema(name="entry_id", dtype=DataType.INT64, is_primary=True, auto_id=False),
    FieldSchema(name="object_id", dtype=DataType.VARCHAR, max_length=32),
    FieldSchema(name="sensor_id", dtype=DataType.VARCHAR, max_length=255),
    FieldSchema(name="frame_id", dtype=DataType.INT64),
    FieldSchema(name="weight", dtype=DataType.INT64),
    FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=DIMENSION),
]
if ENABLE_CLT_ESTIMATION:
    compressed_fields.append(FieldSchema(name="mean", dtype=DataType.VARCHAR, max_length=MEAN_B64_MAX_LENGTH))
compressed_schema = CollectionSchema(
    fields=compressed_fields, description="Compressed embeddings (entry_id = main collection id)"
)
# Column order for every insert/upsert into the compressed collection; must
# match compressed_fields.
COMPRESSED_FIELD_ORDER = [f.name for f in compressed_fields]

# Create collection if it doesn't exist
if not utility.has_collection(COLLECTION_NAME):
    collection = Collection(name=COLLECTION_NAME, schema=schema)
    collection.create_index(field_name="vector", index_params=MILVUS_INDEX_PARAMS)
    # Scalar BITMAP index on is_clustered: the clustering tick filters
    # `is_clustered == false` every tick and search/stats/compression filter
    # `== true`. BITMAP suits the low-cardinality BOOL so the filter is
    # O(matching) rather than a full segment scan. Guarded so an unsupported
    # index type on this Milvus version cannot block startup (filter still
    # works via scan without it).
    try:
        collection.create_index(
            field_name="is_clustered",
            index_params={"index_type": "BITMAP"},
        )
        logger.info("Created BITMAP scalar index on is_clustered")
    except Exception as e:
        logger.warning(f"Could not create is_clustered scalar index (filter will scan): {e}")
    logger.info(f"Created main collection '{COLLECTION_NAME}' with index {MILVUS_INDEX_PARAMS}")
else:
    collection = Collection(COLLECTION_NAME)
    _existing = [idx.params.get("index_type") for idx in collection.indexes if idx.field_name == "vector"]
    if _existing and _existing[0] != MILVUS_INDEX_TYPE:
        logger.warning(
            f"Main collection '{COLLECTION_NAME}' has existing vector index "
            f"{_existing[0]!r} but MILVUS_INDEX_TYPE={MILVUS_INDEX_TYPE!r}. "
            "Milvus will keep the existing index. Set CLEAN_RESTART=true to rebuild."
        )

# Load the collection at startup
try:
    collection.load()
    logger.info("Collection loaded successfully at startup")
except Exception as e:
    logger.error(f"Failed to load collection at startup: {str(e)}")
    raise

# --- Compressed Embeddings Collection (on separate Milvus instance) ---
if CLEAN_RESTART and utility.has_collection(COMPRESSED_COLLECTION_NAME, using="compressed"):
    logger.warning("CLEAN_RESTART is enabled - dropping compressed embeddings collection")
    compressed_coll = Collection(COMPRESSED_COLLECTION_NAME, using="compressed")
    compressed_coll.release()
    utility.drop_collection(COMPRESSED_COLLECTION_NAME, using="compressed")

if not utility.has_collection(COMPRESSED_COLLECTION_NAME, using="compressed"):
    compressed_collection = Collection(name=COMPRESSED_COLLECTION_NAME, schema=compressed_schema, using="compressed")
    compressed_collection.create_index(field_name="vector", index_params=MILVUS_INDEX_PARAMS)
    # Scalar INVERTED index on object_id accelerates the
    # ``object_id in [...]`` merge query in _perform_compression
    compressed_collection.create_index(
        field_name="object_id",
        index_params={"index_type": "INVERTED"},
        index_name="object_id_idx",
    )
    logger.info(
        f"Created compressed collection '{COMPRESSED_COLLECTION_NAME}' with vector index "
        f"{MILVUS_INDEX_PARAMS} and INVERTED scalar index on object_id"
    )
else:
    compressed_collection = Collection(COMPRESSED_COLLECTION_NAME, using="compressed")
    _existing_c = [idx.params.get("index_type") for idx in compressed_collection.indexes if idx.field_name == "vector"]
    if _existing_c and _existing_c[0] != MILVUS_INDEX_TYPE:
        logger.warning(
            f"Compressed collection '{COMPRESSED_COLLECTION_NAME}' has existing vector index "
            f"{_existing_c[0]!r} but MILVUS_INDEX_TYPE={MILVUS_INDEX_TYPE!r}. "
            "Milvus will keep the existing index. Set CLEAN_RESTART=true to rebuild."
        )

try:
    compressed_collection.load()
    logger.info("Compressed embeddings collection loaded successfully at startup")
except Exception as e:
    logger.error(f"Failed to load compressed collection at startup: {str(e)}")
    raise


def ensure_collection_loaded():
    """Ensure collection is loaded, with retries"""
    global collection_loaded
    max_retries = 3
    retry_delay = 1  # seconds
    with collection_load_lock:
        if collection_loaded:
            return

        for attempt in range(max_retries):
            try:
                collection.load()
                collection_loaded = True
                logger.info("Collection loaded successfully")
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Failed to load collection (attempt {attempt + 1}/{max_retries}): {str(e)}")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"Failed to load collection after {max_retries} attempts: {str(e)}")
                    raise


def ensure_compressed_collection_loaded():
    """Ensure compressed collection is loaded, with retries"""
    global compressed_collection_loaded
    max_retries = 3
    retry_delay = 1
    with compressed_collection_load_lock:
        if compressed_collection_loaded:
            return
        for attempt in range(max_retries):
            try:
                compressed_collection.load()
                compressed_collection_loaded = True
                logger.info("Sampled collection loaded successfully")
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Failed to load compressed collection (attempt {attempt + 1}/{max_retries}): {str(e)}"
                    )
                    time.sleep(retry_delay)
                else:
                    logger.error(f"Failed to load compressed collection after {max_retries} attempts: {str(e)}")
                    raise


def _get_embeddings_collection(use_compressed: bool):
    """Return the main or compressed collection and ensure it is loaded. Used by offline endpoints."""
    if use_compressed:
        ensure_compressed_collection_loaded()
        return compressed_collection
    ensure_collection_loaded()
    return collection


def _compressed_entity_columns(items: List[dict]) -> List[list]:
    """Column-major insert/upsert payload for the compressed collection,
    ordered to match its schema.

    Every write to the collection funnels through here, so this is also where
    the mean crosses into its stored form: callers hand over the plain
    per-dimension sequence that ``cluster_sampling`` and ``cluster_stats``
    produce and consume, and encoding stays out of both.
    """
    return [
        [_encode_mean(item["mean"]) for item in items] if field == "mean" else [item[field] for item in items]
        for field in COMPRESSED_FIELD_ORDER
    ]


# pymilvus returns most columns eagerly but leaves FLOAT_VECTOR lazy: the row
# dicts hold None until something reads the field, and the first read of a row
# builds a Python list of DIMENSION floats for it. Grouping a query result by
# object_id therefore materializes the whole vector column one row at a time,
# which for a few hundred rows costs more than the query that fetched it. The
# bytes behind that column are already a contiguous little-endian float32 block,
# so the two helpers below decode it once with numpy and hand each row a view.
#
# Proto3 packs repeated numeric fields, so a FloatArray (`repeated float data =
# 1`) serializes to one length-delimited record: the tag below, a varint length,
# then the raw floats.
_PACKED_FLOAT_ARRAY_TAG = 0x0A


def _decode_float_vector_column(field_data, row_count: int, dim: int) -> Optional[np.ndarray]:
    """Decode a whole FLOAT_VECTOR column from its protobuf payload.

    Returns None if the payload is not the packed layout described above, so a
    pymilvus or protobuf change degrades to the ordinary per-row path instead of
    returning wrong vectors. The result is a private copy, leaving it writable
    and releasing the serialized blob.
    """
    if row_count <= 0:
        return None
    blob = field_data.vectors.float_vector.SerializeToString()
    if not blob or blob[0] != _PACKED_FLOAT_ARRAY_TAG:
        return None
    offset, shift, length = 1, 0, 0
    while offset < len(blob):
        byte = blob[offset]
        length |= (byte & 0x7F) << shift
        offset += 1
        if not byte & 0x80:
            break
        shift += 7
    else:
        return None
    if length != row_count * dim * 4 or offset + length != len(blob):
        return None
    return np.frombuffer(memoryview(blob)[offset : offset + length], dtype="<f4").reshape(row_count, dim).copy()


def _rows_grouped_by_object_id(rows) -> Dict[str, List[dict]]:
    """Group a query result by object_id, decoding lazy vector columns in bulk.

    Falls back to plain iteration unless every lazy column was decoded, since a
    column left undecoded still needs pymilvus to materialize it and mixing the
    two paths would leave those fields None.
    """
    grouped: Dict[str, List[dict]] = {}
    lazy_field_data = getattr(rows, "_lazy_field_data", None) or ()
    columns = {}
    for field_data in lazy_field_data:
        if field_data.type != DataType.FLOAT_VECTOR:
            continue
        column = _decode_float_vector_column(field_data, len(rows), DIMENSION)
        if column is None:
            break
        columns[field_data.field_name] = column
    if not columns or len(columns) != len(lazy_field_data):
        for row in rows:
            grouped.setdefault(row["object_id"], []).append(row)
        return grouped
    for index in range(len(rows)):
        # list.__getitem__ reaches the underlying dict without triggering the
        # per-row materialization that the subclass adds.
        row = list.__getitem__(rows, index)
        for name, column in columns.items():
            row[name] = column[index]
        grouped.setdefault(row["object_id"], []).append(row)
    return grouped


def _read_prev_compressed_rows(object_ids: List[str]) -> Dict[str, List[dict]]:
    """Previously compressed rows for these objects, grouped by object_id.

    The mean is decoded here, at the read boundary, so recompression and the
    summary comparison downstream see the plain per-dimension form they emit --
    the encoding never reaches ``cluster_sampling``. Decoded after grouping
    because iterating the raw result is what the grouping helper exists to
    avoid.
    """
    rows = compressed_collection.query(
        expr=f"object_id in {object_ids}",
        output_fields=COMPRESSED_FIELD_ORDER,
        consistency_level=MAINTENANCE_READ_CONSISTENCY_LEVEL,
    )
    grouped = _rows_grouped_by_object_id(rows)
    if ENABLE_CLT_ESTIMATION:
        for object_rows in grouped.values():
            for row in object_rows:
                row["mean"] = _decode_mean(row["mean"])
    return grouped


def _perform_compression(outlier_threshold: float, cosine_threshold: float, max_samples: int, batch_limit: int) -> dict:
    """Run the full compression pipeline: main collection -> compressed collection.

    If the main collection has more than batch_limit entries, only the first
    batch_limit entries are processed and then deleted from the main collection.
    """
    ensure_collection_loaded()
    ensure_compressed_collection_loaded()

    # Get total count first to report entries_remaining later
    try:
        count_result = collection.query(
            expr="",
            output_fields=["count(*)"],
            consistency_level=MAINTENANCE_READ_CONSISTENCY_LEVEL,
        )
        total_in_main = count_result[0]["count(*)"] if count_result else 0
    except Exception:
        total_in_main = -1
    # When clustering is enabled, compression consumes only rows that have
    # already been relabeled to a cluster rep (is_clustered == true). Raw
    # un-clustered rows stay in main until a clustering pass claims them.
    read_expr = "is_clustered == true" if ENABLE_CLUSTERING else ""
    if batch_limit <= COLLECTION_MAX_ENTITIES:
        all_results = list(
            collection.query(
                expr=read_expr,
                output_fields=["entry_id", "object_id", "sensor_id", "frame_id", "vector"],
                limit=batch_limit,
                consistency_level=MAINTENANCE_READ_CONSISTENCY_LEVEL,
            )
        )
    else:
        iterator = collection.query_iterator(
            batch_size=min(LOAD_DELETE_BATCH_SIZE, batch_limit),
            expr=read_expr,
            output_fields=["entry_id", "object_id", "sensor_id", "frame_id", "vector"],
            consistency_level=MAINTENANCE_READ_CONSISTENCY_LEVEL,
        )
        all_results = []
        while True:
            batch = iterator.next()
            if not batch:
                iterator.close()
                break
            all_results.extend(batch)
            if len(all_results) >= batch_limit:
                iterator.close()
                all_results = all_results[:batch_limit]
                break
    logger.info(f"Sampling: read {len(all_results)} of {total_in_main} embeddings from main collection")

    if not all_results:
        return {
            "message": "No embeddings in main collection to sample",
            "total_original": 0,
            "total_compressed": 0,
            "objects_processed": 0,
            "per_object": [],
            "entries_processed": 0,
            "entries_remaining": max(0, total_in_main),
        }

    processed_entry_ids = [item["entry_id"] for item in all_results]

    clusters = {}
    for item in all_results:
        clusters.setdefault(item["object_id"], []).append(item)

    compression_args = Namespace(
        outlier_threshold=outlier_threshold,
        cosine_threshold=cosine_threshold,
        max_samples=max_samples,
        merge_max_distance=COMPRESSION_MERGE_MAX_DISTANCE,
        # Per-row moments exist only to serve CLT distance estimation at
        # search time; with that off, don't compute or carry them.
        with_moments=ENABLE_CLT_ESTIMATION,
        seed=100,
    )
    per_object_results = []
    all_sampled = []
    # Map of sensor_id -> max frame_id seen across all processed entries.
    # Used downstream to know the latest frame already compressed per stream.
    sensor_id_to_max_processed_frame_id: SensorIdToMaxFrameIdMap = {}

    _process_compression_clusters(
        clusters,
        compression_args,
        all_sampled,
        per_object_results,
        sensor_id_to_max_processed_frame_id,
    )

    # Now that we have compressed embeddings from live dB
    # Combine these with previous compressed embeddings
    clusters = {}
    for item in all_sampled:
        clusters.setdefault(item["object_id"], []).append(item)

    object_ids_in_batch = list(clusters.keys())
    prev_by_object_id: Dict[str, list] = {}
    if object_ids_in_batch:
        prev_by_object_id = _read_prev_compressed_rows(object_ids_in_batch)

    items_to_insert = []
    items_to_update = []
    items_to_delete = []
    _merge_recompress_clusters(
        clusters,
        prev_by_object_id,
        compression_args,
        items_to_insert,
        items_to_update,
        items_to_delete,
    )

    if items_to_insert:
        for i in range(0, len(items_to_insert), LOAD_DELETE_BATCH_SIZE):
            batch = items_to_insert[i : i + LOAD_DELETE_BATCH_SIZE]
            compressed_collection.insert(_compressed_entity_columns(batch))
    if items_to_update:
        # Surviving compressed rows whose summary changed during recompression.
        # entry_id is a non-auto primary key, so upsert replaces them in place
        # without re-triggering secondary embedding generation.
        for i in range(0, len(items_to_update), LOAD_DELETE_BATCH_SIZE):
            batch = items_to_update[i : i + LOAD_DELETE_BATCH_SIZE]
            compressed_collection.upsert(_compressed_entity_columns(batch))
    if items_to_delete:
        compressed_collection.delete(expr=f"entry_id in {items_to_delete}")

    if ENABLE_SECONDARY_EMBEDDING:
        # Resolve TrackedObject metadata and hand the full payloads to
        # the secondary-embedding generator process directly.
        resolved = 0
        missing = 0
        for item in items_to_insert:
            tracked_obj = tracking_store.lookup_anywhere(item["object_id"], item["sensor_id"], item["frame_id"])
            if tracked_obj is None:
                missing += 1
                logger.warning(
                    f"[CompressedEmbeddingsMetadata] No TrackedObject found "
                    f"for object_id={item['object_id']} "
                    f"sensor_id={item['sensor_id']} "
                    f"frame_id={item['frame_id']}"
                )
                continue
            _compressed_embeddings_object_metadata_queue.put(tracked_obj.model_dump())
            resolved += 1
        logger.info(
            f"Forwarded {resolved} resolved TrackedObjects to embedding " f"output queue ({missing} unresolved)"
        )

    # Delete processed entries from main collection
    for i in range(0, len(processed_entry_ids), LOAD_DELETE_BATCH_SIZE):
        batch_ids = processed_entry_ids[i : i + LOAD_DELETE_BATCH_SIZE]
        collection.delete(expr=f"entry_id in {batch_ids}")
    entries_remaining = max(0, total_in_main - len(processed_entry_ids))
    logger.info(
        f"Deleted {len(processed_entry_ids)} processed entries from main collection, "
        f"~{entries_remaining} remaining (approximate)"
    )

    if TRACKING_DB_CLEANUP:
        # Drop superseded tracking history for each sensor we just compressed.
        # When clustering is enabled, prune only rows already folded into a
        # cluster rep (is_clustered == True); raw un-clustered history stays
        # put for a future clustering pass.
        for sensor_id, max_frame_id in sensor_id_to_max_processed_frame_id.items():
            tracking_store.delete_below(sensor_id, max_frame_id, clustered_only=ENABLE_CLUSTERING)
        logger.info(f"Submitted {len(sensor_id_to_max_processed_frame_id)} delete_below requests to tracking store")

    logger.info(f"Compressed sampling complete: {len(all_results)} -> {len(all_sampled)} embeddings")
    return {
        "message": "Compression completed successfully",
        "total_original": len(all_results),
        "total_compressed": len(all_sampled),
        "objects_processed": len(clusters),
        "per_object": per_object_results,
        "entries_processed": len(processed_entry_ids),
        "entries_remaining": entries_remaining,
    }


def _object_id_filter_expr(object_id: str) -> str:
    """Build Milvus filter expression for VARCHAR object_id. object_id must contain only digits 0-9."""
    if not object_id or not object_id.isdigit():
        raise HTTPException(status_code=400, detail="object_id must be non-empty and contain only digits 0-9")
    return f'object_id == "{object_id}"'


def _drop_inherited_milvus_aliases(*aliases: str) -> None:
    """Disconnect pymilvus aliases inherited from the parent process.

    When a child is fork()'d, it inherits the parent's gRPC channel objects
    along with pymilvus's alias dict, but the channel's background I/O
    threads do not survive the fork. Those zombie channels keep locked
    mutexes and half-initialized state in the child, and pymilvus's
    Connections singleton cannot fully re-initialize while they're still
    registered -- which is exactly what manifests as a ``__internal_register``
    ping timeout on the *second* ``connections.connect()`` in the child.

    Calling ``disconnect()`` first tears down the inherited entries cleanly
    so the child's own ``connect()`` calls start from a blank slate.
    Unknown aliases are silently ignored.
    """
    for _alias in aliases:
        try:
            connections.disconnect(_alias)
        except Exception:
            pass


_compression_process_pool: Optional[ProcessPoolExecutor] = None
_compression_process_pool_lock = threading.Lock()


def _set_numeric_thread_cap(thread_count: int, log_label: str = "Maintenance") -> None:
    """Cap BLAS/FAISS/native numeric threads for the current process."""
    thread_count = max(1, int(thread_count))
    _vars = [
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "BLIS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ]
    for _var in _vars:
        os.environ[_var] = str(thread_count)

    try:
        import faiss

        faiss.omp_set_num_threads(thread_count)
    except (ImportError, AttributeError):
        pass

    try:
        import torch

        torch.set_num_threads(thread_count)
    except (ImportError, AttributeError):
        pass

    # OpenBLAS -- numpy in this container ships scipy_openblas64 (ILP64)
    #    bundled at <site-packages>/numpy.libs/libscipy_openblas64_-<hash>.so
    #    Two gotchas vs the naive CDLL('libopenblas.so'):
    #      a. filename starts with libscipy_openblas..., not libopenblas...
    #      b. ILP64 builds suffix every symbol with "64_", so the setter is
    #         openblas_set_num_threads64_  (not openblas_set_num_threads).
    #    We glob the numpy/scipy bundle dirs, then probe both symbol names.
    try:
        import ctypes
        import glob
        import numpy as _np

        _candidates = []
        _np_libs_dir = os.path.join(os.path.dirname(_np.__file__), os.pardir, "numpy.libs")
        if os.path.isdir(_np_libs_dir):
            _candidates += sorted(glob.glob(os.path.join(_np_libs_dir, "libscipy_openblas*.so*")))
            _candidates += sorted(glob.glob(os.path.join(_np_libs_dir, "libopenblas*.so*")))
        try:
            import scipy as _sp

            _sp_libs_dir = os.path.join(os.path.dirname(_sp.__file__), os.pardir, "scipy.libs")
            if os.path.isdir(_sp_libs_dir):
                _candidates += sorted(glob.glob(os.path.join(_sp_libs_dir, "libscipy_openblas*.so*")))
                _candidates += sorted(glob.glob(os.path.join(_sp_libs_dir, "libopenblas*.so*")))
        except ImportError:
            pass
        _candidates += ["libopenblas.so.0", "libopenblas.so"]

        _openblas_hits = []
        for _path in _candidates:
            try:
                _lib = ctypes.CDLL(_path)
            except OSError:
                continue
            for _sym in ("openblas_set_num_threads64_", "openblas_set_num_threads"):
                _fn = getattr(_lib, _sym, None)
                if _fn is not None:
                    _fn(ctypes.c_int(thread_count))
                    _openblas_hits.append(f"{os.path.basename(_path)}:{_sym}")
                    break
        if _openblas_hits and log_label:
            logger.info(f"{log_label} OpenBLAS cap applied via: {_openblas_hits}")
        elif log_label:
            logger.warning(f"{log_label} OpenBLAS cap: no setter found " "(numpy may still fan out across all cores)")
    except Exception as e:
        if log_label:
            logger.warning(f"{log_label} OpenBLAS cap failed: {e}")

    if log_label:
        logger.info(f"{log_label} numeric thread cap: {thread_count}")


def _maintenance_set_max_threads():
    _set_numeric_thread_cap(
        MAINTENANCE_MAX_THREADS,
        log_label="Maintenance worker",
    )


def _compression_pool_threads_per_worker() -> int:
    """Split the maintenance BLAS/FAISS budget across pool workers."""
    return max(1, MAINTENANCE_MAX_THREADS // MAINTENANCE_COMPRESSION_POOL_WORKERS)


def _compression_pool_worker_init():
    _ignore_sigint_in_worker()
    _set_numeric_thread_cap(_compression_pool_threads_per_worker(), log_label="")


def _compression_process_object_worker(
    task: Tuple[str, List[dict], Dict[str, Union[float, int]]],
) -> Dict:
    """Run ``process_single_object`` in a pool worker (top-level for pickling)."""
    object_id, items, args_dict = task
    original_count = len(items)
    entry_ids_list = []
    object_ids_list = []
    sensor_ids_list = []
    frame_ids_list = []
    vectors_list = []
    sensor_max_frames: Dict[str, int] = {}
    for it in items:
        entry_ids_list.append(it["entry_id"])
        object_ids_list.append(it["object_id"])
        sensor_ids_list.append(it["sensor_id"])
        frame_ids_list.append(it["frame_id"])
        vectors_list.append(it["vector"])
        sensor_id = it["sensor_id"]
        frame_id = it["frame_id"]
        prev_max = sensor_max_frames.get(sensor_id)
        if prev_max is None or frame_id > prev_max:
            sensor_max_frames[sensor_id] = frame_id

    embeddings_data = {
        "entry_ids": np.array(entry_ids_list),
        "object_ids": np.array(object_ids_list),
        "sensor_ids": np.array(sensor_ids_list),
        "frame_ids": np.array(frame_ids_list),
        "vectors": np.array(vectors_list, dtype=np.float32),
    }
    compression_args = Namespace(**args_dict)
    (entry_ids, obj_ids, sensor_ids, frame_ids, weights, sampled_vectors, means, _outlier_data, stats) = (
        process_single_object(object_id, embeddings_data, compression_args)
    )
    sampled = []
    for i in range(len(entry_ids)):
        vec = sampled_vectors[i]
        row = {
            "entry_id": int(entry_ids[i]),
            "object_id": str(obj_ids[i]),
            "sensor_id": str(sensor_ids[i]),
            "frame_id": int(frame_ids[i]),
            "weight": int(weights[i]),
            "vector": vec.tolist() if hasattr(vec, "tolist") else list(vec),
        }
        if means is not None:
            row["mean"] = means[i].tolist()
        sampled.append(row)
    return {
        "object_id": object_id,
        "original_count": original_count,
        "stats": stats,
        "sampled": sampled,
        "sensor_max_frames": sensor_max_frames,
    }


def _get_compression_process_pool() -> Optional[ProcessPoolExecutor]:
    global _compression_process_pool
    if MAINTENANCE_COMPRESSION_POOL_WORKERS <= 1:
        return None
    if _compression_process_pool is not None:
        return _compression_process_pool
    with _compression_process_pool_lock:
        if _compression_process_pool is None:
            _compression_process_pool = ProcessPoolExecutor(
                max_workers=MAINTENANCE_COMPRESSION_POOL_WORKERS,
                initializer=_compression_pool_worker_init,
            )
            _per_worker = _compression_pool_threads_per_worker()
            logger.info(
                f"Compression process pool started with "
                f"{MAINTENANCE_COMPRESSION_POOL_WORKERS} workers "
                f"({_per_worker} BLAS/FAISS thread(s) each, "
                f"budget={MAINTENANCE_MAX_THREADS})"
            )
    return _compression_process_pool


def _merge_compression_object_result(
    result: Dict,
    all_sampled: List[dict],
    per_object_results: List[dict],
    sensor_id_to_max_processed_frame_id: SensorIdToMaxFrameIdMap,
) -> None:
    all_sampled.extend(result["sampled"])
    stats = result["stats"]
    per_object_results.append(
        {
            "object_id": result["object_id"],
            "original_count": result["original_count"],
            "after_outlier_removal": stats["after_outlier_removal"],
            "after_cosine_prune": stats["after_cosine_prune"],
            "final_sampled": stats["final_sampled"],
        }
    )
    for sensor_id, frame_id in result["sensor_max_frames"].items():
        prev_max = sensor_id_to_max_processed_frame_id.get(sensor_id)
        if prev_max is None or frame_id > prev_max:
            sensor_id_to_max_processed_frame_id[sensor_id] = frame_id


def _compression_args_dict(compression_args: Namespace) -> Dict[str, Union[float, int]]:
    return {
        "outlier_threshold": compression_args.outlier_threshold,
        "cosine_threshold": compression_args.cosine_threshold,
        "max_samples": compression_args.max_samples,
        "merge_max_distance": compression_args.merge_max_distance,
        "with_moments": compression_args.with_moments,
        "seed": compression_args.seed,
    }


def _compressed_summary_changed(new_item: dict, prev_item: dict) -> bool:
    """Whether a surviving compressed row's cluster summary needs rewriting.

    Weight is checked first because it changes in almost every case a
    reassignment happened. The mean is still compared when the weight matches:
    absorbing one representative while shedding another of equal weight leaves
    the count intact but shifts the mean. Without moments the weight is the
    whole summary, so it is also the whole test.
    """
    if int(new_item["weight"]) != int(prev_item["weight"]):
        return True
    if not ENABLE_CLT_ESTIMATION:
        return False
    return not np.array_equal(
        np.asarray(new_item["mean"], dtype=np.float32),
        np.asarray(prev_item["mean"], dtype=np.float32),
    )


def _compression_merge_recompress_worker(
    task: Tuple[str, List[dict], List[dict], Dict[str, Union[float, int]]],
) -> Dict:
    """Merge new samples with prior compressed rows and recompress (picklable)."""
    object_id, new_items, prev_compressed_items, args_dict = task
    prev_entry_ids = [item["entry_id"] for item in prev_compressed_items]
    prev_by_entry_id = {item["entry_id"]: item for item in prev_compressed_items}
    merged_items = list(new_items)
    merged_items.extend(prev_compressed_items)
    recompressed_items = recompress_single_object(merged_items, Namespace(**args_dict))
    new_entry_id_set = {item["entry_id"] for item in recompressed_items}
    prev_entry_id_set = set(prev_entry_ids)
    items_to_insert = []
    items_to_update = []
    for item in recompressed_items:
        if item["entry_id"] not in prev_entry_id_set:
            # Genuinely new sample selected during recompression.
            items_to_insert.append(item)
        elif _compressed_summary_changed(item, prev_by_entry_id[item["entry_id"]]):
            # Surviving sample whose summary changed because recompression
            # reassigned merged vectors to it; persist the new one.
            items_to_update.append(item)
    return {
        "object_id": object_id,
        "prev_entry_ids": prev_entry_ids,
        "items_to_insert": items_to_insert,
        "items_to_update": items_to_update,
        "items_to_delete": [
            item["entry_id"] for item in prev_compressed_items if item["entry_id"] not in new_entry_id_set
        ],
    }


def _apply_merge_recompress_result(
    result: Dict,
    items_to_insert: List[dict],
    items_to_update: List[dict],
    items_to_delete: List[int],
) -> None:
    object_id = result["object_id"]
    logger.info(f"Previous compressed ids for object_id: {object_id}: {result['prev_entry_ids']}")
    for entry_id in result["items_to_delete"]:
        items_to_delete.append(entry_id)
        logger.info(f"Deleting previous compressed item: {entry_id}")
    for item in result["items_to_insert"]:
        items_to_insert.append(item)
        logger.info(f"Inserting new compressed item: {item['entry_id']}")
    for item in result.get("items_to_update", []):
        items_to_update.append(item)
        logger.info(f"Updating summary of compressed item: {item['entry_id']} " f"-> weight {item['weight']}")


def _merge_recompress_clusters(
    clusters: Dict[str, List[dict]],
    prev_by_object_id: Dict[str, list],
    compression_args: Namespace,
    items_to_insert: List[dict],
    items_to_update: List[dict],
    items_to_delete: List[int],
) -> None:
    args_dict = _compression_args_dict(compression_args)
    recompress_tasks = []
    for object_id, items in clusters.items():
        prev_compressed_items = prev_by_object_id.get(object_id, [])
        if prev_compressed_items:
            recompress_tasks.append((object_id, items, prev_compressed_items, args_dict))
        else:
            items_to_insert.extend(items)

    pool = _get_compression_process_pool()
    if pool is None or len(recompress_tasks) < 2:
        for task in recompress_tasks:
            result = _compression_merge_recompress_worker(task)
            _apply_merge_recompress_result(result, items_to_insert, items_to_update, items_to_delete)
        return

    futures = [pool.submit(_compression_merge_recompress_worker, task) for task in recompress_tasks]
    for future in as_completed(futures):
        result = future.result()
        _apply_merge_recompress_result(result, items_to_insert, items_to_update, items_to_delete)


def _process_compression_clusters(
    clusters: Dict[str, List[dict]],
    compression_args: Namespace,
    all_sampled: List[dict],
    per_object_results: List[dict],
    sensor_id_to_max_processed_frame_id: SensorIdToMaxFrameIdMap,
) -> None:
    args_dict = _compression_args_dict(compression_args)
    pool = _get_compression_process_pool()
    if pool is None or len(clusters) < 2:
        for object_id, items in clusters.items():
            result = _compression_process_object_worker((object_id, items, args_dict))
            _merge_compression_object_result(
                result,
                all_sampled,
                per_object_results,
                sensor_id_to_max_processed_frame_id,
            )
        return

    tasks = [(object_id, items, args_dict) for object_id, items in clusters.items()]
    futures = [pool.submit(_compression_process_object_worker, task) for task in tasks]
    for future in as_completed(futures):
        result = future.result()
        _merge_compression_object_result(
            result,
            all_sampled,
            per_object_results,
            sensor_id_to_max_processed_frame_id,
        )


# ---------------------------------------------------------------------------
# Online clustering execution (helpers used by the maintenance worker)
# ---------------------------------------------------------------------------
def _snapshot_dbs(
    batch_idx: int,
    sections: Sequence[str] = ("main", "tracking", "compressed"),
    stage: str = "",
) -> Dict[str, str]:
    """Bind service-scoped Milvus + tracking handles to
    cluster_service.write_db_snapshots. Tracking comes from tracking_store
    (a consistent snapshot via get_all()), so no external lock is needed.
    """
    if "compressed" in sections:
        try:
            ensure_compressed_collection_loaded()
        except Exception:
            pass
    return write_db_snapshots(
        batch_idx,
        output_dir=CLUSTER_ASSIGNMENTS_DIR,
        main_collection=collection,
        compressed_collection=compressed_collection,
        tracking_db=tracking_store.get_all(),
        tracking_lock=nullcontext(),
        sections=sections,
        stage=stage,
        iterator_batch_size=ITERATOR_BATCH_SIZE,
        logger=logger,
    )


class _PhaseTimer:
    """Records (phase_name, duration_s) blocks via context manager and prints a
    single summary line at the end. Used to break down time inside clustering
    and compression. Negligible overhead, so always-on."""

    def __init__(self, label: str):
        self._label = label
        self._rows: List[Tuple[str, float]] = []

    def phase(self, name: str):
        return _PhaseTimer._Phase(self, name)

    def summary(self) -> str:
        total = sum(d for _, d in self._rows)
        body = ", ".join(f"{n}={d:.2f}s" for n, d in self._rows)
        return f"{self._label} phases (total={total:.2f}s): {body}"

    class _Phase:
        def __init__(self, owner: "_PhaseTimer", name: str):
            self._owner = owner
            self._name = name

        def __enter__(self):
            self._t0 = time.perf_counter()
            return self

        def __exit__(self, *_):
            self._owner._rows.append((self._name, time.perf_counter() - self._t0))
            return False


# Deterministic mode: per-tick bbox index built ONCE per tick from the tracking
# store, so tracking_prep does O(window-frames) lookups per window instead of
# re-scanning the whole tracking DB on every window. Frame-bucketed:
# frame_id -> {(object_id, sensor_id): bbox}. Rebuilt fresh each tick.
# _tick_tracking_keys is the flat key-set for the coverage check.
_tick_tracking_by_frame: Dict[int, dict] = {}
_tick_tracking_keys: set = set()


def _build_tick_tracking_index() -> None:
    """Snapshot the tracking store into a frame-bucketed bbox index, once/tick."""
    global _tick_tracking_by_frame, _tick_tracking_keys
    by_frame: Dict[int, dict] = {}
    keys: set = set()
    for streams in tracking_store.get_all().values():
        for stream_id, tracked_list in streams.items():
            for obj in tracked_list:
                if obj.is_clustered:
                    continue
                fr = int(obj.frame_id)
                oid = int(obj.object_id)
                by_frame.setdefault(fr, {})[(oid, stream_id)] = {
                    "leftX": obj.leftX,
                    "topY": obj.topY,
                    "rightX": obj.rightX,
                    "bottomY": obj.bottomY,
                }
                keys.add((oid, stream_id, fr))
    _tick_tracking_by_frame = by_frame
    _tick_tracking_keys = keys


def _det_refill_from_main() -> None:
    """Deterministic mode: pull NEW un-clustered rows from main ONCE per tick.

    Reads via the entry_id cursor (each row fetched from Milvus exactly once),
    appends fresh rows to the in-memory ``_det_window_buffer``, advances the
    cursor, and re-sorts the buffer by (frame_id, sensor_id, object_id,
    entry_id). The maintenance loop calls this once per tick, then drains the
    buffer into fixed windows WITHOUT any further Milvus reads.
    """
    global _max_entry_id_seen
    if _cluster_manager is None:
        return
    seen_entry_ids = _cluster_manager.archive.seen_entry_ids
    pending_entry_ids = {int(r.entry_id) for r in _pending_records}
    buffered_ids = {int(r.entry_id) for r in _det_window_buffer}
    iterator = collection.query_iterator(
        batch_size=ITERATOR_BATCH_SIZE,
        expr=f"is_clustered == false && entry_id > {_max_entry_id_seen}",
        output_fields=["entry_id", "object_id", "sensor_id", "frame_id", "vector"],
    )
    raw_rows: List[dict] = []
    try:
        while True:
            page = iterator.next()
            if not page:
                iterator.close()
                break
            raw_rows.extend(page)
    except Exception:
        iterator.close()
        raise
    if not raw_rows:
        return
    for row in raw_rows:
        eid = int(row["entry_id"])
        if eid in seen_entry_ids or eid in pending_entry_ids or eid in buffered_ids:
            continue
        _det_window_buffer.append(EmbeddingRecord.from_dict(row))
    _max_entry_id_seen = max(int(r["entry_id"]) for r in raw_rows)
    _det_window_buffer.sort(key=lambda e: (int(e.frame_id), str(e.sensor_id), int(e.object_id), int(e.entry_id)))


def _run_online_clustering_batch() -> Optional[dict]:
    """Run one online clustering pass over entries that have not yet been
    clustered.

    Called from the maintenance loop after the trigger has already fired.
    Skips when the un-clustered embedding count is below
    ``TRIGGER_MIN_BATCH``. In the non-deterministic path the entire
    un-clustered set read this tick is clustered in one batch (no per-tick
    cap). In the deterministic path work is drained in fixed
    ``TRIGGER_BATCH_LIMIT`` windows across the tick's window loop.

    Updates after ``process_batch`` returns:
      * main: clustered rows are upserted with ``object_id`` set to the
        cluster's representative tracker_id and ``is_clustered=True``;
        Stage 2A-filtered rows are deleted.
      * tracking: matching entries are moved under the rep ``object_id``
        with ``is_clustered=True`` in both the maintenance and main
        process copies.
      * compressed: on cluster merges, rows under retired reps are
        relabeled to the surviving rep so prior per-cluster samples
        stay consistent with archive state.

    Returns a stats dict for the batch that fired, or ``None`` if nothing
    was processed this tick.
    """
    global _pending_records, _max_entry_id_seen, _det_window_buffer
    if _cluster_manager is None:
        return None

    # Server-side filter on is_clustered=False is the source of truth
    # for "row hasn't been clustered yet" -- it survives maintenance
    # restarts that wipe in-memory state. seen_entry_ids is still kept
    # as a defensive secondary check in case a row's column is somehow
    # out of sync (e.g. a partial relabel write).
    seen_entry_ids = _cluster_manager.archive.seen_entry_ids

    timer = _PhaseTimer("Clustering")

    def _emit_timer():
        if timer._rows:
            logger.warning(timer.summary())

    # read_main is split into two nested sub-phases so the per-tick
    # phase summary attributes time between:
    #   * read_main_query  : Milvus server-side filter + paginated
    #                        gRPC transfer (network + protobuf decode).
    #   * read_main_build  : Python loop turning each raw row dict into
    #                        an EmbeddingRecord (incl. vector list ->
    #                        numpy conversion).
    # If read_main_query dominates, the bottleneck is Milvus / network.
    # If read_main_build dominates, the bottleneck is Python CPU.
    if CLUSTER_DETERMINISTIC:
        # Read-once-per-tick: _det_refill_from_main() (called once per tick by the
        # maintenance loop) has already pulled new rows into the buffer and sorted
        # it by (frame_id, sensor_id, object_id, entry_id). Here we ONLY window
        # the buffer in memory -- no Milvus read -- so the drain loop runs many
        # fixed TRIGGER_BATCH_LIMIT-row windows per tick cheaply ("cluster 100,
        # then the next 100, in one batch", made O(n) instead of O(n^2)). Mirrors
        # the notebook iloc windows; the watermark just guarantees every row of a
        # frame has arrived before that frame is windowed.
        if not _det_window_buffer:
            return None
        frontier = int(_det_window_buffer[-1].frame_id)  # sorted asc -> last = max
        watermark = frontier - CLUSTER_WATERMARK_LAG
        n_settled = 0
        for _e in _det_window_buffer:
            if int(_e.frame_id) <= watermark:
                n_settled += 1
            else:
                break
        if n_settled < TRIGGER_BATCH_LIMIT:
            _emit_timer()
            return None
        embeddings = _det_window_buffer[:TRIGGER_BATCH_LIMIT]
        _det_window_buffer = _det_window_buffer[TRIGGER_BATCH_LIMIT:]
        logger.warning(
            f"Deterministic mode: read {len(embeddings)} embeddings from the buffer for processing. "
            f"Pending embeddings: {len(_det_window_buffer)}"
        )
    else:
        with timer.phase("read_main"):
            with timer.phase("read_main_query"):
                # Server-side cursor excludes rows already pulled in past ticks.
                _cursor_expr = f"is_clustered == false && entry_id > {_max_entry_id_seen}"
                iterator = collection.query_iterator(
                    batch_size=ITERATOR_BATCH_SIZE,
                    expr=_cursor_expr,
                    output_fields=["entry_id", "object_id", "sensor_id", "frame_id", "vector"],
                )
                raw_rows: List[dict] = []
                try:
                    while True:
                        page = iterator.next()
                        if not page:
                            iterator.close()
                            break
                        raw_rows.extend(page)
                except Exception:
                    iterator.close()
                    raise
                _candidate_cursor = max(int(r["entry_id"]) for r in raw_rows) if raw_rows else _max_entry_id_seen

            with timer.phase("read_main_build"):
                pending_entry_ids = {int(r.entry_id) for r in _pending_records}
                embeddings: List[EmbeddingRecord] = []
                for row in raw_rows:
                    eid = int(row["entry_id"])
                    if eid in seen_entry_ids:
                        continue
                    if eid in pending_entry_ids:
                        continue
                    embeddings.append(EmbeddingRecord.from_dict(row))

        if len(embeddings) < TRIGGER_MIN_BATCH:
            logger.warning(
                "Clustering skipped: %d un-clustered embeddings < minimum %d "
                "(cursor held at %d so rows can accumulate next tick)",
                len(embeddings),
                TRIGGER_MIN_BATCH,
                _max_entry_id_seen,
            )
            _emit_timer()
            return None

        # We're committed to processing this batch -- safe to advance the cursor.
        if _candidate_cursor > _max_entry_id_seen:
            logger.warning(
                "read_main cursor advanced: %d -> %d (read %d new rows, " "processing all)",
                _max_entry_id_seen,
                _candidate_cursor,
                len(raw_rows),
            )
            _max_entry_id_seen = _candidate_cursor

        # Sort by frame so the sub-tracklet accumulator sees a
        # temporally-coherent stream.
        embeddings.sort(key=lambda e: (int(e.frame_id), str(e.sensor_id), int(e.object_id), int(e.entry_id)))

    # ---- Sub-tracklet accumulator (notebook's Option B / pending-list buffer) ----
    # Union this tick's new arrivals with held-back records from prior ticks,
    # run Stage 1 globally, and ship only sub-tracklets that meet the
    # min_embs_threshold floor. The rest stay in _pending_records for the
    # next tick. Aged-out hold records (older than CLUSTER_MAX_FRAME_AGE
    # frames behind the current max frame_id) are evicted and deleted from
    # Milvus alongside the Stage-2A-filtered records below.
    new_records = embeddings
    n_pending_in = len(_pending_records)
    all_records = _pending_records + new_records

    with timer.phase("pending_split"):
        pre_subs = split_batch_into_subtracklets(
            all_records,
            batch_idx=len(_cluster_manager.archive.batch_log),
            cos_threshold=_cluster_manager.params.cos_threshold,
        )
    ship_entry_ids: Set[int] = set()
    for st in pre_subs.values():
        if len(st.vecs) > _cluster_manager.params.min_embs_threshold:
            ship_entry_ids.update(int(eid) for eid in st.entry_ids)
    ship_records = [r for r in all_records if int(r.entry_id) in ship_entry_ids]
    hold_records = [r for r in all_records if int(r.entry_id) not in ship_entry_ids]

    current_max_frame = max((int(r.frame_id) for r in all_records), default=0)
    kept_after_age = [r for r in hold_records if current_max_frame - int(r.frame_id) <= CLUSTER_MAX_FRAME_AGE]
    n_evicted_age = len(hold_records) - len(kept_after_age)
    evicted_eids: Set[int] = {int(r.entry_id) for r in hold_records} - {int(r.entry_id) for r in kept_after_age}

    if len(kept_after_age) > CLUSTER_MAX_PENDING_ROWS:
        kept_after_age.sort(key=lambda r: int(r.frame_id))
        n_evicted_cap = len(kept_after_age) - CLUSTER_MAX_PENDING_ROWS
        evicted_eids |= {int(r.entry_id) for r in kept_after_age[:n_evicted_cap]}
        _pending_records = kept_after_age[n_evicted_cap:]
    else:
        n_evicted_cap = 0
        _pending_records = kept_after_age

    # Per-object_id breakdown for the two inputs to the accumulator.
    # Pending side is the carry-over from prior ticks (first n_pending_in
    # entries of all_records, by construction); new side is this tick's
    # fresh Milvus arrivals. Keys are sorted ascending so raw 10xxx and
    # shifted 20xxx ids group naturally.
    def _oid_counts(_records) -> Dict[int, int]:
        _c: Dict[int, int] = {}
        for _r in _records:
            _o = int(_r.object_id)
            _c[_o] = _c.get(_o, 0) + 1
        return dict(sorted(_c.items(), key=lambda kv: kv[0]))

    _pending_in_oids = _oid_counts(all_records[:n_pending_in])
    _new_in_oids = _oid_counts(new_records)
    logger.warning(
        "Pending buffer: pending_in=%d new_in=%d ship=%d hold=%d "
        "evicted_age=%d evicted_cap=%d pending_out=%d max_frame=%d  "
        "pending_in_oids=%s  new_in_oids=%s",
        n_pending_in,
        len(new_records),
        len(ship_records),
        len(hold_records),
        n_evicted_age,
        n_evicted_cap,
        len(_pending_records),
        current_max_frame,
        _pending_in_oids,
        _new_in_oids,
    )

    embeddings = ship_records
    if not embeddings:
        # Nothing qualifies this tick; everything went into the pending
        # buffer (or was evicted). Still need to delete evicted rows from
        # Milvus so they don't loop back, but skip the rest of the pipeline.
        if evicted_eids:
            try:
                delete_filtered_from_main(
                    collection,
                    list(evicted_eids),
                    batch_size=LOAD_DELETE_BATCH_SIZE,
                )
                logger.warning(
                    "Pending buffer: %d evicted rows deleted from main; " "0 ship_records this tick",
                    len(evicted_eids),
                )
            except Exception as e:
                logger.error(f"Failed to delete evicted pending rows: {e}")
        _emit_timer()
        return None

    with timer.phase("tracking_prep"):
        # Coverage joins on raw tracker ids. Filter per-entry on
        # is_clustered=False so a bucket whose key collides with a cluster
        # rep still contributes its un-clustered entries (a real DeepStream
        # case: cluster rep "3" and a live tracker 3 share the same bucket
        # because rep is itself a past tracker_id).
        max_allowed_untracked_embeddings = CLUSTERING_MAX_UNTRACKED_EMBEDDINGS_FRACTION * len(embeddings)
        min_frame = min(r.frame_id for r in embeddings)
        max_frame = max(r.frame_id for r in embeddings)
        n_missing = 0
        sample = []
        tracking: Dict[tuple, dict] = {}
        # Re-read tracking coverage each attempt: the tracking DB is still
        # ingesting in the background, so a shortfall now may resolve within a
        # few retries. Skip the batch (return None) only after exhausting them.
        for _untracked_attempt in range(CLUSTERING_UNTRACKED_MAX_RETRIES + 1):
            if CLUSTER_DETERMINISTIC:
                # Per-tick frame-bucketed index (built once this tick); fixed for
                # the tick, so retries can't change deterministic-path coverage.
                available_keys = _tick_tracking_keys
            else:
                # One index-backed, frame-scoped, is_clustered=0 query instead of
                # a full-table get_all() + per-row pydantic materialisation. The
                # result is already in `tracking` shape
                # ({(oid, sid, frame): bbox}), so on the accepting attempt we
                # reuse it directly -- no second scan to build `tracking`.
                tracking = tracking_store.unclustered_bboxes_in_frame_range(min_frame, max_frame)
                available_keys = tracking.keys()
            coverage = tracking_coverage(embeddings, available_keys)
            n_missing = coverage.n_missing
            sample = coverage.sample_missing_keys
            if n_missing <= max_allowed_untracked_embeddings:
                break
            if _untracked_attempt >= CLUSTERING_UNTRACKED_MAX_RETRIES:
                logger.warning(
                    "Clustering skipped: tracking incomplete after %d retries "
                    "(missing %d of %d entries, threshold=%.0f; sample keys=%s)",
                    _untracked_attempt,
                    n_missing,
                    len(embeddings),
                    max_allowed_untracked_embeddings,
                    sample,
                )
                _emit_timer()
                return None
            logger.warning(
                "Waiting for tracking database to update: missing %d of %d entries, " "threshold=%.0f; sample keys=%s",
                n_missing,
                len(embeddings),
                max_allowed_untracked_embeddings,
                sample,
            )
            try:
                snap_paths = _snapshot_dbs(
                    len(_cluster_manager.archive.batch_log),
                    stage=f"untracked_retry{_untracked_attempt}",
                )
                logger.info(
                    "DB snapshots (untracked_retry attempt %d) saved: %s",
                    _untracked_attempt,
                    snap_paths,
                )
            except Exception as e:
                logger.error("Failed to write untracked-retry DB snapshots: %s", e)
            time.sleep(CLUSTERING_UNTRACKED_RETRY_DELAY)

        # Coverage acceptable -- build the tracking lookup for this batch. The
        # non-deterministic path already populated `tracking` above (the
        # frame-scoped bbox query is exactly the lookup we need).
        if CLUSTER_DETERMINISTIC:
            # O(distinct frames in range) lookups into the per-tick index.
            for fr, per_obj in _tick_tracking_by_frame.items():
                if min_frame <= fr <= max_frame:
                    for (oid, sid), bbox in per_obj.items():
                        tracking[(oid, sid, fr)] = bbox
    if n_missing > 0:
        logger.warning(
            "Clustering proceeding with %d missing tracking entries " "(within threshold %.0f); sample keys=%s",
            n_missing,
            max_allowed_untracked_embeddings,
            sample,
        )

    # Pre-clustering snapshot (debug-only). ClusterManager.process_batch
    # uses ``len(archive.batch_log)`` as the implicit batch_idx when none
    # is passed, so we can preview it here without grabbing the lock.
    if CLUSTER_DB_SNAPSHOT_MODE == "every_batch":
        with timer.phase("snapshot_initial"):
            pending_batch_idx = len(_cluster_manager.archive.batch_log)
            try:
                snap_paths = _snapshot_dbs(pending_batch_idx, stage="initial")
                logger.info(f"DB snapshots (initial) saved: {snap_paths}")
            except Exception as e:
                logger.error(f"Failed to write initial DB snapshots: {e}")

    with timer.phase("process_batch"):
        with _cluster_manager_lock:
            # Snapshot each cluster's emitted rep object_id BEFORE the merge.
            # A merge can change a *surviving* cluster's rep (it adopts the
            # absorbed cluster's earlier-min_frame rep); the cascade below
            # diffs against this snapshot so the survivor's prior rows are
            # migrated and not orphaned under the stale rep.
            _arch_pre = _cluster_manager.archive
            pre_rep_object_ids = {
                int(cid): cluster_id_to_object_id(int(cid), _arch_pre) for cid in _arch_pre.rep_object_id
            }
            result = _cluster_manager.process_batch(embeddings, tracking)
            last_subs = dict(_cluster_manager.last_subtracklets)

    # Resolve retired -> surviving cluster reps once for this batch.
    # Used by the main-DB, tracking-DB, and compressed-DB cascades below
    # so they all relabel against the same source of truth (the archive's
    # rep_object_id at this point in time, before any later batch can
    # mutate it). Empty when no merges happened.
    retired_map = retired_to_surviving_from_groups(result.group_info)
    archive = _cluster_manager.archive

    # Per-batch object_id summary: how many embeddings per raw tracker_id
    # this batch saw (total) and per cluster the algorithm assigned. The
    # total view shows ingest mix; the per-cluster view reveals which raw
    # trackers each cluster pulled together (multi-camera linking when
    # many distinct obj_ids land in one cluster; single-tracker re-id
    # when few do).
    _batch_obj_id_counts: Dict[int, int] = {}
    for _e in embeddings:
        _oid = int(_e.object_id)
        _batch_obj_id_counts[_oid] = _batch_obj_id_counts.get(_oid, 0) + 1
    logger.warning(
        "Batch obj_id counts (%d distinct, %d total embeddings): %s",
        len(_batch_obj_id_counts),
        sum(_batch_obj_id_counts.values()),
        dict(sorted(_batch_obj_id_counts.items(), key=lambda kv: kv[0])),
    )

    # Per-cluster CUMULATIVE obj_id breakdown across the cluster's full
    # ``members_history`` (every sub-tracklet ever absorbed by this cluster,
    # not just this batch's additions). Iterates EVERY active cluster in
    # the archive -- including ones that weren't touched this batch -- so
    # the picture is complete each tick. Each ``MemberHistoryEntry`` has a
    # single ``obj_id`` and a list of ``entry_ids``; the count contribution
    # is ``len(entry_ids)``.
    for _cid in sorted(archive.clusters):
        _cinfo = archive.clusters[_cid]
        _counts: Dict[int, int] = {}
        for _h in _cinfo.members_history:
            _oid = int(_h.obj_id)
            _counts[_oid] = _counts.get(_oid, 0) + len(_h.entry_ids)
        _rep = cluster_id_to_object_id(_cid, archive)
        _counts_sorted = dict(sorted(_counts.items(), key=lambda kv: kv[0]))
        logger.warning(
            "Cluster c%d (rep=%s, n=%d) obj_id counts (cumulative): %s",
            _cid,
            _rep,
            _cinfo.n_embs,
            _counts_sorted,
        )
    retired_rep_map: Dict[str, str] = {
        cluster_id_to_object_id(retired_cid, archive): cluster_id_to_object_id(surviving_cid, archive)
        for retired_cid, surviving_cid in retired_map.items()
    }
    # Also migrate prior rows of any *surviving* cluster that adopted an
    # absorbed cluster's rep during this merge. Without this, the surviving
    # cluster's old-rep rows are orphaned across the live, tracking, and
    # compressed DBs (the retired->surviving entry self-maps and is skipped),
    # splitting one identity across two object_ids.
    add_surviving_rep_transitions(
        retired_rep_map,
        set(retired_map.values()),
        pre_rep_object_ids,
        archive,
    )
    # Cluster stats are computed from the (post-clustering) main collection by
    # the maintenance loop's main_stats_cache.feed path -- no incremental
    # running-stats update needed here.
    if retired_map:
        merges_summary = ", ".join(
            f"c{r_cid}(rep={cluster_id_to_object_id(r_cid, archive)}) -> "
            f"c{s_cid}(rep={cluster_id_to_object_id(s_cid, archive)})"
            for r_cid, s_cid in retired_map.items()
        )
        logger.warning(f"Retired this batch ({len(retired_map)}): {merges_summary}")

    # Main-DB updates owned by clustering. Split into three timed phases
    # so we can attribute relabel-upsert vs Stage-2A-delete vs retired-
    # cascade cost separately in the per-tick phase summary.
    this_batch_eids = {int(e.entry_id) for e in embeddings}
    clustered_eids = set(int(k) for k in result.entry_to_cluster.keys())
    # Stage-2A-filtered ship records plus pending-buffer evictions (aged or
    # capped) both go through the same main-DB delete path.
    filtered_eids = (this_batch_eids - clustered_eids) | evicted_eids
    n_retired_main = 0
    try:
        with timer.phase("main_db_relabel"):
            n_relabeled = relabel_main_clusters(
                collection,
                embeddings,
                result.entry_to_cluster,
                _cluster_manager.archive,
                batch_size=LOAD_DELETE_BATCH_SIZE,
                logger=logger,
            )
        with timer.phase("main_db_delete"):
            n_filtered = delete_filtered_from_main(
                collection,
                list(filtered_eids),
                batch_size=LOAD_DELETE_BATCH_SIZE,
            )
        # Retired-cluster cascade for the main collection: prior-batch rows
        # still labeled with a now-retired rep are rewritten to the
        # surviving rep. Without this pass, search returns the same
        # identity split across two object_ids until those rows are
        # compressed away (which never happens when ENABLE_COMPRESSION is
        # off). Runs unconditionally; companion to the tracking-DB and
        # compressed-DB cascades below.
        with timer.phase("main_db_retired_relabel"):
            n_retired_main = relabel_retired_main_clusters(
                collection,
                retired_rep_map,
                batch_size=LOAD_DELETE_BATCH_SIZE,
                logger=logger,
            )
        logger.warning(
            "Main-DB updates: %d rows relabeled to cluster reps, "
            "%d Stage 2A rows deleted, %d rows cascaded from %d retired reps",
            n_relabeled,
            n_filtered,
            n_retired_main,
            len(retired_rep_map),
        )
    except Exception as e:
        logger.error(f"Failed main-DB relabel/filter: {e}")

    with timer.phase("tracking_relabel"):
        # Tracking-DB relabel (both process copies)
        rows_by_eid = {int(e.entry_id): e for e in embeddings}
        tracking_updates: List[Tuple[str, str, int, str]] = []
        for eid, cid in result.entry_to_cluster.items():
            rec = rows_by_eid.get(int(eid))
            if rec is None:
                continue
            tracking_updates.append(
                (
                    str(rec.object_id),
                    str(rec.sensor_id),
                    int(rec.frame_id),
                    cluster_id_to_object_id(cid, _cluster_manager.archive),
                )
            )
        try:
            # tracking_store.apply_relabel is dispatch-aware: called from the
            # maintenance worker it applies locally AND propagates to the owner
            # process, replacing the old local-apply + _cluster_label_update_queue.
            n_moved = tracking_store.apply_relabel(tracking_updates)
            logger.info(
                "Tracking-store relabel: %d entries moved to cluster reps",
                n_moved,
            )
        except Exception as e:
            logger.error(f"Failed tracking-store relabel: {e}")

        # On cluster merges, migrate tracking entries from retired cluster
        # keys to the surviving key (mirrors the main-DB and compressed-DB
        # cascades). ``retired_map`` is resolved once above so all three
        # surfaces relabel against the same archive snapshot.
        retired_tracking_updates: List[Tuple[str, str, int, str]] = []
        if retired_rep_map:
            try:
                # Drive tracking off the SAME augmented rep->rep map as the
                # live and compressed cascades so surviving-cluster rep
                # transitions are migrated here too.
                retired_tracking_updates = build_tracking_updates_from_rep_map(
                    tracking_store.get_all(),
                    nullcontext(),
                    retired_rep_map,
                )
            except Exception as e:
                logger.error(f"Failed to build retired-cluster tracking updates: {e}")
        if retired_tracking_updates:
            try:
                # Retired-cluster cascade migrates rows that were already
                # clustered in a prior batch, so match clustered source rows.
                n_cascaded = tracking_store.apply_relabel(retired_tracking_updates, source_clustered=True)
                logger.info(
                    "Tracking-store merge cascade: %d entries moved from retired "
                    "to surviving cluster reps (%d retired ids)",
                    n_cascaded,
                    len(retired_map),
                )
            except Exception as e:
                logger.error(f"Failed tracking-store merge cascade: {e}")

    with timer.phase("write_assignments"):
        try:
            assignments_path = write_batch_assignments(
                result,
                last_subs,
                _cluster_manager.archive,
                min_frame,
                max_frame,
                CLUSTER_ASSIGNMENTS_DIR,
            )
            logger.info(
                "Clustering assignments saved -> %s (%d entries)",
                assignments_path,
                len(result.entry_to_cluster),
            )
        except Exception as e:
            logger.error(f"Failed to write clustering assignments: {e}")

    with timer.phase("snapshot_post"):
        if CLUSTER_DB_SNAPSHOT_MODE == "every_batch":
            try:
                snap_paths = _snapshot_dbs(int(result.stats.batch), stage="post_clustering")
                logger.info(f"DB snapshots (post-clustering) saved: {snap_paths}")
            except Exception as e:
                logger.error(f"Failed to write post-clustering DB snapshots: {e}")

        # Retroactive relabel for the compressed collection. Uses the
        # ``retired_rep_map`` resolved once above so the main, tracking,
        # and compressed cascades all share the same archive snapshot.
        if ENABLE_COMPRESSION and retired_rep_map:
            try:
                ensure_compressed_collection_loaded()
                n_rewritten = relabel_compressed_clusters(
                    compressed_collection,
                    retired_rep_map,
                    batch_size=LOAD_DELETE_BATCH_SIZE,
                    logger=logger,
                    include_mean=ENABLE_CLT_ESTIMATION,
                )
                logger.info(
                    f"Clustering merges this batch: {len(retired_rep_map)} retired ids, "
                    f"{n_rewritten} compressed rows relabeled"
                )
            except Exception as e:
                logger.error(f"Failed to relabel compressed clusters: {e}")

    _emit_timer()
    return {
        "frame_span": [min_frame, max_frame],
        "batch": result.stats.batch,
        "n_groups": result.stats.n_groups,
        "n_new_clusters": result.stats.n_new_clusters,
        "n_updated": result.stats.n_updated,
        "n_unchanged": result.stats.n_unchanged,
        "n_retired": result.stats.n_retired,
        "n_clusters_total": result.stats.n_clusters_total,
        "n_cm_pairs": result.stats.n_cm_pairs,
        "n_embeddings_assigned": len(result.entry_to_cluster),
        "n_embeddings_processed": len(embeddings),
    }


def _maintenance_worker(ready_event=None):
    """Target for the maintenance process."""
    _ignore_sigint_in_worker()
    # Phase 1: no-op for in-memory backends. SQLite backends open their
    # per-process connections here (and the cluster-stats stores drop the
    # inherited read view so the next read reloads from the shared DB).
    tracking_store.attach_post_fork()
    main_stats_cache.attach_post_fork()
    compressed_stats_cache.attach_post_fork()
    global collection, compressed_collection, logger

    logger = logging.getLogger("maintenance")

    _drop_inherited_milvus_aliases("default", "compressed")

    # Reassign module-level collection handles to use dedicated maintenance connections.
    # Main collection on the main Milvus instance, compressed on the compressed instance.
    connections.connect(alias="maintenance", host=MILVUS_HOST, port=MILVUS_PORT)
    connections.connect(alias="maintenance_compressed", host=MILVUS_COMPRESSED_HOST, port=MILVUS_COMPRESSED_PORT)
    collection = Collection(COLLECTION_NAME, using="maintenance")
    collection.load()
    compressed_collection = Collection(COMPRESSED_COLLECTION_NAME, using="maintenance_compressed")
    compressed_collection.load()
    # Reset load flags so ensure_*_loaded() works with the new handles.
    global collection_loaded, compressed_collection_loaded
    collection_loaded = False
    compressed_collection_loaded = False

    logger.info("Maintenance process started with dedicated Milvus connection")

    _maintenance_set_max_threads()

    if ready_event is not None:
        ready_event.set()

    # Track wall-clock time of the last successful compression so we can
    # honor COMPRESSION_INTERVAL_SEC. Initialized to "now" so the first
    # time-based fire is one full interval after process start.
    last_compression_ts = time.monotonic()
    last_cluster_stats_ts = time.monotonic()

    # Shared clustering+compression trigger state (used only when
    # ENABLE_CLUSTERING). last_trigger_ts paces the unified tick;
    # first_trigger_pending is a one-shot warm-up gate; consecutive_skip_ticks /
    # final_snapshot_saved drive the final-only DB snapshot once ingestion winds
    # down.
    last_trigger_ts = time.monotonic()
    first_trigger_pending = True
    consecutive_skip_ticks = 0
    final_snapshot_saved = False

    while True:
        try:
            ensure_collection_loaded()
            ensure_compressed_collection_loaded()

            elapsed = time.monotonic() - last_cluster_stats_ts
            do_processing_cluster_stats = _new_data_event.is_set() and elapsed >= CLUSTER_STATS_INTERVAL

            if do_processing_cluster_stats:
                _new_data_event.clear()
                if not CUSTOM_GROUP_SEARCH:
                    # Flush the collection
                    logger.info("Performing periodic collection flush")
                    collection.flush()
                    logger.info(f"Collection flushed. Current count: {collection.num_entities}")
                try:
                    results = collection.query(expr="", output_fields=["count(*)"])
                    collection_count = results[0]["count(*)"] if results else 0
                except Exception as e:
                    logger.warning(f"Could not get collection count: {str(e)}")
                    collection_count = 0
                logger.info(f"Collection count before stats calculation: {collection_count}")
                # Update cluster statistics
                logger.debug("Calculating cluster statistics")
                try:
                    stats = _compute_cluster_stats_from_collection(
                        collection,
                        expr=("is_clustered == true" if ENABLE_CLUSTERING else ""),
                    )
                    if stats:
                        main_stats_cache.replace(stats)
                    last_cluster_stats_ts = time.monotonic()
                    logger.info(f"Computed {len(stats) if stats else 0} main cluster stats")
                except Exception as e:
                    logger.error(f"Error computing cluster stats: {str(e)}")
            else:
                if elapsed >= CLUSTER_STATS_INTERVAL:
                    try:
                        results = collection.query(expr="", output_fields=["count(*)"])
                        collection_count = results[0]["count(*)"] if results else 0
                    except Exception as e:
                        logger.warning(f"Could not get collection count: {str(e)}")
                        collection_count = 0
                    logger.info(f"Collection not changed. Current count: {collection_count}")
                    last_cluster_stats_ts = time.monotonic()
        except Exception as e:
            logger.error(f"Error in maintenance cluster stats loop: {str(e)}")

        # --- Clustering + (coupled) compression -----------------------------
        # When clustering is enabled, clustering and compression fire TOGETHER
        # on a single shared trigger: clustering relabels live-DB rows to their
        # cluster rep, then (if compression is enabled) compression runs on that
        # result. When clustering is disabled, fall back to the standalone
        # compression trigger below.
        if ENABLE_CLUSTERING:
            try:
                results = collection.query(
                    expr=f"is_clustered == false && entry_id > {_max_entry_id_seen}", output_fields=["count(*)"]
                )
                unclustered_count = results[0]["count(*)"] if results else 0
            except Exception as e:
                logger.warning(f"Could not get un-clustered count: {str(e)}")
                unclustered_count = 0

            elapsed = time.monotonic() - last_trigger_ts
            size_trigger = unclustered_count >= TRIGGER_BATCH_LIMIT
            time_trigger = (
                TRIGGER_INTERVAL_SEC > 0 and unclustered_count >= TRIGGER_MIN_BATCH and elapsed >= TRIGGER_INTERVAL_SEC
            )
            if TRIGGER_INTERVAL_SEC > 0:
                would_fire = time_trigger
            else:
                would_fire = size_trigger
            if would_fire and first_trigger_pending and unclustered_count < FIRST_TRIGGER_MIN_BATCH:
                logger.info(
                    "First-trigger gate: deferring fire until un-clustered count " ">= %d (current=%d, elapsed=%.1fs)",
                    FIRST_TRIGGER_MIN_BATCH,
                    unclustered_count,
                    elapsed,
                )
                would_fire = False

            if would_fire:
                trigger = "time" if time_trigger else "size"
                logger.info(
                    f"Clustering trigger fired (trigger={trigger}): "
                    f"unclustered={unclustered_count}, limit={TRIGGER_BATCH_LIMIT}, "
                    f"interval={TRIGGER_INTERVAL_SEC}s, elapsed={elapsed:.1f}s"
                )
                # Reset the trigger clock at FIRE START so a slow
                # clustering+compression pass doesn't push the next tick out.
                last_trigger_ts = time.monotonic()
                this_tick_batch_idx = None

                # --- Stage 1: clustering --------------------------------------
                _clustering_t0 = time.perf_counter()
                try:
                    if CLUSTER_DETERMINISTIC:
                        # Read NEW main rows once, snapshot tracking once, then
                        # drain every settled full window -- all in memory.
                        _det_refill_from_main()
                        _build_tick_tracking_index()
                        stats = None
                        for _wi in range(CLUSTER_MAX_WINDOWS_PER_TICK):
                            _s = _run_online_clustering_batch()
                            logger.warning(f"Deterministic mode: Clustering stats: {_s}")
                            if _s is None:
                                break
                            stats = _s
                    else:
                        stats = _run_online_clustering_batch()
                    if stats is not None:
                        this_tick_batch_idx = int(stats["batch"])
                        logger.warning(f"Clustering stats: {stats}")
                        if _cluster_manager is not None:
                            _archive = _cluster_manager.archive
                            _clusters_map = _archive.clusters
                            _active_cids = sorted(_clusters_map.keys())
                            _summary = ", ".join(
                                f"c{cid}(rep={_archive.rep_object_id.get(cid, '?')})" f"=n{_clusters_map[cid].n_embs}"
                                for cid in _active_cids
                            )
                            logger.warning(f"Active cluster_ids ({len(_active_cids)}): {_summary}")
                            # All-time retired: cluster_ids known to the archive
                            # (via rep_object_id, never pruned) but no longer in
                            # the active clusters map.
                            _retired_cids = sorted(cid for cid in _archive.rep_object_id if cid not in _clusters_map)
                            if _retired_cids:
                                _retired_summary = ", ".join(
                                    f"c{cid}(rep={_archive.rep_object_id[cid]})" for cid in _retired_cids
                                )
                                logger.warning(
                                    f"Retired cluster_ids (cumulative, {len(_retired_cids)}): " f"{_retired_summary}"
                                )
                        # Clustering ran; reset idle window and re-arm the final
                        # snapshot for the next idle period.
                        consecutive_skip_ticks = 0
                        final_snapshot_saved = False
                    else:
                        consecutive_skip_ticks += 1
                except Exception as e:
                    logger.error(f"Error during online clustering: {str(e)}")
                finally:
                    logger.warning(f"Clustering took {time.perf_counter() - _clustering_t0:.2f}s")

                # --- Stage 2: compression on the clustering result ------------
                if ENABLE_COMPRESSION:
                    try:
                        results = collection.query(expr="is_clustered == true", output_fields=["count(*)"])
                        clustered_count = results[0]["count(*)"] if results else 0
                    except Exception as e:
                        logger.warning(f"Could not get clustered count: {str(e)}")
                        clustered_count = 0
                    logger.info(f"Clustered count: {clustered_count}")
                    if clustered_count > TRIGGER_MIN_BATCH:
                        logger.warning(
                            f"Performing compression (trigger={trigger}): "
                            f"clustered={clustered_count}, "
                            f"limit={TRIGGER_BATCH_LIMIT}, "
                            f"interval={TRIGGER_INTERVAL_SEC}s, "
                            f"elapsed={elapsed:.1f}s, "
                            f"compression_batch_size={clustered_count}"
                        )
                        _compression_t0 = time.perf_counter()
                        try:
                            _perform_compression(
                                OUTLIER_STD_DEV_THRESHOLD,
                                COSINE_SIMILARITY_THRESHOLD,
                                MAX_COMPRESSED_SAMPLES,
                                clustered_count,
                            )
                            compressed_stats = _compute_cluster_stats_from_collection(compressed_collection)
                            if compressed_stats:
                                compressed_stats_cache.replace(compressed_stats)
                            if CLUSTER_DB_SNAPSHOT_MODE == "every_batch" and this_tick_batch_idx is not None:
                                try:
                                    snap_paths = _snapshot_dbs(this_tick_batch_idx, stage="post_compression")
                                    logger.warning(f"DB snapshots (post-compression) saved: {snap_paths}")
                                except Exception as e:
                                    logger.error(f"Failed to write post-compression DB snapshots: {e}")
                        except Exception as e:
                            logger.error(f"Error during compression: {str(e)}")
                        finally:
                            logger.warning(
                                f"Compression cycle completed in {time.perf_counter() - _compression_t0:.3f}s"
                            )

                # Consume the one-shot warm-up gate (even on stage failure, to
                # match "a fire happened" semantics).
                first_trigger_pending = False

                # Final-only snapshot: clustering has skipped for N consecutive
                # ticks (ingestion wound down). Write one post_clustering snapshot
                # under the last batch dir, then arm the flag so idle ticks don't
                # re-write it (cleared the moment clustering runs again).
                if (
                    CLUSTER_DB_SNAPSHOT_MODE == "final_only"
                    and not final_snapshot_saved
                    and consecutive_skip_ticks >= CLUSTER_DB_SNAPSHOT_IDLE_TICKS
                    and _cluster_manager is not None
                    and _cluster_manager.archive.batch_log
                ):
                    try:
                        last_batch_idx = int(_cluster_manager.archive.batch_log[-1].batch)
                        snap_paths = _snapshot_dbs(last_batch_idx, stage="post_clustering")
                        logger.warning(
                            "Final DB snapshots saved at batch %d after %d idle ticks: %s",
                            last_batch_idx,
                            consecutive_skip_ticks,
                            snap_paths,
                        )
                        final_snapshot_saved = True
                    except Exception as e:
                        logger.error(f"Failed to write final DB snapshots: {e}")

        elif ENABLE_COMPRESSION:
            # Clustering off, compression on: standalone compression trigger.
            try:
                results = collection.query(expr="", output_fields=["count(*)"])
                collection_count = results[0]["count(*)"] if results else 0
            except Exception as e:
                logger.warning(f"Could not get collection count: {str(e)}")
                collection_count = 0

            elapsed = time.monotonic() - last_compression_ts
            size_trigger = False
            time_trigger = False
            if COMPRESSION_INTERVAL_SEC > 0:
                time_trigger = collection_count >= COMPRESSION_MIN_BATCH and elapsed >= COMPRESSION_INTERVAL_SEC
                compression_batch_size = collection_count
            else:
                size_trigger = collection_count >= COMPRESSION_BATCH_LIMIT
                compression_batch_size = COMPRESSION_BATCH_LIMIT

            if size_trigger or time_trigger:
                trigger = "size" if size_trigger else "time"
                logger.info(
                    f"Performing compression (trigger={trigger}): "
                    f"count={collection_count}, "
                    f"limit={COMPRESSION_BATCH_LIMIT}, "
                    f"interval={COMPRESSION_INTERVAL_SEC}s, "
                    f"elapsed={elapsed:.1f}s, "
                    f"compression_batch_size={compression_batch_size}"
                )
                try:
                    last_compression_ts = time.monotonic()
                    compression_start = time.monotonic()
                    _perform_compression(
                        OUTLIER_STD_DEV_THRESHOLD,
                        COSINE_SIMILARITY_THRESHOLD,
                        MAX_COMPRESSED_SAMPLES,
                        compression_batch_size,
                    )
                    logger.info(f"Compression cycle completed in {time.monotonic() - compression_start:.3f}s")
                    compressed_stats = _compute_cluster_stats_from_collection(compressed_collection)
                    if compressed_stats:
                        compressed_stats_cache.replace(compressed_stats)
                except Exception as e:
                    logger.error(f"Error during compression: {str(e)}")


def create_maintenance_process():
    """Initialize and start the maintenance as a separate process (own GIL)."""
    ready_event = multiprocessing.Event()
    proc = multiprocessing.Process(
        target=_maintenance_worker,
        args=(ready_event,),
        daemon=False,
    )
    proc.start()
    _register_child_proc("maintenance", proc, ready_event)
    return proc


def _is_queue_shutdown_error(exc: BaseException) -> bool:
    """Detect errors that mean the multiprocessing queue is gone (e.g. Ctrl+C).

    During interpreter shutdown, ``multiprocessing.Queue``'s underlying pipes
    are closed by atexit hooks, and daemon threads blocked on ``.get()`` wake
    up with one of these errors. Treat them as a signal to exit the loop
    instead of spamming logs forever.
    """
    if isinstance(exc, (EOFError, BrokenPipeError)):
        return True
    if isinstance(exc, OSError) and "handle is closed" in str(exc):
        return True
    if isinstance(exc, ValueError) and "closed" in str(exc):
        return True
    return False


def _secondary_embedding_generator(ready_event=None):
    """Separate process: consume resolved TrackedObjects from the queue."""
    _ignore_sigint_in_worker()
    from secondary_embedding_generation.secondary_embedding_generation import (
        SecondaryEmbeddingGeneration,
    )

    logger_proc = logging.getLogger("embedding_output")
    logger_proc.info("Embedding output generator process started")

    output_dir = os.getenv("PATCH_OUTPUT_DIR", "extracted_patches")
    secondaryEmbedding = SecondaryEmbeddingGeneration(
        model_name=SECONDARY_EMBEDDING_MODEL, onnx_path=SECONDARY_EMBEDDING_ONNX_MODEL_PATH
    )

    if ready_event is not None:
        ready_event.set()

    while True:
        try:
            tracked_obj_dict = _compressed_embeddings_object_metadata_queue.get()
            logger_proc.debug(f"[EmbeddingOutput] {tracked_obj_dict}")
            result = secondaryEmbedding.process_object(tracked_obj_dict, output_dir)
            if result:
                logger_proc.info(f"[EmbeddingOutput] Created embedding for : {result}")
            else:
                logger_proc.warning(
                    f"[EmbeddingOutput] Could not extract patch for "
                    f"object_id={tracked_obj_dict.get('object_id')} "
                    f"frame_id={tracked_obj_dict.get('frame_id')}"
                )
        except Exception as e:
            if _is_queue_shutdown_error(e):
                logger_proc.info("Embedding output queue closed, exiting worker.")
                break
            logger_proc.error(f"Error in embedding output generator: {e}")


def create_secondary_embedding_generator():
    """Start the secondary embedding generator as a separate process."""
    ready_event = multiprocessing.Event()
    proc = multiprocessing.Process(
        target=_secondary_embedding_generator,
        args=(ready_event,),
        daemon=True,
    )
    proc.start()
    _register_child_proc("secondary_embedding_generator", proc, ready_event)
    return proc


@app.get("/")
async def root():
    return {"message": "Welcome to the ReID Service"}


@app.get(
    "/health/live",
    status_code=200,
    response_model=HealthLiveResponse,
    summary="Liveness probe",
    description="Returns 200 if the service process is alive. Does not check downstream dependencies. "
    "Use for Kubernetes livenessProbe / Docker HEALTHCHECK.",
    tags=["Health"],
)
async def health_live():
    return {"status": "alive"}


@app.get(
    "/health/ready",
    status_code=200,
    response_model=HealthReadyResponse,
    responses={
        503: {
            "model": HealthNotReadyResponse,
            "description": (
                "Service is not ready to serve traffic. FastAPI wraps the readiness "
                "payload in ``detail`` (the handler raises ``HTTPException``)."
            ),
        },
    },
    summary="Readiness probe",
    description="Returns 200 only when the service is ready to serve traffic: "
    "Milvus is reachable, every required child process is alive and has "
    "signalled it finished its own setup (Milvus connect + collection load + "
    "internal threads started; the Kafka consumer also requires a successful "
    "first broker connect), and every in-process thread is alive. "
    "Returns 503 otherwise. Use for Kubernetes readinessProbe.",
    tags=["Health"],
)
async def health_ready():
    checks: Dict[str, str] = {}
    ready = True

    try:
        if not utility.has_collection(COLLECTION_NAME, using="default"):
            ready = False
            checks["milvus"] = f"collection '{COLLECTION_NAME}' missing"
        else:
            checks["milvus"] = "ok"
    except Exception as e:
        ready = False
        checks["milvus"] = f"error: {e}"

    if ENABLE_COMPRESSION:
        try:
            if not utility.has_collection(COMPRESSED_COLLECTION_NAME, using="compressed"):
                ready = False
                checks["milvus_compressed"] = f"collection '{COMPRESSED_COLLECTION_NAME}' missing"
            else:
                checks["milvus_compressed"] = "ok"
        except Exception as e:
            ready = False
            checks["milvus_compressed"] = f"error: {e}"

    # Snapshot the registries under the lock, then evaluate without holding it
    # (so a probe never blocks process registration during startup).
    with _health_registry_lock:
        child_procs_snapshot = list(_required_child_procs)
        threads_snapshot = list(_required_main_threads)

    for name, proc, ready_event in child_procs_snapshot:
        key = f"proc:{name}"
        if proc is None:
            ready = False
            checks[key] = "not started"
            continue
        if not proc.is_alive():
            ready = False
            checks[key] = f"dead (pid={proc.pid}, exitcode={proc.exitcode})"
            continue
        if ready_event is not None and not ready_event.is_set():
            ready = False
            checks[key] = f"alive but not ready (pid={proc.pid}); worker setup/threads not signalled"
            continue
        checks[key] = f"ok (pid={proc.pid})"

    for name, thread in threads_snapshot:
        key = f"thread:{name}"
        if thread is None or not thread.is_alive():
            ready = False
            checks[key] = "dead"
        else:
            checks[key] = "ok"

    # Backend-managed resources (e.g. the in-memory store's drainer
    # threads, or a SQLite connection pool's per-process connections).
    # An empty dict means "nothing to check".
    try:
        for key, status in tracking_store.health_check().items():
            checks[key] = status
            if status != "ok":
                ready = False
    except Exception as e:
        ready = False
        checks["tracking_store:health_check"] = f"error: {e}"

    for store in (main_stats_cache, compressed_stats_cache):
        try:
            for key, status in store.health_check().items():
                checks[key] = status
                if status != "ok":
                    ready = False
        except Exception as e:
            ready = False
            checks["cluster_stats:health_check"] = f"error: {e}"

    if not ready:
        logger.warning(f"Health check failed: {checks}")

    payload = {"status": "ready" if ready else "not_ready", "checks": checks}
    if not ready:
        raise HTTPException(status_code=503, detail=payload)
    return payload


@debug_router.post(
    "/insert",
    response_model=InsertResponse,
    status_code=201,
    summary="Insert an embedding into the collection",
    description="Insert a ReID embedding into the collection. This functionality is used by "
    "Kafka consumer inside ReID service to insert embeddings into the collection",
    tags=["Run Time Operations"],
)
async def create_embedding(embedding: EmbeddingInput):
    if len(embedding.vector) != DIMENSION:
        raise HTTPException(
            status_code=400, detail=f"Vector dimension must be {DIMENSION}, got {len(embedding.vector)}"
        )

    try:
        ensure_collection_loaded()
        ingest.insert_embedding(
            collection,
            embedding.vector,
            embedding.object_id,
            embedding.sensor_id,
            embedding.frame_id,
        )
        _new_data_event.set()
        return InsertResponse(success=True, message="Embedding inserted successfully", object_id=embedding.object_id)
    except Exception as e:
        logger.error(f"Failed to insert embedding: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to insert embedding: {str(e)}")


@debug_router.get(
    "/embeddings/{object_id}",
    response_model=List[EmbeddingOutput],
    status_code=200,
    summary="Get all embeddings for an object",
    description="Get all embeddings for an object specified by the object_id. "
    "Use use_compressed=true for the compressed collection.",
    tags=["Offline Operations"],
)
async def get_embedding(object_id: str, use_compressed: bool = False):
    coll = _get_embeddings_collection(use_compressed)
    iterator = coll.query_iterator(
        batch_size=ITERATOR_BATCH_SIZE,
        expr=_object_id_filter_expr(object_id),
        output_fields=["entry_id", "object_id", "vector", "sensor_id", "frame_id"],
    )
    all_results = []
    while True:
        batch = iterator.next()
        if not batch:
            iterator.close()
            break
        all_results.extend(batch)
    if not all_results:
        raise HTTPException(status_code=404, detail="No embeddings found for this object_id")
    return [EmbeddingOutput(**item) for item in all_results]


def _hit_contributions(hit, query_vector, weight, k_max, clt_estimation):
    """The ``(distance, weight)`` pairs one search hit contributes to its object.

    Normally a hit is one pair: the distance Milvus measured, carrying the
    weight of every vector the row stands for. With ``clt_estimation`` the row
    instead reports estimated distances to the ``k_max`` nearest vectors it
    actually holds, each weighing one vector, so the caller scores against
    something much closer to the raw collection it replaced.

    Weight-1 rows are passed through: their representative *is* the only
    vector, so Milvus already measured the exact distance.
    """
    if not clt_estimation or weight <= 1:
        return [(hit.distance, weight)]

    mean = hit.entity.get("mean")
    # Rows written before moments existed carry no mean. Scoring those as one
    # weighted representative is the old behaviour, which is still correct.
    if not mean:
        return [(hit.distance, weight)]

    # The estimates come back squared, matching the squared L2 Milvus reports
    # for the hits these pairs are pooled with.
    estimated = estimate_knn_normal_clt(
        query_vector,
        _decode_mean(mean),
        weight,
        k_max=k_max,
        sigma_scale=CLT_SIGMA_SCALE,
    )
    return [(float(d), 1) for d in estimated]


def _keep_nearest_contributions(clusters, max_pairs):
    """Keep only the ``max_pairs`` nearest vectors overall.

    Milvus's ``limit`` already bounds this when one hit means one vector, which
    is how an uncompressed search behaves. A compressed row with ``weight > 1``
    stands for that many vectors at the measured distance, so it is expanded
    into ``weight`` unit-weight pairs before the bound is applied;
    ``clt_estimation`` already expands into per-vector pairs of weight 1. Either
    way a compressed search competes over the same number of candidates an
    uncompressed one would have returned.

    The ceiling being restored is a global one, so the trim is global too: an
    object whose rows are all distant gives up its pairs to nearer objects,
    exactly as its vectors would have lost their slots, and an object left with
    nothing drops out of the result.
    """

    def _as_unit_vectors(pairs):
        for distance, weight in pairs:
            if weight > 1:
                for _ in range(int(weight)):
                    yield (distance, 1)
            else:
                yield (distance, weight)

    total = sum(int(weight) if weight > 1 else 1 for pairs in clusters.values() for _, weight in pairs)
    if total <= max_pairs:
        if total == sum(len(pairs) for pairs in clusters.values()):
            return clusters
        return {object_id: list(_as_unit_vectors(pairs)) for object_id, pairs in clusters.items()}

    nearest = heapq.nsmallest(
        max_pairs,
        (
            (distance, weight, object_id)
            for object_id, pairs in clusters.items()
            for distance, weight in _as_unit_vectors(pairs)
        ),
        key=lambda contribution: contribution[0],
    )
    kept = {}
    for distance, weight, object_id in nearest:
        kept.setdefault(object_id, []).append((distance, weight))
    # Rebuilt in the order Milvus returned the objects in, which the callers
    # that merge two collections' results rely on.
    return {object_id: kept[object_id] for object_id in clusters if object_id in kept}


_non_unit_weight_warned = False


def _warn_non_unit_weights(object_id, pairs):
    """Warn once that scoring saw a pair standing for more than one vector.

    Scoring averages distances unweighted because every pair reaching it stands
    for exactly one vector. A weight above 1 means something delivered pairs
    without going through :func:`_keep_nearest_contributions`, and the mean is
    then silently under-counting that row rather than failing, so the breach is
    surfaced here. Logged once: this sits on the per-request search path.
    """
    global _non_unit_weight_warned
    if _non_unit_weight_warned:
        return
    _non_unit_weight_warned = True
    logger.warning(
        f"Search scoring expects unit weights, but object_id={object_id} "
        f"carried {sorted({w for _, w in pairs if w != 1})}. Distances are "
        f"being averaged unweighted; check _keep_nearest_contributions."
    )


def _search_single_collection(
    coll,
    vector,
    top_k_objects,
    max_vectors_per_object,
    custom_group_search,
    search_params,
    output_fields,
    label="main",
    clt_estimation=False,
):
    """Search a single Milvus collection and return clusters: {object_id: [(distance, weight), ...]}.

    ``clt_estimation`` expands each hit into per-vector distance estimates from
    its stored moments; see :func:`_hit_contributions`. Only the compressed
    collection carries those moments.

    At most ``search_limit`` pairs come back,
    matching the ceiling Milvus's ``limit`` imposes on an uncompressed search;
    see :func:`_keep_nearest_contributions`.
    """
    clusters = {}
    # Hits per object, tracked separately from len(clusters[oid]) because one
    # hit can contribute several pairs under clt_estimation.
    hits_per_object = {}
    search_start = time.time()
    if ENABLE_COMPRESSION:
        search_limit = top_k_objects * max_vectors_per_object
    else:
        search_limit = top_k_objects * max_vectors_per_object * 2
    # When clustering is enabled, the main collection holds both raw un-clustered
    # rows (is_clustered=false) and cluster reps (is_clustered=true). Search must
    # match only cluster reps so it returns cluster identities, not raw recent
    # detections. The compressed collection contains only clustered rows, so it
    # needs no filter.
    search_expr = "is_clustered == true" if (ENABLE_CLUSTERING and "main" in label) else None
    # Both hit loops below log one line per hit whose payload is expensive to
    # build. Hoisted rather than checked per hit so the branch is a local lookup.
    log_hits = logger.isEnabledFor(logging.DEBUG)
    if custom_group_search:
        milvus_search_start = time.time()
        group_results = coll.search(
            data=[vector],
            anns_field="vector",
            param=search_params,
            limit=search_limit,
            expr=search_expr,
            output_fields=output_fields,
        )
        milvus_search_time = (time.time() - milvus_search_start) * 1000
        logger.info(f"Milvus {label} search completed in {milvus_search_time:.3f}ms")
        for hits in group_results:
            for hit in hits:
                object_id = hit.entity.get("object_id")
                if object_id not in clusters:
                    # if number of object_ids is more than top_k, then exit the loop
                    if len(clusters) >= top_k_objects:
                        break
                    clusters[object_id] = []
                    hits_per_object[object_id] = 0
                # If number of hits is less than top_k, then add the hit to the cluster
                # else ignore the hit
                if hits_per_object[object_id] < max_vectors_per_object:
                    weight = hit.entity.get("weight", 1)
                    hits_per_object[object_id] += 1
                    contributions = _hit_contributions(hit, vector, weight, search_limit, clt_estimation)
                    clusters[object_id].extend(contributions)
                    if log_hits:
                        logger.debug(
                            f"[{label}] hit object_id={object_id}, "
                            f"entry_id={hit.entity.get('entry_id')}, "
                            f"sensor_id={hit.entity.get('sensor_id')}, "
                            f"frame_id={hit.entity.get('frame_id')}, weight={weight}, "
                            f"contributions={[(round(d, 4), w) for d, w in contributions]}"
                        )
    else:
        # use milvus group search : requires collection.flush()
        milvus_search_start = time.time()
        group_results = coll.search(
            data=[vector],
            anns_field="vector",
            limit=top_k_objects,
            param=search_params,
            group_by_field="object_id",
            group_size=max_vectors_per_object,
            expr=search_expr,
            output_fields=output_fields,
        )
        milvus_search_time = (time.time() - milvus_search_start) * 1000
        logger.debug(f"Milvus {label} group search completed in {milvus_search_time:.3f}ms")
        for hits in group_results:
            for hit in hits:
                object_id = hit.entity.get("object_id")
                if object_id not in clusters:
                    clusters[object_id] = []
                weight = hit.entity.get("weight", 1)
                contributions = _hit_contributions(hit, vector, weight, search_limit, clt_estimation)
                clusters[object_id].extend(contributions)
                if log_hits:
                    logger.debug(
                        f"[{label}] hit object_id={object_id}, "
                        f"entry_id={hit.entity.get('entry_id')}, "
                        f"sensor_id={hit.entity.get('sensor_id')}, "
                        f"frame_id={hit.entity.get('frame_id')}, weight={weight}, "
                        f"contributions={[(round(d, 4), w) for d, w in contributions]}"
                    )

    clusters = _keep_nearest_contributions(clusters, search_limit)

    search_time = (time.time() - search_start) * 1000
    logger.info(f"{label} collection search completed in {search_time:.3f}ms")
    return clusters


_VECTOR_DTYPE = np.float32


def _vec_to_bytes(vector) -> bytes:
    """Serialize a query vector as raw float32 bytes for the search pipe.

    Sending bytes instead of a Python list of floats makes the
    multiprocessing.Pipe pickle/unpickle cheap on both ends (one buffer
    copy vs. boxing/unboxing hundreds of float objects), cutting
    GIL-held serialization time on the hot path. Milvus compares vectors
    in float32 anyway, so the cast is lossless relative to the
    server-side search.
    """
    return np.asarray(vector, dtype=_VECTOR_DTYPE).tobytes()


# ---------------------------------------------------------------------------
# Search worker processes (one for main, one for compressed)
# ---------------------------------------------------------------------------
# Each worker runs _search_single_collection in its own process (own GIL,
# own Milvus gRPC channel).  Communication uses a pool of multiprocessing
# Pipe pairs so multiple search threads can issue concurrent requests.


class _SearchWorkerPool:
    """Manages a fleet of search-worker subprocesses sharing a pipe pool.

    The pipe pool size stays MAX_CONCURRENT_SEARCHES (so the application-
    level semaphore behaviour is unchanged), but pipes are partitioned
    across ``num_workers`` subprocesses. Each subprocess holds exactly
    one persistent gRPC channel to Milvus and runs one pipe-handler
    thread per pipe assigned to it.

    Why multiple subprocesses: all pipe-handler threads inside ONE process
    share a single Python GIL, so per-call Python overhead (pickle,
    pymilvus wrappers) serializes regardless of how many threads or gRPC
    channels there are. Splitting across N independent interpreters
    gives N concurrent Python execution streams.
    """

    def __init__(self):
        self.pipes: list = []  # list of (parent_conn, child_conn)
        self.free: "queue.SimpleQueue[int]" = queue.SimpleQueue()
        self.procs: list = []  # list of multiprocessing.Process
        self.ready_events: list = []  # one Event per proc

    def create(
        self,
        milvus_host,
        milvus_port,
        collection_name,
        output_fields,
        custom_group_search_flag,
        label,
        pool_size,
        num_workers,
        log_queue,
        log_level,
        clt_estimation=False,
    ):
        num_workers = max(1, min(num_workers, pool_size))

        # Partition pool_size pipes across num_workers subprocesses as
        # evenly as possible. e.g. 30 pipes / 4 workers -> [8, 8, 7, 7].
        pipes_per_worker = [
            pool_size // num_workers + (1 if i < pool_size % num_workers else 0) for i in range(num_workers)
        ]

        for w_idx, n_pipes in enumerate(pipes_per_worker):
            child_conns_for_proc: list = []
            for _ in range(n_pipes):
                parent_conn, child_conn = multiprocessing.Pipe()
                self.pipes.append((parent_conn, child_conn))
                self.free.put(len(self.pipes) - 1)
                child_conns_for_proc.append(child_conn)

            ready_event = multiprocessing.Event()
            proc_label = label if num_workers == 1 else f"{label}_{w_idx}"
            proc = multiprocessing.Process(
                target=_search_worker_main,
                args=(
                    child_conns_for_proc,
                    milvus_host,
                    milvus_port,
                    collection_name,
                    output_fields,
                    custom_group_search_flag,
                    proc_label,
                    log_queue,
                    log_level,
                    ready_event,
                    clt_estimation,
                ),
                daemon=True,
            )
            proc.start()
            self.procs.append(proc)
            self.ready_events.append(ready_event)

        # Close child ends in the parent now that all subprocesses have
        # inherited their copy.
        for _, child_conn in self.pipes:
            child_conn.close()

    def send(self, vec_bytes, top_k_objects, max_vectors_per_object):
        """Acquire an idle pipe and dispatch a search request on it.

        Returns the pipe index; the caller collects the reply later via
        :meth:`recv`. Splitting send/recv lets a SINGLE caller thread fire
        both the main and compressed searches and then block on both
        replies
        """
        idx = self._acquire()
        try:
            self.pipes[idx][0].send((vec_bytes, top_k_objects, max_vectors_per_object))
        except Exception:
            self._release(idx)
            raise
        return idx

    def recv(self, idx):
        """Block for the reply on pipe ``idx``, then release the pipe."""
        try:
            return self.pipes[idx][0].recv()
        finally:
            self._release(idx)

    def search(self, vector, top_k_objects, max_vectors_per_object):
        """Convenience send+recv on one pipe (single-collection path)."""
        idx = self.send(_vec_to_bytes(vector), top_k_objects, max_vectors_per_object)
        return self.recv(idx)

    def _acquire(self):
        # Blocks until a pipe is free (see __init__ for why a queue rather
        # than a spin loop).
        return self.free.get()

    def _release(self, idx):
        self.free.put(idx)


_main_search_pool = _SearchWorkerPool()
_compressed_search_pool = _SearchWorkerPool()


def _search_worker_main(
    pipe_conns,
    milvus_host,
    milvus_port,
    collection_name,
    output_fields,
    custom_group_search_flag,
    label,
    log_queue,
    log_level,
    ready_event=None,
    clt_estimation=False,
):
    """
    Generic search-worker process entry point.
    Connects to a single Milvus instance, then services pipe requests using
    one thread per pipe.
    """
    _ignore_sigint_in_worker()
    if log_queue is not None:
        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(logging.handlers.QueueHandler(log_queue))
        root.setLevel(getattr(logging, log_level, logging.INFO))
    else:
        logging.basicConfig(level=getattr(logging, log_level, logging.INFO), format=_LOG_FORMAT)

    proc_logger = logging.getLogger(f"search_worker_{label}")
    proc_logger.info(
        f"{label} search worker starting (pid={os.getpid()}), "
        f"connecting to {milvus_host}:{milvus_port}, "
        f"serving {len(pipe_conns)} pipe(s)"
    )

    _drop_inherited_milvus_aliases("default", "compressed")

    alias = f"search_{label}"
    connections.connect(alias=alias, host=milvus_host, port=milvus_port)
    coll = Collection(collection_name, using=alias)
    coll.load()
    proc_logger.info(f"{label} search worker: collection loaded (alias={alias})")

    search_params = MILVUS_SEARCH_PARAMS

    def _handle_pipe(conn):
        while True:
            try:
                msg = conn.recv()
                if msg is None:
                    break
                vec_bytes, top_k_objects, max_vectors_per_object = msg
                vector = np.frombuffer(vec_bytes, dtype=_VECTOR_DTYPE)
                clusters = _search_single_collection(
                    coll,
                    vector,
                    top_k_objects,
                    max_vectors_per_object,
                    custom_group_search_flag,
                    search_params,
                    output_fields,
                    label,
                    clt_estimation,
                )
                conn.send(clusters)
            except EOFError:
                break
            except Exception as e:
                proc_logger.error(f"{label} search worker error: {e}")
                try:
                    conn.send({})
                except Exception:
                    break

    threads = []
    for pipe_idx, c in enumerate(pipe_conns):
        t = threading.Thread(
            target=_handle_pipe,
            args=(c,),
            daemon=True,
            name=f"search-{label}-pipe{pipe_idx}",
        )
        t.start()
        threads.append(t)

    # All pipe-handler threads have been started; the worker is now ready
    # to service search requests. Signal the main process so /health/ready
    # can flip to ready.
    if ready_event is not None:
        ready_event.set()

    for t in threads:
        t.join()


def create_search_processes(log_queue=None):
    """Spawn search-worker subprocesses for both collections.

    Each collection gets ``MILVUS_SEARCH_WORKER_PROCESSES`` subprocesses
    sharing a single ``MAX_CONCURRENT_SEARCHES``-sized pipe pool. Each
    subprocess opens one persistent gRPC channel to Milvus.
    """
    pool_size = MAX_CONCURRENT_SEARCHES
    num_workers = MILVUS_SEARCH_WORKER_PROCESSES

    _main_search_pool.create(
        milvus_host=MILVUS_HOST,
        milvus_port=MILVUS_PORT,
        collection_name=COLLECTION_NAME,
        output_fields=["entry_id", "object_id", "sensor_id", "frame_id"],
        custom_group_search_flag=CUSTOM_GROUP_SEARCH,
        label="main",
        pool_size=pool_size,
        num_workers=num_workers,
        log_queue=log_queue,
        log_level=LOG_LEVEL,
    )
    for w_idx, (proc, ev) in enumerate(zip(_main_search_pool.procs, _main_search_pool.ready_events)):
        name = "main_search" if num_workers == 1 else f"main_search_{w_idx}"
        _register_child_proc(name, proc, ev)

    if ENABLE_COMPRESSION:
        compressed_output_fields = ["entry_id", "object_id", "sensor_id", "frame_id", "weight"]
        if ENABLE_CLT_ESTIMATION:
            # Fetched per hit so the worker can expand the row into per-vector
            # distance estimates: one vector-width column per hit, carried as
            # base64 text so pymilvus hands it over without building a Python
            # list per row (see the schema comment).
            compressed_output_fields += ["mean"]
            logger.warning(
                f"CLT distance estimation enabled "
                f"(sigma_scale={CLT_SIGMA_SCALE:.4f}): compressed "
                f"search fetches per-row means"
            )
        _compressed_search_pool.create(
            milvus_host=MILVUS_COMPRESSED_HOST,
            milvus_port=MILVUS_COMPRESSED_PORT,
            collection_name=COMPRESSED_COLLECTION_NAME,
            output_fields=compressed_output_fields,
            custom_group_search_flag=CUSTOM_GROUP_SEARCH,
            label="compressed",
            pool_size=pool_size,
            num_workers=num_workers,
            log_queue=log_queue,
            log_level=LOG_LEVEL,
            clt_estimation=ENABLE_CLT_ESTIMATION,
        )
        for w_idx, (proc, ev) in enumerate(zip(_compressed_search_pool.procs, _compressed_search_pool.ready_events)):
            name = "compressed_search" if num_workers == 1 else f"compressed_search_{w_idx}"
            _register_child_proc(name, proc, ev)


def _perform_group_search(vector, top_k_objects, max_vectors_per_object, custom_group_search, use_compressed=False):
    """
    Helper function to perform blocking Milvus search operations.
    This runs in a thread pool to avoid blocking the async event loop.
    Both main and compressed searches are dispatched to dedicated worker
    processes (own GIL, own Milvus gRPC channel) for full isolation.
    use_compressed: if True, search compressed collection and use the
    compressed cluster-stats cache.
    """
    if ENABLE_COMPRESSION and not compressed_stats_cache.is_empty():
        # Issue BOTH collection searches from this single thread: fire the
        # two sends, then block on the two recvs. The subprocess searches
        # run concurrently, so wall time is ~max(main, compressed) .
        vec_bytes = _vec_to_bytes(vector)
        parallel_start = time.time()
        c_idx = _compressed_search_pool.send(vec_bytes, top_k_objects, max_vectors_per_object)
        m_idx = _main_search_pool.send(vec_bytes, top_k_objects, max_vectors_per_object)
        clusters = _main_search_pool.recv(m_idx)
        compressed_clusters = _compressed_search_pool.recv(c_idx)
        parallel_time = (time.time() - parallel_start) * 1000
        logger.info(f"Parallel main+compressed search completed in {parallel_time:.3f}ms")

        for object_id, pairs in compressed_clusters.items():
            if object_id in clusters:
                clusters[object_id].extend(pairs)
            else:
                clusters[object_id] = pairs
    else:
        clusters = _main_search_pool.search(vector, top_k_objects, max_vectors_per_object)

    cluster_stats_start = time.time()

    # Snapshot cluster stats. Priority matches the pre-refactor behaviour:
    # prefer the compressed cache (when compression is enabled), then fall
    # back to the main cache for any oid the compressed cache didn't cover.
    stats_snapshot: Dict[str, CachedStats] = {}

    if ENABLE_COMPRESSION:
        stats_snapshot.update(compressed_stats_cache.snapshot_for(clusters))

    # Only hit the main cache for oids the compressed cache didn't cover.
    # When ``use_compressed`` is set, the compressed snapshot above is the
    # authoritative source and we skip the main cache entirely.
    if not (ENABLE_COMPRESSION and use_compressed):
        missing = [oid for oid in clusters if oid not in stats_snapshot]
        if missing:
            stats_snapshot.update(main_stats_cache.snapshot_for(missing))

    similar_items = []
    # Same eager-f-string trap as the worker's hit loops, and this one runs in
    # the uvicorn process, so the wasted work is held against the event loop's
    # GIL rather than a worker's.
    log_under_filled = logger.isEnabledFor(logging.DEBUG)
    # Calculate statistics for each cluster
    for object_id, dist_weight_pairs in clusters.items():
        # sort dist_weight_pairs by distance in ascending order
        dist_weight_pairs.sort(key=lambda x: x[0])

        nearest = dist_weight_pairs[:max_vectors_per_object]

        if not nearest:
            continue

        if any(w != 1 for _, w in nearest):
            _warn_non_unit_weights(object_id, nearest)

        # Plain Python arithmetic for small arrays (typically 5-20 elements).
        # numpy array creation overhead dominates for this size.
        mean_distance = sum(d for d, _ in nearest) / len(nearest)
        group_count = len(nearest)

        if group_count >= max_vectors_per_object:
            logger.info(f"object_id: {object_id}, group_count: {group_count}, mean_distance: {mean_distance}")
        elif log_under_filled:
            logger.debug(
                f"object_id: {object_id}, distances: {[d for d, _ in nearest]}, "
                f"group_count: {group_count}, mean_distance: {mean_distance}"
            )

        confidence = 1.0 / (1.0 + mean_distance)
        exp_confidence = math.exp(-4.0 * mean_distance)

        entry = stats_snapshot.get(object_id)
        if entry is not None:
            # diff = vector_np - entry.centroid
            # distance_to_centroid = float(np.dot(diff, diff))
            distance_to_centroid = -1
            cluster_variance = entry.variance
            cluster_count = entry.count
        else:
            distance_to_centroid = -1
            cluster_variance = -1
            cluster_count = -1

        similar_items.append(
            SearchResult(
                object_id=object_id,
                confidence=confidence,
                distance=mean_distance,
                exp_confidence=exp_confidence,
                group_count=group_count,
                cluster_variance=cluster_variance,
                distance_to_centroid=distance_to_centroid,
                cluster_count=cluster_count,
            )
        )
    similar_items.sort(key=lambda x: (x.exp_confidence * x.group_count), reverse=True)
    # return only top_k_objects results
    similar_items = similar_items[:top_k_objects]
    cluster_stats_time = (time.time() - cluster_stats_start) * 1000
    logger.info(f"Cluster stats calculation completed in {cluster_stats_time:.3f}ms for {len(similar_items)} results")
    return similar_items


# Rate + latency counter for the HTTP /search path. Module-scope is safe
# because the only caller (``search_embeddings`` below) runs in the
# FastAPI parent process; forked subprocesses inherit a dormant reporter
# thread but never call ``.record()`` on this instance.
_search_profiler = RateProfiler(
    "search_requests",
    unit="req",
    log_interval_sec=PROFILE_LOG_INTERVAL_SEC,
    track_latency=True,
    logger=logger,
)


@app.post(
    "/search",
    response_model=List[SearchResult],
    status_code=200,
    summary="Search for objects similar to the query vector",
    description="Search for objects similar to the query vector in the collected embeddings database. "
    "The API evaluates up to the top ``top_k`` objects, and for each of those objects, a maximum of "
    "``max_group_count`` underlying vectors are processed to generate the response payload.",
    tags=["Run Time Operations"],
)
async def search_embeddings(
    search_input: SearchInput, use_compressed: bool = Query(default=False, include_in_schema=ENABLE_DEBUG_ENDPOINTS)
):

    if not ENABLE_DEBUG_ENDPOINTS:
        use_compressed = False  # silently ignore in prod

    start_time = time.time()
    if len(search_input.vector) != DIMENSION:
        raise HTTPException(
            status_code=400, detail=f"Vector dimension must be {DIMENSION}, got {len(search_input.vector)}"
        )

    if ENABLE_COMPRESSION and use_compressed:
        logger.error(
            "When ENABLE_COMPRESSION is true, use_compressed must be false for primary search "
            "since compessed db will be used for secondary search"
        )
        raise HTTPException(
            status_code=400,
            detail="When ENABLE_COMPRESSION is true, use_compressed must be false for primary search "
            "since compessed db will be used for secondary search",
        )

    _get_embeddings_collection(use_compressed)

    top_k_objects = search_input.top_k
    max_vectors_per_object = search_input.max_group_count

    semaphore_wait_start = time.time()
    async with search_semaphore:
        semaphore_wait = (time.time() - semaphore_wait_start) * 1000
        logger.info(f"Semaphore acquired after {semaphore_wait:.3f}ms")
        if semaphore_wait > 50:
            logger.warning(f"Search semaphore wait: {semaphore_wait:.1f}ms (slots likely saturated)")
        search_start = time.time()
        # Run blocking search in thread pool to avoid blocking event loop
        loop = asyncio.get_event_loop()
        similar_items = await loop.run_in_executor(
            None,  # Use default thread pool
            _perform_group_search,
            search_input.vector,
            top_k_objects,
            max_vectors_per_object,
            CUSTOM_GROUP_SEARCH,
            use_compressed,
        )
        search_time = (time.time() - search_start) * 1000
        logger.info(f"Search completed in {search_time:.3f}ms")

    total_time = (time.time() - start_time) * 1000
    logger.info(f"Search request completed in {total_time:.3f}ms, found {len(similar_items)} results")
    # End-to-end latency includes semaphore wait + executor dispatch +
    # actual search; that matches what callers actually observe.
    _search_profiler.record(latency_sec=total_time / 1000.0)
    return similar_items


@debug_router.delete(
    "/embeddings/{object_id}",
    status_code=200,
    summary="Delete embeddings from the collection",
    description="Delete embeddings for a specific object_id. " "Use use_compressed=true for the compressed collection.",
    tags=["Offline Operations"],
)
async def delete_embedding(object_id: str, use_compressed: bool = False):
    coll = _get_embeddings_collection(use_compressed)
    coll.delete(_object_id_filter_expr(object_id))
    return {"message": "Embedding deleted successfully"}


@debug_router.get(
    "/list",
    response_model=List[EmbeddingOutput],
    status_code=200,
    summary="List all embeddings in the collection",
    description="List all embeddings. Use use_compressed=true for the compressed collection.",
    tags=["Offline Operations"],
)
async def list_embeddings(use_compressed: bool = False):
    coll = _get_embeddings_collection(use_compressed)
    iterator = coll.query_iterator(
        batch_size=ITERATOR_BATCH_SIZE,
        expr="",
        output_fields=["entry_id", "object_id", "vector", "sensor_id", "frame_id"],
    )
    all_results = []
    while True:
        batch = iterator.next()
        if not batch:
            iterator.close()
            break
        all_results.extend(batch)
    return [EmbeddingOutput(**item) for item in all_results]


@app.get(
    "/count",
    response_model=int,
    status_code=200,
    summary="Get the total count of embeddings in the collection",
    description="Get the total count of embeddings. Use use_compressed=true for the compressed collection.",
    tags=["Offline Operations"],
)
async def count_embeddings(
    use_compressed: bool = Query(
        default=False,
        description=(
            "If ``true``, return the entry count of the *compressed* "
            "embeddings collection instead of the main one. The compressed "
            "collection only contains entries once the periodic compression "
            "pipeline has run, so a count of 0 here is normal early on."
        ),
    ),
):
    coll = _get_embeddings_collection(use_compressed)
    try:
        results = coll.query(expr="", output_fields=["count(*)"])
        return results[0]["count(*)"] if results else 0
    except Exception as e:
        logger.warning(f"Could not get collection count: {str(e)}")
        return 0


# Dump/load fields whose CSV representation is a comma-separated float list.
# The mean is stored base64-encoded but dumped expanded like the vector, so a
# dump stays readable and loadable independent of how the column is encoded.
_CSV_VECTOR_FIELDS = ("vector", "mean")

# On-the-wire width of each vector-sized column, for batch sizing below. The
# mean is base64, so it costs 4/3 of the float32 block it encodes.
_DUMP_COLUMN_BYTES = {
    "vector": DIMENSION * 4,
    "mean": MEAN_B64_MAX_LENGTH,
}

# Per-batch payload ceiling for query_iterator. Milvus rejects a query whose
# per-segment result exceeds its output-size cap ("query results exceed the
# limit size"), and ITERATOR_BATCH_SIZE alone cannot see that: 16384 rows is
# 80MB with one vector column and 185MB with the compressed collection's
# vector+mean. 32MB leaves room under the cap for metadata columns
# and protobuf framing. Smaller batches are not slower -- dumping 31649
# compressed rows took 8.3s in 2 batches of 16384 and 8.2s in 16 of 2048,
# since the cost is dominated by moving the same bytes either way.
_ITERATOR_BATCH_BYTES = 32 * 1024 * 1024


def _iterator_batch_size(output_fields: Sequence[str]) -> int:
    """Rows per ``query_iterator`` batch that keep one batch under Milvus's
    result-size cap, given how many vector-width columns are requested."""
    bytes_per_row = sum(_DUMP_COLUMN_BYTES.get(field, 0) for field in output_fields)
    if bytes_per_row == 0:
        return ITERATOR_BATCH_SIZE
    return max(1, min(ITERATOR_BATCH_SIZE, _ITERATOR_BATCH_BYTES // bytes_per_row))


def _dump_field_order(use_compressed: bool, include_vectors: bool) -> List[str]:
    """Column order shared by /dump/file and /load/file.

    The compressed collection's mean is vector-sized, so it rides along with
    ``include_vectors`` rather than doubling every metadata-only dump. It only
    exists as a column when ENABLE_CLT_ESTIMATION is on.
    """
    fields = ["entry_id", "object_id", "sensor_id", "frame_id"]
    fields.append("weight" if use_compressed else "is_clustered")
    if include_vectors:
        fields.append("vector")
        if use_compressed and ENABLE_CLT_ESTIMATION:
            fields.append("mean")
    return fields


@debug_router.post(
    "/dump/file",
    summary="Dump the collection data to a file",
    description="Dump the collection data to a file. " "Use use_compressed=true for the compressed collection.",
    status_code=200,
    tags=["Offline Operations"],
)
async def dump_to_file(
    filename: Optional[str] = None, include_vectors: bool = True, format: str = "csv", use_compressed: bool = False
):
    """Dump collection data to a file on the server."""
    coll = _get_embeddings_collection(use_compressed)
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = "compressed_" if use_compressed else ""
        filename = f"{prefix}milvus_dump_{timestamp}.{format}"

    # Only a plain file name in the working directory is allowed.
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not filename.endswith(f".{format}"):
        filename += f".{format}"

    base_dir = os.path.realpath(os.getcwd())
    output_path = os.path.realpath(os.path.join(base_dir, os.path.basename(filename)))
    if os.path.dirname(output_path) != base_dir:
        raise HTTPException(status_code=400, detail="Invalid filename")

    output_fields = _dump_field_order(use_compressed, include_vectors)
    batch_size = _iterator_batch_size(output_fields)
    iterator = coll.query_iterator(batch_size=batch_size, expr="", output_fields=output_fields)

    all_results = []
    batch_count = 0

    logger.info(f"Starting QueryIterator to dump all entities " f"(batch_size={batch_size}, fields={output_fields})")

    while True:
        batch = iterator.next()
        if not batch:
            iterator.close()
            break

        all_results.extend(batch)
        batch_count += 1
        logger.info(f"Dump batch {batch_count}: {len(batch)} entities, total_so_far={len(all_results)}")

    logger.info(f"Total entities to dump: {len(all_results)}")
    results = all_results

    # Function to clean numpy types
    def clean_numpy_value(value):
        if isinstance(value, np.ndarray):
            return [float(x) for x in value]  # Convert each element to Python float
        elif isinstance(value, (np.float32, np.float64, np.floating)):
            return float(value)
        elif isinstance(value, (np.int32, np.int64, np.integer)):
            return int(value)
        return value

    # Process results to clean numpy types
    cleaned_results = []
    for result in results:
        cleaned_result = {}
        for key, value in result.items():
            if key == "mean":
                value = _decode_mean(value)
            cleaned_result[key] = clean_numpy_value(value)
        cleaned_results.append(cleaned_result)

    dump_data = cleaned_results

    field_order = _dump_field_order(use_compressed, include_vectors)

    try:
        if format == "json":
            # Reorder dictionaries to maintain field order
            ordered_data = []
            for item in dump_data:
                ordered_item = {field: item[field] for field in field_order if field in item}
                ordered_data.append(ordered_item)

            with open(output_path, "w") as f:
                json.dump(ordered_data, f, indent=2)

        elif format == "csv":
            import csv

            with open(output_path, "w", newline="") as f:
                if cleaned_results:
                    # Use explicit field order
                    writer = csv.DictWriter(f, fieldnames=field_order)
                    writer.writeheader()

                    for row in cleaned_results:
                        csv_row = {}
                        for key in field_order:
                            if key in row:
                                value = row[key]
                                if key in _CSV_VECTOR_FIELDS and isinstance(value, list):
                                    # Convert vector list to comma-separated
                                    # string of clean numbers
                                    csv_row[key] = ",".join([str(float(x)) for x in value])
                                else:
                                    csv_row[key] = value
                        writer.writerow(csv_row)

        else:
            raise HTTPException(status_code=400, detail="Unsupported format. Use 'json' or 'csv'")

        file_size = os.path.getsize(output_path)

        return {
            "message": "Dump completed successfully",
            "filename": filename,
            "file_size_bytes": file_size,
            "records_exported": len(cleaned_results),
            "format": format,
            "include_vectors": include_vectors,
            "use_compressed": use_compressed,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {str(e)}")


def _compressed_row_from_dump(item: dict) -> dict:
    """Normalize one dumped record into a full compressed-collection row.

    Dumps taken before compression carried per-row moments have no mean, so
    those rows are reconstructed as a single-point cluster: the normalized
    representative is its own mean, which carries zero spread. That is the only
    honest summary available once the raw vectors are gone, and it is replaced
    with a real mean the first time the row is recompressed.

    With ENABLE_CLT_ESTIMATION off the collection has no mean column, so any
    mean in the dump is dropped rather than reconstructed.
    """
    vector = item["vector"]
    row = {
        "entry_id": int(item["entry_id"]),
        "object_id": str(item["object_id"]),
        "sensor_id": str(item["sensor_id"]),
        "frame_id": int(item["frame_id"]),
        "weight": int(item["weight"]),
        "vector": vector,
    }
    if ENABLE_CLT_ESTIMATION:
        mean = item.get("mean")
        if not mean:
            vec = np.asarray(vector, dtype=np.float32)
            norm = float(np.linalg.norm(vec))
            mean = (vec / norm if norm > 0 else vec).tolist()
        row["mean"] = list(mean)
    return row


@debug_router.post(
    "/load/file",
    summary="Load collection data from a file",
    description="Load data from a dumped file into the main or compressed collection. "
    "Use use_compressed=true to load into the compressed collection.",
    status_code=200,
    tags=["Offline Operations"],
)
async def load_from_file(
    filename: str, format: Optional[str] = None, batch_size: int = LOAD_DELETE_BATCH_SIZE, use_compressed: bool = False
):
    """Load collection data from a file on the server."""
    coll = _get_embeddings_collection(use_compressed)

    # Check if file exists
    # Only a plain file name in the working directory is allowed.
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    base_dir = os.path.realpath(os.getcwd())
    # Resolve against the directory listing so the opened path comes from the filesystem, not the request.
    matched_name = next((name for name in os.listdir(base_dir) if name == filename), None)
    if matched_name is None:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    input_path = os.path.join(base_dir, matched_name)

    # Auto-detect format from extension if not provided
    if format is None:
        if filename.endswith(".json"):
            format = "json"
        elif filename.endswith(".csv"):
            format = "csv"
        else:
            raise HTTPException(status_code=400, detail="Cannot detect format. Please specify " "'json' or 'csv'")

    logger.info(f"Loading data from {filename} (format: {format})")

    try:
        # Read the file
        data = []

        if format == "json":
            with open(input_path, "r") as f:
                data = json.load(f)
            for item in data:
                item["object_id"] = str(item.get("object_id", ""))

        elif format == "csv":
            import csv

            with open(input_path, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Convert CSV row to proper format
                    processed_row = {}
                    for key, value in row.items():
                        if key in _CSV_VECTOR_FIELDS:
                            # Convert comma-separated string back to list
                            processed_row[key] = [float(x) for x in value.split(",")]
                        elif key == "entry_id":
                            processed_row[key] = int(value)
                        elif key == "object_id":
                            processed_row[key] = str(value)
                        elif key == "sensor_id":
                            processed_row[key] = str(value)
                        elif key == "frame_id":
                            processed_row[key] = int(value)
                        elif key == "weight":
                            processed_row[key] = int(value)
                        elif key == "is_clustered":
                            processed_row[key] = bool(value)
                        else:
                            processed_row[key] = value
                    data.append(processed_row)

        else:
            raise HTTPException(status_code=400, detail="Unsupported format. Use 'json' or 'csv'")

        if not data:
            return {"message": "No data found in file", "filename": filename, "records_loaded": 0}

        logger.info(f"Read {len(data)} records from file")

        has_vectors = "vector" in data[0]
        # Compressed collection has auto_id=False; we must supply entry_id when loading into it
        need_entry_id = use_compressed
        if need_entry_id and "entry_id" not in data[0]:
            raise HTTPException(
                status_code=400,
                detail="Loading into compressed collection requires 'entry_id' column in the file. "
                "Use a dump from the compressed collection or include entry_id in the data.",
            )
        if need_entry_id and not has_vectors:
            raise HTTPException(
                status_code=400,
                detail="Loading into compressed collection requires a 'vector' column. "
                "Dump with include_vectors=true.",
            )
        if need_entry_id and ENABLE_CLT_ESTIMATION and "mean" not in data[0]:
            logger.warning(
                "Compressed dump has no mean column (written before "
                "compression carried per-row moments). Substituting "
                "mean=vector for every row; distance estimates from these rows "
                "will assume zero spread until they are recompressed."
            )

        total_inserted = 0
        failed_count = 0

        for i in range(0, len(data), batch_size):
            batch = data[i : i + batch_size]

            if need_entry_id:
                entities = _compressed_entity_columns([_compressed_row_from_dump(item) for item in batch])
            else:
                # Main collection: auto_id=True, so omit entry_id. Column order
                # must match the schema: object_id, sensor_id, frame_id,
                # is_clustered, vector.
                object_ids = [item["object_id"] for item in batch]
                sensor_ids = [item["sensor_id"] for item in batch]
                frame_ids = [item["frame_id"] for item in batch]
                is_clustered_col = [bool(item.get("is_clustered", False)) for item in batch]
                entities = [object_ids, sensor_ids, frame_ids, is_clustered_col]
                if has_vectors:
                    entities.append([item["vector"] for item in batch])

            try:
                coll.insert(entities)
                total_inserted += len(batch)
                logger.info(
                    f"Inserted batch {i//batch_size + 1}: "
                    f"{len(batch)} records "
                    f"(total: {total_inserted}/{len(data)})"
                )
            except Exception as e:
                logger.error(f"Failed to insert batch {i//batch_size + 1}: " f"{str(e)}")
                failed_count += len(batch)

        coll.flush()
        logger.info("Data flushed to collection")

        return {
            "message": "Load completed successfully",
            "filename": filename,
            "records_loaded": total_inserted,
            "records_failed": failed_count,
            "format": format,
            "had_vectors": has_vectors,
            "use_compressed": use_compressed,
        }

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON format: {str(e)}")
    except Exception as e:
        logger.error(f"Error loading file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to load file: {str(e)}")


@debug_router.post(
    "/dump/tracking",
    summary="Dump the tracked objects database to a file",
    description="Dump all tracked objects to a file. " "The file gets dumped in the current working directory.",
    status_code=200,
    tags=["Offline Operations"],
)
async def dump_tracking_to_file(filename: Optional[str] = None, format: str = "json"):
    """
    Dump tracked objects database to a file on the server
    """
    if format not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="Unsupported format. Use 'json' or 'csv'")

    # Generate filename if not provided
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"tracking_dump_{timestamp}.{format}"

    # Only a plain file name in the working directory is allowed.
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Ensure filename has correct extension
    if not filename.endswith(f".{format}"):
        filename += f".{format}"

    base_dir = os.path.realpath(os.getcwd())
    output_path = os.path.realpath(os.path.join(base_dir, os.path.basename(filename)))
    if os.path.dirname(output_path) != base_dir:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # snapshot() returns a consistent view (a real snapshot for SQL
    # backends, the same dict as get_all() for the in-memory backend).
    snapshot = tracking_store.snapshot()

    flat_data = []
    for object_id, streams in snapshot.items():
        for stream_id, tracked_objects in streams.items():
            for obj in tracked_objects:
                flat_data.append(
                    {
                        "object_id": obj.object_id,
                        "stream_id": obj.stream_id,
                        "frame_id": obj.frame_id,
                        "timestamp": obj.timestamp,
                        "leftX": obj.leftX,
                        "topY": obj.topY,
                        "rightX": obj.rightX,
                        "bottomY": obj.bottomY,
                        "confidence": obj.confidence,
                    }
                )

    logger.info(f"Total tracked objects to dump: {len(flat_data)}")

    try:
        if format == "json":
            with open(output_path, "w") as f:
                json.dump(flat_data, f, indent=2)
        elif format == "csv":
            import csv

            with open(output_path, "w", newline="") as f:
                if flat_data:
                    fieldnames = [
                        "object_id",
                        "stream_id",
                        "frame_id",
                        "timestamp",
                        "leftX",
                        "topY",
                        "rightX",
                        "bottomY",
                        "confidence",
                    ]
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(flat_data)
        else:
            raise HTTPException(status_code=400, detail="Unsupported format. " "Use 'json' or 'csv'")

        file_size = os.path.getsize(output_path)
        stats = tracking_store.stats()
        return {
            "message": "Tracking dump completed successfully",
            "filename": filename,
            "file_size_bytes": file_size,
            "records_exported": len(flat_data),
            "format": format,
            "total_objects": stats.get("total_objects", len(snapshot)),
            "total_streams": stats.get(
                "total_streams",
                sum(len(streams) for streams in snapshot.values()),
            ),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {str(e)}")


@debug_router.post(
    "/load/tracking",
    summary="Load tracked objects database from a file",
    description="Load tracked objects from a dumped file back into "
    "the tracking database. Supports JSON and CSV formats.",
    status_code=200,
    tags=["Offline Operations"],
)
async def load_tracking_from_file(filename: str, format: Optional[str] = None, clear_existing: bool = False):
    """
    Load tracked objects database from a file on the server

    Args:
        filename: Name of the file to load
        format: File format ('json' or 'csv'). Auto-detected from extension
        clear_existing: Whether to clear existing tracking data before loading
    """
    # Only a plain file name in the working directory is allowed.
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    base_dir = os.path.realpath(os.getcwd())
    input_path = os.path.realpath(os.path.join(base_dir, os.path.basename(filename)))
    if os.path.dirname(input_path) != base_dir:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not os.path.exists(input_path):
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    if format is None:
        if filename.endswith(".json"):
            format = "json"
        elif filename.endswith(".csv"):
            format = "csv"
        else:
            raise HTTPException(status_code=400, detail="Cannot detect format. " "Please specify 'json' or 'csv'")

    logger.info(f"Loading tracking data from {filename} (format: {format})")

    try:
        data = []

        if format == "json":
            with open(input_path, "r") as f:
                data = json.load(f)
            for item in data:
                item["object_id"] = str(item.get("object_id", ""))

        elif format == "csv":
            import csv

            with open(input_path, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    data.append(
                        {
                            "object_id": str(row["object_id"]),
                            "stream_id": str(row["stream_id"]),
                            "frame_id": int(row["frame_id"]),
                            "timestamp": str(row["timestamp"]),
                            "leftX": float(row["leftX"]),
                            "topY": float(row["topY"]),
                            "rightX": float(row["rightX"]),
                            "bottomY": float(row["bottomY"]),
                            "confidence": float(row["confidence"]),
                        }
                    )

        else:
            raise HTTPException(status_code=400, detail="Unsupported format. " "Use 'json' or 'csv'")

        if not data:
            return {"message": "No tracking data found in file", "filename": filename, "records_loaded": 0}

        logger.info(f"Read {len(data)} tracking records from file")

        if clear_existing:
            tracking_store.clear()
            logger.info("Cleared existing tracking database")

        # Build TrackedObject instances and hand the batch to the store
        # so it can do one lock acquire / one transaction for the whole
        # restore. Matches the old code's single-locked-loop semantics.
        tracked_objects = [TrackedObject(**item) for item in data]
        tracking_store.insert_many(tracked_objects)
        loaded_count = len(tracked_objects)
        logger.info(f"Loaded {loaded_count} tracking records")

        stats = tracking_store.stats()
        return {
            "message": "Tracking load completed successfully",
            "filename": filename,
            "records_loaded": loaded_count,
            "format": format,
            "total_objects": stats.get("total_objects", 0),
            "total_streams": stats.get("total_streams", 0),
            "cleared_existing": clear_existing,
        }

    except HTTPException:
        raise
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON format: {str(e)}")
    except Exception as e:
        logger.error(f"Error loading tracking file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to load tracking file: {str(e)}")


def _compute_cluster_stats_from_collection(coll, expr: str = "") -> List[ClusterStats]:
    """Compute cluster stats from a loaded collection. Used for both main and
    compressed. When clustering is enabled, the main-collection call passes
    expr="is_clustered == true" so stats reflect cluster reps, not raw
    un-clustered rows.
    """
    iterator = coll.query_iterator(
        batch_size=ITERATOR_BATCH_SIZE, expr=expr, output_fields=["entry_id", "object_id", "vector"]
    )
    all_results = []
    while True:
        batch = iterator.next()
        if not batch:
            iterator.close()
            break
        all_results.extend(batch)
    if not all_results:
        return []
    clusters = {}
    for item in all_results:
        object_id = item["object_id"]
        vector = item["vector"]
        clusters.setdefault(object_id, []).append(vector)
    stats = []
    for object_id, vectors in clusters.items():
        vectors_array = np.array(vectors)
        centroid = np.mean(vectors_array, axis=0)
        variance = np.var(vectors_array, axis=0)
        mean_variance = float(np.mean(variance))
        stats.append(ClusterStats(object_id=object_id, centroid=centroid, variance=mean_variance, count=len(vectors)))
    stats.sort(key=lambda x: x.object_id)
    return stats


@debug_router.get(
    "/cluster_stats",
    response_model=List[ClusterStats],
    status_code=200,
    summary="Get cluster statistics for all object IDs",
    description="Get cluster statistics for main or compressed collection. "
    "Use use_compressed=true for the compressed collection.",
    tags=["Run Time Operations"],
)
async def get_cluster_stats(force_recalculate: bool = False, use_compressed: bool = False):
    """Get statistics (centroid, variance, count) for all object ID clusters."""
    try:
        target_cache = compressed_stats_cache if use_compressed else main_stats_cache
        target_collection = compressed_collection if use_compressed else collection
        ensure_loaded = ensure_compressed_collection_loaded if use_compressed else ensure_collection_loaded

        if not force_recalculate and not target_cache.is_empty():
            logger.info(
                "Returning cached %s cluster statistics",
                "compressed" if use_compressed else "main",
            )
            return target_cache.values()

        ensure_loaded()
        stats = _compute_cluster_stats_from_collection(
            target_collection,
            expr=("is_clustered == true" if (ENABLE_CLUSTERING and not use_compressed) else ""),
        )
        if stats:
            target_cache.replace(stats)
        return stats

    except Exception as e:
        logger.error(f"Error calculating cluster statistics: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error calculating cluster statistics: {str(e)}")


@debug_router.post(
    "/refresh_cluster_stats",
    response_model=List[ClusterStats],
    status_code=200,
    summary="Force recalculation of cluster statistics",
    description="Force recalculation of cluster statistics. " "Use use_compressed=true for the compressed collection.",
    tags=["Offline Operations"],
)
async def refresh_cluster_stats(use_compressed: bool = False):
    """Force recalculation of cluster statistics"""
    return await get_cluster_stats(force_recalculate=True, use_compressed=use_compressed)


@debug_router.post(
    "/compress_embeddings",
    response_model=CompressedResponse,
    status_code=200,
    summary="Compress embeddings from main collection into compressed collection",
    description="Read embeddings from the main collection (up to batch_limit), apply a compression "
    "pipeline (outlier removal, cosine pruning, FPS + K-medoids), store the "
    "compressed embeddings in the compressed collection, and delete the processed "
    "entries from the main collection.",
    tags=["Offline Operations"],
)
async def compress_embeddings_endpoint(
    outlier_threshold: float = OUTLIER_STD_DEV_THRESHOLD,
    cosine_threshold: float = COSINE_SIMILARITY_THRESHOLD,
    max_samples: int = MAX_COMPRESSED_SAMPLES,
    batch_limit: int = COMPRESSION_BATCH_LIMIT,
):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, _perform_compression, outlier_threshold, cosine_threshold, max_samples, batch_limit
    )
    stats = await loop.run_in_executor(None, _compute_cluster_stats_from_collection, compressed_collection)
    if stats:
        compressed_stats_cache.replace(stats)
    return result


# Optional: Add an endpoint to get stats for specific object_ids


@debug_router.get(
    "/cluster_stats/{object_id}",
    response_model=ClusterStats,
    status_code=200,
    summary="Get cluster statistics for a specific object ID",
    description="Get cluster statistics for a specific object ID. "
    "Use use_compressed=true for the compressed collection.",
    tags=["Offline Operations"],
)
async def get_cluster_stats_by_id(object_id: str, use_compressed: bool = False):
    """Get statistics for a specific object ID"""
    target_cache = compressed_stats_cache if use_compressed else main_stats_cache
    if target_cache.is_empty():
        await get_cluster_stats(use_compressed=use_compressed)
    hit = target_cache.get(object_id)
    if hit is None:
        raise HTTPException(
            status_code=404,
            detail=f"Statistics for object_id {object_id} not found",
        )
    return hit


# API to reset all the data in the collection and cluster stats and start fresh


@app.post(
    "/reset",
    status_code=200,
    summary="Reset the collection and cluster stats",
    description="Reset the collection, cluster stats, and tracking database. This deletes all the "
    "data from the collection, cluster stats, and tracked objects and starts fresh. "
    "This is a useful endpoint to reset everything and start fresh without restarting the server.",
    tags=["Offline Operations"],
)
async def reset(
    clear_main: bool = Query(
        default=True,
        description=(
            "If ``true``, drop every entry from the main embeddings "
            "collection, clear the main cluster-stats cache, and (when "
            "``clear_compressed`` is also ``true``) wipe the in-memory "
            "tracked-objects DB. Set to ``false`` to leave the main "
            "collection untouched and only reset the compressed side."
        ),
    ),
    clear_compressed: bool = Query(
        default=True,
        description=(
            "If ``true``, drop every entry from the compressed embeddings "
            "collection and clear its cluster-stats cache. The "
            "tracked-objects DB is only cleared when BOTH ``clear_main`` "
            "and ``clear_compressed`` are ``true`` (a full reset)."
        ),
    ),
):
    """Reset the collection(s), cluster stats, and tracking database"""
    if clear_main:
        ensure_collection_loaded()
        collection.delete(expr="entry_id >= 0")
        collection.flush()
        main_stats_cache.clear()
    if clear_compressed:
        ensure_compressed_collection_loaded()
        compressed_collection.delete(expr="entry_id >= 0")
        compressed_collection.flush()
        compressed_stats_cache.clear()

    if not clear_main and not clear_compressed:
        return {"message": "No collections to reset. Please specify clear_main or clear_compressed"}

    if clear_main and clear_compressed:
        tracking_store.clear()

    parts = []
    if clear_main:
        parts.append("main collection")
    if clear_compressed:
        parts.append("compressed collection")
    return {"message": f"Reset: {', '.join(parts)}, cluster stats caches, and tracking database"}


@debug_router.get(
    "/outliers",
    response_model=List[OutlierOutput],
    status_code=200,
    summary="Get the outliers from the collection",
    description="Get the outliers from the collection.",
    tags=["Offline Operations"],
)
async def get_outliers(std_dev_threshold: float = 2.0):
    """Get the outliers from the collection"""
    ensure_collection_loaded()
    # Use QueryIterator to get ALL entries regardless of collection size
    iterator = collection.query_iterator(
        batch_size=ITERATOR_BATCH_SIZE,  # Your batch size constant
        expr="",  # Get all entries
        output_fields=["entry_id", "object_id", "vector", "sensor_id", "frame_id"],
    )

    all_results = []
    outliers = []
    while True:
        batch = iterator.next()
        if not batch:
            iterator.close()
            break
        all_results.extend(batch)

    # Group embeddings by object_id
    clusters: Dict[str, List[EmbeddingOutput]] = {}
    for item in all_results:
        object_id = item["object_id"]
        if object_id not in clusters:
            clusters[object_id] = []
        clusters[object_id].append(EmbeddingOutput(**item))

    # Calculate statistics for each cluster
    outliers_output = []
    for object_id, embeddings in clusters.items():
        vectors = [embedding.vector for embedding in embeddings]
        vectors_array = np.array(vectors)
        centroid = np.mean(vectors_array, axis=0)
        distance_to_centroid = np.linalg.norm(vectors_array - centroid, axis=1)
        logger.debug(f"distance_to_centroid: {distance_to_centroid}")
        # Using the distance distribution's standard deviation
        # is better than computing per-dimension standard deviation,
        # because it respects the multidimensional geometry.
        mean_dist = np.mean(distance_to_centroid)
        std_dist = np.std(distance_to_centroid)
        distance_threshold = mean_dist + std_dist * std_dev_threshold
        logger.debug(f"mean_dist: {mean_dist}")
        logger.debug(f"std_dist: {std_dist}")
        logger.debug(f"distance_threshold: {distance_threshold}")

        outliers = [embeddings[i] for i in range(len(embeddings)) if distance_to_centroid[i] > distance_threshold]

        # Group outliers by sensor_id
        outliers_dict: Dict[str, List[int]] = {}
        for embedding in outliers:
            if embedding.sensor_id not in outliers_dict:
                outliers_dict[embedding.sensor_id] = []
            outliers_dict[embedding.sensor_id].append(embedding.frame_id)

        outliers_output.append(OutlierOutput(object_id=object_id, outliers=outliers_dict))
    return outliers_output


# ========== Object Tracking Database API Endpoints ==========


@debug_router.post(
    "/tracked_objects/insert",
    response_model=Dict[str, str],
    status_code=201,
    summary="Insert a tracked object with bounding box",
    description="Insert a tracked object into the tracking database. "
    "Objects are organized by object_id -> stream_id and sorted by frame_id.",
    tags=["Run Time Operations"],
)
async def insert_tracked_object_api(tracked_obj: TrackedObject):
    """Insert a tracked object into the database"""
    try:
        tracking_store.insert(tracked_obj)
        return {"message": "Tracked object inserted successfully"}
    except Exception as e:
        logger.error(f"Failed to insert tracked object: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to insert tracked object: {str(e)}")


@debug_router.get(
    "/tracked_objects/{object_id}",
    response_model=List[ObjectHistoryResponse],
    status_code=200,
    summary="Get tracked objects by object ID",
    description="Get all tracked objects for a given object_id across all streams or a specific stream. "
    "Objects are sorted by frame_id within each stream.",
    tags=["Offline Operations"],
)
async def get_tracked_objects_api(object_id: str, stream_id: Optional[str] = None):
    """Get tracked objects for a specific object_id"""
    result = tracking_store.get_by_object(object_id, stream_id)

    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No tracked objects found for object_id={object_id}"
            + (f" and stream_id={stream_id}" if stream_id else ""),
        )

    # Format response as list of ObjectHistoryResponse
    response = [
        ObjectHistoryResponse(object_id=object_id, stream_id=sid, frames=frames) for sid, frames in result.items()
    ]

    return response


@debug_router.get(
    "/tracked_objects",
    response_model=Dict[str, List[ObjectHistoryResponse]],
    status_code=200,
    summary="Get all tracked objects",
    description="Get all tracked objects in the database organized by object_id.",
    tags=["Offline Operations"],
)
async def get_all_tracked_objects_api():
    """Get all tracked objects in the database"""
    all_objects = tracking_store.get_all()

    # Format response
    response = {}
    for oid, streams in all_objects.items():
        response[oid] = [
            ObjectHistoryResponse(object_id=oid, stream_id=sid, frames=frames) for sid, frames in streams.items()
        ]

    return response


@debug_router.delete(
    "/tracked_objects/{object_id}",
    status_code=200,
    summary="Delete tracked objects",
    description="Delete all tracked objects for a specific object_id.",
    tags=["Offline Operations"],
)
async def delete_tracked_objects_api(object_id: str):
    """Delete tracked objects for a specific object_id"""
    tracking_store.clear(object_id)
    return {"message": f"Tracked objects for object_id={object_id} deleted successfully"}


@debug_router.delete(
    "/tracked_objects",
    status_code=200,
    summary="Clear all tracked objects",
    description="Clear all tracked objects from the database.",
    tags=["Offline Operations"],
)
async def clear_all_tracked_objects_api():
    """Clear all tracked objects"""
    tracking_store.clear()
    return {"message": "All tracked objects cleared successfully"}


@debug_router.get(
    "/tracked_objects/stats/summary",
    status_code=200,
    summary="Get tracking database statistics",
    description="Get summary statistics about the tracking database (counts by object_id and stream_id).",
    tags=["Offline Operations"],
)
async def get_tracking_stats():
    """Get statistics about the tracking database"""
    all_objects = tracking_store.get_all()

    stats = {"total_objects": len(all_objects), "objects": {}}

    for oid, streams in all_objects.items():
        stats["objects"][oid] = {
            "total_streams": len(streams),
            "streams": {
                sid: {
                    "total_frames": len(frames),
                    "frame_range": {
                        "min": min(f.frame_id for f in frames) if frames else None,
                        "max": max(f.frame_id for f in frames) if frames else None,
                    },
                    "timestamp_range": {
                        "min": min(f.timestamp for f in frames) if frames else None,
                        "max": max(f.timestamp for f in frames) if frames else None,
                    },
                }
                for sid, frames in streams.items()
            },
        }

    return stats


if ENABLE_DEBUG_ENDPOINTS:
    app.include_router(debug_router)
    logger.info("Debug endpoints registered")
else:
    logger.info("Debug endpoints not registered")

if __name__ == "__main__":
    # Configuration is loaded from environment variables at module import time
    # (see KAFKA_CONSUMER_TOPIC and DEEPSTREAM_SCHEMA at the top of this file).
    # Override via .env or the process environment, e.g.:
    #   KAFKA_CONSUMER_TOPIC=mv3dt DEEPSTREAM_SCHEMA=protobuf python main.py
    _VALID_SCHEMAS = ("deepstream", "minimal", "protobuf")
    if DEEPSTREAM_SCHEMA not in _VALID_SCHEMAS:
        raise ValueError(f"Invalid DEEPSTREAM_SCHEMA={DEEPSTREAM_SCHEMA!r}; " f"must be one of {_VALID_SCHEMAS}")

    if MESSAGE_BROKER == "redis":
        logger.info(f"Using Redis stream: {REDIS_CONSUMER_TOPIC}")
    else:
        logger.info(f"Using Kafka topic: {KAFKA_CONSUMER_TOPIC}")
    logger.info(f"Using deepstream schema type: {DEEPSTREAM_SCHEMA}")

    # Initialize the broker consumer before starting uvicorn. All
    # subprocess-side state lives on KafkaIngestor, which picks the Kafka or
    # Redis read loop from MESSAGE_BROKER.
    consumer_proc = ingest.create_kafka_consumer(
        kafka_config=ingest.KafkaConfig(
            server=KAFKA_CONSUMER_SERVER,
            topic=KAFKA_CONSUMER_TOPIC,
            group_id=KAFKA_CONSUMER_GROUP_ID,
        ),
        redis_config=ingest.RedisConfig(
            host=REDIS_CONSUMER_HOST,
            port=REDIS_CONSUMER_PORT,
            password=REDIS_CONSUMER_PASSWORD,
            stream=REDIS_CONSUMER_TOPIC,
            group=REDIS_CONSUMER_GROUP,
            consumer_name=REDIS_CONSUMER_NAME,
            payload_key=REDIS_CONSUMER_PAYLOAD_KEY,
            start_id=REDIS_CONSUMER_START_ID,
        ),
        milvus_config=ingest.MilvusConfig(
            host=MILVUS_HOST,
            port=MILVUS_PORT,
            collection_name=COLLECTION_NAME,
        ),
        tracking_store=tracking_store,
        new_data_event=_new_data_event,
        register_child_proc=_register_child_proc,
        ignore_sigint=_ignore_sigint_in_worker,
    )

    # Start the maintenance process (own GIL).
    maintenance_proc = create_maintenance_process()

    # Start the tracking-store drainer threads now that every child
    # process has been forked. The
    # ``/health/ready`` probe consults ``tracking_store.health_check()``
    # to verify the drainer threads stayed up.
    tracking_store.start_drainers()

    # Start the cluster-stats store helper threads now that children are
    # forked. For the in-memory backend this launches the owner-side
    # drainer that applies cross-process ``replace()`` payloads; the
    # SQLite backend has no owner thread (reads refresh lazily). Liveness
    # is asserted via each store's ``health_check()`` in /health/ready.
    main_stats_cache.start_drainers()
    compressed_stats_cache.start_drainers()

    # Start dedicated search worker processes (own GIL + own Milvus connections)
    create_search_processes(log_queue=_log_queue)
    logger.info("Search worker processes started")

    if ENABLE_SECONDARY_EMBEDDING:
        secondary_embedding_generator_proc = create_secondary_embedding_generator()
    else:
        secondary_embedding_generator_proc = None

    # Then start uvicorn; on Ctrl+C, uvicorn returns and the finally block
    # tears down every child process the main process spawned.
    try:
        uvicorn.run(
            app,
            host=REID_SERVICE_HOST,
            port=REID_SERVICE_PORT,
            reload=False,
            log_level=LOG_LEVEL.lower(),
            backlog=4096,
        )
    finally:
        logger.info("Shutting down: stopping tracking-store drainers...")
        try:
            tracking_store.shutdown()
        except Exception as e:
            logger.warning(f"tracking_store.shutdown() failed: {e}")

        logger.info("Shutting down: stopping cluster-stats drainers...")
        for name, cache in (("main_stats_cache", main_stats_cache), ("compressed_stats_cache", compressed_stats_cache)):
            try:
                cache.shutdown()
            except Exception as e:
                logger.warning(f"{name}.shutdown() failed: {e}")

        logger.info("Shutting down: terminating child processes...")
        child_procs = [
            ("kafka_consumer", consumer_proc),
            ("maintenance", maintenance_proc),
            ("secondary_embedding_generator", secondary_embedding_generator_proc),
            ("log_listener", _log_process),
        ]
        # Search worker pools also hold subprocesses; terminate them too.
        for pool_name, pool in (("main_search", _main_search_pool), ("compressed_search", _compressed_search_pool)):
            for w_idx, proc in enumerate(getattr(pool, "procs", [])):
                if proc is None:
                    continue
                name = pool_name if len(pool.procs) == 1 else f"{pool_name}_{w_idx}"
                child_procs.append((name, proc))

        for name, proc in child_procs:
            if proc is None or not proc.is_alive():
                continue
            try:
                proc.terminate()
            except Exception as e:
                logger.warning(f"Could not terminate {name} (pid={proc.pid}): {e}")

        for name, proc in child_procs:
            if proc is None:
                continue
            try:
                proc.join(timeout=5)
                if proc.is_alive():
                    logger.warning(f"{name} (pid={proc.pid}) did not exit; killing.")
                    proc.kill()
                    proc.join(timeout=2)
            except Exception as e:
                logger.warning(f"Error joining {name}: {e}")
        logger.info("All child processes shut down.")
