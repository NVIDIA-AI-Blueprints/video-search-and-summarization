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

"""Unit tests for ``utils.field_extractor``.

This is the schema-file-driven variant of the extractor (the config-dict
variant lives in ``utils.schema_util``). It is what ``StreamMessage`` uses to
pull core fields out of an arbitrary customer payload, so its failure mode is
what matters most: when the schema file is missing or unreadable, extraction
must fall back to the built-in MDX field paths rather than returning nothing.

``load_schema`` memoises into a module-level dict; the ``clear_schema_cache``
fixture resets it so tests cannot leak state into each other.
"""

import os
from unittest.mock import mock_open, patch

import pytest

import utils.field_extractor as fe
from utils.field_extractor import (
    _extract_fields_fallback,
    _format_template,
    extract_core_fields,
    format_output_message,
    get_nested_field,
    load_schema,
    validate_required_fields,
)

REQUEST_SCHEMA = {
    "fields": {
        "message_id": "eventId",
        "timestamp": "timestamp",
        "sensor_id": "sensor.id",
        "vehicle_id": "object.id",
        "anomaly_type": "analyticsModule.id",
        "stream_id": "streamId",
        "alert_type": "alertType",
        "media_file_path": "mediaFilePath",
    },
    "defaults": {
        "anomaly_type": "unknown",
        "stream_id": "default_stream",
        "alert_type": "general",
    },
    "required_fields": ["eventId", "sensor.id"],
}

SAMPLE_EVENT = {
    "eventId": "evt-1",
    "timestamp": "2021-01-01T00:00:00Z",
    "sensor": {"id": "cam-1"},
    "object": {"id": "obj-1"},
    "analyticsModule": {"id": "intrusion"},
    "streamId": "stream-9",
    "alertType": "fire",
    "mediaFilePath": "/videos/a.mp4",
}


@pytest.fixture(autouse=True)
def clear_schema_cache():
    fe._schema_cache.clear()
    yield
    fe._schema_cache.clear()


@pytest.fixture
def loaded_schema():
    """Pre-seed the cache so no file system access happens."""
    fe._schema_cache["request_schema.yaml"] = REQUEST_SCHEMA
    return REQUEST_SCHEMA


class TestLoadSchema:
    def test_reads_and_parses_a_relative_schema_file(self):
        with patch("builtins.open", mock_open(read_data="fields:\n  message_id: eventId\n")):
            schema = load_schema("request_schema.yaml")
        assert schema == {"fields": {"message_id": "eventId"}}

    def test_relative_paths_resolve_under_the_schemas_directory(self):
        with patch("builtins.open", mock_open(read_data="fields: {}\n")) as opener:
            load_schema("request_schema.yaml")

        opened = opener.call_args[0][0]
        assert opened.endswith(os.path.join("schemas", "request_schema.yaml"))

    def test_absolute_paths_are_used_verbatim(self):
        with patch("builtins.open", mock_open(read_data="fields: {}\n")) as opener:
            load_schema("/etc/alert/custom.yaml")
        assert opener.call_args[0][0] == "/etc/alert/custom.yaml"

    def test_result_is_cached(self):
        with patch("builtins.open", mock_open(read_data="fields: {}\n")) as opener:
            load_schema("request_schema.yaml")
            load_schema("request_schema.yaml")
        assert opener.call_count == 1

    def test_missing_file_returns_an_empty_schema(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert load_schema("nope.yaml") == {}

    def test_malformed_yaml_returns_an_empty_schema(self):
        with patch("builtins.open", mock_open(read_data="fields: [unclosed\n")):
            assert load_schema("bad.yaml") == {}

    def test_failures_are_not_cached(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            load_schema("nope.yaml")
        assert "nope.yaml" not in fe._schema_cache


class TestGetNestedField:
    def test_reads_a_dotted_path(self):
        assert get_nested_field(SAMPLE_EVENT, "sensor.id") == "cam-1"

    def test_missing_key_returns_the_default(self):
        assert get_nested_field(SAMPLE_EVENT, "sensor.missing", "fallback") == "fallback"

    @pytest.mark.parametrize("field_path", ["", None])
    def test_empty_path_returns_the_default(self, field_path):
        assert get_nested_field(SAMPLE_EVENT, field_path, "fallback") == "fallback"

    def test_path_through_a_scalar_returns_the_default(self):
        assert get_nested_field({"sensor": "cam-1"}, "sensor.id", "fallback") == "fallback"

    def test_falsy_stored_value_is_returned(self):
        assert get_nested_field({"count": 0}, "count", 99) == 0


class TestExtractCoreFields:
    def test_maps_every_field_through_the_schema(self, loaded_schema):
        result = extract_core_fields(SAMPLE_EVENT)

        assert result == {
            "message_id": "evt-1",
            "timestamp": "2021-01-01T00:00:00Z",
            "sensor_id": "cam-1",
            "vehicle_id": "obj-1",
            "anomaly_type": "intrusion",
            "stream_id": "stream-9",
            "alert_type": "fire",
            "media_file_path": "/videos/a.mp4",
        }

    def test_schema_defaults_fill_the_three_defaulted_fields(self, loaded_schema):
        result = extract_core_fields({})

        assert result["anomaly_type"] == "unknown"
        assert result["stream_id"] == "default_stream"
        assert result["alert_type"] == "general"
        assert result["message_id"] is None
        assert result["sensor_id"] is None

    def test_unloadable_schema_falls_back_to_builtin_paths(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = extract_core_fields(SAMPLE_EVENT, "nope.yaml")

        assert result["message_id"] == "evt-1"
        assert result["sensor_id"] == "cam-1"
        assert result["anomaly_type"] == "intrusion"

    def test_schema_without_a_fields_block_yields_defaults_only(self):
        fe._schema_cache["partial.yaml"] = {"defaults": {"alert_type": "general"}}
        result = extract_core_fields(SAMPLE_EVENT, "partial.yaml")

        assert result["message_id"] is None
        assert result["alert_type"] == "general"


class TestExtractFieldsFallback:
    def test_uses_the_builtin_mdx_paths(self):
        result = _extract_fields_fallback(SAMPLE_EVENT)

        assert result["message_id"] == "evt-1"
        assert result["vehicle_id"] == "obj-1"
        assert result["media_file_path"] == "/videos/a.mp4"

    def test_hardcoded_defaults_apply_to_an_empty_event(self):
        result = _extract_fields_fallback({})

        assert result["anomaly_type"] == "unknown"
        assert result["stream_id"] == "default_stream"
        assert result["alert_type"] == "general"


class TestValidateRequiredFields:
    def test_passes_when_all_required_fields_are_present(self, loaded_schema):
        assert validate_required_fields(SAMPLE_EVENT) is True

    def test_fails_when_a_required_field_is_missing(self, loaded_schema):
        assert validate_required_fields({"eventId": "evt-1"}) is False

    def test_unloadable_schema_allows_processing_to_continue(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert validate_required_fields({}, "nope.yaml") is True

    def test_schema_without_required_fields_passes(self):
        fe._schema_cache["empty.yaml"] = {"fields": {}}
        assert validate_required_fields({}, "empty.yaml") is True


class TestFormatTemplate:
    def test_placeholders_are_substituted(self):
        assert _format_template("{sensor_id}", {"sensor_id": "cam-1"}) == "cam-1"

    def test_unknown_placeholder_is_left_verbatim(self):
        assert _format_template("{nope}", {}) == "{nope}"

    def test_plain_strings_pass_through(self):
        assert _format_template("literal", {"literal": "x"}) == "literal"

    def test_nested_dicts_are_walked(self):
        template = {"outer": {"inner": "{sensor_id}"}}
        assert _format_template(template, {"sensor_id": "cam-1"}) == {"outer": {"inner": "cam-1"}}

    def test_lists_are_walked(self):
        assert _format_template(["{a}", "{b}"], {"a": 1, "b": 2}) == [1, 2]

    def test_non_string_scalars_pass_through(self):
        assert _format_template(42, {}) == 42
        assert _format_template(None, {}) is None


class TestFormatOutputMessage:
    RESPONSE_SCHEMA = {
        "output_template": {
            "id": "{message_id}",
            "sensor": {"id": "{sensor_id}"},
            "verdict": "{verdict}",
            "kind": "alert",
        },
        "output_defaults": {"verdict": "unknown"},
    }

    @pytest.fixture
    def response_schema(self):
        fe._schema_cache["response_schema.yaml"] = self.RESPONSE_SCHEMA
        return self.RESPONSE_SCHEMA

    def test_renders_the_template_from_core_fields(self, response_schema):
        result = format_output_message({"message_id": "evt-1", "sensor_id": "cam-1"})

        assert result == {
            "id": "evt-1",
            "sensor": {"id": "cam-1"},
            "verdict": "unknown",
            "kind": "alert",
        }

    def test_enhanced_data_overrides_core_fields(self, response_schema):
        result = format_output_message(
            {"message_id": "evt-1", "verdict": "core"}, {"verdict": "confirmed"}
        )
        assert result["verdict"] == "confirmed"

    def test_defaults_do_not_override_supplied_values(self, response_schema):
        result = format_output_message({"message_id": "evt-1", "verdict": "confirmed"})
        assert result["verdict"] == "confirmed"

    def test_unloadable_schema_returns_the_core_fields_unchanged(self):
        core = {"message_id": "evt-1"}
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert format_output_message(core, schema_file="nope.yaml") == core

    def test_schema_without_a_template_yields_an_empty_message(self):
        fe._schema_cache["bare.yaml"] = {"output_defaults": {}}
        assert format_output_message({"message_id": "evt-1"}, schema_file="bare.yaml") == {}

    def test_template_failure_falls_back_to_the_merged_data(self, response_schema):
        core = {"message_id": "evt-1"}
        with patch("utils.field_extractor._format_template", side_effect=RuntimeError("boom")):
            result = format_output_message(core)

        assert result["message_id"] == "evt-1"
        assert result["verdict"] == "unknown"
