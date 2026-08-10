# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""VSS share service -- publish an agent's result set at a shareable URL.

An agent computes results (search hits are non-deterministic, so they cannot be
reproduced by re-running the query) and POSTs them here. The service returns an
unguessable id; the read-only UI renders that id, and chat channels get a link.

Three properties the design turns on:

* **Originals never leave the service.** ``screenshot_url`` values are stored
  server-side and rewritten to ``/api/view/<id>/thumb/<n>`` on read, so a
  viewer off the LAN can load thumbnails that point at a private VST address,
  and the proxy is scoped to one published view.
* **The proxy is index-addressed, not URL-addressed.** A client names a
  position, never a URL, so this cannot be aimed at an arbitrary host.
* **The id is the version.** A new publish mints a new id, so a session slot's
  current id doubles as its ETag.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC
from datetime import datetime
from datetime import timedelta
import json
import logging
import secrets
from typing import Annotated
from typing import Any

from fastapi import Depends
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi.middleware.cors import CORSMiddleware
import httpx
from pydantic import BaseModel
from pydantic import Field
import redis.asyncio as aioredis

from . import settings
from .preview import render_card

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("vss.share")

VIEW_KEY = "vss:share:view:{id}"
SESSION_KEY = "vss:share:session:{sid}"
PREVIEW_KEY = "vss:share:preview:{id}"


# --- Wire models ---------------------------------------------------------


class PublishRequest(BaseModel):
    """What an agent posts after running a search."""

    data: list[dict[str, Any]] = Field(description="Result rows, Search API shaped.")
    title: str | None = Field(default=None, description="Headline, usually the user's query.")
    query: str | None = Field(default=None, description="The query that produced these rows.")
    session: str | None = Field(
        default=None,
        description=(
            "Optional stable slot id. When set, the slot is repointed at this "
            "view so an already-open page following the slot picks it up."
        ),
    )


class PublishResponse(BaseModel):
    id: str
    url: str
    expires_at: str
    count: int


# --- Storage helpers -----------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _proxyable(url: object) -> bool:
    """True when ``url`` is a string the thumbnail proxy is permitted to fetch."""
    if not isinstance(url, str) or not url:
        return False
    return any(url.startswith(prefix) for prefix in settings.THUMB_ALLOWED_PREFIXES)


def _public_url(view_id: str) -> str:
    if settings.PUBLIC_BASE_URL:
        return f"{settings.PUBLIC_BASE_URL}/view/{view_id}"
    # No public origin configured yet -- hand back a relative path rather than
    # inventing a hostname that will not resolve for the recipient.
    return f"/view/{view_id}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
    app.state.http = httpx.AsyncClient(
        timeout=settings.THUMB_TIMEOUT_SECONDS,
        follow_redirects=False,  # a redirect would escape the allowlist check
    )
    logger.info(
        "share service up: redis=%s ttl=%ss thumb_prefixes=%s",
        settings.REDIS_URL,
        settings.TTL_SECONDS,
        settings.THUMB_ALLOWED_PREFIXES or "<disabled>",
    )
    try:
        yield
    finally:
        await app.state.http.aclose()
        await app.state.redis.aclose()


app = FastAPI(
    title="VSS Share",
    description="Publishes agent-computed result sets at shareable, expiring URLs.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


async def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis


RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]


async def _load(redis: aioredis.Redis, view_id: str) -> dict[str, Any]:
    raw = await redis.get(VIEW_KEY.format(id=view_id))
    if raw is None:
        # Expired and never-existed are the same response on purpose: probing
        # for which ids were once valid should not be possible.
        raise HTTPException(status_code=404, detail="View not found or expired")
    return json.loads(raw)


# --- Routes --------------------------------------------------------------


@app.get("/health", include_in_schema=False)
async def health(redis: RedisDep) -> dict[str, Any]:
    try:
        await redis.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis unreachable: {exc}") from exc
    return {"value": {"isAlive": True}}


@app.post("/api/view", response_model=PublishResponse)
async def publish(body: PublishRequest, redis: RedisDep) -> PublishResponse:
    """Store a result set and return its shareable id."""
    if not body.data:
        raise HTTPException(status_code=400, detail="data must contain at least one row")
    if len(body.data) > settings.MAX_RESULTS:
        raise HTTPException(
            status_code=413,
            detail=f"{len(body.data)} rows exceeds SHARE_MAX_RESULTS={settings.MAX_RESULTS}",
        )

    view_id = secrets.token_urlsafe(settings.ID_BYTES)
    created = _now()
    expires = created + timedelta(seconds=settings.TTL_SECONDS)

    record = {
        "id": view_id,
        "title": body.title or body.query or "Search results",
        "query": body.query,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
        "data": body.data,
    }

    encoded = json.dumps(record).encode()
    if len(encoded) > settings.MAX_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"payload {len(encoded)}B exceeds SHARE_MAX_PAYLOAD_BYTES={settings.MAX_PAYLOAD_BYTES}",
        )

    await redis.set(VIEW_KEY.format(id=view_id), encoded, ex=settings.TTL_SECONDS)

    if body.session:
        # Repoint the slot. An open page polling the slot sees a new id and
        # re-renders; the previous view stays resolvable at its own id.
        await redis.set(SESSION_KEY.format(sid=body.session), view_id.encode(), ex=settings.TTL_SECONDS)

    unproxyable = sum(1 for row in body.data if not _proxyable(row.get("screenshot_url")))
    if unproxyable:
        logger.warning(
            "view %s: %d/%d rows have a screenshot_url outside SHARE_THUMB_ALLOWED_PREFIXES; "
            "those thumbnails will 404",
            view_id,
            unproxyable,
            len(body.data),
        )

    logger.info("published view %s (%d rows, session=%s)", view_id, len(body.data), body.session or "-")
    return PublishResponse(
        id=view_id,
        url=_public_url(view_id),
        expires_at=expires.isoformat(),
        count=len(body.data),
    )


@app.get("/api/view/{view_id}")
async def read_view(view_id: str, redis: RedisDep) -> dict[str, Any]:
    """Return a published view with thumbnails rewritten to proxy paths."""
    record = await _load(redis, view_id)

    rows: list[dict[str, Any]] = []
    for index, row in enumerate(record["data"]):
        copy = dict(row)
        # Relative on purpose: the page and this service share a public origin,
        # so a relative path works without knowing what that origin is.
        copy["screenshot_url"] = (
            f"/api/view/{view_id}/thumb/{index}" if _proxyable(row.get("screenshot_url")) else ""
        )
        rows.append(copy)

    return {
        "id": record["id"],
        "title": record["title"],
        "query": record.get("query"),
        "created_at": record["created_at"],
        "expires_at": record["expires_at"],
        "count": len(rows),
        "data": rows,
    }


@app.get("/api/view/session/{session_id}")
async def read_session(session_id: str, request: Request, redis: RedisDep) -> Response:
    """Resolve a session slot to its current view.

    The view id doubles as the ETag: publishing mints a new id, so an unchanged
    id means unchanged content and the poll can be answered with a 304.
    """
    current = await redis.get(SESSION_KEY.format(sid=session_id))
    if current is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    view_id = current.decode()

    etag = f'"{view_id}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})

    payload = await read_view(view_id, redis)
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


@app.get("/api/view/{view_id}/thumb/{index}")
async def thumbnail(view_id: str, index: int, redis: RedisDep) -> Response:
    """Proxy the nth row's screenshot.

    Addressed by index rather than URL so a caller can never steer this at a
    host of their choosing; the target still has to clear the allowlist.
    """
    record = await _load(redis, view_id)
    rows = record["data"]
    if index < 0 or index >= len(rows):
        raise HTTPException(status_code=404, detail="No such result index")

    source = rows[index].get("screenshot_url")
    if not _proxyable(source):
        raise HTTPException(status_code=404, detail="Thumbnail source is not permitted by the proxy allowlist")

    client: httpx.AsyncClient = app.state.http
    try:
        upstream = await client.get(source)
    except httpx.HTTPError as exc:
        logger.warning("view %s thumb %d: upstream error: %s", view_id, index, exc)
        raise HTTPException(status_code=502, detail="Thumbnail source unreachable") from exc

    if upstream.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Thumbnail source returned {upstream.status_code}")

    body = upstream.content
    if len(body) > settings.THUMB_MAX_BYTES:
        raise HTTPException(status_code=502, detail="Thumbnail exceeds SHARE_THUMB_MAX_BYTES")

    content_type = upstream.headers.get("content-type", "image/jpeg")
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=502, detail=f"Thumbnail source returned non-image {content_type!r}")

    return Response(
        content=body,
        media_type=content_type,
        # Immutable: a published view never changes, so a viewer and any chat
        # channel's link unfurler can cache freely for the view's lifetime.
        headers={"Cache-Control": f"public, max-age={settings.TTL_SECONDS}, immutable"},
    )


@app.get("/api/view/{view_id}/preview.png")
async def preview(view_id: str, redis: RedisDep) -> Response:
    """Open Graph card for the view, cached in Redis after first render."""
    cache_key = PREVIEW_KEY.format(id=view_id)
    cached = await redis.get(cache_key)
    if cached is not None:
        return Response(content=cached, media_type="image/png")

    record = await _load(redis, view_id)
    rows = record["data"]

    client: httpx.AsyncClient = app.state.http
    thumbs: list[bytes] = []
    for row in rows[:4]:
        source = row.get("screenshot_url")
        if not _proxyable(source):
            continue
        try:
            upstream = await client.get(source)
            if upstream.status_code == 200 and len(upstream.content) <= settings.THUMB_MAX_BYTES:
                thumbs.append(upstream.content)
        except httpx.HTTPError as exc:
            # A missing tile is cosmetic; the card must still render so the
            # shared link previews at all.
            logger.warning("view %s preview: thumbnail fetch failed: %s", view_id, exc)

    sensors = sorted({str(row.get("sensor_id", "")) for row in rows if row.get("sensor_id")})
    sensor_note = ", ".join(sensors[:3]) + ("…" if len(sensors) > 3 else "")
    subtitle = f"{len(rows)} result{'s' if len(rows) != 1 else ''}"
    if sensor_note:
        subtitle += f" · {sensor_note}"

    png = render_card(
        title=record["title"],
        subtitle=subtitle,
        footer=f"NVIDIA VSS · expires {record['expires_at'][:10]}",
        thumbnails=thumbs,
    )

    await redis.set(cache_key, png, ex=settings.TTL_SECONDS)
    return Response(content=png, media_type="image/png")
