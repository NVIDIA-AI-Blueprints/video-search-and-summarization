# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vss search`` on the fixed verb grammar.

The old surface exposed ``run``, ``embed`` and ``attribute`` as equal
siblings, because it mirrored the three primitive modules in
``search_core.primitives``. That flattened a real hierarchy: SDD §2 keeps
``embed``/``attribute`` as "low-level non-job primitives (developer
surface)", while ``run`` is the job-shaped facade that fuses them. They are
different tiers, and the grammar now says so -- ``run`` sits with
``status``/``get``/``list`` as a framework verb, the primitives are declared
separately and mint no job.

The 50-option surface splits three ways:

* **19 stay** as :class:`SearchRunInput` -- what a caller is asking for.
* **15 leave entirely.** Endpoints, index names and the embedding model are
  read from ``~/.vss/config.json``, which ``vss configure`` populated from
  what the backends reported about themselves.
* **5 are deleted.** ``--deployment/--profile/--namespace/--release/
  --kube-context`` inspected compose files and kubectl; NFR-6 removes
  deployment discovery outright.

Four behaviour knobs (request timeout, frame lookup, result cap, embed-only
fallback) leave the command line without entering the config: they are
caller preferences, not facts about a deployment, so they take library
defaults until a preferences tier exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar
from typing import Literal

import click
from pydantic import BaseModel
from pydantic import Field

from . import config as config_mod
from .exits import Exit
from .group import CommandGroup
from .group import Context
from .group import Result

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Index families the deployment reports, mapped to the runtime field that
#: consumes them. Discovered rather than declared -- `vss configure` reads
#: them from Elasticsearch's own _cat/indices.
_INDEX_PREFIXES = {
    "video_embed_index": "mdx-embed-filtered-",
    "behavior_index": "mdx-behavior-",
    "frames_index": "mdx-raw-",
}


class SearchRunInput(BaseModel):
    """What a caller is asking for. Nothing about where the backend lives.

    This is also SDD §5.2's ``job.request`` and the MCP tool's input schema --
    one declaration, three surfaces.
    """

    # -- the query ---------------------------------------------------------
    query: str = Field("", description="Decomposed visual query to embed and search for.")
    search_mode: Literal["embed", "attribute", "fusion", "object"] | None = Field(
        None, description="Explicit execution path. Default: embed."
    )
    decomposed_json: str | None = Field(
        None, description="JSON object produced by the host agent's query decomposition."
    )
    source_type: Literal["video_file", "rtsp"] | None = Field(None, description="Media source type.")
    video_sources: list[str] = Field(
        default_factory=list,
        description="Registered source name to search; repeatable.",
        json_schema_extra={"cli_flag": "--video-source"},
    )
    description: str | None = Field(None, description="Free-text description accompanying the query.")
    timestamp_start: str | None = Field(None, description="Absolute ISO-8601 window start.")
    timestamp_end: str | None = Field(None, description="Absolute ISO-8601 window end.")
    top_k: int | None = Field(None, ge=1, le=1000, description="Maximum results to return.")
    min_cosine_similarity: float | None = Field(
        None, ge=-1.0, le=1.0, description="Minimum cosine similarity threshold."
    )
    attributes: list[str] = Field(
        default_factory=list,
        description="Appearance/metadata attribute for attribute or fusion search; repeatable.",
        json_schema_extra={"cli_flag": "--attribute"},
    )
    object_ids: list[str] = Field(
        default_factory=list,
        description="Restrict to these object ids; repeatable.",
        json_schema_extra={"cli_flag": "--object-id"},
    )

    # -- fusion tuning -----------------------------------------------------
    fusion_method: Literal["weighted_linear", "rrf", "rrf_with_attribute_rank"] | None = Field(
        None, description="How embed and attribute legs are combined."
    )
    w_attribute: float | None = Field(None, ge=0.0, le=1.0, description="Attribute leg weight.")
    w_embed: float | None = Field(None, ge=0.0, le=1.0, description="Embedding leg weight.")
    rrf_k: int | None = Field(None, ge=1, description="Reciprocal-rank-fusion k.")
    rrf_w: float | None = Field(None, ge=0.0, le=1.0, description="Reciprocal-rank-fusion weight.")
    top_percent_filter: float | None = Field(None, ge=0.0, le=1.0, description="Keep only the top fraction of hits.")
    embed_confidence_threshold: float | None = Field(
        None, ge=0.0, le=1.0, description="Confidence floor for the embedding leg."
    )


def _runtime_from(deployment: config_mod.Deployment) -> Any:
    """Build a SearchRuntime from the recorded deployment.

    Every endpoint and index here was reported by a backend, not typed by a
    caller -- which is the point of `vss configure`. A missing service raises
    :class:`vss_cli.config.ConfigError` naming what *is* available, so the
    failure arrives with the fix attached instead of as a connection timeout
    deep inside a search.
    """
    from vss_core.search_core.runtime import SearchRuntime

    es = deployment.endpoint("elasticsearch")
    embed_service = deployment.services.get("rt_embed")
    es_service = deployment.services.get("elasticsearch")

    kwargs: dict[str, Any] = {
        "es_endpoint": es,
        "behavior_es_endpoint": es,
        "cosmos_embed_endpoint": deployment.endpoint("rt_embed"),
        "rtvi_cv_endpoint": deployment.endpoint("rtvi_cv"),
        # VST takes the *origin*, not the mount: search_core appends the
        # `/vst/api/v1/...` prefix itself, so handing it the mounted
        # `.../vst` yields `/vst/vst/api/v1/...`. Asserting the mount exists
        # first keeps the "not routed" diagnostic rather than silently
        # producing URLs that 404 much later.
        "vst_internal_url": (deployment.endpoint("vst") and deployment.base_url),
        "vst_external_url": (deployment.endpoint("vst") and deployment.base_url),
    }
    if embed_service and embed_service.models:
        kwargs["cosmos_embed_model"] = embed_service.models[0]

    available = sorted(es_service.indices) if es_service else []
    for field_name, prefix in _INDEX_PREFIXES.items():
        matches = [i for i in available if i.startswith(prefix)]
        if matches:
            kwargs[field_name] = matches[0]
            kwargs[f"{field_name}_wildcard"] = f"{prefix}*"

    return SearchRuntime(**kwargs)


def _primitive(name: str, summary: str) -> click.Command:
    """A non-job primitive: no job id, no persistence, argv forwarded.

    §2 keeps these as the developer surface beneath ``run``. They keep the
    argparse parser that owns their option documentation rather than being
    restated here, so the two cannot drift.
    """

    @click.command(
        name=name,
        short_help=summary,
        add_help_option=False,
        context_settings={"ignore_unknown_options": True, "help_option_names": []},
    )
    @click.argument("argv", nargs=-1, type=click.UNPROCESSED)
    @click.pass_context
    def _command(ctx: click.Context, argv: tuple[str, ...]) -> None:
        from .search import run as run_search

        try:
            code = run_search(name, list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 1
        ctx.exit(code)

    return _command


class SearchGroup(CommandGroup):
    """Search indexed video."""

    name: ClassVar[str] = "search"
    summary: ClassVar[str] = "Search indexed video"
    Input: ClassVar[type[BaseModel]] = SearchRunInput

    primitives: ClassVar[Sequence[click.Command]] = (
        _primitive("embed", "Embedding-similarity primitive (developer surface)."),
        _primitive("attribute", "Attribute-filter primitive (developer surface)."),
    )

    def run(self, inputs: BaseModel, ctx: Context) -> Result:
        import asyncio

        if ctx.deployment is None:
            raise config_mod.ConfigError("no deployment configured. Run `vss configure --base-url <origin>` first.")
        from vss_core.search_core.host import VSSSearch

        payload = inputs.model_dump(exclude_none=True, exclude_defaults=True)
        runtime = _runtime_from(ctx.deployment)

        async def _go() -> Any:
            async with VSSSearch.from_runtime(runtime) as vss:
                return await vss.search(**payload)

        output = asyncio.run(_go())
        body = output.model_dump() if hasattr(output, "model_dump") else output
        return Result(body=body, exit=Exit.SUCCESS)


SEARCH = SearchGroup()

__all__ = ["SEARCH", "SearchGroup", "SearchRunInput"]
