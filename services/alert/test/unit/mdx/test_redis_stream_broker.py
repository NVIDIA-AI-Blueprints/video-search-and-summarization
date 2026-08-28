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
    DEFAULT_RECLAIM_MIN_IDLE_MS,
    HEADERS_FIELD,
    KEY_FIELD,
    PAYLOAD_FIELD,
    PAYLOAD_FIELD_PRECEDENCE,
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

    def test_an_unreadable_password_file_falls_back_rather_than_crashing(self):
        """A missing mount must not take the process down before it can log
        which file it wanted."""
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

    def test_the_idle_threshold_keeps_a_slow_consumer_from_being_raced(self):
        broker = make_broker()
        broker._client.xautoclaim.return_value = (b"0-0", [])
        broker.claim_stale("s", "g", "c1", count=10)
        kwargs = broker._client.xautoclaim.call_args.kwargs
        assert kwargs["min_idle_time"] == DEFAULT_RECLAIM_MIN_IDLE_MS
        assert kwargs["start_id"] == "0-0"

    def test_the_idle_threshold_is_configurable(self):
        broker = make_broker({"reclaim_min_idle_time": 1000})
        broker._client.xautoclaim.return_value = (b"0-0", [])
        broker.claim_stale("s", "g", "c1", count=10)
        assert broker._client.xautoclaim.call_args.kwargs["min_idle_time"] == 1000

    def test_a_per_call_threshold_overrides_the_configured_one(self):
        broker = make_broker({"reclaim_min_idle_time": 1000})
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
