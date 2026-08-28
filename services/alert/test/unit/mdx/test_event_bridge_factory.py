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
            "event_bridge": {
                "sourceType": "redisStream",
                "sinkType": "redisStream",
                "redis_source": {"streams": {"incident": "mdx-incidents"}},
                "redis_sink": {"streams": {"incidents": "out"}},
            }
        }
        assert EventBridgeFactory.validate_configuration(config) is True

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
