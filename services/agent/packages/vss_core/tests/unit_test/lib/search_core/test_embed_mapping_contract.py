# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deployment-template contract for embed source-type filtering.

The embed read path partitions ``video_file`` vs ``rtsp`` with a positive term
filter on ``sensor.type.keyword`` (see
``vss_core.search_core.primitives._embed_helpers.build_source_type_filter``).
That filter is only queryable because the ``mdx-embed-filtered-*`` index template
leaves ``sensor.type`` to Elasticsearch's default dynamic string mapping (``text``
plus a ``keyword`` sub-field) — it neither disables dynamic mapping nor remaps
``sensor``.

This is the failure mode a mock-only shape assertion cannot catch: if a deployment
template later sets ``dynamic: strict``/``false`` or maps ``sensor`` explicitly
without a keyword sub-field, ``sensor.type.keyword`` stops matching and the fix
regresses silently — exactly the class of bug this change set removed. These tests
pin the template contract at unit speed. What they cannot prove without a live
cluster (that ES actually materializes ``.keyword`` and that no producer writes a
conflicting mapping first) remains the release-time ``_count`` verification.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[8]

#: Both deployment copies of the ES template bootstrap. They must agree, so the
#: contract is asserted against each to prevent drift.
_TEMPLATE_SCRIPTS = (
    _REPO_ROOT / "deploy/docker/services/infra/elk/elasticsearch/init-scripts/elasticsearch-template-creation.sh",
    _REPO_ROOT / "deploy/helm/services/infra/charts/elasticsearch/configs/elasticsearch-template-creation.sh",
)

_EMBED_TEMPLATE_MARKER = "mdx_embed_filtered_template"
_NEXT_TEMPLATE_MARKER = "create_index_template"


def _embed_template_block(script: Path) -> str:
    """Return the ``mdx-embed-filtered-*`` template definition as raw text.

    Sliced by marker rather than JSON-parsed: the definition embeds a shell
    interpolation (``'"${...}"'`` for the vector dims) that is not valid JSON, and
    the invariants under test are structural presence/absence, not values.
    """
    text = script.read_text(encoding="utf-8")
    start = text.find(_EMBED_TEMPLATE_MARKER)
    assert start != -1, f"embed template marker not found in {script}"
    end = text.find(_NEXT_TEMPLATE_MARKER, start + len(_EMBED_TEMPLATE_MARKER))
    return text[start:end] if end != -1 else text[start:]


@pytest.mark.parametrize("script", _TEMPLATE_SCRIPTS, ids=lambda p: "docker" if "docker" in p.parts else "helm")
def test_embed_template_exists_and_targets_family(script: Path) -> None:
    assert script.exists(), f"missing deployment template script: {script}"
    block = _embed_template_block(script)
    assert '"index_patterns": ["mdx-embed-filtered-*"]' in block


@pytest.mark.parametrize("script", _TEMPLATE_SCRIPTS, ids=lambda p: "docker" if "docker" in p.parts else "helm")
def test_embed_template_does_not_disable_dynamic_mapping(script: Path) -> None:
    # `dynamic: strict|false` would drop or reject the dynamically-mapped
    # `sensor.type`, taking `sensor.type.keyword` with it.
    block = _embed_template_block(script)
    for forbidden in ('"dynamic": "strict"', '"dynamic": "false"', '"dynamic": false', '"dynamic":false'):
        assert forbidden not in block, f"embed template disables dynamic mapping ({forbidden}) in {script}"


@pytest.mark.parametrize("script", _TEMPLATE_SCRIPTS, ids=lambda p: "docker" if "docker" in p.parts else "helm")
def test_embed_template_does_not_remap_sensor(script: Path) -> None:
    # `sensor` must stay dynamically mapped so `sensor.type` gets its default
    # `.keyword` sub-field. Any explicit `sensor` mapping is a deliberate change
    # that must be reviewed against build_source_type_filter's `sensor.type.keyword`
    # term, so surface it here rather than let it silently break source filtering.
    block = _embed_template_block(script)
    assert '"sensor"' not in block, f"embed template explicitly maps sensor in {script}; review sensor.type.keyword"
