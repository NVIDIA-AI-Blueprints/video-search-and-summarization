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

"""The chat task (C1c-chat): corpora, planning, the harness's ports and run loop, scoring blocks, readiness, config.

Everything here runs without the agent package: the router is a stub. The real router runs in
``test_v2_chat_harness_agent.py`` with the agent's Python.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from evalv2 import cli as evalv2_cli
from evalv2.core.artifacts import ArtifactStore
from evalv2.core.budget import BudgetExceeded, BudgetLedger, Cap
from evalv2.core.config import RunConfig, load_config
from evalv2.core.hashing import sha256_file
from evalv2.core.netguard import no_network
from evalv2.tasks.base import TASKS
from evalv2.tasks.chat import cli as chat_cli
from evalv2.tasks.chat import corpus, evaluate, harness, readiness, runner

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat_task_fixtures import CLIPS, RECORDS, _prediction  # noqa: E402
from test_v2_chat_references import GT  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parents[2] / "experiments" / "v2" / "c1c-chat-pilot.json"
AGENT_CHAT = Path(__file__).resolve().parents[7] / "services" / "agent" / "src" / "vss_agents" / "utils" / \
    "grounded_chat.py"


def _config(**overrides):
    data = json.loads(CONFIG_PATH.read_text())
    data["tasks"]["chat"]["options"]["references"]["sha256"] = "0" * 64
    data.update(overrides)
    return RunConfig.model_validate(data)


def _items(records=RECORDS, corpora=("oracle",)):
    chosen = runner.half_records(records, half="dev", clips=CLIPS)
    return runner.work_items(chosen, corpora=corpora, subjects=["chat-subject"], repeats=1)


# ---------------------------------------------------------------------------
# corpora and mapping
# ---------------------------------------------------------------------------


def test_the_oracle_corpus_is_the_ground_truths_reports_with_stable_version_ids():
    snap = corpus.oracle_snapshot(GT, CLIPS)
    assert sorted(snap["reports"]) == CLIPS
    assert [snap["reports"][c]["version_id"] for c in CLIPS] == [100_001, 100_002, 100_003]
    assert snap["reports"]["Assault001"]["payload"] == GT["by_incident_id"]["Assault001"]["p1_shaped"]
    assert all(r["evidence"] == [] for r in snap["reports"].values())
    assert corpus.snapshot_sha256(snap) == corpus.snapshot_sha256(corpus.oracle_snapshot(GT, list(reversed(CLIPS))))
    with pytest.raises(corpus.CorpusError, match="Missing001"):
        corpus.oracle_snapshot(GT, ["Missing001"])


def test_the_system_corpus_keeps_valid_reports_strips_images_and_lists_the_missing():
    predictions = {"Assault001": _prediction("Assault001"), "Assault002": None,
                   "Burglary003": _prediction("Burglary003", valid=False)}
    snap = corpus.system_snapshot(predictions, CLIPS, source={"run": "r", "subject": "s"})
    assert sorted(snap["reports"]) == ["Assault001"]
    assert snap["source"]["missing"] == ["Assault002", "Burglary003"]
    entry = snap["reports"]["Assault001"]["evidence"][0]
    assert entry["image"] == "" and "frame" not in entry and entry["box_seconds"] == 3
    assert snap["reports"]["Assault001"]["version_id"] == 200_001
    with pytest.raises(corpus.CorpusError, match="stage"):
        corpus.system_snapshot(predictions, CLIPS, stage="raw")


def test_system_predictions_come_from_a_sealed_run_only(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    run = store.run("run-1")
    for clip in ("Assault001", "Assault002"):
        prediction_ref = store.put("pipeline-prediction", _prediction(clip)) if clip == "Assault001" else None
        record_ref = store.put("prediction-record", {"prediction_ref": str(prediction_ref) if prediction_ref else None})
        run.record(f"k-{clip}", record_ref, item_id=clip, subject="super", repeat=1)
    run.record("k-other", store.put("prediction-record", {"prediction_ref": None}), item_id="Burglary003",
               subject="nano", repeat=1)
    with pytest.raises(corpus.CorpusError, match="not sealed"):
        corpus.load_system_predictions(tmp_path / "store", "run-1", "super")
    run.seal({"experiment": "x"})
    loaded = corpus.load_system_predictions(tmp_path / "store", "run-1", "super")
    assert sorted(loaded) == ["Assault001", "Assault002"] and loaded["Assault002"] is None
    with pytest.raises(corpus.CorpusError, match="no prediction"):
        corpus.load_system_predictions(tmp_path / "store", "run-1", "omni")


def test_a_reference_maps_to_its_corpus_report_by_incident_id():
    snap = corpus.system_snapshot({"Assault001": _prediction("Assault001")}, CLIPS)
    assert corpus.subject_incident(RECORDS[0]["payload"], snap) == "Assault001"
    assert corpus.subject_incident(RECORDS[1]["payload"], snap) is None  # Burglary003 has no system report
    assert corpus.subject_incident(RECORDS[4]["payload"], snap) == "library"


# ---------------------------------------------------------------------------
# planning and the budget worst case
# ---------------------------------------------------------------------------


def test_the_plan_keeps_the_halfs_questions_per_corpus_and_skips_rejected_references():
    rejected = {**RECORDS[1], "tier": "human_verified",
                "human": {"decision": "reject", "reviewer": "A Person", "at": "2026-11-02"}}
    items = _items([*RECORDS[:1], rejected, *RECORDS[2:]], corpora=("oracle", "system"))
    ids = {i.spec["record_id"] for i in items}
    assert RECORDS[6]["record_id"] not in ids, "a test-half library question is not a dev question"
    assert rejected["record_id"] not in ids
    assert len(items) == 2 * 5 and {i.spec["corpus"] for i in items} == {"oracle", "system"}
    assert len({i.key for i in items}) == len(items)


def test_the_budget_counts_one_call_per_incident_question_and_two_per_library_question():
    config = _config()
    items = _items()
    estimate = runner.ChatTask().budget_estimate(config, items)
    bounds = config.tasks["chat"].options["tokens_bound"]
    incident = sum(1 for i in items if i.spec["scope"] == "incident")
    library = len(items) - incident
    assert estimate == {"inference": {"attempts": incident + 2 * library,
                                      "tokens": incident * bounds["incident"]
                                      + library * (bounds["library_plan"] + bounds["library_answer"])}}


def test_the_generic_plan_and_preflight_see_the_chat_task():
    config = _config()
    assert "chat" in TASKS
    items = evalv2_cli.plan(config, lambda cfg: {"clips": CLIPS, "chat_references": RECORDS})
    assert len(items["chat"]) == 2 * 6  # six dev questions, two corpora
    blocking, _ = evalv2_cli.preflight_problems(config, items)
    assert blocking == []
    with pytest.raises(RuntimeError, match="agent's Python"):
        runner.ChatTask().execute(items["chat"][0], None, None)


def test_options_and_pinned_references_are_checked(tmp_path):
    data = json.loads(CONFIG_PATH.read_text())
    data["tasks"]["chat"]["options"]["corpora"] = ["oracle", "pooled"]
    with pytest.raises(runner.ChatTaskError, match="corpora"):
        runner.options(RunConfig.model_validate(data))
    path = tmp_path / "records.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in RECORDS))
    opts = {"references": {"path": "records.jsonl", "sha256": "f" * 64}}
    with pytest.raises(runner.ChatTaskError, match="pinned"):
        runner.load_references(opts, tmp_path)
    opts["references"]["sha256"] = sha256_file(path)
    assert len(runner.load_references(opts, tmp_path)) == len(RECORDS)


# ---------------------------------------------------------------------------
# snapshot ports, fake model, metering and budget refusal
# ---------------------------------------------------------------------------


def test_snapshot_ports_serve_each_report_as_its_incidents_official_version():
    snap = corpus.oracle_snapshot(GT, CLIPS)
    store = harness.SnapshotRevisionStore(snap)
    official = asyncio.run(store.get_official_revision_id("Assault001"))
    revision = asyncio.run(store.get_revision(official))
    assert revision["video_id"] == "Assault001" and revision["payload"]["incident"]["type"] == "assault"
    revision["payload"]["incident"]["type"] = "edited"
    assert asyncio.run(store.get_revision(official))["payload"]["incident"]["type"] == "assault", "copies only"
    assert asyncio.run(store.get_revision(1)) is None
    assert asyncio.run(store.get_official_revision_id("Nope")) is None
    reports = asyncio.run(harness.SnapshotLibraryIndex(snap).reports())
    assert [r.incident_id for r in reports] == CLIPS


def _plan_messages(question):
    return [{"role": "system", "content": "Plan.\nIncident types: assault, burglary, road_accident."},
            {"role": "user", "content": question}]


def test_the_fake_model_plans_answers_and_refuses_deterministically():
    model = harness.FakeChatModel()
    schema = {"required": ["incident_type", "wants_count"]}
    plan = asyncio.run(model.complete(_plan_messages("How many assault incidents are there?"), schema))
    assert plan["incident_type"] == "assault" and plan["wants_count"] and plan["query"] == ""
    common = asyncio.run(model.complete(_plan_messages("Which type is the most common?"), schema))
    assert common["group_by"] == "incident_type"
    report = {"incident": {"type": "assault", "severity_level": 4}, "items": [{"item_id": "E1"}], "timeline": []}
    messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "REPORT:\n" + json.dumps(report)},
                {"role": "user", "content": "What type is it?"}]
    answer = asyncio.run(model.complete(messages, {"required": ["answer"]}))
    assert answer["citations"] == [{"item_id": "E1"}] and not answer["insufficient_evidence"]
    messages[-1]["content"] = "What was the licence plate?"
    assert asyncio.run(model.complete(messages, {"required": ["answer"]}))["insufficient_evidence"]
    assert model.last_usage["total_tokens"] > 0 and model.calls == 4


def test_metering_reserves_before_the_call_and_refuses_one_that_would_pass_a_cap(tmp_path):
    inner = harness.FakeChatModel()
    ledger = BudgetLedger([Cap("inference", max_attempts=1, max_tokens=100_000)], tmp_path / "budget.jsonl")
    model = harness.MeteredChatModel(inner, ledger=ledger, subject="s")
    asyncio.run(model.complete(_plan_messages("How many?"), {"required": ["wants_count"]}))
    assert model.records[0].kind == "library_plan" and model.records[0].tokens_bound > harness.MAX_OUTPUT_TOKENS
    assert ledger.totals("inference").tokens == inner.last_usage["total_tokens"], "settled with measured tokens"
    with pytest.raises(BudgetExceeded):
        asyncio.run(model.complete(_plan_messages("How many?"), {"required": ["wants_count"]}))
    assert inner.calls == 1, "the refused call was never sent"
    resumed = BudgetLedger([Cap("inference", max_attempts=1, max_tokens=100_000)], tmp_path / "budget.jsonl")
    assert resumed.totals("inference").attempts == 1, "the log carries the counts into a resumed run"


def test_a_failed_call_keeps_its_bound_when_no_usage_was_reported(tmp_path):
    class Failing:
        model, last_usage = "m", {}

        async def complete(self, messages, schema):
            raise RuntimeError("down")

    ledger = BudgetLedger([Cap("inference", max_tokens=100_000)])
    model = harness.MeteredChatModel(Failing(), ledger=ledger, subject="s")
    with pytest.raises(RuntimeError):
        asyncio.run(model.complete(_plan_messages("x"), {"required": ["wants_count"]}))
    assert ledger.totals("inference").tokens == model.records[0].tokens_bound and model.records[0].error


def test_token_bounds_are_pessimistic():
    messages = [{"role": "user", "content": "x" * 300}]
    assert harness.prompt_tokens_bound(messages) == 100 + 8
    assert harness.call_tokens_bound(messages) == 108 + harness.MAX_OUTPUT_TOKENS


# ---------------------------------------------------------------------------
# the run loop, with a stub router (the real one: test_v2_chat_harness_agent.py)
# ---------------------------------------------------------------------------


class _HTTPError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class _Request(SimpleNamespace):
    @classmethod
    def model_validate(cls, body):
        return cls(**body)


class _Response:
    def __init__(self, message, grounding):
        self.message, self.grounding = message, grounding

    def model_dump(self, exclude_none=False):
        return {"message": self.message, "grounding": self.grounding}


def _stub_router(snap, model, *, fail_for=()):
    async def endpoint(request):
        question = request.messages[0]["content"]
        if any(word in question for word in fail_for):
            raise _HTTPError(502, "Chat model error: down")
        await model.complete(_plan_messages(question), {"required": ["wants_count"]})
        incident = (getattr(request, "context", None) or {}).get("incident_id")
        citations = [{"incident_id": incident, "item_id": "E1"}] if incident else []
        return _Response({"content": "An answer.", "citations": citations, "insufficient_evidence": False},
                         {"answered_by": "model", "plan": None})

    gc = SimpleNamespace(MAX_LISTED=5, rank=lambda reports, query: reports, matches=lambda report, plan: True)
    return harness.CorpusRouter("oracle", snap, endpoint, harness.SnapshotLibraryIndex(snap), _Request, _HTTPError, gc)


def _run(tmp_path, items, model, router, **kwargs):
    records = {r["record_id"]: r for r in RECORDS}
    return asyncio.run(harness.run_items(items, records, {"oracle": router}, model, tmp_path / "answers.jsonl",
                                         **kwargs))


def test_a_run_writes_one_row_per_item_and_a_resume_asks_only_open_items(tmp_path):
    snap = corpus.oracle_snapshot(GT, CLIPS)
    model = harness.MeteredChatModel(harness.FakeChatModel(), subject="chat-subject")
    items = _items()
    first = _run(tmp_path, items, model, _stub_router(snap, model), limit=2)
    assert first.asked == 2 and first.to_json()["open"] == len(items) - 2
    second = _run(tmp_path, items, model, _stub_router(snap, model))
    assert second.already_done == 2 and second.asked == len(items) - 2
    rows = harness.read_rows(tmp_path / "answers.jsonl")
    assert len(rows) == len(items) and {r["key"] for r in rows} == {i.key for i in items}
    row = next(r for r in rows if r["class"] == "incident.type")
    assert row["outcome"] == "answered" and row["usage"]["calls"] == 1 and row["calls"][0]["kind"] == "library_plan"
    library = next(r for r in rows if r["scope"] == "library")
    assert library["library"] == {"ranked": [], "named": []}


def test_http_errors_are_recorded_and_retried_only_on_request(tmp_path):
    snap = corpus.oracle_snapshot(GT, CLIPS)
    model = harness.MeteredChatModel(harness.FakeChatModel(), subject="chat-subject")
    items = _items()
    summary = _run(tmp_path, items, model, _stub_router(snap, model, fail_for=("licence",)))
    assert summary.outcomes == {"answered": len(items) - 1, "http_error": 1}
    again = _run(tmp_path, items, model, _stub_router(snap, model))
    assert again.asked == 0
    retried = _run(tmp_path, items, model, _stub_router(snap, model), retry_errors=True)
    assert retried.asked == 1 and retried.outcomes == {"answered": 1}
    latest = harness.latest_rows(harness.read_rows(tmp_path / "answers.jsonl"))
    assert all(r["outcome"] == "answered" for r in latest.values())


def test_a_budget_refusal_stops_the_run_and_leaves_the_rest_open(tmp_path):
    snap = corpus.oracle_snapshot(GT, CLIPS)
    ledger = BudgetLedger([Cap("inference", max_attempts=2)], tmp_path / "budget.jsonl")
    model = harness.MeteredChatModel(harness.FakeChatModel(), ledger=ledger, subject="chat-subject")
    items = _items()
    summary = _run(tmp_path, items, model, _stub_router(snap, model))
    assert summary.asked == 2 and summary.stopped.startswith("budget") and summary.to_json()["open"] == len(items) - 2
    assert len(harness.read_rows(tmp_path / "answers.jsonl")) == 2


def test_an_incident_without_a_corpus_report_is_no_report_and_costs_nothing(tmp_path):
    snap = corpus.system_snapshot({"Assault001": _prediction("Assault001")}, CLIPS)
    model = harness.MeteredChatModel(harness.FakeChatModel(), subject="chat-subject")
    item = next(i for i in _items() if i.spec["record_id"] == RECORDS[1]["record_id"])
    _run(tmp_path, [item], model, _stub_router(snap, model))
    row = harness.read_rows(tmp_path / "answers.jsonl")[0]
    assert row["outcome"] == "no_report" and row["usage"] == {"calls": 0}


def test_a_resume_with_a_changed_manifest_is_refused(tmp_path):
    harness.check_manifest(tmp_path, {"live": False, "model": "fake", "started_at": "t1"})
    harness.check_manifest(tmp_path, {"live": False, "model": "fake", "started_at": "t2"})
    with pytest.raises(harness.HarnessError, match="live"):
        harness.check_manifest(tmp_path, {"live": True, "model": "fake", "started_at": "t3"})


def test_a_live_run_is_refused_without_the_switch_and_a_marker(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(chat_cli.LIVE_ENV, raising=False)
    code = chat_cli.main(["run", "--config", str(CONFIG_PATH), "--out", str(tmp_path / "out"), "--live"])
    assert code == 2 and "refusing a live chat run" in capsys.readouterr().err
    monkeypatch.setenv(chat_cli.LIVE_ENV, "1")
    code = chat_cli.main(["run", "--config", str(CONFIG_PATH), "--out", str(tmp_path / "out"), "--live"])
    assert code == 2, "the switch alone is not enough: the owner's marker is required"


# ---------------------------------------------------------------------------
# scoring: three blocks, denominators, pending judge checks, bootstrap
# ---------------------------------------------------------------------------


def _row(record, *, corpus_name="oracle", content="", citations=(), refused=False, library=None, outcome="answered",
         dropped=(), repeat=1):
    payload = record["payload"]
    incident = payload["params"].get("incident_id")
    row = {"key": f"{corpus_name}:{record['record_id']}:{repeat}", "record_id": record["record_id"],
           "corpus": corpus_name, "repeat": repeat, "subject": "s", "class": payload["class"],
           "scope": payload["scope"], "incident_id": incident, "outcome": outcome}
    if outcome == "answered":
        row |= {"answer": {"content": content, "citations": [dict(c) for c in citations],
                           "insufficient_evidence": refused},
                "grounding": {"dropped_citations": list(dropped), "answered_by": "model"},
                "calls": [{"context": "REPORT: ..."}], "usage": {"calls": 1, "total_tokens": 10}, "elapsed_ms": 5.0}
        if library is not None:
            row["library"] = library
    return row


def _scores(rows):
    records = {r["record_id"]: r for r in RECORDS}
    with no_network():
        return evaluate.score_run(rows, records, GT)


def test_an_incident_list_answer_is_read_from_the_incidents_it_cites_or_names():
    record = RECORDS[5]  # library.retrieval, target Burglary003
    row = _row(record, content="the burglary (\"Shop door forced\")",
               citations=[{"incident_id": "Burglary003", "item_id": "I1"}],
               library={"ranked": ["Burglary003", "Assault001"], "named": []})
    answer = evaluate.chat_answer(row, record["payload"]["class"])
    assert answer.text == "Burglary003" and answer.citations == ("Burglary003",)
    assert answer.retrieved == ("Burglary003", "Assault001")
    code = _row(RECORDS[4], content="The library holds two assault reports: ...",
                library={"ranked": ["Assault001", "Assault002"], "named": ["Assault001", "Assault002"]})
    assert evaluate.cited_incidents(code) == ["Assault001", "Assault002"]


def test_the_report_keeps_retrieval_answers_and_citations_apart_with_denominators():
    rows = [
        _row(RECORDS[0], content="It is an assault.", citations=[{"incident_id": "Assault001", "item_id": "E1"}]),
        _row(RECORDS[1], content="Three people.", citations=[{"incident_id": "Burglary003", "item_id": "E9"}],
             dropped=["E9x"]),
        _row(RECORDS[2], content="The report does not say.", refused=True),
        _row(RECORDS[3], content="A man hits another man.", citations=[{"incident_id": "Assault001", "item_id": "E1"}]),
        _row(RECORDS[4], content="The library holds two assault reports.",
             library={"ranked": ["Assault001", "Assault002"], "named": ["Assault001", "Assault002"]}),
        _row(RECORDS[5], content="the burglary", citations=[{"incident_id": "Assault001", "item_id": "E1"}],
             library={"ranked": ["Assault001", "Burglary003"], "named": []}),
        _row(RECORDS[0], corpus_name="system", outcome="no_report"),
    ]
    scores = _scores(rows)
    result = evaluate.report(scores, resamples=200)
    oracle = result["corpora"]["oracle"]
    assert oracle["coverage"] == {"planned": 6, "scored": 6}
    assert result["corpora"]["system"]["coverage"] == {"planned": 1, "no_report": 1}
    every = oracle["tiers"]["all"]
    retrieval = every["retrieval"]
    assert retrieval["questions"] == 2
    assert retrieval["by_k"]["@1"]["recall"]["value"] == 0.25  # count: 1 of its 2 at rank 1; retrieval: target at 2
    assert retrieval["by_class"]["library.retrieval"]["mrr@10"] == 0.5
    answers = every["answers"]
    assert answers["exact"]["n"] == 4  # type, people count, type count, retrieval list (facts are judged)
    assert answers["exact_by_class"]["incident.type"]["value"] == 1.0
    assert answers["exact_by_class"]["incident.people_count"]["value"] == 0.0
    assert answers["exact_by_class"]["library.type_count"]["value"] == 1.0
    assert answers["exact_by_class"]["library.retrieval"]["value"] == 0.0
    assert answers["refusal"]["recall"] == {"value": 1.0, "n": 1, "ci95": None}
    assert answers["refusal"]["false_refusal_rate"]["value"] == 0.0
    assert answers["judged"]["pending"]["facts"] == 1 and answers["judged"]["fact_recall"]["n"] == 0
    citations = every["citations"]
    assert citations["level"] == "incident" and citations["precision"]["n"] == 5
    assert citations["by_scope"]["library"]["recall"] == 0.5  # the count names both; the retrieval cites the wrong one
    assert citations["validity"]["value"] < 1.0, "a dropped citation lowers validity"
    assert set(oracle["tiers"]) == {"all", "ai_validated", "ai_proposed"}


def test_the_cluster_bootstrap_is_deterministic_and_needs_two_clusters():
    rows = [{"cluster": c, "v": v} for c, v in [("a", 1.0), ("a", 0.0), ("b", 1.0), ("c", 0.0)]]
    metric = lambda sample: sum(r["v"] for r in sample) / len(sample)  # noqa: E731
    first = evaluate.bootstrap(rows, metric, resamples=300)
    assert first == evaluate.bootstrap(rows, metric, resamples=300)
    assert 0.0 <= first[0] <= first[1] <= 1.0
    assert evaluate.bootstrap(rows[:2], metric) is None


# ---------------------------------------------------------------------------
# reference readiness
# ---------------------------------------------------------------------------


def test_readiness_recounts_every_record_and_lists_who_reviews_what():
    halves = {"dev": CLIPS, "test": []}
    gt = {"by_incident_id": GT["by_incident_id"]}
    records = [r for r in RECORDS if r["payload"]["params"].get("corpus") != "test"]
    result = readiness.readiness(records, gt, halves, seed=7)
    assert result["consistent"] == len(records) and result["inconsistent"] == []
    proposed = {r["record_id"] for r in records if r["tier"] == "ai_proposed"}
    review = {r["record_id"] for r in result["needs_review"]["records"]}
    assert proposed <= review and result["needs_review"]["ai_proposed"] == len(proposed)
    assert result["not_recountable"] == {"incident.facts": 1, "library.retrieval": 1}
    assert result["human_verified"] == 0 and "AI-generated" in result["disclosure"]
    tampered = json.loads(json.dumps(records[0]))
    tampered["payload"]["answer"]["value"] = "burglary"
    bad = readiness.readiness([tampered, *records[1:]], gt, halves, seed=7)
    assert [r["record_id"] for r in bad["inconsistent"]] == [tampered["record_id"]]
    assert bad["by_class"]["incident.type"]["inconsistent"] == 1


# ---------------------------------------------------------------------------
# the proposed pilot config
# ---------------------------------------------------------------------------


def test_the_proposed_chat_pilot_is_dev_only_capped_and_asks_the_grounded_default_model():
    config = load_config(CONFIG_PATH)
    assert config.split.half == "dev" and config.purpose == "pilot" and config.budget_mode == "hard_stop"
    assert config.repeats == 1 and list(config.tasks) == ["chat"]
    default = re.search(r'DEFAULT_MODEL = "([^"]+)"', AGENT_CHAT.read_text()).group(1)
    assert [s.model for s in config.subjects] == [default]
    opts = runner.options(config)
    assert opts["corpora"] == ["oracle", "system"] and re.fullmatch(r"[0-9a-f]{64}", opts["references"]["sha256"])
    run_wide = [c for c in config.budget_caps() if c.subject == "*" and c.ledger != "judge"]
    assert [(c.ledger, c.max_attempts, c.max_tokens) for c in run_wide] == [("inference", 180, 600_000)]
    judge = next(c for c in config.budget_caps() if c.ledger == "judge")
    assert judge.max_attempts == 0 and judge.max_tokens == 0, "judged checks wait for a judge budget"
    assert "PROPOSED" in config.notes


def test_the_proposed_pilot_plans_its_items_from_the_private_references_when_present():
    try:
        records = runner.load_references(runner.options(load_config(CONFIG_PATH)))
    except Exception as exc:  # noqa: BLE001 - the private records are absent on CI
        pytest.skip(f"private chat references not available: {exc}")
    config = load_config(CONFIG_PATH)
    split = json.loads((Path(evalv2_cli.__file__).parents[1] / "eval_reproducibility" / "split-v2.json").read_text())
    items = evalv2_cli.plan(config, lambda cfg: {"clips": split["dev"], "chat_references": records})["chat"]
    assert len(items) == 140 and not {i.spec["record_id"] for i in items} & {
        r["record_id"] for r in records if r["payload"]["params"].get("corpus") == "test"}
    attempts, tokens = evalv2_cli.worst_cases(config, {"chat": items})[("chat", "nemotron-ultra-chat", "inference")]
    assert attempts == 180
    blocking, hard_stop = evalv2_cli.preflight_problems(config, {"chat": items})
    assert blocking == [] and hard_stop, "the cap sits below the configured per-call worst case, and says so"


def test_the_generic_executor_refuses_a_chat_config(capsys):
    config = _config()
    loader = lambda cfg: {"clips": CLIPS, "chat_references": RECORDS}  # noqa: E731
    code = evalv2_cli.main(["run", "--config", str(CONFIG_PATH), "--live"], load_datasets=loader)
    assert code == 2 and "evalv2.tasks.chat" in capsys.readouterr().err
    assert config.tasks["chat"]
