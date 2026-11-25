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

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import uuid


@dataclass
class AdapterInput:
    """Input passed to every adapter's infer() method."""
    image_path: Path
    output_dir: Path
    config: dict = field(default_factory=dict)
    camera_model: str = "pinhole"


@dataclass
class AdapterOutput:
    """Normalized output returned by every adapter.

    Only focal_length_px is guaranteed by all models.
    Rotation, uncertainty, and distortion fields are GeoCalib-specific
    and will be None for other models -- GeoCalib must always run alongside
    other models to supply AMC-required pose/distortion outputs.
    """
    # guaranteed by ALL adapters
    focal_length_px: Optional[float] = None
    runtime_ms: float = 0.0
    status: str = "success"
    error: Optional[str] = None

    # GeoCalib-specific (None for other models)
    roll_deg: Optional[float] = None
    pitch_deg: Optional[float] = None
    vfov_deg: Optional[float] = None
    uncertainty: dict = field(default_factory=dict)
    distortion: dict = field(default_factory=dict)

    # model-specific catch-all
    extra: dict = field(default_factory=dict)


@dataclass
class ModelRunResult:
    """Results from running a single model (possibly N times)."""
    model_id: str
    outputs: list[AdapterOutput] = field(default_factory=list)
    stats: Optional[dict] = None


@dataclass
class RunnerRequest:
    """Top-level request to the coordinator."""
    input_image: Optional[Path] = None
    input_video: Optional[Path] = None
    video_frame_index: Optional[int] = None
    camera_id: Optional[str] = None
    output_dir: Path = Path("output")
    enabled_models: list[str] = field(default_factory=list)
    n_runs: int = 1
    camera_model: str = "pinhole"
    require_pose_from_geocalib: bool = True
    config: dict = field(default_factory=dict)
    seed: Optional[int] = None
    save_per_model_output: bool = False

    def __post_init__(self):
        if self.input_image is None and self.input_video is None:
            raise ValueError("Either input_image or input_video must be provided")


@dataclass
class RunnerResult:
    """Top-level result from the coordinator."""
    request_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    per_model: dict[str, ModelRunResult] = field(default_factory=dict)
    aggregate: Optional[dict] = None
    pose_source: str = "geocalib"
    focal_source: str = "geocalib"
    provenance: dict = field(default_factory=dict)
    preprocessed_image: Optional[Path] = None
