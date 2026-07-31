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

"""Unit tests for ``utils.event_utils``.

Alert-style payloads arrive with the nested MDX shape (``sensor.id``,
``analyticsModule.id``) while the rest of the pipeline keys off flat
``sensorId`` / ``category`` fields. ``normalize_alert_message`` bridges the
two and records what it injected in ``_normalized_added_fields`` so
``strip_normalization_fields`` can undo it before the document is published.

The round-trip property matters: whatever normalization adds, stripping must
remove, so an alert document reaching Elasticsearch is not polluted with
synthetic keys. These helpers are called from the vlm_enhanced_sink base and
elastic sinks on every event.
"""

import pytest

from utils.event_utils import (
    get_notification_type,
    is_alert,
    normalize_alert_message,
    strip_normalization_fields,
)


class TestNormalizeAlertMessage:
    """Injection of the flat helper fields."""

    def test_non_dict_input_is_returned_unchanged(self):
        assert normalize_alert_message("not-a-dict") == "not-a-dict"
        assert normalize_alert_message(None) is None
        assert normalize_alert_message([1, 2]) == [1, 2]

    def test_message_without_nested_blocks_is_untouched(self):
        message = {"id": "evt-1", "category": "existing"}
        assert normalize_alert_message(message) == message

    def test_nested_blocks_without_ids_are_untouched(self):
        message = {"sensor": {"type": "camera"}, "analyticsModule": {"version": "1"}}
        assert normalize_alert_message(message) == message

    def test_non_dict_nested_blocks_are_ignored(self):
        """A scalar ``sensor`` must not be treated as the nested MDX block."""
        message = {"sensor": "cam-1", "analyticsModule": ["x"]}
        assert normalize_alert_message(message) == message

    def test_injects_sensor_id_and_category(self):
        result = normalize_alert_message(
            {"sensor": {"id": "cam-1"}, "analyticsModule": {"id": "intrusion"}}
        )
        assert result["sensorId"] == "cam-1"
        assert result["category"] == "intrusion"
        assert result["notification_type"] == "alert"
        assert set(result["_normalized_added_fields"]) == {"sensorId", "category"}

    def test_injects_sensor_id_only(self):
        result = normalize_alert_message({"sensor": {"id": "cam-1"}, "analyticsModule": {}})
        assert result["sensorId"] == "cam-1"
        assert "category" not in result
        assert result["_normalized_added_fields"] == ["sensorId"]

    def test_injects_category_only(self):
        result = normalize_alert_message({"sensor": {}, "analyticsModule": {"id": "loitering"}})
        assert result["category"] == "loitering"
        assert "sensorId" not in result
        assert result["_normalized_added_fields"] == ["category"]

    def test_does_not_overwrite_existing_flat_fields(self):
        result = normalize_alert_message(
            {
                "sensor": {"id": "cam-1"},
                "analyticsModule": {"id": "intrusion"},
                "sensorId": "already-set",
                "category": "already-set",
            }
        )
        assert result["sensorId"] == "already-set"
        assert result["category"] == "already-set"
        assert result.get("_normalized_added_fields") is None

    def test_does_not_overwrite_existing_notification_type(self):
        result = normalize_alert_message(
            {"sensor": {"id": "cam-1"}, "analyticsModule": {}, "notification_type": "incident"}
        )
        assert result["notification_type"] == "incident"

    def test_injects_object_ids_from_object_block(self):
        result = normalize_alert_message(
            {"sensor": {"id": "cam-1"}, "analyticsModule": {}, "object": {"id": "obj-7"}}
        )
        assert result["objectIds"] == ["obj-7"]
        assert "objectIds" in result["_normalized_added_fields"]

    @pytest.mark.parametrize("obj_id", [None, ""])
    def test_blank_object_id_is_not_injected(self, obj_id):
        result = normalize_alert_message(
            {"sensor": {"id": "cam-1"}, "analyticsModule": {}, "object": {"id": obj_id}}
        )
        assert "objectIds" not in result

    def test_existing_object_ids_are_preserved(self):
        result = normalize_alert_message(
            {
                "sensor": {"id": "cam-1"},
                "analyticsModule": {},
                "object": {"id": "obj-7"},
                "objectIds": ["kept"],
            }
        )
        assert result["objectIds"] == ["kept"]

    def test_original_message_is_not_mutated(self):
        message = {"sensor": {"id": "cam-1"}, "analyticsModule": {"id": "intrusion"}}
        normalize_alert_message(message)
        assert "sensorId" not in message
        assert "notification_type" not in message

    def test_tracker_merges_with_pre_existing_entries(self):
        result = normalize_alert_message(
            {
                "sensor": {"id": "cam-1"},
                "analyticsModule": {"id": "intrusion"},
                "_normalized_added_fields": ["earlier"],
            }
        )
        assert set(result["_normalized_added_fields"]) == {"earlier", "sensorId", "category"}

    def test_analytics_module_without_sensor_is_normalized(self):
        """nv.Incident shape: flat ``sensorId`` plus ``analyticsModule``, no ``sensor``."""
        result = normalize_alert_message(
            {
                "sensorId": "HWY_20_AND_LOCUST__WBA",
                "category": "collision",
                "analyticsModule": {"id": "Collision Detection Module"},
            }
        )

        assert result["sensorId"] == "HWY_20_AND_LOCUST__WBA"
        assert result["category"] == "collision"
        assert result["notification_type"] == "alert"

    def test_sensor_without_analytics_module_is_normalized(self):
        """The mirror case: ``sensor`` present, ``analyticsModule`` absent."""
        result = normalize_alert_message({"sensor": {"id": "cam-1"}})

        assert result["sensorId"] == "cam-1"
        assert "category" not in result
        assert result["notification_type"] == "alert"

    def test_object_only_message_is_normalized(self):
        """An ``object``-only payload should yield ``objectIds``, not raise."""
        result = normalize_alert_message({"object": {"id": "obj-1"}})

        assert result["objectIds"] == ["obj-1"]
        assert result["notification_type"] == "alert"


class TestStripNormalizationFields:
    def test_non_dict_input_is_returned_unchanged(self):
        assert strip_normalization_fields("not-a-dict") == "not-a-dict"
        assert strip_normalization_fields(None) is None

    def test_removes_tracked_fields_and_the_tracker(self):
        doc = {
            "id": "evt-1",
            "sensorId": "cam-1",
            "category": "intrusion",
            "_normalized_added_fields": ["sensorId", "category"],
        }
        assert strip_normalization_fields(doc) == {"id": "evt-1"}

    def test_untracked_fields_are_kept(self):
        doc = {
            "sensorId": "cam-1",
            "category": "intrusion",
            "_normalized_added_fields": ["sensorId"],
        }
        result = strip_normalization_fields(doc)
        assert result == {"category": "intrusion"}

    def test_missing_tracker_leaves_document_intact(self):
        doc = {"id": "evt-1", "sensorId": "cam-1"}
        assert strip_normalization_fields(doc) == doc

    def test_null_tracker_is_tolerated(self):
        doc = {"id": "evt-1", "_normalized_added_fields": None}
        assert strip_normalization_fields(doc) == {"id": "evt-1"}

    def test_tracked_field_already_absent_is_tolerated(self):
        doc = {"id": "evt-1", "_normalized_added_fields": ["sensorId"]}
        assert strip_normalization_fields(doc) == {"id": "evt-1"}

    def test_original_document_is_not_mutated(self):
        doc = {"sensorId": "cam-1", "_normalized_added_fields": ["sensorId"]}
        strip_normalization_fields(doc)
        assert doc["sensorId"] == "cam-1"

    def test_notification_type_survives_stripping(self):
        """``notification_type`` is not tracked, so it is not removed."""
        doc = {"notification_type": "alert", "_normalized_added_fields": []}
        assert strip_normalization_fields(doc) == {"notification_type": "alert"}


class TestNormalizeStripRoundTrip:
    def test_strip_undoes_the_injected_id_fields(self):
        original = {
            "id": "evt-1",
            "sensor": {"id": "cam-1"},
            "analyticsModule": {"id": "intrusion"},
            "object": {"id": "obj-7"},
        }
        stripped = strip_normalization_fields(normalize_alert_message(original))

        assert "sensorId" not in stripped
        assert "category" not in stripped
        assert "objectIds" not in stripped
        assert stripped["sensor"] == {"id": "cam-1"}
        assert stripped["id"] == "evt-1"


class TestIsAlert:
    def test_true_for_explicit_alert_marker(self):
        assert is_alert({"notification_type": "alert"}) is True

    def test_false_for_incident_marker(self):
        assert is_alert({"notification_type": "incident"}) is False

    def test_false_without_marker(self):
        assert is_alert({"sensor": {"id": "cam-1"}}) is False

    @pytest.mark.parametrize("value", ["not-a-dict", None, 42, ["alert"]])
    def test_false_for_non_dict_input(self, value):
        assert is_alert(value) is False

    def test_true_after_normalization(self):
        normalized = normalize_alert_message({"sensor": {"id": "cam-1"}, "analyticsModule": {}})
        assert is_alert(normalized) is True


class TestGetNotificationType:
    def test_alert_for_marked_message(self):
        assert get_notification_type({"notification_type": "alert"}) == "alert"

    def test_incident_for_unmarked_message(self):
        assert get_notification_type({"id": "evt-1"}) == "incident"

    def test_incident_for_non_dict_input(self):
        assert get_notification_type(None) == "incident"
