# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""C1c dashboard task, functional tests only: fake models and synthetic references, never a quality result.

The request equals the console's golden byte for byte; a correct spec, a wrong spec, a refusal and an invalid reply
are stored and scored apart; the executor runs C1c with no clip or R2, refuses over a cap before sending, and
resumes without re-sending; ``score`` aggregates with denominators, intervals and figures (recount and SQL)."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import httpx
import pytest

from evalv2 import approval as approvals
from evalv2 import cli
from evalv2.adapters.base import CallContext
from evalv2.adapters.fake import FakeAdapter, Scripted
from evalv2.core.artifacts import ArtifactRef, ArtifactStore
from evalv2.core.budget import BudgetLedger, Cap
from evalv2.core.checkpoint import Checkpoint
from evalv2.core.config import RunConfig, load_config
from evalv2.core.pacing import Pacer
from evalv2.core.retry import POLICIES, Failure
from evalv2.executor import LiveExecutor
from evalv2.references import dashboard as dash
from evalv2.references import dashboard_snapshot as snap
from evalv2.requests import chart
from evalv2.score_stage import ScoreRefused, run_dashboard_score
from evalv2.tasks.base import TASKS, WorkItem
from evalv2.tasks.dashboard import report
from evalv2.tasks.dashboard.runner import TOKENS_PER_ATTEMPT, DashboardTask, reply_kind

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "chart-allowlist.json"
ALLOWLIST = json.loads(FIXTURE.read_text())
GOLDEN_DIR = Path(__file__).resolve().parents[3] / "contracts" / "golden"
CONFIG_PATH = Path(__file__).resolve().parents[2] / "experiments" / "v2" / "c1c-dashboard-pilot.json"
_spec = importlib.util.spec_from_file_location("load_golden", GOLDEN_DIR / "load_golden.py")
assert _spec is not None and _spec.loader is not None
golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(golden)

ULTRA, NANO = "nvidia/nemotron-3-ultra", "nvidia/nemotron-3-nano-30b-a3b"
GEN = {"prompt": "gen-dashboard-request-v1", "model": "claude"}
VAL = {"id": "gen-dashboard-respec-v1"}
TYPES = {"metric": "incidents", "dimension": "incident_type", "period": "last_30_days", "incidentType": None,
         "severity": None}
SEVERITY = {"metric": "incidents", "dimension": "severity", "period": "last_90_days", "incidentType": "assault",
            "severity": None}
OUTCOMES = {"metric": "analyses", "dimension": "analysis_outcome", "period": "all_time", "incidentType": None,
            "severity": None}
ENV = {"VLM_GATEWAY_BASE_URL": "https://gateway.example/v1", "VLM_GATEWAY_API_KEY": "secret-key"}  # no R2 at all
USAGE = {"prompt_tokens": 1010, "completion_tokens": 45, "total_tokens": 1055}


def _record(request, expected):
    return dash.build_record(request, expected, ALLOWLIST, paraphrases=[f"{request} (a)", f"{request} (b)"],
                             dataset="dashboard-test", generator=GEN,
                             rederived=dict(expected), validator=VAL)


RECORDS = [_record("What kinds of incidents happened in the last month?", TYPES),
           _record("Severity of assaults over the last quarter", SEVERITY),
           _record("How did analysis attempts turn out?", OUTCOMES),
           _record("Incidents at the north gate", dash.UNSUPPORTED),
           _record("Make up some numbers for next year", dash.UNSUPPORTED)]
BY_REQUEST = {r["payload"]["request"]: r for r in RECORDS}
# What the fake model answers per request: right, wrong period, no JSON, correct refusal, a spec for a refusal.
REPLIES = {
    "What kinds of incidents happened in the last month?": "```json\n" + json.dumps(TYPES) + "\n```",
    "Severity of assaults over the last quarter": json.dumps({**SEVERITY, "period": "last_30_days"}),
    "How did analysis attempts turn out?": "Sorry, I can only describe charts.",
    "Incidents at the north gate": 'Here you go: {"unsupported": true}',
    "Make up some numbers for next year": json.dumps(TYPES),
}


def _datasets(records=RECORDS, snapshot=None):
    return {"split": {"version": "split-v2", "dev": ["Assault001"], "test": ["Fighting003"]}, "half": "dev",
            "clips": ["Assault001"], "inputs": {"split-v2/split-v2.json": "a" * 64},
            "dashboard_references": {"dataset": "dashboard-test", "kind": "dashboard_references",
                                     "records": list(records),
                                     "snapshot": snapshot if snapshot is not None else snap.seeded_snapshot(3)}}


def _config(**changes):
    raw = {"experiment": "v2-test-dashboard", "split": {"version": "split-v2", "half": "dev"},
           "datasets": {"dashboard_references": "dashboard-test"},
           "subjects": [{"id": "ultra-chart", "adapter": "openai_compat", "endpoint": "switchyard", "model": ULTRA},
                        {"id": "nano-chart", "adapter": "openai_compat", "endpoint": "switchyard", "model": NANO}],
           "tasks": {"dashboard": {}}, "repeats": 1, "seed": 20261101, "retry_policy": "retry-v2",
           "purpose": "pilot",
           "caps": [{"ledger": "inference", "max_attempts": 30, "max_tokens": 45000}]}
    raw.update(changes)
    return RunConfig.model_validate(raw)


def _context(tmp_path):
    return CallContext(store=ArtifactStore(tmp_path / "store"), ledger=BudgetLedger([Cap("inference", 100, 1_000_000)]),
                       policy=POLICIES["retry-v2"], subject="ultra-chart", task="dashboard", sleep=lambda s: None)


def _item(record, subject="ultra-chart", model=ULTRA):
    return WorkItem("dashboard", subject, record["record_id"], 1,
                    {"model": model, "prompt_sha256": chart.chart_provenance()["prompt_sha256"],
                     "request": record["payload"]["request"]})


class Upstream:
    """A fake OpenAI-compatible model: it answers each chart request from REPLIES and keeps every body it got."""

    def __init__(self, crash_at=None, status=None):
        self.bodies, self.crash_at, self.status = [], crash_at, status

    def __call__(self, request):
        self.bodies.append(request.content)
        body = json.loads(request.content)
        if self.crash_at is not None and len(self.bodies) == self.crash_at:
            raise KeyboardInterrupt("the harness host went away")
        if self.status is not None:
            return httpx.Response(self.status, json={"error": "upstream"})
        text = REPLIES[body["messages"][1]["content"]]
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": text},
                                                      "finish_reason": "stop"}], "usage": USAGE})


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _executor(tmp_path, config, *, upstream, datasets=None, marker_changes=None):
    datasets = datasets or _datasets()
    items = cli.plan(config, lambda cfg: datasets)
    marker = approvals.draft(config, items)
    marker.update(approved_by="Owner (test)", approved_at="2026-11-02", note="test", **(marker_changes or {}))
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(marker))
    clock = Clock()

    def no_signing(key, ttl):
        raise AssertionError("C1c never signs a clip URL")

    return LiveExecutor(config, datasets, items, out=tmp_path / "run", marker=approvals.read_approval(path),
                        hard_stop=cli.preflight_problems(config, items)[1], manifest_clips=[], signer=no_signing,
                        adapter_kwargs={"transport": httpx.MockTransport(upstream)}, environ=ENV,
                        pacer=Pacer(rpm=60, parallel=1, tpm={}, clock=clock, sleep=clock.sleep),
                        code_dir=tmp_path, sleep=clock.sleep, clock=clock)


# --- the request ---------------------------------------------------------------------------------------------------


def test_the_request_is_the_consoles_golden_byte_for_byte():
    case = golden.case("chart", "request")
    built = chart.build_chart_request(case["inputs"]["model"], case["inputs"]["request"])
    assert golden.mismatch("chart", "request", built, "eval") is None
    on_file = json.loads((GOLDEN_DIR / "chart.json").read_text())["cases"]["request"]["body"]
    assert list(built) == list(on_file), "the console's key order (vlm-gateway re-posts it unchanged)"
    url = "https://upstream.example/v1/chat/completions"
    assert httpx.Request("POST", url, json=built).content == httpx.Request("POST", url, json=on_file).content
    assert built["messages"][0]["content"] == (GOLDEN_DIR.parent / "prompts" / "chart" / "system.txt").read_text()


def test_the_request_is_trimmed_and_length_checked_as_the_route_does():
    body = chart.build_chart_request(ULTRA, " ﻿ incident distribution \n")
    assert body["messages"][1]["content"] == "incident distribution"
    assert chart.request_text("x" * 300) == "x" * 300
    for bad in ("", " \n\t", "x" * 301):
        with pytest.raises(chart.ChartRequestError):
            chart.request_text(bad)


def test_a_changed_prompt_is_refused(monkeypatch, tmp_path):
    prompts = tmp_path / "prompts" / "chart"
    prompts.mkdir(parents=True)
    (prompts / "system.txt").write_text("edited")
    (prompts / "allowlist.json").write_text("{}")
    registry = json.loads((chart.CONTRACTS_DIR / "prompts" / "registry.json").read_text())
    (tmp_path / "prompts" / "registry.json").write_text(json.dumps(registry))
    monkeypatch.setattr(chart, "CONTRACTS_DIR", tmp_path)
    with pytest.raises(chart.ChartRequestError, match="registry sha256"):
        chart.system_prompt()


@pytest.mark.parametrize(("text", "parsed"), [
    ('{"unsupported": true}', {"unsupported": True}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Sure. {"a": {"b": 2}} Done.', {"a": {"b": 2}}),
    ("no json here", None),
    ("} backwards {", None),
    ('{"a": NaN}', None),
    ("", None),
    (None, None),
])
def test_the_reply_is_parsed_as_the_console_parses_it(text, parsed):
    assert chart.parse_reply(text) == parsed


# --- the task ------------------------------------------------------------------------------------------------------


def test_the_task_is_registered_and_plans_record_subject_repeat():
    config = _config(repeats=2)
    items = TASKS["dashboard"]().plan(config, _datasets())
    assert len(items) == len(RECORDS) * 2 * 2 and len({i.key for i in items}) == len(items)
    rejected = {**RECORDS[0], "tier": "human_verified", "human": {"decision": "reject"}}
    assert len(DashboardTask().plan(_config(), _datasets([rejected, *RECORDS[1:]]))) == (len(RECORDS) - 1) * 2
    too_long = dash.build_record("x" * 301, TYPES, ALLOWLIST, paraphrases=[], dataset="d", generator=GEN)
    with pytest.raises(chart.ChartRequestError):
        DashboardTask().plan(_config(), _datasets([too_long]))
    needs = cli.worst_cases(config, {"dashboard": items})
    assert needs[("dashboard", "ultra-chart", "inference")] == (10 * 3, 10 * 3 * TOKENS_PER_ATTEMPT)


@pytest.mark.parametrize(("request_text", "outcome", "kind"), [
    ("What kinds of incidents happened in the last month?", "ok", "spec"),        # correct spec (in a code fence)
    ("Severity of assaults over the last quarter", "ok", "spec"),                 # wrong spec (another period)
    ("Incidents at the north gate", "ok", "refusal"),                             # a refusal
    ("How did analysis attempts turn out?", "invalid_output", "no_json"),         # an invalid reply
])
def test_execute_stores_the_reply_and_how_the_console_reads_it(tmp_path, request_text, outcome, kind):
    record = BY_REQUEST[request_text]
    adapter = FakeAdapter("ultra-chart", ULTRA, [Scripted(text=REPLIES[request_text], usage=USAGE)])
    ctx = _context(tmp_path)
    prediction = DashboardTask(allowlist=ALLOWLIST).execute(_item(record), adapter, ctx)
    assert prediction.outcome == outcome and adapter.calls == 1
    stored = ctx.store.get(ArtifactRef.parse(prediction.prediction_ref))
    assert stored["raw"] == REPLIES[request_text] and stored["reply_kind"] == kind
    assert stored["reply"] == chart.parse_reply(REPLIES[request_text])
    assert prediction.provenance["stages"]["chart"]["prompt_sha256"] == chart.chart_provenance()["prompt_sha256"]


def test_execute_sends_the_golden_body_through_the_adapter(tmp_path):
    record = RECORDS[0]
    adapter = FakeAdapter("ultra-chart", ULTRA, [Scripted(text=REPLIES[record["payload"]["request"]], usage=USAGE)])
    seen = []
    original = adapter.complete

    def spy(request, ctx):
        seen.append(request)
        return original(request, ctx)

    adapter.complete = spy  # type: ignore[method-assign]
    DashboardTask(allowlist=ALLOWLIST).execute(_item(record), adapter, _context(tmp_path))
    assert seen == [chart.build_chart_request(ULTRA, record["payload"]["request"])]


def test_a_failed_request_is_a_failure_not_a_reply(tmp_path):
    adapter = FakeAdapter("ultra-chart", ULTRA, [Scripted(failure=Failure("http_error", "500", 500))])
    prediction = DashboardTask(allowlist=ALLOWLIST).execute(_item(RECORDS[0]), adapter, _context(tmp_path))
    assert prediction.outcome == "http_error" and prediction.prediction_ref is None
    assert prediction.failure_kind == "http_error"


def test_the_reply_kinds():
    assert reply_kind("", None, ALLOWLIST) == "empty"
    assert reply_kind("x", None, ALLOWLIST) == "no_json"
    assert reply_kind("x", {"unsupported": True}, ALLOWLIST) == "refusal"
    assert reply_kind("x", {"unsupported": 1}, ALLOWLIST) == "invalid_object"
    assert reply_kind("x", {**TYPES, "title": "t"}, ALLOWLIST) == "invalid_object"
    assert reply_kind("x", dict(TYPES), ALLOWLIST) == "spec"


# --- the executor --------------------------------------------------------------------------------------------------


def test_the_executor_runs_c1c_with_no_clip_and_seals(tmp_path):
    upstream = Upstream()
    executor = _executor(tmp_path, _config(), upstream=upstream)
    assert executor.refusals() == [], "C1c needs no R2 credentials"
    summary = executor.run()
    assert summary["sealed"] and summary["items"] == 10 and len(upstream.bodies) == 10
    assert summary["outcomes"] == {"ok": 8, "invalid_output": 2}
    for raw in upstream.bodies:
        body = json.loads(raw)
        expected = chart.build_chart_request(body["model"], body["messages"][1]["content"])
        assert raw == httpx.Request("POST", "https://x/v1", json=expected).content, "the golden body, byte for byte"
    manifest = json.loads((tmp_path / "run" / "store" / "runs" / "v2-test-dashboard" / "manifest.json").read_text())
    assert manifest["track"] == "C" and "clip_urls" not in manifest
    assert manifest["requests"]["chart"] == chart.chart_provenance()
    attempts = [json.loads(line) for line in (tmp_path / "run" / "attempts.jsonl").read_text().splitlines()]
    assert len(attempts) == 10 and {a["prompt_sha256"] for a in attempts} == {manifest["requests"]["chart"]
                                                                                ["prompt_sha256"]}
    assert all(a["tokens"]["total"] == USAGE["total_tokens"] for a in attempts)


def test_a_cap_refuses_before_sending_and_the_items_stay_open(tmp_path):
    config = _config(caps=[{"ledger": "inference", "max_attempts": 4, "max_tokens": 45000}], budget_mode="hard_stop")
    upstream = Upstream()
    executor = _executor(tmp_path, config, upstream=upstream)
    summary = executor.run()
    assert len(upstream.bodies) == 4, "only the attempts the cap covers are sent"
    assert summary["outcomes"].get("budget_exceeded") is None and summary["open"] == 6 and not summary["sealed"]
    attempts = [json.loads(line) for line in (tmp_path / "run" / "attempts.jsonl").read_text().splitlines()]
    refused = [a for a in attempts if a["outcome"] == "budget_exceeded"]
    assert len(refused) == 6 and all(a["sent"] is False for a in refused)


def test_a_marker_that_does_not_fit_refuses_everything(tmp_path):
    upstream = Upstream()
    executor = _executor(tmp_path, _config(), upstream=upstream, marker_changes={"max_items": 3})
    with pytest.raises(Exception, match="over the approved 3"):
        executor.run()
    assert upstream.bodies == []


def test_resume_sends_only_the_open_items(tmp_path):
    with pytest.raises(KeyboardInterrupt):
        _executor(tmp_path, _config(), upstream=Upstream(crash_at=4)).run()
    done = Checkpoint(tmp_path / "run" / "checkpoint.jsonl").final_keys()
    assert len(done) == 3
    upstream = Upstream()
    summary = _executor(tmp_path, _config(), upstream=upstream).run()
    assert summary["sealed"] and len(upstream.bodies) == 7, "the three final items are never re-sent"
    again = Upstream()
    assert _executor(tmp_path, _config(), upstream=again).run()["sent_items"] == 0 and again.bodies == []


# --- scoring -------------------------------------------------------------------------------------------------------


def _scored_run(tmp_path, config=None, datasets=None):
    config = config or _config()
    datasets = datasets or _datasets()
    _executor(tmp_path, config, upstream=Upstream(), datasets=datasets).run()
    return config, datasets


def test_scoring_separates_invalid_replies_from_wrong_specs_with_denominators(tmp_path):
    config, datasets = _scored_run(tmp_path)
    result = run_dashboard_score(config, tmp_path / "run", datasets=datasets, figures="recount", allowlist=ALLOWLIST)
    ultra = result["summary"]["subjects"]["ultra-chart"]
    assert ultra["model"] == ULTRA and ultra["items"] == 5 and ultra["answered"] == 5 and ultra["coverage"] == 1.0
    assert ultra["outcomes"] == {"exact": 1, "false_accept": 1, "parse_failure": 1, "refusal_correct": 1,
                                 "valid_but_wrong": 1}
    assert ultra["errors"] == {"invalid_reply": 1, "invalid_reply_kinds": {"no_json": 1}, "wrong_spec": 1,
                               "false_accept": 1, "false_refusal": 0}
    metrics = ultra["metrics"]
    assert metrics["exact_spec_accuracy"] | {"interval": None} == {"value": 0.3333, "interval": None,
                                                                    "numerator": 1, "denominator": 3}
    assert metrics["refusal_precision"]["value"] == 1.0 and metrics["refusal_precision"]["denominator"] == 1
    assert metrics["refusal_recall"]["value"] == 0.5 and metrics["refusal_recall"]["denominator"] == 2
    assert metrics["overall_accuracy"]["value"] == 0.4 and metrics["invalid_reply_rate"]["value"] == 0.2
    low, high = metrics["overall_accuracy"]["interval"]
    assert 0.0 <= low <= 0.4 <= high <= 1.0
    fields = ultra["field_accuracy"]
    assert fields["denominator"] == 2 and fields["period"] == 0.5 and fields["dimension"] == 1.0
    assert fields["filters"] == 1.0
    assert ultra["figure_outcomes"]["refusal_correct"] == 1 and ultra["figure_outcomes"]["false_accept"] == 1
    assert ultra["by_tier"]["ai_validated"]["replies"] == 5 and "primary" not in ultra["by_tier"]
    summary = result["summary"]
    assert summary["figures"]["source"] == "recount" and summary["figures"]["clock"] == "2026-11-01T12:00:00+00:00"
    assert summary["bootstrap"]["seed"] == 20261027 and summary["final_items"] == 10
    out = tmp_path / "run" / "scoring" / "evalv2-dashboard-score-v1" / "primary"
    assert json.loads((out / "dashboard-summary.json").read_text())["subjects"]["nano-chart"]["items"] == 5
    assert len((out / "dashboard-items.jsonl").read_text().splitlines()) == 10
    again = run_dashboard_score(config, tmp_path / "run", datasets=datasets, figures="recount", allowlist=ALLOWLIST)
    assert again["summary"]["subjects"] == result["summary"]["subjects"], "the seeded bootstrap is reproducible"


def test_the_right_spec_shows_the_right_figures_and_a_wrong_one_may_not(tmp_path):
    config, datasets = _scored_run(tmp_path)
    result = run_dashboard_score(config, tmp_path / "run", datasets=datasets, figures="recount", allowlist=ALLOWLIST)
    by_record = {(i["subject"], i["record_id"]): i for i in result["items"]}
    right = by_record[("ultra-chart", BY_REQUEST["What kinds of incidents happened in the last month?"]["record_id"])]
    assert right["outcome"] == "exact" and right["figures"] == "figures_match"
    invalid = by_record[("ultra-chart", BY_REQUEST["How did analysis attempts turn out?"]["record_id"])]
    assert invalid["outcome"] == "parse_failure" and invalid["figures"] == "false_refusal", \
        "the console shows an unparseable reply as 'Can't chart that'"


def test_failed_requests_are_counted_not_scored(tmp_path):
    config, datasets = _config(), _datasets()
    _executor(tmp_path, config, upstream=Upstream(status=500), datasets=datasets).run()
    result = run_dashboard_score(config, tmp_path / "run", datasets=datasets, figures="none", allowlist=ALLOWLIST)
    ultra = result["summary"]["subjects"]["ultra-chart"]
    assert ultra["request_failed"] == 5 and ultra["answered"] == 0 and ultra["coverage"] == 0.0
    assert ultra["metrics"] == {}


def test_bootstrap_resamples_records_with_their_repeats():
    rows = [{"record_id": "a", "class": "spec", "outcome": "exact"},
            {"record_id": "a", "class": "spec", "outcome": "valid_but_wrong"},
            {"record_id": "b", "class": "unsupported", "outcome": "refusal_correct"}]
    stats = report.bootstrap(rows, resamples=2000)
    assert stats["exact_spec_accuracy"]["value"] == 0.5 and stats["exact_spec_accuracy"]["denominator"] == 2
    assert stats["figure_accuracy"] == {"value": None, "interval": None, "numerator": 0, "denominator": 0}
    assert report.bootstrap(rows, resamples=2000) == stats


def test_score_refuses_without_a_snapshot_or_a_run(tmp_path):
    config = _config()
    with pytest.raises(ScoreRefused, match="no checkpoint"):
        run_dashboard_score(config, tmp_path / "nothing", datasets=_datasets(), figures="none", allowlist=ALLOWLIST)
    _scored_run(tmp_path)
    no_snapshot = _datasets()
    del no_snapshot["dashboard_references"]["snapshot"]
    with pytest.raises(ScoreRefused, match="corpus snapshot"):
        run_dashboard_score(config, tmp_path / "run", datasets=no_snapshot, figures="recount", allowlist=ALLOWLIST)


def test_the_cli_scores_a_dashboard_run_without_a_judge_or_live_flag(tmp_path, capsys, monkeypatch):
    config, datasets = _scored_run(tmp_path)
    path = tmp_path / "config.json"
    path.write_text(config.model_dump_json())
    monkeypatch.delenv(cli.LIVE_ENV, raising=False)
    monkeypatch.setattr("evalv2.references.dashboard.ALLOWLIST_PATH", FIXTURE)
    code = cli.main(["score", "--config", str(path), "--out", str(tmp_path / "run"), "--figures", "recount"],
                    load_datasets=lambda cfg: datasets,
                    load_sheets=lambda: pytest.fail("a dashboard score reads no ground-truth sheet"))
    printed = json.loads(capsys.readouterr().out)
    assert code == 0 and printed["ok"] and printed["final_items"] == 10 and printed["figures"] == "recount"


HAS_PGSERVER = all(importlib.util.find_spec(m) is not None for m in ("pgserver", "psycopg"))


@pytest.mark.skipif(not HAS_PGSERVER, reason="pgserver and psycopg run in a throwaway environment")
def test_sql_figures_equal_the_recount(tmp_path):
    config, datasets = _scored_run(tmp_path)
    sql = run_dashboard_score(config, tmp_path / "run", datasets=datasets, figures="sql", allowlist=ALLOWLIST)
    recount = run_dashboard_score(config, tmp_path / "run", datasets=datasets, figures="recount",
                                  allowlist=ALLOWLIST)
    assert [i.get("figures") for i in sql["items"]] == [i.get("figures") for i in recount["items"]]
    assert sql["summary"]["figures"]["calls"]["sql"] > 0


# --- the proposed pilot config -------------------------------------------------------------------------------------


def test_the_proposed_pilot_is_a_capped_dev_pilot_of_the_consoles_chart_model():
    config = load_config(CONFIG_PATH)
    assert config.purpose == "pilot" and config.split.half == "dev" and config.repeats == 1
    assert "APPROVED BY THE OWNER 2026-11-02" in config.notes
    assert config.subjects[0].model == "nvidia/nemotron-3-ultra", "the console's CHART_MODEL default (env.ts)"
    env_ts = (Path(__file__).resolve().parents[3] / "incident-console-v2" / "src" / "lib" / "env.ts").read_text()
    assert "optionalEnvironmentValue('CHART_MODEL') || 'nvidia/nemotron-3-ultra'" in env_ts
    assert {s.endpoint for s in config.subjects} == {"switchyard"}
    items = {"dashboard": [WorkItem("dashboard", s.id, f"r{i}", 1) for s in config.subjects for i in range(75)]}
    problems, hard_stop = cli.preflight_problems(config, items)
    assert problems == [] and hard_stop, "the approved caps (225 calls) sit below the retry worst case: hard stop"
    run_wide = next(c for c in config.caps if c.subject == "*")
    assert run_wide.max_attempts == 225 and run_wide.max_tokens == 1_010_000, "the owner-approved totals"


REAL_RB2 = (Path(os.environ.get("EVALV2_DATA_ROOT") or "/nonexistent") / "v2" / "references"
            / "dashboard-references-v2" / "records.jsonl").is_file()


@pytest.mark.skipif(not REAL_RB2, reason="the RB2 references are private and not on this machine")
def test_the_proposed_pilot_plans_every_registered_reference():
    from evalv2.datasets.loader import load_for_config

    config = load_config(CONFIG_PATH)
    datasets = load_for_config(config)
    items = cli.plan(config, lambda cfg: datasets)["dashboard"]
    assert len(items) == 225 and len({i.item_id for i in items}) == 75
    assert datasets["dashboard_references"]["snapshot"]["taken_at"] == "2026-11-01T12:00:00+00:00"
    for item in items:  # every request builds as the console would send it
        chart.build_chart_request(item.spec["model"], item.spec["request"])
