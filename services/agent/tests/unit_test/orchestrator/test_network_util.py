# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for vss_agents/orchestrator/network_util.py."""

from vss_agents.orchestrator import network_util


def test_apply_brev_proxy_env_sets_brev_and_public_ui_routes(monkeypatch):
    monkeypatch.setenv("PROXY_PORT", "7777")
    monkeypatch.setenv("BREV_LINK_PREFIX", "7777")
    monkeypatch.setenv("KIBANA_PROXY_PORT_PREFIX", "5601")
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    monkeypatch.setattr(
        network_util.subprocess,
        "run",
        lambda *args, **kwargs: network_util.subprocess.CompletedProcess(args[0], returncode=1),
    )
    merged: dict[str, str] = {}

    network_util.apply_brev_proxy_env(merged, "jr240wyfm")

    assert merged["BREV_LINK_DOMAIN"] == "brevlab.com"
    assert merged["KIBANA_PUBLIC_URL"] == "https://5601-jr240wyfm.brevlab.com"
    assert merged["VST_EXTERNAL_URL"] == "https://7777-jr240wyfm.brevlab.com"
    assert merged["VSS_AGENT_EXTERNAL_URL"] == "https://7777-jr240wyfm.brevlab.com"
    assert merged["VSS_AGENT_REPORTS_BASE_URL"] == "https://7777-jr240wyfm.brevlab.com/static/"
    assert merged["VSS_PUBLIC_HTTP_PROTOCOL"] == "https"
    assert merged["VSS_PUBLIC_WS_PROTOCOL"] == "wss"
    assert merged["VSS_PUBLIC_HOST"] == "7777-jr240wyfm.brevlab.com"
    assert merged["VSS_PUBLIC_PORT"] == "443"


def test_apply_brev_proxy_env_respects_custom_link_prefix(monkeypatch):
    monkeypatch.setenv("BREV_LINK_PREFIX", "12340")
    monkeypatch.setenv("PROXY_PORT", "7777")
    monkeypatch.setenv("KIBANA_PROXY_PORT_PREFIX", "56010")
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    monkeypatch.setattr(
        network_util.subprocess,
        "run",
        lambda *args, **kwargs: network_util.subprocess.CompletedProcess(args[0], returncode=1),
    )
    merged: dict[str, str] = {}

    network_util.apply_brev_proxy_env(merged, "example")

    assert merged["VST_EXTERNAL_URL"] == "https://12340-example.brevlab.com"
    assert merged["VSS_PUBLIC_HOST"] == "12340-example.brevlab.com"
    assert merged["KIBANA_PUBLIC_URL"] == "https://56010-example.brevlab.com"


def test_apply_brev_proxy_env_selects_skybridge_for_active_netbird(monkeypatch):
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    monkeypatch.setattr(
        network_util.subprocess,
        "run",
        lambda *args, **kwargs: network_util.subprocess.CompletedProcess(args[0], returncode=0),
    )
    merged = {
        "PROXY_PORT": "7777",
        "BREV_LINK_PREFIX": "vss",
        "KIBANA_PROXY_PORT_PREFIX": "logs",
    }

    network_util.apply_brev_proxy_env(merged, "example")

    assert merged["BREV_LINK_DOMAIN"] == "apps.run.brev.nvidia.com"
    assert merged["VSS_PUBLIC_HOST"] == "vss-example.apps.run.brev.nvidia.com"
    assert merged["KIBANA_PUBLIC_URL"] == "https://logs-example.apps.run.brev.nvidia.com"


def test_apply_brev_proxy_env_explicit_domain_wins_and_is_persisted(monkeypatch):
    monkeypatch.setenv("BREV_LINK_DOMAIN", "  links.example.test  ")

    def fail_if_netbird_runs(*args, **kwargs):
        raise AssertionError("netbird must not run for an explicit domain")

    monkeypatch.setattr(network_util.subprocess, "run", fail_if_netbird_runs)
    merged = {
        "PROXY_PORT": "7777",
        "BREV_LINK_PREFIX": "vss",
        "KIBANA_PROXY_PORT_PREFIX": "logs",
    }

    network_util.apply_brev_proxy_env(merged, "example")

    assert merged["BREV_LINK_DOMAIN"] == "links.example.test"
    assert merged["VSS_PUBLIC_HOST"] == "vss-example.links.example.test"
    assert merged["KIBANA_PUBLIC_URL"] == "https://logs-example.links.example.test"
