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

import importlib.util
from pathlib import Path
from types import ModuleType

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from vss_agents.api.version import SEMVER_PATTERN
from vss_agents.api.version import register_version_route
import vss_core.version

_UNAVAILABLE_DETAIL = "The deployed VSS version is unavailable or is not valid Semantic Versioning 2.0.0."

# Every value the endpoint and the ci-vss-oss eval preflight
# (eval/scripts/tests/vss_version.py) must agree on.
ACCEPTED_VERSIONS = ["3.3.0", "3.3.0-65576357eb80", "3.3.0-rc.1+build.42"]
REJECTED_VERSIONS = ["03.3.0", "3.3.0-.", "3.3.0-01", "v1.0.0", "3.3", "develop-latest", ""]


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_version_route(app)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_version_env(monkeypatch) -> None:
    """Start every test from a deployment that configures no version at all.

    The git-tag fallback is neutralised too: these tests are about what the
    endpoint does with its environment, and the test process runs inside a
    checkout that ``git describe`` would happily answer for. The fallback has
    its own tests below, and :mod:`vss_core.version` covers the derivation.
    """
    monkeypatch.delenv("VSS_DEPLOYMENT_VERSION", raising=False)
    monkeypatch.delenv("VSS_AGENT_VERSION", raising=False)
    monkeypatch.setattr(vss_core.version, "describe_version", lambda *_: None)


@pytest.mark.parametrize("version", ACCEPTED_VERSIONS)
def test_version_endpoint_returns_configured_semver(client: TestClient, monkeypatch, version: str) -> None:
    monkeypatch.setenv("VSS_DEPLOYMENT_VERSION", version)

    response = client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {"service": "vss", "version": version}


@pytest.mark.parametrize("version", REJECTED_VERSIONS)
def test_version_endpoint_rejects_invalid_semver(client: TestClient, monkeypatch, version: str) -> None:
    """``03.3.0``, ``3.3.0-.`` and ``3.3.0-01`` are what the relaxed pattern let through."""
    monkeypatch.setenv("VSS_DEPLOYMENT_VERSION", version)

    response = client.get("/api/v1/version")

    assert response.status_code == 503
    assert response.json() == {"detail": _UNAVAILABLE_DETAIL}


def test_version_endpoint_503s_when_nothing_can_be_resolved(client: TestClient) -> None:
    """No deployment environment and no git tags to fall back on: a container."""
    response = client.get("/api/v1/version")

    assert response.status_code == 503
    assert response.json() == {"detail": _UNAVAILABLE_DETAIL}


def test_unconfigured_checkout_reports_its_derived_version(client: TestClient, monkeypatch) -> None:
    """A bare ``nat serve`` from a clone answers with the source it is running.

    Without this it answered 503, which a benchmark reads as indeterminate and
    stops on — even though the version was sitting right there in git.
    """
    monkeypatch.setattr(vss_core.version, "describe_version", lambda *_: "3.2.1-dev.1519+gc85c4a4e8")

    response = client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {"service": "vss", "version": "3.2.1-dev.1519+gc85c4a4e8"}


def test_configured_version_outranks_the_derived_one(client: TestClient, monkeypatch) -> None:
    """A deployment's own statement wins: its image need not match this checkout."""
    monkeypatch.setenv("VSS_DEPLOYMENT_VERSION", "3.3.0")
    monkeypatch.setattr(vss_core.version, "describe_version", lambda *_: "3.2.1-dev.1519+gc85c4a4e8")

    response = client.get("/api/v1/version")

    assert response.json() == {"service": "vss", "version": "3.3.0"}


def test_dedicated_variable_wins_over_legacy(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("VSS_DEPLOYMENT_VERSION", "3.4.0")
    monkeypatch.setenv("VSS_AGENT_VERSION", "3.3.0")

    response = client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {"service": "vss", "version": "3.4.0"}


def test_legacy_variable_is_used_when_dedicated_one_is_unset(client: TestClient, monkeypatch) -> None:
    """Helm and bare ``nat serve`` deployments that only set VSS_AGENT_VERSION keep working."""
    monkeypatch.setenv("VSS_AGENT_VERSION", "3.3.0-65576357eb80")

    response = client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {"service": "vss", "version": "3.3.0-65576357eb80"}


def test_empty_dedicated_variable_falls_through_to_legacy(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("VSS_DEPLOYMENT_VERSION", "  ")
    monkeypatch.setenv("VSS_AGENT_VERSION", "3.3.0")

    response = client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {"service": "vss", "version": "3.3.0"}


def test_invalid_dedicated_variable_does_not_fall_through_to_legacy(client: TestClient, monkeypatch) -> None:
    """A set-but-wrong value is reported as a problem, not masked by the fallback."""
    monkeypatch.setenv("VSS_DEPLOYMENT_VERSION", "develop-latest")
    monkeypatch.setenv("VSS_AGENT_VERSION", "3.3.0")

    response = client.get("/api/v1/version")

    assert response.status_code == 503
    assert response.json() == {"detail": _UNAVAILABLE_DETAIL}


def test_legacy_variable_carrying_an_image_tag_503s(client: TestClient, monkeypatch) -> None:
    """VSS_AGENT_VERSION also drives image-tag resolution, so it often holds a tag.

    A set-but-unusable variable does not reach the git fallback either: the
    deployment stated a version and the statement is wrong, which is worth
    surfacing rather than papering over with the checkout's version.
    """
    monkeypatch.setattr(vss_core.version, "describe_version", lambda *_: "3.2.1-dev.1519+gc85c4a4e8")
    monkeypatch.setenv("VSS_AGENT_VERSION", "develop-8f4eb94707ba")

    response = client.get("/api/v1/version")

    assert response.status_code == 503


def _load_checker_script() -> ModuleType:
    """Import ``scripts/check_vss_version.py`` by path (it is stdlib-only)."""
    script = Path(__file__).resolve().parents[5] / "scripts" / "check_vss_version.py"
    spec = importlib.util.spec_from_file_location("check_vss_version", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_standalone_checker_uses_the_same_pattern() -> None:
    """scripts/check_vss_version.py duplicates the pattern to stay stdlib-only.

    It is the benchmark team's copy-and-run tool, so it must not import VSS.
    This asserts the duplicate cannot drift from the source of truth in
    ``vss_core.version``.
    """
    assert _load_checker_script().SEMVER_PATTERN.pattern == SEMVER_PATTERN.pattern


@pytest.mark.parametrize("version", ACCEPTED_VERSIONS)
def test_standalone_checker_accepts_the_same_versions(version: str) -> None:
    assert _load_checker_script().SEMVER_PATTERN.fullmatch(version)


@pytest.mark.parametrize("version", REJECTED_VERSIONS)
def test_standalone_checker_rejects_the_same_versions(version: str) -> None:
    assert _load_checker_script().SEMVER_PATTERN.fullmatch(version) is None
