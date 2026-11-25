# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vss configure check` reports LVS /v1/ready without treating warmup as drift.

The route is recorded on /v1/live. Readiness answers 503 while the model
loads, so that answer must not fail the check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner

from vss_cli import config as config_mod
from vss_cli import configure as configure_mod

if TYPE_CHECKING:
    from pathlib import Path


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _configured(tmp_path: Path, monkeypatch: Any, *, lvs: bool) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    services = {"agent": config_mod.Service(url="http://example/api")}
    if lvs:
        services["lvs"] = config_mod.Service(url="http://example/lvs")
    config_mod.save(
        config_mod.Deployment(
            base_url="http://example",
            services=services,
            written_at="2026-09-18T00:00:00+00:00",
        )
    )
    monkeypatch.setattr(configure_mod, "_probe", lambda *_: (True, "HTTP 200"))
    monkeypatch.setattr(configure_mod, "_deployment_version", lambda *_: ("3.3.0", ""))


def _check(monkeypatch: Any, status: int | Exception) -> Any:
    def get(url: str, **_kwargs: Any) -> _Response:
        assert url == "http://example/lvs/v1/ready", url
        if isinstance(status, Exception):
            raise status
        return _Response(status)

    monkeypatch.setattr("httpx.get", get)
    return CliRunner().invoke(configure_mod.configure, ["check"])


def test_ready_is_reported(tmp_path: Path, monkeypatch: Any) -> None:
    _configured(tmp_path, monkeypatch, lvs=True)
    result = _check(monkeypatch, 200)

    assert result.exit_code == 0, result.output
    assert "lvs ready" in result.output
    assert "ready" in result.output
    assert "HTTP 200" in result.output


def test_warmup_does_not_fail_the_check(tmp_path: Path, monkeypatch: Any) -> None:
    _configured(tmp_path, monkeypatch, lvs=True)
    result = _check(monkeypatch, 503)

    assert result.exit_code == 0, result.output
    assert "warming" in result.output
    assert "HTTP 503" in result.output


def test_a_deployment_without_lvs_skips_the_probe(tmp_path: Path, monkeypatch: Any) -> None:
    _configured(tmp_path, monkeypatch, lvs=False)

    def get(*_args: Any, **_kwargs: Any) -> _Response:
        raise AssertionError("no readiness probe without a recorded lvs service")

    monkeypatch.setattr("httpx.get", get)
    result = CliRunner().invoke(configure_mod.configure, ["check"])

    assert result.exit_code == 0, result.output
    assert "lvs ready" not in result.output
