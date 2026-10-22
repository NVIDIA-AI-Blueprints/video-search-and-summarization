# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Replay the frozen convergence vectors against the live implementation.

The vectors were generated from the pre-refactor code by
``tests/vectors/generate.py`` and must keep passing unchanged.

A failure here means convergence behaviour changed, so fix the code. Do not
regenerate the vectors to make the test green; that erases the proof. Regenerate
only for a deliberate, separately reviewed behaviour change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.api.schemas import RoutingEntry
from tests.vectors.generate import (
    CONVERGENCE_PATH,
    SCHEMA_VERSION,
    build_state,
    canonical_key,
    evaluate,
    vector_id,
)

_MAX_REPORTED = 10


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():  # pragma: no cover - the files are committed
        pytest.fail(
            f"missing golden vectors: {path}\n"
            "Generate them from the PRE-refactor implementation:\n"
            "    uv run python tests/vectors/generate.py"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION, (
        f"{path.name} schema_version={payload['schema_version']} but this test "
        f"understands {SCHEMA_VERSION}; regenerate or update the reader."
    )
    return payload


CONVERGENCE = _load(CONVERGENCE_PATH)


def _describe(vector: dict[str, Any]) -> str:
    """A one-glance rendering of the vector that diverged."""
    inputs = vector["inputs"]
    state = inputs["state"]
    routing = (
        ", ".join(f"{e['version']}:{e['role']}@{e['weight']}" for e in state["routing"]) or "-"
    )
    return (
        f"vector id={vector['id']} index={vector['index']}\n"
        f"  control={inputs['control']} candidate={inputs['candidate']} "
        f"from_version={inputs['from_version']} canary_weight={inputs['canary_weight']}\n"
        f"  default_version={state['default_version']} routing=[{routing}]\n"
        f"  routing_available={state['routing_available']} "
        f"loaded_available={state['loaded_available']}\n"
        f"  ready_versions={state['ready_versions']} "
        f"resident_versions={state['resident_versions']}\n"
        f"  ready={state['ready']} loaded_versions={state['loaded_versions']} "
        f"serving_versions={state['serving_versions']}\n"
        f"  raw inputs: {canonical_key(inputs)}"
    )


def _fail(header: str, divergences: list[str], total: int) -> None:
    shown = divergences[:_MAX_REPORTED]
    more = len(divergences) - len(shown)
    tail = f"\n... and {more} more divergent case(s)." if more > 0 else ""
    pytest.fail(
        f"{header}\n"
        f"{len(divergences)} of {total} frozen case(s) diverged.\n\n"
        + "\n\n".join(shown)
        + tail
        + "\n\nThe refactor changed convergence behaviour. Fix the code, do not "
        "regenerate the vectors.",
        pytrace=False,
    )


def test_vector_file_is_wellformed():
    """The corpus itself must be intact: sized, uniquely keyed, self-consistent."""
    vectors = CONVERGENCE["vectors"]
    assert vectors, "convergence.json carries no vectors"
    assert CONVERGENCE["count"] == len(vectors)
    assert len({v["id"] for v in vectors}) == len(vectors), "duplicate vector ids"
    assert [v["index"] for v in vectors] == list(range(len(vectors))), "index is not dense/ordered"
    # A vector id is the hash of its inputs, so check the binding still holds.
    mismatched = [v["id"] for v in vectors if vector_id(v["inputs"]) != v["id"]]
    assert not mismatched, f"vector id does not match its inputs: {mismatched[:5]}"
    # Every control is exercised.
    assert {v["inputs"]["control"] for v in vectors} == {
        "shadow",
        "canary",
        "full_switch",
        "unload",
    }
    # Both answers must occur for each frozen output, or a predicate stubbed to
    # always return False would pass.
    for key in ("reflected", "servable", "converged"):
        answers = {v["expected"][key] for v in vectors}
        assert answers == {True, False}, f"corpus never observes both answers for {key!r}"


def test_serving_states_are_constructible():
    """Every frozen state still maps onto the live ServingState/RoutingEntry."""
    for vector in CONVERGENCE["vectors"]:
        build_state(vector["inputs"]["state"])  # raises on a schema drift


def test_convergence_predicate_matches_frozen_vectors():
    """_control_reflected / _candidate_servable answer exactly as they did."""
    divergences: list[str] = []
    for vector in CONVERGENCE["vectors"]:
        try:
            actual = evaluate(vector["inputs"])
        except Exception as exc:  # a refactor that raises is also a divergence
            divergences.append(f"{_describe(vector)}\n  RAISED  {type(exc).__name__}: {exc}")
            continue
        expected = vector["expected"]
        if actual != expected:
            deltas = ", ".join(
                f"{k}: expected={expected[k]} actual={actual[k]}"
                for k in sorted(expected)
                if expected[k] != actual.get(k)
            )
            divergences.append(f"{_describe(vector)}\n  DIVERGED  {deltas}")

    if divergences:
        _fail(
            "Convergence predicate diverged from tests/vectors/convergence.json.",
            divergences,
            len(CONVERGENCE["vectors"]),
        )


def test_evaluate_never_disagrees_with_the_raw_predicate():
    """``_evaluate`` may name a reason; it may never change the answer.

    It re-walks the predicate's conditions to attach a reason code, so that
    second walk is checked against every frozen vector.
    """
    from src.core.manager import _evaluate

    divergent: list[tuple] = []
    for vector in CONVERGENCE["vectors"]:
        inputs = vector["inputs"]
        converged, reason = _evaluate(
            build_state(inputs["state"]),
            inputs["control"],
            inputs["candidate"],
            from_version=inputs["from_version"],
            canary_weight=inputs["canary_weight"],
        )
        if converged != vector["expected"]["converged"]:
            divergent.append((vector["id"], vector["expected"]["converged"], converged))
        if converged and reason is not None:
            divergent.append((vector["id"], "no reason expected", reason.code))
        if not converged and reason is None:
            divergent.append((vector["id"], "a reason was expected", None))

    assert not divergent, (
        f"{len(divergent)} vector(s) where _evaluate disagrees with the frozen "
        f"predicate or mislabels a reason: {divergent[:5]}"
    )


def test_every_reason_code_is_reachable_from_the_corpus():
    """A reason code no vector can produce is a branch nobody has exercised."""
    from src.core.manager import _evaluate

    seen = set()
    for vector in CONVERGENCE["vectors"]:
        inputs = vector["inputs"]
        converged, reason = _evaluate(
            build_state(inputs["state"]),
            inputs["control"],
            inputs["candidate"],
            from_version=inputs["from_version"],
            canary_weight=inputs["canary_weight"],
        )
        if not converged:
            seen.add(reason.code)

    expected = {
        "routing_unreadable",
        "loaded_unreadable",
        "primary_moved",
        "default_version_mismatch",
        "routing_not_exclusive",
        "shadow_absent",
        "canary_absent",
        "canary_weight_mismatch",
        "candidate_not_ready",
        "still_resident",
    }
    assert seen == expected, f"unreachable reason codes: {sorted(expected - seen)}"


@pytest.mark.parametrize("control", ["shadow", "canary"])
@pytest.mark.parametrize("observed_default", [None, "", "6"])
@pytest.mark.parametrize("expectation_from", [None, ""])
def test_an_expectation_naming_no_prior_default_is_never_terminal(
    control, observed_default, expectation_from
):
    """A primary that was never expected to hold cannot have MOVED.

    ``primary_moved`` is terminal, and DFW stops on terminal. So claiming it for
    an expectation that named no ``from_version`` fails the promotion on its
    opening poll at ~0s -- the precise failure the stateless contract exists to
    remove, and the one place it had survived.

    The trigger is a falsy ``from_version``, NOT (as first reported) an empty
    ``default_version``: ``expected_primary = from_version or candidate``
    collapsed to the candidate, so the comparison failed against ANY observed
    default, live or absent. Hence the third ``observed_default`` case here --
    a backend with a perfectly good default, which the day-0 framing misses.

    The ``full_switch`` branch has always guarded this with
    ``from_version is not None``; this is the same guard on the sibling branch.
    """
    from src.adapters.base import ServingState
    from src.core.manager import _evaluate

    state = ServingState(
        default_version=observed_default,
        routing=[],
        serving_versions=set(),
        loaded_versions=["7"],
        ready_versions={"7"},
        ready=True,
    )
    converged, reason = _evaluate(
        state,
        control,
        "7",
        from_version=expectation_from,
        canary_weight=0.1 if control == "canary" else None,
    )

    assert converged is False, "nothing has landed yet; this must not read as converged"
    assert reason is not None
    assert reason.terminal is False, (
        f"{control} with from_version={expectation_from!r} against "
        f"default_version={observed_default!r} was reported terminal; DFW stops on "
        f"terminal, so this fails the promotion at ~0s. Reason was {reason.code}."
    )


@pytest.mark.parametrize("control", ["shadow", "canary"])
def test_a_primary_that_really_moved_is_still_terminal(control):
    """The guard must not cost the signal it guards.

    With a real ``from_version``, an observed default that is neither it nor the
    candidate means another apply landed on top of this one. Waiting cannot
    recover that, so it stays terminal -- otherwise DFW polls out its whole
    budget on an expectation that is genuinely dead.
    """
    from src.adapters.base import ServingState
    from src.core.manager import _evaluate

    state = ServingState(
        default_version="9",
        routing=[],
        serving_versions=set(),
        loaded_versions=["7"],
        ready_versions={"7"},
        ready=True,
    )
    converged, reason = _evaluate(
        state,
        control,
        "7",
        from_version="6",
        canary_weight=0.1 if control == "canary" else None,
    )

    assert converged is False
    assert reason.code == "primary_moved"
    assert reason.terminal is True


@pytest.mark.parametrize("control", ["shadow", "canary"])
@pytest.mark.parametrize("observed_default", [None, ""])
def test_a_routing_view_reporting_no_primary_at_all_is_never_terminal(control, observed_default):
    """No primary observed is an unsettled READ, not a primary that moved.

    This one has nothing to do with day 0: an ordinary v6 -> v7 shadow whose
    routing view momentarily reports no primary would be declared stale, with a
    perfectly good ``from_version`` in hand. The unified state layer produces
    exactly that shape -- a policy carrying only a shadow slot is legal there and
    yields ``default_version=None``.

    It is the more dangerous half of the two. Once shadow has served,
    ``has_served()`` is true, so a terminal verdict here does not merely fail the
    promotion -- it enqueues an auto-rollback, off a transient read.
    """
    from src.adapters.base import ServingState
    from src.core.manager import _evaluate

    state = ServingState(
        default_version=observed_default,
        routing=[],
        serving_versions=set(),
        loaded_versions=["7"],
        ready_versions={"7"},
        ready=True,
    )
    converged, reason = _evaluate(
        state,
        control,
        "7",
        from_version="6",
        canary_weight=0.1 if control == "canary" else None,
    )

    assert converged is False
    assert reason.terminal is False, (
        f"{control} with a real from_version='6' against default_version="
        f"{observed_default!r} was reported terminal; that is a transient read, "
        f"and past the shadow gate it would trigger an auto-rollback. "
        f"Reason was {reason.code}."
    )


@pytest.mark.parametrize("observed_default", [None, ""])
def test_full_switch_reporting_no_primary_at_all_is_never_terminal(observed_default):
    """The sibling of the shadow/canary guard, on the branch that matters most.

    ``full_switch`` guarded the missing ``from_version`` but not the missing
    observed default, so the same unsettled read it tolerates elsewhere was
    still fatal here. And every full_switch expectation carries a real
    ``from_version`` -- it is the baseline at the full gate -- so the existing
    guard never short-circuits and this always fired.

    Worst possible gate for it: ``has_served()`` is true by then, so a false
    terminal enqueues an auto-rollback rather than merely failing, and on the
    ROLLBACK full_switch it marks the record failed while the regressed
    candidate may still be serving.
    """
    from src.adapters.base import ServingState
    from src.core.manager import _evaluate

    state = ServingState(
        default_version=observed_default,
        routing=[],
        serving_versions=set(),
        loaded_versions=["7"],
        ready_versions={"7"},
        ready=True,
    )
    converged, reason = _evaluate(state, "full_switch", "7", from_version="6")

    assert converged is False
    assert reason.terminal is False, (
        f"full_switch with from_version='6' against default_version="
        f"{observed_default!r} was reported terminal. Reason was {reason.code}."
    )


def test_full_switch_displaced_by_a_real_third_version_is_still_terminal():
    """The guard must not cost the signal it guards, on this branch either."""
    from src.adapters.base import ServingState
    from src.core.manager import _evaluate

    state = ServingState(
        default_version="9",
        routing=[RoutingEntry(version="9", role="default", weight=1.0)],
        serving_versions=set(),
        loaded_versions=["7", "9"],
        ready_versions={"7", "9"},
        ready=True,
    )
    converged, reason = _evaluate(state, "full_switch", "7", from_version="6")

    assert converged is False
    assert reason.code == "primary_moved"
    assert reason.terminal is True


def test_terminality_always_requires_a_real_displacement():
    """The invariant, swept over every frozen state and a WIDENED from_version.

    This is the class-level closure, and it exists because of a hole the frozen
    corpus cannot cover. ``_canonicalize`` zeroes ``from_version`` for anything
    that is not shadow/canary::

        # ``from_version`` is read only by shadow/canary (the expected primary).
        if control not in ("shadow", "canary"):
            v["from_version"] = None

    That is true of ``_control_reflected``, which the corpus freezes. It is NOT
    true of ``_evaluate``, which reads ``from_version`` on the full_switch branch
    too -- so all full_switch vectors carry ``from_version=None``, the guard
    short-circuits, and that branch is unreachable in replay. The safety net has
    a hole exactly where the newer code reads an input the frozen functions did
    not.

    Regenerating the corpus with a wider domain would close it and destroy what
    the corpus is FOR: it was generated from the pre-refactor implementation, and
    re-emitting it from today's code turns a behaviour-preservation proof into a
    snapshot of current behaviour. So instead this reuses the frozen states and
    widens only the axis the canonicalizer collapsed.

    The invariant itself is the principle the two fixes share, stated once so it
    binds every branch -- including ones not written yet: terminality requires
    positive evidence that a different, REAL primary now holds. Two absences are
    not that.
    """
    from src.core.manager import _evaluate

    offenders: list[str] = []
    checked = 0
    for vector in CONVERGENCE["vectors"]:
        inputs = vector["inputs"]
        state = build_state(inputs["state"])
        for from_version in (None, "", "6", "9", inputs["from_version"]):
            checked += 1
            _, reason = _evaluate(
                state,
                inputs["control"],
                inputs["candidate"],
                from_version=from_version,
                canary_weight=inputs["canary_weight"],
            )
            if reason is None or not reason.terminal:
                continue
            observed = state.default_version
            if not from_version or not observed or observed == from_version:
                offenders.append(
                    f"{vector['id']} control={inputs['control']} "
                    f"candidate={inputs['candidate']!r} from={from_version!r} "
                    f"observed={observed!r} -> {reason.code} terminal=True"
                )

    assert not offenders, (
        f"{len(offenders)} of {checked} evaluations claimed terminality without a "
        f"real displacement (a truthy from_version AND a truthy, different observed "
        f"default). DFW STOPS on terminal.\n  " + "\n  ".join(offenders[:10])
    )
