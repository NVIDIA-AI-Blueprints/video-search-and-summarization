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
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
BUDGET_PATH = ROOT / "config" / "ledger-budgets.json"
ATTEMPT_CONTEXT_ENV = "VSS_INTROSPECTION_ATTEMPT_CONTEXT"
DEFAULT_ATTEMPT_CONTEXT_PATH = Path(
    "/sandbox/.openclaw/workspace/.vss/introspection-attempt.json"
)

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
    "media_scope",
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
VLM_SENSOR_SOURCE_KEYS = {"type", "job_id", "sensor_id", "start", "end"}
VLM_MEDIA_URL_SOURCE_KEYS = {"type", "job_id", "media_url"}
VLM_FILE_SOURCE_KEYS = {"type", "job_id", "path"}
SENSOR_SCOPE_KEYS = {"type", "sensor_id", "start", "end"}
MEDIA_URL_SCOPE_KEYS = {"type", "media_url"}
FILE_SCOPE_KEYS = {"type", "path"}
CLAIM_ID_RE = re.compile(r"^claim-[a-z0-9]+(?:-[a-z0-9]+)*$")
TASK_ID_RE = re.compile(r"^inspect-claim-[a-z0-9]+(?:-[a-z0-9]+)*-r[1-9][0-9]*$")
OBSERVATION_ID_RE = re.compile(r"^obs-[a-f0-9]{24}$")
ANSWER_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class LedgerValidationError(ValueError):
    """Raised when a contract or state transition is invalid."""


def _fail(path: str, message: str) -> None:
    raise LedgerValidationError(f"{path}: {message}")


def _choice_label(value: Any, path: str, *, required: bool) -> str | None:
    """Normalize a question's own choice label, or accept none for an open question."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            _fail(path, "must name one of the question's choices")
        return None
    if not isinstance(value, str) or not ANSWER_LABEL_RE.fullmatch(value.strip()):
        _fail(path, "must be one short choice label, not the explanation")
    label = value.strip()
    return label.upper() if re.fullmatch(r"[A-Za-z]", label) else label


def _render_answer(label: str | None, explanation: str) -> str:
    return f"{label}. {explanation}" if label else explanation


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


def _parse_timestamp(value: Any, path: str) -> datetime:
    value = _nonempty(value, path)
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise LedgerValidationError(f"{path}: must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(path, "must include a timezone")
    return parsed


def _validate_window(start: Any, end: Any, path: str) -> None:
    start_time = _parse_timestamp(start, f"{path}.start")
    end_time = _parse_timestamp(end, f"{path}.end")
    if start_time >= end_time:
        _fail(path, "start must be strictly earlier than end")


def load_budgets(path: str | os.PathLike[str] = BUDGET_PATH) -> dict[str, int]:
    """Load and validate the authoritative introspection budget document."""
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
    """Validate the exact initial or expansion evidence-plan contract."""
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
        identifiers["job_id"] = source["job_id"]
        for field in ("sensor_id", "start", "end", "media_url", "path"):
            if field in source:
                identifiers[field] = source[field]
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


def canonicalize_result_observation_ids(result: Any) -> Any:
    """Repair only externally supplied observation IDs from canonical content."""
    if not isinstance(result, dict) or not isinstance(result.get("observations"), list):
        return copy.deepcopy(result)
    repaired = copy.deepcopy(result)
    for item in repaired["observations"]:
        if isinstance(item, dict):
            item["observation_id"] = observation_id(item)
    return repaired


def canonicalize_memory_update_observation_ids(updates: Any) -> Any:
    """Repair only observation IDs in externally supplied memory updates."""
    if not isinstance(updates, Sequence) or isinstance(updates, (str, bytes)):
        return copy.deepcopy(updates)
    repaired = copy.deepcopy(list(updates))
    for update in repaired:
        if not isinstance(update, dict) or not isinstance(update.get("observations"), list):
            continue
        for item in update["observations"]:
            if isinstance(item, dict):
                item["observation_id"] = observation_id(item)
    return repaired


def _validate_media_url(value: Any, path: str) -> None:
    media_url = _nonempty(value, path)
    parsed = urlsplit(media_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        _fail(path, "must be an absolute HTTP(S) URL")


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
        has_start = source.get("start") is not None
        has_end = source.get("end") is not None
        if has_start != has_end:
            _fail(path, "memory start and end must be provided together")
        if has_start:
            _validate_window(source["start"], source["end"], path)
    elif source_type == "vlm":
        if "sensor_id" in source or "start" in source or "end" in source:
            source = _strict(source, VLM_SENSOR_SOURCE_KEYS, path)
            for field in ("job_id", "sensor_id"):
                _nonempty(source[field], f"{path}.{field}")
            _validate_window(source["start"], source["end"], path)
        elif "media_url" in source:
            source = _strict(source, VLM_MEDIA_URL_SOURCE_KEYS, path)
            _nonempty(source["job_id"], f"{path}.job_id")
            _validate_media_url(source["media_url"], f"{path}.media_url")
        elif "path" in source:
            source = _strict(source, VLM_FILE_SOURCE_KEYS, path)
            _nonempty(source["job_id"], f"{path}.job_id")
            _nonempty(source["path"], f"{path}.path")
        else:
            _fail(path, "vlm source must identify a sensor window, media URL, or file")
    else:
        _fail(f"{path}.type", "must be memory or vlm")


def _validate_media_scope(scope: Any, path: str = "media_scope") -> None:
    if not isinstance(scope, dict):
        _fail(path, "must be an object")
    scope_type = scope.get("type")
    if scope_type == "sensor":
        scope = _strict(scope, SENSOR_SCOPE_KEYS, path)
        _nonempty(scope["sensor_id"], f"{path}.sensor_id")
        _validate_window(scope["start"], scope["end"], path)
    elif scope_type == "media_url":
        scope = _strict(scope, MEDIA_URL_SCOPE_KEYS, path)
        _validate_media_url(scope["media_url"], f"{path}.media_url")
    elif scope_type == "file":
        scope = _strict(scope, FILE_SCOPE_KEYS, path)
        _nonempty(scope["path"], f"{path}.path")
    else:
        _fail(f"{path}.type", "must be sensor, media_url, or file")


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
    # A ledger plan can contain the initial claims plus accepted expansions.
    _strict(plan, PLAN_KEYS, "ledger.plan")
    if plan["plan_version"] != "2.0" or plan["mode"] != "initial":
        _fail("ledger.plan", "must retain the initial evidence-plan identity")
    _nonempty(plan["question_id"], "ledger.plan.question_id")
    _nonempty(plan["question_text"], "ledger.plan.question_text")
    _nullable_string(plan["asset_id"], "ledger.plan.asset_id")
    plan_claims = plan["claims"]
    if (
        not isinstance(plan_claims, list)
        or not 1 <= len(plan_claims) <= BUDGETS["max_total_claims"]
    ):
        _fail(
            "ledger.plan.claims",
            f"must contain one to {BUDGETS['max_total_claims']} claims",
        )
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
    """Initialize a canonical ledger from a valid initial evidence plan."""
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
    _validate_media_scope(task["media_scope"], "task.media_scope")
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


def _needs_inspection(state: Mapping[str, Any]) -> bool:
    """Keep a claim eligible until it is resolved with sufficient coverage.

    Derived status becomes ``supported`` or ``contradicted`` as soon as one
    matching observation is accepted. Partial coverage is not completion, so
    that claim stays eligible for another evidence task while budget remains.
    """
    if state["status"] not in ("supported", "contradicted"):
        return True
    return state["coverage"] != "sufficient"


def _budget_exhausted(ledger: Mapping[str, Any]) -> bool:
    return (
        ledger["round"] >= BUDGETS["max_inspection_rounds"]
        or ledger["vlm_calls_used"] >= BUDGETS["max_total_vlm_calls"]
    )


def create_inspection_tasks(
    ledger: Mapping[str, Any],
    media_scopes: Mapping[str, Mapping[str, Any]],
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
    eligible = [
        state["claim_id"] for state in ledger["claims"] if _needs_inspection(state)
    ]
    selected = list(claim_ids) if claim_ids is not None else eligible
    if not selected:
        _fail("claim_ids", "must select at least one claim that still needs evidence")
    if len(selected) != len(set(selected)):
        _fail("claim_ids", "must not contain duplicates")
    if len(selected) > BUDGETS["max_parallel_subagents"]:
        _fail("claim_ids", "parallel subagent limit exceeded")
    if any(claim_id not in eligible for claim_id in selected):
        _fail("claim_ids", "tasks may target only claims that still need evidence")
    if not isinstance(media_scopes, dict):
        _fail("media_scopes", "must be an object keyed by selected claim ID")
    if set(media_scopes) != set(selected):
        _fail("media_scopes", "must contain exactly one scope per selected claim")
    for claim_id in selected:
        _validate_media_scope(media_scopes[claim_id], f"media_scopes.{claim_id}")
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
            "media_scope": copy.deepcopy(media_scopes[claim_id]),
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
    scope = task["media_scope"]
    for index, item in enumerate(result["observations"]):
        source = item["source"]
        source_path = f"result.observations[{index}].source"
        if scope["type"] == "sensor":
            if "sensor_id" not in source:
                _fail(source_path, "does not match assigned sensor scope")
            if source["sensor_id"] != scope["sensor_id"]:
                _fail(
                    f"{source_path}.sensor_id",
                    "does not match assigned media scope",
                )
            scope_start = _parse_timestamp(scope["start"], "task.media_scope.start")
            scope_end = _parse_timestamp(scope["end"], "task.media_scope.end")
            start = _parse_timestamp(source["start"], f"{source_path}.start")
            end = _parse_timestamp(source["end"], f"{source_path}.end")
            if start < scope_start or end > scope_end:
                _fail(source_path, "window falls outside assigned media scope")
        elif scope["type"] == "media_url":
            if source.get("media_url") != scope["media_url"]:
                _fail(source_path, "does not match assigned media URL scope")
        elif source.get("path") != scope["path"]:
            _fail(source_path, "does not match assigned file scope")


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
    if ledger["status"] != "in_progress":
        _fail("ledger.status", "cannot merge memory into a terminal ledger")
    if (
        not isinstance(updates, Sequence)
        or isinstance(updates, (str, bytes))
        or not updates
    ):
        _fail("updates", "must contain at least one claim update")
    updates = canonicalize_memory_update_observation_ids(updates)
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
    normalized_results = [
        canonicalize_result_observation_ids(result) for result in results
    ]
    result_map: dict[str, Mapping[str, Any]] = {}
    for index, result in enumerate(normalized_results):
        task = task_map.get(result.get("task_id") if isinstance(result, dict) else None)
        if task is None:
            _fail(f"results[{index}].task_id", "unknown task")
        validate_inspection_result(result, task)
        if result["task_id"] in result_map:
            _fail(f"results[{index}].task_id", "duplicate result")
        result_map[result["task_id"]] = result
    if set(result_map) != set(task_map):
        _fail("results", "missing assigned task result")
    calls = sum(result["vlm_calls_used"] for result in normalized_results)
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

    all_failed = all(result["error"] is not None for result in normalized_results)
    if _is_sufficient(updated):
        updated["status"] = "answered"
        updated["stop_reason"] = "resolved"
    elif all_failed and not updated["observations"]:
        updated["status"] = "unresolved"
        updated["stop_reason"] = "tool_failure"
    elif accepted == 0 and not coverage_improved:
        updated["status"] = "unresolved"
        updated["stop_reason"] = "no_progress"
    elif _budget_exhausted(updated):
        updated["status"] = "unresolved"
        updated["stop_reason"] = "budget_exhausted"
    validate_ledger(updated)
    return updated


def collect_inspection_results(
    tasks: Sequence[Mapping[str, Any]],
    results_dir: str | os.PathLike[str],
) -> list[dict[str, Any]]:
    """Collect one persisted result per task in deterministic task-ID order."""
    directory = Path(results_dir)
    if not directory.is_dir():
        _fail("results_dir", "must be an existing directory")
    task_map: dict[str, Mapping[str, Any]] = {}
    for index, task in enumerate(tasks):
        validate_inspection_task(task)
        task_id = task["task_id"]
        if task_id in task_map:
            _fail(f"tasks[{index}].task_id", "duplicate task")
        task_map[task_id] = task
    collected: list[dict[str, Any]] = []
    missing: list[str] = []
    for task_id in sorted(task_map):
        claim_id = task_map[task_id]["claim"]["claim_id"]
        exact = directory / f"result-{claim_id}.json"
        candidates = [exact] if exact.is_file() else sorted(
            directory.glob(f"*-result-{claim_id}.json")
        )
        if not candidates:
            missing.append(task_id)
            continue
        if len(candidates) > 1:
            _fail("results_dir", f"multiple result files found for {task_id}")
        value = canonicalize_result_observation_ids(_read(candidates[0]))
        validate_inspection_result(value, task_map[task_id])
        if value != _read(candidates[0]):
            atomic_write(candidates[0], value)
        collected.append(value)
    if missing:
        _fail("results_dir", f"missing results for: {', '.join(missing)}")
    return collected


def resume_round_merge(
    base_ledger: Mapping[str, Any],
    current_ledger: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Merge from a frozen base, or return an already-applied merge unchanged."""
    validate_ledger(base_ledger)
    validate_ledger(current_ledger)
    expected = merge_round_results(base_ledger, tasks, results)
    if current_ledger == base_ledger:
        return expected
    if current_ledger == expected:
        return copy.deepcopy(current_ledger)
    _fail(
        "ledger",
        "current ledger is neither the frozen base nor its deterministic merged revision",
    )


def expand_ledger(
    ledger: Mapping[str, Any], expansion_plan: Mapping[str, Any]
) -> dict[str, Any]:
    """Accept one expansion while preserving prior claims and evidence."""
    validate_ledger(ledger)
    if ledger["status"] != "in_progress":
        _fail("ledger.status", "cannot expand a terminal ledger")
    validate_plan(expansion_plan, "expansion")
    if ledger["expansions_used"] >= BUDGETS["max_expansions"]:
        _fail(
            "ledger.expansions_used",
            f"at most {BUDGETS['max_expansions']} expansions are permitted",
        )
    if len(ledger["plan"]["claims"]) >= BUDGETS["max_total_claims"]:
        _fail("ledger.plan.claims", "configured total claim limit reached")
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
    if _budget_exhausted(ledger):
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


def prepare_for_final_result(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Return a terminal ledger, applying an explicit budget stop when required.

    An in-progress ledger with remaining budget is refused. The caller must
    create another evidence task for every claim that still needs coverage.
    This never invents an answer.
    """
    validate_ledger(ledger)
    if ledger["status"] != "in_progress":
        return copy.deepcopy(ledger)
    if evaluate_stop(ledger)["reason"] != "budget_exhausted":
        _fail(
            "ledger.status",
            "cannot finalize an in-progress ledger while inspection budget remains",
        )
    return apply_budget_stop(ledger)


def final_result(
    ledger: Mapping[str, Any],
    artifact_dir: str,
    answer_label: str | None = None,
    answer_explanation: str | None = None,
) -> dict[str, Any]:
    """Build an answered or unresolved handoff with self-contained provenance."""
    ledger = prepare_for_final_result(ledger)
    if ledger["status"] == "in_progress":
        _fail("ledger.status", "cannot finalize an in-progress ledger")
    artifact_dir = _nonempty(artifact_dir, "artifact_dir")
    observation_map = {item["observation_id"]: item for item in ledger["observations"]}
    if ledger["status"] == "answered":
        answer_label = _choice_label(answer_label, "answer_label", required=False)
        answer_explanation = _nonempty(answer_explanation, "answer_explanation")
        evidence = sorted(
            {
                item_id
                for state in ledger["claims"]
                for item_id in state["observation_ids"]
            }
        )
        return {
            "status": "answered",
            "evidence_status": "resolved",
            "answer_label": answer_label,
            "answer_explanation": answer_explanation,
            "answer": _render_answer(answer_label, answer_explanation),
            "decision_source": "introspection",
            "evidence": evidence,
            "evidence_details": [
                copy.deepcopy(observation_map[item_id]) for item_id in evidence
            ],
            "unresolved_gaps": [],
            "revision": ledger["revision"],
            "artifact_dir": artifact_dir,
        }
    if answer_label is not None or answer_explanation is not None:
        answer_label = _choice_label(answer_label, "answer_label", required=True)
        answer_explanation = _nonempty(answer_explanation, "answer_explanation")
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
    if answer_label is not None:
        evidence = sorted(observation_map)
        return {
            "status": "answered",
            "evidence_status": "unresolved",
            "answer_label": answer_label,
            "answer_explanation": answer_explanation,
            "answer": _render_answer(answer_label, answer_explanation),
            "decision_source": "best_available_choice",
            "evidence": evidence,
            "evidence_details": [
                copy.deepcopy(observation_map[item_id]) for item_id in evidence
            ],
            "unresolved_gaps": gaps,
            "revision": ledger["revision"],
            "artifact_dir": artifact_dir,
        }
    return {
        "status": "unresolved",
        "evidence_status": "unresolved",
        "answer_label": None,
        "answer_explanation": None,
        "answer": None,
        "decision_source": "abstention",
        "evidence": [],
        "evidence_details": [],
        "unresolved_gaps": gaps,
        "revision": ledger["revision"],
        "artifact_dir": artifact_dir,
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


def validate_terminal_pair(
    ledger: Mapping[str, Any], terminal_result: Mapping[str, Any]
) -> None:
    """Validate a revision-matched terminal ledger and final-result pair."""
    validate_ledger(ledger)
    if ledger["status"] not in ("answered", "unresolved"):
        _fail("ledger.status", "terminal commit requires a terminal ledger")
    if terminal_result.get("revision") != ledger["revision"]:
        _fail("final_result.revision", "must match terminal ledger revision")
    expected_evidence_status = (
        "resolved" if ledger["status"] == "answered" else "unresolved"
    )
    if terminal_result.get("evidence_status") != expected_evidence_status:
        _fail(
            "final_result.evidence_status",
            "must reflect the terminal ledger evidence status",
        )
    status = terminal_result.get("status")
    label = terminal_result.get("answer_label")
    explanation = terminal_result.get("answer_explanation")
    if status == "answered":
        if label is not None:
            _choice_label(label, "final_result.answer_label", required=True)
        _nonempty(explanation, "final_result.answer_explanation")
        if ledger["status"] == "unresolved":
            if terminal_result.get("decision_source") != "best_available_choice":
                _fail(
                    "final_result.decision_source",
                    "an unresolved ledger may answer only as a best-available choice",
                )
    elif status == "unresolved":
        if ledger["status"] != "unresolved":
            _fail("final_result.status", "a resolved ledger must produce an answer")
        if label is not None or explanation is not None:
            _fail(
                "final_result.answer_label",
                "an unresolved result cannot contain an answer",
            )
    else:
        _fail("final_result.status", "must be answered or unresolved")


def atomic_commit_terminal(
    ledger_path: str | os.PathLike[str],
    ledger: Mapping[str, Any],
    final_path: str | os.PathLike[str],
    terminal_result: Mapping[str, Any],
) -> None:
    """Commit a terminal pair and publish a revision/hash marker last."""
    validate_terminal_pair(ledger, terminal_result)
    ledger_destination = Path(ledger_path)
    final_destination = Path(final_path)
    if ledger_destination.resolve().parent != final_destination.resolve().parent:
        _fail("final_result", "ledger and final result must share an artifact directory")
    atomic_write(ledger_destination, ledger)
    atomic_write(final_destination, terminal_result)
    marker = {
        "status": terminal_result["status"],
        "evidence_status": terminal_result["evidence_status"],
        "ledger_status": ledger["status"],
        "revision": ledger["revision"],
        "ledger": ledger_destination.name,
        "ledger_sha256": _digest(ledger),
        "final_result": final_destination.name,
        "final_result_sha256": _digest(terminal_result),
    }
    context_path = Path(
        os.environ.get(ATTEMPT_CONTEXT_ENV, str(DEFAULT_ATTEMPT_CONTEXT_PATH))
    )
    if context_path.is_file():
        context = _read(context_path)
        required = {
            "schema_version",
            "case_id",
            "attempt_id",
            "question_sha256",
            "video_id",
            "sensor_id",
        }
        context = _strict(context, required, "attempt_context")
        if context["schema_version"] != 1:
            _fail("attempt_context.schema_version", "must be 1")
        for field in required - {"schema_version"}:
            _nonempty(context[field], f"attempt_context.{field}")
        question_sha256 = hashlib.sha256(
            ledger["plan"]["question_text"].encode("utf-8")
        ).hexdigest()
        if question_sha256 != context["question_sha256"]:
            _fail("attempt_context.question_sha256", "does not match the ledger plan")
        if ledger["plan"]["asset_id"] != context["video_id"]:
            _fail("attempt_context.video_id", "does not match the ledger plan asset")
        marker["attempt_context"] = dict(context)
    atomic_write(ledger_destination.parent / "terminal-commit.json", marker)


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
    create.add_argument("--media-scopes", required=True)
    create.add_argument("--claim-id", action="append")
    create.add_argument("--output", required=True)
    memory = commands.add_parser("merge-memory")
    memory.add_argument("--ledger", required=True)
    memory.add_argument("--updates", required=True)
    memory.add_argument("--output")
    merge = commands.add_parser("merge-round")
    merge.add_argument("--ledger", required=True)
    merge.add_argument("--base-ledger")
    merge.add_argument("--tasks", required=True)
    merge_results = merge.add_mutually_exclusive_group(required=True)
    merge_results.add_argument("--results")
    merge_results.add_argument("--results-dir")
    merge.add_argument("--output")
    expand = commands.add_parser("expand")
    expand.add_argument("--ledger", required=True)
    expand.add_argument("--plan", required=True)
    expand.add_argument("--output")
    assess = commands.add_parser("assess")
    assess.add_argument("--ledger", required=True)
    finish = commands.add_parser("final-result")
    finish.add_argument("--ledger", required=True)
    finish.add_argument("--artifact-dir", required=True)
    finish.add_argument("--answer-label")
    finish.add_argument("--answer-explanation")
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
        _emit(
            create_inspection_tasks(
                _read(args.ledger), _read(args.media_scopes), args.claim_id
            ),
            args.output,
        )
    elif args.command == "merge-memory":
        _emit(merge_memory(_read(args.ledger), _read(args.updates)), args.output)
    elif args.command == "merge-round":
        current = _read(args.ledger)
        base = _read(args.base_ledger) if args.base_ledger else current
        tasks = _read(args.tasks)
        results = (
            _read(args.results)
            if args.results
            else collect_inspection_results(tasks, args.results_dir)
        )
        merged = resume_round_merge(base, current, tasks, results)
        if args.output and merged["status"] == "unresolved":
            artifact_dir = str(Path(args.output).resolve().parent)
            terminal = final_result(merged, artifact_dir, None)
            atomic_commit_terminal(
                args.output,
                merged,
                Path(artifact_dir) / "final-result.json",
                terminal,
            )
        else:
            _emit(merged, args.output)
    elif args.command == "expand":
        _emit(expand_ledger(_read(args.ledger), _read(args.plan)), args.output)
    elif args.command == "assess":
        ledger = _read(args.ledger)
        _emit(
            {"sufficiency": assess_sufficiency(ledger), "stop": evaluate_stop(ledger)},
            None,
        )
    elif args.command == "final-result":
        prepared = prepare_for_final_result(_read(args.ledger))
        terminal = final_result(
            prepared,
            args.artifact_dir,
            args.answer_label,
            args.answer_explanation,
        )
        if args.output:
            atomic_commit_terminal(args.ledger, prepared, args.output, terminal)
        else:
            atomic_write(args.ledger, prepared)
            _emit(terminal, None)
    else:
        print(observation_id(_read(args.observation)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LedgerValidationError as error:
        raise SystemExit(f"error: {error}") from error
