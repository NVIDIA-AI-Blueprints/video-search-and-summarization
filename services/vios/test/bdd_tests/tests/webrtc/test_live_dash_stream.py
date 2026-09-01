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

"""End-to-end validation for shared live MPEG-DASH delivery."""

import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

import pytest
import requests
from pytest_bdd import given, scenarios, then, when

scenarios("../../features/webrtc/live_dash_stream.feature")


@dataclass
class DashContext:
    """State shared by DASH BDD steps."""

    stream_id: str = ""
    viewer_id: str = ""
    manifest_url: str = ""
    manifest_response: Optional[requests.Response] = None


@pytest.fixture
def dash_context() -> DashContext:
    """Create scenario-local DASH state."""

    return DashContext()


@given("the live DASH API is configured")
def dash_api_configured(api_config: dict) -> None:
    """Validate common API test configuration."""

    assert api_config.get("base_url"), "api.base_url is required"


@when("a DASH viewer is started for an available live stream")
def start_dash_viewer(dash_context: DashContext, api_config: dict, config: dict) -> None:
    """Select the first live stream and acquire a DASH viewer lease."""

    timeout = config["tests"]["webrtc_tests"]["test_parameters"]["timeout"]
    base_url = api_config["base_url"]
    streams_response = requests.get(
        f"{base_url}/vst/api/v1/live/streams",
        timeout=timeout,
        verify=api_config.get("verify_ssl", False),
    )
    streams_response.raise_for_status()
    streams = streams_response.json()
    stream_ids = [
        stream_id
        for stream_entry in streams
        if isinstance(stream_entry, dict)
        for stream_id in stream_entry
        if not stream_id.startswith("test_upload_")
    ]
    assert stream_ids, "No live stream is available for DASH validation"
    dash_context.stream_id = stream_ids[0]

    start_response = requests.post(
        f"{base_url}/vst/api/v1/live/dash/start",
        json={"streamId": dash_context.stream_id},
        headers={"streamid": dash_context.stream_id},
        timeout=timeout,
        verify=api_config.get("verify_ssl", False),
    )
    start_response.raise_for_status()
    payload = start_response.json()
    payload = payload.get("data", payload)
    dash_context.viewer_id = payload["viewerId"]
    dash_context.manifest_url = urljoin(base_url, payload["manifestUrl"])


@then("the DASH manifest becomes available")
def fetch_dash_manifest(dash_context: DashContext, api_config: dict, config: dict) -> None:
    """Poll the non-blocking endpoint until the MPD is ready."""

    timeout = config["tests"]["webrtc_tests"]["test_parameters"]["timeout"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = requests.get(
            dash_context.manifest_url,
            timeout=timeout,
            verify=api_config.get("verify_ssl", False),
        )
        if response.status_code == 200:
            dash_context.manifest_response = response
            break
        assert response.status_code == 202, f"Unexpected MPD response: {response.status_code} {response.text}"
        time.sleep(1)

    assert dash_context.manifest_response is not None, "DASH manifest did not become ready"
    assert "<MPD" in dash_context.manifest_response.text
    assert "video/mp4" in dash_context.manifest_response.text


@then("the DASH viewer lease can be released")
def stop_dash_viewer(dash_context: DashContext, api_config: dict, config: dict) -> None:
    """Release the viewer without destroying other viewers of the shared stream."""

    timeout = config["tests"]["webrtc_tests"]["test_parameters"]["timeout"]
    response = requests.post(
        f"{api_config['base_url']}/vst/api/v1/live/dash/stop",
        json={"viewerId": dash_context.viewer_id},
        headers={"streamid": dash_context.stream_id},
        timeout=timeout,
        verify=api_config.get("verify_ssl", False),
    )
    response.raise_for_status()
