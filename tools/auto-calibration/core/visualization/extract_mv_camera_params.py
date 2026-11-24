#!/usr/bin/env python3
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

"""Extract per-camera params from MV ``results_ba_scaled_world`` calibration.

Reads ``camInfo_*.yaml`` (``projectionMatrix_3x4``) and reports four numbers per
camera in AMC naming:

* ``focal_length``    — focal length in pixels (``K[0,0]``)
* ``rotation_x``      — tilt below horizontal (degrees)
* ``rotation_z``      — roll / twist (degrees)
* ``mounting_height`` — camera-centre height in the MV world frame (metres, ``z``)

If a YAML file already has ``best_param`` / ``cameraInfo`` fields, those are used
instead of decomposing the projection matrix.

Usage:
    python extract_mv_camera_params.py /path/to/project/output
    python extract_mv_camera_params.py /path/to/results_ba_scaled_world
    python extract_mv_camera_params.py /path/to/output --json params.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

MV_KEY = "projectionMatrix_3x4"


@dataclass
class MvCameraParams:
    """Per-camera MV calibration summary."""

    cam_id: int
    name: str
    focal_length: float
    rotation_x: float
    rotation_z: float
    mounting_height: float
    source: str
    calib_file: str


def _cam_index_from_name(name: str) -> int:
    m = re.search(r"(\d+)", name)
    if not m:
        raise ValueError(f"Cannot parse camera index from {name!r}")
    return int(m.group(1))


def find_mv_camparams_dir(root: Path, override: Path | None = None) -> Path:
    """Locate ``results_ba_scaled_world`` under an AMC output folder."""
    root = root.expanduser().resolve()
    if override is not None:
        override = override.expanduser().resolve()
        if sorted(override.glob("camInfo_*.y*ml")):
            return override
        raise FileNotFoundError(f"No camInfo_*.yaml in {override}")
    if sorted(root.glob("camInfo_*.y*ml")):
        return root
    for d in sorted(root.glob("**/results_ba_scaled_world")):
        if d.is_dir() and sorted(d.glob("camInfo_*.y*ml")):
            return d
    raise FileNotFoundError(
        f"Could not find results_ba_scaled_world/camInfo_*.yaml under {root}"
    )


def _roll_deg_from_rotation(R: np.ndarray) -> float:
    """Roll (twist) in degrees relative to world z-up."""
    view = R.T @ np.array([0.0, 0.0, 1.0])
    vn = float(np.linalg.norm(view))
    if vn < 1e-9:
        return 0.0
    view /= vn
    up = R.T @ np.array([0.0, 1.0, 0.0])
    ref = np.array([0.0, 0.0, 1.0]) - np.dot([0.0, 0.0, 1.0], view) * view
    rn = float(np.linalg.norm(ref))
    if rn < 1e-9:
        ref = np.array([0.0, 1.0, 0.0]) - np.dot([0.0, 1.0, 0.0], view) * view
        rn = float(np.linalg.norm(ref))
    if rn < 1e-9:
        return 0.0
    ref /= rn
    up_p = up - np.dot(up, view) * view
    un = float(np.linalg.norm(up_p))
    if un < 1e-9:
        return 0.0
    up_p /= un
    sin_roll = float(np.dot(np.cross(ref, up_p), view))
    cos_roll = float(np.dot(ref, up_p))
    return float(np.degrees(np.arctan2(sin_roll, cos_roll)))


def params_from_projection_matrix(P: np.ndarray) -> tuple[float, float, float, float]:
    """Decompose a 3x4 world->pixel matrix into (f, rx, rz, height)."""
    _, R, t_h, _, _, _, _ = cv2.decomposeProjectionMatrix(P)
    K, _, _, _, _, _, _ = cv2.decomposeProjectionMatrix(P)
    K = K / K[2, 2]
    center = (t_h[:3] / t_h[3]).ravel()
    view = R.T @ np.array([0.0, 0.0, 1.0])
    rx = float(np.degrees(np.arctan2(-view[2], np.hypot(view[0], view[1]))))
    rz = _roll_deg_from_rotation(R)
    return float(K[0, 0]), rx, rz, float(center[2])


def _params_from_yaml_fields(data: dict[str, Any]) -> tuple[float, float, float, float] | None:
    bp = data.get("best_param") or {}
    ci = data.get("cameraInfo") or {}
    focal = bp.get("focal_length", ci.get("focalLength"))
    rx = bp.get("rotation_x", ci.get("yaw"))
    rz = bp.get("rotation_z", ci.get("pitch"))
    height = bp.get("translation_y", ci.get("Ty"))
    if None in (focal, rx, rz, height):
        return None
    return float(focal), float(rx), float(rz), float(height)


def extract_from_caminfo_yaml(path: Path) -> MvCameraParams:
    """Extract params from one MV ``camInfo_*.yaml`` file."""
    path = path.expanduser().resolve()
    data = yaml.safe_load(path.read_text())
    cam_id = _cam_index_from_name(path.stem)
    fields = _params_from_yaml_fields(data)
    if fields is not None:
        focal, rx, rz, height = fields
        source = "yaml"
    else:
        if MV_KEY not in data:
            raise ValueError(f"No {MV_KEY} in {path}")
        P = np.asarray(data[MV_KEY], dtype=np.float64).reshape(3, 4)
        focal, rx, rz, height = params_from_projection_matrix(P)
        source = "computed"
    return MvCameraParams(
        cam_id=cam_id,
        name=f"cam_{cam_id:02d}",
        focal_length=focal,
        rotation_x=rx,
        rotation_z=rz,
        mounting_height=height,
        source=source,
        calib_file=str(path),
    )


def load_mv_camera_params(
    root: Path,
    camparams_dir: Path | None = None,
) -> list[MvCameraParams]:
    """Load params for every camera in an MV calibration folder.

    Args:
        root: AMC ``output`` folder or a ``results_ba_scaled_world`` directory.
        camparams_dir: Optional explicit camInfo directory.

    Returns:
        Sorted list of per-camera params.
    """
    cam_dir = find_mv_camparams_dir(root, camparams_dir)
    out = [extract_from_caminfo_yaml(p) for p in sorted(cam_dir.glob("camInfo_*.y*ml"))]
    if not out:
        raise FileNotFoundError(f"No camInfo files in {cam_dir}")
    return out


def print_params_table(params: list[MvCameraParams]) -> None:
    """Print a compact table to stdout."""
    hdr = f"{'cam':<8} {'f (px)':>8} {'rx (°)':>8} {'rz (°)':>8} {'h (m)':>8}  source"
    print(hdr)
    print("-" * len(hdr))
    for p in params:
        print(
            f"{p.name:<8} {p.focal_length:8.1f} {p.rotation_x:8.2f} "
            f"{p.rotation_z:8.2f} {p.mounting_height:8.2f}  {p.source}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        type=Path,
        help="AMC output folder or results_ba_scaled_world directory",
    )
    parser.add_argument(
        "--camparams-dir",
        type=Path,
        default=None,
        help="Explicit camInfo directory (default: auto-detect)",
    )
    parser.add_argument("--json", type=Path, default=None, help="Write params to JSON file")
    args = parser.parse_args()

    params = load_mv_camera_params(args.path, camparams_dir=args.camparams_dir)
    print_params_table(params)

    if args.json is not None:
        payload = {
            "camparams_dir": str(find_mv_camparams_dir(args.path, args.camparams_dir)),
            "cameras": [asdict(p) for p in params],
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2))
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
