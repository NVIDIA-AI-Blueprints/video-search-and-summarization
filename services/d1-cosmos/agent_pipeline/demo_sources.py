# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Switch between prepared inputs without accepting a browser-supplied URL."""

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException

from .contracts import StrictModel

LOG = logging.getLogger(__name__)
# The developer profile bootstrap owns the URL and VIOS sensor for each input.
SOURCES = (
    ("flowing", "Flowing traffic", "d1-flowing", "d1-flowing"),
    ("queued", "Queued traffic", "d1-queued", "d1-queued"),
)


class SourceSelection(StrictModel):
    source_id: Literal["flowing", "queued"]


def selection_plan(state, selected):
    """Validate before any writes and preserve every field except enabled."""
    stream_id, rule_id = next((stream, rule) for source, _, stream, rule in SOURCES if source == selected)
    streams = {row["id"]: row for row in state["streams"]}
    rules = {row["id"]: row for row in state["rules"]}
    if stream_id not in streams or rule_id not in rules:
        raise ValueError("Prepared stream and rule are required.")
    rule = rules[rule_id]["config"]
    if rule["stream_id"] != stream_id or rule["config_revision"] != streams[stream_id]["revision"]:
        raise ValueError("The prepared rule does not match the stream revision.")
    changes = []

    def change(kind, row, enabled):
        if row["config"]["enabled"] != enabled:
            body = copy.deepcopy(row["config"])
            body["enabled"] = enabled
            changes.append((kind, copy.deepcopy(row), body))

    # Pause other rules before switching inference. Arm the selected rule last.
    for _, _, _, other_rule in SOURCES:
        if other_rule != rule_id and other_rule in rules:
            change("rules", rules[other_rule], False)
    for _, _, other_stream, _ in SOURCES:
        if other_stream != stream_id and other_stream in streams:
            change("streams", streams[other_stream], False)
    change("streams", streams[stream_id], True)
    change("rules", rules[rule_id], True)
    return changes


def apply_plan(changes, request):
    applied = []
    try:
        for kind, previous, body in changes:
            current = next((row for row in request(kind) if row["id"] == previous["id"]), None)
            if current != previous:
                raise RuntimeError("Monitoring settings changed while switching sources.")
            # The request may time out after the server commits the change.
            applied.append((kind, previous, body))
            request(kind, previous["id"], body, previous["revision"])
    except Exception:
        for kind, previous, written in reversed(applied):
            try:
                current = next((row for row in request(kind) if row["id"] == previous["id"]), None)
                if current and current["config"] == written:
                    request(kind, previous["id"], previous["config"], current["revision"])
            except (RuntimeError, httpx.HTTPError) as exc:
                LOG.warning("Could not restore %s/%s (%s)", kind, previous["id"], type(exc).__name__)
        raise


def router(store):
    api = APIRouter(prefix="/v1/demo")
    selection_lock = threading.Lock()

    def request(kind, item_id=None, body=None, revision=None):
        if kind == "rules":
            if body is None:
                return store.configs("rule")
            with store.transaction():
                row = next((r for r in store.configs("rule") if r["id"] == item_id), None)
                if not row or row["revision"] != revision:
                    raise RuntimeError("Monitoring settings changed; reload before switching.")
                version = store.put_config("rule", item_id, body)
            return {"id": item_id, "revision": version, "config": body}
        base = os.getenv("D1_INGEST_URL", "http://127.0.0.1:8020").rstrip("/")
        with httpx.Client(timeout=15) as client:
            if body is None:
                response = client.get(base + "/v1/streams")
            else:
                response = client.put(
                    base + "/v1/streams/" + item_id,
                    json=body,
                    headers={"If-Match": str(revision)},
                )
            response.raise_for_status()
            return response.json()

    def state():
        return {kind: request(kind) for kind in ("streams", "rules")}

    def public_state(current):
        sources = []
        streams = {r["id"]: r for r in current["streams"]}
        rules = {r["id"]: r for r in current["rules"]}
        for source_id, label, stream_id, rule_id in SOURCES:
            stream, rule = streams.get(stream_id), rules.get(rule_id)
            available = bool(
                stream and rule and rule["config"]["stream_id"] == stream_id
                and rule["config"]["config_revision"] == stream["revision"]
            )
            active = bool(available and stream["config"]["enabled"] and rule["config"]["enabled"])
            kind = "recorded" if os.getenv(f"D1_{source_id.upper()}_KIND") == "recorded" else "live"
            sources.append(
                dict(id=source_id, label=label, kind=kind, stream_id=stream_id,
                     rule_id=rule_id, available=available, active=active)
            )
        active = [row["id"] for row in sources if row["active"]]
        return {"sources": sources, "active_source": active[0] if len(active) == 1 else None}

    @api.get("/sources")
    def sources():
        try:
            return public_state(state())
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(503, "Cannot read the monitoring sources. Try again shortly.") from exc

    @api.post("/sources")
    def select(payload: SourceSelection):
        if not selection_lock.acquire(blocking=False):
            raise HTTPException(409, "A source change is already in progress.")
        try:
            current = state()
            changes = selection_plan(current, payload.source_id)
            if changes:
                folder = Path(os.getenv("D1_SELECTION_BACKUPS", "/data/source-backups"))
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = folder / f"{time.time_ns()}-{payload.source_id}-before.json"
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
                    json.dump(current, out, indent=2)
                apply_plan(changes, request)
            result = public_state(state())
            if result["active_source"] != payload.source_id:
                raise RuntimeError("Monitoring changed during selection.")
            selected = next(source for source in result["sources"] if source["id"] == payload.source_id)
            result["message"] = (
                "Live input selected. Waiting for fresh frames." if selected["kind"] == "live"
                else "Recorded footage selected. This source repeats."
            )
            return result
        except ValueError as exc:
            raise HTTPException(409, "This source needs matching monitoring settings before it can be selected.") from exc
        except (httpx.HTTPError, OSError, RuntimeError) as exc:
            raise HTTPException(503, "Source selection did not complete. Reload to check the active input.") from exc
        finally:
            selection_lock.release()

    return api
