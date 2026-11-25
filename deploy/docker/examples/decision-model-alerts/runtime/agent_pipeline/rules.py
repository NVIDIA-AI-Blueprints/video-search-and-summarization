# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic, replay-safe rules using capture time rather than consumer time."""
import json
import logging
import uuid
from pydantic import ValidationError
from .contracts import Rule, matches
from .bus import EVENTS
from .incidents import attach_candidate, observe_decision
from .verification import candidate_condition, VerificationFormatError

LOG = logging.getLogger(__name__)


def process_decision(store, event, *, position=None):
    """Commit effects and broker position together; callers commit Kafka after this.

    Kafka delivers each partition in order. Replays at or below its durable
    watermark stay suppressed even after the bounded producer-ID cache expires.
    Direct/offline callers may omit position and use the bounded ID cache only.
    """
    if event.get("schema_version") != 1 or event.get("kind") != "d1.decision":
        raise ValueError("Unsupported decision schema")
    with store.transaction() as db:
        if store.decision_seen(db, event["id"], position):
            store.record_decision(db, event["id"], position)
            return []
        triggered = []
        for row in db.execute("SELECT * FROM config WHERE kind='rule'").fetchall():
            raw_rule = json.loads(row["body"])
            if not raw_rule.get("enabled", True) or raw_rule.get("stream_id") != event["stream_id"]:
                continue
            try:
                rule = Rule.model_validate(raw_rule)
            except ValidationError as exc:
                message = "; ".join(error["msg"] for error in exc.errors(include_input=False))
                store.pause_invalid_rule(db, row, message)
                LOG.warning("Rule %s paused: its trigger cannot be verified", row["id"])
                continue
            previous = db.execute("SELECT body FROM rule_state WHERE id=?", (row["id"],)).fetchone()
            state = json.loads(previous[0]) if previous else {"last": 0, "fired": None, "ids": []}
            now = event["captured_at"]
            if now <= state["last"]:
                continue
            if rule.config_revision == event["config_revision"]:
                try:
                    candidate_condition({"rule": rule.model_dump(), "trigger": event})
                except VerificationFormatError:
                    # The message's saved question is not authoritative rule
                    # configuration. Missing/legacy/mismatched metadata must
                    # not disable a valid rule for all subsequent messages.
                    # Break the positive streak without clearing an incident,
                    # resetting cooldown or pretending this was a negative.
                    state["ids"] = []
                    db.execute("INSERT OR REPLACE INTO rule_state VALUES(?,?)", (row["id"], json.dumps(state)))
                    # Unknown evidence also interrupts a pending negative-clear
                    # streak. Use the same unknown-answer transition as missing
                    # model output; never interpret this message's answer when
                    # its saved question cannot be verified.
                    observe_decision(db, row["id"], rule, {**event, "answer": {}})
                    LOG.warning("Decision %s ignored for rule %s: question metadata cannot be verified",
                                event["id"], row["id"])
                    continue
            if observe_decision(db, row["id"], rule, event):
                # The old condition cleared. Its cooldown must not suppress the
                # first candidate of a genuinely new episode.
                state["fired"] = None
            if now - state["last"] > rule.max_gap_seconds:
                state["ids"] = []
            state["last"] = now
            if event["config_revision"] != rule.config_revision or not matches(rule, event["answer"]):
                state["ids"] = []
            else:
                state["ids"].append(event["id"])
                state["ids"] = state["ids"][-rule.consecutive_samples:]
                cooled = state["fired"] is None or now - state["fired"] >= rule.cooldown_seconds
                if len(state["ids"]) == rule.consecutive_samples and cooled:
                    key = str(uuid.uuid5(uuid.NAMESPACE_URL, f'{row["id"]}:{row["revision"]}:{event["id"]}'))
                    incident = {"schema_version": 1, "kind": "d1.event", "id": key,
                        "stream_id": event["stream_id"], "captured_at": now,
                        "rule_id": row["id"], "rule_revision": row["revision"], "rule": rule.model_dump(),
                        "decision_ids": list(state["ids"]), "trigger": event,
                        "clip_start": now - rule.pre_seconds, "clip_end": now + rule.post_seconds}
                    attach_candidate(db, incident)
                    store.enqueue(db, EVENTS, incident)
                    db.execute("INSERT OR IGNORE INTO jobs VALUES(?,?,?,0,?,NULL)",
                               (key, incident["clip_end"] + 2, "pending", json.dumps(incident)))
                    state["fired"] = now
                    state["ids"] = []
                    triggered.append(incident)
            db.execute("INSERT OR REPLACE INTO rule_state VALUES(?,?)", (row["id"], json.dumps(state)))
        store.record_decision(db, event["id"], position)
        return triggered
