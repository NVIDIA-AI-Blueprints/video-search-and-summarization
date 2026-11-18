# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import logging
import os
import re
import threading
import time
import tempfile
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from .clips import clip_path, save_clip, LOCK
from .bus import DECISIONS, REVIEWS, consumer, drain_outbox
from .contracts import Rule
from .review import review_event, resolve_clip, ClipEvidenceChanged
from .verification import VerificationFormatError
from .rules import process_decision
from .store import Store
from .incidents import record_review, reconcile_rule
from .video_qa import QuestionQueue
from .inference_scheduler import InferenceCancelled

LOG = logging.getLogger(__name__)


def consume_decisions(store, stop):
    client = consumer("d1-analytics-v1", [DECISIONS])
    try:
        while not stop.is_set():
            message = client.poll(1)
            if message is None:
                continue
            if message.error():
                LOG.error("Kafka consumer error: %s", message.error())
                continue
            # Do not skip failed messages by committing a later offset.
            while not stop.is_set():
                try:
                    process_decision(store, json.loads(message.value()))
                    client.commit(message=message, asynchronous=False)
                    break
                except Exception:
                    LOG.exception("Decision processing failed; offset remains uncommitted")
                    stop.wait(5)
    finally:
        client.close()


def run_reviews(store, stop):
    while not stop.wait(0.5):
        rows = store.rows("SELECT * FROM jobs WHERE status='pending' AND due<=? ORDER BY due LIMIT 1", (time.time(),))
        if not rows:
            continue
        row = rows[0]
        try:
            with store.transaction() as db:
                db.execute("UPDATE jobs SET status='reviewing' WHERE id=?", (row["id"],))
            review = review_event(json.loads(row["body"]), stop=stop)
            review["reviewed_at"] = time.time()
            with store.transaction() as db:
                record_review(db, review)
                store.enqueue(db, REVIEWS, review)
                db.execute("UPDATE jobs SET status='complete',error=NULL WHERE id=?", (row["id"],))
        except InferenceCancelled:
            with store.transaction() as db:
                db.execute("UPDATE jobs SET status='pending' WHERE id=?", (row["id"],))
        except Exception as exc:
            attempts = row["attempts"] + 1
            # review_event already gives malformed model output one specific
            # corrective generation. Replaying that entire job ten times ties
            # up Cosmos without new evidence. Keep the failed candidate visible
            # and manually retryable; never turn it into a rejection.
            terminal = isinstance(exc, (VerificationFormatError, ClipEvidenceChanged)) or attempts >= 10
            status = "failed" if terminal else "pending"
            LOG.warning("Review %s failed (%s); %s", row["id"], type(exc).__name__,
                        "automatic retry budget exhausted" if terminal else "retained for retry")
            with store.transaction() as db:
                db.execute("UPDATE jobs SET attempts=?,due=?,status=?,error=? WHERE id=?",
                    (attempts, time.time() + min(300, 2 ** min(attempts, 8)),
                     status, type(exc).__name__, row["id"]))
                diagnostics = getattr(exc, "diagnostics", None)
                if diagnostics:
                    # Only bounded field/error metadata and response hashes;
                    # raw model text and credentials are not diagnostic logs.
                    db.execute("INSERT OR REPLACE INTO review_diagnostics VALUES(?,?,?)",
                               (row["id"], time.time(), json.dumps(diagnostics, allow_nan=False)))
                    db.execute("DELETE FROM review_diagnostics WHERE event_id NOT IN "
                               "(SELECT event_id FROM review_diagnostics ORDER BY created_at DESC LIMIT 200)")


def create_app(store=None, start_workers=True):
    store = store or Store(os.getenv("D1_ANALYTICS_DB", "results/agent/analytics.sqlite"))
    stop = threading.Event()
    questions = QuestionQueue(store)

    @asynccontextmanager
    async def lifespan(_app):
        if start_workers:
            # Recover jobs interrupted by process shutdown before starting the worker.
            with store.transaction() as db:
                db.execute("UPDATE jobs SET status='pending' WHERE status='reviewing'")
            questions.start()
            for target in (consume_decisions, drain_outbox, run_reviews):
                threading.Thread(target=target, args=(store, stop), daemon=True).start()
        try:
            yield
        finally:
            stop.set()
            questions.stop()

    app = FastAPI(title="D1 Verified Event Analytics", version="0.3.0", lifespan=lifespan)
    app.state.question_queue = questions

    @app.get("/v1/rules")
    def rules():
        return store.configs("rule")

    @app.put("/v1/rules/{rule_id}")
    def configure(rule_id: str, rule: Rule):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", rule_id):
            raise HTTPException(422, "Invalid rule ID")
        revision = store.put_config("rule", rule_id, rule.model_dump())
        return {"id": rule_id, "revision": revision, "config": rule}

    @app.delete("/v1/rules/{rule_id}")
    def remove(rule_id: str):
        with store.transaction() as db:
            reconcile_rule(db, rule_id, None)
            db.execute("DELETE FROM config WHERE kind='rule' AND id=?", (rule_id,))
            db.execute("DELETE FROM rule_state WHERE id=?", (rule_id,))
        return {"removed": rule_id}

    @app.get("/v1/reviews")
    def jobs():
        result = []
        for row in store.rows("SELECT * FROM jobs ORDER BY rowid DESC LIMIT 100"):
            event = json.loads(row.pop("body"))
            row.update({key: event[key] for key in ("stream_id", "captured_at", "clip_start", "clip_end")})
            row["answer"] = event["trigger"]["answer"]
            row["clip_ready"] = clip_path(row["id"]).exists()
            result.append(row)
        return result

    @app.get("/v1/reviews/{event_id}/clip")
    def clip(event_id: str):
        rows = store.rows("SELECT body,status FROM jobs WHERE id=?", (event_id,))
        if not rows:
            raise HTTPException(404, "Event not found")
        path = clip_path(event_id)
        persisted = store.rows("SELECT review FROM incident_candidates WHERE event_id=?", (event_id,))
        review = json.loads(persisted[0]["review"]) if persisted and persisted[0]["review"] else None
        if review and review.get("verification_version") == 1:
            # A new VIOS export of the same requested window is not guaranteed to
            # be the exact bytes that supported this verdict. Verified evidence
            # is immutable: never silently recover or substitute another file.
            digest = review.get("clip_sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise HTTPException(410, "The exact verified event clip is unavailable")
            try:
                with LOCK, path.open("rb") as media:
                    if hashlib.file_digest(media, "sha256").hexdigest() != digest:
                        raise HTTPException(410, "The cached clip no longer matches its verification evidence")
            except OSError as exc:
                raise HTTPException(410, "The exact verified event clip is no longer cached") from exc
            return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})
        if not path.exists():
            if rows[0]["status"] in ("pending", "reviewing"):
                raise HTTPException(409, "Clip is being prepared")
            # Older reviews predate the cache; recover their recorded time window
            # using the configured VSS CLI, never a browser-provided media URL.
            try:
                with LOCK:
                    if not path.exists():
                        metadata = resolve_clip(json.loads(rows[0]["body"]))
                        with httpx.stream("GET", metadata["media_url"], timeout=60) as response, tempfile.TemporaryFile() as media:
                            response.raise_for_status()
                            size = 0
                            for block in response.iter_bytes():
                                size += len(block)
                                if size > 256 * 1024 * 1024:
                                    raise ValueError("Clip too large")
                                media.write(block)
                            if not size:
                                raise ValueError("Empty clip")
                            save_clip(event_id, media)
            except Exception as exc:
                LOG.warning("Clip recovery failed for %s (%s)", event_id, type(exc).__name__)
                raise HTTPException(410, "Recording is no longer available") from exc
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/v1/reviews/{event_id}/retry")
    def retry(event_id: str):
        with store.transaction() as db:
            changed = db.execute("UPDATE jobs SET status='pending',attempts=0,due=? WHERE id=? AND status='failed'", (time.time(), event_id)).rowcount
        if not changed:
            raise HTTPException(404, "Failed review not found")
        return {"queued": event_id}

    @app.get("/v1/health/live")
    def live():
        return {"alive": True, "version": "0.3.0", "verification_version": 1,
                "queued_video_questions": True, "format_retry_policy": "one_corrective_generation",
                "reviews": store.rows("SELECT status,count(*) AS count FROM jobs GROUP BY status")}

    from .assistant import router
    app.include_router(router(store))
    from .incidents import router as incidents_router
    from .video_qa import router as questions_router
    from .demo_sources import router as sources_router
    app.include_router(incidents_router(store))
    app.include_router(questions_router(store, queue=questions))
    app.include_router(sources_router(store))
    return app
