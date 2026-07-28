# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for model discovery in LVS summarization workflows."""

import json
from pathlib import Path
import subprocess

import pytest


MODEL_SELECTOR = """
  [.data[]?.id | select(type == "string" and length > 0)] | unique as $ids
  | if $preferred != "" and ($ids | index($preferred)) != null then $preferred
    elif ($ids | length) == 1 then $ids[0]
    else empty
    end
"""

REPO_ROOT = Path(__file__).resolve().parents[3]
SELECTOR_DOCUMENTS = {
    "skills/vss-summarize-video/references/end-to-end-example.md": 2,
    "skills/vss-summarize-video/references/hitl-prompts.md": 1,
    "skills/vss-summarize-video/references/video-summarization-api.md": 2,
}
LVS_DISCOVERY_DOCUMENTS = [
    "skills/vss-summarize-video/references/end-to-end-example.md",
    "skills/vss-summarize-video/references/hitl-prompts.md",
    "skills/vss-summarize-video/references/video-summarization-api.md",
]


def select_model(
    models: list[str], preferred: str = ""
) -> subprocess.CompletedProcess[str]:
    """Execute the documented jq selector against advertised model IDs."""
    payload = {"data": [{"id": model} for model in models]}
    return subprocess.run(
        ["jq", "-er", "--arg", "preferred", preferred, MODEL_SELECTOR],
        input=json.dumps(payload),
        capture_output=True,
        check=False,
        text=True,
    )


@pytest.mark.parametrize(
    ("models", "preferred", "expected"),
    [
        (["model-a", "model-b"], "model-b", "model-b"),
        (["model-a"], "stale-model", "model-a"),
        (["model-a", "model-a"], "", "model-a"),
    ],
)
def test_model_selection(models: list[str], preferred: str, expected: str) -> None:
    """Select an advertised preference or an unambiguous sole model."""
    result = select_model(models, preferred)

    assert result.returncode == 0
    assert result.stdout.strip() == expected


def test_model_selection_rejects_unmatched_ambiguous_models() -> None:
    """Reject discovery when multiple models exist and none is preferred."""
    result = select_model(["model-a", "model-b"], "stale-model")

    assert result.returncode != 0
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("relative_path", "expected_count"), SELECTOR_DOCUMENTS.items()
)
def test_documented_workflows_use_tested_selector(
    relative_path: str, expected_count: int
) -> None:
    """Keep documented selection snippets aligned with the tested jq logic."""
    content = (REPO_ROOT / relative_path).read_text()
    normalized_content = " ".join(content.split())
    normalized_selector = " ".join(MODEL_SELECTOR.split())

    assert normalized_content.count(normalized_selector) == expected_count


@pytest.mark.parametrize("relative_path", LVS_DISCOVERY_DOCUMENTS)
def test_lvs_workflows_use_unversioned_models_route(relative_path: str) -> None:
    """Require LVS discovery to use the service's unversioned models route."""
    content = (REPO_ROOT / relative_path).read_text()

    expected_routes = (
        '"$VIDEO_SUMMARIZATION_URL/models"',
        '"$LVS_BASE/models"',
        '"$BASE_URL/models"',
    )
    assert any(route in content for route in expected_routes)


def test_direct_vlm_fallback_uses_versioned_models_route() -> None:
    """Require direct VLM discovery to use its versioned models route."""
    content = (
        REPO_ROOT / "skills/vss-summarize-video/references/end-to-end-example.md"
    ).read_text()

    assert '"$VLM/v1/models"' in content
