# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Discovery contract for the Video Analytics API."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner

from vss_cli import config as config_mod
from vss_cli import configure as configure_mod

if TYPE_CHECKING:
    import pytest


def test_configure_probes_and_records_video_analytics(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    probes: list[str] = []

    def probe(_base: str, path: str, _timeout: float) -> tuple[bool, str]:
        probes.append(path)
        return path == "/video-analytics-api/livez", "HTTP 200"

    monkeypatch.setattr(configure_mod, "_probe", probe)
    monkeypatch.setattr(configure_mod, "_describe", lambda *_a, **_kw: [])
    result = CliRunner().invoke(configure_mod.configure, ["--base-url", "https://vss.test"])
    assert result.exit_code == 0, result.output
    assert "/video-analytics-api/livez" in probes
    deployment = config_mod.load()
    assert deployment.services.keys() == {"video_analytics"}
    assert deployment.endpoint("video_analytics") == "https://vss.test/video-analytics-api"

    shown = CliRunner().invoke(configure_mod.configure, ["show"])
    assert shown.exit_code == 0
    assert json.loads(shown.stdout)["services"]["video_analytics"]["url"] == ("https://vss.test/video-analytics-api")


def test_configure_check_reports_analytics_availability(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    monkeypatch.setattr(configure_mod, "_probe", lambda *_a, **_kw: (True, "HTTP 200"))

    for services, expected in (
        ({"video_analytics": config_mod.Service("https://vss.test/video-analytics-api")}, "available"),
        ({"vst": config_mod.Service("https://vss.test/vst")}, "unavailable"),
    ):
        config_mod.save(config_mod.Deployment(base_url="https://vss.test", services=services))
        result = CliRunner().invoke(configure_mod.configure, ["check"])
        analytics_line = next(line for line in result.output.splitlines() if line.strip().startswith("analytics"))
        assert expected in analytics_line
