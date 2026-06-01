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
the service dependency with a fake backed by ``fakeredis`` so behaviour
is exercised end-to-end (router → schema → service → store) without any
external infrastructure.
"""

import importlib
import importlib.util
import os
import sys
import types

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Make the project root importable for handlers/* — but the root-level
# ``app.py`` shadows the ``app`` namespace inside ``alert-agent-web``.
# Build the ``app.api`` package manually so the routes module's relative
# imports resolve cleanly under pytest.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO_ROOT)
from handlers.alert_config import AlertConfigService, AlertConfigStore  # noqa: E402

_API_DIR = os.path.join(_REPO_ROOT, "alert-agent-web", "app", "api")


def _load_module(qualname: str, file_name: str):
    spec = importlib.util.spec_from_file_location(
        qualname, os.path.join(_API_DIR, file_name)
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualname] = module
    spec.loader.exec_module(module)
    return module


# Register synthetic parent packages first so relative imports work.
sys.modules.pop("app", None)  # drop the root-level app.py shadow
_app_pkg = types.ModuleType("app")
_app_pkg.__path__ = [os.path.join(_REPO_ROOT, "alert-agent-web", "app")]
sys.modules["app"] = _app_pkg

_app_api_pkg = types.ModuleType("app.api")
_app_api_pkg.__path__ = [_API_DIR]
sys.modules["app.api"] = _app_api_pkg

# Load schemas + the schemas dependency before the routes module.
_load_module("app.api.alert_schemas", "alert_schemas.py")
_load_module("app.api.alert_config_schemas", "alert_config_schemas.py")
_routes_mod = _load_module("app.api.alert_config_routes", "alert_config_routes.py")

router = _routes_mod.router
_get_service = _routes_mod._get_service


@pytest.fixture
def client():
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    fake_service = AlertConfigService(store=AlertConfigStore(redis_client))
    app = FastAPI()
    app.include_router(router)
    # Routes call ``_get_service()`` directly inside their try/except
    # (so a build failure surfaces as 503 instead of bypassing the
    # handler via ``Depends``-time evaluation). The module-level
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
