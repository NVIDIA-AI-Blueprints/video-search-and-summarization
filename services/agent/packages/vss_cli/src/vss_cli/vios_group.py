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

import click

from . import params as params_mod
from .exits import Exit
from .group import Result
from .group import context_from
from .group import emit
from .group import guarded
from .group import require_services

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
        help=help_text,
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
    # The envelope across every recorded segment. Reporting only the first
    # would understate what is on disk for a stream that recorded in bursts.
    span = _run(vios.get_timelines_map(origin)).get(ref.stream_id)
    if span is None:
        return Result(body=_with_ref(ref, {"start_time": None, "end_time": None, "recorded": False}))
    return Result(body=_with_ref(ref, {"start_time": span[0], "end_time": span[1], "recorded": True}))


def _clip(ctx: Any, values: dict[str, Any]) -> Result:
    from vss_core import vios

    origin = _origin(ctx)
    ref = _run(vios.resolve_sensor(origin, values["sensor"]))
    start, end = _run(vios.get_timeline(ref.stream_id, origin))
    # Default to the covering segment rather than making the caller read the
    # timeline and hand the bounds back -- that round trip is where invented
    # timestamps come from.
    start = values.get("start_time") or start
    end = values.get("end_time") or end
    url = _run(
        vios.get_video_clip_url(
            stream_id=ref.stream_id,
            start_time=start,
            end_time=end,
            vst_internal_url=origin,
        )
    )
    # Echo the window this command resolved -- the segment bounds when none was
    # given. VIOS does not report the window it actually served, so this is the
    # requested range, not a confirmation of the bytes behind the URL.
    return Result(body=_with_ref(ref, {"media_url": url, "start_time": start, "end_time": end, "kind": "clip"}))


def _snapshot(ctx: Any, values: dict[str, Any]) -> Result:
    from vss_core import vios

    origin = _origin(ctx)
    ref = _run(vios.resolve_sensor(origin, values["sensor"]))
    at = values.get("at")
    url = _run(vios.get_snapshot_url(origin, ref.stream_id, at=at))
    body = {"media_url": url, "kind": "snapshot", "source": "replay" if at else "live"}
    if at:
        body["at"] = at
    return Result(body=_with_ref(ref, body))


def _add(ctx: Any, values: dict[str, Any]) -> Result:
    from vss_core import vios

    origin = _origin(ctx)
    source = values["source"]
    if values["type"] == "stream":
        name = values.get("name") or source.rstrip("/").rsplit("/", 1)[-1]
        sensor_id = _run(vios.add_stream(origin, source, name))
        return Result(body={"name": name, "sensor_id": sensor_id, "type": "stream", "added": True})

    if values.get("name"):
        raise click.UsageError("--name applies to --type stream; a video's filename becomes its sensor name")
    path = pathlib.Path(source)
    result = _run(vios.upload_media(origin, path))
    return Result(
        body={
            "name": result.get("filename") or path.name,
            "sensor_id": result.get("sensorId"),
            "stream_id": result.get("streamId"),
            "type": "video",
            "bytes": result.get("bytes"),
            "added": True,
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
    return Result(body=_run(vios.delete_media(origin, ref)))


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
            "List sensors joined with their streams.\n\n"
            "--type filters by provenance; omitting it lists everything with its type resolved.",
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
            "Mint a clip URL. Defaults to the whole covering segment.",
            [
                _sensor_option(),
                click.Option(["--start-time"], default=None, help="ISO-8601 start; defaults to the segment start."),
                click.Option(["--end-time"], default=None, help="ISO-8601 end; defaults to the segment end."),
            ],
            _clip,
        )
    )
    group.add_command(
        _command(
            "snapshot",
            "Mint a picture URL: the latest live frame, or the frame nearest --at.",
            [_sensor_option(), click.Option(["--at"], default=None, help="ISO-8601 timestamp; omit for a live frame.")],
            _snapshot,
        )
    )
    group.add_command(
        _command(
            "add",
            "Register media: a local file (--type video) or an RTSP URL (--type stream).",
            [
                click.Option(["--type"], type=_TYPES, required=True, help="What SOURCE is."),
                click.Argument(["source"]),
                click.Option(["--name"], default=None, help="Sensor name for an RTSP source."),
            ],
            _add,
        )
    )
    group.add_command(
        _command(
            "delete",
            "Remove a sensor and its recordings, by the flow its provenance needs.",
            [click.Option(["--type"], type=_TYPES, required=True, help="What the target is."), _sensor_option()],
            _delete,
        )
    )
    return group


class _ViosGroup:
    """Entry-point object; see :mod:`vss_cli.plugins` for the contract."""

    api_version = 1
    name = "vios"
    summary = "Sensors, timelines, clips and snapshots (no jobs)"

    def cli(self) -> click.Group:
        return _build()


VIOS = _ViosGroup()

__all__ = ["VIOS"]
