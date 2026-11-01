# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Hermetic tests of the finals schema on a throwaway Postgres (no Supabase, no network).

    cd supabase/tests && uv run --no-project --python 3.12 --with pgserver --with 'psycopg[binary]' python test_baseline.py

Applies every file in supabase/migrations/ in order (the finals baseline, then the report-revision
schema, then its functions). pgserver ships Postgres 16 with pgvector. supabase_stub.sql adds the
API roles and Supabase's default grants, so the lockdown is tested against what a new project
really starts with. Writes go through SET ROLE service_role, the role PostgREST uses with the
service key.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

import pgserver
import psycopg
from psycopg import errors

HERE = Path(__file__).resolve().parent
MIGRATIONS = sorted((HERE.parent / "migrations").glob("*.sql"))
REVISION_MIGRATIONS = [path for path in MIGRATIONS if path.name >= "20261023"]
PASSED: list[str] = []


def test(name):
    def wrap(fn):
        fn()
        PASSED.append(name)
        print("ok  ", name)
    return wrap


def expect_error(conn: psycopg.Connection, sql: str, params=None, kind=errors.Error, contains: str = "") -> str:
    with conn.transaction(force_rollback=True):
        try:
            conn.execute(sql, params)
        except kind as exc:
            assert contains in str(exc), (contains, str(exc))
            raise _Expected(str(exc))
    raise AssertionError(f"no error from: {sql}")


class _Expected(Exception):
    pass


def refused(conn, sql, params=None, kind=errors.Error, contains=""):
    try:
        expect_error(conn, sql, params, kind, contains)
    except _Expected as exc:
        return str(exc)


def one(conn, sql, params=None):
    return conn.execute(sql, params).fetchone()


def vector(index: int = 0) -> list[float]:
    values = [0.0] * 1024
    values[index] = 1.0
    return values


def vector_literal(index: int = 0) -> str:
    return json.dumps(vector(index))


def report(kind="assault", severity=4, start=2, end=9, title="Fight at the bus stop", entity="Man in a red jacket"):
    return {
        "incident": {"type": kind, "title": title, "start_timestamp": start, "end_timestamp": end, "duration": end - start,
                     "description": "Two people fight.", "severity_level": severity,
                     "confidence_score": 0.8, "location": None},
        "entities": [{"entity_id": "E1", "type": "human", "description": entity, "timestamp_seconds": 3, "label": "man"}],
        "instruments": [{"instrument_id": "I1", "entity_id": "E1", "name": "bottle", "description": "Glass bottle",
                         "threat_level": 3, "timestamp_seconds": 4, "label": "bottle"}],
        "assets": [{"asset_id": "A1", "name": "bench", "description": "Bus stop bench", "timestamp_seconds": 5, "label": "bench"}],
        "timeline": [],
        "uncertainties": [],
    }


EVIDENCE = [
    {"item_type": "entity", "item_id": "E1", "image": "evidence/v1/u1/E1.jpg", "bounding_box": [1, 2, 300, 400], "timestamp_seconds": 3},
    {"item_type": "instrument", "item_id": "I1", "image": "evidence/v1/u2/I1.jpg", "bounding_box": None, "timestamp_seconds": None, "source": "upload"},
    {"item_type": "asset", "item_id": "A1", "image": None, "bounding_box": None, "timestamp_seconds": 5},
]


def crop(item_type: str, item_id: str, image: str, embedding: list[float] | None, **fields) -> dict:
    return {"item_type": item_type, "item_id": item_id, "image": image, "bounding_box": fields.get("bounding_box"),
            "timestamp_seconds": fields.get("timestamp_seconds"), "item_name": fields.get("item_name"),
            "description": fields.get("description"), "threat_level": fields.get("threat_level"),
            "holder_entity_id": fields.get("holder_entity_id"), "embedding": embedding}


with tempfile.TemporaryDirectory() as tmp:
    server = pgserver.get_server(tmp, cleanup_mode="stop")
    conn = psycopg.connect(server.get_uri(), autocommit=True)
    conn.execute((HERE / "supabase_stub.sql").read_text())
    with conn.transaction():
        for migration in MIGRATIONS:
            conn.execute(migration.read_text())

    tables = [r[0] for r in conn.execute(
        "SELECT c.oid::regclass::TEXT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v') ORDER BY 1").fetchall()]
    functions = [r[0] for r in conn.execute(
        "SELECT p.oid::regprocedure::TEXT FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e') ORDER BY 1").fetchall()]

    @test("schema: 7 tables, no views, no legacy report tables or functions")
    def _():
        kinds = dict(conn.execute("SELECT relkind::TEXT, count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                  "WHERE n.nspname = 'public' AND relkind IN ('r','v') GROUP BY 1").fetchall())
        assert kinds == {"r": 7}, kinds
        assert sorted(tables) == ["analysis_jobs", "evidence_crops", "model_runs", "queries", "query_results",
                                  "report_revisions", "videos"], tables
        names = " ".join(tables + functions)
        for gone in ("incidents", "reports", "entities", "instruments", "assets", "human_reports", "review_status",
                     "gt_incidents", "severity_eval_log", "insert_incident", "save_human_report_edit",
                     "select_official_report", "set_review_status", "effective_incidents"):
            assert gone not in names, gone
        for present in ("save_human_revision", "set_official_revision", "search_evidence_items",
                        "list_incident_report_summaries", "get_incident_dashboard", "report_revisions_append_only",
                        "set_revision_crops", "searchable_revision_id", "claim_analysis_job", "claim_queued_analysis_job",
                        "touch_analysis_job",
                        "finish_analysis_job", "retry_analysis_job", "cancel_analysis_job"):
            assert present in names, present
        assert one(conn, "SELECT extnamespace::regnamespace::TEXT FROM pg_extension WHERE extname = 'vector'")[0] == "extensions"
        columns = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'videos'").fetchall()]
        assert "official_revision_id" in columns and "selected_model_run_id" not in columns, columns
        assert "official_since" in columns, columns

    @test("migrations: the revision migrations can be applied again over the live shape")
    def _():
        with conn.transaction():
            for migration in REVISION_MIGRATIONS:
                conn.execute(migration.read_text())
        assert one(conn, "SELECT count(*) FROM public.report_revisions")[0] == 0
        assert one(conn, "SELECT count(*) FROM public.analysis_jobs")[0] == 0

    @test("security: anon and authenticated cannot read any table or run any function")
    def _():
        for role in ("anon", "authenticated"):
            conn.execute(f"SET ROLE {role}")
            for table in tables:
                refused(conn, f"SELECT 1 FROM {table} LIMIT 1", kind=errors.InsufficientPrivilege)
            for fn in functions:
                assert not one(conn, "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, fn))[0], (role, fn)
            refused(conn, "SELECT public.save_human_revision('v', 1, 'x', NULL, '{}', '[]', 'assault', 3, 0, 1)",
                    kind=errors.InsufficientPrivilege)
            refused(conn, "SELECT public.set_official_revision('v', NULL)", kind=errors.InsufficientPrivilege)
            refused(conn, "SELECT public.list_incident_report_summaries()", kind=errors.InsufficientPrivilege)
            refused(conn, "SELECT public.get_incident_dashboard()", kind=errors.InsufficientPrivilege)
            conn.execute("RESET ROLE")

    @test("security: the dashboard has one signature, for the service role only")
    def _():
        rows = conn.execute("SELECT oid::regprocedure::text, has_function_privilege('anon', oid, 'EXECUTE'), has_function_privilege('authenticated', oid, 'EXECUTE'), has_function_privilege('service_role', oid, 'EXECUTE') FROM pg_proc WHERE proname = 'get_incident_dashboard'").fetchall()
        assert len(rows) == 1 and rows[0][1:] == (False, False, True), rows

    @test("security: objects created later in public are not exposed either (default privileges)")
    def _():
        conn.execute("CREATE TABLE public.zz_later (id INT)")
        conn.execute("CREATE FUNCTION public.zz_later_fn() RETURNS INT LANGUAGE sql AS 'SELECT 1'")
        try:
            for role in ("anon", "authenticated"):
                assert not one(conn, "SELECT has_table_privilege(%s, 'public.zz_later', 'SELECT')", (role,))[0]
                assert not one(conn, "SELECT has_function_privilege(%s, 'public.zz_later_fn()', 'EXECUTE')", (role,))[0]
            assert one(conn, "SELECT has_table_privilege('service_role', 'public.zz_later', 'SELECT')")[0]
            assert one(conn, "SELECT has_function_privilege('service_role', 'public.zz_later_fn()', 'EXECUTE')")[0]
        finally:
            conn.execute("DROP TABLE public.zz_later; DROP FUNCTION public.zz_later_fn()")

    @test("security: tests/security_check.sql reports nothing")
    def _():
        rows = conn.execute((HERE / "security_check.sql").read_text()).fetchall()
        assert rows == [], rows

    conn.execute("SET ROLE service_role")
    for video in ("v1", "v2", "v3", "v4"):
        conn.execute("INSERT INTO public.videos (id, filepath, source) VALUES (%s, %s, 'cam')", (video, f"uploads/{video}/clip.mp4"))
    conn.execute("INSERT INTO public.model_runs (id, model_name, notes) VALUES ('m1', 'model-a', %s), ('m2', 'model-b', NULL)",
                 (json.dumps({"incidentConsoleV2": {"contractVersion": "incident-contract-v3", "status": "valid_first_pass"}}),))

    def ai_revision(video: str, revision_no: int, run: str, payload: dict, evidence: list) -> int:
        inc = payload["incident"]
        return one(conn, """
            INSERT INTO public.report_revisions (video_id, revision_no, kind, model_run_id, payload, evidence,
                incident_type, severity_level, start_seconds, end_seconds)
            VALUES (%s, %s, 'ai', %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (video, revision_no, run, json.dumps(payload), json.dumps(evidence), inc["type"], inc["severity_level"],
             inc["start_timestamp"], inc["end_timestamp"]))[0]

    ai1 = ai_revision("v1", 1, "m1", report(), EVIDENCE)
    ai2 = ai_revision("v2", 1, "m2", report(kind="burglary", severity=2, title="Shop break-in"), [])

    def save(video, parent, payload, evidence, by="Dana", note=None, incident_type=None, severity=None,
             start=None, end=None):
        incident = payload["incident"]
        return one(conn, """
            SELECT public.save_human_revision(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (video, parent, by, note, json.dumps(payload), json.dumps(evidence),
             incident_type if incident_type is not None else incident["type"],
             severity if severity is not None else incident["severity_level"],
             start if start is not None else incident["start_timestamp"],
             end if end is not None else incident["end_timestamp"]))[0]

    human = save("v1", ai1, report(kind="explosion", severity=5, start=1, end=8, entity="Woman in a coat"),
                 EVIDENCE, note=" fixed type ", incident_type="explosion", severity=5, start=1, end=8)

    @test("save_human_revision appends the next revision with the caller's facets and parent run")
    def _():
        assert human["revisionNo"] == 2 and human["kind"] == "human" and human["parentId"] == ai1, human
        assert human["modelRunId"] == "m1" and human["createdBy"] == "Dana" and human["note"] == "fixed type", human
        row = one(conn, "SELECT incident_type, severity_level, start_seconds, end_seconds, created_by, kind FROM public.report_revisions WHERE id = %s",
                  (human["revisionId"],))
        assert row == ("explosion", 5, 1, 8, "Dana", "human"), row

    @test("save_human_revision hands out one revision_no at a time per video")
    def _():
        first = save("v3", ai_revision("v3", 1, "m1", report(), []), report(), [])
        second = save("v3", first["revisionId"], report(), [])
        assert (first["revisionNo"], second["revisionNo"]) == (2, 3), (first, second)
        assert second["parentId"] == first["revisionId"], second
        assert [r[0] for r in conn.execute("SELECT revision_no FROM public.report_revisions WHERE video_id = 'v3' ORDER BY 1").fetchall()] == [1, 2, 3]

    @test("save_human_revision refuses a bad parent, name, payload or evidence")
    def _():
        args = "SELECT public.save_human_revision(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        refused(conn, args, ("v1", ai2, "Dana", None, json.dumps(report()), "[]", "assault", 4, 2, 9),
                contains="Revision not found for this video")
        refused(conn, args, ("nope", ai1, "Dana", None, json.dumps(report()), "[]", "assault", 4, 2, 9),
                contains="Video not found")
        refused(conn, args, ("v1", None, "Dana", None, json.dumps(report()), "[]", "assault", 4, 2, 9),
                contains="parent revision")
        refused(conn, args, ("v1", ai1, "  ", None, json.dumps(report()), "[]", "assault", 4, 2, 9),
                contains="reviewer name")
        refused(conn, args, ("v1", ai1, "Dana", None, json.dumps({"entities": []}), "[]", "assault", 4, 2, 9),
                contains="incident")
        refused(conn, args, ("v1", ai1, "Dana", None, json.dumps(report()), "{}", "assault", 4, 2, 9),
                contains="evidence must be a list")

    @test("revisions are append-only")
    def _():
        refused(conn, "UPDATE public.report_revisions SET note = 'x' WHERE id = %s", (ai1,), contains="append-only")
        refused(conn, "DELETE FROM public.report_revisions WHERE id = %s", (ai1,), contains="append-only")

    crops = [
        crop("entity", "E1", "evidence/v1/u1/E1.jpg", vector(0), bounding_box=[1, 2, 300, 400],
             timestamp_seconds=3, item_name="human", description="Man in a red jacket"),
        crop("instrument", "I1", "evidence/v1/u2/I1.jpg", vector(1), item_name="bottle",
             description="Glass bottle", threat_level=3, holder_entity_id="E1"),
        crop("asset", "A1", None, vector(2), item_name="bench", description="Bus stop bench"),
    ]

    @test("set_official_revision stores the caller's crops and the pointer")
    def _():
        result = one(conn, "SELECT public.set_official_revision(%s, %s, %s)", ("v1", ai1, json.dumps(crops)))[0]
        assert result == {"videoId": "v1", "officialRevisionId": ai1, "crops": 2}, result
        rows = conn.execute("""SELECT item_type, item_id, item_name, threat_level, holder_entity_id, timestamp_seconds,
            bounding_box, image, embedding IS NOT NULL FROM public.evidence_crops ORDER BY item_type""").fetchall()
        assert rows == [
            ("entity", "E1", "human", None, None, 3, [1, 2, 300, 400], "evidence/v1/u1/E1.jpg", True),
            ("instrument", "I1", "bottle", 3, "E1", 0, [0, 0, 1000, 1000], "evidence/v1/u2/I1.jpg", True),
        ], rows
        assert one(conn, "SELECT official_revision_id FROM public.videos WHERE id = 'v1'")[0] == ai1

    @test("search_evidence_items finds the official revision's embedded crops")
    def _():
        rows = conn.execute("""SELECT incident_id, revision_id, model_run_id, item_id, incident_type,
            incident_start_timestamp FROM public.search_evidence_items(%s::extensions.vector, 5, 0.0)""",
                            (vector_literal(0),)).fetchall()
        assert rows == [("v1", ai1, "m1", "E1", "assault", "2"), ("v1", ai1, "m1", "I1", "assault", "2")], rows

    @test("marking another revision official replaces the crops; NULL leaves the newest revision searchable")
    def _():
        one_crop = [crop("entity", "E1", "evidence/v1/u3/E1.jpg", vector(0), item_name="animal",
                         description="Edited", timestamp_seconds=1)]
        result = one(conn, "SELECT public.set_official_revision(%s, %s, %s)",
                     ("v1", human["revisionId"], json.dumps(one_crop)))[0]
        assert result["crops"] == 1, result
        assert one(conn, "SELECT count(*) FROM public.evidence_crops WHERE revision_id = %s", (ai1,))[0] == 0
        assert one(conn, "SELECT official_revision_id FROM public.videos WHERE id = 'v1'")[0] == human["revisionId"]
        cleared = one(conn, "SELECT public.set_official_revision('v1', NULL)")[0]
        # the edited revision is also v1's newest, so it stays searchable and keeps its row
        assert cleared == {"videoId": "v1", "officialRevisionId": None, "crops": 1}, cleared
        assert one(conn, "SELECT count(*) FROM public.evidence_crops WHERE revision_id = %s", (human["revisionId"],))[0] == 1
        assert one(conn, "SELECT count(*) FROM public.evidence_crops")[0] == 1
        assert one(conn, "SELECT official_revision_id FROM public.videos WHERE id = 'v1'")[0] is None
        refused(conn, "SELECT public.set_official_revision('v1', %s)", (ai2,), contains="Revision not found for this video")
        refused(conn, "SELECT public.set_official_revision('nope', NULL)", None, contains="Video not found")
        refused(conn, "SELECT public.set_official_revision('v1', %s, '{}')", (ai1,), contains="crops must be a list")

    conn.execute("SELECT public.set_official_revision(%s, %s, %s)", ("v1", ai1, json.dumps(crops)))

    def summaries(**kwargs):
        named = ", ".join(f"{key} => %({key})s" for key in kwargs)
        return one(conn, f"SELECT public.list_incident_report_summaries({named})", kwargs)[0]

    @test("the library shows the official revision, else the newest, one card per incident")
    def _():
        result = summaries()
        cards = {item["videoId"]: item for item in result["reports"]}
        assert result["totalItems"] == 3 and set(cards) == {"v1", "v2", "v3"}, result
        assert cards["v1"]["revisionId"] == ai1 and cards["v1"]["isOfficial"] and cards["v1"]["kind"] == "ai", cards["v1"]
        assert cards["v1"]["title"] == "Fight at the bus stop" and cards["v1"]["model"] == "model-a"
        assert cards["v1"]["outcome"] == "valid_first_pass", cards["v1"]
        assert cards["v2"]["revisionId"] == ai2 and not cards["v2"]["isOfficial"] and cards["v2"]["outcome"] == "legacy", cards["v2"]
        assert result["incidentTypes"] == ["assault", "burglary"], result["incidentTypes"]

    @test("the library filters by scope, kind, type, evidence and search")
    def _():
        assert [r["videoId"] for r in summaries(p_scope="official")["reports"]] == ["v1"]
        assert sorted(r["videoId"] for r in summaries(p_scope="not_official")["reports"]) == ["v2", "v3"]
        assert summaries(p_kind="human")["totalItems"] == 1  # only v3's newest card is human
        assert [r["videoId"] for r in summaries(p_type="burglary")["reports"]] == ["v2"]
        assert summaries(p_entities=["red jacket"])["totalItems"] == 3
        assert [r["videoId"] for r in summaries(p_search="break-in")["reports"]] == ["v2"]
        assert summaries(p_instruments=["Bottle"], p_evidence_match="exact")["totalItems"] == 3
        assert [r["videoId"] for r in summaries(p_sort="severity-low", p_scope="official")["reports"]] == ["v1"]
        conn.execute("SELECT public.set_official_revision('v1', NULL)")
        cards = {item["videoId"]: item for item in summaries()["reports"]}
        assert cards["v1"]["revisionId"] == human["revisionId"] and cards["v1"]["kind"] == "human", cards["v1"]
        assert sorted(r["videoId"] for r in summaries(p_kind="human")["reports"]) == ["v1", "v3"]
        conn.execute("SELECT public.set_official_revision(%s, %s, %s)", ("v1", ai1, json.dumps(crops)))

    def dashboard_of(**kwargs):
        kwargs.setdefault("p_scope", "all")
        named = ", ".join(f"{key} => %({key})s" for key in kwargs)
        return one(conn, f"SELECT public.get_incident_dashboard({named})", kwargs)[0]

    @test("the dashboard counts each video once: official, else newest, and reports the split")
    def _():
        dashboard = dashboard_of()
        assert dashboard["total"] == 3 and (dashboard["official"], dashboard["notOfficial"]) == (1, 2), dashboard
        assert dashboard["revisions"] == {"ai": 2, "human": 1}, dashboard
        assert dashboard["videos"] == {"uploaded": 4, "withOfficial": 1, "awaitingSelection": 2, "withoutReport": 1}, dashboard["videos"]
        assert {t["type"]: (t["count"], t["official"], t["notOfficial"]) for t in dashboard["types"]} == {"assault": (2, 1, 1), "burglary": (1, 0, 1)}, dashboard["types"]
        assert dashboard["topInstruments"] == [{"label": "bottle", "count": 3}], dashboard["topInstruments"]
        assert dashboard["threatLevels"] == [{"level": 3, "count": 3}] and abs(dashboard["averageConfidence"] - 0.8) < 1e-9, dashboard
        by_scope = dashboard["severityByScope"]
        for band in ("low", "medium", "high"):
            assert by_scope["official"][band] + by_scope["notOfficial"][band] == dashboard["severity"][band], dashboard
        assert sum(day["official"] for day in dashboard["trend"]) == 1 and sum(day["notOfficial"] for day in dashboard["trend"]) == 2, dashboard["trend"]
        assert sum(cell["official"] for cell in dashboard["heatmap"]) == 1 and sum(cell["notOfficial"] for cell in dashboard["heatmap"]) == 2, dashboard["heatmap"]
        assert all(asset["official"] + asset["notOfficial"] == asset["count"] for asset in dashboard["topAssets"]), dashboard["topAssets"]
        coverage = {row["model"]: row for row in dashboard["modelCoverage"]}
        assert coverage["model-a"]["withReport"] == 2 and coverage["model-b"]["legacy"] == 1, coverage

    @test("dashboard ties between equal counts are broken in byte order (COLLATE \"C\"), whatever the database collation")
    def _():
        definition = one(conn, "SELECT pg_get_functiondef('public.get_incident_dashboard'::regproc)")[0]
        tie_breaks = re.findall(r"ORDER BY [^)\n]*?\b(?:label|incident_type|model_name)\b[^)\n]*", definition)
        assert len(tie_breaks) >= 11, tie_breaks
        assert all('COLLATE "C"' in clause for clause in tie_breaks), [c for c in tie_breaks if 'COLLATE "C"' not in c]

    @test("every dashboard number equals the total of the library list it opens")
    def _():
        dashboard = dashboard_of()
        assert summaries()["totalItems"] == dashboard["total"]
        assert summaries(p_scope="official")["totalItems"] == dashboard["official"]
        assert summaries(p_scope="not_official")["totalItems"] == dashboard["notOfficial"]
        for item in dashboard["types"]:
            assert summaries(p_type=item["type"])["totalItems"] == item["count"], item
            assert summaries(p_type=item["type"], p_scope="official")["totalItems"] == item["official"], item
        for band, count in dashboard["severity"].items():
            assert summaries(p_severity=band)["totalItems"] == count, band
        for asset in dashboard["topAssets"]:
            assert summaries(p_assets=[asset["label"]], p_evidence_match="exact")["totalItems"] == asset["count"], asset
        for kind, count in dashboard["revisions"].items():
            assert summaries(p_kind=kind)["totalItems"] == count, kind

    @test("the dashboard filters by report status, and leaves the video and model counts alone")
    def _():
        everything = dashboard_of()
        for scope, expected in (("all", 3), ("official", 1), ("not_official", 2)):
            dashboard = dashboard_of(p_scope=scope)
            assert dashboard["total"] == expected == summaries(p_scope=scope)["totalItems"], (scope, dashboard["total"])
            assert dashboard["videos"] == everything["videos"] and dashboard["modelCoverage"] == everything["modelCoverage"], scope
        official = dashboard_of(p_scope="official")
        assert (official["official"], official["notOfficial"]) == (1, 0), official
        assert [t["type"] for t in official["types"]] == ["assault"], official["types"]
        assert (dashboard_of(p_scope="not_official")["official"]) == 0
        assert dashboard_of(p_scope=None)["total"] == 1
        bounds = {"p_period_start": "2026-01-01 00:00:00", "p_period_end": "2099-01-01 00:00:00"}
        assert dashboard_of(**bounds, p_scope="official")["previous"]["total"] == 0, "the comparison is scoped too"

    @test("a caller that omits the report status, like a console from before it existed, sees official reports only")
    def _():
        omitted = one(conn, "SELECT public.get_incident_dashboard()")[0]
        assert omitted["total"] == 1 and omitted["revisions"] == {"ai": 1, "human": 0}, omitted
        assert [t["type"] for t in omitted["types"]] == ["assault"], omitted["types"]
        assert omitted["videos"] == dashboard_of()["videos"]

    @test("the dashboard with no official revision still counts every report and marks nothing official")
    def _():
        with conn.transaction(force_rollback=True):
            conn.execute("SELECT public.set_official_revision('v1', NULL)")
            before = conn.execute("SELECT id, official_revision_id, official_since FROM public.videos ORDER BY id").fetchall()
            dashboard = dashboard_of()
            assert dashboard["total"] == 3 and (dashboard["official"], dashboard["notOfficial"]) == (0, 3), dashboard
            assert dashboard["videos"]["withOfficial"] == 0 and dashboard["videos"]["awaitingSelection"] == 3, dashboard["videos"]
            assert conn.execute("SELECT id, official_revision_id, official_since FROM public.videos ORDER BY id").fetchall() == before
        volatility = one(conn, "SELECT provolatile FROM pg_proc WHERE proname = 'get_incident_dashboard'")[0]
        assert volatility == "s", "STABLE: the dashboard cannot write"

    @test("the dashboard's trend days are the viewer's days, like the heatmap")
    def _():
        with conn.transaction(force_rollback=True):
            # 20:00 UTC on the 30th is 04:00 on the 31st in Singapore.
            conn.execute("""INSERT INTO public.videos (id, filepath, uploaded_datetime) VALUES ('v-late', 'uploads/late.mp4', '2026-10-30 19:00:00')""")
            conn.execute("""INSERT INTO public.report_revisions (video_id, revision_no, kind, model_run_id, payload, evidence,
                incident_type, severity_level, start_seconds, end_seconds, created_at)
                VALUES ('v-late', 1, 'ai', 'm1', %s, '[]', 'assault', 3, 1, 2, '2026-10-30 20:00:00')""", (json.dumps(report()),))
            bounds = {"p_period_start": "2026-10-30 12:00:00", "p_period_end": "2026-10-30 23:00:00"}
            utc = dashboard_of(**bounds, p_timezone="UTC")
            singapore = dashboard_of(**bounds, p_timezone="Asia/Singapore")
            assert [(d["date"], d["count"]) for d in utc["trend"]] == [("2026-10-30", 1)], utc["trend"]
            assert [(d["date"], d["count"]) for d in singapore["trend"]] == [("2026-10-30", 0), ("2026-10-31", 1)], singapore["trend"]
            assert singapore["heatmap"] == [{"day": 6, "hour": 4, "count": 1, "official": 0, "notOfficial": 1}], singapore["heatmap"]

    @test("an Unclassified row opens its reports in the library")
    def _():
        with conn.transaction(force_rollback=True):
            conn.execute("""INSERT INTO public.videos (id, filepath, uploaded_datetime) VALUES ('v-none', 'uploads/none.mp4', now())""")
            conn.execute("""INSERT INTO public.report_revisions (video_id, revision_no, kind, model_run_id, payload, evidence,
                incident_type, severity_level, start_seconds, end_seconds)
                VALUES ('v-none', 1, 'ai', 'm1', %s, '[]', NULL, 3, 1, 2)""", (json.dumps(report()),))
            dashboard = dashboard_of()
            row = next(item for item in dashboard["types"] if item["type"] == "Unclassified")
            assert row["count"] == 1 and row["notOfficial"] == 1, row
            result = summaries(p_type="Unclassified")
            assert result["totalItems"] == 1 and result["reports"][0]["videoId"] == "v-none", result
            assert dashboard_of(p_type="Unclassified")["total"] == 1
            assert summaries(p_type="assault")["totalItems"] == dashboard_of(p_type="assault")["total"] == 2

    def cards():
        return {item["videoId"]: item for item in summaries()["reports"]}

    @test("library cards carry officialSince, versionCount, evidenceCount and thumbnail")
    def _():
        found = cards()
        assert found["v1"]["isOfficial"] and found["v1"]["officialSince"] is not None, found["v1"]
        assert found["v1"]["versionCount"] == 2 and found["v1"]["evidenceCount"] == 2, found["v1"]
        assert found["v1"]["thumbnail"] == "evidence/v1/u1/E1.jpg", found["v1"]
        assert found["v2"]["officialSince"] is None and found["v2"]["versionCount"] == 1, found["v2"]
        assert found["v2"]["evidenceCount"] == 0 and found["v2"]["thumbnail"] is None, found["v2"]
        assert found["v3"]["versionCount"] == 3 and found["v3"]["officialSince"] is None, found["v3"]

    @test("official_since is set when the pointer moves, kept on a repeat mark and cleared with it")
    def _():
        since = lambda: one(conn, "SELECT official_since FROM public.videos WHERE id = 'v1'")[0]
        first = since()
        conn.execute("SELECT public.set_official_revision(%s, %s, %s)", ("v1", ai1, json.dumps(crops)))
        assert since() == first, (since(), first)
        conn.execute("SELECT public.set_official_revision('v1', NULL)")
        assert since() is None
        conn.execute("SELECT public.set_official_revision(%s, %s, %s)", ("v1", ai1, json.dumps(crops)))
        assert since() is not None and since() >= first

    searchable = lambda video: one(conn, "SELECT public.searchable_revision_id(%s)", (video,))[0]
    searched = lambda index: conn.execute(
        "SELECT incident_id, revision_id, item_id FROM public.search_evidence_items(%s::extensions.vector, 5, 0.5)",
        (vector_literal(index),)).fetchall()
    crop_count = lambda video: one(
        conn, "SELECT count(*) FROM public.evidence_crops WHERE revision_id IN (SELECT id FROM public.report_revisions WHERE video_id = %s)",
        (video,))[0]
    set_crops = lambda video, revision, rows: one(conn, "SELECT public.set_revision_crops(%s, %s, %s)", (video, revision, json.dumps(rows)))[0]

    @test("the searchable revision is the official one, else the newest, else none")
    def _():
        assert searchable("v1") == ai1 and searchable("v2") == ai2 and searchable("v4") is None
        assert searchable("v3") == one(conn, "SELECT max(id) FROM public.report_revisions WHERE video_id = 'v3'")[0]

    @test("a video with no official revision becomes searchable through its newest revision")
    def _():
        assert searched(5) == []
        result = set_crops("v2", ai2, [crop("entity", "E1", "evidence/v2/u1/E1.jpg", vector(5), item_name="human", description="Burglar")])
        assert result == {"videoId": "v2", "revisionId": ai2, "crops": 1}, result
        assert searched(5) == [("v2", ai2, "E1")], searched(5)
        assert one(conn, "SELECT official_revision_id FROM public.videos WHERE id = 'v2'")[0] is None

    @test("search_evidence_items scopes to one video, and lists it in video time order with no query")
    def _():
        scoped = lambda video, index, min_similarity: conn.execute(
            """SELECT incident_id, item_id, similarity_score FROM public.search_evidence_items(
                %s::extensions.vector, 20, %s, p_video_id => %s)""",
            (vector_literal(index) if index is not None else None, min_similarity, video)).fetchall()
        # the query matches v2's crop best, but a scoped search still returns only the scoped video's rows
        assert {row[0] for row in scoped("v1", 5, -1.0)} == {"v1"}
        assert {row[0] for row in scoped("v2", 5, -1.0)} == {"v2"}
        assert scoped("nope", 5, -1.0) == []
        # no embedding: every crop of the video, in video time, similarity 0, whatever p_min_similarity says
        listed = scoped("v1", None, 0.9)
        assert [row[1] for row in listed] == ["I1", "E1"] and {row[2] for row in listed} == {0.0}, listed
        assert len(conn.execute(
            "SELECT 1 FROM public.search_evidence_items(%s::extensions.vector, 20, -1.0)", (vector_literal(5),)).fetchall()) >= 3

    @test("set_revision_crops refuses a revision that is not the searchable one, and bad input")
    def _():
        first_v3 = one(conn, "SELECT min(id) FROM public.report_revisions WHERE video_id = 'v3'")[0]
        refused(conn, "SELECT public.set_revision_crops('v3', %s, '[]')", (first_v3,), contains="not the searchable revision")
        refused(conn, "SELECT public.set_revision_crops('v1', %s, '[]')", (human["revisionId"],), contains="not the searchable revision")
        refused(conn, "SELECT public.set_revision_crops('v4', NULL, '[]')", contains="not the searchable revision")
        refused(conn, "SELECT public.set_revision_crops('nope', 1, '[]')", contains="Video not found")
        refused(conn, "SELECT public.set_revision_crops('v2', %s, '{}')", (ai2,), contains="crops must be a list")

    @test("saving a newer revision with no official one hides the old rows until the new ones are stored")
    def _():
        newer = save("v2", ai2, report(kind="burglary", severity=3, title="Shop break-in, edited"), [])
        assert searchable("v2") == newer["revisionId"]
        assert searched(5) == [] and crop_count("v2") == 1  # old row stays but is never returned
        refused(conn, "SELECT public.set_revision_crops('v2', %s, '[]')", (ai2,), contains="not the searchable revision")
        set_crops("v2", newer["revisionId"], [crop("entity", "E1", "evidence/v2/u2/E1.jpg", vector(5), item_name="human", description="Burglar")])
        assert searched(5) == [("v2", newer["revisionId"], "E1")] and crop_count("v2") == 1
        assert one(conn, "SELECT count(*) FROM public.evidence_crops WHERE revision_id = %s", (ai2,))[0] == 0

    save_with_crops = lambda video, parent, rows: one(conn, """
        SELECT public.save_human_revision_with_crops(%s, %s, 'Dana', NULL, %s, '[]', 'assault', 4, 2, 9, %s)""",
        (video, parent, json.dumps(report()), json.dumps(rows)))[0]

    @test("save_human_revision_with_crops stores the revision and its crops together when the video has no official revision")
    def _():
        parent = ai_revision("v4", 1, "m1", report(), [])
        rows = [crop("entity", "E1", "evidence/v4/u1/E1.jpg", vector(3), item_name="human", description="Man in a red jacket"),
                crop("asset", "A1", None, vector(4), item_name="bench", description="Bus stop bench")]
        stored = save_with_crops("v4", parent, rows)
        assert stored["revisionNo"] == 2 and stored["parentId"] == parent and stored["crops"] == 1, stored
        assert searchable("v4") == stored["revisionId"] and searched(3) == [("v4", stored["revisionId"], "E1")], searched(3)
        refused(conn, "SELECT public.save_human_revision_with_crops('v4', %s, 'Dana', NULL, %s, '[]', 'assault', 4, 2, 9, '{}')",
                (parent, json.dumps(report())), contains="crops must be a list")

    @test("save_human_revision_with_crops writes no revision when one of its crops cannot be stored")
    def _():
        parent = one(conn, "SELECT max(id) FROM public.report_revisions WHERE video_id = 'v4'")[0]
        before = one(conn, "SELECT count(*) FROM public.report_revisions WHERE video_id = 'v4'")[0]
        searchable_before = searchable("v4")
        bad = [{"item_type": "entity", "item_id": "E9", "image": "evidence/v4/u9/E9.jpg", "embedding": [1.0, 2.0]}]
        refused(conn, """SELECT public.save_human_revision_with_crops('v4', %s, 'Dana', NULL, %s, '[]', 'assault', 4, 2, 9, %s)""",
                (parent, json.dumps(report()), json.dumps(bad)), contains="dimensions")
        assert one(conn, "SELECT count(*) FROM public.report_revisions WHERE video_id = 'v4'")[0] == before
        assert searchable("v4") == searchable_before, "the searchable revision is unchanged"

    @test("save_human_revision_with_crops leaves an official revision's rows alone")
    def _():
        parent = one(conn, "SELECT max(id) FROM public.report_revisions WHERE video_id = 'v4'")[0]
        assert one(conn, "SELECT public.set_official_revision('v4', %s)", (parent,))[0]["crops"] == 1
        before = crop_count("v4")
        saved = save_with_crops("v4", parent, [crop("entity", "E2", "evidence/v4/u2/E2.jpg", vector(5), item_name="other", description="Ignored")])
        assert saved["crops"] == 0 and crop_count("v4") == before and searchable("v4") == parent, saved
        assert one(conn, "SELECT public.set_official_revision('v4', NULL)")[0]["officialRevisionId"] is None

    @test("marking the newest revision official keeps its rows; clearing it falls back to the newest")
    def _():
        newest = searchable("v2")
        result = one(conn, "SELECT public.set_official_revision('v2', %s)", (newest,))[0]
        assert result["crops"] == 1 and searched(5) == [("v2", newest, "E1")], result
        assert one(conn, "SELECT official_since FROM public.videos WHERE id = 'v2'")[0] is not None
        later = save("v2", newest, report(kind="burglary", severity=3), [])  # official stays; nothing moves
        assert searchable("v2") == newest and searched(5) == [("v2", newest, "E1")]
        refused(conn, "SELECT public.set_revision_crops('v2', %s, '[]')", (later["revisionId"],), contains="not the searchable revision")
        cleared = one(conn, "SELECT public.set_official_revision('v2', NULL)")[0]
        assert cleared == {"videoId": "v2", "officialRevisionId": None, "crops": 0}, cleared  # newest has no rows yet
        assert searchable("v2") == later["revisionId"] and crop_count("v2") == 0 and searched(5) == []
        assert one(conn, "SELECT official_since FROM public.videos WHERE id = 'v2'")[0] is None
        set_crops("v2", later["revisionId"], [crop("asset", "A1", "evidence/v2/u3/A1.jpg", vector(5), item_name="bench", description="Bench")])
        kept = one(conn, "SELECT public.set_official_revision('v2', NULL)")[0]
        assert kept["crops"] == 1 and searched(5) == [("v2", later["revisionId"], "A1")], kept
        assert set_crops("v2", later["revisionId"], [])["crops"] == 0 and crop_count("v2") == 0

    @test("the revisions of other videos are never touched by a crop write")
    def _():
        assert crop_count("v1") == 2 and crop_count("v3") == 0 and crop_count("v4") == 0
    @test("apply_r2_rename points a video, its evidence and its crops at renamed keys and nothing else")
    def _():
        conn.execute("INSERT INTO public.videos (id, filepath, source) VALUES ('v10', 'uploads/cam/0123.mp4', 'cam')")
        evidence = [
            {"item_type": "entity", "item_id": "E1", "image": "evidence/old/E1.jpg"},
            {"item_type": "asset", "item_id": "A1", "image": None},
            {"item_type": "instrument", "item_id": "I1", "image": "evidence/old/I1.jpg"},
        ]
        rev = ai_revision("v10", 1, "m1", report(), evidence)
        for item_type, item_id, image in (("entity", "E1", "evidence/old/E1.jpg"), ("instrument", "I1", "evidence/old/I1.jpg")):
            conn.execute("""INSERT INTO public.evidence_crops (revision_id, item_type, item_id, item_name, description, timestamp_seconds, bounding_box, image)
                            VALUES (%s, %s, %s, 'n', 'd', 1, '{1,2,3,4}', %s)""", (rev, item_type, item_id, image))
        before = one(conn, "SELECT payload, note, created_at FROM public.report_revisions WHERE id = %s", (rev,))
        notes = one(conn, "SELECT notes FROM public.model_runs WHERE id = 'm1'")[0]
        key_map = json.dumps({"evidence/old/E1.jpg": "evidence/Clip.mp4/run-1/E1.jpg", "evidence/old/I1.jpg": "evidence/Clip.mp4/run-1/I1.jpg"})
        result = one(conn, "SELECT public.apply_r2_rename('v10', 'uploads/Clip.mp4', %s)", (key_map,))[0]
        assert result == {"videoId": "v10", "filepath": "uploads/Clip.mp4", "revisions": 1, "crops": 2}, result
        assert one(conn, "SELECT filepath FROM public.videos WHERE id = 'v10'")[0] == "uploads/Clip.mp4"
        row = one(conn, "SELECT evidence, payload, note, created_at FROM public.report_revisions WHERE id = %s", (rev,))
        assert [e["image"] for e in row[0]] == ["evidence/Clip.mp4/run-1/E1.jpg", None, "evidence/Clip.mp4/run-1/I1.jpg"], row[0]
        assert [e["item_id"] for e in row[0]] == ["E1", "A1", "I1"], "evidence order is kept"
        assert (row[1], row[2], row[3]) == before, "only the evidence column of a revision changes"
        images = sorted(r[0] for r in conn.execute("SELECT image FROM public.evidence_crops WHERE revision_id = %s", (rev,)).fetchall())
        assert images == ["evidence/Clip.mp4/run-1/E1.jpg", "evidence/Clip.mp4/run-1/I1.jpg"], images
        assert one(conn, "SELECT notes FROM public.model_runs WHERE id = 'm1'")[0] == notes, "model_runs.notes is immutable"
        again = one(conn, "SELECT public.apply_r2_rename('v10', 'uploads/Clip.mp4', %s)", (key_map,))[0]
        assert again["revisions"] == 0 and again["crops"] == 0, "running it again changes nothing"
        other = one(conn, "SELECT evidence FROM public.report_revisions WHERE video_id = 'v2'")[0]
        assert other == [], other

    @test("apply_r2_rename leaves the append-only guard in force and refuses bad input")
    def _():
        refused(conn, "UPDATE public.report_revisions SET evidence = '[]' WHERE video_id = 'v10'", contains="append-only")
        refused(conn, "UPDATE public.report_revisions SET note = 'x' WHERE video_id = 'v10'", contains="append-only")
        refused(conn, "DELETE FROM public.report_revisions WHERE video_id = 'v10'", contains="append-only")
        refused(conn, "SELECT public.apply_r2_rename('v10', NULL, '[]')", contains="object")
        refused(conn, "SELECT public.apply_r2_rename('v10', NULL, '{\"a\": 1}')", contains="string")
        refused(conn, "SELECT public.apply_r2_rename('nope', 'uploads/x.mp4', '{}')", contains="Video not found")
        assert one(conn, "SELECT current_setting('app.r2_rename', true)")[0] in (None, "", "off")
        conn.execute("DELETE FROM public.videos WHERE id = 'v10'")
        assert one(conn, "SELECT count(*) FROM public.report_revisions WHERE video_id = 'v10'")[0] == 0

    @test("security: apply_r2_rename is for the service role only")
    def _():
        conn.execute("RESET ROLE")
        sig = "public.apply_r2_rename(character varying, text, jsonb)"
        for role in ("anon", "authenticated"):
            assert not one(conn, "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig))[0], role
        assert one(conn, "SELECT has_function_privilege('service_role', %s, 'EXECUTE')", (sig,))[0]
        conn.execute("SET ROLE service_role")

    @test("search_report_revisions ranks embedded revisions and skips NULL vectors")
    def _():
        conn.execute("INSERT INTO public.videos (id) VALUES ('v8'), ('v9')")
        embedded = one(conn, """
            INSERT INTO public.report_revisions (video_id, revision_no, kind, model_run_id, payload, evidence,
                incident_type, severity_level, start_seconds, end_seconds, embedding)
            VALUES ('v8', 1, 'ai', 'm1', %s, '[]', 'assault', 4, 2, 9, %s::extensions.vector) RETURNING id""",
            (json.dumps(report(title="Vector hit")), vector_literal(7)))[0]
        ai_revision("v9", 1, "m1", report(title="No vector"), [])
        rows = conn.execute("""SELECT revision_id, video_id, title, similarity_score FROM public.search_report_revisions(%s::extensions.vector, 5, 0.0)""",
                            (vector_literal(7),)).fetchall()
        assert rows == [(embedded, "v8", "Vector hit", 1.0)], rows
        assert conn.execute("SELECT count(*) FROM public.search_report_revisions(%s::extensions.vector, 5, 1.1)",
                            (vector_literal(7),)).fetchone()[0] == 0
        refused(conn, "UPDATE public.report_revisions SET embedding = NULL WHERE id = %s", (embedded,), contains="append-only")
        assert one(conn, "SELECT count(*) FROM pg_indexes WHERE indexname = 'report_revisions_embedding_hnsw_idx'")[0] == 1

    # Background analysis jobs (20261031120000_analysis_jobs.sql).
    def enqueue(name: str) -> int:
        return one(conn, "INSERT INTO public.analysis_jobs (file_path, filename, model) VALUES (%s, %s, 'model-a') RETURNING id",
                   (f"uploads/{name}", name))[0]

    def claim(stale_seconds: int = 180):
        row = conn.execute("SELECT id, status, attempts, started_at IS NOT NULL, heartbeat_at IS NOT NULL "
                           "FROM public.claim_analysis_job(%s)", (stale_seconds,)).fetchone()
        return row

    job_status = lambda job: one(conn, "SELECT status, attempts, error FROM public.analysis_jobs WHERE id = %s", (job,))
    age = lambda job, seconds: conn.execute(
        "UPDATE public.analysis_jobs SET heartbeat_at = heartbeat_at - make_interval(secs => %s) WHERE id = %s", (seconds, job))
    finish = lambda job, attempt, status, error=None, video=None, revision=None: conn.execute(
        "SELECT id, status, error, video_id, revision_id FROM public.finish_analysis_job(%s, %s, %s, %s, %s, %s)",
        (job, attempt, status, error, video, revision)).fetchall()

    @test("analysis jobs: a new job is queued and claims run oldest first, one at a time")
    def _():
        first, second = enqueue("a.mp4"), enqueue("b.mp4")
        assert job_status(first) == ("queued", 0, None)
        assert claim() == (first, "running", 1, True, True)
        assert claim() == (second, "running", 1, True, True)
        assert claim() is None, "nothing left to claim"
        assert finish(first, 1, "done", video="v2", revision=ai2) == [(first, "done", None, "v2", ai2)]
        assert finish(second, 1, "failed", error="  ") == [(second, "failed", "The analysis failed", None, None)]

    @test("analysis jobs: only the claim that is still running can heartbeat or finish")
    def _():
        job = enqueue("c.mp4")
        assert claim()[0] == job
        assert one(conn, "SELECT public.touch_analysis_job(%s, 1)", (job,))[0] is True
        assert one(conn, "SELECT public.touch_analysis_job(%s, 2)", (job,))[0] is False, "another claim's attempt"
        assert finish(job, 2, "done", video="v2", revision=ai2) == []
        refused(conn, "SELECT * FROM public.finish_analysis_job(%s, 1, 'done')", (job,), contains="needs its video")
        refused(conn, "SELECT * FROM public.finish_analysis_job(%s, 1, 'running')", (job,), contains="done or failed")
        assert finish(job, 1, "failed", error="Unsupported model") == [(job, "failed", "Unsupported model", None, None)]
        assert one(conn, "SELECT public.touch_analysis_job(%s, 1)", (job,))[0] is False, "a finished job takes no heartbeat"
        assert finish(job, 1, "done", video="v2", revision=ai2) == [], "a finished job is not finished again"

    @test("analysis jobs: a stale running job is queued again once, then failed")
    def _():
        job = enqueue("d.mp4")
        assert claim()[0] == job
        age(job, 120)
        assert claim() is None and job_status(job)[0] == "running", "a fresh enough heartbeat is left alone"
        age(job, 120)
        assert claim() == (job, "running", 2, True, True), "first stale run: back to the queue and claimed again"
        assert one(conn, "SELECT public.touch_analysis_job(%s, 1)", (job,))[0] is False, "the old runner lost its claim"
        age(job, 600)
        assert claim() is None
        status, attempts, error = job_status(job)
        assert (status, attempts) == ("failed", 2) and "stopped twice" in error, (status, attempts, error)
        assert one(conn, "SELECT finished_at IS NOT NULL FROM public.analysis_jobs WHERE id = %s", (job,))[0]
        refused(conn, "SELECT * FROM public.claim_analysis_job(0)", contains="at least 1")

    @test("analysis jobs: retry requeues a failed or cancelled job; cancel takes only a queued one")
    def _():
        failed = one(conn, "SELECT id FROM public.analysis_jobs WHERE filename = 'd.mp4'")[0]
        retried = conn.execute("SELECT status, attempts, error, started_at, finished_at FROM public.retry_analysis_job(%s)",
                               (failed,)).fetchall()
        assert retried == [("queued", 0, None, None, None)], retried
        assert conn.execute("SELECT id FROM public.retry_analysis_job(%s)", (failed,)).fetchall() == [], "a queued job is not retried"
        cancelled = conn.execute("SELECT status FROM public.cancel_analysis_job(%s)", (failed,)).fetchall()
        assert cancelled == [("cancelled",)] and claim() is None, "a cancelled job is never claimed"
        assert conn.execute("SELECT status FROM public.retry_analysis_job(%s)", (failed,)).fetchall() == [("queued",)]
        assert claim()[0] == failed
        assert conn.execute("SELECT id FROM public.cancel_analysis_job(%s)", (failed,)).fetchall() == [], "a running job is not cancelled"
        done = one(conn, "SELECT id FROM public.analysis_jobs WHERE status = 'done'")[0]
        assert conn.execute("SELECT id FROM public.retry_analysis_job(%s)", (done,)).fetchall() == [], "a done job is not retried"
        assert finish(failed, 1, "failed", error="x")[0][1] == "failed"

    @test("analysis jobs: two loops never claim the same job")
    def _():
        job = enqueue("e.mp4")
        other = psycopg.connect(server.get_uri(), autocommit=True)
        try:
            other.execute("SET ROLE service_role")
            with conn.transaction():
                assert claim()[0] == job
                # the first claim is not committed yet: the second loop skips the locked row
                assert other.execute("SELECT id FROM public.claim_analysis_job(180)").fetchall() == []
            assert other.execute("SELECT id FROM public.claim_analysis_job(180)").fetchall() == []
        finally:
            other.close()
        assert finish(job, 1, "failed", error="x")[0][1] == "failed"
        refused(conn, "INSERT INTO public.analysis_jobs (file_path, filename, status) VALUES ('k', 'k', 'paused')",
                contains="analysis_jobs_status_check")

    @test("security: analysis jobs are for the service role only")
    def _():
        conn.execute("RESET ROLE")
        for role in ("anon", "authenticated"):
            assert not one(conn, "SELECT has_table_privilege(%s, 'public.analysis_jobs', 'SELECT')", (role,))[0], role
            for sig in ("public.claim_analysis_job(integer)", "public.touch_analysis_job(bigint, integer)",
                        "public.finish_analysis_job(bigint, integer, text, text, character varying, bigint)",
                        "public.retry_analysis_job(bigint)", "public.cancel_analysis_job(bigint)"):
                assert not one(conn, "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig))[0], (role, sig)
        assert one(conn, "SELECT has_function_privilege('service_role', 'public.claim_analysis_job(integer)', 'EXECUTE')")[0]
        conn.execute("SET ROLE service_role")

    # Privacy blur (20261101090000_video_blur_faces.sql).
    @test("privacy blur: the toggle defaults to off, and a re-analysis job names its video and goes with it")
    def _():
        conn.execute("INSERT INTO public.videos (id, filepath) VALUES ('vb', 'uploads/vb.mp4')")
        assert one(conn, "SELECT blur_faces FROM public.videos WHERE id = 'vb'")[0] is False
        conn.execute("UPDATE public.videos SET blur_faces = true WHERE id = 'vb'")
        refused(conn, "UPDATE public.videos SET blur_faces = NULL WHERE id = 'vb'", contains="blur_faces")
        upload = enqueue("upload.mp4")
        assert one(conn, "SELECT reanalyse_video_id, blur_faces FROM public.analysis_jobs WHERE id = %s", (upload,)) == (None, False)
        job = one(conn, "INSERT INTO public.analysis_jobs (file_path, filename, model, reanalyse_video_id, blur_faces) "
                        "VALUES ('uploads/vb.mp4', 'vb.mp4', 'model-a', 'vb', true) RETURNING id")[0]
        later = enqueue("later.mp4")
        claim_any = lambda: conn.execute(
            "SELECT id, reanalyse_video_id, blur_faces FROM public.claim_queued_analysis_job(180, false)").fetchone()
        claim_old = lambda: conn.execute("SELECT id FROM public.claim_analysis_job(180)").fetchone()
        assert claim_any() == (upload, None, False), "jobs still run oldest first"
        assert claim_old() == (later,), "an older console's claim passes over the re-analysis job"
        assert claim_old() is None and job_status(job)[0] == "queued"
        assert claim_any() == (job, "vb", True)
        refused(conn, "SELECT * FROM public.claim_queued_analysis_job(0, false)", contains="at least 1")
        refused(conn, "INSERT INTO public.analysis_jobs (file_path, filename, reanalyse_video_id) VALUES ('k', 'k', 'nope')",
                contains="analysis_jobs_reanalyse_video_id_fkey")
        conn.execute("DELETE FROM public.videos WHERE id = 'vb'")
        assert one(conn, "SELECT count(*) FROM public.analysis_jobs WHERE id = %s", (job,))[0] == 0
        assert finish(upload, 1, "failed", error="x")[0][1] == "failed"
        assert finish(later, 1, "failed", error="x")[0][1] == "failed"
        conn.execute("RESET ROLE")
        for role in ("anon", "authenticated"):
            assert not one(conn, "SELECT has_function_privilege(%s, 'public.claim_queued_analysis_job(integer, boolean)', "
                                 "'EXECUTE')", (role,))[0], role
        assert one(conn, "SELECT has_function_privilege('service_role', 'public.claim_queued_analysis_job(integer, boolean)', "
                         "'EXECUTE')")[0]
        conn.execute("SET ROLE service_role")

    @test("deleting a video removes its revisions, crops and query results")
    def _():
        conn.execute("INSERT INTO public.queries (id, query_text) VALUES ('q1', 'man')")
        conn.execute("""INSERT INTO public.query_results (id, query_id, revision_id, item_type, item_id, rank, similarity_score, critic_verdict)
                        VALUES ('r1', 'q1', %s, 'entity', 'E1', 1, 0.9, 'confirmed')""", (ai1,))
        linked = one(conn, "INSERT INTO public.analysis_jobs (file_path, filename, status, video_id, revision_id) "
                           "VALUES ('uploads/v1.mp4', 'v1.mp4', 'done', 'v1', %s) RETURNING id", (ai1,))[0]
        conn.execute("DELETE FROM public.videos WHERE id = 'v1'")
        assert one(conn, "SELECT status, video_id, revision_id FROM public.analysis_jobs WHERE id = %s", (linked,)) == ("done", None, None)
        assert one(conn, "SELECT count(*) FROM public.report_revisions WHERE video_id = 'v1'")[0] == 0
        assert one(conn, "SELECT count(*) FROM public.evidence_crops")[0] == 0
        assert one(conn, "SELECT count(*) FROM public.query_results")[0] == 0

    conn.execute("RESET ROLE")
    conn.close()

print(f"\n{len(PASSED)} passed")
sys.exit(0)
