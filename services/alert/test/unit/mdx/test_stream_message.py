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

"""Unit tests for ``mdx.stream_message``.

``StreamMessage`` is the transport-neutral envelope every event bridge
produces. The behaviour worth pinning is the ID and timestamp resolution,
because both feed downstream correlation:

* the ID falls back through explicit argument -> Kafka key -> schema-extracted
  ``message_id`` -> empty string;
* an unparseable or absent timestamp degrades to "now" rather than raising,
  so one malformed event cannot stall the whole batch.

``utils.field_extractor`` is patched out: schema-file loading is its own
concern and is exercised by its own tests.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from mdx.stream_message import StreamMessage

CORE_FIELDS = {"message_id": "evt-1", "timestamp": "2021-01-01T00:00:00Z", "sensor_id": "cam-1"}


@pytest.fixture
def extractor():
    """Patch the schema-driven extractor used by the ``from_*`` constructors."""
    with patch("utils.field_extractor.extract_core_fields", return_value=dict(CORE_FIELDS)) as extract, \
         patch("utils.field_extractor.validate_required_fields", return_value=True) as validate:
        yield extract, validate


def make_kafka_message(key=b"kafka-key", value=b'{"eventId": "evt-1"}', partition=0, offset=42):
    msg = MagicMock()
    msg.key.return_value = key
    msg.value.return_value = value
    msg.partition.return_value = partition
    msg.offset.return_value = offset
    return msg


class TestFromJsonWithSchema:
    def test_builds_message_from_extracted_fields(self, extractor):
        message = StreamMessage.from_json_with_schema('{"eventId": "evt-1"}')

        assert message.id == "evt-1"
        assert message.data == {"eventId": "evt-1"}
        assert message.core_fields == CORE_FIELDS
        assert message.raw_data == b'{"eventId": "evt-1"}'

    def test_metadata_records_the_source_and_schema(self, extractor):
        message = StreamMessage.from_json_with_schema("{}", schema_file="custom.yaml")
        assert message.metadata == {"source": "json", "schema_file": "custom.yaml"}

    def test_explicit_message_id_wins_over_extraction(self, extractor):
        message = StreamMessage.from_json_with_schema("{}", message_id="override")
        assert message.id == "override"

    def test_missing_message_id_yields_an_empty_id(self):
        with patch("utils.field_extractor.extract_core_fields", return_value={}), \
             patch("utils.field_extractor.validate_required_fields", return_value=True):
            assert StreamMessage.from_json_with_schema("{}").id == ""

    def test_timestamp_is_parsed_from_core_fields(self, extractor):
        message = StreamMessage.from_json_with_schema("{}")
        assert message.timestamp == datetime(2021, 1, 1, tzinfo=timezone.utc)

    def test_validation_failure_only_warns(self):
        with patch("utils.field_extractor.extract_core_fields", return_value=dict(CORE_FIELDS)), \
             patch("utils.field_extractor.validate_required_fields", return_value=False):
            assert StreamMessage.from_json_with_schema("{}").id == "evt-1"

    def test_malformed_json_reraises(self, extractor):
        with pytest.raises(json.JSONDecodeError):
            StreamMessage.from_json_with_schema("{not json")

    def test_extractor_failure_reraises(self):
        with patch("utils.field_extractor.validate_required_fields", return_value=True), \
             patch("utils.field_extractor.extract_core_fields", side_effect=RuntimeError("bad schema")):
            with pytest.raises(RuntimeError, match="bad schema"):
                StreamMessage.from_json_with_schema("{}")


class TestFromJsonWithConfig:
    def test_delegates_to_the_schema_constructor(self, extractor):
        message = StreamMessage.from_json_with_config("{}", config={"ignored": True})
        assert message.metadata["schema_file"] == "request_schema.yaml"

    def test_message_id_is_forwarded(self, extractor):
        message = StreamMessage.from_json_with_config("{}", config={}, message_id="override")
        assert message.id == "override"


class TestFromJson:
    """Legacy constructor — no schema extraction."""

    def test_id_comes_from_the_event_id_field(self):
        message = StreamMessage.from_json('{"eventId": "evt-1"}')
        assert message.id == "evt-1"
        assert message.core_fields is None

    def test_explicit_message_id_wins(self):
        assert StreamMessage.from_json('{"eventId": "evt-1"}', message_id="override").id == "override"

    def test_missing_event_id_yields_an_empty_id(self):
        assert StreamMessage.from_json("{}").id == ""

    def test_timestamp_is_parsed_from_the_payload(self):
        message = StreamMessage.from_json('{"timestamp": "2021-01-01T00:00:00Z"}')
        assert message.timestamp == datetime(2021, 1, 1, tzinfo=timezone.utc)

    def test_metadata_marks_the_json_source(self):
        assert StreamMessage.from_json("{}").metadata == {"source": "json"}

    def test_malformed_json_reraises(self):
        with pytest.raises(json.JSONDecodeError):
            StreamMessage.from_json("{not json")


class TestFromKafkaMessage:
    def test_id_prefers_the_kafka_key(self, extractor):
        message = StreamMessage.from_kafka_message(make_kafka_message())
        assert message.id == "kafka-key"

    def test_id_falls_back_to_the_extracted_message_id(self, extractor):
        message = StreamMessage.from_kafka_message(make_kafka_message(key=None))
        assert message.id == "evt-1"

    def test_metadata_carries_partition_and_offset(self, extractor):
        message = StreamMessage.from_kafka_message(make_kafka_message(partition=3, offset=99))

        assert message.metadata["source"] == "kafka"
        assert message.metadata["partition"] == 3
        assert message.metadata["offset"] == 99
        assert message.metadata["schema_file"] == "request_schema.yaml"

    def test_raw_data_keeps_the_original_bytes(self, extractor):
        payload = b'{"eventId": "evt-1"}'
        message = StreamMessage.from_kafka_message(make_kafka_message(value=payload))
        assert message.raw_data == payload

    def test_custom_schema_file_is_recorded(self, extractor):
        message = StreamMessage.from_kafka_message(make_kafka_message(), schema_file="other.yaml")
        assert message.metadata["schema_file"] == "other.yaml"

    def test_malformed_payload_reraises(self, extractor):
        with pytest.raises(json.JSONDecodeError):
            StreamMessage.from_kafka_message(make_kafka_message(value=b"{not json"))

    def test_undecodable_payload_reraises(self, extractor):
        with pytest.raises(UnicodeDecodeError):
            StreamMessage.from_kafka_message(make_kafka_message(value=b"\xff\xfe"))


class TestFromRedisStream:
    """The Redis constructor keeps the entry ID in metadata because that is
    where Redis records the publish time — there is no separate timestamp field
    like the Kafka record header."""

    def test_builds_message_from_the_mdx_envelope(self, extractor):
        message = StreamMessage.from_redis_stream(
            "mdx-incidents",
            b"1700000000000-0",
            {b"key": b"cam-1", b"value": b'{"eventId": "evt-1"}', b"headers": b"{}"},
        )

        assert message.data == {"eventId": "evt-1"}
        assert message.core_fields == CORE_FIELDS

    def test_metadata_records_the_stream_and_entry_id(self, extractor):
        message = StreamMessage.from_redis_stream(
            "mdx-incidents", b"1700000000000-0", {b"value": b"{}"}
        )

        assert message.metadata["source"] == "redisStream"
        assert message.metadata["stream"] == "mdx-incidents"
        assert message.metadata["entry_id"] == "1700000000000-0"
        assert message.metadata["published_at_ms"] == 1700000000000

    def test_envelope_key_is_preferred_as_the_id(self, extractor):
        message = StreamMessage.from_redis_stream("s", b"1-0", {b"key": b"cam-1", b"value": b"{}"})
        assert message.id == "cam-1"

    def test_headers_are_preserved_for_downstream_use(self, extractor):
        message = StreamMessage.from_redis_stream(
            "s", b"1-0", {b"value": b"{}", b"headers": b'{"trace": "abc"}'}
        )
        assert message.metadata["headers"] == {"trace": "abc"}

    def test_an_entry_without_a_payload_raises(self, extractor):
        with pytest.raises(ValueError, match="carries no payload field"):
            StreamMessage.from_redis_stream("s", b"1-0", {b"headers": b"{}"})

    def test_a_non_json_payload_raises(self, extractor):
        """Protobuf entries are decoded by the source, not here."""
        with pytest.raises(Exception):
            StreamMessage.from_redis_stream("s", b"1-0", {b"value": b"\x08\x01"})


class TestToRedisFields:
    def test_emits_the_canonical_mdx_envelope(self):
        message = make_message({"id": "evt-1"}, core_fields={"sensor_id": "cam-1"})
        fields = message.to_redis_fields()

        assert fields[b"key"] == b"cam-1"
        assert json.loads(fields[b"value"]) == {"id": "evt-1"}
        assert fields[b"headers"] == "{}"

    def test_falls_back_to_the_message_id_for_the_key(self):
        message = make_message({"id": "evt-1"})
        assert message.to_redis_fields()[b"key"] == b"evt-1"

    def test_headers_round_trip_from_metadata(self):
        message = make_message({})
        message.metadata = {"headers": {"trace": "abc"}}
        assert json.loads(message.to_redis_fields()[b"headers"]) == {"trace": "abc"}

    def test_the_payload_matches_what_the_kafka_sink_writes(self):
        """Both event-bridge sinks carry byte-identical JSON bodies so a
        deployment can switch transports without downstream changes."""
        message = make_message({"id": "evt-1", "verdict": "confirmed"})
        assert message.to_redis_fields()[b"value"] == message.to_json().encode("utf-8")


class TestParseTimestamp:
    def test_parses_z_suffixed_iso(self):
        assert StreamMessage._parse_timestamp("2021-01-01T00:00:00Z") == datetime(
            2021, 1, 1, tzinfo=timezone.utc
        )

    def test_parses_offset_iso(self):
        parsed = StreamMessage._parse_timestamp("2021-01-01T00:00:00+02:00")
        assert parsed.utcoffset().total_seconds() == 2 * 3600

    def test_parses_naive_iso(self):
        assert StreamMessage._parse_timestamp("2021-01-01T00:00:00") == datetime(2021, 1, 1)

    @pytest.mark.parametrize("value", [None, ""])
    def test_absent_timestamp_falls_back_to_now(self, value):
        before = datetime.now()
        parsed = StreamMessage._parse_timestamp(value)
        assert before <= parsed <= datetime.now()

    def test_unparseable_timestamp_falls_back_to_now(self):
        before = datetime.now()
        parsed = StreamMessage._parse_timestamp("not-a-timestamp")
        assert before <= parsed <= datetime.now()

    def test_non_string_timestamp_falls_back_to_now(self):
        assert isinstance(StreamMessage._parse_timestamp(1609459200), datetime)


class TestGetField:
    def _message(self, core_fields):
        return StreamMessage(
            id="evt-1", timestamp=datetime(2021, 1, 1), data={}, metadata={},
            core_fields=core_fields,
        )

    def test_reads_from_core_fields(self):
        assert self._message({"sensor_id": "cam-1"}).get_field("sensor_id") == "cam-1"

    def test_missing_key_returns_the_default(self):
        assert self._message({"sensor_id": "cam-1"}).get_field("nope", "fallback") == "fallback"

    def test_default_is_none_when_unspecified(self):
        assert self._message({}).get_field("nope") is None

    def test_absent_core_fields_returns_the_default(self):
        assert self._message(None).get_field("sensor_id", "fallback") == "fallback"

    def test_empty_core_fields_returns_the_default(self):
        """An empty dict is falsy, so the default is returned without a lookup."""
        assert self._message({}).get_field("sensor_id", "fallback") == "fallback"


def make_message(data=None, raw_data=None, core_fields=None):
    return StreamMessage(
        id="evt-1", timestamp=datetime(2021, 1, 1), data=data if data is not None else {},
        metadata={}, raw_data=raw_data, core_fields=core_fields,
    )


class TestToJson:
    def test_serialises_the_data_block(self):
        assert make_message({"eventId": "evt-1"}).to_json() == '{"eventId": "evt-1"}'

    def test_unserialisable_data_degrades_to_an_empty_object(self):
        """A publish must not crash the sink loop over one bad payload."""
        assert make_message({"blob": object()}).to_json() == "{}"


class TestToBytes:
    def test_prefers_the_original_raw_bytes(self):
        message = make_message({"eventId": "evt-1"}, raw_data=b"original")
        assert message.to_bytes() == b"original"

    def test_falls_back_to_serialising_the_data_block(self):
        assert make_message({"eventId": "evt-1"}).to_bytes() == b'{"eventId": "evt-1"}'


class TestExtractCoreFieldsIfNeeded:
    def test_extracts_when_core_fields_are_absent(self):
        message = make_message({"eventId": "evt-1"})
        with patch("utils.field_extractor.extract_core_fields", return_value=dict(CORE_FIELDS)):
            message.extract_core_fields_if_needed()
        assert message.core_fields == CORE_FIELDS

    def test_existing_core_fields_are_not_recomputed(self):
        message = make_message({}, core_fields={"message_id": "kept"})
        with patch("utils.field_extractor.extract_core_fields") as extract:
            message.extract_core_fields_if_needed()

        extract.assert_not_called()
        assert message.core_fields == {"message_id": "kept"}

    def test_schema_file_is_forwarded(self):
        message = make_message({})
        with patch("utils.field_extractor.extract_core_fields", return_value={"a": 1}) as extract:
            message.extract_core_fields_if_needed("other.yaml")
        assert extract.call_args[0][1] == "other.yaml"

    def test_extraction_failure_yields_an_empty_mapping(self):
        message = make_message({})
        with patch("utils.field_extractor.extract_core_fields", side_effect=RuntimeError("boom")):
            message.extract_core_fields_if_needed()
        assert message.core_fields == {}
