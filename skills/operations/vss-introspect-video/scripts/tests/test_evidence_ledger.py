# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit contracts for the deterministic introspection evidence ledger."""

from __future__ import annotations

import copy
import hashlib
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
        if state["status"] not in ("supported", "contradicted")
        or state["coverage"] != "sufficient"
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


def finalize(
    ledger: dict,
    directory: str,
    label: str | None,
    explanation: str | None,
    **kwargs: object,
) -> dict:
    evidence_ids = kwargs.pop(
        "evidence_ids",
        [item["observation_id"] for item in ledger["observations"]],
    )
    choices = kwargs.pop("choices", None)
    if (
        choices is None
        and isinstance(label, str)
        and len(label.strip()) == 1
        and label.strip().isalpha()
    ):
        choices = ["A", "B", "C", "D"]
    return ledger_mod.final_result(
        ledger,
        directory,
        label,
        explanation,
        evidence_ids=evidence_ids,
        choices=choices,
        **kwargs,
    )


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


def test_12_enforces_global_twelve_call_cap() -> None:
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
                calls=4,
            ),
            result(
                tasks[1],
                (observation("claim-count", "context", "A vehicle is visible."),),
                coverage="partial",
                calls=4,
            ),
        ],
    )
    assert ledger["vlm_calls_used"] == 8
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
                        text="A second window remains partial.",
                        job_id="vlm-2",
                    ),
                ),
                calls=3,
                coverage="partial",
            )
        ],
    )
    assert ledger["vlm_calls_used"] == 11
    third = create_tasks(ledger, ["claim-color"])
    overallocated = copy.deepcopy(third)
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


def test_memory_merge_repairs_malformed_observation_id() -> None:
    item = observation(source_type="memory")
    item["observation_id"] = "agent-invented-id"
    merged = ledger_mod.merge_memory(
        initialized(),
        [memory_update("claim-color", item)],
    )
    assert merged["observations"][0]["observation_id"] == ledger_mod.observation_id(item)


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
    final = finalize(
        ledger,
        "runs/question-1",
        "A",
        "The worker wore a yellow vest.",
    )
    assert final["status"] == "answered"
    assert final["answer_label"] == "A"
    assert final["answer_explanation"] == "The worker wore a yellow vest."
    assert final["answer"] == "A. The worker wore a yellow vest."
    assert final["evidence"] == ledger["claims"][0]["observation_ids"]
    assert final["evidence_details"] == ledger["observations"]
    assert final["revision"] == ledger["revision"]
    assert final["artifact_dir"] == "runs/question-1"
    assert final["unresolved_gaps"] == []


def test_open_question_answers_without_a_choice_label() -> None:
    ledger = ledger_mod.merge_memory(
        initialized(),
        [memory_update("claim-color", observation(source_type="memory"))],
    )
    evidence = [item["observation_id"] for item in ledger["observations"]]
    final = ledger_mod.final_result(
        ledger,
        "runs/question-1",
        None,
        "The worker wore a yellow vest.",
        evidence_ids=evidence,
    )
    assert final["answer_label"] is None
    assert final["answer"] == "The worker wore a yellow vest."
    worded = ledger_mod.final_result(
        ledger,
        "runs/question-1",
        "yellow",
        "The vest is yellow.",
        evidence_ids=evidence,
        choices=["red", "yellow"],
    )
    assert worded["answer_label"] == "yellow"
    assert worded["answer"] == "yellow. The vest is yellow."


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
    assert ledger["status"] == "in_progress"
    third = create_tasks(ledger, ["claim-color"])
    ledger = ledger_mod.merge_round_results(
        ledger,
        third,
        [
            result(
                third[0],
                (
                    observation(
                        relation="context",
                        text="A third bounded window remains inconclusive.",
                        job_id="vlm-3",
                    ),
                ),
                coverage="partial",
                calls=1,
            )
        ],
    )
    assert ledger["status"] == "in_progress"
    fourth = create_tasks(ledger, ["claim-color"])
    ledger = ledger_mod.merge_round_results(
        ledger,
        fourth,
        [
            result(
                fourth[0],
                (
                    observation(
                        relation="context",
                        text="A fourth bounded window remains inconclusive.",
                        job_id="vlm-4",
                    ),
                ),
                coverage="partial",
                calls=1,
            )
        ],
    )
    assert ledger["round"] == 4
    assert ledger["vlm_calls_used"] == 7
    assert ledger["stop_reason"] == "budget_exhausted"


def test_21_allows_four_expansions() -> None:
    ledger = initialized()
    names = ("claim-order", "claim-count", "claim-duration", "claim-spatial")
    types = ("order", "count", "duration", "spatial")
    coverages = (
        "repeated_observation",
        "whole_video",
        "local_window",
        "local_window",
    )
    for name, evidence_type, coverage in zip(names, types, coverages):
        ledger = ledger_mod.expand_ledger(
            ledger,
            plan(claim(name, evidence_type, coverage), mode="expansion"),
        )
    assert ledger["expansions_used"] == 4
    with pytest.raises(ledger_mod.LedgerValidationError, match="4 expansions"):
        ledger_mod.expand_ledger(
            ledger,
            plan(claim("claim-trajectory", "trajectory", "repeated_observation"), mode="expansion"),
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


def test_23_rejects_more_than_six_total_claims() -> None:
    ledger = initialized(claim(), claim("claim-count"))
    names = ("claim-order", "claim-duration", "claim-spatial", "claim-trajectory")
    types = ("order", "duration", "spatial", "trajectory")
    coverages = (
        "repeated_observation",
        "local_window",
        "local_window",
        "repeated_observation",
    )
    for name, evidence_type, coverage in zip(names, types, coverages):
        ledger = ledger_mod.expand_ledger(
            ledger,
            plan(claim(name, evidence_type, coverage), mode="expansion"),
        )
    assert len(ledger["plan"]["claims"]) == 6
    overflow = copy.deepcopy(ledger)
    overflow["plan"]["claims"].append(claim("claim-seventh"))
    overflow["claims"].append(
        {
            "claim_id": "claim-seventh",
            "status": "unresolved",
            "coverage": "none",
            "observation_ids": [],
            "gap": "Missing evidence.",
        }
    )
    with pytest.raises(ledger_mod.LedgerValidationError, match="one to 6"):
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
        "max_expansions": 4,
        "max_total_claims": 6,
        "max_inspection_rounds": 4,
        "max_parallel_subagents": 2,
        "max_vlm_calls_per_subagent": 4,
        "max_total_vlm_calls": 12,
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


def test_unresolved_options_question_records_best_available_choice() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    ledger = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [result(tasks[0], coverage="none", gap="The person is not visible.")],
    )
    with pytest.raises(ledger_mod.LedgerValidationError, match="must cite"):
        ledger_mod.final_result(
            ledger,
            "runs/question-1",
            "C",
            "C is the closest option; the vest color was not directly visible.",
            choices=["A", "B", "C", "D"],
        )
    forced = ledger_mod.final_result(
        ledger,
        "runs/question-1",
        "C",
        "No observation supports a choice; this selection is unsupported.",
        choices=["A", "B", "C", "D"],
        require_choice=True,
    )
    assert forced["decision_source"] == "unsupported_forced_choice"
    assert forced["evidence_status"] == "unresolved"
    assert forced["evidence"] == []
    assert ledger["status"] == "unresolved"
    ledger_mod.validate_terminal_pair(
        ledger_mod.prepare_for_final_result(ledger), forced
    )
    with pytest.raises(ledger_mod.LedgerValidationError, match="answer_explanation"):
        ledger_mod.final_result(
            ledger,
            "runs/question-1",
            "C",
            None,
            choices=["A", "B", "C", "D"],
            require_choice=True,
        )


def test_citations_follow_the_synthesizer_and_labels_must_be_members(
    tmp_path: Path,
) -> None:
    ledger = ledger_mod.merge_memory(
        initialized(),
        [
            memory_update(
                "claim-color",
                observation(source_type="memory"),
                observation(
                    relation="context",
                    text="Ambient lighting is dim.",
                    source_type="memory",
                    record_id="record-2",
                ),
            )
        ],
    )
    support_id = next(
        item["observation_id"]
        for item in ledger["observations"]
        if item["relation"] == "supports"
    )
    context_id = next(
        item["observation_id"]
        for item in ledger["observations"]
        if item["relation"] == "context"
    )
    final = ledger_mod.final_result(
        ledger,
        "runs/question-1",
        "A",
        "The vest is yellow.",
        evidence_ids=[support_id, support_id],
        choices=["A", "B", "C", "D"],
    )
    assert final["evidence"] == [support_id]
    assert context_id not in final["evidence"]
    assert final["evidence_details"][0]["source"]["record_id"] == "record-1"
    with pytest.raises(ledger_mod.LedgerValidationError, match="unknown observation"):
        ledger_mod.final_result(
            ledger,
            "runs/question-1",
            "A",
            "The vest is yellow.",
            evidence_ids=["obs-" + "f" * 24],
            choices=["A", "B", "C", "D"],
        )
    with pytest.raises(ledger_mod.LedgerValidationError, match="one of the question's choices"):
        ledger_mod.final_result(
            ledger,
            "runs/question-1",
            "E",
            "Not a choice.",
            evidence_ids=[support_id],
            choices=["A", "B", "C", "D"],
        )
    accepted = ledger_mod.final_result(
        ledger,
        "runs/question-1",
        "h",
        "H is allowed.",
        evidence_ids=[support_id],
        choices=list("ABCDEFGH"),
    )
    assert accepted["answer_label"] == "H"
    with pytest.raises(ledger_mod.LedgerValidationError, match="allowed labels"):
        ledger_mod.final_result(
            ledger,
            "runs/question-1",
            "A",
            "Missing the choice set.",
            evidence_ids=[support_id],
        )
    with pytest.raises(ledger_mod.LedgerValidationError, match="unique"):
        ledger_mod.final_result(
            ledger,
            "runs/question-1",
            "A",
            "Ambiguous labels.",
            evidence_ids=[support_id],
            choices=["A", "a"],
        )
    open_ended = ledger_mod.final_result(
        ledger,
        "runs/question-1",
        None,
        "The vest is yellow.",
        evidence_ids=[support_id],
    )
    assert open_ended["answer_label"] is None
    assert open_ended["evidence"] == [support_id]

    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ledger_mod.LedgerValidationError, match="blank choice"):
        ledger_mod.main(
            [
                "final-result",
                "--ledger",
                str(path),
                "--artifact-dir",
                str(tmp_path),
                "--answer-label",
                "",
                "--answer-explanation",
                "",
            ]
        )


def test_best_available_choice_keeps_gaps_and_selected_citations() -> None:
    ledger = initialized()
    while ledger["status"] == "in_progress":
        tasks = create_tasks(ledger)
        note = observation(
            relation="context",
            text=f"Context note for round {ledger['round']}.",
            job_id=f"vlm-c{ledger['round']}",
        )
        fact = observation(
            text=f"A yellow vest is partly visible in round {ledger['round']}.",
            job_id=f"vlm-s{ledger['round']}",
        )
        ledger = ledger_mod.merge_round_results(
            ledger,
            tasks,
            [result(tasks[0], (note, fact), coverage="partial", calls=1)],
        )
    assert ledger["status"] == "unresolved"
    fact_id = next(
        item["observation_id"]
        for item in ledger["observations"]
        if item["relation"] == "supports"
    )
    context_id = next(
        item["observation_id"]
        for item in ledger["observations"]
        if item["relation"] == "context"
    )
    final = ledger_mod.final_result(
        ledger,
        "runs/question-1",
        "B",
        "B is the closest supported color; coverage is still partial.",
        evidence_ids=[fact_id, fact_id],
        choices=["A", "B", "C", "D"],
    )
    assert final["decision_source"] == "best_available_choice"
    assert final["evidence_status"] == "unresolved"
    assert final["evidence"] == [fact_id]
    assert context_id not in final["evidence"]
    assert final["unresolved_gaps"]
    assert ledger["status"] == "unresolved"


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
    final = finalize(ledger, "runs/question-1", "A", "Visible result.")
    assert final["evidence_details"] == ledger["observations"]
    assert final["revision"] == ledger["revision"]
    assert final["artifact_dir"] == "runs/question-1"


def test_supported_partial_claim_stays_eligible_while_budget_remains() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    partial = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [result(tasks[0], (observation(),), coverage="partial", calls=1)],
    )
    assert partial["status"] == "in_progress"
    assert partial["claims"][0]["status"] == "supported"
    assert partial["claims"][0]["coverage"] == "partial"
    assert partial["round"] == 1
    again = create_tasks(partial)
    assert again[0]["claim"]["claim_id"] == "claim-color"
    assert again[0]["task_id"].endswith("-r2")
    with pytest.raises(ledger_mod.LedgerValidationError, match="budget remains"):
        ledger_mod.final_result(partial, "runs/question-1", "A", "Too early.")


def test_exhausted_partial_ledger_terminates_and_writes_final_result(tmp_path: Path) -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    partial = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [result(tasks[0], (observation(),), coverage="partial", calls=1)],
    )
    second = create_tasks(partial)
    second_partial = ledger_mod.merge_round_results(
        partial,
        second,
        [
            result(
                second[0],
                (
                    observation(
                        text="A second window still shows only part of the clothing.",
                        job_id="vlm-2",
                    ),
                ),
                coverage="partial",
                calls=1,
            )
        ],
    )
    assert second_partial["status"] == "in_progress"
    third = create_tasks(second_partial)
    third_partial = ledger_mod.merge_round_results(
        second_partial,
        third,
        [
            result(
                third[0],
                (
                    observation(
                        text="A third window still leaves the clothing partially covered.",
                        job_id="vlm-3",
                    ),
                ),
                coverage="partial",
                calls=1,
            )
        ],
    )
    assert third_partial["status"] == "in_progress"
    fourth = create_tasks(third_partial)
    exhausted = ledger_mod.merge_round_results(
        third_partial,
        fourth,
        [
            result(
                fourth[0],
                (
                    observation(
                        text="A fourth window still leaves the clothing partially covered.",
                        job_id="vlm-4",
                    ),
                ),
                coverage="partial",
                calls=1,
            )
        ],
    )
    assert exhausted["status"] == "unresolved"
    assert exhausted["stop_reason"] == "budget_exhausted"
    assert exhausted["round"] == 4
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(exhausted), encoding="utf-8")
    assert ledger_mod.main(
        [
            "final-result",
            "--ledger",
            str(ledger_path),
            "--artifact-dir",
            str(tmp_path),
            "--output",
            str(tmp_path / "final-result.json"),
        ]
    ) == 0
    final = json.loads((tmp_path / "final-result.json").read_text(encoding="utf-8"))
    assert final["status"] == "unresolved"
    assert final["answer"] is None
    assert final["unresolved_gaps"][0]["reason"] == "budget_exhausted"


def test_contradicted_partial_claim_stays_eligible_while_budget_remains() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    partial = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [
            result(
                tasks[0],
                (observation(relation="contradicts", text="The claimed color is visibly absent."),),
                coverage="partial",
                calls=1,
            )
        ],
    )
    assert partial["status"] == "in_progress"
    assert partial["claims"][0]["status"] == "contradicted"
    assert partial["claims"][0]["coverage"] == "partial"
    again = create_tasks(partial)
    assert again[0]["claim"]["claim_id"] == "claim-color"
    with pytest.raises(ledger_mod.LedgerValidationError, match="budget remains"):
        ledger_mod.final_result(partial, "runs/question-1")


def test_window_completion_alone_does_not_satisfy_the_evidence_gate() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    occluded = observation(
        relation="context",
        text="The worker is occluded, so no hat transition is visibly established.",
    )
    merged = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [result(tasks[0], (occluded,), coverage="sufficient")],
    )
    assert merged["claims"][0]["status"] == "unresolved"
    assert ledger_mod.assess_sufficiency(merged)["sufficient"] is False
    assert merged["status"] != "answered"

    visible = observation(
        text="The worker is partly occluded, and the yellow vest is clearly visible."
    )
    supported = ledger_mod.merge_round_results(
        initialized(),
        tasks,
        [result(tasks[0], (visible,), coverage="sufficient")],
    )
    assert supported["claims"][0]["status"] == "supported"
    assert ledger_mod.assess_sufficiency(supported)["sufficient"] is True


def test_sufficient_supported_claim_resolves() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    resolved = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [result(tasks[0], (observation(),), coverage="sufficient")],
    )
    assert resolved["status"] == "answered"
    assert resolved["stop_reason"] == "resolved"
    final = finalize(resolved, "runs/question-1", "A", "The claim holds.")
    assert final["status"] == "answered"
    assert final["unresolved_gaps"] == []


def test_sufficient_contradicted_claim_resolves() -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    resolved = ledger_mod.merge_round_results(
        ledger,
        tasks,
        [
            result(
                tasks[0],
                (observation(relation="contradicts", text="The claimed color is visibly absent."),),
                coverage="sufficient",
            )
        ],
    )
    assert resolved["claims"][0]["status"] == "contradicted"
    assert resolved["claims"][0]["coverage"] == "sufficient"
    assert resolved["status"] == "answered"
    assert resolved["stop_reason"] == "resolved"


def _run_merge(tmp_path: Path, results: list[dict]) -> tuple[dict, dict]:
    ledger = initialized()
    tasks = create_tasks(ledger)
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    (tmp_path / "results.json").write_text(json.dumps(results), encoding="utf-8")
    assert ledger_mod.main(
        [
            "merge-round",
            "--ledger",
            str(ledger_path),
            "--tasks",
            str(tmp_path / "tasks.json"),
            "--results",
            str(tmp_path / "results.json"),
            "--output",
            str(ledger_path),
        ]
    ) == 0
    merged = json.loads(ledger_path.read_text(encoding="utf-8"))
    final = json.loads((tmp_path / "final-result.json").read_text(encoding="utf-8"))
    return merged, final


def test_unresolved_no_progress_merge_writes_final_result(tmp_path: Path) -> None:
    tasks = create_tasks(initialized())
    merged, final = _run_merge(
        tmp_path,
        [result(tasks[0], coverage="none", gap="No useful view was found.")],
    )
    assert merged["status"] == "unresolved"
    assert merged["stop_reason"] == "no_progress"
    assert final["status"] == "unresolved"
    assert final["answer"] is None


def test_tool_failure_merge_writes_final_result(tmp_path: Path) -> None:
    tasks = create_tasks(initialized())
    merged, final = _run_merge(
        tmp_path,
        [result(tasks[0], coverage="none", error="vlm timed out", calls=1)],
    )
    assert merged["status"] == "unresolved"
    assert merged["stop_reason"] == "tool_failure"
    assert final["status"] == "unresolved"
    assert final["unresolved_gaps"][0]["reason"] == "tool_failure"


def test_collects_persisted_results_in_task_order(tmp_path: Path) -> None:
    ledger = initialized(claim(), claim("claim-count", "count", "whole_video"))
    tasks = create_tasks(ledger)
    for task in reversed(tasks):
        claim_id = task["claim"]["claim_id"]
        payload = result(
            task,
            (
                observation(
                    claim_id,
                    text=f"Visible evidence for {claim_id}.",
                    job_id=f"vlm-{claim_id}",
                ),
            ),
        )
        (tmp_path / f"result-{claim_id}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    collected = ledger_mod.collect_inspection_results(tasks, tmp_path)
    assert [item["task_id"] for item in collected] == sorted(
        task["task_id"] for task in tasks
    )


def test_collect_repairs_malformed_observation_id_atomically(tmp_path: Path) -> None:
    tasks = create_tasks(initialized())
    payload = result(tasks[0], (observation(),))
    payload["observations"][0]["observation_id"] = "obs-not-canonical"
    path = tmp_path / "result-claim-color.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    collected = ledger_mod.collect_inspection_results(tasks, tmp_path)

    expected = ledger_mod.observation_id(payload["observations"][0])
    assert collected[0]["observations"][0]["observation_id"] == expected
    assert json.loads(path.read_text())["observations"][0]["observation_id"] == expected
    merged = ledger_mod.merge_round_results(initialized(), tasks, collected)
    assert merged["observations"][0]["observation_id"] == expected


def test_collect_results_reports_missing_task(tmp_path: Path) -> None:
    tasks = create_tasks(initialized())
    with pytest.raises(ledger_mod.LedgerValidationError, match="missing results"):
        ledger_mod.collect_inspection_results(tasks, tmp_path)


def test_resumed_round_merge_is_idempotent() -> None:
    base = initialized()
    tasks = create_tasks(base)
    results = [result(tasks[0], (observation(),))]
    merged = ledger_mod.resume_round_merge(base, base, tasks, results)
    resumed = ledger_mod.resume_round_merge(base, merged, tasks, results)
    assert resumed == merged
    assert resumed["revision"] == 1
    assert resumed["round"] == 1
    assert resumed["vlm_calls_used"] == 1


def test_resumed_round_rejects_unrelated_current_ledger() -> None:
    base = initialized()
    tasks = create_tasks(base)
    results = [result(tasks[0], (observation(),))]
    unrelated = copy.deepcopy(base)
    unrelated["revision"] = 2
    with pytest.raises(ledger_mod.LedgerValidationError, match="neither"):
        ledger_mod.resume_round_merge(base, unrelated, tasks, results)


def test_terminal_commit_publishes_revision_matched_marker(tmp_path: Path) -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    answered = ledger_mod.merge_round_results(
        ledger, tasks, [result(tasks[0], (observation(),))]
    )
    final = finalize(answered, str(tmp_path), "A", "Visible result")
    ledger_mod.atomic_commit_terminal(
        tmp_path / "ledger.json",
        answered,
        tmp_path / "final-result.json",
        final,
    )
    marker = json.loads((tmp_path / "terminal-commit.json").read_text())
    assert marker["revision"] == answered["revision"] == final["revision"]
    assert marker["status"] == "answered"
    assert marker["ledger_sha256"] == ledger_mod._digest(answered)
    assert marker["final_result_sha256"] == ledger_mod._digest(final)


def test_terminal_commit_binds_attempt_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    answered = ledger_mod.merge_round_results(
        ledger, tasks, [result(tasks[0], (observation(),))]
    )
    final = finalize(answered, str(tmp_path), "A", "Visible result")
    context = {
        "schema_version": 1,
        "case_id": "dataset-case-1",
        "attempt_id": "attempt-3",
        "question_sha256": hashlib.sha256(
            answered["plan"]["question_text"].encode("utf-8")
        ).hexdigest(),
        "video_id": answered["plan"]["asset_id"],
        "sensor_id": "canonical-sensor-uuid",
    }
    context_path = tmp_path / "attempt-context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    monkeypatch.setenv(ledger_mod.ATTEMPT_CONTEXT_ENV, str(context_path))

    ledger_mod.atomic_commit_terminal(
        tmp_path / "ledger.json",
        answered,
        tmp_path / "final-result.json",
        final,
    )

    marker = json.loads((tmp_path / "terminal-commit.json").read_text())
    assert marker["attempt_context"] == context


def test_terminal_commit_rejects_context_for_wrong_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    answered = ledger_mod.merge_round_results(
        ledger, tasks, [result(tasks[0], (observation(),))]
    )
    final = finalize(answered, str(tmp_path), "A", "Visible result")
    context = {
        "schema_version": 1,
        "case_id": "dataset-case-1",
        "attempt_id": "attempt-3",
        "question_sha256": hashlib.sha256(
            answered["plan"]["question_text"].encode("utf-8")
        ).hexdigest(),
        "video_id": "wrong-video",
        "sensor_id": "canonical-sensor-uuid",
    }
    context_path = tmp_path / "attempt-context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    monkeypatch.setenv(ledger_mod.ATTEMPT_CONTEXT_ENV, str(context_path))

    with pytest.raises(ledger_mod.LedgerValidationError, match="video_id"):
        ledger_mod.atomic_commit_terminal(
            tmp_path / "ledger.json",
            answered,
            tmp_path / "final-result.json",
            final,
        )


def test_terminal_commit_rejects_revision_mismatch(tmp_path: Path) -> None:
    ledger = initialized()
    tasks = create_tasks(ledger)
    answered = ledger_mod.merge_round_results(
        ledger, tasks, [result(tasks[0], (observation(),))]
    )
    final = finalize(answered, str(tmp_path), "A", "Visible result")
    final["revision"] += 1
    with pytest.raises(ledger_mod.LedgerValidationError, match="revision"):
        ledger_mod.atomic_commit_terminal(
            tmp_path / "ledger.json",
            answered,
            tmp_path / "final-result.json",
            final,
        )


def test_categories_and_budgets_stay_at_the_base_contract() -> None:
    assert ledger_mod.EVIDENCE_TYPES == (
        "attribute",
        "object",
        "count",
        "action",
        "state_change",
        "order",
        "duration",
        "trajectory",
        "identity",
        "spatial",
        "cause",
        "prediction",
        "counterfactual",
        "negative",
    )
    assert ledger_mod.CLAIM_STATUSES == ("supported", "contradicted", "unresolved")
    assert ledger_mod.STOP_REASONS == (
        None,
        "resolved",
        "no_progress",
        "budget_exhausted",
        "tool_failure",
    )
    assert ledger_mod.BUDGETS == {
        "max_initial_claims": 2,
        "max_expansions": 4,
        "max_total_claims": 6,
        "max_inspection_rounds": 4,
        "max_parallel_subagents": 2,
        "max_vlm_calls_per_subagent": 4,
        "max_total_vlm_calls": 12,
    }
