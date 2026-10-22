# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vss vios`` -- the media plane.

Deliberately *not* a :class:`vss_cli.group.CommandGroup`. VIOS operations are
not VSS processing: they run no model and produce no evidence, they resolve
handles and mint URLs. So they mint no ``job_id``, write no memory record, and
emit no completion marker, and the job grammar does not apply: there is no
``run``, no ``status``, no ``get``, and the ``list`` here lists *sensors*, not
jobs. ``CommandGroup.cli()`` is final and would mount all four job verbs.

What it keeps from the framework is the part that should be uniform: a missing
backend is reported by :func:`vss_cli.group.require_services` with the same
wording every other group uses, and results leave through the same emitter.

Six commands::

    vss vios list     [--type video|stream] [--sensor NAME]
    vss vios timeline --sensor NAME
    vss vios clip     --sensor NAME [--start-time T --end-time T]
    vss vios snapshot --sensor NAME [--at T]
    vss vios add      --type video|stream SOURCE [--name NAME]
    vss vios delete   --type video|stream --sensor NAME

Media is addressed by sensor **name**; id resolution happens inside
:mod:`vss_core.vios`.
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any
import urllib.parse

import click

from . import params as params_mod
from .exits import Exit
from .group import Result
from .group import context_from
from .group import emit
from .group import guarded
from .group import require_services
from .group import requires_note

#: Every command here talks to VIOS and nothing else.
REQUIRES = frozenset({"vst"})

_TYPES = click.Choice(["video", "stream"])


def _origin(ctx: Any) -> str:
    """The deployment origin these commands call.

    Single-origin (NFR-6): the `/vst` path route hangs off the recorded base
    URL, so there is no separate VIOS endpoint to discover or pass.
    """
    return str(ctx.deployment.base_url).rstrip("/")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _sensor_option(required: bool = True) -> click.Option:
    return click.Option(
        ["--sensor"],
        required=required,
        metavar="NAME",
        help="Sensor name (a sensorId or streamId is accepted as a fallback).",
    )


def _command(name: str, help_text: str, extra: list[click.Parameter], fn: Any) -> click.Command:
    """One vios command, wired to the shared context/preflight/emit path."""

    def callback(**values: Any) -> None:
        ctx = context_from(values)
        require_services(f"vios {name}", REQUIRES, ctx)
        emit(guarded(lambda: fn(ctx, values)), ctx)

    return click.Command(
        name=name,
        callback=callback,
        params=[*extra, *params_mod.shared_options()],
        help=help_text + requires_note(REQUIRES),
        short_help=help_text.split("\n")[0],
    )


# ------------------------------------------------------------------ commands


def _list(ctx: Any, values: dict[str, Any]) -> Result:
    from vss_core import vios

    kind = values.get("type")
    rows = _run(vios.list_media(_origin(ctx), kind=kind))
    if values.get("sensor"):
        rows = [r for r in rows if values["sensor"] in (r["name"], r["sensor_id"], r["stream_id"])]
    # An empty listing is a fact, not a failure -- a backend problem raises and
    # exits 3 instead, so the two are never confused.
    return Result(body={"count": len(rows), "type": kind, "sensors": rows})


def _timeline(ctx: Any, values: dict[str, Any]) -> Result:
    from vss_core import vios

    origin = _origin(ctx)
    ref = _run(vios.resolve_sensor(origin, values["sensor"]))

    segments = _run(vios.recorded_segments(origin, ref.stream_id))
    # Every segment, not an envelope: a stream recorded in bursts has gaps, and
    # reporting one pair claims a continuous recording that does not exist.
    return Result(
        body=_with_ref(
            ref,
            {
                "recorded": bool(segments),
                "segments": [{"start_time": a, "end_time": b} for a, b in segments],
            },
        )
    )


def _clip(ctx: Any, values: dict[str, Any]) -> Result:
    from vss_core import vios

    origin = _origin(ctx)
    ref = _run(vios.resolve_sensor(origin, values["sensor"]))
    # Validate the window against what is actually recorded before asking VIOS.
    # VIOS answers a malformed or out-of-range window with a bare HTTP 400 that
    # names neither the offending bound nor the range that was available.
    segments = _run(vios.recorded_segments(origin, ref.stream_id))
    start, end = vios.resolve_window(segments, values.get("start_time"), values.get("end_time"), ref.kind)
    url = _run(
        vios.get_video_clip_url(
            stream_id=ref.stream_id,
            start_time=start,
            end_time=end,
            vst_internal_url=origin,
        )
    )
    url = vios.normalise_media_url(url, origin)
    warmed = _run(vios.warm_media_url(url))
    # Echo the window this command resolved -- the segment bounds when none was
    # given. VIOS does not report the window it actually served, so this is the
    # requested range, not a confirmation of the bytes behind the URL.
    return Result(
        body=_with_ref(
            ref,
            {"media_url": url, "start_time": start, "end_time": end, "kind": "clip", "warmed": warmed},
        )
    )


def _snapshot(ctx: Any, values: dict[str, Any]) -> Result:
    from vss_core import vios

    origin = _origin(ctx)
    ref = _run(vios.resolve_sensor(origin, values["sensor"]))
    at = values.get("at")

    # Only an RTSP sensor has a live frame; VIOS answers 400 for one on
    # anything else. Keyed on "not stream" rather than "is video" because
    # provenance can also be "unknown", which is equally not live. We already know the provenance, so resolve to the first recorded
    # frame instead of sending a request that cannot succeed. `clip` defaults
    # to the covering segment for the same reason.
    if at is None and ref.kind != "stream":
        span = _run(vios.recorded_span(origin, ref.stream_id))
        if span is None:
            return Result(
                body={
                    "error": f"{ref.name!r} is an uploaded file with no recordings yet, so it has "
                    f"neither a live frame nor one to replay",
                    "name": ref.name,
                    "type": ref.kind,
                },
                exit=Exit.NOT_FOUND,
            )
        at = span[0]
    elif at is not None:
        # One instant, validated the same way: reuse the window check with both
        # bounds set to it.
        at, _ = vios.resolve_window(
            _run(vios.recorded_segments(origin, ref.stream_id)) if ref.kind != "stream" else [],
            at,
            at,
            ref.kind,
        )

    url = vios.normalise_media_url(_run(vios.get_snapshot_url(origin, ref.stream_id, at=at)), origin)
    body = {"media_url": url, "kind": "snapshot", "source": "replay" if at else "live"}
    if at:
        body["at"] = at
    return Result(body=_with_ref(ref, body))


def _add(ctx: Any, values: dict[str, Any]) -> Result:
    from vss_core import vios

    origin = _origin(ctx)
    source = values["source"]
    # SOURCE already says what it is, so do not make the caller restate it.
    kind = vios.classify_media_source(source)
    declared = values.get("type")
    if declared and declared != kind:
        return Result(
            body={"error": f"{source!r} is a {kind} source, not a {declared}", "type": kind},
            exit=Exit.INVALID_INPUT,
        )
    if kind == "stream":
        name = values.get("name") or source.rstrip("/").rsplit("/", 1)[-1]
        sensor_id = _run(vios.add_stream(origin, source, name))
        return Result(body={"name": name, "sensor_id": sensor_id, "type": "stream", "added": True})

    if source.lower().startswith(("http://", "https://")):
        # Streamed straight into VIOS rather than staged on disk.
        result = _run(vios.upload_from_url(origin, source, name=values.get("name")))
    else:
        path = pathlib.Path(source)
        result = _run(vios.upload_media(origin, path, name=values.get("name")))
    stream_id = str(result.get("streamId") or "")

    # VIOS accepts the bytes before it has indexed them, so `add` is not done
    # when the PUT returns -- a clip taken immediately afterwards finds nothing
    # recorded. Wait for the timeline, and fail (exit 7) rather than report a
    # sensor that cannot yet be used.
    recorded = _run(vios.await_timeline(origin, stream_id)) if stream_id else None

    return Result(
        body={
            "name": result.get("filename") or _fallback_name(source),
            "sensor_id": result.get("sensorId"),
            "stream_id": stream_id,
            "type": "video",
            "bytes": result.get("bytes"),
            "added": True,
            "recorded": {"start_time": recorded[0], "end_time": recorded[1]} if recorded else None,
        }
    )


def _delete(ctx: Any, values: dict[str, Any]) -> Result:
    from vss_core import vios

    origin = _origin(ctx)
    ref = _run(vios.resolve_sensor(origin, values["sensor"]))
    if ref.kind == "unknown":
        return Result(
            body={"error": f"VIOS reports no url for {ref.name!r}, so its provenance is unknown", "name": ref.name},
            exit=Exit.INVALID_INPUT,
        )
    if ref.kind != values["type"]:
        # Refusing beats deleting the wrong thing: --type is the caller saying
        # what they believe this is, and a mismatch means one of us is wrong.
        return Result(
            body={
                "error": f"sensor {ref.name!r} is a {ref.kind}, not a {values['type']}",
                "name": ref.name,
                "type": ref.kind,
            },
            exit=Exit.INVALID_INPUT,
        )
    return Result(body=_run(vios.delete_media(origin, ref, keep_recordings=bool(values.get("keep_recordings")))))


def _fallback_name(source: str) -> str:
    """What we called it, when VIOS's response does not say."""
    return pathlib.PurePosixPath(urllib.parse.urlparse(source).path).name or pathlib.Path(source).name


def _with_ref(ref: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Attach the resolved identity to a result, including any assumption."""
    body |= {"name": ref.name, "sensor_id": ref.sensor_id, "stream_id": ref.stream_id, "type": ref.kind}
    if ref.main_stream_assumed:
        # Say it rather than resolve silently: the caller may be reading a
        # substream without knowing it.
        body["main_stream_assumed"] = True
    return body


def _build() -> click.Group:
    group = click.Group(
        name="vios",
        help=(
            "The media plane: sensors, recorded ranges, and the clip and snapshot URLs that feed `vss vlm`.\n"
            "\n"
            "These commands resolve handles and mint URLs; they run no model and produce no evidence, so "
            "they mint no job_id, write no memory record, and have no run/status/get/list verbs.\n"
            "\n"
            "Media is addressed by sensor NAME; sensorId and streamId resolution happens internally. "
            "--type selects provenance: `video` is a file-backed sensor, `stream` is an RTSP one."
        ),
        short_help="Sensors, timelines, clips and snapshots (no jobs)",
    )
    group.add_command(
        _command(
            "list",
            "List sensors joined with their streams.\n"
            "\n"
            "--type filters by provenance; omitting it lists everything with its type resolved.\n"
            "\n"
            "`source` is a camera's RTSP address, reported for streams only. To get a URL you can "
            "fetch, use `vss vios clip` or `vss vios snapshot`.\n"
            "\n"
            "\b\n"
            "  vss vios list\n"
            "  vss vios list --type video\n"
            "  vss vios list --sensor warehouse_safety_0001\n",
            [
                click.Option(["--type"], type=_TYPES, default=None, help="Filter by provenance."),
                _sensor_option(required=False),
            ],
            _list,
        )
    )
    group.add_command(
        _command(
            "timeline",
            "Show the recorded ranges for a sensor.",
            [_sensor_option()],
            _timeline,
        )
    )
    group.add_command(
        _command(
            "clip",
            "Mint a clip URL. Defaults to the whole covering segment.\n"
            "\n"
            "For a recorded file either bound may be omitted, and either may be given as seconds from "
            "the start of the recording. A live stream needs both, as ISO-8601. The window is checked "
            "against what is recorded before VIOS is asked.\n"
            "\n"
            "\b\n"
            "  vss vios clip --sensor warehouse_safety_0001\n"
            "  vss vios clip --sensor dock-cam --start-time 2026-08-01T12:00:00Z --end-time 2026-08-01T12:00:10Z\n",
            [
                _sensor_option(),
                click.Option(
                    ["--start-time"],
                    default=None,
                    help="ISO-8601, or seconds from the recording start. Defaults to the recording start.",
                ),
                click.Option(
                    ["--end-time"],
                    default=None,
                    help="ISO-8601, or seconds from the recording start. Defaults to the recording end.",
                ),
            ],
            _clip,
        )
    )
    group.add_command(
        _command(
            "snapshot",
            "Mint a picture URL: the latest live frame, or the frame nearest --at.\n"
            "\n"
            "A live frame only exists for an RTSP sensor. For an uploaded file, omitting --at "
            "gives the first recorded frame rather than an error.\n"
            "\n"
            "\b\n"
            "  vss vios snapshot --sensor dock-cam\n"
            "  vss vios snapshot --sensor warehouse_safety_0001 --at 2025-01-01T00:01:00.000Z\n",
            [_sensor_option(), click.Option(["--at"], default=None, help="ISO-8601 timestamp; omit for a live frame.")],
            _snapshot,
        )
    )
    group.add_command(
        _command(
            "add",
            "Register media: a local file, or an RTSP URL.\n"
            "\n"
            "A video upload waits until VIOS has indexed the recording, because the bytes are "
            "accepted before the timeline exists and a clip taken in between finds nothing. Exits 7 "
            "if that has not happened within a minute.\n"
            "\n"
            "What SOURCE is, is read from SOURCE: rtsp:// or rtsps:// is a live stream, "
            "anything else is a video. The sensor is named after the file unless --name says otherwise.\n"
            "\n"
            "\b\n"
            "  vss vios add ./warehouse_safety_0001.mp4\n"
            "  vss vios add ./clip.mp4 --name warehouse_safety_0002.mp4\n"
            "  vss vios add https://example.com/clip.mp4\n"
            "\n"
            "An http(s) source is fetched with your own network access, exactly as `curl` would "
            "be — the CLI adds no boundary you do not already cross. Treat an untrusted URL "
            "accordingly.\n"
            "  vss vios add rtsp://cam.local/stream1 --name dock-cam\n",
            [
                click.Option(
                    ["--type"],
                    type=_TYPES,
                    default=None,
                    help="Optional check: fail if SOURCE is not this kind.",
                ),
                click.Argument(["source"]),
                click.Option(["--name"], default=None, help="Name to register it under. Defaults to the filename."),
            ],
            _add,
        )
    )
    group.add_command(
        _command(
            "delete",
            "Remove a sensor and its recordings, by the flow its provenance needs.\n"
            "\n"
            "`--keep-recordings` stops a camera without reclaiming its footage, so search hits "
            "recorded before it was stopped still resolve to a clip. Streams only: an uploaded "
            "file's storage delete is what deregisters it.\n"
            "\n"
            "--type is required here because a name does not say what it is, and the two "
            "teardowns differ; a mismatch is refused rather than guessed.\n"
            "\n"
            "\b\n"
            "  vss vios delete --type video --sensor warehouse_safety_0001\n",
            [
                click.Option(["--type"], type=_TYPES, required=True, help="What the target is."),
                _sensor_option(),
                click.Option(
                    ["--keep-recordings"],
                    is_flag=True,
                    default=False,
                    help="Stop a stream but keep its recordings, so existing search hits still resolve.",
                ),
            ],
            _delete,
        )
    )
    return group


class _ViosGroup:
    """Entry-point object; see :mod:`vss_cli.plugins` for the contract."""

    api_version = 1
    name = "vios"
    #: Read by `vss configure check` to report whether this group can be served.
    requires = REQUIRES
    summary = "Sensors, timelines, clips and snapshots (no jobs)"

    def cli(self) -> click.Group:
        return _build()


VIOS = _ViosGroup()

__all__ = ["VIOS"]
