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

"""Unit tests for ``mdx.source.source_redis_stream``.

``read_data`` must return exactly the batch shape ``SourceKafka.read_data``
returns, because ``process_anomalies`` reads ``batch['kind']`` to decide whether
a batch is decoded as an ``Incident`` or a ``Behavior``. Getting the kind wrong
does not raise — it decodes every incident with the wrong protobuf schema — so
the stream-to-kind mapping and the batch keys are pinned here.

The two payload encodings the MDX envelope carries need different downstream
handling: ``process_batch_vlm`` dispatches on the element type of
``batch['messages']`` (JSON strings versus Kafka-style tuples) and inspects the
whole list, so a single batch must never mix them.

Acks are asserted because the entries stay in the pending list forever
otherwise, and the backoff is asserted because ``XREADGROUP`` returns
immediately when the broker is unreachable — without a sleep the consume loop
becomes a hot loop.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from mdx.source.source_redis_stream import SourceRedisStream

CONFIG = {
    "redis": {"host": "redis", "port": 6379},
    "event_bridge": {
        "sourceType": "redisStream",
        "redis_source": {
            "streams": {"incident": "mdx-incidents", "alert": "mdx-alerts"},
            "consumer_group": "alert-bridge-vlm-group",
            "consumer_config": {"count": 10, "block_time": 100},
        },
    },
}


def make_source(config=None):
    """Build a source with the broker replaced by a mock."""
    with patch("mdx.source.source_redis_stream.RedisStreamBroker") as broker_cls:
        broker_cls.return_value.ensure_group.return_value = True
        source = SourceRedisStream(config or CONFIG)
    return source


def envelope(payload, key=b"sensor-1"):
    return {b"key": key, b"value": payload, b"headers": b"{}"}


class TestConfiguration:
    def test_streams_map_to_kinds(self):
        source = make_source()
        assert source.stream_to_kind == {"mdx-incidents": "incident", "mdx-alerts": "alert"}
        assert sorted(source.source_streams) == ["mdx-alerts", "mdx-incidents"]

    def test_heartbeat_stream_is_held_apart_from_the_data_streams(self):
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "i", "heartbeat": "hb"},
                    "consumer_group": "g",
                }
            }
        }
        source = make_source(config)
        assert source.heartbeat_stream == "hb"
        assert source.source_streams == ["i"]

    def test_legacy_stream_suffix_keys_are_accepted(self):
        """The pre-existing config layout named keys ``<kind>_stream``."""
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"anomaly_stream": "in", "heartbeat_stream": "hb"},
                    "consumer_group": "g",
                }
            }
        }
        source = make_source(config)
        assert source.stream_to_kind == {"in": "anomaly"}
        assert source.heartbeat_stream == "hb"

    def test_blank_stream_names_are_ignored(self):
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "i", "alert": ""},
                    "consumer_group": "g",
                }
            }
        }
        assert make_source(config).source_streams == ["i"]

    def test_consumer_defaults_are_applied(self):
        config = {"event_bridge": {"redis_source": {"streams": {"incident": "i"}, "consumer_group": "g"}}}
        source = make_source(config)
        assert source.count == 10
        assert source.block_ms == 100
        assert source.start_id == "$"

    def test_missing_redis_source_section_raises(self):
        with pytest.raises(ValueError, match="event_bridge.redis_source must be configured"):
            make_source({"event_bridge": {}})

    def test_no_data_streams_raises(self):
        config = {"event_bridge": {"redis_source": {"streams": {"heartbeat": "hb"}, "consumer_group": "g"}}}
        with pytest.raises(ValueError, match="at least one non-heartbeat stream"):
            make_source(config)

    def test_missing_consumer_group_raises(self):
        """Reading without a group would bypass the at-least-once delivery
        tracking entirely."""
        config = {"event_bridge": {"redis_source": {"streams": {"incident": "i"}}}}
        with pytest.raises(ValueError, match="consumer_group must be configured"):
            make_source(config)

    def test_consumer_groups_are_created_for_every_stream_including_heartbeats(self):
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "i", "heartbeat": "hb"},
                    "consumer_group": "g",
                }
            }
        }
        source = make_source(config)
        created = {call.args[0] for call in source.broker.ensure_group.call_args_list}
        assert created == {"i", "hb"}

    def test_consumer_name_is_unique_per_process(self):
        """Replicas share the group, so they must not share a consumer name or
        they steal each other's pending entries."""
        source = make_source()
        assert str(__import__("os").getpid()) in source.consumer_name


class TestReadDataProtobuf:
    def test_protobuf_entries_become_kafka_style_tuples(self):
        """Emitting the Kafka tuple shape routes these through the existing
        protobuf decode path with no transport-specific branch."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1700000000000-0", envelope(b"\x08\x01"))
        ]
        batches = source.read_data()

        assert len(batches) == 1
        assert batches[0]["kind"] == "incident"
        assert batches[0]["messages"] == [(b"sensor-1", b"\x08\x01", 1700000000000)]

    def test_batches_are_split_by_kind(self):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01")),
            ("mdx-alerts", b"2-0", envelope(b"\x08\x02")),
        ]
        kinds = {batch["kind"] for batch in source.read_data()}
        assert kinds == {"incident", "alert"}

    def test_entries_of_the_same_kind_share_one_batch(self):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01")),
            ("mdx-incidents", b"1-1", envelope(b"\x08\x02")),
        ]
        batches = source.read_data()
        assert len(batches) == 1
        assert len(batches[0]["messages"]) == 2

    def test_published_at_uses_the_earliest_entry_id_timestamp(self):
        """Redis encodes the publish time in the entry ID, which stands in for
        the Kafka record timestamp in the latency metrics."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1700000009000-0", envelope(b"\x08\x01")),
            ("mdx-incidents", b"1700000000000-0", envelope(b"\x08\x02")),
        ]
        assert source.read_data()[0]["kafka_published_at"].startswith("2023-11-14T22:13:20")

    def test_every_batch_carries_the_timing_keys_the_pipeline_reads(self):
        source = make_source()
        source.broker.read_group.return_value = [("mdx-incidents", b"1-0", envelope(b"\x08\x01"))]
        batch = source.read_data()[0]
        assert set(batch) == {"kind", "messages", "kafka_consumed_at", "kafka_published_at"}
        assert batch["kafka_consumed_at"]


class TestReadDataJson:
    def test_json_entries_become_json_strings(self):
        source = make_source()
        payload = json.dumps({"id": "evt-1", "sensorId": "sensor-1"}).encode()
        source.broker.read_group.return_value = [("mdx-incidents", b"1-0", envelope(payload))]

        messages = source.read_data()[0]["messages"]
        assert messages == [payload.decode()]

    def test_json_and_protobuf_of_the_same_kind_are_split_into_separate_batches(self):
        """``process_batch_vlm`` checks that *all* elements are strings, so a
        mixed list would send the JSON entries down the protobuf path."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(json.dumps({"id": "a"}).encode())),
            ("mdx-incidents", b"1-1", envelope(b"\x08\x01")),
        ]
        batches = source.read_data()

        assert len(batches) == 2
        assert all(batch["kind"] == "incident" for batch in batches)
        for batch in batches:
            types = {type(message) for message in batch["messages"]}
            assert len(types) == 1

    def test_a_json_array_payload_is_treated_as_protobuf_not_json(self):
        """Only a JSON object is a valid event; anything else takes the
        protobuf path where the decoder can report a real error."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1700000000000-0", envelope(b"[1, 2]"))
        ]
        assert source.read_data()[0]["messages"] == [
            (b"sensor-1", b"[1, 2]", 1700000000000)
        ]


class TestReadDataResilience:
    def test_no_entries_yields_no_batches(self):
        source = make_source()
        source.broker.read_group.return_value = []
        assert source.read_data() == []

    def test_entries_are_acked_per_stream_in_one_call(self):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01")),
            ("mdx-incidents", b"1-1", envelope(b"\x08\x02")),
            ("mdx-alerts", b"2-0", envelope(b"\x08\x03")),
        ]
        source.read_data()

        acked = {call.args[0]: call.args[2] for call in source.broker.ack.call_args_list}
        assert acked == {"mdx-incidents": [b"1-0", b"1-1"], "mdx-alerts": [b"2-0"]}

    def test_an_entry_without_a_payload_is_acked_and_skipped(self):
        """Leaving it un-acked would replay the same broken entry forever."""
        source = make_source()
        source.broker.read_group.return_value = [("mdx-incidents", b"1-0", {b"headers": b"{}"})]

        assert source.read_data() == []
        source.broker.ack.assert_called_once_with("mdx-incidents", "alert-bridge-vlm-group", [b"1-0"])

    def test_an_unmapped_stream_falls_back_to_the_unknown_kind(self):
        source = make_source()
        source.broker.read_group.return_value = [("surprise", b"1-0", envelope(b"\x08\x01"))]
        assert source.read_data()[0]["kind"] == "unknown"

    def test_an_unreachable_broker_backs_off_instead_of_spinning(self):
        source = make_source()
        source.broker.ensure_group.return_value = False

        with patch("mdx.source.source_redis_stream.time.sleep") as sleep:
            assert source.read_data() == []

        sleep.assert_called_once_with(source._error_backoff)
        source.broker.read_group.assert_not_called()

    def test_error_backoff_is_configurable(self):
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "i"},
                    "consumer_group": "g",
                    "consumer_config": {"error_backoff": 5.0},
                }
            }
        }
        source = make_source(config)
        assert source._error_backoff == 5.0

    def test_read_group_is_called_with_the_configured_block_and_count(self):
        """The BLOCK is what keeps an idle stream from spinning the loop."""
        source = make_source()
        source.broker.read_group.return_value = []
        source.read_data()

        kwargs = source.broker.read_group.call_args.kwargs
        assert kwargs["count"] == 10
        assert kwargs["block_ms"] == 100
        assert kwargs["group"] == "alert-bridge-vlm-group"


class TestOtherSourceMethods:
    def test_read_returns_raw_payloads_and_acks(self):
        source = make_source()
        source.broker.read_group.return_value = [("mdx-incidents", b"1-0", envelope(b"\x08\x01"))]
        assert source.read() == [b"\x08\x01"]
        source.broker.ack.assert_called_once()

    def test_poll_builds_stream_messages(self):
        source = make_source()
        payload = json.dumps({"id": "evt-1", "timestamp": "2026-01-01T00:00:00Z"}).encode()
        source.broker.read_group.return_value = [("mdx-incidents", b"1-0", envelope(payload))]

        messages = source.poll()
        assert len(messages) == 1
        assert messages[0].data["id"] == "evt-1"
        assert messages[0].metadata["source"] == "redisStream"

    def test_poll_acks_and_skips_an_undecodable_entry(self):
        source = make_source()
        source.broker.read_group.return_value = [("mdx-incidents", b"1-0", envelope(b"\x08\x01"))]
        assert source.poll() == []
        source.broker.ack.assert_called_once()

    def test_poll_heartbeats_reads_only_the_heartbeat_stream(self):
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "i", "heartbeat": "hb"},
                    "consumer_group": "g",
                }
            }
        }
        source = make_source(config)
        source.broker.read_group.return_value = []
        source.poll_heartbeats()
        assert source.broker.read_group.call_args.kwargs["streams"] == ["hb"]

    def test_poll_heartbeats_without_a_heartbeat_stream_is_a_no_op(self):
        source = make_source()
        assert source.poll_heartbeats() == []
        source.broker.read_group.assert_not_called()

    def test_close_releases_the_connection(self):
        source = make_source()
        source.close()
        source.broker.close.assert_called_once()
