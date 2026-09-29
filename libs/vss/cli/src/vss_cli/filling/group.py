# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Operate the deployed filling extension without NAT, MCP, or local inference."""

from __future__ import annotations

from typing import Any

import click

from vss_cli import params as params_mod
from vss_cli.group import Result
from vss_cli.group import context_from
from vss_cli.group import emit
from vss_cli.group import require_services
from vss_cli.group import requires_note
from vss_core.filling import FillingClient
from vss_core.filling import FillingError

REQUIRES = frozenset({"filling"})


def _stream() -> click.Option:
    return click.Option(
        ["--stream-id"], required=True, type=click.UUID, help="Exact registered stream UUID from filling sources."
    )


def _command(name: str, help_text: str, options: list[click.Parameter], fn: Any) -> click.Command:
    def callback(**values: Any) -> None:
        ctx = context_from(values)
        require_services(f"filling {name}", REQUIRES, ctx)
        try:
            assert ctx.deployment is not None
            with FillingClient(ctx.deployment.endpoint("filling")) as client:
                if values.get("stream_id") is not None:
                    values["stream_id"] = str(values["stream_id"])
                output = fn(client, values)
        except FillingError as exc:
            click.echo(f"vss: {exc}", err=True)
            raise SystemExit(exc.code) from exc
        emit(Result(body=output), ctx)

    return click.Command(
        name=name,
        callback=callback,
        params=[*options, *params_mod.shared_options()],
        help=help_text + requires_note(REQUIRES),
        short_help=help_text.split("\n")[0],
    )


def _build_live() -> click.Group:
    live = click.Group(
        name="live",
        help=(
            "Operate actual RTSP filling sessions separately from recorded analysis. "
            "Read stream/session IDs from returned state; provisional observations are not finalized cycles."
        ),
    )

    def session_options(*, required: bool = True) -> list[click.Parameter]:
        return [
            click.Option(["--session-id"], required=required, help="Exact session ID returned by live start/status."),
            click.Option(["--stream-id"], type=click.UUID, help="Additionally require this exact source stream UUID."),
        ]

    live.add_command(
        _command("sources", "List registered live RTSP source choices.", [], lambda c, _v: c.live_sources())
    )
    live.add_command(
        _command(
            "start",
            "Start the requested live stream session; inspect its returned readiness.",
            [
                _stream(),
                click.Option(
                    ["--request-id"], type=click.UUID, help="Reuse an actual request UUID for an idempotent start."
                ),
            ],
            lambda c, v: c.live_start(
                v["stream_id"], request_id=str(v["request_id"]) if v["request_id"] is not None else None
            ),
        )
    )
    for name, help_text in (
        ("status", "Read an exact live session, or discover the active/latest session when omitted."),
        ("stop", "Stop only the specified live measurement session."),
    ):
        live.add_command(
            _command(
                name,
                help_text,
                session_options(required=name != "status"),
                lambda c, v, operation=name: getattr(c, "live_" + operation)(v["session_id"], stream_id=v["stream_id"]),
            )
        )
    live.add_command(
        _command(
            "events",
            "Read a bounded window of actual live events and their evidence availability.",
            [
                *session_options(),
                click.Option(["--after-seq"], type=click.IntRange(min=0), default=0, show_default=True),
                click.Option(["--limit"], type=click.IntRange(min=1, max=200), default=100, show_default=True),
            ],
            lambda c, v: c.live_events(
                v["session_id"], stream_id=v["stream_id"], after_seq=v["after_seq"], limit=v["limit"]
            ),
        )
    )
    live.add_command(
        _command(
            "query",
            "Look up an exact live cycle or answer a bounded session-bound question; evidence stays qualified.",
            [
                *session_options(),
                click.Option(["--question"], help="Natural measured question, including a cycle-N label."),
                click.Option(
                    ["--cycle-id"], help="Exact cycle-N label or complete returned track ID; never event seq."
                ),
                click.Option(["--selected-cycle"], multiple=True, help="Actual UI-selected full track identity; repeat for a pair."),
                click.Option(
                    ["--epoch"], type=click.IntRange(min=1), help="Explicit requested cycle's connection epoch."
                ),
                click.Option(["--limit"], type=click.IntRange(min=1, max=50), default=10, show_default=True),
                click.Option(
                    ["--operator-view"],
                    is_flag=True,
                    help="Return a compact measured answer and native verified image artifacts; full validation remains enabled.",
                ),
            ],
            lambda c, v: c.live_query(
                v["session_id"],
                v["question"],
                stream_id=v["stream_id"],
                cycle_id=v["cycle_id"],
                cycle_ids=list(v["selected_cycle"]) or None,
                epoch=v["epoch"],
                limit=v["limit"],
                operator_view=v["operator_view"],
            ),
        )
    )
    return live


def _build() -> click.Group:
    group = click.Group(
        name="filling",
        help=(
            "Inspect recorded bottle measurements or explicit live RTSP sessions through the filling extension. "
            "The server owns analysis and its cache; the CLI mints no separate job. "
            "analyze waits for completion with a bounded timeout. Every recorded measurement command is "
            "bound to an explicitly selected stream UUID and recording clock."
        ),
    )
    group.add_command(
        _command("sources", "List actual registered VIOS recording choices.", [], lambda c, _v: c.sources())
    )
    group.add_command(
        _command(
            "source", "Read the currently selected recording and its measurement profile.", [], lambda c, _v: c.source()
        )
    )
    group.add_command(
        _command(
            "select",
            "Select and identity-verify a registered recording.",
            [_stream(), click.Option(["--refresh"], is_flag=True)],
            lambda c, v: c.select(v["stream_id"], refresh=v["refresh"]),
        )
    )
    group.add_command(
        _command(
            "analyze",
            "Analyze the selected recording; wait until complete.",
            [
                _stream(),
                click.Option(["--force"], is_flag=True),
                click.Option(["--timeout"], type=click.FloatRange(min=1, max=3600), default=1200, show_default=True),
            ],
            lambda c, v: c.analyze(v["stream_id"], force=v["force"], timeout=v["timeout"]),
        )
    )
    group.add_command(
        _command("status", "Read source-bound analysis state.", [_stream()], lambda c, v: c.status(v["stream_id"]))
    )
    group.add_command(
        _command(
            "results",
            "Read current-engine cycles, model provenance and quality; samples are omitted by default.",
            [_stream(), click.Option(["--include-samples"], is_flag=True)],
            lambda c, v: c.results(v["stream_id"], include_samples=v["include_samples"]),
        )
    )
    group.add_command(
        _command(
            "query",
            "Answer a measured question with each observation's real video evidence.",
            [
                _stream(),
                click.Option(["--question"], required=True),
                click.Option(["--scene-id"]),
                click.Option(["--reference-percent"], type=click.FloatRange(min=0, max=100)),
                click.Option(["--include-evidence/--no-evidence"], default=True),
            ],
            lambda c, v: c.query(
                v["stream_id"],
                v["question"],
                scene_id=v["scene_id"],
                reference_percent=v["reference_percent"],
                include_evidence=v["include_evidence"],
            ),
        )
    )
    group.add_command(
        _command(
            "evidence",
            "Get real VIOS evidence for a measured interval in source seconds.",
            [
                _stream(),
                click.Option(["--start"], required=True, type=float),
                click.Option(["--end"], required=True, type=float),
            ],
            lambda c, v: c.evidence(v["stream_id"], v["start"], v["end"]),
        )
    )
    segmentation = click.Group(
        name="segmentation",
        help="Read raw GPU bottle/liquid masks; filling results/query reports derived measurement provenance.",
    )
    segmentation.add_command(
        _command(
            "run",
            "Analyze recorded frames with the GPU mask models; wait within a bounded timeout.",
            [
                _stream(),
                click.Option(["--force"], is_flag=True),
                click.Option(["--timeout"], type=click.FloatRange(min=1, max=3600), default=1200, show_default=True),
            ],
            lambda c, v: c.segmentation_run(v["stream_id"], force=v["force"], timeout=v["timeout"]),
        )
    )
    segmentation.add_command(
        _command(
            "status",
            "Read source-bound GPU mask analysis state and model identities.",
            [_stream()],
            lambda c, v: c.segmentation_status(v["stream_id"]),
        )
    )
    segmentation.add_command(
        _command(
            "get",
            "Read mask provenance; use --at for one actual frame, or explicitly include all samples.",
            [
                _stream(),
                click.Option(["--at"], type=click.FloatRange(min=0), help="Offset in original recording seconds."),
                click.Option(["--include-samples"], is_flag=True, help="Include every frame's polygon arrays."),
            ],
            lambda c, v: c.segmentation_get(v["stream_id"], at=v["at"], include_samples=v["include_samples"]),
        )
    )
    group.add_command(segmentation)
    group.add_command(_build_live())
    return group


class _FillingGroup:
    api_version = 1
    name = "filling"
    requires = REQUIRES
    summary = "Inspect recorded and live bottle filling measurements"

    def cli(self) -> click.Group:
        return _build()


FILLING = _FillingGroup()
