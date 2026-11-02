# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contracts for the optional person-state benchmark profile."""

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_raw_clip_eval as runner
from flows.decompose import DecompositionError, LiveDecomposer, load_prompt
from flows.person_state import person_state_route
from test_v22_original import mock_model


def extraction(**patch):
    return {
        "person_subject": True,
        "actions": [],
        "scene_relations": [],
        "attribute_query": "A model-generated caption that retrieval must ignore",
        **patch,
    }


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (extraction(actions=["walk"]), "attribute"),
        (extraction(actions=["jump"]), "embed"),
        (extraction(actions=["walk"], scene_relations=["through a corridor"]), "embed"),
        (extraction(person_subject=False), "embed"),
        (extraction(actions=["unrecognized-motion"]), "embed"),
    ],
)
def test_expert_eligibility_and_external_context(response, expected):
    assert person_state_route(response) == expected


@pytest.mark.parametrize(
    ("relations", "expected"),
    [
        (["side view"], "attribute"),
        (["SEEN FROM A FRONT VIEW."], "attribute"),
        (["  in   back view  "], "attribute"),
        (["rear profile"], "attribute"),
        (["seen from behind"], "attribute"),
        (["captured from the back view"], "attribute"),
        (["visible in the front view"], "attribute"),
        (["shown from the side"], "attribute"),
        (["viewed from behind a car"], "embed"),
        (["seen from behind", "beside a car"], "embed"),
        (["side view", "beside a car"], "embed"),
        (["side view of a car"], "embed"),
        (["behind a warehouse"], "embed"),
    ],
)
def test_camera_viewpoint_does_not_become_an_external_scene(relations, expected):
    assert person_state_route(extraction(scene_relations=relations)) == expected


def test_viewpoint_correction_preserves_subject_and_motion_restrictions():
    assert (
        person_state_route(
            extraction(person_subject=False, scene_relations=["front view"])
        )
        == "embed"
    )
    assert (
        person_state_route(extraction(actions=["jump"], scene_relations=["back view"]))
        == "embed"
    )


@pytest.mark.parametrize("path", ["embed", "attribute"])
def test_original_scope_and_caption_passthrough_contract(path):
    text = '  蓝色 shirts\nwith "white shoes"...  '
    response = extraction(
        person_subject=path == "attribute",
        video_sources=["invented-source"],
        timestamp_start="2030-01-01T00:00:00Z",
        top_k=1,
    )
    scope = {
        "timestamp_start": "2025-01-01T00:00:00Z",
        "timestamp_end": "2025-01-01T00:00:06Z",
    }
    plan = runner.live_routing_plan(text, response, "person-state-photo")
    selected, payload = runner.build_request(
        text,
        "live-routed",
        plan,
        scope,
        SimpleNamespace(
            top_k=80,
            min_cosine_similarity=-1,
            candidate_limit=100000,
            max_hits_per_source=1,
        ),
    )
    assert selected == path and payload["top_k"] == 80
    assert payload["source_type"] == "video_file"
    assert "video_sources" not in payload
    assert all(payload[k] == value for k, value in scope.items())
    if path == "embed":
        assert payload["query"].encode() == text.encode()
    else:
        assert payload["attributes"] == ['A photo of 蓝色 shirts\nwith "white shoes".']
    assert "model-generated" not in str(payload)


@pytest.mark.parametrize(
    "patch",
    [
        {"person_subject": "true"},
        {"person_subject": 1},
        {"actions": "walk"},
        {"actions": [None]},
        {"scene_relations": [""]},
        {"scene_relations": None},
        {"attribute_query": " "},
    ],
)
def test_bad_model_contract_is_reported_without_fallback(monkeypatch, patch):
    mock_model(monkeypatch, extraction(**patch))
    llm = LiveDecomposer(
        "http://fixture/llm",
        repo_root=runner.REPO_ROOT,
        model="fixture",
        prompt_variant="person-state",
    )
    with pytest.raises(DecompositionError, match="person-state requires") as error:
        llm.decompose_detailed("original query")
    assert error.value.response["choices"][0]["message"]["content"]


def test_fresh_requests_and_accurate_prompt_provenance(monkeypatch):
    sent = mock_model(monkeypatch, extraction())
    llm = LiveDecomposer(
        "http://fixture/llm",
        repo_root=runner.REPO_ROOT,
        model="nvidia/nemotron-3.5-lightning-30b-a3b",
        prompt_variant="person-state",
        max_tokens=32768,
    )
    for _ in range(2):
        llm.decompose_detailed("a person in blue")
    assert len(sent) == 2
    assert sent[0]["temperature"] == 0 and sent[0]["max_tokens"] == 32768
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert llm.describe()["prompt_source"] == "flows/person_state_prompt.json"
    template, examples = load_prompt(runner.REPO_ROOT, "person-state")
    assert examples == ""
    assert sent[0]["messages"][1]["content"] == template.format(
        user_query="a person in blue"
    )
    assert (
        hashlib.sha256(template.encode()).hexdigest()
        == "64cde1f61738e12618a36caa9bf6b6de62fc9a7df1b5762c7c562e1cf66e34e8"
    )


def test_truncated_valid_json_still_fails(monkeypatch):
    mock_model(monkeypatch, extraction(), finish_reason="length")
    llm = LiveDecomposer(
        "http://fixture/llm",
        repo_root=runner.REPO_ROOT,
        model="fixture",
        prompt_variant="person-state",
    )
    with pytest.raises(DecompositionError, match="truncated"):
        llm.decompose_detailed("a person")


@pytest.mark.parametrize("verb", ["standing", "sits", "lying", "walking", "RUNS"])
def test_ordinary_state_inflections_preserve_the_supported_action(verb):
    assert person_state_route(extraction(actions=[verb])) == "attribute"


@pytest.mark.parametrize("verb", ["walking a dog", "run and jump", "carrying", "kneel"])
def test_state_normalization_does_not_drop_additional_actions(verb):
    assert person_state_route(extraction(actions=[verb])) == "embed"


@pytest.mark.parametrize(
    ("text", "actions", "expected"),
    [
        ("a person walking", ["walk"], "embed"),
        ("A person standing still!", ["stand"], "embed"),
        ("a group running quickly together", ["run"], "embed"),
        ("person in blue pants walking", ["walk"], "attribute"),
        ("side view of a person standing", ["stand"], "attribute"),
        ("a tall person walking", ["walk"], "attribute"),
        ("person with tattoos", [], "attribute"),
        ("a person wearing a kimono", [], "attribute"),
        ("person with freckles", [], "attribute"),
        ("person standing 2026", ["stand"], "attribute"),
        ("person walking 白いシャツ", ["walk"], "attribute"),
        ("person walking without a hat", ["walk"], "attribute"),
    ],
)
def test_action_only_source_gate_preserves_unknown_requirements(
    text, actions, expected
):
    response = extraction(actions=actions)
    assert person_state_route(response, original_query=text) == expected
    plan = runner.live_routing_plan(text, response, "person-state-photo")
    assert plan["path"] == expected
    assert plan["query"] == text
    assert plan["attributes"] == ["A photo of " + text.strip().rstrip(".") + "."]


def test_source_gate_preserves_external_scene_restriction():
    response = extraction(actions=["walk"], scene_relations=["through a corridor"])
    assert (
        person_state_route(response, original_query="person in blue walking") == "embed"
    )
