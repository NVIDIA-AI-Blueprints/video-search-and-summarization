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

"""The chat harness with the agent's real chat router (grounded mode) and the fake model, offline.

Runs with the agent's Python; the eval environment has no ``vss_agents`` and skips it:

    cd eval && ../../../../../services/agent/.venv/bin/python -m pytest --noconftest \\
        tests/v2/test_v2_chat_harness_agent.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("vss_agents.api.incident_chat", reason="needs the agent's Python (services/agent/.venv)")

from vss_agents.utils import grounded_chat  # noqa: E402

from evalv2.core.budget import BudgetLedger, Cap  # noqa: E402
from evalv2.core.netguard import no_network  # noqa: E402
from evalv2.tasks.chat import corpus, evaluate, harness, runner  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_chat_references import GT  # noqa: E402
from chat_task_fixtures import CLIPS, RECORDS, _prediction  # noqa: E402

BY_ID = {r["record_id"]: r for r in RECORDS}


def _items(corpora=("oracle",)):
    chosen = runner.half_records(RECORDS, half="dev", clips=CLIPS)
    return runner.work_items(chosen, corpora=corpora, subjects=["chat-subject"], repeats=1)


def _snapshots():
    return {"oracle": corpus.oracle_snapshot(GT, CLIPS),
            "system": corpus.system_snapshot({"Assault001": _prediction("Assault001")}, CLIPS)}


def _run(tmp_path, model, *, corpora=("oracle",), **kwargs):
    snaps = {name: s for name, s in _snapshots().items() if name in corpora}
    with no_network():
        return harness.run(_items(corpora), BY_ID, snaps, model, tmp_path, manifest={"live": False,
                                                                                    "model": model.model}, **kwargs)


def _rows(tmp_path):
    return {(r["corpus"], r["class"]): r for r in harness.read_rows(tmp_path / "answers.jsonl")}


def test_the_real_router_answers_every_item_over_the_snapshots_with_no_network(tmp_path):
    model = harness.MeteredChatModel(harness.FakeChatModel(), subject="chat-subject")
    result = _run(tmp_path, model, corpora=("oracle", "system"))
    assert result["outcomes"] == {"answered": 10, "no_report": 2}  # Assault002 and Burglary003 lack system reports
    rows = _rows(tmp_path)
    incident = rows[("oracle", "incident.type")]
    assert incident["grounding"]["mode"] == "grounded" and incident["grounding"]["scope"] == "incident"
    assert incident["grounding"]["version_id"] == 100_001 and incident["grounding"]["official"] is True
    assert incident["answer"]["citations"][0]["incident_id"] == "Assault001"
    assert incident["calls"][0]["context"].startswith("REPORT:\n") and incident["usage"]["calls"] == 1
    refused = rows[("oracle", "incident.unanswerable")]
    assert refused["answer"]["insufficient_evidence"] is True and refused["answer"]["citations"] == []
    count = rows[("oracle", "library.type_count")]
    assert count["grounding"]["answered_by"] == "code" and count["usage"]["calls"] == 1
    assert count["library"]["ranked"] == ["Assault001", "Assault002"]
    assert count["library"]["named"] == ["Assault001", "Assault002"]
    assert "two" in count["answer"]["content"]
    retrieval = rows[("oracle", "library.retrieval")]
    assert retrieval["grounding"]["answered_by"] == "model" and retrieval["usage"]["calls"] == 2
    system = rows[("system", "library.type_count")]
    assert system["library"]["ranked"] == ["Assault001"], "the system library holds only its own reports"


def test_scoring_the_real_routers_answers_keeps_the_blocks_apart(tmp_path):
    model = harness.MeteredChatModel(harness.FakeChatModel(), subject="chat-subject")
    _run(tmp_path, model, corpora=("oracle", "system"))
    with no_network():
        scores = evaluate.score_run(harness.read_rows(tmp_path / "answers.jsonl"), BY_ID, GT)
        result = evaluate.report(scores, resamples=100)
    oracle = result["corpora"]["oracle"]["tiers"]["all"]
    assert oracle["answers"]["exact_by_class"]["library.type_count"]["value"] == 1.0, "a code count is exact"
    assert oracle["answers"]["exact_by_class"]["incident.type"]["value"] == 1.0
    assert oracle["answers"]["refusal"]["recall"]["value"] == 1.0
    assert oracle["retrieval"]["questions"] == 2 and oracle["citations"]["precision"]["n"] > 0
    assert result["corpora"]["system"]["coverage"]["no_report"] == 2


def test_a_model_failure_becomes_an_http_error_row_and_a_retry_answers_it(tmp_path):
    failing = harness.MeteredChatModel(harness.FakeChatModel(fail=grounded_chat.ChatModelError), subject="chat-subject")
    result = _run(tmp_path, failing)
    assert result["outcomes"] == {"http_error": 6}
    row = next(iter(_rows(tmp_path).values()))
    assert row["status"] == 502 and "Chat model error" in row["detail"] and row["calls"][0]["error"]
    model = harness.MeteredChatModel(harness.FakeChatModel(), subject="chat-subject")
    again = _run(tmp_path, model, retry_errors=True)
    assert again["asked"] == 6 and again["outcomes"] == {"answered": 6}


def test_the_budget_stops_the_router_before_a_call_that_would_pass_the_cap(tmp_path):
    ledger = BudgetLedger([Cap("inference", max_attempts=3)], tmp_path / "budget.jsonl")
    inner = harness.FakeChatModel()
    model = harness.MeteredChatModel(inner, ledger=ledger, subject="chat-subject")
    result = _run(tmp_path, model)
    assert result["stopped"].startswith("budget") and inner.calls == 3
    assert result["open"] > 0 and result["budget"]["caps"][0]["attempts"] == 3
    resumed = BudgetLedger([Cap("inference", max_attempts=10)], tmp_path / "budget.jsonl")
    more = harness.MeteredChatModel(harness.FakeChatModel(), ledger=resumed, subject="chat-subject")
    finished = _run(tmp_path, more)
    assert finished["stopped"] is None and finished["open"] == 0
    assert len(harness.latest_rows(harness.read_rows(tmp_path / "answers.jsonl"))) == 6


def test_snapshot_reports_are_the_routers_library_reports():
    reports = asyncio.run(harness.SnapshotLibraryIndex(_snapshots()["oracle"], grounded_chat.LibraryReport).reports())
    assert all(isinstance(r, grounded_chat.LibraryReport) for r in reports)
    plan = grounded_chat.check_plan({"incident_type": "burglary", "severity": None, "instrument": None,
                                     "asset": None, "query": "", "wants_count": True, "group_by": None})
    assert [r.incident_id for r in reports if grounded_chat.matches(r, plan)] == ["Burglary003"]
