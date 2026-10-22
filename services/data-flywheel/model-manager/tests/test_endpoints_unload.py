# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""API-layer tests for POST /v1/models/unload: the wire body a caller receives.

``raise_server_exceptions=False`` makes a 500 come back as a response instead of
re-raising, which is how a bare 500 once shipped unnoticed here. ``_ServingFake``
answers inventory as well as routing state, or ``_evict`` fails closed with a 503
and every "nothing was deleted" assertion passes for the wrong reason.
"""

import pytest
from fastapi.testclient import TestClient

from main import app
from src.adapters.base import (
    EvictionAttestation,
    ServingAdapter,
    ServingState,
    VersionInventory,
)
from src.core.manager import ModelManager

NAME = "changenet_classify"


class _ServingFake(ServingAdapter):
    """A backend where v1 is retired and idle and v2 is the live default."""

    def __init__(self, *, deletable=("1",)):
        self._deletable = list(deletable)
        self.unload_calls: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    def stage(self, name, version, artifact_uri, sha256, **kwargs):
        raise AssertionError("unload must not stage anything")

    def apply_control(self, **kwargs):
        raise AssertionError("unload must not write routing")

    def serving_state(self, name, *, destructive=False):
        return ServingState(
            default_version="2",
            routing=[],
            serving_versions={"2"},
            loaded_versions=["1", "2"],
            ready=True,
            routing_available=True,
            loaded_available=True,
            ready_versions=["2"],
            resident_versions=["1", "2"],
        )

    def inventory(self, name, *, evicted=None):
        return VersionInventory(
            model_name=name, versions=[], deletable=list(self._deletable), available=True
        )

    def unload(self, name, version):
        self.unload_calls.append((name, version))
        return EvictionAttestation(version=version, evicted_affirmed=True)

    def delete_version(self, name, version):
        self.deleted.append((name, version))


@pytest.fixture
def client(monkeypatch):
    """TestClient over an injected adapter; never re-raises server exceptions."""

    def _install(adapter):
        monkeypatch.setattr("src.api.endpoints.model_manager", ModelManager(adapter=adapter))
        return TestClient(app, raise_server_exceptions=False)

    return _install


def test_the_unload_route_answers_200_and_omits_disk_reclaimed(client):
    """Equality, not key checks: only ``==`` catches a stray ``disk_reclaimed``."""
    adapter = _ServingFake()
    response = client(adapter).post("/v1/models/unload", json={"model_name": NAME, "version": "1"})

    assert response.status_code == 200, response.text
    assert response.json() == {"version": "1", "status": "removed"}
    assert "disk_reclaimed" not in response.json()
    assert adapter.unload_calls == [(NAME, "1")]
    assert adapter.deleted == [], "the eviction verb reclaimed disk"


def test_the_unload_route_refuses_the_live_version_with_the_error_envelope(client):
    """A refusal comes back as the 409 envelope, not as a bare 500."""
    adapter = _ServingFake()
    response = client(adapter).post("/v1/models/unload", json={"model_name": NAME, "version": "2"})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"
    assert adapter.unload_calls == []
    assert adapter.deleted == []
