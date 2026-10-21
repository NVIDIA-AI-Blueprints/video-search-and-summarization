# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Explicit decomposition settings and product prompt contract, without NAT imports."""

from __future__ import annotations

import ast
import copy
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows import REPO_ROOT
from flows.decompose import LiveDecomposer
from flows.routing import InvalidDecomposition, route, validate_decomposition


def test_prompt_keeps_filters_explicit_and_person_appearance_separate():
    source = (
        REPO_ROOT / "services/agent/packages/vss_agents/src/vss_agents/tools/search.py"
    )
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id
        in ("QUERY_DECOMPOSITION_PROMPT", "DEFAULT_FEW_SHOT_EXAMPLES")
    }
    prompt = assignments["QUERY_DECOMPOSITION_PROMPT"]
    examples = assignments["DEFAULT_FEW_SHOT_EXAMPLES"]
    assert "Do not invent time filters" in prompt
    assert "Do not infer source_type from filenames" in prompt
    assert "only when explicitly requested" in prompt
    assert "standing" in prompt
    assert "person appearance descriptors only" in prompt
    formatted = prompt.format(
        video_sources="Video files: camera_20250101.mp4",
        few_shot_examples=examples,
        user_query="person standing in a red shirt",
    )
    assert "camera_20250101.mp4" in formatted
    assert '"has_action": true' in examples


def test_null_object_ids_are_absence_not_invalid_or_an_object_route():
    value = {
        "query": "person standing",
        "object_ids": None,
        "attributes": [],
        "has_action": True,
    }
    assert validate_decomposition(value) == value
    assert route(value) == "embed"
    assert (
        route({"attributes": ["red shirt"], "has_action": False, "object_ids": None})
        == "attribute"
    )


@pytest.mark.parametrize(
    "ids", [True, False, 42, "42", {}, [True], [None], [1.5], [""], [[1]]]
)
def test_null_object_id_support_does_not_accept_malformed_ids(ids):
    with pytest.raises(InvalidDecomposition):
        validate_decomposition({"object_ids": ids})


@pytest.mark.parametrize(
    "settings",
    [
        [],
        "{}",
        False,
        {1: "bad"},
        {"nested": {False: "bad"}},
        {"value": math.nan},
        {"value": math.inf},
        {"value": object()},
        {"value": (1, 2)},
    ],
)
def test_chat_template_settings_reject_non_json_objects_before_network(
    monkeypatch, settings
):
    get = Mock(side_effect=AssertionError("must validate before model discovery"))
    monkeypatch.setattr("flows.decompose.requests.get", get)
    with pytest.raises(ValueError, match="chat_template_kwargs"):
        LiveDecomposer("http://llm", repo_root=REPO_ROOT, chat_template_kwargs=settings)
    get.assert_not_called()


def test_cyclic_chat_template_settings_are_rejected():
    settings = {}
    settings["cycle"] = settings
    with pytest.raises(ValueError, match="chat_template_kwargs"):
        LiveDecomposer(
            "http://llm",
            repo_root=REPO_ROOT,
            model="test",
            chat_template_kwargs=settings,
        )


def response():
    return SimpleNamespace(
        text='{"choices": [{"message": {"content": "{\\"query\\":\\"person\\",\\"object_ids\\":null}"}}]}',
        status_code=200,
        raise_for_status=lambda: None,
        json=lambda: {
            "choices": [
                {"message": {"content": '{"query":"person","object_ids":null}'}}
            ]
        },
    )


def test_settings_are_detached_submitted_and_recorded(monkeypatch):
    settings = {"enable_thinking": False, "nested": {"stop": ["a", 1, None]}}
    expected = copy.deepcopy(settings)
    post = Mock(return_value=response())
    monkeypatch.setattr("flows.decompose.requests.post", post)
    decomposer = LiveDecomposer(
        "http://llm", repo_root=REPO_ROOT, model="test", chat_template_kwargs=settings
    )
    settings["nested"]["stop"].append("mutated")
    described = decomposer.describe()
    assert described["chat_template_kwargs"] == expected
    described["chat_template_kwargs"]["nested"]["stop"].append("also mutated")
    evidence = []
    parsed, _ = decomposer.decompose(
        "person", evidence_sink=lambda value: evidence.append(copy.deepcopy(value))
    )
    assert parsed["object_ids"] is None
    assert post.call_args.kwargs["json"]["chat_template_kwargs"] == expected
    assert evidence[0]["submitted_request"]["chat_template_kwargs"] == expected
    assert decomposer.describe()["chat_template_kwargs"] == expected
    json.dumps(evidence, allow_nan=False)


@pytest.mark.parametrize("settings", [None, {}])
def test_settings_default_omission_and_explicit_empty_object(monkeypatch, settings):
    post = Mock(return_value=response())
    monkeypatch.setattr("flows.decompose.requests.post", post)
    decomposer = LiveDecomposer(
        "http://llm", repo_root=REPO_ROOT, model="test", chat_template_kwargs=settings
    )
    decomposer.decompose("person")
    body = post.call_args.kwargs["json"]
    assert ("chat_template_kwargs" in body) is (settings is not None)
    assert ("chat_template_kwargs" in decomposer.describe()) is (settings is not None)
