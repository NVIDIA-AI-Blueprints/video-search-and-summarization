# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CPU-only ownership tests; no model, torch, CUDA, or vLLM is imported."""

import asyncio
import ast
from collections.abc import AsyncGenerator
import concurrent.futures
import importlib.util
import os
import sys
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _load_tracker():
    path = (
        Path(__file__).resolve().parents[2]
        / "src/models/vllm_compatible/request_generation_tracker.py"
    )
    spec = importlib.util.spec_from_file_location("request_generation_tracker", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.RequestGenerationTracker


RequestGenerationTracker = _load_tracker()


def _source_method(relative_path, class_name, name, extra_namespace=None):
    """Exercise production methods without importing vLLM's GPU runtime."""
    path = Path(__file__).resolve().parents[2] / relative_path
    source = ast.parse(path.read_text())
    engine = next(
        node for node in source.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in engine.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name
    )
    helpers = [
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "_is_evs_session_enabled"
    ]
    module = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *helpers,
            method,
        ],
        type_ignores=[],
    )
    namespace = {
        "asyncio": asyncio,
        "AsyncGenerator": AsyncGenerator,
        "EngineCoreRequest": type("EngineCoreRequest", (), {}),
        "PoolingParams": type("PoolingParams", (), {}),
        "concurrent": concurrent,
        "os": os,
        "uuid": uuid,
    }
    namespace.update(extra_namespace or {})
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


def _engine_method(name):
    return _source_method(
        "docker/rtvi_vlm/patches/evs_vllm_public_files/v1/engine/async_llm.py",
        "AsyncLLM",
        name,
    )


class RequestCancellationTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever)
        self.thread.start()
        self.tracker = RequestGenerationTracker()
        self.aborted = []

    def tearDown(self):
        async def drain():
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(drain(), self.loop).result(timeout=2)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self.loop.close()

    async def abort_engine(self, engine_ids):
        self.aborted.append(tuple(engine_ids))

    def submit_blocked(self, parent_id, engine_id, cleanup=None):
        started = threading.Event()

        async def generate():
            started.set()
            await asyncio.Event().wait()

        result = self.tracker.submit(parent_id, engine_id, generate, self.loop, cleanup)
        self.assertTrue(started.wait(timeout=2))
        return result

    def test_actual_engine_ids_and_parent_isolation(self):
        first = self.submit_blocked("first", "engine-first")
        second = self.submit_blocked("second", "engine-second")

        count = self.tracker.abort("first", self.loop, self.abort_engine, 2)

        self.assertEqual(count, 1)
        self.assertTrue(first.cancelled())
        self.assertFalse(second.done())
        self.assertEqual(set(sum(self.aborted, ())), {"engine-first"})
        self.tracker.abort("second", self.loop, self.abort_engine, 2)

    def test_all_generations_of_one_parent_are_aborted(self):
        first = self.submit_blocked("parent", "chunk-1")
        second = self.submit_blocked("parent", "chunk-2")
        self.assertEqual(self.tracker.abort("parent", self.loop, self.abort_engine, 2), 2)
        self.assertTrue(first.cancelled())
        self.assertTrue(second.cancelled())

    def test_cancellation_before_registration_prevents_submission(self):
        self.tracker.abort("parent", self.loop, self.abort_engine, 2)
        called = []
        result = self.tracker.submit(
            "parent",
            "late",
            lambda: called.append(True),
            self.loop,
        )
        self.assertTrue(result.cancelled())
        self.assertEqual(called, [])
        self.tracker.release("parent")

    def test_cancellation_before_coroutine_start_runs_cleanup(self):
        loop = asyncio.new_event_loop()
        cleaned = []
        called = []
        result = self.tracker.submit(
            "parent",
            "engine",
            lambda: called.append(True),
            loop,
            cleanup=lambda: cleaned.append(True),
        )
        result.cancel()
        loop.run_until_complete(asyncio.sleep(0))
        loop.close()
        self.assertEqual(called, [])
        self.assertEqual(cleaned, [True])
        self.tracker.release("parent")

    def test_public_future_cancellation_is_not_drain(self):
        started = threading.Event()
        cleaning = threading.Event()
        release_cleanup = threading.Event()

        async def generate():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await self.loop.run_in_executor(None, release_cleanup.wait)

        result = self.tracker.submit("parent", "engine", generate, self.loop)
        self.assertTrue(started.wait(timeout=2))
        result.cancel()
        self.assertTrue(cleaning.wait(timeout=2))
        self.assertTrue(result.cancelled())
        try:
            with self.assertRaises(RuntimeError):
                self.tracker.release("parent")
            with self.assertRaises(concurrent.futures.TimeoutError):
                self.tracker.abort("parent", self.loop, self.abort_engine, 0.02)
        finally:
            release_cleanup.set()
        self.tracker.abort("parent", self.loop, self.abort_engine, 2)
        self.tracker.release("parent")

    def test_repeated_abort_is_idempotent(self):
        result = self.submit_blocked("parent", "engine")
        self.tracker.abort("parent", self.loop, self.abort_engine, 2)
        self.assertEqual(self.tracker.abort("parent", self.loop, self.abort_engine, 2), 0)
        self.assertTrue(result.cancelled())

    def test_normal_completion_preserves_result_and_runs_cleanup_once(self):
        cleaned = []

        async def generate():
            return "unchanged answer"

        result = self.tracker.submit(
            "parent",
            "engine",
            generate,
            self.loop,
            cleanup=lambda: cleaned.append(True),
        )
        self.assertEqual(result.result(timeout=2), "unchanged answer")
        self.assertEqual(cleaned, [True])
        self.assertEqual(self.aborted, [])
        self.tracker.release("parent")

    def test_generation_failure_preserves_exception(self):
        async def generate():
            raise ValueError("bad input")

        result = self.tracker.submit("parent", "engine", generate, self.loop)
        with self.assertRaisesRegex(ValueError, "bad input"):
            result.result(timeout=2)
        self.tracker.release("parent")

    def test_abort_failure_remains_visible(self):
        self.submit_blocked("parent", "engine")

        async def fail_abort(_):
            raise RuntimeError("engine unavailable")

        with self.assertRaisesRegex(RuntimeError, "engine unavailable"):
            self.tracker.abort("parent", self.loop, fail_abort, 2)
        self.assertTrue(self.tracker.submit("parent", "late", lambda: None, self.loop).cancelled())

    def test_final_engine_abort_failure_cannot_be_forgotten_after_coroutine_drain(self):
        result = self.submit_blocked("parent", "engine")
        unrelated = self.submit_blocked("other", "other-engine")
        calls = []

        async def fail_final_abort(engine_ids):
            calls.append(engine_ids)
            if len(calls) == 2:
                raise RuntimeError("final engine abort failed")

        with self.assertRaisesRegex(RuntimeError, "final engine abort failed"):
            self.tracker.abort("parent", self.loop, fail_final_abort, 2)
        self.assertTrue(result.cancelled())
        self.assertEqual(calls, [("engine",), ("engine",)])
        self.assertFalse(unrelated.done())
        with self.assertRaisesRegex(RuntimeError, "final engine abort failed"):
            self.tracker.abort("parent", self.loop, self.abort_engine, 2)
        with self.assertRaisesRegex(RuntimeError, "final engine abort failed"):
            self.tracker.release("parent")
        self.assertTrue(self.tracker.submit("parent", "late", lambda: None, self.loop).cancelled())
        self.assertEqual(self.tracker.abort("other", self.loop, self.abort_engine, 2), 1)
        self.tracker.release("other")

    def test_cleanup_failure_is_not_reported_as_drain(self):
        def cleanup():
            raise RuntimeError("cleanup failed")

        self.submit_blocked("parent", "engine", cleanup=cleanup)
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            self.tracker.abort("parent", self.loop, self.abort_engine, 2)
        with self.assertRaises(RuntimeError):
            self.tracker.release("parent")

    def test_engine_cancellation_failure_cannot_be_retried_as_drained(self):
        started = threading.Event()

        async def generate():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                raise RuntimeError("inner engine abort failed")

        result = self.tracker.submit("parent", "engine", generate, self.loop)
        self.assertTrue(started.wait(timeout=2))
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "inner engine abort failed"):
                self.tracker.abort("parent", self.loop, self.abort_engine, 2)
        with self.assertRaisesRegex(RuntimeError, "inner engine abort failed"):
            result.result(timeout=2)
        with self.assertRaises(RuntimeError):
            self.tracker.release("parent")

    def test_model_submission_passes_actual_engine_id_to_abort(self):
        model_path = "src/models/vllm_compatible/vllm_compatible_model.py"
        started = threading.Event()
        engine_ids = []

        async def generate(_inputs, _sampling, engine_id, _preserve):
            engine_ids.append(engine_id)
            started.set()
            await asyncio.Event().wait()

        model = SimpleNamespace(
            _request_generations=self.tracker,
            _event_loop=self.loop,
            _llm=SimpleNamespace(abort=self.abort_engine),
            _inflight_req_ids=[],
            _resolve_prompt_reasoning_config=lambda config: config,
            _apply_chat_template=lambda *_: "prompt",
            _processor=SimpleNamespace(
                tokenizer=SimpleNamespace(encode=lambda *_args, **_kwargs: [1])
            ),
            _process_text_only_async=generate,
        )
        model._release_live_request = lambda _, engine_id: model._inflight_req_ids.remove(engine_id)
        submit = _source_method(
            model_path,
            "VllmCompatible",
            "generate_text_only",
            {"_build_vllm_sampling_kwargs": lambda _: {}},
        )
        model._apply_reasoning_suppression_sampling_params = lambda *_: None
        with patch.dict(sys.modules, {"vllm": SimpleNamespace(SamplingParams=lambda **_: None)}):
            future = submit(
                model,
                [],
                SimpleNamespace(preserve_reasoning_tags=False),
                parent_request_id="parent-from-pipeline",
            )
        self.assertTrue(started.wait(timeout=2))
        abort = _source_method(model_path, "VllmCompatible", "abort_request_generations")
        with patch.dict(os.environ, {"VIA_EVS_SESSION": ""}):
            self.assertEqual(abort(model, "parent-from-pipeline", 2), 1)
        self.assertTrue(future.cancelled())
        self.assertEqual(set(sum(self.aborted, ())), set(engine_ids))
        self.assertEqual(model._inflight_req_ids, [])

    def test_model_abort_rejects_shared_evs_sessions_without_touching_backend(self):
        model_path = "src/models/vllm_compatible/vllm_compatible_model.py"
        abort = _source_method(model_path, "VllmCompatible", "abort_request_generations")
        tracker, engine, sessions = MagicMock(), MagicMock(), MagicMock()
        model = SimpleNamespace(
            _request_generations=tracker,
            _event_loop=self.loop,
            _llm=engine,
            _evs_handler=sessions,
        )
        for enabled in ("true", "1"):
            with (
                self.subTest(enabled=enabled),
                patch.dict(os.environ, {"VIA_EVS_SESSION": enabled}),
            ):
                with self.assertRaisesRegex(NotImplementedError, "shared EVS sessions"):
                    abort(model, "parent", 2)
        tracker.abort.assert_not_called()
        engine.abort.assert_not_called()
        sessions.delete_session.assert_not_called()

    def test_invalid_timeout_does_not_cancel_parent(self):
        with self.assertRaises(ValueError):
            self.tracker.abort("parent", self.loop, self.abort_engine, 0)
        generation = self.tracker.register("parent", "engine")
        self.tracker.finished(generation)


class EnginePreprocessingCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_preprocessing_is_drained_before_return(self):
        started = threading.Event()
        finish = threading.Event()
        admitted = []

        def process_inputs(*_args, **_kwargs):
            started.set()
            finish.wait(timeout=2)
            return SimpleNamespace()

        async def get_supported_tasks():
            return []

        engine = SimpleNamespace(
            errored=False,
            vllm_config=SimpleNamespace(
                cache_config=SimpleNamespace(kv_sharing_fast_prefill=False)
            ),
            get_supported_tasks=get_supported_tasks,
            _preprocess_executor=None,
            input_processor=SimpleNamespace(process_inputs=process_inputs),
        )
        task = asyncio.create_task(
            _engine_method("add_request")(
                engine,
                "actual-engine-id",
                {},
                SimpleNamespace(n=1),
            )
        )
        self.assertTrue(await asyncio.to_thread(started.wait, 2))
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        task.cancel()  # A second disconnect must not cut the drain short.
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(admitted, [])

    async def test_admission_finishes_before_internal_abort(self):
        admitted = asyncio.Event()
        release = asyncio.Event()
        operations = []

        async def add_request(_request):
            admitted.set()
            await release.wait()
            operations.append("admitted")

        async def abort(engine_id, internal=False):
            operations.append(("abort", engine_id, internal))

        engine = SimpleNamespace(
            output_processor=SimpleNamespace(add_request=lambda *_: None),
            engine_core=SimpleNamespace(add_request_async=add_request),
            abort=abort,
            log_requests=False,
        )
        request = SimpleNamespace(request_id="internal-engine-id")
        task = asyncio.create_task(
            _engine_method("_add_request")(
                engine,
                request,
                None,
                None,
                0,
                None,
            )
        )
        await admitted.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(operations, ["admitted", ("abort", "internal-engine-id", True)])


if __name__ == "__main__":
    unittest.main()
