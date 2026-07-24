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
"""SearchRuntime and RuntimeSnapshot — runtime construction.

Primitives and clients NEVER read env; they receive a SearchRuntime that was
built by one of the explicit builders here. CLI entrypoints pass explicit args
or literal config files and do not use env fallbacks.

Four builders, three runtime-state shapes:
  SearchRuntime.from_kwargs       — explicit; preferred by tests and NAT shim
  SearchRuntime.from_env          — read os.environ; minimal, no profile-level fields
  SearchRuntime.from_config_file  — parse NAT-style YAML; can interpolate
                                    against an explicit env mapping
  SearchRuntime.from_remote       — fetch /api/v1/runtime/config from a running agent

  RuntimeSnapshot.from_remote     — fetch and validate a runtime snapshot
  RuntimeSnapshot.from_dict       — parse a JSON-decoded payload

Builders map every failure onto the library error hierarchy: unreadable or
malformed config and non-200/invalid-JSON remote responses raise
ConfigurationError; connection/timeout failures raise BackendUnreachableError.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
from typing import Any

from .errors import BackendUnreachableError
from .errors import ConfigurationError
from .models.common import FusionMethod  # noqa: TC001  used in dataclass field annotation

# =============================================================================
# Helpers
# =============================================================================


def _req(env: Mapping[str, str], key: str) -> str:
    """Read a required env var. Raises ConfigurationError if missing or empty."""
    val = env.get(key)
    if not val:
        raise ConfigurationError(f"Required env var '{key}' is missing or empty")
    return val


def _first_non_empty(*values: Any) -> Any:
    """Return the first value that is neither None nor the empty string."""
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _require_config_value(name: str, *values: Any) -> Any:
    value = _first_non_empty(*values)
    if value is None:
        raise ConfigurationError(f"Required runtime value '{name}' is missing or empty")
    return value


# Config values arrive from YAML or a JSON snapshot where a quoted scalar
# (``"7"``) or a stringly-typed boolean (``"no"``) would otherwise be stored
# verbatim on the frozen dataclass — an int field holding a str, or a truthy
# ``"no"`` string. The coercion helpers below normalise those to the field's
# declared type and raise ConfigurationError (exit 4) on values that cannot be
# interpreted, instead of silently poisoning runtime behaviour.
_BOOL_CONFIG_FIELDS = frozenset(
    {
        "enable_frame_lookup",
    }
)
_INT_CONFIG_FIELDS = frozenset(
    {
        "default_max_results",
        "rrf_k",
        "request_timeout_seconds",
    }
)
_FLOAT_CONFIG_FIELDS = frozenset(
    {
        "embed_confidence_threshold",
        "w_attribute",
        "w_embed",
        "rrf_w",
        "top_percent_filter",
    }
)
_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "no", "off"})


def _coerce_config_bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_STRINGS:
            return True
        if normalized in _FALSE_STRINGS:
            return False
    raise ConfigurationError(f"config value '{name}' must be a boolean, got {value!r}")


def _coerce_config_int(name: str, value: Any) -> int:
    # bool is an int subclass; accept it explicitly so `True`/`False` in a
    # numeric field is a clear error rather than a silent 1/0.
    if isinstance(value, bool):
        raise ConfigurationError(f"config value '{name}' must be an integer, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ConfigurationError(f"config value '{name}' must be an integer, got {value!r}")
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except (TypeError, ValueError) as e:
            raise ConfigurationError(f"config value '{name}' must be an integer, got {value!r}") from e
    raise ConfigurationError(f"config value '{name}' must be an integer, got {value!r}")


def _coerce_config_float(name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ConfigurationError(f"config value '{name}' must be a number, got {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as e:
            raise ConfigurationError(f"config value '{name}' must be a number, got {value!r}") from e
    raise ConfigurationError(f"config value '{name}' must be a number, got {value!r}")


def _coerce_config_value(name: str, value: Any) -> Any:
    """Coerce one raw config value to the SearchRuntime field's declared type."""
    if value is None:
        return None
    if name in _BOOL_CONFIG_FIELDS:
        return _coerce_config_bool(name, value)
    if name in _INT_CONFIG_FIELDS:
        return _coerce_config_int(name, value)
    if name in _FLOAT_CONFIG_FIELDS:
        return _coerce_config_float(name, value)
    return value


def _coerce_runtime_kwargs(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce every SearchRuntime kwarg to its declared type (see helpers above)."""
    return {name: _coerce_config_value(name, value) for name, value in kwargs.items()}


# ${VAR} and ${VAR:-default} with shell `:-` semantics: the default fires when
# the variable is UNSET *or* set to the empty string. Plain env.get(key, default)
# would return "" for an empty-but-set var, diverging from the shell and from
# NAT's config interpolation. The pinned `:-` cases are covered by TestInterpolate
# in tests/unit_test/search_core/test_runtime.py — keep them green if you change this.
_INTERP_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


def _interpolate(text: str, env: Mapping[str, str]) -> str:
    """Resolve ``${VAR}`` and ``${VAR:-default}`` against `env`.

    Known limitations (intentional, to keep the surface small and match NAT's
    interpolation): variable names must be uppercase (``[A-Z_][A-Z0-9_]*``),
    defaults are literal text with no nested ``${...}`` expansion, and no other
    shell expansions (``${VAR:+x}``, ``${VAR/…}``, command substitution) are
    supported.

    Security: interpolated values must not contain newlines. A value with an
    embedded ``\\n``/``\\r`` could otherwise inject arbitrary YAML keys once the
    rendered text is parsed, so such values are rejected with a
    ``ConfigurationError`` rather than spliced into the document.
    """

    def _sub(m: re.Match[str]) -> str:
        val = env.get(m.group(1))
        if val is None or val == "":
            return m.group(2) or ""
        if "\n" in val or "\r" in val:
            raise ConfigurationError(
                f"interpolated value for '{m.group(1)}' contains a newline; refusing to inject into config",
            )
        return val

    return _INTERP_RE.sub(_sub, text)


# =============================================================================
# SearchRuntime — the one env boundary
# =============================================================================


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchRuntime:
    """All state any primitive needs, in one frozen dataclass.

    Built once at session start (via one of the from_* builders) and passed
    through to every primitive via from_runtime(). Primitives never read env
    or config directly.
    """

    # ---- Backend URLs ----
    es_endpoint: str | None = None
    # ES cluster for behavior index (object_id re-search). Often the same URL
    # as es_endpoint in single-cluster deployments; kept separate to match
    # SearchConfig.behavior_es_endpoint at tools/search.py:1560.
    behavior_es_endpoint: str | None = None
    # COSMOS_EMBED_ENDPOINT and RTVI_EMBED port 8017 are the same physical
    # service in current deployments — one logical embed service exposed via
    # two env names (see from_env() below).
    cosmos_embed_endpoint: str | None = None
    cosmos_embed_model: str = "cosmos-embed1-448p"  # from RTVI_EMBED_MODEL
    rtvi_cv_endpoint: str | None = None
    vst_internal_url: str | None = None
    vst_external_url: str | None = None

    # ---- Indexes ----
    behavior_index: str = "mdx-behavior-2025-01-01"  # DEFAULT_BEHAVIOR_INDEX in code
    # NEW in v1: today's code hardcodes this literal at tools/search.py:997 and
    # tools/attribute_search.py:1218. The library extracts it to a field so
    # deployments with different index naming families don't need a code change.
    behavior_index_wildcard: str = "mdx-behavior-*"
    video_embed_index: str = "mdx-embed-filtered-2025-01-01"  # from ELASTIC_SEARCH_INDEX
    # NEW in v1: today's code hardcodes "mdx-embed-filtered-*" at
    # tools/embed_search.py:615 for the RTSP search-index selection. The library
    # extracts it for the same reason as behavior_index_wildcard.
    video_embed_index_wildcard: str = "mdx-embed-filtered-*"
    frames_index: str | None = None  # None disables frame-level lookups
    # NEW in v1: today's code hardcodes "mdx-raw-*" at tools/attribute_search.py:1223
    # for the RTSP frames-index wildcard. Extracted for the same reason.
    frames_index_wildcard: str = "mdx-raw-*"
    enable_frame_lookup: bool = True  # mirrors attribute_search.py:190

    # ---- Behavior knobs ----
    # Search orchestrator default from functions.search.default_max_results.
    default_max_results: int = 10
    embed_confidence_threshold: float = 0.1  # config.yml:80 override; code default is 0.2
    fusion_method: FusionMethod = "rrf"
    w_attribute: float = 0.55
    w_embed: float = 0.35
    rrf_k: int = 60
    rrf_w: float = 0.5
    top_percent_filter: float | None = None
    request_timeout_seconds: int = 30

    @property
    def raw_index(self) -> str | None:
        """Alias for :attr:`frames_index`.

        The host-CLI RUNTIME_JSON contract (skills/vss-search-archive) exposes
        this value under the key ``raw_index`` (the index family is
        ``mdx-raw-*``), so callers routinely reach for ``runtime.raw_index``.
        Keep both names valid rather than making one an AttributeError trap.
        """
        return self.frames_index

    def __post_init__(self) -> None:
        """Reject invalid behavior knobs before they reach backend code."""
        if self.default_max_results < 1:
            raise ConfigurationError("default_max_results must be >= 1")
        if self.request_timeout_seconds < 1:
            raise ConfigurationError("request_timeout_seconds must be >= 1")
        if self.rrf_k < 1:
            raise ConfigurationError("rrf_k must be >= 1")
        if self.fusion_method not in {"weighted_linear", "rrf", "rrf_with_attribute_rank"}:
            raise ConfigurationError(f"unsupported fusion_method: {self.fusion_method!r}")
        for name in ("embed_confidence_threshold", "w_attribute", "w_embed", "rrf_w"):
            value = getattr(self, name)
            if not math.isfinite(value):
                raise ConfigurationError(f"{name} must be finite")
        if not -1.0 <= self.embed_confidence_threshold <= 1.0:
            raise ConfigurationError("embed_confidence_threshold must be in [-1, 1]")
        if self.w_attribute < 0 or self.w_embed < 0 or self.rrf_w < 0:
            raise ConfigurationError("fusion weights must be non-negative")
        if self.top_percent_filter is not None and not 0 < self.top_percent_filter < 1:
            raise ConfigurationError("top_percent_filter must be in (0, 1) when provided")

    def require(self, name: str) -> str:
        """Return one required non-empty string field or raise a typed error."""
        value = getattr(self, name, None)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"Required runtime value '{name}' is missing or empty")
        return value

    # =========================================================================
    # Builders — the FOUR doors into the library. No primitive may read env.
    # =========================================================================

    @classmethod
    def from_kwargs(cls, **kw: Any) -> SearchRuntime:
        """Explicit construction. Preferred for tests and the NAT adapter."""
        return cls(**kw)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SearchRuntime:
        """Read from a mapping (defaults to os.environ).

        Env-name reality (verified against current deployments):
          Helm   (values.yaml): RTVI_CV_BASE_URL, RTVI_EMBED_BASE_URL,
                                COSMOS_EMBED_ENDPOINT, ELASTIC_SEARCH_ENDPOINT,
                                ELASTIC_SEARCH_INDEX, RTVI_EMBED_MODEL, HOST_IP
          Docker (compose.yml): RTVI_CV_ENDPOINT, COSMOS_EMBED_ENDPOINT,
                                ELASTIC_SEARCH_ENDPOINT, ELASTIC_SEARCH_INDEX,
                                RTVI_EMBED_MODEL, HOST_IP, RTVI_EMBED_PORT,
                                RTVI_CV_PORT.

        This runtime is retrieval-only: it configures embed, attribute, and
        fusion search and intentionally does not read VLM or critic settings.
        """
        env = os.environ if env is None else env
        host_ip = env.get("HOST_IP", "localhost")

        # Docker exports RTVI_CV_ENDPOINT; Helm commonly uses RTVI_CV_BASE_URL.
        rtvi_cv = (
            env.get("RTVI_CV_ENDPOINT")
            or env.get("RTVI_CV_BASE_URL")
            or (
                f"http://{host_ip}:{env['RTVI_CV_PORT']}" if env.get("RTVI_CV_PORT") else None  # NOSONAR
            )
        )
        if not rtvi_cv:
            raise ConfigurationError(
                "RTVI CV endpoint missing: set RTVI_CV_ENDPOINT, RTVI_CV_BASE_URL, or RTVI_CV_PORT (+ HOST_IP)"
            )

        # cosmos_embed: prefer COSMOS_EMBED_ENDPOINT; fall back to RTVI_EMBED_BASE_URL
        # (same physical service in current deployments, port 8017).
        cosmos_embed = (
            env.get("COSMOS_EMBED_ENDPOINT")
            or env.get("RTVI_EMBED_BASE_URL")
            or (f"http://{host_ip}:{env['RTVI_EMBED_PORT']}" if env.get("RTVI_EMBED_PORT") else None)  # NOSONAR
        )
        if not cosmos_embed:
            raise ConfigurationError(
                "Embed endpoint missing: set COSMOS_EMBED_ENDPOINT or "
                "RTVI_EMBED_BASE_URL (Helm) or RTVI_EMBED_PORT (+ HOST_IP) (Docker)"
            )

        es_endpoint = _req(env, "ELASTIC_SEARCH_ENDPOINT")

        return cls(
            es_endpoint=es_endpoint,
            behavior_es_endpoint=env.get("BEHAVIOR_ES_ENDPOINT", es_endpoint),
            cosmos_embed_endpoint=cosmos_embed,
            cosmos_embed_model=env.get("RTVI_EMBED_MODEL", "cosmos-embed1-448p"),
            rtvi_cv_endpoint=rtvi_cv,
            vst_internal_url=_req(env, "VST_INTERNAL_URL"),
            vst_external_url=_req(env, "VST_EXTERNAL_URL"),
            video_embed_index=env.get("ELASTIC_SEARCH_INDEX", "mdx-embed-filtered-2025-01-01"),
            video_embed_index_wildcard=_first_non_empty(
                env.get("ELASTIC_SEARCH_INDEX_WILDCARD"),
                env.get("RTSP_EMBED_ES_INDEX_PATTERN"),
                "mdx-embed-filtered-*",
            ),
            behavior_index=_first_non_empty(
                env.get("BEHAVIOR_ES_INDEX"),
                env.get("BEHAVIOR_INDEX"),
                "mdx-behavior-2025-01-01",
            ),
            behavior_index_wildcard=_first_non_empty(
                env.get("BEHAVIOR_INDEX_WILDCARD"),
                env.get("RTSP_BEHAVIOR_ES_INDEX_PATTERN"),
                "mdx-behavior-*",
            ),
            frames_index=_first_non_empty(env.get("FRAMES_INDEX"), env.get("RAW_ES_INDEX")),
            frames_index_wildcard=_first_non_empty(
                env.get("FRAMES_INDEX_WILDCARD"),
                env.get("RTSP_RAW_ES_INDEX_PATTERN"),
                "mdx-raw-*",
            ),
        )

    @classmethod
    def from_config_file(cls, path: str | Path, *, env: Mapping[str, str] | None = None) -> SearchRuntime:
        """Parse a NAT-style config file (YAML), interpolate ${VAR} against `env`,
        and build a SearchRuntime.

        This builder is useful for callers that must reproduce a deployed
        profile. Env alone cannot do that — profile-level settings live only
        in the NAT config:
          - functions.search.default_max_results
          - functions.embed_search.default_max_results
          - functions.search.embed_confidence_threshold (config.yml:80)
          - functions.search.w_attribute / w_embed / rrf_k / rrf_w / top_percent_filter
        These have no env-var counterpart in current deployments; env-only
        would silently shift profile defaults.

        Layered precedence (later wins):
          1. Class defaults
          2. Values resolved from the NAT config block (functions.search,
             functions.embed_search, functions.attribute_search)
          3. Values from `env` (for fields the NAT block references as ${VAR})

        Legacy ``functions.search.use_attribute_search`` values are ignored;
        per-request ``search_mode`` is authoritative.
        """
        env = os.environ if env is None else env
        fns = _load_config_functions(path, env=env)
        search_cfg = fns.get("search", {}) or {}
        embed_cfg = fns.get("embed_search", {}) or {}
        attr_cfg = fns.get("attribute_search", {}) or {}
        for name, block in (("search", search_cfg), ("embed_search", embed_cfg), ("attribute_search", attr_cfg)):
            if not isinstance(block, Mapping):
                raise ConfigurationError(f"NAT config function '{name}' must be a mapping")

        host_ip = env.get("HOST_IP", "localhost")
        rtvi_cv = _first_non_empty(
            attr_cfg.get("rtvi_cv_endpoint"),
            env.get("RTVI_CV_ENDPOINT"),
            env.get("RTVI_CV_BASE_URL"),
            f"http://{host_ip}:{env['RTVI_CV_PORT']}" if env.get("RTVI_CV_PORT") else None,  # NOSONAR
        )
        cosmos_embed = _first_non_empty(
            embed_cfg.get("cosmos_embed_endpoint"),
            env.get("COSMOS_EMBED_ENDPOINT"),
            env.get("RTVI_EMBED_BASE_URL"),
            f"http://{host_ip}:{env['RTVI_EMBED_PORT']}" if env.get("RTVI_EMBED_PORT") else None,  # NOSONAR
        )
        es_endpoint = _require_config_value(
            "es_endpoint",
            embed_cfg.get("es_endpoint"),
            env.get("ELASTIC_SEARCH_ENDPOINT"),
        )
        vst_internal_url = _require_config_value(
            "vst_internal_url",
            embed_cfg.get("vst_internal_url"),
            env.get("VST_INTERNAL_URL"),
        )
        vst_external_url = _require_config_value(
            "vst_external_url",
            embed_cfg.get("vst_external_url"),
            env.get("VST_EXTERNAL_URL"),
        )
        rtvi_cv_endpoint = _require_config_value("rtvi_cv_endpoint", rtvi_cv)
        cosmos_embed_endpoint = _require_config_value("cosmos_embed_endpoint", cosmos_embed)

        kwargs: dict[str, Any] = {
            # ES + behavior
            "es_endpoint": es_endpoint,
            "behavior_es_endpoint": _first_non_empty(
                search_cfg.get("behavior_es_endpoint"),
                env.get("BEHAVIOR_ES_ENDPOINT"),
                es_endpoint,
            ),
            "video_embed_index": _first_non_empty(
                embed_cfg.get("es_index"),
                env.get("ELASTIC_SEARCH_INDEX"),
                "mdx-embed-filtered-2025-01-01",
            ),
            "video_embed_index_wildcard": _first_non_empty(
                embed_cfg.get("es_index_wildcard"),
                env.get("ELASTIC_SEARCH_INDEX_WILDCARD"),
                env.get("RTSP_EMBED_ES_INDEX_PATTERN"),
                "mdx-embed-filtered-*",
            ),
            "behavior_index": _first_non_empty(
                attr_cfg.get("behavior_index"),
                env.get("BEHAVIOR_ES_INDEX"),
                env.get("BEHAVIOR_INDEX"),
                "mdx-behavior-2025-01-01",
            ),
            "behavior_index_wildcard": _first_non_empty(
                attr_cfg.get("behavior_index_wildcard"),
                env.get("BEHAVIOR_INDEX_WILDCARD"),
                env.get("RTSP_BEHAVIOR_ES_INDEX_PATTERN"),
                "mdx-behavior-*",
            ),
            # Attribute-search frame-lookup knobs
            "frames_index": _first_non_empty(
                attr_cfg.get("frames_index"),
                env.get("FRAMES_INDEX"),
                env.get("RAW_ES_INDEX"),
            ),
            "frames_index_wildcard": _first_non_empty(
                attr_cfg.get("frames_index_wildcard"),
                env.get("FRAMES_INDEX_WILDCARD"),
                env.get("RTSP_RAW_ES_INDEX_PATTERN"),
                "mdx-raw-*",
            ),
            "enable_frame_lookup": attr_cfg.get("enable_frame_lookup", True),
            # VST
            "vst_internal_url": vst_internal_url,
            "vst_external_url": vst_external_url,
            # Embed clients
            "cosmos_embed_endpoint": cosmos_embed_endpoint,
            "cosmos_embed_model": env.get("RTVI_EMBED_MODEL", "cosmos-embed1-448p"),
            "rtvi_cv_endpoint": rtvi_cv_endpoint,
            # Search orchestrator profile knobs — the whole point of this builder
            "embed_confidence_threshold": search_cfg.get("embed_confidence_threshold", 0.1),
            "default_max_results": search_cfg.get("default_max_results", 10),
            "fusion_method": search_cfg.get("fusion_method", "rrf"),
            "w_attribute": search_cfg.get("w_attribute", 0.55),
            "w_embed": search_cfg.get("w_embed", 0.35),
            "rrf_k": search_cfg.get("rrf_k", 60),
            "rrf_w": search_cfg.get("rrf_w", 0.5),
            "top_percent_filter": search_cfg.get("top_percent_filter"),
        }
        return cls(**_coerce_runtime_kwargs(kwargs))

    @classmethod
    def from_remote(cls, agent_url: str, *, timeout: float = 5.0) -> SearchRuntime:
        """Fetch a runtime snapshot from a running agent.

        Convenience for callers that only need the flat runtime.

        WARNING: in Helm the agent returns in-cluster DNS URLs that are NOT
        reachable from a developer laptop. Host callers should use
        ``vss search run --deployment kubernetes`` so it discovers the
        live ConfigMap/Deployment and creates managed port-forwards instead.
        """
        return RuntimeSnapshot.from_remote(agent_url, timeout=timeout).runtime


# =============================================================================
# RuntimeSnapshot — full bundle returned by GET /api/v1/runtime/config
# =============================================================================


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeSnapshot:
    """Validated runtime returned by ``GET /api/v1/runtime/config``."""

    runtime: SearchRuntime

    @classmethod
    def from_config_file(cls, path: str | Path, *, env: Mapping[str, str] | None = None) -> RuntimeSnapshot:
        """Parse runtime values from one NAT config file."""
        env = os.environ if env is None else env
        return cls(runtime=SearchRuntime.from_config_file(path, env=env))

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> RuntimeSnapshot:
        """Parse the JSON returned by /api/v1/runtime/config.

        Ignores the legacy nested ``search`` block and unknown fields.
        """
        if not isinstance(payload, Mapping):
            raise ConfigurationError("runtime snapshot must be a JSON object")
        # Anything not in the SearchRuntime field set is ignored — newer agents
        # may add fields we don't yet know about; older agents may omit fields
        # we have defaults for. Either way we don't blow up.
        runtime_payload = {k: v for k, v in payload.items() if k != "search" and k in _SEARCH_RUNTIME_FIELDS}
        # A partial payload that drops a field the frozen dataclass requires would
        # raise a bare TypeError (outside the library error hierarchy). Validate
        # up front and report exactly what's absent as a ConfigurationError.
        missing = sorted(_REQUIRED_SEARCH_RUNTIME_FIELDS - runtime_payload.keys())
        if missing:
            raise ConfigurationError(f"runtime snapshot is missing required field(s): {', '.join(missing)}")
        # Wire values are untrusted: coerce numeric/bool fields before use.
        coerced = _coerce_runtime_kwargs(runtime_payload)
        return cls(runtime=SearchRuntime.from_kwargs(**coerced))

    @classmethod
    def from_remote(cls, agent_url: str, *, timeout: float = 5.0) -> RuntimeSnapshot:
        """Fetch from a running agent. See SearchRuntime.from_remote() warning
        about Helm in-cluster DNS.

        Framework failures never escape: a connection/timeout maps to
        ``BackendUnreachableError("agent", ...)`` while a non-200 response or an
        invalid JSON body maps to ``ConfigurationError``. The original exception
        is chained via ``from e``.
        """
        # httpx is imported locally so the bare API doesn't drag it in.
        import json

        import httpx

        url = f"{agent_url.rstrip('/')}/api/v1/runtime/config"
        try:
            with httpx.Client(timeout=timeout) as c:
                response = c.get(url)
                response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as e:
            raise ConfigurationError(f"agent at {url} returned HTTP {e.response.status_code}") from e
        except httpx.TransportError as e:
            # ConnectError / ConnectTimeout / ReadTimeout / ... all subclass this.
            raise BackendUnreachableError("agent", f"could not reach agent at {url}: {e}", e) from e
        except (json.JSONDecodeError, ValueError) as e:
            raise ConfigurationError(f"agent at {url} returned invalid JSON: {e}") from e
        if not isinstance(payload, Mapping):
            raise ConfigurationError("agent runtime snapshot must be a JSON object")
        return cls.from_dict(payload)


# Derived once at import time. Used by RuntimeSnapshot.from_dict to drop fields
# the host doesn't recognize (forward compatibility) rather than blow up.
_SEARCH_RUNTIME_FIELDS = frozenset(f.name for f in SearchRuntime.__dataclass_fields__.values())
# Fields with no default (and no default_factory) that SearchRuntime.__init__
# requires; from_dict validates their presence before constructing so a partial
# payload raises ConfigurationError instead of a bare TypeError.
_REQUIRED_SEARCH_RUNTIME_FIELDS = frozenset(
    {"es_endpoint", "cosmos_embed_endpoint", "rtvi_cv_endpoint", "vst_internal_url", "vst_external_url"}
)


def _load_config_functions(path: str | Path, *, env: Mapping[str, str] | None = None) -> Mapping[str, Any]:
    """Load the NAT config's functions block after shell-style interpolation.

    Kept in runtime.py so config parsing has one owner. PyYAML is imported
    locally to keep bare runtime/model imports light. File-read failures
    (missing path, a directory, permission errors) and YAML parse failures are
    re-raised as ``ConfigurationError`` (exit 4) rather than escaping as raw
    ``OSError``/``yaml.YAMLError`` (which would surface as an unexpected exit 1).
    """
    import yaml

    effective_env = os.environ if env is None else env
    try:
        raw = Path(path).read_text()
    except OSError as e:
        # Covers IsADirectoryError, FileNotFoundError, PermissionError, ...
        raise ConfigurationError(f"could not read config file {str(path)!r}: {e}") from e
    rendered = _interpolate(raw, effective_env)
    try:
        doc = yaml.safe_load(rendered) or {}
    except yaml.YAMLError as e:
        raise ConfigurationError(f"could not parse YAML config {str(path)!r}: {e}") from e
    functions = doc.get("functions", {}) or {}
    if not isinstance(functions, Mapping):
        raise ConfigurationError("NAT config 'functions' block must be a mapping")
    return functions
