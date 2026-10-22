# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CPU-only HTTP lifecycle tests, including real TCP disconnects.

Run with ``pytest --confcutdir=tests/cancellation tests/cancellation`` to avoid
loading the unrelated CUDA/GStreamer fixtures. The small server-method harness
executes the production methods without importing the GPU application module.
"""

import ast
import asyncio
import functools
import importlib.util
import json
import logging
import queue
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request

_ROOT = Path(__file__).resolve().parents[2]
_HELPER_PATH = _ROOT / "src/server/request_lifecycle.py"
_SPEC = importlib.util.spec_from_file_location("request_lifecycle_under_test", _HELPER_PATH)
assert _SPEC and _SPEC.loader
_HELPER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_HELPER)


class _ServiceException(Exception):
    def __init__(self, message, code, status_code):
        super().__init__(message)
        self.status_code = status_code


def _server_harness():
    names = {
        "_track_temporary_chat_asset_cleanup_task",
        "_finalize_owned_http_request",
        "_wait_for_http_request",
        "_process_http_vlm_request",
        "_submit_text_request_for_http",
        "_request_cleanup_lifespan",
    }
    source = ast.parse((_ROOT / "src/server/rtvi_vlm_server.py").read_text())
    server = next(
        node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "RTVIServer"
    )
    methods = [node for node in server.body if getattr(node, "name", None) in names]
    assert {node.name for node in methods} == names
    cls = ast.ClassDef(name="ServerHarness", bases=[], keywords=[], body=methods, decorator_list=[])
    namespace = {
        "asyncio": asyncio,
        "functools": functools,
        "sys": sys,
        "asynccontextmanager": asynccontextmanager,
        "Request": Request,
        "Event": threading.Event,
        "ServiceException": _ServiceException,
        "ClientDisconnected": _HELPER.ClientDisconnected,
        "wait_for_completion_or_disconnect": _HELPER.wait_for_completion_or_disconnect,
        "logger": logging.getLogger(__name__),
    }
    exec(  # noqa: S102 - Execute only selected methods from this checkout, not external input.
        compile(
            ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
            "rtvi_vlm_server.py",
            "exec",
        ),
        namespace,
    )
    result = namespace["ServerHarness"]()
    result._http_request_finalization_tasks = {}
    result._temporary_chat_asset_cleanup_tasks = set()
    result._stream_handler = SimpleNamespace(
        request_is_done=MagicMock(return_value=False),
        request_is_drained=MagicMock(return_value=False),
        abort_request=MagicMock(),
    )
    result._cleanup_temporary_chat_assets = AsyncMock()
    return result


def test_completed_request_does_not_read_disconnect_channel():
    disconnected = AsyncMock(return_value=True)
    asyncio.run(_HELPER.wait_for_completion_or_disconnect(lambda: True, disconnected))
    disconnected.assert_not_awaited()


def test_disconnect_before_completion_is_distinct_from_timeout():
    with pytest.raises(_HELPER.ClientDisconnected):
        asyncio.run(
            _HELPER.wait_for_completion_or_disconnect(lambda: False, AsyncMock(return_value=True))
        )


def test_completion_wins_disconnect_race():
    done = False

    async def disconnect():
        nonlocal done
        done = True
        return True

    asyncio.run(_HELPER.wait_for_completion_or_disconnect(lambda: done, disconnect))


def test_server_deadline_is_distinct_from_disconnect():
    with pytest.raises(TimeoutError):
        asyncio.run(
            _HELPER.wait_for_completion_or_disconnect(
                lambda: False, AsyncMock(return_value=False), timeout=0
            )
        )


def test_finalizer_aborts_off_loop_then_cleans_assets_once():
    async def run():
        server = _server_harness()
        loop_thread = threading.get_ident()
        order = []

        def abort(*args, **kwargs):
            assert threading.get_ident() != loop_thread
            order.append("abort")

        async def cleanup(assets):
            assert assets == ["temporary"]
            order.append("cleanup")

        server._stream_handler.abort_request.side_effect = abort
        server._cleanup_temporary_chat_assets.side_effect = cleanup
        await asyncio.gather(
            *[server._finalize_owned_http_request("request", ["temporary"]) for _ in range(3)]
        )
        assert order == ["abort", "cleanup"]
        server._stream_handler.abort_request.assert_called_once_with(
            "request", reason="http_response_closed", timeout_sec=30
        )

    asyncio.run(run())


def test_completed_request_preserves_generation_and_uploaded_assets():
    async def run():
        server = _server_harness()
        server._stream_handler.request_is_done.return_value = True
        server._stream_handler.request_is_drained.return_value = True
        await server._finalize_owned_http_request("done")
        server._stream_handler.abort_request.assert_not_called()
        server._cleanup_temporary_chat_assets.assert_awaited_once_with([])

    asyncio.run(run())


def test_failed_status_notification_does_not_skip_draining_remaining_work():
    async def run():
        server = _server_harness()
        server._stream_handler.request_is_done.return_value = True
        server._stream_handler.request_is_drained.return_value = False
        await server._finalize_owned_http_request("failed", ["temp"])
        server._stream_handler.abort_request.assert_called_once()
        server._cleanup_temporary_chat_assets.assert_awaited_once_with(["temp"])

    asyncio.run(run())


def test_failed_drain_retains_temporary_assets():
    async def run():
        server = _server_harness()
        server._stream_handler.abort_request.side_effect = TimeoutError("still decoding")
        with pytest.raises(TimeoutError, match="still decoding"):
            await server._finalize_owned_http_request("request", ["temporary"])
        server._cleanup_temporary_chat_assets.assert_not_awaited()

    asyncio.run(run())


def test_http_task_cancellation_does_not_cancel_finalization():
    async def run():
        server = _server_harness()
        started, finish = threading.Event(), threading.Event()

        def abort(*args, **kwargs):
            started.set()
            assert finish.wait(2)

        server._stream_handler.abort_request.side_effect = abort
        waiter = asyncio.create_task(server._finalize_owned_http_request("request", ["temp"]))
        await asyncio.to_thread(started.wait, 2)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert server._temporary_chat_asset_cleanup_tasks
        finish.set()
        async with server._request_cleanup_lifespan(None):
            pass
        server._cleanup_temporary_chat_assets.assert_awaited_once_with(["temp"])
        assert not server._temporary_chat_asset_cleanup_tasks

    asyncio.run(run())


def test_abort_failure_does_not_replace_the_original_http_cancellation():
    async def run():
        server = _server_harness()
        server._stream_handler.abort_request.side_effect = TimeoutError("still decoding")
        started = asyncio.Event()

        async def request():
            try:
                started.set()
                await asyncio.Future()
            finally:
                await server._finalize_owned_http_request("request", preserve_error=True)

        waiter = asyncio.create_task(request())
        await started.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        server._cleanup_temporary_chat_assets.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("text", [False, True])
def test_failed_abort_keeps_submission_owned_until_its_late_failure(text):
    async def run():
        server = _server_harness()
        server._stream_handler.abort_request.side_effect = TimeoutError("abort unavailable")
        release = threading.Event()
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))

        def enqueue():
            assert release.wait(2)
            raise ValueError("late submission failure")

        async def submit(*, on_registered):
            on_registered("request")
            await asyncio.to_thread(release.wait, 2)
            raise ValueError("late submission failure")

        server._process_vlm_request = submit
        with ThreadPoolExecutor(max_workers=1) as server._async_executor:
            with pytest.raises(_ServiceException) as error:
                if text:
                    await server._submit_text_request_for_http(request, "request", enqueue)
                else:
                    await server._process_http_vlm_request(request)
            assert error.value.status_code == 499
            pending = [
                task for task in server._temporary_chat_asset_cleanup_tasks if not task.done()
            ]
            assert len(pending) == 1

            async def shutdown():
                async with server._request_cleanup_lifespan(None):
                    pass

            draining = asyncio.create_task(shutdown())
            await asyncio.sleep(0)
            assert not draining.done(), "shutdown lost ownership of the pending submission"
            release.set()
            await asyncio.wait_for(draining, 1)
        assert not server._temporary_chat_asset_cleanup_tasks
        # CPython's flag distinguishes an unobserved exception from one already
        # retrieved by the production tracking callback, before this test reads it.
        assert not pending[0]._log_traceback
        assert isinstance(pending[0].exception(), ValueError)
        server._cleanup_temporary_chat_assets.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("disconnect", [False, True])
def test_submission_id_is_recovered_after_http_cancellation(disconnect):
    async def run():
        server = _server_harness()
        registered = asyncio.Event()
        submission_started = asyncio.Event()

        async def submit(*, on_registered):
            on_registered("late-id")
            submission_started.set()
            await registered.wait()
            return "late-id", None, []

        server._process_vlm_request = submit
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
        waiter = asyncio.create_task(
            server._process_http_vlm_request(request, temp_asset_ids=["temp"])
        )
        await submission_started.wait()
        if disconnect:
            request.is_disconnected.return_value = True
            await asyncio.sleep(0.06)
        else:
            waiter.cancel()
            await asyncio.sleep(0)
        registered.set()
        with pytest.raises(_ServiceException if disconnect else asyncio.CancelledError):
            await waiter
        async with server._request_cleanup_lifespan(None):
            pass
        server._stream_handler.abort_request.assert_called_once_with(
            "late-id", reason="http_response_closed", timeout_sec=30
        )
        server._cleanup_temporary_chat_assets.assert_awaited_once_with(["temp"])

    asyncio.run(run())


def test_submission_failure_cleans_only_http_owned_assets():
    async def run():
        server = _server_harness()
        server._process_vlm_request = AsyncMock(side_effect=ValueError("bad media"))
        with pytest.raises(ValueError, match="bad media"):
            await server._process_http_vlm_request(
                SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
                temp_asset_ids=["temp"],
            )
        server._stream_handler.abort_request.assert_not_called()
        server._cleanup_temporary_chat_assets.assert_awaited_once_with(["temp"])

    asyncio.run(run())


def test_registered_video_submission_is_aborted_before_waiting_for_handoff():
    async def run():
        server = _server_harness()
        abort_unblocks_submission = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def submit(*, on_registered):
            on_registered("registered-parent")
            await abort_unblocks_submission.wait()
            return "registered-parent", None, []

        def abort(request_id, **kwargs):
            assert request_id == "registered-parent"
            loop.call_soon_threadsafe(abort_unblocks_submission.set)

        server._process_vlm_request = submit
        server._stream_handler.abort_request.side_effect = abort
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
        with pytest.raises(_ServiceException) as error:
            await asyncio.wait_for(server._process_http_vlm_request(request), timeout=1)
        assert error.value.status_code == 499
        server._stream_handler.abort_request.assert_called_once()

    asyncio.run(run())


def test_blocked_text_queue_preserves_event_loop_and_handoff_ownership():
    async def run():
        server = _server_harness()
        full_queue = queue.Queue(maxsize=1)
        full_queue.put("previous request")
        enqueue_started, cancellation_marked = threading.Event(), threading.Event()
        retired = threading.Event()

        def enqueue():
            enqueue_started.set()
            full_queue.put("cancelled parent")
            assert cancellation_marked.is_set()

        def abort(request_id, *, submission_done, **kwargs):
            assert request_id == "text-parent"
            cancellation_marked.set()
            full_queue.get_nowait()  # Cancelling active work frees the full queue.
            assert submission_done.wait(1), "marker retired before the handoff returned"
            assert full_queue.get_nowait() == "cancelled parent"
            retired.set()

        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
        server._stream_handler.abort_request.side_effect = abort
        with ThreadPoolExecutor(max_workers=1) as server._async_executor:
            waiter = asyncio.create_task(
                server._submit_text_request_for_http(request, "text-parent", enqueue)
            )
            assert await asyncio.to_thread(enqueue_started.wait, 1)
            # This scheduling point cannot run if a queue.put blocks the ASGI loop.
            await asyncio.wait_for(asyncio.sleep(0), timeout=0.2)
            request.is_disconnected.return_value = True
            with pytest.raises(_ServiceException) as error:
                await asyncio.wait_for(waiter, timeout=2)
            assert error.value.status_code == 499
        assert retired.is_set()

    asyncio.run(run())


def test_text_cancellation_marker_survives_until_queued_submission_starts():
    async def run():
        server = _server_harness()
        executor_slot = threading.Event()
        cancellation_marked = threading.Event()
        submitted_under_marker = []

        def enqueue():
            submitted_under_marker.append(cancellation_marked.is_set())

        def abort(request_id, *, submission_done, **kwargs):
            cancellation_marked.set()
            executor_slot.set()
            assert submission_done.wait(1)

        server._stream_handler.abort_request.side_effect = abort
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
        with ThreadPoolExecutor(max_workers=1) as server._async_executor:
            occupied = server._async_executor.submit(executor_slot.wait, 2)
            with pytest.raises(_ServiceException):
                await asyncio.wait_for(
                    server._submit_text_request_for_http(request, "late-text-parent", enqueue),
                    timeout=2,
                )
            occupied.result(timeout=1)
        assert submitted_under_marker == [True]

    asyncio.run(run())


def test_sse_send_failure_before_first_yield_still_finalizes():
    async def run():
        started = False

        async def body():
            nonlocal started
            started = True
            yield "not reached"

        async def send(message):
            raise OSError("connection closed")

        async def receive():
            await asyncio.Future()

        finalized = AsyncMock()
        response = _HELPER.RequestOwnedEventSourceResponse(body(), finalize=finalized)
        with pytest.raises(OSError, match="connection closed"):
            await response({"type": "http"}, receive, send)
        assert not started
        finalized.assert_awaited_once()

    asyncio.run(run())


def test_sse_abort_failure_preserves_the_original_send_error():
    async def run():
        server = _server_harness()
        server._stream_handler.abort_request.side_effect = TimeoutError("abort failed")

        async def body():
            yield "not reached"

        async def send(message):
            raise OSError("original send failure")

        async def receive():
            await asyncio.Future()

        response = _HELPER.RequestOwnedEventSourceResponse(
            body(),
            finalize=functools.partial(
                server._finalize_owned_http_request, "request", preserve_error=True
            ),
        )
        with pytest.raises(OSError, match="original send failure"):
            await response({"type": "http"}, receive, send)
        server._cleanup_temporary_chat_assets.assert_not_awaited()

    asyncio.run(run())


def test_sse_disconnect_has_one_receive_consumer_and_finalizes_once():
    async def run():
        receive_calls = []

        async def receive():
            receive_calls.append(asyncio.current_task())
            return {"type": "http.disconnect"}

        async def body():
            await asyncio.Future()
            yield "never"

        finalized = AsyncMock()
        response = _HELPER.RequestOwnedEventSourceResponse(body(), finalize=finalized)
        await response({"type": "http"}, receive, AsyncMock())
        assert len(set(receive_calls)) == 1
        finalized.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
def test_tcp_disconnect_aborts_owned_work_and_next_control_succeeds(stream):
    """Real ASGI receive/disconnect behavior, no GPU or model timing dependency."""
    backend_started, backend_drained = threading.Event(), threading.Event()
    server = _server_harness()

    def abort(*args, **kwargs):
        backend_drained.set()

    server._stream_handler.abort_request.side_effect = abort
    server._stream_handler.request_is_done.side_effect = lambda rid: backend_drained.is_set()
    server._stream_handler.request_is_drained.side_effect = lambda rid: backend_drained.is_set()
    app = FastAPI(lifespan=server._request_cleanup_lifespan)

    @app.post("/completion")
    async def completion(request: Request):
        body = await request.json()
        if body.get("control"):
            return {"ok": not backend_started.is_set() or backend_drained.is_set()}
        backend_started.set()

        async def tokens():
            while not backend_drained.is_set():
                await asyncio.sleep(0.01)
            yield "done"

        if body["stream"]:
            return _HELPER.RequestOwnedEventSourceResponse(
                tokens(),
                finalize=functools.partial(server._finalize_owned_http_request, "request"),
                ping=1,
            )
        try:
            await server._wait_for_http_request(request, "request")
            return {"done": True}
        except _ServiceException:
            return {"disconnected": True}
        finally:
            await server._finalize_owned_http_request("request")

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    application = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=application.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not application.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert application.started
        origin = f"http://127.0.0.1:{port}"
        with httpx.Client(timeout=2) as client:
            assert client.post(f"{origin}/completion", json={"control": True}).json()["ok"]
            payload = json.dumps({"stream": stream}).encode()
            connection = socket.create_connection(("127.0.0.1", port), timeout=2)
            connection.sendall(
                b"POST /completion HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: application/json\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\n\r\n"
                + payload
            )
            assert backend_started.wait(2)
            connection.close()
            assert backend_drained.wait(2), "disconnect did not reach backend cancellation"
            assert client.post(f"{origin}/completion", json={"control": True}).json()["ok"]
        server._stream_handler.abort_request.assert_called_once()
    finally:
        application.should_exit = True
        thread.join(timeout=5)
        listener.close()
    assert not thread.is_alive()


def test_http_lifecycle_helper_is_in_runtime_manifests():
    assert (
        "server/request_lifecycle.py"
        in (_ROOT / "docker/rtvi_vlm/package_file_list.txt").read_text()
    )
    assert (
        "src/server/request_lifecycle.py"
        in (_ROOT / "scripts/rt_vlm_release_file_list.txt").read_text()
    )
