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

"""Load camera poses from a calibration JSON file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from spatialai_data_utils.constants import KEY_IMAGE_SIZE
from spatialai_data_utils.core.cameras.utils import (
    extract_camera_matrices,
    get_calib_field,
)
from spatialai_data_utils.loaders.calibration import load_calib_json
from spatialai_data_utils.utils.string_utils import natural_sort_key
from spatialai_data_utils.loaders.calibration import (
    load_calib_into_dict_with_group_memberships,
    load_calib_into_dict,
)


@dataclass(frozen=True)
class CameraPose:
    """Pose and optics data for one camera."""

    sensor_id: str
    position_xyz: np.ndarray
    rotation_c2w: np.ndarray
    intrinsic_matrix: np.ndarray
    image_size: tuple[int, int] | None = None


@dataclass(frozen=True)
class CameraPlacementContext:
    """Combined calibration context used by dual-view renderers."""

    camera_poses: list[CameraPose]
    sensors_by_id: dict[str, dict]
    cams_by_group: dict[str, list[str]]
    calibration_data: dict


def _normalize_w2c_matrix(matrix: np.ndarray, sensor_id: str) -> np.ndarray:
    """Return a 4x4 world-to-camera matrix from either 3x4 or 4x4 input."""

    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.shape == (4, 4):
        return matrix
    if matrix.shape == (3, 4):
        padded = np.eye(4, dtype=np.float64)
        padded[:3, :] = matrix
        return padded
    raise ValueError(
        f"Camera {sensor_id!r} has unsupported w2c matrix shape {matrix.shape}; "
        "expected (3, 4) or (4, 4)."
    )


def _parse_image_size(raw_value: object) -> tuple[int, int] | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, (list, tuple)) or len(raw_value) < 2:
        return None
    try:
        return int(raw_value[0]), int(raw_value[1])
    except (TypeError, ValueError):
        return None


def load_camera_poses_from_calibration(
    calibration_json_path: str | Path,
    sensor_ids: Sequence[str] | None = None,
    recentering: bool = False,
) -> list[CameraPose]:
    """Parse ``calibration.json`` into camera pose objects.

    :param calibration_json_path: Path to the calibration JSON.
    :param sensor_ids: Optional subset of camera names to keep.
    :param recentering: Apply group-origin recentering if available.
    :returns: Camera pose objects sorted with natural camera ordering.
    :raises KeyError: If requested cameras are missing from the file.
    """

    requested = set(sensor_ids) if sensor_ids is not None else None
    # Reuse the package's standard calibration loader so this module follows
    # the same schema handling + key conventions as the rest of the toolkit.
    calib_dict = load_calib_into_dict(
        str(calibration_json_path),
        recentering=recentering,
    )

    poses: list[CameraPose] = []
    for sensor_id in sorted(calib_dict.keys(), key=natural_sort_key):
        if requested is not None and sensor_id not in requested:
            continue

        calib_info = calib_dict[sensor_id]
        intrinsic_matrix, w2c_matrix = extract_camera_matrices(calib_info)
        if intrinsic_matrix is None or w2c_matrix is None:
            raise ValueError(
                f"Camera {sensor_id!r} has invalid calibration matrices "
                f"and cannot be visualized."
            )
        w2c_matrix = _normalize_w2c_matrix(w2c_matrix, sensor_id=sensor_id)
        c2w_matrix = np.linalg.inv(w2c_matrix)

        poses.append(
            CameraPose(
                sensor_id=sensor_id,
                position_xyz=c2w_matrix[:3, 3].copy(),
                rotation_c2w=c2w_matrix[:3, :3].copy(),
                intrinsic_matrix=intrinsic_matrix,
                image_size=_parse_image_size(
                    get_calib_field(calib_info, KEY_IMAGE_SIZE, default=None)
                ),
            )
        )

    if requested is not None:
        present = {pose.sensor_id for pose in poses}
        missing = sorted(requested - present, key=natural_sort_key)
        if missing:
            raise KeyError(
                "Requested sensor_ids were not found in calibration: "
                f"{missing}. Available sensors: "
                f"{sorted(calib_dict.keys(), key=natural_sort_key)}"
            )

    return poses


def load_camera_placement_context(
    calibration_json_path: str | Path,
    sensor_ids: Sequence[str] | None = None,
    recentering: bool = False,
) -> CameraPlacementContext:
    """Load camera poses + raw sensor metadata + group membership."""

    calibration_path = Path(calibration_json_path)
    camera_poses = load_camera_poses_from_calibration(
        calibration_path,
        sensor_ids=sensor_ids,
        recentering=recentering,
    )
    selected_ids = {pose.sensor_id for pose in camera_poses}

    calibration_data = load_calib_json(
        calibration_path, load_original=True, validate=True,
    )
    sensors_by_id = {
        sensor["id"]: sensor
        for sensor in calibration_data.get("sensors", [])
        if sensor.get("id") in selected_ids
    }

    _, cams_by_group = load_calib_into_dict_with_group_memberships(
        str(calibration_path),
        recentering=recentering,
    )
    filtered_groups: dict[str, list[str]] = {}
    for group_name, group_cams in cams_by_group.items():
        members = [
            cam
            for cam in sorted(group_cams, key=natural_sort_key)
            if cam in selected_ids
        ]
        if members:
            filtered_groups[group_name] = members

    return CameraPlacementContext(
        camera_poses=camera_poses,
        sensors_by_id=sensors_by_id,
        cams_by_group=filtered_groups,
        calibration_data=calibration_data,
    )
