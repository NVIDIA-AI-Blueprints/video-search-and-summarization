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


class TestStreamKindValidation:
    """The stream key names the event kind, and the kind selects the decode
    schema: anything that is not ``incident`` is decoded as a Behavior. So a
    typo in a stream key does not fail — it silently decodes every incident on
    that stream with the wrong schema and publishes it to the wrong place.
    Rejecting the key at construction is what turns that into an error an
    operator sees at boot instead of a mis-routing nobody sees at all.
    """

    @pytest.mark.parametrize("kind", ["incident", "alert", "anomaly"])
    def test_supported_kinds_are_accepted(self, kind):
        config = {
            "event_bridge": {
                "redis_source": {"streams": {kind: "s"}, "consumer_group": "g"}
            }
        }
        assert make_source(config).stream_to_kind == {"s": kind}

    def test_a_typo_in_a_stream_key_is_rejected_at_construction(self):
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incidents": "mdx-incidents"},
                    "consumer_group": "g",
                }
            }
        }
        with pytest.raises(ValueError, match="unsupported key 'incidents'"):
            make_source(config)

    def test_the_error_names_the_kinds_that_would_have_worked(self):
        """A rejection an operator cannot act on is only marginally better than
        the silent mis-decode it replaced."""
        config = {
            "event_bridge": {
                "redis_source": {"streams": {"warning": "s"}, "consumer_group": "g"}
            }
        }
        with pytest.raises(ValueError, match="incident, alert, anomaly"):
            make_source(config)

    def test_heartbeat_is_exempt_because_it_is_never_decoded_as_an_event(self):
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
        assert "hb" not in source.stream_to_kind


class TestAckLifecycle:
    """An entry acked before it is examined is unrecoverable: it is out of the
    pending list and ``XREADGROUP >`` will never offer it again, so any path
    that fails to reach acceptance loses the event silently. These tests pin
    that every ack follows a decision about the entry.
    """

    def test_an_accepted_entry_is_acked(self):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01"))
        ]
        source.read_data()
        assert source.broker.ack.call_args.args[2] == [b"1-0"]

    def test_nothing_is_acked_when_the_read_returns_nothing(self):
        source = make_source()
        source.broker.read_group.return_value = []
        source.read_data()
        source.broker.ack.assert_not_called()

    def test_an_entry_is_not_acked_before_its_payload_is_examined(self):
        """The ordering guarantee, asserted directly: the ack call must not
        have happened at the point the envelope is being extracted."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01"))
        ]
        with patch("mdx.source.source_redis_stream.extract_envelope") as extract:
            def assert_not_yet_acked(fields):
                source.broker.ack.assert_not_called()
                return b"\x08\x01", b"sensor-1", {}

            extract.side_effect = assert_not_yet_acked
            source.read_data()
        source.broker.ack.assert_called_once()

    def test_a_decode_failure_mid_batch_does_not_ack_the_untouched_entries(self):
        """A crash partway through must leave the entries it never reached in
        the pending list, where the reclaim pass can still find them."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01")),
            ("mdx-incidents", b"1-1", envelope(b"\x08\x02")),
        ]
        with patch(
            "mdx.source.source_redis_stream.record_key_alignment",
            side_effect=[None, RuntimeError("boom")],
        ):
            with pytest.raises(RuntimeError):
                source.read_data()
        source.broker.ack.assert_not_called()

    def test_every_entry_in_a_mixed_batch_reaches_a_decision(self):
        """Accepted, payloadless and unmapped entries all have to be acked, or
        the ones that were not become permanent redelivery."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01")),
            ("mdx-incidents", b"1-1", {b"headers": b"{}"}),
            ("surprise", b"3-0", envelope(b"\x08\x03")),
        ]
        source.read_data()
        acked = {
            call.args[0]: call.args[2] for call in source.broker.ack.call_args_list
        }
        assert acked == {"mdx-incidents": [b"1-0", b"1-1"], "surprise": [b"3-0"]}


class TestReclaimStalePendingEntries:
    """``XREADGROUP ... >`` only returns entries no one has seen.

    An entry delivered to a replica that then died stays in that replica's
    pending list forever — never redelivered, never visible to its
    replacement — so without a reclaim pass a consumer lost mid-batch strands
    its work with no upper bound.
    """

    def test_an_idle_poll_sweeps_for_stranded_entries(self):
        source = make_source()
        source.broker.read_group.return_value = []
        source.broker.claim_stale.return_value = []
        source.read_data()
        claimed = {call.kwargs["stream"] for call in source.broker.claim_stale.call_args_list}
        assert claimed == {"mdx-incidents", "mdx-alerts"}

    def test_reclaimed_entries_are_decoded_by_the_same_path_as_new_ones(self):
        source = make_source()
        source.broker.read_group.return_value = []
        source.broker.claim_stale.side_effect = lambda stream, **_: (
            [(stream, b"1-0", envelope(b"\x08\x01"))] if stream == "mdx-incidents" else []
        )
        batches = source.read_data()
        assert len(batches) == 1
        assert batches[0]["kind"] == "incident"
        assert batches[0]["messages"] == [(b"sensor-1", b"\x08\x01", 1)]

    def test_reclaimed_entries_are_acked_so_they_do_not_cycle(self):
        source = make_source()
        source.broker.read_group.return_value = []
        source.broker.claim_stale.side_effect = lambda stream, **_: (
            [(stream, b"1-0", envelope(b"\x08\x01"))] if stream == "mdx-incidents" else []
        )
        source.read_data()
        source.broker.ack.assert_called_once_with(
            "mdx-incidents", "alert-bridge-vlm-group", [b"1-0"]
        )

    def test_a_productive_poll_skips_the_sweep(self):
        """Reclaimed entries are by definition not urgent; an XAUTOCLAIM per
        stream on every poll would be hot-path cost for no benefit."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01"))
        ]
        source.read_data()
        source.broker.claim_stale.assert_not_called()

    def test_the_sweep_is_throttled_between_idle_polls(self):
        source = make_source()
        source.broker.read_group.return_value = []
        source.broker.claim_stale.return_value = []
        source.read_data()
        first = source.broker.claim_stale.call_count
        source.read_data()
        assert source.broker.claim_stale.call_count == first

    def test_the_sweep_runs_again_once_the_interval_has_passed(self):
        source = make_source()
        source.broker.read_group.return_value = []
        source.broker.claim_stale.return_value = []
        source.read_data()
        first = source.broker.claim_stale.call_count
        source._last_reclaim_at -= source._reclaim_interval + 1
        source.read_data()
        assert source.broker.claim_stale.call_count > first

    def test_the_interval_is_configurable(self):
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "i"},
                    "consumer_group": "g",
                    "consumer_config": {"reclaim_interval": 5.0},
                }
            }
        }
        assert make_source(config)._reclaim_interval == 5.0

    def test_the_sweep_can_be_disabled(self):
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "i"},
                    "consumer_group": "g",
                    "consumer_config": {"reclaim_interval": 0},
                }
            }
        }
        source = make_source(config)
        source.broker.read_group.return_value = []
        source.read_data()
        source.broker.claim_stale.assert_not_called()


class TestReadiness:
    """An unreachable Redis returns the same empty entry list as an idle
    stream. Without this the process publishes itself ready and ``/health``
    answers 200 while nothing is being consumed at all.
    """

    def test_a_healthy_source_is_ready(self):
        source = make_source()
        source.broker.connection_healthy = True
        assert source.is_ready() is True

    def test_an_unreachable_broker_is_not_ready(self):
        source = make_source()
        source.broker.connection_healthy = False
        assert source.is_ready() is False

    def test_a_missing_consumer_group_is_not_ready(self):
        source = make_source()
        source.broker.connection_healthy = True
        source._groups_ready = False
        assert source.is_ready() is False

    def test_readiness_before_the_first_command_is_not_held_against_it(self):
        """``None`` means no command has run yet, which is startup, not an
        outage — reporting not-ready there would fail every cold start."""
        source = make_source()
        source.broker.connection_healthy = None
        assert source.is_ready() is True

    def test_a_failed_read_flips_readiness(self):
        source = make_source()
        source.broker.connection_healthy = True
        source.broker.ensure_group.return_value = False
        with patch("mdx.source.source_redis_stream.time.sleep"):
            source.read_data()
        assert source.is_ready() is False

    def test_await_ready_returns_true_once_redis_answers(self):
        source = make_source()
        source.broker.ping.return_value = True
        source.broker.ensure_group.return_value = True
        assert source.await_ready(timeout=1.0) is True

    def test_await_ready_retries_until_redis_comes_up(self):
        source = make_source()
        source.broker.ping.side_effect = [False, False, True]
        source.broker.ensure_group.return_value = True
        with patch("mdx.source.source_redis_stream.time.sleep"):
            assert source.await_ready(timeout=30.0) is True
        assert source.broker.ping.call_count == 3

    def test_await_ready_gives_up_so_a_bad_endpoint_fails_the_start(self):
        """The caller turns this into a failed start, so a deployment pointed
        at a Redis that is not there fails visibly instead of idling."""
        source = make_source()
        source.broker.ping.return_value = False
        with patch("mdx.source.source_redis_stream.time.sleep"):
            assert source.await_ready(timeout=0.0) is False

    def test_await_ready_fails_when_the_group_cannot_be_created(self):
        """Reachable but unusable — a WRONGTYPE key on the stream name, say."""
        source = make_source()
        source.broker.ping.return_value = True
        source.broker.ensure_group.return_value = False
        with patch("mdx.source.source_redis_stream.time.sleep"):
            assert source.await_ready(timeout=0.0) is False


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

    def test_a_payloadless_entry_is_counted_not_just_logged(self):
        """Dropping is correct here, but a log line alone is not alertable: a
        producer emitting garbage would degrade the pipeline invisibly."""
        source = make_source()
        source.broker.read_group.return_value = [("mdx-incidents", b"1-0", {b"headers": b"{}"})]

        with patch("mdx.source.source_redis_stream.record_source_drop") as drop:
            source.read_data()
        drop.assert_called_once_with("redis_stream", "no_payload")

    def test_an_entry_from_an_unmapped_stream_is_dropped_not_guessed(self):
        """The stream key is what selects the decode schema.

        An entry from a stream with no configured kind — reachable once the
        reclaim pass can hand back entries from a stream since removed from the
        config — used to be labelled ``unknown``, which decodes as a Behavior.
        That routes an incident through the wrong protobuf schema and publishes
        it to the alert stream without raising anything. Dropping it and
        counting the drop is the only honest answer, and it is still acked so
        the entry cannot strand the consumer.
        """
        source = make_source()
        source.broker.read_group.return_value = [("surprise", b"1-0", envelope(b"\x08\x01"))]

        with patch("mdx.source.source_redis_stream.record_source_drop") as drop:
            assert source.read_data() == []

        drop.assert_called_once_with("redis_stream", "unmapped_kind")
        source.broker.ack.assert_called_once_with(
            "surprise", "alert-bridge-vlm-group", [b"1-0"]
        )

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

    def test_an_undecodable_entry_is_counted(self):
        source = make_source()
        source.broker.read_group.return_value = [("mdx-incidents", b"1-0", envelope(b"\x08\x01"))]
        with patch("mdx.source.source_redis_stream.record_source_drop") as drop:
            source.poll()
        drop.assert_called_once_with("redis_stream", "undecodable")

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
