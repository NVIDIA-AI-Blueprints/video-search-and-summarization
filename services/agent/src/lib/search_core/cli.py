# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""CLI entrypoints for the search_core exec transport.

Two console scripts are exposed:

  - ``search-archive``: agent-friendly wrapper for archived-video search.
  - ``vss-cli``: lower-level primitive dispatcher for developers and tests.

Invocation contract (DESIGN.md §10):

    vss-cli <primitive> [--runtime-flags...] [--config <path>] [--json '<payload>'] [--stream]
       <primitive> ∈ embed_search | attribute_search | search | critic
       runtime:   backend URLs and runtime knobs are passed explicitly as CLI
                  flags. The CLI intentionally does not read endpoint env vars
                  or $VSS_AGENT_CONFIG_FILE.
       --config:  optional explicit NAT-style config file path. Interpolation
                  values may be supplied with --config-env KEY=VALUE.
                  Process-environment fallback is intentionally not supported.
       --json:    payload as a JSON object matching the input model.
       --stream:  only valid for `search`; emits SearchEvent JSON lines.
       stdin:     alternative payload source; mutually exclusive with --json.

    Query decomposition is host-agent-owned. `vss-cli search` and
    ``search-archive`` accept fields that have already been decomposed by the
    calling agent.

    Exit codes:
       0   success (one final output produced)
       1   any other unexpected error
       2   invalid input / Pydantic validation error
       3   backend unreachable
       4   configuration error
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace as dataclass_replace
import json
import logging
from pathlib import Path
import sys
from typing import TYPE_CHECKING
from typing import Any

from pydantic import SecretStr
from pydantic import ValidationError

from .errors import BackendUnreachableError
from .errors import ConfigurationError
from .errors import InvalidInputError

if TYPE_CHECKING:
    from .host import VSSSearch

logger = logging.getLogger(__name__)

PRIMITIVES = ("embed_search", "attribute_search", "search", "critic")
SOURCE_TYPES = ("video_file", "rtsp")
_REQUIRED_RUNTIME_ARGS = (
    ("es_endpoint", "--es-endpoint"),
    ("cosmos_embed_endpoint", "--cosmos-embed-endpoint"),
    ("rtvi_cv_endpoint", "--rtvi-cv-endpoint"),
    ("vst_internal_url", "--vst-internal-url"),
    ("vst_external_url", "--vst-external-url"),
)
_STREAM_ERROR_EXIT_CODES = {
    "InvalidInputError": 2,
    "ValidationError": 2,
    "BackendUnreachableError": 3,
    "ConfigurationError": 4,
}


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected boolean, got {value!r}")


def _parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"expected integer, got {value!r}") from e
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"expected integer >= 1, got {value!r}")
    return parsed


def _parse_cosine_similarity(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"expected float, got {value!r}") from e
    if parsed < -1.0 or parsed > 1.0:
        raise argparse.ArgumentTypeError(f"expected value in [-1.0, 1.0], got {value!r}")
    return parsed


def _parse_unit_interval(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"expected float, got {value!r}") from e
    if parsed < 0.0 or parsed > 1.0:
        raise argparse.ArgumentTypeError(f"expected value in [0.0, 1.0], got {value!r}")
    return parsed


def _add_runtime_args(p: argparse.ArgumentParser) -> None:
    runtime = p.add_argument_group(
        "runtime/backend options",
        description=(
            "All backend/runtime values must be passed explicitly. The CLI does not read "
            "$VSS_AGENT_CONFIG_FILE or service endpoint env vars."
        ),
    )
    runtime.add_argument(
        "--config",
        default=None,
        help="Explicit NAT-style config file path. Process-env fallback is intentionally not supported.",
    )
    runtime.add_argument(
        "--config-env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Explicit environment value used only to interpolate --config. "
            "May be repeated; the CLI never reads process env vars."
        ),
    )
    runtime.add_argument("--es-endpoint", default=None, help="Elasticsearch endpoint for video embedding search.")
    runtime.add_argument(
        "--behavior-es-endpoint",
        default=None,
        help="Elasticsearch endpoint for behavior/object search. Defaults to --es-endpoint.",
    )
    runtime.add_argument("--cosmos-embed-endpoint", default=None, help="Cosmos/RTVI Embed service endpoint.")
    runtime.add_argument("--rtvi-cv-endpoint", default=None, help="RTVI-CV text embedding service endpoint.")
    runtime.add_argument("--vst-internal-url", default=None, help="Internal VST URL for timeline/source resolution.")
    runtime.add_argument("--vst-external-url", default=None, help="External VST URL used to build screenshot links.")
    runtime.add_argument(
        "--vlm-base-url", default=None, help="OpenAI-compatible VLM base URL, e.g. http://vlm:8000/v1."
    )
    runtime.add_argument("--vlm-model", dest="vlm_model_name", default=None, help="VLM model/deployment name.")
    runtime.add_argument("--vlm-api-key", default=None, help="Optional VLM API key. Pass explicitly; no env fallback.")
    runtime.add_argument(
        "--vlm-media-mode",
        choices=("video-url", "video-base64", "frame-base64"),
        default="video-url",
        help="Send VST clips as URLs, inline MP4 base64, or sampled JPEG frame base64.",
    )
    runtime.add_argument(
        "--vlm-video-url-scope",
        choices=("internal", "external"),
        default="internal",
        help="Use internal or external VST clip URLs when --vlm-media-mode=video-url.",
    )
    runtime.add_argument("--vlm-max-frames", type=_parse_positive_int, default=None)
    runtime.add_argument("--vlm-max-fps", type=_parse_positive_int, default=None)
    runtime.add_argument(
        "--vst-clip-enable-audio",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Preserve audio in VST clips for audio-capable VLMs.",
    )
    runtime.add_argument(
        "--cosmos-embed-model",
        default=None,
        help="Cosmos embedding model name. Defaults to search_core runtime default when omitted.",
    )
    runtime.add_argument("--video-embed-index", default=None, help="Video embedding index for uploaded video files.")
    runtime.add_argument(
        "--video-embed-index-wildcard",
        default=None,
        help="Wildcard index pattern for RTSP video embeddings.",
    )
    runtime.add_argument("--behavior-index", default=None, help="Behavior index for attribute/object search.")
    runtime.add_argument("--behavior-index-wildcard", default=None, help="Wildcard behavior index pattern.")
    runtime.add_argument("--frames-index", default=None, help="Optional raw frame index for attribute frame lookup.")
    runtime.add_argument("--frames-index-wildcard", default=None, help="Wildcard raw frame index pattern.")
    runtime.add_argument(
        "--enable-frame-lookup",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable attribute-search frame lookup.",
    )
    runtime.add_argument(
        "--enable-critic",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable critic wiring in the search runtime.",
    )
    runtime.add_argument(
        "--critic-time-format",
        choices=("iso", "offset"),
        default=None,
        help="Timestamp format used for critic clip extraction.",
    )
    runtime.add_argument(
        "--critic-evaluation-count",
        type=_parse_positive_int,
        default=None,
        help="Maximum number of candidate clips to verify with the critic.",
    )
    runtime.add_argument("--default-max-results", type=_parse_positive_int, default=None)
    runtime.add_argument("--embed-default-max-results", type=_parse_positive_int, default=None)
    runtime.add_argument("--request-timeout-seconds", type=_parse_positive_int, default=None)
    runtime.add_argument(
        "--use-attribute-search",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable fusion reranking when attributes and actions are supplied.",
    )
    runtime.add_argument("--embed-confidence-threshold", type=_parse_cosine_similarity, default=None)
    runtime.add_argument("--search-max-iterations", type=_parse_positive_int, default=None)
    runtime.add_argument("--fusion-method", choices=("weighted_linear", "rrf"), default=None)
    runtime.add_argument("--w-attribute", type=float, default=None)
    runtime.add_argument("--w-embed", type=float, default=None)
    runtime.add_argument("--rrf-k", type=_parse_positive_int, default=None)
    runtime.add_argument("--rrf-w", type=float, default=None)
    runtime.add_argument("--top-percent-filter", type=_parse_unit_interval, default=None)
    runtime.add_argument("--log-level", default="WARNING", help="Python logging level for the CLI process.")


def _add_output_args(p: argparse.ArgumentParser) -> None:
    output = p.add_argument_group("output options")
    output.add_argument(
        "--output",
        choices=("json", "jsonl", "table"),
        default="json",
        help=(
            "Output format: a single JSON object (default), one JSON object per result row "
            "(jsonl, ideal for piping), or a human-readable table. Ignored for --stream, which "
            "always emits event JSON lines."
        ),
    )
    output.add_argument(
        "--pretty",
        action="store_true",
        help="Indent the JSON output. This is the default when stdout is an interactive terminal.",
    )
    output.add_argument(
        "--raw",
        action="store_true",
        help="Emit compact single-line JSON. This is the default when stdout is not a terminal.",
    )
    output.add_argument(
        "--include-embedding",
        action="store_true",
        help=(
            "Include the query embedding vector in the output. Omitted by default because it is "
            "large and rarely needed; pass this when you want to reuse the vector as a "
            "precomputed_embedding for follow-up searches."
        ),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="vss-cli",
        description="Invoke VSS search primitives directly (exec-transport entrypoint).",
    )
    p.add_argument("primitive", choices=PRIMITIVES, help="Which primitive to invoke.")
    p.add_argument(
        "--json",
        dest="json_payload",
        default=None,
        help="Payload as a JSON object matching the primitive's input model.",
    )
    p.add_argument(
        "--stream",
        action="store_true",
        help="Emit SearchEvent JSON lines instead of a single output JSON. Only valid for `search`.",
    )
    _add_runtime_args(p)
    _add_output_args(p)
    p.set_defaults(cli_name="vss-cli")
    return p.parse_args(argv)


def _parse_archive_search_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="search-archive",
        description="Search archived VSS video through lib.search_core without calling the VSS agent /generate API.",
    )
    p.add_argument("--query", default=None, help="Decomposed visual query to embed and search for.")
    p.add_argument(
        "--decomposed-json",
        default=None,
        help=(
            "JSON object produced by the host agent's query decomposition. CLI flags override fields from this object."
        ),
    )
    p.add_argument(
        "--source-type",
        choices=SOURCE_TYPES,
        default=None,
        help="Search ingested video files or RTSP stream embeddings. Default: video_file.",
    )
    p.add_argument(
        "--video-source",
        dest="video_sources",
        action="append",
        default=[],
        help="Restrict search to a registered VIOS source name or sensor ID. May be repeated.",
    )
    p.add_argument(
        "--description",
        default=None,
        help="Optional camera/source metadata filter, such as a location or tag.",
    )
    p.add_argument(
        "--timestamp-start",
        default=None,
        help="Optional ISO-8601 lower bound for result timestamps.",
    )
    p.add_argument(
        "--timestamp-end",
        default=None,
        help="Optional ISO-8601 upper bound for result timestamps.",
    )
    p.add_argument("--top-k", type=_parse_positive_int, default=None, help="Maximum number of results to return.")
    p.add_argument(
        "--min-cosine-similarity",
        type=_parse_cosine_similarity,
        default=None,
        help="Minimum cosine similarity threshold. Default: 0.0.",
    )
    p.add_argument(
        "--attribute",
        dest="attributes",
        action="append",
        default=[],
        help=("Appearance/metadata attribute for attribute or fusion search, e.g. 'white jacket'. May be repeated."),
    )
    p.add_argument(
        "--has-action",
        type=_parse_bool,
        default=None,
        help=("Set true when the query includes an action plus attributes (fusion); false for attribute-only search."),
    )
    p.add_argument(
        "--object-id",
        dest="object_ids",
        action="append",
        type=int,
        default=[],
        help="Search for visually similar tracked objects by object ID. May be repeated.",
    )
    p.add_argument(
        "--use-critic",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Verify candidate results with the NAT-free VLM critic when VLM runtime args are supplied.",
    )
    p.add_argument(
        "--stream",
        action="store_true",
        help="Emit SearchEvent JSON lines instead of a single output JSON.",
    )
    _add_runtime_args(p)
    _add_output_args(p)
    p.set_defaults(cli_name="search-archive", primitive="search")
    return p.parse_args(argv)


def _build_archive_search_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Convert agent-friendly flags into the structured SearchInput shape.

    The wrapper deliberately sets ``agent_mode=false``. Query decomposition is
    a host-agent concern; this CLI receives fields already extracted by the
    calling agent and invokes the NAT-free library path.
    """
    base: dict[str, Any] = {}
    if args.decomposed_json is not None:
        try:
            parsed = json.loads(args.decomposed_json)
        except json.JSONDecodeError as e:
            raise InvalidInputError(f"--decomposed-json is not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise InvalidInputError("--decomposed-json must be a JSON object")
        base = dict(parsed)

    query = args.query or base.get("query")
    if not query:
        raise InvalidInputError("--query is required unless --decomposed-json includes a non-empty query")

    source_type = args.source_type or base.get("source_type") or "video_file"
    if source_type not in SOURCE_TYPES:
        raise InvalidInputError(f"source_type must be one of {SOURCE_TYPES}, got {source_type!r}")

    payload = {
        "query": query,
        "original_query": base.get("original_query") or base.get("query") or query,
        "source_type": source_type,
        "video_sources": args.video_sources or base.get("video_sources") or None,
        "description": args.description if args.description is not None else base.get("description"),
        "timestamp_start": args.timestamp_start if args.timestamp_start is not None else base.get("timestamp_start"),
        "timestamp_end": args.timestamp_end if args.timestamp_end is not None else base.get("timestamp_end"),
        "top_k": args.top_k if args.top_k is not None else base.get("top_k"),
        "attributes": args.attributes or base.get("attributes") or [],
        "has_action": args.has_action if args.has_action is not None else base.get("has_action"),
        "object_ids": args.object_ids or base.get("object_ids") or None,
        "min_cosine_similarity": (
            args.min_cosine_similarity
            if args.min_cosine_similarity is not None
            else base.get("min_cosine_similarity", 0.0)
        ),
        "agent_mode": False,
    }
    if args.use_critic is not None:
        payload["use_critic"] = args.use_critic
    elif "use_critic" in base:
        payload["use_critic"] = bool(base["use_critic"])
    return payload


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Read payload from --json or stdin; mutually exclusive."""
    if args.json_payload is not None:
        if not sys.stdin.isatty() and sys.stdin.readable() and not sys.stdin.closed:
            # Best-effort detection of "stdin was redirected" without blocking.
            # We can't reliably tell at this point, so the test below favors --json.
            pass
        try:
            parsed: dict[str, Any] = json.loads(args.json_payload)
            return parsed
        except json.JSONDecodeError as e:
            raise InvalidInputError(f"--json is not valid JSON: {e}") from e

    if not sys.stdin.isatty():
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
            return parsed
        except json.JSONDecodeError as e:
            raise InvalidInputError(f"stdin is not valid JSON: {e}") from e

    return {}


def _build_facade(args: argparse.Namespace, search_payload: dict[str, Any] | None = None) -> VSSSearch:
    """Build the facade without reading process environment.

    Runtime values come from explicit CLI flags. ``--config`` can be paired
    with explicit ``--config-env KEY=VALUE`` pairs to reproduce a deployment
    config, but there is intentionally no ``$VSS_AGENT_CONFIG_FILE`` or
    endpoint-env fallback in the CLI layer.
    """
    from .host import VSSSearch
    from .runtime import RuntimeSnapshot

    if args.config is not None:
        if not Path(args.config).exists():
            raise ConfigurationError(f"--config path does not exist: {args.config!r}")
        config_env = _config_env_from_args(args)
        try:
            snap = RuntimeSnapshot.from_config_file(args.config, env=config_env)
        except ConfigurationError:
            if not _has_required_runtime_args(args):
                raise
            snap = RuntimeSnapshot(
                runtime=_runtime_from_args(args),
                search=_search_options_from_args(args, search_payload=search_payload),
            )
        else:
            snap = RuntimeSnapshot(
                runtime=_apply_runtime_overrides(snap.runtime, args),
                search=_search_options_from_args(args, base=snap.search, search_payload=search_payload),
            )
        vlm_analyzer = _vlm_analyzer_from_runtime(snap.runtime, args)
        _validate_critic_request(args, search_payload, snap.runtime, vlm_analyzer)
        return VSSSearch.from_runtime(snap.runtime, search_options=snap.search, vlm_analyzer=vlm_analyzer)

    runtime = _runtime_from_args(args)
    search_options = _search_options_from_args(args, search_payload=search_payload)
    vlm_analyzer = _vlm_analyzer_from_runtime(runtime, args)
    _validate_critic_request(args, search_payload, runtime, vlm_analyzer)
    return VSSSearch.from_runtime(runtime, search_options=search_options, vlm_analyzer=vlm_analyzer)


def _has_required_runtime_args(args: argparse.Namespace) -> bool:
    return all(getattr(args, attr, None) for attr, _flag in _REQUIRED_RUNTIME_ARGS)


def _config_env_from_args(args: argparse.Namespace) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in getattr(args, "config_env", []) or []:
        key, sep, value = item.partition("=")
        if not sep or not key or any(char.isspace() for char in key):
            raise InvalidInputError(f"--config-env must be KEY=VALUE, got {item!r}")
        env[key] = value
    return env


def _runtime_from_args(args: argparse.Namespace):
    from .runtime import SearchRuntime

    missing = [flag for attr, flag in _REQUIRED_RUNTIME_ARGS if not getattr(args, attr, None)]
    if missing:
        raise ConfigurationError(
            "missing required backend/runtime CLI option(s): "
            + ", ".join(missing)
            + ". Provide them explicitly or pass --config with literal runtime values "
            + "or --config-env KEY=VALUE pairs for interpolated deployment config."
        )

    kwargs: dict[str, Any] = {
        "es_endpoint": args.es_endpoint,
        "behavior_es_endpoint": args.behavior_es_endpoint or args.es_endpoint,
        "cosmos_embed_endpoint": args.cosmos_embed_endpoint,
        "rtvi_cv_endpoint": args.rtvi_cv_endpoint,
        "vst_internal_url": args.vst_internal_url,
        "vst_external_url": args.vst_external_url,
    }
    for field in _RUNTIME_OVERRIDE_FIELDS:
        value = getattr(args, field, None)
        if value is not None:
            kwargs[field] = value
    if args.vlm_api_key is not None:
        kwargs["vlm_api_key"] = SecretStr(args.vlm_api_key)
    if args.enable_frame_lookup is not None:
        kwargs["enable_frame_lookup"] = args.enable_frame_lookup
    if args.enable_critic is not None:
        kwargs["enable_critic"] = args.enable_critic
    if args.vst_clip_enable_audio is not None:
        kwargs["vst_clip_enable_audio"] = args.vst_clip_enable_audio
    return SearchRuntime.from_kwargs(**kwargs)


_RUNTIME_OVERRIDE_FIELDS = (
    "cosmos_embed_model",
    "video_embed_index",
    "video_embed_index_wildcard",
    "behavior_index",
    "behavior_index_wildcard",
    "frames_index",
    "frames_index_wildcard",
    "default_max_results",
    "embed_default_max_results",
    "request_timeout_seconds",
    "vlm_base_url",
    "vlm_model_name",
    "vlm_max_frames",
    "vlm_max_fps",
    "embed_confidence_threshold",
    "search_max_iterations",
    "critic_time_format",
    "critic_evaluation_count",
    "fusion_method",
    "w_attribute",
    "w_embed",
    "rrf_k",
    "rrf_w",
    "top_percent_filter",
)


def _apply_runtime_overrides(runtime: Any, args: argparse.Namespace) -> Any:
    overrides: dict[str, Any] = {}
    for field in (
        "es_endpoint",
        "behavior_es_endpoint",
        "cosmos_embed_endpoint",
        "rtvi_cv_endpoint",
        "vst_internal_url",
        "vst_external_url",
        *_RUNTIME_OVERRIDE_FIELDS,
    ):
        value = getattr(args, field, None)
        if value is not None:
            overrides[field] = value
    if args.enable_frame_lookup is not None:
        overrides["enable_frame_lookup"] = args.enable_frame_lookup
    if args.enable_critic is not None:
        overrides["enable_critic"] = args.enable_critic
    if args.vst_clip_enable_audio is not None:
        overrides["vst_clip_enable_audio"] = args.vst_clip_enable_audio
    if args.vlm_api_key is not None:
        overrides["vlm_api_key"] = SecretStr(args.vlm_api_key)
    if overrides.get("behavior_es_endpoint") is None and overrides.get("es_endpoint") is not None:
        overrides["behavior_es_endpoint"] = overrides["es_endpoint"]
    return dataclass_replace(runtime, **overrides) if overrides else runtime


def _search_options_from_args(
    args: argparse.Namespace,
    *,
    base: Any | None = None,
    search_payload: dict[str, Any] | None = None,
) -> Any:
    from .runtime import SearchOptions

    if args.use_attribute_search is not None:
        return SearchOptions(use_attribute_search=args.use_attribute_search)

    if base and getattr(base, "use_attribute_search", False):
        return base

    if _payload_wants_fusion(search_payload):
        return SearchOptions(use_attribute_search=True)

    if base is not None:
        return base

    return SearchOptions()


def _payload_wants_fusion(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    return bool(payload.get("attributes")) and payload.get("has_action") is True


def _payload_requests_critic(args: argparse.Namespace, payload: dict[str, Any] | None) -> bool:
    if getattr(args, "primitive", None) == "critic":
        return True
    if getattr(args, "primitive", None) == "search" and payload:
        return bool(payload.get("use_critic"))
    return False


def _vlm_analyzer_from_runtime(runtime: Any, args: argparse.Namespace) -> Any | None:
    if not runtime.vlm_base_url or not runtime.vlm_model_name:
        return None

    from .clients.vlm_openai import OpenAIVLMAnalyzer
    from .clients.vst import VSTClient

    api_key = runtime.vlm_api_key.get_secret_value() if runtime.vlm_api_key is not None else None
    media_mode = args.vlm_media_mode.replace("-", "_")
    return OpenAIVLMAnalyzer(
        base_url=runtime.vlm_base_url,
        model=runtime.vlm_model_name,
        api_key=api_key,
        vst=VSTClient.from_runtime(runtime),
        timeout_seconds=runtime.request_timeout_seconds,
        media_mode=media_mode,
        video_url_scope=args.vlm_video_url_scope,
        disable_audio=not runtime.vst_clip_enable_audio,
        max_frames=runtime.vlm_max_frames,
        max_fps=runtime.vlm_max_fps,
    )


def _validate_critic_request(
    args: argparse.Namespace,
    payload: dict[str, Any] | None,
    runtime: Any,
    vlm_analyzer: Any | None,
) -> None:
    if not _payload_requests_critic(args, payload):
        return
    if not runtime.enable_critic:
        raise ConfigurationError("critic verification was requested but --no-enable-critic disables critic wiring")
    if vlm_analyzer is None:
        raise ConfigurationError("critic verification requires explicit --vlm-base-url and --vlm-model runtime options")


# Keys under which each primitive/search output nests its list of result rows.
_ROW_KEYS = ("results", "data", "video_results")
_MAX_CELL = 40


def _drop_embeddings(data: Any) -> None:
    """Remove ``query_embedding`` vectors from a dumped output in place.

    The embedding is a large, low-signal artifact for both humans and agents, so
    it is excluded from CLI output unless ``--include-embedding`` is passed.
    """
    if isinstance(data, dict):
        data.pop("query_embedding", None)
        for value in data.values():
            _drop_embeddings(value)
    elif isinstance(data, list):
        for item in data:
            _drop_embeddings(item)


def _extract_rows(model: Any) -> list[dict[str, Any]]:
    """Return the primary list of result rows from an output model.

    Looks for the first known result key (``results`` / ``data`` /
    ``video_results``); if none is present, the whole object is treated as a
    single row.
    """
    data = model.model_dump(mode="json")
    for key in _ROW_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            return [row if isinstance(row, dict) else {"value": row} for row in value]
    return [data]


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))


def _flatten_scalars(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten a row to scalar columns, descending one level into nested dicts.

    Top-level scalars keep their name; nested scalars become ``parent.child``.
    Lists and deeper structures are dropped — the full detail is available via
    ``--output json``/``jsonl``.
    """
    flat: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if _is_scalar(sub_value) or sub_value is None:
                    flat[f"{key}.{sub_key}"] = sub_value
        elif _is_scalar(value) or value is None:
            flat[key] = value
    return flat


def _render_table(rows: list[dict[str, Any]]) -> str:
    """Render result rows as an aligned, truncated text table."""
    if not rows:
        return "(no results)"

    flat_rows = [_flatten_scalars(row) for row in rows]

    columns: list[str] = []
    for flat in flat_rows:
        for key in flat:
            if key not in columns:
                columns.append(key)

    def cell(value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        return text if len(text) <= _MAX_CELL else text[: _MAX_CELL - 1] + "\u2026"

    table = [{col: cell(flat.get(col)) for col in columns} for flat in flat_rows]
    # Drop columns that are empty across every row (keep at least one).
    columns = [col for col in columns if any(row[col] for row in table)] or columns

    widths = {col: max(len(col), *(len(row[col]) for row in table)) for col in columns}
    lines = [
        "  ".join(col.ljust(widths[col]) for col in columns),
        "  ".join("-" * widths[col] for col in columns),
    ]
    lines.extend("  ".join(row[col].ljust(widths[col]) for col in columns) for row in table)
    return "\n".join(lines)


def _render_output(model: Any, args: argparse.Namespace) -> str:
    """Serialize a primitive/search output for stdout in the requested format.

    - ``json`` (default): the full output object. The query embedding is omitted
      unless ``--include-embedding`` is set. Indentation defaults to the
      terminal (pretty on a TTY / ``--pretty``, compact otherwise / ``--raw``).
    - ``jsonl``: one JSON object per result row, ideal for streaming/piping.
    - ``table``: a compact, human-readable table of result rows.
    """
    output_format = getattr(args, "output", "json")

    if output_format == "table":
        return _render_table(_extract_rows(model))
    if output_format == "jsonl":
        return "\n".join(json.dumps(row, separators=(",", ":")) for row in _extract_rows(model))

    # mode="json" yields JSON-native values (enums -> values, datetimes -> ISO
    # strings), matching model_dump_json() while letting us post-process the dict.
    data = model.model_dump(mode="json")
    if not getattr(args, "include_embedding", False):
        _drop_embeddings(data)
    pretty = args.pretty or (not args.raw and sys.stdout.isatty())
    if pretty:
        return json.dumps(data, indent=2)
    return json.dumps(data, separators=(",", ":"))


async def _write_search_stream(stream: Any) -> int:
    exit_code = 0
    async for event in stream:
        sys.stdout.write(event.model_dump_json() + "\n")
        sys.stdout.flush()
        if getattr(event, "type", None) == "error":
            exit_code = _STREAM_ERROR_EXIT_CODES.get(getattr(event, "error_code", ""), 1)
    return exit_code


async def _close_cli_clients() -> None:
    from .clients.elastic import ElasticClient

    await ElasticClient.close_all()


async def _run_archive_search(args: argparse.Namespace) -> int:
    payload = _build_archive_search_payload(args)
    try:
        async with _build_facade(args, payload) as vss:
            if args.stream:
                return await _write_search_stream(vss.search_stream(**payload))

            out = await vss.search(**payload)
            sys.stdout.write(_render_output(out, args) + "\n")
            sys.stdout.flush()
            return 0
    finally:
        await _close_cli_clients()


async def _run(args: argparse.Namespace) -> int:
    if args.stream and args.primitive != "search":
        raise InvalidInputError("--stream is only valid for the `search` primitive")

    payload = _load_payload(args)
    if args.primitive == "search" and payload.get("agent_mode") is True:
        raise InvalidInputError(
            "vss-cli search does not perform NAT query decomposition; set agent_mode=false "
            "or call the NAT search/search_agent function"
        )

    try:
        async with _build_facade(args, payload) as vss:
            if args.primitive == "search" and args.stream:
                return await _write_search_stream(vss.search_stream(**payload))

            out = await getattr(vss, args.primitive)(**payload)
            sys.stdout.write(_render_output(out, args) + "\n")
            sys.stdout.flush()
            return 0
    finally:
        await _close_cli_clients()


def main(argv: list[str] | None = None) -> int:
    """Entrypoint installed via pyproject.toml's [project.scripts]."""
    try:
        args = _parse_args(argv)
        logging.basicConfig(level=args.log_level)
        return asyncio.run(_run(args))
    except InvalidInputError as e:
        sys.stderr.write(f"[vss-cli] invalid input: {e}\n")
        return 2
    except ValidationError as e:
        sys.stderr.write(f"[vss-cli] invalid input: {e}\n")
        return 2
    except BackendUnreachableError as e:
        sys.stderr.write(f"[vss-cli] backend unreachable: {e}\n")
        return 3
    except ConfigurationError as e:
        sys.stderr.write(f"[vss-cli] configuration error: {e}\n")
        return 4
    except NotImplementedError as e:
        sys.stderr.write(f"[vss-cli] not yet implemented: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"[vss-cli] unexpected error: {e!r}\n")
        return 1


def archive_search_main(argv: list[str] | None = None) -> int:
    """Agent-friendly ``search-archive`` console script."""
    try:
        args = _parse_archive_search_args(argv)
        logging.basicConfig(level=args.log_level)
        return asyncio.run(_run_archive_search(args))
    except InvalidInputError as e:
        sys.stderr.write(f"[search-archive] invalid input: {e}\n")
        return 2
    except ValidationError as e:
        sys.stderr.write(f"[search-archive] invalid input: {e}\n")
        return 2
    except BackendUnreachableError as e:
        sys.stderr.write(f"[search-archive] backend unreachable: {e}\n")
        return 3
    except ConfigurationError as e:
        sys.stderr.write(f"[search-archive] configuration error: {e}\n")
        return 4
    except Exception as e:
        sys.stderr.write(f"[search-archive] unexpected error: {e!r}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
