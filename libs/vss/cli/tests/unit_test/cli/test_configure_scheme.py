# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vss configure` must not blame the ingress for a missing scheme.

Without a scheme httpx refuses to build the request, so every route probe
returns UnsupportedProtocol and the summary reads as "your deployment exposed
nothing" — sending the operator to check a stack that was fine all along.
"""

from __future__ import annotations

from click.testing import CliRunner
import pytest

from vss_cli import configure as configure_mod


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the URLs probed; report every route absent."""
    seen: list[str] = []

    def fake_probe(base_url: str, probe_path: str, _timeout: float) -> tuple[bool, str]:
        seen.append(f"{base_url.rstrip('/')}{probe_path}")
        return False, "connection refused"

    monkeypatch.setattr(configure_mod, "_probe", fake_probe)
    return seen


def test_a_scheme_less_origin_is_assumed_http_and_said_out_loud(probes: list[str]) -> None:
    result = CliRunner().invoke(configure_mod.configure, ["--base-url", "localhost:7777"])

    assert all(url.startswith("http://localhost:7777") for url in probes), probes
    assert "no scheme given, assuming http://localhost:7777" in result.output


def test_a_bare_host_is_assumed_http(probes: list[str]) -> None:
    CliRunner().invoke(configure_mod.configure, ["--base-url", "127.0.0.1"])

    assert all(url.startswith("http://127.0.0.1/") for url in probes), probes


@pytest.mark.parametrize("origin", ["http://localhost:7777", "https://vss.example.com"])
def test_an_explicit_scheme_is_left_alone(origin: str, probes: list[str]) -> None:
    result = CliRunner().invoke(configure_mod.configure, ["--base-url", origin])

    assert all(url.startswith(origin) for url in probes), probes
    assert "no scheme given" not in result.output


def test_host_port_is_not_parsed_as_a_scheme(probes: list[str]) -> None:
    """urlparse reads "localhost:7777" as scheme "localhost" — hence the "://" check."""
    CliRunner().invoke(configure_mod.configure, ["--base-url", "localhost:7777"])

    assert probes and "//localhost:7777/" in probes[0], probes
