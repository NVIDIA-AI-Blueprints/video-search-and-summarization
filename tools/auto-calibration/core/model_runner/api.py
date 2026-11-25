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

"""Thin Python API for programmatic use of the FL Model Runner."""
from pathlib import Path
from typing import Optional

import yaml

from .schemas import RunnerRequest, RunnerResult
from .coordinator import ModelCoordinator
from .exporters.amc_exporter import AmcExporter

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "configs" / "config_AutoMagicCalib"
_DEFAULT_MANIFEST = _CONFIG_DIR / "models_manifest.yaml"
_DEFAULT_RUNNER_CONFIG = _CONFIG_DIR / "runner_config.yaml"


def run_amc_compatible(
    image: Optional[Path] = None,
    video: Optional[Path] = None,
    output_dir: Path = Path("output"),
    camera_model: str = "pinhole",
    models: Optional[list[str]] = None,
    amc_config: Optional[dict] = None,
    manifest_path: Path = _DEFAULT_MANIFEST,
    runner_config_path: Path = _DEFAULT_RUNNER_CONFIG,
    project_root: Optional[Path] = None,
) -> RunnerResult:
    """Run all enabled models and export AMC-compatible artifacts."""
    runner_cfg = _load_config(runner_config_path)

    coord = ModelCoordinator(
        manifest_path=manifest_path,
        runner_config=runner_cfg,
        project_root=project_root,
    )

    request = RunnerRequest(
        input_image=image,
        input_video=video,
        output_dir=output_dir,
        enabled_models=models or [],
        camera_model=camera_model,
        config=amc_config or {},
    )

    result = coord.run(request)

    exporter = AmcExporter()
    exporter.export(result, output_dir, amc_config or {})

    return result


def benchmark(
    image: Optional[Path] = None,
    video: Optional[Path] = None,
    output_dir: Path = Path("output"),
    n_runs: int = 10,
    seed: int = 42,
    camera_model: str = "pinhole",
    models: Optional[list[str]] = None,
    manifest_path: Path = _DEFAULT_MANIFEST,
    runner_config_path: Path = _DEFAULT_RUNNER_CONFIG,
    project_root: Optional[Path] = None,
) -> RunnerResult:
    """Run N-times repeatability benchmark."""
    runner_cfg = _load_config(runner_config_path)

    coord = ModelCoordinator(
        manifest_path=manifest_path,
        runner_config=runner_cfg,
        project_root=project_root,
    )

    request = RunnerRequest(
        input_image=image,
        input_video=video,
        output_dir=output_dir,
        enabled_models=models or [],
        n_runs=n_runs,
        camera_model=camera_model,
        seed=seed,
    )

    return coord.run(request)


def _load_config(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}
