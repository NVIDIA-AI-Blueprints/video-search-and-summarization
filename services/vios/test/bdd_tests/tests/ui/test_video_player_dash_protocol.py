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

"""Browser BDD coverage for switching between WebRTC and live MPEG-DASH."""

from dataclasses import dataclass, field
from typing import Any

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pytest_bdd import given, scenarios, then, when

scenarios("../../features/ui/video_player_dash_protocol.feature")


@dataclass
class DashUiContext:
    """State shared by UI protocol-switching steps."""

    page: Any = None
    first_frame_seen: bool = False
    stop_response_seen: bool = False
    manifest_statuses: list[int] = field(default_factory=list)
    media_segment_seen: bool = False


@pytest.fixture
def dash_ui_context() -> DashUiContext:
    """Create scenario-local browser state."""

    return DashUiContext()


@given("the VIOS live-stream page has a DASH-capable video player")
def open_live_player(dash_ui_context: DashUiContext, browser_page: Any, ui_base_url: str) -> None:
    """Open the first available live sensor."""

    browser_page.on(
        "console",
        lambda message: setattr(dash_ui_context, "first_frame_seen", True)
        if message.text == "on First FrameReceived"
        else None,
    )
    browser_page.on(
        "response",
        lambda response: setattr(dash_ui_context, "stop_response_seen", True)
        if "/api/v1/live/dash/stop" in response.url and response.ok
        else None,
    )
    browser_page.on(
        "response",
        lambda response: dash_ui_context.manifest_statuses.append(response.status)
        if "/dash/" in response.url and response.url.endswith(".mpd")
        else None,
    )
    browser_page.on(
        "response",
        lambda response: setattr(dash_ui_context, "media_segment_seen", True)
        if "/dash/" in response.url and response.status == 200 and response.url.endswith((".m4s", ".mp4"))
        else None,
    )
    browser_page.goto(f"{ui_base_url}/live-streams", wait_until="domcontentloaded")
    sensor_input = browser_page.get_by_role("combobox", name="Select Sensors")
    sensor_input.wait_for(state="visible")
    sensor_input.click()
    first_sensor = browser_page.get_by_role("option").first
    try:
        first_sensor.wait_for(state="visible", timeout=5_000)
    except PlaywrightTimeoutError:
        pytest.skip("No live sensor is available in the VIOS deployment")
    first_sensor.click()
    browser_page.get_by_role("button", name="DASH", exact=True).wait_for(state="visible", timeout=10_000)
    dash_ui_context.page = browser_page


@then("WebRTC is the selected delivery protocol")
def webrtc_is_default(dash_ui_context: DashUiContext) -> None:
    """Verify the default remains WebRTC."""

    button = dash_ui_context.page.get_by_role("button", name="WebRTC", exact=True)
    assert "MuiButton-contained" in (button.get_attribute("class") or "")


@when("I switch the live player to DASH")
def switch_to_dash(dash_ui_context: DashUiContext) -> None:
    """Select DASH delivery."""

    dash_ui_context.first_frame_seen = False
    dash_ui_context.page.get_by_role("button", name="DASH", exact=True).click()


@then("the DASH player reports its first frame")
def dash_first_frame(dash_ui_context: DashUiContext) -> None:
    """Wait for the existing first-frame test signal."""

    dash_ui_context.page.wait_for_function(
        "() => document.querySelector('video')?.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA",
        timeout=60_000,
    )
    assert dash_ui_context.first_frame_seen
    assert 200 in dash_ui_context.manifest_statuses
    assert dash_ui_context.media_segment_seen
    assert dash_ui_context.page.evaluate("() => document.querySelector('video')?.currentTime > 0")


@then("WebRTC-only quality controls are hidden")
def webrtc_controls_hidden(dash_ui_context: DashUiContext) -> None:
    """Verify WebRTC quality selection is not exposed in DASH mode."""

    assert dash_ui_context.page.get_by_role("button", name="Quality Settings", exact=True).count() == 0


@when("I switch the live player back to WebRTC")
def switch_back_to_webrtc(dash_ui_context: DashUiContext) -> None:
    """Return to the default delivery mode."""

    dash_ui_context.page.get_by_role("button", name="WebRTC", exact=True).click()


@then("the DASH viewer lease is released")
def dash_lease_released(dash_ui_context: DashUiContext) -> None:
    """Verify protocol cleanup called the viewer-stop API."""

    dash_ui_context.page.wait_for_function(
        "() => document.querySelector('button.MuiButton-contained')?.textContent?.includes('WebRTC')",
    )
    dash_ui_context.page.wait_for_timeout(1_000)
    assert dash_ui_context.stop_response_seen
