# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Multi-k math, one-call routing, usage accounting and offline scoring contracts."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_raw_clip_eval as runner
from flows import clip_metrics
from flows.decompose import LiveDecomposer
from flows.eval_observation import GpuSampler, token_summary
from score_raw_routing import score_saved_run


@pytest.mark.parametrize("k,expected", [(5, 0.4), (10, 0.2), (20, 0.1)])
def test_denominator_uses_min_relevant_and_k(k, expected):
    sensors = {f"s{i}": f"c{i}" for i in range(30)}
    q = {
        "query_id": "q",
        "query_domain": "pas",
        "relevant_clip_ids": list(sensors.values()),
    }
    score = clip_metrics.score_query(
        q, [{"sensor_id": "s0"}, {"sensor_id": "s1"}], sensors, k=k
    )
    assert score[f"ap_at_{k}"] == expected
    assert f"ap_at_{k}" in score


def test_duplicates_and_empty_results_do_not_inflate_ap():
    q = {"query_id": "q", "query_domain": "event", "relevant_clip_ids": ["a", "b"]}
    sensors = {"a": "a", "b": "b", "x": "x"}
    hits = [{"sensor_id": s} for s in ["a", "a", "x", "b"]]
    assert clip_metrics.score_query(q, hits, sensors, k=5)["ap_at_5"] == pytest.approx(
        (1 + 2 / 3) / 2
    )
    assert clip_metrics.score_query(q, [], sensors, k=20)["ap_at_20"] == 0
    assert clip_metrics.score_query(q, hits, sensors) == clip_metrics.score_query(
        q, hits, sensors, k=10
    )
    with pytest.raises(ValueError):
        clip_metrics.score_query(q, [], sensors, k=0)


def test_all_six_orders_are_balanced():
    orders = [runner.top_k_order([5, 10, 20], i) for i in range(6)]
    assert len({tuple(o) for o in orders}) == 6
    for position in range(3):
        assert sorted(o[position] for o in orders) == [5, 5, 10, 10, 20, 20]
    assert runner.top_k_order([10], 7) == [10]


def test_missing_usage_is_unknown_and_detail_tokens_are_subsets():
    routes = [
        {
            "llm_metadata": {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "total_tokens": 110,
                },
                "raw_usage": {
                    "prompt_tokens_details": {"cached_tokens": 80},
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            }
        },
        {"llm_metadata": None},
    ]
    r = token_summary(routes)
    assert r["routing_calls"] == 2 and r["calls_with_complete_usage"] == 1
    assert r["total_tokens"]["known_sum"] == 110
    assert r["prompt_tokens"]["calls_missing"] == 1
    assert r["cached_tokens"]["known_sum"] == 80
    assert token_summary([{}])["total_tokens"]["known_sum"] is None


def test_decomposition_metadata_is_bound_to_each_response(monkeypatch):
    import flows.decompose as module

    monkeypatch.setattr(
        module,
        "load_prompt",
        lambda _: ("{user_query} {video_sources} {few_shot_examples}", ""),
    )
    obj = LiveDecomposer(
        "http://fixture", repo_root=Path("."), model="fixture", timeout_s=1
    )
    responses = iter(
        [
            {
                "choices": [{"message": {"content": '{"query":"a"}'}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
            {"choices": [{"message": {"content": '{"query":"b"}'}}]},
        ]
    )
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        payload = next(responses)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    monkeypatch.setattr(module.requests, "post", post)
    a = obj.decompose_detailed("a")
    b = obj.decompose_detailed("b")
    assert len(calls) == 2
    assert a.metadata["usage"]["total_tokens"] == 12
    assert b.metadata["usage"]["total_tokens"] is None
    assert a.metadata["usage"]["total_tokens"] == 12


def test_gpu_windows_do_not_fabricate_missing_samples(tmp_path):
    sampler = GpuSampler(tmp_path)
    assert sampler.window_summary([(0, 1)])["status"] == "no_samples"
    sampler.samples = [
        {
            "gpu_uuid": "a",
            "received_monotonic_s": 2,
            "gpu_util_percent": 30,
            "memory_controller_util_percent": None,
            "vram_mib": 200,
            "power_watts": 80,
        }
    ]
    summary = sampler.window_summary([(1, 3)])["devices"]["a"]
    assert summary["gpu_util_percent"]["sample_mean"] == 30
    assert summary["memory_controller_util_percent"]["sample_mean"] is None


def test_offline_routes_are_scored_once_independent_of_k(tmp_path):
    runner.save(tmp_path / "complete.json", {"all_modes_complete": True})
    runner.save(
        tmp_path / "queries.json",
        {"queries": [{"query_id": "q", "query_domain": "pas"}]},
    )
    (tmp_path / "routes.jsonl").write_text(
        json.dumps({"query_id": "q", "chosen_route": "fusion"}) + "\n"
    )
    reference = {
        "reference": "reviewed",
        "rows": [{"query_id": "q", "expected_route": "fusion"}],
    }
    result = score_saved_run(tmp_path, reference)
    assert result["summary"]["overall"]["queries"] == 1
    assert result["summary"]["overall"]["agreement_percent"] == 100
    with (tmp_path / "routes.jsonl").open("a") as f:
        f.write(json.dumps({"query_id": "q", "chosen_route": "fusion"}) + "\n")
    with pytest.raises(ValueError, match="Duplicate"):
        score_saved_run(tmp_path, reference)


@pytest.mark.asyncio
async def test_multi_k_routes_once_and_excludes_warmup(tmp_path, monkeypatch):
    from flows import raw_sdk
    from test_raw_clip_runner import fixture_dataset
    from vss_cli import config

    fixture_dataset(tmp_path)
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
            "--top-k-values",
            "5",
            "10",
            "20",
            "--warmup-queries",
            "1",
        ]
    )
    calls, requests = [], []

    class LLM:
        def __init__(self, *args, **kwargs):
            pass

        def describe(self):
            return {}

        def decompose_detailed(self, query, **kwargs):
            calls.append(query)
            return SimpleNamespace(
                decomposition={"query": query, "attributes": [], "has_action": False},
                metadata={
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                    "raw_usage": None,
                },
            )

    class SDK:
        def __init__(self, **kwargs):
            assert kwargs["embedding_cache_policy"] == "query"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def search(self, path, payload):
            requests.append((path, dict(payload)))
            return {"data": [{"sensor_id": "sensor"}]}

    monkeypatch.setattr(runner, "inventory", lambda *args: ["source"])
    monkeypatch.setattr(runner, "LiveDecomposer", LLM)
    monkeypatch.setattr(runner, "source_commit", lambda: "fixture")
    monkeypatch.setattr(config, "load", lambda: object())
    monkeypatch.setattr(raw_sdk, "RawSdkRunner", SDK)
    await runner.run(args)
    assert len(calls) == 2  # one warmup and one measured, not one per k
    assert len(requests) == 6
    assert sorted(r[1]["top_k"] for r in requests) == [5, 5, 10, 10, 20, 20]
    assert (
        len(
            {
                json.dumps(
                    {key: value for key, value in p.items() if key != "top_k"},
                    sort_keys=True,
                )
                for _, p in requests
            }
        )
        == 1
    )
    routes = [
        json.loads(line)
        for line in (args.output_dir / "routes.jsonl").read_text().splitlines()
    ]
    assert len(routes) == 1
    usage = runner.read(args.output_dir / "token-usage.json")
    assert usage["measured"]["total_tokens"]["known_sum"] == 12
    assert usage["warmup"]["total_tokens"]["known_sum"] == 12
    summary = runner.read(args.output_dir / "summary.json")["live-routed"]["by_top_k"]
    route_ids = set()
    for k in (5, 10, 20):
        assert summary[str(k)]["summary"]["overall"][f"mAP@{k}_percent"] == 100
        record = runner.read(
            args.output_dir / "captures/live-routed" / f"top-k-{k}/event_a.json"
        )
        route_ids.add(record["route_id"])
    assert len(route_ids) == 1
    assert (
        runner.read(args.output_dir / "complete.json")["actual_measured_routing_calls"]
        == 1
    )


def test_gpu_sampling_unavailable_does_not_raise_or_report_zero(tmp_path, monkeypatch):
    import flows.eval_observation as observation

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("fixture missing nvidia-smi")

    monkeypatch.setattr(observation.subprocess, "run", unavailable)
    with GpuSampler(tmp_path, enabled=True) as sampler:
        assert sampler.status == "unavailable"
    assert sampler.metadata()["samples"] == 0
    assert sampler.window_summary([(0, 1)])["devices"] == {}
