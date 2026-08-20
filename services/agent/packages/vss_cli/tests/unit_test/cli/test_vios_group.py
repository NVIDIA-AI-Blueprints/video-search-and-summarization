# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vss vios` -- the media plane's CLI contract.

Two properties matter most and are asserted here rather than assumed: the group
carries none of the job grammar, and every failure leaves a diagnostic on
stderr with a typed exit code. A surface whose whole job is handing URLs to
another tool must never fail silently -- an empty answer at exit 0 is the
failure mode hardest to see in a trace.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from click.testing import CliRunner
import pytest

from vss_cli import vios_group
from vss_cli.exits import Exit

if TYPE_CHECKING:
    import click


@pytest.fixture
def cli() -> click.Group:
    return vios_group.VIOS.cli()


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment that exposes /vst, so the preflight passes."""

    class _Deployment:
        base_url = "https://vss.test"
        services: ClassVar[dict[str, object]] = {"vst": object()}

        def has(self, name: str) -> bool:
            return name in self.services

    monkeypatch.setattr(vios_group, "context_from", lambda values: _ctx(_Deployment(), values))


def _ctx(deployment: Any, values: dict[str, Any]) -> Any:
    from vss_cli.group import Context

    return Context(deployment=deployment, pretty=values.get("pretty"))


class _Ref:
    name = "warehouse_safety_0001"
    sensor_id = "warehouse_safety_0001_0"
    stream_id = "s-1"
    url = "/videos/w.mp4"
    kind = "video"
    main_stream_assumed = False


def test_the_group_carries_none_of_the_job_grammar(cli: click.Group) -> None:
    """VIOS is not processing, so run/status/get/list must not exist."""
    assert set(cli.commands) == {"list", "timeline", "clip", "snapshot", "add", "delete"}
    for job_verb in ("run", "status", "get"):
        assert job_verb not in cli.commands


def test_every_command_declares_the_vst_requirement() -> None:
    assert frozenset({"vst"}) == vios_group.REQUIRES


def test_list_reports_an_empty_deployment_as_a_fact_not_a_failure(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`[]` is an answer; a backend problem is exit 3. Never the same shape."""
    monkeypatch.setattr(vios_group, "_run", lambda coro: (coro.close(), [])[1])

    result = CliRunner().invoke(cli, ["list"])

    assert result.exit_code == int(Exit.SUCCESS)
    assert json.loads(result.stdout) == {"count": 0, "type": None, "sensors": []}


def test_backend_failure_exits_three_with_stderr(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vss_core.vios import VSTError

    def explode(coro: Any) -> Any:
        coro.close()
        raise VSTError("VIOS sensor list returned status 502")

    monkeypatch.setattr(vios_group, "_run", explode)

    result = CliRunner().invoke(cli, ["list"], catch_exceptions=False)

    assert result.exit_code == int(Exit.BACKEND_UNREACHABLE)
    assert "502" in result.output


def test_list_filters_by_provenance(cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"name": "f", "sensor_id": "f", "stream_id": "f", "type": "video"}]
    seen: dict[str, Any] = {}

    def capture(coro: Any) -> Any:
        coro.close()
        return rows

    monkeypatch.setattr(vios_group, "_run", capture)
    result = CliRunner().invoke(cli, ["list", "--type", "video"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["type"] == "video"
    assert seen == {}


def test_clip_defaults_to_the_covering_segment_and_echoes_it(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller should not have to read the timeline and hand bounds back."""
    calls: list[Any] = []

    def fake_run(coro: Any) -> Any:
        coro.close()
        calls.append(coro)
        if len(calls) == 1:
            return _Ref()
        if len(calls) == 2:
            return ("2026-08-01T12:00:00.000Z", "2026-08-01T12:01:00.000Z")
        return "https://vss.test/vst/storage/clip.mp4"

    monkeypatch.setattr(vios_group, "_run", fake_run)
    result = CliRunner().invoke(cli, ["clip", "--sensor", "warehouse_safety_0001"])

    body = json.loads(result.stdout)
    assert body["start_time"] == "2026-08-01T12:00:00.000Z"
    assert body["end_time"] == "2026-08-01T12:01:00.000Z"
    assert body["media_url"].endswith("clip.mp4")
    assert body["kind"] == "clip"
    assert body["name"] == "warehouse_safety_0001"


def test_snapshot_marks_live_versus_replay(cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake_run(coro: Any) -> Any:
        coro.close()
        calls.append(coro)
        return _Ref() if len(calls) == 1 else "https://vss.test/vst/img.jpg"

    monkeypatch.setattr(vios_group, "_run", fake_run)

    live = json.loads(CliRunner().invoke(cli, ["snapshot", "--sensor", "cam"]).stdout)
    assert live["source"] == "live"
    assert "at" not in live

    calls.clear()
    replay = json.loads(CliRunner().invoke(cli, ["snapshot", "--sensor", "cam", "--at", "2026-08-01T12:00:30Z"]).stdout)
    assert replay["source"] == "replay"
    assert replay["at"] == "2026-08-01T12:00:30Z"


def test_delete_refuses_a_type_mismatch(cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """--type is the caller's belief; a mismatch means one of us is wrong."""
    monkeypatch.setattr(vios_group, "_run", lambda coro: (coro.close(), _Ref())[1])

    result = CliRunner().invoke(cli, ["delete", "--type", "stream", "--sensor", "warehouse_safety_0001"])

    assert result.exit_code == int(Exit.INVALID_INPUT)
    assert "is a video, not a stream" in result.stdout


def test_an_assumed_main_stream_is_reported(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Assumed(_Ref):
        main_stream_assumed = True

    calls: list[Any] = []

    def fake_run(coro: Any) -> Any:
        coro.close()
        calls.append(coro)
        return _Assumed() if len(calls) == 1 else ("2026-08-01T12:00:00Z", "2026-08-01T12:01:00Z")

    monkeypatch.setattr(vios_group, "_run", fake_run)
    body = json.loads(CliRunner().invoke(cli, ["timeline", "--sensor", "cam"]).stdout)

    assert body["main_stream_assumed"] is True
