# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the non-job ``vss analytics`` group."""

from __future__ import annotations

import json
from typing import Any
from typing import ClassVar

from click.testing import CliRunner
import pytest

from vss_cli.analytics import group as analytics_group
from vss_cli.exits import Exit
from vss_cli.group import Context


class _Deployment:
    base_url = "https://vss.test"
    services: ClassVar[dict[str, object]] = {"video_analytics": object()}

    def has(self, name: str) -> bool:
        return name in self.services

    def endpoint(self, name: str) -> str:
        assert name == "video_analytics"
        return "https://vss.test/video-analytics-api"


class _Client:
    calls: ClassVar[list[tuple[str, dict[str, Any]]]] = []
    failure: ClassVar[Exception | None] = None

    def __init__(self, endpoint: str) -> None:
        assert endpoint == "https://vss.test/video-analytics-api"

    @classmethod
    def _record(cls, operation: str, values: dict[str, Any]) -> None:
        cls.calls.append((operation, values))
        if cls.failure is not None:
            raise cls.failure

    async def incidents(self, **values: Any) -> list[dict[str, Any]]:
        self._record("incidents", values)
        return [{"id": "i-1", "timestamp": "2026-01-01T00:00:00Z"}]

    async def incident(self, incident_id: str, **values: Any) -> dict[str, Any]:
        self._record("incident", {"incident_id": incident_id, **values})
        return {"id": incident_id}

    async def sensors(self, **values: Any) -> list[str]:
        self._record("sensors", values)
        return []

    async def places(self) -> list[str]:
        self._record("places", {})
        return ["building=Warehouse/room=Room-1"]

    async def fov_histogram(self, **values: Any) -> dict[str, Any]:
        self._record("fov-histogram", values)
        return {"bucketSizeInSec": 5, "histogram": []}

    async def average_speed(self, **values: Any) -> dict[str, Any]:
        self._record("average-speed", values)
        return {"metrics": [{"direction": "North", "averageSpeed": "25 mph"}]}

    async def analyze(self, **values: Any) -> dict[str, Any]:
        self._record("analyze", values)
        return {"analysis_type": values["analysis_type"], "result": {}, "summary": "done"}


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.calls = []
    _Client.failure = None
    monkeypatch.setattr(analytics_group, "context_from", lambda values: Context(_Deployment(), values.get("pretty")))
    import vss_core.analytics

    monkeypatch.setattr(vss_core.analytics, "AnalyticsClient", _Client)


@pytest.fixture
def cli() -> Any:
    return analytics_group.ANALYTICS.cli()


def _invoke(cli: Any, args: list[str]) -> Any:
    return CliRunner().invoke(cli, args, catch_exceptions=False)


def test_group_has_only_read_commands(cli: Any) -> None:
    assert set(cli.commands) == {
        "incidents",
        "incident",
        "sensors",
        "places",
        "fov-histogram",
        "average-speed",
        "analyze",
    }
    assert frozenset({"video_analytics"}) == analytics_group.REQUIRES


def test_help_documents_service_and_sensor_distinction(cli: Any) -> None:
    result = _invoke(cli, ["sensors", "--help"])
    assert "Requires: video_analytics" in result.stdout
    assert "not the VIOS registry" in result.stdout
    for forbidden in (
        "--endpoint",
        "--host",
        "--port",
        "--deployment",
        "--profile",
        "--namespace",
        "--release",
        "--kube-context",
        "--es-endpoint",
    ):
        assert forbidden not in result.stdout


def test_incidents_returns_stable_object_and_translates_options(cli: Any) -> None:
    result = _invoke(
        cli,
        [
            "incidents",
            "--source",
            "cam-1",
            "--source-type",
            "sensor",
            "--start-time",
            "2026-01-01T00:00:00Z",
            "--end-time",
            "2026-01-01T00:01:00Z",
            "--limit",
            "4",
            "--include",
            "info",
            "--vlm-verdict",
            "confirmed",
        ],
    )
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["count"] == 1
    assert body["has_more"] is False
    operation, values = _Client.calls[0]
    assert operation == "incidents"
    assert values["limit"] == 5
    assert values["includes"] == ("info",)
    assert values["vlm_verdict"] == "confirmed"


def test_empty_results_are_success(cli: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty(self: _Client, **values: Any) -> list[dict[str, Any]]:
        self._record("incidents", values)
        return []

    monkeypatch.setattr(_Client, "incidents", empty)
    result = _invoke(cli, ["incidents"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"count": 0, "incidents": [], "has_more": False}


def test_incidents_reports_has_more_when_the_page_is_full(cli: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def page(self: _Client, **values: Any) -> list[dict[str, Any]]:
        self._record("incidents", values)
        return [{"id": f"i-{index}"} for index in range(int(values["limit"]))]

    monkeypatch.setattr(_Client, "incidents", page)
    result = _invoke(cli, ["incidents", "--limit", "3"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "count": 3,
        "incidents": [{"id": "i-0"}, {"id": "i-1"}, {"id": "i-2"}],
        "has_more": True,
    }
    assert _Client.calls[0][1]["limit"] == 4


def test_incidents_accepts_the_maximum_user_visible_limit(cli: Any) -> None:
    result = _invoke(cli, ["incidents", "--limit", "9999"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["has_more"] is False
    assert _Client.calls[0][1]["limit"] == 10000


@pytest.mark.parametrize(
    "args,fragment",
    [
        (["incidents", "--source", "cam"], "source-type"),
        (["incidents", "--source-type", "sensor"], "source"),
        (["incidents", "--start-time", "2026-01-01T00:00:00Z"], "end-time"),
        (["incidents", "--limit", "0"], "Invalid value"),
        (["incidents", "--limit", "10000"], "Invalid value"),
        (["incidents", "--vlm-verdict", "unverified"], "Invalid value"),
        (
            [
                "average-speed",
                "--source",
                "cam",
                "--start-time",
                "not-a-time",
                "--end-time",
                "2026-01-01T00:01:00Z",
            ],
            "ISO-8601",
        ),
        (
            [
                "average-speed",
                "--source",
                "cam",
                "--start-time",
                "2026-01-01T00:02:00Z",
                "--end-time",
                "2026-01-01T00:01:00Z",
            ],
            "later",
        ),
        (
            [
                "average-speed",
                "--source",
                "cam",
                "--start-time",
                "2026-01-01T00:01:00Z",
                "--end-time",
                "2026-01-01T00:01:00Z",
            ],
            "later",
        ),
        (
            [
                "fov-histogram",
                "--source",
                "cam",
                "--start-time",
                "2026-01-01T00:00:00Z",
                "--end-time",
                "2026-01-01T00:01:00Z",
                "--bucket-count",
                "0",
            ],
            "Invalid value",
        ),
        (
            [
                "analyze",
                "--source",
                "cam",
                "--start-time",
                "2026-01-01T00:00:00Z",
                "--end-time",
                "2026-01-01T00:01:00Z",
                "--analysis-type",
                "unknown",
            ],
            "Invalid value",
        ),
        (["incident"], "Missing option"),
    ],
)
def test_invalid_inputs_fail_before_backend(cli: Any, args: list[str], fragment: str) -> None:
    result = _invoke(cli, args)
    assert result.exit_code == int(Exit.INVALID_INPUT)
    assert fragment in result.output
    assert _Client.calls == []


def test_each_read_shape(cli: Any) -> None:
    common = [
        "--source",
        "cam",
        "--start-time",
        "2026-01-01T00:00:00Z",
        "--end-time",
        "2026-01-01T00:01:00Z",
    ]
    cases = [
        (["incident", "--incident-id", "i-1"], {"id": "i-1"}),
        (["sensors"], {"count": 0, "sensors": []}),
        (
            ["places"],
            {"count": 1, "places": ["building=Warehouse/room=Room-1"]},
        ),
        (["fov-histogram", *common], {"bucketSizeInSec": 5, "histogram": []}),
        (
            ["average-speed", *common],
            {"metrics": [{"direction": "North", "averageSpeed": "25 mph"}]},
        ),
        (
            ["analyze", *common, "--analysis-type", "average-speed"],
            {"analysis_type": "average-speed", "result": {}, "summary": "done"},
        ),
    ]
    for args, expected in cases:
        result = _invoke(cli, args)
        assert result.exit_code == 0
        assert json.loads(result.stdout) == expected


@pytest.mark.parametrize(
    "analysis_type",
    ["max-min-incidents", "average-speed", "avg-num-people", "avg-num-vehicles"],
)
def test_every_analysis_type_reaches_core(cli: Any, analysis_type: str) -> None:
    result = _invoke(
        cli,
        [
            "analyze",
            "--source",
            "cam",
            "--start-time",
            "2026-01-01T00:00:00Z",
            "--end-time",
            "2026-01-01T00:01:00Z",
            "--analysis-type",
            analysis_type,
        ],
    )
    assert result.exit_code == 0
    assert _Client.calls[-1][1]["analysis_type"] == analysis_type


def test_pretty_and_compact_json(cli: Any) -> None:
    compact = _invoke(cli, ["sensors"])
    pretty = _invoke(cli, ["sensors", "--pretty"])
    assert compact.stdout.count("\n") == 1
    assert pretty.stdout.count("\n") > 1
    assert compact.stderr == pretty.stderr == ""


@pytest.mark.parametrize(
    "error,exit_code",
    [
        (pytest.param(Exception("unexpected"), Exit.ERROR, id="unexpected")),
    ],
)
def test_unexpected_failure_is_not_mistaken_for_an_empty_result(cli: Any, error: Exception, exit_code: Exit) -> None:
    _Client.failure = error
    result = CliRunner().invoke(cli, ["incidents"])
    assert result.exit_code == int(exit_code)
    assert result.stdout == ""


def test_typed_operational_failures_use_stderr(cli: Any) -> None:
    from vss_core.analytics import AnalyticsError
    from vss_core.analytics import AnalyticsInvalidInputError
    from vss_core.analytics import AnalyticsNotFoundError
    from vss_core.analytics import AnalyticsTimeoutError

    for error, code in (
        (AnalyticsInvalidInputError("incident list rejected invalid source"), Exit.INVALID_INPUT),
        (AnalyticsError("incident list connection failed"), Exit.BACKEND_UNREACHABLE),
        (AnalyticsNotFoundError("incident 'missing' was not found"), Exit.NOT_FOUND),
        (AnalyticsTimeoutError("incident list timed out"), Exit.TIMEOUT),
    ):
        _Client.failure = error
        args = ["incident", "--incident-id", "missing"] if code == Exit.NOT_FOUND else ["incidents"]
        result = _invoke(cli, args)
        assert result.exit_code == int(code)
        assert "incident" in result.stderr
        assert result.stdout == ""


def test_missing_config_exits_four(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import vss_cli

    monkeypatch.setattr(
        analytics_group,
        "context_from",
        lambda values: Context(
            deployment=None,
            pretty=values.get("pretty"),
            config_error="no deployment configured; run `vss configure --base-url <origin>`",
        ),
    )
    assert vss_cli.main(["analytics", "incidents"]) == int(Exit.CONFIGURATION)
    assert "vss configure" in capsys.readouterr().err


def test_deployment_without_analytics_exits_four(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import vss_cli

    deployment = _Deployment()
    deployment.services = {"vst": object()}
    monkeypatch.setattr(
        analytics_group,
        "context_from",
        lambda values: Context(deployment=deployment, pretty=values.get("pretty")),
    )
    assert vss_cli.main(["analytics", "incidents"]) == int(Exit.CONFIGURATION)
    assert "video_analytics" in capsys.readouterr().err
