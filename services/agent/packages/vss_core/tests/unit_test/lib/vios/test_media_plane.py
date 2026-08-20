# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Media-plane operations behind `vss vios`.

The cases here are the ones VIOS actually gets wrong in the field: sensorIds
that do not match their names, multi-stream cameras where the substream looks
identical to the main one, and deletes that answer non-200 for a source that is
still registered.
"""

from __future__ import annotations

from typing import Any

import pytest

from vss_core.vios import classify_source
from vss_core.vios import client as vios
from vss_core.vios import validate_media_name


class _Response:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def text(self) -> str:
        import json

        return json.dumps(self._payload) if not isinstance(self._payload, str) else self._payload


class _Session:
    """Serves canned payloads by URL suffix and records what was called."""

    def __init__(self, routes: dict[str, Any], calls: list[str]) -> None:
        self._routes = routes
        self._calls = calls

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def _match(self, url: str, verb: str) -> _Response:
        self._calls.append(f"{verb} {url}")
        for suffix, payload in self._routes.items():
            if suffix in url:
                if isinstance(payload, tuple):
                    return _Response(payload[1], status=payload[0])
                return _Response(payload)
        return _Response({"error_message": "not routed"}, status=404)

    def get(self, url: str, **_kw: object) -> _Response:
        return self._match(url, "GET")

    def delete(self, url: str, **_kw: object) -> _Response:
        return self._match(url, "DELETE")

    def post(self, url: str, **_kw: object) -> _Response:
        return self._match(url, "POST")


@pytest.fixture
def vios_http(monkeypatch: pytest.MonkeyPatch):
    """Install a canned VIOS; returns (set_routes, calls)."""
    calls: list[str] = []
    routes: dict[str, Any] = {}

    monkeypatch.setattr(vios.aiohttp, "ClientSession", lambda **_kw: _Session(routes, calls))

    def configure(**new: Any) -> None:
        routes.update(new)

    return configure, calls, routes


VST = "http://vios.test:30888"


def _routes(sensors: list[dict], streams: dict[str, list[dict]]) -> dict[str, Any]:
    out: dict[str, Any] = {"/sensor/list": sensors}
    for sensor_id, entries in streams.items():
        out[f"/sensor/{sensor_id}/streams"] = entries
    return out


# ---------------------------------------------------------------- provenance


def test_classify_source_reads_the_stream_url() -> None:
    assert classify_source("rtsp://cam.local/stream1") == "stream"
    assert classify_source("rtsps://cam.local/stream1") == "stream"
    assert classify_source("/home/vst/streamer_videos/warehouse.mp4") == "video"


@pytest.mark.parametrize("bad", ["has space.mp4", "", "-leading.mp4", "sl/ash.mp4"])
def test_upload_names_that_vios_would_reject_fail_locally(bad: str) -> None:
    with pytest.raises(vios.VIOSInvalidInputError, match="invalid media name"):
        validate_media_name(bad)


def test_conventional_upload_names_are_accepted() -> None:
    for good in ("warehouse_safety_0001.mp4", "dock-cam.mp4", "clip.2026.mp4"):
        validate_media_name(good)


# ----------------------------------------------------------- name resolution


@pytest.mark.asyncio
async def test_sensor_id_is_read_from_the_listing_not_built_from_the_name(vios_http) -> None:
    """Auto-discovered files carry a `_N` uniqueifier on sensorId but not name.

    Constructing `/sensor/<name>/streams` returns CameraNotFoundError, so the
    id must come from the listing.
    """
    configure, calls, _ = vios_http
    configure(
        **_routes(
            sensors=[{"name": "warehouse_safety_0001", "sensorId": "warehouse_safety_0001_0"}],
            streams={"warehouse_safety_0001_0": [{"streamId": "s-1", "isMain": True, "url": "/videos/w.mp4"}]},
        )
    )

    ref = await vios.resolve_sensor(VST, "warehouse_safety_0001")

    assert ref.sensor_id == "warehouse_safety_0001_0"
    assert ref.stream_id == "s-1"
    assert ref.kind == "video"
    assert any("/sensor/warehouse_safety_0001_0/streams" in c for c in calls)
    assert not any("/sensor/warehouse_safety_0001/streams" in c for c in calls)


@pytest.mark.asyncio
async def test_a_raw_uuid_still_resolves_after_the_name_lookup_misses(vios_http) -> None:
    configure, _, _ = vios_http
    configure(
        **_routes(
            sensors=[{"name": "dock-cam", "sensorId": "0c8f-uuid"}],
            streams={"0c8f-uuid": [{"streamId": "0c8f-uuid", "isMain": True, "url": "rtsp://x/1"}]},
        )
    )

    ref = await vios.resolve_sensor(VST, "0c8f-uuid")
    assert ref.name == "dock-cam"
    assert ref.kind == "stream"


@pytest.mark.asyncio
async def test_duplicate_names_refuse_rather_than_guess(vios_http) -> None:
    configure, _, _ = vios_http
    configure(**_routes(sensors=[{"name": "dup", "sensorId": "a"}, {"name": "dup", "sensorId": "b"}], streams={}))

    with pytest.raises(vios.VIOSInvalidInputError, match="2 sensors are named"):
        await vios.resolve_sensor(VST, "dup")


@pytest.mark.asyncio
async def test_unknown_handle_is_a_not_found(vios_http) -> None:
    configure, _, _ = vios_http
    configure(**_routes(sensors=[{"name": "other", "sensorId": "a"}], streams={}))

    with pytest.raises(vios.VIOSNotFoundError, match="no VIOS sensor named"):
        await vios.resolve_sensor(VST, "absent")


@pytest.mark.asyncio
async def test_main_stream_is_preferred_over_substreams(vios_http) -> None:
    configure, _, _ = vios_http
    configure(
        **_routes(
            sensors=[{"name": "cam", "sensorId": "cam-id"}],
            streams={
                "cam-id": [
                    {"streamId": "sub", "isMain": False, "url": "rtsp://x/sub"},
                    {"streamId": "main", "isMain": True, "url": "rtsp://x/main"},
                ]
            },
        )
    )

    ref = await vios.resolve_sensor(VST, "cam")
    assert ref.stream_id == "main"
    assert ref.main_stream_assumed is False


@pytest.mark.asyncio
async def test_multi_stream_with_no_main_flag_refuses_instead_of_taking_the_first(vios_http) -> None:
    """Resolving to a substream yields degraded frames with no error anywhere."""
    configure, _, _ = vios_http
    configure(
        **_routes(
            sensors=[{"name": "cam", "sensorId": "cam-id"}],
            streams={
                "cam-id": [
                    {"streamId": "a", "url": "rtsp://x/a"},
                    {"streamId": "b", "url": "rtsp://x/b"},
                ]
            },
        )
    )

    with pytest.raises(vios.VIOSInvalidInputError, match="none is flagged isMain"):
        await vios.resolve_sensor(VST, "cam")


@pytest.mark.asyncio
async def test_sole_unflagged_stream_is_used_but_reported(vios_http) -> None:
    configure, _, _ = vios_http
    configure(
        **_routes(
            sensors=[{"name": "cam", "sensorId": "cam-id"}],
            streams={"cam-id": [{"streamId": "only", "url": "/videos/x.mp4"}]},
        )
    )

    ref = await vios.resolve_sensor(VST, "cam")
    assert ref.stream_id == "only"
    assert ref.main_stream_assumed is True


# ------------------------------------------------------------------- listing


@pytest.mark.asyncio
async def test_list_joins_streams_and_filters_by_provenance(vios_http) -> None:
    configure, _, _ = vios_http
    configure(
        **_routes(
            sensors=[
                {"name": "file-one", "sensorId": "f1", "state": "online", "isTimelinePresent": True},
                {"name": "rtsp-one", "sensorId": "r1", "state": "online"},
            ],
            streams={
                "f1": [{"streamId": "f1", "isMain": True, "url": "/videos/one.mp4"}],
                "r1": [{"streamId": "r1", "isMain": True, "url": "rtsp://cam/1"}],
            },
        )
    )

    everything = await vios.list_media(VST)
    assert {row["type"] for row in everything} == {"video", "stream"}
    assert everything[0]["has_timeline"] is True

    assert [row["name"] for row in await vios.list_media(VST, kind="video")] == ["file-one"]
    assert [row["name"] for row in await vios.list_media(VST, kind="stream")] == ["rtsp-one"]


# ------------------------------------------------------------------ snapshot


@pytest.mark.asyncio
async def test_snapshot_without_at_is_live_and_with_at_is_replay(vios_http) -> None:
    configure, calls, _ = vios_http
    configure(**{"/picture/url": {"imageUrl": "http://vios/img.jpg"}})

    assert await vios.get_snapshot_url(VST, "s-1") == "http://vios/img.jpg"
    assert "/live/stream/s-1/picture/url" in calls[-1]

    await vios.get_snapshot_url(VST, "s-1", at="2026-08-01T12:00:00Z")
    assert "/replay/stream/s-1/picture/url" in calls[-1]
    assert "startTime=2026-08-01T12%3A00%3A00Z" in calls[-1]


# -------------------------------------------------------------------- delete


@pytest.mark.asyncio
async def test_absence_is_confirmed_by_name_not_by_sensor_id(vios_http) -> None:
    """The fail-open case: VIOS drops the UUID but still lists the name."""
    configure, _, _ = vios_http
    configure(**{"/sensor/list": [{"name": "ghost", "sensorId": "ghost_0"}]})

    with pytest.raises(vios.VSTError, match="still lists 'ghost' after delete"):
        await vios.confirm_absent(VST, "ghost")


@pytest.mark.asyncio
async def test_confirm_absent_passes_when_the_name_is_gone(vios_http) -> None:
    configure, _, _ = vios_http
    configure(**{"/sensor/list": [{"name": "someone-else", "sensorId": "x"}]})

    await vios.confirm_absent(VST, "ghost")


@pytest.mark.asyncio
async def test_deleting_an_uploaded_file_skips_the_sensor_call(vios_http, monkeypatch) -> None:
    configure, calls, _ = vios_http
    configure(**{"/sensor/list": [], "/storage/file/": (200, {})})
    monkeypatch.setattr(vios, "get_timelines_map", _fake_spans)

    ref = vios.SensorRef(name="w", sensor_id="w_0", stream_id="w-stream", url="/videos/w.mp4", kind="video")
    result = await vios.delete_media(VST, ref)

    assert result["deleted"] == ["storage"]
    assert not any("DELETE" in c and "/sensor/w_0" in c for c in calls)


@pytest.mark.asyncio
async def test_deleting_an_rtsp_sensor_stops_recording_then_reclaims_storage(vios_http, monkeypatch) -> None:
    configure, calls, _ = vios_http
    configure(**{"/sensor/list": [], "/sensor/r1": (200, {}), "/storage/file/": (200, {})})
    monkeypatch.setattr(vios, "get_timelines_map", _fake_spans)

    ref = vios.SensorRef(name="r", sensor_id="r1", stream_id="r-stream", url="rtsp://c/1", kind="stream")
    result = await vios.delete_media(VST, ref)

    assert result["deleted"] == ["sensor", "storage"]
    order = [c for c in calls if c.startswith("DELETE")]
    assert "/sensor/r1" in order[0]
    assert "/storage/file/" in order[1]


@pytest.mark.asyncio
async def test_delete_treats_404_as_the_goal_state(vios_http, monkeypatch) -> None:
    """Storage deletion can cascade the registration away before the paired call."""
    configure, _, _ = vios_http
    configure(**{"/sensor/list": [], "/sensor/r1": (404, {}), "/storage/file/": (404, {})})
    monkeypatch.setattr(vios, "get_timelines_map", _fake_spans)

    ref = vios.SensorRef(name="r", sensor_id="r1", stream_id="r-stream", url="rtsp://c/1", kind="stream")
    assert (await vios.delete_media(VST, ref))["confirmed"] is True


async def _fake_spans(*_args: object, **_kw: object) -> dict[str, tuple[str, str]]:
    return {
        "w-stream": ("2026-08-01T12:00:00.000Z", "2026-08-01T12:01:00.000Z"),
        "r-stream": ("2026-08-01T12:00:00.000Z", "2026-08-01T12:01:00.000Z"),
    }


# ------------------------------------------- regressions found in code review


@pytest.mark.asyncio
async def test_delete_removes_every_recorded_segment_not_just_the_first(vios_http, monkeypatch) -> None:
    """A burst-recorded stream must not keep everything after segment one."""
    configure, calls, _ = vios_http
    configure(**{"/sensor/list": [], "/sensor/r1": (200, {}), "/storage/file/": (200, {})})

    async def spans(*_a: object, **_k: object) -> dict[str, tuple[str, str]]:
        return {"r-stream": ("2026-08-01T12:00:00.000Z", "2026-08-01T18:30:00.000Z")}

    monkeypatch.setattr(vios, "get_timelines_map", spans)

    ref = vios.SensorRef(name="r", sensor_id="r1", stream_id="r-stream", url="rtsp://c/1", kind="stream")
    result = await vios.delete_media(VST, ref)

    storage = next(c for c in calls if c.startswith("DELETE") and "/storage/file/" in c)
    assert "startTime=2026-08-01T12%3A00%3A00.000Z" in storage
    assert "endTime=2026-08-01T18%3A30%3A00.000Z" in storage
    assert result["recordings"] == "removed"


@pytest.mark.asyncio
async def test_delete_propagates_a_timeline_read_failure(vios_http, monkeypatch) -> None:
    """ "Could not read the timelines" must never be reported as a clean delete."""
    configure, _, _ = vios_http
    configure(**{"/sensor/list": [], "/sensor/r1": (200, {}), "/storage/file/": (200, {})})

    async def boom(*_a: object, **_k: object) -> dict[str, tuple[str, str]]:
        raise vios.VSTError("VIOS timelines API returned status 503")

    monkeypatch.setattr(vios, "get_timelines_map", boom)

    ref = vios.SensorRef(name="r", sensor_id="r1", stream_id="r-stream", url="rtsp://c/1", kind="stream")
    with pytest.raises(vios.VSTError, match="503"):
        await vios.delete_media(VST, ref)


@pytest.mark.asyncio
async def test_delete_says_plainly_when_there_was_nothing_recorded(vios_http, monkeypatch) -> None:
    configure, calls, _ = vios_http
    configure(**{"/sensor/list": [], "/sensor/r1": (200, {})})

    async def empty(*_a: object, **_k: object) -> dict[str, tuple[str, str]]:
        return {}

    monkeypatch.setattr(vios, "get_timelines_map", empty)

    ref = vios.SensorRef(name="r", sensor_id="r1", stream_id="r-stream", url="rtsp://c/1", kind="stream")
    result = await vios.delete_media(VST, ref)

    assert result["recordings"] == "none"
    assert result["deleted"] == ["sensor"]
    assert not any("/storage/file/" in c for c in calls if c.startswith("DELETE"))


@pytest.mark.asyncio
async def test_list_fails_rather_than_reporting_a_short_list(vios_http) -> None:
    """A streams outage must not read as "this deployment has no sensors"."""
    configure, _, _ = vios_http
    configure(**{"/sensor/list": [{"name": "cam", "sensorId": "cam-id"}], "/sensor/cam-id/streams": (503, {})})

    with pytest.raises(vios.VSTError, match="503"):
        await vios.list_media(VST)


@pytest.mark.asyncio
async def test_a_sensor_with_no_id_is_reported_not_dropped(vios_http) -> None:
    configure, _, _ = vios_http
    configure(**{"/sensor/list": [{"name": "orphan", "sensorId": ""}]})

    rows = await vios.list_media(VST)

    assert len(rows) == 1
    assert rows[0]["error"] == "VIOS reported no sensorId"


def test_a_missing_url_is_unknown_provenance_not_video() -> None:
    """Guessing "video" would send delete down the wrong teardown flow."""
    assert classify_source("") == "unknown"


@pytest.mark.asyncio
async def test_a_stream_id_resolves_when_the_name_and_sensor_id_both_miss(vios_http) -> None:
    """_pick_stream tells callers to address a stream explicitly; honour it."""
    configure, _, _ = vios_http
    configure(
        **_routes(
            sensors=[{"name": "cam", "sensorId": "cam-id"}],
            streams={
                "cam-id": [
                    {"streamId": "sub-a", "url": "rtsp://x/a"},
                    {"streamId": "sub-b", "url": "rtsp://x/b"},
                ]
            },
        )
    )

    ref = await vios.resolve_sensor(VST, "sub-b")

    assert ref.stream_id == "sub-b"
    assert ref.name == "cam"


@pytest.mark.asyncio
async def test_ambiguity_and_absence_carry_different_error_types(vios_http) -> None:
    """Exit 2 for "you were ambiguous", exit 5 for "it is not here"."""
    configure, _, routes = vios_http
    configure(**_routes(sensors=[{"name": "dup", "sensorId": "a"}, {"name": "dup", "sensorId": "b"}], streams={}))
    with pytest.raises(vios.VIOSInvalidInputError):
        await vios.resolve_sensor(VST, "dup")

    routes.clear()
    configure(**{"/sensor/list": []})
    with pytest.raises(vios.VIOSNotFoundError):
        await vios.resolve_sensor(VST, "absent")


@pytest.mark.asyncio
async def test_add_stream_recovers_the_id_when_vios_omits_it(vios_http) -> None:
    """The sensor exists; retrying on a false failure would duplicate it."""
    configure, _, _ = vios_http
    configure(
        **{
            "/sensor/add": {},
            "/sensor/list": [{"name": "dock", "sensorId": "dock-uuid"}],
            "/sensor/dock-uuid/streams": [{"streamId": "dock-uuid", "isMain": True, "url": "rtsp://c/1"}],
        }
    )

    assert await vios.add_stream(VST, "rtsp://c/1", "dock") == "dock-uuid"


def test_a_bad_filename_is_a_caller_error_not_a_backend_outage() -> None:
    with pytest.raises(vios.VIOSInvalidInputError):
        validate_media_name("has space.mp4")
