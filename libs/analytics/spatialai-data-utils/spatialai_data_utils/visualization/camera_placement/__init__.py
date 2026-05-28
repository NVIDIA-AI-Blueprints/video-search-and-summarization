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

"""Camera placement visualization utilities.

This package converts calibration extrinsics into camera poses and renders
dual-view camera placement outputs (3D frustums + BEV coverage) for quick
calibration sanity checks. BEV coverage uses frustum-derived footprints by
default, supports map-backed rendering with optional scene-bound clipping, and
the CLI can open a matplotlib interactive window for rotating the 3D panel.
"""

from spatialai_data_utils.visualization.camera_placement.calibration_parser import (
    CameraPlacementContext,
    CameraPose,
    load_camera_placement_context,
    load_camera_poses_from_calibration,
)
from spatialai_data_utils.visualization.camera_placement.plotter import (
    CameraPlacementBevStyle,
    CameraPlacementStyle,
    render_camera_placement,
    render_camera_placement_sequence,
)

__all__ = [
    "CameraPlacementBevStyle",
    "CameraPlacementContext",
    "CameraPlacementStyle",
    "CameraPose",
    "load_camera_placement_context",
    "load_camera_poses_from_calibration",
    "render_camera_placement",
    "render_camera_placement_sequence",
]
