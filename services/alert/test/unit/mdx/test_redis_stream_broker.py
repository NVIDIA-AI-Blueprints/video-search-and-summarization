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

"""Unit tests for ``mdx.redis_stream_broker``.

The envelope this module writes and reads is an interop contract, not an
internal detail: vss-behavior-analytics publishes ``mdx-incidents`` /
``mdx-alerts`` with ``key`` / ``value`` / ``headers`` and the Logstash
``redis_stream`` input reads the VLM output streams with ``data_field =>
"value"``. Publishing under any other field name silently strands every
message, so the field names are pinned here.

The read path is equally deliberate about failure: a Redis outage must surface
as an empty batch rather than an exception, because the consume loop has no
handler for one and would die. Each Redis error class is therefore asserted to
degrade rather than raise.
"""

import json
import logging
from unittest.mock import MagicMock, call, patch

import pytest
import redis

from mdx.redis_stream_broker import (
    DEFAULT_PENDING_MIN_IDLE_MS,
    DEFAULT_PUBLISH_RETRIES,
    DEFAULT_SOCKET_TIMEOUT,
    HEADERS_FIELD,
    KEY_FIELD,
    PAYLOAD_FIELD,
    PAYLOAD_FIELD_PRECEDENCE,
    RedisStreamBroker,
    _NO_CLIENT_RETRY,
    _may_have_landed,
    coerce_tuning,
    extract_envelope,
    message_id_to_epoch_ms,
    require_redis_db,
    resolve_redis_config,
)


def make_broker(config=None):
    """Build a broker with a mocked client so no server is required."""
    broker = RedisStreamBroker(config or {"host": "redis", "port": 6379})
    broker._client = MagicMock(name="redis-client")
    return broker


class TestResolveRedisConfig:
    def test_top_level_redis_block_is_the_base(self):
        config = {"redis": {"host": "redis", "port": 6379, "db": 2}}
        assert resolve_redis_config(config) == {"host": "redis", "port": 6379, "db": 2}

    def test_event_bridge_section_overrides_the_base(self):
        config = {
            "redis": {"host": "redis", "port": 6379},
            "event_bridge": {"redis_source": {"host": "other-redis"}},
        }
        resolved = resolve_redis_config(config, "redis_source")
        assert resolved["host"] == "other-redis"
        assert resolved["port"] == 6379

    def test_none_values_in_the_override_do_not_erase_the_base(self):
        """A commented-out or blank YAML key must not blank the shared setting."""
        config = {
            "redis": {"host": "redis", "port": 6379},
            "event_bridge": {"redis_source": {"host": None}},
        }
        assert resolve_redis_config(config, "redis_source")["host"] == "redis"

    def test_explicit_override_is_applied_last(self):
        config = {
            "redis": {"host": "redis"},
            "event_bridge": {"redis_sink": {"host": "bridge-redis"}},
        }
        resolved = resolve_redis_config(config, "redis_sink", override={"host": "vlm-redis"})
        assert resolved["host"] == "vlm-redis"

    def test_missing_redis_block_yields_an_empty_dict(self):
        assert resolve_redis_config({}) == {}

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_override_does_not_erase_the_base(self, blank):
        """An unset variable substitutes as ``""``, not as an absent key.

        So a per-component block that mentions ``host:`` and is given nothing
        for it used to erase a perfectly good top-level host and send that one
        component to localhost, while the others kept working.
        """
        config = {
            "redis": {"host": "redis", "password": "s3cret"},
            "event_bridge": {"redis_source": {"host": blank, "password": blank}},
        }
        resolved = resolve_redis_config(config, "redis_source")
        assert resolved["host"] == "redis"
        assert resolved["password"] == "s3cret"

    def test_a_blank_explicit_override_does_not_erase_it_either(self):
        config = {"redis": {"host": "redis"}}
        assert resolve_redis_config(
            config, override={"host": ""},
        )["host"] == "redis"

    def test_a_named_value_still_overrides(self):
        """The rule must not swallow a real setting along with the blanks."""
        config = {"redis": {"host": "redis", "db": 0}}
        resolved = resolve_redis_config(
            config, override={"host": "other", "db": 3},
        )
        assert (resolved["host"], resolved["db"]) == ("other", 3)


class TestThePortIsCheckedRatherThanPassedThrough:
    """An out-of-range port used to reach the client and fail as a connection
    error — reported against the address, which reads as "Redis is down" and
    sends the operator to look at a machine that is fine.
    """

    @pytest.mark.parametrize("port", [0, -1, 65536, 70000])
    def test_a_number_that_is_not_a_port_is_rejected(self, port):
        with pytest.raises(ValueError, match="not a TCP port"):
            RedisStreamBroker({"host": "redis", "port": port})

    @pytest.mark.parametrize("port", ["six-three-seven-nine", "63 79", []])
    def test_a_value_that_is_not_a_number_is_rejected(self, port):
        with pytest.raises(ValueError, match="must be a TCP port number"):
            RedisStreamBroker({"host": "redis", "port": port})

    @pytest.mark.parametrize("port", [None, "", "   "])
    def test_absent_or_blank_means_the_registered_port(self, port):
        """Safe to infer, unlike the host: a config naming a host and no port
        means the ordinary one."""
        assert RedisStreamBroker({"host": "redis", "port": port}).port == 6379

    def test_a_config_that_omits_the_key_entirely_gets_the_default(self):
        assert RedisStreamBroker({"host": "redis"}).port == 6379

    @pytest.mark.parametrize("port,expected", [(6380, 6380), ("6380", 6380), (" 6380 ", 6380)])
    def test_a_port_in_range_is_used(self, port, expected):
        assert RedisStreamBroker({"host": "redis", "port": port}).port == expected

    @pytest.mark.parametrize("port", [1, 65535])
    def test_the_bounds_themselves_are_allowed(self, port):
        assert RedisStreamBroker({"host": "redis", "port": port}).port == port


class TestMessageIdToEpochMs:
    def test_extracts_the_millisecond_prefix(self):
        assert message_id_to_epoch_ms(b"1700000000000-0") == 1700000000000

    def test_accepts_str_ids(self):
        assert message_id_to_epoch_ms("1700000000000-5") == 1700000000000

    @pytest.mark.parametrize("value", [None, b"", b"not-an-id", b"0-0", "abc-1"])
    def test_unparseable_ids_return_none(self, value):
        assert message_id_to_epoch_ms(value) is None

    @pytest.mark.parametrize("value", [
        b"99999999999999999999-0",
        b"253402300800000-0",  # 10000-01-01T00:00:00Z, one ms past the bound
        str(2 ** 70).encode() + b"-0",
    ])
    def test_a_date_no_calendar_has_returns_none_too(self, value):
        """Redis accepts an explicit entry ID, so this half of the ID is
        producer input rather than a clock reading, and every caller treats what
        comes back as a Unix epoch. An unrepresentable one is not a bad
        measurement, it is an argument that raises -- and it raises on a path
        that has already acked the entries it would lose."""
        assert message_id_to_epoch_ms(value) is None

    def test_the_last_representable_millisecond_is_still_a_timestamp(self):
        """The bound is inclusive, so it is a date and not an off-by-one."""
        assert message_id_to_epoch_ms(b"253402300799999-0") == 253402300799999


class TestExtractEnvelope:
    def test_reads_the_mdx_envelope(self):
        payload, key, headers = extract_envelope(
            {KEY_FIELD: b"sensor-1", PAYLOAD_FIELD: b"\x08\x01", HEADERS_FIELD: b'{"a": "b"}'}
        )
        assert payload == b"\x08\x01"
        assert key == b"sensor-1"
        assert headers == {"a": "b"}

    def test_accepts_str_field_names_and_values(self):
        """Tolerates producers (and tests) that ran with decode_responses on."""
        payload, key, _ = extract_envelope({"key": "sensor-1", "value": '{"id": 1}'})
        assert payload == b'{"id": 1}'
        assert key == b"sensor-1"

    @pytest.mark.parametrize("field", [b"metadata", b"data", b"payload"])
    def test_falls_back_to_alternate_payload_fields(self, field):
        """RT-VLM defaults to ``metadata``; the pre-MDX Alert prototype used
        ``data`` / ``payload``. Reading those keeps older producers usable."""
        payload, _key, _headers = extract_envelope({field: b"body"})
        assert payload == b"body"

    def test_canonical_value_field_wins_over_fallbacks(self):
        payload, _key, _headers = extract_envelope({PAYLOAD_FIELD: b"canonical", b"data": b"legacy"})
        assert payload == b"canonical"


class TestDualFormatPrecedence:
    """Two envelope formats reach this source and both must decode the same way.

    The MDX envelope puts the body in ``value``; the JSON envelope puts it in
    ``data`` with ``metadata`` as a sidecar of attributes describing it. An
    entry carrying both a body and a sidecar therefore has one correct answer,
    and it is never the sidecar: reading ``metadata`` as the event yields a
    payload that decodes cleanly but describes nothing the pipeline can verify,
    which is the failure the fixed precedence exists to prevent.
    """

    def test_data_wins_over_metadata_on_a_json_envelope(self):
        payload, _key, _headers = extract_envelope(
            {b"data": b"the-event", b"metadata": b'{"sensor": "s1"}'}
        )
        assert payload == b"the-event"

    def test_payload_wins_over_metadata(self):
        payload, _key, _headers = extract_envelope({b"payload": b"the-event", b"metadata": b"sidecar"})
        assert payload == b"the-event"

    def test_metadata_is_still_read_when_it_is_the_only_field(self):
        """RT-VLM's REDIS_PAYLOAD_KEY default; last resort, not ignored."""
        payload, _key, _headers = extract_envelope({b"metadata": b"the-event"})
        assert payload == b"the-event"

    def test_precedence_holds_regardless_of_field_insertion_order(self):
        """Guards against a first-match-wins regression: dict order must not
        decide which field is the body."""
        ordered = extract_envelope({b"data": b"body", b"metadata": b"sidecar"})[0]
        reversed_ = extract_envelope({b"metadata": b"sidecar", b"data": b"body"})[0]
        assert ordered == reversed_ == b"body"

    def test_the_full_precedence_chain_is_ordered(self):
        fields = {
            PAYLOAD_FIELD: b"mdx",
            b"data": b"json",
            b"payload": b"legacy",
            b"metadata": b"sidecar",
        }
        for expected in (b"mdx", b"json", b"legacy", b"sidecar"):
            assert extract_envelope(fields)[0] == expected
            fields.pop(next(f for f in PAYLOAD_FIELD_PRECEDENCE if f in fields))

    def test_precedence_starts_with_the_field_publish_uses(self):
        """The read contract has to begin where the write contract is."""
        assert PAYLOAD_FIELD_PRECEDENCE[0] == PAYLOAD_FIELD
        assert PAYLOAD_FIELD_PRECEDENCE[-1] == b"metadata"

    def test_missing_payload_returns_none(self):
        payload, key, headers = extract_envelope({b"unrelated": b"x"})
        assert payload is None
        assert key is None
        assert headers == {}

    def test_empty_fields_map_is_safe(self):
        assert extract_envelope({}) == (None, None, {})

    def test_non_json_headers_are_ignored_rather_than_raising(self):
        _payload, _key, headers = extract_envelope({PAYLOAD_FIELD: b"x", HEADERS_FIELD: b"not json"})
        assert headers == {}

    def test_non_object_headers_are_ignored(self):
        _payload, _key, headers = extract_envelope({PAYLOAD_FIELD: b"x", HEADERS_FIELD: b"[1, 2]"})
        assert headers == {}


class TestEnsureGroup:
    def test_creates_the_group_and_the_stream(self):
        broker = make_broker()
        assert broker.ensure_group("mdx-incidents", "grp") is True
        broker._client.xgroup_create.assert_called_once_with(
            "mdx-incidents", "grp", id="$", mkstream=True
        )

    def test_default_start_id_matches_kafka_latest_semantics(self):
        broker = make_broker()
        broker.ensure_group("s", "g")
        assert broker._client.xgroup_create.call_args.kwargs["id"] == "$"

    def test_start_id_is_configurable_for_replay(self):
        broker = make_broker()
        broker.ensure_group("s", "g", start_id="0-0")
        assert broker._client.xgroup_create.call_args.kwargs["id"] == "0-0"

    def test_existing_group_is_not_an_error(self):
        broker = make_broker()
        broker._client.xgroup_create.side_effect = redis.exceptions.ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        assert broker.ensure_group("s", "g") is True

    def test_result_is_cached_so_every_poll_does_not_hit_redis(self):
        broker = make_broker()
        broker.ensure_group("s", "g")
        broker.ensure_group("s", "g")
        assert broker._client.xgroup_create.call_count == 1

    def test_other_response_errors_report_failure(self):
        broker = make_broker()
        broker._client.xgroup_create.side_effect = redis.exceptions.ResponseError("WRONGTYPE")
        assert broker.ensure_group("s", "g") is False

    def test_connection_error_reports_failure_and_drops_the_client(self):
        broker = make_broker()
        broker._client.xgroup_create.side_effect = redis.exceptions.ConnectionError("down")
        assert broker.ensure_group("s", "g") is False
        assert broker._client is None


class TestReadGroup:
    def test_reads_all_streams_in_a_single_round_trip(self):
        """One XREADGROUP across both streams keeps incident and alert latency
        symmetric; reading them in sequence would add a block per stream."""
        broker = make_broker()
        broker._client.xreadgroup.return_value = []
        broker.read_group(["mdx-incidents", "mdx-alerts"], "grp", "c1", count=10, block_ms=100)
        assert broker._client.xreadgroup.call_args.kwargs["streams"] == {
            "mdx-incidents": ">",
            "mdx-alerts": ">",
        }

    def test_flattens_the_response_and_decodes_stream_names(self):
        broker = make_broker()
        broker._client.xreadgroup.return_value = [
            (b"mdx-incidents", [(b"1-0", {PAYLOAD_FIELD: b"a"}), (b"1-1", {PAYLOAD_FIELD: b"b"})]),
            (b"mdx-alerts", [(b"2-0", {PAYLOAD_FIELD: b"c"})]),
        ]
        entries = broker.read_group(["mdx-incidents", "mdx-alerts"], "g", "c", 10, 100)
        assert [(s, i) for s, i, _ in entries] == [
            ("mdx-incidents", b"1-0"),
            ("mdx-incidents", b"1-1"),
            ("mdx-alerts", b"2-0"),
        ]

    def test_no_streams_configured_short_circuits(self):
        broker = make_broker()
        assert broker.read_group([], "g", "c", 10, 100) == []
        broker._client.xreadgroup.assert_not_called()

    def test_blank_stream_names_are_filtered_out(self):
        broker = make_broker()
        broker._client.xreadgroup.return_value = []
        broker.read_group(["real", "", None], "g", "c", 10, 100)
        assert broker._client.xreadgroup.call_args.kwargs["streams"] == {"real": ">"}

    def test_connection_error_returns_empty_and_forces_reconnect(self):
        broker = make_broker()
        broker._client.xreadgroup.side_effect = redis.exceptions.ConnectionError("down")
        assert broker.read_group(["s"], "g", "c", 10, 100) == []
        assert broker._client is None

    def test_timeout_returns_empty_without_dropping_the_client(self):
        broker = make_broker()
        client = broker._client
        client.xreadgroup.side_effect = redis.exceptions.TimeoutError("blocked")
        assert broker.read_group(["s"], "g", "c", 10, 100) == []
        assert broker._client is client

    def test_nogroup_clears_the_cache_so_the_group_is_recreated(self):
        """A flushed Redis loses the group; the next poll has to recreate it."""
        broker = make_broker()
        broker.ensure_group("s", "g")
        broker._client.xreadgroup.side_effect = redis.exceptions.ResponseError("NOGROUP no such key")
        assert broker.read_group(["s"], "g", "c", 10, 100) == []
        assert broker._ensured_groups == set()

    def test_generic_redis_error_returns_empty(self):
        broker = make_broker()
        broker._client.xreadgroup.side_effect = redis.exceptions.RedisError("boom")
        assert broker.read_group(["s"], "g", "c", 10, 100) == []

    def test_none_response_is_tolerated(self):
        broker = make_broker()
        broker._client.xreadgroup.return_value = None
        assert broker.read_group(["s"], "g", "c", 10, 100) == []


class TestAck:
    def test_acks_every_id_in_one_call(self):
        broker = make_broker()
        broker.ack("s", "g", [b"1-0", b"1-1"])
        broker._client.xack.assert_called_once_with("s", "g", b"1-0", b"1-1")

    def test_empty_id_list_is_a_no_op(self):
        broker = make_broker()
        broker.ack("s", "g", [])
        broker._client.xack.assert_not_called()

    def test_failures_are_swallowed(self):
        """An un-acked entry is replayable; raising here would kill the loop."""
        broker = make_broker()
        broker._client.xack.side_effect = redis.exceptions.RedisError("boom")
        broker.ack("s", "g", [b"1-0"])


class TestAdd:
    def test_publishes_the_mdx_envelope(self):
        broker = make_broker()
        broker.add("mdx-vlm-incidents", b"\x08\x01", key="sensor-1", headers={"h": "v"})
        stream, fields = broker._client.xadd.call_args.args
        assert stream == "mdx-vlm-incidents"
        assert fields[KEY_FIELD] == b"sensor-1"
        assert fields[PAYLOAD_FIELD] == b"\x08\x01"
        assert json.loads(fields[HEADERS_FIELD]) == {"h": "v"}

    def test_headers_default_to_an_empty_json_object(self):
        """behavior-analytics and VIOS both write ``{}`` rather than omitting
        the field; Logstash's filter removes it unconditionally."""
        broker = make_broker()
        broker.add("s", b"body")
        fields = broker._client.xadd.call_args.args[1]
        assert fields[HEADERS_FIELD] == "{}"
        assert fields[KEY_FIELD] == b""

    def test_does_not_trim_by_default(self):
        """The stream belongs to the deployment, not to Alert MS.

        A MAXLEN on every XADD makes ordinary successful output delete a
        customer's older entries, which is a retention decision this service
        cannot make for them. Trimming is opt-in.
        """
        broker = make_broker()
        broker.add("s", b"body")
        assert broker._client.xadd.call_args.kwargs == {}
        assert broker.maxlen is None

    def test_maxlen_is_configurable(self):
        broker = make_broker({"maxlen": 50})
        broker.add("s", b"body")
        assert broker._client.xadd.call_args.kwargs == {
            "maxlen": 50,
            "approximate": True,
        }

    @pytest.mark.parametrize("maxlen", [0, -1])
    def test_non_positive_maxlen_disables_trimming(self, maxlen):
        broker = make_broker({"maxlen": maxlen})
        broker.add("s", b"body")
        assert broker._client.xadd.call_args.kwargs == {}

    def test_unparseable_maxlen_leaves_the_stream_untrimmed(self):
        """Guessing a cap deletes records; declining to trim does not."""
        broker = make_broker({"maxlen": "not-a-number"})
        assert broker.maxlen is None
        broker.add("s", b"body")
        assert broker._client.xadd.call_args.kwargs == {}

    def test_returns_the_generated_entry_id(self):
        broker = make_broker()
        broker._client.xadd.return_value = b"1700000000000-0"
        assert broker.add("s", b"body") == b"1700000000000-0"

    def test_connection_error_returns_none_and_forces_reconnect(self):
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xadd.side_effect = redis.exceptions.ConnectionError("down")
        # Retries rebuild the client, so keep the replacement mocked too rather
        # than letting the retry dial a real socket.
        rebuilt = MagicMock(name="rebuilt")
        rebuilt.xadd.side_effect = redis.exceptions.ConnectionError("still down")
        with patch("mdx.redis_stream_broker.redis.Redis", return_value=rebuilt):
            assert broker.add("s", b"body") is None
        assert broker._client is None

    def test_redis_error_returns_none(self):
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xadd.side_effect = redis.exceptions.RedisError("boom")
        assert broker.add("s", b"body") is None


class TestPublishRetry:
    """A redisStream sink is the payload's only destination.

    Nothing upstream will hand the verdict back after the source acked, so a
    write lost to a broker blip is gone for good. These tests pin the bounded
    retry and the counter that makes a real drop visible.
    """

    def test_a_transient_failure_is_retried_and_recovers(self):
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xadd.side_effect = [
            redis.exceptions.RedisError("blip"),
            b"1700000000000-0",
        ]
        with patch.object(broker, "_record_publish_failure") as record:
            assert broker.add("s", b"body") == b"1700000000000-0"
        record.assert_called_once_with("recovered")

    def test_a_connection_error_rebuilds_the_client_before_retrying(self):
        """The Redis-restart case: the retry must not reuse the dead socket."""
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xadd.side_effect = redis.exceptions.ConnectionError("down")
        rebuilt = MagicMock(name="rebuilt")
        rebuilt.xadd.return_value = b"1700000000000-0"
        with patch("mdx.redis_stream_broker.redis.Redis", return_value=rebuilt):
            assert broker.add("s", b"body") == b"1700000000000-0"
        assert rebuilt.xadd.call_count == 1

    def test_exhausted_retries_drop_the_payload_and_count_it(self):
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xadd.side_effect = redis.exceptions.RedisError("boom")
        with patch.object(broker, "_record_publish_failure") as record:
            assert broker.add("s", b"body") is None
        record.assert_called_once_with("dropped")
        assert broker._client.xadd.call_count == DEFAULT_PUBLISH_RETRIES + 1

    def test_a_first_attempt_success_counts_nothing(self):
        broker = make_broker()
        broker._client.xadd.return_value = b"1700000000000-0"
        with patch.object(broker, "_record_publish_failure") as record:
            broker.add("s", b"body")
        record.assert_not_called()

    def test_retries_can_be_disabled(self):
        broker = make_broker({"publish_retries": 0})
        broker._client.xadd.side_effect = redis.exceptions.RedisError("boom")
        assert broker.add("s", b"body") is None
        assert broker._client.xadd.call_count == 1

    @pytest.mark.parametrize("value", ["not-a-number", None])
    def test_unparseable_retry_count_falls_back_to_the_default(self, value):
        assert make_broker({"publish_retries": value}).publish_retries == DEFAULT_PUBLISH_RETRIES

    def test_a_negative_retry_count_is_clamped_to_zero(self):
        assert make_broker({"publish_retries": -3}).publish_retries == 0


class TestARetryOfAnUnansweredWriteMayDuplicate:
    """A retry appends a second entry when the first one landed unseen.

    Redis assigns the entry ID, so a re-sent XADD is a new entry rather than
    the same one twice, and there is nothing to ask the server whether the
    first attempt applied. Retrying is still the right choice -- the sink has
    no second destination -- so what these tests pin is that the duplicate is
    counted where it can happen and not counted where it cannot, which is the
    only thing that lets a duplicate found downstream be dated.
    """

    @pytest.mark.parametrize("exc", [
        redis.exceptions.ConnectionError("Connection closed by server"),
        redis.exceptions.TimeoutError("Timeout reading from socket"),
        redis.exceptions.InvalidResponse("bad reply"),
    ])
    def test_a_write_with_no_reply_may_have_landed(self, exc):
        assert _may_have_landed(exc) is True

    @pytest.mark.parametrize("exc", [
        redis.exceptions.ConnectionError("Error 111 connecting to redis:6379. Refused."),
        redis.exceptions.TimeoutError("Timeout connecting to server"),
        redis.exceptions.AuthenticationError("invalid username-password pair"),
        redis.exceptions.BusyLoadingError("Redis is loading the dataset in memory"),
        redis.exceptions.NoPermissionError("NOPERM no permissions to run 'xadd'"),
        redis.exceptions.ResponseError("WRONGTYPE"),
        redis.exceptions.RedisError("something else entirely"),
    ])
    def test_a_refusal_or_an_unopened_connection_did_not_land(self, exc):
        assert _may_have_landed(exc) is False

    def test_retrying_an_unanswered_write_counts_the_possible_duplicate(self):
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xadd.side_effect = [
            redis.exceptions.TimeoutError("Timeout reading from socket"),
            b"1700000000000-0",
        ]
        with patch.object(broker, "_record_publish_failure") as record:
            assert broker.add("s", b"body") == b"1700000000000-0"
        # Recovered as well: the payload was not lost. The two answer different
        # questions and both apply to this publish.
        assert record.call_args_list == [call("replayed"), call("recovered")]

    def test_retrying_a_refusal_counts_only_the_recovery(self):
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xadd.side_effect = [
            redis.exceptions.ResponseError("LOADING"),
            b"1700000000000-0",
        ]
        with patch.object(broker, "_record_publish_failure") as record:
            broker.add("s", b"body")
        record.assert_called_once_with("recovered")

    def test_an_exhausted_publish_is_a_drop_not_a_replay(self):
        """Nothing was re-sent after the last attempt, so no second copy can
        exist because of it."""
        broker = make_broker({"publish_retries": 0})
        broker._client.xadd.side_effect = redis.exceptions.TimeoutError(
            "Timeout reading from socket"
        )
        with patch.object(broker, "_record_publish_failure") as record:
            assert broker.add("s", b"body") is None
        record.assert_called_once_with("dropped")


class TestOnePublishCannotCostAnUnboundedAmountOfTime:
    """A retry count does not bound time, because each attempt can spend the
    whole connect timeout.

    Against a host that accepts packets and answers nothing, one publish
    measured 126.7 seconds at the shipped timeouts -- per verdict, on the
    consume path, for a destination that has already said it is not there. The
    budget is what turns "three tries" into "three tries or fifteen seconds,
    whichever comes first".
    """

    @staticmethod
    def _broker_that_never_answers(budget):
        broker = make_broker({
            "publish_budget": budget, "publish_retry_backoff": 0,
        })
        broker._client.xadd.side_effect = redis.exceptions.RedisError("timeout")
        return broker

    def test_a_spent_budget_stops_the_retries(self):
        broker = self._broker_that_never_answers(budget=5)
        with patch("mdx.redis_stream_broker.time.monotonic",
                   side_effect=[0, 100, 200]):
            assert broker.add("s", b"body") is None
        assert broker._client.xadd.call_count == 1, "retried past the budget"

    def test_the_payload_is_still_counted_as_dropped(self):
        """Giving up early is still giving up, and the series that says a verdict
        reached nobody must not depend on how the attempts ended."""
        broker = self._broker_that_never_answers(budget=5)
        with patch("mdx.redis_stream_broker.time.monotonic",
                   side_effect=[0, 100, 200]):
            with patch.object(broker, "_record_publish_failure") as record:
                broker.add("s", b"body")
        record.assert_called_once_with("dropped")

    def test_a_budget_that_is_not_spent_changes_nothing(self):
        broker = self._broker_that_never_answers(budget=60)
        assert broker.add("s", b"body") is None
        assert broker._client.xadd.call_count == DEFAULT_PUBLISH_RETRIES + 1

    def test_the_budget_can_be_turned_off(self):
        """Zero means no ceiling, for a deployment that would rather wait."""
        broker = self._broker_that_never_answers(budget=0)
        assert broker.add("s", b"body") is None
        assert broker._client.xadd.call_count == DEFAULT_PUBLISH_RETRIES + 1

    def test_one_batch_shares_one_budget(self):
        """The fallback publishes each entry on its own. Given a budget each, a
        ten-entry batch against a blackhole costs ten budgets -- the
        multiplication the ceiling exists to stop, one level up."""
        broker = make_broker({"publish_budget": 5, "publish_retry_backoff": 0})
        pipe = MagicMock(name="pipeline")
        broker._client.pipeline.return_value = pipe
        pipe.execute.side_effect = redis.exceptions.ConnectionError("down")

        deadlines = []
        with patch.object(broker, "_add",
                          side_effect=lambda *a: deadlines.append(a[-1])):
            broker.add_batch("s", [(b"a", "k1"), (b"b", "k2"), (b"c", "k3")])

        assert len(set(deadlines)) == 1, "each entry was given its own budget"

    def test_an_unparseable_budget_falls_back_to_the_default(self):
        assert make_broker({"publish_budget": ""}).publish_budget == 15.0


class TestAckIsRetriedBecauseItIsIdempotent:
    """The better retry candidate of the two, and the one that had none.

    Acking twice is acking once, so unlike a publish this cannot duplicate
    anything. What a lost ack does cost is the entry staying pending until the
    reclaim sweep gives it to another consumer, which verifies it a second time
    and publishes a second verdict -- the expensive path in this service, paid to
    save two round trips.
    """

    def test_a_transient_failure_is_retried(self):
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xack.side_effect = [
            redis.exceptions.RedisError("blip"), 1,
        ]
        broker.ack("s", "g", [b"1-0"])
        assert broker._client.xack.call_count == 2

    def test_a_recovered_ack_is_counted_apart_from_a_recovered_publish(self):
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xack.side_effect = [
            redis.exceptions.RedisError("blip"), 1,
        ]
        with patch.object(broker, "_record_publish_failure") as record:
            broker.ack("s", "g", [b"1-0"])
        record.assert_called_once_with("ack_recovered")

    def test_an_ack_that_never_lands_is_counted(self):
        """Without a series this was invisible until a duplicate verdict turned
        up downstream with nothing to tie it to."""
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xack.side_effect = redis.exceptions.RedisError("boom")
        with patch.object(broker, "_record_publish_failure") as record:
            broker.ack("s", "g", [b"1-0"])
        record.assert_called_once_with("ack_dropped")

    def test_it_still_does_not_raise(self):
        """The consume loop calls this; raising would kill it."""
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xack.side_effect = redis.exceptions.RedisError("boom")
        broker.ack("s", "g", [b"1-0"])

    def test_a_first_attempt_success_counts_nothing(self):
        broker = make_broker()
        with patch.object(broker, "_record_publish_failure") as record:
            broker.ack("s", "g", [b"1-0"])
        record.assert_not_called()

    def test_a_dropped_connection_is_rebuilt_before_the_retry(self):
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xack.side_effect = redis.exceptions.ConnectionError("down")
        rebuilt = MagicMock(name="rebuilt")
        rebuilt.xack.return_value = 1
        with patch("mdx.redis_stream_broker.redis.Redis", return_value=rebuilt):
            broker.ack("s", "g", [b"1-0"])
        assert rebuilt.xack.call_count == 1

    def test_the_budget_applies_here_too(self):
        broker = make_broker({"publish_budget": 5, "publish_retry_backoff": 0})
        broker._client.xack.side_effect = redis.exceptions.RedisError("timeout")
        with patch("mdx.redis_stream_broker.time.monotonic",
                   side_effect=[0, 100, 200]):
            broker.ack("s", "g", [b"1-0"])
        assert broker._client.xack.call_count == 1


class TestTheValuesThatDecideWhereThisConnects:
    """``db`` is on the loud side of the line, with the host and the port.

    It selects which logical database is read and written, so a value coerced to
    0 connects to a database that exists, accepts every command and consumes an
    empty stream in the wrong place -- which reads as "the producer published
    nothing" and sends the operator to look at the producer.
    """

    @pytest.mark.parametrize("value,expected", [
        (None, 0), ("", 0), ("   ", 0), (3, 3), ("3", 3), (" 3 ", 3),
    ])
    def test_a_database_number_is_read(self, value, expected):
        assert require_redis_db(value) == expected

    @pytest.mark.parametrize("value", ["one", "3.5", [], {}])
    def test_a_value_that_is_not_a_number_is_refused(self, value):
        with pytest.raises(ValueError, match="redis.db must be a database number"):
            require_redis_db(value)

    def test_a_negative_database_is_refused(self):
        with pytest.raises(ValueError, match="database numbers start at 0"):
            require_redis_db(-1)

    def test_the_constructor_refuses_it_too(self):
        with pytest.raises(ValueError, match="redis.db"):
            RedisStreamBroker({"host": "redis", "db": "one"})


class TestTheSocketTimeoutsAreCoerced:
    """redis-py compares these against a clock, so a string reaches the socket
    layer intact and raises ``TypeError`` on the first command -- outside every
    ``redis.exceptions.*`` handler in the module, so it killed the process at the
    first poll instead of being reported as a config mistake. A rendered config
    makes strings of everything, which is how ``socket_timeout: "30"`` gets here.
    """

    @pytest.mark.parametrize("key", ["socket_timeout", "socket_connect_timeout"])
    def test_a_numeric_string_is_a_number(self, key):
        broker = RedisStreamBroker({"host": "redis", key: "30"})
        assert getattr(broker, f"_{key}") == 30.0

    @pytest.mark.parametrize("key", ["socket_timeout", "socket_connect_timeout"])
    def test_an_unusable_value_falls_back_to_the_default(self, key):
        broker = RedisStreamBroker({"host": "redis", key: "half a minute"})
        assert getattr(broker, f"_{key}") == DEFAULT_SOCKET_TIMEOUT

    @pytest.mark.parametrize("key", ["socket_timeout", "socket_connect_timeout"])
    def test_zero_is_floored_rather_than_honoured(self, key):
        """Zero disables timeouts in redis-py, i.e. blocks forever, which is
        never what a deployment meant by setting the value it set."""
        broker = RedisStreamBroker({"host": "redis", key: 0})
        assert getattr(broker, f"_{key}") == 0.1

    def test_the_client_is_built_with_the_coerced_values(self):
        broker = RedisStreamBroker({
            "host": "redis", "socket_timeout": "5", "socket_connect_timeout": "2",
        })
        with patch("mdx.redis_stream_broker.redis.Redis") as redis_cls:
            broker.client
        kwargs = redis_cls.call_args.kwargs
        assert kwargs["socket_timeout"] == 5.0
        assert kwargs["socket_connect_timeout"] == 2.0


class TestTheSharedTuningCoercer:
    """One policy for every knob that tunes behaviour: warn, fall back, start.

    The counterpart of the loud checks above. A typo in a backoff is not worth
    refusing to start over -- but falling back silently, which the copies of this
    did, meant an operator's setting had no effect they could see.
    """

    def test_a_number_passes_through(self):
        assert coerce_tuning(7, 3, "a.b") == 7

    def test_a_numeric_string_is_read(self):
        assert coerce_tuning("7", 3, "a.b") == 7

    @pytest.mark.parametrize("value", ["", "   ", None, "lots", [], {}])
    def test_an_unusable_value_falls_back(self, value):
        assert coerce_tuning(value, 3, "a.b") == 3

    def test_the_fallback_is_announced_with_the_full_path(self, caplog):
        """The leaf name alone appears under both ``redis`` and a component's
        ``consumer_config``, so it sends the operator to the wrong file."""
        with caplog.at_level("WARNING"):
            coerce_tuning("lots", 3, "event_bridge.redis_source.consumer_config.count")
        assert "event_bridge.redis_source.consumer_config.count" in caplog.text

    def test_a_value_below_the_floor_is_raised_to_it(self):
        assert coerce_tuning(0, 1.0, "a.b", cast=float, minimum=0.05) == 0.05

    def test_the_floor_is_announced_too(self, caplog):
        with caplog.at_level("WARNING"):
            coerce_tuning(-1, 1.0, "a.b", cast=float, minimum=0.05)
        assert "below the" in caplog.text

    def test_the_default_floor_is_zero(self):
        assert coerce_tuning(-5, 3, "a.b") == 0


class TestClientLifecycle:
    def test_client_is_built_with_binary_responses(self):
        """Payloads are protobuf; decoding responses would corrupt them."""
        broker = RedisStreamBroker({"host": "redis", "port": 6380, "db": 3, "password": "s3cr3t"})
        with patch("mdx.redis_stream_broker.redis.Redis") as redis_cls:
            broker.client
        kwargs = redis_cls.call_args.kwargs
        assert kwargs["decode_responses"] is False
        assert kwargs["host"] == "redis"
        assert kwargs["port"] == 6380
        assert kwargs["db"] == 3
        assert kwargs["password"] == "s3cr3t"

    def test_client_is_reused_across_calls(self):
        broker = RedisStreamBroker({})
        with patch("mdx.redis_stream_broker.redis.Redis") as redis_cls:
            broker.client
            broker.client
        assert redis_cls.call_count == 1

    def test_blank_password_is_sent_as_none(self):
        """An unset ``${REDIS_PASSWORD}`` substitutes to an empty string, which
        Redis would otherwise treat as an actual credential."""
        broker = RedisStreamBroker({"password": ""})
        assert broker.password is None

    def test_reconnect_reasserts_consumer_groups(self):
        """The replacement Redis may not have the group, or the data may have
        been flushed while we were disconnected."""
        broker = make_broker()
        broker.ensure_group("s", "g")
        broker._reset_client()
        assert broker._ensured_groups == set()

    def test_ping_failure_is_reported_not_raised(self):
        broker = make_broker()
        broker._client.ping.side_effect = redis.exceptions.ConnectionError("down")
        assert broker.ping() is False

    def test_ping_success(self):
        assert make_broker().ping() is True

    def test_close_releases_and_clears_the_client(self):
        broker = make_broker()
        client = broker._client
        broker.close()
        client.close.assert_called_once()
        assert broker._client is None

    def test_close_without_a_client_is_a_no_op(self):
        RedisStreamBroker({}).close()

    def test_compose_default_no_longer_points_at_the_bundled_redis(self):
        """An unset REDIS_HOST must not silently attach to this stack's own
        development Redis: a pipeline pointed at the wrong instance is worse
        than one that refuses to start and names the address it tried."""
        assert RedisStreamBroker({"host": ""}).host == "localhost"


class TestCredentialResolution:
    """A customer-managed Redis needs a password, and the only place the plain
    ``password`` key can come from is the rendered service config — a ConfigMap
    in Helm, a bind-mounted file in Compose, neither of them a secret. The
    indirections below are what let the credential stay out of it.
    """

    def test_password_file_is_read(self, tmp_path):
        secret = tmp_path / "redis-password"
        secret.write_text("from-a-secret")
        assert RedisStreamBroker({"password_file": str(secret)}).password == "from-a-secret"

    def test_a_trailing_newline_is_stripped(self, tmp_path):
        """``kubectl create secret --from-literal`` and ``echo >`` both add one,
        and Redis would reject it as part of the password."""
        secret = tmp_path / "redis-password"
        secret.write_text("from-a-secret\n")
        assert RedisStreamBroker({"password_file": str(secret)}).password == "from-a-secret"

    def test_password_file_wins_over_the_inline_value(self):
        """Adding a Secret must override the inline key without also having to
        blank it, or every deployment has to be edited in two places."""
        with patch("builtins.open", new_callable=MagicMock) as opener:
            opener.return_value.__enter__.return_value.read.return_value = "from-a-secret"
            broker = RedisStreamBroker({"password": "inline", "password_file": "/run/secret"})
        assert broker.password == "from-a-secret"

    def test_password_env_is_read(self):
        with patch.dict("os.environ", {"MY_REDIS_PW": "from-the-env"}):
            broker = RedisStreamBroker({"password_env": "MY_REDIS_PW"})
        assert broker.password == "from-the-env"

    def test_an_unreadable_password_file_falls_back_to_a_usable_credential(self):
        """The inline value may well be the working one, and a deployment that
        would have connected should not be stopped from connecting."""
        broker = RedisStreamBroker({"password": "inline", "password_file": "/nope/missing"})
        assert broker.password == "inline"

    def test_an_empty_password_file_falls_back(self, tmp_path):
        secret = tmp_path / "empty"
        secret.write_text("\n")
        assert RedisStreamBroker({"password": "inline", "password_file": str(secret)}).password == "inline"

    def test_an_unset_password_env_falls_back(self):
        with patch.dict("os.environ", {}, clear=True):
            broker = RedisStreamBroker({"password": "inline", "password_env": "NOT_SET"})
        assert broker.password == "inline"

    def test_the_env_is_used_when_the_file_is_missing(self):
        with patch.dict("os.environ", {"MY_REDIS_PW": "from-the-env"}):
            broker = RedisStreamBroker({"password_file": "/nope/missing", "password_env": "MY_REDIS_PW"})
        assert broker.password == "from-the-env"

    def test_a_named_secret_that_yields_nothing_at_all_is_fatal(self):
        """This is the Helm shape: passwordSecret set, so password_file is
        rendered and the inline key is empty. Falling back to *nothing* means
        connecting unauthenticated, and the operator sees NOAUTH on the first
        command rather than the mount that never appeared.
        """
        with pytest.raises(ValueError) as excinfo:
            RedisStreamBroker({"password_file": "/etc/alert-bridge/redis-auth/password"})
        message = str(excinfo.value)
        assert "/etc/alert-bridge/redis-auth/password" in message, "the path is the fix"
        assert "password_file" in message

    def test_an_empty_secret_with_no_fallback_is_fatal(self, tmp_path):
        secret = tmp_path / "empty"
        secret.write_text("\n")
        with pytest.raises(ValueError, match="is empty"):
            RedisStreamBroker({"password_file": str(secret)})

    def test_a_named_env_that_is_unset_with_no_fallback_is_fatal(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="MY_REDIS_PW"):
                RedisStreamBroker({"password_env": "MY_REDIS_PW"})

    def test_naming_no_source_at_all_stays_passwordless(self):
        """An instance with no requirepass is the ordinary local case, and the
        shipped configs render password_file to an empty string."""
        assert RedisStreamBroker({}).password is None
        assert RedisStreamBroker({"password_file": "", "password_env": ""}).password is None

    def test_a_username_enables_redis_acl_auth(self):
        broker = RedisStreamBroker({"username": "alert-bridge", "password": "pw"})
        with patch("mdx.redis_stream_broker.redis.Redis") as redis_cls:
            broker.client
        assert redis_cls.call_args.kwargs["username"] == "alert-bridge"

    def test_a_blank_username_is_sent_as_none(self):
        assert RedisStreamBroker({"username": ""}).username is None


class TestTlsOptions:
    def test_tls_is_off_by_default(self):
        assert RedisStreamBroker({}).tls == {}
        with patch("mdx.redis_stream_broker.redis.Redis") as redis_cls:
            RedisStreamBroker({}).client
        assert "ssl" not in redis_cls.call_args.kwargs

    @pytest.mark.parametrize("key", ["ssl", "tls"])
    def test_either_spelling_enables_tls(self, key):
        assert RedisStreamBroker({key: True}).tls["ssl"] is True

    def test_verification_is_on_by_default_once_tls_is_enabled(self):
        """An encrypted connection that does not check the certificate protects
        against nothing an operator who asked for TLS was worried about."""
        assert RedisStreamBroker({"ssl": True}).tls["ssl_cert_reqs"] == "required"

    def test_verification_can_be_relaxed_explicitly(self):
        """Available for a self-signed development instance, and it says so in
        the config rather than being the silent default."""
        assert RedisStreamBroker({"ssl": True, "ssl_cert_reqs": "none"}).tls["ssl_cert_reqs"] == "none"

    def test_a_private_ca_is_passed_through(self):
        tls = RedisStreamBroker({"ssl": True, "ssl_ca_certs": "/etc/ca.crt"}).tls
        assert tls["ssl_ca_certs"] == "/etc/ca.crt"

    def test_client_certificates_are_passed_through(self):
        tls = RedisStreamBroker(
            {"ssl": True, "ssl_certfile": "/etc/tls.crt", "ssl_keyfile": "/etc/tls.key"}
        ).tls
        assert tls["ssl_certfile"] == "/etc/tls.crt"
        assert tls["ssl_keyfile"] == "/etc/tls.key"

    def test_tls_settings_are_ignored_while_tls_is_off(self):
        """So a config that pre-stages a CA path does not half-enable TLS."""
        assert RedisStreamBroker({"ssl_ca_certs": "/etc/ca.crt"}).tls == {}

    def test_tls_options_reach_the_client(self):
        broker = RedisStreamBroker({"ssl": True, "ssl_ca_certs": "/etc/ca.crt"})
        with patch("mdx.redis_stream_broker.redis.Redis") as redis_cls:
            broker.client
        kwargs = redis_cls.call_args.kwargs
        assert kwargs["ssl"] is True
        assert kwargs["ssl_cert_reqs"] == "required"
        assert kwargs["ssl_ca_certs"] == "/etc/ca.crt"


class TestWritingTheTlsBlockIsAskingForTls:
    """An empty mapping is falsy in Python, so ``tls: {}`` -- a deployment
    writing the block and meaning "on, with the defaults" -- ran unencrypted and
    said nothing about it. The block form is read by presence for that reason.
    """

    @pytest.mark.parametrize("key", ["ssl", "tls"])
    def test_an_empty_block_still_means_on(self, key):
        assert RedisStreamBroker({key: {}}).tls["ssl"] is True

    def test_a_block_with_settings_in_it_means_on_too(self):
        assert RedisStreamBroker({"tls": {"certReqs": "none"}}).tls["ssl"] is True

    def test_settings_nested_under_the_block_are_named_not_ignored(self, caplog):
        """They are not read -- the schema is flat -- so the failure mode is a
        connection that verifies against the system trust store while a CA sits
        in the config. Same class of quiet mistake, one level down."""
        with caplog.at_level(logging.WARNING):
            tls = RedisStreamBroker({"tls": {"ca_certs": "/etc/ca.crt"}}).tls
        assert "ssl_ca_certs" not in tls
        assert "ca_certs" in caplog.text
        assert "ssl_ca_certs" in caplog.text

    @pytest.mark.parametrize("value", ["", "   ", None, "null", "none"])
    def test_an_unresolved_variable_is_not_a_request(self, value):
        """A rendered config leaves the key present and empty, which has to stay
        off: enabling TLS against a plaintext instance breaks the deployment."""
        assert RedisStreamBroker({"ssl": value}).tls == {}

    @pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
    def test_the_string_forms_of_yes_are_honoured(self, value):
        assert RedisStreamBroker({"ssl": value}).tls["ssl"] is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off"])
    def test_the_string_forms_of_no_are_too(self, value):
        assert RedisStreamBroker({"ssl": value}).tls == {}

    def test_a_value_neither_way_stays_off_and_says_so(self, caplog):
        with caplog.at_level(logging.WARNING):
            tls = RedisStreamBroker({"ssl": "maybe"}).tls
        assert tls == {}
        assert "redis.ssl" in caplog.text

    def test_one_spelling_asking_is_enough(self):
        """``ssl: false`` alongside a written ``tls`` block is a config that has
        been half-migrated between the two spellings, not one asking for
        plaintext."""
        assert RedisStreamBroker({"ssl": False, "tls": {}}).tls["ssl"] is True


class TestTheClientDoesNoRetryingOfItsOwn:
    """redis-py 6 otherwise retries a connect that times out four times, with up
    to ten seconds of jittered sleeping between the attempts, underneath
    publish_retries and publish_budget -- which does not compose with them. One
    command against a host that times out on connect spent four connect attempts
    and ten seconds this module neither asked for nor could see.
    """

    def test_the_client_is_built_for_one_attempt_per_command(self):
        broker = RedisStreamBroker({"host": "redis"})
        with patch("mdx.redis_stream_broker.redis.Redis") as redis_cls:
            broker.client
        assert redis_cls.call_args.kwargs["retry"].get_retries() == 0

    def test_the_deprecated_timeout_flag_is_not_passed(self):
        """redis-py 6 deprecated it, and it never controlled how many attempts
        were made -- only which errors the client's own retry applied to."""
        broker = RedisStreamBroker({"host": "redis"})
        with patch("mdx.redis_stream_broker.redis.Redis") as redis_cls:
            broker.client
        assert "retry_on_timeout" not in redis_cls.call_args.kwargs

    def test_that_policy_runs_a_failing_command_once(self):
        """What the two above buy: the policy object itself, asked to run
        something that fails, makes one attempt and raises -- so the retrying
        this module counts is the only retrying done."""
        attempts = []

        def refuse():
            attempts.append(1)
            raise redis.exceptions.ConnectionError("down")

        with pytest.raises(redis.exceptions.ConnectionError):
            _NO_CLIENT_RETRY.call_with_retry(refuse, lambda _: None)
        assert len(attempts) == 1


class TestABudgetBelowTheSocketTimeoutCannotHold:
    """The budget is read between attempts, and an attempt runs for as long as
    the socket allows. Below the socket timeout it therefore bounds nothing,
    which is why the default timeout sits under the default budget -- so a
    deployment that has raised one and not the other is worth a line.
    """

    def test_the_defaults_fit_inside_each_other(self, caplog):
        with caplog.at_level(logging.WARNING):
            RedisStreamBroker({"host": "redis"})
        assert caplog.text == ""

    def test_the_mismatch_is_reported_with_both_values(self, caplog):
        with caplog.at_level(logging.WARNING):
            RedisStreamBroker({"publish_budget": 15, "socket_timeout": 30})
        assert "publish_budget" in caplog.text
        assert "socket_timeout" in caplog.text

    def test_a_budget_the_socket_timeout_fits_inside_is_quiet(self, caplog):
        with caplog.at_level(logging.WARNING):
            RedisStreamBroker({"publish_budget": 15, "socket_timeout": 5})
        assert "publish_budget" not in caplog.text

    def test_no_ceiling_is_not_a_mismatch(self, caplog):
        """0 removes the budget, so there is nothing for the timeout to exceed."""
        with caplog.at_level(logging.WARNING):
            RedisStreamBroker({"publish_budget": 0, "socket_timeout": 30})
        assert "publish_budget" not in caplog.text


class TestABlockingReadHasToFitInOneCommand:
    """A blocking XREADGROUP is answered when the block elapses, so a socket
    timeout at or under it makes every idle poll a timeout -- reported as a
    broker failure, with readiness flapping while nothing is wrong. The two
    knobs live in different sections and the failure names neither, so the
    broker is asked about it at construction by whoever does the blocking.
    """

    def test_a_block_past_the_timeout_is_reported(self, caplog):
        broker = make_broker({"host": "redis", "socket_timeout": 5})
        with caplog.at_level(logging.WARNING):
            broker.warn_if_block_exceeds_timeout(10_000, "block_time")
        assert "block_time" in caplog.text
        assert "socket_timeout" in caplog.text

    def test_the_shipped_pairing_is_quiet(self, caplog):
        """5s against the shipped 100ms block: an ordinary deployment says
        nothing, because this is a warning about a knob rather than a fact of
        the transport."""
        broker = make_broker({"host": "redis", "socket_timeout": 5})
        with caplog.at_level(logging.WARNING):
            broker.warn_if_block_exceeds_timeout(100, "block_time")
        assert caplog.text == ""

    def test_an_equal_block_is_a_mismatch_too(self, caplog):
        """Equal is not safe: the read is answered at the same moment the socket
        gives up on it, which is a race rather than a working configuration."""
        broker = make_broker({"host": "redis", "socket_timeout": 5})
        with caplog.at_level(logging.WARNING):
            broker.warn_if_block_exceeds_timeout(5_000, "block_time")
        assert "block_time" in caplog.text


class TestConnectionHealth:
    """An unreachable broker returns the same empty entry list as an idle
    stream. Without a health signal the source cannot tell the two apart, and
    reports ready either way — which is the readiness bug this tracks.
    """

    def test_health_is_unknown_before_the_first_command(self):
        assert make_broker().connection_healthy is None

    def test_a_successful_ping_marks_healthy(self):
        broker = make_broker()
        broker.ping()
        assert broker.connection_healthy is True

    def test_a_failed_ping_marks_unhealthy(self):
        broker = make_broker()
        broker._client.ping.side_effect = redis.exceptions.ConnectionError("down")
        broker.ping()
        assert broker.connection_healthy is False

    def test_an_empty_read_leaves_health_alone(self):
        """The idle-stream case: nothing to read is not a fault."""
        broker = make_broker()
        broker._client.xreadgroup.return_value = []
        broker.read_group(["s"], "g", "c", 10, 100)
        assert broker.connection_healthy is True

    def test_a_read_connection_error_marks_unhealthy(self):
        broker = make_broker()
        broker._client.xreadgroup.side_effect = redis.exceptions.ConnectionError("down")
        broker.read_group(["s"], "g", "c", 10, 100)
        assert broker.connection_healthy is False

    def test_a_read_timeout_does_not_mark_unhealthy(self):
        """A blocking XREADGROUP that returns nothing within block_ms is the
        normal idle path, not an outage."""
        broker = make_broker()
        broker._client.xreadgroup.side_effect = redis.exceptions.TimeoutError("blocked")
        broker.read_group(["s"], "g", "c", 10, 100)
        assert broker.connection_healthy is not False

    def test_recovery_marks_healthy_again(self):
        broker = make_broker()
        broker._client.ping.side_effect = redis.exceptions.ConnectionError("down")
        broker.ping()
        broker._client = MagicMock(name="recovered")
        broker.ping()
        assert broker.connection_healthy is True

    def test_a_failed_publish_marks_unhealthy(self):
        broker = make_broker({"publish_retry_backoff": 0})
        broker._client.xadd.side_effect = redis.exceptions.ConnectionError("down")
        rebuilt = MagicMock(name="rebuilt")
        rebuilt.xadd.side_effect = redis.exceptions.ConnectionError("still down")
        with patch("mdx.redis_stream_broker.redis.Redis", return_value=rebuilt):
            broker.add("s", b"body")
        assert broker.connection_healthy is False


class TestAddBatch:
    """One round trip for a whole write call instead of one per entry.

    The caller is on the consume path, so the latency of *n* sequential XADDs is
    time the source spends not reading. The pipeline is not transactional: these
    are independent appends and a MULTI would only add a mode where one bad
    entry discards the rest.
    """

    @staticmethod
    def _pipeline(broker):
        pipe = MagicMock()
        broker._client.pipeline.return_value = pipe
        return pipe

    def test_every_entry_goes_out_in_one_execute(self):
        broker = make_broker()
        pipe = self._pipeline(broker)
        pipe.execute.return_value = [b"1-0", b"1-1", b"1-2"]

        ids = broker.add_batch("s", [(b"a", "k1"), (b"b", "k2"), (b"c", "k3")])

        assert ids == [b"1-0", b"1-1", b"1-2"]
        assert pipe.xadd.call_count == 3
        pipe.execute.assert_called_once()
        broker._client.xadd.assert_not_called()

    def test_the_envelope_matches_the_single_write_path(self):
        """A consumer cannot tell which method published an entry, so the
        batched path must produce byte-identical fields."""
        broker = make_broker()
        pipe = self._pipeline(broker)
        pipe.execute.return_value = [b"1-0", b"1-1"]

        broker.add_batch("s", [(b"payload", "cam-1"), (b"other", None)])

        first = pipe.xadd.call_args_list[0].args
        assert first[0] == "s"
        assert first[1][KEY_FIELD] == b"cam-1"
        assert first[1][PAYLOAD_FIELD] == b"payload"
        assert first[1][HEADERS_FIELD] == "{}"
        # A None key is the empty bytes the envelope uses, not the string
        # "None" — the key is a partition hint a consumer may read.
        assert pipe.xadd.call_args_list[1].args[1][KEY_FIELD] == b""

    def test_trimming_is_passed_through_only_when_configured(self):
        broker = make_broker()
        pipe = self._pipeline(broker)
        pipe.execute.return_value = [b"1-0", b"1-1"]
        broker.add_batch("s", [(b"a", "k"), (b"b", "k")])
        assert "maxlen" not in pipe.xadd.call_args.kwargs

        trimming = make_broker({"maxlen": 500})
        pipe = self._pipeline(trimming)
        pipe.execute.return_value = [b"1-0", b"1-1"]
        trimming.add_batch("s", [(b"a", "k"), (b"b", "k")])
        assert pipe.xadd.call_args.kwargs["maxlen"] == 500

    def test_a_single_entry_takes_the_ordinary_retrying_path(self):
        """A pipeline of one buys nothing and would skip the retry logic."""
        broker = make_broker()
        broker._client.xadd.return_value = b"1-0"
        assert broker.add_batch("s", [(b"a", "k")]) == [b"1-0"]
        broker._client.xadd.assert_called_once()
        broker._client.pipeline.assert_not_called()

    def test_an_empty_batch_touches_nothing(self):
        broker = make_broker()
        assert broker.add_batch("s", []) == []
        broker._client.pipeline.assert_not_called()

    def test_a_failed_pipeline_falls_back_to_individual_writes(self):
        """A pipeline usually fails as a whole, and the retry, backoff and drop
        accounting all live on the single-write path."""
        broker = make_broker()
        pipe = self._pipeline(broker)
        pipe.execute.side_effect = redis.exceptions.RedisError("boom")
        broker._client.xadd.return_value = b"9-0"

        assert broker.add_batch("s", [(b"a", "k1"), (b"b", "k2")]) == [b"9-0", b"9-0"]
        assert broker._client.xadd.call_count == 2

    def test_the_replay_is_counted_as_a_possible_duplicate(self):
        """The commands were sent, so entries may have landed and been replayed.

        Dropping the batch instead would lose responses that cannot be
        reconstructed, so duplicating is the chosen failure -- but it is only
        traceable if the moment it was made is recorded. Someone finding a
        duplicate downstream has this counter to date it by.
        """
        broker = make_broker()
        pipe = self._pipeline(broker)
        pipe.execute.side_effect = redis.exceptions.RedisError("boom")
        broker._client.xadd.return_value = b"9-0"

        with patch("mdx.redis_stream_broker._metrics") as metrics:
            broker.add_batch("s", [(b"a", "k1"), (b"b", "k2")])

        metrics.inc_redis_publish_failure.assert_any_call("replayed")

    def test_a_clean_batch_is_not_counted_as_replayed(self):
        broker = make_broker()
        pipe = self._pipeline(broker)
        pipe.execute.return_value = [b"1-0", b"1-1"]

        with patch("mdx.redis_stream_broker._metrics") as metrics:
            broker.add_batch("s", [(b"a", "k1"), (b"b", "k2")])

        metrics.inc_redis_publish_failure.assert_not_called()

    def test_a_pipeline_connection_failure_rebuilds_the_client_first(self):
        """The fallback is only worth taking if it reconnects, so the dropped
        connection is discarded before the individual writes are attempted —
        and each of those keeps the retry budget it always had."""
        broker = make_broker()
        pipe = self._pipeline(broker)
        pipe.execute.side_effect = redis.exceptions.ConnectionError("down")

        with patch.object(broker, "_add", return_value=b"9-0") as single:
            assert broker.add_batch("s", [(b"a", "k1"), (b"b", "k2")]) == [b"9-0", b"9-0"]

        assert broker._client is None, "the dropped connection was reused"
        assert single.call_count == 2

    def test_one_rejected_entry_is_retried_without_costing_the_others(self):
        """`raise_on_error=False` reports a per-command failure in the results
        list, so the entries that landed keep their ids."""
        broker = make_broker()
        pipe = self._pipeline(broker)
        pipe.execute.return_value = [
            b"1-0",
            redis.exceptions.ResponseError("WRONGTYPE"),
            b"1-2",
        ]
        broker._client.xadd.return_value = b"retried-0"

        ids = broker.add_batch("s", [(b"a", "k1"), (b"b", "k2"), (b"c", "k3")])

        assert ids == [b"1-0", b"retried-0", b"1-2"]
        broker._client.xadd.assert_called_once()

    def test_a_pipeline_failure_marks_the_connection_unhealthy(self):
        """Readiness has to see it: a sink that cannot publish has nowhere to
        put the verdicts it is handed."""
        broker = make_broker()
        broker.ping()
        assert broker.connection_healthy is True

        pipe = self._pipeline(broker)
        pipe.execute.side_effect = redis.exceptions.RedisError("boom")
        broker._client.xadd.side_effect = redis.exceptions.RedisError("boom")

        with patch.object(broker, "publish_retry_backoff", 0):
            broker.add_batch("s", [(b"a", "k1"), (b"b", "k2")])
        assert broker.connection_healthy is False


class TestClaimStale:
    """``XREADGROUP ... >`` only returns entries no one has seen.

    An entry delivered to a consumer that then died stays in that consumer's
    pending list forever — neither redelivered nor visible to a replacement —
    so without this pass a replica lost mid-batch strands its work with no
    upper bound.
    """

    def test_claims_pending_entries_and_returns_them_in_read_shape(self):
        broker = make_broker()
        broker._client.xautoclaim.return_value = (
            b"0-0",
            [(b"1-0", {PAYLOAD_FIELD: b"stranded"})],
        )
        entries = broker.claim_stale("s", "g", "c1", count=10)
        assert entries == [("s", b"1-0", {PAYLOAD_FIELD: b"stranded"})]

    def test_the_idle_threshold_is_how_long_a_stranded_entry_waits(self):
        """A minute, not five.

        Every read path acks an entry before returning it, so the pending window
        is one poll cycle and never contains a verification — the threshold the
        sweep clears is a crash window, and the only cost of overshooting it is
        how long a dead replica's work sits unclaimed.
        """
        broker = make_broker()
        broker._client.xautoclaim.return_value = (b"0-0", [])
        broker.claim_stale("s", "g", "c1", count=10)
        kwargs = broker._client.xautoclaim.call_args.kwargs
        assert kwargs["min_idle_time"] == DEFAULT_PENDING_MIN_IDLE_MS == 60_000
        assert kwargs["start_id"] == "0-0"

    def test_the_idle_threshold_is_configurable(self):
        broker = make_broker({"pending_min_idle_ms": 1000})
        broker._client.xautoclaim.return_value = (b"0-0", [])
        broker.claim_stale("s", "g", "c1", count=10)
        assert broker._client.xautoclaim.call_args.kwargs["min_idle_time"] == 1000

    @pytest.mark.parametrize(
        "legacy", ["reclaim_min_idle_ms", "reclaim_min_idle_time"],
    )
    def test_an_old_key_name_still_works_and_says_it_is_old(self, legacy, caplog):
        """A config written under either earlier name keeps working.

        The value was always milliseconds; the names differed only in whether
        they said so. So an old spelling has to mean exactly what it meant —
        renaming the setting must not become a thousand-fold change in reclaim
        behaviour for anyone who has not renamed it yet.
        """
        with caplog.at_level(logging.WARNING):
            broker = make_broker({legacy: 1000})
        assert broker.pending_min_idle_ms == 1000
        assert "pending_min_idle_ms" in caplog.text

    def test_the_current_key_wins_when_several_are_present(self):
        broker = make_broker({
            "pending_min_idle_ms": 1000,
            "reclaim_min_idle_ms": 5000,
            "reclaim_min_idle_time": 9000,
        })
        assert broker.pending_min_idle_ms == 1000

    def test_the_newer_of_two_old_names_wins(self):
        broker = make_broker(
            {"reclaim_min_idle_ms": 5000, "reclaim_min_idle_time": 9000}
        )
        assert broker.pending_min_idle_ms == 5000

    def test_a_per_call_threshold_overrides_the_configured_one(self):
        broker = make_broker({"pending_min_idle_ms": 1000})
        broker._client.xautoclaim.return_value = (b"0-0", [])
        broker.claim_stale("s", "g", "c1", count=10, min_idle_ms=50)
        assert broker._client.xautoclaim.call_args.kwargs["min_idle_time"] == 50

    def test_the_redis_7_three_element_response_is_handled(self):
        """7.0 appends a list of ids that no longer exist; 6.2 does not send
        it. Indexing rather than unpacking keeps one server version from being
        a TypeError on the consume path."""
        broker = make_broker()
        broker._client.xautoclaim.return_value = (
            b"0-0",
            [(b"1-0", {PAYLOAD_FIELD: b"x"})],
            [b"9-0"],
        )
        assert broker.claim_stale("s", "g", "c1", count=10) == [
            ("s", b"1-0", {PAYLOAD_FIELD: b"x"})
        ]

    def test_a_tombstoned_entry_is_returned_so_the_caller_can_ack_it(self):
        """Some versions report a deleted id as ``(id, None)``. It carries
        nothing to process but still has to leave the pending list."""
        broker = make_broker()
        broker._client.xautoclaim.return_value = (b"0-0", [(b"1-0", None)])
        assert broker.claim_stale("s", "g", "c1", count=10) == [("s", b"1-0", {})]

    def test_nothing_pending_returns_empty(self):
        broker = make_broker()
        broker._client.xautoclaim.return_value = (b"0-0", [])
        assert broker.claim_stale("s", "g", "c1", count=10) == []

    def test_a_server_without_xautoclaim_degrades_quietly(self):
        """Redis < 6.2 has no such command. That is a deployment fact, not a
        fault, so it must not be able to flap readiness on every poll."""
        broker = make_broker()
        broker.ping()
        broker._client.xautoclaim.side_effect = redis.exceptions.ResponseError(
            "unknown command 'XAUTOCLAIM'"
        )
        assert broker.claim_stale("s", "g", "c1", count=10) == []
        assert broker.connection_healthy is True

    def test_a_server_without_xautoclaim_is_not_asked_twice(self):
        """The sweep runs on every idle poll, so retrying a command the server
        does not have would spend a round trip and a log line per poll for the
        life of the process."""
        broker = make_broker()
        broker._client.xautoclaim.side_effect = redis.exceptions.ResponseError(
            "ERR unknown command 'XAUTOCLAIM'"
        )
        for _ in range(5):
            assert broker.claim_stale("s", "g", "c1", count=10) == []
        assert broker._client.xautoclaim.call_count == 1

    @pytest.mark.parametrize("error", [
        "NOPERM this user has no permissions to run the 'xautoclaim' command",
        "WRONGTYPE Operation against a key holding the wrong kind of value",
        "OOM command not allowed when used memory > 'maxmemory'",
    ])
    def test_a_refused_command_is_not_mistaken_for_an_old_server(self, error):
        """These say the command exists and was refused, which is an operator
        problem. Reporting them as benign left reclaim silently off with
        readiness green — the one outcome that hides an ACL mistake entirely.
        """
        broker = make_broker()
        broker.ping()
        broker._client.xautoclaim.side_effect = redis.exceptions.ResponseError(error)
        assert broker.claim_stale("s", "g", "c1", count=10) == []
        assert broker.connection_healthy is False

    def test_a_refused_command_is_retried_because_it_may_be_fixed(self):
        """Unlike a missing command, an ACL or memory problem can be corrected
        without restarting Alert MS, so the sweep must keep trying."""
        broker = make_broker()
        broker._client.xautoclaim.side_effect = redis.exceptions.ResponseError(
            "NOPERM no permissions"
        )
        broker.claim_stale("s", "g", "c1", count=10)
        broker.claim_stale("s", "g", "c1", count=10)
        assert broker._client.xautoclaim.call_count == 2

    def test_nogroup_clears_the_cache_so_the_group_is_recreated(self):
        broker = make_broker()
        broker.ensure_group("s", "g")
        broker._client.xautoclaim.side_effect = redis.exceptions.ResponseError("NOGROUP no such key")
        assert broker.claim_stale("s", "g", "c1", count=10) == []
        assert broker._ensured_groups == set()

    def test_a_connection_error_returns_empty_and_forces_reconnect(self):
        broker = make_broker()
        broker._client.xautoclaim.side_effect = redis.exceptions.ConnectionError("down")
        assert broker.claim_stale("s", "g", "c1", count=10) == []
        assert broker._client is None
        assert broker.connection_healthy is False

    def test_a_generic_redis_error_returns_empty(self):
        broker = make_broker()
        broker._client.xautoclaim.side_effect = redis.exceptions.RedisError("boom")
        assert broker.claim_stale("s", "g", "c1", count=10) == []

    def test_a_none_response_is_tolerated(self):
        broker = make_broker()
        broker._client.xautoclaim.return_value = None
        assert broker.claim_stale("s", "g", "c1", count=10) == []


class TestReadingAndRemovingConsumerRecords:
    """The primitives behind the sweep's housekeeping.

    A group keeps a record per consumer name that has ever read from it and
    expires none of them, so something has to remove the ones left by processes
    that are gone. Both commands are best-effort: this is hygiene, and a group
    that cannot be inspected is not a reason to report a working pipeline
    unready.
    """

    def test_records_are_decoded_into_plain_values(self):
        """The client is built with ``decode_responses=False`` for the payloads,
        so an XINFO reply arrives as bytes keys and values."""
        broker = make_broker()
        broker._client.xinfo_consumers.return_value = [
            {b"name": b"alert-bridge-host-1", b"pending": 3, b"idle": 120000},
        ]
        assert broker.list_consumers("s", "g") == [
            {"name": "alert-bridge-host-1", "pending": 3, "idle": 120000},
        ]

    def test_a_decoded_reply_is_read_the_same_way(self):
        broker = make_broker()
        broker._client.xinfo_consumers.return_value = [
            {"name": "c1", "pending": 0, "idle": 5},
        ]
        assert broker.list_consumers("s", "g") == [
            {"name": "c1", "pending": 0, "idle": 5},
        ]

    def test_a_missing_group_is_not_an_error_here(self):
        broker = make_broker()
        broker.ping()
        broker.ensure_group("s", "g")
        broker._client.xinfo_consumers.side_effect = redis.exceptions.ResponseError(
            "NOGROUP no such key"
        )
        assert broker.list_consumers("s", "g") == []
        assert broker._ensured_groups == set()
        assert broker.connection_healthy is True

    def test_a_refused_inspection_does_not_report_the_pipeline_unready(self):
        """An ACL without XINFO leaves consuming and acking working perfectly.

        Marking the connection unhealthy for a housekeeping command would take
        the whole pipeline out of service over a leak that costs nothing for
        weeks.
        """
        broker = make_broker()
        broker.ping()
        broker._client.xinfo_consumers.side_effect = redis.exceptions.ResponseError(
            "NOPERM no permissions for 'xinfo'"
        )
        assert broker.list_consumers("s", "g") == []
        assert broker.connection_healthy is True

    def test_a_refused_inspection_is_not_asked_again(self):
        broker = make_broker()
        broker._client.xinfo_consumers.side_effect = redis.exceptions.ResponseError(
            "NOPERM no permissions for 'xinfo'"
        )
        for _ in range(5):
            broker.list_consumers("s", "g")
        assert broker._client.xinfo_consumers.call_count == 1
        # And the removal half stops with it, rather than being attempted blind.
        assert broker.delete_consumer("s", "g", "c1") is None
        broker._client.xgroup_delconsumer.assert_not_called()

    def test_reclaim_survives_a_refused_inspection(self):
        """Separate flags: an ACL may allow XAUTOCLAIM and not XINFO, and losing
        the reclaim because the cleanup was refused would trade a slow leak for
        stranded entries."""
        broker = make_broker()
        broker._client.xinfo_consumers.side_effect = redis.exceptions.ResponseError(
            "NOPERM no permissions for 'xinfo'"
        )
        broker.list_consumers("s", "g")
        broker._client.xautoclaim.return_value = (b"0-0", [])
        assert broker._autoclaim_supported is True
        broker.claim_stale("s", "g", "c1", count=10)
        broker._client.xautoclaim.assert_called_once()

    def test_removal_reports_how_many_pending_entries_it_discarded(self):
        """DELCONSUMER deletes the consumer's pending entries with it, so the
        count is the caller's evidence that it did not."""
        broker = make_broker()
        broker._client.xgroup_delconsumer.return_value = 0
        assert broker.delete_consumer("s", "g", "c1") == 0
        broker._client.xgroup_delconsumer.assert_called_once_with("s", "g", "c1")

    def test_a_failed_removal_says_it_did_not_run(self):
        broker = make_broker()
        broker._client.xgroup_delconsumer.side_effect = redis.exceptions.RedisError("no")
        assert broker.delete_consumer("s", "g", "c1") is None

    def test_a_server_without_the_command_stops_being_asked(self):
        broker = make_broker()
        broker._client.xinfo_consumers.side_effect = redis.exceptions.ResponseError(
            "ERR unknown command 'XINFO'"
        )
        for _ in range(3):
            broker.list_consumers("s", "g")
        assert broker._client.xinfo_consumers.call_count == 1
