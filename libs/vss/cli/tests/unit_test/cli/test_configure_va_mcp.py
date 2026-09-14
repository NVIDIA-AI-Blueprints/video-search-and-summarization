# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""`/va-mcp` is part of the gateway's path contract, so `configure` probes it.

The Video Analytics MCP server is the one mount an operator cannot check from
the host any other way -- the agent calls it over the container network -- and
"is /va-mcp routed?" is the first question when the agent's video-analytics
tools go missing. It is deliberately required by no command group: nothing in
this CLI is its client, and attaching it to `search` would make that group
report unavailable for a service it never calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner

from vss_cli import config as config_mod
from vss_cli import configure as configure_mod

if TYPE_CHECKING:
    import pytest


def test_va_mcp_is_mounted_and_probed_on_health() -> None:
    """A bare GET on /mcp answers 406, which is not a "present" status.

    Measured against a live streamable-HTTP MCP server: probing /mcp would
    record a routed server as absent. /health is the same 200 the container
    healthcheck already relies on, so the two agree on what "up" means.
    """
    from vss_cli.configure import _PRESENT_STATUSES

    route = config_mod.INGRESS_SERVICES["va_mcp"]

    assert route.mount == "/va-mcp"
    assert route.probe == "/va-mcp/health"
    assert 406 not in _PRESENT_STATUSES
    # Listing MCP tools needs an initialized JSON-RPC session, which discovery
    # must not open just to write a config file.
    assert route.describe is None


def test_va_mcp_is_recorded_from_the_same_origin(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    monkeypatch.setattr(
        configure_mod,
        "_probe",
        lambda _base, path, _t: (path == "/va-mcp/health", "HTTP 200"),
    )
    monkeypatch.setattr(configure_mod, "_describe", lambda *_a, **_k: [])

    result = CliRunner().invoke(configure_mod.configure, ["--base-url", "http://h:7777"])
    assert result.exit_code == 0, result.output

    recorded = config_mod.load()
    assert recorded.services.keys() == {"va_mcp"}
    assert recorded.endpoint("va_mcp") == "http://h:7777/va-mcp"


def test_a_profile_without_the_mcp_server_records_no_route(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HAProxy answers 503 when the backend is down, which is absent.

    Recording it as present would move the failure from `configure`, where the
    URL is on screen, to the first call that needed it.
    """
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    monkeypatch.setattr(
        configure_mod,
        "_probe",
        lambda _base, path, _t: (path != "/va-mcp/health", "HTTP 200" if path != "/va-mcp/health" else "HTTP 503"),
    )
    monkeypatch.setattr(configure_mod, "_describe", lambda *_a, **_k: [])

    result = CliRunner().invoke(configure_mod.configure, ["--base-url", "http://h:7777"])
    assert result.exit_code == 0, result.output
    assert "va_mcp" not in config_mod.load().services


def test_va_mcp_is_no_group_s_requirement() -> None:
    """It is recorded for the operator, not consumed by a command.

    A deployment exposing only /va-mcp can serve `configure` and nothing else,
    and no group's "needs ..." line mentions it.
    """
    deployment = config_mod.Deployment(
        base_url="http://h:7777",
        services={"va_mcp": config_mod.Service(url="http://h:7777/va-mcp")},
    )

    rows = {name: (ok, detail) for name, ok, detail in configure_mod._command_availability(deployment)}

    assert rows["configure"][0] is True
    assert rows["search"][0] is False
    assert not [detail for _ok, detail in rows.values() if "va_mcp" in detail]


def test_configure_check_reports_the_recorded_va_mcp_route(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    config_mod.save(
        config_mod.Deployment(
            base_url="http://h:7777",
            services={"va_mcp": config_mod.Service(url="http://h:7777/va-mcp")},
            written_at="2026-08-26T00:00:00+00:00",
        )
    )
    probed: list[str] = []

    def fake_probe(_base: str, path: str, _timeout: float) -> tuple[bool, str]:
        probed.append(path)
        return True, "HTTP 200"

    monkeypatch.setattr(configure_mod, "_probe", fake_probe)

    result = CliRunner().invoke(configure_mod.check, [])
    assert result.exit_code == 0, result.output
    assert probed == ["/va-mcp/health"]
    assert "va_mcp" in result.output
    assert "http://h:7777/va-mcp" in result.output
