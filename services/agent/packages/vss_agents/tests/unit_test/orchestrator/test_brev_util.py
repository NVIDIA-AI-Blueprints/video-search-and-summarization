# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for vss_agents/orchestrator/brev_util.py."""

import json

from vss_agents.orchestrator import brev_util
from vss_agents.orchestrator.brev_util import apply_brev_proxy_env


def _write_context(path, *, env_id: str, ports: list[dict]) -> None:
    path.write_text(json.dumps({"environment_id": env_id, "ports": ports}), encoding="utf-8")


def test_apply_brev_proxy_env_uses_exact_fqdns_from_context(tmp_path, monkeypatch):
    context_path = tmp_path / "environment-context.json"
    _write_context(
        context_path,
        env_id="jr240wyfm",
        ports=[
            {"destination_port": 7777, "fqdn": "7777-jr240wyfm.stg.apps.launchpad.nvidia.com"},
            {"destination_port": 5601, "fqdn": "5601-jr240wyfm.stg.apps.launchpad.nvidia.com"},
        ],
    )
    monkeypatch.setenv("BREV_ENVIRONMENT_CONTEXT_PATH", str(context_path))
    monkeypatch.delenv("PROXY_PORT", raising=False)
    monkeypatch.delenv("KIBANA_PROXY_PORT_PREFIX", raising=False)
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    merged: dict[str, str] = {}

    apply_brev_proxy_env(merged, "jr240wyfm")

    assert merged["KIBANA_PUBLIC_URL"] == "https://5601-jr240wyfm.stg.apps.launchpad.nvidia.com"
    assert merged["VST_EXTERNAL_URL"] == "https://7777-jr240wyfm.stg.apps.launchpad.nvidia.com"
    assert merged["VSS_AGENT_EXTERNAL_URL"] == "https://7777-jr240wyfm.stg.apps.launchpad.nvidia.com"
    assert merged["VSS_AGENT_REPORTS_BASE_URL"] == "https://7777-jr240wyfm.stg.apps.launchpad.nvidia.com/static/"
    assert merged["VSS_PUBLIC_HTTP_PROTOCOL"] == "https"
    assert merged["VSS_PUBLIC_WS_PROTOCOL"] == "wss"
    assert merged["VSS_PUBLIC_HOST"] == "7777-jr240wyfm.stg.apps.launchpad.nvidia.com"
    assert merged["BREV_LINK_DOMAIN"] == "stg.apps.launchpad.nvidia.com"
    assert merged["VSS_PUBLIC_PORT"] == "443"


def test_apply_brev_proxy_env_reads_context_once(tmp_path, monkeypatch):
    context_path = tmp_path / "environment-context.json"
    _write_context(
        context_path,
        env_id="single-read",
        ports=[
            {"destination_port": 7777, "fqdn": "7777-single-read.apps.example.com"},
            {"destination_port": 5601, "fqdn": "5601-single-read.apps.example.com"},
        ],
    )
    monkeypatch.setenv("BREV_ENVIRONMENT_CONTEXT_PATH", str(context_path))
    monkeypatch.delenv("BREV_ENV_ID", raising=False)
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    original_read = brev_util.read_brev_environment_context
    read_count = 0

    def counting_read(path: str | None = None) -> dict:
        nonlocal read_count
        read_count += 1
        return original_read(path)

    monkeypatch.setattr(brev_util, "read_brev_environment_context", counting_read)
    merged: dict[str, str] = {}

    apply_brev_proxy_env(merged)

    assert read_count == 1
    assert merged["BREV_ENV_ID"] == "single-read"
    assert merged["BREV_LINK_DOMAIN"] == "apps.example.com"


def test_apply_brev_proxy_env_noop_when_proxy_port_missing_from_context(tmp_path, monkeypatch):
    context_path = tmp_path / "environment-context.json"
    _write_context(
        context_path,
        env_id="example",
        ports=[
            {"destination_port": 18789, "fqdn": "18789-example.stg.apps.launchpad.nvidia.com"},
        ],
    )
    monkeypatch.setenv("BREV_ENVIRONMENT_CONTEXT_PATH", str(context_path))
    monkeypatch.setenv("PROXY_PORT", "7777")
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    merged: dict[str, str] = {"KEEP": "me"}

    apply_brev_proxy_env(merged, "example")

    assert merged == {"KEEP": "me"}


def test_detect_brev_link_domain_returns_empty_without_context(monkeypatch):
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    monkeypatch.delenv("BREV_ENVIRONMENT_CONTEXT_PATH", raising=False)

    assert brev_util.detect_brev_link_domain() == ""


def test_detect_brev_link_domain_derives_from_context(tmp_path, monkeypatch):
    context_path = tmp_path / "environment-context.json"
    _write_context(
        context_path,
        env_id="skybridge-env",
        ports=[
            {
                "destination_port": 7777,
                "fqdn": "7777-skybridge-env.apps.run.brev.nvidia.com",
            }
        ],
    )
    monkeypatch.setenv("BREV_ENVIRONMENT_CONTEXT_PATH", str(context_path))
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)

    assert brev_util.detect_brev_link_domain() == "apps.run.brev.nvidia.com"


def test_apply_brev_proxy_env_explicit_domain_wins(tmp_path, monkeypatch):
    context_path = tmp_path / "environment-context.json"
    _write_context(
        context_path,
        env_id="explicit-env",
        ports=[
            {"destination_port": 7777, "fqdn": "7777-explicit-env.stg.apps.launchpad.nvidia.com"},
        ],
    )
    monkeypatch.setenv("BREV_ENVIRONMENT_CONTEXT_PATH", str(context_path))
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    merged: dict[str, str] = {}

    apply_brev_proxy_env(merged, "explicit-env", explicit_link_domain=" custom.example.com ")

    assert merged["BREV_LINK_DOMAIN"] == "custom.example.com"
    assert merged["VST_EXTERNAL_URL"] == "https://7777-explicit-env.stg.apps.launchpad.nvidia.com"


def test_apply_brev_proxy_env_noop_without_context(monkeypatch):
    monkeypatch.delenv("BREV_LINK_DOMAIN", raising=False)
    monkeypatch.delenv("BREV_ENVIRONMENT_CONTEXT_PATH", raising=False)
    merged: dict[str, str] = {"KEEP": "me"}

    apply_brev_proxy_env(merged, "explicit-env", explicit_link_domain="custom.example.com")

    assert merged == {"KEEP": "me"}


def test_brev_environment_id_prefers_env_then_context(tmp_path, monkeypatch):
    context_path = tmp_path / "environment-context.json"
    _write_context(context_path, env_id="from-context", ports=[])
    monkeypatch.setenv("BREV_ENVIRONMENT_CONTEXT_PATH", str(context_path))
    monkeypatch.setenv("BREV_ENV_ID", "from-env")

    assert brev_util.brev_environment_id() == "from-env"

    monkeypatch.delenv("BREV_ENV_ID", raising=False)
    assert brev_util.brev_environment_id() == "from-context"


def test_brev_secure_link_fqdn_reads_exact_port(tmp_path, monkeypatch):
    context_path = tmp_path / "environment-context.json"
    _write_context(
        context_path,
        env_id="env-123",
        ports=[{"destination_port": 7777, "fqdn": "7777-env-123.stg.apps.launchpad.nvidia.com"}],
    )
    monkeypatch.setenv("BREV_ENVIRONMENT_CONTEXT_PATH", str(context_path))

    assert brev_util.brev_secure_link_fqdn(7777) == "7777-env-123.stg.apps.launchpad.nvidia.com"
    assert brev_util.brev_secure_link_fqdn(5601) is None


def test_brev_secure_link_fqdn_returns_none_without_context(monkeypatch):
    monkeypatch.delenv("BREV_ENVIRONMENT_CONTEXT_PATH", raising=False)

    assert brev_util.brev_secure_link_fqdn(7777) is None
