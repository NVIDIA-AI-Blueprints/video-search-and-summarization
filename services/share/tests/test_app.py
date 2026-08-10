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

"""Behavioural tests for the share service.

Redis is faked and every upstream thumbnail fetch is stubbed, so the suite runs
with no containers.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

import fakeredis.aioredis
from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from vss_share import app as app_module
from vss_share import settings

ALLOWED = "http://vst-ingress:30888/"


def _row(index: int = 0, *, url: str | None = None) -> dict[str, Any]:
    return {
        "video_name": f"clip{index}.mp4",
        "description": f"Person walking {index}",
        "start_time": "2026-01-15T09:00:00",
        "end_time": "2026-01-15T09:05:00",
        "sensor_id": f"sensor-{index}",
        "similarity": 0.9 - index / 100,
        "screenshot_url": ALLOWED + f"vst/api/v1/replay/stream/{index}/picture" if url is None else url,
        "object_ids": [],
    }


def _png_bytes(size: tuple[int, int] = (320, 180)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, (90, 120, 60)).save(buf, format="PNG")
    return buf.getvalue()


class _StubHTTP:
    """Stands in for httpx.AsyncClient, recording what was requested."""

    def __init__(self, *, status: int = 200, content: bytes | None = None, content_type: str = "image/png"):
        self.status = status
        self.content = _png_bytes() if content is None else content
        self.content_type = content_type
        self.requested: list[str] = []
        self.raise_error = False

    async def get(self, url: str) -> httpx.Response:
        self.requested.append(url)
        if self.raise_error:
            raise httpx.ConnectError("stubbed failure")
        return httpx.Response(
            status_code=self.status,
            content=self.content,
            headers={"content-type": self.content_type},
            request=httpx.Request("GET", url),
        )

    async def aclose(self) -> None:  # pragma: no cover - lifespan tidy-up
        return None


@pytest.fixture
def stub_http() -> _StubHTTP:
    return _StubHTTP()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, stub_http: _StubHTTP):
    monkeypatch.setattr(settings, "THUMB_ALLOWED_PREFIXES", [ALLOWED])
    monkeypatch.setattr(app_module.settings, "THUMB_ALLOWED_PREFIXES", [ALLOWED])

    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)

    with TestClient(app_module.app) as test_client:
        # TestClient runs the real lifespan; swap the live clients afterwards so
        # nothing reaches an actual Redis or VST.
        app_module.app.state.redis = fake
        app_module.app.state.http = stub_http
        yield test_client


# --- publish / read ------------------------------------------------------


def test_publish_returns_id_and_count(client):
    resp = client.post("/api/view", json={"data": [_row(0), _row(1)], "query": "forklifts"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert len(body["id"]) >= 20  # 128 bits, url-safe
    assert body["expires_at"]


def test_read_roundtrip_preserves_rows(client):
    view_id = client.post("/api/view", json={"data": [_row(0)], "query": "forklifts"}).json()["id"]

    body = client.get(f"/api/view/{view_id}").json()
    assert body["count"] == 1
    assert body["title"] == "forklifts"
    assert body["data"][0]["video_name"] == "clip0.mp4"
    assert body["data"][0]["similarity"] == 0.9


def test_screenshot_url_is_rewritten_to_proxy_path(client):
    """Originals must not reach the client -- they are private-network URLs."""
    view_id = client.post("/api/view", json={"data": [_row(0), _row(1)]}).json()["id"]

    rows = client.get(f"/api/view/{view_id}").json()["data"]
    assert rows[0]["screenshot_url"] == f"/api/view/{view_id}/thumb/0"
    assert rows[1]["screenshot_url"] == f"/api/view/{view_id}/thumb/1"
    assert ALLOWED not in client.get(f"/api/view/{view_id}").text


def test_disallowed_screenshot_url_yields_empty_not_passthrough(client):
    """A row pointing outside the allowlist must not leak its URL to the page."""
    view_id = client.post("/api/view", json={"data": [_row(0, url="http://evil.example/x.jpg")]}).json()["id"]

    rows = client.get(f"/api/view/{view_id}").json()["data"]
    assert rows[0]["screenshot_url"] == ""


def test_missing_view_is_404(client):
    assert client.get("/api/view/does-not-exist").status_code == 404


def test_empty_data_rejected(client):
    assert client.post("/api/view", json={"data": []}).status_code == 400


def test_too_many_rows_rejected(monkeypatch, client):
    monkeypatch.setattr(app_module.settings, "MAX_RESULTS", 2)
    resp = client.post("/api/view", json={"data": [_row(i) for i in range(3)]})
    assert resp.status_code == 413
    assert "SHARE_MAX_RESULTS" in resp.json()["detail"]


def test_oversized_payload_rejected(monkeypatch, client):
    monkeypatch.setattr(app_module.settings, "MAX_PAYLOAD_BYTES", 200)
    resp = client.post("/api/view", json={"data": [_row(i) for i in range(5)]})
    assert resp.status_code == 413
    assert "SHARE_MAX_PAYLOAD_BYTES" in resp.json()["detail"]


# --- thumbnail proxy -----------------------------------------------------


def test_thumbnail_proxies_allowlisted_source(client, stub_http):
    view_id = client.post("/api/view", json={"data": [_row(0)]}).json()["id"]

    resp = client.get(f"/api/view/{view_id}/thumb/0")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")
    assert stub_http.requested == [_row(0)["screenshot_url"]]


def test_thumbnail_index_out_of_range_is_404(client):
    view_id = client.post("/api/view", json={"data": [_row(0)]}).json()["id"]
    assert client.get(f"/api/view/{view_id}/thumb/7").status_code == 404


def test_thumbnail_refuses_source_outside_allowlist(client, stub_http):
    """The proxy must never fetch a host the operator did not permit."""
    view_id = client.post("/api/view", json={"data": [_row(0, url="http://evil.example/x.jpg")]}).json()["id"]

    assert client.get(f"/api/view/{view_id}/thumb/0").status_code == 404
    assert stub_http.requested == []  # never dialled out


def test_thumbnail_rejects_non_image_upstream(client, stub_http):
    stub_http.content_type = "text/html"
    view_id = client.post("/api/view", json={"data": [_row(0)]}).json()["id"]
    assert client.get(f"/api/view/{view_id}/thumb/0").status_code == 502


def test_thumbnail_upstream_failure_is_502(client, stub_http):
    stub_http.raise_error = True
    view_id = client.post("/api/view", json={"data": [_row(0)]}).json()["id"]
    assert client.get(f"/api/view/{view_id}/thumb/0").status_code == 502


# --- session slot --------------------------------------------------------


def test_session_slot_resolves_to_latest_view(client):
    first = client.post("/api/view", json={"data": [_row(0)], "session": "s1"}).json()["id"]
    body = client.get("/api/view/session/s1").json()
    assert body["id"] == first

    second = client.post("/api/view", json={"data": [_row(1), _row(2)], "session": "s1"}).json()["id"]
    body = client.get("/api/view/session/s1").json()
    assert body["id"] == second
    assert body["count"] == 2

    # The superseded view stays resolvable at its own id.
    assert client.get(f"/api/view/{first}").status_code == 200


def test_session_etag_returns_304_when_unchanged(client):
    view_id = client.post("/api/view", json={"data": [_row(0)], "session": "s2"}).json()["id"]

    first = client.get("/api/view/session/s2")
    assert first.status_code == 200
    assert first.headers["etag"] == f'"{view_id}"'

    again = client.get("/api/view/session/s2", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304


def test_session_etag_changes_after_republish(client):
    client.post("/api/view", json={"data": [_row(0)], "session": "s3"})
    stale = client.get("/api/view/session/s3").headers["etag"]

    client.post("/api/view", json={"data": [_row(1)], "session": "s3"})
    fresh = client.get("/api/view/session/s3", headers={"If-None-Match": stale})
    assert fresh.status_code == 200
    assert fresh.headers["etag"] != stale


def test_unknown_session_is_404(client):
    assert client.get("/api/view/session/nope").status_code == 404


# --- preview card --------------------------------------------------------


def test_preview_renders_png_at_og_dimensions(client):
    view_id = client.post("/api/view", json={"data": [_row(0), _row(1)], "query": "forklifts"}).json()["id"]

    resp = client.get(f"/api/view/{view_id}/preview.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"

    img = Image.open(BytesIO(resp.content))
    assert img.size == (1200, 630)


def test_preview_is_cached_after_first_render(client, stub_http):
    view_id = client.post("/api/view", json={"data": [_row(0)]}).json()["id"]

    client.get(f"/api/view/{view_id}/preview.png")
    fetches_after_first = len(stub_http.requested)
    client.get(f"/api/view/{view_id}/preview.png")

    assert len(stub_http.requested) == fetches_after_first  # served from cache


def test_preview_still_renders_when_every_thumbnail_fails(client, stub_http):
    """A shared link must preview even with no reachable imagery."""
    stub_http.raise_error = True
    view_id = client.post("/api/view", json={"data": [_row(0)]}).json()["id"]

    resp = client.get(f"/api/view/{view_id}/preview.png")
    assert resp.status_code == 200
    assert Image.open(BytesIO(resp.content)).size == (1200, 630)
