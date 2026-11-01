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

"""The shared incident contract (``dev-profile-incident/contracts/``) for the agent.

The files next to this module are a vendored copy of the profile's ``contracts/``
directory (the agent is deployed from ``services/agent``); run
``deploy/docker/developer-profiles/dev-profile-incident/contracts/sync.py`` to
synchronise them. A unit test fails when the two copies differ. The JSON Schema file is what the model is sent as
``response_format``. Responses are validated strictly with the Pydantic models below
(``strict=True``, ``extra="forbid"``), which mirror that schema - another unit test
checks the two agree - then with the cross-field rules the schema cannot express,
under validation policy ``core-scored-v1`` with the ``id-normalization-v1`` recovery,
exactly as ``incident-console-v2`` applies them (``lib/contract/validate.ts`` and
``lib/contract/repair.ts``): a cross-field failure confined to the timeline is not
fatal, and ID-only failures are renumbered. Contextual video-length rules
(``WINDOW_BEYOND_VIDEO``, ``TIMELINE_BEYOND_VIDEO``) are evaluated whenever
``video_duration_seconds`` is provided.

There is no fence stripping, brace extraction, alias mapping, coercion or any other
repair: a response either passes the policy or raises :class:`IncidentExtractionError`.
See ``.docs/analysis-schema.md`` in the incident profile.
"""

from __future__ import annotations

from functools import cache
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError

CONTRACT_DIR = Path(__file__).resolve().parent

IncidentType = Literal["road accident", "burglary", "explosion", "assault", "animal attack"]
EntityType = Literal["human", "animal", "unknown"]

_STRICT = ConfigDict(strict=True, extra="forbid")


class IncidentExtractionError(ValueError):
    """A model response does not conform to the incident contract (HTTP 422 on ``/analyze``).

    ``raw_output``, ``finish_reason`` and ``model`` describe the rejected response, so the
    caller can record it unaltered.
    """

    def __init__(
        self, message: str, *, raw_output: str = "", finish_reason: str | None = None, model: str | None = None
    ) -> None:
        super().__init__(message)
        self.raw_output = raw_output
        self.finish_reason = finish_reason
        self.model = model


class ContractIncident(BaseModel):
    """``incident`` as the model returns it; ``duration`` is derived afterwards, never requested."""

    model_config = _STRICT

    type: IncidentType
    title: str
    start_timestamp: int = Field(ge=0)
    end_timestamp: int = Field(ge=0)
    description: str
    # Strict int with bounds rather than Literal[1..5]: Literal compares by equality, so it would
    # accept true and 3.0, which the JSON Schema enum rejects.
    severity_level: int = Field(ge=1, le=5)
    severity_reason: str
    confidence_score: float | None = Field(ge=0, le=1)
    location: str | None


class ContractEntity(BaseModel):
    model_config = _STRICT

    entity_id: str
    type: EntityType
    description: str
    timestamp_seconds: int = Field(ge=0)
    first_seen_seconds: int = Field(ge=0)
    last_seen_seconds: int = Field(ge=0)
    label: str


class ContractInstrument(BaseModel):
    model_config = _STRICT

    instrument_id: str
    entity_id: str | None
    name: str
    description: str
    threat_level: int = Field(ge=1, le=5)
    timestamp_seconds: int = Field(ge=0)
    label: str


class ContractAsset(BaseModel):
    model_config = _STRICT

    asset_id: str
    name: str
    description: str
    timestamp_seconds: int = Field(ge=0)
    label: str


class ContractTimelineEvent(BaseModel):
    model_config = _STRICT

    start_seconds: int = Field(ge=0)
    end_seconds: int | None = Field(ge=0)
    description: str


class ContractReport(BaseModel):
    """The P1 output exactly as the contract schema defines it (no derived fields)."""

    model_config = _STRICT

    incident: ContractIncident
    entities: list[ContractEntity]
    instruments: list[ContractInstrument]
    assets: list[ContractAsset]
    timeline: list[ContractTimelineEvent]
    uncertainties: list[str]


def _read(name: str) -> str:
    return (CONTRACT_DIR / name).read_text(encoding="utf-8")


def _read_json(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_read(name))
    return data


PROMPTS_DIR = CONTRACT_DIR / "prompts"
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")


class PromptRegistryError(RuntimeError):
    """A prompt that is not registered, whose file does not match its pinned SHA-256, or a wrong set of fields."""


@cache
def _prompt_entries() -> dict[str, dict[str, Any]]:
    registry = json.loads((PROMPTS_DIR / "registry.json").read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in registry["prompts"] if "agent" in entry["used_by"]}


@cache
def prompt(prompt_id: str) -> str:
    """A prompt from the shared registry (``contracts/prompts/``, vendored by ``sync.py``), hash-checked.

    Every model prompt the agent sends besides P1 and RP1 lives there: the caption pass, the P1 preambles,
    grounding and the zoomed second look. The request goldens in ``contracts/golden/`` pin what they render to.
    """
    entry = _prompt_entries().get(prompt_id)
    if entry is None:
        raise PromptRegistryError(f"no agent prompt {prompt_id!r} in contracts/prompts/registry.json")
    file_name: str = entry["file"]
    raw = (PROMPTS_DIR / file_name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise PromptRegistryError(f"{file_name} does not match its registered sha256 (run contracts/sync.py)")
    return raw.decode("utf-8")


def prompt_sha256(prompt_id: str) -> str:
    """The registered SHA-256 of a prompt's template, recorded with each run as the prompt's version."""
    prompt(prompt_id)
    return str(_prompt_entries()[prompt_id]["sha256"])


def render_prompt(prompt_id: str, **fields: object) -> str:
    """The prompt with each ``{{name}}`` replaced by ``str(fields[name])`` in one pass; fields must match exactly."""
    template = prompt(prompt_id)
    expected = set(_PLACEHOLDER.findall(template))
    if expected != set(fields):
        raise PromptRegistryError(f"{prompt_id} takes {sorted(expected)}, got {sorted(fields)}")
    return _PLACEHOLDER.sub(lambda match: str(fields[match.group(1)]), template)


@cache
def load_schema() -> dict[str, Any]:
    return _read_json("incident_report.schema.json")


@cache
def contract_version() -> str:
    return _read("VERSION").strip()


@cache
def extraction_prompt() -> str:
    return _read("incident_extraction_prompt.txt")


@cache
def report_prompt_template() -> str:
    return _read("report_generation_prompt.txt")


def p1_request() -> dict[str, Any]:
    """Fixed P1 request settings shared with eval and the console (temperature, max_tokens, media_io_kwargs)."""
    p1: dict[str, Any] = _read_json("inference.json")["p1"]
    return p1


def allowed_models() -> list[str]:
    """Ids of the VLMs P1 may run on: the ``models`` allowlist in ``inference.json``, shared with the console."""
    return [str(model["id"]) for model in _read_json("inference.json")["models"]]


def rp1_request() -> dict[str, Any]:
    """Fixed RP1 request: ``model`` plus its inference settings, shared with eval."""
    rp1: dict[str, Any] = _read_json("inference.json")["rp1"]
    return rp1


def response_format() -> dict[str, Any]:
    """OpenAI-compatible ``response_format`` enforcing the contract schema file (strict mode)."""
    schema = {key: value for key, value in load_schema().items() if key not in ("$schema", "$id")}
    return {"type": "json_schema", "json_schema": {"name": "incident_report", "schema": schema, "strict": True}}


WHOLE_SECOND_RESOLUTION = 1

ENRICHMENT_CODES = frozenset(
    {
        "TIMELINE_START_OUTSIDE_WINDOW",
        "TIMELINE_END_OUTSIDE_WINDOW",
        "TIMELINE_NOT_CHRONOLOGICAL",
        "TIMELINE_BEYOND_VIDEO",
    }
)
REPAIRABLE_CODES = frozenset(
    {
        "ID_NOT_SEQUENTIAL_ENTITY",
        "ID_NOT_SEQUENTIAL_INSTRUMENT",
        "ID_NOT_SEQUENTIAL_ASSET",
        "INSTRUMENT_HOLDER_UNKNOWN",
    }
)
_ID_COLLECTIONS = (
    ("ID_NOT_SEQUENTIAL_ENTITY", "E", "entity_id", "entities"),
    ("ID_NOT_SEQUENTIAL_INSTRUMENT", "I", "instrument_id", "instruments"),
    ("ID_NOT_SEQUENTIAL_ASSET", "A", "asset_id", "assets"),
)


def timestamp_within_video(seconds: float, video_duration_seconds: float) -> bool:
    """Whether an integer-second timestamp can name a moment of a ``video_duration_seconds`` video."""
    return 0 <= seconds < video_duration_seconds + WHOLE_SECOND_RESOLUTION


def video_bounds_violations(report: ContractReport, video_duration_seconds: float) -> list[tuple[str, str]]:
    """Contextual rules (runtime input, not the schema): timestamps must lie within the video."""
    violations: list[tuple[str, str]] = []
    start, end = report.incident.start_timestamp, report.incident.end_timestamp
    if not timestamp_within_video(start, video_duration_seconds):
        violations.append(
            ("WINDOW_BEYOND_VIDEO", f"start_timestamp {start} is outside the {video_duration_seconds:g} s video")
        )
    if not timestamp_within_video(end, video_duration_seconds):
        violations.append(
            ("WINDOW_BEYOND_VIDEO", f"end_timestamp {end} is outside the {video_duration_seconds:g} s video")
        )
    for index, event in enumerate(report.timeline):
        if not timestamp_within_video(event.start_seconds, video_duration_seconds):
            violations.append(
                (
                    "TIMELINE_BEYOND_VIDEO",
                    f"timeline[{index}].start_seconds {event.start_seconds} is outside the {video_duration_seconds:g} s video",
                )
            )
        if event.end_seconds is not None and not timestamp_within_video(event.end_seconds, video_duration_seconds):
            violations.append(
                (
                    "TIMELINE_BEYOND_VIDEO",
                    f"timeline[{index}].end_seconds {event.end_seconds} is outside the {video_duration_seconds:g} s video",
                )
            )
    return violations


def cross_field_violations(report: ContractReport) -> list[tuple[str, str]]:
    """Rules JSON Schema cannot express as ``(code, message)`` (same codes as eval/contract.py and the console)."""
    violations: list[tuple[str, str]] = []
    start, end = report.incident.start_timestamp, report.incident.end_timestamp
    if end < start:
        violations.append(("WINDOW_END_BEFORE_START", f"end_timestamp {end} is before start_timestamp {start}"))

    for code, prefix, key, collection in _ID_COLLECTIONS:
        ids = [getattr(item, key) for item in getattr(report, collection)]
        expected = [f"{prefix}{n}" for n in range(1, len(ids) + 1)]
        if ids != expected:
            violations.append((code, f"{key} values {ids} are not sequential {expected}"))

    entity_ids = {entity.entity_id for entity in report.entities}
    for instrument in report.instruments:
        if instrument.entity_id is not None and instrument.entity_id not in entity_ids:
            violations.append(
                (
                    "INSTRUMENT_HOLDER_UNKNOWN",
                    f"{instrument.instrument_id} references unknown entity_id {instrument.entity_id}",
                )
            )

    previous_start: int | None = None
    for index, event in enumerate(report.timeline):
        if not start <= event.start_seconds <= end:
            violations.append(
                (
                    "TIMELINE_START_OUTSIDE_WINDOW",
                    f"timeline[{index}].start_seconds {event.start_seconds} is outside [{start}, {end}]",
                )
            )
        if event.end_seconds is not None and not event.start_seconds <= event.end_seconds <= end:
            violations.append(
                (
                    "TIMELINE_END_OUTSIDE_WINDOW",
                    f"timeline[{index}].end_seconds {event.end_seconds} is outside [{event.start_seconds}, {end}]",
                )
            )
        if previous_start is not None and event.start_seconds < previous_start:
            violations.append(("TIMELINE_NOT_CHRONOLOGICAL", f"timeline[{index}] is not in chronological order"))
        previous_start = event.start_seconds
    return violations


def core_violations(report: ContractReport, video_duration_seconds: float | None = None) -> list[tuple[str, str]]:
    """The cross-field violations that decide the outcome under core-scored-v1."""
    violations = list(cross_field_violations(report))
    if video_duration_seconds is not None and video_duration_seconds > 0:
        violations.extend(video_bounds_violations(report, video_duration_seconds))
    return [violation for violation in violations if violation[0] not in ENRICHMENT_CODES]


def _free_texts(report: ContractReport) -> list[str]:
    incident = report.incident
    return [
        incident.title,
        incident.description,
        incident.severity_reason,
        incident.location or "",
        *(entity.description for entity in report.entities),
        *(text for item in report.instruments for text in (item.name, item.description)),
        *(text for item in report.assets for text in (item.name, item.description)),
        *(event.description for event in report.timeline),
        *report.uncertainties,
    ]


def _mentioned(token: str, texts: list[str]) -> bool:
    pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])")
    return any(pattern.search(text) for text in texts)


def _semantic_projection(report: ContractReport) -> str:
    """Semantic projection used by the invariance check: everything except identifiers and list order."""
    entity_by_id = {entity.entity_id: entity for entity in report.entities}

    def _sorted(items: list[dict[str, Any]]) -> list[str]:
        return sorted(json.dumps(item, sort_keys=True) for item in items)

    entities = _sorted([entity.model_dump(mode="json", exclude={"entity_id"}) for entity in report.entities])
    instruments_proj: list[dict[str, Any]] = []
    for instrument in report.instruments:
        item = instrument.model_dump(mode="json", exclude={"instrument_id", "entity_id"})
        holder = instrument.entity_id
        entity = entity_by_id.get(holder) if holder is not None else None
        item["holder"] = {"type": entity.type, "description": entity.description} if entity is not None else holder
        instruments_proj.append(item)
    instruments = _sorted(instruments_proj)
    assets = _sorted([asset.model_dump(mode="json", exclude={"asset_id"}) for asset in report.assets])

    projection = {
        "incident": report.incident.model_dump(mode="json"),
        "timeline": [t.model_dump(mode="json") for t in report.timeline],
        "uncertainties": list(report.uncertainties),
        "entities": entities,
        "instruments": instruments,
        "assets": assets,
    }
    return json.dumps(projection, sort_keys=True)


def normalize_ids(report: ContractReport, violations: list[tuple[str, str]]) -> ContractReport | None:
    """id-normalization-v1: renumber E/I/A ids, remap instrument holders and reorder; ``None`` when ineligible.

    Eligible only when every core violation is an ID sequence one or conditionally eligible holder reference,
    no list has duplicate ids, no renamed or target id appears in free text, and semantic invariance holds.
    """
    if not violations or any(code not in REPAIRABLE_CODES for code, _ in violations):
        return None
    texts = _free_texts(report)
    data = report.model_dump(mode="json")
    entity_mapping: dict[str, str] = {}
    for _, prefix, key, collection in _ID_COLLECTIONS:
        items = data[collection]
        ids = [item[key] for item in items]
        if len(set(ids)) != len(ids):
            return None
        well_formed = re.compile(rf"{prefix}([1-9][0-9]*)")
        numbers: list[int | None] = []
        for item_id in ids:
            match = well_formed.fullmatch(item_id)
            numbers.append(int(match[1]) if match and int(match[1]) <= len(ids) else None)
        free = iter(n for n in range(1, len(ids) + 1) if n not in numbers)
        mapping = {item_id: f"{prefix}{number or next(free)}" for item_id, number in zip(ids, numbers, strict=True)}
        for old, new in mapping.items():
            if old != new and ((re.search(r"[A-Za-z0-9]", old) and _mentioned(old, texts)) or _mentioned(new, texts)):
                return None
        data[collection] = sorted(({**item, key: mapping[item[key]]} for item in items), key=lambda i: int(i[key][1:]))
        if collection == "entities":
            entity_mapping = mapping
    for instrument in data["instruments"]:
        if instrument["entity_id"] is not None:
            target = entity_mapping.get(instrument["entity_id"])
            if target is None:
                return None
            instrument["entity_id"] = target
    try:
        repaired = ContractReport.model_validate(data)
    except ValidationError:
        return None
    if _semantic_projection(repaired) != _semantic_projection(report):
        return None
    return repaired


def with_derived_fields(report: ContractReport) -> dict[str, Any]:
    """The validated report as a plain dict plus fields computed by code (``incident.duration``)."""
    data = report.model_dump(mode="json")
    data["incident"]["duration"] = report.incident.end_timestamp - report.incident.start_timestamp
    return data


def validate_report(value: Any, video_duration_seconds: float | None = None) -> dict[str, Any]:
    """Validate an already-decoded model output under core-scored-v1, applying id-normalization-v1 when eligible.

    Raises :class:`IncidentExtractionError` listing every violation of a schema-invalid or core-invalid report.
    """
    try:
        report = ContractReport.model_validate(value)
    except ValidationError as exc:
        details = "; ".join(
            f"{'/'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}" for error in exc.errors()
        )
        raise IncidentExtractionError(f"schema violation: {details}") from exc
    violations = core_violations(report, video_duration_seconds=video_duration_seconds)
    if not violations:
        return with_derived_fields(report)
    repaired = normalize_ids(report, violations)
    if repaired is not None and not core_violations(repaired, video_duration_seconds=video_duration_seconds):
        return with_derived_fields(repaired)
    raise IncidentExtractionError(f"core violation: {'; '.join(message for _, message in violations)}")


def parse_report(content: str | None, video_duration_seconds: float | None = None) -> dict[str, Any]:
    """Strictly decode and validate a model's message content, then add derived fields."""
    if not content:
        raise IncidentExtractionError("empty response content")
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as exc:
        raise IncidentExtractionError(f"content is not a single JSON document: {exc}") from exc
    return validate_report(decoded, video_duration_seconds=video_duration_seconds)


def report_prompt(report: dict[str, Any]) -> str:
    """The RP1 prompt for a validated (derived) report."""
    return report_prompt_template().format(structured_incident_json=json.dumps(report, indent=2))
