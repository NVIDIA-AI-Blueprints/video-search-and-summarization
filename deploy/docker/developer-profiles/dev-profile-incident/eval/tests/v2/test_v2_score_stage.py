# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``score``: a live run's predictions scored with a fake judge upstream and a fake embedder, then re-scored offline
from the caches with the network blocked; both tracks; judge budgets refuse before sending."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest

from evalv2 import cli
from evalv2.core.config import RunConfig
from evalv2.score_stage import SCORE_VERSION, EmbeddingCache, ScoreRefused, StackedLedger, cached_embedder, run_score
from evalv2.tasks.incident_objects.scoring import SimilarityUnavailableError

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import eval_gt_schema  # noqa: E402
import ground_truth_freeze as gtf  # noqa: E402
from test_v2_incident_objects import _embed, _sheets  # noqa: E402
from test_v2_live_executor import FakeAgent, Upstream, _answer, _datasets, _executor, _marker, _track_a, _track_b  # noqa: E402

JUDGE_ENV = {"NVIDIA_API_KEY": "judge-key"}


@pytest.fixture(scope="module")
def gt(tmp_path_factory):
    directory = tmp_path_factory.mktemp("gt") / "ground-truth-v1.0"
    directory.mkdir()
    split = {"version": "split-v2", "dev": ["Assault001", "Burglary002"], "test": [], "excluded": [],
             "by_category": {"Assault": {"dev": ["Assault001"], "test": []},
                             "Burglary": {"dev": ["Burglary002"], "test": []}}}
    files = {"ground_truth.json": gtf.canonical_json({"version": gtf.VERSION, "status": "frozen", "sheets": _sheets()}),
             "split-v2.json": gtf.canonical_json(split)}
    for name, content in files.items():
        (directory / name).write_bytes(content)
    (directory / "MANIFEST.json").write_text(json.dumps(
        {"status": "frozen", "blocking": [], "files": {n: gtf.sha256_bytes(c) for n, c in files.items()}}))
    return eval_gt_schema.load_frozen_gt_index(directory), gtf.read_frozen(directory)["sheets"]


class Judge:
    """A fake catalog endpoint answering the judge prompt; counts every request."""

    def __init__(self, fail=False):
        self.requests, self.fail = [], fail

    def __call__(self, request):
        if self.fail:
            raise AssertionError("an offline score must never send a request")
        body = json.loads(request.content)
        self.requests.append(body)
        text = body["messages"][1]["content"]
        expected, predicted = text.split("\nPREDICTED: ")
        score = 0.9 if expected.removeprefix("EXPECTED: ").strip().lower() == predicted.strip().lower() else 0.3
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"score": score,
                                                                                          "reasoning": "r"})},
                                                      "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 400, "completion_tokens": 100,
                                                   "total_tokens": 500}})


def _run_track_a(tmp_path, config=None):
    config = config or _track_a()
    executor = _executor(tmp_path, config, upstream=Upstream())
    executor.run()
    items = cli.plan(config, lambda cfg: _datasets())
    return config, _marker(tmp_path, config, items, judge={"max_requests": 500, "max_tokens": 1_000_000})


def _score(tmp_path, gt, config, *, offline, marker=None, judge=None, out=None, compute=_embed):
    return run_score(config, out or tmp_path / "run", datasets={**_datasets(), "incident_gt": gt[0]}, sheets=gt[1],
                     offline=offline, marker=marker, judge_ledger_dir=tmp_path / "judge-ledger", environ=JUDGE_ENV,
                     transport=httpx.MockTransport(judge or Judge()), compute=compute, sleep=lambda s: None)


def _without_mode(summary):
    return {k: v for k, v in summary.items() if k != "mode"}


def test_track_a_scores_live_then_offline_from_the_caches_identically(tmp_path, gt):
    config, marker = _run_track_a(tmp_path)
    judge = Judge()
    live = _score(tmp_path, gt, config, offline=False, marker=marker, judge=judge)
    summary = live["summary"]
    assert summary["final_items"] == 8 and summary["unscored"]["fields"] == 0
    block = summary["subjects"]["nano-direct"]["incident_objects"]
    assert block["videos"] == 4 and block["outcomes"] == {"scored": 4}
    assert block["continuity_v1"]["items"]["entities"]["tp"] > 0
    assert judge.requests and all(r["model"] == "openai/gpt-oss-20b" for r in judge.requests)
    assert summary["mode"]["judge_calls"] == len(judge.requests), "repeats share the judge cache"
    offline = _score(tmp_path, gt, config, offline=True, judge=Judge(fail=True), compute=None)
    assert _without_mode(offline["summary"]) == _without_mode(summary)
    assert offline["videos"] == live["videos"] and offline["summary"]["mode"]["judge_cache_misses"] == 0
    assert (tmp_path / "run" / "scoring" / SCORE_VERSION / "primary" / "offline-summary.json").exists()


def test_offline_without_caches_leaves_fields_unscored_and_sends_nothing(tmp_path, gt):
    config, _ = _run_track_a(tmp_path)
    result = _score(tmp_path, gt, config, offline=True, judge=Judge(fail=True), compute=None)
    unscored = result["summary"]["unscored"]
    assert unscored["not_judged"] > 0 and unscored["not_embedded"] > 0
    video = result["videos"][0]
    assert video["incident_field_scores"]["description"]["score"] is None, "unscored, never a fail"


def test_offline_embed_local_scores_the_matching_without_any_judge(tmp_path, gt):
    config, _ = _run_track_a(tmp_path)
    judge = Judge(fail=True)
    result = run_score(config, tmp_path / "run", datasets={**_datasets(), "incident_gt": gt[0]}, sheets=gt[1],
                       offline=True, embed_local=True, compute=_embed, transport=httpx.MockTransport(judge))
    unscored = result["summary"]["unscored"]
    assert unscored["not_embedded"] == 0 and unscored["not_judged"] > 0, "vectors computed, the judge stays cached-only"
    assert result["summary"]["mode"]["embed_local"] is True and not judge.requests
    again = _score(tmp_path, gt, config, offline=True, judge=Judge(fail=True), compute=None)
    assert again["summary"]["unscored"]["not_embedded"] == 0, "the vectors were cached for a plain offline re-score"


def test_a_live_score_is_refused_without_a_judge_approval(tmp_path, gt):
    config, marker = _run_track_a(tmp_path)
    with pytest.raises(ScoreRefused, match="needs an approval marker"):
        _score(tmp_path, gt, config, offline=False)
    no_judge = {k: v for k, v in marker.items() if k != "judge"}
    with pytest.raises(ScoreRefused, match="approves no judge budget"):
        _score(tmp_path, gt, config, offline=False, marker=no_judge)
    with pytest.raises(ScoreRefused, match="set NVIDIA_API_KEY"):
        run_score(config, tmp_path / "run", datasets={**_datasets(), "incident_gt": gt[0]}, sheets=gt[1],
                  offline=False, marker=marker, judge_ledger_dir=tmp_path / "jl", environ={}, compute=_embed)


def test_the_run_judge_cap_refuses_before_sending(tmp_path, gt):
    config = _track_a(caps=[{"ledger": "inference", "max_attempts": 30, "max_tokens": 600000},
                            {"ledger": "judge", "max_attempts": 2, "max_tokens": 200000}])
    config, marker = _run_track_a(tmp_path, config)
    judge = Judge()
    result = _score(tmp_path, gt, config, offline=False, marker=marker, judge=judge)
    assert len(judge.requests) == 2, "the third judge attempt was refused by the run's judge cap"
    errors = [e["error"] for v in result["videos"] for e in v["scoring_errors"]]
    assert any("budget_exceeded" in e for e in errors)
    shared = (tmp_path / "judge-ledger" / "catalog-gpt-oss-20b-v1.jsonl").read_text().splitlines()
    assert sum('"event":"reserve"' in line for line in shared) == 2, "the shared ledger saw the same two attempts"


def test_the_shared_judge_ledger_holds_across_runs(tmp_path):
    from evalv2.core.budget import BudgetExceeded, BudgetLedger, Cap

    shared = BudgetLedger([Cap("judge", max_attempts=3)], tmp_path / "shared.jsonl")
    first = StackedLedger([shared, BudgetLedger([Cap("judge", max_attempts=10)], tmp_path / "a.jsonl")])
    for _ in range(3):
        first.settle(first.reserve("judge", subject="a", task="t", tokens_bound=10), tokens_used=5)
    second = StackedLedger([BudgetLedger([Cap("judge", max_attempts=3)], tmp_path / "shared.jsonl"),
                            BudgetLedger([Cap("judge", max_attempts=10)], tmp_path / "b.jsonl")])
    with pytest.raises(BudgetExceeded):
        second.reserve("judge", subject="b", task="t", tokens_bound=10)
    assert BudgetLedger([], tmp_path / "b.jsonl").totals("judge").attempts == 0, "refused on both, charged on none"


def test_the_embedding_cache_is_reused_and_offline_misses_are_unscored(tmp_path):
    cache = EmbeddingCache(tmp_path / "e.jsonl")
    calls = []

    def compute(texts):
        calls.append(list(texts))
        return _embed(texts)

    embed = cached_embedder(cache, compute)
    assert embed(["a b", "c"]) == _embed(["a b", "c"]) and embed(["c", "a b"]) == _embed(["c", "a b"])
    assert len(calls) == 1
    assert cached_embedder(EmbeddingCache(tmp_path / "e.jsonl"), None)(["c"]) == _embed(["c"])
    with pytest.raises(SimilarityUnavailableError, match="offline"):
        cached_embedder(cache, None)(["new text"])


def test_track_b_scores_three_stages_zoom_effect_and_caps(tmp_path, gt):
    config = _track_b(tasks={"pipeline": {"options": {"judges": {"primary": "catalog-gpt-oss-20b-v1"}}}},
                      caps=[{"ledger": "inference", "max_attempts": 10, "max_tokens": 200000},
                            {"ledger": "grounding", "max_attempts": 200, "max_tokens": 800000},
                            {"ledger": "zoom", "max_attempts": 400, "max_tokens": 1000000},
                            {"ledger": "judge", "max_attempts": 200, "max_tokens": 400000}])
    executor = _executor(tmp_path, config, adapter_kwargs={"post": FakeAgent(_answer(), _answer())})
    executor.run()
    marker = _marker(tmp_path, config, cli.plan(config, lambda cfg: _datasets()),
                     judge={"max_requests": 200, "max_tokens": 400000})
    live = _score(tmp_path, gt, config, offline=False, marker=marker)
    block = live["summary"]["subjects"]["super-pipeline"]["pipeline"]
    assert set(block["stages"]) == {"p1", "p1_agent", "final"}
    assert block["zoom_effect"]["runs_paired"] == 2 and block["caps"]["runs"] == 2
    assert block["crops"]["unscored"].startswith("no box_references")
    assert block["caption_pass"] == {"runs": 2, "ran": 0, "chunking_enabled": 0}
    offline = _score(tmp_path, gt, config, offline=True, judge=Judge(fail=True), compute=None)
    assert _without_mode(offline["summary"]) == _without_mode(live["summary"])


def test_score_offline_through_the_cli(tmp_path, gt, capsys):
    config, marker = _run_track_a(tmp_path)
    _score(tmp_path, gt, config, offline=False, marker=marker)
    path = tmp_path / "c.json"
    path.write_text(config.model_dump_json())
    code = cli.main(["score", "--config", str(path), "--out", str(tmp_path / "run"), "--offline"],
                    load_datasets=lambda cfg: {**_datasets(), "incident_gt": gt[0]}, load_sheets=lambda: gt[1])
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["unscored"]["fields"] == 0 and report["mode"]["offline"]
    shutil.rmtree(tmp_path / "run" / "scoring" / SCORE_VERSION / "primary")


def test_a_live_score_through_the_cli_needs_the_live_switch(tmp_path, gt, monkeypatch, capsys):
    config, _ = _run_track_a(tmp_path)
    path = tmp_path / "c.json"
    path.write_text(config.model_dump_json())
    monkeypatch.delenv(cli.LIVE_ENV, raising=False)
    assert cli.main(["score", "--config", str(path), "--out", str(tmp_path / "run")]) == 2
    assert "refusing a live score" in capsys.readouterr().err


def test_the_approval_draft_is_a_skeleton_that_approves_nothing(tmp_path, capsys):
    config = RunConfig.model_validate(_track_a().model_dump())
    path = tmp_path / "c.json"
    path.write_text(config.model_dump_json())
    assert cli.main(["approval", "draft", "--config", str(path)], load_datasets=lambda cfg: _datasets()) == 0
    draft = json.loads(capsys.readouterr().out)
    assert draft["approved_by"] == "" and draft["max_items"] == 8 and draft["judge"]["max_requests"] == 100
    assert draft["caps"] == [{"ledger": "inference", "max_attempts": 30, "max_tokens": 600000}]


def test_track_b_crops_are_scored_against_box_references_when_the_config_names_them(tmp_path, gt):
    config = _track_b(tasks={"pipeline": {"options": {"judges": {"primary": "catalog-gpt-oss-20b-v1"}}}},
                      caps=[{"ledger": "inference", "max_attempts": 10, "max_tokens": 200000},
                            {"ledger": "grounding", "max_attempts": 200, "max_tokens": 800000},
                            {"ledger": "zoom", "max_attempts": 400, "max_tokens": 1000000},
                            {"ledger": "judge", "max_attempts": 200, "max_tokens": 400000}])
    _executor(tmp_path, config, adapter_kwargs={"post": FakeAgent(_answer(), _answer())}).run()
    _score(tmp_path, gt, config, offline=False, marker=_marker(tmp_path, config, cli.plan(config, lambda c: _datasets()),
                                                               judge={"max_requests": 200, "max_tokens": 400000}))
    result = run_score(config, tmp_path / "run", datasets={**_datasets(), "incident_gt": gt[0],
                                                           "box_references": {"records": []}},
                       sheets=gt[1], offline=True)
    crops = result["summary"]["subjects"]["super-pipeline"]["pipeline"]["crops"]
    assert "unscored" not in crops, "with references named, A8 scores every evidence entry (here: none matched)"


def test_a_judge_override_swaps_only_the_endpoint_of_the_same_model(tmp_path, gt):
    from evalv2.score_stage import with_judge_endpoint

    config, _ = _run_track_a(tmp_path)
    task = next(iter(config.tasks))
    swapped = with_judge_endpoint(config, task, "primary", "openrouter-gpt-oss-20b-v1")
    assert swapped.tasks[task].options["judges"]["primary"] == "openrouter-gpt-oss-20b-v1"
    assert config.tasks[task].options["judges"]["primary"] == "catalog-gpt-oss-20b-v1", "the original is untouched"
    with pytest.raises(ScoreRefused, match="differs"):
        with_judge_endpoint(config, task, "primary", "openrouter-mistral-large-4-v1")


def test_the_openrouter_sensitivity_judge_is_mistral_large_4_served_elsewhere():
    from evalv2.score_stage import _same_weights
    from evalv2.tasks.judges import load_judges

    registry = load_judges()
    assert _same_weights(registry.get("catalog-mistral-large-4-v1").model, registry.get("openrouter-mistral-large-4-v1").model)
    assert registry.get("openrouter-gpt-oss-20b-v1").request["extra_body"]["provider"]["allow_fallbacks"] is False


def test_a_dedicated_judge_approval_replaces_a_zero_judge_share_and_must_name_the_judge(tmp_path, gt):
    from evalv2.approval import config_sha256
    from evalv2.score_stage import JUDGE_APPROVAL_VERSION, judge_run_caps, marker_problems

    config, _ = _run_track_a(tmp_path)
    marker = {"approval": JUDGE_APPROVAL_VERSION, "experiment": config.experiment, "config_sha256": config_sha256(config),
              "judges": ["openrouter-gpt-oss-20b-v1"], "judge": {"max_requests": 50, "max_tokens": 100_000},
              "approved_by": "owner", "approved_at": "2026-11-02"}
    assert marker_problems(marker, config) == []
    caps = judge_run_caps(config, marker)
    assert [(c.max_attempts, c.max_tokens) for c in caps] == [(50, 100_000)], "only the judge approval's caps"
    assert "approver" in " ".join(marker_problems({**marker, "approved_by": ""}, config))
    with pytest.raises(ScoreRefused, match="does not name judge"):
        _score(tmp_path, gt, config, offline=False, marker={**marker, "judges": ["someone-else"]})


def test_the_openrouter_judges_fit_the_approved_judge_budget():
    from evalv2.tasks.judges import load_judges

    reg = load_judges()
    p, s = reg.get("openrouter-gpt-oss-20b-v1"), reg.get("openrouter-mistral-large-4-v1")
    assert p.max_requests + s.max_requests <= 1_298 and p.max_tokens + s.max_tokens <= 2_998_000, "2 calls kept for the probe"
    usd = p.max_tokens * 0.13 / 1e6 + s.max_tokens * 2.10 / 1e6
    probe_worst = 2 * 2_275 * 2.10 / 1e6  # the compatibility probe, priced at the dearer judge
    assert usd + probe_worst <= 1.77, usd
