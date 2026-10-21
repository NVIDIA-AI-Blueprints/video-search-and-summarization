# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read-only live ingestion diagnostics; no model calls or job persistence."""

from __future__ import annotations

import asyncio
import json
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
            content_contract = None
            if values["content_contract"] is not None:
                from vss_core.search_core.errors import InvalidInputError

                try:
                    with open(values["content_contract"], encoding="utf-8") as file:
                        content_contract = json.load(file)
                    if not isinstance(content_contract, dict):
                        raise InvalidInputError("Content contract JSON must be an object")
                except (OSError, ValueError) as error:
                    raise InvalidInputError("Could not read content contract JSON") from error
            body = asyncio.run(
                gallery_readiness(
                    runtime, list(values["video_source"]), require_attributes=values["require_attributes"], content_contract=content_contract
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
            click.Option(["--content-contract"], type=click.Path(exists=True, dir_okay=False), help="Opt-in JSON embedding and decoded-frame content contract."),
            *params_mod.shared_options(),
        ],
    )
