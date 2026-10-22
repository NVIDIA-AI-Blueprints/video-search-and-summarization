# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate the product prompt without importing the agent framework."""

import ast
import json
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[6] / "services/agent/packages/vss_agents/src/vss_agents/tools/search.py"


def _constants():
    return {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(SOURCE.read_text()).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"QUERY_DECOMPOSITION_PROMPT", "DEFAULT_FEW_SHOT_EXAMPLES"}
    }


def test_prompt_requires_person_appearance_and_typed_fields():
    prompt = _constants()["QUERY_DECOMPOSITION_PROMPT"]
    assert '"pedestrian"' in prompt
    assert "traffic lights" in prompt
    assert "Always provide query, video_sources, attributes, and has_action" in prompt
    assert "Use empty lists for absent attributes or source names" in prompt
    assert "Do not invent time filters" in prompt
    assert "only when explicitly requested" in prompt
    assert "Never include actions or verbs" in prompt


def test_every_few_shot_has_typed_required_fields_and_keeps_constraints():
    examples = _constants()["DEFAULT_FEW_SHOT_EXAMPLES"]
    values = [
        json.loads(line.removeprefix("Output: ").replace("{{", "{").replace("}}", "}"))
        for line in examples.splitlines()
        if line.startswith("Output: ")
    ]
    assert len(values) >= 13
    for value in values:
        assert isinstance(value["query"], str) and value["query"]
        assert isinstance(value["video_sources"], list)
        assert all(isinstance(source, str) for source in value["video_sources"])
        assert isinstance(value["attributes"], list)
        assert all(isinstance(attribute, str) for attribute in value["attributes"])
        assert type(value["has_action"]) is bool
    objects = next(value for value in values if value.get("object_ids") == [5, 6])
    assert objects["video_sources"] == objects["attributes"] == []
    assert values[0]["timestamp_start"] == "2025-01-01T13:00:00Z"
    assert values[0]["video_sources"] == ["Endeavor heart"]
    assert values[1]["source_type"] == "rtsp"
    assert values[1]["attributes"] == []
