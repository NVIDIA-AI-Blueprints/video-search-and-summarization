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
"""Search domain implementation for the extensible VSS CLI.

A single search domain dispatches every primitive:

    vss search {run,embed,attribute} [--runtime-flags...] [--config <path>] [--json '<payload>']
       runtime:   backend URLs and runtime knobs are passed explicitly as CLI
                  flags. The CLI intentionally does not read endpoint env vars
                  or $VSS_AGENT_CONFIG_FILE.
       --config:  optional explicit NAT-style config file path. Interpolation
                  values may be supplied with --config-env KEY=VALUE.
                  Process-environment fallback is intentionally not supported.
       --json:    payload as a JSON object matching the input model.
       stdin:     alternative payload source. When both --json and piped stdin
                  are present, --json takes precedence (stdin is not read).

    The ``search`` primitive additionally accepts agent-friendly, already-
    decomposed fields as flags (``--query``, ``--search-mode``, ``--attribute``,
    ``--object-id``, ``--video-source``, ``--timestamp-start/-end``, ``--top-k``,
    ``--min-cosine-similarity``, ``--description``, ``--decomposed-json``) so a host agent can invoke it directly without hand-writing
    a JSON payload. An explicit ``--json``/stdin
    payload takes precedence when present. Query decomposition is host-agent-owned:
    the CLI consumes fields that have already been decomposed by the caller.

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
from collections.abc import Mapping
from dataclasses import replace as dataclass_replace
import json
import logging
from pathlib import Path
import re
import sys
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal
from typing import get_args

import httpx
from pydantic import ValidationError

from lib._foundation.errors import BackendUnreachableError
from lib.search_core.errors import ConfigurationError
from lib.search_core.errors import IndexNotFoundError
from lib.search_core.errors import InvalidInputError
from lib.search_core.models.common import FusionMethod

from .search_operations import SEARCH_OPERATIONS

_PREFLIGHT_MAX_ATTEMPTS = 3
_PREFLIGHT_RETRY_STATUS_CODES = frozenset({429, 502, 503, 504})
_PREFLIGHT_RETRY_DELAYS_SECONDS = (0.25, 0.5)

if TYPE_CHECKING:
    from lib.search_core.runtime import SearchRuntime

logger = logging.getLogger(__name__)

SOURCE_TYPES = ("video_file", "rtsp")
# Derived from the shared FusionMethod literal so CLI choices can never drift
# from the strategies the orchestrator actually implements.
FUSION_METHODS = get_args(FusionMethod)


def _required_runtime_args(primitive: str) -> tuple[tuple[str, str], ...]:
    """Return the (attr, flag) pairs a given primitive actually requires.

    ``embed_search`` never touches the RTVI-CV text embedder and
    ``attribute_search`` never touches the Cosmos embed service, so forcing
    those endpoints for every primitive would reject valid invocations.
    ``search`` can route across both surfaces and therefore needs all five.
    """
    if primitive == "embed_search":
        return (
            ("es_endpoint", "--es-endpoint"),
            ("cosmos_embed_endpoint", "--cosmos-embed-endpoint"),
            ("vst_external_url", "--vst-external-url"),
        )
    if primitive == "attribute_search":
        return (
            ("es_endpoint", "--es-endpoint"),
            ("rtvi_cv_endpoint", "--rtvi-cv-endpoint"),
            ("vst_internal_url", "--vst-internal-url"),
            ("vst_external_url", "--vst-external-url"),
        )
    return (
        ("es_endpoint", "--es-endpoint"),
        ("cosmos_embed_endpoint", "--cosmos-embed-endpoint"),
        ("rtvi_cv_endpoint", "--rtvi-cv-endpoint"),
        ("vst_internal_url", "--vst-internal-url"),
        ("vst_external_url", "--vst-external-url"),
    )


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
    runtime.add_argument("--default-max-results", type=_parse_positive_int, default=None)
    runtime.add_argument("--request-timeout-seconds", type=_parse_positive_int, default=None)
    runtime.add_argument(
        "--allow-embed-only-fallback",
        action="store_true",
        help=(
            "Permit search to continue without RTVI-CV text embeddings when its capability preflight fails. "
            "The default is to fail rather than silently drop attribute/object search."
        ),
    )
    runtime.add_argument("--embed-confidence-threshold", type=_parse_cosine_similarity, default=None)
    runtime.add_argument("--fusion-method", choices=FUSION_METHODS, default=None)
    runtime.add_argument("--w-attribute", type=float, default=None)
    runtime.add_argument("--w-embed", type=float, default=None)
    runtime.add_argument("--rrf-k", type=_parse_positive_int, default=None)
    runtime.add_argument("--rrf-w", type=float, default=None)
    runtime.add_argument("--top-percent-filter", type=_parse_unit_interval, default=None)
    runtime.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default="WARNING",
        help="Python logging level for the CLI process.",
    )


def _add_output_args(p: argparse.ArgumentParser) -> None:
    output = p.add_argument_group("output options")
    output.add_argument(
        "--output",
        choices=("json", "jsonl", "table"),
        default="json",
        help=(
            "Output format: a single JSON object (default), one JSON object per result row "
            "(jsonl, ideal for piping), or a human-readable table."
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
            "large and rarely needed."
        ),
    )


def _add_search_query_args(p: argparse.ArgumentParser) -> None:
    """Add the agent-friendly, already-decomposed query flags for `search`.

    These are only meaningful for the ``search`` primitive; a host agent uses
    them to invoke search directly without hand-writing a JSON payload.
    An explicit ``--json``/stdin payload takes precedence over these flags.
    """
    group = p.add_argument_group(
        "search query options",
        description="Agent-friendly, already-decomposed fields for the `search` primitive.",
    )
    group.add_argument("--query", default=None, help="Decomposed visual query to embed and search for.")
    group.add_argument(
        "--search-mode",
        choices=("embed", "attribute", "fusion", "object"),
        default=None,
        help="Explicit execution path. Default: embed.",
    )
    group.add_argument(
        "--decomposed-json",
        default=None,
        help=(
            "JSON object produced by the host agent's query decomposition. CLI flags override fields from this object."
        ),
    )
    group.add_argument(
        "--source-type",
        choices=SOURCE_TYPES,
        default=None,
        help="Search ingested video files or RTSP stream embeddings. Default: video_file.",
    )
    group.add_argument(
        "--video-source",
        dest="video_sources",
        action="append",
        default=[],
        help="Restrict search to a registered VIOS source name or sensor ID. May be repeated.",
    )
    group.add_argument(
        "--description",
        default=None,
        help="Optional camera/source metadata filter, such as a location or tag.",
    )
    group.add_argument(
        "--timestamp-start",
        default=None,
        help="Optional ISO-8601 lower bound for result timestamps.",
    )
    group.add_argument(
        "--timestamp-end",
        default=None,
        help="Optional ISO-8601 upper bound for result timestamps.",
    )
    group.add_argument("--top-k", type=_parse_positive_int, default=None, help="Maximum number of results to return.")
    group.add_argument(
        "--min-cosine-similarity",
        type=_parse_cosine_similarity,
        default=None,
        help="Minimum cosine similarity threshold. Default: 0.0.",
    )
    group.add_argument(
        "--attribute",
        dest="attributes",
        action="append",
        default=[],
        help=("Appearance/metadata attribute for attribute or fusion search, e.g. 'white jacket'. May be repeated."),
    )
    group.add_argument(
        "--object-id",
        dest="object_ids",
        action="append",
        type=int,
        default=[],
        help="Search for visually similar tracked objects by object ID. May be repeated.",
    )


def _parse_args(argv: list[str] | None = None, *, operation: str) -> argparse.Namespace:
    """Parse a search-domain invocation.

    ``operation`` is supplied by the root domain dispatcher and is the only
    selector this module accepts. This keeps the old primitive-root grammar out
    of both the installed entry point and the domain parser.
    """
    primitive = SEARCH_OPERATIONS.get(operation)
    if primitive is None:
        raise ValueError(f"unknown search operation: {operation!r}")
    prog = f"vss search {operation}"
    p = argparse.ArgumentParser(
        prog=prog,
        description="Invoke VSS search primitives directly (exec-transport entrypoint).",
    )
    p.set_defaults(primitive=primitive)
    p.add_argument(
        "--json",
        dest="json_payload",
        default=None,
        help="Payload as a JSON object matching the primitive's input model.",
    )
    if primitive == "search":
        _add_search_query_args(p)
    _add_runtime_args(p)
    _add_output_args(p)
    p.set_defaults(cli_name=prog)
    return p.parse_args(argv)


def _build_archive_search_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Convert agent-friendly flags into the structured SearchInput shape.

    Query decomposition and route selection are host-agent concerns; this CLI
    receives fields already extracted by the caller.
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
        "search_mode": args.search_mode or base.get("search_mode", "embed"),
        "attributes": args.attributes or base.get("attributes") or [],
        "object_ids": args.object_ids or base.get("object_ids") or None,
        "min_cosine_similarity": (
            args.min_cosine_similarity
            if args.min_cosine_similarity is not None
            else base.get("min_cosine_similarity", 0.0)
        ),
    }
    return payload


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Read the payload from ``--json`` or stdin.

    ``--json`` takes precedence: when it is provided, stdin is never read (we
    cannot reliably detect redirected-but-empty stdin without blocking, so we
    document the precedence rather than pretending to enforce exclusivity).
    """
    if args.json_payload is not None:
        try:
            parsed = json.loads(args.json_payload)
        except json.JSONDecodeError as e:
            raise InvalidInputError(f"--json is not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise InvalidInputError("--json must be a JSON object")
        return parsed

    if not sys.stdin.isatty():
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise InvalidInputError(f"stdin is not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise InvalidInputError("stdin JSON must be an object")
        return parsed

    return {}


def _build_runtime(args: argparse.Namespace) -> SearchRuntime:
    """Resolve the runtime without reading process environment.

    Runtime values come from explicit CLI flags, optionally seeded by an
    explicit ``--config`` file interpolated with ``--config-env KEY=VALUE``
    pairs. There is intentionally no ``$VSS_AGENT_CONFIG_FILE`` fallback, no
    endpoint-env fallback, and no deployment discovery: the CLI never reads
    config out of a deployed VSS.
    """
    from lib.search_core.runtime import RuntimeSnapshot

    if args.config is not None:
        config_path = Path(args.config)
        if not config_path.exists():
            raise ConfigurationError(f"--config path does not exist: {str(config_path)!r}")
        # When --config is explicitly provided we must NOT silently fall back to
        # args-only construction on a ConfigurationError: doing so would drop
        # every profile knob the config carries (w_*,
        # rrf_*, embed_confidence_threshold, ...). Let the
        # error surface (exit 4) so the operator fixes the config they asked for.
        snap = RuntimeSnapshot.from_config_file(config_path, env=_config_env_from_args(args))
        return _apply_runtime_overrides(snap.runtime, args)

    return _runtime_from_args(args)


def _config_env_from_args(args: argparse.Namespace) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in getattr(args, "config_env", []) or []:
        key, sep, value = item.partition("=")
        if not sep or not key or any(char.isspace() for char in key):
            raise InvalidInputError(f"--config-env must be KEY=VALUE, got {item!r}")
        # A value with an embedded newline could inject arbitrary YAML keys once
        # spliced into --config during interpolation; reject it at the boundary.
        if "\n" in value or "\r" in value:
            raise InvalidInputError(f"--config-env value for {key!r} must not contain newlines")
        env[key] = value
    return env


def _runtime_from_args(args: argparse.Namespace) -> SearchRuntime:
    from lib.search_core.runtime import SearchRuntime

    primitive = getattr(args, "primitive", "search")
    required = list(_required_runtime_args(primitive))
    missing = [flag for attr, flag in required if not getattr(args, attr, None)]
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
    if args.enable_frame_lookup is not None:
        kwargs["enable_frame_lookup"] = args.enable_frame_lookup
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
    "request_timeout_seconds",
    "embed_confidence_threshold",
    "fusion_method",
    "w_attribute",
    "w_embed",
    "rrf_k",
    "rrf_w",
    "top_percent_filter",
)


def _apply_runtime_overrides(runtime: SearchRuntime, args: argparse.Namespace) -> SearchRuntime:
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
    # Only default behavior_es_endpoint from a --es-endpoint override when the
    # config didn't already configure a *distinct* behavior cluster. Otherwise a
    # user tweaking only --es-endpoint would silently clobber a separate
    # behavior_es_endpoint that the config set on purpose.
    base_behavior_is_distinct = (
        runtime.behavior_es_endpoint is not None and runtime.behavior_es_endpoint != runtime.es_endpoint
    )
    if (
        overrides.get("behavior_es_endpoint") is None
        and overrides.get("es_endpoint") is not None
        and not base_behavior_is_distinct
    ):
        overrides["behavior_es_endpoint"] = overrides["es_endpoint"]
    return dataclass_replace(runtime, **overrides) if overrides else runtime


def _effective_search_attributes(payload: dict[str, Any]) -> list[str]:
    """Return the validated, non-blank attribute list."""
    return [
        attribute.strip()
        for attribute in payload.get("attributes") or []
        if isinstance(attribute, str) and attribute.strip()
    ]


def _search_backend_route(payload: dict[str, Any]) -> tuple[bool, bool]:
    """Return ``(uses_embed, uses_attribute)`` using core search semantics."""
    mode = payload.get("search_mode", "embed")
    if mode == "object":
        return False, True
    return mode in {"embed", "fusion"}, mode in {"attribute", "fusion"}


def _activate_embed_only_fallback(payload: dict[str, Any], *, reason: str) -> None:
    """Convert a fusion/attribute request to the explicitly allowed embed route."""
    payload["attributes"] = []
    payload["search_mode"] = "embed"
    sys.stderr.write(f"[vss] RTVI-CV {reason}; continuing with explicit embed-only fallback.\n")


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


async def _close_cli_clients() -> None:
    from lib.search_core.clients.elastic import ElasticClient

    await ElasticClient.close_all()


def _resolve_search_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Build the `search` payload from an explicit --json/stdin payload or flags.

    An explicit ``--json``/stdin payload wins (power-user path); otherwise the
    agent-friendly, already-decomposed flags are assembled into a SearchInput.
    """
    raw = _load_payload(args)
    # An explicit --json payload (even an empty "{}") or a non-empty stdin body is
    # a full SearchInput payload and takes precedence; only fall back to the
    # agent-friendly flags when no explicit payload was supplied at all.
    if args.json_payload is not None or raw:
        return raw
    return _build_archive_search_payload(args)


def _normalized_source_name(value: str) -> str:
    """Normalize source names for human-friendly but non-semantic matching."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


async def _resolve_named_sources(payload: dict[str, Any], runtime: SearchRuntime) -> None:
    """Resolve every requested source against the deployment's own VST listing.

    A named source is a safety constraint, not a suggestion.  We therefore
    permit only an exact match, an exact stream ID, or one *unambiguous*
    normalized substring match.  A missing/ambiguous name stops before any ES
    query runs; it is never replaced with a semantically similar source.
    """
    requested = payload.get("video_sources")
    if not requested:
        return
    if not isinstance(requested, list) or not all(isinstance(source, str) and source.strip() for source in requested):
        raise InvalidInputError("video_sources must be a non-empty list of source names or stream IDs")

    from lib.vst import get_name_to_stream_id_map

    name_to_stream = await get_name_to_stream_id_map(runtime.require("vst_internal_url"))
    if not name_to_stream:
        raise ConfigurationError(
            "the deployment returned no registered VST sources; ingest the named source through the agent before search"
        )
    stream_ids = set(name_to_stream.values())
    resolved: list[str] = []
    for source in requested:
        if source in name_to_stream or source in stream_ids:
            resolved.append(source)
            continue
        normalized = _normalized_source_name(source)
        matches = [
            candidate
            for candidate in name_to_stream
            if normalized
            and (normalized in _normalized_source_name(candidate) or _normalized_source_name(candidate) in normalized)
        ]
        if len(matches) == 1:
            resolved.append(matches[0])
            continue
        available = ", ".join(sorted(name_to_stream))
        if not matches:
            raise ConfigurationError(
                f"named source {source!r} is unavailable. Registered sources: {available}. "
                "Stop and clarify the intended source or ingest it before searching."
            )
        raise ConfigurationError(
            f"named source {source!r} is ambiguous ({', '.join(sorted(matches))}). "
            "Stop and ask the user which registered source to search."
        )
    payload["video_sources"] = resolved


async def _preflight_index(runtime: SearchRuntime, *, source_type: str) -> None:
    """Fail early with useful nearby-index diagnostics when ingestion is absent.

    The preflight must probe the same index expression as the primitive.  In
    particular, RTSP search uses the configured wildcard while excluding the
    uploaded-file index; probing only the latter rejects valid RTSP-only
    deployments.
    """
    from lib.search_core.clients.elastic import ElasticClient
    from lib.search_core.primitives._embed_helpers import select_search_index

    elastic = ElasticClient.from_runtime(runtime)
    search_index = select_search_index(
        source_type,
        video_embed_index=runtime.video_embed_index,
        video_embed_index_wildcard=runtime.video_embed_index_wildcard,
    )
    try:
        response = await elastic.search(index=search_index, body={"size": 0, "query": {"match_all": {}}})
        if source_type == "rtsp":
            shards = response.get("_shards") if isinstance(response, Mapping) else None
            total = shards.get("total") if isinstance(shards, Mapping) else None
            if not isinstance(total, int):
                raise BackendUnreachableError(
                    "elasticsearch",
                    "RTSP index preflight response did not report a concrete shard count",
                )
            if total < 1:
                raise IndexNotFoundError(search_index)
        return
    except IndexNotFoundError as missing:
        candidates: list[str] = []
        try:
            aliases = await elastic.raw.indices.get_alias(index=runtime.video_embed_index_wildcard)
            if isinstance(aliases, Mapping):
                candidates = sorted(str(name) for name in aliases if str(name) != runtime.video_embed_index)
        except Exception:
            # The missing target index is already a fully typed outcome. A
            # diagnostic lookup must not mask it when wildcard privileges are
            # more restricted than ordinary search privileges.
            pass
        if candidates:
            raise IndexNotFoundError(search_index, missing, available_indices=candidates) from missing
        raise


async def _request_preflight_with_retry(
    client: httpx.AsyncClient,
    method: Literal["get", "post"],
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """Issue a bounded preflight request, retrying only transient failures."""
    for attempt in range(_PREFLIGHT_MAX_ATTEMPTS):
        try:
            if method == "get":
                response = await client.get(url, **kwargs)
            else:
                response = await client.post(url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            retryable = error.response.status_code in _PREFLIGHT_RETRY_STATUS_CODES
            if not retryable or attempt == _PREFLIGHT_MAX_ATTEMPTS - 1:
                raise
        except httpx.TransportError:
            if attempt == _PREFLIGHT_MAX_ATTEMPTS - 1:
                raise
        await asyncio.sleep(_PREFLIGHT_RETRY_DELAYS_SECONDS[attempt])

    raise AssertionError("preflight retry loop exhausted without returning or raising")


async def _preflight_embed_model(runtime: SearchRuntime) -> None:
    """Verify that the deployed embed service exposes the requested model ID.

    We deliberately never select an arbitrary returned model.  A configured
    value is preserved, and a default/mistyped value fails with the IDs an
    operator can choose from explicitly.
    """
    endpoint = runtime.require("cosmos_embed_endpoint")
    url = f"{endpoint.rstrip('/')}/v1/models"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await _request_preflight_with_retry(client, "get", url)
            payload = response.json()
    except httpx.HTTPError as e:
        raise BackendUnreachableError("cosmos_embed", f"model preflight at {url} failed: {e}", e) from e
    except (TypeError, ValueError) as e:
        raise ConfigurationError(f"embed model preflight at {url} returned invalid JSON: {e}") from e

    data = payload.get("data") if isinstance(payload, dict) else None
    model_ids = sorted(
        str(item["id"])
        for item in data or []
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    )
    if not model_ids:
        raise ConfigurationError(f"embed model preflight at {url} returned no model IDs")
    if runtime.cosmos_embed_model not in model_ids:
        raise ConfigurationError(
            f"configured embed model {runtime.cosmos_embed_model!r} is unavailable; "
            f"the deployed service exposes: {', '.join(model_ids)}. Pass an explicit --cosmos-embed-model to choose one."
        )


async def _preflight_rtvi_cv(args: argparse.Namespace, payload: dict[str, Any], runtime: SearchRuntime) -> None:
    """Check RTVI-CV text-embedding support before a fusion search can hang."""
    needs_text_embedding = bool(_effective_search_attributes(payload)) and not payload.get("object_ids")
    if args.primitive == "attribute_search":
        needs_text_embedding = True
    if not needs_text_embedding:
        return
    endpoint = runtime.require("rtvi_cv_endpoint")
    url = f"{endpoint.rstrip('/')}/api/v1/generate_text_embeddings"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)) as client:
            response = await _request_preflight_with_retry(
                client,
                "post",
                url,
                json={"text_input": "vss capability probe", "model": ""},
            )
            body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("data"), list) or not body["data"]:
            raise ValueError("response did not contain text embedding data")
    except (httpx.HTTPError, TypeError, ValueError) as e:
        if args.primitive == "search" and args.allow_embed_only_fallback:
            _activate_embed_only_fallback(payload, reason="text embeddings are unavailable")
            return
        raise ConfigurationError(
            "RTVI-CV text-embedding capability preflight failed. "
            "Fix the deployed RTVI-CV service or pass --allow-embed-only-fallback for a deliberate embed-only search. "
            f"Details: {e}"
        ) from e


async def _preflight_search_runtime(args: argparse.Namespace, payload: dict[str, Any], runtime: SearchRuntime) -> None:
    """Run inexpensive, typed deployment checks before invoking a primitive."""
    # RTVI fallback mutates the payload into an embed-only request, so run it
    # first and then compute the same post-pruning route as the core primitive.
    needs_rtvi_preflight = args.primitive == "attribute_search" or (
        args.primitive == "search" and not payload.get("object_ids") and bool(_effective_search_attributes(payload))
    )
    if needs_rtvi_preflight:
        await _preflight_rtvi_cv(args, payload, runtime)
    search_uses_embed = args.primitive == "search" and _search_backend_route(payload)[0]
    if search_uses_embed:
        await _preflight_index(runtime, source_type=str(payload.get("source_type") or "video_file"))
    if search_uses_embed or args.primitive == "embed_search":
        await _preflight_embed_model(runtime)


def _replace_payload_with_model(payload: dict[str, Any], model: Any) -> None:
    """Make routing and invocation consume the same Pydantic-normalized values."""
    normalized = model.model_dump(mode="python")
    payload.clear()
    payload.update(normalized)


def _validate_payload_before_preflight(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    """Keep invalid input deterministic; never make backend calls for it."""
    if args.primitive == "search":
        from lib.search_core.models.search import SearchInput

        search_input = SearchInput(**payload)
        search_input.validate_semantics()
        _replace_payload_with_model(payload, search_input)
        return
    if args.primitive == "embed_search":
        from lib.search_core.models.embed_search import EmbedSearchInput

        embed_input = EmbedSearchInput(**payload)
        embed_input.validate_semantics()
        _replace_payload_with_model(payload, embed_input)
        return
    if args.primitive == "attribute_search":
        from lib.search_core.models.attribute_search import AttributeSearchInput

        attribute_input = AttributeSearchInput(**payload)
        attribute_input.validate_semantics()
        _replace_payload_with_model(payload, attribute_input)
        return


async def _search_preflights(args: argparse.Namespace, payload: dict[str, Any], runtime: SearchRuntime) -> None:
    """Named-source resolution + typed deployment checks (one event loop)."""
    try:
        await _resolve_named_sources(payload, runtime)
        await _preflight_search_runtime(args, payload, runtime)
    finally:
        # Clients created here are bound to this (about to close) loop.
        await _close_cli_clients()


def _run_search_pipeline(payload: dict[str, Any], runtime: SearchRuntime) -> Any:
    """Execute `search` through the synchronous Ranks pipeline facade."""
    from lib.search_core.models.search import SearchInput
    from lib.search_core.pipeline.bridge import deps_from_runtime
    from lib.search_core.pipeline.facade import SearchParams
    from lib.search_core.pipeline.facade import run_search

    search_input = SearchInput(**payload)
    top_k = search_input.top_k if search_input.top_k is not None else runtime.default_max_results
    deps = deps_from_runtime(
        runtime,
        source_type=search_input.source_type,
        top_k=top_k,
        video_sources=search_input.video_sources,
        timestamp_start=search_input.timestamp_start,
        timestamp_end=search_input.timestamp_end,
    )
    params = SearchParams(
        default_max_results=runtime.default_max_results,
        embed_confidence_threshold=runtime.embed_confidence_threshold,
        fusion_method=runtime.fusion_method,
        w_attribute=runtime.w_attribute,
        w_embed=runtime.w_embed,
        rrf_k=runtime.rrf_k,
        rrf_w=runtime.rrf_w,
        top_percent_filter=runtime.top_percent_filter,
    )
    return run_search(search_input, deps, params)


async def _run_primitive(primitive: str, payload: dict[str, Any], runtime: SearchRuntime) -> Any:
    """Run one low-level primitive (embed_search / attribute_search) directly."""
    if primitive == "embed_search":
        from lib.search_core.models.embed_search import EmbedSearchInput
        from lib.search_core.primitives.embed_search import EmbedSearch

        embed_engine = EmbedSearch.from_runtime(runtime)
        try:
            return await embed_engine.run(EmbedSearchInput(**payload))
        finally:
            await embed_engine.aclose()
    from lib.search_core.models.attribute_search import AttributeSearchInput
    from lib.search_core.primitives.attribute_search import AttributeSearch

    attribute_engine = AttributeSearch.from_runtime(runtime)
    try:
        return await attribute_engine.run(AttributeSearchInput(**payload))
    finally:
        await attribute_engine.aclose()


def _run(args: argparse.Namespace) -> int:
    if args.primitive == "search":
        payload = _resolve_search_payload(args)
    else:
        payload = _load_payload(args)

    try:
        # Validate before any backend call: invalid input must exit 2
        # deterministically without touching the deployment.
        _validate_payload_before_preflight(args, payload)
        runtime = _build_runtime(args)
        if args.primitive == "search":
            asyncio.run(_search_preflights(args, payload, runtime))
            out = _run_search_pipeline(payload, runtime)
        else:
            out = asyncio.run(_run_primitive(args.primitive, payload, runtime))
        sys.stdout.write(_render_output(out, args) + "\n")
        sys.stdout.flush()
        return 0
    finally:
        asyncio.run(_close_cli_clients())


def _invoke(argv: list[str] | None = None, *, operation: str) -> int:
    try:
        args = _parse_args(argv, operation=operation)
        logging.basicConfig(level=args.log_level)
        return _run(args)
    except InvalidInputError as e:
        sys.stderr.write(f"[vss] invalid input: {e}\n")
        return 2
    except ValidationError as e:
        sys.stderr.write(f"[vss] invalid input: {e}\n")
        return 2
    except BackendUnreachableError as e:
        sys.stderr.write(f"[vss] backend unreachable: {e}\n")
        return 3
    except ConfigurationError as e:
        sys.stderr.write(f"[vss] configuration error: {e}\n")
        return 4
    except NotImplementedError as e:
        sys.stderr.write(f"[vss] not yet implemented: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"[vss] unexpected error: {e!r}\n")
        return 1


def run(operation: str, argv: list[str] | None = None) -> int:
    """Run one root-dispatched search operation."""
    return _invoke(argv, operation=operation)
