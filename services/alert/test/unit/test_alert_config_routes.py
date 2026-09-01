# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""FastAPI route tests for /api/v1/verification/config (the alert-config REST API).

Mounts ``alert_config_router`` on a stand-alone FastAPI app and overrides
the service dependency with a fake backed by an in-process store so
behaviour is exercised end-to-end (router → schema → service → store)
without any external infrastructure.
"""

import importlib
import importlib.util
import os
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ``web`` is a proper importable package (src/ is on sys.path via the
# top-level conftest), so import the routes module directly.
from handlers.alert_config import AlertConfigService, AlertConfigStore  # noqa: E402
import web.api.alert_config_routes as _routes_mod  # noqa: E402

router = _routes_mod.router
_get_service = _routes_mod._get_service


@pytest.fixture
def client():
    fake_service = AlertConfigService(store=AlertConfigStore())
    app = FastAPI()
    app.include_router(router)
    # Routes call ``_get_service_async()`` inside their try/except (so a
    # build failure surfaces as 503 instead of bypassing the handler via
    # ``Depends``-time evaluation). The module-level
    # ``_service`` cache is the override point: pre-populating it makes
    # the first call return the fake without touching config / Redis /
    # ES. We restore the previous value on teardown so tests don't
    # leak the fake into one another.
    previous = getattr(_routes_mod, "_service", None)
    _routes_mod._service = fake_service
    try:
        yield TestClient(app)
    finally:
        _routes_mod._service = previous


# Reusable payloads ----------------------------------------------------------

def _payload(**overrides):
    base = {
        "alert_type": "collision",
        "prompt": "Analyze",
        "system_prompt": "Yes/No",
        "vlm_params": {"max_tokens": 256, "num_frames": 5},
        "output_category": "Vehicle Collision",
    }
    base.update(overrides)
    return base


# Endpoint tests -------------------------------------------------------------

class TestPostCreate:

    def test_post_201(self, client):
        resp = client.post("/api/v1/verification/config", json=_payload())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["alert_type"] == "collision"
        assert body["vlm_params"]["max_tokens"] == 256
        assert body["created_at"]

    def test_post_with_enrichment_prompt(self, client):
        resp = client.post(
            "/api/v1/verification/config",
            json=_payload(enrichment_prompt="Describe what happened"),
        )
        assert resp.status_code == 201
        assert resp.json()["enrichment_prompt"] == "Describe what happened"

    def test_post_without_enrichment_prompt_defaults_to_none(self, client):
        resp = client.post("/api/v1/verification/config", json=_payload())
        assert resp.json().get("enrichment_prompt") is None

    def test_post_duplicate_409(self, client):
        client.post("/api/v1/verification/config", json=_payload())
        resp = client.post("/api/v1/verification/config", json=_payload())
        assert resp.status_code == 409
        body = resp.json()
        assert body["status"] == "error"
        assert body["code"] == "config_exists"

    def test_post_validation_422_typo(self, client):
        bad = _payload()
        bad["vlm_params"]["max_token"] = 999  # typo
        resp = client.post("/api/v1/verification/config", json=bad)
        assert resp.status_code == 422

    def test_post_validation_422_unknown_top_level(self, client):
        bad = _payload(typo_field="x")
        resp = client.post("/api/v1/verification/config", json=bad)
        assert resp.status_code == 422

    def test_post_validation_422_empty_prompt(self, client):
        bad = _payload(prompt="")
        resp = client.post("/api/v1/verification/config", json=bad)
        assert resp.status_code == 422


class TestGet:

    def test_get_single_200(self, client):
        client.post("/api/v1/verification/config", json=_payload())
        resp = client.get("/api/v1/verification/config/collision")
        assert resp.status_code == 200
        assert resp.json()["alert_type"] == "collision"

    def test_get_single_404(self, client):
        resp = client.get("/api/v1/verification/config/never")
        assert resp.status_code == 404
        assert resp.json()["code"] == "config_not_found"

    def test_get_list_empty(self, client):
        resp = client.get("/api/v1/verification/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["configs"] == []

    def test_get_list_returns_all(self, client):
        client.post("/api/v1/verification/config", json=_payload())
        client.post("/api/v1/verification/config", json=_payload(alert_type="other"))
        resp = client.get("/api/v1/verification/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2


class TestPut:

    def test_put_deep_merges_vlm_params(self, client):
        client.post("/api/v1/verification/config", json=_payload())
        resp = client.put(
            "/api/v1/verification/config/collision",
            json={"vlm_params": {"max_tokens": 1024}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["vlm_params"]["max_tokens"] == 1024  # updated
        assert body["vlm_params"]["num_frames"] == 5     # preserved

    def test_put_404_for_missing(self, client):
        resp = client.put(
            "/api/v1/verification/config/missing",
            json={"prompt": "p"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "config_not_found"

    def test_put_422_for_typo_field(self, client):
        client.post("/api/v1/verification/config", json=_payload())
        resp = client.put(
            "/api/v1/verification/config/collision",
            json={"vlm_params": {"max_token": 256}},
        )
        assert resp.status_code == 422

    def test_put_explicit_null_clears_system_prompt(self, client):
        client.post("/api/v1/verification/config", json=_payload())
        resp = client.put(
            "/api/v1/verification/config/collision",
            json={"system_prompt": None},
        )
        assert resp.status_code == 200
        assert resp.json()["system_prompt"] is None

    def test_put_omitted_field_keeps_existing(self, client):
        client.post("/api/v1/verification/config", json=_payload())
        # Update only output_category — system_prompt and prompt must remain.
        resp = client.put(
            "/api/v1/verification/config/collision",
            json={"output_category": "Other"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["output_category"] == "Other"
        assert body["system_prompt"] == "Yes/No"
        assert body["prompt"] == "Analyze"


class TestDelete:

    def test_delete_200(self, client):
        client.post("/api/v1/verification/config", json=_payload())
        resp = client.delete("/api/v1/verification/config/collision")
        assert resp.status_code == 200

    def test_delete_then_get_404(self, client):
        client.post("/api/v1/verification/config", json=_payload())
        client.delete("/api/v1/verification/config/collision")
        resp = client.get("/api/v1/verification/config/collision")
        assert resp.status_code == 404

    def test_delete_missing_404(self, client):
        resp = client.delete("/api/v1/verification/config/missing")
        assert resp.status_code == 404


# Service construction ------------------------------------------------------
#
# Two properties of the lazy singleton that the route tests above cannot see,
# because their fixture pre-populates ``_service`` and so never builds one.

@pytest.fixture
def unbuilt_service():
    """Clear the cached service so a build actually happens."""
    previous = getattr(_routes_mod, "_service", None)
    _routes_mod._service = None
    try:
        yield
    finally:
        _routes_mod._service = previous


def test_concurrent_callers_build_exactly_one_store(unbuilt_service, monkeypatch):
    """Construction is single-flight.

    Startup and request handlers both reach the builder from worker threads,
    so without the lock several would see an empty cache at once and build a
    store apiece -- each opening its own Elasticsearch client, and each but
    the last silently discarded after being handed to a caller.
    """
    import threading

    builds = []
    # Forces every thread to be inside the builder simultaneously *if* they
    # can get there. Under the lock only one ever does, so the barrier is
    # never satisfied and it raises instead -- which is the observation: the
    # builder list stays at one.
    barrier = threading.Barrier(8, timeout=1)

    def build(_cfg):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass  # expected: the lock let only this one thread in
        obj = object()
        builds.append(obj)
        return obj

    monkeypatch.setattr(_routes_mod, "load_config", lambda: {})
    monkeypatch.setattr(_routes_mod, "build_alert_config_store", build)
    monkeypatch.setattr(_routes_mod, "AlertConfigService", lambda store: store)

    handed_out = []
    threads = [
        threading.Thread(target=lambda: handed_out.append(_routes_mod._get_service()))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "a caller never returned"

    # Exact counts, not upper bounds: a change that made construction always
    # raise would leave both lists empty, and "<= 1" would wave it through
    # while testing nothing.
    assert len(builds) == 1, f"built {len(builds)} stores concurrently, expected 1"
    assert len(handed_out) == 8, "not every caller received a store"
    assert len({id(s) for s in handed_out}) == 1, "callers received different stores"


@pytest.mark.asyncio
async def test_a_request_does_not_build_the_store_on_the_event_loop(unbuilt_service,
                                                                    monkeypatch):
    """Handlers must reach the service through the async accessor.

    Building talks to Elasticsearch synchronously and holds the construction
    lock while it does. A handler that called the blocking accessor directly
    would park the event loop for that whole time, so /health -- the endpoint
    a deployment gates on -- would stop answering for exactly as long as an
    unreachable Elasticsearch takes to fail.

    The app is driven in-process, so the loop runs on this very thread: if the
    build happened on the loop, ``built_on`` would be this thread.
    """
    import threading

    import httpx

    loop_thread = threading.current_thread()
    built_on = []

    def build(_cfg):
        built_on.append(threading.current_thread())
        return AlertConfigStore()

    monkeypatch.setattr(_routes_mod, "load_config", lambda: {})
    monkeypatch.setattr(_routes_mod, "build_alert_config_store", build)

    app = FastAPI()
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://testserver") as client:
        response = await client.get("/api/v1/verification/config")

    assert response.status_code == 200
    assert built_on, "the store was never built"
    assert built_on[0] is not loop_thread, (
        "the store was built on the event loop thread; /health would stall for "
        "the length of the build"
    )


@pytest.mark.asyncio
async def test_waiting_requests_do_not_occupy_worker_threads(unbuilt_service,
                                                             monkeypatch):
    """Only one build may be in flight, and it may cost only one thread.

    asyncio.to_thread draws from the loop's default executor, which this
    process shares with the realtime and incident services. That pool is
    min(32, cpu+4) threads -- 8 on a 4-core pod. Letting every waiting config
    request take a slot and block there on the construction mutex would fill
    the pool with threads that cannot make progress, and unrelated endpoints
    would stall behind them for the length of an Elasticsearch outage. The
    waiting has to happen on the loop, where it costs nothing.
    """
    import asyncio as _asyncio
    import threading

    entered = []
    release = threading.Event()

    def blocking_build():
        entered.append(threading.current_thread())
        release.wait(timeout=5)
        return object()

    monkeypatch.setattr(_routes_mod, "_get_service", blocking_build)

    tasks = [_asyncio.create_task(_routes_mod._get_service_async())
             for _ in range(8)]
    try:
        # Wait for the first builder rather than for a fixed delay, so a slow
        # machine cannot make this pass by having reached nobody yet.
        for _ in range(200):
            if entered:
                break
            await _asyncio.sleep(0.01)
        assert entered, "no builder ever started"
        await _asyncio.sleep(0.1)  # settle: let any others through if they can

        assert len(entered) == 1, (
            f"{len(entered)} worker threads were consumed by one build; "
            "waiting callers must queue on the event loop, not in the executor"
        )
    finally:
        release.set()
        await _asyncio.gather(*tasks)
