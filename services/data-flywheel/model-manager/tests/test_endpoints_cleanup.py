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

"""API-layer tests for POST /v1/models/cleanup: the body a caller receives.

Driven over the real app because ``response_model`` is part of the contract: a
``status`` value it does not declare is a 500 at serialisation time, which no
unit test building the response object would ever see.
"""

import pytest
from fastapi.testclient import TestClient

from main import app
from src.adapters.base import ServingAdapter, ServingState, VersionInventory
from src.config import settings
from src.core.manager import ModelManager

NAME = "changenet_classify"


class _ServingFake(ServingAdapter):
    """A backend where v1 is retired and idle and v2 is the live default."""

    def __init__(self, *, deletable=()):
        self._deletable = list(deletable)
        self.deleted: list[tuple[str, str]] = []

    def stage(self, name, version, artifact_uri, sha256, **kwargs):
        raise AssertionError("cleanup must not stage anything")

    def apply_control(self, **kwargs):
        raise AssertionError("cleanup must not write routing")

    def serving_state(self, name, *, destructive=False):
        return ServingState(
            default_version="2",
            routing=[],
            serving_versions={"2"},
            loaded_versions=["2"],
            ready=True,
            routing_available=True,
            loaded_available=True,
            ready_versions=["2"],
            resident_versions=["2"],
        )

    def inventory(self, name, *, reclaiming=None, evicted_by_caller=False):
        return VersionInventory(
            model_name=name, versions=[], deletable=list(self._deletable), available=True
        )

    def unload(self, name, version):
        raise AssertionError("the reclaim path must never unload")

    def delete_version(self, name, version):
        self.deleted.append((name, version))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient over an injected adapter, against a real model repository.

    Real because the shipped default ``/models`` does not exist under test, so
    every case would take the already-gone branch.
    """
    repo = tmp_path / "repo"
    (repo / NAME).mkdir(parents=True)
    monkeypatch.setattr(settings, "model_repo_path", str(repo))

    def _install(adapter, *, on_disk=()):
        for version in on_disk:
            (repo / NAME / version).mkdir()
        monkeypatch.setattr("src.api.endpoints.model_manager", ModelManager(adapter=adapter))
        return TestClient(app, raise_server_exceptions=False)

    return _install


def test_a_directory_left_in_place_comes_back_as_kept(client):
    """The refusal that is a 200. ``status`` was hardcoded to "removed" until this
    release, so a key-presence check would have passed right through the bug.
    """
    adapter = _ServingFake(deletable=[])
    response = client(adapter, on_disk=["1"]).post(
        "/v1/models/cleanup", json={"model_name": NAME, "version": "1"}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"version": "1", "status": "kept", "disk_reclaimed": False}
    assert adapter.deleted == []


def test_a_directory_that_is_already_gone_comes_back_reclaimed(client):
    """Measured live: this returned ``disk_reclaimed: false``, byte-identical to
    the refusal above, so the sweep re-offered the version every tick forever.
    """
    adapter = _ServingFake(deletable=[])
    response = client(adapter).post("/v1/models/cleanup", json={"model_name": NAME, "version": "1"})

    assert response.status_code == 200, response.text
    assert response.json() == {"version": "1", "status": "removed", "disk_reclaimed": True}
    assert adapter.deleted == [], "there was nothing on disk to delete"
