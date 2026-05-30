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

import pytest

from spatialai_data_utils.visualization.camera_placement import (
    CameraPlacementBevStyle,
    CameraPlacementStyle,
    load_camera_placement_context,
    load_camera_poses_from_calibration,
    render_camera_placement,
    render_camera_placement_sequence,
)
from spatialai_data_utils.visualization.camera_placement import plotter as camera_placement_plotter


def _write_calibration(path):
    payload = {
        "sensors": [
            {
                "id": "Camera_01",
                "intrinsicMatrix": [
                    [1000.0, 0.0, 960.0],
                    [0.0, 1000.0, 540.0],
                    [0.0, 0.0, 1.0],
                ],
                "extrinsicMatrix": [
                    [1.0, 0.0, 0.0, 1.0],
                    [0.0, 1.0, 0.0, 2.0],
                    [0.0, 0.0, 1.0, 3.0],
                ],
                "attributes": [
                    {"name": "frameWidth", "value": "1920"},
                    {"name": "frameHeight", "value": "1080"},
                ],
            },
            {
                "id": "Camera_02",
                "intrinsicMatrix": [
                    [900.0, 0.0, 640.0],
                    [0.0, 900.0, 360.0],
                    [0.0, 0.0, 1.0],
                ],
                "extrinsicMatrix": [
                    [1.0, 0.0, 0.0, -4.0],
                    [0.0, 1.0, 0.0, 5.0],
                    [0.0, 0.0, 1.0, -6.0],
                ],
            },
        ]
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _write_grouped_calibration(path):
    payload = {
        "sensors": [
            {
                "id": "Camera_01",
                "intrinsicMatrix": [
                    [1000.0, 0.0, 960.0],
                    [0.0, 1000.0, 540.0],
                    [0.0, 0.0, 1.0],
                ],
                "extrinsicMatrix": [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 3.0],
                ],
                "attributes": [
                    {"name": "frameWidth", "value": "1920"},
                    {"name": "frameHeight", "value": "1080"},
                    {
                        "name": "fieldOfViewPolygon",
                        "value": "POLYGON((0 0, 8 0, 8 6, 0 6, 0 0))",
                    },
                ],
                "group": {
                    "name": "bev-sensor-1",
                    "type": "bev",
                    "origin": [0, 0],
                    "dimensions": [0, 0, 10, 10],
                },
            },
            {
                "id": "Camera_02",
                "intrinsicMatrix": [
                    [900.0, 0.0, 640.0],
                    [0.0, 900.0, 360.0],
                    [0.0, 0.0, 1.0],
                ],
                "extrinsicMatrix": [
                    [1.0, 0.0, 0.0, 4.0],
                    [0.0, 1.0, 0.0, 1.0],
                    [0.0, 0.0, 1.0, 3.0],
                ],
                # Intentionally no fieldOfViewPolygon: should use frustum fallback.
                "attributes": [
                    {"name": "frameWidth", "value": "1280"},
                    {"name": "frameHeight", "value": "720"},
                ],
                "group": {
                    "name": "bev-sensor-1",
                    "type": "bev",
                    "origin": [0, 0],
                    "dimensions": [0, 0, 10, 10],
                },
            },
            {
                "id": "Camera_03",
                "intrinsicMatrix": [
                    [950.0, 0.0, 960.0],
                    [0.0, 950.0, 540.0],
                    [0.0, 0.0, 1.0],
                ],
                "extrinsicMatrix": [
                    [1.0, 0.0, 0.0, -3.0],
                    [0.0, 1.0, 0.0, -2.0],
                    [0.0, 0.0, 1.0, 3.0],
                ],
                "attributes": [
                    {"name": "fieldOfViewPolygon", "value": "POLYGON((-5 -5, -2 -5, -2 -2, -5 -2, -5 -5))"},
                ],
                "group": {
                    "name": "bev-sensor-2",
                    "type": "bev",
                    "origin": [0, 0],
                    "dimensions": [0, 0, 10, 10],
                },
            },
        ]
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_load_camera_poses_from_calibration_uses_existing_loader(tmp_path):
    calib_path = tmp_path / "calibration.json"
    _write_calibration(calib_path)

    poses = load_camera_poses_from_calibration(calib_path)
    assert [pose.sensor_id for pose in poses] == ["Camera_01", "Camera_02"]

    # w2c is [I | t] so camera center in world is -t.
    assert poses[0].position_xyz.tolist() == pytest.approx([-1.0, -2.0, -3.0])
    assert poses[1].position_xyz.tolist() == pytest.approx([4.0, -5.0, 6.0])
    assert poses[0].image_size == (1920, 1080)
    assert poses[1].image_size is None


def test_load_camera_poses_from_calibration_sensor_filter_and_missing(tmp_path):
    calib_path = tmp_path / "calibration.json"
    _write_calibration(calib_path)

    poses = load_camera_poses_from_calibration(calib_path, sensor_ids=["Camera_02"])
    assert [pose.sensor_id for pose in poses] == ["Camera_02"]

    with pytest.raises(KeyError):
        load_camera_poses_from_calibration(calib_path, sensor_ids=["Camera_99"])


def test_render_camera_placement_writes_output(tmp_path):
    import matplotlib
    import matplotlib.pyplot as plt

    matplotlib.use("Agg")

    calib_path = tmp_path / "calibration.json"
    _write_calibration(calib_path)
    poses = load_camera_poses_from_calibration(calib_path)

    out_path = tmp_path / "camera_placement.png"
    render_camera_placement(poses, output_path=out_path, show=False)

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    plt.close("all")


def test_camera_placement_bev_style_defaults():
    style = CameraPlacementBevStyle()

    assert style.source_mode == "frustum"
    assert style.max_camera_distance == 20.0


def test_render_camera_placement_adds_footer_text(tmp_path):
    import matplotlib
    import matplotlib.pyplot as plt

    matplotlib.use("Agg")

    calib_path = tmp_path / "calibration.json"
    _write_calibration(calib_path)
    poses = load_camera_poses_from_calibration(calib_path)
    footer_text = f"Calibration: {calib_path}"

    fig, _ = render_camera_placement(
        poses,
        show=False,
        footer_text=footer_text,
    )

    assert any(text.get_text() == footer_text for text in fig.texts)
    plt.close(fig)


def test_load_camera_placement_context_includes_groups_and_sensor_metadata(tmp_path):
    calib_path = tmp_path / "calibration.json"
    _write_grouped_calibration(calib_path)

    context = load_camera_placement_context(calib_path)

    assert [pose.sensor_id for pose in context.camera_poses] == [
        "Camera_01",
        "Camera_02",
        "Camera_03",
    ]
    assert sorted(context.sensors_by_id.keys()) == ["Camera_01", "Camera_02", "Camera_03"]
    assert context.cams_by_group == {
        "bev-sensor-1": ["Camera_01", "Camera_02"],
        "bev-sensor-2": ["Camera_03"],
    }


def test_render_camera_placement_dual_view_auto_fallback(tmp_path):
    import matplotlib

    matplotlib.use("Agg")

    calib_path = tmp_path / "calibration.json"
    _write_grouped_calibration(calib_path)
    context = load_camera_placement_context(calib_path)

    out_path = tmp_path / "dual_view.png"
    render_camera_placement(
        context.camera_poses,
        output_path=out_path,
        sensors_by_id=context.sensors_by_id,
        calibration_data=context.calibration_data,
        show=False,
        bev_style=CameraPlacementBevStyle(source_mode="auto"),
    )

    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_render_camera_placement_sequence_writes_all_and_groups(tmp_path):
    import matplotlib
    import matplotlib.pyplot as plt

    matplotlib.use("Agg")

    calib_path = tmp_path / "calibration.json"
    _write_grouped_calibration(calib_path)
    context = load_camera_placement_context(calib_path)

    plt.close("all")
    output_dir = tmp_path / "camera_placement_seq"
    outputs = render_camera_placement_sequence(
        context.camera_poses,
        output_dir=output_dir,
        sensors_by_id=context.sensors_by_id,
        cams_by_group=context.cams_by_group,
        calibration_data=context.calibration_data,
        bev_style=CameraPlacementBevStyle(source_mode="auto"),
    )

    assert sorted(outputs.keys()) == ["all_cameras", "bev-sensor-1", "bev-sensor-2"]
    for file_path in outputs.values():
        assert file_path.exists()
        assert file_path.stat().st_size > 0
    assert plt.get_fignums() == []


def test_extract_bev_polygons_clips_to_scene_bounds(tmp_path):
    calib_path = tmp_path / "calibration.json"
    _write_grouped_calibration(calib_path)
    context = load_camera_placement_context(calib_path)

    polygon_by_id, _ = camera_placement_plotter._extract_bev_polygons(
        camera_poses=context.camera_poses,
        sensors_by_id=context.sensors_by_id,
        style=CameraPlacementStyle(),
        bev_style=CameraPlacementBevStyle(source_mode="attributes"),
        scene_bounds=(0.0, 0.0, 4.0, 4.0),
    )

    for geometry in polygon_by_id.values():
        if geometry is None or geometry.is_empty:
            continue
        min_x, min_y, max_x, max_y = geometry.bounds
        eps = 1e-6
        assert min_x >= 0.0 - eps
        assert min_y >= 0.0 - eps
        assert max_x <= 4.0 + eps
        assert max_y <= 4.0 + eps


def test_resolve_fov_clip_bounds_prefers_group_dimensions(tmp_path):
    calib_path = tmp_path / "calibration.json"
    _write_grouped_calibration(calib_path)
    context = load_camera_placement_context(calib_path)

    clip_bounds = camera_placement_plotter._resolve_fov_clip_bounds(
        camera_poses=context.camera_poses,
        sensors_by_id=context.sensors_by_id,
        calibration_data=context.calibration_data,
        map_width=1920,
        map_height=1080,
    )
    assert clip_bounds == (0.0, 0.0, 10.0, 10.0)


def test_resolve_fov_clip_bounds_uses_attribute_bounds_before_map(tmp_path):
    calib_path = tmp_path / "calibration.json"
    payload = {
        "sensors": [
            {
                "id": "Camera_01",
                "intrinsicMatrix": [[1000.0, 0.0, 960.0], [0.0, 1000.0, 540.0], [0.0, 0.0, 1.0]],
                "extrinsicMatrix": [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 2.0]],
                "translationToGlobalCoordinates": {"x": 100.0, "y": 50.0},
                "scaleFactor": 10.0,
                "attributes": [
                    {"name": "fieldOfViewPolygon", "value": "POLYGON((0 0, 2 0, 2 1, 0 1, 0 0))"},
                ],
            },
            {
                "id": "Camera_02",
                "intrinsicMatrix": [[900.0, 0.0, 640.0], [0.0, 900.0, 360.0], [0.0, 0.0, 1.0]],
                "extrinsicMatrix": [[1.0, 0.0, 0.0, 1.0], [0.0, 1.0, 0.0, -1.0], [0.0, 0.0, 1.0, 2.0]],
                "translationToGlobalCoordinates": {"x": 100.0, "y": 50.0},
                "scaleFactor": 10.0,
                "attributes": [
                    {"name": "fieldOfViewPolygon", "value": "POLYGON((-1 -1, 1 -1, 1 0, -1 0, -1 -1))"},
                ],
            },
        ]
    }
    with open(calib_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    context = load_camera_placement_context(calib_path)
    clip_bounds = camera_placement_plotter._resolve_fov_clip_bounds(
        camera_poses=context.camera_poses,
        sensors_by_id=context.sensors_by_id,
        calibration_data=context.calibration_data,
        map_width=1920,
        map_height=1080,
    )
    assert clip_bounds == (-1.0, -1.0, 2.0, 1.0)
