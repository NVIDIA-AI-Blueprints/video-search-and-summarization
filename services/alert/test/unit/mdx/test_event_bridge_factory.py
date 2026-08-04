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

Kafka is the only supported transport: the ``redisStream`` / ``elasticsearch``
source implementations still exist in the tree but the factory refuses to
construct them. Pinning that refusal matters — a config carrying the legacy
``sourceType: redisStream`` must fail loudly at boot rather than silently
falling back to Kafka and reading the wrong topic.

``validate_configuration`` is deliberately more permissive than
``create_source``: it accepts a config with no ``kafka_source`` block (legacy
layout, warns only) but rejects an unknown transport.
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

    @pytest.mark.parametrize("source_type", ["redisStream", "elasticsearch", "rabbitmq", ""])
    def test_unsupported_source_type_raises(self, source_type):
        with pytest.raises(ValueError, match="Unsupported source type"):
            EventBridgeFactory.create_source({"event_bridge": {"sourceType": source_type}})

    def test_constructor_failure_propagates(self):
        with patch("mdx.source.source_kafka.SourceKafka", side_effect=RuntimeError("no brokers")):
            with pytest.raises(RuntimeError, match="no brokers"):
                EventBridgeFactory.create_source({"event_bridge": {"sourceType": "kafka"}})


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

    @pytest.mark.parametrize("sink_type", ["redisStream", "elasticsearch", ""])
    def test_unsupported_sink_type_raises(self, sink_type):
        with pytest.raises(ValueError, match="Unsupported sink type"):
            EventBridgeFactory.create_sink({"event_bridge": {"sinkType": sink_type}})

    def test_constructor_failure_propagates(self):
        with patch("mdx.sink.sink_kafka.KafkaSink", side_effect=RuntimeError("no brokers")):
            with pytest.raises(RuntimeError, match="no brokers"):
                EventBridgeFactory.create_sink({"event_bridge": {"sinkType": "kafka"}})


class TestAvailableTypes:
    def test_only_kafka_is_advertised_as_a_source(self):
        assert list(EventBridgeFactory.get_available_source_types()) == ["kafka"]

    def test_only_kafka_is_advertised_as_a_sink(self):
        assert list(EventBridgeFactory.get_available_sink_types()) == ["kafka"]

    def test_descriptions_are_present(self):
        assert EventBridgeFactory.get_available_source_types()["kafka"]
        assert EventBridgeFactory.get_available_sink_types()["kafka"]


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
        config = {"event_bridge": {"sourceType": "redisStream", "sinkType": "kafka"}}
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_unknown_sink_type_is_rejected(self):
        config = {"event_bridge": {"sourceType": "kafka", "sinkType": "elasticsearch"}}
        assert EventBridgeFactory.validate_configuration(config) is False

    def test_malformed_config_is_rejected_rather_than_raising(self):
        assert EventBridgeFactory.validate_configuration(None) is False


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
