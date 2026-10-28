# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Scenario contracts for count inventories and the bounded refinement."""

from __future__ import annotations

import copy
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("evidence_ledger_scenarios", SCRIPTS / "evidence_ledger.py")
assert SPEC is not None and SPEC.loader is not None
L = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(L)
FIXTURES = Path(__file__).resolve().parent / "fixtures"

START = "2026-09-21T20:00:00Z"
END = "2026-09-21T20:02:00Z"
SCOPE = {"type": "sensor", "sensor_id": "sensor-1", "start": START, "end": END}
CLOCK = datetime(2026, 9, 21, 20, 30, tzinfo=timezone.utc)
DEADLINE = "2026-09-21T21:00:00Z"
Error = L.LedgerValidationError


@pytest.fixture(autouse=True)
def _no_runtime_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(L, "POLICY_PATH", tmp_path / "missing-policy.json")


def at(seconds: float) -> str:
    moment = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def scope(start: float, end: float) -> dict:
    return {"type": "sensor", "sensor_id": "sensor-1", "start": at(start), "end": at(end)}


def count_claim(evidence_type: str = "count") -> dict:
    return {
        "claim_id": "claim-events",
        "requirement": "Count the distinct qualifying events in the asked interval.",
        "evidence_type": evidence_type,
        "coverage_requirement": "whole_video",
        "support_test": "Each qualifying event is visibly completed.",
        "falsification_test": "A visible event does not meet the definition.",
    }


def new_ledger(evidence_type: str = "count") -> dict:
    return L.initialize_ledger(
        {
            "plan_version": "2.0",
            "mode": "initial",
            "question_id": "question-1",
            "question_text": "How many qualifying events happen?",
            "asset_id": "asset-1",
            "claims": [count_claim(evidence_type)],
        }
    )


def obs(text: str, start: float = 0, end: float = 120, job: str = "vlm-1") -> dict:
    value = {
        "observation_id": "obs-" + "0" * 24,
        "claim_id": "claim-events",
        "relation": "supports",
        "text": text,
        "source": {"type": "vlm", "job_id": job, "sensor_id": "sensor-1", "start": at(start), "end": at(end)},
    }
    value["observation_id"] = L.observation_id(value)
    return value


def candidate(cid: str, status: str = "observed", cites: list[dict] = (), **extra) -> dict:
    value = {
        "candidate_id": cid,
        "status": status,
        "counted": status in ("observed", "inferred"),
        "description": f"Event {cid}.",
        "justification": "See the cited caption.",
    }
    if cites:
        value["observation_ids"] = [item["observation_id"] for item in cites]
    value.update(extra)
    return value


def gap(gid: str, kind: str = "obscured_interval", start: float = 40, end: float = 70, affects: bool = True) -> dict:
    return {
        "gap_id": gid,
        "kind": kind,
        "description": f"{kind} {gid} could hide or cut off a qualifying event.",
        "affects_answer": affects,
        "start": at(start),
        "end": at(end),
    }


def inventory(candidates=(), open_gaps=(), resolved=()) -> dict:
    return {"candidates": list(candidates), "open_gaps": list(open_gaps), "resolved_gaps": list(resolved)}


def run_round(ledger, observations=(), count_state=None, *, coverage="sufficient", calls=1, task_scope=None, refinement=None, now=CLOCK, deadline=DEADLINE):
    task = L.create_inspection_tasks(
        ledger, {"claim-events": task_scope or copy.deepcopy(SCOPE)}, None, refinement, now=now, deadline=deadline
    )[0]
    result = {
        "task_id": task["task_id"],
        "base_revision": task["base_revision"],
        "observations": list(observations),
        "coverage": coverage,
        "gap": None,
        "vlm_calls_used": calls,
        "error": None,
    }
    if count_state is not None:
        result["count_state"] = count_state
    return task, L.merge_round_results(ledger, [task], [result])


def refinement(strategy="narrower_denser", fps=2, gap_ids=("gap-occluded",), prior=(0, 120, 0.5)) -> dict:
    return {
        "claim_id": "claim-events",
        "gap_ids": list(gap_ids),
        "missing_evidence": "Whether a qualifying event completes inside the gap.",
        "rationale": "The prior skim sampled only two frames across this span.",
        "strategy": strategy,
        "fps": fps,
        "prior_request": {"start": at(prior[0]), "end": at(prior[1]), "fps": prior[2]},
    }


def two_seen_one_hidden() -> tuple[dict, list[dict]]:
    seen = [obs("[10:12] First event completes."), obs("[90:92] Second event completes.")]
    _, ledger = run_round(
        new_ledger(),
        seen,
        inventory([candidate("evt-first", cites=seen[:1]), candidate("evt-second", cites=seen[1:])], [gap("gap-occluded")]),
    )
    return ledger, seen


def assessment(ledger: dict) -> dict:
    return L.assess_sufficiency(ledger)["claims"][0]["count_assessment"]


def test_01_two_observed_events_and_a_relevant_hidden_interval_are_only_a_lower_bound() -> None:
    ledger = two_seen_one_hidden()[0]
    state = ledger["claims"][0]
    assert ledger["status"] == "in_progress"
    assert state["coverage"] == "partial"
    assert state["coverage_cap"]["gap_ids"] == ["gap-occluded"]
    summary = assessment(ledger)
    assert summary["count_kind"] == "lower_bound"
    assert summary["counted"] == 2
    assert summary["answer_affecting_gap_ids"] == ["gap-occluded"]
    assert L.assess_sufficiency(ledger)["sufficient"] is False


def test_02_an_occlusion_that_cannot_hide_an_event_does_not_block_resolution() -> None:
    seen = [obs("[10:12] First event completes."), obs("[90:92] Second event completes.")]
    _, ledger = run_round(
        new_ledger(),
        seen,
        inventory(
            [candidate("evt-first", cites=seen[:1]), candidate("evt-second", cites=seen[1:])],
            [gap("gap-passerby", affects=False)],
        ),
    )
    assert ledger["status"] == "answered"
    assert assessment(ledger)["count_kind"] == "exact"
    assert assessment(ledger)["non_blocking_gap_ids"] == ["gap-passerby"]


def test_03_complete_count_evidence_resolves_normally(tmp_path: Path) -> None:
    seen = [obs(f"[{t}:{t + 2}] Event completes.") for t in (10, 50, 90)]
    _, ledger = run_round(
        new_ledger(), seen, inventory([candidate(f"evt-{n}", cites=[item]) for n, item in zip("abc", seen)])
    )
    assert ledger["status"] == "answered" and ledger["stop_reason"] == "resolved"
    final = L.final_result(ledger, str(tmp_path), "Three", evidence_ids=[item["observation_id"] for item in seen])
    assert final["decision_source"] == "introspection"
    assert final["count_assessment"][0]["count_kind"] == "exact"
    assert final["count_assessment"][0]["counted"] == 3


def test_04_duplicate_evidence_does_not_add_events() -> None:
    first = obs("[10:12] Event completes.", 0, 60, job="vlm-1")
    overlap = obs("[0:2] Event completes.", 10, 70, job="vlm-2")
    _, ledger = run_round(
        new_ledger(),
        [first, overlap],
        inventory(
            [
                candidate("evt-a", cites=[first]),
                candidate("evt-a-again", "duplicate", cites=[overlap], counted=False, duplicate_of="evt-a"),
            ]
        ),
    )
    summary = assessment(ledger)
    assert summary["counted"] == 1
    assert summary["duplicate_candidate_ids"] == ["evt-a-again"]
    with pytest.raises(Error, match="cannot be counted"):
        L.validate_inspection_result(
            {
                "task_id": "inspect-claim-events-r1",
                "base_revision": 0,
                "observations": [first, overlap],
                "coverage": "partial",
                "gap": None,
                "vlm_calls_used": 1,
                "error": None,
                "count_state": inventory(
                    [candidate("evt-a", cites=[first]), candidate("evt-b", "duplicate", cites=[overlap], counted=True, duplicate_of="evt-a")]
                ),
            }
        )


def test_04b_a_rereported_candidate_replaces_rather_than_adds() -> None:
    ledger, seen = two_seen_one_hidden()
    _, merged = run_round(
        ledger,
        [obs("[0:3] First event, seen again.", 0, 30, job="vlm-9")],
        inventory([candidate("evt-first", cites=seen[:1])]),
        coverage="partial",
    )
    assert assessment(merged)["counted"] == 2


def test_05_temporal_boundary_uncertainty_stays_visible(tmp_path: Path) -> None:
    seen = [obs("[10:12] Event completes.")]
    _, ledger = run_round(
        new_ledger(),
        seen,
        inventory([candidate("evt-a", cites=seen)], [gap("gap-edge", "temporal_boundary", 115, 120)]),
    )
    assert assessment(ledger)["count_kind"] == "lower_bound"
    _, stalled = run_round(ledger, coverage="partial")
    assert stalled["no_progress_continuation"]["gap_ids"] == ["gap-edge"]
    declined = L.prepare_for_final_result(stalled)
    assert declined["status"] == "unresolved" and declined["stop_reason"] == "no_progress"
    final = L.final_result(stalled, str(tmp_path), "One", evidence_ids=[seen[0]["observation_id"]])
    assert final["revision"] == declined["revision"]
    assert final["count_assessment"][0]["count_kind"] == "lower_bound"
    assert any(item["gap"].startswith("gap-edge:") for item in final["unresolved_gaps"])


def test_06_a_justified_affordable_strategy_change_is_allowed() -> None:
    ledger = two_seen_one_hidden()[0]
    task, merged = run_round(
        ledger,
        [obs("[3:5] An event completes behind the rack.", 35, 75, job="vlm-5")],
        coverage="partial",
        task_scope=scope(35, 75),
        refinement=refinement(),
    )
    record = task["refinement"]
    assert record["strategy"] == "narrower_denser"
    assert record["gap_ids"] == ["gap-occluded"]
    assert record["missing_evidence"] and record["rationale"]
    assert record["new_request"] == {"start": at(35), "end": at(75), "fps": 2}
    assert record["changes"] == [
        f"window {at(0)}..{at(120)} (120s) -> {at(35)}..{at(75)} (40s)",
        "fps 0.5 -> 2",
    ]
    assert record["budget_remaining"] == {
        "vlm_calls_remaining": 11,
        "vlm_calls_allocated": 4,
        "rounds_remaining": 3,
        "frames_planned": 80,
        "frames_affordable": 240,
        "deadline": DEADLINE,
        "seconds_remaining": 1800.0,
    }
    assert merged["refinements"][0]["outcome"]["observations_accepted"] == 1
    adjacent = refinement("adjacent_context", fps=1, prior=(40, 70, 1))
    assert L.create_inspection_tasks(ledger, {"claim-events": scope(30, 45)}, None, adjacent, now=CLOCK)
    boundary = refinement("boundary_check", fps=2, prior=(0, 120, 2))
    assert L.create_inspection_tasks(ledger, {"claim-events": scope(65, 80)}, None, boundary, now=CLOCK)


def test_07_identical_retries_and_repeated_exemptions_are_rejected() -> None:
    ledger, _ = two_seen_one_hidden()
    with pytest.raises(Error, match="identical retry"):
        L.create_inspection_tasks(ledger, {"claim-events": copy.deepcopy(SCOPE)}, None, refinement(fps=0.5), now=CLOCK)
    with pytest.raises(Error, match="higher fps"):
        L.create_inspection_tasks(ledger, {"claim-events": scope(35, 75)}, None, refinement(fps=0.5), now=CLOCK)
    with pytest.raises(Error, match="does not reach"):
        L.create_inspection_tasks(ledger, {"claim-events": scope(0, 20)}, None, refinement(), now=CLOCK)

    _, stalled = run_round(ledger, coverage="partial")
    assert stalled["status"] == "in_progress"
    assert stalled["no_progress_continuation"]["gap_ids"] == ["gap-occluded"]
    assert L.evaluate_stop(stalled)["refinement_only"] is True
    with pytest.raises(Error, match="only one refinement"):
        L.create_inspection_tasks(stalled, {"claim-events": copy.deepcopy(SCOPE)})

    _, after = run_round(stalled, coverage="partial", task_scope=scope(35, 75), refinement=refinement())
    assert after["status"] == "unresolved" and after["stop_reason"] == "no_progress"
    assert after["no_progress_continuation"] == stalled["no_progress_continuation"]

    _, progressed = run_round(
        ledger, [obs("[1:2] Rack view.", 35, 75, job="vlm-7")], coverage="partial", task_scope=scope(35, 75), refinement=refinement()
    )
    with pytest.raises(Error, match="already used"):
        L.create_inspection_tasks(progressed, {"claim-events": scope(40, 60)}, None, refinement(fps=4, prior=(35, 75, 2)), now=CLOCK)
    _, no_second = run_round(progressed, coverage="partial")
    assert no_second["stop_reason"] == "no_progress"
    assert "no_progress_continuation" not in no_second


def test_08_call_round_frame_and_time_limits_still_hold(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ledger, _ = two_seen_one_hidden()
    with pytest.raises(Error, match="do not fit"):
        L.create_inspection_tasks(ledger, {"claim-events": scope(35, 75)}, None, refinement(fps=10), now=CLOCK)
    late = datetime(2026, 9, 21, 20, 57, tzinfo=timezone.utc)
    with pytest.raises(Error, match="before the deadline"):
        L.create_inspection_tasks(ledger, {"claim-events": scope(35, 75)}, None, refinement(), now=late, deadline=DEADLINE)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"deadline": DEADLINE}), encoding="utf-8")
    monkeypatch.setattr(L, "POLICY_PATH", policy)
    with pytest.raises(Error, match="before the deadline"):
        L.create_inspection_tasks(ledger, {"claim-events": scope(35, 75)}, None, refinement(), now=late)
    task = L.create_inspection_tasks(ledger, {"claim-events": scope(35, 75)}, None, refinement(), now=CLOCK)[0]
    assert task["refinement"]["budget_remaining"]["deadline"] == DEADLINE
    assert ledger["budgets"] == L.load_budgets()

    exhausted = copy.deepcopy(ledger)
    exhausted["vlm_calls_used"] = exhausted["budgets"]["max_total_vlm_calls"]
    exhausted["revision"] += 1
    with pytest.raises(Error, match="budget is exhausted"):
        L.create_inspection_tasks(exhausted, {"claim-events": scope(35, 75)}, None, refinement(), now=CLOCK)
    last_round = copy.deepcopy(ledger)
    last_round["round"] = last_round["budgets"]["max_inspection_rounds"]
    with pytest.raises(Error, match="round budget"):
        L.create_inspection_tasks(last_round, {"claim-events": scope(35, 75)}, None, refinement(), now=CLOCK)
    tight = copy.deepcopy(ledger)
    tight["vlm_calls_used"] = tight["budgets"]["max_total_vlm_calls"] - 1
    with pytest.raises(Error, match="do not fit the 1-call"):
        L.create_inspection_tasks(tight, {"claim-events": scope(30, 80)}, None, refinement(fps=2), now=CLOCK)


def test_09_budget_exhaustion_is_terminal_and_auditable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger, seen = two_seen_one_hidden()
    _, ledger = run_round(ledger, [obs("[1:2] Rack view.", 35, 75, job="vlm-3")], coverage="partial", calls=4, task_scope=scope(35, 75), refinement=refinement())
    _, ledger = run_round(ledger, [obs("[1:2] Another view.", 0, 30, job="vlm-4")], coverage="partial", calls=4)
    _, ledger = run_round(ledger, [obs("[1:2] Late view.", 90, 120, job="vlm-6")], coverage="partial", calls=3)
    assert ledger["status"] == "unresolved" and ledger["stop_reason"] == "budget_exhausted"
    assert ledger["vlm_calls_used"] == ledger["budgets"]["max_total_vlm_calls"]
    assert "no_progress_continuation" not in ledger
    final = L.final_result(ledger, str(tmp_path), "Two", evidence_ids=[item["observation_id"] for item in seen])
    monkeypatch.setenv(L.ATTEMPT_CONTEXT_ENV, str(tmp_path / "missing-context.json"))
    L.atomic_commit_terminal(tmp_path / "ledger.json", ledger, tmp_path / "final-result.json", final)
    marker = json.loads((tmp_path / "terminal-commit.json").read_text(encoding="utf-8"))
    assert marker["revision"] == ledger["revision"]
    stored = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert stored["refinements"][0]["changes"] and stored["refinements"][0]["outcome"]
    assert final["count_assessment"][0]["count_kind"] == "lower_bound"


def test_10_a_best_available_answer_does_not_resolve_the_count(tmp_path: Path) -> None:
    ledger, seen = two_seen_one_hidden()
    _, stalled = run_round(ledger, coverage="partial")
    final = L.final_result(stalled, str(tmp_path), "Two", evidence_ids=[item["observation_id"] for item in seen])
    assert final["status"] == "answered"
    assert final["decision_source"] == "best_available_answer"
    assert final["evidence_status"] == "unresolved"
    assert final["count_assessment"][0]["count_kind"] == "lower_bound"
    assert {"claim_id", "gap", "reason"} == set(final["unresolved_gaps"][-1])
    assert "answer_label" not in final


def test_11_non_count_claims_keep_existing_behavior() -> None:
    ledger = new_ledger("attribute")
    _, first = run_round(ledger, [obs("[1:2] The vest is yellow.")], coverage="partial")
    _, stalled = run_round(first, coverage="partial")
    assert stalled["stop_reason"] == "no_progress"
    assert "no_progress_continuation" not in stalled
    with pytest.raises(Error, match="only valid for a count claim"):
        run_round(first, count_state=inventory(), coverage="partial")
    with pytest.raises(Error, match="count claim"):
        L.create_inspection_tasks(first, {"claim-events": scope(35, 75)}, None, refinement(), now=CLOCK)
    assert "count_assessment" not in L.assess_sufficiency(first)["claims"][0]


def test_12_existing_ledgers_and_artifacts_remain_compatible() -> None:
    def load(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    in_progress = load("legacy-ledger-in-progress.json")
    stopped = load("legacy-ledger-no-progress.json")
    L.validate_ledger(in_progress)
    L.validate_ledger(stopped)
    task = load("legacy-task-r2.json")
    L.validate_inspection_task(task, in_progress)
    assert L.merge_round_results(in_progress, [task], [load("legacy-result-r2.json")]) == stopped
    final = L.final_result(
        stopped,
        "/artifacts/case",
        "B",
        evidence_ids=[item["observation_id"] for item in stopped["observations"]],
    )
    assert final == load("legacy-final-result.json")
    assert L.assess_sufficiency(stopped)["claims"][0]["count_assessment"] == {
        "claim_id": "claim-count",
        "inventory": "absent",
        "count_kind": "unrecorded",
    }
