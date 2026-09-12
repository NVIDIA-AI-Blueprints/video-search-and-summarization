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


def test_list_passes_the_requested_type_through_to_the_library(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assert the filter reaches list_media, not just that the flag parses."""
    seen: dict[str, Any] = {}

    def capture(origin: str, kind: str | None = None, *_a: Any, **_kw: Any) -> Any:
        seen["origin"], seen["kind"] = origin, kind

        async def _rows() -> list[dict[str, Any]]:
            return [{"name": "f", "sensor_id": "f", "stream_id": "f", "type": "video"}]

        return _rows()

    from vss_core import vios as vios_lib

    monkeypatch.setattr(vios_lib, "list_media", capture)
    monkeypatch.setattr(vios_group, "_run", lambda coro: (coro.close(), [{"name": "f", "type": "video"}])[1])

    result = CliRunner().invoke(cli, ["list", "--type", "video"])

    assert result.exit_code == 0
    assert seen["kind"] == "video"
    assert json.loads(result.stdout)["type"] == "video"


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
            return [("2026-08-01T12:00:00.000Z", "2026-08-01T12:01:00.000Z")]
        return "https://vss.test/vst/storage/clip.mp4"

    monkeypatch.setattr(vios_group, "_run", fake_run)
    result = CliRunner().invoke(cli, ["clip", "--sensor", "warehouse_safety_0001"])

    body = json.loads(result.stdout)
    assert body["start_time"] == "2026-08-01T12:00:00.000Z"
    assert body["end_time"] == "2026-08-01T12:01:00.000Z"
    assert body["media_url"].endswith("clip.mp4")
    assert body["kind"] == "clip"
    assert body["name"] == "warehouse_safety_0001"


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
        return _Assumed() if len(calls) == 1 else [("2026-08-01T12:00:00Z", "2026-08-01T12:01:00Z")]

    monkeypatch.setattr(vios_group, "_run", fake_run)
    body = json.loads(CliRunner().invoke(cli, ["timeline", "--sensor", "cam"]).stdout)

    assert body["main_stream_assumed"] is True


def test_timeline_reports_every_recorded_segment(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reporting only the first segment understates what is on disk."""
    calls: list[Any] = []

    def fake_run(coro: Any) -> Any:
        coro.close()
        calls.append(coro)
        if len(calls) == 1:
            return _Ref()
        return [("2026-08-01T12:00:00Z", "2026-08-01T18:30:00Z")]

    monkeypatch.setattr(vios_group, "_run", fake_run)
    body = json.loads(CliRunner().invoke(cli, ["timeline", "--sensor", "cam"]).stdout)

    assert body["recorded"] is True
    assert body["segments"] == [{"start_time": "2026-08-01T12:00:00Z", "end_time": "2026-08-01T18:30:00Z"}]


def test_timeline_says_so_when_nothing_was_recorded(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    def fake_run(coro: Any) -> Any:
        coro.close()
        calls.append(coro)
        return _Ref() if len(calls) == 1 else []

    monkeypatch.setattr(vios_group, "_run", fake_run)
    body = json.loads(CliRunner().invoke(cli, ["timeline", "--sensor", "cam"]).stdout)

    assert body["recorded"] is False
    assert body["segments"] == []


def test_delete_refuses_an_unknown_provenance(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VIOS gave no url, so neither teardown flow is known to be right."""

    class _Unknown(_Ref):
        kind = "unknown"
        url = ""

    monkeypatch.setattr(vios_group, "_run", lambda coro: (coro.close(), _Unknown())[1])

    result = CliRunner().invoke(cli, ["delete", "--type", "video", "--sensor", "cam"])

    assert result.exit_code == int(Exit.INVALID_INPUT)
    assert "provenance is unknown" in result.stdout


def test_add_derives_provenance_and_does_not_require_type(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vios_group, "_run", lambda coro: (coro.close(), "rtsp-sensor-id")[1])

    result = CliRunner().invoke(cli, ["add", "rtsp://cam.local/stream1", "--name", "dock-cam"])

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["type"] == "stream"
    assert body["name"] == "dock-cam"


def test_add_refuses_a_type_that_contradicts_the_source(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--type is an optional check, so a mismatch is the caller's error."""
    result = CliRunner().invoke(cli, ["add", "rtsp://cam.local/s1", "--type", "video"])

    assert result.exit_code == int(Exit.INVALID_INPUT)
    assert "is a stream source, not a video" in result.stdout


def test_snapshot_of_an_uploaded_file_replays_instead_of_asking_for_a_live_frame(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VIOS answers 400 for a live frame on a file-backed sensor.

    We know the provenance already, so the request is never sent.
    """
    calls: list[Any] = []

    def fake_run(coro: Any) -> Any:
        coro.close()
        calls.append(coro)
        if len(calls) == 1:
            return _Ref()  # kind == "video"
        if len(calls) == 2:
            return ("2025-01-01T00:00:00.000Z", "2025-01-01T00:03:30.000Z")
        return "https://vss.test/vst/img.jpg"

    monkeypatch.setattr(vios_group, "_run", fake_run)
    body = json.loads(CliRunner().invoke(cli, ["snapshot", "--sensor", "warehouse_safety_0001"]).stdout)

    assert body["source"] == "replay"
    assert body["at"] == "2025-01-01T00:00:00.000Z"


def test_snapshot_says_so_when_an_upload_has_nothing_recorded_yet(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    def fake_run(coro: Any) -> Any:
        coro.close()
        calls.append(coro)
        return _Ref() if len(calls) == 1 else None

    monkeypatch.setattr(vios_group, "_run", fake_run)
    result = CliRunner().invoke(cli, ["snapshot", "--sensor", "warehouse_safety_0001"])

    assert result.exit_code == int(Exit.NOT_FOUND)
    assert "neither a live frame nor one to replay" in result.stdout


def test_snapshot_of_an_rtsp_sensor_is_still_live(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Cam(_Ref):
        kind = "stream"
        url = "rtsp://cam/1"

    calls: list[Any] = []

    def fake_run(coro: Any) -> Any:
        coro.close()
        calls.append(coro)
        return _Cam() if len(calls) == 1 else "https://vss.test/vst/img.jpg"

    monkeypatch.setattr(vios_group, "_run", fake_run)
    body = json.loads(CliRunner().invoke(cli, ["snapshot", "--sensor", "dock-cam"]).stdout)

    assert body["source"] == "live"
    assert "at" not in body


def test_snapshot_of_an_unclassifiable_sensor_does_not_ask_for_a_live_frame(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only RTSP has a live frame; "unknown" is equally not live."""

    class _Unknown(_Ref):
        kind = "unknown"
        url = ""

    calls: list[Any] = []

    def fake_run(coro: Any) -> Any:
        coro.close()
        calls.append(coro)
        if len(calls) == 1:
            return _Unknown()
        if len(calls) == 2:
            return ("2025-01-01T00:00:00.000Z", "2025-01-01T00:03:30.000Z")
        return "https://vss.test/vst/img.jpg"

    monkeypatch.setattr(vios_group, "_run", fake_run)
    body = json.loads(CliRunner().invoke(cli, ["snapshot", "--sensor", "orphan"]).stdout)

    assert body["source"] == "replay"
    assert body["at"] == "2025-01-01T00:00:00.000Z"


def test_add_waits_for_indexing_before_reporting_success(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The PUT returning is not the upload being usable."""
    calls: list[Any] = []

    def fake_run(coro: Any) -> Any:
        coro.close()
        calls.append(coro)
        if len(calls) == 1:
            return {"filename": "clip.mp4", "sensorId": "u", "streamId": "u", "bytes": 1}
        return ("2025-01-01T00:00:00.000Z", "2025-01-01T00:03:30.000Z")

    monkeypatch.setattr(vios_group, "_run", fake_run)
    local = tmp_path / "clip.mp4"
    local.write_bytes(b"x")

    body = json.loads(CliRunner().invoke(cli, ["add", "--type", "video", str(local)]).stdout)

    assert len(calls) == 2, "upload then wait"
    assert body["recorded"]["start_time"] == "2025-01-01T00:00:00.000Z"


def test_add_exits_seven_when_vios_never_indexes(
    cli: click.Group, configured: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Reporting a sensor that cannot be used is the fail-open this group keeps hitting."""
    from vss_core.vios import VIOSTimeoutError

    calls: list[Any] = []

    def fake_run(coro: Any) -> Any:
        coro.close()
        calls.append(coro)
        if len(calls) == 1:
            return {"filename": "clip.mp4", "sensorId": "u", "streamId": "u"}
        raise VIOSTimeoutError("VIOS accepted the upload but had not indexed u after 60s")

    monkeypatch.setattr(vios_group, "_run", fake_run)
    local = tmp_path / "clip.mp4"
    local.write_bytes(b"x")

    result = CliRunner().invoke(cli, ["add", "--type", "video", str(local)], catch_exceptions=False)

    assert result.exit_code == int(Exit.TIMEOUT)
    assert "had not indexed" in result.output
