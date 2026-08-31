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

#: Helm configs, which cannot be parsed as YAML here for the reason above but
#: are still checked textually for the event kinds they subscribe to.
HELM_CONFIGS = [
    "deploy/helm/services/alert/configs/config.yml",
    "deploy/helm/services/alert/configs/EDGE-LOCAL-VLM-config.yml",
]

#: Same pattern env-substitute.py uses.
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def mapping_block(text: str, key: str) -> dict:
    """Read the flat ``name: value`` mapping nested under ``key``.

    Indentation-based and deliberately not a YAML parse: the Helm configs are Go
    templates and will not load, but the blocks this reads contain no templating.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != f"{key}:":
            continue
        indent = len(line) - len(line.lstrip())
        entries = {}
        for candidate in lines[index + 1:]:
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            if len(candidate) - len(candidate.lstrip()) <= indent:
                break
            name, _, value = candidate.partition(":")
            entries[name.strip()] = value.strip().strip("'\"")
        return entries
    raise AssertionError(f"no '{key}:' block found")


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
        # Null renders to no trimming, which is also the default: Alert MS does
        # not delete entries from a stream it does not own.
        assert broker.maxlen is None
        assert broker.password is None
        assert broker.tls == {}


class TestSelectingRedisFromADeploymentConfig:
    """The same files must actually select Redis once an operator edits them.

    The Alerts compose passes no ``REDIS_*`` variables into the container, and
    this file is what it mounts — so the transports and the connection are
    stated here, the same arrangement the Helm chart uses for its own config.

    What the shipped file has to guarantee is that the edit is *only* the
    selections and the connection: every stream name, consumer group and
    per-kind route the Redis path needs must already be present, or an operator
    following the comments gets a service that refuses to start.
    """

    CONFIG = "deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/config.yml"

    #: Dotted paths into the config, as an operator would edit them by hand.
    EDITS = {
        "event_bridge.sourceType": "redisStream",
        "event_bridge.sinkType": "redisStream",
        "vlm_enhanced_sink.type": "redisStream",
        "redis.host": "redis",
        "redis.maxlen": 10000,
    }

    def rendered(self):
        path = REPO_ROOT / self.CONFIG
        if not path.exists():
            pytest.skip(f"{self.CONFIG} is not present in this checkout")
        # Other sections still hold ${VAR} placeholders — the VLM endpoint, for
        # one — so the file is rendered the way env-substitute.py renders it
        # before the Redis edits are applied on top.
        config = yaml.safe_load(render_with_unset_env(path))
        for dotted, value in self.EDITS.items():
            keys = dotted.split(".")
            node = config
            for key in keys[:-1]:
                node = node.setdefault(key, {})
            node[keys[-1]] = value
        return config

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

    def test_the_connection_is_read_from_the_file(self):
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


class TestSecureRedisFromADeploymentConfig(TestSelectingRedisFromADeploymentConfig):
    """The shipped files have to be able to reach an authenticated TLS endpoint
    without the password appearing in them.

    Inherits the cases above so the secure rendering is held to the same
    contract: a customer-managed endpoint must not be a different code path
    that only the happy configuration is tested against.
    """

    EDITS = dict(
        TestSelectingRedisFromADeploymentConfig.EDITS,
        **{
            "redis.username": "alertms",
            "redis.password_file": "/etc/alert-bridge/redis-auth/password",
            # No ssl_cert_reqs: verification is what enabling TLS means, and the
            # shipped files do not write the key. Its absence here is the case
            # under test — see test_tls_is_configured_with_verification_on.
            "redis.ssl": True,
            "redis.ssl_ca_certs": "/etc/alert-bridge/redis-ca/ca.crt",
            "redis.ssl_certfile": "/run/secrets/alert-redis/client.crt",
            "redis.ssl_keyfile": "/run/secrets/alert-redis/client.key",
        },
    )

    @pytest.fixture(autouse=True)
    def mounted_password_secret(self, tmp_path, monkeypatch):
        """Give the configured mount path a file that exists.

        The broker reads the named secret at construction and refuses to
        continue when a named source yields nothing, so a config that points at
        an absent mount cannot be constructed — which is the point, but it means
        these cases have to provide the mount the deployment would.
        """
        secret = tmp_path / "password"
        secret.write_text("from-a-secret\n")
        monkeypatch.setitem(self.EDITS, "redis.password_file", str(secret))

    def test_tls_is_configured_with_verification_on(self):
        """Turning TLS on is the whole edit: verification follows from it.

        The shipped files name no ``ssl_cert_reqs``, so this is what proves an
        operator cannot end up with an encrypted connection that checks nothing
        by simply not knowing the key exists.
        """
        assert "redis.ssl_cert_reqs" not in self.EDITS
        broker = RedisStreamBroker(resolve_redis_config(self.rendered()))
        assert broker.tls["ssl"] is True
        assert broker.tls["ssl_cert_reqs"] == "required"
        assert broker.tls["ssl_ca_certs"] == "/etc/alert-bridge/redis-ca/ca.crt"

    def test_the_client_certificate_pair_reaches_the_broker(self):
        """An instance with `tls-auth-clients yes` refuses a connection that
        presents none, so TLS alone could not reach one."""
        broker = RedisStreamBroker(resolve_redis_config(self.rendered()))
        assert broker.tls["ssl_certfile"] == "/run/secrets/alert-redis/client.crt"
        assert broker.tls["ssl_keyfile"] == "/run/secrets/alert-redis/client.key"

    def test_the_acl_username_reaches_the_broker(self):
        """Redis 6+ AUTH takes a username; an instance with ACL users rather
        than a single requirepass cannot authenticate without it."""
        assert RedisStreamBroker(resolve_redis_config(self.rendered())).username == "alertms"

    def test_the_password_is_read_from_the_named_file_not_the_config(self, tmp_path):
        secret = tmp_path / "password"
        secret.write_text("from-a-secret\n")
        config = self.rendered()
        config["redis"]["password_file"] = str(secret)
        assert RedisStreamBroker(resolve_redis_config(config)).password == "from-a-secret"

    def test_no_credential_is_written_into_the_rendered_config(self):
        """The rendered file is a ConfigMap in Helm and a bind mount in
        Compose. Neither is a secret, so the only thing that may appear here is
        the path to one."""
        redis_block = self.rendered()["redis"]
        assert not (redis_block.get("password") or "")
        assert redis_block["password_file"].startswith("/")


class TestEveryShippedProfileSubscribesToBothKinds:
    """A profile that lists only incidents drops every alert its deployment
    produces, silently and with no error anywhere.

    The Helm edge profile shipped that way: incidents on both transports and no
    alert stream at all, while the Docker edge profile it otherwise mirrors had
    both. Nothing caught it because the two files are only ever read by
    different tools.
    """

    @staticmethod
    def _text(relative: str) -> str:
        return (REPO_ROOT / relative).read_text(encoding="utf-8")

    @pytest.mark.parametrize("relative", DEPLOYMENT_CONFIGS + HELM_CONFIGS)
    @pytest.mark.parametrize(
        "block,key",
        [("kafka_source", "topics"), ("redis_source", "streams")],
    )
    def test_both_kinds_are_subscribed(self, relative, block, key):
        text = self._text(relative)
        if f"{block}:" not in text:
            pytest.skip(f"{relative} configures no {block}")
        kinds = mapping_block(text, key)
        assert "incident" in kinds, f"{relative}: {block}.{key} has no incident"
        assert "alert" in kinds, f"{relative}: {block}.{key} has no alert"

    @pytest.mark.parametrize("relative", DEPLOYMENT_CONFIGS + HELM_CONFIGS)
    def test_a_kind_does_not_change_name_between_transports(self, relative):
        """Both transports carry the same upstream events, so a profile that
        renames a kind on one of them is reading a different feed than it
        looks like it is."""
        text = self._text(relative)
        if "kafka_source:" not in text or "redis_source:" not in text:
            pytest.skip(f"{relative} does not configure both transports")
        assert mapping_block(text, "topics") == mapping_block(text, "streams")


class TestNoHelmConfigWritesTheRedisPasswordItself:
    """Both Helm configs render into a ConfigMap, which is not encrypted and is
    readable by anything that can read ConfigMaps in the namespace.

    The password therefore reaches the pod through a mounted Secret, and the
    inline key goes through ``vss-alert-bridge.redisPassword`` -- which renders
    empty when a Secret is configured and otherwise refuses a plaintext password
    unless the deployment opts in. Reading ``.Values.redis.password`` directly
    bypasses both halves of that.

    Checked textually, and on every Helm config rather than the main one,
    because this is exactly how the edge profile broke the last two times: the
    fix landed on ``configs/config.yml`` and the file whose own header says it is
    "kept identical" was not touched. A test that renders is not an option here
    -- these are Go templates and the suite has no ``helm`` binary.
    """

    @pytest.mark.parametrize("relative", HELM_CONFIGS)
    def test_the_password_key_goes_through_the_helper(self, relative):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        password_lines = [
            line for line in text.splitlines()
            if line.strip().startswith("password:")
        ]
        assert password_lines, f"{relative}: no redis password key at all"
        for line in password_lines:
            assert "vss-alert-bridge.redisPassword" in line, (
                f"{relative}: renders {line.strip()!r} into a ConfigMap. Use "
                f'include "vss-alert-bridge.redisPassword" so a configured '
                f"Secret wins and a plaintext password has to be opted into."
            )

    @pytest.mark.parametrize("relative", HELM_CONFIGS)
    def test_the_raw_value_is_not_read_anywhere_else(self, relative):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        offenders = [
            line for line in text.splitlines()
            if ".password" in line
            and "passwordSecret" not in line
            and "password_file" not in line
            and "vss-alert-bridge.redisPassword" not in line
            and not line.strip().startswith("#")
        ]
        assert not offenders, f"{relative}: reads the password directly: {offenders}"
