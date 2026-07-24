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

"""Playwright fixtures for opt-in VIOS browser tests."""

from collections.abc import Generator
from typing import Any

import pytest
from playwright.sync_api import Browser, Error, Page, sync_playwright


@pytest.fixture(scope="session")
def ui_base_url(request: pytest.FixtureRequest, api_config: dict[str, Any]) -> str:
    """Return the explicitly configured UI URL or derive it from the API URL."""
    configured_url = request.config.getoption("--ui-base-url")
    if configured_url:
        return configured_url.rstrip("/")

    api_base_url = api_config["base_url"].rstrip("/")
    return f"{api_base_url}/vst/#"


@pytest.fixture(scope="session")
def ui_browser(request: pytest.FixtureRequest) -> Generator[Browser, None, None]:
    """Launch Chromium once for the selected UI scenarios."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=not request.config.getoption("--headed"),
            )
        except Error as exc:
            raise pytest.UsageError(
                "Chromium is unavailable; run `poetry run playwright install chromium`"
            ) from exc

        yield browser
        browser.close()


@pytest.fixture
def browser_page(ui_browser: Browser) -> Generator[Page, None, None]:
    """Create an isolated browser context and page for each UI scenario."""
    context = ui_browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()
