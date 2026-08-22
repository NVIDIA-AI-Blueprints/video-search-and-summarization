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

"""Unit tests for ``mdx.sink.sink_redis_stream``.

This is the event-bridge sink, so it mirrors ``KafkaSink``: anomalies and
incidents go to separate streams, and routing one to the other's stream is
silent. Both the current ``enhanced_anomaly`` / ``incidents`` keys and the
legacy ``*_stream`` spellings are pinned because existing configs use the
latter.

Per-message failures are swallowed so one bad document cannot drop the rest of
the batch — the same continue-on-error contract the Kafka sink has. Connection
failures at construction are the exception: there is no retry loop on this
path, so a bad host must surface at boot rather than silently discarding every
validation error.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from mdx.redis_stream_broker import KEY_FIELD, PAYLOAD_FIELD
from mdx.sink.sink_redis_stream import SinkRedisStream
from mdx.stream_message import StreamMessage

CONFIG = {
    "redis": {"host": "redis", "port": 6379},
    "event_bridge": {
        "sinkType": "redisStream",
        "redis_sink": {
            "streams": {
                "enhanced_anomaly": "alert-bridge-enhanced-alerts",
                "incidents": "alert-bridge-incidents",
            }
        },
    },
}

LEGACY_CONFIG = {
    "event_bridge": {
        "redis_sink": {
            "streams": {
                "enhanced_anomaly_stream": "legacy-enhanced",
                "incidents_stream": "legacy-incidents",
            }
        }
    }
}


def make_sink(config=None):
    with patch("mdx.sink.sink_redis_stream.RedisStreamBroker") as broker_cls:
        broker_cls.return_value.ping.return_value = True
        broker_cls.return_value.add.return_value = b"1-0"
        return SinkRedisStream(config or CONFIG)


def make_stream_message(data=None, core_fields=None, message_id="evt-1"):
    return StreamMessage(
        id=message_id,
        timestamp=datetime(2026, 1, 1),
        data=data if data is not None else {"id": message_id},
        metadata={},
        core_fields=core_fields,
    )


class TestConfiguration:
    def test_current_stream_keys_are_read(self):
        sink = make_sink()
        assert sink.enhanced_anomaly_stream == "alert-bridge-enhanced-alerts"
        assert sink.incidents_stream == "alert-bridge-incidents"

    def test_legacy_stream_keys_are_read(self):
        sink = make_sink(LEGACY_CONFIG)
        assert sink.enhanced_anomaly_stream == "legacy-enhanced"
        assert sink.incidents_stream == "legacy-incidents"

    def test_only_one_stream_is_enough(self):
        config = {"event_bridge": {"redis_sink": {"streams": {"incidents": "only"}}}}
        sink = make_sink(config)
        assert sink.incidents_stream == "only"
        assert sink.enhanced_anomaly_stream is None

    def test_missing_redis_sink_section_raises(self):
        with pytest.raises(ValueError, match="event_bridge.redis_sink must be configured"):
            make_sink({"event_bridge": {}})

    def test_no_streams_configured_raises(self):
        with pytest.raises(ValueError, match="must define 'enhanced_anomaly' and/or 'incidents'"):
            make_sink({"event_bridge": {"redis_sink": {"streams": {}}}})

    def test_an_unreachable_broker_fails_at_construction(self):
        """There is no retry loop here; a silent sink would drop every
        validation-error response."""
        with patch("mdx.sink.sink_redis_stream.RedisStreamBroker") as broker_cls:
            broker_cls.return_value.ping.return_value = False
            with pytest.raises(ConnectionError, match="Unable to reach Redis"):
                SinkRedisStream(CONFIG)


class TestWrite:
    def test_publishes_json_to_the_enhanced_anomaly_stream(self):
        sink = make_sink()
        sink.write([make_stream_message({"id": "evt-1", "verdict": "confirmed"})])

        stream, payload = sink.broker.add.call_args.args
        assert stream == "alert-bridge-enhanced-alerts"
        assert json.loads(payload) == {"id": "evt-1", "verdict": "confirmed"}

    def test_keys_by_sensor_id_when_available(self):
        """Cohort affinity depends on the key, exactly as with Kafka partitions."""
        sink = make_sink()
        sink.write([make_stream_message(core_fields={"sensor_id": "sensor-9"})])
        assert sink.broker.add.call_args.kwargs["key"] == "sensor-9"

    def test_falls_back_to_the_message_id_for_the_key(self):
        sink = make_sink()
        sink.write([make_stream_message(message_id="evt-7")])
        assert sink.broker.add.call_args.kwargs["key"] == "evt-7"

    def test_empty_and_none_batches_are_no_ops(self):
        sink = make_sink()
        sink.write([])
        sink.write(None)
        sink.broker.add.assert_not_called()

    def test_a_failing_message_does_not_drop_the_rest_of_the_batch(self):
        sink = make_sink()
        broken = make_stream_message()
        broken.to_json = MagicMock(side_effect=RuntimeError("boom"))

        sink.write([broken, make_stream_message(message_id="evt-2")])

        assert sink.broker.add.call_count == 1

    def test_a_missing_stream_logs_instead_of_raising(self):
        config = {"event_bridge": {"redis_sink": {"streams": {"incidents": "only"}}}}
        sink = make_sink(config)
        sink.write([make_stream_message()])
        sink.broker.add.assert_not_called()


class TestWriteIncidents:
    def test_publishes_to_the_incidents_stream(self):
        sink = make_sink()
        sink.write_incidents([make_stream_message({"id": "inc-1"})])
        assert sink.broker.add.call_args.args[0] == "alert-bridge-incidents"

    def test_empty_batch_is_a_no_op(self):
        sink = make_sink()
        sink.write_incidents([])
        sink.broker.add.assert_not_called()


class TestWriteMsg:
    def test_publishes_raw_bytes_unchanged(self):
        sink = make_sink()
        sink.write_msg([b"\x08\x01"])

        stream, payload = sink.broker.add.call_args.args
        assert stream == "alert-bridge-enhanced-alerts"
        assert payload == b"\x08\x01"

    def test_index_is_used_as_the_key(self):
        sink = make_sink()
        sink.write_msg([b"a", b"b"])
        assert [call.kwargs["key"] for call in sink.broker.add.call_args_list] == ["0", "1"]


class TestWriteData:
    def test_serializes_to_json_without_a_transform(self):
        sink = make_sink()
        sink.write_data([{"id": "evt-1", "sensor": {"id": "sensor-3"}}])

        payload = sink.broker.add.call_args.args[1]
        assert json.loads(payload)["id"] == "evt-1"
        assert sink.broker.add.call_args.kwargs["key"] == "sensor-3"

    def test_uses_the_transform_to_produce_protobuf(self):
        sink = make_sink()
        transform = MagicMock()
        transform.return_value.SerializeToString.return_value = b"\x08\x01"

        sink.write_data([{"id": "evt-1"}], transform)

        assert sink.broker.add.call_args.args[1] == b"\x08\x01"

    def test_top_level_sensor_id_is_preferred_for_incidents(self):
        """Incident payloads carry ``sensorId``; alerts nest it under ``sensor``."""
        sink = make_sink()
        sink.write_incident_data([{"sensorId": "sensor-1", "sensor": {"id": "other"}}])
        assert sink.broker.add.call_args.kwargs["key"] == "sensor-1"

    def test_incident_data_goes_to_the_incidents_stream(self):
        sink = make_sink()
        sink.write_incident_data([{"id": "inc-1"}])
        assert sink.broker.add.call_args.args[0] == "alert-bridge-incidents"

    def test_a_failing_transform_does_not_drop_the_rest_of_the_batch(self):
        sink = make_sink()
        transform = MagicMock(side_effect=[RuntimeError("boom"), MagicMock()])
        sink.write_data([{"id": "a"}, {"id": "b"}], transform)
        assert sink.broker.add.call_count == 1


class TestClose:
    def test_releases_the_connection(self):
        sink = make_sink()
        sink.close()
        sink.broker.close.assert_called_once()
