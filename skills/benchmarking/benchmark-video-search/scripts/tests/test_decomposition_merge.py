# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Merge contracts: effective settings, prompt scope, and durable failed evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows import REPO_ROOT
from flows.decompose import DecompositionError, LiveDecomposer


def _response(message, status=200):
    payload = {"choices": [{"message": message}]}
    return SimpleNamespace(
        text=json.dumps(payload), status_code=status,
        json=lambda: payload,
        raise_for_status=lambda: None,
    )


@pytest.mark.parametrize("settings", [{}, {"enable_thinking": True}])
@pytest.mark.parametrize("model", [
    "nvidia/nemotron-3-nano", "nvidia/llama-3.3-nemotron-super-49b-v1.5"
])
def test_explicit_settings_override_all_automatic_thinking_controls(monkeypatch, settings, model):
    post = Mock(return_value=_response({"content": '{"query":"person"}'}))
    monkeypatch.setattr("flows.decompose.requests.post", post)
    decomposer = LiveDecomposer(
        "http://llm", repo_root=REPO_ROOT, model=model,
        chat_template_kwargs=settings,
    )
    decomposer.decompose("person")
    body = post.call_args.kwargs["json"]
    config = decomposer.describe()
    assert body["chat_template_kwargs"] == settings
    assert config["chat_template_kwargs"] == settings
    assert config["system_thinking_tag"] is None
    assert body["messages"][0]["content"] == config["system_prompt"]
    assert "/no_think" not in config["system_prompt"]


@pytest.mark.parametrize(("model", "kwargs", "tag"), [
    ("nvidia/nemotron-3-nano", {"enable_thinking": False}, None),
    ("nvidia/llama-3.3-nemotron-super-49b-v1.5", None, "/no_think"),
    ("strict/server", None, None),
])
def test_automatic_settings_and_effective_prompt_are_fingerprinted(monkeypatch, model, kwargs, tag):
    post = Mock(return_value=_response({"content": '{"query":"person"}'}))
    monkeypatch.setattr("flows.decompose.requests.post", post)
    decomposer = LiveDecomposer("http://llm", repo_root=REPO_ROOT, model=model)
    evidence = []
    decomposer.decompose("person", evidence_sink=lambda value: evidence.append(copy.deepcopy(value)))
    config = decomposer.describe()
    body = post.call_args.kwargs["json"]
    assert body.get("chat_template_kwargs") == kwargs
    assert config.get("chat_template_kwargs") == kwargs
    assert config["system_thinking_tag"] == tag
    assert config["system_prompt_sha256"] == hashlib.sha256(body["messages"][0]["content"].encode()).hexdigest()
    assert evidence[0]["configuration"] == config
    if kwargs is not None:
        explicit = LiveDecomposer("http://llm", repo_root=REPO_ROOT, model=model, chat_template_kwargs={})
        assert explicit.describe() != config


@pytest.mark.parametrize(("source_type", "label"), [("rtsp", "Video streams"), ("video_file", "Video files")])
def test_source_scope_is_submitted_and_recorded(monkeypatch, source_type, label):
    post = Mock(return_value=_response({"content": '{"query":"person"}'}))
    monkeypatch.setattr("flows.decompose.requests.post", post)
    decomposer = LiveDecomposer("http://llm", repo_root=REPO_ROOT, model="test", source_type=source_type)
    decomposer.decompose("person", video_sources=["dock_cam"])
    assert f"{label}: dock_cam" in post.call_args.kwargs["json"]["messages"][1]["content"]
    assert decomposer.describe()["source_type"] == source_type


@pytest.mark.parametrize("content", [None, "", "   ", "<think>only reasoning</think>"])
def test_reasoning_only_failure_preserves_response_before_parsing(monkeypatch, content):
    response = _response({"content": content, "reasoning_content": "Need more tokens"})
    monkeypatch.setattr("flows.decompose.requests.post", Mock(return_value=response))
    decomposer = LiveDecomposer("http://llm", repo_root=REPO_ROOT, model="test")
    evidence = []
    with pytest.raises(DecompositionError, match="no content.*Need more tokens"):
        decomposer.decompose("person", query_id="q1", evidence_sink=lambda value: evidence.append(copy.deepcopy(value)))
    assert evidence[0]["raw_response"] is None
    assert evidence[1]["raw_http_response"] == response.text
    assert evidence[-1]["raw_message"]["reasoning_content"] == "Need more tokens"
    assert evidence[-1]["inference_s"] >= 0
    assert evidence[-1]["query_id"] == "q1"
    assert evidence[-1]["error"]
    assert evidence[-1]["parsed"] is None


def test_http_error_keeps_response_and_failed_call_timing(monkeypatch):
    response = _response({"content": "failure"}, status=500)
    response.raise_for_status = Mock(side_effect=requests.HTTPError("upstream failed"))
    monkeypatch.setattr("flows.decompose.requests.post", Mock(return_value=response))
    decomposer = LiveDecomposer("http://llm", repo_root=REPO_ROOT, model="test")
    evidence = []
    with pytest.raises(DecompositionError, match="upstream failed"):
        decomposer.decompose("person", evidence_sink=lambda value: evidence.append(copy.deepcopy(value)))
    assert evidence[-1]["raw_http_response"] == response.text
    assert evidence[-1]["http_status"] == 500
    assert evidence[-1]["inference_s"] >= 0
