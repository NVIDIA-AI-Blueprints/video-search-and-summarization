#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic contracts and merge operations for video introspection."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BUDGET_PATH = ROOT / "config" / "ledger-budgets.json"

EVIDENCE_TYPES = (
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
COVERAGE_REQUIREMENTS = (
    "local_window",
    "before_after",
    "repeated_observation",
    "whole_video",
)
COVERAGES = ("none", "partial", "sufficient")
CLAIM_STATUSES = ("supported", "contradicted", "unresolved")
LEDGER_STATUSES = ("in_progress", "answered", "unresolved")
STOP_REASONS = (None, "resolved", "no_progress", "budget_exhausted", "tool_failure")
RELATIONS = ("supports", "contradicts", "context")
UNRESOLVED_REASONS = (
    "insufficient_coverage",
    "not_visible",
    "tool_failure",
    "budget_exhausted",
)

PLAN_KEYS = {
    "plan_version",
    "mode",
    "question_id",
    "question_text",
    "asset_id",
    "claims",
}
PLAN_CLAIM_KEYS = {
    "claim_id",
    "requirement",
    "evidence_type",
    "coverage_requirement",
    "support_test",
    "falsification_test",
}
LEDGER_KEYS = {
    "ledger_version",
    "revision",
    "plan",
    "claims",
    "observations",
    "round",
    "expansions_used",
    "vlm_calls_used",
    "status",
    "stop_reason",
}
CLAIM_STATE_KEYS = {"claim_id", "status", "coverage", "observation_ids", "gap"}
OBSERVATION_KEYS = {"observation_id", "claim_id", "relation", "text", "source"}
TASK_KEYS = {
    "task_id",
    "base_revision",
    "claim",
    "gap",
    "existing_observations",
    "asset_id",
    "max_vlm_calls",
}
RESULT_KEYS = {
    "task_id",
    "base_revision",
    "observations",
    "coverage",
    "gap",
    "vlm_calls_used",
    "error",
}
MEMORY_SOURCE_REQUIRED = {"type", "record_id"}
MEMORY_SOURCE_OPTIONAL = {"job_id", "sensor_id", "start", "end"}
VLM_SOURCE_KEYS = {"type", "job_id", "sensor_id", "start", "end"}
CLAIM_ID_RE = re.compile(r"^claim-[a-z0-9]+(?:-[a-z0-9]+)*$")
TASK_ID_RE = re.compile(r"^inspect-claim-[a-z0-9]+(?:-[a-z0-9]+)*-r[1-9][0-9]*$")
OBSERVATION_ID_RE = re.compile(r"^obs-[a-f0-9]{24}$")


class LedgerValidationError(ValueError):
    """Raised when a contract or state transition is invalid."""


def _fail(path: str, message: str) -> None:
    raise LedgerValidationError(f"{path}: {message}")


def _strict(value: Any, keys: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    missing = keys - set(value)
    unknown = set(value) - keys
    if missing:
        _fail(path, f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        _fail(path, f"unknown fields: {', '.join(sorted(unknown))}")
    return value


def _nonempty(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "must be a non-empty string")
    return value


def _nullable_string(value: Any, path: str) -> None:
    if value is not None:
        _nonempty(value, path)


def _integer(
    value: Any, path: str, minimum: int = 0, maximum: int | None = None
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, "must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" and at most {maximum}" if maximum is not None else ""
        _fail(path, f"must be at least {minimum}{upper}")
    return value


def _timestamp(value: Any, path: str) -> str:
    value = _nonempty(value, path)
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise LedgerValidationError(f"{path}: must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(path, "must include a timezone")
    return value


def load_budgets(path: str | os.PathLike[str] = BUDGET_PATH) -> dict[str, int]:
    """Load and validate the single authoritative POC budget document."""
    with open(path, encoding="utf-8") as stream:
        value = json.load(stream)
    keys = {
        "max_initial_claims",
        "max_expansions",
        "max_total_claims",
        "max_inspection_rounds",
        "max_parallel_subagents",
        "max_vlm_calls_per_subagent",
        "max_total_vlm_calls",
    }
    _strict(value, keys, "budgets")
    for key in keys:
        _integer(value[key], f"budgets.{key}", 1)
    return dict(value)


BUDGETS = load_budgets()


def _validate_claim(claim: Any, path: str) -> None:
    claim = _strict(claim, PLAN_CLAIM_KEYS, path)
    claim_id = _nonempty(claim["claim_id"], f"{path}.claim_id")
    if not CLAIM_ID_RE.fullmatch(claim_id):
        _fail(f"{path}.claim_id", "must match claim-<descriptive-slug>")
    for field in ("requirement", "support_test", "falsification_test"):
        _nonempty(claim[field], f"{path}.{field}")
    if claim["evidence_type"] not in EVIDENCE_TYPES:
        _fail(f"{path}.evidence_type", "unknown evidence type")
    if claim["coverage_requirement"] not in COVERAGE_REQUIREMENTS:
        _fail(f"{path}.coverage_requirement", "unknown coverage requirement")


def validate_plan(plan: Any, expected_mode: str | None = None) -> None:
    """Validate the exact PR #2322 initial or expansion plan contract."""
    plan = _strict(plan, PLAN_KEYS, "plan")
    if plan["plan_version"] != "2.0":
        _fail("plan.plan_version", "must be 2.0")
    if plan["mode"] not in ("initial", "expansion"):
        _fail("plan.mode", "must be initial or expansion")
    if expected_mode is not None and plan["mode"] != expected_mode:
        _fail("plan.mode", f"must be {expected_mode}")
    _nonempty(plan["question_id"], "plan.question_id")
    _nonempty(plan["question_text"], "plan.question_text")
    _nullable_string(plan["asset_id"], "plan.asset_id")
    claims = plan["claims"]
    if not isinstance(claims, list):
        _fail("plan.claims", "must be an array")
    if plan["mode"] == "initial":
        if not 1 <= len(claims) <= BUDGETS["max_initial_claims"]:
            _fail("plan.claims", "an initial plan must contain one or two claims")
    elif len(claims) != 1:
        _fail("plan.claims", "an expansion must contain exactly one claim")
    for index, claim in enumerate(claims):
        _validate_claim(claim, f"plan.claims[{index}]")
    ids = [claim["claim_id"] for claim in claims]
    if len(ids) != len(set(ids)):
        _fail("plan.claims", "claim IDs must be unique")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _observation_material(observation: Mapping[str, Any]) -> dict[str, Any]:
    source = observation["source"]
    identifiers: dict[str, Any] = {"type": source["type"]}
    if source["type"] == "memory":
        identifiers["job_id"] = source.get("job_id")
        identifiers["record_id"] = source["record_id"]
        identifiers["sensor_id"] = source.get("sensor_id")
        identifiers["start"] = source.get("start")
        identifiers["end"] = source.get("end")
    else:
        identifiers.update(
            job_id=source["job_id"],
            sensor_id=source["sensor_id"],
            start=source["start"],
            end=source["end"],
        )
    return {
        "claim_id": observation["claim_id"],
        "relation": observation["relation"],
        "text": " ".join(observation["text"].split()).casefold(),
        "source": identifiers,
    }


def observation_id(observation: Mapping[str, Any]) -> str:
    """Return the stable content-derived ID for an observation."""
    material = {
        key: value for key, value in observation.items() if key != "observation_id"
    }
    _validate_observation(
        {**material, "observation_id": "obs-" + "0" * 24}, check_id=False
    )
    return "obs-" + _digest(_observation_material(material))[:24]


def _validate_source(source: Any, path: str) -> None:
    if not isinstance(source, dict):
        _fail(path, "must be an object")
    source_type = source.get("type")
    if source_type == "memory":
        missing = MEMORY_SOURCE_REQUIRED - set(source)
        unknown = set(source) - MEMORY_SOURCE_REQUIRED - MEMORY_SOURCE_OPTIONAL
        if missing or unknown:
            if missing:
                _fail(path, f"missing fields: {', '.join(sorted(missing))}")
            _fail(path, f"unknown fields: {', '.join(sorted(unknown))}")
        _nonempty(source["record_id"], f"{path}.record_id")
        _nullable_string(source.get("job_id"), f"{path}.job_id")
        _nullable_string(source.get("sensor_id"), f"{path}.sensor_id")
        for field in ("start", "end"):
            if source.get(field) is not None:
                _timestamp(source[field], f"{path}.{field}")
    elif source_type == "vlm":
        source = _strict(source, VLM_SOURCE_KEYS, path)
        for field in ("job_id", "sensor_id"):
            _nonempty(source[field], f"{path}.{field}")
        _timestamp(source["start"], f"{path}.start")
        _timestamp(source["end"], f"{path}.end")
    else:
        _fail(f"{path}.type", "must be memory or vlm")


def _validate_observation(
    observation: Any, path: str = "observation", *, check_id: bool = True
) -> None:
    observation = _strict(observation, OBSERVATION_KEYS, path)
    if not CLAIM_ID_RE.fullmatch(str(observation["claim_id"])):
        _fail(f"{path}.claim_id", "invalid claim ID")
    if observation["relation"] not in RELATIONS:
        _fail(f"{path}.relation", "must be supports, contradicts, or context")
    _nonempty(observation["text"], f"{path}.text")
    _validate_source(observation["source"], f"{path}.source")
    if not OBSERVATION_ID_RE.fullmatch(str(observation["observation_id"])):
        _fail(f"{path}.observation_id", "must be a stable obs-<24 hex> ID")
    if check_id and observation["observation_id"] != observation_id(observation):
        _fail(f"{path}.observation_id", "does not match the deterministic content ID")


def _validate_claim_state(state: Any, path: str) -> None:
    state = _strict(state, CLAIM_STATE_KEYS, path)
    if not CLAIM_ID_RE.fullmatch(str(state["claim_id"])):
        _fail(f"{path}.claim_id", "invalid claim ID")
    if state["status"] not in CLAIM_STATUSES:
        _fail(f"{path}.status", "invalid claim status")
    if state["coverage"] not in COVERAGES:
        _fail(f"{path}.coverage", "invalid coverage")
    if not isinstance(state["observation_ids"], list):
        _fail(f"{path}.observation_ids", "must be an array")
    if len(state["observation_ids"]) != len(set(state["observation_ids"])):
        _fail(f"{path}.observation_ids", "must contain unique IDs")
    for observation in state["observation_ids"]:
        if not OBSERVATION_ID_RE.fullmatch(str(observation)):
            _fail(f"{path}.observation_ids", "contains an invalid observation ID")
    _nullable_string(state["gap"], f"{path}.gap")


def _derived_claim_status(
    claim_id: str,
    observation_ids: Sequence[str],
    observations: Mapping[str, Mapping[str, Any]],
) -> str:
    relations = {
        observations[item]["relation"]
        for item in observation_ids
        if item in observations and observations[item]["claim_id"] == claim_id
    }
    if "supports" in relations and "contradicts" not in relations:
        return "supported"
    if "contradicts" in relations and "supports" not in relations:
        return "contradicted"
    return "unresolved"


def validate_ledger(ledger: Any) -> None:
    """Strictly validate the canonical minimal ledger and cross references."""
    ledger = _strict(ledger, LEDGER_KEYS, "ledger")
    if ledger["ledger_version"] != "1.0":
        _fail("ledger.ledger_version", "must be 1.0")
    _integer(ledger["revision"], "ledger.revision")
    _integer(
        ledger["round"],
        "ledger.round",
        maximum=BUDGETS["max_inspection_rounds"],
    )
    _integer(
        ledger["expansions_used"],
        "ledger.expansions_used",
        maximum=BUDGETS["max_expansions"],
    )
    _integer(
        ledger["vlm_calls_used"],
        "ledger.vlm_calls_used",
        maximum=BUDGETS["max_total_vlm_calls"],
    )
    if ledger["status"] not in LEDGER_STATUSES:
        _fail("ledger.status", "invalid ledger status")
    if ledger["stop_reason"] not in STOP_REASONS:
        _fail("ledger.stop_reason", "invalid stop reason")

    plan = ledger["plan"]
    # A ledger plan can contain the initial claims plus one accepted expansion.
    _strict(plan, PLAN_KEYS, "ledger.plan")
    if plan["plan_version"] != "2.0" or plan["mode"] != "initial":
        _fail("ledger.plan", "must retain the initial PR #2322 plan identity")
    _nonempty(plan["question_id"], "ledger.plan.question_id")
    _nonempty(plan["question_text"], "ledger.plan.question_text")
    _nullable_string(plan["asset_id"], "ledger.plan.asset_id")
    plan_claims = plan["claims"]
    if (
        not isinstance(plan_claims, list)
        or not 1 <= len(plan_claims) <= BUDGETS["max_total_claims"]
    ):
        _fail("ledger.plan.claims", "must contain one to three claims")
    for index, claim in enumerate(plan_claims):
        _validate_claim(claim, f"ledger.plan.claims[{index}]")
    plan_ids = [claim["claim_id"] for claim in plan_claims]
    if len(plan_ids) != len(set(plan_ids)):
        _fail("ledger.plan.claims", "claim IDs must be unique")
    if (
        len(plan_claims) > BUDGETS["max_initial_claims"]
        and not ledger["expansions_used"]
    ):
        _fail("ledger.expansions_used", "must record the accepted expansion")

    states = ledger["claims"]
    if not isinstance(states, list) or len(states) != len(plan_claims):
        _fail("ledger.claims", "must contain exactly one state per plan claim")
    for index, state in enumerate(states):
        _validate_claim_state(state, f"ledger.claims[{index}]")
    state_ids = [state["claim_id"] for state in states]
    if state_ids != plan_ids:
        _fail("ledger.claims", "must match plan claim IDs and order")

    items = ledger["observations"]
    if not isinstance(items, list):
        _fail("ledger.observations", "must be an array")
    for index, item in enumerate(items):
        _validate_observation(item, f"ledger.observations[{index}]")
        if item["claim_id"] not in plan_ids:
            _fail(f"ledger.observations[{index}].claim_id", "unknown claim")
    observation_ids = [item["observation_id"] for item in items]
    if len(observation_ids) != len(set(observation_ids)):
        _fail("ledger.observations", "contains duplicate observation IDs")
    materials = [_digest(_observation_material(item)) for item in items]
    if len(materials) != len(set(materials)):
        _fail("ledger.observations", "contains duplicate observation content")
    observation_map = {item["observation_id"]: item for item in items}
    referenced: list[str] = []
    for index, state in enumerate(states):
        for item_id in state["observation_ids"]:
            item = observation_map.get(item_id)
            if item is None or item["claim_id"] != state["claim_id"]:
                _fail(
                    f"ledger.claims[{index}].observation_ids",
                    "references mismatched evidence",
                )
            referenced.append(item_id)
        expected = _derived_claim_status(
            state["claim_id"], state["observation_ids"], observation_map
        )
        if state["status"] != expected:
            _fail(f"ledger.claims[{index}].status", "does not match accepted evidence")
    if sorted(referenced) != sorted(observation_ids):
        _fail(
            "ledger.observations",
            "every accepted observation must be cited by its claim",
        )

    sufficient = _is_sufficient(ledger)
    if ledger["status"] == "answered" and (
        not sufficient or ledger["stop_reason"] != "resolved"
    ):
        _fail("ledger.status", "answered requires a resolved sufficient ledger")
    if ledger["status"] == "in_progress" and ledger["stop_reason"] is not None:
        _fail("ledger.stop_reason", "must be null while in progress")
    if (
        ledger["status"] == "unresolved"
        and ledger["stop_reason"] not in STOP_REASONS[2:]
    ):
        _fail("ledger.stop_reason", "unresolved requires a terminal unresolved reason")


def initialize_ledger(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Initialize a canonical ledger from a valid PR #2322 initial plan."""
    validate_plan(plan, "initial")
    ledger = {
        "ledger_version": "1.0",
        "revision": 0,
        "plan": copy.deepcopy(plan),
        "claims": [
            {
                "claim_id": claim["claim_id"],
                "status": "unresolved",
                "coverage": "none",
                "observation_ids": [],
                "gap": f"Evidence is needed to execute: {claim['support_test']}",
            }
            for claim in plan["claims"]
        ],
        "observations": [],
        "round": 0,
        "expansions_used": 0,
        "vlm_calls_used": 0,
        "status": "in_progress",
        "stop_reason": None,
    }
    validate_ledger(ledger)
    return ledger


def _claim(ledger: Mapping[str, Any], claim_id: str) -> Mapping[str, Any]:
    for claim in ledger["plan"]["claims"]:
        if claim["claim_id"] == claim_id:
            return claim
    _fail("claim_id", "unknown claim")
    raise AssertionError


def _state(ledger: Mapping[str, Any], claim_id: str) -> Mapping[str, Any]:
    for state in ledger["claims"]:
        if state["claim_id"] == claim_id:
            return state
    _fail("claim_id", "unknown claim")
    raise AssertionError


def validate_inspection_task(
    task: Any, ledger: Mapping[str, Any] | None = None
) -> None:
    """Validate a one-claim task, optionally against a frozen ledger."""
    task = _strict(task, TASK_KEYS, "task")
    if not TASK_ID_RE.fullmatch(str(task["task_id"])):
        _fail("task.task_id", "invalid task ID")
    _integer(task["base_revision"], "task.base_revision")
    _validate_claim(task["claim"], "task.claim")
    _nullable_string(task["gap"], "task.gap")
    if not isinstance(task["existing_observations"], list):
        _fail("task.existing_observations", "must be an array")
    for index, item in enumerate(task["existing_observations"]):
        _validate_observation(item, f"task.existing_observations[{index}]")
        if item["claim_id"] != task["claim"]["claim_id"]:
            _fail(
                f"task.existing_observations[{index}].claim_id",
                "must target assigned claim",
            )
    _nullable_string(task["asset_id"], "task.asset_id")
    _integer(
        task["max_vlm_calls"],
        "task.max_vlm_calls",
        1,
        BUDGETS["max_vlm_calls_per_subagent"],
    )
    match = re.fullmatch(
        r"inspect-(claim-[a-z0-9]+(?:-[a-z0-9]+)*)-r([1-9][0-9]*)", task["task_id"]
    )
    assert match is not None
    if match.group(1) != task["claim"]["claim_id"]:
        _fail("task.task_id", "does not match assigned claim")
    if ledger is None:
        return
    validate_ledger(ledger)
    if task["base_revision"] != ledger["revision"]:
        _fail("task.base_revision", "stale task")
    if int(match.group(2)) != ledger["round"] + 1:
        _fail("task.task_id", "must target the next inspection round")
    if task["claim"] != _claim(ledger, task["claim"]["claim_id"]):
        _fail("task.claim", "must preserve the complete plan claim unchanged")
    state = _state(ledger, task["claim"]["claim_id"])
    if task["gap"] != state["gap"]:
        _fail("task.gap", "does not match the canonical gap")
    expected = [
        item
        for item in ledger["observations"]
        if item["observation_id"] in state["observation_ids"]
    ]
    if task["existing_observations"] != expected:
        _fail("task.existing_observations", "does not match accepted claim evidence")
    if task["asset_id"] != ledger["plan"]["asset_id"]:
        _fail("task.asset_id", "does not match the plan")


def create_inspection_tasks(
    ledger: Mapping[str, Any],
    claim_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Create a bounded task batch without mutating canonical state."""
    validate_ledger(ledger)
    if ledger["status"] != "in_progress":
        _fail("ledger.status", "cannot create tasks for a terminal ledger")
    if ledger["round"] >= BUDGETS["max_inspection_rounds"]:
        _fail("ledger.round", "inspection round budget is exhausted")
    remaining = BUDGETS["max_total_vlm_calls"] - ledger["vlm_calls_used"]
    if remaining <= 0:
        _fail("ledger.vlm_calls_used", "global VLM-call budget is exhausted")
    unresolved = [
        state["claim_id"]
        for state in ledger["claims"]
        if state["status"] == "unresolved"
    ]
    selected = list(claim_ids) if claim_ids is not None else unresolved
    if not selected:
        _fail("claim_ids", "must select at least one unresolved claim")
    if len(selected) != len(set(selected)):
        _fail("claim_ids", "must not contain duplicates")
    if len(selected) > BUDGETS["max_parallel_subagents"]:
        _fail("claim_ids", "parallel subagent limit exceeded")
    if any(claim_id not in unresolved for claim_id in selected):
        _fail("claim_ids", "tasks may target only unresolved claims")
    if len(selected) > remaining:
        selected = selected[:remaining]
    observations = {item["observation_id"]: item for item in ledger["observations"]}
    tasks: list[dict[str, Any]] = []
    for index, claim_id in enumerate(selected):
        slots = len(selected) - index
        allocation = min(
            BUDGETS["max_vlm_calls_per_subagent"],
            remaining - (slots - 1),
        )
        remaining -= allocation
        state = _state(ledger, claim_id)
        task = {
            "task_id": f"inspect-{claim_id}-r{ledger['round'] + 1}",
            "base_revision": ledger["revision"],
            "claim": copy.deepcopy(_claim(ledger, claim_id)),
            "gap": state["gap"],
            "existing_observations": [
                copy.deepcopy(observations[item_id])
                for item_id in state["observation_ids"]
            ],
            "asset_id": ledger["plan"]["asset_id"],
            "max_vlm_calls": allocation,
        }
        validate_inspection_task(task, ledger)
        tasks.append(task)
    return tasks


def validate_inspection_result(
    result: Any, task: Mapping[str, Any] | None = None
) -> None:
    """Validate a small subagent result; it never carries canonical state."""
    result = _strict(result, RESULT_KEYS, "result")
    if not TASK_ID_RE.fullmatch(str(result["task_id"])):
        _fail("result.task_id", "invalid task ID")
    _integer(result["base_revision"], "result.base_revision")
    if not isinstance(result["observations"], list):
        _fail("result.observations", "must be an array")
    for index, item in enumerate(result["observations"]):
        _validate_observation(item, f"result.observations[{index}]")
        if item["source"]["type"] != "vlm":
            _fail(f"result.observations[{index}].source.type", "must be vlm")
    if result["coverage"] not in COVERAGES:
        _fail("result.coverage", "invalid coverage")
    _nullable_string(result["gap"], "result.gap")
    _integer(
        result["vlm_calls_used"],
        "result.vlm_calls_used",
        maximum=BUDGETS["max_vlm_calls_per_subagent"],
    )
    _nullable_string(result["error"], "result.error")
    if task is None:
        return
    validate_inspection_task(task)
    if result["task_id"] != task["task_id"]:
        _fail("result.task_id", "does not match assigned task")
    if result["base_revision"] != task["base_revision"]:
        _fail("result.base_revision", "does not match the frozen task revision")
    if result["vlm_calls_used"] > task["max_vlm_calls"]:
        _fail("result.vlm_calls_used", "exceeds task allocation")
    claim_id = task["claim"]["claim_id"]
    if any(item["claim_id"] != claim_id for item in result["observations"]):
        _fail("result.observations", "every observation must target the assigned claim")


def _append_observations(
    ledger: dict[str, Any], observations: Sequence[Mapping[str, Any]]
) -> int:
    known = {
        _digest(_observation_material(item)): item for item in ledger["observations"]
    }
    candidates = sorted(
        (copy.deepcopy(item) for item in observations),
        key=lambda item: (_digest(_observation_material(item)), item["observation_id"]),
    )
    added = 0
    for item in candidates:
        material = _digest(_observation_material(item))
        if material in known:
            continue
        item["observation_id"] = "obs-" + material[:24]
        ledger["observations"].append(item)
        state = _state(ledger, item["claim_id"])
        state["observation_ids"].append(item["observation_id"])
        known[material] = item
        added += 1
    ledger["observations"].sort(key=lambda item: item["observation_id"])
    observation_map = {item["observation_id"]: item for item in ledger["observations"]}
    for state in ledger["claims"]:
        state["observation_ids"].sort()
        state["status"] = _derived_claim_status(
            state["claim_id"], state["observation_ids"], observation_map
        )
    return added


def merge_memory(
    ledger: Mapping[str, Any],
    updates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind memory evidence in one canonical revision before inspection."""
    validate_ledger(ledger)
    if (
        not isinstance(updates, Sequence)
        or isinstance(updates, (str, bytes))
        or not updates
    ):
        _fail("updates", "must contain at least one claim update")
    allowed = {"claim_id", "observations", "coverage", "gap"}
    prepared: list[Mapping[str, Any]] = []
    for index, update in enumerate(updates):
        update = _strict(update, allowed, f"updates[{index}]")
        state = _state(ledger, update["claim_id"])
        if update["coverage"] not in COVERAGES:
            _fail(f"updates[{index}].coverage", "invalid coverage")
        _nullable_string(update["gap"], f"updates[{index}].gap")
        if not isinstance(update["observations"], list):
            _fail(f"updates[{index}].observations", "must be an array")
        for item_index, item in enumerate(update["observations"]):
            _validate_observation(item, f"updates[{index}].observations[{item_index}]")
            if item["claim_id"] != state["claim_id"]:
                _fail(
                    f"updates[{index}].observations[{item_index}].claim_id",
                    "wrong claim",
                )
            if item["source"]["type"] != "memory":
                _fail(
                    f"updates[{index}].observations[{item_index}].source.type",
                    "must be memory",
                )
        prepared.append(update)
    updated = copy.deepcopy(ledger)
    for update in prepared:
        _append_observations(updated, update["observations"])
        state = _state(updated, update["claim_id"])
        if COVERAGES.index(update["coverage"]) > COVERAGES.index(state["coverage"]):
            state["coverage"] = update["coverage"]
        state["gap"] = update["gap"]
    updated["revision"] += 1
    if _is_sufficient(updated):
        updated["status"] = "answered"
        updated["stop_reason"] = "resolved"
    validate_ledger(updated)
    return updated


def _is_sufficient(ledger: Mapping[str, Any]) -> bool:
    observations = {item["observation_id"]: item for item in ledger["observations"]}
    for state in ledger["claims"]:
        if state["status"] not in ("supported", "contradicted"):
            return False
        if state["coverage"] != "sufficient" or not state["observation_ids"]:
            return False
        relations = {
            observations[item_id]["relation"] for item_id in state["observation_ids"]
        }
        if "supports" in relations and "contradicts" in relations:
            return False
    return True


def assess_sufficiency(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Return deterministic gate details for every claim."""
    validate_ledger(ledger)
    observations = {item["observation_id"]: item for item in ledger["observations"]}
    claims = []
    for state in ledger["claims"]:
        relations = {
            observations[item_id]["relation"] for item_id in state["observation_ids"]
        }
        claims.append(
            {
                "claim_id": state["claim_id"],
                "resolved": state["status"] in ("supported", "contradicted"),
                "coverage_sufficient": state["coverage"] == "sufficient",
                "has_observation": bool(state["observation_ids"]),
                "conflicting": "supports" in relations and "contradicts" in relations,
            }
        )
    return {"sufficient": _is_sufficient(ledger), "claims": claims}


def merge_round_results(
    ledger: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Merge one frozen parallel round into exactly one new revision."""
    validate_ledger(ledger)
    if ledger["status"] != "in_progress":
        _fail("ledger.status", "cannot merge into a terminal ledger")
    if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes)) or not tasks:
        _fail("tasks", "must contain at least one assigned task")
    if len(tasks) > BUDGETS["max_parallel_subagents"]:
        _fail("tasks", "parallel subagent limit exceeded")
    task_map: dict[str, Mapping[str, Any]] = {}
    for index, task in enumerate(tasks):
        validate_inspection_task(task, ledger)
        if task["task_id"] in task_map:
            _fail(f"tasks[{index}].task_id", "duplicate task")
        task_map[task["task_id"]] = task
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        _fail("results", "must be an array")
    if len(results) != len(tasks):
        _fail("results", "must contain exactly one result per assigned task")
    result_map: dict[str, Mapping[str, Any]] = {}
    for index, result in enumerate(results):
        task = task_map.get(result.get("task_id") if isinstance(result, dict) else None)
        if task is None:
            _fail(f"results[{index}].task_id", "unknown task")
        validate_inspection_result(result, task)
        if result["task_id"] in result_map:
            _fail(f"results[{index}].task_id", "duplicate result")
        result_map[result["task_id"]] = result
    if set(result_map) != set(task_map):
        _fail("results", "missing assigned task result")
    calls = sum(result["vlm_calls_used"] for result in results)
    if ledger["vlm_calls_used"] + calls > BUDGETS["max_total_vlm_calls"]:
        _fail("results", "global VLM-call cap exceeded")

    updated = copy.deepcopy(ledger)
    coverage_improved = False
    accepted = 0
    for task_id in sorted(task_map):
        result = result_map[task_id]
        accepted += _append_observations(updated, result["observations"])
        state = _state(updated, task_map[task_id]["claim"]["claim_id"])
        if COVERAGES.index(result["coverage"]) > COVERAGES.index(state["coverage"]):
            state["coverage"] = result["coverage"]
            coverage_improved = True
        state["gap"] = result["gap"]
    updated["vlm_calls_used"] += calls
    updated["round"] += 1
    updated["revision"] += 1

    all_failed = all(result["error"] is not None for result in results)
    if _is_sufficient(updated):
        updated["status"] = "answered"
        updated["stop_reason"] = "resolved"
    elif all_failed and not updated["observations"]:
        updated["status"] = "unresolved"
        updated["stop_reason"] = "tool_failure"
    elif accepted == 0 and not coverage_improved:
        updated["status"] = "unresolved"
        updated["stop_reason"] = "no_progress"
    elif (
        updated["round"] >= BUDGETS["max_inspection_rounds"]
        or updated["vlm_calls_used"] >= BUDGETS["max_total_vlm_calls"]
    ):
        updated["status"] = "unresolved"
        updated["stop_reason"] = "budget_exhausted"
    validate_ledger(updated)
    return updated


def expand_ledger(
    ledger: Mapping[str, Any], expansion_plan: Mapping[str, Any]
) -> dict[str, Any]:
    """Accept one PR #2322 expansion while preserving prior claims and evidence."""
    validate_ledger(ledger)
    validate_plan(expansion_plan, "expansion")
    if ledger["expansions_used"] >= BUDGETS["max_expansions"]:
        _fail("ledger.expansions_used", "at most one expansion is permitted")
    if len(ledger["plan"]["claims"]) >= BUDGETS["max_total_claims"]:
        _fail("ledger.plan.claims", "total POC claim limit reached")
    for field in ("question_id", "question_text", "asset_id"):
        if expansion_plan[field] != ledger["plan"][field]:
            _fail(f"plan.{field}", "must match the initial plan")
    new_claim = expansion_plan["claims"][0]
    if new_claim["claim_id"] in {
        claim["claim_id"] for claim in ledger["plan"]["claims"]
    }:
        _fail("plan.claims[0].claim_id", "must be a new stable claim ID")
    updated = copy.deepcopy(ledger)
    updated["plan"]["claims"].append(copy.deepcopy(new_claim))
    updated["claims"].append(
        {
            "claim_id": new_claim["claim_id"],
            "status": "unresolved",
            "coverage": "none",
            "observation_ids": [],
            "gap": f"Evidence is needed to execute: {new_claim['support_test']}",
        }
    )
    updated["expansions_used"] += 1
    updated["revision"] += 1
    updated["status"] = "in_progress"
    updated["stop_reason"] = None
    validate_ledger(updated)
    return updated


def evaluate_stop(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate deterministic sufficiency and remaining budget."""
    validate_ledger(ledger)
    if _is_sufficient(ledger):
        return {"stop": True, "status": "answered", "reason": "resolved"}
    if ledger["status"] == "unresolved":
        return {
            "stop": True,
            "status": "unresolved",
            "reason": ledger["stop_reason"],
        }
    exhausted = (
        ledger["round"] >= BUDGETS["max_inspection_rounds"]
        or ledger["vlm_calls_used"] >= BUDGETS["max_total_vlm_calls"]
    )
    if exhausted:
        return {"stop": True, "status": "unresolved", "reason": "budget_exhausted"}
    return {"stop": False, "status": "in_progress", "reason": None}


def apply_budget_stop(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Persist a budget stop when no further task can be allocated."""
    decision = evaluate_stop(ledger)
    if decision["reason"] != "budget_exhausted":
        return copy.deepcopy(ledger)
    updated = copy.deepcopy(ledger)
    updated["status"] = "unresolved"
    updated["stop_reason"] = "budget_exhausted"
    updated["revision"] += 1
    validate_ledger(updated)
    return updated


def final_result(
    ledger: Mapping[str, Any], answer: str | None = None
) -> dict[str, Any]:
    """Build the required small answered or unresolved handoff object."""
    validate_ledger(ledger)
    if ledger["status"] == "answered":
        answer = _nonempty(answer, "answer")
        evidence = sorted(
            {
                item_id
                for state in ledger["claims"]
                for item_id in state["observation_ids"]
            }
        )
        return {
            "status": "answered",
            "answer": answer,
            "evidence": evidence,
            "unresolved_gaps": [],
        }
    if answer is not None:
        _fail("answer", "must be null for an unresolved ledger")
    gaps = []
    for state in ledger["claims"]:
        if (
            state["status"] in ("supported", "contradicted")
            and state["coverage"] == "sufficient"
        ):
            continue
        if ledger["stop_reason"] == "tool_failure":
            reason = "tool_failure"
        elif ledger["stop_reason"] == "budget_exhausted":
            reason = "budget_exhausted"
        elif state["gap"] and any(
            word in state["gap"].casefold()
            for word in ("not visible", "occluded", "blurred")
        ):
            reason = "not_visible"
        else:
            reason = "insufficient_coverage"
        gaps.append(
            {
                "claim_id": state["claim_id"],
                "gap": state["gap"] or "The claim remains unresolved.",
                "reason": reason,
            }
        )
    return {
        "status": "unresolved",
        "answer": None,
        "evidence": [],
        "unresolved_gaps": gaps,
    }


def atomic_write(path: str | os.PathLike[str], value: Any) -> None:
    """Serialize JSON and atomically replace the destination."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _read(path: str | os.PathLike[str]) -> Any:
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def _emit(value: Any, output: str | None) -> None:
    if output:
        atomic_write(output, value)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--plan", required=True)
    init.add_argument("--output")
    validate = commands.add_parser("validate")
    validate.add_argument("--kind", choices=("ledger", "task", "result"), required=True)
    validate.add_argument("--input", required=True)
    validate.add_argument("--ledger")
    validate.add_argument("--task")
    create = commands.add_parser("create-tasks")
    create.add_argument("--ledger", required=True)
    create.add_argument("--claim-id", action="append")
    create.add_argument("--output", required=True)
    memory = commands.add_parser("merge-memory")
    memory.add_argument("--ledger", required=True)
    memory.add_argument("--updates", required=True)
    memory.add_argument("--output")
    merge = commands.add_parser("merge-round")
    merge.add_argument("--ledger", required=True)
    merge.add_argument("--tasks", required=True)
    merge.add_argument("--results", required=True)
    merge.add_argument("--output")
    expand = commands.add_parser("expand")
    expand.add_argument("--ledger", required=True)
    expand.add_argument("--plan", required=True)
    expand.add_argument("--output")
    assess = commands.add_parser("assess")
    assess.add_argument("--ledger", required=True)
    finish = commands.add_parser("final-result")
    finish.add_argument("--ledger", required=True)
    finish.add_argument("--answer")
    finish.add_argument("--output")
    identifier = commands.add_parser("observation-id")
    identifier.add_argument("--observation", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the standard-library command interface."""
    args = _parser().parse_args(argv)
    if args.command == "init":
        _emit(initialize_ledger(_read(args.plan)), args.output)
    elif args.command == "validate":
        value = _read(args.input)
        if args.kind == "ledger":
            validate_ledger(value)
        elif args.kind == "task":
            validate_inspection_task(value, _read(args.ledger) if args.ledger else None)
        else:
            validate_inspection_result(value, _read(args.task) if args.task else None)
        print("valid")
    elif args.command == "create-tasks":
        _emit(create_inspection_tasks(_read(args.ledger), args.claim_id), args.output)
    elif args.command == "merge-memory":
        _emit(merge_memory(_read(args.ledger), _read(args.updates)), args.output)
    elif args.command == "merge-round":
        _emit(
            merge_round_results(
                _read(args.ledger), _read(args.tasks), _read(args.results)
            ),
            args.output,
        )
    elif args.command == "expand":
        _emit(expand_ledger(_read(args.ledger), _read(args.plan)), args.output)
    elif args.command == "assess":
        ledger = _read(args.ledger)
        _emit(
            {"sufficiency": assess_sufficiency(ledger), "stop": evaluate_stop(ledger)},
            None,
        )
    elif args.command == "final-result":
        _emit(final_result(_read(args.ledger), args.answer), args.output)
    else:
        print(observation_id(_read(args.observation)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LedgerValidationError as error:
        raise SystemExit(f"error: {error}") from error
