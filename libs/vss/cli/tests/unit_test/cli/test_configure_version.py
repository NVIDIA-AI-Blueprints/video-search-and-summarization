# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vss configure check` reports what version the deployment says it is.

The benchmark skills stop when a deployment cannot report a version, so an
operator should be able to learn that from the prober they already run rather
than from an aborted benchmark.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner
import pytest

from vss_cli import config as config_mod
from vss_cli import configure as configure_mod

if TYPE_CHECKING:
    from pathlib import Path


class _Response:
    def __init__(self, status_code: int, payload: Any = None, *, json_fails: bool = False) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_fails = json_fails

    def json(self) -> Any:
        if self._json_fails:
            raise ValueError("not JSON")
        return self._payload


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    config_mod.save(
        config_mod.Deployment(
            base_url="http://example",
            services={"agent": config_mod.Service(url="http://example/api")},
            written_at="2026-09-18T00:00:00+00:00",
        )
    )
    # Route reachability is a separate concern; keep every route present so the
    # output under test is the version line.
    monkeypatch.setattr(configure_mod, "_probe", lambda *_: (True, "HTTP 200"))


def _check(monkeypatch: pytest.MonkeyPatch, response: _Response | Exception) -> Any:
    def get(url: str, **_kwargs: Any) -> _Response:
        assert url == "http://example/api/v1/version", url
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("httpx.get", get)
    return CliRunner().invoke(configure_mod.configure, ["check"])


@pytest.mark.usefixtures("configured")
def test_a_reported_version_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _check(monkeypatch, _Response(200, {"service": "vss", "version": "3.3.0-65576357eb80"}))

    assert "version" in result.output
    assert "3.3.0-65576357eb80" in result.output


@pytest.mark.usefixtures("configured")
def test_no_agent_behind_the_origin_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """404 is the lean-stack and old-deployment case, and they read alike."""
    result = _check(monkeypatch, _Response(404))

    assert "not reported" in result.output
    assert "no agent behind /api" in result.output


@pytest.mark.usefixtures("configured")
def test_a_misconfigured_deployment_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _check(monkeypatch, _Response(503))

    assert "not reported" in result.output
    assert "no usable version" in result.output


@pytest.mark.usefixtures("configured")
def test_a_non_json_answer_is_not_a_version(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _check(monkeypatch, _Response(200, json_fails=True))

    assert "not reported" in result.output


@pytest.mark.usefixtures("configured")
def test_an_unreportable_version_does_not_fail_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stack without the agent is a legitimate deployment, not drift."""
    result = _check(monkeypatch, _Response(404))

    assert result.exit_code == 0, result.output
