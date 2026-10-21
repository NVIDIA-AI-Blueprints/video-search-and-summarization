# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read-only live ingestion diagnostics; no model calls or job persistence."""

from __future__ import annotations

import asyncio
from typing import Any

import click

from vss_cli import params as params_mod
from vss_cli.exits import Exit
from vss_cli.group import Result
from vss_cli.group import context_from
from vss_cli.group import emit
from vss_cli.group import guarded
from vss_cli.group import require_services


def readiness_command() -> click.Command:
    def callback(**values: Any) -> None:
        ctx = context_from(values)
        require_services("search readiness", frozenset({"elasticsearch", "vst"}), ctx)
        from vss_core.search_core.readiness import gallery_readiness

        from .group import _runtime_from

        def run() -> Result:
            runtime = _runtime_from(ctx.deployment)
            body = asyncio.run(
                gallery_readiness(
                    runtime, list(values["video_source"]), require_attributes=values["require_attributes"]
                )
            )
            return Result(body=body, exit=Exit.SUCCESS if body["ready"] else Exit.PARTIAL)

        emit(guarded(run), ctx)

    return click.Command(
        "readiness",
        callback=callback,
        help="Check live uploads-index coverage; missing detections are reported per source.",
        params=[
            click.Option(["--video-source"], multiple=True, required=True),
            click.Option(["--require-attributes/--no-require-attributes"], default=True),
            *params_mod.shared_options(),
        ],
    )
