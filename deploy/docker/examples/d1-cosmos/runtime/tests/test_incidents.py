# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Operator incidents are verified evidence, not all detector candidates."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_pipeline.contracts import Rule
from agent_pipeline.incidents import record_review, reconcile_rule, router, snapshot
from agent_pipeline.rules import process_decision
from agent_pipeline.store import Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("D1_CLIP_CACHE", str(tmp_path / "clips"))
    result = Store(tmp_path / "state.sqlite")
    result.put_config("rule", "congestion", rule())
    return result


def rule(**kwargs):
    return Rule(stream_id="traffic", config_revision=1, field="noul", value=.8,
                consecutive_samples=1, cooldown_seconds=0, **kwargs).model_dump()


def decision(index, probability=.95, revision=1, at=None):
    return {"id": f"decision-{index}", "schema_version": 1, "kind": "d1.decision", "stream_id": "traffic",
            "captured_at": at if at is not None else 1000 + index * 2, "config_revision": revision,
            "answer": {"noul": probability}, "question": {"type": "noul", "instructions": "Is traffic congested?"}}


def confirm(store, event, verdict="confirmed", **extra):
    review = {"id": event["id"] + "-review", "event_id": event["id"], "stream_id": event["stream_id"],
              "verification_version": 1, "verdict": verdict, "summary": "A tightly spaced queue is visible.",
              "evidence": ["Cars barely move."], "uncertainty": ["Cause outside view is unknown."], **extra}
    with store.transaction() as db:
        record_review(db, review)
        db.execute("UPDATE jobs SET status='complete' WHERE id=?", (event["id"],))
    return review


def test_candidates_only_become_incidents_after_verified_confirmation(store):
    event = process_decision(store, decision(1))[0]
    before = snapshot(store, now=1002)
    assert before["incidents"] == []
    assert before["counts"]["pending"] == 1
    assert before["candidates"][0]["status"] == "pending"
    confirm(store, event)
    after = snapshot(store, now=1003)
    assert after["counts"]["confirmed_incidents"] == 1
    assert after["incidents"][0]["representative_event_id"] == event["id"]
    assert after["incidents"][0]["monitoring_status"] == "observing"


def test_repeats_consolidate_and_delayed_review_does_not_replace_newer_clip(store):
    events = [process_decision(store, decision(i))[0] for i in (1, 2, 3)]
    assert len({event["incident_id"] for event in events}) == 1
    confirm(store, events[2])
    confirm(store, events[0])  # slow old result finishes after new evidence
    confirm(store, events[1], "rejected")
    result = snapshot(store, now=1006)
    assert len(result["incidents"]) == 1
    assert result["incidents"][0]["candidate_count"] == 3
    assert result["incidents"][0]["confirmed_count"] == 2
    assert result["incidents"][0]["representative_event_id"] == events[2]["id"]
    assert result["counts"]["rejected"] == 1


@pytest.mark.parametrize("verdict", ["rejected", "uncertain"])
def test_non_confirmations_retained_in_audit_without_operator_incident(store, verdict):
    event = process_decision(store, decision(1))[0]
    confirm(store, event, verdict)
    result = snapshot(store, now=1002)
    assert result["incidents"] == []
    assert result["candidates"][0]["review"]["verdict"] == verdict
    assert result["counts"][verdict] == 1


def test_failed_review_is_not_semantic_rejection(store):
    event = process_decision(store, decision(1))[0]
    with store.transaction() as db:
        db.execute("UPDATE jobs SET status='failed',error='VerificationFormatError' WHERE id=?", (event["id"],))
    result = snapshot(store)
    assert result["incidents"] == []
    assert result["counts"]["failed"] == 1
    assert result["counts"]["rejected"] == 0
    assert result["candidates"][0]["error"] == "VerificationFormatError"


def test_mid_scores_and_outages_do_not_close_an_incident(store):
    event = process_decision(store, decision(1))[0]
    confirm(store, event)
    for i in range(2, 20):
        process_decision(store, decision(i, probability=.79))
    result = snapshot(store, now=1038)
    assert result["incidents"][0]["status"] == "active"
    assert result["incidents"][0]["monitoring_status"] == "observing"
    result = snapshot(store, now=1100)
    assert result["incidents"][0]["status"] == "active"
    assert result["incidents"][0]["monitoring_status"] == "stale"
    assert result["incidents"][0]["closed_at"] is None
    resumed = process_decision(store, decision(50, at=1100))[0]
    assert resumed["incident_id"] == event["incident_id"]
    after = snapshot(store, now=1100)["incidents"][0]
    assert after["observation_gap_count"] == 1
    assert after["last_observation_gap"] == {"after": 1038, "before": 1100}


def test_latest_disagreement_is_visible_separately_from_prior_confirmation(store):
    first = process_decision(store, decision(1))[0]
    confirm(store, first)
    latest = process_decision(store, decision(2))[0]
    confirm(store, latest, "rejected")
    incident = snapshot(store, now=1004)["incidents"][0]
    assert incident["representative_event_id"] == first["id"]
    assert incident["review"]["verdict"] == "confirmed"
    assert incident["latest_event_id"] == latest["id"]
    assert incident["latest_status"] == "rejected"
    assert incident["latest_review"]["verdict"] == "rejected"
    assert incident["latest_candidate_event"]["id"] == latest["id"]


def test_sustained_clear_then_return_creates_new_episode_and_ignores_old_cooldown(store):
    current = rule()
    current["cooldown_seconds"] = 60
    store.put_config("rule", "congestion", current)
    first = process_decision(store, decision(1))[0]
    confirm(store, first)
    for i in range(2, 8):  # six observations, from 1004 through 1014 = ten seconds
        process_decision(store, decision(i, probability=.1))
    closed = snapshot(store, now=1014)["incidents"][0]
    assert closed["closed_at"] == 1014
    assert closed["close_reason"] == "condition_cleared"
    second = process_decision(store, decision(8))[0]
    assert second["incident_id"] != first["incident_id"]
    confirm(store, second)
    assert snapshot(store, now=1016)["counts"]["confirmed_incidents"] == 2


def test_unknown_answers_and_gaps_break_clear_streak(store):
    event = process_decision(store, decision(1))[0]
    confirm(store, event)
    for i in range(2, 7):
        process_decision(store, decision(i, probability=.1))
    invalid = decision(7)
    invalid["answer"] = {}
    process_decision(store, invalid)
    process_decision(store, decision(8, probability=.1))
    process_decision(store, decision(9, probability=.1, at=1100))
    assert snapshot(store, now=1100)["incidents"][0]["status"] == "active"


def test_duplicate_out_of_order_and_restart_preserve_group(store):
    first = process_decision(store, decision(1))[0]
    confirm(store, first)
    assert process_decision(store, decision(1)) == []
    assert process_decision(store, decision(0)) == []
    path = store.rows("PRAGMA database_list")[0]["file"]
    restarted = Store(path)
    second = process_decision(restarted, decision(2))[0]
    assert second["incident_id"] == first["incident_id"]
    assert snapshot(restarted, now=1004)["incidents"][0]["candidate_count"] == 2


def test_configuration_revision_isolation_and_pause_are_not_scene_clear(store):
    first = process_decision(store, decision(1))[0]
    confirm(store, first)
    current = rule()
    current["config_revision"] = 2
    store.put_config("rule", "congestion", current)
    assert process_decision(store, decision(2)) == []
    second = process_decision(store, decision(3, revision=2))[0]
    confirm(store, second)
    assert first["incident_id"] != second["incident_id"]
    old = next(item for item in snapshot(store)["incidents"] if item["id"] == first["incident_id"])
    assert old["close_reason"] == "configuration_changed"
    current["enabled"] = False
    store.put_config("rule", "congestion", current)
    paused = next(item for item in snapshot(store)["incidents"] if item["id"] == second["incident_id"])
    assert paused["monitoring_status"] == "paused"
    assert paused["close_reason"] == "monitoring_paused"


def test_review_prompt_edit_does_not_split_condition(store):
    first = process_decision(store, decision(1))[0]
    current = rule()
    current["review_prompt"] = "Keep the evidence brief."
    store.put_config("rule", "congestion", current)
    second = process_decision(store, decision(2))[0]
    assert second["incident_id"] == first["incident_id"]


def test_pause_before_first_candidate_does_not_backdate_new_observation(store):
    current = rule()
    current["consecutive_samples"] = 2
    store.put_config("rule", "congestion", current)
    assert process_decision(store, decision(1)) == []
    current["enabled"] = False
    store.put_config("rule", "congestion", current)
    current["enabled"] = True
    store.put_config("rule", "congestion", current)
    assert process_decision(store, decision(2)) == []
    event = process_decision(store, decision(3))[0]
    confirm(store, event)
    assert snapshot(store, now=1006)["incidents"][0]["started_at"] == 1004


def test_removing_rule_closes_monitoring_without_claiming_clear(store):
    event = process_decision(store, decision(1))[0]
    confirm(store, event)
    with store.transaction() as db:
        reconcile_rule(db, "congestion")
        db.execute("DELETE FROM config WHERE id='congestion'")
    result = snapshot(store)["incidents"][0]
    assert result["close_reason"] == "configuration_changed"


def test_historical_captions_never_claim_verified_even_if_content_says_confirmed(store):
    event = process_decision(store, decision(1))[0]
    with store.transaction() as db:
        record_review(db, {"event_id": event["id"], "stream_id": "traffic", "context": "Congestion is confirmed."})
        db.execute("UPDATE jobs SET status='complete' WHERE id=?", (event["id"],))
    result = snapshot(store)
    assert result["incidents"] == []
    assert result["counts"]["legacy"] == 1
    assert result["candidates"][0]["status"] == "legacy"


def test_historical_job_verified_after_upgrade_is_closed_historical_evidence(store):
    event = process_decision(store, decision(1))[0]
    with store.transaction() as db:
        db.execute("DELETE FROM incident_candidates")
        db.execute("DELETE FROM incident_episodes")
        db.execute("DELETE FROM incident_conditions")
    confirm(store, event)
    result = snapshot(store, now=1200)
    assert result["incidents"][0]["close_reason"] == "historical_review"
    assert result["counts"]["active_incidents"] == 0


@pytest.mark.parametrize("override", [{"verdict": "yes"}, {"summary": ""}, {"stream_id": "other"}, {"verification_version": 2}, {"evidence": [","]}])
def test_invalid_or_wrong_stream_review_never_promotes(store, override):
    event = process_decision(store, decision(1))[0]
    with pytest.raises(ValueError):
        confirm(store, event, **override)
    assert snapshot(store)["incidents"] == []


def test_api_source_filter_limit_and_pending_counts(store):
    for i in (1, 2, 3):
        process_decision(store, decision(i))
    with store.transaction() as db:
        db.execute("UPDATE jobs SET status='reviewing' WHERE rowid=1")
    app = FastAPI()
    app.include_router(router(store))
    with TestClient(app) as client:
        response = client.get("/v1/incidents?stream_id=traffic&limit=1")
        assert response.status_code == 200
        assert len(response.json()["candidates"]) == 1
        assert response.json()["counts"]["pending"] == 3
        assert client.get("/v1/incidents?stream_id=other").json()["counts"]["candidates"] == 0
        assert client.get("/v1/incidents?limit=1000").status_code == 422


def test_candidate_projection_and_job_roll_back_together(store, monkeypatch):
    def fail(*_):
        raise OSError("outbox unavailable")
    monkeypatch.setattr(store, "enqueue", fail)
    with pytest.raises(OSError):
        process_decision(store, decision(1))
    assert store.rows("SELECT * FROM incident_candidates") == []
    assert store.rows("SELECT * FROM incident_episodes") == []
    assert store.rows("SELECT * FROM incident_conditions") == []
    assert store.rows("SELECT * FROM jobs") == []
