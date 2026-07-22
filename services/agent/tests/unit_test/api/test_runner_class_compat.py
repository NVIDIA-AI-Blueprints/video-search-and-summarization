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

"""Regression tests for runner-class compatibility during the package rename."""

from pathlib import Path

from agent.api.custom_fastapi_worker import CustomFastApiFrontEndWorker
from vss_agents.api.custom_fastapi_worker import CustomFastApiFrontEndWorker as LegacyCustomFastApiFrontEndWorker

LEGACY_RUNNER_CLASS = "vss_agents.api.custom_fastapi_worker.CustomFastApiFrontEndWorker"


def test_legacy_runner_class_resolves_to_current_worker() -> None:
    assert LegacyCustomFastApiFrontEndWorker is CustomFastApiFrontEndWorker


def test_deployment_configs_use_image_compatible_runner_class() -> None:
    repo_root = Path(__file__).resolve().parents[5]
    configured_runner_classes: list[tuple[Path, str]] = []

    for config_path in (repo_root / "deploy").rglob("*.yml"):
        for line in config_path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("runner_class:"):
                configured_runner_classes.append((config_path, stripped.partition(":")[2].strip()))

    assert configured_runner_classes
    assert all(runner_class == LEGACY_RUNNER_CLASS for _, runner_class in configured_runner_classes)
