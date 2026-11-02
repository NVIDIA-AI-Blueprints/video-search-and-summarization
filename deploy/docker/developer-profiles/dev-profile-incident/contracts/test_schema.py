# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Schema validation tests for MVP2 contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
from jsonschema import ValidationError, validate

CONTRACTS_DIR = Path(__file__).resolve().parent


def _load(filename: str) -> dict:
    with open(CONTRACTS_DIR / filename, "r") as f:
        return json.load(f)


def _validate(schema: dict, instance: dict, label: str) -> None:
    try:
        validate(instance, schema)
    except ValidationError as e:
        print(f"  FAIL: {label} - {e.message}", file=sys.stderr)
        raise AssertionError(f"Schema validation failed for {label}: {e.message}")


# --- Search Contract v1 ---


def test_search_request_schema_valid_fixtures():
    """Valid request fixtures pass search_request.schema.json."""
    schema = _load("search-contract-v1/search_request.schema.json")
    for name in ["minimal-request.json", "full-request.json", "version-request.json", "draft-request.json"]:
        fixture = _load(f"search-contract-v1/fixtures/valid/{name}")
        _validate(schema, fixture, f"request/{name}")


def test_search_request_schema_invalid_fixtures():
    """Invalid request fixtures fail search_request.schema.json."""
    schema = _load("search-contract-v1/search_request.schema.json")
    for name in [
        "request-missing-query.json",
        "request-invalid-top-k.json",
        "request-invalid-item-type.json",
    ]:
        fixture = _load(f"search-contract-v1/fixtures/invalid/{name}")
        try:
            validate(fixture, schema)
            assert False, f"Expected {name} to be invalid"
        except ValidationError:
            pass


def test_search_response_schema_valid_fixtures():
    """Valid response fixtures pass search_response.schema.json."""
    schema = _load("search-contract-v1/search_response.schema.json")
    for name in ["empty-response.json", "full-response.json"]:
        fixture = _load(f"search-contract-v1/fixtures/valid/{name}")
        _validate(schema, fixture, f"response/{name}")


def test_search_response_schema_invalid_fixtures():
    """Invalid response fixtures fail search_response.schema.json."""
    schema = _load("search-contract-v1/search_response.schema.json")
    for name in [
        "response-missing-critic.json",
        "response-invalid-verdict.json",
        "response-negative-timestamp.json",
    ]:
        fixture = _load(f"search-contract-v1/fixtures/invalid/{name}")
        try:
            validate(fixture, schema)
            assert False, f"Expected {name} to be invalid"
        except ValidationError:
            pass


def test_search_response_pilot_constraints():
    """Response hits carry pilot-mandated fields: timestamp_seconds, label, critic."""
    fixture = _load("search-contract-v1/fixtures/valid/full-response.json")
    for idx, hit in enumerate(fixture["hits"]):
        item = hit["item"]
        assert isinstance(item["timestamp_seconds"], int), (
            f"hit[{idx}].timestamp_seconds must be int"
        )
        assert item["timestamp_seconds"] >= 0, (
            f"hit[{idx}].timestamp_seconds must be >= 0"
        )
        assert isinstance(item["label"], str), f"hit[{idx}].label must be string"
        assert isinstance(item["crop_image_url"], str), (
            f"hit[{idx}].crop_image_url must be string"
        )
        assert "critic" in hit, f"hit[{idx}] must have critic block"
        assert hit["critic"]["verdict"] in ("confirmed", "rejected", "unverified"), (
            f"hit[{idx}].critic.verdict must be enum"
        )
        # bounding_box is optional: either null or 4-int array
        bb = item.get("bounding_box")
        if bb is not None:
            assert isinstance(bb, list) and len(bb) == 4, (
                f"hit[{idx}].bounding_box must be null or [4]ints"
            )
        else:
            pass  # null is fine


# --- Chat Contract v1 ---


def test_chat_request_schema_valid_fixtures():
    """Valid request fixtures pass chat_request.schema.json."""
    schema = _load("chat-contract-v1/chat_request.schema.json")
    for name in ["minimal-request.json", "full-request.json", "version-request.json", "draft-request.json"]:
        fixture = _load(f"chat-contract-v1/fixtures/valid/{name}")
        _validate(schema, fixture, f"chat-request/{name}")


def test_chat_request_schema_invalid_fixtures():
    """Invalid request fixtures fail chat_request.schema.json."""
    schema = _load("chat-contract-v1/chat_request.schema.json")
    for name in [
        "request-missing-messages.json",
        "request-empty-messages.json",
        "request-invalid-scope.json",
        "request-draft-without-version.json",
        "request-invalid-version-id.json",
    ]:
        fixture = _load(f"chat-contract-v1/fixtures/invalid/{name}")
        try:
            validate(fixture, schema)
            assert False, f"Expected {name} to be invalid"
        except ValidationError:
            pass


def test_chat_response_schema_valid_fixtures():
    """Valid response fixtures pass chat_response.schema.json."""
    schema = _load("chat-contract-v1/chat_response.schema.json")
    for name in ["empty-response.json", "full-response.json"]:
        fixture = _load(f"chat-contract-v1/fixtures/valid/{name}")
        _validate(schema, fixture, f"chat-response/{name}")


def test_chat_response_schema_invalid_fixtures():
    """Invalid response fixtures fail chat_response.schema.json."""
    schema = _load("chat-contract-v1/chat_response.schema.json")
    for name in [
        "response-missing-session.json",
        "response-invalid-role.json",
        "response-invalid-citation.json",
    ]:
        fixture = _load(f"chat-contract-v1/fixtures/invalid/{name}")
        try:
            validate(fixture, schema)
            assert False, f"Expected {name} to be invalid"
        except ValidationError:
            pass


def test_chat_contract_v1_version():
    """VERSION file matches chat-contract-v1."""
    version = (CONTRACTS_DIR / "chat-contract-v1" / "VERSION").read_text().strip()
    assert version == "chat-contract-v1", f"Expected chat-contract-v1, got {version}"


# --- Incident Contract v3 ---


def test_incident_contract_v4_schema_has_required_fields():
    """incident_report.schema.json has timestamp_seconds and label in entities/instruments/assets."""
    schema = _load("incident_report.schema.json")

    # Check entities have timestamp_seconds and label
    entity_schema = schema["properties"]["entities"]["items"]["properties"]
    assert "timestamp_seconds" in entity_schema, "entities must have timestamp_seconds"
    assert "label" in entity_schema, "entities must have label"
    assert entity_schema["timestamp_seconds"]["minimum"] == 0, (
        "timestamp_seconds min must be 0"
    )
    entity_required = schema["properties"]["entities"]["items"]["required"]
    for key in ("first_seen_seconds", "last_seen_seconds"):
        assert entity_schema[key]["type"] == "integer", f"entities {key} must be an integer"
        assert entity_schema[key]["minimum"] == 0, f"entities {key} min must be 0"
        assert key in entity_required, f"entities {key} must be required"

    # Check instruments have timestamp_seconds and label
    instrument_schema = schema["properties"]["instruments"]["items"]["properties"]
    assert "timestamp_seconds" in instrument_schema, (
        "instruments must have timestamp_seconds"
    )
    assert "label" in instrument_schema, "instruments must have label"
    assert instrument_schema["timestamp_seconds"]["minimum"] == 0

    # Check assets have timestamp_seconds and label
    asset_schema = schema["properties"]["assets"]["items"]["properties"]
    assert "timestamp_seconds" in asset_schema, "assets must have timestamp_seconds"
    assert "label" in asset_schema, "assets must have label"
    assert asset_schema["timestamp_seconds"]["minimum"] == 0

    # Check NO bounding_box in the report schema
    for sch_name in ["entities", "instruments", "assets"]:
        assert "bounding_box" not in schema["properties"][sch_name]["items"].get(
            "properties", {}
        ), f"incident-contract-v4 must NOT have bounding_box in {sch_name}"


def test_incident_contract_v3_version():
    """VERSION file matches incident-contract-v4."""
    version = (CONTRACTS_DIR / "VERSION").read_text().strip()
    assert version == "incident-contract-v4", (
        f"Expected incident-contract-v4, got {version}"
    )


# --- Cross-contract checks ---


def test_search_response_incident_metadata_present():
    """Search hits carry parent incident metadata: incident_id, playback_url, incident_window."""
    fixture = _load("search-contract-v1/fixtures/valid/full-response.json")
    for idx, hit in enumerate(fixture["hits"]):
        inc = hit["incident"]
        assert "incident_id" in inc, f"hit[{idx}].incident must have incident_id"
        assert "playback_url" in inc, f"hit[{idx}].incident must have playback_url"
        assert "incident_window" in inc, (
            f"hit[{idx}].incident must have incident_window"
        )
        assert (
            isinstance(inc["incident_window"], list)
            and len(inc["incident_window"]) == 2
        ), f"hit[{idx}].incident.incident_window must be [start, end]"


CHAT_V2 = Path(__file__).resolve().parent / "chat-contract-v2"


def test_chat_v2_response_fixtures():
    schema = json.loads((CHAT_V2 / "chat_response.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    for path in sorted((CHAT_V2 / "fixtures" / "valid").glob("response_*.json")):
        validator.validate(json.loads(path.read_text()))
    for path in sorted((CHAT_V2 / "fixtures" / "invalid").glob("response_*.json")):
        assert list(validator.iter_errors(json.loads(path.read_text()))), path.name


def test_chat_v2_request_schema_matches_v1_apart_from_its_id():
    v1 = json.loads((CHAT_V2.parent / "chat-contract-v1" / "chat_request.schema.json").read_text())
    v2 = json.loads((CHAT_V2 / "chat_request.schema.json").read_text())
    assert {k: v for k, v in v1.items() if k != "$id"} == {k: v for k, v in v2.items() if k != "$id"}
