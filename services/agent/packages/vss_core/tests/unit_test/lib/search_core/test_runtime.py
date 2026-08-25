# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""What SearchRuntime still does, now that construction is the only door.

The env, config-file and remote builders are gone -- callers pass values in
directly (`vss configure` records a deployment for the CLI; the NAT adapter
reads its own config). That leaves two behaviours worth pinning: the knob
validation every construction path runs, and the lazy endpoint check.
"""

from __future__ import annotations

import pytest

from vss_core.search_core import SearchRuntime
from vss_core.search_core.errors import ConfigurationError


class TestKnobValidation:
    """__post_init__ rejects nonsense before a primitive can act on it."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("default_max_results", 0),
            ("request_timeout_seconds", 0),
            ("rrf_k", 0),
            ("top_percent_filter", 1.0),
            ("fusion_method", "unknown"),
            ("embed_confidence_threshold", 2.0),
            ("w_tag", -0.1),
        ],
    )
    def test_invalid_knob_is_rejected(self, field: str, value: object) -> None:
        with pytest.raises(ConfigurationError, match=field):
            SearchRuntime.from_kwargs(**{field: value})

    def test_valid_knobs_construct(self) -> None:
        rt = SearchRuntime.from_kwargs(fusion_method="rrf", rrf_k=60, default_max_results=10)
        assert rt.rrf_k == 60

    def test_at_least_one_fusion_weight_must_be_positive(self) -> None:
        with pytest.raises(ConfigurationError, match="at least one"):
            SearchRuntime.from_kwargs(w_tag=0.0, w_embed=0.0, w_attribute=0.0)


class TestRequire:
    """Endpoints are checked at use, not at construction.

    That split is what lets an embedding search run on a deployment with no
    RT-CV: the runtime is built with rtvi_cv_endpoint=None and only the paths
    that build a CV client ever ask for it.
    """

    def test_missing_endpoint_names_itself(self) -> None:
        rt = SearchRuntime.from_kwargs(es_endpoint="http://es:9200")
        with pytest.raises(ConfigurationError, match="rtvi_cv_endpoint"):
            rt.require("rtvi_cv_endpoint")

    def test_present_endpoint_is_returned(self) -> None:
        rt = SearchRuntime.from_kwargs(es_endpoint="http://es:9200")
        assert rt.require("es_endpoint") == "http://es:9200"

    def test_constructing_without_endpoints_is_allowed(self) -> None:
        assert SearchRuntime.from_kwargs().rtvi_cv_endpoint is None
