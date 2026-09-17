# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vss analytics`` -- read-only incidents and metrics, without jobs."""

from __future__ import annotations

import asyncio
from typing import Any

import click

from . import params as params_mod
from .group import InvalidInput
from .group import Result
from .group import context_from
from .group import emit
from .group import guarded
from .group import require_services
from .group import requires_note

REQUIRES = frozenset({"video_analytics"})
_SOURCE_TYPES = click.Choice(["sensor", "place"])
_VERDICTS = click.Choice(["all", "confirmed", "rejected", "verification-failed", "not-confirmed"])
_ANALYSIS_TYPES = click.Choice(["max-min-incidents", "average-speed", "avg-num-people", "avg-num-vehicles"])


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _instant(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    if value is None:
        return None
    from datetime import UTC

    from vss_core._foundation.time import iso8601_to_datetime

    try:
        instant = iso8601_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise click.BadParameter(
            f"{value!r} is not an ISO-8601 instant, e.g. 2026-08-13T20:00:00Z",
            ctx=ctx,
            param=param,
        ) from exc
    return instant.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_pair(values: dict[str, Any], first: str, second: str) -> None:
    if bool(values.get(first)) != bool(values.get(second)):
        raise InvalidInput(f"--{first.replace('_', '-')} and --{second.replace('_', '-')} must be provided together")


def _validate_window(values: dict[str, Any]) -> None:
    start, end = values.get("start_time"), values.get("end_time")
    if start is None or end is None:
        return
    from vss_core._foundation.time import iso8601_to_datetime

    if iso8601_to_datetime(end) <= iso8601_to_datetime(start):
        raise InvalidInput("--end-time must be later than --start-time")


def _client(ctx: Any) -> Any:
    from vss_core.analytics import AnalyticsClient

    assert ctx.deployment is not None
    return AnalyticsClient(ctx.deployment.endpoint("video_analytics"))


def _command(name: str, help_text: str, options: list[click.Parameter], fn: Any) -> click.Command:
    def callback(**values: Any) -> None:
        _validate_window(values)
        ctx = context_from(values)
        require_services(f"analytics {name}", REQUIRES, ctx)
        emit(guarded(lambda: fn(ctx, values)), ctx)

    return click.Command(
        name=name,
        callback=callback,
        params=[*options, *params_mod.shared_options()],
        help=help_text + requires_note(REQUIRES),
        short_help=help_text.split("\n")[0],
    )


def _source_option(required: bool = True) -> click.Option:
    return click.Option(["--source"], required=required, metavar="TEXT", help="Sensor ID or analytics place.")


def _source_type_option(required: bool = False) -> click.Option:
    return click.Option(
        ["--source-type"],
        type=_SOURCE_TYPES,
        required=required,
        default=None if required else "sensor",
        show_default=not required,
        help="Interpret --source as a sensor ID or analytics place.",
    )


def _time_option(name: str, required: bool) -> click.Option:
    return click.Option(
        [f"--{name.replace('_', '-')}"],
        required=required,
        callback=_instant,
        metavar="ISO-8601",
    )


def _include_option() -> click.Option:
    return click.Option(
        ["--include"],
        multiple=True,
        metavar="FIELD",
        help="Additional incident field to include; repeat for multiple fields.",
    )


def _incidents(ctx: Any, values: dict[str, Any]) -> Result:
    _validate_pair(values, "source", "source_type")
    _validate_pair(values, "start_time", "end_time")
    requested = values["limit"]
    incidents = _run(
        _client(ctx).incidents(
            source=values.get("source"),
            source_type=values.get("source_type"),
            start_time=values.get("start_time"),
            end_time=values.get("end_time"),
            limit=requested + 1,
            includes=values["include"],
            vlm_verdict=values.get("vlm_verdict"),
        )
    )
    has_more = len(incidents) > requested
    incidents = incidents[:requested]
    return Result(body={"count": len(incidents), "incidents": incidents, "has_more": has_more})


def _incident(ctx: Any, values: dict[str, Any]) -> Result:
    return Result(body=_run(_client(ctx).incident(values["incident_id"], includes=values["include"])))


def _sensors(ctx: Any, values: dict[str, Any]) -> Result:
    sensors = _run(_client(ctx).sensors(place=values.get("place")))
    return Result(body={"count": len(sensors), "sensors": sensors})


def _places(ctx: Any, _values: dict[str, Any]) -> Result:
    places = _run(_client(ctx).places())
    return Result(body={"count": len(places), "places": places})


def _fov_histogram(ctx: Any, values: dict[str, Any]) -> Result:
    return Result(
        body=_run(
            _client(ctx).fov_histogram(
                source=values["source"],
                source_type=values["source_type"],
                start_time=values["start_time"],
                end_time=values["end_time"],
                object_type=values.get("object_type"),
                bucket_count=values["bucket_count"],
            )
        )
    )


def _average_speed(ctx: Any, values: dict[str, Any]) -> Result:
    return Result(
        body=_run(
            _client(ctx).average_speed(
                source=values["source"],
                source_type=values["source_type"],
                start_time=values["start_time"],
                end_time=values["end_time"],
            )
        )
    )


def _analyze(ctx: Any, values: dict[str, Any]) -> Result:
    return Result(
        body=_run(
            _client(ctx).analyze(
                source=values["source"],
                source_type=values["source_type"],
                start_time=values["start_time"],
                end_time=values["end_time"],
                analysis_type=values["analysis_type"],
            )
        )
    )


def _build() -> click.Group:
    group = click.Group(
        name="analytics",
        help=(
            "Read incidents and video analytics metrics from the configured Video Analytics API.\n\n"
            "These are read-only operations: they create no jobs or memory records. "
            "`analytics sensors` lists sensors represented in analytics calibration data; "
            "`vss vios list` lists sensors registered in VIOS."
        ),
        short_help="Read incidents and video analytics metrics",
    )
    group.add_command(
        _command(
            "incidents",
            "List recent incidents.",
            [
                _source_option(required=False),
                click.Option(["--source-type"], type=_SOURCE_TYPES),
                _time_option("start_time", required=False),
                _time_option("end_time", required=False),
                click.Option(["--limit"], type=click.IntRange(min=1), default=10, show_default=True),
                _include_option(),
                click.Option(["--vlm-verdict"], type=_VERDICTS),
            ],
            _incidents,
        )
    )
    group.add_command(
        _command(
            "incident",
            "Get one incident by ID.",
            [
                click.Option(["--incident-id"], required=True, metavar="ID"),
                _include_option(),
            ],
            _incident,
        )
    )
    group.add_command(
        _command(
            "sensors",
            "List sensors represented in analytics data (not the VIOS registry).",
            [click.Option(["--place"], metavar="TEXT", help="Filter by an analytics place name.")],
            _sensors,
        )
    )
    group.add_command(_command("places", "List places represented in analytics data.", [], _places))
    group.add_command(
        _command(
            "fov-histogram",
            "Read field-of-view occupancy histogram buckets.",
            [
                _source_option(),
                _source_type_option(),
                _time_option("start_time", required=True),
                _time_option("end_time", required=True),
                click.Option(["--object-type"], metavar="TEXT"),
                click.Option(
                    ["--bucket-count"],
                    type=click.IntRange(min=1),
                    default=10,
                    show_default=True,
                ),
            ],
            _fov_histogram,
        )
    )
    group.add_command(
        _command(
            "average-speed",
            "Read average object speed by direction.",
            [
                _source_option(),
                _source_type_option(),
                _time_option("start_time", required=True),
                _time_option("end_time", required=True),
            ],
            _average_speed,
        )
    )
    group.add_command(
        _command(
            "analyze",
            "Run deterministic analysis over analytics API results.",
            [
                _source_option(),
                _source_type_option(),
                _time_option("start_time", required=True),
                _time_option("end_time", required=True),
                click.Option(
                    ["--analysis-type"],
                    type=_ANALYSIS_TYPES,
                    required=True,
                ),
            ],
            _analyze,
        )
    )
    return group


class _AnalyticsGroup:
    api_version = 1
    name = "analytics"
    requires = REQUIRES
    summary = "Read incidents and video analytics metrics"

    def cli(self) -> click.Group:
        return _build()


ANALYTICS = _AnalyticsGroup()

__all__ = ["ANALYTICS"]
