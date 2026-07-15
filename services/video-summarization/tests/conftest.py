# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import base64
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

import prometheus_client as prom
import pytest


def _download_from_artifactory(url, dest):
    """Download a file from Artifactory. When credentials are set, use urllib with Basic auth
    (same approach as download_artifactory_test.py); otherwise try without auth and skip on 401/403.
    """
    parsed = urlparse(url)
    user = os.environ.get("ARTIFACTORY_USER", "").strip()
    token = os.environ.get("ARTIFACTORY_TOKEN", "").strip()
    is_artifactory = "artifactory" in (parsed.hostname or "") or "/artifactory/" in parsed.path

    if is_artifactory and user and token:
        creds = base64.b64encode(f"{user}:{token}".encode()).decode()
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {creds}"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                with open(dest, "wb") as out:
                    out.write(resp.read())
            if os.path.getsize(dest) < 10_000:
                pytest.skip(
                    f"Downloaded file from {url} is too small (likely error page). "
                    "Check ARTIFACTORY_USER and ARTIFACTORY_TOKEN "
                )
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                pytest.skip(
                    f"Artifactory returned {e.code} for {url}. "
                    "Check ARTIFACTORY_USER and ARTIFACTORY_TOKEN "
                )
            raise
    else:
        try:
            urllib.request.urlretrieve(url, dest)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                pytest.skip(
                    f"Artifactory returned {e.code} for {url}. "
                    "Set ARTIFACTORY_USER and ARTIFACTORY_TOKEN to run integration tests."
                )
            raise


# Base URL for lmm/streams (used by asset_manager unit tests and integration tests).
ARTIFACTORY_LMM_STREAMS_BASE = (
    "https://artifactory.nvidia.com/artifactory/sw-ds-generic-bld-local/lmm/streams"
)


def _get_artifactory_lmm_streams_mp4_path():
    """
    Return a path to an mp4 from Artifactory lmm/streams when credentials are set.
    Downloads to a cache path; returns None if credentials missing or download fails
    (no skip, so unit tests can fall back to fake files).
    """
    cache_path = "/tmp/lvs_asset_manager_test.mp4"
    min_size = 10_000
    if os.path.isfile(cache_path) and os.path.getsize(cache_path) >= min_size:
        return cache_path
    user = os.environ.get("ARTIFACTORY_USER", "").strip()
    token = os.environ.get("ARTIFACTORY_TOKEN", "").strip()
    if not user or not token:
        return None
    url = f"{ARTIFACTORY_LMM_STREAMS_BASE}/sample_1080p_h264.mp4"
    creds = base64.b64encode(f"{user}:{token}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {creds}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            with open(cache_path, "wb") as out:
                out.write(resp.read())
        if os.path.getsize(cache_path) >= min_size:
            return cache_path
    except (urllib.error.HTTPError, OSError):
        pass
    return None


@pytest.fixture(scope="session")
def artifactory_lmm_streams_mp4():
    """
    Path to an mp4 from Artifactory lmm/streams for unit tests.
    When ARTIFACTORY_USER and ARTIFACTORY_TOKEN are set, downloads (or uses cached)
    sample_1080p_h264.mp4 from sw-ds-generic-bld-local/lmm/streams.
    Returns None when credentials are not set or download fails, so tests can
    fall back to a fake file and still run without Artifactory access.
    """
    return _get_artifactory_lmm_streams_mp4_path()


@pytest.fixture(scope="session")
def integration_test_setup():
    """
    Heavy setup for integration tests that need video files and API keys.

    This is NOT autouse - tests must explicitly request it via:
    - Adding fixture parameter: def test_foo(integration_test_setup): ...
    - Or using @pytest.mark.usefixtures("integration_test_setup")
    """
    if not os.path.exists("/opt/nvidia/via/streams/concat_wh_52.mp4"):
        os.makedirs("/opt/nvidia/via/streams", exist_ok=True)
        _download_from_artifactory(
            "https://artifactory.nvidia.com/artifactory/sw-ds-generic-bld-local/lmm/streams/concat_wh_52.mp4",
            "/opt/nvidia/via/streams/concat_wh_52.mp4",
        )
    if not os.path.exists(
        "/opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h264.mp4"
    ):
        os.makedirs("/opt/nvidia/deepstream/deepstream/samples/streams", exist_ok=True)
        _download_from_artifactory(
            "https://artifactory.nvidia.com/artifactory/sw-ds-generic-bld-local/"
            "lmm/streams/sample_1080p_h264.mp4",
            "/opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h264.mp4",
        )

    required_env_vars = [
        "OPENAI_API_KEY",
        "NGC_API_KEY",
        "NVIDIA_API_KEY",
        "VIA_VLM_API_KEY",
        "HF_TOKEN",
    ]
    missing = [var for var in required_env_vars if not os.environ.get(var)]
    if missing:
        pytest.exit(
            f"Required environment variables not set or empty: {', '.join(missing)}. "
            f"Please set these variables to run the tests.",
            returncode=1,
        )
    os.environ["MAX_RAILS_INSTANCES"] = "1"


@pytest.fixture(autouse=True)
def use_temp_env(monkeypatch):
    monkeypatch.setenv("VIA_SKIP_PIPELINE_WARMUP", "1")
    yield


@pytest.fixture(autouse=True)
def reset_sse_appstatus_event():
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None

    import threading

    all_threads = threading.enumerate()
    main_thread = threading.main_thread()
    running_threads = [
        t for t in all_threads if t is not main_thread and t.is_alive() and not t.daemon
    ]
    if running_threads:
        print("Non-daemon threads still running: %s" % [t.name for t in running_threads])


@pytest.fixture(autouse=True)
def cleanup_prom_registry():
    for collector in list(prom.REGISTRY._names_to_collectors.values()):
        try:
            prom.REGISTRY.unregister(collector)
        except KeyError:
            # Handle the case where a collector is already unregistered
            pass


@pytest.fixture
def gpu_test_video():
    """Path to test video for GPU tests. Skip if file or GPU not available."""
    path = "/opt/nvidia/via/streams/warehouse.mp4"
    if not os.path.exists(path):
        pytest.skip(f"Test video not found: {path}")
    return path
