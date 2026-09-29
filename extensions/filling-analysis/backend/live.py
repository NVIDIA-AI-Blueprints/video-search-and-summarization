# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded live-session API over the GPU worker; never reads recorded measurements."""
from __future__ import annotations

import asyncio
import json
import math
import re
from urllib.parse import urlencode
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stream_id: UUID
    request_id: UUID | None = None


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID


class Question(Session):
    question: str = Field(min_length=1, max_length=2000)
    cycle_id: str | None = Field(default=None, min_length=1, max_length=180)
    cycle_ids: list[str] = Field(default_factory=list, max_length=2)
    epoch: int | None = Field(default=None, ge=1)
    limit: int = Field(default=10, ge=1, le=50)


class LiveProxy:
    def __init__(self, url, profiles_path, registry, transport=None, public_origin=None, public_prefix="/filling"):
        self.url = url
        self.profiles_path = Path(profiles_path)
        self.registry = registry
        self.transport = transport
        self.public_origin = (public_origin or "").rstrip("/")
        self.public_prefix = public_prefix

    def profiles(self):
        try:
            profiles = json.loads(self.profiles_path.read_text())
            assert profiles["schema_version"] == 1
            streams = profiles["streams"]
            assert isinstance(streams, dict)
            for stream_id, profile in streams.items():
                assert str(UUID(stream_id)) == stream_id
                assert all(k in profile for k in ("name", "width", "height", "fps", "profile_id"))
            return streams
        except (OSError, ValueError, KeyError, TypeError, AssertionError):
            raise HTTPException(503, "Live camera profiles are unavailable") from None

    async def sources(self):
        profiles = self.profiles()
        registry = await self.registry()
        if not registry.get("available"):
            raise HTTPException(503, "VIOS source registry is unavailable")
        sources = []
        seen = set()
        for row in registry["sources"]:
            sid = row.get("stream_id")
            if sid not in profiles or sid in seen or row.get("type") not in {"stream", "sensor", "sensor_rtsp"}:
                continue
            seen.add(sid)
            p = profiles[sid]
            sources.append({"stream_id": sid, "name": row.get("name", p["name"]),
                            "state": row.get("state"), "approved": True,
                            **{k: p[k] for k in ("width", "height", "fps", "profile_id")}})
        return {"mode": "live", "sources": sources}

    async def call(self, method, path, *, body=None, params=None, client=None):
        if not self.url:
            raise HTTPException(503, "Live analysis is not configured")
        if client is None:
            async with httpx.AsyncClient(timeout=15, transport=self.transport, follow_redirects=False) as client:
                return await self.call(method, path, body=body, params=params, client=client)
        try:
            response = await client.request(method, self.url + "/live/" + path, json=body, params=params)
            if response.status_code in {404, 409, 422}:
                detail = {404: "Live session was not found", 409: "Live session conflicts with the current operation",
                          422: "Live request or camera profile is invalid"}[response.status_code]
                raise HTTPException(response.status_code, detail)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("mode") != "live":
                raise ValueError("Invalid live response")
            session_id = (body or {}).get("session_id") or (params or {}).get("session_id")
            if session_id and data.get("session_id") != session_id:
                raise ValueError("Session mismatch")
            if data.get("session_id"):
                UUID(data["session_id"])
                if data.get("stream_id") not in self.profiles():
                    raise ValueError("Unapproved source")
            return data
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise HTTPException(503, "Live worker is unavailable or returned mismatched data") from None

    def public_event(self, event, session_id, stream_id):
        if event.get("session_id") != session_id or event.get("stream_id") != stream_id:
            raise HTTPException(503, "Live event identity mismatch")
        event = dict(event, evidence_status="pending", video_evidences=[])
        snapshots = []
        for raw in event.get("snapshot_evidences", []):
            if (not isinstance(raw, dict) or raw.get("session_id") != session_id
                    or raw.get("stream_id") != stream_id or raw.get("event_id") != event.get("event_id")
                    or raw.get("verified") is not True or raw.get("verification") != "exact-decoded-frame"
                    or type(raw.get("frame_id")) is not int or raw["frame_id"] < 1
                    or type(raw.get("source_pts_seconds")) not in {int, float}
                    or not math.isfinite(raw["source_pts_seconds"])
                    or raw.get("mime_type") != "image/jpeg"):
                raise HTTPException(503, "Live snapshot provenance is invalid")
            snapshot = {k: raw[k] for k in ("session_id", "stream_id", "event_id", "frame_id",
                        "source_pts_seconds", "verified", "verification", "mime_type") if k in raw}
            if self.public_origin:
                snapshot["public_url"] = self.public_origin + self.public_prefix + "/api/live/evidence?" + urlencode({
                    "session_id": session_id, "event_id": event["event_id"], "frame_id": raw["frame_id"]})
            snapshots.append(snapshot)
        event["snapshot_evidences"] = snapshots
        return event

    async def all_events(self, sid, status):
        events, after = [], 0
        for _ in range(25):
            batch = await self.call("GET", "events", params={"session_id": sid, "after_seq": after, "limit": 200})
            if batch["stream_id"] != status["stream_id"]:
                raise HTTPException(503, "Live event source changed unexpectedly")
            rows = batch.get("events", [])
            events.extend(self.public_event(e, sid, status["stream_id"]) for e in rows)
            if len(rows) < 200:
                return events, False
            next_seq = batch.get("next_seq", after)
            if not isinstance(next_seq, int) or next_seq <= after:
                raise HTTPException(503, "Live event cursor did not advance")
            after = next_seq
        return events, True

    @staticmethod
    def cycle_number(identifier):
        match = re.search(r"(?:^|:)cycle-(\d+)$", str(identifier))
        return int(match.group(1)) if match else None

    async def query(self, body):
        sid = str(body.session_id)
        status = await self.call("GET", "status", params={"session_id": sid})
        events, scanned_limit = await self.all_events(sid, status)
        words = body.question.lower()
        requested = []
        exact_track = None
        selected_tracks = set()
        epoch = body.epoch
        if body.cycle_ids:
            if body.cycle_id or epoch is not None:
                raise HTTPException(422, "Use selected full track IDs without a separate cycle or epoch")
            for identifier in body.cycle_ids:
                match = re.fullmatch(r"(?P<sid>[0-9a-fA-F-]{36}):epoch-(?P<epoch>\d+):cycle-(?P<number>\d+)", identifier)
                if not match or int(match["epoch"]) < 1 or int(match["number"]) < 1:
                    raise HTTPException(422, "Selected bottles require returned full session/epoch/track identities")
                if str(UUID(match["sid"])) != sid:
                    raise HTTPException(409, "Selected bottle belongs to another session")
                selected_tracks.add(identifier)
                requested.append(int(match["number"]))
            if len(selected_tracks) != len(body.cycle_ids):
                raise HTTPException(422, "Select distinct bottles")
            mentioned = {int(n) for n in re.findall(r"\bcy(?:cle|ce)[\s_-]*(\d+)\b", words)}
            if mentioned and mentioned != set(requested):
                raise HTTPException(409, "Explicit question labels conflict with the selected bottles")
        if body.cycle_id:
            m = re.fullmatch(r"(?:(?P<sid>[0-9a-fA-F-]{36}):epoch-(?P<epoch>\d+):)?cycle-(?P<number>\d+)", body.cycle_id)
            if not m or int(m["number"]) < 1:
                raise HTTPException(422, "Use a returned cycle-N or full session/epoch/track identifier")
            if m["sid"]:
                if int(m["epoch"]) < 1:
                    raise HTTPException(422, "Cycle epoch must be positive")
                if str(UUID(m["sid"])) != sid or (epoch is not None and epoch != int(m["epoch"])):
                    raise HTTPException(409, "Cycle identifier belongs to another session or epoch")
                epoch = int(m["epoch"])
                exact_track = body.cycle_id
            requested.append(int(m["number"]))
        elif not body.cycle_ids:
            requested = list(dict.fromkeys(int(n) for n in re.findall(r"\bcy(?:cle|ce)[\s_-]*(\d+)\b", words)))
        current = (status.get("current", {}) if status.get("status") == "running"
                   and (epoch is None or status.get("epoch") == epoch) else {}) or {}
        query_status, candidates, matches = "ok", [], []
        total_matches = 0
        def brief(event):
            return {k: event[k] for k in ("session_id", "stream_id", "epoch", "track_id", "event_id", "status") if k in event}
        def describe(event):
            number = self.cycle_number(event.get("track_id"))
            label = f"cycle-{number}" if number is not None else event["track_id"]
            verdict = event["status"]
            result = f"Bottle {label}: {verdict}."
            level, reference = event.get("final_level"), event.get("reference_level")
            if level is not None:
                result += f" Final visible liquid height {100*level:.1f}% of bottle height."
            else:
                result += " A reliable final liquid height was not established."
            if reference is not None:
                result += f" Reference {100*reference:.1f}%."
            if verdict == "underfill" and level is not None and reference is not None:
                tolerance = event.get("measurement", {}).get("tolerance")
                if tolerance is not None:
                    result += f" Lower acceptance limit {100*(reference-tolerance):.1f}%; shortfall below that limit {100*(reference-tolerance-level):.1f} percentage points."
            if event.get("reason"):
                result += " " + event["reason"]
            return result
        wrong_selection_count = (body.cycle_ids and (
            (re.search(r"\b(?:compare|these two|those two)\b", words) and len(body.cycle_ids) != 2)
            or ("this bottle" in words and len(body.cycle_ids) != 1)))
        if wrong_selection_count:
            query_status = "unsupported"
            answer = "Select one bottle for a single-bottle question, or exactly two bottles for a comparison. No other bottle was selected automatically."
        elif requested:
            selected = [e for e in events if self.cycle_number(e.get("track_id")) in requested
                        and (epoch is None or e.get("epoch") == epoch)
                        and (exact_track is None or e.get("track_id") == exact_track)
                        and (not selected_tracks or e.get("track_id") in selected_tracks)]
            ambiguity = not selected_tracks and any(len({e.get("epoch") for e in selected if self.cycle_number(e.get("track_id")) == n}) > 1 for n in requested)
            if ambiguity:
                query_status = "ambiguous"
                candidates = [brief(e) for e in selected]
                answer = "That cycle label occurs in multiple connection epochs. Select the returned full track identifier; no bottle was chosen automatically."
            else:
                # Prefer the final inspection to any earlier provisional overflow observation.
                unique = {}
                for event in sorted(selected, key=lambda x: x["seq"]):
                    key = (event.get("epoch"), event.get("track_id"))
                    if key not in unique or event["kind"] in {"cycle.finalized", "cycle.incomplete"}:
                        unique[key] = event
                matches = list(unique.values())
                total_matches = len(matches)
                found = {self.cycle_number(e.get("track_id")) for e in matches}
                missing = [n for n in requested if n not in found]
                live_number = self.cycle_number(current.get("track_id"))
                current_requested = live_number in missing and (epoch is None or status.get("epoch") == epoch)
                if current_requested:
                    query_status = "in_progress"
                    answer = f"Bottle cycle-{live_number} is still being observed ({current.get('phase', 'unreadable')}); it has no final verdict. " + current.get("quality", {}).get("reason", "")
                    if matches:
                        answer += " " + " ".join(describe(e) for e in matches)
                elif missing:
                    query_status = "not_found"
                    answer = "No inspection record for " + ", ".join(f"cycle-{n}" for n in missing) + " in the specified live session" + (f"/epoch {epoch}" if epoch else "") + ". Cycle labels are inspection identifiers, not archive-search terms."
                    if scanned_limit:
                        answer += " The bounded history scan reached its limit; absence from older history is not established."
                    if matches:
                        answer += " " + " ".join(describe(e) for e in matches)
                else:
                    answer = " ".join(describe(e) for e in matches)
                candidates = [brief(e) for e in matches]
                if query_status != "ok":
                    matches = []
        elif re.search(r"\b(?:this bottle|these (?:two )?bottles|those (?:two )?bottles)\b", words):
            query_status = "unsupported"
            answer = "Select one bottle, or two bottles to compare, in Filling Analysis first. No bottle is selected by this question alone. You can also type the displayed cycle labels."
        elif any(word in words for word in ("current bottle", "right now")):
            query_status = "in_progress" if current.get("track_id") else "ok"
            answer = (f"Current bottle: {current.get('track_id') or 'none'}. "
                      f"Session state: {status.get('status')}. Observation: {current.get('phase', 'none')}. ")
            if epoch is not None and status.get("epoch") != epoch:
                answer += f"Requested epoch {epoch} is not the current connection epoch {status.get('epoch')}; no current observation was substituted. "
            quality = current.get("quality", {})
            if current.get("level") is not None and quality.get("readable", True):
                answer += f"Provisional visible height {100*current['level']:.1f}%; no completed-cycle verdict yet. "
            answer += quality.get("reason", "")
        elif any(word in words for word in ("volume", "millilit", "liters", "litres", "root cause", "why did")):
            query_status = "unsupported"
            answer = "The inspection records visible height and observed exterior overflow. It does not measure liquid volume or establish the mechanical cause of a fault."
        else:
            wanted = "underfill" if any(w in words for w in ("underfill", "under fill", "below", "shortfall", "too low")) else (
                "overflow" if any(w in words for w in ("overflow", "spill", "leak")) else None)
            complete = [e for e in events if e["kind"] == "cycle.finalized"
                        and (epoch is None or e.get("epoch") == epoch)]
            selected = [e for e in complete if wanted is None or e["status"] == wanted]
            if "anomal" in words or "problem" in words:
                selected = [e for e in complete if e["status"] in {"underfill", "overflow"}]
            total_matches = len(selected)
            if any(w in words for w in ("worst", "largest shortfall", "lowest")):
                selected = [e for e in selected if e.get("final_level") is not None]
                selected.sort(key=lambda x: (x["final_level"], -x["seq"]))
                selected = selected[:1]
            else:
                selected.sort(key=lambda x: x["seq"], reverse=True)
                if re.search(r"\b(?:most recent|latest|last)\b", words):
                    count = re.search(r"\b(?:most recent|latest|last)\s+(\d+)\b", words)
                    selected = selected[:max(1, min(body.limit, int(count[1]))) if count else 1]
            matches = selected[:body.limit]
            summary = status.get("summary", {})
            answer = (f"Whole session across connection epochs: {summary.get('total', 0)} completed bottles; {summary.get('normal', 0)} normal, "
                      f"{summary.get('underfill', 0)} underfilled, {summary.get('overflow', 0)} overflow, "
                      f"{summary.get('uncertain', 0)} uncertain. ")
            if matches:
                answer += " ".join(describe(e) for e in matches)
            elif wanted:
                answer += f"No matching finalized {wanted} records in the scanned history."
            if scanned_limit:
                query_status, matches = "unsupported", []
                answer = "This session exceeds the bounded inspection-history scan. A latest or worst bottle cannot be established from this incomplete history."
        matches = matches[:body.limit]
        if matches:
            answer += " Levels measure visible bottle height, not volume."
        snapshots = [s for e in matches for s in e.get("snapshot_evidences", [])]
        # A few representative exact frames are useful in chat; clips remain a distinct contract.
        if len(snapshots) > 3:
            snapshots = [snapshots[0], snapshots[len(snapshots)//2], snapshots[-1]]
        return {"mode": "live", "session_id": sid, "stream_id": status["stream_id"], "answer": answer,
                "epoch": status.get("epoch"),
                "session_status": status.get("status"),
                "query_status": query_status, "requested_cycle_ids": list(dict.fromkeys(f"cycle-{n}" for n in requested)), "selected_track_ids": sorted(selected_tracks),
                "candidates": candidates, "current": current, "summary": status.get("summary", {}),
                "matches": matches, "total_matches": total_matches, "video_evidences": [],
                "snapshot_evidences": snapshots, "measurement": status.get("measurement", {}),
                "measurement_history": status.get("measurement_history", []), "clock": status.get("clock", {}),
                "truncated": scanned_limit or total_matches > len(matches)}

    async def snapshot(self, session_id, event_id, frame_id):
        sid = str(session_id)
        status = await self.call("GET", "status", params={"session_id": sid})
        events, truncated = await self.all_events(sid, status)
        allowed = any(e["event_id"] == event_id and any(s["frame_id"] == frame_id for s in e.get("snapshot_evidences", [])) for e in events)
        if not allowed:
            if truncated:
                raise HTTPException(503, "Snapshot lookup exceeds the bounded inspection-history scan")
            raise HTTPException(404, "No verified decoded snapshot for that event and frame")
        async with httpx.AsyncClient(timeout=15, transport=self.transport, follow_redirects=False) as client:
            try:
                response = await client.get(self.url + "/live/evidence", params={"session_id": sid, "event_id": event_id, "frame_id": frame_id})
                response.raise_for_status()
                if (response.headers.get("content-type", "").split(";")[0] != "image/jpeg"
                        or not response.content.startswith(b"\xff\xd8") or len(response.content) > 5_000_000):
                    raise ValueError("Invalid image response")
                return Response(response.content, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})
            except (httpx.HTTPError, ValueError):
                raise HTTPException(503, "The verified snapshot is temporarily unavailable") from None


def live_router(proxy: LiveProxy):
    router = APIRouter(prefix="/api/live")

    @router.get("/sources")
    async def sources():
        return await proxy.sources()

    @router.post("/start")
    async def start(body: Start):
        stream_id = str(body.stream_id)
        approved = (await proxy.sources())["sources"]
        matches = [s for s in approved if s["stream_id"] == stream_id]
        if not matches:
            raise HTTPException(422, "Select an approved, registered live source")
        if matches[0]["state"] != "online":
            raise HTTPException(409, "The selected live source is offline")
        data = await proxy.call("POST", "start", body=body.model_dump(mode="json", exclude_none=True))
        if data.get("stream_id") != stream_id:
            raise HTTPException(503, "Live worker selected a different source")
        return data

    @router.get("/status")
    async def status(session_id: UUID | None = None):
        return await proxy.call("GET", "status", params={"session_id": str(session_id)} if session_id else {})

    @router.post("/stop")
    async def stop(body: Session):
        return await proxy.call("POST", "stop", body=body.model_dump(mode="json"))

    @router.get("/events")
    async def events(session_id: UUID, after_seq: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200), latest: bool = False):
        data = await proxy.call("GET", "events", params={"session_id": str(session_id), "after_seq": after_seq, "limit": limit, "latest": latest})
        data["events"] = [proxy.public_event(row, str(session_id), data["stream_id"]) for row in data.get("events", [])]
        return JSONResponse(data)

    @router.post("/query")
    async def query(body: Question):
        return JSONResponse(await proxy.query(body))

    @router.get("/evidence")
    async def evidence(session_id: UUID, event_id: str = Query(min_length=1, max_length=220), frame_id: int = Query(ge=1)):
        return await proxy.snapshot(session_id, event_id, frame_id)

    @router.websocket("/ws")
    async def websocket(websocket: WebSocket, session_id: UUID):
        await websocket.accept()
        previous = None
        last_status = 0.0
        sid = str(session_id)
        stream_id = None
        try:
            async with httpx.AsyncClient(timeout=10, transport=proxy.transport, follow_redirects=False) as client:
                initial = await proxy.call("GET", "status", params={"session_id": sid}, client=client)
                stream_id = initial["stream_id"]
                await websocket.send_json(initial)
                while True:
                    data = await proxy.call("GET", "frame", params={"session_id": sid}, client=client)
                    if data.get("stream_id") != stream_id:
                        raise HTTPException(503, "Live frame source changed unexpectedly")
                    identity = (data.get("epoch"), data.get("frame_id"))
                    now = asyncio.get_running_loop().time()
                    if data.get("type") == "frame" and identity != previous:
                        # Await sends: a slow viewer never grows a server-side frame queue.
                        await asyncio.wait_for(websocket.send_json(data), timeout=5)
                        previous = identity
                    if now - last_status >= 1:
                        status = await proxy.call("GET", "status", params={"session_id": sid}, client=client)
                        await asyncio.wait_for(websocket.send_json(status), timeout=5)
                        last_status = now
                        if status.get("status") in {"stopped", "error"}:
                            break
                    await asyncio.sleep(0.025)
        except HTTPException as exc:
            await websocket.send_json({"type": "error", "mode": "live", "session_id": sid,
                                       "stream_id": stream_id, "error": exc.detail})
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            return
        finally:
            try:
                await websocket.close()
            except (RuntimeError, WebSocketDisconnect):
                pass

    return router
