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
from unittest.mock import patch

import pytest

from mdx.source.source_redis_stream import (
    RELEASE_BUDGET_SECONDS,
    SourceRedisStream,
)
from mdx.stream_routing import canonical_kind

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


#: Streams ``make_source`` fills in for a kind the caller's config does not
#: name. Recognizable in an assertion as something the case did not ask for.
FILLER_STREAMS = {"incident": "filler-incidents", "alert": "filler-alerts"}


def with_required_keys(config):
    """``config`` plus what the constructor requires and the caller is not testing.

    A connection, and a stream for each event kind. The source requires both
    kinds — a config naming one carries half its traffic in silence — and it
    requires a host, or it would poll localhost. Neither is the subject of a case
    about heartbeat handling, reclaim, or poll behaviour, so both are filled in
    here rather than written into every config in the file. A case about either
    requirement builds its own config and calls the constructor directly.

    Coverage is judged through :func:`canonical_kind`, the same folding the
    source applies, so a config naming ``anomaly_stream`` counts as naming the
    alert kind and does not get a second alert stream filled in behind it.
    """
    config = dict(config or CONFIG)
    config.setdefault("redis", CONFIG["redis"])

    bridge = dict(config.get("event_bridge") or {})
    section = bridge.get("redis_source")
    if isinstance(section, dict):
        streams = dict(section.get("streams") or {})
        covered = {canonical_kind(key) for key in streams}
        for kind, stream in FILLER_STREAMS.items():
            if kind not in covered:
                streams[kind] = stream
        bridge["redis_source"] = dict(section, streams=streams)
        config["event_bridge"] = bridge
    return config


def make_source(config=None, **_):
    """Build a source with the broker replaced by a mock."""
    config = with_required_keys(config)
    with patch("mdx.source.source_redis_stream.RedisStreamBroker") as broker_cls:
        broker_cls.return_value.ensure_group.return_value = True
        source = SourceRedisStream(config)
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
        assert "i" in source.source_streams
        assert "hb" not in source.source_streams

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
        # Normalized to the canonical kind on the way in, so nothing downstream
        # has to know two names for one thing.
        assert source.stream_to_kind["in"] == "alert"
        assert source.heartbeat_stream == "hb"

    def test_a_blank_stream_name_is_rejected_rather_than_skipped(self):
        """A blank value is what a rendered config produces for an unset
        variable. Skipping it meant the deployment consumed one kind, dropped
        the other, and said nothing about it."""
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "i", "alert": ""},
                    "consumer_group": "g",
                }
            }
        }
        with pytest.raises(ValueError, match=r"streams\['alert'\] is empty"):
            make_source(config)

    def test_two_kinds_cannot_share_one_stream(self):
        """The kind selects the decode schema, and the map is keyed by stream —
        so the second key used to overwrite the first and one of the two kinds
        was then decoded with the other's schema."""
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "both", "alert": "both"},
                    "consumer_group": "g",
                }
            }
        }
        with pytest.raises(ValueError, match="cannot carry two event kinds"):
            make_source(config)

    def test_a_heartbeat_stream_cannot_double_as_an_event_stream(self):
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "s", "heartbeat": "s"},
                    "consumer_group": "g",
                }
            }
        }
        with pytest.raises(ValueError, match="cannot carry two event kinds"):
            make_source(config)

    @pytest.mark.parametrize(
        "configured,missing", [("incident", "alert"), ("alert", "incident")],
    )
    def test_configuring_only_one_kind_is_rejected(self, configured, missing):
        """It was a warning, and a warning is not enough.

        Both kinds are produced upstream and verified by the same pipeline, so a
        map naming one is a config that lost a line rather than a deployment
        shape somebody chose. The service it produced ran, reported healthy, and
        never read half its traffic — and from outside, "the alerts never
        arrived" reads the same as "no alert stream was configured".
        """
        config = {
            "redis": CONFIG["redis"],
            "event_bridge": {
                "redis_source": {"streams": {configured: "s"}, "consumer_group": "g"}
            },
        }
        with patch("mdx.source.source_redis_stream.RedisStreamBroker"):
            with pytest.raises(ValueError, match=f"configures no {missing} stream"):
                SourceRedisStream(config)

    def test_consumer_defaults_are_applied(self):
        config = {"event_bridge": {"redis_source": {"streams": {"incident": "i"}, "consumer_group": "g"}}}
        source = make_source(config)
        assert source.count == 10
        assert source.block_ms == 100
        assert source.start_id == "$"

    def test_missing_redis_source_section_raises(self):
        with pytest.raises(ValueError, match="event_bridge.redis_source must be configured"):
            make_source({"event_bridge": {}})

    def test_a_map_naming_only_a_heartbeat_is_rejected(self):
        """Reported as the two missing keys rather than as "no data streams":
        the operator's next action is to add them, so name them."""
        config = {
            "redis": CONFIG["redis"],
            "event_bridge": {
                "redis_source": {"streams": {"heartbeat": "hb"}, "consumer_group": "g"}
            },
        }
        with patch("mdx.source.source_redis_stream.RedisStreamBroker"):
            with pytest.raises(ValueError, match="no incident or alert stream"):
                SourceRedisStream(config)

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
        assert {"i", "hb"} <= created

    def test_consumer_name_is_unique_per_process(self):
        """Replicas share the group, so they must not share a consumer name or
        they steal each other's pending entries."""
        source = make_source()
        assert str(__import__("os").getpid()) in source.consumer_name


class TestTheTuningKnobsCannotStopTheContainerStarting:
    """These were bare ``int()`` and ``float()`` calls in the constructor.

    The constructor runs inside a forked pipeline child, so ``count: ""`` from an
    unset variable raised there and crash-looped the container -- over a poll
    size. Same policy as the broker's own knobs: warn, fall back, start. What
    decides *where* this connects still fails loudly; a display or pacing value
    does not.
    """

    @staticmethod
    def _with(**consumer_config):
        return make_source({
            "redis": CONFIG["redis"],
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "i", "alert": "a"},
                    "consumer_group": "g",
                    "consumer_config": consumer_config,
                },
            },
        })

    @pytest.mark.parametrize("value", ["", "   ", None, "ten", []])
    def test_an_unusable_count_falls_back_to_the_default(self, value):
        assert self._with(count=value).count == 10

    @pytest.mark.parametrize("value", ["", "lots", None])
    def test_an_unusable_block_time_falls_back_to_the_default(self, value):
        assert self._with(block_time=value).block_ms == 100

    @pytest.mark.parametrize("value", ["", "a second", None])
    def test_an_unusable_error_backoff_falls_back_to_the_default(self, value):
        assert self._with(error_backoff=value)._error_backoff == 1.0

    @pytest.mark.parametrize("value", ["", "often", None])
    def test_an_unusable_reclaim_interval_falls_back_to_the_default(self, value):
        assert self._with(reclaim_interval=value)._reclaim_interval == 30.0

    @pytest.mark.parametrize("value", ["", "an hour", None])
    def test_an_unusable_consumer_ttl_falls_back_to_the_default(self, value):
        assert self._with(consumer_ttl_ms=value)._consumer_ttl_ms == 3_600_000

    def test_numeric_strings_are_still_read(self):
        """A rendered config makes strings of everything."""
        source = self._with(count="5", block_time="50", error_backoff="0.5")
        assert (source.count, source.block_ms, source._error_backoff) == (5, 50, 0.5)

    @pytest.mark.parametrize("value", [0, -1])
    def test_a_zero_or_negative_error_backoff_is_floored(self, value):
        """This backoff is the only thing pacing the consume loop while Redis
        refuses commands, because a refused read returns instantly. At zero it
        paces nothing -- the hot loop it exists to prevent -- and ``time.sleep``
        raises on a negative."""
        assert self._with(error_backoff=value)._error_backoff == 0.05

    @pytest.mark.parametrize("value", [0, -5])
    def test_a_count_that_would_read_nothing_is_floored(self, value):
        assert self._with(count=value).count == 1

    def test_a_disabled_reclaim_sweep_is_still_allowed(self):
        """Zero is documented as "do not sweep", so it is not floored."""
        assert self._with(reclaim_interval=0)._reclaim_interval == 0

    def test_the_fallback_names_the_setting(self, caplog):
        with caplog.at_level("WARNING"):
            self._with(count="ten")
        assert "event_bridge.redis_source.consumer_config.count" in caplog.text


class TestStreamKindValidation:
    """The stream key names the event kind, and the kind selects the decode
    schema: anything that is not ``incident`` is decoded as a Behavior. So a
    typo in a stream key does not fail — it silently decodes every incident on
    that stream with the wrong schema and publishes it to the wrong place.
    Rejecting the key at construction is what turns that into an error an
    operator sees at boot instead of a mis-routing nobody sees at all.
    """

    @pytest.mark.parametrize("kind", ["incident", "alert"])
    def test_supported_kinds_are_accepted(self, kind):
        config = {
            "event_bridge": {
                "redis_source": {"streams": {kind: "s"}, "consumer_group": "g"}
            }
        }
        assert make_source(config).stream_to_kind["s"] == kind

    def test_the_legacy_anomaly_spelling_is_accepted_and_normalized(self, caplog):
        """Kept working because existing configs use it; normalized to 'alert'
        so it does not travel further, and warned about because a config still
        using it is one nobody has revisited since the layout changed."""
        config = {
            "event_bridge": {
                "redis_source": {"streams": {"anomaly": "s"}, "consumer_group": "g"}
            }
        }
        with caplog.at_level("WARNING"):
            source = make_source(config)
        assert source.stream_to_kind["s"] == "alert"
        assert "legacy kind name" in caplog.text

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
        with pytest.raises(ValueError, match="incident, alert"):
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


class TestPayloadValidation:
    """Any Redis client can XADD to a stream this source consumes.

    Before this check an arbitrary JSON object was decoded, wrapped in a batch
    and handed to the VLM, which then paid to verify it — and a `metadata`
    sidecar decoded as a body took exactly that path. The check is one field
    because a sensor identity is the one thing every shape carries and the
    pipeline cannot work without: it prefixes the dedup cohort key and addresses
    the VST lookup that fetches the footage.
    """

    @pytest.mark.parametrize("payload", [b"{}", b'{"category": "Loitering"}'])
    def test_an_object_with_no_sensor_identity_is_dropped(self, payload):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(payload))
        ]
        with patch("mdx.source.source_redis_stream.record_source_drop") as drop:
            assert source.read_data() == []
        drop.assert_called_once_with("redis_stream", "schema_invalid")

    def test_a_dropped_entry_is_still_acked(self):
        """Leaving it pending would replay it on every reclaim sweep forever."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"{}"))
        ]
        source.read_data()
        source.broker.ack.assert_called_once_with(
            "mdx-incidents", "alert-bridge-vlm-group", [b"1-0"],
        )

    @pytest.mark.parametrize("payload", [
        b'{"sensorId": "cam-1"}',
        b'{"sensor": {"id": "cam-1"}}',
    ])
    def test_either_spelling_of_the_sensor_identity_is_accepted(self, payload):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(payload))
        ]
        assert source.read_data()[0]["messages"] == [payload.decode()]

    def test_a_protobuf_payload_is_not_schema_checked(self):
        """It cannot be inspected without decoding it, which is the pipeline's
        job — and requiring a JSON shape of it would reject every valid entry
        vss-behavior-analytics publishes."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01"))
        ]
        assert len(source.read_data()) == 1

    def test_a_payload_declaring_the_other_kind_is_dropped(self):
        """The stream decides the kind and the pipeline stamps it over the
        payload's, so this was not mis-decoded — it was relabelled, verified as
        an alert, and published as one, with nothing raised anywhere."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-alerts", b"1-0",
             envelope(b'{"sensorId": "cam-1", "notification_type": "incident"}')),
        ]
        with patch("mdx.source.source_redis_stream.record_source_drop") as drop:
            assert source.read_data() == []
        drop.assert_called_once_with("redis_stream", "kind_mismatch")

    def test_the_conflict_is_counted_apart_from_a_bad_payload(self):
        """A producer publishing to the wrong stream and a producer publishing
        malformed events need different people told."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0",
             envelope(b'{"sensorId": "cam-1", "notification_type": "alert"}')),
        ]
        with patch("mdx.source.source_redis_stream.record_source_drop") as drop:
            source.read_data()
        assert drop.call_args.args[1] == "kind_mismatch"

    def test_an_alert_that_declares_nothing_is_still_accepted(self):
        """The common case, and why the check is for a contradiction rather than
        for the field: most alerts arrive without notification_type, and
        requiring it would drop the traffic to catch the misdirected part."""
        payload = b'{"sensorId": "cam-1", "category": "Loitering"}'
        source = make_source()
        source.broker.read_group.return_value = [("mdx-alerts", b"1-0", envelope(payload))]
        assert source.read_data()[0]["messages"] == [payload.decode()]

    def test_a_declaration_that_agrees_with_the_stream_is_accepted(self):
        payload = b'{"sensorId": "cam-1", "notification_type": "alert"}'
        source = make_source()
        source.broker.read_group.return_value = [("mdx-alerts", b"1-0", envelope(payload))]
        assert source.read_data()[0]["messages"] == [payload.decode()]

    @pytest.mark.parametrize("declared", ["ALERT", " alert "])
    def test_the_declaration_is_read_the_way_the_pipeline_reads_it(self, declared):
        """Case and surrounding space are not a producer claiming another kind."""
        payload = json.dumps(
            {"sensorId": "cam-1", "notification_type": declared}
        ).encode()
        source = make_source()
        source.broker.read_group.return_value = [("mdx-alerts", b"1-0", envelope(payload))]
        assert source.read_data()[0]["messages"] == [payload.decode()]

    @pytest.mark.parametrize("declared", ["notification", "", 7, None])
    def test_a_value_that_names_no_kind_is_left_to_the_stream(self, declared):
        """Not a claim this can adjudicate, so it is not treated as one."""
        payload = json.dumps(
            {"sensorId": "cam-1", "notification_type": declared}
        ).encode()
        source = make_source()
        source.broker.read_group.return_value = [("mdx-alerts", b"1-0", envelope(payload))]
        assert source.read_data()[0]["messages"] == [payload.decode()]

    def test_a_protobuf_payload_is_not_kind_checked(self):
        """Its kind is the schema it was serialized with, and reading that means
        choosing a schema — which is what the stream is consulted for."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-alerts", b"1-0", envelope(b"\x08\x01"))
        ]
        assert len(source.read_data()) == 1

    def test_an_empty_payload_field_counts_as_no_payload(self):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b""))
        ]
        with patch("mdx.source.source_redis_stream.record_source_drop") as drop:
            assert source.read_data() == []
        drop.assert_called_once_with("redis_stream", "no_payload")

    def test_a_metadata_only_sidecar_is_rejected_rather_than_verified(self):
        """The envelope reads `metadata` as a last resort, which is right for
        RT-VLM's default but means a sidecar can arrive where a body belongs.
        This is what stops it before the VLM."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0",
             {b"metadata": json.dumps({"model": "cosmos", "fps": 4}).encode()}),
        ]
        with patch("mdx.source.source_redis_stream.record_source_drop") as drop:
            assert source.read_data() == []
        drop.assert_called_once_with("redis_stream", "schema_invalid")


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

    def test_a_productive_poll_still_sweeps(self):
        """Under load the read is never empty, and that used to skip the sweep.

        Gating it on an idle poll read as keeping XAUTOCLAIM off the hot path,
        but the effect was that the one deployment which needs the reclaim --
        a busy one that just lost a replica -- was the one that never got it.
        The interval is what keeps the cost down; a productive poll is not.
        """
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01"))
        ]
        source.broker.claim_stale.return_value = []
        source.read_data()
        claimed = {call.kwargs["stream"] for call in source.broker.claim_stale.call_args_list}
        assert claimed == {"mdx-incidents", "mdx-alerts"}

    def test_a_productive_poll_is_swept_at_the_same_interval(self):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01"))
        ]
        source.broker.claim_stale.return_value = []
        source.read_data()
        first = source.broker.claim_stale.call_count
        source.read_data()
        assert source.broker.claim_stale.call_count == first

    def test_new_and_reclaimed_entries_arrive_in_one_batch(self):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01"))
        ]
        source.broker.claim_stale.side_effect = lambda stream, **_: (
            [(stream, b"2-0", envelope(b"\x08\x02"))] if stream == "mdx-incidents" else []
        )
        batches = source.read_data()
        assert [batch["messages"] for batch in batches] == [
            [(b"sensor-1", b"\x08\x01", 1), (b"sensor-1", b"\x08\x02", 2)]
        ]
        # And both are acked, or the reclaimed one would come round again.
        source.broker.ack.assert_called_once_with(
            "mdx-incidents", "alert-bridge-vlm-group", [b"1-0", b"2-0"],
        )

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


class TestTheKindIsNeverTheHardcodedOne:
    """The regression this transport exists to close.

    The Redis read path used to label every batch ``anomaly`` regardless of
    which stream the entry came from, and the kind is what selects the protobuf
    schema downstream — so an incident read from Redis was decoded as a Behavior
    and verified as a behavior alert. The kind must come from the stream-to-kind
    map and from nowhere else.

    Pinned here, on a mocked broker, because the round-trip suite that also
    covers it needs a live Redis and is skipped wherever there is none.
    """

    @pytest.mark.parametrize("stream, expected", [
        ("mdx-incidents", "incident"),
        ("mdx-alerts", "alert"),
    ])
    def test_the_batch_carries_the_kind_its_stream_is_mapped_to(self, stream, expected):
        source = make_source()
        source.broker.read_group.return_value = [(stream, b"1-0", envelope(b"\x08\x01"))]
        assert [batch["kind"] for batch in source.read_data()] == [expected]

    def test_no_batch_is_labelled_anomaly(self):
        """Including when both streams are read in the same poll, which is the
        shape the old hardcode collapsed."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01")),
            ("mdx-alerts", b"2-0", envelope(b"\x08\x02")),
        ]
        assert "anomaly" not in {batch["kind"] for batch in source.read_data()}

    def test_the_legacy_key_still_resolves_to_a_canonical_kind(self):
        """``streams.anomaly`` is accepted as the old spelling of ``alert``, and
        what it must never do is reintroduce ``anomaly`` as a third kind on the
        wire: it is normalized at configuration time, so nothing downstream sees
        it."""
        source = make_source({
            "event_bridge": {
                "redis_source": {
                    "streams": {"anomaly": "legacy-alerts", "incident": "mdx-incidents"},
                    "consumer_group": "g",
                }
            }
        })
        source.broker.read_group.return_value = [
            ("legacy-alerts", b"1-0", envelope(b"\x08\x01"))
        ]
        assert source.stream_to_kind["legacy-alerts"] == "alert"
        assert [batch["kind"] for batch in source.read_data()] == ["alert"]


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
            ("mdx-incidents", b"1-0",
             envelope(json.dumps({"sensorId": "sensor-1"}).encode())),
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


class TestARefusedReadIsNotAnIdleOne:
    """Both come back as an empty list, and only one of them has waited.

    ``XREADGROUP`` blocking for ``block_time`` is what paces the consume loop on
    an idle stream. A read the server *refuses* -- ``OOM``, or an ACL without
    XREADGROUP -- returns instantly, and the loop has no sleep of its own, so it
    span at full speed writing one error line per iteration for as long as the
    condition lasted. A lost connection did not do this only because it clears
    the group cache on the way out and the next poll's group assertion sleeps.
    """

    def test_a_refused_read_sleeps_before_returning(self):
        source = make_source()
        source.broker.read_group.return_value = []
        source.broker.connection_healthy = False

        with patch("mdx.source.source_redis_stream.time.sleep") as sleep:
            assert source.read_data() == []

        sleep.assert_called_once_with(source._error_backoff)

    def test_an_idle_read_does_not_sleep_again(self):
        """The BLOCK already spent the time; sleeping here would double it."""
        source = make_source()
        source.broker.read_group.return_value = []
        source.broker.connection_healthy = True

        with patch("mdx.source.source_redis_stream.time.sleep") as sleep:
            source.read_data()

        sleep.assert_not_called()

    def test_a_broker_that_has_answered_nothing_yet_does_not_sleep(self):
        """``None`` is "no command has run", which is not a failure."""
        source = make_source()
        source.broker.read_group.return_value = []
        source.broker.connection_healthy = None

        with patch("mdx.source.source_redis_stream.time.sleep") as sleep:
            source.read_data()

        sleep.assert_not_called()

    def test_a_refused_read_does_not_sweep(self):
        """The sweep issues XAUTOCLAIM, which is refused for the same reason."""
        source = make_source()
        source.broker.read_group.return_value = []
        source.broker.connection_healthy = False

        with patch("mdx.source.source_redis_stream.time.sleep"):
            source.read_data()

        source.broker.claim_stale.assert_not_called()


class TestJsonThatIsNotUtf8:
    """One XADD used to be enough to take the consumer down for good.

    ``json.loads`` sniffs a BOM, so UTF-16 JSON parses -- and then the batch
    builder decoded the same bytes a second time as UTF-8 and raised. The
    exception escaped ``read_data``, so nothing in the poll was acked, the
    consume loop died, and the entry came back to the next process through the
    reclaim sweep. A restart loop, from a payload anybody with write access
    could publish.
    """

    @staticmethod
    def _utf16(document):
        return json.dumps(document).encode("utf-16")

    def test_the_entry_is_dropped_rather_than_raising(self):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-alerts", b"1-0", envelope(self._utf16({"sensorId": "cam-1"}))),
        ]
        assert source.read_data() == []

    def test_the_entry_is_acked_so_it_cannot_come_round_again(self):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-alerts", b"1-0", envelope(self._utf16({"sensorId": "cam-1"}))),
        ]
        source.read_data()
        source.broker.ack.assert_called_once_with(
            "mdx-alerts", "alert-bridge-vlm-group", [b"1-0"],
        )

    def test_the_drop_has_its_own_reason(self):
        """The fix is the producer's encoder setting, not its schema."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-alerts", b"1-0", envelope(self._utf16({"sensorId": "cam-1"}))),
        ]
        with patch("mdx.source.source_redis_stream.record_source_drop") as drop:
            source.read_data()
        drop.assert_called_once_with("redis_stream", "payload_encoding")

    def test_the_rest_of_the_batch_still_arrives(self):
        """What made this a crash rather than a drop: it took its neighbours."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-alerts", b"1-0", envelope(self._utf16({"sensorId": "cam-1"}))),
            ("mdx-alerts", b"1-1", envelope(json.dumps({"sensorId": "cam-2"}).encode())),
        ]
        batches = source.read_data()
        assert [json.loads(m)["sensorId"] for m in batches[0]["messages"]] == ["cam-2"]
        source.broker.ack.assert_called_once_with(
            "mdx-alerts", "alert-bridge-vlm-group", [b"1-0", b"1-1"],
        )

    def test_utf8_json_is_still_carried_as_text(self):
        source = make_source()
        payload = json.dumps({"sensorId": "cam-1", "note": "café"}).encode("utf-8")
        source.broker.read_group.return_value = [
            ("mdx-alerts", b"1-0", envelope(payload)),
        ]
        batches = source.read_data()
        assert batches[0]["messages"] == [payload.decode("utf-8")]

    def test_protobuf_is_still_read_as_protobuf(self):
        """Binary that is not JSON must keep taking the tuple path."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"\x08\x01")),
        ]
        batches = source.read_data()
        assert batches[0]["messages"] == [(b"sensor-1", b"\x08\x01", 1)]


class TestTheParserIsNotTrustedToFailInTwoWays:
    """Deeply nested JSON raises RecursionError, which is not a ValueError.

    This is the only place bytes a producer chose are parsed, and the two
    exceptions it was written to expect are not the only ones it can raise. A
    few hundred kilobytes of ``[[[[...`` -- one XADD, no special access -- came
    back as a RecursionError that escaped ``read_data`` entirely: nothing in the
    poll was acked, the consume loop died, and the reclaim sweep handed the same
    entry to the next process. The same permanent restart the UTF-16 payload
    above used to cause, through a different door.
    """

    @staticmethod
    def _too_deep():
        return b"[" * 200_000 + b"]" * 200_000

    def test_the_entry_is_dropped_rather_than_raising(self):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-alerts", b"1-0", envelope(self._too_deep())),
        ]
        assert source.read_data() == []

    def test_the_entry_is_acked_so_it_cannot_come_round_again(self):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-alerts", b"1-0", envelope(self._too_deep())),
        ]
        source.read_data()
        source.broker.ack.assert_called_once_with(
            "mdx-alerts", "alert-bridge-vlm-group", [b"1-0"],
        )

    def test_it_is_counted_as_undecodable(self):
        """Not its own reason: what reaches the counter is "the decoders could
        not read this", which is the same thing a corrupt protobuf is."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-alerts", b"1-0", envelope(self._too_deep())),
        ]
        with patch("mdx.source.source_redis_stream.record_source_drop") as drop:
            source.read_data()
        drop.assert_called_once_with("redis_stream", "undecodable")

    def test_the_rest_of_the_batch_still_arrives(self):
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-alerts", b"1-0", envelope(self._too_deep())),
            ("mdx-alerts", b"1-1", envelope(json.dumps({"sensorId": "cam-2"}).encode())),
        ]
        batches = source.read_data()
        assert [json.loads(m)["sensorId"] for m in batches[0]["messages"]] == ["cam-2"]


class TestAnEntryIdIsNotACredibleClock:
    """A stream ID's millisecond half is whatever the producer chose.

    Redis accepts an explicit ID, so it is producer input rather than a clock
    reading, and any integer is a valid one. ``datetime.fromtimestamp`` raises on
    a year out of range -- and it is called *after* the batch has been acked, so
    that exception lost every entry in the poll to buy a latency stamp.
    """

    @staticmethod
    def _at(message_id):
        source = make_source()
        source.broker.read_group.return_value = [
            (
                "mdx-alerts", message_id,
                envelope(json.dumps({"sensorId": "cam-1"}).encode()),
            ),
        ]
        return source

    def test_an_absurd_timestamp_costs_the_stamp_and_not_the_batch(self):
        source = self._at(b"99999999999999999999-0")
        batches = source.read_data()
        assert [json.loads(m)["sensorId"] for m in batches[0]["messages"]] == ["cam-1"]
        assert batches[0]["kafka_published_at"] is None

    def test_the_entry_is_still_acked(self):
        source = self._at(b"99999999999999999999-0")
        source.read_data()
        source.broker.ack.assert_called_once_with(
            "mdx-alerts", "alert-bridge-vlm-group", [b"99999999999999999999-0"],
        )

    def test_a_real_timestamp_is_still_stamped(self):
        source = self._at(b"1735689600000-0")
        batches = source.read_data()
        assert batches[0]["kafka_published_at"].startswith("2025-01-01T00:00:00")


class TestConsumerRecordsDoNotAccumulate:
    """A Redis consumer is created by being named and removed by nothing.

    There is no session for the server to expire -- the difference from a Kafka
    group -- and this consumer's name carries its PID, so every restart and
    every forked pipeline child left one more record behind, each of which
    XAUTOCLAIM and XINFO then walk.
    """

    @staticmethod
    def _consumers(source, records):
        source.broker.read_group.return_value = []
        source.broker.claim_stale.return_value = []
        source.broker.list_consumers.return_value = records
        source.broker.delete_consumer.return_value = 0

    def test_an_idle_record_with_nothing_pending_is_removed(self):
        source = make_source()
        self._consumers(source, [
            {"name": "alert-bridge-host-999", "pending": 0, "idle": 7_200_000},
        ])
        source.read_data()
        source.broker.delete_consumer.assert_any_call(
            "mdx-incidents", "alert-bridge-vlm-group", "alert-bridge-host-999",
        )

    def test_a_record_holding_pending_entries_is_left_alone(self):
        """DELCONSUMER discards a consumer's pending entries rather than
        releasing them, so removing this one would lose exactly the entries the
        reclaim sweep exists to rescue."""
        source = make_source()
        self._consumers(source, [
            {"name": "alert-bridge-host-999", "pending": 3, "idle": 7_200_000},
        ])
        source.read_data()
        source.broker.delete_consumer.assert_not_called()

    def test_a_recently_active_record_is_left_alone(self):
        source = make_source()
        self._consumers(source, [
            {"name": "alert-bridge-host-999", "pending": 0, "idle": 5_000},
        ])
        source.read_data()
        source.broker.delete_consumer.assert_not_called()

    def test_this_consumer_never_removes_itself_mid_run(self):
        source = make_source()
        self._consumers(source, [
            {"name": source.consumer_name, "pending": 0, "idle": 7_200_000},
        ])
        source.read_data()
        source.broker.delete_consumer.assert_not_called()

    def test_the_reclaim_runs_before_the_cleanup(self):
        """An entry is rescued before the record that held it is considered."""
        source = make_source()
        calls = []
        source.broker.read_group.return_value = []
        source.broker.claim_stale.side_effect = lambda **_: calls.append("claim") or []
        source.broker.list_consumers.side_effect = lambda *_: calls.append("list") or []
        source.read_data()
        assert calls.index("claim") < calls.index("list")

    def test_the_cleanup_is_throttled_with_the_sweep(self):
        source = make_source()
        self._consumers(source, [])
        source.read_data()
        first = source.broker.list_consumers.call_count
        source.read_data()
        assert source.broker.list_consumers.call_count == first

    def test_the_threshold_is_configurable(self):
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "i"},
                    "consumer_group": "g",
                    "consumer_config": {"consumer_ttl_ms": 60_000},
                }
            }
        }
        assert make_source(config)._consumer_ttl_ms == 60_000

    def test_closing_releases_this_consumer_record(self):
        """Otherwise an ordinary restart leaves one behind every single time."""
        source = make_source()
        source.broker.list_consumers.return_value = [
            {"name": source.consumer_name, "pending": 0, "idle": 0},
        ]
        source.close()
        assert {
            call.args[0] for call in source.broker.delete_consumer.call_args_list
        } == {"mdx-incidents", "mdx-alerts"}

    def test_closing_keeps_a_record_that_still_holds_entries(self):
        source = make_source()
        source.broker.list_consumers.return_value = [
            {"name": source.consumer_name, "pending": 2, "idle": 0},
        ]
        source.close()
        source.broker.delete_consumer.assert_not_called()

    def test_closing_still_releases_the_connection_when_cleanup_fails(self):
        source = make_source()
        source.broker.list_consumers.side_effect = RuntimeError("no")
        source.close()
        source.broker.close.assert_called_once()


class TestShutdownDoesNotOutlastTheGracePeriod:
    """The release is housekeeping -- the idle sweep does the same work later --
    and it runs after SIGTERM, where two commands per stream against a host that
    is not answering cost a socket timeout each. Overrunning the grace period
    earns a SIGKILL, which loses the shutdown this was a part of.
    """

    def test_a_broker_already_known_down_is_not_tidied_up(self):
        source = make_source()
        source.broker.connection_healthy = False
        source.close()
        source.broker.list_consumers.assert_not_called()
        source.broker.close.assert_called_once()

    def test_the_release_gives_up_once_it_has_spent_its_budget(self):
        source = make_source()
        source.broker.connection_healthy = True
        source.broker.list_consumers.return_value = []
        # Clock reads: the deadline, then one per stream. The second stream is
        # reached with the budget already spent.
        with patch(
            "mdx.source.source_redis_stream.time.monotonic",
            side_effect=[0.0, 0.0, RELEASE_BUDGET_SECONDS + 1],
        ):
            source.close()
        assert source.broker.list_consumers.call_count == 1
        source.broker.close.assert_called_once()

    def test_a_release_inside_the_budget_visits_every_stream(self):
        source = make_source()
        source.broker.connection_healthy = True
        source.broker.list_consumers.return_value = []
        with patch(
            "mdx.source.source_redis_stream.time.monotonic",
            side_effect=[0.0, 0.0, 0.1],
        ):
            source.close()
        assert source.broker.list_consumers.call_count == 2


class TestABlockingReadHasToFitInsideTheSocketTimeout:
    """A blocking XREADGROUP is only answered when the block elapses, so a
    socket timeout under it makes every *idle* poll a timeout: the read is
    reported as a broker failure and readiness flaps with nothing wrong. Two
    knobs in different sections, so the source hands its block time to the
    broker, which owns the timeout and reports the mismatch.
    """

    def test_the_block_time_is_checked_against_the_connection(self):
        source = make_source()
        source.broker.warn_if_block_exceeds_timeout.assert_called_once_with(
            100, "event_bridge.redis_source.consumer_config.block_time",
        )

    def test_the_coerced_block_time_is_what_gets_checked(self):
        """Not the configured one: a value that fell back to the default would
        otherwise be reported against a block time nothing uses."""
        config = {
            "event_bridge": {
                "redis_source": {
                    "streams": {"incident": "i", "alert": "a"},
                    "consumer_group": "g",
                    "consumer_config": {"block_time": ""},
                }
            }
        }
        source = make_source(config)
        assert source.broker.warn_if_block_exceeds_timeout.call_args.args[0] == (
            source.block_ms
        )


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


class TestRejectionIsOneDecision:
    """What happens to an entry this source cannot use is decided in one place.

    It used to be restated at each of the five drop sites across the three read
    paths, which is why these are worth asserting together: the paths agreeing
    is the property, and it is the kind that decays quietly when a sixth site is
    added to whichever loop the next change happens to touch.
    """

    GROUP = "alert-bridge-vlm-group"

    def test_every_read_path_counts_and_acks_a_rejected_entry(self):
        """read_data, read and poll reject for different reasons and must still
        do the same two things about it."""
        cases = [
            ("read_data", envelope(b"{}"), "schema_invalid"),
            ("read", {b"unexpected": b"field"}, "no_payload"),
            ("poll", envelope(b"\x08\x01"), "undecodable"),
        ]
        for method, fields, reason in cases:
            source = make_source()
            source.broker.read_group.return_value = [
                ("mdx-incidents", b"1-0", fields)
            ]
            with patch("mdx.source.source_redis_stream.record_source_drop") as drop:
                getattr(source, method)()
            drop.assert_called_once_with("redis_stream", reason), method
            source.broker.ack.assert_called_once_with(
                "mdx-incidents", self.GROUP, [b"1-0"],
            ), method

    def test_the_policy_is_stated_once(self):
        """The guard for M2. Acking a rejected entry is a delivery-semantics
        decision -- the spec argues for leaving it pending instead -- and it can
        only be reviewed or changed if there is one line to change."""
        import inspect

        from mdx.source import source_redis_stream as module

        body = inspect.getsource(module)
        # Every drop goes through the ledger, so these appear once each: in it.
        assert body.count("record_source_drop(") == 1
        assert body.count("_acks.setdefault(") == 1

    def test_a_rejected_entry_is_not_left_for_the_reclaim_sweep(self):
        """States the current choice explicitly, so changing it is a visible
        change to a named expectation rather than a silent one."""
        source = make_source()
        source.broker.read_group.return_value = [
            ("mdx-incidents", b"1-0", envelope(b"{}"))
        ]
        source.read_data()
        acked = source.broker.ack.call_args.args[2]
        assert acked == [b"1-0"]


class TestTheEndpointIsRequiredByTheConstructor:
    """Not only by ``validate_configuration``.

    The factory validates before it builds, so the operational path was already
    covered — but the guard living only there meant it belonged to one route
    into the source rather than to the source. Built any other way, a config
    with no host fell back to localhost and the source then polled it forever,
    since it tolerates an unreachable broker by design and would never say why
    nothing arrived.

    These construct directly, bypassing ``make_source``, which supplies a host
    for every other case here.
    """

    @pytest.mark.parametrize("redis_block", [{"host": ""}, {"host": "   "}, {}])
    def test_a_config_with_no_host_is_rejected(self, redis_block):
        config = dict(CONFIG, redis=redis_block)
        with patch("mdx.source.source_redis_stream.RedisStreamBroker"):
            with pytest.raises(ValueError, match="redis.host is empty"):
                SourceRedisStream(config)

    def test_the_message_names_what_selected_redis(self):
        config = dict(CONFIG, redis={"host": ""})
        with patch("mdx.source.source_redis_stream.RedisStreamBroker"):
            with pytest.raises(ValueError, match="event_bridge.sourceType"):
                SourceRedisStream(config)

    def test_a_host_still_builds(self):
        assert make_source(dict(CONFIG, redis={"host": "r"})) is not None
