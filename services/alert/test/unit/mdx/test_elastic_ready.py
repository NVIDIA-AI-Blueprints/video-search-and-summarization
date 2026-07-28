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

"""Unit tests for ``mdx.utils.elastic_ready``.

These helpers reproduce, in Python, the cleanup and fingerprinting a Logstash
pipeline used to perform before indexing MDX events. Two consequences drive
what is tested here:

* **Fingerprint stability.** The fingerprint is the Elasticsearch document ID,
  so it doubles as the dedup key. It must be deterministic for the same event,
  insensitive to key ordering, and sensitive to every field that participates
  — otherwise distinct events collide into one document, or the same event
  re-indexes as a duplicate.
* **Timestamp normalization to exactly 3-digit milliseconds.** Both the
  ``{seconds, nanos}`` Protobuf shape and ISO strings must collapse to the
  same representation, because the fingerprint is computed over the
  normalized value; a mismatch there makes the Kafka and Protobuf ingest
  paths disagree on the document ID for the same incident.
"""

import pytest

from mdx.utils.elastic_ready import (
    coerce_epoch_fields,
    generate_alert_fingerprint,
    generate_incident_fingerprint,
    normalize_alert_event,
    normalize_incident_event,
    normalize_location_fields,
    remove_logstash_artifacts,
    strip_embeddings,
)

# 2021-01-01T00:00:00Z
EPOCH_SECONDS = 1609459200


class TestCoerceEpochFields:
    def test_seconds_and_nanos_become_an_iso_string(self):
        event = {"timestamp": {"seconds": EPOCH_SECONDS, "nanos": 500_000_000}}
        coerce_epoch_fields(event)
        assert event["timestamp"] == "2021-01-01T00:00:00.500000Z"

    def test_seconds_only_is_supported(self):
        event = {"timestamp": {"seconds": EPOCH_SECONDS}}
        coerce_epoch_fields(event)
        assert event["timestamp"] == "2021-01-01T00:00:00Z"

    def test_nanos_only_is_supported(self):
        event = {"timestamp": {"nanos": 500_000_000}}
        coerce_epoch_fields(event)
        assert event["timestamp"] == "1970-01-01T00:00:00.500000Z"

    def test_string_timestamps_are_left_alone(self):
        event = {"timestamp": "2021-01-01T00:00:00Z"}
        coerce_epoch_fields(event)
        assert event["timestamp"] == "2021-01-01T00:00:00Z"

    def test_missing_field_is_ignored(self):
        event = {"id": "evt-1"}
        coerce_epoch_fields(event)
        assert event == {"id": "evt-1"}

    def test_empty_mapping_is_ignored(self):
        event = {"timestamp": {}}
        coerce_epoch_fields(event)
        assert event["timestamp"] == {}

    def test_non_numeric_values_are_ignored(self):
        event = {"timestamp": {"seconds": "not-a-number"}}
        coerce_epoch_fields(event)
        assert event["timestamp"] == {"seconds": "not-a-number"}

    def test_multiple_fields_are_converted(self):
        event = {
            "timestamp": {"seconds": EPOCH_SECONDS},
            "end": {"seconds": EPOCH_SECONDS + 60},
        }
        coerce_epoch_fields(event, fields=("timestamp", "end"))
        assert event["timestamp"] == "2021-01-01T00:00:00Z"
        assert event["end"] == "2021-01-01T00:01:00Z"


class TestStripEmbeddings:
    def test_top_level_embeddings_are_removed(self):
        event = {"id": "evt-1", "embeddings": [{"vector": [0.1]}]}
        strip_embeddings(event)
        assert event == {"id": "evt-1"}

    def test_bbox3d_embeddings_are_removed(self):
        event = {"object": {"id": "obj-1", "bbox3d": {"embeddings": [1], "w": 2}}}
        strip_embeddings(event)
        assert event["object"]["bbox3d"] == {"w": 2}

    def test_object_without_bbox3d_is_untouched(self):
        event = {"object": {"id": "obj-1"}}
        strip_embeddings(event)
        assert event["object"] == {"id": "obj-1"}

    def test_non_mapping_object_is_ignored(self):
        event = {"object": "obj-1"}
        strip_embeddings(event)
        assert event == {"object": "obj-1"}

    def test_non_mapping_bbox3d_is_ignored(self):
        event = {"object": {"bbox3d": "n/a"}}
        strip_embeddings(event)
        assert event["object"]["bbox3d"] == "n/a"

    def test_missing_embeddings_is_a_noop(self):
        event = {"id": "evt-1"}
        strip_embeddings(event)
        assert event == {"id": "evt-1"}


class TestNormalizeLocationFields:
    def test_point_wrappers_are_flattened(self):
        event = {"locations": {"type": "Point", "coordinates": [{"point": [1.0, 2.0]}]}}
        normalize_location_fields(event)
        assert event["locations"]["coordinates"] == [[1.0, 2.0]]

    def test_bare_coordinates_are_passed_through(self):
        event = {"locations": {"type": "Point", "coordinates": [[1.0, 2.0]]}}
        normalize_location_fields(event)
        assert event["locations"]["coordinates"] == [[1.0, 2.0]]

    @pytest.mark.parametrize("declared", ["linestring", "LINESTRING", "LineString"])
    def test_linestring_type_is_canonicalised(self, declared):
        event = {"locations": {"type": declared, "coordinates": []}}
        normalize_location_fields(event)
        assert event["locations"]["type"] == "LineString"

    def test_other_geometry_types_keep_their_casing(self):
        event = {"locations": {"type": "Point", "coordinates": []}}
        normalize_location_fields(event)
        assert event["locations"]["type"] == "Point"

    def test_smooth_locations_are_flattened_too(self):
        event = {"smoothLocations": {"type": "linestring", "coordinates": [{"point": [1.0]}]}}
        normalize_location_fields(event)
        assert event["smoothLocations"]["coordinates"] == [[1.0]]
        assert event["smoothLocations"]["type"] == "LineString"

    def test_non_mapping_locations_are_ignored(self):
        event = {"locations": "n/a"}
        normalize_location_fields(event)
        assert event == {"locations": "n/a"}

    def test_missing_locations_is_a_noop(self):
        event = {"id": "evt-1"}
        normalize_location_fields(event)
        assert event == {"id": "evt-1"}


class TestRemoveLogstashArtifacts:
    def test_all_helper_fields_are_dropped(self):
        event = {
            "id": "evt-1",
            "kafka": {"topic": "t"},
            "message": "raw",
            "@timestamp": "2021-01-01T00:00:00Z",
            "@version": "1",
        }
        remove_logstash_artifacts(event)
        assert event == {"id": "evt-1"}

    def test_absent_fields_are_tolerated(self):
        event = {"id": "evt-1"}
        remove_logstash_artifacts(event)
        assert event == {"id": "evt-1"}


class TestGenerateAlertFingerprint:
    def _event(self, **overrides):
        event = {
            "analyticsModule": {"id": "intrusion"},
            "object": {"id": "obj-1"},
            "place": {"name": "gate-3"},
            "sensor": {"id": "cam-1"},
            "timestamp": "2021-01-01T00:00:00.000Z",
        }
        event.update(overrides)
        return event

    def test_returns_a_sha1_hex_digest(self):
        fingerprint = generate_alert_fingerprint(self._event())
        assert len(fingerprint) == 40
        assert all(c in "0123456789abcdef" for c in fingerprint)

    def test_is_deterministic(self):
        assert generate_alert_fingerprint(self._event()) == generate_alert_fingerprint(
            self._event()
        )

    def test_is_insensitive_to_key_ordering(self):
        reordered = {
            "timestamp": "2021-01-01T00:00:00.000Z",
            "sensor": {"id": "cam-1"},
            "place": {"name": "gate-3"},
            "object": {"id": "obj-1"},
            "analyticsModule": {"id": "intrusion"},
        }
        assert generate_alert_fingerprint(reordered) == generate_alert_fingerprint(self._event())

    @pytest.mark.parametrize(
        "override",
        [
            {"sensor": {"id": "cam-2"}},
            {"object": {"id": "obj-2"}},
            {"place": {"name": "gate-4"}},
            {"analyticsModule": {"id": "loitering"}},
            {"timestamp": "2021-01-01T00:00:01.000Z"},
        ],
    )
    def test_every_participating_field_changes_the_fingerprint(self, override):
        assert generate_alert_fingerprint(self._event(**override)) != generate_alert_fingerprint(
            self._event()
        )

    def test_unrelated_fields_do_not_change_the_fingerprint(self):
        assert generate_alert_fingerprint(
            self._event(videoPath="/videos/a.mp4")
        ) == generate_alert_fingerprint(self._event())

    def test_missing_fields_are_skipped_not_defaulted(self):
        partial = {"sensor": {"id": "cam-1"}}
        assert generate_alert_fingerprint(partial) is not None
        assert generate_alert_fingerprint(partial) != generate_alert_fingerprint(self._event())

    def test_returns_none_when_no_field_participates(self):
        assert generate_alert_fingerprint({"videoPath": "/videos/a.mp4"}) is None
        assert generate_alert_fingerprint({}) is None

    def test_protobuf_and_string_timestamps_agree(self):
        """The two ingest paths must produce the same document ID."""
        from_string = generate_alert_fingerprint(self._event())
        from_dict = generate_alert_fingerprint(
            self._event(timestamp={"seconds": EPOCH_SECONDS, "nanos": 0})
        )
        assert from_string == from_dict

    def test_nested_path_through_a_scalar_is_skipped(self):
        assert generate_alert_fingerprint({"sensor": "cam-1"}) is None


class TestGenerateIncidentFingerprint:
    def _event(self, **overrides):
        event = {
            "category": "collision",
            "sensorId": "cam-1",
            "timestamp": "2021-01-01T00:00:00.000Z",
        }
        event.update(overrides)
        return event

    def test_returns_a_sha1_hex_digest(self):
        assert len(generate_incident_fingerprint(self._event())) == 40

    def test_is_deterministic(self):
        assert generate_incident_fingerprint(self._event()) == generate_incident_fingerprint(
            self._event()
        )

    def test_primary_object_id_participates_when_present(self):
        with_obj = generate_incident_fingerprint(
            self._event(info={"primaryObjectId": "obj-1"})
        )
        assert with_obj != generate_incident_fingerprint(self._event())

    @pytest.mark.parametrize("blank", [None, ""])
    def test_blank_primary_object_id_is_ignored(self, blank):
        assert generate_incident_fingerprint(
            self._event(info={"primaryObjectId": blank})
        ) == generate_incident_fingerprint(self._event())

    def test_non_mapping_info_is_ignored(self):
        assert generate_incident_fingerprint(
            self._event(info="n/a")
        ) == generate_incident_fingerprint(self._event())

    @pytest.mark.parametrize(
        "override",
        [{"category": "fire"}, {"sensorId": "cam-2"}, {"timestamp": "2021-01-01T00:00:01.000Z"}],
    )
    def test_every_common_field_changes_the_fingerprint(self, override):
        assert generate_incident_fingerprint(
            self._event(**override)
        ) != generate_incident_fingerprint(self._event())

    @pytest.mark.parametrize("blank", [None, ""])
    def test_blank_common_fields_are_skipped(self, blank):
        assert generate_incident_fingerprint({"category": blank, "sensorId": "cam-1"}) is not None

    def test_returns_none_when_no_field_participates(self):
        assert generate_incident_fingerprint({}) is None
        assert generate_incident_fingerprint({"category": "", "sensorId": None}) is None

    def test_protobuf_and_string_timestamps_agree(self):
        from_string = generate_incident_fingerprint(self._event())
        from_dict = generate_incident_fingerprint(
            self._event(timestamp={"seconds": EPOCH_SECONDS, "nanos": 0})
        )
        assert from_string == from_dict

    def test_unparseable_timestamp_is_used_verbatim(self):
        """``_normalize_ts_to_millis`` falls back to the raw string."""
        assert generate_incident_fingerprint(self._event(timestamp="not-a-timestamp")) is not None

    def test_alert_and_incident_fingerprints_are_distinct_schemes(self):
        event = {
            "category": "collision",
            "sensorId": "cam-1",
            "sensor": {"id": "cam-1"},
            "timestamp": "2021-01-01T00:00:00.000Z",
        }
        assert generate_incident_fingerprint(event) != generate_alert_fingerprint(event)


class TestNormalizeAlertEvent:
    def test_applies_every_cleanup_step_in_place(self):
        event = {
            "timestamp": {"seconds": EPOCH_SECONDS, "nanos": 500_000_000},
            "end": {"seconds": EPOCH_SECONDS + 60, "nanos": 0},
            "embeddings": [{"vector": [0.1]}],
            "locations": {"type": "linestring", "coordinates": [{"point": [1.0, 2.0]}]},
            "kafka": {"topic": "t"},
            "@version": "1",
            "id": "evt-1",
        }
        result = normalize_alert_event(event)

        assert result is event
        assert event["timestamp"] == "2021-01-01T00:00:00.500Z"
        assert event["end"] == "2021-01-01T00:01:00.000Z"
        assert "embeddings" not in event
        assert event["locations"] == {"type": "LineString", "coordinates": [[1.0, 2.0]]}
        assert "kafka" not in event
        assert "@version" not in event
        assert event["id"] == "evt-1"

    def test_timestamps_are_padded_to_three_millisecond_digits(self):
        event = {"timestamp": "2021-01-01T00:00:00.5Z"}
        normalize_alert_event(event)
        assert event["timestamp"] == "2021-01-01T00:00:00.500Z"

    def test_is_idempotent(self):
        event = {"timestamp": {"seconds": EPOCH_SECONDS, "nanos": 0}, "id": "evt-1"}
        once = dict(normalize_alert_event(dict(event)))
        twice = normalize_alert_event(normalize_alert_event(dict(event)))
        assert once == twice

    def test_empty_event_is_tolerated(self):
        assert normalize_alert_event({}) == {}


class TestNormalizeIncidentEvent:
    def test_applies_cleanup_without_flattening_locations(self):
        event = {
            "timestamp": {"seconds": EPOCH_SECONDS, "nanos": 0},
            "embeddings": [{"vector": [0.1]}],
            "locations": {"type": "linestring", "coordinates": [{"point": [1.0]}]},
            "message": "raw",
        }
        result = normalize_incident_event(event)

        assert result is event
        assert event["timestamp"] == "2021-01-01T00:00:00.000Z"
        assert "embeddings" not in event
        assert "message" not in event
        # Locations are deliberately left untouched for incidents.
        assert event["locations"]["coordinates"] == [{"point": [1.0]}]
        assert event["locations"]["type"] == "linestring"

    def test_empty_event_is_tolerated(self):
        assert normalize_incident_event({}) == {}
