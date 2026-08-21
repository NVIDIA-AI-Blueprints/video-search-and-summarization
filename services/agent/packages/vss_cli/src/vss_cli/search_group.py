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
from pydantic import ConfigDict
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

    from vss_core.critic import CriticAgent
    from vss_core.vlm import OpenAIVLMAnalyzer

#: Prefix of the raw family. Its presence in the recorded inventory is the one
#: thing discovery (`vss configure` reads ES `_cat/indices`) decides: whether
#: frame-level lookups are enabled. Bases and wildcards are pinned
#: `SearchRuntime` defaults, never discovered; see :func:`_runtime_from`.
_RAW_INDEX_PREFIX = "mdx-raw-"


class _Common(BaseModel):
    """Fields every retrieval path accepts."""

    # Unknown keys are an error, not something to drop. Click rejects unknown
    # flags itself, so this guards the programmatic callers -- a plugin, or the
    # MCP tool surface these models will back -- where pydantic would otherwise
    # ignore a misspelled key and silently use the default.
    model_config = ConfigDict(extra="forbid")

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
    """Semantic similarity against video-chunk embeddings.

    The query text is embedded by RT-Embed (the deployment's cosmos-embed
    model, whichever `vss configure` recorded) and matched by cosine
    similarity against the per-chunk vectors in `mdx-embed-filtered-*`. Those
    vectors were produced at ingest time from the video frames themselves, so
    this finds footage that *looks like* the description.

    It cannot filter on detections: object classes, colours and other
    attributes live in a different index and are not part of the embedding.
    Use `attribute` for those, or `fusion` to rank embedding hits by them.

    Contiguous matching windows are merged into one result by default; pass
    --no-merge-adjacent for the raw chunks.
    """

    query: str = Field(..., description="Text to embed and match against video embeddings.")
    description: str | None = Field(None, description="Free-text description accompanying the query.")
    min_cosine_similarity: float | None = Field(
        None, ge=-1.0, le=1.0, description="Minimum cosine similarity threshold."
    )


class AttributeInput(_Common):
    """Structured match against detected-object attributes.

    Queries `mdx-behavior-*`, the documents RT-CV writes for every object it
    detects and tracks (class, colour, and other extracted attributes), and
    optionally `mdx-raw-*` for frame-level lookups. No embeddings are involved
    and nothing is embedded at query time.

    This is the right path when the thing you are looking for is a property a
    detector reports ("white jacket", "forklift") rather than a scene you
    would describe in prose. It will not find anything the CV pipeline did not
    detect, however well it matches the words.
    """

    attributes: list[str] = Field(
        ...,
        description="Appearance/metadata attribute, e.g. 'white jacket'; repeatable.",
        json_schema_extra={"cli_flag": "--attribute"},
    )


class FusionInput(_Common):
    """Embedding retrieval, re-ranked by attribute evidence.

    The two legs are NOT symmetric, and this is the thing to understand
    before using it: the embedding leg decides *which* results exist, and the
    attribute leg only decides how they are *ordered*.

    \b
    1. --query is embedded and matched against `mdx-embed-filtered-*`,
       producing the candidate set (identical to `run embed`).
    2. For each candidate, `mdx-behavior-*` is queried for the --attribute
       terms within that candidate's sensor and time window. Per-candidate
       attribute scores are summed, then normalised by how many attributes
       were supplied.
    3. The two scores are combined by --fusion-method:
         rrf (default)            1/(embed_rank + rrf_k) + rrf_w * attr_score
         weighted_linear          w_embed * embed_score + w_attribute * attr_score
         rrf_with_attribute_rank  as rrf, but ranked on the attribute side

    Consequence: an object matching every attribute is unreachable if the
    embedding leg did not surface its window. Attributes cannot add results,
    only reorder them. If you want attribute matches regardless of visual
    similarity, use `run attribute`.

    Fallback: when the best embed score is below --embed-confidence-threshold
    the embedding leg is judged uninformative and the search degrades to
    attribute-only.
    """

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
    """Retrieve every window containing specific tracked objects.

    Looks up the given object ids -- the tracker-assigned identities carried
    in `mdx-behavior-*` -- and returns their windows directly. No text is
    embedded and no similarity is computed; this is an identity lookup, not a
    search, and is the path to use after another search has surfaced an
    object id you want to follow through the footage.
    """

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
    no_merge_adjacent: bool = Field(
        False,
        description=(
            "Report raw retrieval windows instead of merging contiguous same-sensor "
            "ones into a single result. Merging is on by default; this matches what "
            "the agent's search API returns."
        ),
        json_schema_extra={"cli_flag": "--no-merge-adjacent"},
    )


def _deployment_or_raise() -> config_mod.Deployment:
    """The recorded deployment, or a ConfigError the root maps to exit 4."""
    return config_mod.load()


def _runtime_from(deployment: config_mod.Deployment, tuning: dict[str, Any] | None = None) -> Any:
    """Build a SearchRuntime from the recorded deployment.

    Every endpoint here was reported by a backend, not typed by a caller -- the
    point of `vss configure`. Indices are not: bases and wildcards are pinned
    `SearchRuntime` defaults, and only the raw family's presence is read, to
    enable frame lookups.

    Nothing is required *here*: the framework has already checked the action's
    declared :attr:`~vss_cli.group.Action.requires` against the deployment, so
    a service still absent at this point is one the action does not call.
    Resolving it to None keeps the deployment usable for the paths it can
    serve instead of failing them all on the strictest path's needs.
    """
    from vss_core.search_core.runtime import RAW_INDEX_ANCHOR
    from vss_core.search_core.runtime import SearchRuntime

    es = deployment.endpoint_or_none("elasticsearch")
    embed_service = deployment.services.get("rt_embed")
    es_service = deployment.services.get("elasticsearch")
    # VST takes the *origin*, not the mount: search_core appends the
    # `/vst/api/v1/...` prefix itself, so handing it the mounted `.../vst`
    # yields `/vst/vst/api/v1/...`. Absent VST, the search still runs and
    # simply returns no media links.
    vst = deployment.base_url if deployment.has("vst") else None

    kwargs: dict[str, Any] = {
        "es_endpoint": es,
        "behavior_es_endpoint": es,
        "cosmos_embed_endpoint": deployment.endpoint_or_none("rt_embed"),
        "rtvi_cv_endpoint": deployment.endpoint_or_none("rtvi_cv"),
        "vst_internal_url": vst,
        "vst_external_url": vst,
    }
    if embed_service and embed_service.models:
        kwargs["cosmos_embed_model"] = embed_service.models[0]

    # Bases and family wildcards are the pinned ``SearchRuntime`` anchor defaults
    # and are NOT discovered: promoting a discovered index to a base inverts
    # ``rtsp`` source-type selection (it subtracts the only index holding live
    # data in a stream-first deployment), and every family wildcard already
    # equals its default. Discovery's only load-bearing output is enabling frame
    # lookups when the raw family exists, pinned to the raw uploads anchor;
    # otherwise ``frames_index`` stays ``None`` and frame-level lookups are off.
    available = es_service.indices if es_service else []
    if any(i.startswith(_RAW_INDEX_PREFIX) for i in available):
        kwargs["frames_index"] = RAW_INDEX_ANCHOR

    kwargs.update(tuning or {})
    return SearchRuntime(**kwargs)


async def _rt_vlm_available(service_url: str, model: str) -> bool:
    """Return whether the configured RT-VLM route currently serves ``model``.

    Deployment configuration is a snapshot and may outlive the service. Probe
    once before an all-hit critic run so an outage does not fan out into one
    retried request per search result.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{service_url.rstrip('/')}/v1/models")
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return False
    return any(isinstance(item, dict) and item.get("id") == model for item in payload["data"])


async def _critic_from(
    deployment: config_mod.Deployment,
) -> tuple[CriticAgent | None, OpenAIVLMAnalyzer | None]:
    """Build the reusable critic stack when this deployment exposes one.

    RT-VLM is optional for archive search. Returning ``(None, None)`` keeps
    retrieval available and causes the result model's fail-open ``unverified``
    state to remain untouched.
    """
    rt_vlm = deployment.services.get("rt_vlm")
    if not deployment.has("vst") or rt_vlm is None or not rt_vlm.url or not rt_vlm.models:
        return None, None
    if not await _rt_vlm_available(rt_vlm.url, rt_vlm.models[0]):
        return None, None

    from vss_core.critic import CriticAgent
    from vss_core.vlm import OpenAIVLMAnalyzer
    from vss_core.vst import VSTClient

    vst = VSTClient(
        internal_url=deployment.base_url,
        external_url=deployment.base_url,
    )
    vlm = OpenAIVLMAnalyzer(
        base_url=f"{rt_vlm.url.rstrip('/')}/v1",
        model=rt_vlm.models[0],
        vst=vst,
        # Match the search profile's video_understanding contract: RT-VLM
        # fetches the bounded VST clip. Inlining the MP4 is subject to the
        # proxy's base64-size cap and makes otherwise valid hits unverifiable.
        media_mode="video_url",
        video_url_scope="external",
        # A Cosmos model id does not make this a direct Cosmos NIM endpoint.
        # RT-VLM performs its own preprocessing, so the direct-NIM
        # media_io_kwargs that OpenAIVLMAnalyzer normally adds do not belong
        # in this proxy request.
        cosmos_nim_runtime_options=False,
    )
    return CriticAgent(vlm_analyzer=vlm, vst=vst, time_format="offset"), vlm


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
    #:
    #: `requires` is per path, and deliberately excludes VST: it only mints
    #: media links, so a deployment without it still searches. `embed` not
    #: requiring `rtvi_cv` is the point -- a deployment running embeddings
    #: without the CV service can serve embedding search, and used to be
    #: refused for a service that path never calls.
    actions: ClassVar[Sequence[Action]] = (
        Action(
            "embed",
            "Semantic similarity over video-chunk embeddings (mdx-embed-*).",
            EmbedInput,
            requires=frozenset({"elasticsearch", "rt_embed"}),
        ),
        Action(
            "attribute",
            "Structured match over detected-object attributes (mdx-behavior-*).",
            AttributeInput,
            requires=frozenset({"elasticsearch", "rtvi_cv"}),
        ),
        Action(
            "fusion",
            "Embedding retrieval re-ranked by attribute evidence.",
            FusionInput,
            requires=frozenset({"elasticsearch", "rt_embed", "rtvi_cv"}),
        ),
        Action(
            "object",
            "Identity lookup by tracked object id.",
            ObjectInput,
            requires=frozenset({"elasticsearch", "rtvi_cv"}),
        ),
    )
    extra_params: ClassVar[Sequence[click.Parameter]] = tuple(params_mod.options_from_model(SearchTuning))

    def run(self, action: str, inputs: BaseModel, ctx: Context) -> Result:
        import asyncio

        from vss_core.search_core.host import VSSSearch

        deployment = ctx.deployment or _deployment_or_raise()
        payload = inputs.model_dump(exclude_none=True, exclude_defaults=True)
        # Tuning arrives via extra_params, never the request: SearchInput is
        # extra=forbid, so these would be a hard validation error in payload.
        tuning = {k: v for k, v in ctx.extra.items() if k in SearchTuning.model_fields}
        # The flag reads as a negation; the runtime field is positive.
        if tuning.pop("no_merge_adjacent", False):
            tuning["merge_adjacent"] = False
        # The library still selects a path by `search_mode`; the CLI just no
        # longer asks the caller to name it. The sub-action is the mode.
        payload["search_mode"] = action
        runtime = _runtime_from(deployment, tuning)

        # Validate the complete library request before probing optional
        # verification infrastructure.  Besides preserving the CLI's usage
        # error semantics, this guarantees invalid input has no network side
        # effects (the facade validates the same model again at dispatch).
        # Field constraints alone are not enough: the cross-field errors the
        # CLI actually produces -- an empty query on embed/fusion, no
        # attributes on attribute/fusion -- only surface from
        # validate_semantics(), so without it `_critic_from` would still make
        # its RT-VLM probe before the usage error appeared.
        from vss_core.search_core.models.search import SearchInput

        SearchInput(**payload).validate_semantics()

        async def _go() -> Any:
            critic, vlm = await _critic_from(deployment)
            try:
                async with VSSSearch.from_runtime(runtime, critic=critic) as vss:
                    return await vss.search(**payload)
            finally:
                if vlm is not None:
                    await vlm.aclose()

        output = asyncio.run(_go())
        body = output.model_dump() if hasattr(output, "model_dump") else output
        return Result(body=body, exit=Exit.SUCCESS)


SEARCH = SearchGroup()

__all__ = ["SEARCH", "AttributeInput", "EmbedInput", "FusionInput", "ObjectInput", "SearchGroup"]
