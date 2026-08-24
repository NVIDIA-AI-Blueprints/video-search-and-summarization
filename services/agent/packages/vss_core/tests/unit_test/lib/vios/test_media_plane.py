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
    def __init__(self, payload: Any, status: int = 200, content_length: int | None = None) -> None:
        self._payload = payload
        self.status = status
        #: None models a chunked source, which is the case upload_from_url refuses.
        self.content_length = content_length

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

    def put(self, url: str, **_kw: object) -> _Response:
        return self._match(url, "PUT")


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
    # Streams route cleanly and simply do not carry the handle: a completed
    # search that found nothing, as distinct from a search VIOS could not serve.
    configure(**_routes(sensors=[{"name": "other", "sensorId": "a"}], streams={"a": []}))

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
    # A camera's source is its RTSP address; an upload's would be a container
    # path that only repeats `name`, so it is omitted rather than misleading.
    video_row = next(r for r in everything if r["type"] == "video")
    stream_row = next(r for r in everything if r["type"] == "stream")
    assert "source" not in video_row
    assert stream_row["source"] == "rtsp://cam/1"

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
    monkeypatch.setattr(vios, "recorded_span", _fake_span)

    ref = vios.SensorRef(name="w", sensor_id="w_0", stream_id="w-stream", url="/videos/w.mp4", kind="video")
    result = await vios.delete_media(VST, ref)

    assert result["deleted"] == ["storage"]
    assert not any("DELETE" in c and "/sensor/w_0" in c for c in calls)


@pytest.mark.asyncio
async def test_deleting_an_rtsp_sensor_stops_recording_then_reclaims_storage(vios_http, monkeypatch) -> None:
    configure, calls, _ = vios_http
    configure(**{"/sensor/list": [], "/sensor/r1": (200, {}), "/storage/file/": (200, {})})
    monkeypatch.setattr(vios, "recorded_span", _fake_span)

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
    monkeypatch.setattr(vios, "recorded_span", _fake_span)

    ref = vios.SensorRef(name="r", sensor_id="r1", stream_id="r-stream", url="rtsp://c/1", kind="stream")
    assert (await vios.delete_media(VST, ref))["confirmed"] is True


async def _fake_span(*_args: object, **_kw: object) -> tuple[str, str]:
    return "2026-08-01T12:00:00.000Z", "2026-08-01T12:01:00.000Z"


# ------------------------------------------- regressions found in code review


@pytest.mark.asyncio
async def test_delete_removes_every_recorded_segment_not_just_the_first(vios_http, monkeypatch) -> None:
    """A burst-recorded stream must not keep everything after segment one."""
    configure, calls, _ = vios_http
    configure(**{"/sensor/list": [], "/sensor/r1": (200, {}), "/storage/file/": (200, {})})

    async def span(*_a: object, **_k: object) -> tuple[str, str]:
        return "2026-08-01T12:00:00.000Z", "2026-08-01T18:30:00.000Z"

    monkeypatch.setattr(vios, "recorded_span", span)

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

    async def boom(*_a: object, **_k: object) -> tuple[str, str]:
        raise vios.VSTError("VIOS timelines API returned status 503")

    monkeypatch.setattr(vios, "recorded_span", boom)

    ref = vios.SensorRef(name="r", sensor_id="r1", stream_id="r-stream", url="rtsp://c/1", kind="stream")
    with pytest.raises(vios.VSTError, match="503"):
        await vios.delete_media(VST, ref)


@pytest.mark.asyncio
async def test_delete_says_plainly_when_there_was_nothing_recorded(vios_http, monkeypatch) -> None:
    configure, calls, _ = vios_http
    configure(**{"/sensor/list": [], "/sensor/r1": (200, {})})

    async def empty(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(vios, "recorded_span", empty)

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


# ------------------------------- second review round (Greptile) regressions


@pytest.mark.asyncio
async def test_a_malformed_timeline_payload_does_not_confirm_a_clean_delete(vios_http) -> None:
    """HTTP 200 with unusable segments is not the same as "nothing recorded"."""
    configure, _, _ = vios_http
    configure(**{"/storage/timelines": {"r-stream": [{"note": "no times here"}]}})

    with pytest.raises(vios.VSTError, match="without a usable start and end"):
        await vios.recorded_span(VST, "r-stream")


@pytest.mark.asyncio
async def test_a_non_dict_timeline_payload_is_an_error(vios_http) -> None:
    configure, _, _ = vios_http
    configure(**{"/storage/timelines": ["not", "a", "map"]})

    with pytest.raises(vios.VSTError, match="Unexpected timelines response shape"):
        await vios.recorded_span(VST, "r-stream")


@pytest.mark.asyncio
async def test_a_stream_absent_from_the_listing_genuinely_has_no_recordings(vios_http) -> None:
    configure, _, _ = vios_http
    configure(**{"/storage/timelines": {"someone-else": [{"startTime": "a", "endTime": "b"}]}})

    assert await vios.recorded_span(VST, "r-stream") is None


@pytest.mark.asyncio
async def test_recorded_span_covers_every_segment(vios_http) -> None:
    configure, _, _ = vios_http
    configure(
        **{
            "/storage/timelines": {
                "r-stream": [
                    {"startTime": "2026-08-01T12:00:00.000Z", "endTime": "2026-08-01T12:30:00.000Z"},
                    {"startTime": "2026-08-01T17:00:00.000Z", "endTime": "2026-08-01T18:30:00.000Z"},
                ]
            }
        }
    )

    assert await vios.recorded_span(VST, "r-stream") == (
        "2026-08-01T12:00:00.000Z",
        "2026-08-01T18:30:00.000Z",
    )


@pytest.mark.asyncio
async def test_a_failed_stream_scan_is_a_backend_error_not_a_missing_sensor(vios_http) -> None:
    """If VIOS could not answer, "no such sensor" is a lie the caller acts on."""
    configure, _, _ = vios_http
    configure(**{"/sensor/list": [{"name": "cam", "sensorId": "cam-id"}], "/sensor/cam-id/streams": (503, {})})

    with pytest.raises(vios.VSTError, match="503"):
        await vios.resolve_sensor(VST, "some-stream-id")


# ---------------------------------------- third review round (pane + Codex)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # VIOS 3.2.0 can double the scheme; the host is unreachable from the VLM.
        ("http://http://localhost:30888/storage/temp/clip.mp4?t=a", "https://vss.test/vst/storage/temp/clip.mp4?t=a"),
        ("/storage/temp/clip.mp4", "https://vss.test/vst/storage/temp/clip.mp4"),
        ("http://localhost:30888/vst/storage/x.mp4", "https://vss.test/vst/storage/x.mp4"),
        ("https://vss.test/vst/storage/y.mp4", "https://vss.test/vst/storage/y.mp4"),
    ],
)
def test_media_urls_are_re_anchored_on_the_reachable_origin(raw: str, expected: str) -> None:
    assert vios.normalise_media_url(raw, "https://vss.test") == expected


def test_an_empty_media_url_is_an_error_not_a_handle() -> None:
    with pytest.raises(vios.VSTError, match="empty media url"):
        vios.normalise_media_url("", "https://vss.test")


@pytest.mark.asyncio
async def test_deleting_an_uploaded_file_whose_timeline_never_appears_is_not_confirmed(vios_http, monkeypatch) -> None:
    """The bytes outlive the registration, so this must not read as a clean delete.

    The storage delete is keyed on the timeline, and an uploaded file exists on
    disk whether or not one has been indexed. Deregistering the sensor and
    reporting success would leave the file with nothing pointing at it.
    """
    configure, calls, _ = vios_http
    configure(**{"/sensor/list": [{"name": "w", "sensorId": "w_0"}], "/sensor/w_0": (200, {})})

    async def no_span(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(vios, "recorded_span", no_span)
    # Expire the wait immediately: stubbing sleep alone leaves the loop spinning
    # against a real 15s deadline.
    monkeypatch.setattr(vios, "_DELETE_TIMELINE_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(vios.asyncio, "sleep", _no_sleep)

    async def absent_after_delete(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(vios, "confirm_absent", absent_after_delete)

    ref = vios.SensorRef(name="w", sensor_id="w_0", stream_id="w-stream", url="/videos/w.mp4", kind="video")
    result = await vios.delete_media(VST, ref)

    # Still deregistered: leaving it listed would be worse.
    assert result["deleted"] == ["sensor"]
    assert any(c.startswith("DELETE") and "/sensor/w_0" in c for c in calls)
    # But not claimed as clean, and no storage delete was possible.
    assert result["recordings"] == "unconfirmed"
    assert result["confirmed"] is False
    assert not any(c.startswith("DELETE") and "/storage/" in c for c in calls)


@pytest.mark.asyncio
async def test_deleting_a_stream_with_no_recordings_is_still_clean(vios_http, monkeypatch) -> None:
    """A stream that recorded nothing has no stored bytes -- "none" is the truth."""
    configure, _, _ = vios_http
    configure(**{"/sensor/list": [{"name": "cam", "sensorId": "cam_0"}], "/sensor/cam_0": (200, {})})

    async def no_span(*_a: object, **_k: object) -> None:
        return None

    async def absent(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(vios, "recorded_span", no_span)
    monkeypatch.setattr(vios, "confirm_absent", absent)

    ref = vios.SensorRef(name="cam", sensor_id="cam_0", stream_id="cam-1", url="rtsp://c/1", kind="stream")
    result = await vios.delete_media(VST, ref)

    assert result["recordings"] == "none"
    assert result["confirmed"] is True


@pytest.mark.asyncio
async def test_a_segment_missing_one_end_is_rejected_not_partially_used(vios_http) -> None:
    """Collecting starts and ends independently would invent a span."""
    configure, _, _ = vios_http
    configure(
        **{
            "/storage/timelines": {
                "r-stream": [
                    {"startTime": "2026-08-01T12:00:00Z", "endTime": "2026-08-01T12:30:00Z"},
                    {"startTime": "2026-08-01T17:00:00Z"},
                ]
            }
        }
    )

    with pytest.raises(vios.VSTError, match="without a usable start and end"):
        await vios.recorded_span(VST, "r-stream")


@pytest.mark.asyncio
async def test_two_half_segments_do_not_combine_into_an_invented_span(vios_http) -> None:
    configure, _, _ = vios_http
    configure(**{"/storage/timelines": {"r-stream": [{"startTime": "10"}, {"endTime": "11"}]}})

    with pytest.raises(vios.VSTError, match="without a usable start and end"):
        await vios.recorded_span(VST, "r-stream")


@pytest.mark.asyncio
async def test_an_unclassifiable_sensor_stays_visible_under_every_filter(vios_http) -> None:
    """The filtered list is what ask-video checks before uploading.

    Hiding a sensor there answers "does this name exist" with a wrong no, and
    the caller creates a duplicate.
    """
    configure, _, _ = vios_http
    configure(**{"/sensor/list": [{"name": "orphan", "sensorId": ""}]})

    for kind in (None, "video", "stream"):
        rows = await vios.list_media(VST, kind=kind)
        assert [r["name"] for r in rows] == ["orphan"], kind
        assert rows[0]["type"] == "unknown"
        assert rows[0]["error"]


@pytest.mark.asyncio
async def test_an_upload_name_conflict_is_a_caller_error(vios_http, tmp_path) -> None:
    """409 is deterministic; retrying it is never the right response."""
    configure, _, _ = vios_http
    configure(**{"/storage/file/": (409, {"error_message": "File already exists"})})
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")

    with pytest.raises(vios.VIOSInvalidInputError, match="already holds a file"):
        await vios.upload_media(VST, media)


# ----------------------------------------------- add: derive, do not restate


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("rtsp://cam.local/stream1", "stream"),
        ("RTSP://CAM.LOCAL/stream1", "stream"),
        ("rtsps://cam.local/stream1", "stream"),
        ("./warehouse_safety_0001.mp4", "video"),
        ("/abs/path/clip.mp4", "video"),
        ("https://example.com/clip.mp4", "video"),
    ],
)
def test_an_add_source_says_what_it_is(source: str, expected: str) -> None:
    assert vios.classify_media_source(source) == expected


@pytest.mark.asyncio
async def test_an_upload_can_be_stored_under_a_different_name(vios_http, tmp_path) -> None:
    """The stored filename becomes the sensor name, so it must be nameable."""
    configure, calls, _ = vios_http
    configure(**{"/storage/file/": {"filename": "warehouse_safety_0002.mp4", "sensorId": "u"}})
    local = tmp_path / "clip (1).mp4"
    local.write_bytes(b"x")

    await vios.upload_media(VST, local, name="warehouse_safety_0002.mp4")

    put = next(c for c in calls if c.startswith("PUT"))
    assert "warehouse_safety_0002.mp4" in put
    assert "clip" not in put


@pytest.mark.asyncio
async def test_a_bad_explicit_name_is_rejected_before_the_upload(vios_http, tmp_path) -> None:
    configure, calls, _ = vios_http
    configure(**{"/storage/file/": {}})
    local = tmp_path / "fine.mp4"
    local.write_bytes(b"x")

    with pytest.raises(vios.VIOSInvalidInputError, match="invalid media name"):
        await vios.upload_media(VST, local, name="has space.mp4")
    assert not calls


# ------------------------------------------------------- window validation
#
# VIOS answers a bad window with a bare HTTP 400 naming neither the offending
# bound nor the range that was available, so every one of these is caught here.

SPAN = ("2025-01-01T00:00:00.000Z", "2025-01-01T00:03:30.000Z")


def test_a_recorded_file_defaults_to_its_whole_recording() -> None:
    assert vios.resolve_window([SPAN], None, None, "video") == SPAN


def test_either_bound_may_be_omitted_for_a_file() -> None:
    start, end = vios.resolve_window([SPAN], "2025-01-01T00:01:00.000Z", None, "video")
    assert (start, end) == ("2025-01-01T00:01:00.000Z", SPAN[1])

    start, end = vios.resolve_window([SPAN], None, "2025-01-01T00:01:00.000Z", "video")
    assert (start, end) == (SPAN[0], "2025-01-01T00:01:00.000Z")


def test_a_file_accepts_second_offsets() -> None:
    """ "Ten seconds in" is the natural way to talk about a file."""
    assert vios.resolve_window([SPAN], "10", "20", "video") == (
        "2025-01-01T00:00:10.000Z",
        "2025-01-01T00:00:20.000Z",
    )
    assert vios.resolve_window([SPAN], "0", "30.5", "video")[1] == "2025-01-01T00:00:30.500Z"


def test_a_malformed_timestamp_is_caught_before_vios_sees_it() -> None:
    """The reported case: `--start-time 2025-01-0Z` produced a bare HTTP 400."""
    with pytest.raises(vios.VIOSInvalidInputError, match="not an ISO-8601 timestamp"):
        vios.resolve_window([SPAN], "2025-01-0Z", None, "video")


def test_start_after_end_is_refused() -> None:
    with pytest.raises(vios.VIOSInvalidInputError, match="is after --end-time"):
        vios.resolve_window([SPAN], "20", "10", "video")


def test_a_start_before_the_recording_is_refused_and_names_the_range() -> None:
    with pytest.raises(vios.VIOSInvalidInputError, match="before the recording starts"):
        vios.resolve_window([SPAN], "2024-12-31T23:59:00.000Z", None, "video")


def test_an_end_past_the_recording_is_refused_and_names_the_range() -> None:
    with pytest.raises(vios.VIOSInvalidInputError, match="after the recording ends") as exc:
        vios.resolve_window([SPAN], None, "9999", "video")
    assert SPAN[1] in str(exc.value)


def test_a_negative_offset_is_refused() -> None:
    with pytest.raises(vios.VIOSInvalidInputError, match="negative"):
        vios.resolve_window([SPAN], "-5", None, "video")


def test_a_live_stream_must_state_both_bounds() -> None:
    with pytest.raises(vios.VIOSInvalidInputError, match="no default window"):
        vios.resolve_window([SPAN], None, None, "stream")
    with pytest.raises(vios.VIOSInvalidInputError, match="no default window"):
        vios.resolve_window([SPAN], "2025-01-01T00:00:00.000Z", None, "stream")


def test_a_live_stream_refuses_a_second_offset() -> None:
    """There is no natural zero to count seconds from on a live stream."""
    with pytest.raises(vios.VIOSInvalidInputError, match="only means something for a recorded file"):
        vios.resolve_window([SPAN], "10", "20", "stream")


def test_a_stream_window_inside_the_recording_is_accepted() -> None:
    assert vios.resolve_window([SPAN], SPAN[0], SPAN[1], "stream") == SPAN


@pytest.mark.asyncio
async def test_a_registered_sensor_with_no_streams_is_still_listed(vios_http) -> None:
    """Vanishing from a successful listing reads as "not registered".

    vss-ask-video lists, and uploads when the name is absent — so a sensor
    dropped here becomes a duplicate upload and a 409.
    """
    configure, _, _ = vios_http
    configure(**_routes(sensors=[{"name": "stream-less", "sensorId": "s0", "state": "offline"}], streams={"s0": []}))

    rows = await vios.list_media(VST)

    assert [r["name"] for r in rows] == ["stream-less"]
    assert rows[0]["type"] == "unknown"
    assert rows[0]["error"]


@pytest.mark.asyncio
async def test_a_stream_less_sensor_stays_visible_under_a_filter(vios_http) -> None:
    configure, _, _ = vios_http
    configure(**_routes(sensors=[{"name": "stream-less", "sensorId": "s0"}], streams={"s0": []}))

    rows = await vios.list_media(VST, kind="video")
    assert [r["name"] for r in rows] == ["stream-less"]
    assert rows[0]["error"]


@pytest.mark.asyncio
async def test_a_filter_still_excludes_the_other_provenance(vios_http) -> None:
    """Visible-when-unclassifiable must not become "the filter does nothing"."""
    configure, _, _ = vios_http
    configure(
        **_routes(
            sensors=[{"name": "f", "sensorId": "f"}, {"name": "r", "sensorId": "r"}],
            streams={
                "f": [{"streamId": "f", "isMain": True, "url": "/videos/f.mp4"}],
                "r": [{"streamId": "r", "isMain": True, "url": "rtsp://c/1"}],
            },
        )
    )

    assert [r["name"] for r in await vios.list_media(VST, kind="video")] == ["f"]
    assert [r["name"] for r in await vios.list_media(VST, kind="stream")] == ["r"]


@pytest.mark.asyncio
async def test_a_null_timeline_is_not_the_same_as_no_recordings(vios_http) -> None:
    """VIOS listing a stream it cannot describe must not read as "nothing to reclaim"."""
    configure, _, _ = vios_http
    configure(**{"/storage/timelines": {"r-stream": None}})

    with pytest.raises(vios.VSTError, match="null timeline"):
        await vios.recorded_span(VST, "r-stream")


@pytest.mark.asyncio
async def test_an_unparseable_segment_time_is_refused(vios_http) -> None:
    """A truthy but unparseable value would otherwise reach the delete query."""
    configure, _, _ = vios_http
    configure(**{"/storage/timelines": {"r-stream": [{"startTime": "not-a-time", "endTime": "also-not"}]}})

    with pytest.raises(vios.VSTError, match="unparseable time"):
        await vios.recorded_span(VST, "r-stream")


@pytest.mark.asyncio
async def test_a_segment_ending_before_it_starts_is_refused(vios_http) -> None:
    configure, _, _ = vios_http
    configure(
        **{
            "/storage/timelines": {
                "r-stream": [{"startTime": "2025-01-01T00:05:00.000Z", "endTime": "2025-01-01T00:01:00.000Z"}]
            }
        }
    )

    with pytest.raises(vios.VSTError, match="ending before it starts"):
        await vios.recorded_span(VST, "r-stream")


@pytest.mark.asyncio
async def test_the_span_is_ordered_by_time_not_by_string(vios_http) -> None:
    """Mixed precision sorts wrong lexicographically; parsed values do not."""
    configure, _, _ = vios_http
    configure(
        **{
            "/storage/timelines": {
                "r-stream": [
                    # Chosen so string order and time order disagree: as text
                    # "2025-01-01T00:09:00Z" > "2025-01-01T00:10:00.000Z"
                    # because '9' > '1', while as instants it is earlier.
                    {"startTime": "2025-01-01T00:10:00.000Z", "endTime": "2025-01-01T00:20:00.000Z"},
                    {"startTime": "2025-01-01T00:09:00Z", "endTime": "2025-01-01T00:21:00Z"},
                ]
            }
        }
    )

    assert await vios.recorded_span(VST, "r-stream") == (
        "2025-01-01T00:09:00.000Z",
        "2025-01-01T00:21:00.000Z",
    )


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "1e400"])
def test_a_non_finite_offset_is_a_caller_error_not_a_traceback(bad: str) -> None:
    """float() accepts these; timedelta does not, and the raw error is unmapped."""
    with pytest.raises(vios.VIOSInvalidInputError, match="finite"):
        vios.resolve_window([SPAN], bad, None, "video")


def test_the_typed_errors_are_importable_from_the_package() -> None:
    """The CLI maps exits by class name, so a missing export failed silently."""
    import vss_core.vios as pkg

    assert pkg.VIOSInvalidInputError is vios.VIOSInvalidInputError
    assert pkg.VIOSNotFoundError is vios.VIOSNotFoundError


# ------------------------------------------------- add waits for indexing


@pytest.mark.asyncio
async def test_await_timeline_returns_once_vios_has_indexed(vios_http, monkeypatch) -> None:
    """VIOS accepts the bytes before the timeline exists; the wait covers that gap."""
    configure, _, _ = vios_http
    configure(**{"/storage/timelines": {}})
    monkeypatch.setattr(vios.asyncio, "sleep", _no_sleep)

    calls = {"n": 0}

    async def appears_on_third(*_a: object, **_k: object) -> tuple[str, str] | None:
        calls["n"] += 1
        return SPAN if calls["n"] >= 3 else None

    monkeypatch.setattr(vios, "recorded_span", appears_on_third)

    assert await vios.await_timeline(VST, "s-1") == SPAN
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_await_timeline_gives_up_saying_so(vios_http, monkeypatch) -> None:
    """Never indexed is a failure, not a sensor reported as usable."""
    configure, _, _ = vios_http
    configure(**{"/storage/timelines": {}})
    monkeypatch.setattr(vios.asyncio, "sleep", _no_sleep)

    async def never(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(vios, "recorded_span", never)

    with pytest.raises(vios.VIOSTimeoutError, match="had not indexed"):
        await vios.await_timeline(VST, "s-1", timeout_seconds=0.0)


@pytest.mark.asyncio
async def test_a_backend_error_is_not_retried_as_slow_indexing(vios_http, monkeypatch) -> None:
    """A 503 must fail now, not be polled at for a minute."""
    configure, _, _ = vios_http
    configure(**{"/storage/timelines": {}})
    monkeypatch.setattr(vios.asyncio, "sleep", _no_sleep)

    calls = {"n": 0}

    async def boom(*_a: object, **_k: object) -> None:
        calls["n"] += 1
        raise vios.VSTError("VIOS timelines API returned status 503")

    monkeypatch.setattr(vios, "recorded_span", boom)

    with pytest.raises(vios.VSTError, match="503"):
        await vios.await_timeline(VST, "s-1")
    assert calls["n"] == 1


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_the_stream_fallback_does_not_re_read_and_cannot_race(vios_http) -> None:
    """Re-reading opened a window: the stream could vanish between the two calls,
    and the second lookup's bare next() surfaced as
    `RuntimeError: coroutine raised StopIteration` — exit 1 and a traceback.
    """
    configure, calls, _ = vios_http
    configure(
        **_routes(
            sensors=[{"name": "cam", "sensorId": "cam-id"}],
            streams={"cam-id": [{"streamId": "sub-b", "url": "rtsp://x/b"}]},
        )
    )

    ref = await vios.resolve_sensor(VST, "sub-b")

    assert ref.stream_id == "sub-b"
    assert len([c for c in calls if "/streams" in c]) == 1, "the scan's read is reused"


# ------------------------------------------- gaps are not recorded video


GAPPED = [
    ("2025-01-01T12:00:00.000Z", "2025-01-01T12:10:00.000Z"),
    ("2025-01-01T13:00:00.000Z", "2025-01-01T13:10:00.000Z"),
]


def test_a_window_inside_a_gap_is_refused() -> None:
    """The envelope 12:00-13:10 contains 12:30; the recordings do not."""
    with pytest.raises(vios.VIOSInvalidInputError, match="not inside a single recorded segment"):
        vios.resolve_window(GAPPED, "2025-01-01T12:30:00.000Z", "2025-01-01T12:40:00.000Z", "video")


def test_a_window_spanning_two_segments_is_refused() -> None:
    """VIOS rejects a range crossing a gap; catching it here says why."""
    with pytest.raises(vios.VIOSInvalidInputError, match="not inside a single recorded segment"):
        vios.resolve_window(GAPPED, "2025-01-01T12:05:00.000Z", "2025-01-01T13:05:00.000Z", "video")


def test_a_window_inside_the_later_segment_is_accepted() -> None:
    assert vios.resolve_window(GAPPED, "2025-01-01T13:02:00.000Z", "2025-01-01T13:03:00.000Z", "video") == (
        "2025-01-01T13:02:00.000Z",
        "2025-01-01T13:03:00.000Z",
    )


def test_defaults_and_offsets_use_the_first_segment_not_the_envelope() -> None:
    assert vios.resolve_window(GAPPED, None, None, "video") == GAPPED[0]
    assert vios.resolve_window(GAPPED, "30", "60", "video") == (
        "2025-01-01T12:00:30.000Z",
        "2025-01-01T12:01:00.000Z",
    )


@pytest.mark.asyncio
async def test_recorded_segments_keeps_gaps_and_the_span_collapses_them(vios_http) -> None:
    """delete wants the envelope; anything choosing a window wants segments."""
    configure, _, _ = vios_http
    configure(
        **{
            "/storage/timelines": {
                "r": [
                    {"startTime": GAPPED[1][0], "endTime": GAPPED[1][1]},
                    {"startTime": GAPPED[0][0], "endTime": GAPPED[0][1]},
                ]
            }
        }
    )

    assert await vios.recorded_segments(VST, "r") == GAPPED
    assert await vios.recorded_span(VST, "r") == (GAPPED[0][0], GAPPED[1][1])


# ------------------------------- guards for the fixes that had none


@pytest.mark.asyncio
async def test_warm_up_reads_one_chunk_not_the_whole_clip(monkeypatch) -> None:
    """`read()` pulls the entire body — gigabytes for a long recording."""
    read_calls: list[str] = []

    class _Content:
        async def read(self) -> bytes:
            read_calls.append("read")
            return b"x" * 1024

        async def iter_chunked(self, size: int):
            read_calls.append(f"iter_chunked({size})")
            yield b"x" * size
            raise AssertionError("warm-up consumed a second chunk")

    class _Resp:
        status = 200
        content = _Content()

        async def read(self) -> bytes:
            read_calls.append("read")
            return b"x"

        async def __aenter__(self) -> _Resp:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        def get(self, _url: str, **_kw: object) -> _Resp:
            return _Resp()

    monkeypatch.setattr(vios.aiohttp, "ClientSession", lambda **_kw: _Session())

    assert await vios.warm_media_url("https://vss.test/vst/clip.mp4") is True
    assert read_calls == ["iter_chunked(65536)"], read_calls


@pytest.mark.asyncio
async def test_a_source_without_content_length_is_refused_not_buffered(vios_http) -> None:
    """VIOS needs Content-Length; discovering it by buffering is what this avoids."""
    configure, calls, _ = vios_http
    configure(**{"example.com": {}})

    with pytest.raises(vios.VIOSInvalidInputError, match="Content-Length"):
        await vios.upload_from_url(VST, "https://example.com/clip.mp4")

    assert not [c for c in calls if c.startswith("PUT")], "nothing should be uploaded"


@pytest.mark.asyncio
async def test_a_stream_with_no_url_stays_visible_under_a_filter(vios_http) -> None:
    """The third hiding path: an id but no url is unclassifiable too.

    `type` becomes "unknown", which matches no --type, so without an `error`
    the filter drops it and the sensor reads as absent.
    """
    configure, _, _ = vios_http
    configure(
        **_routes(
            sensors=[{"name": "urlless", "sensorId": "u0"}],
            streams={"u0": [{"streamId": "u0", "isMain": True}]},
        )
    )

    rows = await vios.list_media(VST, kind="video")

    assert [r["name"] for r in rows] == ["urlless"]
    assert rows[0]["error"] == "VIOS reported a stream with no url"


@pytest.mark.asyncio
async def test_keep_recordings_stops_the_camera_without_reclaiming_its_footage(vios_http, monkeypatch) -> None:
    """A stopped camera's hits must still resolve to a clip.

    Search documents outlive the sensor, and a hit is only useful while the
    recording it points at is still there.
    """
    configure, calls, _ = vios_http
    configure(**{"/sensor/list": [], "/sensor/cam_0": (200, {})})

    async def span(*_a: object, **_k: object) -> tuple[str, str]:
        return ("2025-01-01T00:00:00.000Z", "2025-01-01T00:03:30.000Z")

    async def absent(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(vios, "recorded_span", span)
    monkeypatch.setattr(vios, "confirm_absent", absent)

    ref = vios.SensorRef(name="cam", sensor_id="cam_0", stream_id="cam-1", url="rtsp://c/1", kind="stream")
    result = await vios.delete_media(VST, ref, keep_recordings=True)

    assert result["deleted"] == ["sensor"]
    assert result["recordings"] == "kept"
    assert result["confirmed"] is True
    assert not any(c.startswith("DELETE") and "/storage/" in c for c in calls), "footage must survive"


@pytest.mark.asyncio
async def test_keep_recordings_is_refused_for_an_uploaded_file(vios_http, monkeypatch) -> None:
    """For an upload the storage delete IS the deregistration.

    Keeping the bytes would leave a file with no sensor pointing at it -- the
    orphaning this command was just fixed to avoid.
    """
    configure, _, _ = vios_http
    configure(**{"/sensor/list": []})

    ref = vios.SensorRef(name="w", sensor_id="w_0", stream_id="w-1", url="/videos/w.mp4", kind="video")
    with pytest.raises(vios.VIOSInvalidInputError, match="would orphan the file"):
        await vios.delete_media(VST, ref, keep_recordings=True)
