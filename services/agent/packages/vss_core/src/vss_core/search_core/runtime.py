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
"""SearchRuntime — what a caller hands the library.

Primitives and clients NEVER read env or files; they receive a SearchRuntime
built by whoever already knows the deployment. There is one door,
``from_kwargs``, and callers supply what they have: the CLI passes what
`vss configure` recorded in ~/.vss/config.json, the NAT adapter passes its own
config object.

It previously offered four. ``from_env`` read os.environ, ``from_config_file``
parsed NAT-style YAML with its own interpolation, and ``from_remote`` fetched
/api/v1/runtime/config from a running agent -- roughly 480 lines of parsing,
coercion and interpolation for three ways of answering the same question. Each
also decided *where* configuration lives, which is the caller's business, not
the library's. The remote door was the clearest case: the endpoint it needed is
not exposed, and its own guidance pointed at a `--deployment kubernetes` CLI
flag that no longer exists.

Validation splits by cost. Behaviour knobs are checked eagerly in
``__post_init__`` -- they are cheap and always wrong if wrong. Endpoints are
checked lazily in ``require()``, at the moment a client is built, so a
deployment missing one service still serves the paths that never call it.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from .errors import ConfigurationError
from .models.common import FusionMethod  # noqa: TC001  used in dataclass field annotation

# =============================================================================
# Uploads anchor (single source of truth)
# =============================================================================

#: Synthetic epoch uploaded files are ingested under (write-side contract in
#: ``video_ingest.py`` / ``video_delete.py``), so their embed/behavior/raw docs
#: always land in the ``-2025-01-01`` indices. Single-sourced here so the index
#: defaults below, the CLI ``_runtime_from`` frames anchor, and the graceful-empty
#: catch in ``_attribute_helpers`` cannot drift.
UPLOADS_ANCHOR_DATE = "2025-01-01"
BEHAVIOR_INDEX_ANCHOR = f"mdx-behavior-{UPLOADS_ANCHOR_DATE}"
VIDEO_EMBED_INDEX_ANCHOR = f"mdx-embed-filtered-{UPLOADS_ANCHOR_DATE}"
RAW_INDEX_ANCHOR = f"mdx-raw-{UPLOADS_ANCHOR_DATE}"

# =============================================================================
# Helpers
# =============================================================================


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
    cosmos_embed_endpoint: str | None = None
    cosmos_embed_model: str = "cosmos-embed1-448p"  # from RTVI_EMBED_MODEL
    rtvi_cv_endpoint: str | None = None
    vst_internal_url: str | None = None
    vst_external_url: str | None = None

    # ---- Indexes ----
    # The ``*_index`` bases are the fixed uploads anchors. Uploaded files are
    # ingested with a synthetic ``2025-01-01`` timestamp (``video_ingest.py``), so
    # their embed/behavior/raw docs always land in the ``-2025-01-01`` indices, and
    # ``video_delete.py`` hardcodes the same three names as its cleanup targets.
    # This is a write-side contract: the base must NOT be discovered from the live
    # index inventory, or a live-dated index can masquerade as the uploads base and
    # invert ``rtsp`` source-type selection. The absent-anchor graceful-empty for
    # ``video_file`` is gated on these exact constants (see
    # ``_is_absent_uploads_anchor`` / ``EmbedSearch.run``), so overriding
    # ``behavior_index`` or ``video_embed_index`` turns a fresh-stack ``video_file``
    # search from an empty result into exit 5.
    behavior_index: str = BEHAVIOR_INDEX_ANCHOR
    behavior_index_wildcard: str = "mdx-behavior-*"
    # The embed read path selects ``video_file`` -> this anchor and ``rtsp`` ->
    # wildcard minus this anchor, exactly as behavior/raw do. (The vss CLI never
    # reads ``ELASTIC_SEARCH_INDEX``; only the agent container consumes it.)
    video_embed_index: str = VIDEO_EMBED_INDEX_ANCHOR
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
    #: Merge contiguous same-sensor chunks into one result. A 5s window that
    #: matches usually has neighbours that also match; merging reports one
    #: event rather than several fragments of it. Disable to see the raw
    #: retrieval chunks -- which is what the agent's search tools return, so
    #: this is the knob that makes the two paths comparable.
    merge_adjacent: bool = True

    @property
    def raw_index(self) -> str | None:
        """Alias for :attr:`frames_index`.

        The host-CLI RUNTIME_JSON contract (skills/operations/vss-search-archive) exposes
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
