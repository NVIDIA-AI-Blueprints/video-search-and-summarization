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

"""Unit tests for the Redis Streams and console VLM-enhanced sinks.

This is the sink that carries VLM-verified results — ``event_bridge.sinkType``
only carries validation errors — so Redis Streams support here is what makes a
Redis-only deployment actually publish verdicts.

Two contracts matter most:

* **Payload parity with the Kafka sink.** Results are the same ``nv.Incident``
  and ``nv.Behavior`` protobuf messages, wrapped in the MDX envelope, so the
  Logstash ``redis_stream`` input decodes ``mdx-vlm-incidents`` and
  ``mdx-vlm-alerts`` identically in Redis and Kafka mode. Publishing JSON here
  by default would break that consumer silently.
* **Alert and incident routing.** The two kinds go to separate streams with
  different protobuf types; crossing them produces a decode failure in the
  consumer, not in Alert MS.

``output_category`` remapping is re-asserted because it is applied at publish
time from the live config store, and every sink has to do it independently.
"""

import json
import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from mdx.sink.vlm_enhanced_sink.factory import build_vlm_enhanced_sink
from mdx.sink.vlm_enhanced_sink.sink_console import VLMEnhancedConsoleSink
from mdx.sink.vlm_enhanced_sink.sink_redis_stream import (
    DEFAULT_ALERT_STREAM,
    DEFAULT_INCIDENT_STREAM,
    PROBE_INTERVAL_SECONDS,
    VLMEnhancedRedisStreamSink,
)

REDIS_CONFIG = {
    "redis": {"host": "redis", "port": 6379},
    "vlm_enhanced_sink": {
        "type": "redisStream",
        "incident": {"redisStream": {"stream": "mdx-vlm-incidents", "message_type": "incident"}},
        "alert": {"redisStream": {"stream": "mdx-vlm-alerts", "message_type": "alert"}},
    },
}

INCIDENT = {"id": "inc-1", "sensorId": "cam-1", "category": "Loitering"}
ALERT = {"id": "alt-1", "notification_type": "alert", "sensor": {"id": "cam-2"}}


#: What the shipped configs name each route, and what ``make_redis_sink`` fills
#: in for a caller that does not name one itself.
SHIPPED_STREAMS = {"incident": "mdx-vlm-incidents", "alert": "mdx-vlm-alerts"}


def with_required_keys(config):
    """``config`` plus the keys the sink requires but the caller is not testing.

    A connection, and a stream on each route. Every caller here passes only the
    fragment it cares about, and the sink refuses to infer either of these: no
    host would mean connecting to localhost, and no stream would mean publishing
    verdicts to a name the deployment never gave. Filling them in keeps each
    case about its own subject; a case about one of these requirements builds
    its config itself rather than going through here.
    """
    config = dict(config or REDIS_CONFIG)
    config.setdefault("redis", REDIS_CONFIG["redis"])

    sink_root = dict(config.get("vlm_enhanced_sink") or {})
    for kind, stream in SHIPPED_STREAMS.items():
        kind_block = dict(sink_root.get(kind) or {})
        route = dict(kind_block.get("redisStream") or {})
        route.setdefault("stream", stream)
        kind_block["redisStream"] = route
        sink_root[kind] = kind_block
    config["vlm_enhanced_sink"] = sink_root
    return config


def make_redis_sink(config=None, **kwargs):
    """Build the sink from ``config``, supplying what it does not name."""
    config = with_required_keys(config)
    with patch("mdx.sink.vlm_enhanced_sink.sink_redis_stream.RedisStreamBroker") as broker_cls:
        broker_cls.return_value.add.return_value = b"1-0"
        return VLMEnhancedRedisStreamSink.from_config(config, **kwargs)


@pytest.fixture
def protobuf():
    """Patch the protobuf converters; their own tests cover the conversion."""
    module = "mdx.sink.vlm_enhanced_sink.sink_redis_stream"
    with patch(f"{module}.convert_incident_to_protobuf_incident") as incident, \
         patch(f"{module}.convert_behavior_to_protobuf_behavior") as behavior:
        incident.return_value.SerializeToString.return_value = b"incident-proto"
        behavior.return_value.SerializeToString.return_value = b"behavior-proto"
        yield incident, behavior


class TestRedisStreamRouting:
    def test_incidents_are_published_as_incident_protobuf(self, protobuf):
        sink = make_redis_sink()
        sink.publish_success(dict(INCIDENT), "prompt", None, {"verdict": "confirmed"})

        stream, payload = sink._broker.add.call_args.args
        assert stream == "mdx-vlm-incidents"
        assert payload == b"incident-proto"

    def test_alerts_are_published_as_behavior_protobuf(self, protobuf):
        sink = make_redis_sink()
        sink.publish_success(dict(ALERT), "prompt", None, {"verdict": "confirmed"})

        stream, payload = sink._broker.add.call_args.args
        assert stream == "mdx-vlm-alerts"
        assert payload == b"behavior-proto"

    def test_protobuf_is_the_default_payload_format(self, protobuf):
        """Logstash decodes these streams as protobuf; JSON would strand them."""
        sink = make_redis_sink()
        assert sink._incident_route["payload_format"] == "protobuf"
        assert sink._alert_route["payload_format"] == "protobuf"

    @pytest.mark.parametrize("kind", ["incident", "alert"])
    def test_a_route_with_no_stream_is_rejected(self, kind):
        """There is no default for where a verdict goes.

        Inferring one produced a deployment that published, reported healthy and
        was read by nobody — the failure mode a default is supposed to prevent,
        arriving instead as silence.
        """
        config = {
            "redis": REDIS_CONFIG["redis"],
            "vlm_enhanced_sink": {
                "type": "redisStream",
                # The other kind is named, so the message must be about this one.
                **{k: {"redisStream": {"stream": s}}
                   for k, s in SHIPPED_STREAMS.items() if k != kind},
            },
        }
        with patch("mdx.sink.vlm_enhanced_sink.sink_redis_stream.RedisStreamBroker"):
            with pytest.raises(ValueError, match=f"{kind}.redisStream.stream is not set"):
                VLMEnhancedRedisStreamSink.from_config(config)

    @pytest.mark.parametrize("kind", ["incident", "alert"])
    def test_the_rejection_names_what_the_shipped_configs_use(self, kind):
        """The name is still worth telling an operator — as the likely intent,
        not as something applied on their behalf."""
        config = {
            "redis": REDIS_CONFIG["redis"],
            "vlm_enhanced_sink": {"type": "redisStream"},
        }
        with patch("mdx.sink.vlm_enhanced_sink.sink_redis_stream.RedisStreamBroker"):
            with pytest.raises(ValueError) as raised:
                VLMEnhancedRedisStreamSink.from_config(config)
        assert SHIPPED_STREAMS["incident"] in str(raised.value)

    def test_the_shipped_stream_names_match_the_kafka_topics(self):
        """Both transports carry the same verdicts, so a consumer switching
        between them reads the same names."""
        assert DEFAULT_INCIDENT_STREAM == SHIPPED_STREAMS["incident"]
        assert DEFAULT_ALERT_STREAM == SHIPPED_STREAMS["alert"]

    def test_errors_are_published_too(self, protobuf):
        sink = make_redis_sink()
        sink.publish_error(dict(INCIDENT), "prompt", None, {"error": "vlm timeout"})
        sink._broker.add.assert_called_once()

    def test_json_payload_format_is_opt_in(self):
        config = {
            "vlm_enhanced_sink": {
                "type": "redisStream",
                "incident": {"redisStream": {"stream": "s", "payload_format": "json"}},
            }
        }
        sink = make_redis_sink(config)
        sink.publish_success(dict(INCIDENT), "prompt", None, {})

        payload = sink._broker.add.call_args.args[1]
        assert json.loads(payload)["id"] == "inc-1"

    def test_payload_format_can_be_set_once_for_both_kinds(self):
        config = {
            "vlm_enhanced_sink": {
                "type": "redisStream",
                "redisStream": {"payload_format": "json"},
            }
        }
        sink = make_redis_sink(config)
        assert sink._incident_route["payload_format"] == "json"
        assert sink._alert_route["payload_format"] == "json"

    def test_an_unknown_message_type_is_rejected_at_construction(self):
        """It used to be caught at publish time, which meant a verdict had
        already been produced and paid for before anyone found out."""
        config = {
            "vlm_enhanced_sink": {
                "type": "redisStream",
                "incident": {"redisStream": {"stream": "s", "message_type": "bogus"}},
            }
        }
        with pytest.raises(ValueError, match="message_type"):
            make_redis_sink(config)

    def test_a_message_type_that_belongs_to_the_other_route_is_rejected(self):
        """The dangerous case, and the one that used to succeed: 'incident' on
        the alert route serializes a Behavior with the Incident schema, which
        publishes cleanly and is undecodable downstream."""
        config = {
            "vlm_enhanced_sink": {
                "type": "redisStream",
                "alert": {"redisStream": {"stream": "s", "message_type": "incident"}},
            }
        }
        with pytest.raises(ValueError, match="on the alert route"):
            make_redis_sink(config)

    def test_both_kinds_on_one_stream_is_rejected(self):
        config = {
            "vlm_enhanced_sink": {
                "type": "redisStream",
                "incident": {"redisStream": {"stream": "shared"}},
                "alert": {"redisStream": {"stream": "shared"}},
            }
        }
        with pytest.raises(ValueError, match="cannot carry both"):
            make_redis_sink(config)

    def test_a_blank_stream_does_not_fall_back_to_the_default(self):
        """A blank value is an unresolved variable; substituting the default
        publishes verdicts to a stream the deployment never asked for."""
        config = {
            "vlm_enhanced_sink": {
                "type": "redisStream",
                "incident": {"redisStream": {"stream": ""}},
            }
        }
        with pytest.raises(ValueError, match="stream is empty") as raised:
            make_redis_sink(config)
        # Removing the key is explicitly not the way out, and the message says
        # so: this sink has no default for where a verdict goes.
        assert "does not guess" in str(raised.value)

    def test_an_unsupported_payload_format_is_rejected(self):
        config = {
            "vlm_enhanced_sink": {
                "type": "redisStream",
                "redisStream": {"payload_format": "avro"},
            }
        }
        with pytest.raises(ValueError, match="payload_format"):
            make_redis_sink(config)


class TestRedisStreamKeys:
    def test_incidents_are_keyed_by_top_level_sensor_id(self, protobuf):
        """Keying by sensor keeps a cohort together, mirroring the Kafka
        partition-key contract dedup relies on."""
        sink = make_redis_sink()
        sink.publish_success(dict(INCIDENT), "prompt", None, {})
        assert sink._broker.add.call_args.kwargs["key"] == "cam-1"

    def test_alerts_are_keyed_by_the_nested_sensor_id(self, protobuf):
        sink = make_redis_sink()
        sink.publish_success(dict(ALERT), "prompt", None, {})
        assert sink._broker.add.call_args.kwargs["key"] == "cam-2"

    def test_an_explicit_key_field_wins(self, protobuf):
        config = {
            "vlm_enhanced_sink": {
                "type": "redisStream",
                "incident": {"redisStream": {"stream": "s", "key_field": "id"}},
            }
        }
        sink = make_redis_sink(config)
        sink.publish_success(dict(INCIDENT), "prompt", None, {})
        assert sink._broker.add.call_args.kwargs["key"] == "inc-1"

    def test_a_missing_key_field_falls_back_to_the_sensor_id(self, protobuf):
        config = {
            "vlm_enhanced_sink": {
                "type": "redisStream",
                "incident": {"redisStream": {"stream": "s", "key_field": "absent.path"}},
            }
        }
        sink = make_redis_sink(config)
        sink.publish_success(dict(INCIDENT), "prompt", None, {})
        assert sink._broker.add.call_args.kwargs["key"] == "cam-1"

    def test_a_document_without_any_sensor_id_still_publishes(self, protobuf):
        sink = make_redis_sink()
        sink.publish_success({"id": "inc-9"}, "prompt", None, {})
        assert sink._broker.add.call_args.kwargs["key"] == "inc-9"


class TestRedisStreamCategoryMapping:
    """The remap is applied to the copy that gets published, never to the
    caller's dict — the dedup fingerprint was already computed from the
    original category upstream."""

    @staticmethod
    def published_category(protobuf):
        incident_converter, _ = protobuf
        return incident_converter.call_args.args[0]["category"]

    def test_the_configured_output_category_is_applied(self, protobuf):
        sink = make_redis_sink(category_mapping={"Loitering": "Suspicious Activity"})
        sink.publish_success(dict(INCIDENT), "prompt", None, {})
        assert self.published_category(protobuf) == "Suspicious Activity"

    def test_the_live_store_overrides_the_file_mapping(self, protobuf):
        """PUT API edits must take effect without a restart."""
        store = MagicMock()
        store.get.return_value = {"output_category": "From Store"}
        sink = make_redis_sink(
            category_mapping={"Loitering": "From File"}, alert_config_store=store
        )
        sink.publish_success(dict(INCIDENT), "prompt", None, {})
        assert self.published_category(protobuf) == "From Store"

    def test_an_unmapped_category_is_left_alone(self, protobuf):
        sink = make_redis_sink(category_mapping={"Other": "X"})
        sink.publish_success(dict(INCIDENT), "prompt", None, {})
        assert self.published_category(protobuf) == "Loitering"

    def test_the_callers_document_is_not_mutated(self, protobuf):
        sink = make_redis_sink(category_mapping={"Loitering": "Suspicious Activity"})
        document = dict(INCIDENT)
        sink.publish_success(document, "prompt", None, {})
        assert document["category"] == "Loitering"


class TestRedisStreamFailureHandling:
    def test_a_failed_write_does_not_raise(self, protobuf):
        """A broker outage must not take down the VLM worker thread."""
        sink = make_redis_sink()
        sink._broker.add.return_value = None
        sink.publish_success(dict(INCIDENT), "prompt", None, {})

    def test_a_dropped_verdict_is_logged_as_an_error(self, protobuf, caplog):
        """Redis is the only destination here, so a swallowed write is lost data.
        It has to leave something behind that an operator can alert on."""
        sink = make_redis_sink()
        sink._broker.add.return_value = None
        with caplog.at_level(logging.ERROR):
            sink.publish_success(dict(INCIDENT), "prompt", None, {})
        assert any(
            record.levelno >= logging.ERROR and "Dropped" in record.getMessage()
            for record in caplog.records
        ), caplog.text

    def test_a_serialization_failure_does_not_raise(self, protobuf):
        incident_converter, _ = protobuf
        incident_converter.side_effect = RuntimeError("bad document")
        sink = make_redis_sink()
        sink.publish_success(dict(INCIDENT), "prompt", None, {})

    def test_connection_settings_come_from_the_shared_redis_block(self):
        with patch(
            "mdx.sink.vlm_enhanced_sink.sink_redis_stream.RedisStreamBroker"
        ) as broker_cls:
            VLMEnhancedRedisStreamSink.from_config(REDIS_CONFIG)
        assert broker_cls.call_args.args[0]["host"] == "redis"

    def test_the_sink_block_can_override_the_shared_connection(self):
        config = with_required_keys({
            "redis": {"host": "redis"},
            "vlm_enhanced_sink": {"type": "redisStream", "redisStream": {"host": "other"}},
        })
        with patch(
            "mdx.sink.vlm_enhanced_sink.sink_redis_stream.RedisStreamBroker"
        ) as broker_cls:
            VLMEnhancedRedisStreamSink.from_config(config)
        assert broker_cls.call_args.args[0]["host"] == "other"


class TestRedisStreamStartupCheck:
    """Per-write retries are bounded and cannot ride out a wrong host or a
    Redis that is not running. Without a check at construction the service
    starts cleanly and then discards every verdict it produces until Redis
    appears — a deployment that looks healthy and delivers nothing.
    """

    def test_construction_pings_redis(self):
        with patch(
            "mdx.sink.vlm_enhanced_sink.sink_redis_stream.RedisStreamBroker"
        ) as broker_cls:
            VLMEnhancedRedisStreamSink.from_config(REDIS_CONFIG)
        broker_cls.return_value.ping.assert_called_once()

    def test_an_unreachable_redis_fails_the_start(self):
        with patch(
            "mdx.sink.vlm_enhanced_sink.sink_redis_stream.RedisStreamBroker"
        ) as broker_cls:
            broker_cls.return_value.ping.return_value = False
            broker_cls.return_value.host = "nope"
            broker_cls.return_value.port = 6379
            with pytest.raises(ConnectionError, match="nope:6379"):
                VLMEnhancedRedisStreamSink.from_config(REDIS_CONFIG)

    def test_the_error_names_the_setting_that_would_change_it(self):
        with patch(
            "mdx.sink.vlm_enhanced_sink.sink_redis_stream.RedisStreamBroker"
        ) as broker_cls:
            broker_cls.return_value.ping.return_value = False
            with pytest.raises(ConnectionError, match="vlm_enhanced_sink.type"):
                VLMEnhancedRedisStreamSink.from_config(REDIS_CONFIG)


class TestRedisStreamDropAccounting:
    """A log line is not alertable. Redis is the only destination for this
    sink, so an exhausted write is a verdict the VLM already paid to produce
    and which no longer exists anywhere — that needs a counter and it needs to
    move readiness, not just leave a message for whoever reads the logs.
    """

    def test_an_exhausted_write_is_counted_as_a_lost_verdict(self, protobuf):
        sink = make_redis_sink()
        sink._broker.add.return_value = None
        with patch("metrics.recorder.inc_terminal_publish_dropped") as dropped:
            sink.publish_success(dict(INCIDENT), "prompt", None, {})
        dropped.assert_called_once_with("redis_stream", "incident")

    def test_a_dropped_alert_is_counted_under_its_own_kind(self, protobuf):
        sink = make_redis_sink()
        sink._broker.add.return_value = None
        with patch("metrics.recorder.inc_terminal_publish_dropped") as dropped:
            sink.publish_success(dict(ALERT), "prompt", None, {})
        dropped.assert_called_once_with("redis_stream", "alert")

    def test_a_serialization_failure_is_counted_too(self, protobuf):
        """The verdict is just as gone as when the write failed."""
        incident_converter, _ = protobuf
        incident_converter.side_effect = RuntimeError("bad document")
        sink = make_redis_sink()
        with patch("metrics.recorder.inc_terminal_publish_dropped") as dropped:
            sink.publish_success(dict(INCIDENT), "prompt", None, {})
        dropped.assert_called_once_with("redis_stream", "incident")

    def test_a_successful_write_counts_nothing(self, protobuf):
        sink = make_redis_sink()
        with patch("metrics.recorder.inc_terminal_publish_dropped") as dropped:
            sink.publish_success(dict(INCIDENT), "prompt", None, {})
        dropped.assert_not_called()

    def test_a_failing_metrics_backend_cannot_break_the_publish_path(self, protobuf):
        sink = make_redis_sink()
        sink._broker.add.return_value = None
        with patch(
            "metrics.recorder.inc_terminal_publish_dropped",
            side_effect=RuntimeError("registry gone"),
        ):
            sink.publish_success(dict(INCIDENT), "prompt", None, {})


class TestTerminalSinkHealth:
    """Readiness has to see a terminal sink that cannot deliver. The source is
    still consuming happily in that state, so nothing else in the pipeline
    would report it.
    """

    def test_a_reachable_sink_is_healthy(self):
        sink = make_redis_sink()
        sink._broker.connection_healthy = True
        assert sink.is_healthy() is True

    def test_an_unreachable_sink_is_unhealthy(self):
        sink = make_redis_sink()
        sink._broker.connection_healthy = False
        sink._broker.ping.return_value = False
        assert sink.is_healthy() is False

    def test_a_reachable_sink_does_not_pay_for_a_ping(self):
        """The healthy answer is on the readiness timer, once a second."""
        sink = make_redis_sink()
        sink._broker.connection_healthy = True
        # The constructor's own ping is not what is under test here.
        sink._broker.ping.reset_mock()
        sink.is_healthy()
        sink._broker.ping.assert_not_called()

    def test_an_unhealthy_sink_recovers_without_a_publish(self):
        """A sink is passive: the only commands it issues are publishes.

        So the flag it reads only cleared on the next verdict, and a deployment
        with no traffic stayed unready for as long as it had nothing to say --
        long after Redis came back. Restarting the container was the only way
        out of a state the container was in no way responsible for.
        """
        sink = make_redis_sink()
        sink._broker.connection_healthy = False
        sink._broker.ping.reset_mock()
        sink._broker.ping.return_value = True
        assert sink.is_healthy() is True
        sink._broker.ping.assert_called_once_with()

    def test_nothing_published_yet_counts_as_healthy(self):
        """The constructor already pinged, and an idle deployment must not look
        broken just because it has had nothing to publish."""
        sink = make_redis_sink()
        sink._broker.connection_healthy = None
        assert sink.is_healthy() is True

    def test_the_recovery_probe_is_rate_limited(self):
        """Readiness is not its only caller. Every consumer-group assignment and
        revocation reads it on the rebalance callback, where a ping against a
        host that is not answering costs a socket timeout inside the window the
        group allows a member -- so a rebalance storm could evict the member it
        was checking on.
        """
        sink = make_redis_sink()
        sink._broker.connection_healthy = False
        sink._broker.ping.reset_mock()
        sink._broker.ping.return_value = False
        assert [sink.is_healthy() for _ in range(20)] == [False] * 20
        sink._broker.ping.assert_called_once_with()

    def test_the_next_interval_probes_again(self):
        """Rate-limited, not cached: a broker that came back has to be noticed
        without waiting for a publish that may never come."""
        sink = make_redis_sink()
        sink._broker.connection_healthy = False
        sink._broker.ping.reset_mock()
        sink._broker.ping.return_value = False
        sink.is_healthy()
        sink._last_probe_at -= PROBE_INTERVAL_SECONDS + 1
        sink._broker.ping.return_value = True
        assert sink.is_healthy() is True
        assert sink._broker.ping.call_count == 2

    def test_the_transport_label_identifies_the_sink_in_metrics(self):
        assert make_redis_sink().transport_label == "redis_stream"

    def test_sinks_without_a_remote_dependency_are_always_healthy(self):
        """The default on the base class, so adding a sink does not silently
        make the pipeline unready."""
        assert VLMEnhancedConsoleSink().is_healthy() is True


class TestConsoleSink:
    def test_a_verdict_is_rendered_to_the_log(self, caplog):
        sink = VLMEnhancedConsoleSink()
        with caplog.at_level("INFO"):
            sink.publish_success(dict(INCIDENT), "prompt", None, {"verdict": "confirmed"})
        assert "inc-1" in caplog.text

    def test_incidents_and_alerts_are_labelled(self, caplog):
        sink = VLMEnhancedConsoleSink()
        with caplog.at_level("INFO"):
            sink.publish_success(dict(ALERT), "prompt", None, {})
        assert "alert" in caplog.text

    def test_errors_are_rendered_too(self, caplog):
        sink = VLMEnhancedConsoleSink()
        with caplog.at_level("INFO"):
            sink.publish_error(dict(INCIDENT), "prompt", None, {"error": "timeout"})
        assert "error" in caplog.text

    def test_output_is_truncated_when_configured(self, caplog):
        sink = VLMEnhancedConsoleSink(max_chars=10)
        with caplog.at_level("INFO"):
            sink.publish_success(dict(INCIDENT), "prompt", None, {})
        assert "truncated" in caplog.text

    def test_an_unserialisable_document_does_not_raise(self, caplog):
        sink = VLMEnhancedConsoleSink()
        with caplog.at_level("INFO"):
            sink.publish_success({"id": "x", "bad": object()}, "prompt", None, {})

    def test_the_output_category_mapping_still_applies(self, caplog):
        """Console output should show what a real sink would publish."""
        sink = VLMEnhancedConsoleSink(category_mapping={"Loitering": "Suspicious Activity"})
        with caplog.at_level("INFO"):
            sink.publish_success(dict(INCIDENT), "prompt", None, {})
        assert "Suspicious Activity" in caplog.text

    def test_config_options_are_read(self):
        config = {"vlm_enhanced_sink": {"type": "console", "console": {"pretty": False, "max_chars": 5}}}
        sink = VLMEnhancedConsoleSink.from_config(config)
        assert sink._pretty is False
        assert sink._max_chars == 5


class TestFactorySelection:
    def test_redis_stream_is_selectable(self):
        with patch("mdx.sink.vlm_enhanced_sink.sink_redis_stream.RedisStreamBroker"):
            sink = build_vlm_enhanced_sink(REDIS_CONFIG)
        assert isinstance(sink, VLMEnhancedRedisStreamSink)

    @pytest.mark.parametrize("spelling", ["redisStream", "redis_stream", "redisstream"])
    def test_redis_stream_spellings_all_resolve(self, spelling):
        config = with_required_keys({
            "redis": REDIS_CONFIG["redis"],
            "vlm_enhanced_sink": {"type": spelling},
        })
        with patch("mdx.sink.vlm_enhanced_sink.sink_redis_stream.RedisStreamBroker"):
            assert isinstance(build_vlm_enhanced_sink(config), VLMEnhancedRedisStreamSink)

    def test_console_is_selectable(self):
        sink = build_vlm_enhanced_sink({"vlm_enhanced_sink": {"type": "console"}})
        assert isinstance(sink, VLMEnhancedConsoleSink)

    def test_elastic_remains_the_default(self):
        """No existing deployment sets a top-level type, so the default is what
        keeps them on Elasticsearch."""
        with patch(
            "mdx.sink.vlm_enhanced_sink.sink_elastic.VLMEnhancedElasticSink.from_config"
        ) as from_config:
            build_vlm_enhanced_sink({"vlm_enhanced_sink": {}})
        from_config.assert_called_once()

    def test_an_unknown_type_raises_and_lists_the_options(self):
        with pytest.raises(ValueError, match="redisStream"):
            build_vlm_enhanced_sink({"vlm_enhanced_sink": {"type": "rabbitmq"}})

    def test_the_error_quotes_what_the_operator_configured(self):
        """The resolved name is None on an unknown type, so echoing it back would
        tell the operator nothing about what to fix."""
        with pytest.raises(ValueError, match="rabbitmq"):
            build_vlm_enhanced_sink({"vlm_enhanced_sink": {"type": "rabbitmq"}})

    @pytest.mark.parametrize("value", [123, ["redisStream"], {"type": "kafka"}, True])
    def test_a_non_string_type_is_rejected_rather_than_crashing(self, value):
        """_normalize_sink_type() shares one contract with
        event_bridge_factory._normalize_transport(): anything unrecognized,
        including a non-string, resolves to None instead of raising
        AttributeError from inside the normalizer."""
        with pytest.raises(ValueError, match="Unsupported vlm_enhanced_sink.type"):
            build_vlm_enhanced_sink({"vlm_enhanced_sink": {"type": value}})


class TestRedisIsNotRequiredByDefault:
    """The ``redis`` package must stay optional at import time.

    An Elasticsearch or Kafka deployment that upgrades the code without
    re-running ``pip install -r requirements.txt`` would crash at startup if the
    factory imported the Redis sink eagerly, so the import is deliberately
    deferred into the ``redisStream`` branch. Re-adding a module-scope import
    would reintroduce that failure silently, which is what this pins.
    """

    def test_the_factory_module_does_not_import_redis(self):
        import mdx.sink.vlm_enhanced_sink.factory as factory

        assert not hasattr(factory, "VLMEnhancedRedisStreamSink")

    @pytest.mark.parametrize("sink_type", ["elastic", "kafka", "console"])
    def test_other_transports_build_without_the_redis_package(self, sink_type, monkeypatch):
        """Simulate a host with no ``redis`` installed and build each non-Redis
        sink; only the redisStream branch may need the dependency."""
        import builtins

        real_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "redis" or name.startswith("redis."):
                raise ModuleNotFoundError("No module named 'redis'")
            return real_import(name, *args, **kwargs)

        # Every module that already holds a reference to `redis` has to go too,
        # otherwise the re-import is served from sys.modules and `import redis`
        # is never reached, making the block a no-op.
        for module in [
            m for m in list(sys.modules)
            if m.startswith(("mdx.sink.vlm_enhanced_sink", "mdx.redis_stream_broker", "redis", "fakeredis"))
        ]:
            monkeypatch.delitem(sys.modules, module, raising=False)
        monkeypatch.setattr(builtins, "__import__", blocked_import)

        from mdx.sink.vlm_enhanced_sink.factory import (
            build_vlm_enhanced_sink as build,
        )

        target = {
            "elastic": "mdx.sink.vlm_enhanced_sink.sink_elastic.VLMEnhancedElasticSink.from_config",
            "kafka": "mdx.sink.vlm_enhanced_sink.sink_kafka.VLMEnhancedKafkaSink.from_config",
            "console": "mdx.sink.vlm_enhanced_sink.sink_console.VLMEnhancedConsoleSink.from_config",
        }[sink_type]

        with patch(target) as from_config:
            build({"vlm_enhanced_sink": {"type": sink_type}})
        from_config.assert_called_once()


class TestTransportSelectionIsLegible:
    """The factory has to say which transport it picked, and why.

    Every shipped config carries ``incident.type`` / ``alert.type`` keys that
    no code reads — the transport comes from the top-level
    ``vlm_enhanced_sink.type`` alone. That was harmless decoration while
    Elasticsearch was the only option, but the charts now render the top-level
    key from ``vlmSinkType`` while leaving the per-kind keys hardcoded to
    ``elastic``, so the config contradicts itself and reads as though incidents
    still go to Elasticsearch. Nothing can be raised over it, because those
    stale keys sit in working deployments; the warning is the whole defence.
    """

    def test_the_resolved_transport_is_logged_next_to_the_configured_one(self, caplog):
        with caplog.at_level("INFO"):
            with patch(
                "mdx.sink.vlm_enhanced_sink.sink_console.VLMEnhancedConsoleSink.from_config"
            ):
                build_vlm_enhanced_sink({"vlm_enhanced_sink": {"type": "CONSOLE"}})
        assert "'CONSOLE'" in caplog.text
        assert "resolved to 'console'" in caplog.text

    def test_a_contradicting_per_kind_type_is_called_out(self, caplog):
        with caplog.at_level("WARNING"):
            with patch(
                "mdx.sink.vlm_enhanced_sink.sink_console.VLMEnhancedConsoleSink.from_config"
            ):
                build_vlm_enhanced_sink({
                    "vlm_enhanced_sink": {
                        "type": "console",
                        "incident": {"type": "elastic"},
                    },
                })
        assert "vlm_enhanced_sink.incident.type" in caplog.text
        assert "never read" in caplog.text

    def test_a_redundant_per_kind_type_stays_quiet(self, caplog):
        """The default config agrees with the default transport; do not nag."""
        with caplog.at_level("WARNING"):
            with patch(
                "mdx.sink.vlm_enhanced_sink.sink_elastic.VLMEnhancedElasticSink.from_config"
            ):
                build_vlm_enhanced_sink({
                    "vlm_enhanced_sink": {
                        "incident": {"type": "elastic"},
                        "alert": {"type": "elastic"},
                    },
                })
        assert "never read" not in caplog.text

    def test_the_per_kind_key_does_not_change_the_selected_sink(self, caplog):
        """The warning is advisory: the top-level key still governs."""
        with patch(
            "mdx.sink.vlm_enhanced_sink.sink_console.VLMEnhancedConsoleSink.from_config"
        ) as console:
            build_vlm_enhanced_sink({
                "vlm_enhanced_sink": {
                    "type": "console",
                    "incident": {"type": "elastic"},
                    "alert": {"type": "elastic"},
                },
            })
        console.assert_called_once()
