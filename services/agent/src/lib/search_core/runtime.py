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
"""SearchRuntime, RuntimeSnapshot, SearchOptions — runtime construction.

Primitives and clients NEVER read env; they receive a SearchRuntime that was
built by one of the explicit builders here. CLI entrypoints pass explicit args
or literal config files and do not use env fallbacks.

Four builders, three runtime-state shapes:
  SearchRuntime.from_kwargs       — explicit; preferred by tests and NAT shim
  SearchRuntime.from_env          — read os.environ; minimal, no profile-level fields
  SearchRuntime.from_config_file  — parse NAT-style YAML; can interpolate
                                    against an explicit env mapping
  SearchRuntime.from_remote       — fetch /api/v1/runtime/config from a running agent

  RuntimeSnapshot.from_remote     — fetch the full {runtime, search_options} bundle
  RuntimeSnapshot.from_dict       — parse a JSON-decoded payload

See DESIGN.md §3 for the full contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
import os
from pathlib import Path
import re
from typing import Any
from typing import Literal

from pydantic import SecretStr

from .errors import ConfigurationError

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


def _bool(s: str | None) -> bool:
    """Parse a shell-style boolean string. None / unset / falsy → False."""
    if s is None:
        return False
    return s.strip().lower() in {"1", "true", "yes", "on"}


# ${VAR} and ${VAR:-default} with shell `:-` semantics: the default fires when
# the variable is UNSET *or* set to the empty string. Plain env.get(key, default)
# would return "" for an empty-but-set var, diverging from the shell and from
# NAT's config interpolation. Don't change this without updating the four
# pinned cases in DESIGN.md §14.
_INTERP_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


def _interpolate(text: str, env: Mapping[str, str]) -> str:
    """Resolve ${VAR} and ${VAR:-default} against `env`."""

    def _sub(m: re.Match[str]) -> str:
        val = env.get(m.group(1))
        if val is None or val == "":
            return m.group(2) or ""
        return val

    return _INTERP_RE.sub(_sub, text)


# =============================================================================
# SearchOptions — orchestrator config-time flags that don't belong in SearchRuntime
# =============================================================================


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchOptions:
    """Search-orchestrator config-time flags surfaced separately from SearchRuntime.

    `use_attribute_search` lives in the NAT config block `functions.search`
    (config.yml:75 sets it to true for the search profile). It is NOT a
    SearchRuntime field because it's a Search-constructor arg, not state.
    The CLI / facade reads it from the same config file as the runtime and
    passes it to Search.from_runtime(..., use_attribute_search=...).
    """

    use_attribute_search: bool = False

    @classmethod
    def from_config_file(cls, path: str | Path, *, env: Mapping[str, str] | None = None) -> SearchOptions:
        """Parse orchestrator-only options from the same NAT config as SearchRuntime."""
        fns = _load_config_functions(path, env=env)
        search_cfg = fns.get("search", {}) or {}
        return cls(use_attribute_search=bool(search_cfg.get("use_attribute_search", False)))


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
    es_endpoint: str
    # ES cluster for behavior index (object_id re-search). Often the same URL
    # as es_endpoint in single-cluster deployments; kept separate to match
    # SearchConfig.behavior_es_endpoint at tools/search.py:1560.
    behavior_es_endpoint: str | None = None
    # COSMOS_EMBED_ENDPOINT and RTVI_EMBED port 8017 are the same physical
    # service in current deployments — one logical embed service exposed via
    # two env names. See DESIGN.md §3 from_env() notes.
    cosmos_embed_endpoint: str
    cosmos_embed_model: str = "cosmos-embed1-448p"  # from RTVI_EMBED_MODEL
    rtvi_cv_endpoint: str
    vst_internal_url: str
    vst_external_url: str

    # ---- VLM (used by CriticAgent via the video_understanding NAT tool) ----
    vlm_base_url: str | None = None
    vlm_model_name: str | None = None
    vlm_model_type: Literal["nim", "openai"] = "nim"
    vlm_api_key: SecretStr | None = None
    vlm_max_frames: int = 60
    vlm_max_fps: int = 2
    vst_clip_enable_audio: bool = False

    # ---- Indexes ----
    behavior_index: str = "mdx-behavior-2025-01-01"  # DEFAULT_BEHAVIOR_INDEX in code
    # NEW in v1: today's code hardcodes this literal at tools/search.py:997 and
    # tools/attribute_search.py:1218. The library extracts it to a field so
    # deployments with different index naming families don't need a code change.
    behavior_index_wildcard: str = "mdx-behavior-*"
    video_embed_index: str = "video_embeddings"  # from ELASTIC_SEARCH_INDEX
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
    enable_critic: bool = True
    # Search orchestrator default from functions.search.default_max_results.
    default_max_results: int = 10
    # Result cap for the embed primitive when a request omits top_k. Defaults to
    # 100 to match the deployed search profiles; a bare from_kwargs() runtime
    # should not silently return only a handful of embed hits.
    embed_default_max_results: int = 100
    embed_confidence_threshold: float = 0.1  # config.yml:80 override; code default is 0.2
    fusion_method: Literal["weighted_linear", "rrf"] = "rrf"
    w_attribute: float = 0.55
    w_embed: float = 0.35
    rrf_k: int = 60
    rrf_w: float = 0.5
    top_percent_filter: float | None = None
    search_max_iterations: int = 1
    max_concurrent_verifications: int = 5
    critic_time_format: Literal["iso", "offset"] = "iso"
    critic_evaluation_count: int | None = None
    request_timeout_seconds: int = 30

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
          Docker (compose.yml): COSMOS_EMBED_ENDPOINT, ELASTIC_SEARCH_ENDPOINT,
                                ELASTIC_SEARCH_INDEX, RTVI_EMBED_MODEL, HOST_IP,
                                RTVI_EMBED_PORT, RTVI_CV_PORT (no RTVI_CV_ENDPOINT
                                today; assembled here from HOST_IP + RTVI_CV_PORT).

        VLM fields are optional — embed_search, attribute_search, and search do
        not need them. CriticAgent.from_runtime() validates VLM presence at
        construction. This lets `vss-cli embed_search` run on pods that have
        no VLM_BASE_URL set.
        """
        env = os.environ if env is None else env
        host_ip = env.get("HOST_IP", "localhost")

        # rtvi_cv: prefer Helm-style RTVI_CV_BASE_URL; else Docker-style HOST_IP+port.
        rtvi_cv = env.get("RTVI_CV_BASE_URL") or (
            f"http://{host_ip}:{env['RTVI_CV_PORT']}" if env.get("RTVI_CV_PORT") else None
        )
        if not rtvi_cv:
            raise ConfigurationError(
                "RTVI CV endpoint missing: set RTVI_CV_BASE_URL (Helm) or RTVI_CV_PORT (+ HOST_IP) (Docker)"
            )

        # cosmos_embed: prefer COSMOS_EMBED_ENDPOINT; fall back to RTVI_EMBED_BASE_URL
        # (same physical service in current deployments, port 8017).
        cosmos_embed = (
            env.get("COSMOS_EMBED_ENDPOINT")
            or env.get("RTVI_EMBED_BASE_URL")
            or (f"http://{host_ip}:{env['RTVI_EMBED_PORT']}" if env.get("RTVI_EMBED_PORT") else None)
        )
        if not cosmos_embed:
            raise ConfigurationError(
                "Embed endpoint missing: set COSMOS_EMBED_ENDPOINT or "
                "RTVI_EMBED_BASE_URL (Helm) or RTVI_EMBED_PORT (+ HOST_IP) (Docker)"
            )

        vlm_model_type: Literal["nim", "openai"] = "openai" if env.get("VLM_MODEL_TYPE") == "openai" else "nim"
        # API key may be unset for local NIMs.
        vlm_key_env = "OPENAI_API_KEY" if vlm_model_type == "openai" else "NVIDIA_API_KEY"

        es_endpoint = _req(env, "ELASTIC_SEARCH_ENDPOINT")

        return cls(
            es_endpoint=es_endpoint,
            behavior_es_endpoint=env.get("BEHAVIOR_ES_ENDPOINT", es_endpoint),
            cosmos_embed_endpoint=cosmos_embed,
            cosmos_embed_model=env.get("RTVI_EMBED_MODEL", "cosmos-embed1-448p"),
            rtvi_cv_endpoint=rtvi_cv,
            vst_internal_url=_req(env, "VST_INTERNAL_URL"),
            vst_external_url=_req(env, "VST_EXTERNAL_URL"),
            vlm_base_url=env.get("VLM_BASE_URL"),
            vlm_model_name=env.get("VLM_NAME"),
            vlm_model_type=vlm_model_type,
            vlm_api_key=SecretStr(env[vlm_key_env]) if env.get(vlm_key_env) else None,
            vst_clip_enable_audio=_bool(env.get("ENABLE_AUDIO", "false")),
            video_embed_index=env.get("ELASTIC_SEARCH_INDEX", "video_embeddings"),
            enable_critic=_bool(env.get("ENABLE_CRITIC", "true")),
            critic_time_format="offset" if env.get("CRITIC_TIME_FORMAT") == "offset" else "iso",
            critic_evaluation_count=int(env["CRITIC_EVALUATION_COUNT"]) if env.get("CRITIC_EVALUATION_COUNT") else None,
        )

    @classmethod
    def from_config_file(cls, path: str | Path, *, env: Mapping[str, str] | None = None) -> SearchRuntime:
        """Parse a NAT-style config file (YAML), interpolate ${VAR} against `env`,
        and build a SearchRuntime.

        This builder is useful for callers that must reproduce a deployed
        profile. Env alone cannot do that — profile-level settings live only
        in the NAT config:
          - functions.search.use_attribute_search (config.yml:75)
          - functions.search.search_max_iterations, default_max_results
          - functions.embed_search.default_max_results
          - functions.search.embed_confidence_threshold (config.yml:80)
          - functions.search.w_attribute / w_embed / rrf_k / rrf_w / top_percent_filter
        These have no env-var counterpart in current deployments; env-only
        would silently disable use_attribute_search and shift defaults.

        Layered precedence (later wins):
          1. Class defaults
          2. Values resolved from the NAT config block (functions.search,
             functions.embed_search, functions.attribute_search)
          3. Values from `env` (for fields the NAT block references as ${VAR})

        Note: SearchOptions (use_attribute_search) is NOT part of the returned
        SearchRuntime. Callers that drive the orchestrator should also read it
        from the same config; VSSSearch.from_config_file() in host.py does both.
        """
        env = os.environ if env is None else env
        fns = _load_config_functions(path, env=env)
        search_cfg = fns.get("search", {}) or {}
        embed_cfg = fns.get("embed_search", {}) or {}
        attr_cfg = fns.get("attribute_search", {}) or {}
        critic_cfg = fns.get("critic_agent", {}) or {}
        video_understanding_cfg = fns.get("video_understanding", {}) or {}
        clip_cfg = fns.get("vst_video_clip", {}) or {}

        host_ip = env.get("HOST_IP", "localhost")
        rtvi_cv = _first_non_empty(
            attr_cfg.get("rtvi_cv_endpoint"),
            env.get("RTVI_CV_BASE_URL"),
            f"http://{host_ip}:{env['RTVI_CV_PORT']}" if env.get("RTVI_CV_PORT") else None,
        )
        cosmos_embed = _first_non_empty(
            embed_cfg.get("cosmos_embed_endpoint"),
            env.get("COSMOS_EMBED_ENDPOINT"),
            env.get("RTVI_EMBED_BASE_URL"),
            f"http://{host_ip}:{env['RTVI_EMBED_PORT']}" if env.get("RTVI_EMBED_PORT") else None,
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

        vlm_model_type: Literal["nim", "openai"] = "openai" if env.get("VLM_MODEL_TYPE") == "openai" else "nim"
        vlm_key_env = "OPENAI_API_KEY" if vlm_model_type == "openai" else "NVIDIA_API_KEY"

        return cls(
            # ES + behavior
            es_endpoint=es_endpoint,
            behavior_es_endpoint=_first_non_empty(
                search_cfg.get("behavior_es_endpoint"),
                env.get("BEHAVIOR_ES_ENDPOINT"),
                es_endpoint,
            ),
            video_embed_index=_first_non_empty(
                embed_cfg.get("es_index"),
                env.get("ELASTIC_SEARCH_INDEX"),
                "video_embeddings",
            ),
            behavior_index=attr_cfg.get("behavior_index", "mdx-behavior-2025-01-01"),
            # Attribute-search frame-lookup knobs (attribute_search.py:185-194)
            frames_index=attr_cfg.get("frames_index"),
            enable_frame_lookup=attr_cfg.get("enable_frame_lookup", True),
            # VST
            vst_internal_url=vst_internal_url,
            vst_external_url=vst_external_url,
            # Embed clients
            cosmos_embed_endpoint=cosmos_embed_endpoint,
            cosmos_embed_model=env.get("RTVI_EMBED_MODEL", "cosmos-embed1-448p"),
            embed_default_max_results=embed_cfg.get("default_max_results", 100),
            rtvi_cv_endpoint=rtvi_cv_endpoint,
            # VLM
            vlm_base_url=env.get("VLM_BASE_URL"),
            vlm_model_name=env.get("VLM_NAME"),
            vlm_model_type=vlm_model_type,
            vlm_api_key=SecretStr(env[vlm_key_env]) if env.get(vlm_key_env) else None,
            vlm_max_frames=video_understanding_cfg.get("max_frames", 60),
            vlm_max_fps=video_understanding_cfg.get("max_fps", 2),
            vst_clip_enable_audio=clip_cfg.get("enable_audio", False),
            # Search orchestrator profile knobs — the whole point of this builder
            enable_critic=search_cfg.get("enable_critic", True),
            embed_confidence_threshold=search_cfg.get("embed_confidence_threshold", 0.1),
            search_max_iterations=search_cfg.get("search_max_iterations", 1),
            critic_time_format=critic_cfg.get("time_format", "iso"),
            critic_evaluation_count=critic_cfg.get("num_videos_to_evaluate"),
            default_max_results=search_cfg.get("default_max_results", 10),
            fusion_method=search_cfg.get("fusion_method", "rrf"),
            w_attribute=search_cfg.get("w_attribute", 0.55),
            w_embed=search_cfg.get("w_embed", 0.35),
            rrf_k=search_cfg.get("rrf_k", 60),
            rrf_w=search_cfg.get("rrf_w", 0.5),
            top_percent_filter=search_cfg.get("top_percent_filter"),
        )

    @classmethod
    def from_remote(cls, agent_url: str, *, timeout: float = 5.0) -> SearchRuntime:
        """Fetch a runtime snapshot from a running agent.

        Convenience for callers that only need the flat runtime
        (embed_search / attribute_search / critic). For Search orchestrator
        callers, use RuntimeSnapshot.from_remote() instead so use_attribute_search
        survives the round-trip.

        WARNING: in Helm the agent returns in-cluster DNS URLs that are NOT
        reachable from a developer laptop. Use the exec transport (vss-cli via
        kubectl exec) for laptop-to-Helm flows. See DESIGN.md §10a.
        """
        return RuntimeSnapshot.from_remote(agent_url, timeout=timeout).runtime


# =============================================================================
# RuntimeSnapshot — full bundle returned by GET /api/v1/runtime/config
# =============================================================================


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeSnapshot:
    """Bundles SearchRuntime with SearchOptions so the host receives a faithful
    reproduction of the deployed profile's `search()` behavior, including
    use_attribute_search (which is NOT a SearchRuntime field).
    """

    runtime: SearchRuntime
    search: SearchOptions = field(default_factory=SearchOptions)

    @classmethod
    def from_config_file(cls, path: str | Path, *, env: Mapping[str, str] | None = None) -> RuntimeSnapshot:
        """Parse runtime and search options from one NAT config file."""
        env = os.environ if env is None else env
        return cls(
            runtime=SearchRuntime.from_config_file(path, env=env),
            search=SearchOptions.from_config_file(path, env=env),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> RuntimeSnapshot:
        """Parse the JSON returned by /api/v1/runtime/config.

        Tolerates a missing `search` key (older agents that predate the
        nested block).
        """
        search_block = payload.get("search") or {}
        # Anything not in the SearchRuntime field set is ignored — newer agents
        # may add fields we don't yet know about; older agents may omit fields
        # we have defaults for. Either way we don't blow up.
        runtime_payload = {k: v for k, v in payload.items() if k != "search" and k in _SEARCH_RUNTIME_FIELDS}
        return cls(
            runtime=SearchRuntime.from_kwargs(**runtime_payload),
            search=SearchOptions(
                use_attribute_search=bool(search_block.get("use_attribute_search", False))
                if isinstance(search_block, Mapping)
                else False,
            ),
        )

    @classmethod
    def from_remote(cls, agent_url: str, *, timeout: float = 5.0) -> RuntimeSnapshot:
        """Fetch from a running agent. See SearchRuntime.from_remote() warning
        about Helm in-cluster DNS."""
        # httpx is imported locally so the bare API doesn't drag it in.
        import httpx

        with httpx.Client(timeout=timeout) as c:
            response = c.get(f"{agent_url.rstrip('/')}/api/v1/runtime/config")
            response.raise_for_status()
            payload = response.json()
        return cls.from_dict(payload)


# Derived once at import time. Used by RuntimeSnapshot.from_dict to drop fields
# the host doesn't recognize (forward compatibility) rather than blow up.
_SEARCH_RUNTIME_FIELDS = frozenset(f.name for f in SearchRuntime.__dataclass_fields__.values())


def _load_config_functions(path: str | Path, *, env: Mapping[str, str] | None = None) -> Mapping[str, Any]:
    """Load the NAT config's functions block after shell-style interpolation.

    Kept in runtime.py so config parsing has one owner. PyYAML is imported
    locally to keep bare runtime/model imports light.
    """
    import yaml

    effective_env = os.environ if env is None else env
    rendered = _interpolate(Path(path).read_text(), effective_env)
    doc = yaml.safe_load(rendered) or {}
    functions = doc.get("functions", {}) or {}
    if not isinstance(functions, Mapping):
        raise ConfigurationError("NAT config 'functions' block must be a mapping")
    return functions
