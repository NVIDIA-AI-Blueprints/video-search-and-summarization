# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read-only frame scoring capabilities; no embedding, inference or persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import click

from vss_cli import params as params_mod
from vss_cli.group import Result
from vss_cli.group import context_from
from vss_cli.group import emit
from vss_cli.group import guarded
from vss_cli.group import require_services


def _disabled(source_type: str, index: str | list[str] | None, reason: str) -> dict[str, Any]:
    profile = {"enabled": False, "source_type": source_type, "reason": reason, "index": index}
    profile["fingerprint"] = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
    return profile


async def probe(runtime: Any, source_type: str) -> dict[str, Any]:
    from vss_core.search_core._internal.frame_refinement import resolve_frame_capabilities
    from vss_core.search_core.clients.elastic import ElasticClient
    from vss_core.search_core.errors import IndexNotFoundError
    from vss_core.search_core.primitives._attribute_helpers import resolve_index_by_source_type

    index = (
        resolve_index_by_source_type(runtime.frames_index, source_type, runtime.frames_index_wildcard)
        if runtime.frames_index
        else None
        if source_type == "video_file"
        else runtime.frames_index_wildcard
    )
    if not runtime.enable_frame_lookup or index is None:
        return _disabled(source_type, index, "frame lookup disabled or uploads frame index absent")
    es = ElasticClient.from_runtime_behavior(runtime)
    try:
        profile = await resolve_frame_capabilities(
            es, index, scoring=runtime.frame_scoring, transport=runtime.frame_lookup_transport
        )
        return {"enabled": True, "source_type": source_type, **profile}
    except IndexNotFoundError:
        return _disabled(source_type, index, "frame index absent")
    finally:
        await es.aclose()


def frame_capabilities_command() -> click.Command:
    def callback(**values: Any) -> None:
        ctx = context_from(values)
        require_services("search frame-capabilities", frozenset({"elasticsearch"}), ctx)
        from .group import _runtime_from

        def run() -> Result:
            runtime = _runtime_from(
                ctx.deployment,
                {
                    "frame_scoring": values["frame_scoring"],
                    "frame_lookup_transport": values["frame_lookup_transport"],
                },
            )
            return Result(body=asyncio.run(probe(runtime, values["source_type"])))

        emit(guarded(run), ctx)

    return click.Command(
        "frame-capabilities",
        callback=callback,
        help="Inspect frame indices and effective scoring/transport without running a model.",
        params=[
            click.Option(["--source-type"], type=click.Choice(["video_file", "rtsp"]), default="video_file"),
            click.Option(["--frame-scoring"], type=click.Choice(["auto", "source", "indexed"]), default="auto"),
            click.Option(
                ["--frame-lookup-transport"], type=click.Choice(["auto", "single", "msearch"]), default="auto"
            ),
            *params_mod.shared_options(),
        ],
    )
