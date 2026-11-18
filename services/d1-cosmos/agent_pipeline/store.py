# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SQLite durability for config, rule state, review jobs, and Kafka outbox."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


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
        return [{"id": r["id"], "revision": r["revision"], "config": json.loads(r["body"])} for r in self.rows("SELECT * FROM config WHERE kind=?", (kind,))]

    def put_config(self, kind, key, body):
        with self.transaction() as db:
            row = db.execute("SELECT revision,body FROM config WHERE kind=? AND id=?", (kind, key)).fetchone()
            revision = row[0] + 1 if row else 1
            if kind == "stream" and row:
                old = json.loads(row[1])
                # Legacy configurations are image inputs. Adding explicit default
                # fields must not invalidate their already-paired rule revision.
                defaults = {"input_mode": "image", "video_frames": 8, "video_fps": 1}
                semantic_fields = ("question", "rtsp_url", "vios_sensor", *defaults)
                if all(old.get(k, defaults.get(k)) == body.get(k, defaults.get(k)) for k in semantic_fields):
                    revision = row[0]
            db.execute("INSERT OR REPLACE INTO config VALUES(?,?,?,?)", (kind, key, revision, json.dumps(body)))
            if kind == "rule":
                from .incidents import reconcile_rule
                reconcile_rule(db, key, body)
                db.execute("DELETE FROM rule_state WHERE id=?", (key,))
        return revision

    @staticmethod
    def enqueue(db, topic, event):
        db.execute("INSERT OR IGNORE INTO outbox VALUES(?,?,?,?)", (event["id"], topic, event["stream_id"], json.dumps(event, allow_nan=False)))

    def publish_later(self, topic, event):
        with self.transaction() as db:
            self.enqueue(db, topic, event)
