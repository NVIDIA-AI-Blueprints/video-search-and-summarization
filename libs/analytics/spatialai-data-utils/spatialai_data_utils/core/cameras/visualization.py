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

"""Compatibility shim for camera group visualization utilities.

The canonical implementation moved to
``spatialai_data_utils.visualization.camera_groups``. This module remains so
older imports keep compiling, but new code should import from the visualization
package directly.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "spatialai_data_utils.core.cameras.visualization is deprecated; import "
    "camera group visualization APIs from "
    "spatialai_data_utils.visualization.camera_groups instead.",
    DeprecationWarning,
    stacklevel=2,
)

from spatialai_data_utils.visualization.camera_groups import (  # noqa: E402,F401
    CLUSTER_COLORS,
    draw_polygon,
    get_cluster_color,
    plot_sensor_groups,
    plot_sensor_groups_black_background,
    transform_polygon,
)

__all__ = [
    "CLUSTER_COLORS",
    "get_cluster_color",
    "transform_polygon",
    "draw_polygon",
    "plot_sensor_groups",
    "plot_sensor_groups_black_background",
]
