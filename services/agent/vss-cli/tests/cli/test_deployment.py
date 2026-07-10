# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for host-side Docker/Kubernetes deployment discovery."""

from __future__ import annotations

from argparse import Namespace
from io import StringIO
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING
from typing import Any

import pytest

import lib.cli.deployment as deployment
from lib.search_core import SearchRuntime
from lib.search_core.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Mapping

_CONFIG = """
functions:
  embed_search:
    cosmos_embed_endpoint: ${COSMOS_EMBED_ENDPOINT}
    es_endpoint: ${ELASTIC_SEARCH_ENDPOINT}
    es_index: ${ELASTIC_SEARCH_INDEX}
    vst_internal_url: ${VST_INTERNAL_URL}
    vst_external_url: ${VST_EXTERNAL_URL}
  attribute_search:
    rtvi_cv_endpoint: ${RTVI_CV_BASE_URL}
  search:
    behavior_es_endpoint: ${ELASTIC_SEARCH_ENDPOINT}
"""


def test_docker_uses_generated_env_and_rewrites_private_hosts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = tmp_path / "deploy/docker/developer-profiles/dev-profile-search"
    config = profile / "vss-agent/configs/config.yml"
    config.parent.mkdir(parents=True)
    config.write_text(_CONFIG)
    (profile / "generated.env").write_text(
        "\n".join(
            (
                "ELASTIC_SEARCH_ENDPOINT=http://elasticsearch:9200",
                "ELASTICSEARCH_HOST_PORT=19200",
                "COSMOS_EMBED_ENDPOINT=http://rtvi-embed:8000",
                "RTVI_EMBED_PORT=18017",
                "RTVI_CV_BASE_URL=http://vss-rtvi-cv:${RTVI_CV_PORT}",
                "RTVI_CV_PORT=19000",
                "VST_INTERNAL_URL=http://vst-ingress:${VST_PORT}",
                "VST_PORT=30888",
                "VST_EXTERNAL_URL=http://public.example",
                "VLM_BASE_URL=http://vss-vlm-nim:8000",
                "VLM_PORT=30082",
                "ELASTIC_SEARCH_INDEX=mdx-embed-filtered-2025-01-01",
                "VSS_AGENT_HOST_PORT=18000",
                "NVIDIA_API_KEY=must-not-leave-discovery",
                "OPENAI_API_KEY=must-not-leave-discovery-either",
            )
        )
    )
    monkeypatch.setattr(deployment, "_repo_root", lambda: tmp_path)

    discovered = deployment.discover_docker("dev-profile-search")

    assert discovered.config_path == config
    assert discovered.env["ELASTIC_SEARCH_ENDPOINT"] == "http://127.0.0.1:19200"
    assert discovered.env["COSMOS_EMBED_ENDPOINT"] == "http://127.0.0.1:18017"
    assert discovered.env["RTVI_CV_BASE_URL"] == "http://127.0.0.1:19000"
    assert discovered.env["VST_INTERNAL_URL"] == "http://127.0.0.1:30888"
    assert discovered.env["VST_EXTERNAL_URL"] == "http://public.example"
    assert discovered.env["VLM_BASE_URL"] == "http://127.0.0.1:30082"
    assert "NVIDIA_API_KEY" not in discovered.env
    assert "OPENAI_API_KEY" not in discovered.env
    assert deployment.discover_docker_host_endpoints("search") == {
        "agent_url": "http://127.0.0.1:18000",
        "vst_url": "http://127.0.0.1:30888",
        "es_url": "http://127.0.0.1:19200",
    }


def test_docker_retains_external_vss_prefixed_vlm_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = tmp_path / "deploy/docker/developer-profiles/dev-profile-search"
    config = profile / "vss-agent/configs/config.yml"
    config.parent.mkdir(parents=True)
    config.write_text(_CONFIG)
    (profile / "generated.env").write_text(
        "\n".join(
            (
                "ELASTIC_SEARCH_ENDPOINT=http://elasticsearch:9200",
                "COSMOS_EMBED_ENDPOINT=http://rtvi-embed:8000",
                "RTVI_EMBED_PORT=18017",
                "RTVI_CV_BASE_URL=http://vss-rtvi-cv:9000",
                "VST_INTERNAL_URL=http://vst-ingress:30888",
                "VST_EXTERNAL_URL=https://public.example",
                "VLM_BASE_URL=https://vss-models.example.com/v1",
            )
        )
    )
    monkeypatch.setattr(deployment, "_repo_root", lambda: tmp_path)

    discovered = deployment.discover_docker("search")

    assert discovered.env["VLM_BASE_URL"] == "https://vss-models.example.com/v1"


def _deployment(*, secret_vst: bool = False) -> dict[str, Any]:
    env: list[dict[str, Any]] = [
        {"name": "ELASTIC_SEARCH_ENDPOINT", "value": "http://elasticsearch:9200"},
        {"name": "COSMOS_EMBED_ENDPOINT", "valueFrom": {"configMapKeyRef": {"name": "runtime", "key": "embed"}}},
        {"name": "RTVI_CV_BASE_URL", "value": "http://vss-rtvi-cv:9000"},
        {"name": "VST_EXTERNAL_URL", "value": "https://public.example"},
        {"name": "ELASTIC_SEARCH_INDEX", "value": "mdx-embed-filtered-2025-01-01"},
    ]
    if secret_vst:
        env.append({"name": "VST_INTERNAL_URL", "valueFrom": {"secretKeyRef": {"name": "private", "key": "vst"}}})
    else:
        env.append({"name": "VST_INTERNAL_URL", "value": "http://vss-vios-ingress:30888"})
    return {
        "kind": "Deployment",
        "metadata": {"name": "search-vss-agent", "labels": {"app.kubernetes.io/name": "vss-agent"}},
        "spec": {
            "template": {
                "spec": {
                    "volumes": [{"name": "config", "configMap": {"name": "agent-config"}}],
                    "containers": [
                        {
                            "name": "vss-agent",
                            "volumeMounts": [{"name": "config", "mountPath": "/etc/vss-agent"}],
                            "env": env,
                        }
                    ],
                }
            }
        },
    }


def _kubectl_payloads(*, secret_vst: bool = False) -> dict[tuple[str, ...], dict[str, Any]]:
    agent = _deployment(secret_vst=secret_vst)
    return {
        ("get", "deployment", "-l", "app.kubernetes.io/instance=search", "-o", "json"): {"items": [agent]},
        ("get", "configmap", "agent-config", "-o", "json"): {"data": {"config.yml": _CONFIG}},
        ("get", "configmap", "runtime", "-o", "json"): {"data": {"embed": "http://vss-rtvi-embed:8000"}},
    }


def test_kubernetes_reads_only_nonsecret_deployment_values(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = _kubectl_payloads()

    def fake_json(_self: Any, *args: str) -> dict[str, Any]:
        return payloads[args]

    monkeypatch.setattr(deployment._Kubectl, "json", fake_json)
    discovered = deployment.discover_kubernetes(namespace="vss", release="search")
    try:
        assert discovered.env["COSMOS_EMBED_ENDPOINT"] == "http://vss-rtvi-embed:8000"
        assert discovered.env["VST_INTERNAL_URL"] == "http://vss-vios-ingress:30888"
        assert "NVIDIA_API_KEY" not in discovered.env
        assert discovered.config_path.read_text() == _CONFIG
    finally:
        discovered.close()


def test_kubernetes_selects_exact_agent_label_when_ui_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _deployment()
    ui = {
        "kind": "Deployment",
        "metadata": {
            "name": "vss-agent-ui",
            "labels": {"app.kubernetes.io/name": "vss-agent-ui"},
        },
        "spec": {"template": {"spec": {"containers": [{"name": "vss-agent-ui"}]}}},
    }

    def fake_json(_self: Any, *args: str) -> dict[str, Any]:
        assert args == ("get", "deployment", "-l", "app.kubernetes.io/instance=search", "-o", "json")
        return {"items": [ui, agent]}

    monkeypatch.setattr(deployment._Kubectl, "json", fake_json)

    selected = deployment._agent_deployment(deployment._Kubectl("vss", None), "search")

    assert selected is agent


def test_kubernetes_supports_unprefixed_agent_name_without_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _deployment()
    agent["metadata"] = {"name": "vss-agent"}
    ui = {
        "kind": "Deployment",
        "metadata": {"name": "vss-agent-ui"},
        "spec": {"template": {"spec": {"containers": [{"name": "vss-agent-ui"}]}}},
    }

    monkeypatch.setattr(deployment._Kubectl, "json", lambda _self, *_args: {"items": [ui]})
    monkeypatch.setattr(
        deployment._Kubectl,
        "json_optional",
        lambda _self, *args: agent if args[2] == "vss-agent" else None,
    )

    selected = deployment._agent_deployment(deployment._Kubectl("vss", None), "search")

    assert selected is agent


@pytest.mark.parametrize("name", ["search-vss-agent", "vss-agent"])
def test_kubernetes_rejects_named_fallback_owned_by_another_release(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    agent = _deployment()
    agent["metadata"] = {
        "name": name,
        "labels": {
            "app.kubernetes.io/name": "vss-agent",
            "app.kubernetes.io/instance": "another-release",
        },
    }

    monkeypatch.setattr(deployment._Kubectl, "json", lambda _self, *_args: {"items": []})
    monkeypatch.setattr(
        deployment._Kubectl,
        "json_optional",
        lambda _self, *args: agent if args[2] == name else None,
    )

    with pytest.raises(ConfigurationError, match="could not uniquely identify"):
        deployment._agent_deployment(deployment._Kubectl("vss", None), "search")


def test_kubernetes_rejects_required_secret_backed_runtime_values(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = _kubectl_payloads(secret_vst=True)

    def fake_json(_self: Any, *args: str) -> dict[str, Any]:
        return payloads[args]

    monkeypatch.setattr(deployment._Kubectl, "json", fake_json)

    with pytest.raises(ConfigurationError, match="Kubernetes Secrets"):
        deployment.discover_kubernetes(namespace="vss", release="search")


def test_kubernetes_explicit_value_replaces_secret_without_reading_it(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = _kubectl_payloads(secret_vst=True)

    def fake_json(_self: Any, *args: str) -> dict[str, Any]:
        return payloads[args]

    monkeypatch.setattr(deployment._Kubectl, "json", fake_json)
    discovered = deployment.discover_kubernetes(
        namespace="vss",
        release="search",
        env_overrides={"VST_INTERNAL_URL": "https://vst.example"},
    )
    try:
        assert discovered.env["VST_INTERNAL_URL"] == "https://vst.example"
    finally:
        discovered.close()


def test_kubernetes_explicit_index_replaces_secret_without_reading_it(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = _kubectl_payloads()
    config_data = payloads[("get", "configmap", "agent-config", "-o", "json")]["data"]
    config_data["config.yml"] = config_data["config.yml"].replace(
        "    rtvi_cv_endpoint: ${RTVI_CV_BASE_URL}",
        "    rtvi_cv_endpoint: ${RTVI_CV_BASE_URL}\n    behavior_index: ${BEHAVIOR_ES_INDEX}",
    )
    container = payloads[("get", "deployment", "-l", "app.kubernetes.io/instance=search", "-o", "json")]["items"][0][
        "spec"
    ]["template"]["spec"]["containers"][0]
    container["env"].append(
        {
            "name": "BEHAVIOR_ES_INDEX",
            "valueFrom": {"secretKeyRef": {"name": "private", "key": "behavior-index"}},
        }
    )

    def fake_json(_self: Any, *args: str) -> dict[str, Any]:
        return payloads[args]

    monkeypatch.setattr(deployment._Kubectl, "json", fake_json)
    discovered = deployment.discover_kubernetes(
        namespace="vss",
        release="search",
        env_overrides={"BEHAVIOR_ES_INDEX": "tenant-behavior"},
    )
    try:
        assert discovered.env["BEHAVIOR_ES_INDEX"] == "tenant-behavior"
    finally:
        discovered.close()


def test_kubernetes_nonsecret_duplicate_satisfies_secret_backed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = _kubectl_payloads(secret_vst=True)
    container = payloads[("get", "deployment", "-l", "app.kubernetes.io/instance=search", "-o", "json")]["items"][0][
        "spec"
    ]["template"]["spec"]["containers"][0]
    container["env"].append({"name": "VST_INTERNAL_URL", "value": "http://vss-vios-ingress:30888"})

    def fake_json(_self: Any, *args: str) -> dict[str, Any]:
        return payloads[args]

    monkeypatch.setattr(deployment._Kubectl, "json", fake_json)
    discovered = deployment.discover_kubernetes(namespace="vss", release="search")
    try:
        assert discovered.env["VST_INTERNAL_URL"] == "http://vss-vios-ingress:30888"
    finally:
        discovered.close()


def test_kubernetes_secret_env_overrides_stale_nonsecret_envfrom(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = _kubectl_payloads(secret_vst=True)
    container = payloads[("get", "deployment", "-l", "app.kubernetes.io/instance=search", "-o", "json")]["items"][0][
        "spec"
    ]["template"]["spec"]["containers"][0]
    container["envFrom"] = [{"configMapRef": {"name": "runtime-full"}}]
    payloads[("get", "configmap", "runtime-full", "-o", "json")] = {
        "data": {"VST_INTERNAL_URL": "http://stale-vst:30888"}
    }

    def fake_json(_self: Any, *args: str) -> dict[str, Any]:
        return payloads[args]

    monkeypatch.setattr(deployment._Kubectl, "json", fake_json)

    with pytest.raises(ConfigurationError, match="Kubernetes Secrets"):
        deployment.discover_kubernetes(namespace="vss", release="search")


def test_deployment_flags_require_their_scope() -> None:
    with pytest.raises(ConfigurationError, match="--profile"):
        deployment.discover_deployment(Namespace(deployment="docker", profile=None))
    with pytest.raises(ConfigurationError, match="--namespace and --release"):
        deployment.discover_deployment(
            Namespace(deployment="kubernetes", namespace=None, release=None, kube_context=None)
        )


def test_deployment_rejects_local_config_override() -> None:
    with pytest.raises(ConfigurationError, match="cannot be combined"):
        deployment.discover_deployment(Namespace(deployment="docker", profile="search", config="local.yml"))


def test_defaulted_interpolation_is_not_required() -> None:
    config = "enabled: ${ENABLE_CRITIC:-true}\naudio: ${ENABLE_AUDIO:-false}\nes: ${ELASTIC_SEARCH_ENDPOINT}\n"

    assert deployment._required_runtime_env_keys(config) == {"ELASTIC_SEARCH_ENDPOINT"}


@pytest.mark.parametrize(
    "values",
    (
        {"A": "${A}"},
        {"A": "${B}", "B": "${A}"},
    ),
)
def test_docker_env_expansion_rejects_stable_cycles(values: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError, match="circular variable reference"):
        deployment._expand_env_values(values)


def test_kubectl_service_lookup_preserves_command_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*_args: object, **_kwargs: object) -> object:
        return type("Result", (), {"returncode": 1, "stderr": "forbidden", "stdout": ""})()

    monkeypatch.setattr(deployment.subprocess, "run", denied)

    with pytest.raises(ConfigurationError, match="forbidden"):
        deployment._Kubectl("vss", None).service_exists("elasticsearch")


def test_kubectl_service_lookup_treats_not_found_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_found(*_args: object, **_kwargs: object) -> object:
        return type(
            "Result",
            (),
            {"returncode": 1, "stderr": 'Error from server (NotFound): namespaces "example" not found', "stdout": ""},
        )()

    monkeypatch.setattr(deployment.subprocess, "run", not_found)

    assert deployment._Kubectl("vss", None).service_exists("public", namespace="example") is False


def test_kubectl_service_lookup_does_not_mask_missing_context(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_context(*_args: object, **_kwargs: object) -> object:
        return type("Result", (), {"returncode": 1, "stderr": 'context "wrong" not found', "stdout": ""})()

    monkeypatch.setattr(deployment.subprocess, "run", missing_context)

    with pytest.raises(ConfigurationError, match="context"):
        deployment._Kubectl("vss", "wrong").service_exists("elasticsearch")


def test_kubectl_missing_binary_is_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("kubectl")

    monkeypatch.setattr(deployment.subprocess, "run", missing)

    with pytest.raises(ConfigurationError, match="could not execute kubectl"):
        deployment._Kubectl("vss", None).json("get", "deployment", "agent", "-o", "json")


def test_optional_missing_configmaps_are_ignored() -> None:
    class OptionalConfigMaps:
        def json_optional(self, *_args: str) -> None:
            return None

        def json(self, *_args: str) -> Mapping[str, Any]:
            pytest.fail("optional ConfigMaps must use --ignore-not-found")

    container = {
        "envFrom": [{"configMapRef": {"name": "optional-env", "optional": True}}],
        "env": [
            {
                "name": "ELASTIC_SEARCH_ENDPOINT",
                "valueFrom": {"configMapKeyRef": {"name": "optional-key", "key": "endpoint", "optional": True}},
            }
        ],
    }

    env, secret_backed = deployment._read_nonsecret_environment(  # type: ignore[arg-type]
        container,
        OptionalConfigMaps(),
        {},
    )

    assert env == {}
    assert secret_backed == set()


def test_cleanup_index_configmap_values_are_allowlisted_but_secrets_are_not() -> None:
    class RuntimeConfigMap:
        def json(self, *_args: str) -> Mapping[str, Any]:
            return {
                "data": {
                    "ELASTIC_SEARCH_INDEX_WILDCARD": "video-*",
                    "BEHAVIOR_INDEX": "behavior",
                    "FRAMES_INDEX_WILDCARD": "raw-*",
                    "RTSP_RAW_ES_INDEX_PATTERN": "cleanup-raw-*",
                    "NVIDIA_API_KEY": "must-not-be-read",
                }
            }

    container = {"envFrom": [{"configMapRef": {"name": "runtime"}}]}

    env, _secret_backed = deployment._read_nonsecret_environment(  # type: ignore[arg-type]
        container,
        RuntimeConfigMap(),
        {},
    )

    assert env == {
        "ELASTIC_SEARCH_INDEX_WILDCARD": "video-*",
        "BEHAVIOR_INDEX": "behavior",
        "FRAMES_INDEX_WILDCARD": "raw-*",
        "RTSP_RAW_ES_INDEX_PATTERN": "cleanup-raw-*",
    }


def test_kubernetes_port_forward_rewrites_and_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.stderr = StringIO()
            self.terminated = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, *, timeout: int) -> None:
            return None

        def kill(self) -> None:
            return None

    class FakeConnection:
        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    started: list[FakeProcess] = []
    commands: list[list[str]] = []
    events: list[str] = []

    def fake_popen(command: list[str], **_kwargs: Any) -> FakeProcess:
        events.append("spawn")
        commands.append(command)
        process = FakeProcess()
        started.append(process)
        return process

    monkeypatch.setattr(deployment.subprocess, "Popen", fake_popen)

    def fake_connection(*_args: Any, **_kwargs: Any) -> FakeConnection:
        events.append("connect")
        return FakeConnection()

    monkeypatch.setattr(deployment.socket, "create_connection", fake_connection)
    temporary_directory = tempfile.TemporaryDirectory()
    config = deployment.KubernetesDeploymentConfig(
        config_path=Path(temporary_directory.name) / "config.yml",
        env={},
        kubectl=deployment._Kubectl("vss", None),
        temporary_directory=temporary_directory,
    )
    monkeypatch.setattr(
        config._kubectl,
        "service_exists",
        lambda name, *, namespace=None: name == "elasticsearch" and namespace == "data-plane",
    )
    monkeypatch.setattr(config, "_free_local_port", lambda: 18443)
    monkeypatch.setattr(config, "_install_signal_handlers", lambda: events.append("handlers"))

    try:
        assert (
            config._rewrite_endpoint("http://elasticsearch.data-plane.svc.cluster.local:9200/path")
            == "http://127.0.0.1:18443/path"
        )
        # A second parsed endpoint to the same target reuses the one managed
        # port-forward instead of leaking a process per runtime field.
        assert config._rewrite_endpoint("http://elasticsearch.data-plane:9200/other") == "http://127.0.0.1:18443/other"
        assert len(started) == 1
        assert events[:3] == ["spawn", "handlers", "connect"]
        assert commands == [
            [
                "kubectl",
                "--namespace",
                "data-plane",
                "port-forward",
                "--address",
                "127.0.0.1",
                "service/elasticsearch",
                "18443:9200",
            ]
        ]
    finally:
        config.close()
    assert started[0].terminated is True


def test_kubernetes_rejects_ephemeral_external_vst_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    temporary_directory = tempfile.TemporaryDirectory()
    config = deployment.KubernetesDeploymentConfig(
        config_path=Path(temporary_directory.name) / "config.yml",
        env={},
        kubectl=deployment._Kubectl("vss", None),
        temporary_directory=temporary_directory,
    )
    monkeypatch.setattr(
        config._kubectl,
        "service_exists",
        lambda name, *, namespace=None: name == "vss-agent" and namespace == "vss",
    )

    def unexpected_forward(*_args: object) -> int:
        pytest.fail("external VST validation must fail before starting a port-forward")

    monkeypatch.setattr(config, "_start_forward", unexpected_forward)

    runtime = SearchRuntime(
        es_endpoint="https://es.example",
        behavior_es_endpoint="https://es.example",
        cosmos_embed_endpoint="https://embed.example",
        rtvi_cv_endpoint="https://cv.example",
        vst_internal_url="https://vst.example",
        vst_external_url="http://vss-agent:8000",
    )

    try:
        with pytest.raises(ConfigurationError, match="durable host links"):
            config.rewrite_runtime(runtime)
    finally:
        config.close()


def test_kubernetes_allows_internal_vst_for_critic_without_durable_links(monkeypatch: pytest.MonkeyPatch) -> None:
    temporary_directory = tempfile.TemporaryDirectory()
    config = deployment.KubernetesDeploymentConfig(
        config_path=Path(temporary_directory.name) / "config.yml",
        env={},
        kubectl=deployment._Kubectl("vss", None),
        temporary_directory=temporary_directory,
    )
    monkeypatch.setattr(
        config._kubectl,
        "service_exists",
        lambda name, *, namespace=None: name == "vss-agent" and namespace == "vss",
    )
    monkeypatch.setattr(config, "_rewrite_endpoint", lambda endpoint: f"forwarded:{endpoint}")
    runtime = SearchRuntime(
        es_endpoint="https://es.example",
        behavior_es_endpoint="https://es.example",
        cosmos_embed_endpoint="https://embed.example",
        rtvi_cv_endpoint="https://cv.example",
        vst_internal_url="http://vss-agent:8000",
        vst_external_url="http://vss-agent:8000",
    )

    try:
        updated = config.rewrite_runtime(runtime, fields={"vst_internal_url"})
    finally:
        config.close()

    assert updated.vst_internal_url == "forwarded:http://vss-agent:8000"


def test_kubernetes_rewrites_only_requested_runtime_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    temporary_directory = tempfile.TemporaryDirectory()
    config = deployment.KubernetesDeploymentConfig(
        config_path=Path(temporary_directory.name) / "config.yml",
        env={},
        kubectl=deployment._Kubectl("vss", None),
        temporary_directory=temporary_directory,
    )
    rewritten: list[str] = []
    monkeypatch.setattr(config, "_service_target", lambda _hostname: None)
    monkeypatch.setattr(config, "_rewrite_endpoint", lambda endpoint: rewritten.append(endpoint) or f"local:{endpoint}")
    runtime = SearchRuntime(
        es_endpoint="http://es:9200",
        behavior_es_endpoint="http://behavior:9200",
        cosmos_embed_endpoint="http://embed:8017",
        rtvi_cv_endpoint="http://cv:9000",
        vst_internal_url="http://vst:30888",
        vst_external_url="https://public.example",
        vlm_base_url="http://vlm:8000/v1",
    )

    try:
        updated = config.rewrite_runtime(runtime, fields={"es_endpoint", "cosmos_embed_endpoint"})
    finally:
        config.close()

    assert rewritten == ["http://es:9200", "http://embed:8017"]
    assert updated.es_endpoint == "local:http://es:9200"
    assert updated.rtvi_cv_endpoint == runtime.rtvi_cv_endpoint
    assert updated.vlm_base_url == runtime.vlm_base_url
