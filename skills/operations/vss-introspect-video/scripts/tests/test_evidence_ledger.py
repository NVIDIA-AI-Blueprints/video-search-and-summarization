# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit contracts for the deterministic introspection evidence ledger."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "evidence_ledger.py"
SPEC = importlib.util.spec_from_file_location("evidence_ledger", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
ledger_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ledger_mod)

START = "2026-09-21T20:00:00Z"
END = "2026-09-21T20:00:10Z"
MEDIA_SCOPE = {
    "type": "sensor",
    "sensor_id": "sensor-1",
    "start": START,
    "end": END,
}


def claim(
    claim_id: str = "claim-color",
    evidence_type: str = "attribute",
    coverage_requirement: str = "local_window",
) -> dict:
    return {
        "claim_id": claim_id,
        "requirement": f"Determine the visible outcome for {claim_id}.",
        "evidence_type": evidence_type,
        "coverage_requirement": coverage_requirement,
        "support_test": "A visible outcome satisfies the requirement.",
        "falsification_test": "A visibly incompatible outcome contradicts it.",
    }


def plan(*claims: dict, mode: str = "initial") -> dict:
    return {
        "plan_version": "2.0",
        "mode": mode,
        "question_id": "question-1",
        "question_text": "What happened?",
        "asset_id": "asset-1",
        "claims": list(claims or (claim(),)),
    }


def observation(
    claim_id: str = "claim-color",
    relation: str = "supports",
    text: str = "The worker is visibly wearing a yellow vest.",
    source_type: str = "vlm",
    record_id: str = "record-1",
    job_id: str = "vlm-1",
    media_scope: dict | None = None,
) -> dict:
    if source_type == "memory":
        source = {"type": "memory", "job_id": "memory-job-1", "record_id": record_id}
    else:
        scope = media_scope or MEDIA_SCOPE
        source = {"type": "vlm", "job_id": job_id}
        if scope["type"] == "sensor":
            source.update(
                {
                    "sensor_id": scope["sensor_id"],
                    "start": scope["start"],
                    "end": scope["end"],
                }
            )
        elif scope["type"] == "media_url":
            source["media_url"] = scope["media_url"]
        else:
            source["path"] = scope["path"]
    value = {
        "observation_id": "obs-" + "0" * 24,
        "claim_id": claim_id,
        "relation": relation,
        "text": text,
        "source": source,
    }
    value["observation_id"] = ledger_mod.observation_id(value)
    return value


def initialized(*claims: dict) -> dict:
    return ledger_mod.initialize_ledger(plan(*claims))


def create_tasks(ledger: dict, claim_ids: list[str] | None = None) -> list[dict]:
    selected = claim_ids or [
        state["claim_id"]
        for state in ledger["claims"]
        if state["status"] == "unresolved"
    ]
    media_scopes = {claim_id: copy.deepcopy(MEDIA_SCOPE) for claim_id in selected}
    return ledger_mod.create_inspection_tasks(ledger, media_scopes, claim_ids)


def result(
    task: dict,
    observations: tuple[dict, ...] = (),
    *,
    coverage: str = "sufficient",
    gap: str | None = None,
    calls: int = 1,
    error: str | None = None,
) -> dict:
    return {
        "task_id": task["task_id"],
        "base_revision": task["base_revision"],
        "observations": list(observations),
        "coverage": coverage,
        "gap": gap,
        "vlm_calls_used": calls,
        "error": error,
    }


def memory_update(
    claim_id: str,
    *observations: dict,
    coverage: str = "sufficient",
    gap: str | None = None,
) -> dict:
    return {
        "claim_id": claim_id,
        "observations": list(observations),
        "coverage": coverage,
        "gap": gap,
    }


def test_01_initializes_valid_one_claim_plan() -> None:
    ledger = initialized()
    assert ledger["revision"] == ledger["round"] == 0
    assert ledger["claims"] == [
        {
            "claim_id": "claim-color",
            "status": "unresolved",
            "coverage": "none",
            "observation_ids": [],
            "gap": "Evidence is needed to execute: A visible outcome satisfies the requirement.",
        }
    ]
    ledger_mod.validate_ledger(ledger)


def test_02_initializes_valid_two_claim_plan() -> None:
    ledger = initialized(claim(), claim("claim-count", "count", "whole_video"))
    assert [state["claim_id"] for state in ledger["claims"]] == [
        "claim-color",
        "claim-count",
    ]


def test_03_rejects_more_than_two_initial_claims() -> None:
    with pytest.raises(ledger_mod.LedgerValidationError, match="one or two"):
        initialized(claim(), claim("claim-count"), claim("claim-order"))


def test_04_rejects_unknown_evidence_type() -> None:
    with pytest.raises(ledger_mod.LedgerValidationError, match="evidence type"):
        initialized(claim(evidence_type="classification"))


def test_05_rejects_unknown_coverage_requirement() -> None:
    with pytest.raises(ledger_mod.LedgerValidationError, match="coverage requirement"):
        initialized(claim(coverage_requirement="entire_video"))


def test_06_preserves_evidence_plan_unchanged() -> None:
    source = plan(claim(), claim("claim-count", "count", "whole_video"))
    before = copy.deepcopy(source)
    ledger = ledger_mod.initialize_ledger(source)
    assert source == before
    assert ledger["plan"] == before
    assert ledger["plan"] is not source


def test_07_merges_one_successful_inspection_result() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    merged = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [result(tasks[0], (observation(),))],
    )
    assert merged["revision"] == merged["round"] == 1
    assert merged["vlm_calls_used"] == 1
    assert merged["claims"][0]["status"] == "supported"
    assert merged["status"] == "answered"
    assert merged["stop_reason"] == "resolved"


def test_08_merges_two_parallel_results_from_same_revision_once() -> None:
    ledger = initialized(claim(), claim("claim-count", "count", "whole_video"))
    tasks = create_tasks(ledger)
    assert {task["base_revision"] for task in tasks} == {0}
    merged = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [
            result(
                tasks[1],
                (observation("claim-count", text="Two vehicles are visible."),),
            ),
            result(tasks[0], (observation(),)),
        ],
    )
    assert merged["revision"] == merged["round"] == 1
    assert merged["vlm_calls_used"] == 2
    assert merged["status"] == "answered"


def test_parallel_failure_preserves_successful_sibling_result() -> None:
    ledger = initialized(claim(), claim("claim-count", "count", "whole_video"))
    tasks = create_tasks(ledger)
    merged = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [
            result(tasks[0], (observation(),)),
            result(
                tasks[1],
                coverage="none",
                gap="The VLM call failed.",
                error="backend unavailable",
            ),
        ],
    )
    assert merged["claims"][0]["status"] == "supported"
    assert merged["claims"][0]["observation_ids"]
    assert merged["claims"][1]["status"] == "unresolved"
    assert merged["revision"] == merged["round"] == 1


def test_09_rejects_stale_result() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    stale = result(tasks[0], (observation(),))
    stale["base_revision"] = 9
    with pytest.raises(ledger_mod.LedgerValidationError, match="frozen"):
        ledger_mod.merge_round_results(ledger, tasks, [stale])


def test_10_rejects_result_for_unknown_task() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    unknown = result(tasks[0])
    unknown["task_id"] = "inspect-claim-unknown-r1"
    with pytest.raises(ledger_mod.LedgerValidationError, match="unknown task"):
        ledger_mod.merge_round_results(ledger, tasks, [unknown])


def test_11_rejects_result_exceeding_task_allocation() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    tasks[0]["max_vlm_calls"] = 1
    with pytest.raises(ledger_mod.LedgerValidationError, match="allocation"):
        ledger_mod.merge_round_results(
            ledger,
            tasks,
            [result(tasks[0], calls=2)],
        )


def test_12_enforces_global_five_call_cap() -> None:
    ledger = initialized(claim(), claim("claim-count"))
    tasks = create_tasks(ledger)
    ledger = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [
            result(
                tasks[0],
                (observation(relation="context"),),
                coverage="partial",
                calls=2,
            ),
            result(
                tasks[1],
                (observation("claim-count", "context", "A vehicle is visible."),),
                coverage="partial",
                calls=2,
            ),
        ],
    )
    assert ledger["vlm_calls_used"] == 4
    second = create_tasks(ledger, ["claim-color"])
    overallocated = copy.deepcopy(second)
    overallocated[0]["max_vlm_calls"] = 2
    with pytest.raises(ledger_mod.LedgerValidationError, match="global"):
        ledger_mod.merge_round_results(
            ledger,
            overallocated,
            [result(overallocated[0], calls=2, coverage="partial")],
        )


def test_13_deduplicates_identical_observations() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    duplicate = observation()
    merged = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [result(tasks[0], (duplicate, copy.deepcopy(duplicate)))],
    )
    assert len(merged["observations"]) == 1
    assert len(merged["claims"][0]["observation_ids"]) == 1


def test_14_preserves_supporting_and_contradicting_observations() -> None:
    ledger = initialized()
    updates = [
        memory_update(
            "claim-color",
            observation(source_type="memory"),
            observation(
                relation="contradicts",
                text="The worker is visibly wearing a blue vest.",
                source_type="memory",
                record_id="record-2",
            ),
        )
    ]
    merged = ledger_mod.merge_memory(ledger, updates)
    assert {item["relation"] for item in merged["observations"]} == {
        "supports",
        "contradicts",
    }


def test_15_conflicting_support_and_contradiction_remain_unresolved() -> None:
    ledger = initialized()
    ledger = ledger_mod.merge_memory(
        ledger,
        [
            memory_update(
                "claim-color",
                observation(source_type="memory"),
                observation(
                    relation="contradicts",
                    text="The worker is visibly wearing a blue vest.",
                    source_type="memory",
                    record_id="record-2",
                ),
            )
        ],
    )
    assert ledger["claims"][0]["status"] == "unresolved"
    assert not ledger_mod.assess_sufficiency(ledger)["sufficient"]


def test_16_tool_failure_is_error_not_contradiction() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    merged = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [
            result(
                tasks[0],
                coverage="none",
                gap="The VLM call timed out.",
                calls=1,
                error="timeout",
            )
        ],
    )
    assert merged["observations"] == []
    assert merged["claims"][0]["status"] == "unresolved"
    assert merged["stop_reason"] == "tool_failure"


def test_17_missing_visibility_remains_unresolved_not_contradicted() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    merged = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [
            result(
                tasks[0],
                coverage="partial",
                gap="The qualifying person is occluded and not visible.",
                calls=1,
            )
        ],
    )
    assert merged["claims"][0]["status"] == "unresolved"
    assert merged["claims"][0]["observation_ids"] == []
    assert merged["status"] == "in_progress"


def test_18_detects_sufficient_completion_and_builds_traceable_result() -> None:
    ledger = ledger_mod.merge_memory(
        initialized(),
        [
            memory_update(
                "claim-color",
                observation(source_type="memory"),
            )
        ],
    )
    assert ledger_mod.assess_sufficiency(ledger)["sufficient"]
    final = ledger_mod.final_result(
        ledger,
        "runs/question-1",
        "The worker wore a yellow vest.",
    )
    assert final["status"] == "answered"
    assert final["evidence"] == ledger["claims"][0]["observation_ids"]
    assert final["evidence_details"] == ledger["observations"]
    assert final["revision"] == ledger["revision"]
    assert final["artifact_dir"] == "runs/question-1"
    assert final["unresolved_gaps"] == []


def test_19_detects_no_progress() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    merged = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [result(tasks[0], coverage="none", gap="No useful view was found.")],
    )
    assert merged["status"] == "unresolved"
    assert merged["stop_reason"] == "no_progress"


def test_20_detects_budget_exhaustion_across_rounds() -> None:
    ledger = initialized(claim(), claim("claim-count"))
    tasks = create_tasks(ledger)
    ledger = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [
            result(
                tasks[0],
                (observation(relation="context"),),
                coverage="partial",
                calls=2,
            ),
            result(
                tasks[1],
                (observation("claim-count", "context", "One vehicle is visible."),),
                coverage="partial",
                calls=2,
            ),
        ],
    )
    second = create_tasks(ledger, ["claim-color"])
    ledger = ledger_mod.merge_round_results(
        ledger,
        second,
        [
            result(
                second[0],
                (
                    observation(
                        relation="context",
                        text="A second bounded window shows the worker.",
                        job_id="vlm-2",
                    ),
                ),
                coverage="partial",
                calls=1,
            )
        ],
    )
    assert ledger["round"] == 2
    assert ledger["vlm_calls_used"] == 5
    assert ledger["stop_reason"] == "budget_exhausted"


def test_21_allows_exactly_one_expansion() -> None:
    ledger = initialized()
    expanded = ledger_mod.expand_ledger(
        ledger,
        plan(
            claim("claim-order", "order", "repeated_observation"),
            mode="expansion",
        ),
    )
    assert expanded["expansions_used"] == 1
    with pytest.raises(ledger_mod.LedgerValidationError, match="one expansion"):
        ledger_mod.expand_ledger(
            expanded,
            plan(claim("claim-count", "count", "whole_video"), mode="expansion"),
        )


def test_22_expansion_preserves_existing_claim_ids_and_evidence() -> None:
    ledger = ledger_mod.merge_memory(
        initialized(),
        [
            memory_update(
                "claim-color",
                observation(source_type="memory"),
                coverage="partial",
                gap="Another independent outcome is needed.",
            )
        ],
    )
    before_claim = copy.deepcopy(ledger["plan"]["claims"][0])
    before_state = copy.deepcopy(ledger["claims"][0])
    expanded = ledger_mod.expand_ledger(
        ledger,
        plan(claim("claim-count", "count", "whole_video"), mode="expansion"),
    )
    assert expanded["plan"]["claims"][0] == before_claim
    assert expanded["claims"][0] == before_state
    assert expanded["claims"][1]["claim_id"] == "claim-count"


def test_23_rejects_more_than_three_total_poc_claims() -> None:
    ledger = initialized(claim(), claim("claim-count"))
    ledger = ledger_mod.expand_ledger(
        ledger,
        plan(claim("claim-order", "order", "repeated_observation"), mode="expansion"),
    )
    assert len(ledger["plan"]["claims"]) == 3
    overflow = copy.deepcopy(ledger)
    overflow["plan"]["claims"].append(claim("claim-fourth"))
    overflow["claims"].append(
        {
            "claim_id": "claim-fourth",
            "status": "unresolved",
            "coverage": "none",
            "observation_ids": [],
            "gap": "Missing evidence.",
        }
    )
    with pytest.raises(ledger_mod.LedgerValidationError, match="one to three"):
        ledger_mod.validate_ledger(overflow)


def test_24_only_top_level_merge_changes_canonical_ledger(tmp_path: Path) -> None:
    ledger = initialized()
    before = copy.deepcopy(ledger)
    tasks = create_tasks(ledger)
    assert ledger == before
    result_payload = result(tasks[0], (observation(),))
    result_path = tmp_path / "result.json"
    ledger_mod.atomic_write(result_path, result_payload)
    assert ledger == before
    merged = ledger_mod.merge_round_results(ledger, tasks, [result_payload])
    assert merged["revision"] == before["revision"] + 1
    assert ledger == before


def test_budget_config_is_single_stdlib_readable_source() -> None:
    expected = {
        "max_initial_claims": 2,
        "max_expansions": 1,
        "max_total_claims": 3,
        "max_inspection_rounds": 2,
        "max_parallel_subagents": 2,
        "max_vlm_calls_per_subagent": 2,
        "max_total_vlm_calls": 5,
    }
    assert json.loads(ledger_mod.BUDGET_PATH.read_text()) == expected
    assert ledger_mod.BUDGETS == expected


def test_unknown_fields_and_non_iso_vlm_times_are_rejected() -> None:
    ledger = initialized()
    ledger["unknown"] = True
    with pytest.raises(ledger_mod.LedgerValidationError, match="unknown fields"):
        ledger_mod.validate_ledger(ledger)
    item = observation()
    item["source"]["start"] = "120"
    with pytest.raises(ledger_mod.LedgerValidationError, match="ISO-8601"):
        ledger_mod.observation_id(item)


def test_final_unresolved_result_uses_allowed_reason() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    ledger = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [result(tasks[0], coverage="none", gap="The person is not visible.")],
    )
    final = ledger_mod.final_result(ledger, "runs/question-1")
    assert final["status"] == "unresolved"
    assert final["answer"] is None
    assert final["evidence_details"] == []
    assert final["revision"] == ledger["revision"]
    assert final["artifact_dir"] == "runs/question-1"
    assert final["unresolved_gaps"][0]["reason"] == "not_visible"


def test_task_records_and_enforces_assigned_media_scope() -> None:
    ledger = initialized(claim(), claim("claim-count", "count", "whole_video"))
    scopes = {
        "claim-color": MEDIA_SCOPE,
        "claim-count": {"type": "file", "path": "/media/bounded-clip.mp4"},
    }
    tasks = ledger_mod.create_inspection_tasks(ledger, scopes)
    task = tasks[0]
    assert task["media_scope"] == MEDIA_SCOPE
    assert task["media_scope"] is not MEDIA_SCOPE
    assert tasks[1]["media_scope"] == scopes["claim-count"]

    wrong_sensor = observation()
    wrong_sensor["source"]["sensor_id"] = "sensor-2"
    wrong_sensor["observation_id"] = ledger_mod.observation_id(wrong_sensor)
    with pytest.raises(ledger_mod.LedgerValidationError, match="media scope"):
        ledger_mod.validate_inspection_result(
            result(task, (wrong_sensor,)),
            task,
        )

    outside_window = observation()
    outside_window["source"]["start"] = "2026-09-21T19:59:59Z"
    outside_window["observation_id"] = ledger_mod.observation_id(outside_window)
    with pytest.raises(ledger_mod.LedgerValidationError, match="outside assigned"):
        ledger_mod.validate_inspection_result(
            result(task, (outside_window,)),
            task,
        )


@pytest.mark.parametrize(
    "media_scope",
    [
        {"type": "media_url", "media_url": "https://example.com/clip.mp4"},
        {"type": "file", "path": "/media/bounded-clip.mp4"},
    ],
)
def test_non_sensor_vlm_provenance_matches_assigned_scope(media_scope: dict) -> None:
    ledger = initialized()
    task = ledger_mod.create_inspection_tasks(
        ledger,
        {"claim-color": media_scope},
    )[0]
    item = observation(media_scope=media_scope)
    assert "sensor_id" not in item["source"]
    assert "start" not in item["source"]
    assert "end" not in item["source"]

    merged = ledger_mod.merge_round_results(
        ledger,
        [task],
        [result(task, (item,))],
    )
    assert merged["status"] == "answered"
    assert merged["observations"][0]["source"] == item["source"]


@pytest.mark.parametrize(
    ("assigned", "reported"),
    [
        (
            {"type": "media_url", "media_url": "https://example.com/assigned.mp4"},
            {"type": "media_url", "media_url": "https://example.com/other.mp4"},
        ),
        (
            {"type": "file", "path": "/media/assigned.mp4"},
            {"type": "file", "path": "/media/other.mp4"},
        ),
    ],
)
def test_non_sensor_vlm_provenance_rejects_scope_mismatch(
    assigned: dict, reported: dict
) -> None:
    ledger = initialized()
    task = ledger_mod.create_inspection_tasks(
        ledger,
        {"claim-color": assigned},
    )[0]
    with pytest.raises(ledger_mod.LedgerValidationError, match="assigned"):
        ledger_mod.validate_inspection_result(
            result(task, (observation(media_scope=reported),)),
            task,
        )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (START, START),
        (END, START),
    ],
)
def test_rejects_equal_and_reversed_observation_windows(start: str, end: str) -> None:
    item = observation()
    item["source"]["start"] = start
    item["source"]["end"] = end
    with pytest.raises(ledger_mod.LedgerValidationError, match="strictly earlier"):
        ledger_mod.observation_id(item)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (START, START),
        (END, START),
    ],
)
def test_rejects_equal_and_reversed_task_windows(start: str, end: str) -> None:
    media_scope = {**MEDIA_SCOPE, "start": start, "end": end}
    with pytest.raises(ledger_mod.LedgerValidationError, match="strictly earlier"):
        ledger_mod.create_inspection_tasks(
            initialized(),
            {"claim-color": media_scope},
        )


def test_rejects_invalid_media_url_scope() -> None:
    with pytest.raises(ledger_mod.LedgerValidationError, match="HTTP"):
        ledger_mod.create_inspection_tasks(
            initialized(),
            {
                "claim-color": {
                    "type": "media_url",
                    "media_url": "not-a-url",
                }
            },
        )


def test_terminal_ledgers_reject_memory_merge_and_expansion() -> None:
    answered = ledger_mod.merge_memory(
        initialized(),
        [memory_update("claim-color", observation(source_type="memory"))],
    )
    in_progress = initialized()
    tasks = create_tasks(in_progress)
    unresolved = ledger_mod.merge_round_results(
        in_progress,
        tasks,
        [result(tasks[0], coverage="none", gap="No useful view was found.")],
    )
    assert answered["status"] == "answered"
    assert unresolved["status"] == "unresolved"

    expansion = plan(
        claim("claim-count", "count", "whole_video"),
        mode="expansion",
    )
    update = [memory_update("claim-color", observation(source_type="memory"))]
    for terminal in (answered, unresolved):
        with pytest.raises(ledger_mod.LedgerValidationError, match="terminal"):
            ledger_mod.merge_memory(terminal, update)
        with pytest.raises(ledger_mod.LedgerValidationError, match="terminal"):
            ledger_mod.expand_ledger(terminal, expansion)


def test_final_result_contains_audit_provenance() -> None:
    ledger = ledger_mod.merge_memory(
        initialized(),
        [memory_update("claim-color", observation(source_type="memory"))],
    )
    final = ledger_mod.final_result(ledger, "runs/question-1", "Visible result.")
    assert final["evidence_details"] == ledger["observations"]
    assert final["revision"] == ledger["revision"]
    assert final["artifact_dir"] == "runs/question-1"
