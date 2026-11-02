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

"""The SQL harness: snapshot rules and explicit parameters always; the real schema where pgserver is installed."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from evalv2.sql import harness as h

BOUNDS = {"p_period_start": "2026-10-01T00:00:00", "p_period_end": "2026-11-01T00:00:00", "p_timezone": "UTC",
          "p_scope": "all"}


def _report(kind, severity):
    return {"incident": {"type": kind, "title": f"{kind} case", "start_timestamp": 1, "end_timestamp": 9,
                         "description": f"A {kind}.", "severity_level": severity, "severity_reason": "r",
                         "confidence_score": 0.8, "location": "street"},
            "entities": [], "instruments": [], "assets": [], "timeline": [], "uncertainties": []}


SNAPSHOT = {
    "version": h.SNAPSHOT_VERSION, "taken_at": "2026-11-01T00:00:00+00:00",
    "tables": {
        "videos": [{"id": "v1", "filepath": "uploads/v1/clip.mp4", "source": "cam", "official_revision_id": 1},
                   {"id": "v2", "filepath": "uploads/v2/clip.mp4", "source": "cam"}],
        "model_runs": [{"id": "m1", "model_name": "model-a"}],
        "report_revisions": [
            {"id": 1, "video_id": "v1", "revision_no": 1, "kind": "ai", "model_run_id": "m1",
             "payload": _report("assault", 4), "evidence": [], "incident_type": "assault", "severity_level": 4,
             "start_seconds": 1, "end_seconds": 9, "created_at": "2026-10-15T10:00:00"},
            {"id": 2, "video_id": "v2", "revision_no": 1, "kind": "ai", "model_run_id": "m1",
             "payload": _report("burglary", 2), "evidence": [], "incident_type": "burglary", "severity_level": 2,
             "start_seconds": 1, "end_seconds": 9, "created_at": "2026-10-20T10:00:00"},
        ],
    },
}


def test_the_snapshot_rules():
    assert h.validate_snapshot(SNAPSHOT) == []
    assert h.snapshot_sha256(SNAPSHOT) == h.snapshot_sha256(dict(SNAPSHOT))
    bad = {**SNAPSHOT, "version": "x", "taken_at": "yesterday", "tables": {"incidents": [{}]}}
    problems = " ".join(h.validate_snapshot(bad))
    assert "version" in problems and "taken_at" in problems and "unknown tables" in problems


@pytest.mark.parametrize("missing", h.DASHBOARD_REQUIRED)
def test_dashboard_calls_without_explicit_bounds_timezone_or_scope_are_refused(missing):
    with pytest.raises(h.SqlHarnessError, match="explicit"):
        h.dashboard_call({**BOUNDS, missing: None})


def test_dashboard_calls_name_every_parameter():
    sql, params = h.dashboard_call({**BOUNDS, "p_type": "assault"})
    assert "p_period_start => %(p_period_start)s" in sql and params["p_type"] == "assault"
    with pytest.raises(h.SqlHarnessError, match="unknown"):
        h.dashboard_call({**BOUNDS, "p_limit": 3})
    with pytest.raises(ValueError):
        h.dashboard_call({**BOUNDS, "p_period_end": "next week"})
    with pytest.raises(h.SqlHarnessError, match="explicit"):
        h.summaries_call({"p_page": 1})


def test_an_invalid_snapshot_is_refused_before_anything_is_written():
    class NoConnection:
        def execute(self, *args, **kwargs):
            raise AssertionError("nothing may be written")

    with pytest.raises(h.SqlHarnessError, match="version"):
        h.SqlHarness(NoConnection(), dict).load({**SNAPSHOT, "version": "x"})


def test_the_runner_script_runs_in_its_own_environment():
    script = Path(__file__).resolve().parents[2] / "scripts" / "v2_sql_harness.py"
    header = script.read_text().splitlines()[:3]
    assert header[0] == "# /// script" and "pgserver" in header[2]


HAS_PGSERVER = all(importlib.util.find_spec(m) is not None for m in ("pgserver", "psycopg"))


@pytest.mark.skipif(not HAS_PGSERVER, reason="pgserver and psycopg run in a throwaway environment")
def test_the_real_schema_counts_the_snapshot():
    with h.SqlHarness.start() as harness:
        digest = harness.load(SNAPSHOT)
        dashboard = harness.dashboard(**BOUNDS)
        official = harness.dashboard(**{**BOUNDS, "p_scope": "official"})
        summaries = harness.report_summaries(p_page=1, p_page_size=6)
    assert digest == h.snapshot_sha256(SNAPSHOT)
    assert dashboard["total"] == 2 and (dashboard["official"], dashboard["notOfficial"]) == (1, 1)
    assert {t["type"]: t["count"] for t in dashboard["types"]} == {"assault": 1, "burglary": 1}
    assert official["total"] == 1
    assert summaries["totalItems"] == 2 and len(summaries["reports"]) == 2
