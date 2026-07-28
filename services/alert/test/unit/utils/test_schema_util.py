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

"""Unit tests for ``utils.schema_util``.

This module is the JSON <-> nvSchema Protobuf boundary for the Alert Bridge:
the Kafka source decodes anomalies through it and the Kafka sink re-encodes
enriched documents through it. Two behaviours carry most of the risk and are
covered here in depth:

* ``_stringify_map_values`` — protobuf ``map<string, string>`` fields reject
  non-string values, so every ``info`` block must be flattened before
  ``ParseDict``. Nested dicts/lists become JSON text and ``None`` becomes
  ``""`` rather than the string ``"None"``.
* ``get_nested_field`` — the dotted-path reader behind ``extract_core_fields``
  and ``validate_required_fields``, both driven by operator-supplied schema
  config, so malformed paths must yield the default instead of raising.
"""

import json
from datetime import datetime

import pytest
from google.protobuf.message import DecodeError

from mdx.protobuf import Behavior as nvSchemaBehavior, Incident as nvSchemaIncident
from utils.schema_util import (
    _stringify_map_values,
    convert_behavior_to_protobuf_behavior,
    convert_detected_event_to_incident,
    convert_incident_to_protobuf_incident,
    convert_json_to_protobuf,
    extract_core_fields,
    get_nested_field,
    map_geo_location,
    place_to_nv_place,
    protobuf_anomalies_to_json_string_list,
    protobuf_anomaly_to_json_string,
    validate_required_fields,
)


class TestProtobufAnomalyToJsonString:
    def test_decodes_incident_payload(self):
        payload = nvSchemaIncident(sensorId="cam-1", category="intrusion").SerializeToString()
        result = json.loads(protobuf_anomaly_to_json_string(payload, "incident"))
        assert result["sensorId"] == "cam-1"
        assert result["category"] == "intrusion"

    def test_message_type_is_case_insensitive(self):
        payload = nvSchemaIncident(sensorId="cam-1").SerializeToString()
        assert json.loads(protobuf_anomaly_to_json_string(payload, "INCIDENT"))["sensorId"] == "cam-1"

    def test_defaults_to_behavior_for_other_message_types(self):
        payload = nvSchemaBehavior(id="beh-1", direction="north").SerializeToString()
        result = json.loads(protobuf_anomaly_to_json_string(payload, "behavior"))
        assert result["id"] == "beh-1"
        assert result["direction"] == "north"

    def test_default_valued_fields_are_still_emitted(self):
        """``always_print_fields_with_no_presence`` keeps the schema shape stable."""
        payload = nvSchemaBehavior(id="beh-1").SerializeToString()
        result = json.loads(protobuf_anomaly_to_json_string(payload, "behavior"))
        assert result["direction"] == ""
        assert result["distance"] == 0

    def test_undecodable_payload_reraises_decode_error(self):
        with pytest.raises(DecodeError):
            protobuf_anomaly_to_json_string(b"\xff\xfe\xfd\xfc", "incident")


class TestProtobufAnomaliesToJsonStringList:
    def test_flattens_all_partitions(self):
        batch = {
            "topic-0": [
                (b"k1", nvSchemaIncident(sensorId="cam-1").SerializeToString(), 1700000000000),
                (b"k2", nvSchemaIncident(sensorId="cam-2").SerializeToString(), 1700000000001),
            ],
            "topic-1": [(b"k3", nvSchemaIncident(sensorId="cam-3").SerializeToString(), None)],
        }
        result = protobuf_anomalies_to_json_string_list(batch, "incident")

        assert len(result) == 3
        assert {json.loads(r)["sensorId"] for r in result} == {"cam-1", "cam-2", "cam-3"}

    def test_tuples_without_timestamp_are_accepted(self):
        """Older broker paths emit ``(key, value)`` with no timestamp element."""
        batch = {"topic-0": [(b"k1", nvSchemaIncident(sensorId="cam-1").SerializeToString())]}
        assert len(protobuf_anomalies_to_json_string_list(batch, "incident")) == 1

    def test_empty_batch_yields_empty_list(self):
        assert protobuf_anomalies_to_json_string_list({}, "incident") == []

    def test_empty_partition_yields_empty_list(self):
        assert protobuf_anomalies_to_json_string_list({"topic-0": []}, "incident") == []


class TestConvertJsonToProtobuf:
    def test_converts_behavior_dict(self):
        proto = convert_json_to_protobuf({"id": "beh-1", "direction": "north"})
        assert isinstance(proto, nvSchemaBehavior)
        assert proto.id == "beh-1"
        assert proto.direction == "north"

    def test_empty_dict_yields_default_message(self):
        assert convert_json_to_protobuf({}).id == ""

    def test_unknown_field_reraises(self):
        """``ParseDict`` is strict here — unlike the incident converter."""
        with pytest.raises(Exception):
            convert_json_to_protobuf({"definitelyNotAField": 1})


class TestStringifyMapValues:
    def test_nested_dict_becomes_json_text(self):
        target = {"meta": {"a": 1}}
        _stringify_map_values(target)
        assert target["meta"] == '{"a": 1}'

    def test_list_becomes_json_text(self):
        target = {"tags": ["a", "b"]}
        _stringify_map_values(target)
        assert target["tags"] == '["a", "b"]'

    def test_none_becomes_empty_string_not_the_word_none(self):
        target = {"missing": None}
        _stringify_map_values(target)
        assert target["missing"] == ""

    @pytest.mark.parametrize(
        "value,expected",
        [(42, "42"), (3.5, "3.5"), (True, "True"), (False, "False"), ("kept", "kept")],
    )
    def test_scalars_are_stringified(self, value, expected):
        target = {"k": value}
        _stringify_map_values(target)
        assert target["k"] == expected

    def test_mutates_in_place(self):
        target = {"a": 1}
        assert _stringify_map_values(target) is None
        assert target == {"a": "1"}

    def test_empty_dict_is_tolerated(self):
        target = {}
        _stringify_map_values(target)
        assert target == {}


class TestConvertIncidentToProtobufIncident:
    def test_converts_core_fields(self):
        proto = convert_incident_to_protobuf_incident(
            {"sensorId": "cam-1", "category": "intrusion", "isAnomaly": True}
        )
        assert isinstance(proto, nvSchemaIncident)
        assert proto.sensorId == "cam-1"
        assert proto.category == "intrusion"
        assert proto.isAnomaly is True

    def test_unknown_fields_are_ignored_by_default(self):
        """VLM enrichment adds keys the nvSchema does not declare."""
        proto = convert_incident_to_protobuf_incident(
            {"sensorId": "cam-1", "vlmVerdict": "confirmed"}
        )
        assert proto.sensorId == "cam-1"

    def test_unknown_fields_raise_when_strict(self):
        with pytest.raises(Exception):
            convert_incident_to_protobuf_incident(
                {"sensorId": "cam-1", "vlmVerdict": "confirmed"},
                ignore_unknown_fields=False,
            )

    def test_info_block_values_are_stringified(self):
        proto = convert_incident_to_protobuf_incident(
            {
                "sensorId": "cam-1",
                "info": {"count": 3, "nested": {"a": 1}, "blank": None},
            }
        )
        assert proto.info["count"] == "3"
        assert proto.info["nested"] == '{"a": 1}'
        assert proto.info["blank"] == ""

    def test_non_dict_info_block_is_left_alone(self):
        """A scalar ``info`` is not a map, so ParseDict rejects it."""
        with pytest.raises(Exception):
            convert_incident_to_protobuf_incident({"sensorId": "cam-1", "info": "scalar"})

    def test_input_dict_is_not_mutated(self):
        incident = {"sensorId": "cam-1", "info": {"count": 3}}
        convert_incident_to_protobuf_incident(incident)
        assert incident["info"]["count"] == 3

    def test_object_ids_are_preserved(self):
        proto = convert_incident_to_protobuf_incident(
            {"sensorId": "cam-1", "objectIds": ["o1", "o2"]}
        )
        assert list(proto.objectIds) == ["o1", "o2"]


class TestPlaceToNvPlace:
    def test_maps_name(self):
        assert place_to_nv_place({"name": "gate-3"}).name == "gate-3"

    def test_missing_name_defaults_to_empty(self):
        assert place_to_nv_place({}).name == ""

    def test_none_raises_attribute_error(self):
        """Signature says ``Optional[dict]`` but ``None`` is dereferenced."""
        with pytest.raises(AttributeError):
            place_to_nv_place(None)


class TestMapGeoLocation:
    def test_maps_type_and_coordinates(self):
        behavior = nvSchemaBehavior()
        map_geo_location(
            behavior.locations,
            {"type": "Point", "coordinates": [{"point": [1.0, 2.0]}, {"point": [3.0, 4.0]}]},
        )
        assert behavior.locations.type == "Point"
        assert len(behavior.locations.coordinates) == 2
        assert list(behavior.locations.coordinates[0].point) == [1.0, 2.0]

    def test_missing_type_defaults_to_empty(self):
        behavior = nvSchemaBehavior()
        map_geo_location(behavior.locations, {"coordinates": []})
        assert behavior.locations.type == ""

    def test_coordinate_without_point_key_is_empty(self):
        behavior = nvSchemaBehavior()
        map_geo_location(behavior.locations, {"type": "Point", "coordinates": [{}]})
        assert list(behavior.locations.coordinates[0].point) == []

    @pytest.mark.parametrize("empty", [None, {}])
    def test_falsy_input_is_a_noop(self, empty):
        behavior = nvSchemaBehavior()
        map_geo_location(behavior.locations, empty)
        assert behavior.locations.type == ""
        assert len(behavior.locations.coordinates) == 0


class TestConvertBehaviorToProtobufBehavior:
    def _full_behavior(self):
        return {
            "id": "beh-1",
            "edges": ["e1", "e2"],
            "distance": 12.5,
            "speed": 3.25,
            "speedOverTime": [1.0, 2.0],
            "timeInterval": 4.0,
            "bearing": 90.0,
            "direction": "north",
            "length": 7,
            "timestamp": "2025-01-02T03:04:05Z",
            "end": "2025-01-02T03:05:05Z",
            "locations": {"type": "Point", "coordinates": [{"point": [1.0, 2.0]}]},
            "smoothLocations": {"type": "Line", "coordinates": []},
            "place": {"name": "gate-3"},
            "sensor": {"id": "cam-1"},
            "analyticsModule": {"id": "intrusion", "info": {"threshold": 0.8}},
            "object": {"id": "obj-1", "type": "person", "confidence": 0.91},
            "videoPath": "/videos/a.mp4",
            "info": {"note": "manual", "extra": {"k": 1}},
            "embeddings": [{"vector": [0.1, 0.2]}],
            "dropped": True,
        }

    def test_maps_scalar_fields(self):
        proto = convert_behavior_to_protobuf_behavior(self._full_behavior())
        assert proto.id == "beh-1"
        assert list(proto.edges) == ["e1", "e2"]
        assert proto.distance == pytest.approx(12.5)
        assert proto.speed == pytest.approx(3.25)
        assert list(proto.speedOverTime) == pytest.approx([1.0, 2.0])
        assert proto.timeInterval == pytest.approx(4.0)
        assert proto.bearing == pytest.approx(90.0)
        assert proto.direction == "north"
        assert proto.length == 7
        assert proto.videoPath == "/videos/a.mp4"

    def test_maps_timestamps(self):
        proto = convert_behavior_to_protobuf_behavior(self._full_behavior())
        assert proto.timestamp.ToJsonString() == "2025-01-02T03:04:05Z"
        assert proto.end.ToJsonString() == "2025-01-02T03:05:05Z"

    def test_maps_nested_entities(self):
        proto = convert_behavior_to_protobuf_behavior(self._full_behavior())
        assert proto.place.name == "gate-3"
        assert proto.sensor.id == "cam-1"
        assert proto.analyticsModule.id == "intrusion"
        assert proto.object.id == "obj-1"
        assert proto.object.type == "person"
        assert proto.object.confidence == pytest.approx(0.91)

    def test_maps_locations(self):
        proto = convert_behavior_to_protobuf_behavior(self._full_behavior())
        assert proto.locations.type == "Point"
        assert list(proto.locations.coordinates[0].point) == pytest.approx([1.0, 2.0])
        assert proto.smoothLocations.type == "Line"

    def test_dropped_flag_is_recorded_on_analytics_module_info(self):
        proto = convert_behavior_to_protobuf_behavior(self._full_behavior())
        assert proto.analyticsModule.info["dropped"] == "True"

    def test_dropped_defaults_to_false_string(self):
        proto = convert_behavior_to_protobuf_behavior({})
        assert proto.analyticsModule.info["dropped"] == "False"

    def test_analytics_module_info_values_are_stringified(self):
        proto = convert_behavior_to_protobuf_behavior(self._full_behavior())
        assert proto.analyticsModule.info["threshold"] == "0.8"

    def test_info_block_values_are_stringified(self):
        proto = convert_behavior_to_protobuf_behavior(self._full_behavior())
        assert proto.info["note"] == "manual"
        assert proto.info["extra"] == '{"k": 1}'

    def test_maps_embeddings(self):
        proto = convert_behavior_to_protobuf_behavior(self._full_behavior())
        assert len(proto.embeddings) == 1
        assert list(proto.embeddings[0].vector) == pytest.approx([0.1, 0.2])

    def test_empty_behavior_uses_defaults(self):
        proto = convert_behavior_to_protobuf_behavior({})
        assert proto.id == ""
        assert proto.direction == ""
        assert proto.distance == 0.0
        assert proto.timestamp.ToJsonString() == "1970-01-01T00:00:00Z"
        assert proto.end.ToJsonString() == "1970-01-01T00:00:00Z"

    def test_non_dict_info_block_is_skipped(self):
        proto = convert_behavior_to_protobuf_behavior({"info": "scalar"})
        assert len(proto.info) == 0


class TestConvertDetectedEventToIncident:
    def _entity(self, **overrides):
        entity = {
            "timeStamp": "2025-01-02T03:04:05.123456Z",
            "sensorName": "cam-1",
            "sensorLocation": "gate-3",
        }
        entity.update(overrides)
        return entity

    def test_maps_required_fields(self):
        incident = convert_detected_event_to_incident(self._entity())
        assert incident.sensorId == "cam-1"
        assert incident.isAnomaly is True
        assert incident.category == "Others"
        assert list(incident.objectIds) == ["999"]
        assert incident.place.name == "gate-3"

    def test_end_is_two_minutes_after_start(self):
        incident = convert_detected_event_to_incident(self._entity())
        assert incident.end.ToSeconds() - incident.timestamp.ToSeconds() == 120

    def test_accepts_datetime_timestamp(self):
        incident = convert_detected_event_to_incident(
            self._entity(timeStamp=datetime(2025, 1, 2, 3, 4, 5))
        )
        assert incident.timestamp.ToJsonString().startswith("2025-01-02T03:04:05")

    def test_missing_sensor_location_falls_back(self):
        entity = self._entity()
        del entity["sensorLocation"]
        assert convert_detected_event_to_incident(entity).place.name == "unknown_location"

    def test_vlm_response_populates_info(self):
        incident = convert_detected_event_to_incident(
            self._entity(
                prompt="is there a fire?",
                vlmResponse={
                    "response": [
                        {"content": "smoke visible", "metadata": {"scenario_detected": True}}
                    ]
                },
            )
        )
        assert incident.info["vlm_description"] == "smoke visible"
        assert incident.info["scenario_detected"] == "True"
        assert incident.info["sampling_prompt"] == "is there a fire?"

    def test_vlm_response_without_metadata_defaults_to_false(self):
        incident = convert_detected_event_to_incident(
            self._entity(vlmResponse={"response": [{"content": "nothing"}]})
        )
        assert incident.info["scenario_detected"] == "False"
        assert incident.info["sampling_prompt"] == ""

    def test_missing_vlm_response_leaves_info_empty(self):
        assert len(convert_detected_event_to_incident(self._entity()).info) == 0

    def test_vlm_response_without_response_key_leaves_info_empty(self):
        incident = convert_detected_event_to_incident(self._entity(vlmResponse={"other": 1}))
        assert len(incident.info) == 0

    def test_missing_sensor_name_reraises(self):
        entity = self._entity()
        del entity["sensorName"]
        with pytest.raises(KeyError):
            convert_detected_event_to_incident(entity)

    def test_malformed_timestamp_reraises(self):
        with pytest.raises(ValueError):
            convert_detected_event_to_incident(self._entity(timeStamp="not-a-timestamp"))


class TestGetNestedField:
    def test_reads_single_level(self):
        assert get_nested_field({"id": "evt-1"}, "id") == "evt-1"

    def test_reads_dotted_path(self):
        assert get_nested_field({"sensor": {"id": "cam-1"}}, "sensor.id") == "cam-1"

    def test_reads_deeply_nested_path(self):
        data = {"a": {"b": {"c": {"d": "deep"}}}}
        assert get_nested_field(data, "a.b.c.d") == "deep"

    def test_missing_key_returns_default(self):
        assert get_nested_field({"sensor": {}}, "sensor.id", "unknown") == "unknown"

    def test_default_is_none_when_unspecified(self):
        assert get_nested_field({}, "sensor.id") is None

    def test_path_through_a_scalar_returns_default(self):
        assert get_nested_field({"sensor": "cam-1"}, "sensor.id", "fallback") == "fallback"

    @pytest.mark.parametrize("field_path", ["", None])
    def test_empty_path_returns_default(self, field_path):
        assert get_nested_field({"id": "evt-1"}, field_path, "fallback") == "fallback"

    def test_falsy_stored_value_is_returned_not_the_default(self):
        assert get_nested_field({"count": 0}, "count", 99) == 0

    def test_explicit_null_value_is_returned(self):
        assert get_nested_field({"sensor": {"id": None}}, "sensor.id", "fallback") is None

    def test_non_dict_data_returns_default(self):
        assert get_nested_field("not-a-dict", "sensor.id", "fallback") == "fallback"


class TestExtractCoreFields:
    SCHEMA = {
        "schema_fields": {
            "message_id": "id",
            "timestamp": "@timestamp",
            "sensor_id": "sensor.id",
            "vehicle_id": "object.id",
            "anomaly_type": "analyticsModule.id",
            "stream_id": "sensor.streamId",
            "alert_type": "alert.type",
            "media_file_path": "videoPath",
            "defaults": {"anomaly_type": "unknown-anomaly", "stream_id": "default-stream"},
        }
    }

    def test_extracts_every_configured_field(self):
        data = {
            "id": "evt-1",
            "@timestamp": "2025-01-02T03:04:05Z",
            "sensor": {"id": "cam-1", "streamId": "stream-9"},
            "object": {"id": "obj-1"},
            "analyticsModule": {"id": "intrusion"},
            "alert": {"type": "fire"},
            "videoPath": "/videos/a.mp4",
        }
        result = extract_core_fields(data, self.SCHEMA)

        assert result == {
            "message_id": "evt-1",
            "timestamp": "2025-01-02T03:04:05Z",
            "sensor_id": "cam-1",
            "vehicle_id": "obj-1",
            "anomaly_type": "intrusion",
            "stream_id": "stream-9",
            "alert_type": "fire",
            "media_file_path": "/videos/a.mp4",
        }

    def test_defaults_apply_only_to_anomaly_type_and_stream_id(self):
        result = extract_core_fields({}, self.SCHEMA)
        assert result["anomaly_type"] == "unknown-anomaly"
        assert result["stream_id"] == "default-stream"
        assert result["message_id"] is None
        assert result["sensor_id"] is None

    def test_empty_schema_yields_all_none(self):
        result = extract_core_fields({"id": "evt-1"}, {})
        assert set(result) == {
            "message_id",
            "timestamp",
            "sensor_id",
            "vehicle_id",
            "anomaly_type",
            "stream_id",
            "alert_type",
            "media_file_path",
        }
        assert all(value is None for value in result.values())


class TestValidateRequiredFields:
    def test_passes_when_every_required_field_is_present(self):
        config = {"required_fields": ["id", "sensor.id"]}
        assert validate_required_fields({"id": "evt-1", "sensor": {"id": "cam-1"}}, config) is True

    def test_fails_when_a_required_field_is_missing(self):
        config = {"required_fields": ["id", "sensor.id"]}
        assert validate_required_fields({"id": "evt-1"}, config) is False

    def test_fails_when_a_required_field_is_explicitly_null(self):
        config = {"required_fields": ["sensor.id"]}
        assert validate_required_fields({"sensor": {"id": None}}, config) is False

    def test_falsy_but_present_value_passes(self):
        config = {"required_fields": ["count"]}
        assert validate_required_fields({"count": 0}, config) is True

    def test_no_required_fields_passes(self):
        assert validate_required_fields({}, {}) is True
        assert validate_required_fields({}, {"required_fields": []}) is True
