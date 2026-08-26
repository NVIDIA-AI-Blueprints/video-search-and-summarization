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
from unittest.mock import MagicMock, patch

import pytest
import redis

from mdx.redis_stream_broker import (
    DEFAULT_MAXLEN,
    DEFAULT_PUBLISH_RETRIES,
    HEADERS_FIELD,
    KEY_FIELD,
    PAYLOAD_FIELD,
    RedisStreamBroker,
    extract_envelope,
    message_id_to_epoch_ms,
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


class TestMessageIdToEpochMs:
    def test_extracts_the_millisecond_prefix(self):
        assert message_id_to_epoch_ms(b"1700000000000-0") == 1700000000000

    def test_accepts_str_ids(self):
        assert message_id_to_epoch_ms("1700000000000-5") == 1700000000000

    @pytest.mark.parametrize("value", [None, b"", b"not-an-id", b"0-0", "abc-1"])
    def test_unparseable_ids_return_none(self, value):
        assert message_id_to_epoch_ms(value) is None


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

    def test_trims_approximately_by_default(self):
        broker = make_broker()
        broker.add("s", b"body")
        assert broker._client.xadd.call_args.kwargs == {
            "maxlen": DEFAULT_MAXLEN,
            "approximate": True,
        }

    def test_maxlen_is_configurable(self):
        broker = make_broker({"maxlen": 50})
        broker.add("s", b"body")
        assert broker._client.xadd.call_args.kwargs["maxlen"] == 50

    @pytest.mark.parametrize("maxlen", [0, -1])
    def test_non_positive_maxlen_disables_trimming(self, maxlen):
        broker = make_broker({"maxlen": maxlen})
        broker.add("s", b"body")
        assert broker._client.xadd.call_args.kwargs == {}

    def test_unparseable_maxlen_falls_back_to_the_default(self):
        broker = make_broker({"maxlen": "not-a-number"})
        assert broker.maxlen == DEFAULT_MAXLEN

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
