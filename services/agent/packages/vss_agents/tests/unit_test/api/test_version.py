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

"""Tests for the VSS deployment version endpoint."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from vss_agents.api.version import register_version_route


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_version_route(app)
    return TestClient(app)


@pytest.mark.parametrize(
    "version",
    [
        "3.3.0",
        "3.3.0-65576357eb80",  # the Helm chart default
        "3.3.0-rc.1+build.42",
    ],
)
def test_version_endpoint_returns_configured_semver(client: TestClient, monkeypatch, version: str) -> None:
    monkeypatch.setenv("VSS_AGENT_VERSION", version)

    response = client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {"service": "vss", "version": version}


@pytest.mark.parametrize("version", [None, "", "develop-latest", "3.3"])
def test_version_endpoint_rejects_unusable_version(
    client: TestClient,
    monkeypatch,
    version: str | None,
) -> None:
    if version is None:
        monkeypatch.delenv("VSS_AGENT_VERSION", raising=False)
    else:
        monkeypatch.setenv("VSS_AGENT_VERSION", version)

    response = client.get("/api/v1/version")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The deployed VSS version is unavailable or is not valid Semantic Versioning 2.0.0."
    }
