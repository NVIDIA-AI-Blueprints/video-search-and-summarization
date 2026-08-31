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

"""Unit tests for ``mdx.event_bridge_factory`` and the ``SinkBase`` contract.

Kafka is the default source and sink; Redis Streams is an optional alternative
for either, and a console sink exists for local debugging. The two roles are
resolved independently, so the mixed combinations are pinned here — a config
that selects Redis for ingest must not quietly drag the sink along with it.

Transport names are matched case- and separator-insensitively so the
``redisStream`` spelling used by vss-behavior-analytics configs works alongside
``redis_stream``. An unrecognised transport must still fail loudly at boot
rather than falling back to Kafka and reading the wrong topic.

``validate_configuration`` is deliberately asymmetric: Kafka may omit its
``kafka_source`` / ``kafka_sink`` block because a legacy top-level ``kafka``
block can supply the topics (warns only), but Redis Streams has no such
fallback, so a missing ``redis_source`` / ``redis_sink`` is rejected.
"""

from unittest.mock import MagicMock, patch

import pytest

from mdx.event_bridge_factory import EventBridgeFactory
from mdx.sink.sink_base import SinkBase


class TestCreateSource:
    def test_kafka_source_is_constructed_with_the_config(self):
        config = {"event_bridge": {"sourceType": "kafka"}}
        with patch("mdx.source.source_kafka.SourceKafka") as source_cls:
            result = EventBridgeFactory.create_source(config)

        source_cls.assert_called_once_with(config)
        assert result is source_cls.return_value

    def test_source_type_defaults_to_kafka(self):
        with patch("mdx.source.source_kafka.SourceKafka") as source_cls:
            EventBridgeFactory.create_source({})
        source_cls.assert_called_once_with({})

    def test_missing_event_bridge_section_defaults_to_kafka(self):
        with patch("mdx.source.source_kafka.SourceKafka") as source_cls:
            EventBridgeFactory.create_source({"kafka": {}})
        source_cls.assert_called_once()

    def test_redis_stream_source_is_constructed_with_the_config(self):
        config = {"event_bridge": {"sourceType": "redisStream"}}
        with patch("mdx.source.source_redis_stream.SourceRedisStream") as source_cls:
            result = EventBridgeFactory.create_source(config)

        source_cls.assert_called_once_with(config)
        assert result is source_cls.return_value

    @pytest.mark.parametrize("spelling", ["redisStream", "redisstream", "redis_stream", "redis-stream", "REDISSTREAM"])
    def test_redis_stream_spellings_all_resolve(self, spelling):
        """Config files and Helm values disagree on casing; none of them should
        silently fall through to Kafka."""
        with patch("mdx.source.source_redis_stream.SourceRedisStream") as source_cls:
            EventBridgeFactory.create_source({"event_bridge": {"sourceType": spelling}})
        source_cls.assert_called_once()

    def test_console_is_not_a_valid_source(self):
        """The console transport is output-only."""
        with pytest.raises(ValueError, match="Unsupported source type"):
            EventBridgeFactory.create_source({"event_bridge": {"sourceType": "console"}})

    @pytest.mark.parametrize("source_type", ["elasticsearch", "rabbitmq", None, 7])
    def test_unsupported_source_type_raises(self, source_type):
        with pytest.raises(ValueError, match="Unsupported source type"):
            EventBridgeFactory.create_source({"event_bridge": {"sourceType": source_type}})

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_source_type_falls_back_to_kafka(self, blank):
        """Deployment configs are rendered by substituting ``${VAR}``, and an
        unset variable becomes an empty string. A Kafka deployment upgraded
        before its environment gains the Redis variables must keep working."""
        with patch("mdx.source.source_kafka.SourceKafka") as source_cls:
            EventBridgeFactory.create_source({"event_bridge": {"sourceType": blank}})
        source_cls.assert_called_once()

    def test_constructor_failure_propagates(self):
        with patch("mdx.source.source_kafka.SourceKafka", side_effect=RuntimeError("no brokers")):
            with pytest.raises(RuntimeError, match="no brokers"):
                EventBridgeFactory.create_source({"event_bridge": {"sourceType": "kafka"}})

    def test_redis_constructor_failure_propagates(self):
        with patch(
            "mdx.source.source_redis_stream.SourceRedisStream",
            side_effect=RuntimeError("no redis"),
        ):
            with pytest.raises(RuntimeError, match="no redis"):
                EventBridgeFactory.create_source({"event_bridge": {"sourceType": "redisStream"}})


class TestCreateSink:
    def test_kafka_sink_is_constructed_with_the_config(self):
        config = {"event_bridge": {"sinkType": "kafka"}}
        with patch("mdx.sink.sink_kafka.KafkaSink") as sink_cls:
            result = EventBridgeFactory.create_sink(config)

        sink_cls.assert_called_once_with(config)
        assert result is sink_cls.return_value

    def test_sink_type_defaults_to_kafka(self):
        with patch("mdx.sink.sink_kafka.KafkaSink") as sink_cls:
            EventBridgeFactory.create_sink({})
        sink_cls.assert_called_once_with({})

    def test_redis_stream_sink_is_constructed_with_the_config(self):
        config = {"event_bridge": {"sinkType": "redisStream"}}
        with patch("mdx.sink.sink_redis_stream.SinkRedisStream") as sink_cls:
            result = EventBridgeFactory.create_sink(config)

        sink_cls.assert_called_once_with(config)
        assert result is sink_cls.return_value

    def test_console_sink_is_constructed_with_the_config(self):
        config = {"event_bridge": {"sinkType": "console"}}
        with patch("mdx.sink.sink_console.ConsoleSink") as sink_cls:
            result = EventBridgeFactory.create_sink(config)

        sink_cls.assert_called_once_with(config)
        assert result is sink_cls.return_value

    @pytest.mark.parametrize("sink_type", ["elasticsearch", "rabbitmq", None])
    def test_unsupported_sink_type_raises(self, sink_type):
        with pytest.raises(ValueError, match="Unsupported sink type"):
            EventBridgeFactory.create_sink({"event_bridge": {"sinkType": sink_type}})

    def test_a_blank_sink_type_falls_back_to_kafka(self):
        with patch("mdx.sink.sink_kafka.KafkaSink") as sink_cls:
            EventBridgeFactory.create_sink({"event_bridge": {"sinkType": ""}})
        sink_cls.assert_called_once()

    def test_constructor_failure_propagates(self):
        with patch("mdx.sink.sink_kafka.KafkaSink", side_effect=RuntimeError("no brokers")):
            with pytest.raises(RuntimeError, match="no brokers"):
                EventBridgeFactory.create_sink({"event_bridge": {"sinkType": "kafka"}})


class TestIndependentSourceAndSinkSelection:
    """Source and sink transports are chosen separately."""

    def test_redis_source_with_a_kafka_sink(self):
        config = {"event_bridge": {"sourceType": "redisStream", "sinkType": "kafka"}}
        with patch("mdx.source.source_redis_stream.SourceRedisStream") as source_cls, \
             patch("mdx.sink.sink_kafka.KafkaSink") as sink_cls:
            EventBridgeFactory.create_source(config)
            EventBridgeFactory.create_sink(config)
        source_cls.assert_called_once()
        sink_cls.assert_called_once()

    def test_kafka_source_with_a_redis_sink(self):
        config = {"event_bridge": {"sourceType": "kafka", "sinkType": "redisStream"}}
        with patch("mdx.source.source_kafka.SourceKafka") as source_cls, \
             patch("mdx.sink.sink_redis_stream.SinkRedisStream") as sink_cls:
            EventBridgeFactory.create_source(config)
            EventBridgeFactory.create_sink(config)
        source_cls.assert_called_once()
        sink_cls.assert_called_once()


class TestAvailableTypes:
    def test_kafka_and_redis_streams_are_advertised_as_sources(self):
        assert sorted(EventBridgeFactory.get_available_source_types()) == ["kafka", "redisStream"]

    def test_console_is_advertised_as_a_sink_but_not_a_source(self):
        assert sorted(EventBridgeFactory.get_available_sink_types()) == [
            "console", "kafka", "redisStream",
        ]

    def test_descriptions_are_present(self):
        assert all(EventBridgeFactory.get_available_source_types().values())
        assert all(EventBridgeFactory.get_available_sink_types().values())

    def test_the_advertised_types_are_a_copy(self):
        """Callers must not be able to mutate the factory's registry."""
        EventBridgeFactory.get_available_sink_types()["bogus"] = "x"
        assert "bogus" not in EventBridgeFactory.get_available_sink_types()


class TestValidateConfiguration:
    def test_full_kafka_config_is_valid(self):
        config = {
            "event_bridge": {
                "sourceType": "kafka",
                "sinkType": "kafka",
                "kafka_source": {"topics": {}},
                "kafka_sink": {"topics": {}},
            }
        }
        assert EventBridgeFactory.validate_configuration(config) is True

    def test_empty_config_is_valid_because_both_types_default_to_kafka(self):
        assert EventBridgeFactory.validate_configuration({}) is True

    def test_legacy_layout_without_kafka_sections_is_still_valid(self):
        """Only a warning is logged — the legacy top-level kafka block is used."""
        config = {"event_bridge": {"sourceType": "kafka", "sinkType": "kafka"}}
        assert EventBridgeFactory.validate_configuration(config) is True

    def test_unknown_source_type_is_rejected(self):
        config = {"event_bridge": {"sourceType": "rabbitmq", "sinkType": "kafka"}}
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_unknown_sink_type_is_rejected(self):
        config = {"event_bridge": {"sourceType": "kafka", "sinkType": "elasticsearch"}}
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_malformed_config_is_rejected_rather_than_raising(self):
        assert EventBridgeFactory.validate_configuration(None) is False

    def test_full_redis_stream_config_is_valid(self):
        config = {
            "redis": {"host": "my-redis"},
            "event_bridge": {
                "sourceType": "redisStream",
                "sinkType": "redisStream",
                "redis_source": {
                    "streams": {"incident": "mdx-incidents", "alert": "mdx-alerts"},
                    "consumer_group": "g",
                },
                "redis_sink": {"streams": {"incidents": "out"}},
            }
        }
        assert EventBridgeFactory.validate_configuration(config) is True

    def test_a_redis_transport_without_a_host_is_rejected(self):
        """A blank host used to mean localhost.

        The source tolerates an unreachable broker by design, so a deployment
        pointed at a customer's Redis that rendered no host polled a loopback
        address indefinitely with nothing raised. Helm refuses to render this;
        this is the same refusal for the Compose path, which has no such gate.
        """
        config = {
            "redis": {"host": ""},
            "event_bridge": {
                "sourceType": "redisStream",
                "sinkType": "kafka",
                "redis_source": {
                    "streams": {"incident": "mdx-incidents", "alert": "mdx-alerts"},
                    "consumer_group": "g",
                },
            },
        }
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_a_section_level_host_satisfies_the_endpoint_requirement(self):
        """The per-section overlay is a documented way to name the connection."""
        config = {
            "event_bridge": {
                "sourceType": "redisStream",
                "sinkType": "kafka",
                "redis_source": {
                    "host": "source-redis",
                    "streams": {"incident": "mdx-incidents", "alert": "mdx-alerts"},
                    "consumer_group": "g",
                },
            },
        }
        assert EventBridgeFactory.validate_configuration(config) is True

    def test_a_redis_section_missing_its_consumer_group_is_rejected(self):
        """Section presence was the only check, so a section holding nothing
        usable validated and then failed in the source constructor instead —
        reported against a transport class rather than the config file."""
        config = {
            "event_bridge": {
                "sourceType": "redisStream",
                "sinkType": "kafka",
                "redis_source": {
                    "streams": {"incident": "mdx-incidents", "alert": "mdx-alerts"},
                },
            }
        }
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_a_redis_section_with_no_streams_is_rejected(self):
        config = {
            "event_bridge": {
                "sourceType": "redisStream",
                "sinkType": "kafka",
                "redis_source": {"streams": {}, "consumer_group": "g"},
            }
        }
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_a_blank_stream_name_is_rejected(self):
        """What a rendered config produces for an unset variable."""
        config = {
            "event_bridge": {
                "sourceType": "redisStream",
                "sinkType": "kafka",
                "redis_source": {
                    "streams": {"incident": "mdx-incidents", "alert": ""},
                    "consumer_group": "g",
                },
            }
        }
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_a_source_map_naming_one_kind_is_rejected(self):
        """The constructor rejects it too, but this is where an operator meets
        it: reported against the config file rather than a transport class."""
        config = {
            "redis": {"host": "my-redis"},
            "event_bridge": {
                "sourceType": "redisStream",
                "sinkType": "kafka",
                "redis_source": {
                    "streams": {"incident": "mdx-incidents"},
                    "consumer_group": "g",
                },
            }
        }
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_the_sink_map_is_not_held_to_the_source_s_kind_coverage(self):
        """Its keys are output route names, not event kinds, so asking whether
        both kinds are present would reject every valid sink config."""
        config = {
            "redis": {"host": "my-redis"},
            "event_bridge": {
                "sourceType": "kafka",
                "sinkType": "redisStream",
                "redis_sink": {"streams": {"incidents": "out"}},
            }
        }
        assert EventBridgeFactory.validate_configuration(config) is True

    def test_two_keys_sharing_one_stream_are_rejected(self):
        config = {
            "event_bridge": {
                "sourceType": "kafka",
                "sinkType": "redisStream",
                "redis_sink": {"streams": {"incidents": "s", "enhanced_anomaly": "s"}},
            }
        }
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_redis_source_without_its_section_is_rejected(self):
        """Unlike Kafka there is no legacy block to fall back to, so booting
        would fail later with a less obvious error."""
        config = {"event_bridge": {"sourceType": "redisStream", "sinkType": "kafka"}}
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_redis_sink_without_its_section_is_rejected(self):
        config = {
            "event_bridge": {
                "sourceType": "kafka",
                "sinkType": "redisStream",
                "kafka_source": {"topics": {}},
            }
        }
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_an_empty_redis_section_is_rejected(self):
        config = {
            "event_bridge": {
                "sourceType": "redisStream",
                "sinkType": "kafka",
                "redis_source": {},
            }
        }
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_console_sink_needs_no_configuration_section(self):
        config = {"event_bridge": {"sourceType": "kafka", "sinkType": "console"}}
        assert EventBridgeFactory.validate_configuration(config) is True

    def test_blank_transports_validate_as_kafka(self):
        config = {"event_bridge": {"sourceType": "", "sinkType": ""}}
        assert EventBridgeFactory.validate_configuration(config) is True


class TestWhatIsJudgedBeforeAnythingStarts:
    """Two Redis mistakes used to be caught only where the client is built.

    That is inside a forked pipeline child, which starts after the API child,
    the metrics port and the topic-metadata wait — so a mistyped port or a
    misspelt route key crash-looped children instead of failing the container,
    and the operator got a traceback about a class rather than the key to fix.

    Both are pure predicates over config, so they belong with the rest of what
    ``validate_configuration`` answers before anything is started.
    """

    @staticmethod
    def _redis_source(**redis):
        return {
            "redis": {"host": "my-redis", **redis},
            "event_bridge": {
                "sourceType": "redisStream",
                "sinkType": "kafka",
                "redis_source": {
                    "streams": {"incident": "mdx-incidents", "alert": "mdx-alerts"},
                    "consumer_group": "g",
                },
            },
        }

    @staticmethod
    def _redis_terminal_sink(incident_route, alert_route, **sink_root):
        return {
            "redis": {"host": "my-redis"},
            "event_bridge": {"sourceType": "kafka", "sinkType": "kafka"},
            "vlm_enhanced_sink": {
                "type": "redisStream",
                "incident": {"redisStream": incident_route},
                "alert": {"redisStream": alert_route},
                **sink_root,
            },
        }

    @pytest.mark.parametrize("port", [0, -1, 65536, 70000, "not-a-port"])
    def test_a_port_that_is_not_a_port_is_rejected_here(self, port):
        assert EventBridgeFactory.validate_configuration(
            self._redis_source(port=port)
        ) is False

    @pytest.mark.parametrize("port", [None, "", 6380, "6380"])
    def test_a_usable_or_absent_port_passes(self, port):
        assert EventBridgeFactory.validate_configuration(
            self._redis_source(port=port)
        ) is True

    def test_a_terminal_route_with_no_stream_is_rejected_here(self):
        config = self._redis_terminal_sink(
            {"stream": "mdx-vlm-incidents"}, {"message_type": "alert"},
        )
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_both_kinds_on_one_stream_is_rejected_here(self):
        config = self._redis_terminal_sink(
            {"stream": "one"}, {"stream": "one"},
        )
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_a_terminal_sink_with_no_host_is_rejected_here(self):
        config = self._redis_terminal_sink(
            {"stream": "in"}, {"stream": "out"},
        )
        config["redis"]["host"] = ""
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_the_sink_s_own_connection_block_can_supply_the_host(self):
        """It overrides the top-level block for this sink, so it has to be read
        the same way here or a valid split-instance config would be refused."""
        config = self._redis_terminal_sink(
            {"stream": "in"}, {"stream": "out"},
            redisStream={"host": "results-redis"},
        )
        config["redis"]["host"] = ""
        assert EventBridgeFactory.validate_configuration(config) is True

    def test_a_complete_terminal_sink_passes(self):
        config = self._redis_terminal_sink(
            {"stream": "in"}, {"stream": "out"},
        )
        assert EventBridgeFactory.validate_configuration(config) is True

    @pytest.mark.parametrize("sink_type", [None, "elastic", "kafka"])
    def test_the_other_terminal_sinks_are_not_route_checked(self, sink_type):
        """Only redisStream has streams to name. Elasticsearch is the default and
        Kafka reuses the broker config validated with the event bridge, so
        neither should acquire a new way to fail."""
        config = {
            "event_bridge": {"sourceType": "kafka", "sinkType": "kafka"},
            "vlm_enhanced_sink": {"type": sink_type},
        }
        assert EventBridgeFactory.validate_configuration(config) is True

    @pytest.mark.parametrize("sink_type", ["mongo", "elasticsearc", "rabbitmq"])
    def test_a_terminal_sink_nobody_implements_is_rejected_here(self, sink_type):
        """"Not Redis" was as far as this looked, and everything unrecognized
        answered that: the event bridge's alias table has no Elasticsearch entry,
        so ``None`` meant both "not Redis" and "no idea", and only the first was
        acted on. The typo then failed in the forked child."""
        config = {
            "event_bridge": {"sourceType": "kafka", "sinkType": "kafka"},
            "vlm_enhanced_sink": {"type": sink_type},
        }
        assert EventBridgeFactory.validate_configuration(config) is False

    @pytest.mark.parametrize("typo", ["incident", "incidentss", "enhanced-anomaly"])
    def test_a_stream_key_the_sink_does_not_read_is_rejected_here(self, typo):
        """A reader asks for the keys it knows, so a misspelt one is
        indistinguishable from an absent one -- and absent means "do not publish
        that kind". The route silently disappeared while the sink reported
        healthy and logged one line per dropped message."""
        config = {
            "redis": {"host": "my-redis"},
            "event_bridge": {
                "sourceType": "kafka",
                "sinkType": "redisStream",
                "redis_sink": {"streams": {"enhanced_anomaly": "a", typo: "b"}},
            },
        }
        assert EventBridgeFactory.validate_configuration(config) is False

    @pytest.mark.parametrize("keys", [
        ("enhanced_anomaly", "incidents"),
        ("enhanced_anomaly_stream", "incidents_stream"),
        ("incidents",),
    ])
    def test_the_spellings_the_sink_does_read_are_accepted(self, keys):
        """Including the legacy ``<key>_stream`` suffix, and including a config
        that publishes one kind -- absent is a choice for this section."""
        config = {
            "redis": {"host": "my-redis"},
            "event_bridge": {
                "sourceType": "kafka",
                "sinkType": "redisStream",
                "redis_sink": {
                    "streams": {key: f"stream-{i}" for i, key in enumerate(keys)},
                },
            },
        }
        assert EventBridgeFactory.validate_configuration(config) is True

    def test_a_source_key_that_names_no_kind_is_rejected_here(self):
        config = self._redis_source()
        config["event_bridge"]["redis_source"]["streams"]["bogus"] = "b"
        assert EventBridgeFactory.validate_configuration(config) is False

    @pytest.mark.parametrize("key", ["heartbeat", "heartbeat_stream", "anomaly"])
    def test_the_source_keys_that_are_not_kinds_are_still_accepted(self, key):
        """The heartbeat stream is not an event kind, and ``anomaly`` is the
        legacy spelling of ``alert``. Neither is advertised; both work."""
        config = self._redis_source()
        config["event_bridge"]["redis_source"]["streams"][key] = "extra"
        assert EventBridgeFactory.validate_configuration(config) is True


class TestTheWholeConnectionIsJudgedHere:
    """Not just the address. Everything that decides where a Redis component
    connects, or whether it will be let in, is a pure predicate over config --
    and each one that was left to the client crash-looped a forked child on a
    traceback instead of failing the container with the key to fix.
    """

    @staticmethod
    def _redis(**redis):
        return {
            "redis": {"host": "my-redis", **redis},
            "event_bridge": {
                "sourceType": "redisStream",
                "sinkType": "kafka",
                "redis_source": {
                    "streams": {"incident": "i", "alert": "a"},
                    "consumer_group": "g",
                },
            },
        }

    @pytest.mark.parametrize("db", ["one", "3.5", -1])
    def test_a_database_that_is_not_one_is_rejected_here(self, db):
        """``db: "one"`` coerced to 0 connects to a database that exists,
        accepts every command and consumes an empty stream in the wrong place --
        which reads as "the producer published nothing"."""
        assert EventBridgeFactory.validate_configuration(self._redis(db=db)) is False

    @pytest.mark.parametrize("db", [None, "", 0, 3, "3"])
    def test_a_usable_or_absent_database_passes(self, db):
        assert EventBridgeFactory.validate_configuration(self._redis(db=db)) is True

    def test_a_password_file_that_is_not_there_is_rejected_here(self, tmp_path):
        """Asking for a Secret and connecting without one turns a missing mount
        into a NOAUTH on the first command, several layers from the mount that
        caused it -- and in a forked child, so as a crash-loop."""
        config = self._redis(password_file=str(tmp_path / "never-mounted"))
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_a_password_file_that_is_there_passes(self, tmp_path):
        secret = tmp_path / "redis-password"
        secret.write_text("s3cr3t\n")
        config = self._redis(password_file=str(secret))
        assert EventBridgeFactory.validate_configuration(config) is True

    def test_an_unset_password_env_is_rejected_here(self, monkeypatch):
        monkeypatch.delenv("REDIS_PASSWORD_THAT_IS_NOT_SET", raising=False)
        config = self._redis(password_env="REDIS_PASSWORD_THAT_IS_NOT_SET")
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_an_instance_with_no_password_at_all_still_passes(self):
        """The ordinary local case: no `requirepass`, nothing named."""
        assert EventBridgeFactory.validate_configuration(self._redis()) is True


class TestSinkBaseContract:
    def test_cannot_be_instantiated_directly(self):
        with pytest.raises(TypeError):
            SinkBase({})

    def test_every_abstract_method_must_be_implemented(self):
        class Incomplete(SinkBase):
            def write(self, messages):
                pass

        with pytest.raises(TypeError):
            Incomplete({})

    def test_concrete_subclass_keeps_the_config(self):
        sink = self._make_sink({"kafka": {}})
        assert sink.config == {"kafka": {}}

    def test_write_data_delegates_to_write(self):
        sink = self._make_sink({})
        sink.write = MagicMock()

        sink.write_data(["a", "b"])

        sink.write.assert_called_once_with(["a", "b"])

    @staticmethod
    def _make_sink(config):
        class ConcreteSink(SinkBase):
            def write(self, messages):
                pass

            def write_msg(self, messages):
                pass

            def write_incidents(self, messages):
                pass

            def close(self):
                pass

        return ConcreteSink(config)


class TestSelectionIsLegibleInTheLog:
    """The log has to show the resolved transport, not just the raw string.

    Transport names are matched case- and separator-insensitively, so the
    configured value and the implementation actually chosen can differ. Logging
    only the configured string hides that step: a value like ``Kafka`` prints
    back exactly as written, which reads as confirmation even when a consumer
    of the same value elsewhere fails to match it.
    """

    def test_the_source_log_carries_both_spellings(self, caplog):
        config = {"event_bridge": {"sourceType": "REDIS_STREAM"}, "kafka": {}}
        with caplog.at_level("INFO"):
            with patch("mdx.source.source_redis_stream.SourceRedisStream"):
                EventBridgeFactory.create_source(config)
        assert "'REDIS_STREAM'" in caplog.text
        assert "'redisStream'" in caplog.text

    def test_the_sink_log_carries_both_spellings(self, caplog):
        config = {"event_bridge": {"sinkType": "Console"}}
        with caplog.at_level("INFO"):
            with patch("mdx.sink.sink_console.ConsoleSink"):
                EventBridgeFactory.create_sink(config)
        assert "'Console'" in caplog.text
        assert "'console'" in caplog.text


class TestSplitTransportsAreCalledOut:
    """Two sinks are constructed in every deployment: the event bridge sink,
    which carries validation-error responses, and ``vlm_enhanced_sink``, which
    carries verified results. They are selected independently and that is
    deliberate. What is not deliberate is arriving there by accident: because
    ``sinkType`` defaults to kafka, an operator who sets only
    ``vlm_enhanced_sink.type: redisStream`` gets a Kafka error sink they never
    asked for, and in a deployment with no Kafka their validation errors go
    nowhere at all.
    """

    @staticmethod
    def _create(config, caplog):
        with caplog.at_level("WARNING"):
            with patch("mdx.sink.sink_console.ConsoleSink"), \
                 patch("mdx.sink.sink_kafka.KafkaSink"), \
                 patch("mdx.sink.sink_redis_stream.SinkRedisStream"):
                EventBridgeFactory.create_sink(config)
        return caplog.text

    def test_the_default_kafka_error_sink_against_a_redis_result_sink_warns(self, caplog):
        text = self._create({"vlm_enhanced_sink": {"type": "redisStream"}}, caplog)
        assert "different" in text and "transports" in text

    def test_the_warning_names_both_transports(self, caplog):
        text = self._create({"vlm_enhanced_sink": {"type": "redisStream"}}, caplog)
        assert "kafka" in text and "redisStream" in text

    def test_matching_transports_are_silent(self, caplog):
        text = self._create(
            {
                "event_bridge": {"sinkType": "redisStream"},
                "vlm_enhanced_sink": {"type": "redisStream"},
            },
            caplog,
        )
        assert "different" not in text

    def test_alias_spellings_still_count_as_matching(self, caplog):
        """``redis`` is an accepted alias of ``redisStream``; treating them as
        different would make the warning noise an operator learns to ignore."""
        text = self._create(
            {
                "event_bridge": {"sinkType": "REDIS_STREAM"},
                "vlm_enhanced_sink": {"type": "redis"},
            },
            caplog,
        )
        assert "different" not in text

    def test_an_elastic_result_sink_is_not_a_split_worth_warning_about(self, caplog):
        """Elasticsearch is the default and is not an event-bridge transport at
        all, so the pairing is the shipped configuration rather than a mistake."""
        text = self._create({"vlm_enhanced_sink": {"type": "elastic"}}, caplog)
        assert "different" not in text

    def test_no_vlm_sink_configured_is_silent(self, caplog):
        assert "different" not in self._create({}, caplog)

    def test_a_nonsense_vlm_sink_type_does_not_warn_here(self, caplog):
        """Its own factory rejects it with a better message than this could."""
        text = self._create({"vlm_enhanced_sink": {"type": "carrier-pigeon"}}, caplog)
        assert "different" not in text
