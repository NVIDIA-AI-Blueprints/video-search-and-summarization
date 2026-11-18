# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic, replay-safe rules using capture time rather than consumer time."""
import json
import uuid
from .contracts import Rule, matches
from .bus import EVENTS
from .incidents import attach_candidate, observe_decision


def process_decision(store, event):
    if event.get("schema_version") != 1 or event.get("kind") != "d1.decision":
        raise ValueError("Unsupported decision schema")
    with store.transaction() as db:
        if db.execute("SELECT 1 FROM consumed WHERE id=?", (event["id"],)).fetchone():
            return []
        triggered = []
        for row in db.execute("SELECT * FROM config WHERE kind='rule'").fetchall():
            rule = Rule.model_validate_json(row["body"])
            if not rule.enabled or rule.stream_id != event["stream_id"]:
                continue
            previous = db.execute("SELECT body FROM rule_state WHERE id=?", (row["id"],)).fetchone()
            state = json.loads(previous[0]) if previous else {"last": 0, "fired": None, "ids": []}
            now = event["captured_at"]
            if now <= state["last"]:
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
        db.execute("INSERT INTO consumed VALUES(?)", (event["id"],))
        return triggered
