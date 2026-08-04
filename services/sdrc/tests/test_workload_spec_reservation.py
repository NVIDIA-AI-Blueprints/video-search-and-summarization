# SPDX-FileCopyrightText: Copyright (c) 2021-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Placement tests: atomic slot reservation and pod selection.

Stream placement is a check-then-act: provisionStreamRedis snapshots each pod's
stream count, then commits a choice later. Between those two points an async
provision thread or a concurrent caller can fill the chosen pod, so the capacity
check and the slot claim have to happen as one atomic step
(``tryReserveWorkLoadSpec``) and a refused candidate has to be dropped rather
than forced past WDM_WL_THRESHOLD.

app.py is not imported here: it builds a Redis-backed config object at module
scope, so importing it needs a live Redis. The two pure selection helpers are
lifted out of its source instead.
"""
import ast
import json
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

SDRC_ROOT = Path(__file__).resolve().parents[1]
if str(SDRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SDRC_ROOT))

redisconfig_module = pytest.importorskip("lib.parameters.redisconfig")

redisconfig = redisconfig_module.redisconfig

THRESHOLD = 4
CACHE_KEY = "wl-spec"
EVENT_FIELD = "event"
ID_FIELD = "camera_id"


def _load_select_pod():
    """Return app.py's _select_pod_from_candidates, with _pod_ordinal alongside it."""
    tree = ast.parse((SDRC_ROOT / "app.py").read_text(encoding="utf-8"))
    wanted = {"_pod_ordinal", "_select_pod_from_candidates"}
    picked = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    missing = wanted - {n.name for n in picked}
    assert not missing, "app.py no longer defines: {}".format(sorted(missing))
    subset = ast.Module(
        body=[ast.parse("import re").body[0]] + picked,
        type_ignores=[],
    )
    namespace = {}
    exec(compile(subset, "app_selection_subset", "exec"), namespace)
    return namespace["_select_pod_from_candidates"]


select_pod = _load_select_pod()


class FakeRedisHash:
    """Minimal stand-in for the Redis hash holding the workload spec.

    read_delay widens the read-modify-write window so an implementation that
    checks capacity outside the lock fails these tests every run rather than
    occasionally.
    """

    def __init__(self, read_delay=0.001):
        self.data = {}
        self.read_delay = read_delay

    def hget(self, key, field):
        time.sleep(self.read_delay)
        return self.data.get(key, {}).get(field)

    def hset(self, key, field, value):
        self.data.setdefault(key, {})[field] = value

    def hgetall(self, key):
        return dict(self.data.get(key, {}))

    def hdel(self, key, field):
        self.data.get(key, {}).pop(field, None)


def make_redis_cfg(read_delay=0.001):
    """A redisconfig backed by FakeRedisHash.

    __init__ is bypassed because it opens a real connection and constructs a real
    redis_lock; the lock is replaced with a process-local one that provides the
    same mutual exclusion between threads.
    """
    cfg = object.__new__(redisconfig)
    cfg.wl_spec_obj = CACHE_KEY
    cfg.wl_spec = None
    cfg.even_obj = EVENT_FIELD
    cfg.id_field = ID_FIELD
    cfg.redis_lock_timeout = 1
    cfg.redis_connection = FakeRedisHash(read_delay=read_delay)
    guard = threading.RLock()

    @contextmanager
    def hold(retry_sleep_sec=None):
        with guard:
            yield

    @contextmanager
    def try_acquire(max_attempts=3, retry_sleep_sec=None):
        with guard:
            yield True

    cfg._workload_spec_lock_hold = hold
    cfg._workload_spec_lock_try = try_acquire
    return cfg


def stream(camera_id):
    return {EVENT_FIELD: {ID_FIELD: camera_id}}


def stored_ids(cfg, pod_name):
    raw = cfg.redis_connection.data.get(CACHE_KEY, {}).get(pod_name)
    return [s[EVENT_FIELD][ID_FIELD] for s in json.loads(raw)] if raw else []


def candidate(pod_name, spec_count):
    """One eligible_pods entry: (podInfoItm, spec_count, wl_spec)."""
    return ({"podName": pod_name}, spec_count, None)


def place(cfg, snapshot, camera_id):
    """Phase 2 of provisionStreamRedis: propose, reserve, drop and re-select.

    Returns the pod that accepted the stream, or None when every candidate
    refused — the case that makes provisionStreamRedis fall through to scale-up
    or add_stream_failed.
    """
    candidates = list(snapshot)
    while candidates:
        pod, _count, _spec = select_pod(candidates, "lru_round_robin")
        name = pod["podName"]
        if cfg.tryReserveWorkLoadSpec(name, stream(camera_id), THRESHOLD):
            return name
        candidates = [c for c in candidates if c[0]["podName"] != name]
    return None


def run_concurrently(target, count):
    threads = [threading.Thread(target=target, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_reserve_refuses_once_pod_reaches_threshold():
    cfg = make_redis_cfg(read_delay=0)

    granted = [
        cfg.tryReserveWorkLoadSpec("pod-0", stream("cam-{}".format(i)), THRESHOLD)
        for i in range(THRESHOLD + 2)
    ]

    assert granted == [True] * THRESHOLD + [False, False]
    assert cfg.getSpecCount("pod-0") == THRESHOLD


def test_reservations_are_tracked_per_pod():
    cfg = make_redis_cfg(read_delay=0)
    for i in range(THRESHOLD):
        assert cfg.tryReserveWorkLoadSpec("pod-0", stream("a-{}".format(i)), THRESHOLD)

    assert cfg.tryReserveWorkLoadSpec("pod-0", stream("a-extra"), THRESHOLD) is False
    assert cfg.tryReserveWorkLoadSpec("pod-1", stream("b-0"), THRESHOLD) is True


def test_concurrent_reservations_never_exceed_threshold():
    cfg = make_redis_cfg()
    granted = []
    granted_lock = threading.Lock()

    def reserve(i):
        ok = cfg.tryReserveWorkLoadSpec("pod-0", stream("cam-{:02d}".format(i)), THRESHOLD)
        with granted_lock:
            granted.append(ok)

    run_concurrently(reserve, 20)

    ids = stored_ids(cfg, "pod-0")
    assert sum(granted) == THRESHOLD
    assert len(ids) == THRESHOLD
    assert len(set(ids)) == THRESHOLD, "a reserved slot was overwritten: {}".format(ids)


def test_concurrent_adds_do_not_lose_streams():
    """addWorkLoadSpec must read and write the pod's list inside one lock hold."""
    cfg = make_redis_cfg()

    def add(i):
        cfg.addWorkLoadSpec("pod-0", None, stream("cam-{:02d}".format(i)))

    run_concurrently(add, 20)

    ids = stored_ids(cfg, "pod-0")
    assert len(ids) == 20, "concurrent adds lost streams: kept {}".format(sorted(ids))
    assert len(set(ids)) == 20


def test_placement_skips_pod_that_filled_after_the_snapshot():
    cfg = make_redis_cfg(read_delay=0)
    for i in range(THRESHOLD):
        cfg.tryReserveWorkLoadSpec("p-1", stream("pre-{}".format(i)), THRESHOLD)

    # The snapshot is stale: it still reports p-1 as the least loaded pod.
    snapshot = [candidate("p-0", 2), candidate("p-1", 0), candidate("p-2", 3)]

    assert place(cfg, snapshot, "cam-new") == "p-0"
    assert cfg.getSpecCount("p-1") == THRESHOLD


def test_placement_reports_no_pod_when_every_candidate_is_full():
    cfg = make_redis_cfg(read_delay=0)
    for pod in ("p-0", "p-1"):
        for i in range(THRESHOLD):
            cfg.tryReserveWorkLoadSpec(pod, stream("{}-{}".format(pod, i)), THRESHOLD)

    snapshot = [candidate("p-0", 0), candidate("p-1", 0)]

    assert place(cfg, snapshot, "cam-new") is None


def test_concurrent_placement_fills_the_pool_without_overfilling_any_pod():
    """Every caller starts from the same stale all-empty snapshot."""
    cfg = make_redis_cfg()
    pods = ["p-0", "p-1", "p-2"]
    capacity = THRESHOLD * len(pods)
    results = []
    results_lock = threading.Lock()

    def add(i):
        snapshot = [candidate(p, 0) for p in pods]
        pod = place(cfg, snapshot, "cam-{:02d}".format(i))
        with results_lock:
            results.append(pod)

    run_concurrently(add, capacity + 5)

    placed = [pod for pod in results if pod is not None]
    counts = {pod: len(stored_ids(cfg, pod)) for pod in pods}
    assert len(placed) == capacity
    assert sum(counts.values()) == capacity
    assert max(counts.values()) == THRESHOLD, "pod overfilled: {}".format(counts)


def test_lru_selects_pod_with_fewest_streams():
    snapshot = [candidate("p-0", 2), candidate("p-1", 1), candidate("p-2", 3)]
    assert select_pod(snapshot, "lru_round_robin")[0]["podName"] == "p-1"


def test_lru_breaks_ties_by_ordinal_not_list_order():
    snapshot = [candidate("p-2", 0), candidate("p-10", 0), candidate("p-1", 0)]
    assert select_pod(snapshot, "lru_round_robin")[0]["podName"] == "p-1"


def test_lru_breaks_ties_by_name_for_non_indexed_pods():
    snapshot = [candidate("gamma", 0), candidate("alpha", 0)]
    assert select_pod(snapshot, "lru_round_robin")[0]["podName"] == "alpha"


def test_sequential_takes_first_candidate_regardless_of_load():
    snapshot = [candidate("p-9", 3), candidate("p-0", 0)]
    assert select_pod(snapshot, "sequential")[0]["podName"] == "p-9"
