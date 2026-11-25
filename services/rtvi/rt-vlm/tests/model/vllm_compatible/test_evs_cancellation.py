# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise the actual EVS bridge without importing CUDA or vLLM."""

import ast
import asyncio
import concurrent.futures
import logging
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
import uuid
import traceback
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from common.cancellable_coroutine_future import _CancellableCoroutineFuture


SOURCE = Path(__file__).resolve().parents[3] / "src/models/vllm_compatible/vllm_compatible_model.py"


class ServiceError(RuntimeError):
    pass


def load_model():
    tree = ast.parse(SOURCE.read_text())
    model = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "VllmCompatible")
    method = next(
        n
        for n in model.body
        if isinstance(n, ast.FunctionDef) and n.name == "_generate_evs_session"
    )
    cls = ast.ClassDef(name="Model", bases=[], keywords=[], body=[method], decorator_list=[])
    namespace = {
        "asyncio": asyncio,
        "concurrent": concurrent,
        "uuid": uuid,
        "_CancellableCoroutineFuture": _CancellableCoroutineFuture,
        "logger": logging.getLogger(__name__),
        "traceback": traceback,
        "ServiceException": ServiceError,
        "VlmModelOutput": lambda **kwargs: SimpleNamespace(**kwargs),
        "_merge_mm_processor_kwargs": lambda defaults, requested: requested or {},
        "_EVS_MM_PROCESSOR_DEFAULTS": {},
        "_EVS_MIN_CLIP_FRAMES": 2,
        "_get_max_video_frames": lambda: 32,
        "_cap_video_frames": lambda images, times, maximum: (images, times),
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(SOURCE), "exec"
        ),
        namespace,
    )
    return namespace["Model"]


class Frames:
    def __len__(self):
        return 3

    def cpu(self):
        return self

    def numpy(self):
        return self


class TestEvsCancellation(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.model = load_model()()
        self.model._vlm_model_type = "cosmos-reason2"
        self.model._inflight_req_ids = []
        self.model._event_loop = self.loop
        self.model._output_tpool = self.pool
        self.model._ensure_evs_session = lambda stream_id, **kwargs: stream_id
        self.model._uses_absolute_timestamp_metadata = lambda: False
        self.entered = threading.Event()
        self.cancelled = threading.Event()
        self.drained = threading.Event()

        async def events():
            return asyncio.Event(), asyncio.Event()

        self.finish, self.drain = asyncio.run_coroutine_threadsafe(events(), self.loop).result(
            timeout=1
        )

        async def add_clip_tensors(**kwargs):
            self.entered.set()
            try:
                await self.finish.wait()
                return SimpleNamespace(
                    tokens_used=0,
                    tokens_remaining=1,
                    frames_kept=3,
                    frames_dropped=0,
                    generated=False,
                )
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.drain.wait()
                self.drained.set()
                raise

        self.model._evs_handler = SimpleNamespace(add_clip_tensors=add_clip_tensors)

    def tearDown(self):
        self.loop.call_soon_threadsafe(self.finish.set)
        self.loop.call_soon_threadsafe(self.drain.set)
        self.pool.shutdown(wait=True)
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0.01), self.loop).result(timeout=2)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self.loop.close()

    def generate(self, stream_id="one"):
        return self.model._generate_evs_session(
            "describe",
            Frames(),
            {},
            [0, 1, 2],
            SimpleNamespace(
                streamId=stream_id, start_pts=0, end_pts=3_000_000_000, is_last=False, chunkIdx=0
            ),
        )

    def test_running_cancel_reaches_handler_and_keeps_admission_until_drained(self):
        future = self.generate()
        self.assertTrue(self.entered.wait(1))
        completed = threading.Event()
        future.add_done_callback(lambda _: completed.set())
        self.assertFalse(future.cancel())
        self.assertTrue(self.cancelled.wait(1), "Cancellation did not reach the EVS coroutine")
        future.cancel()  # Repeated disconnect cleanup must not interrupt draining.
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), self.loop).result(timeout=1)
        self.assertFalse(completed.is_set())
        self.assertFalse(future.done())
        self.assertEqual(len(self.model._inflight_req_ids), 1)
        self.loop.call_soon_threadsafe(self.drain.set)
        self.assertTrue(completed.wait(1))
        self.assertTrue(self.drained.is_set())
        self.assertTrue(future.cancelled())
        self.assertTrue(future.cancel())
        self.assertEqual(self.model._inflight_req_ids, [])

    def test_cancel_before_coroutine_starts_releases_admission(self):
        blocked, unblock = threading.Event(), threading.Event()

        def block_loop():
            blocked.set()
            unblock.wait(2)

        self.loop.call_soon_threadsafe(block_loop)
        self.assertTrue(blocked.wait(1))
        future = self.generate()
        future.cancel()
        done = threading.Event()
        future.add_done_callback(lambda _: done.set())
        unblock.set()
        self.assertTrue(done.wait(1))
        self.assertTrue(future.cancelled())
        self.assertFalse(self.entered.is_set())
        self.assertEqual(self.model._inflight_req_ids, [])

    def test_success_returns_output_and_releases_admission(self):
        future = self.generate()
        self.assertTrue(self.entered.wait(1))
        self.assertEqual(len(self.model._inflight_req_ids), 1)
        self.loop.call_soon_threadsafe(self.finish.set)
        self.assertEqual(future.result(timeout=1)[0].output, "")
        self.assertEqual(self.model._inflight_req_ids, [])

    def test_cancelling_one_clip_leaves_other_clip_running(self):
        first = self.generate("one")
        self.assertTrue(self.entered.wait(1))
        second = self.generate("two")
        first.cancel()
        self.assertTrue(self.cancelled.wait(1))
        self.loop.call_soon_threadsafe(self.drain.set)
        done = threading.Event()
        first.add_done_callback(lambda _: done.set())
        self.assertTrue(done.wait(1))
        self.assertFalse(second.done())
        self.assertEqual(len(self.model._inflight_req_ids), 1)
        self.loop.call_soon_threadsafe(self.finish.set)
        second.result(timeout=1)
        self.assertEqual(self.model._inflight_req_ids, [])

    def test_handler_error_remains_visible_and_releases_admission(self):
        async def fail(**kwargs):
            raise RuntimeError("encode failed")

        self.model._evs_handler.add_clip_tensors = fail
        with self.assertLogs(level="ERROR"):
            future = self.generate()
            with self.assertRaisesRegex(RuntimeError, "encode failed"):
                future.result(timeout=1)
        self.assertEqual(self.model._inflight_req_ids, [])

    def test_closed_loop_submission_releases_admission(self):
        closed = asyncio.new_event_loop()
        closed.close()
        self.model._event_loop = closed
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.generate()
        self.assertEqual(self.model._inflight_req_ids, [])


if __name__ == "__main__":
    unittest.main()
