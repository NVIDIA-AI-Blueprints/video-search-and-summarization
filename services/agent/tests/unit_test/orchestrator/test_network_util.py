# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for vss_agents/orchestrator/network_util.py."""

from vss_agents.orchestrator.network_util import apply_brev_proxy_env


def test_apply_brev_proxy_env_sets_brev_and_public_ui_routes(monkeypatch):
    monkeypatch.delenv("PROXY_PORT", raising=False)
    monkeypatch.delenv("BREV_LINK_PREFIX", raising=False)
    merged: dict[str, str] = {}

    apply_brev_proxy_env(merged, "jr240wyfm")

    assert merged["KIBANA_PUBLIC_URL"] == "https://56010-jr240wyfm.brevlab.com"
    assert merged["VST_EXTERNAL_URL"] == "https://77770-jr240wyfm.brevlab.com"
    assert merged["VSS_AGENT_EXTERNAL_URL"] == "https://77770-jr240wyfm.brevlab.com"
    assert merged["VSS_AGENT_REPORTS_BASE_URL"] == "https://77770-jr240wyfm.brevlab.com/static/"
    assert merged["VSS_PUBLIC_HTTP_PROTOCOL"] == "https"
    assert merged["VSS_PUBLIC_WS_PROTOCOL"] == "wss"
    assert merged["VSS_PUBLIC_HOST"] == "77770-jr240wyfm.brevlab.com"
    assert merged["VSS_PUBLIC_PORT"] == "443"


def test_apply_brev_proxy_env_respects_custom_link_prefix(monkeypatch):
    monkeypatch.setenv("BREV_LINK_PREFIX", "12340")
    monkeypatch.setenv("PROXY_PORT", "7777")
    merged: dict[str, str] = {}

    apply_brev_proxy_env(merged, "example")

    assert merged["VST_EXTERNAL_URL"] == "https://12340-example.brevlab.com"
    assert merged["VSS_PUBLIC_HOST"] == "12340-example.brevlab.com"
