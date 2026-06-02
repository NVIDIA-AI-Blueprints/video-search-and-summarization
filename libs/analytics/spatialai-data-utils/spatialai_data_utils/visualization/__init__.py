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

import json
from importlib.resources import files

with (files("spatialai_data_utils") / "assets" / "colormap.json").open("r") as f:
    COLOR_MAP = json.load(f)

from spatialai_data_utils.visualization.box_3d import (  # noqa: F401, E402
    box3d_to_corners,
    draw_bbox3d_multicam,
    draw_bbox3d_on_bev,
    draw_bbox3d_on_img,
    draw_points3d_on_img,
    draw_box3d_corners_on_img,
)
from spatialai_data_utils.visualization.draw_utils import (  # noqa: F401, E402
    build_world2img_from_calib,
    build_world2img_from_calib_info,
    draw_camera_tag,
    generate_bbox_text,
    load_image,
    save_viz,
)
from spatialai_data_utils.visualization.camera_groups import (  # noqa: F401, E402
    CLUSTER_COLORS,
    draw_polygon,
    get_cluster_color,
    plot_sensor_groups,
    plot_sensor_groups_black_background,
    transform_polygon,
)
from spatialai_data_utils.core.geometry.projection import (  # noqa: F401, E402
    project_bev_objects_bbox_in_image,
)
from spatialai_data_utils.visualization.render import (  # noqa: F401, E402
    draw_bev_objects_bbox_in_image,
    visualize_3dbbox,
    visualize_nvschema,
)
