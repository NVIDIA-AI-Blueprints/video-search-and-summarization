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

"""Plot calibrated camera positions and orientations on the top-down map.

Given an Auto-Magic-Calib (AMC) project ``output`` folder, this tool reads the
per-camera 3x4 world->pixel projection matrices, recovers each camera's world
position and viewing direction, projects them onto the floor-plan/map image, and
draws a labelled overlay (dot = camera position, arrow = ground heading).

Supported camera-parameter sources (auto-detected, in this priority order):

1. ``mv3dt_output/camInfo/camInfo_*.yml``      key: ``projectionMatrix_3x4_w2p`` (flat list of 12)
2. ``**/results_ba_scaled_world/camInfo_*.yaml`` key: ``projectionMatrix_3x4`` (nested 3x4)

Map image:        ``mv3dt_output/map.png``
World->map xform: ``mv3dt_output/transforms.yml`` (``T_gt2px``, row-major 3x3),
                  with fallback to ``**/transform_world_to_map.json`` (scale/R/t).

The math: a projection matrix ``P = K [R | t]`` is decomposed with
``cv2.decomposeProjectionMatrix``. The camera centre in world coords is
``C = -R^T t`` (returned directly as the homogeneous null-space). The optical
axis in world coords is ``R^T @ [0, 0, 1]``; its (x, y) component gives the
ground heading. World (x, y) are mapped to map pixels with the 3x3 ``T_gt2px``.

Usage:
    python camera_map_overlay.py /path/to/project/output
    python camera_map_overlay.py /path/to/project/output --out ~/Downloads/overlay.png
    python camera_map_overlay.py /path/to/project/output/vggt_results   # a variant subfolder
    python camera_map_overlay.py /path/to/output --camparams-dir <dir> --map <map.png>

Notes:
    * Pass the ``vggt_results`` (or any) subfolder directly to overlay that variant.
    * Only OpenCV + PyYAML are required (no matplotlib needed).
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import cv2
import numpy as np
import yaml

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Distinct BGR colors, cycled if there are more cameras than entries.
_PALETTE_BGR: list[tuple[int, int, int]] = [
    (48, 59, 255),   # red
    (89, 199, 52),   # green
    (255, 122, 0),   # blue
    (0, 149, 255),   # orange
    (212, 0, 175),   # purple
    (0, 213, 255),   # yellow
    (160, 160, 160),  # gray
    (128, 0, 128),   # dark purple
]


def _find_camparams_dir(root: Path, override: Path | None) -> tuple[Path, str]:
    """Locate the directory holding per-camera projection matrices.

    Args:
        root: Project output folder (or a variant subfolder).
        override: Explicit directory to use, if provided.

    Returns:
        Tuple of (directory, key_name) where key_name is the YAML field holding
        the 3x4 projection matrix.

    Raises:
        FileNotFoundError: If no supported camera-parameter directory is found.
    """
    if override is not None:
        # Decide the key by inspecting the first file.
        for pat, key in (("camInfo_*.yml", "projectionMatrix_3x4_w2p"),
                         ("camInfo_*.yaml", "projectionMatrix_3x4")):
            if sorted(override.glob(pat)):
                return override, key
        raise FileNotFoundError(f"No camInfo_*.yml/.yaml found in {override}")

    candidates: list[tuple[Path, str, str]] = [
        (root / "mv3dt_output" / "camInfo", "camInfo_*.yml", "projectionMatrix_3x4_w2p"),
    ]
    # results_ba_scaled_world may be nested under multi_view_results/BA_output.
    candidates += [(p, "camInfo_*.yaml", "projectionMatrix_3x4")
                   for p in sorted(root.glob("**/results_ba_scaled_world"))]

    for d, pat, key in candidates:
        if d.is_dir() and sorted(d.glob(pat)):
            return d, key
    raise FileNotFoundError(
        f"Could not find camera params under {root} "
        f"(looked for mv3dt_output/camInfo and **/results_ba_scaled_world)"
    )


def _load_projection_matrix(path: Path, key: str) -> np.ndarray:
    """Load a 3x4 projection matrix from a camInfo YAML file.

    Args:
        path: Path to the camInfo YAML file.
        key: YAML field name holding the matrix (flat-12 or nested 3x4).

    Returns:
        (3, 4) float64 projection matrix.
    """
    data = yaml.safe_load(path.read_text())
    mat = np.asarray(data[key], dtype=np.float64)
    return mat.reshape(3, 4)


def _find_map(root: Path, override: Path | None) -> Path:
    """Locate the top-down map image."""
    if override is not None:
        return override
    for cand in (root / "mv3dt_output" / "map.png", *sorted(root.glob("**/map.png"))):
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"Could not find map.png under {root}")


def _load_world_to_map(root: Path) -> np.ndarray:
    """Load the 3x3 world(ground)->map-pixel transform.

    Prefers ``mv3dt_output/transforms.yml`` (``T_gt2px``); falls back to
    ``transform_world_to_map.json`` (scale/R/t, using the x/y rows).

    Returns:
        (3, 3) float64 affine matrix mapping homogeneous (x, y, 1) -> (px, py, w).
    """
    tf_yml = root / "mv3dt_output" / "transforms.yml"
    if tf_yml.is_file():
        data = yaml.safe_load(tf_yml.read_text())
        if "T_gt2px" in data:
            return np.asarray(data["T_gt2px"], dtype=np.float64).reshape(3, 3)

    for js in sorted(root.glob("**/transform_world_to_map.json")):
        d = json.loads(js.read_text())
        scale = float(d["scale"])
        R = np.asarray(d["R"], dtype=np.float64)
        t = np.asarray(d["t"], dtype=np.float64)
        # map3d = scale * R @ world + t; keep x/y rows for the 2D (z=0) plane.
        T = np.eye(3, dtype=np.float64)
        T[0, 0], T[0, 1], T[0, 2] = scale * R[0, 0], scale * R[0, 1], t[0]
        T[1, 0], T[1, 1], T[1, 2] = scale * R[1, 0], scale * R[1, 1], t[1]
        logger.info("Using transform_world_to_map.json fallback: %s", js)
        return T

    raise FileNotFoundError(f"Could not find a world->map transform under {root}")


def _camera_pose_from_P(P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Recover world camera centre and optical-axis direction from P = K[R|t].

    Returns:
        Tuple (center_world (3,), view_dir_world (3,)).
    """
    K, R, t_h, _, _, _, _ = cv2.decomposeProjectionMatrix(P)
    center = (t_h[:3] / t_h[3]).ravel()
    view_dir = R.T @ np.array([0.0, 0.0, 1.0])
    return center, view_dir


def build_overlay(
    output_folder: Path,
    out_path: Path | None = None,
    camparams_dir: Path | None = None,
    map_path: Path | None = None,
    upscale: int = 2,
    arrow_len_px: float = 70.0,
) -> Path:
    """Render the camera-on-map overlay and write it to disk.

    Args:
        output_folder: AMC project ``output`` folder (or variant subfolder).
        out_path: Destination image path. Defaults to ``<folder>/camera_overlay.png``,
            falling back to ``~/Downloads`` if that location is not writable.
        camparams_dir: Override directory containing camInfo files.
        map_path: Override path to the map image.
        upscale: Integer upscale factor for crisper annotations.
        arrow_len_px: Heading arrow length in (pre-upscale) map pixels.

    Returns:
        The path the overlay was written to.
    """
    output_folder = output_folder.expanduser().resolve()
    cam_dir, key = _find_camparams_dir(output_folder, camparams_dir)
    map_file = _find_map(output_folder, map_path)
    T = _load_world_to_map(output_folder)

    cam_files = sorted(cam_dir.glob("camInfo_*.y*ml"))
    logger.info("Camera params: %s (%d cameras, key=%s)", cam_dir, len(cam_files), key)
    logger.info("Map image:     %s", map_file)

    img = cv2.imread(str(map_file))
    if img is None:
        raise ValueError(f"Failed to read map image: {map_file}")
    img = cv2.resize(img, (img.shape[1] * upscale, img.shape[0] * upscale),
                     interpolation=cv2.INTER_CUBIC)
    A = T[:2, :2]  # linear part, for rotating heading vectors

    for idx, cf in enumerate(cam_files):
        name = cf.stem  # e.g. camInfo_00
        cam_num = name.split("_")[-1]
        P = _load_projection_matrix(cf, key)
        center, view_dir = _camera_pose_from_P(P)

        px_h = T @ np.array([center[0], center[1], 1.0])
        px, py = (px_h[:2] / px_h[2]) * upscale

        hd = A @ np.array([view_dir[0], view_dir[1]])
        n = np.linalg.norm(hd)
        hd = hd / n if n > 1e-9 else np.zeros(2)

        color = _PALETTE_BGR[idx % len(_PALETTE_BGR)]
        p0 = (int(round(px)), int(round(py)))
        p1 = (int(round(px + hd[0] * arrow_len_px * upscale)),
              int(round(py + hd[1] * arrow_len_px * upscale)))

        cv2.arrowedLine(img, p0, p1, (255, 255, 255), 9, tipLength=0.35)
        cv2.arrowedLine(img, p0, p1, color, 5, tipLength=0.35)
        cv2.circle(img, p0, 16, (255, 255, 255), -1)
        cv2.circle(img, p0, 12, color, -1)
        label = f"cam_{cam_num}"
        org = (p0[0] + 18, p0[1] - 14)
        cv2.putText(img, label, org, cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(img, label, org, cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2, cv2.LINE_AA)
        logger.info("  %s: world=(%.2f, %.2f, %.2f)  map_px=(%.1f, %.1f)",
                    label, center[0], center[1], center[2], px / upscale, py / upscale)

    title = "Camera position & orientation"
    cv2.putText(img, title, (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(img, title, (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)

    out_path = _resolve_out_path(output_folder, out_path)
    if not cv2.imwrite(str(out_path), img):
        raise IOError(f"Failed to write overlay to {out_path}")
    logger.info("Saved overlay: %s", out_path)
    return out_path


def _resolve_out_path(output_folder: Path, out_path: Path | None) -> Path:
    """Resolve the output path, falling back to ~/Downloads if not writable."""
    # Derive a descriptive default name from the project folder.
    proj = output_folder.name
    if proj == "output" and output_folder.parent != output_folder:
        proj = output_folder.parent.name
    default_name = f"camera_overlay_{proj}.png"

    if out_path is not None:
        out_path = out_path.expanduser()
        if out_path.is_dir():
            out_path = out_path / default_name
    else:
        out_path = output_folder / default_name

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Probe writability; on failure, fall back to ~/Downloads.
    try:
        with open(out_path, "ab"):
            pass
        if out_path.stat().st_size == 0:
            out_path.unlink(missing_ok=True)
    except OSError:
        fallback = Path.home() / "Downloads" / default_name
        logger.warning("Cannot write to %s; falling back to %s", out_path, fallback)
        out_path = fallback
        out_path.parent.mkdir(parents=True, exist_ok=True)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output_folder", type=Path,
                        help="AMC project 'output' folder (or a variant subfolder, e.g. vggt_results)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output image path or directory (default: <folder>/camera_overlay_<proj>.png)")
    parser.add_argument("--camparams-dir", type=Path, default=None,
                        help="Override directory containing camInfo_*.yml/.yaml")
    parser.add_argument("--map", type=Path, default=None, dest="map_path",
                        help="Override path to the map image")
    parser.add_argument("--upscale", type=int, default=2,
                        help="Integer upscale factor for annotations (default: 2)")
    parser.add_argument("--arrow-len", type=float, default=70.0,
                        help="Heading arrow length in map pixels (default: 70)")
    args = parser.parse_args()

    build_overlay(
        output_folder=args.output_folder,
        out_path=args.out,
        camparams_dir=args.camparams_dir,
        map_path=args.map_path,
        upscale=args.upscale,
        arrow_len_px=args.arrow_len,
    )


if __name__ == "__main__":
    main()
