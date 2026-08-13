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

"""Browser BDD coverage for the VIOS video-player fullscreen controls."""

from dataclasses import dataclass
from typing import Any

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pytest_bdd import given, scenarios, then, when

scenarios("../../features/ui/video_player_fullscreen_controls.feature")

FULLSCREEN_OVERLAY_SELECTOR = '[data-testid="fullscreen-controls-overlay"]'
FULLSCREEN_CONTROL_NAMES = (
    "Take Screenshot",
    "Exit Fullscreen",
    "Analytics Overlay Settings",
    "Quality Settings",
)
QUALITY_OPTIONS = ("Low", "Medium", "High", "Auto", "Pass through")


@dataclass
class FullscreenContext:
    page: Any = None
    fullscreen_player: Any = None
    controls_overlay: Any = None
    quality_menu: Any = None


@pytest.fixture
def fullscreen_context() -> FullscreenContext:
    return FullscreenContext()


@given("the VIOS live-stream page has a video player")
def live_stream_page_has_video_player(
    fullscreen_context: FullscreenContext,
    browser_page: Any,
    ui_base_url: str,
) -> None:
    browser_page.goto(
        f"{ui_base_url}/live-streams",
        wait_until="domcontentloaded",
    )

    sensor_input = browser_page.get_by_role(
        "combobox",
        name="Select Sensors",
    )
    sensor_input.wait_for(state="visible")
    sensor_input.click()

    first_sensor = browser_page.get_by_role("option").first
    try:
        first_sensor.wait_for(state="visible", timeout=5_000)
    except PlaywrightTimeoutError:
        if browser_page.get_by_role("option").count() == 0:
            pytest.skip("No live sensor is available in the VIOS deployment")
        raise

    first_sensor.click()

    fullscreen_button = browser_page.locator("#fullscreen-control-btn").first
    fullscreen_button.wait_for(state="visible", timeout=10_000)
    fullscreen_context.page = browser_page


@when("I enter fullscreen from the video player controls")
def enter_fullscreen(fullscreen_context: FullscreenContext) -> None:
    page = fullscreen_context.page
    page.locator("#fullscreen-control-btn").first.click()
    page.wait_for_function("document.fullscreenElement !== null")
    fullscreen_context.fullscreen_player = page.locator(":fullscreen")
    fullscreen_context.controls_overlay = fullscreen_context.fullscreen_player.locator(
        FULLSCREEN_OVERLAY_SELECTOR
    )
    fullscreen_context.controls_overlay.wait_for(state="attached")
    fullscreen_context.controls_overlay.locator("button").first.wait_for(state="visible")


@then("the video player is the browser fullscreen element")
def video_player_is_fullscreen(fullscreen_context: FullscreenContext) -> None:
    assert (
        fullscreen_context.page.evaluate(
            "document.fullscreenElement === document.querySelector(':fullscreen')"
        )
        is True
    )


@then("all five live-stream controls are available in the fullscreen player")
def all_live_stream_controls_are_inside_fullscreen(
    fullscreen_context: FullscreenContext,
) -> None:
    fullscreen_player = fullscreen_context.fullscreen_player
    assert fullscreen_player.count() == 1

    buttons = fullscreen_player.get_by_role("button")
    assert buttons.count() == 5

    play_pause_control = fullscreen_player.get_by_role(
        "button",
        name="Pause",
        exact=True,
    )
    if play_pause_control.count() == 0:
        play_pause_control = fullscreen_player.get_by_role(
            "button",
            name="Play",
            exact=True,
        )
    assert play_pause_control.count() == 1
    assert play_pause_control.is_visible()
    assert play_pause_control.is_enabled()

    for control_name in FULLSCREEN_CONTROL_NAMES:
        control = fullscreen_player.get_by_role(
            "button",
            name=control_name,
            exact=True,
        )
        assert control.count() == 1
        assert control.is_visible()
        assert control.is_enabled()


@when("I open the fullscreen quality dropdown")
def open_fullscreen_quality_dropdown(
    fullscreen_context: FullscreenContext,
) -> None:
    fullscreen_context.fullscreen_player.get_by_role(
        "button",
        name="Quality Settings",
        exact=True,
    ).click()
    fullscreen_context.quality_menu = fullscreen_context.fullscreen_player.get_by_role("menu")
    fullscreen_context.quality_menu.wait_for(state="visible")


@then("all quality options are visible")
def all_quality_options_are_visible(
    fullscreen_context: FullscreenContext,
) -> None:
    menu_items = fullscreen_context.quality_menu.get_by_role("menuitem")
    assert menu_items.all_text_contents() == list(QUALITY_OPTIONS)
    for option in QUALITY_OPTIONS:
        assert fullscreen_context.quality_menu.get_by_role(
            "menuitem",
            name=option,
            exact=True,
        ).is_visible()


@when("I close the fullscreen quality dropdown")
def close_fullscreen_quality_dropdown(
    fullscreen_context: FullscreenContext,
) -> None:
    fullscreen_context.page.keyboard.press("Escape")


@then("the quality options are hidden")
def quality_options_are_hidden(fullscreen_context: FullscreenContext) -> None:
    fullscreen_context.quality_menu.wait_for(state="hidden")


@when("I leave the pointer idle over the fullscreen player")
def leave_pointer_idle(fullscreen_context: FullscreenContext) -> None:
    fullscreen_player = fullscreen_context.fullscreen_player
    bounds = fullscreen_player.bounding_box()
    assert bounds is not None
    fullscreen_context.page.mouse.move(
        bounds["x"] + bounds["width"] / 2,
        bounds["y"] + bounds["height"] / 2,
    )


@then("the fullscreen controls are hidden")
def fullscreen_controls_are_hidden(fullscreen_context: FullscreenContext) -> None:
    overlay = fullscreen_context.controls_overlay.element_handle()
    assert overlay is not None
    fullscreen_context.page.wait_for_function(
        """overlay => {
            const style = window.getComputedStyle(overlay);
            return style.opacity === '0' && style.pointerEvents === 'none';
        }""",
        arg=overlay,
        timeout=5_000,
    )


@when("I move the pointer over the fullscreen player")
def move_pointer_over_fullscreen(fullscreen_context: FullscreenContext) -> None:
    bounds = fullscreen_context.fullscreen_player.bounding_box()
    assert bounds is not None
    fullscreen_context.page.mouse.move(
        bounds["x"] + bounds["width"] / 4,
        bounds["y"] + bounds["height"] / 4,
    )


@then("the fullscreen controls are visible")
def fullscreen_controls_are_visible(fullscreen_context: FullscreenContext) -> None:
    overlay = fullscreen_context.controls_overlay.element_handle()
    assert overlay is not None
    fullscreen_context.page.wait_for_function(
        """overlay => {
            const style = window.getComputedStyle(overlay);
            return style.opacity === '1' && style.pointerEvents === 'auto';
        }""",
        arg=overlay,
    )


@when("I exit fullscreen from the video player controls")
def exit_fullscreen(fullscreen_context: FullscreenContext) -> None:
    fullscreen_context.fullscreen_player.get_by_role(
        "button",
        name="Exit Fullscreen",
        exact=True,
    ).click()


@then("the browser leaves fullscreen mode")
def browser_leaves_fullscreen(fullscreen_context: FullscreenContext) -> None:
    fullscreen_context.page.wait_for_function("document.fullscreenElement === null")
