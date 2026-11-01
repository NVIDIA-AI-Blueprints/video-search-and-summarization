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

"""Shared incident contract (``dev-profile-incident/contracts/``) for Python callers.

The JSON Schema file is the single source of truth. This module loads it, builds
the ``response_format`` payload sent to the model, and validates a response
strictly: ``json.loads`` of the raw content, JSON Schema validation, then the
cross-field rules the schema cannot express. Derived fields (``incident.duration``)
are computed here, not requested from the model. There is no fence stripping, brace
extraction, alias mapping or default filling - a response either conforms or
raises ``ContractError``. See ``.docs/prompt-contract-plan.md``.

``parse_report`` checks full contract validity. ``evaluate_policy`` applies
validation policy core-scored-v1, which decides what eval scores: only the
database-backed fields must be valid, and the incident window must lie within
the video when its length is known (see ``contracts/README.md``).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"
SCHEMA_PATH = CONTRACTS_DIR / "incident_report.schema.json"
EXTRACTION_PROMPT_PATH = CONTRACTS_DIR / "incident_extraction_prompt.txt"
REPORT_PROMPT_PATH = CONTRACTS_DIR / "report_generation_prompt.txt"
VERSION_PATH = CONTRACTS_DIR / "VERSION"
INFERENCE_PATH = CONTRACTS_DIR / "inference.json"

# Keys describing the schema document itself; stripped from what is sent to the
# model because some OpenAI-compatible servers reject them inside response_format.
_DOCUMENT_ONLY_KEYS = ("$schema", "$id")


class ContractError(ValueError):
    """A model response does not conform to the incident contract."""


@cache
def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


@cache
def contract_version() -> str:
    return VERSION_PATH.read_text().strip()


@cache
def extraction_prompt() -> str:
    return EXTRACTION_PROMPT_PATH.read_text()


@cache
def report_prompt_template() -> str:
    return REPORT_PROMPT_PATH.read_text()


@cache
def load_inference() -> dict[str, Any]:
    return json.loads(INFERENCE_PATH.read_text())


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema())


def response_format() -> dict[str, Any]:
    """OpenAI-compatible ``response_format`` enforcing the contract (strict mode)."""
    schema = {k: v for k, v in load_schema().items() if k not in _DOCUMENT_ONLY_KEYS}
    return {
        "type": "json_schema",
        "json_schema": {"name": "incident_report", "schema": schema, "strict": True},
    }


def cross_field_violations(report: dict[str, Any]) -> list[dict[str, str]]:
    """The contract's cross-field rules as ``{code, scope, path, message}``.

    Assumes ``report`` already passed the schema. Codes and paths match the
    console's ``crossFieldViolations`` (``incident-console-v2/lib/contract/validate.ts``);
    ``scope`` is the core-scored-v1 classification (see ``evaluate_policy``).
    """
    violations: list[dict[str, str]] = []

    def add(code: str, path: str, message: str) -> None:
        violations.append({"code": code, "scope": violation_scope(code), "path": path, "message": message})

    incident = report["incident"]
    start, end = incident["start_timestamp"], incident["end_timestamp"]
    if end < start:
        add("WINDOW_END_BEFORE_START", "incident/end_timestamp", f"end_timestamp {end} is before start_timestamp {start}")

    for code, prefix, key, collection in (
        ("ID_NOT_SEQUENTIAL_ENTITY", "E", "entity_id", "entities"),
        ("ID_NOT_SEQUENTIAL_INSTRUMENT", "I", "instrument_id", "instruments"),
        ("ID_NOT_SEQUENTIAL_ASSET", "A", "asset_id", "assets"),
    ):
        items = report[collection]
        expected = [f"{prefix}{n}" for n in range(1, len(items) + 1)]
        actual = [item[key] for item in items]
        if actual != expected:
            add(code, collection, f"{key} values {actual} are not sequential {expected}")

    entity_ids = {e["entity_id"] for e in report["entities"]}
    for index, instrument in enumerate(report["instruments"]):
        holder = instrument["entity_id"]
        if holder is not None and holder not in entity_ids:
            add("INSTRUMENT_HOLDER_UNKNOWN", f"instruments/{index}/entity_id",
                f"{instrument['instrument_id']} references unknown entity_id {holder}")

    # incident-contract-v5 adds an optional assets[].last_handled_by {entity_id, timestamp_seconds}.
    # The v4 schema forbids the key, so on v4 this rule never fires; it keeps eval in step with the
    # agent and the console (same code, path and message) if a v5 contract lands.
    for index, asset in enumerate(report["assets"]):
        handler = asset.get("last_handled_by")
        if isinstance(handler, dict) and handler.get("entity_id") not in entity_ids:
            add("ASSET_HANDLER_UNKNOWN", f"assets/{index}/last_handled_by/entity_id",
                f"{asset['asset_id']} references unknown last_handled_by entity_id {handler.get('entity_id')}")

    previous_start = None
    for index, event in enumerate(report["timeline"]):
        event_start, event_end = event["start_seconds"], event["end_seconds"]
        if not start <= event_start <= end:
            add("TIMELINE_START_OUTSIDE_WINDOW", f"timeline/{index}/start_seconds",
                f"timeline[{index}].start_seconds {event_start} is outside [{start}, {end}]")
        if event_end is not None and not event_start <= event_end <= end:
            add("TIMELINE_END_OUTSIDE_WINDOW", f"timeline/{index}/end_seconds",
                f"timeline[{index}].end_seconds {event_end} is outside [{event_start}, {end}]")
        if previous_start is not None and event_start < previous_start:
            add("TIMELINE_NOT_CHRONOLOGICAL", f"timeline/{index}", f"timeline[{index}] is not in chronological order")
        previous_start = event_start
    return violations


def cross_field_errors(report: dict[str, Any]) -> list[str]:
    """Rules JSON Schema cannot express, as messages. Assumes ``report`` already passed the schema."""
    return [violation["message"] for violation in cross_field_violations(report)]


# --- Validation policy core-scored-v1 -------------------------------------------------
#
# A layer on top of the unchanged contract. The contract defines full validity
# (schema + every cross-field rule above): ``full_contract_ok``. core-scored-v1
# decides what is persisted and scored: the database-backed fields (incident
# type/window/description/severity/confidence, entities, instruments, assets)
# must be valid; the best-effort enrichment fields (title, severity_reason,
# location, timeline, uncertainties) are never scored and a cross-field failure
# confined to them does not invalidate the prediction: ``core_ok``.
#
# Scope: only cross-field rules are split. Any schema violation stays fatal,
# because isolating a valid core from a schema-invalid document would need
# partial parsing. Nothing is repaired, clipped or removed here.

POLICY_ID = "core-scored-v1"

# Timestamps are whole seconds. A moment t within a video of D seconds, written as
# a whole second, is at most ceil(t) < D + 1; so an integer timestamp T lies
# within the video iff 0 <= T < D + WHOLE_SECOND_RESOLUTION.
WHOLE_SECOND_RESOLUTION = 1

ENRICHMENT_CODES = frozenset({
    "TIMELINE_START_OUTSIDE_WINDOW",
    "TIMELINE_END_OUTSIDE_WINDOW",
    "TIMELINE_NOT_CHRONOLOGICAL",
    "TIMELINE_BEYOND_VIDEO",
})


def violation_scope(code: str) -> str:
    """``"enrichment"`` for violations confined to unscored fields, ``"core"`` otherwise."""
    return "enrichment" if code in ENRICHMENT_CODES else "core"


def timestamp_within_video(seconds: float, video_duration_seconds: float) -> bool:
    """Whether an integer-second timestamp can name a moment of a ``video_duration_seconds`` video."""
    return 0 <= seconds < video_duration_seconds + WHOLE_SECOND_RESOLUTION


def video_bounds_violations(report: dict[str, Any], video_duration_seconds: float) -> list[dict[str, str]]:
    """Contextual rules (runtime input, not the schema): timestamps must lie within the video."""
    violations: list[dict[str, str]] = []

    def add(code: str, path: str, message: str) -> None:
        violations.append({"code": code, "scope": violation_scope(code), "path": path, "message": message})

    incident = report["incident"]
    for key in ("start_timestamp", "end_timestamp"):
        if not timestamp_within_video(incident[key], video_duration_seconds):
            add("WINDOW_BEYOND_VIDEO", f"incident/{key}",
                f"{key} {incident[key]} is outside the {video_duration_seconds:g} s video")
    for index, event in enumerate(report["timeline"]):
        for key in ("start_seconds", "end_seconds"):
            value = event[key]
            if value is not None and not timestamp_within_video(value, video_duration_seconds):
                add("TIMELINE_BEYOND_VIDEO", f"timeline/{index}/{key}",
                    f"timeline[{index}].{key} {value} is outside the {video_duration_seconds:g} s video")
    return violations


def evaluate_policy(content: str | None, video_duration_seconds: float | None = None) -> dict[str, Any]:
    """Judge a model's raw content under the contract and under core-scored-v1.

    Returns ``full_contract_ok`` (incident-contract validity, exactly as
    ``parse_report``), ``core_ok`` (what persistence and scoring use),
    ``core`` / ``enrichment`` violation lists, ``video_bounds_checked`` and
    ``report`` - the derived report, unaltered apart from ``incident.duration``,
    when ``core_ok``, else ``None``. The contextual video rules are applied only
    when the video length is known; they never affect ``full_contract_ok``.
    """
    result: dict[str, Any] = {
        "validation_policy": POLICY_ID,
        "full_contract_ok": False,
        "core_ok": False,
        "core": [],
        "enrichment": [],
        "video_bounds_checked": False,
        "report": None,
    }
    if not content:
        result["core"] = [{"code": "EMPTY_CONTENT", "scope": "core", "path": "<root>", "message": "empty response content"}]
        return result
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as exc:
        message = f"content is not a single JSON document: {exc}"
        result["core"] = [{"code": "INVALID_JSON", "scope": "core", "path": "<root>", "message": message}]
        return result
    schema_errors = sorted(_validator().iter_errors(decoded), key=lambda e: list(e.absolute_path))
    if schema_errors:
        result["core"] = [
            {"code": "SCHEMA_VIOLATION", "scope": "core", "path": "/".join(map(str, e.absolute_path)) or "<root>",
             "message": f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"}
            for e in schema_errors
        ]
        return result

    contract_violations = cross_field_violations(decoded)
    result["full_contract_ok"] = not contract_violations
    violations = list(contract_violations)
    if video_duration_seconds is not None and video_duration_seconds > 0:  # a non-positive length is unknown
        result["video_bounds_checked"] = True
        violations += video_bounds_violations(decoded, video_duration_seconds)
    result["core"] = [v for v in violations if v["scope"] == "core"]
    result["enrichment"] = [v for v in violations if v["scope"] == "enrichment"]
    result["core_ok"] = not result["core"]
    if result["core_ok"]:
        result["report"] = with_derived_fields(decoded)
    return result


def validate_report(report: Any) -> dict[str, Any]:
    """Validate an already-decoded report; raise ``ContractError`` listing every violation."""
    schema_errors = sorted(_validator().iter_errors(report), key=lambda e: list(e.absolute_path))
    if schema_errors:
        details = "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in schema_errors)
        raise ContractError(f"schema violation: {details}")
    errors = cross_field_errors(report)
    if errors:
        raise ContractError(f"cross-field violation: {'; '.join(errors)}")
    return report


def with_derived_fields(report: dict[str, Any]) -> dict[str, Any]:
    """Add fields computed by code, never requested from the model (``incident.duration``)."""
    incident = report["incident"]
    return {**report, "incident": {**incident, "duration": incident["end_timestamp"] - incident["start_timestamp"]}}


def parse_report(content: str | None) -> dict[str, Any]:
    """Strictly decode and validate a model's message content, then add derived fields."""
    if not content:
        raise ContractError("empty response content")
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ContractError(f"content is not a single JSON document: {exc}") from exc
    return with_derived_fields(validate_report(decoded))
