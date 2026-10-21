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

"""In-memory backend-specific tests.

These cover behaviour that the conformance suite does not exercise:

* Cross-process writes routed through ``mp.Queue`` + drainer.
* Owner-only enforcement on reads.
* Idempotency of ``start_drainers`` / ``shutdown``.
"""

from __future__ import annotations

import multiprocessing
import os
import time

import pytest

from tracking_store import InMemoryTrackingStore

from tracking_store.tests.conftest import make_obj


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_start_drainers_is_idempotent():
    store = InMemoryTrackingStore()
    try:
        store.start_drainers()
        store.start_drainers()  # second call is a no-op
        # Four drainers: insert, delete, relabel, lookup. The lookup
        # one was added when lookup_anywhere() RPC was introduced.
        assert len(store._drainer_threads) == 4
    finally:
        store.shutdown()


def test_shutdown_is_idempotent():
    store = InMemoryTrackingStore()
    store.start_drainers()
    store.shutdown()
    store.shutdown()  # no exception


def test_attach_post_fork_is_a_noop():
    store = InMemoryTrackingStore()
    try:
        store.attach_post_fork()
        store.attach_post_fork()
    finally:
        store.shutdown()


# ---------------------------------------------------------------------------
# Owner enforcement (reads from a non-owner pid must raise).
#
# We fake "non-owner" by pretending the store was constructed in a
# different pid -- much cheaper than spawning a real process for what
# is essentially a guard-clause check.
# ---------------------------------------------------------------------------


def test_reads_require_owner_pid():
    store = InMemoryTrackingStore()
    try:
        store._owner_pid = os.getpid() + 1  # pretend we are a child
        with pytest.raises(RuntimeError, match="owner process"):
            store.lookup("1", "cam", 0)
        with pytest.raises(RuntimeError, match="owner process"):
            store.get_by_object("1")
        with pytest.raises(RuntimeError, match="owner process"):
            store.get_all()
        with pytest.raises(RuntimeError, match="owner process"):
            store.snapshot()
        with pytest.raises(RuntimeError, match="owner process"):
            store.unclustered_keys()
        with pytest.raises(RuntimeError, match="owner process"):
            store.unclustered_bboxes_in_frame_range(0, 100)
        with pytest.raises(RuntimeError, match="owner process"):
            store.stats()
        with pytest.raises(RuntimeError, match="owner process"):
            store.clear()
    finally:
        store._force_owner_for_tests()
        store.shutdown()


def test_non_owner_writes_route_through_queue():
    store = InMemoryTrackingStore()
    try:
        # Drainer must be running before non-owner writes land.
        store.start_drainers()

        # Pretend to be a child process: writes should hit the queue
        # rather than the dict.
        store._owner_pid = os.getpid() + 1
        store.insert(make_obj("1", "cam", 10))
        store.insert(make_obj("1", "cam", 20))
        store.delete_below("cam", 15)  # should drop frame 10 once applied

        # Restore owner for the read.
        store._force_owner_for_tests()

        # Spin until the drainer has applied everything (bounded wait).
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            snap = store.get_all()
            frames = [o.frame_id for o in snap.get("1", {}).get("cam", [])]
            if frames == [20]:
                break
            time.sleep(0.02)

        snap = store.get_all()
        assert [o.frame_id for o in snap["1"]["cam"]] == [20]
    finally:
        store._force_owner_for_tests()
        store.shutdown()


# ---------------------------------------------------------------------------
# True cross-process: spawn a child that uses the store and verify the
# owner sees the writes. Skipped when fork isn't available (e.g. some
# Windows test runners), since the store relies on fork semantics today.
# ---------------------------------------------------------------------------


def _child_inserts(store: InMemoryTrackingStore) -> None:
    store.attach_post_fork()
    for fid in (1, 2, 3):
        store.insert(make_obj("1", "cam", fid))


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="fork() not available on this platform",
)
def test_child_process_inserts_visible_to_owner():
    ctx = multiprocessing.get_context("fork")
    store = InMemoryTrackingStore()
    try:
        store.start_drainers()
        proc = ctx.Process(target=_child_inserts, args=(store,))
        proc.start()
        proc.join(timeout=5)
        assert proc.exitcode == 0, "child process failed"

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            frames = [o.frame_id for o in store.get_by_object("1").get("cam", [])]
            if frames == [1, 2, 3]:
                break
            time.sleep(0.02)

        frames = [o.frame_id for o in store.get_by_object("1").get("cam", [])]
        assert frames == [1, 2, 3]
    finally:
        store.shutdown()


# ---------------------------------------------------------------------------
# lookup_anywhere RPC (the synchronous cross-process read pattern that
# replaced main.py's _compressed_embeddings_metadata_resolver bouncer)
# ---------------------------------------------------------------------------


def _child_calls_lookup_anywhere(store: InMemoryTrackingStore, out_queue) -> None:
    """Run in a forked child: do a cross-process lookup_anywhere().

    The child is by definition a non-owner, so the call must NOT take
    the owner-only-reads short-circuit; it must travel through the
    owner-side ts-lookup-drainer thread and come back with the right
    answer.
    """
    try:
        store.attach_post_fork()
        hit = store.lookup_anywhere("42", "camA", 100)
        miss = store.lookup_anywhere("does-not-exist", "camA", 100)
        out_queue.put(("ok", hit.frame_id if hit else None, miss))
    except Exception as e:  # pragma: no cover -- regression guard
        out_queue.put(("err", repr(e), None))


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="fork() not available on this platform",
)
def test_lookup_anywhere_from_child_process():
    ctx = multiprocessing.get_context("fork")
    store = InMemoryTrackingStore()
    try:
        store.start_drainers()
        # Owner-side insert so the child has something to find.
        store.insert(make_obj("42", "camA", 100, confidence=0.9))

        q: multiprocessing.Queue = ctx.Queue()
        proc = ctx.Process(target=_child_calls_lookup_anywhere, args=(store, q))
        proc.start()
        proc.join(timeout=10)
        assert proc.exitcode == 0, "child process failed"

        status, hit_frame, miss = q.get(timeout=2)
        assert status == "ok", f"child reported error: {hit_frame}"
        assert hit_frame == 100
        assert miss is None
    finally:
        store.shutdown()


def test_lookup_anywhere_owner_path_skips_rpc():
    """Owner-process callers must shortcut to lookup(); the RPC
    machinery is reserved for non-owner callers.

    We assert this indirectly: if start_drainers() is never called,
    the ts-lookup-drainer doesn't exist, so any RPC path would hang.
    Owner-side lookup_anywhere() must therefore return immediately.
    """
    store = InMemoryTrackingStore()
    try:
        # NOTE: start_drainers() intentionally NOT called.
        store.insert(make_obj("1", "cam", 1))
        hit = store.lookup_anywhere("1", "cam", 1)
        assert hit is not None
        assert hit.frame_id == 1
    finally:
        store.shutdown()
