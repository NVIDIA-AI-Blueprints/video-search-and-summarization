# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Configuration recovery and bounded, transactional Kafka replay protection."""
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_pipeline import analytics, assistant, store as store_module
from agent_pipeline.bus import ConsumerPosition, DECISIONS
from agent_pipeline.contracts import Rule
from agent_pipeline.rules import process_decision
from agent_pipeline.store import Store
from agent_pipeline.verification import candidate_condition


def draft():
    return {
        "question": {"type": "noul", "instructions": "Is traffic congested?", "criteria": None},
        "interval_seconds": 2,
        "review": {"field": "noul", "probability_key": None, "operator": "gte", "value": .85,
                   "consecutive_samples": 1, "max_gap_seconds": 10, "cooldown_seconds": 0,
                   "pre_seconds": 10, "post_seconds": 5, "review_prompt": "Describe visible movement."},
    }


def rule(**changes):
    return Rule(stream_id="traffic", config_revision=1, **{**draft()["review"], **changes}).model_dump()


def decision(index):
    return {"schema_version": 1, "kind": "d1.decision", "id": f"decision-{index}",
            "stream_id": "traffic", "config_revision": 1, "captured_at": 1000 + index * 2,
            "answer": {"noul": .99}, "question": draft()["question"]}


@pytest.mark.parametrize("changes", [
    {"field": "confidence"}, {"operator": "eq", "value": 1},
    {"operator": "gte", "value": .4}, {"operator": "lte", "value": .8},
])
def test_unsupported_review_trigger_rejected_before_saving(tmp_path, changes):
    value = draft()
    value["review"].update(changes)
    with pytest.raises(ValueError):
        assistant.MonitorDraft.model_validate(value)
    store = Store(tmp_path / "state.sqlite")
    with TestClient(analytics.create_app(store, start_workers=False)) as client:
        response = client.put("/v1/rules/queue", json={
            "stream_id": "traffic", "config_revision": 1, **value["review"]})
    assert response.status_code == 422
    assert store.configs("rule") == []


@pytest.mark.parametrize("operator,value,expected", [("gte", .85, "yes"), ("lte", .15, "no")])
def test_valid_yes_no_conditions_still_reach_verification(operator, value, expected):
    proposed = draft()
    proposed["review"].update(operator=operator, value=value)
    saved = assistant.MonitorDraft.model_validate(proposed)
    result = candidate_condition({"rule": saved.review.model_dump(), "trigger": {"question": saved.question.model_dump()}})
    assert result["expected_answer"] == expected


@pytest.mark.parametrize("change", [{"field": "confidence"}, {"operator": "eq", "value": 1}])
def test_legacy_unverifiable_rule_is_paused_and_does_not_block_other_rules(tmp_path, change):
    store = Store(tmp_path / "state.sqlite")
    store.put_config("rule", "legacy", {**rule(), **change})
    store.put_config("rule", "valid", rule())
    result = process_decision(store, decision(1), position=ConsumerPosition(DECISIONS, 0, 10))
    assert [event["rule_id"] for event in result] == ["valid"]
    legacy = next(row for row in store.configs("rule") if row["id"] == "legacy")
    assert legacy["config"]["enabled"] is False
    assert legacy["validation_error"]
    assert store.rows("SELECT offset FROM decision_offsets")[0]["offset"] == 10
    process_decision(store, decision(2), position=ConsumerPosition(DECISIONS, 0, 11))
    assert next(row for row in store.configs("rule") if row["id"] == "legacy")["revision"] == legacy["revision"]
    store.put_config("rule", "legacy", rule())
    assert "validation_error" not in next(row for row in store.configs("rule") if row["id"] == "legacy")


@pytest.mark.parametrize("question", [None, {"instructions": "Missing labels", "type": "score"},
                                    {"instructions": "Choose", "type": "choice", "criteria": {"a": "Queue", "b": "Flow"}}])
def test_bad_event_question_does_not_pause_good_rule_and_breaks_only_streak(tmp_path, question):
    store = Store(tmp_path / "state.sqlite")
    store.put_config("rule", "queue", rule(consecutive_samples=2))
    process_decision(store, decision(1), position=ConsumerPosition(DECISIONS, 0, 1))
    malformed = decision(2)
    if question is None:
        malformed.pop("question")
    else:
        malformed["question"] = question
    assert process_decision(store, malformed, position=ConsumerPosition(DECISIONS, 0, 2)) == []
    saved = store.configs("rule")[0]
    assert saved["revision"] == 1 and saved["config"]["enabled"] is True
    assert "validation_error" not in saved
    assert store.rows("SELECT offset FROM decision_offsets")[0]["offset"] == 2
    assert process_decision(store, decision(3), position=ConsumerPosition(DECISIONS, 0, 3)) == []
    result = process_decision(store, decision(4), position=ConsumerPosition(DECISIONS, 0, 4))
    assert len(result) == 1 and result[0]["decision_ids"] == ["decision-3", "decision-4"]


def test_bad_event_metadata_does_not_reset_cooldown_or_clear_existing_incident(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    store.put_config("rule", "queue", rule(cooldown_seconds=60))
    process_decision(store, decision(1), position=ConsumerPosition(DECISIONS, 0, 1))
    original_state = json.loads(store.rows("SELECT body FROM rule_state")[0]["body"])
    original_episode = store.rows("SELECT * FROM incident_episodes")
    malformed = decision(2)
    malformed.pop("question")
    process_decision(store, malformed, position=ConsumerPosition(DECISIONS, 0, 2))
    state = json.loads(store.rows("SELECT body FROM rule_state")[0]["body"])
    assert state["fired"] == original_state["fired"]
    assert state["last"] == original_state["last"]
    assert store.rows("SELECT * FROM incident_episodes") == original_episode
    assert process_decision(store, decision(3), position=ConsumerPosition(DECISIONS, 0, 3)) == []


def test_bad_question_breaks_pending_negative_clear_evidence(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    store.put_config("rule", "queue", rule(cooldown_seconds=60))
    process_decision(store, decision(1), position=ConsumerPosition(DECISIONS, 0, 1))
    for index in range(2, 6):
        event = decision(index)
        event["answer"] = {"noul": .1}
        process_decision(store, event, position=ConsumerPosition(DECISIONS, 0, index))
    malformed = decision(6)
    malformed.pop("question")
    process_decision(store, malformed, position=ConsumerPosition(DECISIONS, 0, 6))
    condition = json.loads(store.rows("SELECT body FROM incident_conditions")[0]["body"])
    assert condition["clear_count"] == 0 and condition["clear_start"] is None
    assert store.rows("SELECT ended_at FROM incident_episodes")[0]["ended_at"] is None
    event = decision(7)
    event["answer"] = {"noul": .1}
    process_decision(store, event, position=ConsumerPosition(DECISIONS, 0, 7))
    assert store.rows("SELECT ended_at FROM incident_episodes")[0]["ended_at"] is None
    assert json.loads(store.rows("SELECT body FROM incident_conditions")[0]["body"])["clear_count"] == 1
    # A fresh uninterrupted negative window can still close the same episode.
    for index in range(8, 13):
        event = decision(index)
        event["answer"] = {"noul": .1}
        process_decision(store, event, position=ConsumerPosition(DECISIONS, 0, index))
    assert store.rows("SELECT ended_at FROM incident_episodes")[0]["ended_at"] == decision(12)["captured_at"]


@pytest.mark.parametrize("initial_enabled", [True, False])
@pytest.mark.parametrize("stream_changed_before_failure", [True, False])
def test_apply_retry_after_restart_restores_original_enablement(tmp_path, monkeypatch, initial_enabled, stream_changed_before_failure):
    path = tmp_path / "state.sqlite"
    store = Store(path)
    store.put_config("rule", "queue", rule(enabled=initial_enabled))
    current = {"id": "traffic", "revision": 1, "config": {
        "rtsp_url": "rtsp://camera.invalid/live", "question": draft()["question"], "enabled": True}}

    class FakeIngest:
        fail = True
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url):
            return httpx.Response(200, request=httpx.Request("GET", url), json=[current])
        def put(self, url, json, headers):
            assert store.configs("rule")[0]["config"]["enabled"] is False
            assert headers["If-Match"] == str(current["revision"])
            if not self.fail or stream_changed_before_failure:
                current.update(revision=current["revision"] + 1, config=json)
            if self.fail:
                raise httpx.ConnectError("Simulated failure; no network")
            return httpx.Response(200, request=httpx.Request("PUT", url), json=current)

    fake = FakeIngest()
    for fail in (True, True, False):
        fake.fail = fail
        # Reopen the state and construct a fresh router: recovery must survive
        # both repeated failures and a process restart, not just a local variable.
        store.db.close()
        store = Store(path)
        with TestClient(analytics.create_app(store, start_workers=False)) as client:
            with monkeypatch.context() as patch:
                patch.setattr(assistant.httpx, "Client", lambda **kwargs: fake)
                result = client.post("/v1/assistant/apply", json={
                    "stream_id": "traffic", "rule_id": "queue", "stream_revision": current["revision"],
                    "rule_revision": store.configs("rule")[0]["revision"], "draft": draft()})
        assert result.status_code == (503 if fail else 200)
        assert store.configs("rule")[0]["config"]["enabled"] is (False if fail else initial_enabled)
    assert store.rows("SELECT * FROM rule_apply_recovery") == []
    assert store.configs("rule")[0]["config"]["config_revision"] == current["revision"]


def test_explicit_operator_edit_supersedes_failed_apply_intent(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    store.put_config("rule", "queue", rule())
    assert store.pause_rule_for_apply("queue") is True
    store.put_config("rule", "queue", rule(enabled=False))
    assert store.rows("SELECT * FROM rule_apply_recovery") == []
    assert store.pause_rule_for_apply("queue") is False


def test_health_distinguishes_configuration_from_temporary_outage(tmp_path, monkeypatch):
    store = Store(tmp_path / "state.sqlite")
    with TestClient(analytics.create_app(store, start_workers=False)) as client:
        monkeypatch.setenv("NEMOTRON_URL", "http://assistant.invalid")
        def offline(*args, **kwargs): raise httpx.ConnectError("Offline")
        monkeypatch.setattr(assistant.httpx, "get", offline)
        response = client.get("/v1/assistant/health").json()
        assert response["configured"] is True and response["ready"] is False
        monkeypatch.delenv("NEMOTRON_URL")
        response = client.get("/v1/assistant/health").json()
        assert response["configured"] is False and response["ready"] is False


def test_offsets_and_jobs_roll_back_together_then_replay_is_suppressed(tmp_path, monkeypatch):
    store = Store(tmp_path / "state.sqlite")
    store.put_config("rule", "queue", rule())
    original = store.enqueue
    def fail(*args): raise OSError("Simulated storage failure")
    monkeypatch.setattr(store, "enqueue", fail)
    with pytest.raises(OSError):
        process_decision(store, decision(1), position=ConsumerPosition(DECISIONS, 0, 91))
    assert store.rows("SELECT * FROM decision_offsets") == []
    assert store.rows("SELECT * FROM recent_decisions") == []
    assert store.rows("SELECT * FROM jobs") == []
    monkeypatch.setattr(store, "enqueue", original)
    assert len(process_decision(store, decision(1), position=ConsumerPosition(DECISIONS, 0, 91))) == 1
    assert process_decision(store, decision(1), position=ConsumerPosition(DECISIONS, 0, 91)) == []
    assert len(store.rows("SELECT * FROM jobs")) == 1


def test_bounded_ids_and_durable_partition_watermarks_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "RECENT_DECISION_LIMIT", 8)
    path = tmp_path / "state.sqlite"
    store = Store(path)
    for index in range(50):
        process_decision(store, decision(index), position=ConsumerPosition(DECISIONS, 0, index))
    assert len(store.rows("SELECT * FROM recent_decisions")) == 8
    assert store.rows("SELECT * FROM consumed") == []
    store.db.close()
    store = Store(path)
    store.put_config("rule", "queue", rule())
    # The original ID is already outside the cache, and a new rule has no old
    # rule-state timestamp to protect it. The broker watermark must stop replay.
    assert process_decision(store, decision(0), position=ConsumerPosition(DECISIONS, 0, 0)) == []
    assert store.rows("SELECT * FROM jobs") == []
    assert len(process_decision(store, decision(51), position=ConsumerPosition(DECISIONS, 1, 0))) == 1
    # A duplicate producer ID at a later offset advances the watermark without
    # creating another candidate, so that offset also remains safe to replay.
    assert process_decision(store, decision(51), position=ConsumerPosition(DECISIONS, 1, 1)) == []
    assert store.rows("SELECT offset FROM decision_offsets WHERE partition=1")[0]["offset"] == 1


def test_upgrade_keeps_legacy_deduplication_without_growing_it(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE consumed(id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO consumed VALUES('decision-1')")
    store = Store(path)
    store.put_config("rule", "queue", rule())
    assert process_decision(store, decision(1), position=ConsumerPosition(DECISIONS, 0, 100)) == []
    assert len(process_decision(store, decision(2), position=ConsumerPosition(DECISIONS, 0, 101))) == 1
    assert store.rows("SELECT * FROM consumed") == [{"id": "decision-1"}]
    assert store.rows("SELECT offset FROM decision_offsets")[0]["offset"] == 101


def test_consumer_commits_only_after_sqlite_and_retries_broker_ack_safely(tmp_path, monkeypatch):
    store = Store(tmp_path / "state.sqlite")
    store.put_config("rule", "queue", rule())
    class Stop:
        stopped = False
        def is_set(self): return self.stopped
        def wait(self, seconds): return self.stopped
    stop = Stop()
    class Message:
        def error(self): return None
        def value(self): return json.dumps(decision(1)).encode()
        def topic(self): return DECISIONS
        def partition(self): return 2
        def offset(self): return 91
    class Consumer:
        commits = 0
        closed = False
        def poll(self, _timeout): return Message()
        def commit(self, **kwargs):
            assert store.rows("SELECT offset FROM decision_offsets")[0]["offset"] == 91
            assert len(store.rows("SELECT * FROM jobs")) == 1
            self.commits += 1
            if self.commits == 1:
                raise RuntimeError("Simulated broker acknowledgement failure")
            stop.stopped = True
        def close(self): self.closed = True
    consumer = Consumer()
    monkeypatch.setattr(analytics, "consumer", lambda *args: consumer)
    analytics.consume_decisions(store, stop)
    assert consumer.commits == 2 and consumer.closed
    assert len(store.rows("SELECT * FROM jobs")) == 1
