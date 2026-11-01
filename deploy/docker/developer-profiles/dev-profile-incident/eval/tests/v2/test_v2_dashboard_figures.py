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

"""Dashboard figures: the recount by hand-checked numbers, snapshots, the harness's open-period call, figure scoring;
against the real SQL where pgserver is installed, and against the console's own chart code where Node strips types."""

from __future__ import annotations

import importlib.util
import itertools
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evalv2.datasets import records as rec
from evalv2.references import dashboard as dash
from evalv2.references import dashboard_figures as fig
from evalv2.references import dashboard_replay as replay
from evalv2.references import dashboard_snapshot as snap
from evalv2.sql import harness as h
from evalv2.tasks.dashboard import scoring as sc

ALLOWLIST = json.loads((Path(__file__).resolve().parent / "fixtures" / "chart-allowlist.json").read_text())
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
WINDOW = {"p_period_start": "2026-10-02T12:00:00.000", "p_period_end": "2026-11-01T12:00:00.000",
          "p_timezone": "UTC", "p_scope": "all"}
VALID = json.dumps({"incidentConsoleV2": {"contractVersion": "incident-contract-v4", "status": "valid_first_pass"}})
REPAIRED = json.dumps({"incidentConsoleV2": {"contractVersion": "incident-contract-v4",
                                             "status": "valid_after_structural_repair"}})
FAILED = json.dumps({"incidentConsoleV2": {"contractVersion": "incident-contract-v4", "status": "contract_failed"}})
REQUEST_FAILED = json.dumps({"incidentConsoleV2": {"contractVersion": "incident-contract-v4",
                                                   "status": "request_failed"}})


def _payload(kind, severity, confidence=None, entities=(), instruments=(), assets=()):
    incident = {"type": kind, "severity_level": severity, "title": "t", "description": "d", "start_timestamp": 1,
                "end_timestamp": 2}
    if confidence is not None:
        incident["confidence_score"] = confidence
    return {"incident": incident, "entities": [{"entity_id": f"E{i}", "type": t} for i, t in enumerate(entities)],
            "instruments": [{"instrument_id": f"I{i}", "name": n, "threat_level": t}
                            for i, (n, t) in enumerate(instruments)],
            "assets": [{"asset_id": f"A{i}", "name": n} for i, n in enumerate(assets)], "timeline": [],
            "uncertainties": []}


def _revision(rid, video, number, kind, payload, created, run=None):
    row = {"id": rid, "video_id": video, "revision_no": number, "kind": kind, "payload": payload, "evidence": [],
           **snap.facets(payload), "created_at": created}
    if run:
        row["model_run_id"] = run
    else:
        row.update(created_by="reviewer@example.org", parent_id=rid - 1)
    return row


SNAPSHOT = {"version": h.SNAPSHOT_VERSION, "taken_at": "2026-11-01T12:00:00+00:00", "tables": {
    "videos": [{"id": "V1", "official_revision_id": 1}, {"id": "V2"}, {"id": "V3"}, {"id": "V4"}],
    "model_runs": [
        {"id": "m1", "model_name": "model-a", "notes": VALID, "run_datetime": "2026-10-20T02:59:00"},
        {"id": "m2", "model_name": "model-a", "notes": "free text", "run_datetime": "2026-10-26T09:00:00"},
        {"id": "m3", "model_name": "model-b", "notes": REPAIRED, "run_datetime": "2026-10-28T15:29:00"},
        {"id": "m4", "model_name": "model-b", "notes": VALID, "run_datetime": "2026-09-15T08:00:00"},
        {"id": "m5", "model_name": "model-a", "notes": FAILED, "run_datetime": "2026-10-30T10:00:00"},
        {"id": "m6", "model_name": "model-b", "notes": REQUEST_FAILED, "run_datetime": "2026-08-01T10:00:00"}],
    "report_revisions": [
        # V1: the official revision is the first, not the newer human edit.
        _revision(1, "V1", 1, "ai", _payload("assault", 4, 0.95, entities=("person", "Human ", "vehicle"),
                                             instruments=(("Knife ", 2.5), ("knife", 3), ("baseball  bat", None))),
                  "2026-10-20T03:00:00", "m1"),
        _revision(2, "V1", 2, "human", _payload("burglary", 2), "2026-10-25T10:00:00"),
        # V2: no official revision, so its newest (a re-generation) counts.
        _revision(3, "V2", 1, "ai", _payload("burglary", 2), "2026-10-26T09:01:00", "m2"),
        _revision(4, "V2", 2, "ai", _payload("road accident", 3, 0.42, instruments=(("knife", 4),),
                                             assets=("Shop  Door",)), "2026-10-28T15:30:00", "m3"),
        # V3: unclassified, before the window (inside the previous one). V4 has no report.
        _revision(5, "V3", 1, "ai", _payload(None, None), "2026-09-15T08:01:00", "m4")]}}


def _recount(**overrides):
    return fig.dashboard(SNAPSHOT, {**WINDOW, **overrides}, now=NOW)


def test_the_recount_counts_each_video_once_by_its_official_else_newest_revision():
    d = _recount()
    assert (d["total"], d["official"], d["notOfficial"]) == (2, 1, 1)
    assert [(t["type"], t["count"], t["official"]) for t in d["types"]] == [("assault", 1, 1), ("road accident", 1, 0)]
    assert d["severity"] == {"low": 0, "medium": 1, "high": 1} and d["revisions"] == {"ai": 2, "human": 0}
    assert d["videos"] == {"uploaded": 4, "withOfficial": 1, "awaitingSelection": 2, "withoutReport": 1}
    assert d["previous"] == {"total": 1, "human": 0, "ai": 1, "high": 0}
    assert d["entityTypes"] == [{"label": "human", "count": 1}, {"label": "vehicle", "count": 1}]
    assert d["topInstruments"] == [{"label": "knife", "count": 2}, {"label": "baseball bat", "count": 1}]
    assert d["topAssets"] == [{"label": "shop door", "count": 1, "official": 0, "notOfficial": 1}]
    assert d["threatLevels"] == [{"level": 3, "count": 1}, {"level": 4, "count": 1}], "2.5 rounds to 3, deduplicated"
    assert d["confidence"] == [{"bucket": 4, "count": 1}, {"bucket": 9, "count": 1}]
    assert d["averageConfidence"] == pytest.approx(0.685) and d["confidenceNotProvided"] == 0
    assert d["modelCoverage"] == [
        {"model": "model-a", "withReport": 2, "validFirstPass": 1, "repaired": 0, "legacy": 1, "contractFailed": 1,
         "requestFailed": 0},
        {"model": "model-b", "withReport": 1, "validFirstPass": 0, "repaired": 1, "legacy": 0, "contractFailed": 0,
         "requestFailed": 0}]
    assert len(d["trend"]) == 31 and d["trend"][0]["date"] == "2026-10-02" and d["trend"][-1]["date"] == "2026-11-01"
    assert [(c["day"], c["hour"]) for c in d["heatmap"]] == [(2, 3), (3, 15)]


@pytest.mark.parametrize(("overrides", "total"), [
    ({"p_type": "Fighting"}, 1), ({"p_type": "unclassified", "p_period_start": "2026-09-01T00:00:00"}, 1),
    ({"p_type": "  "}, 0), ({"p_severity": "urgent"}, 0), ({"p_severity": "high"}, 1),
    ({"p_scope": "official"}, 1), ({"p_scope": "not_official"}, 1), ({"p_scope": None}, 1),
    ({"p_day_of_week": 3}, 1), ({"p_hour": 3}, 1), ({"p_hour": 4}, 0),
])
def test_filters_follow_the_rpc(overrides, total):
    assert _recount(**overrides)["total"] == total


def test_local_days_weekdays_and_hours_use_the_time_zone():
    d = _recount(p_timezone="America/New_York")
    assert [(c["day"], c["hour"]) for c in d["heatmap"]] == [(1, 23), (3, 11)], "V1 is Monday 23:00 in New York"
    assert next(t for t in d["trend"] if t["count"])["date"] == "2026-10-19"


def test_an_all_time_dashboard_spans_its_reports_and_an_empty_one_reads_the_clock():
    params = {"p_timezone": "UTC", "p_scope": "all"}
    d = fig.dashboard(SNAPSHOT, params, now=NOW)
    assert d["previous"] is None and d["total"] == 3
    assert (d["trend"][0]["date"], d["trend"][-1]["date"]) == ("2026-09-15", "2026-10-28")
    assert not fig.clock_dependent(params, d)
    empty = fig.dashboard(SNAPSHOT, {**params, "p_type": "explosion"}, now=NOW)
    assert fig.clock_dependent({**params, "p_type": "explosion"}, empty)
    assert empty["trend"] == [{"date": "2026-11-01", "count": 0, "official": 0, "notOfficial": 0}]


def test_the_sql_helpers_are_reimplemented_exactly():
    assert fig.canonical_incident_type(" Fighting ") == "assault" and fig.canonical_incident_type("") is None
    assert fig.canonical_entity_type(None) == "unknown" and fig.canonical_entity_type("Person") == "human"
    assert fig.normalized_name("  Baseball \t Bat ") == "baseball bat"
    assert fig.normalized_name("stick\n") == "stick ", "btrim trims spaces only; the newline becomes a space"
    assert [fig._threat_level(v) for v in (2.5, -2.5, 3.49, 4, True, "3")] == [3, -3, 3, 4, None, None]
    assert [fig.run_outcome(n) for n in (VALID, REPAIRED, FAILED, "x", None, json.dumps({"a": 1}))] == [
        "valid_first_pass", "valid_after_structural_repair", "contract_failed", "legacy", "legacy", "legacy"]


def _chart(spec, chart_type="bar", snapshot=SNAPSHOT, timezone="UTC"):
    return fig.chart(spec, chart_type=chart_type, timezone=timezone, now=NOW, allowlist=ALLOWLIST,
                     fetch=lambda p: fig.dashboard(snapshot, p, now=NOW))


def _spec(**overrides):
    return {"metric": "incidents", "dimension": "incident_type", "period": "last_30_days", "incidentType": None,
            "severity": None, **overrides}


def test_charts_split_coarsen_and_refuse_as_the_console_does():
    split = _chart(_spec())
    assert split["series"] == ["official", "not_official"]
    assert split["rows"] == [{"category": "Assault", "values": {"official": 1, "not_official": 0}},
                             {"category": "Road accident", "values": {"official": 0, "not_official": 1}}]
    assert _chart(_spec(), "pie")["rows"][0] == {"category": "Assault", "values": {"count": 1}}
    assert _chart(_spec(dimension="report_day", period="last_90_days"), "pie") == {"kind": "needs_bar"}
    assert _chart(_spec(dimension="report_day", period="last_90_days"))["dimension"] == "report_week"
    assert _chart(_spec(dimension="report_day", period="last_365_days"))["dimension"] == "report_month"
    assert _chart(None) == {"kind": "unsupported"}
    outcome = _chart(_spec(metric="analyses", dimension="analysis_outcome"), "pie")
    assert outcome["reports"] is None and outcome["rows"][0] == {"category": "Valid", "values": {"count": 1}}


def test_the_seeded_snapshot_is_deterministic_valid_and_covers_the_rules():
    first, again, other = snap.seeded_snapshot(3), snap.seeded_snapshot(3), snap.seeded_snapshot(4)
    assert h.snapshot_sha256(first) == h.snapshot_sha256(again) != h.snapshot_sha256(other)
    assert h.validate_snapshot(first) == []
    tables = first["tables"]
    revisions = {r["id"]: r for r in tables["report_revisions"]}
    assert all(r["created_at"] for r in revisions.values()) and all(m["run_datetime"] for m in tables["model_runs"])
    newest = {}
    for r in revisions.values():
        newest[r["video_id"]] = max(newest.get(r["video_id"], 0), r["revision_no"])
    assert any(revisions[v["official_revision_id"]]["revision_no"] < newest[v["id"]]
               for v in tables["videos"] if v.get("official_revision_id")), "an official revision that is not newest"
    assert any(v["id"] not in newest for v in tables["videos"]), "a video without a report"
    assert any(fig.run_outcome(m["notes"]) in fig.FAILED for m in tables["model_runs"])
    assert any(r["incident_type"] is None for r in revisions.values())
    assert all(revisions[r["parent_id"]]["video_id"] == r["video_id"] for r in revisions.values() if r.get("parent_id"))


def test_the_corpus_snapshot_spreads_one_official_ai_revision_per_report():
    reports = {"Assault001": _payload("assault", 4), "Burglary002": _payload("burglary", 2)}
    corpus = snap.corpus_snapshot(reports, taken_at="2026-11-01T12:00:00+00:00", model_name="model-a")
    assert h.validate_snapshot(corpus) == []
    rows = corpus["tables"]["report_revisions"]
    assert [r["video_id"] for r in rows] == ["Assault001", "Burglary002"]
    assert [r["created_at"] for r in rows] == ["2026-08-26T00:00:00", "2026-10-10T00:00:00"]
    assert all(v["official_revision_id"] for v in corpus["tables"]["videos"])
    assert fig.dashboard(corpus, {"p_timezone": "UTC", "p_scope": "official"}, now=NOW)["total"] == 2
    with pytest.raises(ValueError, match="20"):
        snap.corpus_snapshot({"x" * 21: _payload("assault", 1)}, taken_at="2026-11-01T12:00:00+00:00", model_name="m")


@pytest.mark.parametrize(("params", "message"), [
    ({**WINDOW}, "no period bounds"), ({"p_scope": "all"}, "p_timezone"),
    ({"p_timezone": "UTC", "p_scope": "all", "p_colour": "red"}, "unknown"),
])
def test_open_period_calls_name_everything_but_the_bounds(params, message):
    with pytest.raises(h.SqlHarnessError, match=message):
        h.open_period_call(params)
    sql, values = h.open_period_call({"p_timezone": "UTC", "p_scope": "all"})
    assert values["p_period_start"] is None and "p_period_end => %(p_period_end)s" in sql


def _record(expected):
    record = rec.new_record(record_id="dash:1", dataset="dashboard-v0", kind="dashboard", clip=None,
                            payload={"request": "r", "expected": expected, "paraphrases": []},
                            generator={"prompt": "gen-dashboard-request-v1"})
    return record


def test_figures_score_what_the_user_would_see():
    def figures_of(spec):
        return _chart(spec, "bar")

    expected = _spec(dimension="severity", period="all_time")
    record = _record(expected)
    same = sc.score_reply(record, expected, ALLOWLIST, figures_of=figures_of)
    looser = sc.score_reply(record, {**expected, "period": "last_365_days"}, ALLOWLIST, figures_of=figures_of)
    narrower = sc.score_reply(record, {**expected, "period": "last_7_days"}, ALLOWLIST, figures_of=figures_of)
    refused = sc.score_reply(record, {"unsupported": True}, ALLOWLIST, figures_of=figures_of)
    assert (same["outcome"], same["figures"]) == ("exact", "figures_match")
    assert (looser["outcome"], looser["figures"]) == ("valid_but_wrong", "figures_match"), "every report is recent"
    assert narrower["figures"] == "figures_differ" and refused["figures"] == "false_refusal"
    unsupported = _record(dash.UNSUPPORTED)
    assert sc.score_reply(unsupported, {"unsupported": True}, ALLOWLIST, figures_of=figures_of)["figures"] == (
        "refusal_correct")
    assert sc.score_reply(unsupported, expected, ALLOWLIST, figures_of=figures_of)["figures"] == "false_accept"
    summary = sc.aggregate([same, looser, narrower, refused])["ai_proposed"]
    assert summary["figure_accuracy"] == 0.5 and summary["figure_outcomes"]["figures_match"] == 2
    assert "figures" not in sc.score_reply(record, expected, ALLOWLIST)


# --- against the real SQL (pgserver, throwaway Postgres) ----------------------------------------------------------

HAS_PGSERVER = all(importlib.util.find_spec(m) is not None for m in ("pgserver", "psycopg"))
PERIODS = [("2026-10-25T12:00:00.000", "2026-11-01T12:00:00.000"), ("2026-10-02T12:00:00.000",
                                                                      "2026-11-01T12:00:00.000"),
           ("2025-11-01T12:00:00.000", "2026-11-01T12:00:00.000"), ("2026-10-25T00:00:00", "2026-11-08T00:00:00")]
ZONES = ("UTC", "America/New_York", "Asia/Kolkata")


def _grid():
    for (start, end), zone, scope in itertools.product(PERIODS, ZONES, ("all", "official", "not_official")):
        yield {"p_period_start": start, "p_period_end": end, "p_timezone": zone, "p_scope": scope}
    base = {"p_period_start": PERIODS[2][0], "p_period_end": PERIODS[2][1], "p_timezone": "America/New_York",
            "p_scope": "all"}
    for kind, band in itertools.product((None, "assault", "Fighting", "unclassified", "robbery"),
                                        (None, "low", "medium", "high", "urgent")):
        yield {**base, "p_type": kind, "p_severity": band}
    for day, hour in itertools.product((None, 0, 3), (None, 0, 23)):
        yield {**base, "p_day_of_week": day, "p_hour": hour}


@pytest.mark.skipif(not HAS_PGSERVER, reason="pgserver and psycopg run in a throwaway environment")
def test_the_recount_equals_the_production_sql_on_seeded_snapshots():
    checked = 0
    for seed in (1, 2):
        snapshot = snap.seeded_snapshot(seed)
        with h.SqlHarness.start() as harness:
            harness.load(snapshot)
            for params in _grid():
                problems = fig.differences(harness.dashboard(**params), fig.dashboard(snapshot, params, now=NOW))
                assert problems == [], (seed, params, problems[:5])
                checked += 1
            for scope, kind in itertools.product(("all", "official", "not_official"), (None, "assault", "explosion")):
                params = {"p_timezone": "Asia/Kolkata", "p_scope": scope, "p_type": kind}
                recount = fig.dashboard(snapshot, params, now=NOW)
                if fig.clock_dependent(params, recount):
                    with pytest.raises(h.SqlHarnessError, match="CURRENT_DATE"):
                        harness.dashboard_open_period(**params)
                    continue
                assert fig.differences(harness.dashboard_open_period(**params), recount) == [], (seed, params)
                checked += 1
    assert checked > 150


# --- against the console's own chart code (Node with TypeScript stripping) ---------------------------------------

@pytest.mark.skipif(not replay.available(), reason="needs a Node that strips TypeScript (EVALV2_NODE) and the console")
def test_the_console_draws_the_same_figures_for_every_spec_and_chart_type():
    snapshot = snap.seeded_snapshot(7)
    cases = [{"id": f"{i}-{kind}", "spec": spec, "chartType": kind}
             for i, spec in enumerate(dash.all_specs(ALLOWLIST)) for kind in ("bar", "pie")]
    cases.append({"id": "refused", "spec": None, "chartType": "bar"})
    payload = replay.replay_input(cases, timezone="America/New_York", now=NOW, allowlist=ALLOWLIST,
                                  fetch=lambda p: fig.dashboard(snapshot, p, now=NOW))
    replayed = replay.run_replay(payload)
    assert replayed["missing"] == [] and len(replayed["results"]) == len(cases)
    assert replay.check_against(payload, replayed, allowlist=ALLOWLIST) == {}
