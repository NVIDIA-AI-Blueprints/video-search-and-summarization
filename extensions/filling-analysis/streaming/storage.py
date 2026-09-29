# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Small durable session/event log; frames remain in a bounded live buffer."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class EventStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, stream_id TEXT, status TEXT, data TEXT);
          CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE,
              session_id TEXT, stream_id TEXT, data TEXT);
          CREATE INDEX IF NOT EXISTS event_session ON events(session_id, seq);
        """)
        # Do not pretend an interrupted decoder is still monitoring after restart.
        for sid, data in self.db.execute("SELECT id,data FROM sessions WHERE status IN ('starting','connecting','running','reconnecting','stopping')").fetchall():
            state = json.loads(data)
            state.update(status="interrupted", error="Live worker restarted; explicit start is required")
            self.db.execute("UPDATE sessions SET status=?,data=? WHERE id=?", ("interrupted", json.dumps(state), sid))
        self.db.commit()

    def save_session(self, state):
        with self.lock:
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data",
                            (state["session_id"], state["stream_id"], state["status"], json.dumps(state, allow_nan=False)))
            self.db.commit()

    def get_session(self, session_id):
        with self.lock:
            row = self.db.execute("SELECT data FROM sessions WHERE id=?", (session_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def find_request(self, request_id):
        if not request_id:
            return None
        with self.lock:
            row = self.db.execute("SELECT data FROM sessions WHERE json_extract(data, '$.request_id')=? LIMIT 1",
                                  (request_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def append(self, event):
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO events(id,session_id,stream_id,data) VALUES(?,?,?,?)",
                            (event["id"], event["session_id"], event["stream_id"], json.dumps(event, allow_nan=False)))
            self.db.commit()
            seq = self.db.execute("SELECT seq FROM events WHERE id=?", (event["id"],)).fetchone()[0]
            return {**event, "seq": seq}

    def events(self, session_id, after_seq=0, limit=100, latest=False):
        with self.lock:
            order = "DESC" if latest else "ASC"
            rows = self.db.execute(f"SELECT seq,data FROM events WHERE session_id=? AND seq>? ORDER BY seq {order} LIMIT ?",
                                   (session_id, after_seq, min(200, max(1, limit)))).fetchall()
            if latest:
                rows.reverse()
            return [{**json.loads(data), "seq": seq} for seq, data in rows]

    def event(self, session_id, event_id):
        with self.lock:
            row = self.db.execute("SELECT seq,data FROM events WHERE session_id=? AND id=?", (session_id, event_id)).fetchone()
            return {**json.loads(row[1]), 'seq': row[0]} if row else None

    def history(self, session_id):
        """Recover immutable per-epoch identities and the highest cycle label."""
        import re
        epochs, maximum_cycle = {}, 0
        with self.lock:
            rows = self.db.execute("SELECT data FROM events WHERE session_id=? ORDER BY seq", (session_id,)).fetchall()
        for (data,) in rows:
            event = json.loads(data)
            entry = {'epoch': event['epoch'], 'measurement': event['measurement'], 'clock': event['clock']}
            if entry['epoch'] in epochs and epochs[entry['epoch']] != entry:
                raise ValueError('Conflicting historical measurement identity in one connection epoch')
            epochs[entry['epoch']] = entry
            match = re.search(r':cycle-(\d+)$', event.get('track_id', ''))
            if match:
                maximum_cycle = max(maximum_cycle, int(match.group(1)))
        return list(epochs.values()), maximum_cycle
