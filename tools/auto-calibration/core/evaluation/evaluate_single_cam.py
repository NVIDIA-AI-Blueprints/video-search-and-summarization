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

"""
Single-camera L2 distance evaluation via floor-plane intersection.

Multi-camera evaluation triangulates 3D positions from two views.  For a
single camera we instead back-project 2D observations through the AMC
camera onto the ground plane (Z = 0) and compare with the known GT 3D
positions in a common map coordinate frame.

Algorithm per GT trajectory point:
    1.  Project the known GT 3D position to 2D using the GT projection matrix.
    2.  Back-project that 2D point through P_world (from sv_post_process)
        and intersect with the floor plane Z = 0  ->  AMC-world 3D estimate.
    3.  Translate both GT and AMC 3D positions into the common map frame
        (analogous to evaluate_amc_calib's ``pt_map = pt3d + translation``).
    4.  Compute L2 distance in map coordinates.

Coordinate alignment
    GT side:  pt_map = pt3d_gt + gt_translation  (from calibration JSON)
    AMC side: The AMC P_world's world frame relates to map coordinates via
              T_world2map = diag(ppm, -ppm, 1), giving:
              amc_translation = [0, layout_height / ppm, 0]

Usage:
    python3 core/evaluation/evaluate_single_cam.py \\
        --amc_calib <camInfo_world.yaml> \\
        --gt_calib  <calibration.json>   \\
        --gt_traj   <ground_truth.json>  \\
        --eval_config <eval_config.yaml> \\
        --output_dir <out>               \\
        --layout_image_path <layout.png>
"""

import argparse
import csv
import json
import os
import sys
from typing import Dict, Iterable, List, Optional, Tuple

import cv2
import matplotlib
import numpy as np
import yaml

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(current_dir)
sys.path.append(project_root)

from core.utils.path_validation import validate_input_file, validate_output_directory
from core.evaluation.evaluate_multi_cam import project_3d_point_to_image_3x4_proj_mat


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------

def load_gt_calibration(
    calib_path: str,
) -> Tuple[np.ndarray, np.ndarray, float, Optional[Tuple[int, int]]]:
    """Load the first camera sensor from a GT calibration JSON.

    Returns
    -------
    P_gt : (3, 4) projection matrix (GT world -> image)
    translation : (3,) translationToGlobalCoordinates
    scale : scaleFactor (layout px / m)
    frame_size : (width, height) or None
    """
    with open(calib_path, "r") as f:
        data = json.load(f)

    for sensor in data.get("sensors", []):
        cam_matrix = sensor.get("cameraMatrix")
        if cam_matrix is None:
            continue

        P_gt = np.array(cam_matrix, dtype=np.float64).reshape(3, 4)

        trans = (
            sensor.get("translationToGlobalCoordinates")
            or sensor.get("translationToGlobalCoordinate")
            or {}
        )
        translation = np.array(
            [float(trans.get("x", 0.0)),
             float(trans.get("y", 0.0)),
             float(trans.get("z", 0.0))],
            dtype=np.float64,
        )

        scale = float(sensor.get("scaleFactor", -1.0))

        frame_size: Optional[Tuple[int, int]] = None
        w_val = h_val = None
        for attr in sensor.get("attributes", []):
            name = attr.get("name")
            if name == "frameWidth":
                w_val = attr.get("value")
            elif name == "frameHeight":
                h_val = attr.get("value")
        if w_val is not None and h_val is not None:
            try:
                frame_size = (int(float(w_val)), int(float(h_val)))
            except ValueError:
                pass

        return P_gt, translation, scale, frame_size

    raise ValueError(
        "No camera sensor with cameraMatrix found in GT calibration."
    )


def load_amc_projection_matrix(yaml_path: str) -> np.ndarray:
    """Load P_world (3x4) from camInfo_world.yaml (sv_post_process output)."""
    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)
    flat = data["projectionMatrix_3x4"]
    return np.array(flat, dtype=np.float64).reshape(3, 4)


# ---------------------------------------------------------------------------
# GT trajectory loader  (mirrors evaluate_amc_calib logic)
# ---------------------------------------------------------------------------

def _is_human_object(obj: dict) -> bool:
    text_fields = [
        obj.get("object name"), obj.get("objectName"), obj.get("name"),
        obj.get("type"), obj.get("category"), obj.get("semantic label"),
    ]
    text = " ".join(str(v) for v in text_fields if v is not None).lower()
    return any(kw in text for kw in ("character", "human", "person", "people"))


def _get_object_id(obj: dict) -> str:
    obj_name = obj.get("object name") or obj.get("objectName") or ""
    if isinstance(obj_name, str) and "/" in obj_name:
        parts = obj_name.split("/")
        return parts[3] if len(parts) >= 4 else parts[-1]
    obj_id = obj.get("object id")
    if obj_id is not None:
        return "Character" if int(obj_id) == 0 else f"Character_{int(obj_id):02d}"
    return str(obj.get("id", "unknown"))


def _get_object_type(obj: dict) -> str:
    for key in ("object type", "object_type", "type", "category"):
        val = obj.get(key)
        if isinstance(val, str):
            return val.strip().lower()
    return ""


def load_gt_trajectories(
    gt_path: str,
    include_all_objects: bool = False,
    object_types: Optional[Iterable[str]] = None,
) -> Dict[int, List[dict]]:
    """Load ground_truth.json -> {frame_idx: [{id, pos_3d}, ...]}."""
    with open(gt_path, "r") as f:
        data = json.load(f)

    allowed_types = None
    if object_types:
        allowed_types = {t.strip().lower() for t in object_types if t.strip()}

    trajectories: Dict[int, List[dict]] = {}
    for frame_key, objects in data.items():
        try:
            frame_idx = int(frame_key)
        except ValueError:
            continue
        if not isinstance(objects, list):
            continue

        frame_objs: List[dict] = []
        for obj in objects:
            if not include_all_objects:
                if allowed_types is not None:
                    if _get_object_type(obj) not in allowed_types:
                        continue
                elif not _is_human_object(obj):
                    continue
            pos_3d = (
                obj.get("3d location")
                or obj.get("3d_location")
                or obj.get("pos_3d")
            )
            if not isinstance(pos_3d, (list, tuple)) or len(pos_3d) < 3:
                continue
            # Project onto the floor plane (Z=0) because the SV pipeline's
            # back-projection assumes all objects lie on the ground.  GT data
            # that stores body-centroid heights (e.g. Z≈0.85 m in syn-KION) would otherwise
            # introduce parallax error unrelated to calibration quality.
            frame_objs.append({
                "id": _get_object_id(obj),
                "pos_3d": np.array([pos_3d[0], pos_3d[1], 0.0], dtype=np.float64),
            })
        trajectories[frame_idx] = frame_objs

    return trajectories


# ---------------------------------------------------------------------------
# Geometry: floor-plane back-projection
# ---------------------------------------------------------------------------

SV_HEIGHT_COL = 2  # Z-up in the AMC/SV world frame (matches sv_post_process.py)


def backproject_to_floor(
    pt2d: np.ndarray,
    H_img2floor: np.ndarray,
) -> np.ndarray:
    """Back-project an image point to the floor plane (Z = 0).

    Uses the precomputed inverse of P_world[:, [0, 1, 3]] (the 3x3
    floor-to-image homography with the height column dropped).

    Returns
    -------
    (3,) array [X, Y, 0] in AMC-world coordinates.
    """
    pt_h = H_img2floor @ np.array([pt2d[0], pt2d[1], 1.0], dtype=np.float64)
    return np.array([pt_h[0] / pt_h[2], pt_h[1] / pt_h[2], 0.0], dtype=np.float64)


def compute_amc_translation(layout_h: int, ppm: float) -> np.ndarray:
    """Derive AMC-world -> map-coordinate translation.

    The AMC P_world's floor homography converts via
    T_world2map = diag(ppm, -ppm, 1).  The evaluate_amc_calib map-frame
    convention uses  _map_to_pixel(pt, scale, img_h):
        pixel_x = pt_map_x * scale
        pixel_y = img_h - pt_map_y * scale

    Equating the two pixel expressions (with scale = ppm) yields:
        map_x = X_amc
        map_y = Y_amc + layout_h / ppm
    i.e.  amc_translation = [0, layout_h / ppm, 0].
    """
    return np.array([0.0, float(layout_h) / float(ppm), 0.0], dtype=np.float64)


# ---------------------------------------------------------------------------
# Pixel / visualisation helpers
# ---------------------------------------------------------------------------

def _map_to_pixel(pt_map: np.ndarray, scale: float, img_h: int) -> np.ndarray:
    """Convert a map-coordinate point to layout-image pixel (matches evaluate_amc_calib)."""
    return np.array(
        [pt_map[0] * scale, img_h - pt_map[1] * scale], dtype=np.float64
    )


def _configure_matplotlib(show_plot: bool) -> None:
    backend = "Agg"
    if show_plot and os.environ.get("DISPLAY"):
        backend = "TkAgg"
    matplotlib.use(backend, force=True)


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _write_csv(path: str, rows: List[dict]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_stats(
    path: str,
    l2_floor_vals: np.ndarray,
    l2_3d_vals: np.ndarray,
) -> None:
    with open(path, "w") as f:
        for label, vals in [
            # ("L2 Distance - Floor (XY)", l2_floor_vals),
            ("L2 Distance - 3D (XYZ)", l2_3d_vals),
        ]:
            f.write(f"{label}\n")
            # f.write(f"  Count : {len(vals)}\n")
            f.write(f"Average L2 distance(m): {np.mean(vals):.6f}\n")
            # f.write(f"  Median: {np.median(vals):.6f}\n")
            f.write(f"Standard deviation of L2 distance(m): {np.std(vals):.6f}\n")
            f.write(f"Max L2 distance(m):  {np.max(vals):.6f}\n")
            f.write(f"Min L2 distance(m): {np.min(vals):.6f}\n")
            # for pct in (50, 75, 90, 95, 99):
            #     f.write(f"  P{pct:<3d} : {np.percentile(vals, pct):.6f}\n")
            f.write("\n")


def _write_basic_plots(
    output_dir: str,
    l2_floor_vals: np.ndarray,
    l2_3d_vals: np.ndarray,
) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))

    axes[0, 0].hist(l2_floor_vals, bins=50, color="#4C72B0", edgecolor="black", alpha=0.85)
    axes[0, 0].set_xlabel("L2 Distance (floor, m)")
    axes[0, 0].set_ylabel("Count")
    axes[0, 0].set_title("Floor L2 Histogram")

    axes[0, 1].hist(l2_3d_vals, bins=50, color="#55A868", edgecolor="black", alpha=0.85)
    axes[0, 1].set_xlabel("L2 Distance (3D, m)")
    axes[0, 1].set_ylabel("Count")
    axes[0, 1].set_title("3D L2 Histogram")

    axes[1, 0].boxplot(
        [l2_floor_vals], vert=False, patch_artist=True,
        boxprops=dict(facecolor="#4C72B0", color="black"),
        medianprops=dict(color="black"),
    )
    axes[1, 0].set_yticklabels(["floor"])
    axes[1, 0].set_xlabel("L2 Distance (m)")
    axes[1, 0].set_title("Floor L2 Boxplot")

    axes[1, 1].boxplot(
        [l2_3d_vals], vert=False, patch_artist=True,
        boxprops=dict(facecolor="#55A868", color="black"),
        medianprops=dict(color="black"),
    )
    axes[1, 1].set_yticklabels(["3D"])
    axes[1, 1].set_xlabel("L2 Distance (m)")
    axes[1, 1].set_title("3D L2 Boxplot")

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "l2_summary.png"), dpi=150)
    plt.close(fig)


def _draw_overlay(
    layout_image_path: str,
    out_path: str,
    gt_points_map: List[np.ndarray],
    est_points_map: List[np.ndarray],
    gt_scale: float,
    est_scale: float,
    gt_color: Tuple[int, int, int] = (0, 0, 0),
    est_color: Tuple[int, int, int] = (0, 0, 255),
) -> None:
    """Draw GT (crosses) and AMC estimates (dots) on the layout image."""
    img = cv2.imread(layout_image_path)
    if img is None:
        return
    img_h, img_w = img.shape[:2]

    for pt in gt_points_map:
        px = _map_to_pixel(pt, gt_scale, img_h)
        x, y = int(round(px[0])), int(round(px[1]))
        if 0 <= x < img_w and 0 <= y < img_h:
            size = 4
            cv2.line(img, (x - size, y), (x + size, y), gt_color, 1)
            cv2.line(img, (x, y - size), (x, y + size), gt_color, 1)

    for pt in est_points_map:
        px = _map_to_pixel(pt, est_scale, img_h)
        x, y = int(round(px[0])), int(round(px[1]))
        if 0 <= x < img_w and 0 <= y < img_h:
            cv2.circle(img, (x, y), 2, est_color, -1)

    cv2.imwrite(out_path, img)


def _draw_l2_heatmap_overlay(
    layout_image_path: str,
    out_path: str,
    points_map: List[np.ndarray],
    l2_values: List[float],
    scale: float,
    alpha: float = 0.6,
    radius: int = 3,
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
) -> None:
    """Draw an L2-distance heatmap on the layout image."""
    if not points_map or len(points_map) != len(l2_values):
        return
    img = cv2.imread(layout_image_path)
    if img is None:
        return
    img_h, img_w = img.shape[:2]

    heatmap = np.full((img_h, img_w), -1.0, dtype=np.float32)
    r = max(1, int(radius))
    for pt_map, l2_val in zip(points_map, l2_values):
        px = _map_to_pixel(pt_map, scale, img_h)
        x, y = int(round(px[0])), int(round(px[1]))
        if not (0 <= x < img_w and 0 <= y < img_h):
            continue
        x0, x1 = max(0, x - r), min(img_w - 1, x + r)
        y0, y1 = max(0, y - r), min(img_h - 1, y + r)
        for yy in range(y0, y1 + 1):
            dy2 = (yy - y) ** 2
            for xx in range(x0, x1 + 1):
                if (xx - x) ** 2 + dy2 > r * r:
                    continue
                if l2_val > heatmap[yy, xx]:
                    heatmap[yy, xx] = l2_val

    mask = heatmap >= 0.0
    if not np.any(mask):
        cv2.imwrite(out_path, img)
        return

    max_l2 = float(np.max(heatmap[mask]))
    if max_l2 <= 0:
        max_l2 = 1.0
    legend_min = 0.0 if vmin is None else float(vmin)
    legend_max = max_l2 if vmax is None else float(vmax)
    if legend_max <= legend_min:
        legend_max = legend_min + 1.0

    ratio = np.clip((heatmap - legend_min) / (legend_max - legend_min), 0.0, 1.0)
    ratio_8u = (ratio * 255.0).astype(np.uint8)
    missing = (~mask).astype(np.uint8) * 255
    ratio_filled = (
        cv2.inpaint(ratio_8u, missing, 3, cv2.INPAINT_TELEA).astype(np.float32) / 255.0
    )

    valid_coords = np.column_stack(np.where(mask))
    if valid_coords.shape[0] >= 3:
        hull = cv2.convexHull(valid_coords[:, ::-1].astype(np.int32))
        hull_mask = np.zeros((img_h, img_w), dtype=np.uint8)
        cv2.fillConvexPoly(hull_mask, hull, 255)
    else:
        hull_mask = mask.astype(np.uint8) * 255

    # Blue -> Green -> Yellow -> Red colour ramp
    t = np.clip(ratio_filled, 0.0, 1.0)
    b_ch = np.zeros_like(t)
    g_ch = np.zeros_like(t)
    r_ch = np.zeros_like(t)
    s0 = t <= 1.0 / 3.0
    s1 = (t > 1.0 / 3.0) & (t <= 2.0 / 3.0)
    s2 = t > 2.0 / 3.0
    b_ch[s0] = 1.0 - t[s0] * 3.0
    g_ch[s0] = t[s0] * 3.0
    g_ch[s1] = 1.0
    r_ch[s1] = (t[s1] - 1.0 / 3.0) * 3.0
    g_ch[s2] = 1.0 - (t[s2] - 2.0 / 3.0) * 3.0
    r_ch[s2] = 1.0

    color_img = np.stack([b_ch * 255, g_ch * 255, r_ch * 255], axis=-1).astype(np.float32)
    blended = img.astype(np.float32)
    use_mask = hull_mask > 0
    blended[use_mask] = (1.0 - alpha) * blended[use_mask] + alpha * color_img[use_mask]
    img = blended.astype(np.uint8)

    # Legend colour bar
    bar_w = max(12, int(img_w * 0.02))
    bar_h = max(60, int(img_h * 0.25))
    pad = max(6, int(min(img_w, img_h) * 0.015))
    x0 = max(0, img_w - bar_w - pad)
    y0 = max(0, pad)
    bar = np.zeros((bar_h, bar_w, 3), dtype=np.uint8)
    for i in range(bar_h):
        tv = 1.0 - (i / max(1, bar_h - 1))
        if tv <= 1.0 / 3.0:
            bv, gv, rv = 1.0 - tv * 3, tv * 3, 0.0
        elif tv <= 2.0 / 3.0:
            bv, gv, rv = 0.0, 1.0, (tv - 1.0 / 3.0) * 3
        else:
            bv, gv, rv = 0.0, 1.0 - (tv - 2.0 / 3.0) * 3, 1.0
        bar[i, :] = [int(bv * 255), int(gv * 255), int(rv * 255)]
    x1 = min(img_w, x0 + bar_w)
    y1 = min(img_h, y0 + bar_h)
    bar = bar[: y1 - y0, : x1 - x0]
    img[y0:y1, x0:x1] = bar
    cv2.rectangle(img, (x0, y0), (x1 - 1, y1 - 1), (0, 0, 0), 1)

    font = cv2.FONT_HERSHEY_SIMPLEX
    text_scale = max(0.35, img_h / 1200.0)
    thickness = 1
    max_label = f"{legend_max:.3f}"
    min_label = f"{legend_min:.3f}"
    max_sz = cv2.getTextSize(max_label, font, text_scale, thickness)[0]
    min_sz = cv2.getTextSize(min_label, font, text_scale, thickness)[0]
    tx = max(2, x0 - 4 - max(max_sz[0], min_sz[0]))
    cv2.putText(img, max_label, (tx, y0 + max_sz[1] + 1),
                font, text_scale, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.putText(img, min_label, (tx, y1 - 2),
                font, text_scale, (0, 0, 0), thickness, cv2.LINE_AA)
    title = "L2 (m)"
    tsz = cv2.getTextSize(title, font, text_scale * 0.85, thickness)[0]
    cv2.putText(img, title, (max(2, x0 - 4 - tsz[0]), max(2 + tsz[1], y0 - 2)),
                font, text_scale * 0.85, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.imwrite(out_path, img)


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def evaluate_single_cam(
    amc_calib_path: str,
    gt_calib_path: str,
    gt_traj_path: str,
    output_dir: str,
    eval_config_path: str,
    layout_image_path: str,
    skip_bounds_check: bool = False,
    include_all_objects: bool = False,
    object_types: Optional[List[str]] = None,
    show_plot: bool = False,
) -> None:
    _configure_matplotlib(show_plot)

    # ---- load inputs ----
    print("[SV Eval] Loading GT calibration ...")
    P_gt, gt_translation, gt_scale, frame_size = load_gt_calibration(gt_calib_path)
    print(f"  GT translation : {gt_translation}")
    print(f"  GT scale       : {gt_scale}")

    print("[SV Eval] Loading AMC P_world ...")
    P_world = load_amc_projection_matrix(amc_calib_path)
    print(f"  P_world shape  : {P_world.shape}")

    print("[SV Eval] Loading eval config ...")
    with open(eval_config_path, "r") as f:
        eval_cfg = yaml.safe_load(f)
    ppm = float(eval_cfg["layout_px_per_m"])
    print(f"  layout_px_per_m: {ppm}")

    print("[SV Eval] Loading layout image ...")
    layout_img = cv2.imread(layout_image_path)
    if layout_img is None:
        raise ValueError(f"Cannot read layout image: {layout_image_path}")
    layout_h, layout_w = layout_img.shape[:2]
    print(f"  Layout size    : {layout_w} x {layout_h}")

    amc_translation = compute_amc_translation(layout_h, ppm)
    amc_scale = ppm
    print(f"  AMC translation (derived): {amc_translation}")
    print(f"  AMC scale      : {amc_scale}")

    print("[SV Eval] Loading GT trajectories ...")
    trajectories = load_gt_trajectories(
        gt_traj_path,
        include_all_objects=include_all_objects,
        object_types=object_types,
    )
    n_frames = len(trajectories)
    n_points = sum(len(objs) for objs in trajectories.values())
    print(f"  {n_frames} frames, {n_points} trajectory points")
    if not trajectories:
        raise ValueError("No valid trajectories loaded from ground_truth.json.")

    # ---- precompute floor homography inverse ----
    # P_world columns [0,1,3] form the 3x3 floor->image homography (Z=0 plane).
    H_floor2img = P_world[:, [0, 1, 3]]
    H_img2floor = np.linalg.inv(H_floor2img)

    # ---- per-point evaluation ----
    # output_path = f"{output_dir}/evaluation"
    # output_dir = validate_output_directory(output_path, "Single-cam evaluation output")
    output_path = f"{output_dir}/single_view_results/evaluation"
    output_dir = validate_output_directory(output_path, "Single-cam evaluation output")

    all_rows: List[dict] = []
    gt_map_points: List[np.ndarray] = []
    est_map_points: List[np.ndarray] = []
    l2_floor_list: List[float] = []

    for frame_idx in sorted(trajectories.keys()):
        for obj in trajectories[frame_idx]:
            pt3d_gt = obj["pos_3d"]

            # 1. Project GT 3D point to 2D using GT camera
            pt2d = project_3d_point_to_image_3x4_proj_mat(pt3d_gt, P_gt)

            if not skip_bounds_check and frame_size is not None:
                w, h = frame_size
                if not (0 <= pt2d[0] <= w and 0 <= pt2d[1] <= h):
                    continue

            # 2. Back-project 2D point through AMC camera onto floor (Z=0)
            pt3d_est = backproject_to_floor(pt2d, H_img2floor)

            # 3. Convert both to common map coordinates
            pt_map_gt = pt3d_gt + gt_translation
            pt_map_est = pt3d_est + amc_translation

            # 4. L2 distance
            diff = pt_map_gt - pt_map_est
            l2_3d = float(np.linalg.norm(diff))
            l2_floor = float(np.linalg.norm(diff[:2]))

            all_rows.append({
                "frame": frame_idx,
                "object_id": obj["id"],
                "l2_dist_floor": l2_floor,
                "l2_dist_3d": l2_3d,
                "pt2d_x": pt2d[0],
                "pt2d_y": pt2d[1],
                "pt_map_gt_x": pt_map_gt[0],
                "pt_map_gt_y": pt_map_gt[1],
                "pt_map_gt_z": pt_map_gt[2],
                "pt_map_est_x": pt_map_est[0],
                "pt_map_est_y": pt_map_est[1],
                "pt_map_est_z": pt_map_est[2],
            })
            gt_map_points.append(pt_map_gt)
            est_map_points.append(pt_map_est)
            l2_floor_list.append(l2_floor)

    if not all_rows:
        raise ValueError(
            "No evaluation points produced.  "
            "Check that GT trajectory points project within image bounds."
        )

    n_eval = len(all_rows)
    l2_floor_arr = np.array([r["l2_dist_floor"] for r in all_rows])
    l2_3d_arr = np.array([r["l2_dist_3d"] for r in all_rows])

    print(f"\n[SV Eval] Evaluated {n_eval} points")
    print(f"  Floor L2  mean: {np.mean(l2_floor_arr):.4f}  "
          f"median: {np.median(l2_floor_arr):.4f}  "
          f"max: {np.max(l2_floor_arr):.4f}")
    print(f"  3D    L2  mean: {np.mean(l2_3d_arr):.4f}  "
          f"median: {np.median(l2_3d_arr):.4f}  "
          f"max: {np.max(l2_3d_arr):.4f}")

    # ---- write outputs ----
    _write_csv(os.path.join(output_path, "all_results.csv"), all_rows)
    _write_stats(os.path.join(output_path, "statistics.txt"), l2_floor_arr, l2_3d_arr)
    _write_basic_plots(output_path, l2_floor_arr, l2_3d_arr)

    _draw_overlay(
        layout_image_path,
        os.path.join(output_path, "results_layout_3d_points.png"),
        gt_map_points, est_map_points,
        gt_scale, amc_scale,
        gt_color=(0, 0, 0),
        est_color=(0, 0, 255),
    )
    # _draw_l2_heatmap_overlay(
    #     layout_image_path,
    #     os.path.join(output_path, "overlay_l2_heatmap.png"),
    #     gt_map_points, l2_floor_list,
    #     gt_scale,
    # )

    print(f"\n[SV Eval] Results written to: {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Single-camera L2 evaluation via floor-plane intersection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--amc_calib", required=True,
        help="Path to AMC camInfo_world.yaml (from sv_post_process)",
    )
    parser.add_argument(
        "--gt_calib", required=True,
        help="Path to GT calibration.json",
    )
    parser.add_argument(
        "--gt_traj", required=True,
        help="Path to ground_truth.json",
    )
    parser.add_argument(
        "--eval_config", required=True,
        help="Path to eval_config.yaml (must contain layout_px_per_m)",
    )
    parser.add_argument(
        "--output_dir", required=True,
        help="Output directory for evaluation results",
    )
    parser.add_argument(
        "--layout_image_path", required=True,
        help="Path to layout image (needed for coordinate alignment and overlays)",
    )
    parser.add_argument(
        "--skip_bounds_check", action="store_true",
        help="Skip 2D image-bounds check for projected GT points",
    )
    parser.add_argument(
        "--include_all_objects", action="store_true",
        help="Use all objects (disable human-only filter)",
    )
    parser.add_argument(
        "--object_types", default=None,
        help="Comma-separated object types to include (e.g. 'person,cardbox')",
    )
    parser.add_argument(
        "--show_plot", action="store_true",
        help="Show interactive plot window (requires display)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    evaluate_single_cam(
        amc_calib_path=validate_input_file(args.amc_calib, "AMC calibration"),
        gt_calib_path=validate_input_file(args.gt_calib, "GT calibration"),
        gt_traj_path=validate_input_file(args.gt_traj, "GT trajectory"),
        output_dir=args.output_dir,
        eval_config_path=validate_input_file(args.eval_config, "eval config"),
        layout_image_path=validate_input_file(args.layout_image_path, "layout image"),
        skip_bounds_check=args.skip_bounds_check,
        include_all_objects=args.include_all_objects,
        object_types=args.object_types.split(",") if args.object_types else None,
        show_plot=args.show_plot,
    )
