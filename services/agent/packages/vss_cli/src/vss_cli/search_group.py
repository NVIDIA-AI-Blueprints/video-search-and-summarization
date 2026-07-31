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

from pydantic import BaseModel
from pydantic import Field

from . import config as config_mod
from . import params as params_mod
from .exits import Exit
from .group import Action
from .group import CommandGroup
from .group import Context
from .group import Result

if TYPE_CHECKING:
    from collections.abc import Sequence

    import click

#: Index families the deployment reports, mapped to the runtime field that
#: consumes them. Discovered rather than declared -- `vss configure` reads
#: them from Elasticsearch's own _cat/indices.
_INDEX_PREFIXES = {
    "video_embed_index": "mdx-embed-filtered-",
    "behavior_index": "mdx-behavior-",
    "frames_index": "mdx-raw-",
}


class _Common(BaseModel):
    """Fields every retrieval path accepts."""

    source_type: Literal["video_file", "rtsp"] | None = Field(None, description="Media source type.")
    video_sources: list[str] = Field(
        default_factory=list,
        description="Registered source name to search; repeatable.",
        json_schema_extra={"cli_flag": "--video-source"},
    )
    timestamp_start: str | None = Field(None, description="Absolute ISO-8601 window start.")
    timestamp_end: str | None = Field(None, description="Absolute ISO-8601 window end.")
    top_k: int | None = Field(None, ge=1, le=1000, description="Maximum results to return.")


class EmbedInput(_Common):
    """`vss search run embed` -- embedding similarity only."""

    query: str = Field(..., description="Text to embed and match against video embeddings.")
    description: str | None = Field(None, description="Free-text description accompanying the query.")
    min_cosine_similarity: float | None = Field(
        None, ge=-1.0, le=1.0, description="Minimum cosine similarity threshold."
    )


class AttributeInput(_Common):
    """`vss search run attribute` -- attribute filtering only."""

    attributes: list[str] = Field(
        ...,
        description="Appearance/metadata attribute, e.g. 'white jacket'; repeatable.",
        json_schema_extra={"cli_flag": "--attribute"},
    )


class FusionInput(_Common):
    """`vss search run fusion` -- both legs, fused."""

    query: str = Field(..., description="Visual query for the embedding leg.")
    description: str | None = Field(None, description="Free-text description accompanying the query.")
    attributes: list[str] = Field(
        ...,
        description="Attribute for the attribute leg; repeatable.",
        json_schema_extra={"cli_flag": "--attribute"},
    )
    min_cosine_similarity: float | None = Field(
        None, ge=-1.0, le=1.0, description="Minimum cosine similarity threshold."
    )


class ObjectInput(_Common):
    """`vss search run object` -- retrieval by tracked object id."""

    object_ids: list[int] = Field(
        ...,
        description="Tracked object id; repeatable.",
        json_schema_extra={"cli_flag": "--object-id"},
    )


class SearchTuning(BaseModel):
    """Retrieval tuning. Configures the *runtime*, not the request.

    ``SearchInput`` is ``extra=forbid``, so passing any of these as part of
    the request is a hard validation error -- they construct
    ``SearchRuntime`` instead. Unset means "use the deployment's value", so
    an omitted flag never silently overrides configuration.
    """

    fusion_method: Literal["weighted_linear", "rrf", "rrf_with_attribute_rank"] | None = Field(
        None, description="How embed and attribute legs are combined."
    )
    w_attribute: float | None = Field(None, ge=0.0, le=1.0, description="Attribute leg weight.")
    w_embed: float | None = Field(None, ge=0.0, le=1.0, description="Embedding leg weight.")
    rrf_k: int | None = Field(None, ge=1, description="Reciprocal-rank-fusion k.")
    rrf_w: float | None = Field(None, ge=0.0, le=1.0, description="Reciprocal-rank-fusion weight.")
    top_percent_filter: float | None = Field(None, ge=0.0, le=1.0, description="Keep only the top fraction of hits.")
    embed_confidence_threshold: float | None = Field(
        None, ge=0.0, le=1.0, description="Score floor below which fusion falls back to attribute-only."
    )


def _deployment_or_raise() -> config_mod.Deployment:
    """The recorded deployment, or a ConfigError the root maps to exit 4."""
    return config_mod.load()


def _runtime_from(deployment: config_mod.Deployment, tuning: dict[str, Any] | None = None) -> Any:
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

    kwargs.update(tuning or {})
    return SearchRuntime(**kwargs)


class SearchGroup(CommandGroup):
    """Search indexed video."""

    name: ClassVar[str] = "search"
    summary: ClassVar[str] = "Search indexed video"

    #: The four retrieval paths, each with only the fields it accepts. This
    #: replaces `--search-mode`: the old flag put every path's fields on one
    #: command, so `SearchInput` needed runtime rules to reject the nonsense
    #: combinations ("search_mode='embed' does not accept attributes",
    #: "search_mode='object' requires at least one object_id"). Those states
    #: are now unrepresentable -- `run embed` has no --attribute to pass.
    actions: ClassVar[Sequence[Action]] = (
        Action("embed", "Embedding similarity only.", EmbedInput),
        Action("attribute", "Attribute filtering only.", AttributeInput),
        Action("fusion", "Both legs, fused.", FusionInput),
        Action("object", "Retrieval by tracked object id.", ObjectInput),
    )
    extra_params: ClassVar[Sequence[click.Parameter]] = tuple(params_mod.options_from_model(SearchTuning))

    def run(self, action: str, inputs: BaseModel, ctx: Context) -> Result:
        import asyncio

        from vss_core.search_core.host import VSSSearch

        deployment = ctx.deployment or _deployment_or_raise()
        payload = inputs.model_dump(exclude_none=True, exclude_defaults=True)
        tuning = {k: payload.pop(k) for k in list(payload) if k in SearchTuning.model_fields}
        # The library still selects a path by `search_mode`; the CLI just no
        # longer asks the caller to name it. The sub-action is the mode.
        payload["search_mode"] = action
        runtime = _runtime_from(deployment, tuning)

        async def _go() -> Any:
            async with VSSSearch.from_runtime(runtime) as vss:
                return await vss.search(**payload)

        output = asyncio.run(_go())
        body = output.model_dump() if hasattr(output, "model_dump") else output
        return Result(body=body, exit=Exit.SUCCESS)


SEARCH = SearchGroup()

__all__ = ["SEARCH", "AttributeInput", "EmbedInput", "FusionInput", "ObjectInput", "SearchGroup"]
