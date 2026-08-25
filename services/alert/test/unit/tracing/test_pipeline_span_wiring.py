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

"""Structural guards on how tracing is wired into the event-loop pipeline.

These assert over the parsed AST rather than over behaviour, on purpose. The
defect they exist to prevent — the root span closing *before* post-publish
enrichment, so the enrichment spans are orphaned and the root's duration is
short — is a property of **where** statements sit relative to each other. It
survives every behavioural test that does not happen to drive enrichment, and it
is invisible to a text search, because each individual line is correct in
isolation. It took several review rounds to find by reading. It takes
milliseconds to find here.

If a refactor moves the close out of the outermost ``finally``, or moves
enrichment outside the ``try`` that ``finally`` belongs to, these fail.
"""

import ast
from pathlib import Path

import pytest

_MIXIN = Path(__file__).resolve().parents[3] / "src" / "handlers" / "event_loop_pipeline_mixin.py"

PIPELINE_METHOD = "_process_single_message_async"
CAPACITY_METHOD = "_capacity_slot"


@pytest.fixture(scope="module")
def module_ast():
    return ast.parse(_MIXIN.read_text())


def _find(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {_MIXIN.name}")


def _calls(node):
    """Every attribute-call name reachable under ``node``, e.g. 'span_handle.close'."""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            value = sub.func.value
            prefix = getattr(value, "id", None) or getattr(getattr(value, "attr", None), "__str__", lambda: None)()
            out.append(f"{prefix}.{sub.func.attr}" if prefix else sub.func.attr)
    return out


def _uses(node, name: str) -> bool:
    """True when ``node`` contains a call whose attribute is ``name``.

    A substring test against the qualified names — ``_calls`` returns entries
    like ``tracing_spans.live_span``, so a plain ``in`` against the list is a
    membership test and silently never matches.
    """
    return any(c.endswith("." + name) or c == name for c in _calls(node))


def _outer_try(method):
    """The outermost ``try`` that is a direct statement of the method body."""
    for stmt in method.body:
        if isinstance(stmt, ast.Try):
            return stmt
    raise AssertionError(f"{method.name} has no top-level try")


def test_pipeline_body_is_wrapped_in_try_finally(module_ast):
    method = _find(module_ast, PIPELINE_METHOD)
    outer = _outer_try(method)
    assert outer.finalbody, "the outermost try must have a finally — that is what guarantees closure"
    # A handler is allowed only if it re-raises. The outer try exists to guarantee
    # the finally, not to change pipeline behaviour; the one handler it carries
    # records that the exit was not clean and re-raises immediately. Swallowing
    # here would silently drop events.
    for handler in outer.handlers:
        assert any(
            isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(handler)
        ), "the outermost try's handler must re-raise; it may observe, never swallow"


def test_span_is_opened_before_the_try(module_ast):
    """Opened outside, so there is nothing to close if opening itself fails.

    ``open_root_span`` is contractually non-raising, which is what makes this
    placement safe.
    """
    method = _find(module_ast, PIPELINE_METHOD)
    outer = _outer_try(method)
    opened_before = [
        s for s in method.body
        if s.lineno < outer.lineno and "tracing_spans.open_root_span" in _calls(s)
    ]
    assert opened_before, "open_root_span must be called before the outer try"


def test_finally_closes_and_detaches_in_order(module_ast):
    method = _find(module_ast, PIPELINE_METHOD)
    calls = _calls(ast.Module(body=_outer_try(method).finalbody, type_ignores=[]))
    assert "span_handle.mark_finally_reached" in calls
    assert "span_handle.should_close_from_finally" in calls
    assert "span_handle.close" in calls
    assert "span_handle.detach" in calls
    # mark_finally_reached must precede the decision, or a callback firing in
    # between cannot tell the finally has already had its turn.
    assert calls.index("span_handle.mark_finally_reached") < calls.index(
        "span_handle.should_close_from_finally"
    )


def test_enrichment_runs_inside_the_guarded_try(module_ast):
    """The root must close *after* enrichment, not before it.

    Enrichment creates its own spans (a VLM capacity acquisition among them).
    Closing the root first orphans them and truncates the root's duration.
    """
    method = _find(module_ast, PIPELINE_METHOD)
    outer = _outer_try(method)
    body_lines = [
        n.lineno
        for stmt in outer.body
        for n in ast.walk(stmt)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_process_enrichment_event_loop"
    ]
    assert body_lines, "_process_enrichment_event_loop must be inside the outer try"
    assert max(body_lines) < outer.finalbody[0].lineno, "enrichment must precede the finally"


def test_the_root_is_closed_only_from_the_finally(module_ast):
    """No `close()` anywhere but the outer `finally` — the defect this file is for.

    The previous version of this test was vacuous: it split the source on the
    first `record_event_complete`, took the first line of the remainder — which
    is the single character `","` — and asserted `span_handle.close` was not in
    it. That is constant `True` for any code, and a mutation inserting
    `span_handle.close(...)` immediately before `_process_enrichment_event_loop`
    left the whole suite green.

    Asserting on *location* rather than on a text neighbourhood is what makes it
    real: a closer in the try body closes before enrichment, orphaning the
    enrichment spans and truncating the root's duration, no matter how correct
    each individual line looks.
    """
    method = _find(module_ast, PIPELINE_METHOD)
    outer = _outer_try(method)

    def closes_in(nodes):
        return [
            n.lineno
            for stmt in nodes
            for n in ast.walk(stmt)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "close"
            and getattr(n.func.value, "id", None) == "span_handle"
        ]

    assert closes_in(outer.finalbody), "the finally must close the span"
    assert not closes_in(outer.body), (
        f"span_handle.close() found in the try body at lines {closes_in(outer.body)} — "
        "the root would close before enrichment"
    )


def test_capacity_span_is_live_per_acquisition(module_ast):
    """One span per acquisition, wrapping the acquire itself.

    `latency['capacityWait'][service]` accumulates across acquisitions, so a
    span reconstructed from that scalar would merge two disjoint VST waits.
    """
    method = _find(module_ast, CAPACITY_METHOD)
    assert _uses(method, "live_span")
    with_stmts = [n for n in ast.walk(method) if isinstance(n, (ast.With, ast.AsyncWith))]
    wrapping = [
        w for w in with_stmts
        if any(_uses(item.context_expr, "live_span") for item in w.items)
        and any(
            isinstance(sub, ast.Await) and _uses(sub.value, "acquire")
            for sub in ast.walk(w)
        )
    ]
    assert wrapping, "the live capacity span must wrap the semaphore acquire"


def test_vlm_span_is_live_per_attempt(module_ast):
    """One span per retry attempt, inside the loop — not one per event.

    `latency['vlmRequest']` is overwritten each iteration, so a reconstruction
    reports only the final attempt and hides a slow-then-fast retry.
    """
    method = _find(module_ast, PIPELINE_METHOD)
    loops = [n for n in ast.walk(method) if isinstance(n, ast.For)]
    inside_a_loop = [
        loop for loop in loops
        if any(
            isinstance(sub, (ast.With, ast.AsyncWith))
            and any(_uses(item.context_expr, "live_span") for item in sub.items)
            for sub in ast.walk(loop)
        )
    ]
    assert inside_a_loop, "the VLM live span must sit inside the retry loop"


def test_vlm_span_sets_success_at_creation_not_after(module_ast):
    """`success` must be an argument to `live_span`, not a later `set_attribute`.

    REQ-003's named deliverable is a total VLM failure showing `success=false` on
    its last attempt. An attribute assigned after `_analyze_video_url_async`
    returns is unreachable exactly when that call raises — which is the only case
    the requirement is about — so the span recorded `success` on precisely the
    attempts that did not need it.
    """
    method = _find(module_ast, PIPELINE_METHOD)
    live_span_calls = [
        n for n in ast.walk(method)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "live_span"
        and any(k.arg == "attempt" for k in n.keywords)
    ]
    assert live_span_calls, "the VLM attempt span call was not found"
    for call in live_span_calls:
        success = next((k for k in call.keywords if k.arg == "success"), None)
        assert success is not None, (
            "live_span must receive `success` at creation so a raising attempt still carries it"
        )
        assert isinstance(success.value, ast.Constant) and success.value.value is False, (
            "the initial value must be False — it is overwritten on the return path"
        )


def test_the_finally_records_why_the_span_closed(module_ast):
    """`close()` must be told whether an exception was in flight.

    Asserting that `close` appears in the `finally` says nothing about what is
    passed to it: replacing the conditional with a bare `None` left every test
    green while `error_reason='uncaught_exception'` — the one classification the
    event-loop path does deliver — silently stopped being recorded.
    """
    method = _find(module_ast, PIPELINE_METHOD)
    finally_body = ast.Module(body=_outer_try(method).finalbody, type_ignores=[])

    close_calls = [
        n for n in ast.walk(finally_body)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "close"
    ]
    assert close_calls, "the finally does not close the span"
    for call in close_calls:
        reason = next((k for k in call.keywords if k.arg == "failure_reason"), None)
        assert reason is not None, "close() is called without a failure_reason"
        assert isinstance(reason.value, ast.IfExp), (
            "failure_reason must be conditional on whether an exception is in flight, "
            "not a constant"
        )

    # ...and the flag it reads must be set by a re-raising handler on the outer
    # try, not by sys.exc_info(). exc_info() reports the exception being handled
    # anywhere on the thread's stack: the sync mirror of this function is called
    # from inside an `except` handler, so it would report a failure for every
    # event on that path that succeeded.
    outer = _outer_try(method)
    flags = {
        t.id
        for h in outer.handlers
        for n in ast.walk(h)
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    assert flags, "no handler on the outer try records that the exit was not clean"
    read = {
        n.id for call in close_calls
        for k in call.keywords if k.arg == "failure_reason"
        for n in ast.walk(k.value) if isinstance(n, ast.Name)
    }
    assert flags & read, f"failure_reason reads {read}, none of which the handler sets ({flags})"
    assert not _uses(finally_body, "exc_info"), (
        "sys.exc_info() in the finally sees the caller's exception, not this frame's"
    )


def test_the_root_span_call_passes_the_stage_timestamps(module_ast):
    """Without this kwarg the root starts at function entry again.

    That is the whole production wiring for "the root covers the stages it
    contains": deleting it makes Kafka Consume Lag, Worker Queue Wait and
    Dispatch Wait render to the left of their own parent. The behavioural test
    calls `open_root_span` directly and never goes through this call site.
    """
    method = _find(module_ast, PIPELINE_METHOD)
    calls = [
        n for n in ast.walk(method)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "open_root_span"
    ]
    assert calls, "open_root_span is not called"
    for call in calls:
        assert any(k.arg == "timestamps" for k in call.keywords), (
            "open_root_span must receive timestamps= or the root starts at function entry"
        )


def test_early_return_recorder_calls_carry_the_span_handle(module_ast):
    """Without it, a malformed message or a missing prompt is indistinguishable
    from a clean skip in the trace — REQ-001's stated reason for the hook."""
    method = _find(module_ast, "_prepare_message_context_async")
    calls = [
        n for n in ast.walk(method)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "record_event_complete"
    ]
    assert len(calls) >= 3, f"expected the three early-return sites, found {len(calls)}"
    for call in calls:
        assert any(k.arg == "span_handle" for k in call.keywords), (
            f"record_event_complete at line {call.lineno} does not pass span_handle"
        )


def _forwarding_audit(path, callees):
    """Every call to one of ``callees`` that does not pass ``span_handle``.

    Asserting over *forwarding* rather than over one literal call site. The
    previous guards checked that a particular line carried the keyword, which is
    true of the site you just edited and says nothing about the seven you did
    not — and that is exactly how the async pipeline came to drop the handle on
    all three of its failure exits while the sync one passed it on all three.
    """
    tree = ast.parse(Path(path).read_text())
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            call = sub.value if isinstance(sub, ast.Await) else sub
            if not isinstance(call, ast.Call):
                continue
            name = getattr(call.func, "attr", None)
            # asyncio.to_thread(fn, ...) hides the callee in the first argument
            if name == "to_thread" and call.args:
                name = getattr(call.args[0], "attr", None)
            if name in callees and not any(k.arg == "span_handle" for k in call.keywords):
                missing.append(f"{name} at line {call.lineno}")
    return sorted(set(missing))


_COMPLETION_SITES = {
    "_handle_media_collection_failure",
    "_handle_url_validation_failure",
    "_handle_vlm_exception",
    "_handle_vlm_exception_async",
    "_publish_outcome_and_complete",
    "_publish_outcome_and_complete_async",
    "_publish_error_and_complete_async",
    "_complete_event_after_publish",
}


def test_no_completion_site_drops_the_span_handle_async():
    """`event_loop` is the shipped pipeline_mode.

    With the handle dropped, `record_event_complete` never decorates, so
    `error_reason` never lands on the root for VST media-collection failures,
    URL-validation failures or any VLM exception — the failures an operator
    opens a trace to diagnose.
    """
    missing = _forwarding_audit(_MIXIN, _COMPLETION_SITES)
    assert not missing, f"span_handle dropped at: {missing}"


def test_no_completion_site_drops_the_span_handle_sync():
    entrypoint = Path(__file__).resolve().parents[3] / "enhance_alert_with_vlm.py"
    missing = _forwarding_audit(entrypoint, _COMPLETION_SITES)
    assert not missing, f"span_handle dropped at: {missing}"


def test_every_declared_span_handle_parameter_is_used():
    """A parameter accepted and never read is a silent drop.

    `_publish_error_and_complete_async` declared `span_handle` and referenced it
    nowhere, so the one async site that *did* forward handed it into a
    black hole.
    """
    orphans = []
    for path in (_MIXIN, Path(__file__).resolve().parents[3] / "enhance_alert_with_vlm.py"):
        tree = ast.parse(Path(path).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            declares = any(
                a.arg == "span_handle" for a in node.args.args + node.args.kwonlyargs
            )
            if not declares:
                continue
            reads = any(
                isinstance(n, ast.Name) and n.id == "span_handle" and isinstance(n.ctx, ast.Load)
                for n in ast.walk(node)
            )
            if not reads:
                orphans.append(f"{Path(path).name}:{node.name}")
    assert not orphans, f"span_handle accepted and never used in: {orphans}"


def test_every_recorder_call_carries_the_pipeline_mode():
    """Nothing writes `pipelineMode` into `latency`, so it has to be passed."""
    missing = []
    for path in (_MIXIN, Path(__file__).resolve().parents[3] / "enhance_alert_with_vlm.py"):
        tree = ast.parse(Path(path).read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "record_event_complete":
                if not any(k.arg == "pipeline_mode" for k in n.keywords):
                    missing.append(f"{Path(path).name}:{n.lineno}")
    assert not missing, f"record_event_complete without pipeline_mode at: {missing}"


# Calls permitted before the guard opens. Everything the root span needs comes
# from function parameters, so the prefix only has to produce them -- and each of
# these is non-raising for any input this function can be handed. `open_root_span`
# is on the list by contract, which `test_open_root_span_never_raises` enforces.
_NON_RAISING_IN_PREFIX = {
    "time",             # time.time()
    "now", "isoformat",  # datetime.now(timezone.utc).isoformat()
    "getattr",           # always called with a default here
    "open_root_span",
}


def _unguarded_prefix(path, method_name):
    """Calls before the guarded try that are not on the allowlist.

    An allowlist rather than a denylist. The previous version rejected anything
    that dereferenced `message`, which caught the defect that existed and would
    have said nothing about the next raisable statement someone adds here. If a
    call belongs in the prefix, it has to be argued for by name.
    """
    tree = ast.parse(Path(path).read_text())
    method = _find(tree, method_name)
    outer = _outer_try(method)
    offenders = []
    for stmt in method.body:
        if stmt.lineno >= outer.lineno:
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # docstring
        for n in ast.walk(stmt):
            if not isinstance(n, ast.Call):
                continue
            name = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if name not in _NON_RAISING_IN_PREFIX:
                offenders.append(f"line {n.lineno}: {name}()")
        if any(isinstance(n, ast.Subscript) for n in ast.walk(stmt)):
            offenders.append(f"line {stmt.lineno}: subscript")
    return sorted(set(offenders))


def test_nothing_that_can_raise_sits_outside_the_guard_async(module_ast):
    """REQ-001 guarantees the span covers the whole function body."""
    assert not _unguarded_prefix(_MIXIN, PIPELINE_METHOD)


def test_nothing_that_can_raise_sits_outside_the_guard_sync():
    entrypoint = Path(__file__).resolve().parents[3] / "enhance_alert_with_vlm.py"
    assert not _unguarded_prefix(entrypoint, "_process_single_message")


@pytest.mark.parametrize(
    "path,method",
    [(_MIXIN, PIPELINE_METHOD),
     (Path(__file__).resolve().parents[3] / "enhance_alert_with_vlm.py", "_process_single_message")],
    ids=["async", "sync"],
)
def test_the_uncaught_exception_reason_keeps_its_name(path, method):
    """`error_reason` values are what an operator filters on in Jaeger.

    Renaming this one was invisible to the suite until now — nothing asserted the
    string, only that *some* conditional was passed. It also has to match between
    the two pipelines, and they are edited separately.
    """
    tree = ast.parse(Path(path).read_text())
    fn = _find(tree, method)
    finally_body = ast.Module(body=_outer_try(fn).finalbody, type_ignores=[])
    values = {
        n.value for n in ast.walk(finally_body)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert "uncaught_exception" in values, f"failure reason values in the finally: {values}"
    # Snake_case, like malformed_message / no_prompt / url_validation / vlm_timeout.
    assert not any(isinstance(v, str) and "-" in v for v in values)
