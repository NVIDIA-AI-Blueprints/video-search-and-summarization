# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Durable candidate-to-incident projection, independent of the ES search index.

Only a validated Cosmos confirmation promotes an episode to an operator incident.
Repeated candidate clips remain available as evidence within that same episode.
Capture gaps do not resolve incidents: closure requires sustained negative D1
observations. Closing monitoring is explicitly different from observing a clear
condition. All write helpers accept the caller's transaction.
"""
import hashlib
import json
import math
import time
import uuid

from fastapi import APIRouter, Query

from .clips import clip_path
from .contracts import Rule, matches
from .verification import Verification

CLEAR_CHECKS = 5
CLEAR_SECONDS = 10.0
VERDICTS = {"confirmed", "rejected", "uncertain"}


def condition_key(rule_id, rule):
    """Prompt formatting and cooldown do not create a new monitored condition."""
    if isinstance(rule, Rule):
        rule = rule.model_dump()
    semantic = {name: rule.get(name) for name in (
        "stream_id", "config_revision", "field", "probability_key", "operator", "value")}
    semantic["rule_id"] = rule_id
    return hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()[:32]


def _read(db, table, key_name, key):
    row = db.execute(f"SELECT body FROM {table} WHERE {key_name}=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _save_condition(db, key, state):
    db.execute("INSERT OR REPLACE INTO incident_conditions VALUES(?,?)", (key, json.dumps(state)))


def _save_episode(db, episode):
    db.execute("INSERT OR REPLACE INTO incident_episodes VALUES(?,?,?,?,?,?,?)", (
        episode["id"], episode["condition_key"], episode["rule_id"], episode["stream_id"],
        episode["started_at"], episode["closed_at"], json.dumps(episode)))


def _close(db, episode, when, reason):
    if episode["closed_at"] is None:
        episode.update(status="closed", closed_at=when, close_reason=reason)
        _save_episode(db, episode)


def reconcile_rule(db, rule_id, body=None):
    """End old monitoring when its rule is removed, paused, or changes meaning.

    This does not claim that the scene became clear. A later re-enable starts a
    new episode because the unobserved interval must not be bridged as evidence.
    """
    new_key = condition_key(rule_id, body) if body is not None else None
    for row in db.execute("SELECT condition_key,body FROM incident_conditions WHERE json_extract(body,'$.rule_id')=?", (rule_id,)).fetchall():
        if body and body.get("enabled", True) and new_key == row["condition_key"]:
            continue
        state = json.loads(row["body"])
        state.update(episode_id=None, positive_start=None, clear_count=0, clear_start=None)
        _save_condition(db, row["condition_key"], state)
    for row in db.execute("SELECT body FROM incident_episodes WHERE rule_id=? AND ended_at IS NULL", (rule_id,)).fetchall():
        episode = json.loads(row[0])
        if body and body.get("enabled", True) and new_key == episode["condition_key"]:
            continue
        reason = "monitoring_paused" if body and not body.get("enabled", True) else "configuration_changed"
        _close(db, episode, time.time(), reason)
        state = _read(db, "incident_conditions", "condition_key", episode["condition_key"])
        if state:
            state.update(episode_id=None, positive_start=None, clear_count=0, clear_start=None)
            _save_condition(db, episode["condition_key"], state)


def _valid_answer(rule, answer):
    value = answer.get("probabilities", {}).get(rule.probability_key) if rule.field == "probability" else answer.get(rule.field)
    if rule.field == "choice":
        return isinstance(value, str) and bool(value)
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def clearly_negative(rule, answer):
    """Use a separate clear threshold for probability-valued conditions.

    Scores and choices have no universal calibrated scale, so those retain the
    configured comparison and still require sustained negative observations.
    """
    value = answer.get("probabilities", {}).get(rule.probability_key) if rule.field == "probability" else answer.get(rule.field)
    if rule.field in ("noul", "probability", "confidence"):
        if rule.operator == "gte":
            return value <= min(0.4, rule.value - 0.2)
        if rule.operator == "lte":
            return value >= max(0.6, rule.value + 0.2)
    return not matches(rule, answer)


def observe_decision(db, rule_id, rule, event):
    """Observe positives and negatives, including samples suppressed by cooldown.

    Returns True when sustained negative evidence closes an episode, allowing a
    subsequent new episode to trigger without waiting out the old cooldown.
    """
    if event.get("config_revision") != rule.config_revision:
        return False
    key, now = condition_key(rule_id, rule), event["captured_at"]
    state = _read(db, "incident_conditions", "condition_key", key) or {
        "last": 0, "episode_id": None, "positive_start": None,
        "clear_count": 0, "clear_start": None,
    }
    state["rule_id"] = rule_id
    if now <= state["last"]:
        return False
    gap = state["last"] > 0 and now - state["last"] > rule.max_gap_seconds
    previous_check = state["last"]
    if gap:
        state.update(clear_count=0, clear_start=None)
        if state["episode_id"] is None:
            state["positive_start"] = None
    state["last"] = now
    episode = _read(db, "incident_episodes", "id", state["episode_id"]) if state["episode_id"] else None
    if episode and gap:
        episode["observation_gap_count"] = episode.get("observation_gap_count", 0) + 1
        episode["last_observation_gap"] = {"after": previous_check, "before": now}
        _save_episode(db, episode)
    # Missing or malformed model answers are not observations that the road cleared.
    if not _valid_answer(rule, event.get("answer", {})):
        state.update(clear_count=0, clear_start=None)
        _save_condition(db, key, state)
        return False
    positive = matches(rule, event["answer"])
    if episode:
        episode["last_checked"] = now
    closed = False
    if positive:
        state.update(clear_count=0, clear_start=None)
        if state["positive_start"] is None:
            state["positive_start"] = now
        if episode:
            episode["last_seen"] = now
    elif clearly_negative(rule, event["answer"]):
        state["clear_count"] += 1
        state["clear_start"] = state["clear_start"] if state["clear_start"] is not None else now
        if episode is None:
            state["positive_start"] = None
        elif state["clear_count"] >= CLEAR_CHECKS and now - state["clear_start"] >= CLEAR_SECONDS:
            _close(db, episode, now, "condition_cleared")
            state.update(episode_id=None, positive_start=None, clear_count=0, clear_start=None)
            closed = True
    else:
        # Near-threshold scores support neither a new positive nor a clear road.
        state.update(clear_count=0, clear_start=None)
        if episode is None:
            state["positive_start"] = None
    if episode:
        _save_episode(db, episode)
    _save_condition(db, key, state)
    return closed


def attach_candidate(db, event, historical=False):
    """Link one deterministic candidate to its episode before enqueuing its job."""
    previous = db.execute("SELECT incident_id FROM incident_candidates WHERE event_id=?", (event["id"],)).fetchone()
    if previous:
        event["incident_id"] = previous[0]
        return previous[0]
    rule = event["rule"]
    key, now = condition_key(event["rule_id"], rule), event["captured_at"]
    state = _read(db, "incident_conditions", "condition_key", key)
    episode = None
    if state and state.get("episode_id") and not historical:
        episode = _read(db, "incident_episodes", "id", state["episode_id"])
    if episode is None or episode["closed_at"] is not None:
        episode_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"d1-incident:{key}:{event['id']}"))
        episode = {
            "id": episode_id, "condition_key": key, "rule_id": event["rule_id"],
            "stream_id": event["stream_id"], "config_revision": rule["config_revision"],
            "question": event["trigger"].get("question"), "status": "closed" if historical else "active",
            "started_at": (state.get("positive_start") if state and not historical else None) or now,
            "last_seen": now, "last_checked": now, "closed_at": now if historical else None,
            "close_reason": "historical_review" if historical else None,
            "max_gap_seconds": rule.get("max_gap_seconds", 10),
            "observation_gap_count": 0, "last_observation_gap": None,
        }
        if not historical:
            state = state or {"last": now, "positive_start": now, "clear_count": 0, "clear_start": None}
            state["rule_id"] = event["rule_id"]
            state["episode_id"] = episode_id
            _save_condition(db, key, state)
        _save_episode(db, episode)
    event["incident_id"] = episode["id"]
    db.execute("INSERT INTO incident_candidates VALUES(?,?,NULL,NULL)", (event["id"], episode["id"]))
    return episode["id"]


def record_review(db, review):
    """Persist verified evidence in the same transaction as review publication.

    Legacy captions can be retained but can never promote an incident. Unexpected
    verification output raises rather than silently recording a successful alert.
    """
    event_id = review.get("event_id")
    if not event_id:
        raise ValueError("Review must identify its candidate event")
    row = db.execute("SELECT body FROM jobs WHERE id=?", (event_id,)).fetchone()
    if row is None:
        raise ValueError("Review candidate is not stored")
    event = json.loads(row[0])
    if review.get("stream_id") != event["stream_id"]:
        raise ValueError("Review stream does not match its candidate")
    version = review.get("verification_version")
    verdict = review.get("verdict") if version == 1 else None
    if version is not None and (version != 1 or verdict not in VERDICTS):
        raise ValueError("Invalid verification verdict")
    if version == 1:
        Verification.model_validate({key: review.get(key) for key in ("verdict", "summary", "evidence", "uncertainty")})
    link = db.execute("SELECT incident_id FROM incident_candidates WHERE event_id=?", (event_id,)).fetchone()
    if link is None:
        attach_candidate(db, event, historical=True)
    db.execute("UPDATE incident_candidates SET verdict=?,review=? WHERE event_id=?",
               (verdict, json.dumps(review, allow_nan=False), event_id))


def _candidate(row):
    event = json.loads(row["body"])
    review = json.loads(row["review"]) if row.get("review") else None
    status = row.get("verdict") or ("legacy" if row["status"] == "complete" else row["status"])
    return {
        "event_id": row["id"], "incident_id": row.get("incident_id"),
        "stream_id": event["stream_id"], "captured_at": event["captured_at"],
        "status": status, "event": event, "review": review,
        "clip_ready": clip_path(row["id"]).exists(), "error": row.get("error"),
    }


def snapshot(store, stream_id=None, limit=50, now=None):
    """One consistent snapshot; totals describe all stored candidates in scope.

    JSON decoding and returned evidence are bounded. Counts query SQLite rather
    than loading the entire historical recording/event archive into memory.
    """
    now = time.time() if now is None else now
    scope = " WHERE json_extract(j.body,'$.stream_id')=?" if stream_id else ""
    args = (stream_id,) if stream_id else ()
    join = " FROM jobs j LEFT JOIN incident_candidates c ON c.event_id=j.id"
    with store.lock:
        counts = {name: 0 for name in ("confirmed_incidents", "active_incidents", "candidates", "confirmed", "rejected", "uncertain", "pending", "failed", "legacy")}
        for row in store.rows("""SELECT coalesce(c.verdict, CASE WHEN j.status='complete' THEN 'legacy'
                WHEN j.status='reviewing' THEN 'pending' ELSE j.status END) AS status,count(*) AS n"""
                + join + scope + " GROUP BY 1", args):
            counts[row["status"]] += row["n"]
            counts["candidates"] += row["n"]
        rows = store.rows("SELECT j.*,c.incident_id,c.verdict,c.review" + join + scope
                          + " ORDER BY j.rowid DESC LIMIT ?", (*args, limit))
        episode_scope = " AND e.stream_id=?" if stream_id else ""
        confirmed_scope = """ FROM incident_episodes e WHERE EXISTS
            (SELECT 1 FROM incident_candidates c WHERE c.incident_id=e.id AND c.verdict='confirmed')""" + episode_scope
        for row in store.rows("SELECT (e.ended_at IS NULL) AS active,count(*) AS n" + confirmed_scope + " GROUP BY active", args):
            counts["confirmed_incidents"] += row["n"]
            if row["active"]:
                counts["active_incidents"] += row["n"]
        incidents = []
        episode_rows = store.rows("SELECT e.body" + confirmed_scope
                                  + " ORDER BY (e.ended_at IS NULL) DESC,e.started_at DESC LIMIT ?", (*args, limit))
        for row in episode_rows:
            episode = json.loads(row["body"])
            linked = " WHERE c.incident_id=?"
            latest = store.rows("SELECT j.*,c.incident_id,c.verdict,c.review" + join + linked + " ORDER BY json_extract(j.body,'$.captured_at') DESC LIMIT 1", (episode["id"],))[0]
            newest = store.rows("SELECT j.*,c.incident_id,c.verdict,c.review" + join + linked
                                + " AND c.verdict='confirmed' ORDER BY json_extract(j.body,'$.captured_at') DESC LIMIT 1", (episode["id"],))[0]
            totals = store.rows("SELECT count(*) AS candidates,sum(verdict='confirmed') AS confirmed FROM incident_candidates WHERE incident_id=?", (episode["id"],))[0]
            ids = store.rows("SELECT j.id" + join + linked + " ORDER BY json_extract(j.body,'$.captured_at') DESC LIMIT 200", (episode["id"],))
            representative = _candidate(newest)
            latest_candidate = _candidate(latest)
            if episode["closed_at"] is None:
                monitoring = "stale" if now - episode["last_checked"] > episode["max_gap_seconds"] else "observing"
            else:
                monitoring = {"monitoring_paused": "paused", "configuration_changed": "configuration_changed"}.get(episode["close_reason"], "ended")
            episode.update(
                monitoring_status=monitoring, candidate_count=totals["candidates"], confirmed_count=totals["confirmed"],
                latest_event_id=latest["id"], representative_event_id=newest["id"],
                candidate_ids=[item["id"] for item in ids],
                candidate_history_truncated=totals["candidates"] > len(ids),
                review=representative["review"], event=representative["event"],
                latest_status=latest_candidate["status"], latest_review=latest_candidate["review"],
                latest_candidate_event=latest_candidate["event"],
            )
            incidents.append(episode)
        return {"incidents": incidents, "candidates": [_candidate(row) for row in rows],
                "counts": counts, "policy": {"clear_checks": CLEAR_CHECKS, "clear_seconds": CLEAR_SECONDS,
                    "probability_clear_gte": "at most min(0.4, trigger threshold - 0.2)",
                    "probability_clear_lte": "at least max(0.6, trigger threshold + 0.2)"},
                "generated_at": now}


def router(store):
    routes = APIRouter()

    @routes.get("/v1/incidents")
    def incidents(stream_id: str | None = Query(default=None, pattern=r"^[a-zA-Z0-9_-]{1,64}$"),
                  limit: int = Query(default=50, ge=1, le=100)):
        return snapshot(store, stream_id, limit)

    return routes
