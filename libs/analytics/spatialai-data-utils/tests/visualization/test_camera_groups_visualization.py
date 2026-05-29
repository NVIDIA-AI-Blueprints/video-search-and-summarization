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

import importlib
import sys
import warnings


def test_camera_groups_public_api_is_canonical():
    from spatialai_data_utils.visualization import camera_groups

    assert camera_groups.get_cluster_color(0) == "#E6194B"
    assert camera_groups.get_cluster_color(0, as_tuple=True) == (
        230 / 255.0,
        25 / 255.0,
        75 / 255.0,
    )


def test_legacy_core_visualization_import_warns_and_reexports():
    sys.modules.pop("spatialai_data_utils.core.cameras.visualization", None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        legacy = importlib.import_module("spatialai_data_utils.core.cameras.visualization")

    assert any(item.category is DeprecationWarning for item in caught)

    from spatialai_data_utils.visualization import camera_groups

    assert legacy.get_cluster_color is camera_groups.get_cluster_color
    assert legacy.transform_polygon is camera_groups.transform_polygon
    assert legacy.plot_sensor_groups is camera_groups.plot_sensor_groups
