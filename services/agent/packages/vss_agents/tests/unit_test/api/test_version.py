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
ACCEPTED_VERSIONS = ["3.3.0", "3.3.0-65576357eb80", "3.3.0-rc.1+build.42", "3.3.0-rc0+tree.4d9b2c1"]
REJECTED_VERSIONS = ["03.3.0", "3.3.0-.", "3.3.0-01", "v1.0.0", "3.3", "develop-latest", ""]


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_version_route(app)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_installed_library(monkeypatch) -> None:
    """Start every test from a deployment whose library reports no version.

    The test process imports an installed ``nvidia-vss-core`` of its own; what
    that says must not decide these tests. Each test states what the library
    reports, and :mod:`vss_core.version`'s own tests cover the derivation.
    """
    monkeypatch.setattr(vss_core.version, "library_version", lambda: None)


@pytest.mark.parametrize("version", ACCEPTED_VERSIONS)
def test_version_endpoint_reports_the_library_version(client: TestClient, monkeypatch, version: str) -> None:
    """A deployment reports the version of the VSS library it imported.

    In an image the build stamped it from the release tag and the source tree
    (``3.3.0-rc0+tree.<sha>``); in a checkout ``hatch-vcs`` derived it.
    """
    monkeypatch.setattr(vss_core.version, "library_version", lambda: version)

    response = client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {"service": "vss", "version": version}


def test_version_endpoint_503s_when_the_library_reports_nothing(client: TestClient) -> None:
    """No installed version that normalises to SemVer: nothing to answer with."""
    response = client.get("/api/v1/version")

    assert response.status_code == 503
    assert response.json() == {"detail": _UNAVAILABLE_DETAIL}


def test_environment_does_not_override_the_library_version(client: TestClient, monkeypatch) -> None:
    """There is no deploy-time override: a version anyone can edit is one nobody can trust."""
    monkeypatch.setenv("VSS_DEPLOYMENT_VERSION", "9.9.9")
    monkeypatch.setattr(vss_core.version, "library_version", lambda: "3.3.0-rc0+tree.4d9b2c1")

    response = client.get("/api/v1/version")

    assert response.json() == {"service": "vss", "version": "3.3.0-rc0+tree.4d9b2c1"}


def test_local_build_stamp_is_served_as_is(client: TestClient, monkeypatch) -> None:
    """A bare `docker build` stamps 0.0.0+local: served, and unable to satisfy any skill's range."""
    monkeypatch.setattr(vss_core.version, "library_version", lambda: "0.0.0+local")

    response = client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {"service": "vss", "version": "0.0.0+local"}


@pytest.mark.parametrize("version", REJECTED_VERSIONS)
def test_contract_rejects_what_the_eval_preflight_rejects(version: str) -> None:
    """``03.3.0``, ``3.3.0-.`` and ``3.3.0-01`` are what the relaxed pattern let through."""
    assert SEMVER_PATTERN.fullmatch(version) is None


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
