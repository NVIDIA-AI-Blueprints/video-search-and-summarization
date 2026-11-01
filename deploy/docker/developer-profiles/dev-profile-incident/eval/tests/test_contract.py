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

"""The shared contract files and the strict Python validator."""

from __future__ import annotations

import copy
import json
import re

import pytest
from jsonschema import Draft202012Validator

import contract
from contract import ContractError, parse_report, validate_report


def _valid_report() -> dict:
    return {
        "incident": {
            "type": "assault",
            "title": "Person strikes another person with a bottle",
            "start_timestamp": 3,
            "end_timestamp": 11,
            "description": "E1 approaches E2 and strikes E2 with a bottle.",
            "severity_level": 3,
            "severity_reason": "Repeated strikes with an improvised weapon.",
            "confidence_score": None,
            "location": "shop interior near the counter",
        },
        "entities": [
            {
                "entity_id": "E1",
                "type": "human",
                "description": "Person in a dark jacket attacking.",
                "timestamp_seconds": 4,
                "first_seen_seconds": 3,
                "last_seen_seconds": 9,
                "label": "person in dark jacket",
            },
            {
                "entity_id": "E2",
                "type": "human",
                "description": "Person at the counter being struck.",
                "timestamp_seconds": 4,
                "first_seen_seconds": 3,
                "last_seen_seconds": 11,
                "label": "person at counter",
            },
        ],
        "instruments": [
            {
                "instrument_id": "I1",
                "entity_id": "E1",
                "name": "bottle",
                "description": "Glass bottle.",
                "threat_level": 3,
                "timestamp_seconds": 5,
                "label": "glass bottle",
            }
        ],
        "assets": [
            {
                "asset_id": "A1",
                "name": "counter",
                "description": "Shop counter E2 stands at.",
                "timestamp_seconds": 3,
                "label": "shop counter",
            }
        ],
        "timeline": [
            {"start_seconds": 3, "end_seconds": 5, "description": "E1 approaches E2."},
            {"start_seconds": 5, "end_seconds": 11, "description": "E1 strikes E2 with I1."},
        ],
        "uncertainties": ["It is unclear whether E2 was injured."],
    }


def _walk_objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _walk_objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_objects(value)


def test_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(contract.load_schema())


def test_schema_is_strict_mode_compatible():
    objects = list(_walk_objects(contract.load_schema()))
    assert len(objects) == 6  # root, incident, entity, instrument, asset, timeline event
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])


def test_taxonomy_is_eval_five_labels():
    incident_type = contract.load_schema()["properties"]["incident"]["properties"]["type"]
    assert incident_type["enum"] == ["road accident", "burglary", "explosion", "assault", "animal attack"]


def test_response_format_strips_document_keys():
    fmt = contract.response_format()
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert "$schema" not in fmt["json_schema"]["schema"]
    assert "$id" not in fmt["json_schema"]["schema"]


def test_version_file():
    # Read from contracts/VERSION, so a contract bump needs no edit here; the format is pinned instead.
    version = contract.contract_version()
    assert version == contract.VERSION_PATH.read_text().strip()
    assert re.fullmatch(r"incident-contract-v\d+", version)


def test_asset_handler_unknown_is_a_core_violation():
    """incident-contract-v5's assets[].last_handled_by must name a known entity (same code and path as the agent)."""
    report = _valid_report()
    report["assets"] = [{"asset_id": "A1", "name": "bag", "description": "a bag", "timestamp_seconds": 1,
                         "label": "bag", "last_handled_by": {"entity_id": "E9", "timestamp_seconds": 1}}]
    violations = contract.cross_field_violations(report)
    assert [(v["code"], v["scope"], v["path"]) for v in violations] == [
        ("ASSET_HANDLER_UNKNOWN", "core", "assets/0/last_handled_by/entity_id")]
    assert "A1 references unknown last_handled_by entity_id E9" in violations[0]["message"]


@pytest.mark.parametrize("handler", [None, "absent", {"entity_id": "E1", "timestamp_seconds": 2}])
def test_asset_handler_absent_null_or_known_is_fine(handler):
    report = _valid_report()
    asset = {"asset_id": "A1", "name": "bag", "description": "a bag", "timestamp_seconds": 1, "label": "bag"}
    if handler != "absent":
        asset["last_handled_by"] = handler
    report["assets"] = [asset]
    assert not [v for v in contract.cross_field_violations(report) if v["code"] == "ASSET_HANDLER_UNKNOWN"]


def test_extraction_prompt_has_no_output_template_or_few_shot():
    prompt = contract.extraction_prompt()
    assert "OUTPUT FORMAT" not in prompt
    assert "EXAMPLE 1" not in prompt
    assert "worked examples" not in prompt
    for section in ("TITLE", "SEVERITY_REASON", "LOCATION", "5. TIMELINE", "6. UNCERTAINTIES"):
        assert section in prompt


def test_prompt_taxonomy_matches_schema():
    prompt = contract.extraction_prompt()
    for label in contract.load_schema()["properties"]["incident"]["properties"]["type"]["enum"]:
        assert f"- {label}\n" in prompt


def test_report_prompt_has_single_placeholder():
    template = contract.report_prompt_template()
    assert template.count("{structured_incident_json}") == 1
    template.format(structured_incident_json="{}")


def test_valid_report_parses_and_duration_is_derived():
    report = _valid_report()
    parsed = parse_report(json.dumps(report))
    assert parsed["incident"]["duration"] == 8  # 11 - 3, computed by code
    assert {k: v for k, v in parsed["incident"].items() if k != "duration"} == report["incident"]
    assert "duration" not in report["incident"]  # the input is not mutated


def test_model_supplied_duration_is_rejected():
    report = _valid_report()
    report["incident"]["duration"] = 8
    with pytest.raises(ContractError, match="schema violation"):
        validate_report(report)


@pytest.mark.parametrize(
    "content",
    [None, "", "```json\n{}\n```", 'Here is the JSON: {"incident": {}}', "<think>x</think>{}"],
)
def test_non_json_content_is_rejected_without_extraction(content):
    with pytest.raises(ContractError):
        parse_report(content)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["incident"].update(type="fighting"),
        lambda r: r["incident"].pop("title"),
        lambda r: r["incident"].update(extra="field"),
        lambda r: r["incident"].update(severity_level=6),
        lambda r: r["incident"].update(confidence_score=0.9 * 2),
        lambda r: r["entities"][0].update(type="person"),
        lambda r: r["entities"][0].pop("timestamp_seconds"),
        lambda r: r["entities"][0].pop("first_seen_seconds"),
        lambda r: r["entities"][0].pop("last_seen_seconds"),
        lambda r: r["entities"][0].update(first_seen_seconds=-1),
        lambda r: r["entities"][0].pop("label"),
        lambda r: r.update(persons=[]),
    ],
)
def test_schema_violations_are_rejected(mutate):
    report = _valid_report()
    mutate(report)
    with pytest.raises(ContractError, match="schema violation"):
        validate_report(report)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r["incident"].update(end_timestamp=2), "before start_timestamp"),
        (lambda r: r["entities"][1].update(entity_id="E3"), "not sequential"),
        (lambda r: r["instruments"][0].update(entity_id="E9"), "unknown entity_id"),
        (lambda r: r["timeline"][1].update(start_seconds=12), "outside"),
        (lambda r: r["timeline"].reverse(), "chronological"),
    ],
)
def test_cross_field_violations_are_rejected(mutate, message):
    report = copy.deepcopy(_valid_report())
    mutate(report)
    with pytest.raises(ContractError, match=message):
        validate_report(report)


def test_empty_arrays_and_null_holder_are_valid():
    report = _valid_report()
    report["instruments"][0]["entity_id"] = None
    report["assets"] = []
    report["uncertainties"] = []
    assert validate_report(report) is report
