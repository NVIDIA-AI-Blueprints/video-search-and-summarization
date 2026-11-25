# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SQLite durability for config, rule state, review jobs, and Kafka outbox."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

RECENT_DECISION_LIMIT = 4096


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS config(kind TEXT, id TEXT, revision INTEGER, body TEXT, PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, topic TEXT, key TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS consumed(id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS decision_offsets(topic TEXT, partition INTEGER, offset INTEGER NOT NULL,
                PRIMARY KEY(topic,partition));
            CREATE TABLE IF NOT EXISTS recent_decisions(id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS rule_apply_recovery(rule_id TEXT PRIMARY KEY,
                paused_revision INTEGER NOT NULL, intended_enabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS rule_validation_errors(rule_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL, message TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rule_state(id TEXT PRIMARY KEY, body TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, due REAL, status TEXT, attempts INTEGER, body TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS review_diagnostics(event_id TEXT PRIMARY KEY, created_at REAL NOT NULL,
                body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS incident_conditions(condition_key TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS incident_episodes(id TEXT PRIMARY KEY, condition_key TEXT NOT NULL, rule_id TEXT NOT NULL,
                stream_id TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL, body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS incident_episodes_stream ON incident_episodes(stream_id, started_at);
            CREATE TABLE IF NOT EXISTS incident_candidates(event_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL,
                verdict TEXT, review TEXT);
            CREATE INDEX IF NOT EXISTS incident_candidates_episode ON incident_candidates(incident_id);
        """)

    @contextmanager
    def transaction(self):
        with self.lock, self.db:
            yield self.db

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(x) for x in self.db.execute(sql, args).fetchall()]

    def configs(self, kind):
        with self.lock:
            result = [{"id": r["id"], "revision": r["revision"], "config": json.loads(r["body"])} for r in self.rows("SELECT * FROM config WHERE kind=?", (kind,))]
            if kind == "rule":
                errors = {r["rule_id"]: r for r in self.rows("SELECT * FROM rule_validation_errors")}
                for row in result:
                    error = errors.get(row["id"])
                    if error and error["revision"] == row["revision"]:
                        row["validation_error"] = error["message"]
            return result

    def put_config(self, kind, key, body):
        with self.transaction() as db:
            return self._put_config(db, kind, key, body)

    @staticmethod
    def _put_config(db, kind, key, body):
        row = db.execute("SELECT revision,body FROM config WHERE kind=? AND id=?", (kind, key)).fetchone()
        revision = row[0] + 1 if row else 1
        if kind == "stream" and row:
            old = json.loads(row[1])
            # Explicit defaults must not invalidate an already paired rule.
            defaults = {"input_mode": "image", "video_frames": 8, "video_fps": 1}
            semantic_fields = ("question", "rtsp_url", "vios_sensor", *defaults)
            if all(old.get(k, defaults.get(k)) == body.get(k, defaults.get(k)) for k in semantic_fields):
                revision = row[0]
        db.execute("INSERT OR REPLACE INTO config VALUES(?,?,?,?)", (kind, key, revision, json.dumps(body)))
        if kind == "rule":
            from .incidents import reconcile_rule
            reconcile_rule(db, key, body)
            db.execute("DELETE FROM rule_state WHERE id=?", (key,))
            # A successful apply or an explicit operator edit supersedes recovery.
            db.execute("DELETE FROM rule_apply_recovery WHERE rule_id=?", (key,))
            db.execute("DELETE FROM rule_validation_errors WHERE rule_id=?", (key,))
        return revision

    def pause_rule_for_apply(self, rule_id):
        """Atomically remember enablement and pause before a non-atomic HTTP update.

        A retry can recover the original intent after a process restart. Any
        intervening operator edit removes that recovery record via put_config.
        """
        with self.transaction() as db:
            row = db.execute("SELECT revision,body FROM config WHERE kind='rule' AND id=?", (rule_id,)).fetchone()
            if row is None:
                raise ValueError("Rule no longer exists")
            body = json.loads(row["body"])
            recovery = db.execute("SELECT * FROM rule_apply_recovery WHERE rule_id=?", (rule_id,)).fetchone()
            intended = (bool(recovery["intended_enabled"])
                        if recovery and recovery["paused_revision"] == row["revision"]
                        else body.get("enabled", True))
            revision = self._put_config(db, "rule", rule_id, {**body, "enabled": False})
            db.execute("INSERT OR REPLACE INTO rule_apply_recovery VALUES(?,?,?)", (rule_id, revision, int(intended)))
            return intended

    def pause_invalid_rule(self, db, row, message):
        """Keep an upgrade/configuration error visible without poisoning Kafka."""
        body = json.loads(row["body"])
        revision = self._put_config(db, "rule", row["id"], {**body, "enabled": False})
        db.execute("INSERT OR REPLACE INTO rule_validation_errors VALUES(?,?,?)",
                   (row["id"], revision, message[:500]))

    @staticmethod
    def decision_seen(db, event_id, position=None):
        if position is not None:
            row = db.execute("SELECT offset FROM decision_offsets WHERE topic=? AND partition=?",
                             (position.topic, position.partition)).fetchone()
            if row is not None and position.offset <= row["offset"]:
                return True
        # The pre-upgrade ID table is frozen, not pruned: old deduplication is
        # preserved even if an upgrade starts from an earlier broker position.
        return bool(db.execute("SELECT 1 FROM consumed WHERE id=?", (event_id,)).fetchone()
                    or db.execute("SELECT 1 FROM recent_decisions WHERE id=?", (event_id,)).fetchone())

    @staticmethod
    def record_decision(db, event_id, position=None):
        if position is not None:
            db.execute("INSERT INTO decision_offsets VALUES(?,?,?) ON CONFLICT(topic,partition) "
                       "DO UPDATE SET offset=max(offset,excluded.offset)",
                       (position.topic, position.partition, position.offset))
        # Bounded protection against duplicate producer IDs at newer offsets;
        # durable partition watermarks protect Kafka replay after IDs age out.
        # Offline callers without broker metadata get this bounded ID window.
        db.execute("INSERT OR IGNORE INTO recent_decisions VALUES(?)", (event_id,))
        db.execute("DELETE FROM recent_decisions WHERE rowid <= "
                   "(SELECT max(rowid) FROM recent_decisions) - ?", (RECENT_DECISION_LIMIT,))

    @staticmethod
    def enqueue(db, topic, event):
        db.execute("INSERT OR IGNORE INTO outbox VALUES(?,?,?,?)", (event["id"], topic, event["stream_id"], json.dumps(event, allow_nan=False)))

    def publish_later(self, topic, event):
        with self.transaction() as db:
            self.enqueue(db, topic, event)
