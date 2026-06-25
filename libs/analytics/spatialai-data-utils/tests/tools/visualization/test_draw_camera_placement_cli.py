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

"""CLI smoke test for tools/visualization/draw_camera_placement.py."""

import importlib.util
import json
import sys
from pathlib import Path


_CLI_PATH = (
    Path(__file__).resolve().parents[3]
    / "tools"
    / "visualization"
    / "draw_camera_placement.py"
)


def _load_cli_module():
    spec = importlib.util.spec_from_file_location(
        "_draw_camera_placement_cli_under_test", _CLI_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_CLI = _load_cli_module()


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
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                ],
                "attributes": [
                    {
                        "name": "fieldOfViewPolygon",
                        "value": "POLYGON((0 0, 4 0, 4 4, 0 4, 0 0))",
                    }
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
                    [1000.0, 0.0, 960.0],
                    [0.0, 1000.0, 540.0],
                    [0.0, 0.0, 1.0],
                ],
                "extrinsicMatrix": [
                    [1.0, 0.0, 0.0, 2.0],
                    [0.0, 1.0, 0.0, 1.0],
                    [0.0, 0.0, 1.0, 0.0],
                ],
                "attributes": [],
                "group": {
                    "name": "bev-sensor-2",
                    "type": "bev",
                    "origin": [0, 0],
                    "dimensions": [0, 0, 10, 10],
                },
            }
        ]
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_resolve_map_file_auto_detects_map_png(tmp_path):
    calib_path = tmp_path / "calibration.json"
    calib_path.write_text("{}", encoding="utf-8")
    map_path = tmp_path / "map.png"
    map_path.write_bytes(b"png")

    resolved = _CLI._resolve_map_file(calib_path, None)
    assert resolved == map_path


def test_draw_camera_placement_cli_defaults(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "draw_camera_placement.py",
            "--calib_path",
            "calibration.json",
        ],
    )

    args = _CLI.parse_args()

    assert args.bev_source == "frustum"
    assert args.max_camera_distance == 20.0
    assert args.elev is None
    assert args.azim is None
    assert args.interactive_3d is False


def test_draw_camera_placement_cli_writes_sequence_pngs(tmp_path, monkeypatch):
    calib_path = tmp_path / "calibration.json"
    _write_calibration(calib_path)
    output_dir = tmp_path / "placement_seq"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "draw_camera_placement.py",
            "--calib_path",
            str(calib_path),
            "--output_path",
            str(output_dir),
            "--frustum_depth",
            "2.0",
        ],
    )
    _CLI.main()

    assert (output_dir / "all_cameras.png").exists()
    assert (output_dir / "groups" / "bev-sensor-1.png").exists()
    assert (output_dir / "groups" / "bev-sensor-2.png").exists()


def test_draw_camera_placement_cli_single_output_mode(tmp_path, monkeypatch):
    calib_path = tmp_path / "calibration.json"
    _write_calibration(calib_path)
    output_path = tmp_path / "single.png"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "draw_camera_placement.py",
            "--calib_path",
            str(calib_path),
            "--output_path",
            str(output_path),
            "--single_output",
        ],
    )
    _CLI.main()

    assert output_path.exists()
    assert output_path.stat().st_size > 0
