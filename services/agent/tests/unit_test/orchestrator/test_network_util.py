# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for agent/orchestrator/network_util.py."""

from agent.orchestrator import network_util
from agent.orchestrator.network_util import apply_brev_proxy_env


def test_apply_brev_proxy_env_sets_brev_and_public_ui_routes(monkeypatch):
    monkeypatch.delenv("PROXY_PORT", raising=False)
    monkeypatch.delenv("BREV_LINK_PREFIX", raising=False)
    monkeypatch.delenv("KIBANA_PROXY_PORT_PREFIX", raising=False)
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    monkeypatch.setattr(
        network_util.subprocess,
        "run",
        lambda *args, **_kwargs: network_util.subprocess.CompletedProcess(args[0], 1),
    )
    merged: dict[str, str] = {}

    apply_brev_proxy_env(merged, "jr240wyfm")

    assert merged["KIBANA_PUBLIC_URL"] == "https://5601-jr240wyfm.brevlab.com"
    assert merged["VST_EXTERNAL_URL"] == "https://7777-jr240wyfm.brevlab.com"
    assert merged["VSS_AGENT_EXTERNAL_URL"] == "https://7777-jr240wyfm.brevlab.com"
    assert merged["VSS_AGENT_REPORTS_BASE_URL"] == "https://7777-jr240wyfm.brevlab.com/static/"
    assert merged["VSS_PUBLIC_HTTP_PROTOCOL"] == "https"
    assert merged["VSS_PUBLIC_WS_PROTOCOL"] == "wss"
    assert merged["VSS_PUBLIC_HOST"] == "7777-jr240wyfm.brevlab.com"
    assert merged["BREV_LINK_DOMAIN"] == "brevlab.com"
    assert merged["VSS_PUBLIC_PORT"] == "443"


def test_apply_brev_proxy_env_respects_custom_link_prefix(monkeypatch):
    monkeypatch.setenv("BREV_LINK_PREFIX", "12340")
    monkeypatch.setenv("PROXY_PORT", "7777")
    monkeypatch.setenv("KIBANA_PROXY_PORT_PREFIX", "56010")
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    monkeypatch.setattr(
        network_util.subprocess,
        "run",
        lambda *args, **_kwargs: network_util.subprocess.CompletedProcess(args[0], 1),
    )
    merged: dict[str, str] = {}

    apply_brev_proxy_env(merged, "example")

    assert merged["VST_EXTERNAL_URL"] == "https://12340-example.brevlab.com"
    assert merged["VSS_PUBLIC_HOST"] == "12340-example.brevlab.com"
    assert merged["KIBANA_PUBLIC_URL"] == "https://56010-example.brevlab.com"


def test_detect_brev_link_domain_keeps_cloudflare_for_generic_netbird(monkeypatch):
    monkeypatch.setattr(
        network_util.subprocess,
        "run",
        lambda *args, **_kwargs: network_util.subprocess.CompletedProcess(
            args[0],
            0,
            stdout="Management: Connected\nSignal: Connected\n",
        ),
    )

    assert network_util.detect_brev_link_domain() == "brevlab.com"


def test_apply_brev_proxy_env_refreshes_stale_domain_after_skybridge_migration(monkeypatch):
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    monkeypatch.setattr(
        network_util.subprocess,
        "run",
        lambda *args, **_kwargs: network_util.subprocess.CompletedProcess(
            args[0],
            0,
            stdout=(
                "Peers detail:\n"
                " skybridge-env.netbird.selfhosted:\n"
                "  NetBird IP: 100.64.0.42\n"
                "  FQDN: skybridge-env.apps.run.brev.nvidia.com\n"
                "  Status: Connected\n"
            ),
        ),
    )
    merged = {"BREV_LINK_DOMAIN": "brevlab.com"}

    apply_brev_proxy_env(merged, "skybridge-env")

    assert merged["BREV_LINK_DOMAIN"] == "apps.run.brev.nvidia.com"
    assert merged["KIBANA_PUBLIC_URL"] == "https://5601-skybridge-env.apps.run.brev.nvidia.com"
    assert merged["VST_EXTERNAL_URL"] == "https://7777-skybridge-env.apps.run.brev.nvidia.com"
    assert merged["VSS_AGENT_EXTERNAL_URL"] == "https://7777-skybridge-env.apps.run.brev.nvidia.com"
    assert merged["VSS_AGENT_REPORTS_BASE_URL"] == ("https://7777-skybridge-env.apps.run.brev.nvidia.com/static/")
    assert merged["VSS_PUBLIC_HOST"] == "7777-skybridge-env.apps.run.brev.nvidia.com"


def test_apply_brev_proxy_env_explicit_domain_wins_and_is_persisted(monkeypatch):
    def unexpected_netbird(*_args, **_kwargs):
        raise AssertionError("netbird must not run")

    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    monkeypatch.setattr(network_util.subprocess, "run", unexpected_netbird)
    merged = {"BREV_LINK_DOMAIN": "brevlab.com"}

    apply_brev_proxy_env(merged, "explicit-env", explicit_link_domain=" custom.example.com ")

    assert merged["BREV_LINK_DOMAIN"] == "custom.example.com"
    assert merged["VST_EXTERNAL_URL"] == "https://7777-explicit-env.custom.example.com"
