# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise the real handler lifecycle without importing CUDA/DeepStream.

The class methods are compiled from the production AST; the surrounding media
and metrics adapters are fakes. Full runtime imports remain covered by the
container test suite.
"""

from __future__ import annotations

import ast
import logging
import sys
import threading
import time
import types
import unittest
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum, auto
from pathlib import Path
from threading import Event, Lock, RLock
from types import SimpleNamespace


def load_handler():
    path = Path(__file__).resolve().parents[2] / "src/server/rtvi_stream_handler.py"
    tree = ast.parse(path.read_text())
    wanted = {
        "abort_request",
        "request_is_done",
        "_finish_cancelled_file_request",
        "_release_file_request_resources",
        "_on_vlm_chunk_response",
        "_process_output",
        "wait_for_request_done",
        "_submit_file_dispatches",
        "_maybe_complete_file_request",
        "query",
    }
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_completed_event":
            nodes.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "RequestInfo":
            nodes.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "RTVIStreamHandler":
            node.body = [method for method in node.body if getattr(method, "name", None) in wanted]
            nodes.append(node)
    module = types.ModuleType("_isolated_request_handler")
    sys.modules[module.__name__] = module
    module.__dict__.update(
        dataclass=dataclass,
        field=field,
        uuid=uuid,
        StrEnum=StrEnum,
        auto=auto,
        Event=Event,
        Lock=Lock,
        RLock=RLock,
        Thread=threading.Thread,
        deque=deque,
        time=time,
        logger=logging.getLogger(__name__),
        nvtx=SimpleNamespace(start_range=lambda **kw: None, end_range=lambda _: None),
        get_tracer=lambda: None,
        MediaFileInfo=SimpleNamespace(get_info=lambda _: SimpleNamespace(video_duration_nsec=10)),
    )
    nodes.insert(
        0, ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    )
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), "exec"
        ),
        module.__dict__,
    )
    return module.RequestInfo, module.RTVIStreamHandler


RequestInfo, Handler = load_handler()


class Counter:
    def __init__(self):
        self.value = 0

    def add(self, value):
        self.value += value


class Asset:
    def __init__(self, fail_once=False):
        self.releases = 0
        self.fail_once = fail_once
        self.path = "video.mp4"
        self.asset_id = "same-video"
        self.use_count = 1

    def lock(self):
        self.use_count += 1

    def unlock(self):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("asset release failed")
        self.releases += 1


class HandlerCancellationTests(unittest.TestCase):
    def setUp(self):
        self.handler = Handler.__new__(Handler)
        self.request = RequestInfo(request_id="A", status=RequestInfo.Status.PROCESSING)
        self.asset = Asset()
        self.request.assets = [self.asset]
        self.handler._lock = RLock()
        self.handler._request_info_map = {"A": self.request}
        self.handler._vlm_admission_ready_request_ids = {"A"}
        self.handler._vlm_admission_active_cost = 2.0
        self.request.active_file_chunk_costs = {0: 2.0}
        self.handler._metrics = SimpleNamespace(
            _queries_processed_counter=Counter(), _queries_pending_counter=Counter()
        )
        self.calls = []
        self.handler._vlm_pipeline = SimpleNamespace(
            abort_request=lambda rid, **kw: self.calls.append(("abort", rid)),
            release_request_cancellation=lambda rid: self.calls.append(("release", rid)),
        )
        self.handler._finalize_stream_fps_tracking = lambda _: None
        self.handler._cleanup_request_files = lambda _: None
        self.handler._dispatch_pending_file_chunks = lambda: None

    def test_abort_drains_then_releases_resources_once(self):
        self.handler.abort_request("A")
        self.handler.abort_request("A")
        self.assertEqual(self.calls, [("abort", "A"), ("release", "A")])
        self.assertEqual(self.asset.releases, 1)
        self.assertEqual(self.handler._metrics._queries_pending_counter.value, -1)
        self.assertEqual(self.handler._vlm_admission_active_cost, 0)
        self.assertEqual(self.request.status, RequestInfo.Status.CANCELLED)
        self.assertTrue(self.request.resources_released.is_set())
        self.assertTrue(self.handler.request_is_done("A"))

    def test_abort_failure_retains_assets_and_can_retry(self):
        def fail(*args, **kwargs):
            raise TimeoutError("engine still running")

        self.handler._vlm_pipeline.abort_request = fail
        with self.assertRaises(TimeoutError):
            self.handler.abort_request("A")
        self.assertEqual(self.asset.releases, 0)
        self.assertFalse(self.request.resources_released.is_set())
        self.assertTrue(self.request.cancel_requested.is_set())
        self.handler._vlm_pipeline.abort_request = lambda *a, **k: None
        self.handler.abort_request("A")
        self.assertEqual(self.asset.releases, 1)

    def test_partial_asset_cleanup_retry_never_unlocks_twice(self):
        other = Asset(fail_once=True)
        self.request.assets.append(other)
        with self.assertRaisesRegex(RuntimeError, "asset release failed"):
            self.handler.abort_request("A")
        self.assertFalse(self.request.resources_released.is_set())
        self.handler.abort_request("A")
        self.assertEqual(self.asset.releases, 1)
        self.assertEqual(other.releases, 1)
        self.assertTrue(self.request.resources_released.is_set())

    def test_producer_must_quiesce_before_abort_and_asset_release(self):
        self.request.producer_done.clear()
        finished = Event()

        def abort():
            self.handler.abort_request("A", timeout_sec=2)
            finished.set()

        thread = threading.Thread(target=abort)
        thread.start()
        try:
            self.assertTrue(self.request.cancel_requested.wait(timeout=1))
            self.assertEqual(self.calls, [("abort", "A")])
            self.assertEqual(self.asset.releases, 0)
        finally:
            self.request.producer_done.set()
            thread.join(timeout=2)
        self.assertTrue(finished.is_set())

    def test_late_result_releases_credit_but_cannot_publish_or_succeed(self):
        self.request.cancel_requested.set()
        result = SimpleNamespace(chunk=SimpleNamespace(chunkIdx=0), cancelled=True)
        self.handler._on_vlm_chunk_response(result, self.request)
        self.assertEqual(self.handler._vlm_admission_active_cost, 0)
        self.assertEqual(self.request.processed_chunk_list, [])
        self.assertFalse(self.request.resources_released.is_set())
        self.handler.abort_request("A")
        self.handler._process_output(self.request, False, [result])
        self.assertEqual(self.request.status, RequestInfo.Status.CANCELLED)

    def test_dispatch_handoff_finishes_before_cancellation_marker_is_released(self):
        started = Event()
        allow_enqueue = Event()
        finished = Event()

        def enqueue(*args):
            started.set()
            self.assertTrue(allow_enqueue.wait(timeout=2))

        self.handler._vlm_pipeline.enqueue_chunk = enqueue
        chunk = SimpleNamespace(chunkIdx=0)
        dispatch = threading.Thread(
            target=self.handler._submit_file_dispatches,
            args=([(self.request, chunk, None, False)],),
        )
        dispatch.start()
        self.assertTrue(started.wait(timeout=1))

        def abort():
            self.handler.abort_request("A", timeout_sec=2)
            finished.set()

        cancellation = threading.Thread(target=abort)
        cancellation.start()
        try:
            self.assertTrue(self.request.cancel_requested.wait(timeout=1))
            self.assertFalse(self.request.dispatch_done.is_set())
            self.assertNotIn(("release", "A"), self.calls)
            self.assertEqual(self.asset.releases, 0)
        finally:
            allow_enqueue.set()
            dispatch.join(timeout=2)
            cancellation.join(timeout=2)
        self.assertTrue(finished.is_set())
        self.assertTrue(self.request.dispatch_done.is_set())

    def test_completion_wins_before_cancellation(self):
        def complete(req, *args):
            req.status = RequestInfo.Status.SUCCESSFUL
            req.status_event.set()

        self.handler._process_output_unchecked = complete
        self.handler._process_output(self.request, False, [])
        self.handler.abort_request("A")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.request.status, RequestInfo.Status.SUCCESSFUL)

    def test_partial_normal_cleanup_can_be_cancelled_without_double_release(self):
        self.request.assets.append(Asset(fail_once=True))
        self.handler._process_output_unchecked = lambda req, *a: (
            self.handler._release_file_request_resources(req)
        )
        with self.assertRaisesRegex(RuntimeError, "asset release failed"):
            self.handler._process_output(self.request, False, [])
        self.assertFalse(self.request._file_finalized)
        self.handler.abort_request("A")
        self.assertEqual(self.asset.releases, 1)
        self.assertTrue(self.request.resources_released.is_set())

    def test_normal_completion_is_claimed_before_metadata_finalization(self):
        entered = Event()
        release = Event()
        prepared = []
        self.request.start_time = time.time()
        self.handler._vlm_pipeline.close_evs_sessions = lambda _: None
        self.handler._finalize_stream_fps_tracking = lambda _: prepared.append(True)

        def output(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(timeout=2))

        self.handler._process_output = output
        completing = threading.Thread(
            target=self.handler._maybe_complete_file_request, args=(self.request,)
        )
        completing.start()
        try:
            self.assertTrue(entered.wait(timeout=1))
            self.handler._maybe_complete_file_request(self.request)
            self.assertEqual(prepared, [True])
        finally:
            release.set()
            completing.join(timeout=2)

    def test_submission_failure_drains_before_releasing_assets(self):
        self.handler._args = SimpleNamespace(max_file_duration=0)
        self.handler._vlm_pipeline.ensure_model_available = lambda: None
        created = []

        def fail_after_submit(req, *args):
            created.append(req)
            self.assertEqual(self.asset.releases, 0)
            raise RuntimeError("split failed")

        self.handler._trigger_query = fail_after_submit
        query = SimpleNamespace(chunk_duration=0, chunk_overlap_duration=0, media_info=None)
        with self.assertRaisesRegex(RuntimeError, "split failed"):
            self.handler.query([self.asset], query)
        request_id = created[0].request_id
        self.assertEqual(self.calls, [("abort", request_id), ("release", request_id)])
        self.assertNotIn(request_id, self.handler._request_info_map)
        self.assertEqual(self.asset.releases, 1)
        self.assertEqual(self.handler._metrics._queries_pending_counter.value, 0)

    def test_failed_outcome_is_preserved_while_remaining_work_is_drained(self):
        self.request.status = RequestInfo.Status.FAILED
        self.handler.abort_request("A")
        self.assertEqual(self.request.status, RequestInfo.Status.FAILED)
        self.assertEqual(self.asset.releases, 1)

    def test_text_request_without_video_record_uses_pipeline_cancellation(self):
        self.handler.abort_request("text")
        self.assertEqual(self.calls, [("abort", "text"), ("release", "text")])

    def test_cancelled_is_terminal_and_waiter_returns(self):
        self.handler.abort_request("A")
        self.assertTrue(self.request.is_terminal)
        self.assertEqual(self.request.progress, 100)
        self.handler.wait_for_request_done("A")


if __name__ == "__main__":
    unittest.main()
