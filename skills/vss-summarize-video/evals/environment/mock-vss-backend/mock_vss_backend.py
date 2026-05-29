#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Mock VSS video summarization backend for skill evaluation."""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


MODEL_ID = os.environ.get("MODEL_ID", "nim_nvidia_cosmos-reason2-8b_hf-1208")


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def _event_type(label: str) -> str:
    return label.strip().lower().replace(" ", "_") or "notable_activity"


def _fixture_for_url(url: str) -> dict[str, Any]:
    lower_url = url.lower()
    if "warehouse-2024-06-15" in lower_url:
        return {
            "video_id": "warehouse-2024-06-15",
            "summary": (
                "00:00-00:12: The warehouse aisle is active with pallets staged near the racks "
                "and a forklift moving slowly through the work area. 00:13-00:31: Several boxes "
                "shift from a stack near the pallet lane, prompting nearby workers to pause and "
                "look toward the obstruction. 00:32-00:52: The forklift stops short of the fallen "
                "items while personnel keep distance from the lane. 00:53-01:08: The area remains "
                "controlled, with workers preparing to clear the scattered boxes before traffic resumes."
            ),
            "defaults": [
                (
                    "00:00:13",
                    "00:00:31",
                    "notable_activity",
                    "A stack of boxes shifts beside the pallet lane, drawing worker attention and creating a temporary obstruction.",
                ),
                (
                    "00:00:32",
                    "00:00:52",
                    "forklift_stopped",
                    "A forklift slows and stops before reaching the affected area, leaving space for workers to respond.",
                ),
            ],
            "event_map": {
                "boxes falling": (
                    "00:00:13",
                    "00:00:31",
                    "boxes_falling",
                    "Several boxes fall from a staged stack near the pallet lane and scatter into the travel path.",
                ),
                "forklift stuck": (
                    "00:00:32",
                    "00:00:52",
                    "forklift_stuck",
                    "The forklift remains stopped in the aisle while the obstruction is assessed, temporarily blocking normal movement.",
                ),
                "person entering restricted area": (
                    "00:00:53",
                    "00:01:08",
                    "person_entering_restricted_area",
                    "A worker steps toward the restricted lane to inspect the fallen boxes while vehicle traffic is paused.",
                ),
                "notable activity": (
                    "00:00:13",
                    "00:00:52",
                    "notable_activity",
                    "Boxes shift into the aisle and the forklift stops, creating a controlled response around the obstruction.",
                ),
            },
        }
    if "parking-lot-overnight" in lower_url:
        return {
            "video_id": "parking-lot-overnight",
            "summary": (
                "00:00-00:18: The overnight parking lot is mostly quiet, with parked vehicles visible "
                "under fixed lighting. 00:19-00:42: A vehicle enters the lot, slows near the center row, "
                "and passes several parked cars without stopping. 00:43-01:05: A person walks along the "
                "edge of the lot and briefly approaches the parked vehicles before continuing out of frame. "
                "01:06-01:20: No collision, forced entry, or obvious emergency is visible by the end of the clip."
            ),
            "defaults": [
                (
                    "00:00:19",
                    "00:00:42",
                    "vehicle_movement",
                    "A vehicle drives through the center of the lot at low speed and exits the camera view.",
                ),
                (
                    "00:00:43",
                    "00:01:05",
                    "pedestrian_activity",
                    "A pedestrian moves along the parking row and briefly pauses near the parked vehicles before walking away.",
                ),
            ],
            "event_map": {
                "notable activity": (
                    "00:00:19",
                    "00:01:05",
                    "notable_activity",
                    "The main activity is a vehicle passing through the lot followed by a pedestrian moving near the parked cars.",
                ),
                "vehicle": (
                    "00:00:19",
                    "00:00:42",
                    "vehicle",
                    "A vehicle enters and crosses the parking area without visible impact or obstruction.",
                ),
                "person": (
                    "00:00:43",
                    "00:01:05",
                    "person",
                    "A person walks near the parking row, pauses briefly, and then leaves the camera view.",
                ),
            },
        }
    if "factory-floor-tuesday" in lower_url:
        return {
            "video_id": "factory-floor-tuesday",
            "summary": (
                "00:00-00:20: The factory floor shows normal activity around a marked work cell, "
                "with workers moving between equipment and material carts. 00:21-00:46: A worker "
                "near the machine line reacts to material on the floor and steps back from the area. "
                "00:47-01:14: Nearby personnel slow movement around the work cell while the affected "
                "space is inspected. 01:15-01:40: The clip ends with the area partially cleared and "
                "workers maintaining distance from the incident location."
            ),
            "defaults": [
                (
                    "00:00:21",
                    "00:00:46",
                    "safety_incident",
                    "A worker reacts to a floor-level hazard near the machine line and backs away from the immediate area.",
                ),
                (
                    "00:00:47",
                    "00:01:14",
                    "area_control",
                    "Other personnel slow movement around the work cell while the incident area is checked.",
                ),
            ],
            "event_map": {
                "notable activity": (
                    "00:00:21",
                    "00:01:14",
                    "notable_activity",
                    "Workers respond to a floor-level issue near the machine line and control movement around the affected area.",
                ),
                "safety incident": (
                    "00:00:21",
                    "00:00:46",
                    "safety_incident",
                    "A worker steps back from a hazard near the equipment, creating a brief safety response on the floor.",
                ),
                "worker fall": (
                    "00:00:21",
                    "00:00:46",
                    "worker_fall",
                    "A worker loses balance near the work cell and nearby personnel react by slowing activity around the area.",
                ),
                "spill": (
                    "00:00:47",
                    "00:01:14",
                    "spill",
                    "A floor-level obstruction or spill is treated as a hazard while workers keep clear of the machine line.",
                ),
            },
        }
    return {
        "video_id": "submitted-video",
        "summary": (
            "00:00-00:15: The recording opens with a stable view of the monitored area. "
            "00:16-00:45: Activity increases in the central field of view and the requested "
            "event categories are visible enough to be logged with timestamps. 00:46-01:10: "
            "Movement decreases and the scene returns to a steady state with no visible escalation."
        ),
        "defaults": [
            (
                "00:00:16",
                "00:00:45",
                "notable_activity",
                "Activity increases in the monitored area and then returns to a steady state.",
            )
        ],
        "event_map": {
            "notable activity": (
                "00:00:16",
                "00:00:45",
                "notable_activity",
                "Activity increases in the monitored area and then returns to a steady state.",
            )
        },
    }


def _make_events(request: dict[str, Any], fixture: dict[str, Any]) -> list[dict[str, Any]]:
    raw_events = request.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        raw_events = ["notable activity"]

    events: list[dict[str, Any]] = []
    event_map = fixture["event_map"]
    default_events = fixture["defaults"]
    for idx, label in enumerate(raw_events, start=1):
        label_text = str(label).strip()
        event_detail = event_map.get(label_text.lower())
        if event_detail is None:
            start_time, end_time, _, description = default_events[(idx - 1) % len(default_events)]
            event_type = _event_type(label_text)
            description = (
                f"{description} This segment is associated with the requested "
                f"event category '{label_text}'."
            )
        else:
            start_time, end_time, event_type, description = event_detail
        events.append(
            {
                "id": idx,
                "start_time": start_time,
                "end_time": end_time,
                "type": event_type,
                "description": description,
            }
        )
    return events


def _summarize_payload(request: dict[str, Any]) -> dict[str, Any]:
    url = str(request.get("url") or request.get("video_url") or "submitted video")
    fixture = _fixture_for_url(url)
    events = _make_events(request, fixture)
    return {
        "video_summary": fixture["summary"],
        "events": events,
    }


def _completion_response(request: dict[str, Any]) -> dict[str, Any]:
    model = str(request.get("model") or MODEL_ID)
    content = json.dumps(_summarize_payload(request), sort_keys=True)
    now = int(time.time())
    return {
        "id": "cmpl-vss-summary",
        "video_id": _fixture_for_url(str(request.get("url") or request.get("video_url") or ""))["video_id"],
        "object": "chat.completion",
        "created": now,
        "model": model,
        "media_info": {"url": request.get("url") or request.get("video_url")},
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "MockVSS/1.0"

    def _send_bytes(
        self,
        status: int,
        body: bytes = b"",
        content_type: str = "application/json",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        self._send_bytes(status, _json_bytes(payload))

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/v1/ready":
            self._send_bytes(200)
            return
        if path in {"/v1/live", "/v1/startup", "/v1/healthz", "/healthz"}:
            self._send_json(200, {"status": "ok"})
            return
        if path == "/v1/metadata":
            self._send_json(
                200,
                {
                    "name": "mock-vss-backend",
                    "version": "eval",
                    "model": MODEL_ID,
                    "mock": True,
                },
            )
            return
        if path in {"/models", "/v1/models"}:
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": MODEL_ID,
                            "object": "model",
                            "owned_by": "nvidia-skill-eval",
                            "api_type": "vlm",
                        }
                    ],
                },
            )
            return
        if path == "/metrics":
            body = (
                "# HELP vss_mock_requests_total Mock VSS request count\n"
                "# TYPE vss_mock_requests_total counter\n"
                "vss_mock_requests_total 1\n"
                "vss_mock_ready 1\n"
            ).encode("utf-8")
            self._send_bytes(200, body, "text/plain; version=0.0.4")
            return
        if path == "/openapi.json":
            self._send_json(
                200,
                {
                    "openapi": "3.0.0",
                    "info": {"title": "Mock VSS backend", "version": "eval"},
                    "paths": {
                        "/v1/ready": {"get": {}},
                        "/models": {"get": {}},
                        "/recommended_config": {"post": {}},
                        "/metrics": {"get": {}},
                        "/v1/summarize": {"post": {}},
                    },
                },
            )
            return
        self._send_json(404, {"error": f"unknown endpoint: {path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        request = self._read_json()
        if path == "/recommended_config":
            video_length = int(request.get("video_length") or 0)
            target_response_time = int(request.get("target_response_time") or 0)
            event_duration = int(request.get("usecase_event_duration") or 0)
            chunk_size = max(5, min(60, event_duration * 2 or target_response_time // 3 or 10))
            self._send_json(
                200,
                {
                    "text": (
                        f"For a {video_length}s video and {target_response_time}s target response, "
                        f"use {chunk_size}s chunks with {event_duration}s event focus."
                    ),
                    "chunk_size": chunk_size,
                    "chunk_duration": chunk_size,
                    "video_length": video_length,
                    "target_response_time": target_response_time,
                    "usecase_event_duration": event_duration,
                },
            )
            return
        if path in {"/v1/summarize", "/summarize", "/v1/stream_summarize"}:
            self._send_json(200, _completion_response(request))
            return
        if path == "/v1/generate_captions":
            self._send_json(
                200,
                {
                    "id": request.get("id") or "mock-stream",
                    "status": "started",
                    "model": request.get("model") or MODEL_ID,
                },
            )
            return
        self._send_json(404, {"error": f"unknown endpoint: {path}"})

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "38111"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"mock VSS backend listening on {host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
