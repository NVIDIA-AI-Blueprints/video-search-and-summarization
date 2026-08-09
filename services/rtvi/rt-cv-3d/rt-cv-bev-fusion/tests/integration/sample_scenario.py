# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""
Sample-dataset-backed fusion scenario.

Unlike the purely-synthetic frame_factory, this builds frames using the REAL
sensor ids from the bundled 4-camera sample calibration
(warehouse-4cams-20mx20m-synthetic/calibration.json) and warehouse-scale
(20m x 20m) world coordinates. Objects walk across the floor and every camera
observes them with a small per-sensor measurement offset, so the fused output
is a meaningful multi-view average — the real situation the BEV fusion handles,
without needing the GPU perception pipeline.

Same interface as frame_factory (generate_stream / expected_fused_coords) so the
integration test can parametrize over both scenarios.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, List

from .frame_factory import ObjectSpec, SensorFrame, make_frame

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CALIB = (
    _REPO_ROOT
    / "warehouse-3d-app-mv3dt"
    / "calibration"
    / "sample-data"
    / "warehouse-4cams-20mx20m-synthetic"
    / "calibration.json"
)

# Fallback to the known 4-cam ids if the calibration file isn't present.
_FALLBACK_SENSORS = ["Camera", "Camera_01", "Camera_02", "Camera_03"]

# Per-sensor measurement offset (metres) applied to every coordinate so each
# camera "sees" the object slightly differently; the fused result is the mean.
_SENSOR_OFFSET_M = 0.02
_FLOOR_M = 20.0  # 20m x 20m warehouse


def load_sample_sensor_ids() -> List[str]:
    """Real sensor ids from the bundled 4-camera sample calibration."""
    try:
        data = json.loads(_CALIB.read_text())
        ids = [s.get("id") for s in data.get("sensors", []) if s.get("id")]
        return ids or _FALLBACK_SENSORS
    except Exception:
        return _FALLBACK_SENSORS


SENSORS = load_sample_sensor_ids()


def _object_box(instant: int, obj_index: int, fps: float) -> List[float]:
    """A plausible 3D bbox (12 floats) for object `obj_index` walking the floor.

    Layout: [x_min,y_min,z_min, x_max,y_max,z_max, cx,cy,cz, yaw,w,l].
    Object starts at a fixed lane and advances ~0.05 m per frame, wrapping at 20 m.
    """
    lane_x = 3.0 + 4.0 * obj_index
    y = (2.0 + 0.05 * instant) % _FLOOR_M
    w, l, h = 0.6, 0.6, 1.7
    cx, cy, cz = lane_x, y, h / 2.0
    return [
        cx - w / 2, cy - l / 2, 0.0,
        cx + w / 2, cy + l / 2, h,
        cx, cy, cz,
        0.0, w, l,
    ]


def generate_stream(
    *,
    num_instants: int,
    sensor_ids: List[str],
    base_ts: float = 1_700_000_000.0,
    fps: float = 30.0,
    num_objects: int = 2,
) -> Iterator[SensorFrame]:
    period = 1.0 / fps
    for i in range(num_instants):
        posix_ts = base_ts + i * period
        for s, sensor_id in enumerate(sensor_ids):
            specs = []
            for o in range(num_objects):
                box = _object_box(i, o, fps)
                # Per-sensor offset so fused mean is non-trivial but recomputable.
                coords = [c + s * _SENSOR_OFFSET_M for c in box]
                specs.append(
                    ObjectSpec(
                        id=f"obj-{o}",
                        type="Person" if o % 2 == 0 else "Forklift",
                        confidence=0.6 + 0.05 * s,
                        coords=coords,
                    )
                )
            yield SensorFrame(
                instant=i,
                sensor_id=sensor_id,
                posix_ts=posix_ts,
                frame=make_frame(sensor_id, posix_ts, specs),
                objects=specs,
            )


def expected_fused_coords(instant: int, obj_index: int, num_sensors: int) -> List[float]:
    box = _object_box(instant, obj_index, 30.0)
    # mean over sensors of (box + s*offset) = box + offset*(S-1)/2
    bias = _SENSOR_OFFSET_M * (num_sensors - 1) / 2.0
    return [c + bias for c in box]
