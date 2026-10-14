# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for recorded-file and live LVS deployment data paths."""

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_CONFIGS = (
    REPO_ROOT / "deploy/docker/services/video-summarization/configs/config.yaml",
    REPO_ROOT / "deploy/helm/services/video-summarization/configs/config.yaml",
)


class EnvSafeLoader(yaml.SafeLoader):
    """Load test configuration while preserving scalar ``!ENV`` expressions."""


def _construct_env_scalar(loader: EnvSafeLoader, node: yaml.Node) -> str:
    """Return an unresolved ``!ENV`` value as a scalar string for assertions."""
    return loader.construct_scalar(node)


EnvSafeLoader.add_constructor("!ENV", _construct_env_scalar)


@pytest.mark.parametrize("config_path", DEPLOYMENT_CONFIGS)
def test_file_and_live_summarization_use_separate_data_paths(config_path: Path) -> None:
    """Keep file summaries synchronous and live summaries Kafka-backed."""
    config = yaml.load(config_path.read_text(), Loader=EnvSafeLoader)
    functions = config["functions"]

    file_summary = functions["summarization"]
    assert file_summary["type"] == "vlm_structured_summarization"
    assert file_summary["params"]["kafka_enabled"] is False

    live_summary = functions["summarization_online"]
    assert live_summary["type"] == "vlm_structured_summarization_online"
    assert live_summary["params"]["kafka_enabled"] is True


# --- NemoClaw clip URLs --------------------------------------------------------
#
# The vss CLI in a NemoClaw sandbox mints clip URLs on host.openshell.internal.
# LVS passes them to its VLM, and the VLM is what fetches the clip. The ingress
# carries that name as a network alias, so every container on the Compose network
# resolves it to vss-haproxy-ingress (which already accepts that Host) without a
# per-service extra_hosts entry.

COMPOSE_SERVICES = REPO_ROOT / "deploy/docker/services"
OPENSHELL_ALIAS = "${HOST_INTERNAL_ALIAS:-host.openshell.internal}"
VLM_PREFIXES = ("rtvi-vlm", "cosmos3-reasoner")


class ComposeLoader(yaml.SafeLoader):
    """Read Compose files, whose merge tags (`!override`, `!reset`) are plain values here."""


def _construct_compose_tag(loader: ComposeLoader, _suffix: str, node: yaml.Node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)


ComposeLoader.add_multi_constructor("!", _construct_compose_tag)


def _compose_services() -> dict[str, dict]:
    services: dict[str, dict] = {}
    for path in sorted(COMPOSE_SERVICES.rglob("*.yml")):
        document = yaml.load(path.read_text(), Loader=ComposeLoader)
        if isinstance(document, dict) and isinstance(document.get("services"), dict):
            services.update(document["services"])
    return services


def _networks(service: dict) -> set[str]:
    networks = service.get("networks") or {"default": None}
    return set(networks) if isinstance(networks, dict) else set(networks)


def test_the_ingress_answers_the_nemoclaw_alias_on_the_compose_network() -> None:
    ingress = _compose_services()["vss-haproxy-ingress"]
    aliases = ((ingress.get("networks") or {}).get("default") or {}).get("aliases") or []
    assert OPENSHELL_ALIAS in aliases, f"vss-haproxy-ingress has no {OPENSHELL_ALIAS} alias on the default network"


def test_lvs_and_every_vlm_it_calls_share_the_ingress_network() -> None:
    services = _compose_services()
    lvs = services["lvs-server"]
    vlms = sorted(name for name in lvs.get("depends_on", {}) if name.startswith(VLM_PREFIXES))
    assert vlms, "lvs-server depends on no VLM service; update VLM_PREFIXES"
    off_network = [
        name for name in ["lvs-server", *vlms]
        if services[name].get("network_mode") or "default" not in _networks(services[name])
    ]
    assert not off_network, f"{off_network} cannot reach the ingress alias; NemoClaw clip URLs would not resolve"
