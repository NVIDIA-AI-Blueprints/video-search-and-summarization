# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
MODEL_ID = "frozen-bwc-summary"

app = FastAPI(title="Frozen VSS Summarization Server")


def _fixture_files() -> list[Path]:
    return sorted(DATA_DIR.glob("*.json"))


def _normalize_video_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, list):
        if not value:
            return None
        value = value[0]
    name = str(value).strip()
    if not name:
        return None
    if "/" in name or "://" in name:
        parsed = urlparse(name)
        name = Path(parsed.path).name
    for suffix in (".mp4", ".mov", ".mkv", ".avi", ".json"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name or None


def _select_fixture(payload: dict[str, Any]) -> Path:
    candidates = [
        _normalize_video_name(payload.get("id")),
        _normalize_video_name(payload.get("url")),
        _normalize_video_name(payload.get("video")),
        _normalize_video_name(payload.get("video_id")),
    ]

    for candidate in candidates:
        if not candidate:
            continue
        path = DATA_DIR / f"{candidate}.json"
        if path.exists():
            return path

    files = _fixture_files()
    if len(files) == 1:
        return files[0]

    raise HTTPException(
        status_code=404,
        detail={
            "error": "Could not determine frozen summary fixture",
            "accepted_lookup_fields": ["id", "url", "video", "video_id"],
            "available_fixtures": [path.stem for path in files],
        },
    )


def _load_summary(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        source = json.load(f)

    events = source.get("events") or source.get("prediction") or []
    if not isinstance(events, list):
        raise HTTPException(status_code=500, detail=f"{path.name}: events/prediction must be a list")

    event_types = sorted({event.get("type", "unknown") for event in events if isinstance(event, dict)})
    video = source.get("video") or path.stem
    video_type = source.get("video_type") or "Frozen BWC video summary"
    video_summary = source.get("video_summary") or (
        f"{video_type}. The frozen fixture contains {len(events)} timestamped events"
        f" across event types: {', '.join(event_types)}."
    )

    return {
        "video": video,
        "video_type": video_type,
        "video_path": source.get("video_path"),
        "video_summary": video_summary,
        "events": events,
    }


def _completion_envelope(summary: dict[str, Any]) -> dict[str, Any]:
    content = json.dumps(
        {
            "video_summary": summary["video_summary"],
            "events": summary["events"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "id": f"frozen-{summary['video']}",
        "video_id": summary["video"],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


@app.get("/v1/ready")
def ready() -> Response:
    return Response(status_code=200)


@app.get("/v1/live")
def live() -> Response:
    return Response(status_code=200)


@app.get("/v1/startup")
def startup() -> Response:
    return Response(status_code=200)


@app.get("/v1/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/metadata")
def metadata() -> dict[str, Any]:
    return {
        "service": "frozen-summarization-server",
        "data_dir": str(DATA_DIR),
        "fixtures": [path.stem for path in _fixture_files()],
    }


@app.get("/models")
def models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_ID,
                "object": "model",
                "owned_by": "local-fixture",
                "api_type": "chat-completions",
            }
        ],
    }


@app.post("/v1/summarize")
@app.post("/summarize")
async def summarize(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")

    fixture = _select_fixture(payload)
    summary = _load_summary(fixture)
    return _completion_envelope(summary)
