# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The explicit v22 profile routes live but retrieves original query bytes."""

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_raw_clip_eval as runner
from flows.decompose import (
    DecompositionError,
    DecompositionTrace,
    LiveDecomposer,
    load_prompt,
)
from test_raw_clip_runner import fixture_dataset

ORIGINAL = '  Find “蓝色” shirts\nwith "white shoes".  '
REWRITTEN = "A different visual description"
SCOPE = {
    "timestamp_start": "2025-01-01T00:00:00Z",
    "timestamp_end": "2025-01-01T00:00:06Z",
}


def decomposition(path):
    return {
        "query": REWRITTEN,
        "has_action": path != "attribute",
        "attributes": []
        if path == "embed"
        else ["Person wearing invented red clothing"],
        "video_sources": ["inferred-source"],
        "source_type": "rtsp",
        "timestamp_start": "2030-01-01T00:00:00Z",
        "timestamp_end": "2031-01-01T00:00:00Z",
        "top_k": 99,
    }


@pytest.mark.parametrize("path", ["embed", "attribute", "fusion"])
def test_original_text_and_scope_contract_on_each_path(path):
    args = SimpleNamespace(
        top_k=10,
        min_cosine_similarity=-1,
        candidate_limit=100000,
        max_hits_per_source=1,
    )
    plan = runner.live_routing_plan(ORIGINAL, decomposition(path), "v22-original")
    selected, payload = runner.build_request(ORIGINAL, "live-routed", plan, SCOPE, args)
    assert selected == path
    assert payload["source_type"] == "video_file" and payload["top_k"] == 10
    assert all(payload[k] == v for k, v in SCOPE.items())
    assert "video_sources" not in payload
    argv = runner.cli_argv(["vss"], path, payload, False)
    if path in {"embed", "fusion"}:
        assert payload["query"].encode() == ORIGINAL.encode()
        assert argv[argv.index("--query") + 1] == ORIGINAL
    if path in {"attribute", "fusion"}:
        assert payload["attributes"] == [ORIGINAL]
        assert argv[argv.index("--attribute") + 1] == ORIGINAL


def test_product_default_retains_its_decomposition_and_filters():
    d = decomposition("fusion")
    plan = runner.live_routing_plan(ORIGINAL, d, "product")
    assert plan["query"] == REWRITTEN
    assert plan["attributes"] == d["attributes"]
    assert plan["video_sources"] == ["inferred-source"]
    assert plan["timestamp_start"] == d["timestamp_start"]
    args = runner.parse_args(
        [
            "--dataset-root",
            "data",
            "--ingestion-manifest",
            "ingest.json",
            "--output-dir",
            "out",
            "--modes",
            "embed-only",
        ]
    )
    assert args.live_routing_profile == "product"


def test_v22_object_route_does_not_fall_back_to_embedding():
    d = {**decomposition("fusion"), "object_ids": [42]}
    plan = runner.live_routing_plan(ORIGINAL, d, "v22-original")
    with pytest.raises(ValueError, match="never silently falls back"):
        runner.build_request(
            ORIGINAL, "live-routed", plan, SCOPE, SimpleNamespace(top_k=10)
        )


def test_v22_prompt_pin_matches_evaluated_literal():
    prompt, examples = load_prompt(runner.REPO_ROOT, "v22")
    assert (
        hashlib.sha256(prompt.encode()).hexdigest()
        == "eb86a04e91804f07c9b5e872f567fafa8cc3ce0b8001dc5b984d1afc498ef9fb"
    )
    assert examples == ""
    assert prompt != load_prompt(runner.REPO_ROOT)[0]


def mock_model(monkeypatch, value, finish_reason="stop"):
    sent = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "message": {"content": json.dumps(value)},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
            }

    def post(url, **kwargs):
        sent.append(kwargs["json"])
        return Response()

    monkeypatch.setattr("flows.decompose.requests.post", post)
    return sent


def test_live_v22_sends_exact_prompt_settings_and_fresh_calls(monkeypatch):
    sent = mock_model(monkeypatch, decomposition("fusion"))
    llm = LiveDecomposer(
        "http://fixture/llm",
        repo_root=runner.REPO_ROOT,
        model="nvidia/nemotron-3.5-lightning-30b-a3b",
        prompt_variant="v22",
        max_tokens=32768,
    )
    for _ in range(2):
        trace = llm.decompose_detailed(ORIGINAL, video_sources=["listed-source"])
        assert trace.metadata["usage"]["total_tokens"] == 30
    assert len(sent) == 2
    template, _ = load_prompt(runner.REPO_ROOT, "v22")
    assert sent[0]["messages"][1]["content"] == template.format(
        video_sources="Video files: listed-source",
        few_shot_examples="",
        user_query=ORIGINAL,
    )
    assert sent[0]["max_tokens"] == 32768 and sent[0]["temperature"] == 0
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert llm.describe()["prompt_variant"] == "v22"
    assert llm.describe()["max_tokens"] == 32768


@pytest.mark.parametrize(
    "patch",
    [
        {"has_action": None},
        {"has_action": "false"},
        {"attributes": "Person wearing red"},
        {"attributes": [42]},
        {"attributes": [""]},
        {"attributes": ["   "]},
        {"query": ""},
    ],
)
def test_invalid_v22_route_fields_fail_without_fallback(monkeypatch, patch):
    mock_model(monkeypatch, decomposition("fusion") | patch)
    llm = LiveDecomposer(
        "http://fixture/llm",
        repo_root=runner.REPO_ROOT,
        model="fixture",
        prompt_variant="v22",
    )
    with pytest.raises(DecompositionError, match="v22 requires") as error:
        llm.decompose_detailed(ORIGINAL)
    assert error.value.response["choices"][0]["message"]["content"]


def test_truncated_v22_response_is_a_failure_even_when_json_parses(monkeypatch):
    mock_model(monkeypatch, decomposition("fusion"), "length")
    llm = LiveDecomposer(
        "http://fixture/llm",
        repo_root=runner.REPO_ROOT,
        model="fixture",
        prompt_variant="v22",
    )
    with pytest.raises(DecompositionError, match="truncated"):
        llm.decompose_detailed(ORIGINAL)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["cli", "sdk"])
@pytest.mark.parametrize("path", ["embed", "attribute", "fusion"])
async def test_canonical_runner_uses_live_v22_and_original_requests(
    tmp_path, monkeypatch, backend, path
):
    from flows import raw_sdk
    from vss_cli import config

    fixture_dataset(tmp_path)
    gt_path = tmp_path / "gt/queries_gt.json"
    gt = runner.read(gt_path)
    gt["queries"][0]["query"] = ORIGINAL
    runner.save(gt_path, gt)
    args = runner.parse_args(
        [
            "--dataset-root",
            str(tmp_path),
            "--ingestion-manifest",
            str(tmp_path / "ingest.json"),
            "--output-dir",
            str(tmp_path / "out"),
            "--execution-backend",
            backend,
            "--modes",
            "live-routed",
            "--llm-url",
            "http://fixture",
            "--live-routing-profile",
            "v22-original",
        ]
    )
    events = []

    class LLM:
        def __init__(self, *args, **kwargs):
            assert kwargs["prompt_variant"] == "v22" and kwargs["max_tokens"] == 32768

        def describe(self):
            return {"prompt_variant": "v22", "max_tokens": 32768}

        def decompose_detailed(self, query, video_sources):
            assert query == ORIGINAL and video_sources == ["listed-source"]
            events.append("fresh-routing")
            return DecompositionTrace(decomposition(path), 0, {})

    class SDK:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def search(self, selected, payload):
            assert events == ["fresh-routing"] and selected == path
            if path in {"embed", "fusion"}:
                assert payload["query"] == ORIGINAL
            if path in {"attribute", "fusion"}:
                assert payload["attributes"] == [ORIGINAL]
            assert (
                payload["source_type"] == "video_file"
                and "video_sources" not in payload
            )
            events.append("search")
            return {"data": []}

    def cli(argv):
        assert events == ["fresh-routing"] and argv[3] == path
        if path in {"embed", "fusion"}:
            assert argv[argv.index("--query") + 1] == ORIGINAL
        if path in {"attribute", "fusion"}:
            assert argv[argv.index("--attribute") + 1] == ORIGINAL
        assert "--video-source" not in argv
        events.append("search")
        return {"data": [], "response_s": 0, "exit_code": 0}

    monkeypatch.setattr(runner, "inventory", lambda *args: ["listed-source"])
    monkeypatch.setattr(runner, "LiveDecomposer", LLM)
    monkeypatch.setattr(runner, "capture_cli", cli)
    monkeypatch.setattr(runner, "source_commit", lambda: "fixture")
    monkeypatch.setattr(config, "load", lambda: object())
    monkeypatch.setattr(raw_sdk, "RawSdkRunner", SDK)
    await runner.run(args)
    assert events == ["fresh-routing", "search"]
    capture = runner.read(args.output_dir / "captures/live-routed/event_a.json")
    assert all(capture["payload"][k] == v for k, v in SCOPE.items())
    assert (
        runner.read(args.output_dir / "identity.json")["live_routing_profile"]
        == "v22-original"
    )
    prompt = runner.read(args.output_dir / "prompt.json")
    assert (
        prompt["few_shot_examples"] == ""
        and prompt["sha256"]
        == "eb86a04e91804f07c9b5e872f567fafa8cc3ce0b8001dc5b984d1afc498ef9fb"
    )


@pytest.mark.asyncio
async def test_runner_retains_failed_v22_response_without_searching(
    tmp_path, monkeypatch
):
    from flows import raw_sdk
    from vss_cli import config

    fixture_dataset(tmp_path)
    mock_model(monkeypatch, decomposition("fusion") | {"has_action": "false"})
    args = runner.parse_args(
        [
            "--dataset-root",
            str(tmp_path),
            "--ingestion-manifest",
            str(tmp_path / "ingest.json"),
            "--output-dir",
            str(tmp_path / "out"),
            "--execution-backend",
            "sdk",
            "--modes",
            "live-routed",
            "--llm-url",
            "http://fixture",
            "--live-routing-profile",
            "v22-original",
        ]
    )

    class SDK:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def search(self, *args):
            pytest.fail("Invalid live route must never execute retrieval")

    monkeypatch.setattr(runner, "inventory", lambda *args: ["listed-source"])
    monkeypatch.setattr(runner, "source_commit", lambda: "fixture")
    monkeypatch.setattr(config, "load", lambda: object())
    monkeypatch.setattr(raw_sdk, "RawSdkRunner", SDK)
    with pytest.raises(DecompositionError, match="v22 requires"):
        await runner.run(args)
    failed = runner.read(args.output_dir / "routing/event_a.failed.json")
    assert failed["status"] == "failed"
    assert failed["llm_response"]["choices"][0]["message"]["content"]
    assert not (args.output_dir / "complete.json").exists()
