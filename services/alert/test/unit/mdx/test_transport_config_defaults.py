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

"""Transport defaults asserted against the configuration files we actually ship.

The other transport tests build config dictionaries by hand, so they pin the
factory's behaviour but say nothing about what a deployment really loads. Redis
Streams is an optional addition and Kafka has to stay the default, and the way
that promise gets broken in practice is a config file — someone flips
``sourceType`` while adding a Redis example, or a ``${VAR}`` placeholder lands
in a spot that does not tolerate an unset variable. These tests read the real
files so that class of regression fails here.

Deployment configs are rendered by ``deploy/docker/services/alert/scripts/
env-substitute.py``, which replaces every ``${VAR}`` with the environment value
and — critically — with the empty string when the variable is unset. Existing
Kafka deployments upgrade the image without adding the new ``REDIS_*`` and
``ALERT_*_TYPE`` variables to their environment, so the "everything unset" case
below is exactly what those deployments boot with.
"""

import re
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from mdx.event_bridge_factory import EventBridgeFactory
from mdx.redis_stream_broker import (
    DEFAULT_MAXLEN,
    DEFAULT_PORT,
    RedisStreamBroker,
    resolve_redis_config,
)

SERVICE_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = Path(__file__).resolve().parents[5]

SHIPPED_CONFIG = SERVICE_ROOT / "config.yaml"

#: Every deployment config that renders an Alert MS config.yml through
#: env-substitute.py. Helm templates are excluded: they are Go templates, not
#: ``${VAR}`` substitution, so they cannot be parsed as YAML here.
DEPLOYMENT_CONFIGS = [
    "deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/config.yml",
    "deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/EDGE-LOCAL-VLM-config.yml",
    "deploy/docker/industry-profiles/smartcities/vlm-as-verifier/configs/config.yml",
    "deploy/docker/industry-profiles/warehouse-operations/vlm-as-verifier/configs/config.yml",
]

#: Same pattern env-substitute.py uses.
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_with_unset_env(path: Path) -> str:
    """Render a deployment config as env-substitute.py does with no variables set."""
    return PLACEHOLDER.sub("", path.read_text(encoding="utf-8"))


def load_shipped_config() -> dict:
    return yaml.safe_load(SHIPPED_CONFIG.read_text(encoding="utf-8"))


def build_transports(config: dict):
    """Resolve source and sink with the broker clients stubbed out.

    Returns ``(source_cls, sink_cls)`` mocks so the caller can assert which
    transport the config selected without needing a live broker.
    """
    with patch("mdx.source.source_kafka.SourceKafka") as kafka_source, \
         patch("mdx.sink.sink_kafka.KafkaSink") as kafka_sink, \
         patch("mdx.source.source_redis_stream.SourceRedisStream") as redis_source, \
         patch("mdx.sink.sink_redis_stream.SinkRedisStream") as redis_sink, \
         patch("mdx.sink.sink_console.ConsoleSink") as console_sink:
        EventBridgeFactory.create_source(config)
        EventBridgeFactory.create_sink(config)
    return {
        "kafka_source": kafka_source.called,
        "kafka_sink": kafka_sink.called,
        "redis_source": redis_source.called,
        "redis_sink": redis_sink.called,
        "console_sink": console_sink.called,
    }


def build_vlm_sink(config: dict) -> str:
    """Return the name of the VLM enhanced sink the config selects."""
    from mdx.sink.vlm_enhanced_sink.factory import build_vlm_enhanced_sink

    with patch(
        "mdx.sink.vlm_enhanced_sink.sink_elastic.VLMEnhancedElasticSink.from_config"
    ) as elastic, \
         patch(
        "mdx.sink.vlm_enhanced_sink.sink_kafka.VLMEnhancedKafkaSink.from_config"
    ) as kafka, \
         patch("mdx.sink.vlm_enhanced_sink.sink_redis_stream.RedisStreamBroker"):
        sink = build_vlm_enhanced_sink(config)
    if elastic.called:
        return "elastic"
    if kafka.called:
        return "kafka"
    return type(sink).__name__


class TestShippedServiceConfig:
    """``services/alert/config.yaml`` is the config a bare ``python
    enhance_alert_with_vlm.py`` run loads, and the template operators copy."""

    def test_kafka_is_the_source_and_the_sink(self):
        event_bridge = load_shipped_config()["event_bridge"]
        assert event_bridge["sourceType"] == "kafka"
        assert event_bridge["sinkType"] == "kafka"

    def test_no_redis_transport_sections_are_active(self):
        """The Redis examples are documentation. Uncommenting one by accident
        would repoint ingest at a broker that is not deployed."""
        config = load_shipped_config()
        assert "redis" not in config
        assert "redis_source" not in config["event_bridge"]
        assert "redis_sink" not in config["event_bridge"]

    def test_the_vlm_sink_has_no_transport_override(self):
        """An absent ``type`` is what keeps the VLM sink on Elasticsearch."""
        assert "type" not in load_shipped_config()["vlm_enhanced_sink"]

    def test_it_validates(self):
        assert EventBridgeFactory.validate_configuration(load_shipped_config()) is True

    def test_it_resolves_to_the_kafka_transports(self):
        built = build_transports(load_shipped_config())
        assert built["kafka_source"] and built["kafka_sink"]
        assert not built["redis_source"] and not built["redis_sink"]

    def test_it_resolves_to_the_elasticsearch_vlm_sink(self):
        assert build_vlm_sink(load_shipped_config()) == "elastic"


@pytest.mark.parametrize("relative_path", DEPLOYMENT_CONFIGS)
class TestDeploymentConfigsWithNoRedisEnvironment:
    """A Kafka deployment that upgrades the image without adding the new
    variables renders every new ``${VAR}`` to an empty string."""

    @staticmethod
    def rendered(relative_path):
        path = REPO_ROOT / relative_path
        if not path.exists():
            pytest.skip(f"{relative_path} is not present in this checkout")
        return yaml.safe_load(render_with_unset_env(path))

    def test_it_still_parses_as_yaml(self, relative_path):
        """An unset variable in a spot that needs a quoted scalar would make the
        whole file unloadable, and Alert MS would not boot at all."""
        assert isinstance(self.rendered(relative_path), dict)

    def test_the_transports_fall_back_to_kafka(self, relative_path):
        config = self.rendered(relative_path)
        assert EventBridgeFactory.validate_configuration(config) is True
        built = build_transports(config)
        assert built["kafka_source"] and built["kafka_sink"]
        assert not built["redis_source"] and not built["redis_sink"]
        assert not built["console_sink"]

    def test_the_vlm_sink_falls_back_to_elasticsearch(self, relative_path):
        assert build_vlm_sink(self.rendered(relative_path)) == "elastic"

    def test_the_null_redis_block_does_not_break_the_broker(self, relative_path):
        """``port: ${REDIS_PORT}`` renders to ``port:`` — YAML null. Nothing
        reads it in a Kafka deployment, but the coercion has to hold so that a
        half-configured environment fails on connect rather than on parse."""
        config = self.rendered(relative_path)
        if "redis" not in config:
            pytest.skip("config carries no redis block")
        broker = RedisStreamBroker(resolve_redis_config(config))
        assert broker.port == DEFAULT_PORT
        assert broker.db == 0
        assert broker.maxlen == DEFAULT_MAXLEN
        assert broker.password is None


class TestSelectingRedisFromADeploymentConfig:
    """The same files must actually select Redis once the variables are set."""

    CONFIG = "deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/config.yml"

    ENVIRONMENT = {
        "ALERT_EVENT_SOURCE_TYPE": "redisStream",
        "ALERT_EVENT_SINK_TYPE": "redisStream",
        "ALERT_VLM_SINK_TYPE": "redisStream",
        "ALERT_REDIS_CONSUMER_GROUP": "alert-bridge-vlm-group",
        "REDIS_HOST": "redis",
        "REDIS_PORT": "6379",
        "REDIS_DB": "0",
        "REDIS_STREAM_MAXLEN": "10000",
    }

    def rendered(self):
        path = REPO_ROOT / self.CONFIG
        if not path.exists():
            pytest.skip(f"{self.CONFIG} is not present in this checkout")
        text = PLACEHOLDER.sub(
            lambda m: self.ENVIRONMENT.get(m.group(1), ""), path.read_text(encoding="utf-8")
        )
        return yaml.safe_load(text)

    def test_the_redis_sections_are_complete_enough_to_validate(self):
        """``validate_configuration`` rejects a redisStream selection whose
        ``redis_source`` / ``redis_sink`` section is missing, so this proves the
        shipped file carries both."""
        assert EventBridgeFactory.validate_configuration(self.rendered()) is True

    def test_it_resolves_to_the_redis_transports(self):
        built = build_transports(self.rendered())
        assert built["redis_source"] and built["redis_sink"]
        assert not built["kafka_source"] and not built["kafka_sink"]

    def test_the_vlm_sink_resolves_to_redis_streams(self):
        assert build_vlm_sink(self.rendered()) == "VLMEnhancedRedisStreamSink"

    def test_the_connection_comes_from_the_environment(self):
        broker = RedisStreamBroker(resolve_redis_config(self.rendered()))
        assert (broker.host, broker.port, broker.maxlen) == ("redis", 6379, 10000)

    def test_source_and_sink_can_be_selected_independently(self):
        """A Redis source with a Kafka sink is a supported combination, and the
        shipped file has to allow it rather than coupling the two."""
        config = self.rendered()
        config["event_bridge"]["sinkType"] = "kafka"
        assert EventBridgeFactory.validate_configuration(config) is True
        built = build_transports(config)
        assert built["redis_source"] and built["kafka_sink"]
