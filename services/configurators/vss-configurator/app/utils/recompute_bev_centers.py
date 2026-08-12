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

from typing import List
from utils.logger import get_logger

logger = get_logger(__name__)

def recompute_bev_centers(calibration_file: str, sensor_names: List[str], n_sensor_groups: int, max_sensors_per_group: int) -> str:
    """Recompute BEV groups from calibration file."""
    try:
        from spatialai_data_utils.core.cameras.bev import calculate_group_origins_from_calibration
    except ImportError as exc:
        raise RuntimeError(
            "spatialai-data-utils is required to recompute BEV centers. "
            "Install it from libs/analytics/spatialai-data-utils/release or use the container image."
        ) from exc

    logger.debug(f"Sensor names: {sensor_names}")
    logger.debug(f"N sensor groups: {n_sensor_groups}")
    logger.debug(f"Max sensors per group: {max_sensors_per_group}")
    output_path = calculate_group_origins_from_calibration(
        input_calibration=calibration_file,
        overwrite=True,
        sensor_names=sensor_names,
        n_sensor_groups=n_sensor_groups,
        max_sensors_per_group=max_sensors_per_group
    )
    return output_path

