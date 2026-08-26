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

"""Structural guards on the sync/thread_bridge pipeline's span wiring.

The async mirror of these lives in ``test_pipeline_span_wiring.py``. Both modes
have to be wired, and they are wired in different files by different edits, so
the guarantees are asserted twice rather than assumed to travel together.

Asserted over the AST because the defects are positional: a closer in the try
body rather than the ``finally`` closes the root before enrichment, and a
``success`` attribute set after the VLM call is unreachable exactly when that
call raises. Neither is visible to a behavioural test that does not happen to
drive the failing path.
"""

import ast
from pathlib import Path

import pytest

_ENTRYPOINT = Path(__file__).resolve().parents[3] / "enhance_alert_with_vlm.py"

PIPELINE_METHOD = "_process_single_message"


@pytest.fixture(scope="module")
def module_ast():
    return ast.parse(_ENTRYPOINT.read_text())


def _find(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {_ENTRYPOINT.name}")


def _outer_try(method):
    for stmt in method.body:
        if isinstance(stmt, ast.Try):
            return stmt
    raise AssertionError(f"{method.name} has no top-level try")


def test_sync_pipeline_opens_a_root_span_before_its_try(module_ast):
    method = _find(module_ast, PIPELINE_METHOD)
    outer = _outer_try(method)
    opened = [
        s for s in method.body
        if s.lineno < outer.lineno
        and any(
            isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "open_root_span"
            for n in ast.walk(s)
        )
    ]
    assert opened, "open_root_span must be called before the outer try"


def test_sync_root_span_call_passes_the_stage_timestamps(module_ast):
    """Without it the root starts when this worker reached the event, not when
    the event arrived — and its historical children fall outside it."""
    method = _find(module_ast, PIPELINE_METHOD)
    calls = [
        n for n in ast.walk(method)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "open_root_span"
    ]
    assert calls
    for call in calls:
        assert any(k.arg == "timestamps" for k in call.keywords)


def test_sync_finally_closes_and_detaches(module_ast):
    method = _find(module_ast, PIPELINE_METHOD)
    finally_body = ast.Module(body=_outer_try(method).finalbody, type_ignores=[])
    called = {
        n.func.attr for n in ast.walk(finally_body)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert {"mark_finally_reached", "should_close_from_finally", "close", "detach"} <= called


def test_sync_handler_re_raises_and_sets_the_failure_flag(module_ast):
    """This function is reached from async_dispatch_mixin's inline fallback,
    which calls it from inside an `except` handler — so sys.exc_info() here
    would report the caller's exception and stamp a clean success as a failure.
    """
    method = _find(module_ast, PIPELINE_METHOD)
    outer = _outer_try(method)
    assert outer.handlers, "no handler records that the exit was not clean"
    for handler in outer.handlers:
        assert any(
            isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(handler)
        ), "the outer handler must re-raise; it may observe, never swallow"

    finally_src = ast.unparse(ast.Module(body=outer.finalbody, type_ignores=[]))
    assert "exc_info" not in finally_src


def test_sync_root_is_closed_only_from_the_finally(module_ast):
    """The twin of the event_loop guard, and it has to stay the twin.

    This file's whole premise is that the two modes are wired by different
    edits, so the guarantees are asserted twice rather than assumed to travel
    together. The event_loop version of this test was widened -- receiver-blind,
    and walking the handlers as well as the body -- and this one was not, so for
    one revision the two mutations that fix caught here passed silently. That is
    the shape this file exists to prevent, reproduced inside the file itself.

    Sync is not the minority path: five of the ten shipped configs resolve to it,
    including the warehouse product profile.
    """
    method = _find(module_ast, PIPELINE_METHOD)
    outer = _outer_try(method)

    def closes_in(nodes):
        return [
            n.lineno for stmt in nodes for n in ast.walk(stmt)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "close"
        ]

    # Pinned to the handle: a receiver-blind positive half would assert only
    # that *something* is closed in the finally.
    assert [
        n.lineno for stmt in outer.finalbody for n in ast.walk(stmt)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "close"
        and getattr(n.func.value, "id", None) == "span_handle"
    ], "the finally must close the span handle"

    # Receiver-blind, and across handlers and orelse too. `_h = span_handle;
    # _h.close(...)` slipped through a name-pinned matcher, and a close in the
    # outer handler sets `_closed` *and* claims `_decorated`, so the finally's
    # close(failure_reason='uncaught_exception') becomes a no-op: the alert
    # loses its error_reason on the one path that has one.
    early = outer.body + outer.handlers + (outer.orelse or [])
    assert not closes_in(early), (
        f"close() found outside the finally at lines {closes_in(early)}. If that "
        "is the root span handle, the root would close before enrichment, or "
        "before the finally can stamp the failure reason. If it is some other "
        "object that legitimately needs closing here, this matcher is "
        "deliberately receiver-blind — an aliased handle slipped through when it "
        "was not — so give it its own name and narrow the match."
    )


def test_sync_vlm_span_is_live_per_attempt_with_success_preset(module_ast):
    """`latency['vlmRequest']` is overwritten each iteration, so a reconstruction
    reports only the last attempt and hides a slow-then-fast retry."""
    method = _find(module_ast, PIPELINE_METHOD)
    loops = [n for n in ast.walk(method) if isinstance(n, ast.For)]
    calls = [
        n for loop in loops for n in ast.walk(loop)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", None) == "live_span"
        and any(k.arg == "attempt" for k in n.keywords)
    ]
    assert calls, "the VLM attempt span is not inside the retry loop"
    for call in calls:
        success = next((k for k in call.keywords if k.arg == "success"), None)
        assert success is not None, "success must be set at creation, not after the call"
        assert isinstance(success.value, ast.Constant) and success.value.value is False


def test_early_return_recorder_calls_carry_the_span_handle(module_ast):
    method = _find(module_ast, "_prepare_message_context")
    calls = [
        n for n in ast.walk(method)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "record_event_complete"
    ]
    assert len(calls) >= 3
    for call in calls:
        assert any(k.arg == "span_handle" for k in call.keywords)


def test_the_deferred_sink_callback_can_close_the_span(module_ast):
    """The handoff the concurrency contract exists for.

    In async sink mode the recorder fires from the sink executor thread, after
    the pipeline's `finally` has already run. `mark_deferred()` is announced
    before `add_done_callback` on purpose: a resolved future runs the callback
    synchronously, so announcing afterwards would hand closure to a callback that
    had already declined it.
    """
    method = _find(module_ast, "_complete_event_after_publish")
    src = ast.unparse(method)
    assert "mark_finalized" in src
    assert "should_close_from_callback" in src
    assert "mark_deferred" in src

    deferred_at = next(
        n.lineno for n in ast.walk(method)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "mark_deferred"
    )
    attached_at = next(
        n.lineno for n in ast.walk(method)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "add_done_callback"
    )
    assert deferred_at < attached_at, (
        "mark_deferred() must precede add_done_callback(); a resolved future fires "
        "the callback synchronously"
    )


def test_ondemand_captures_the_context_at_schedule_time():
    """Captured in the route, not inside the task: by the time the task runs
    there is no current context to read."""
    routes = Path(__file__).resolve().parents[3] / "src/web/api/verification_routes.py"
    tree = ast.parse(routes.read_text())
    add_task = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "add_task"
    )
    passes_context = any(
        isinstance(a, ast.Call) and getattr(a.func, "attr", None) == "current_span_context"
        for a in add_task.args
    )
    assert passes_context, "add_task does not carry the request's span context"


def test_ondemand_task_opens_and_closes_a_linked_root():
    service = Path(__file__).resolve().parents[3] / "src/web/service/ondemand_verification_service.py"
    tree = ast.parse(service.read_text())
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "process_and_publish"
    )
    opens = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "open_root_span"
    ]
    assert opens, "the background task opens no root span"
    assert any(k.arg == "link_to" for k in opens[0].keywords), "the root is not linked to its request"

    outer = next(s for s in fn.body if isinstance(s, ast.Try))
    closed = {
        n.func.attr for n in ast.walk(ast.Module(body=outer.finalbody, type_ignores=[]))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert {"close", "detach"} <= closed, "the background root is not closed on its own thread"

    # REQ-019: the existing Prometheus gate is untouched, so the span must be
    # closed above it rather than inside it.
    gate = next(
        n for n in ast.walk(outer) if isinstance(n, ast.If) and "PROMETHEUS_ENABLED" in ast.dump(n.test)
    )
    close_at = next(
        n.lineno for n in ast.walk(ast.Module(body=outer.finalbody, type_ignores=[]))
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "close"
    )
    assert close_at < gate.lineno, "the span close sits inside the Prometheus gate"


def test_finalize_completes_the_handoff_in_a_finally(module_ast):
    """A raise in the recorder must not strand the span.

    concurrent.futures swallows and logs a callback exception, and the pipeline's
    `finally` has already deferred by the time this runs — so without the
    `finally` here, nobody closes.
    """
    method = _find(module_ast, "_complete_event_after_publish")
    finalize = next(
        n for n in ast.walk(method)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_finalize"
    )
    tries = [n for n in finalize.body if isinstance(n, ast.Try)]
    assert tries, "_finalize does not guard the recorder call"
    guarded = [
        t for t in tries
        if t.finalbody and any(
            isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "mark_finalized"
            for n in ast.walk(ast.Module(body=t.finalbody, type_ignores=[]))
        )
    ]
    assert guarded, "mark_finalized() is not in a finally; a recorder raise strands the span"
