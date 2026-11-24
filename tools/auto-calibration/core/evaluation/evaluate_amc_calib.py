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

import argparse
import csv
import json
import os
import sys
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import cv2
import matplotlib
import yaml
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

# Basic usage:
#   Required: --amc_calib, --gt_calib, --output_dir
#   GT-trajectory mode (default; add --layout_image_path to enable overlays):
#     python3 core/evaluation/evaluate_amc_calib.py --amc_calib <amc.json> --gt_calib <gt.json> \
#         --gt_traj <ground_truth.json> --output_dir <out> [--layout_image_path <layout.png>]
#   Grid mode (requires layout image):
#     python3 core/evaluation/evaluate_amc_calib.py --amc_calib <amc.json> --gt_calib <gt.json> \
#         --output_dir <out> --layout_image_path <layout.png> --use_grid --grid_size 0.5
#   Optional ROI clipping for grid:
#     add --use_roi [--roi_id <id>]
#   Optional:
#     --show_3d (interactive 3D window)

# Coordinate systems:
#   {gt_world}  - GT world coordinate system. Metric scale (meters).
#                 Can be located anywhere in 3D space. Its X and Y axes are
#                 aligned with {map}, so the two frames differ by a pure
#                 translation only (see the calibration.json schema).
#   {cam0}      - Coordinate system of camera 0.
#   {amc_world} - AMC world coordinate system. Metric scale (meters).
#                 Origin is at the top-left corner of the floor plan in 3D
#                 real space.
#   {map}       - Temporary frame with its origin at the bottom-left corner
#                 of the floor plan. Used to derive translationToGlobalCoordinates_y.

# Coordinate math:
#   y_amc_world = -y_gt_world + ty
#   y_map       = -y_amc_world + H_px / scaleFactor
#               = y_gt_world - ty + H_px / scaleFactor
#   translationToGlobalCoordinates_x = tx
#   translationToGlobalCoordinates_y = -ty + H_px / scaleFactor


current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(current_dir)
sys.path.append(project_root)

from core.utils.path_validation import validate_input_file, validate_output_directory
from core.camera_estimation.bundle_adjustment import triangulate_points
from core.evaluation.evaluate_multi_cam import project_3d_point_to_image_3x4_proj_mat


def _write_basic_plots(
    output_dir: str,
    values: List[float],
    per_cam_values: Dict[str, List[float]],
) -> None:
    """Generate and save an L2 histogram and per-camera boxplot to ``output_dir``."""
    import matplotlib.pyplot as plt
    vals = np.array(values, dtype=np.float64)
    if vals.size == 0:
        return

    fig, axes = plt.subplots(2, 1, figsize=(8, 6), gridspec_kw={"height_ratios": [2, 1]})
    axes[0].hist(vals, bins=50, color="#4C72B0", edgecolor="black", alpha=0.85)
    axes[0].set_xlabel("L2 Distance")
    axes[0].set_ylabel("Count")
    axes[0].set_title("L2 Distance Histogram (All Cameras)")

    cam_ids = sorted(per_cam_values.keys())
    box_labels = ["all"] + cam_ids
    box_data = [vals] + [np.array(per_cam_values[c], dtype=np.float64) for c in cam_ids]
    axes[1].boxplot(
        box_data,
        vert=False,
        patch_artist=True,
        boxprops=dict(facecolor="#55A868", color="black"),
        medianprops=dict(color="black"),
    )
    axes[1].set_yticks(range(1, len(box_labels) + 1))
    axes[1].set_yticklabels(box_labels)
    axes[1].set_xlabel("L2 Distance")
    axes[1].set_title("L2 Distance Boxplot (All + Per Camera)")

    plt.tight_layout()
    out_path = os.path.join(output_dir, "l2_summary.png")
    plt.savefig(out_path, dpi=150)
    plt.close(fig)


def _get_attr_value(attributes: List[dict], name: str, default: Optional[str] = None) -> Optional[str]:
    """Look up a named attribute value from a list of {name, value} dicts."""
    for attr in attributes:
        if attr.get("name") == name:
            return attr.get("value")
    return default


def _normalize_cam_id(cam_id: str) -> Optional[str]:
    """Normalize camera identifiers like 'Camera_1' or 'cam_02' to zero-padded two-digit strings."""
    if not isinstance(cam_id, str):
        return None
    base = cam_id.strip()
    if base.lower().startswith("camera"):
        if base == "Camera":
            return "00"
        parts = base.split("_")
        if len(parts) >= 2:
            try:
                return f"{int(parts[1]):02d}"
            except ValueError:
                return None
    if base.lower().startswith("cam_"):
        digits = ""
        for ch in base[4:]:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            return f"{int(digits):02d}"
    return None


def load_calibration(
    calib_path: str,
) -> Tuple[Dict[str, np.ndarray], np.ndarray, float, Dict[str, Tuple[int, int]], Dict[str, str]]:
    """
    Load calibration.json-like file produced by AMC exporter.

    Returns:
        proj_mats: cam_id -> 3x4 projection matrix
        translation_to_global: np.array([x, y, z])
        scale_factor: float (not used for L2 in map coordinates)
        frame_sizes: cam_id -> (width, height)
    """
    with open(calib_path, "r") as f:
        data = json.load(f)

    proj_mats: Dict[str, np.ndarray] = {}
    frame_sizes: Dict[str, Tuple[int, int]] = {}
    norm_to_raw: Dict[str, str] = {}
    translation_to_global: Optional[np.ndarray] = None
    scale_factor: Optional[float] = None

    for sensor in data.get("sensors", []):
        sensor_type = sensor.get("type")
        if sensor_type is not None and sensor_type != "camera":
            continue
        raw_id = sensor.get("id")
        cam_id = _normalize_cam_id(raw_id)
        cam_matrix = sensor.get("cameraMatrix")
        if cam_id is None or cam_matrix is None:
            continue
        if cam_id not in norm_to_raw and isinstance(raw_id, str):
            norm_to_raw[cam_id] = raw_id

        proj_mats[cam_id] = np.array(cam_matrix, dtype=np.float64).reshape((3, 4))

        if translation_to_global is None:
            trans = sensor.get("translationToGlobalCoordinates") or sensor.get("translationToGlobalCoordinate") or {}
            tx = float(trans.get("x", 0.0))
            ty = float(trans.get("y", 0.0))
            tz = float(trans.get("z", 0.0))
            translation_to_global = np.array([tx, ty, tz], dtype=np.float64)

        if scale_factor is None:
            scale_factor = float(sensor.get("scaleFactor", -1.0))

        attributes = sensor.get("attributes", [])
        w_val = _get_attr_value(attributes, "frameWidth")
        h_val = _get_attr_value(attributes, "frameHeight")
        if w_val is not None and h_val is not None:
            try:
                frame_sizes[cam_id] = (int(float(w_val)), int(float(h_val)))
            except ValueError:
                pass

    if translation_to_global is None:
        translation_to_global = np.zeros(3, dtype=np.float64)
    if scale_factor is None:
        scale_factor = -1.0

    return proj_mats, translation_to_global, scale_factor, frame_sizes, norm_to_raw


def _is_human_object(obj: dict) -> bool:
    """Return True if the object dict describes a human/person/character based on its text fields."""
    text_fields = [
        obj.get("object name"),
        obj.get("objectName"),
        obj.get("name"),
        obj.get("type"),
        obj.get("category"),
        obj.get("semantic label"),
    ]
    text = " ".join([str(v) for v in text_fields if v is not None]).lower()
    if "character" in text or "human" in text or "person" in text or "people" in text:
        return True
    return False


def _get_object_id(obj: dict) -> str:
    """Extract a unique object identifier string, handling slash-delimited paths and numeric ids."""
    obj_name = obj.get("object name") or obj.get("objectName") or ""
    if isinstance(obj_name, str) and "/" in obj_name:
        parts = obj_name.split("/")
        if len(parts) >= 4:
            return parts[3]
        return parts[-1]
    obj_id = obj.get("object id")
    if obj_id is not None:
        if int(obj_id) == 0:
            return "Character"
        return f"Character_{int(obj_id):02d}"
    return str(obj.get("id", "unknown"))


def _get_object_type(obj: dict) -> str:
    """Return the lowercased object type/category string, or empty string if not found."""
    for key in ("object type", "object_type", "type", "category"):
        if key in obj and isinstance(obj[key], str):
            return obj[key].strip().lower()
    return ""


def load_gt_trajectories(
    gt_path: str,
    include_all_objects: bool = False,
    object_types: Optional[Iterable[str]] = None,
) -> Dict[int, List[dict]]:
    """
    Load ground_truth.json and return per-frame list of objects.

    Returns:
        dict[int, list[dict]] with keys: frame_idx -> [{"id": str, "pos_3d": np.ndarray}, ...]
    """
    with open(gt_path, "r") as f:
        data = json.load(f)

    trajectories: Dict[int, List[dict]] = {}
    for frame_key, objects in data.items():
        try:
            frame_idx = int(frame_key)
        except ValueError:
            continue
        if not isinstance(objects, list):
            continue

        frame_objs = []
        allowed_types = None
        if object_types:
            allowed_types = {t.strip().lower() for t in object_types if t.strip()}
        for obj in objects:
            if not include_all_objects:
                if allowed_types is not None:
                    obj_type = _get_object_type(obj)
                    if obj_type not in allowed_types:
                        continue
                elif not _is_human_object(obj):
                    continue
            pos_3d = obj.get("3d location") or obj.get("3d_location") or obj.get("pos_3d")
            if not isinstance(pos_3d, (list, tuple)) or len(pos_3d) < 3:
                continue
            frame_objs.append({
                "id": _get_object_id(obj),
                "pos_3d": np.array(pos_3d[:3], dtype=np.float64),
            })
        trajectories[frame_idx] = frame_objs

    return trajectories


def generate_grid_trajectories(
    layout_w: int,
    layout_h: int,
    translation: np.ndarray,
    scale: float,
    grid_size: float,
) -> Dict[int, List[dict]]:
    """Generate a uniform grid of synthetic 3D points spanning the layout image extent.

    Converts layout pixel bounds to world coordinates using ``scale`` and ``translation``,
    then creates points at ``grid_size`` intervals. Returns a single-frame trajectory dict
    (frame 0) compatible with the GT trajectory format.
    """
    if grid_size <= 0:
        raise ValueError("grid_size must be positive.")
    if scale <= 0:
        raise ValueError("scaleFactor must be positive for grid generation.")

    x_min = 0.0 / scale - translation[0]
    x_max = float(layout_w) / scale - translation[0]
    y_min = float(layout_h) / scale - translation[1]
    y_max = 0.0 / scale - translation[1]
    if y_min > y_max:
        y_min, y_max = y_max, y_min

    xs = np.arange(x_min, x_max + grid_size * 0.5, grid_size)
    ys = np.arange(y_min, y_max + grid_size * 0.5, grid_size)

    frame_objs: List[dict] = []
    idx = 0
    for x in xs:
        for y in ys:
            frame_objs.append({
                "id": f"grid_{idx:06d}",
                "pos_3d": np.array([float(x), float(y), 0.0], dtype=np.float64),
            })
            idx += 1

    return {0: frame_objs}


def _point_in_polygon(point: np.ndarray, polygon: List[np.ndarray]) -> bool:
    """Ray-casting test for whether a 2D point lies inside a polygon."""
    x, y = float(point[0]), float(point[1])
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _filter_points_in_rois(
    points: List[np.ndarray],
    roi_polygons: List[List[np.ndarray]],
) -> List[np.ndarray]:
    """Keep only points that fall inside at least one ROI polygon."""
    if not roi_polygons:
        return points
    filtered: List[np.ndarray] = []
    for pt in points:
        for poly in roi_polygons:
            if _point_in_polygon(pt, poly):
                filtered.append(pt)
                break
    return filtered


def _load_roi_polygons(
    calib_path: str,
    roi_id: Optional[str],
) -> List[List[np.ndarray]]:
    """Load ROI polygons from a calibration JSON, optionally filtered by ``roi_id``."""
    with open(calib_path, "r") as f:
        data = json.load(f)
    polygons: List[List[np.ndarray]] = []
    for sensor in data.get("sensors", []):
        for roi in sensor.get("rois", []) or []:
            if roi_id and roi.get("id") != roi_id:
                continue
            coords = roi.get("roiCoordinates") or []
            poly: List[np.ndarray] = []
            for pt in coords:
                if "x" in pt and "y" in pt:
                    poly.append(np.array([float(pt["x"]), float(pt["y"])], dtype=np.float64))
            if len(poly) >= 3:
                polygons.append(poly)
    return polygons


def _parse_camera_pairs(camera_pairs_arg: Optional[str], cam_ids: Iterable[str]) -> List[Tuple[str, str]]:
    """Parse a semicolon-delimited camera-pairs string into (cam_a, cam_b) tuples.

    When ``camera_pairs_arg`` is None, returns all unique pairs from ``cam_ids``.
    """
    cam_ids_sorted = sorted(list(cam_ids))
    if not camera_pairs_arg:
        pairs = []
        for i in range(len(cam_ids_sorted)):
            for j in range(i + 1, len(cam_ids_sorted)):
                pairs.append((cam_ids_sorted[i], cam_ids_sorted[j]))
        return pairs

    pairs = []
    for pair_str in camera_pairs_arg.split(";"):
        if not pair_str.strip():
            continue
        raw_items = [p.strip() for p in pair_str.split(",") if p.strip()]
        items = []
        for item in raw_items:
            items.append(_normalize_cam_id(item) or item)
        if len(items) < 2:
            continue
        if len(items) == 2:
            pairs.append((items[0], items[1]))
            continue
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                pairs.append((items[i], items[j]))
    return pairs


def _in_bounds(pt2d: np.ndarray, frame_size: Optional[Tuple[int, int]]) -> bool:
    """Check whether a 2D point lies within the frame bounds. Always True when frame_size is None."""
    if frame_size is None:
        return True
    w, h = frame_size
    return 0 <= pt2d[0] <= w and 0 <= pt2d[1] <= h


def _get_bbox_map(obj: dict, prefer_visible: bool) -> Optional[dict]:
    """Retrieve the per-camera 2D bounding-box dict, preferring the visible variant if requested."""
    if prefer_visible:
        bbox = obj.get("2d bounding box visible")
        if isinstance(bbox, dict):
            return bbox
    bbox = obj.get("2d bounding box")
    if isinstance(bbox, dict):
        return bbox
    return None


def _map_to_pixel(pt_map: np.ndarray, scale: float, img_h: int) -> np.ndarray:
    """Convert a 2D map-coordinate point to pixel coordinates (y-axis flipped for image space)."""
    px = pt_map[0] * scale
    py = img_h - pt_map[1] * scale
    return np.array([px, py], dtype=np.float64)


def _draw_overlay(
    layout_image_path: str,
    out_path: str,
    gt_points_map: List[np.ndarray],
    est_points_map: List[np.ndarray],
    gt_scale: float,
    est_scale: float,
    gt_color: Tuple[int, int, int],
    est_color: Tuple[int, int, int],
    roi_polygons_map: Optional[List[List[np.ndarray]]] = None,
    roi_color: Tuple[int, int, int] = (0, 0, 0),
) -> None:
    """Draw GT (crosses) and estimated (dots) points onto the layout image and save to ``out_path``.

    Optionally draws semi-transparent ROI polygon overlays.
    """
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
            cv2.circle(img, (x, y), 1, est_color, -1)

    if roi_polygons_map:
        overlay = img.copy()
        for poly in roi_polygons_map:
            pts = [_map_to_pixel(pt, gt_scale, img_h) for pt in poly]
            pts_int = np.array([[int(round(p[0])), int(round(p[1]))] for p in pts], dtype=np.int32)
            if len(pts_int) >= 3:
                cv2.fillPoly(overlay, [pts_int], color=(0, 255, 0))
                cv2.polylines(img, [pts_int], isClosed=True, color=roi_color, thickness=2)
        cv2.addWeighted(overlay, 0.125, img, 0.875, 0, img)

    cv2.imwrite(out_path, img)


def _draw_l2_heatmap_overlay(
    layout_image_path: str,
    out_path: str,
    points_map: List[np.ndarray],
    l2_values: List[float],
    scale: float,
    roi_polygons_map: Optional[List[List[np.ndarray]]] = None,
    roi_color: Tuple[int, int, int] = (0, 0, 0),
    alpha: float = 0.6,
    radius: int = 3,
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
) -> None:
    """Render a blue-green-red L2-error heatmap over the layout image with a color-bar legend.

    Uses inpainting to fill gaps between sample points and a convex-hull mask to
    constrain the colored region. ``vmin``/``vmax`` control the legend range.
    """
    if not points_map or not l2_values or len(points_map) != len(l2_values):
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
    ratio = (heatmap - legend_min) / (legend_max - legend_min)
    ratio = np.clip(ratio, 0.0, 1.0)
    ratio_8u = (ratio * 255.0).astype(np.uint8)
    missing = (~mask).astype(np.uint8) * 255
    ratio_filled = cv2.inpaint(ratio_8u, missing, 3, cv2.INPAINT_TELEA).astype(np.float32) / 255.0
    valid_coords = np.column_stack(np.where(mask))
    if valid_coords.shape[0] >= 3:
        hull = cv2.convexHull(valid_coords[:, ::-1].astype(np.int32))
        hull_mask = np.zeros((img_h, img_w), dtype=np.uint8)
        cv2.fillConvexPoly(hull_mask, hull, 255)
    else:
        hull_mask = mask.astype(np.uint8) * 255
    ratio_use = np.clip(ratio_filled, 0.0, 1.0)
    color_img = np.zeros_like(img, dtype=np.float32)
    t = ratio_use
    b = np.zeros_like(t)
    g = np.zeros_like(t)
    r = np.zeros_like(t)
    seg0 = t <= 1.0 / 3.0
    seg1 = (t > 1.0 / 3.0) & (t <= 2.0 / 3.0)
    seg2 = t > 2.0 / 3.0
    b[seg0] = 1.0 - (t[seg0] * 3.0)
    g[seg0] = t[seg0] * 3.0
    g[seg1] = 1.0
    r[seg1] = (t[seg1] - 1.0 / 3.0) * 3.0
    g[seg2] = 1.0 - (t[seg2] - 2.0 / 3.0) * 3.0
    r[seg2] = 1.0
    color_img[..., 0] = b * 255.0
    color_img[..., 1] = g * 255.0
    color_img[..., 2] = r * 255.0
    blended = img.astype(np.float32)
    use_mask = hull_mask > 0
    blended[use_mask] = (1.0 - alpha) * blended[use_mask] + alpha * color_img[use_mask]
    img = blended.astype(np.uint8)
    if roi_polygons_map:
        for poly in roi_polygons_map:
            pts = [_map_to_pixel(pt, scale, img_h) for pt in poly]
            pts_int = np.array([[int(round(p[0])), int(round(p[1]))] for p in pts], dtype=np.int32)
            if len(pts_int) >= 3:
                cv2.polylines(img, [pts_int], isClosed=True, color=roi_color, thickness=2)
    # Legend: vertical color bar with min/max L2 values.
    bar_w = max(12, int(img_w * 0.02))
    bar_h = max(60, int(img_h * 0.25))
    pad = max(6, int(min(img_w, img_h) * 0.015))
    x0 = max(0, img_w - bar_w - pad)
    y0 = max(0, pad)
    bar = np.zeros((bar_h, bar_w, 3), dtype=np.uint8)
    for i in range(bar_h):
        t = 1.0 - (i / max(1, bar_h - 1))
        if t <= 1.0 / 3.0:
            b = 1.0 - t * 3.0
            g = t * 3.0
            r_val = 0.0
        elif t <= 2.0 / 3.0:
            b = 0.0
            g = 1.0
            r_val = (t - 1.0 / 3.0) * 3.0
        else:
            b = 0.0
            g = 1.0 - (t - 2.0 / 3.0) * 3.0
            r_val = 1.0
        bar[i, :, 0] = int(round(b * 255.0))
        bar[i, :, 1] = int(round(g * 255.0))
        bar[i, :, 2] = int(round(r_val * 255.0))
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
    max_size = cv2.getTextSize(max_label, font, text_scale, thickness)[0]
    min_size = cv2.getTextSize(min_label, font, text_scale, thickness)[0]
    text_x = max(2, x0 - 4 - max(max_size[0], min_size[0]))
    cv2.putText(img, max_label, (text_x, y0 + max_size[1] + 1), font, text_scale, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.putText(img, min_label, (text_x, y1 - 2), font, text_scale, (0, 0, 0), thickness, cv2.LINE_AA)
    legend_title = "L2 (global min/max)"
    title_scale = max(0.3, text_scale * 0.85)
    title_size = cv2.getTextSize(legend_title, font, title_scale, thickness)[0]
    title_x = max(2, x0 - 4 - title_size[0])
    title_y = max(2 + title_size[1], y0 - 2)
    cv2.putText(img, legend_title, (title_x, title_y), font, title_scale, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.imwrite(out_path, img)


def _draw_overlay_multi(
    layout_image_path: str,
    out_path: str,
    gt_points_map: List[np.ndarray],
    est_points_sets: List[Tuple[List[np.ndarray], Tuple[int, int, int]]],
    gt_scale: float,
    est_scale: float,
    gt_color: Tuple[int, int, int],
    roi_polygons_map: Optional[List[List[np.ndarray]]] = None,
    roi_color: Tuple[int, int, int] = (0, 0, 0),
) -> None:
    """Like ``_draw_overlay`` but accepts multiple estimated-point sets, each with its own color."""
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
    for pts, color in est_points_sets:
        for pt in pts:
            px = _map_to_pixel(pt, est_scale, img_h)
            x, y = int(round(px[0])), int(round(px[1]))
            if 0 <= x < img_w and 0 <= y < img_h:
                cv2.circle(img, (x, y), 1, color, -1)
    if roi_polygons_map:
        overlay = img.copy()
        for poly in roi_polygons_map:
            pts = [_map_to_pixel(pt, gt_scale, img_h) for pt in poly]
            pts_int = np.array([[int(round(p[0])), int(round(p[1]))] for p in pts], dtype=np.int32)
            if len(pts_int) >= 3:
                cv2.fillPoly(overlay, [pts_int], color=(0, 255, 0))
                cv2.polylines(img, [pts_int], isClosed=True, color=roi_color, thickness=2)
        cv2.addWeighted(overlay, 0.125, img, 0.875, 0, img)
    cv2.imwrite(out_path, img)


def _configure_matplotlib(show_plot: bool) -> None:
    """Select the matplotlib backend: TkAgg when interactive display is requested, else Agg."""
    backend = "Agg"
    if show_plot and os.environ.get("DISPLAY"):
        backend = "TkAgg"
    matplotlib.use(backend, force=True)


def _draw_points_2d(
    out_path: str,
    frame_size: Tuple[int, int],
    points_2d: List[np.ndarray],
    color: Tuple[int, int, int],
    background_path: Optional[str] = None,
) -> None:
    """Draw 2D points as small circles on a camera image (or black canvas) and save to disk."""
    img = None
    if background_path and os.path.exists(background_path):
        img = cv2.imread(background_path)
    if img is None:
        img_w, img_h = frame_size
        img = np.zeros((img_h, img_w, 3), dtype=np.uint8)
    else:
        img_h, img_w = img.shape[:2]
    for pt in points_2d:
        x, y = int(round(pt[0])), int(round(pt[1]))
        if 0 <= x < img_w and 0 <= y < img_h:
            cv2.circle(img, (x, y), 1, color, -1)
    cv2.imwrite(out_path, img)


def _resolve_camera_image_path(
    cam_id: str,
    norm_to_raw: Dict[str, str],
    search_dirs: List[str],
) -> Optional[str]:
    """Search ``search_dirs`` for a camera image matching ``cam_id`` (png/jpg/jpeg)."""
    raw_id = norm_to_raw.get(cam_id)
    candidates: List[str] = []
    if raw_id:
        base = os.path.splitext(raw_id)[0]
        candidates.append(f"{base}.png")
        candidates.append(f"{base}.jpg")
        candidates.append(f"{base}.jpeg")
    candidates.append(f"cam_{cam_id}.png")
    candidates.append(f"cam_{cam_id}.jpg")
    candidates.append(f"cam_{cam_id}.jpeg")
    for directory in search_dirs:
        if not directory:
            continue
        for name in candidates:
            path = os.path.join(directory, name)
            if os.path.exists(path):
                return path
    return None
def evaluate_amc(
    amc_calib_path: str,
    gt_calib_path: str,
    gt_traj_path: Optional[str],
    output_dir: str,
    camera_pairs_arg: Optional[str],
    skip_bounds_check: bool,
    layout_image_path: Optional[str],
    overlay_transform: str,
    include_all_objects: bool,
    object_types: Optional[List[str]],
    use_bbox_visibility: bool,
    use_grid: bool,
    grid_size: float,
    use_roi: bool,
    roi_id: Optional[str],
    show_plot: bool,
) -> None:
    """Run the full AMC-vs-GT calibration evaluation pipeline.

    For every camera pair, projects GT 3D points through both calibrations,
    triangulates, and computes L2 reprojection error in map coordinates.
    Writes per-pair and aggregate CSVs, statistics, overlay images, heatmaps,
    3D scatter plots, and camera-center visualizations to ``output_dir``.
    """
    amc_proj_mats, amc_translation, amc_scale, amc_frame_sizes, amc_norm_to_raw = load_calibration(amc_calib_path)
    gt_proj_mats, gt_translation, gt_scale, gt_frame_sizes, gt_norm_to_raw = load_calibration(gt_calib_path)

    _configure_matplotlib(show_plot)

    common_cam_ids = sorted(set(amc_proj_mats.keys()) & set(gt_proj_mats.keys()))
    if not common_cam_ids:
        raise ValueError(
            "No common camera ids found between AMC and GT calibrations. "
            f"AMC ids: {sorted(amc_proj_mats.keys())}, GT ids: {sorted(gt_proj_mats.keys())}"
        )

    camera_pairs = _parse_camera_pairs(camera_pairs_arg, common_cam_ids)
    if not camera_pairs:
        raise ValueError("No camera pairs to evaluate.")

    layout_img = None
    layout_h = None
    layout_w = None
    if layout_image_path:
        layout_img = cv2.imread(layout_image_path)
        if layout_img is not None:
            layout_h, layout_w = layout_img.shape[:2]

    grid_input_points_map: List[np.ndarray] = []
    roi_polygons_world: List[List[np.ndarray]] = []
    roi_polygons_gt_world: List[List[np.ndarray]] = []
    roi_polygons_map_for_draw: List[List[np.ndarray]] = []
    if use_roi:
        roi_polygons_world = _load_roi_polygons(amc_calib_path, roi_id)
        if not roi_polygons_world:
            raise ValueError("No ROI polygons found for --use_roi (check roi_id).")
        gt_translation_2d = gt_translation[:2]
        amc_translation_2d = amc_translation[:2]
        roi_polygons_map_for_draw = [
            [pt + amc_translation_2d for pt in poly] for poly in roi_polygons_world
        ]
        roi_polygons_gt_world = [
            [pt + amc_translation_2d - gt_translation_2d for pt in poly] for poly in roi_polygons_world
        ]

    if use_grid:
        if layout_h is None or layout_w is None:
            raise ValueError("layout_image_path is required when using --use_grid.")
        trajectories = generate_grid_trajectories(
            layout_w,
            layout_h,
            gt_translation,
            gt_scale,
            grid_size,
        )
        grid_frame = trajectories.get(0, [])
        if grid_frame:
            grid_points = [obj["pos_3d"] for obj in grid_frame]
            if use_roi:
                grid_points = _filter_points_in_rois(grid_points, roi_polygons_gt_world)
                trajectories = {0: [{"id": f"grid_{idx:06d}", "pos_3d": pt} for idx, pt in enumerate(grid_points)]}
                grid_frame = trajectories.get(0, [])
            grid_input_points_map = [obj["pos_3d"] + gt_translation for obj in grid_frame]
    else:
        if not gt_traj_path:
            raise ValueError("--gt_traj is required unless --use_grid is set.")
        trajectories = load_gt_trajectories(
            gt_traj_path,
            include_all_objects=include_all_objects,
            object_types=object_types,
        )
    if not trajectories:
        raise ValueError("No valid trajectories found in ground_truth.json (after filtering).")

    output_dir = validate_output_directory(output_dir, "AMC evaluation output directory")
    all_rows: List[dict] = []
    overlay_rows_all: List[dict] = []

    if use_grid and grid_input_points_map and layout_h is not None:
        min_x = float(np.min([p[0] for p in grid_input_points_map]))
        max_x = float(np.max([p[0] for p in grid_input_points_map]))
        min_y = float(np.min([p[1] for p in grid_input_points_map]))
        max_y = float(np.max([p[1] for p in grid_input_points_map]))
        print(
            "[AMC Eval] grid map bounds after translation: "
            f"x[{min_x:.3f}, {max_x:.3f}], y[{min_y:.3f}, {max_y:.3f}]"
        )
        grid_rows: List[dict] = []
        for idx, pt_map in enumerate(grid_input_points_map):
            px = _map_to_pixel(pt_map, gt_scale, layout_h)
            grid_rows.append({
                "id": f"grid_{idx:06d}",
                "map_x": pt_map[0],
                "map_y": pt_map[1],
                "map_z": pt_map[2],
                "px_x": px[0],
                "px_y": px[1],
            })
        _write_csv(os.path.join(output_dir, "grid_input_points.csv"), grid_rows)
        _draw_overlay(
            layout_image_path,
            os.path.join(output_dir, "overlay_grid_input.png"),
            grid_input_points_map,
            [],
            gt_scale,
            amc_scale,
            (0, 0, 0),
            (0, 0, 0),
            roi_polygons_map_for_draw if use_roi else None,
            (0, 0, 255),
        )

    overlay_colors = [
        (0, 0, 255),
        (0, 255, 0),
        (255, 0, 0),
        (0, 255, 255),
        (255, 0, 255),
        (255, 255, 0),
        (255, 255, 255),
    ]
    overlay_gt_points: List[np.ndarray] = []
    overlay_est_points: List[np.ndarray] = []
    overlay_est_points_by_color: List[List[np.ndarray]] = [
        [] for _ in range(len(overlay_colors))
    ]
    heatmap_points_all: List[np.ndarray] = []
    heatmap_l2_all: List[float] = []
    pair_heatmap_data: Dict[Tuple[str, str], Tuple[List[np.ndarray], List[float]]] = {}
    gt_map_points: List[np.ndarray] = []
    amc_map_points: List[np.ndarray] = []
    grid_proj_points_by_cam: Dict[str, List[np.ndarray]] = {}
    for pair_idx, (cam0, cam1) in enumerate(camera_pairs):
        if cam0 not in common_cam_ids or cam1 not in common_cam_ids:
            continue
        gt_P0 = gt_proj_mats[cam0]
        gt_P1 = gt_proj_mats[cam1]
        amc_P0 = amc_proj_mats[cam0]
        amc_P1 = amc_proj_mats[cam1]

        frame_size0 = gt_frame_sizes.get(cam0) or amc_frame_sizes.get(cam0)
        frame_size1 = gt_frame_sizes.get(cam1) or amc_frame_sizes.get(cam1)

        pair_rows: List[dict] = []
        pair_gt_points: List[np.ndarray] = []
        pair_est_points: List[np.ndarray] = []
        pair_heatmap_points: List[np.ndarray] = []
        pair_heatmap_l2: List[float] = []
        for frame_idx in sorted(trajectories.keys()):
            for obj in trajectories[frame_idx]:
                bbox_map = _get_bbox_map(obj, use_bbox_visibility)
                if bbox_map is not None:
                    cam0_raw = gt_norm_to_raw.get(cam0)
                    cam1_raw = gt_norm_to_raw.get(cam1)
                    if cam0_raw not in bbox_map or cam1_raw not in bbox_map:
                        continue
                pt3d_gt_world = obj["pos_3d"]
                pt2d0 = project_3d_point_to_image_3x4_proj_mat(pt3d_gt_world, gt_P0)
                pt2d1 = project_3d_point_to_image_3x4_proj_mat(pt3d_gt_world, gt_P1)
                if use_grid:
                    grid_proj_points_by_cam.setdefault(cam0, []).append(np.array(pt2d0, dtype=np.float64))
                    grid_proj_points_by_cam.setdefault(cam1, []).append(np.array(pt2d1, dtype=np.float64))

                if not skip_bounds_check and bbox_map is None:
                    if not _in_bounds(pt2d0, frame_size0) or not _in_bounds(pt2d1, frame_size1):
                        continue

                pt3d_gt = triangulate_points(gt_P0, gt_P1, pt2d0, pt2d1)
                pt3d_est = triangulate_points(amc_P0, amc_P1, pt2d0, pt2d1)
                if pt3d_gt is None or pt3d_est is None:
                    continue

                pt3d_gt = np.array(pt3d_gt).flatten()
                pt3d_est = np.array(pt3d_est).flatten()

                pt_map_gt = pt3d_gt + gt_translation
                pt_map_est = pt3d_est + amc_translation

                gt_map_points.append(pt_map_gt)
                amc_map_points.append(pt_map_est)

                l2_dist = float(np.linalg.norm(pt_map_gt - pt_map_est))

                row = {
                    "frame": frame_idx,
                    "object_id": obj["id"],
                    "cam0": cam0,
                    "cam1": cam1,
                    "l2_dist": l2_dist,
                    "pt_map_gt_x": pt_map_gt[0],
                    "pt_map_gt_y": pt_map_gt[1],
                    "pt_map_gt_z": pt_map_gt[2],
                    "pt_map_est_x": pt_map_est[0],
                    "pt_map_est_y": pt_map_est[1],
                    "pt_map_est_z": pt_map_est[2],
                }
                pair_rows.append(row)
                all_rows.append(row)
                pair_gt_points.append(pt_map_gt)
                pair_est_points.append(pt_map_est)
                pair_heatmap_points.append(pt_map_gt)
                pair_heatmap_l2.append(l2_dist)
                heatmap_points_all.append(pt_map_gt)
                heatmap_l2_all.append(l2_dist)

                if layout_h is not None:
                    gt_px = _map_to_pixel(pt_map_gt, gt_scale, layout_h)
                    est_px = _map_to_pixel(pt_map_est, amc_scale, layout_h)
                    overlay_row = {
                        "frame": frame_idx,
                        "object_id": obj["id"],
                        "cam0": cam0,
                        "cam1": cam1,
                        "gt_px_x": gt_px[0],
                        "gt_px_y": gt_px[1],
                        "est_px_x": est_px[0],
                        "est_px_y": est_px[1],
                    }
                    overlay_rows_all.append(overlay_row)

        if pair_rows:
            pair_csv = os.path.join(output_dir, f"l2_dist_{cam0}_{cam1}.csv")
            _write_csv(pair_csv, pair_rows)
            overlay_gt_points.extend(pair_gt_points)
            overlay_est_points.extend(pair_est_points)
            overlay_est_points_by_color[pair_idx % len(overlay_colors)].extend(pair_est_points)

            if layout_image_path:
                est_color = overlay_colors[pair_idx % len(overlay_colors)]
                pair_overlay_path = os.path.join(output_dir, f"overlay_{cam0}_{cam1}.png")
                _draw_overlay(
                    layout_image_path,
                    pair_overlay_path,
                    pair_gt_points,
                    pair_est_points,
                    gt_scale,
                    amc_scale,
                    (0, 0, 0),
                    est_color,
                    roi_polygons_map_for_draw if use_roi else None,
                    (0, 0, 255),
                )
                if pair_heatmap_points:
                    pair_heatmap_data[(cam0, cam1)] = (pair_heatmap_points, pair_heatmap_l2)

            if layout_h is not None and overlay_rows_all:
                pair_overlay_csv = os.path.join(output_dir, f"overlay_points_{cam0}_{cam1}.csv")
                _write_csv(pair_overlay_csv, overlay_rows_all)

    if not all_rows:
        raise ValueError("No evaluation rows produced. Check inputs and filters.")

    all_csv = os.path.join(output_dir, "all_results.csv")
    _write_csv(all_csv, all_rows)

    stats_path = os.path.join(output_dir, "statistics.txt")
    l2_values = [r["l2_dist"] for r in all_rows]
    per_cam_values: Dict[str, List[float]] = {}
    for row in all_rows:
        for cam_key in ("cam0", "cam1"):
            cam_id = row[cam_key]
            per_cam_values.setdefault(cam_id, []).append(row["l2_dist"])
    _write_stats(stats_path, l2_values, per_cam_values)
    _write_basic_plots(output_dir, l2_values, per_cam_values)

    l2_global_min = float(np.min(heatmap_l2_all)) if heatmap_l2_all else None
    l2_global_max = float(np.max(heatmap_l2_all)) if heatmap_l2_all else None

    if layout_image_path and overlay_gt_points and overlay_est_points:
        overlay_path = os.path.join(output_dir, "overlay_all.png")
        est_sets = [
            (overlay_est_points_by_color[idx], overlay_colors[idx])
            for idx in range(len(overlay_colors))
            if overlay_est_points_by_color[idx]
        ]
        _draw_overlay_multi(
            layout_image_path,
            overlay_path,
            overlay_gt_points,
            est_sets,
            gt_scale,
            amc_scale,
            (0, 0, 0),
            roi_polygons_map_for_draw if use_roi else None,
            (0, 0, 255),
        )
        if heatmap_points_all:
            overlay_heatmap_path = os.path.join(output_dir, "overlay_heatmap_all.png")
            _draw_l2_heatmap_overlay(
                layout_image_path,
                overlay_heatmap_path,
                heatmap_points_all,
                heatmap_l2_all,
                gt_scale,
                roi_polygons_map_for_draw if use_roi else None,
                (0, 0, 255),
                vmin=l2_global_min,
                vmax=l2_global_max,
            )

    if layout_image_path and pair_heatmap_data:
        for (cam0, cam1), (pair_points, pair_l2) in pair_heatmap_data.items():
            pair_heatmap_path = os.path.join(output_dir, f"overlay_heatmap_{cam0}_{cam1}.png")
            _draw_l2_heatmap_overlay(
                layout_image_path,
                pair_heatmap_path,
                pair_points,
                pair_l2,
                gt_scale,
                roi_polygons_map_for_draw if use_roi else None,
                (0, 0, 255),
                vmin=l2_global_min,
                vmax=l2_global_max,
            )

    if use_grid and grid_proj_points_by_cam:
        search_dirs = [
            os.path.dirname(amc_calib_path),
            output_dir,
        ]
        for cam_id, points_2d in grid_proj_points_by_cam.items():
            frame_size = gt_frame_sizes.get(cam_id) or amc_frame_sizes.get(cam_id)
            if frame_size is None:
                continue
            background_path = _resolve_camera_image_path(cam_id, amc_norm_to_raw, search_dirs)
            out_path = os.path.join(output_dir, f"grid_projection_{cam_id}.png")
            _draw_points_2d(out_path, frame_size, points_2d, (255, 255, 255), background_path)

    if overlay_rows_all:
        overlay_csv = os.path.join(output_dir, "overlay_points_all.csv")
        _write_csv(overlay_csv, overlay_rows_all)

    _write_3d_plot(
        output_dir,
        gt_map_points,
        amc_map_points,
        gt_translation,
        amc_translation,
        show_plot,
    )
    _write_amc_in_gt_world_outputs(
        amc_calib_path=amc_calib_path,
        gt_translation=gt_translation,
        amc_translation=amc_translation,
        output_dir=output_dir,
    )
    _write_camera_center_plot(
        output_dir=output_dir,
        gt_calib_path=gt_calib_path,
        amc_calib_path=amc_calib_path,
        gt_translation=gt_translation,
        amc_translation=amc_translation,
    )


def _write_csv(path: str, rows: List[dict]) -> None:
    """Write a list of dicts to a CSV file, using keys from the first row as headers."""
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_stats(path: str, values: List[float], per_cam_values: Dict[str, List[float]]) -> None:
    """Write overall and per-camera L2 statistics (count, mean, std, min, max) to a text file."""
    vals = np.array(values, dtype=np.float64)
    with open(path, "w") as f:
        f.write("L2 Distance (Map coordinates)\n")
        f.write("Overall\n")
        f.write(f"Count: {len(vals)}\n")
        f.write(f"Mean: {np.mean(vals):.6f}\n")
        f.write(f"Std: {np.std(vals):.6f}\n")
        f.write(f"Min: {np.min(vals):.6f}\n")
        f.write(f"Max: {np.max(vals):.6f}\n")
        f.write("\nPer-camera\n")
        for cam_id in sorted(per_cam_values.keys()):
            cam_vals = np.array(per_cam_values[cam_id], dtype=np.float64)
            if cam_vals.size == 0:
                continue
            f.write(f"{cam_id}\n")
            f.write(f"Count: {len(cam_vals)}\n")
            f.write(f"Mean: {np.mean(cam_vals):.6f}\n")
            f.write(f"Std: {np.std(cam_vals):.6f}\n")
            f.write(f"Min: {np.min(cam_vals):.6f}\n")
            f.write(f"Max: {np.max(cam_vals):.6f}\n")
            f.write("\n")



def _camera_center_from_P(proj_mat: np.ndarray) -> Optional[np.ndarray]:
    """Recover the camera center in world coordinates from a 3x4 projection matrix via C = -M^{-1} p4."""
    if proj_mat.shape != (3, 4):
        return None
    M = proj_mat[:, :3]
    p4 = proj_mat[:, 3]
    try:
        center = -np.linalg.inv(M) @ p4
    except np.linalg.LinAlgError:
        return None
    return center


def _camera_center_from_extrinsic(extrinsic: np.ndarray) -> Optional[np.ndarray]:
    """Recover the camera center from a 3x4 extrinsic [R|t] matrix via C = -R^T t."""
    if extrinsic.shape != (3, 4):
        return None
    R = extrinsic[:, :3]
    t = extrinsic[:, 3]
    return -R.T @ t


def _get_frame_size(sensor: dict) -> Optional[Tuple[int, int]]:
    """Extract (width, height) from a sensor's attributes, or None if unavailable."""
    attributes = sensor.get("attributes", [])
    w_val = _get_attr_value(attributes, "frameWidth")
    h_val = _get_attr_value(attributes, "frameHeight")
    if w_val is not None and h_val is not None:
        try:
            return int(float(w_val)), int(float(h_val))
        except ValueError:
            return None
    return None


def _compute_camera_frustum_lines(
    K: np.ndarray,
    extrinsic: np.ndarray,
    frame_size: Optional[Tuple[int, int]],
    depth: float = 1.0,
) -> Optional[List[Tuple[np.ndarray, np.ndarray]]]:
    """Compute 3D line segments forming the camera frustum wireframe at a given depth.

    Returns eight lines: four from the camera center to the far-plane corners,
    and four connecting adjacent far-plane corners.
    """
    if frame_size is None:
        return None
    if K.shape != (3, 3) or extrinsic.shape != (3, 4):
        return None

    w, h = frame_size
    corners = np.array([
        [0.0, 0.0, 1.0],
        [float(w), 0.0, 1.0],
        [float(w), float(h), 1.0],
        [0.0, float(h), 1.0],
    ], dtype=np.float64).T  # 3x4

    try:
        K_inv = np.linalg.inv(K)
    except np.linalg.LinAlgError:
        return None

    R = extrinsic[:, :3]
    t = extrinsic[:, 3]
    center = -R.T @ t

    rays_cam = K_inv @ corners
    rays_cam = rays_cam / np.linalg.norm(rays_cam, axis=0, keepdims=True)
    rays_world = R.T @ rays_cam
    base_pts = center.reshape(3, 1) + depth * rays_world

    lines = []
    for i in range(4):
        lines.append((center, base_pts[:, i]))
    for i in range(4):
        j = (i + 1) % 4
        lines.append((base_pts[:, i], base_pts[:, j]))
    return lines


def _write_camera_center_plot(
    output_dir: str,
    gt_calib_path: str,
    amc_calib_path: str,
    gt_translation: np.ndarray,
    amc_translation: np.ndarray,
) -> None:
    """Generate a 3D scatter plot comparing GT and AMC camera centers with frustum wireframes.

    AMC cameras are shifted into the GT world frame before plotting.
    """
    data_gt = _load_calibration_json(gt_calib_path)
    data_amc = _load_calibration_json(amc_calib_path)
    gt_centers: List[np.ndarray] = []
    amc_centers: List[np.ndarray] = []
    gt_frustums: List[List[Tuple[np.ndarray, np.ndarray]]] = []
    amc_frustums: List[List[Tuple[np.ndarray, np.ndarray]]] = []

    gt_sensors = data_gt.get("sensors", [])
    amc_sensors = data_amc.get("sensors", [])
    if not isinstance(gt_sensors, list) or not isinstance(amc_sensors, list):
        return

    delta = (gt_translation - amc_translation).astype(np.float64)
    if delta.size >= 3:
        delta = delta.copy()
        delta[2] = 0.0

    for sensor in gt_sensors:
        sensor_type = sensor.get("type")
        if sensor_type is not None and sensor_type != "camera":
            continue
        matrices = _get_sensor_matrices(sensor)
        if matrices is None:
            continue
        K, extrinsic = matrices
        center = _camera_center_from_extrinsic(extrinsic)
        if center is not None:
            gt_centers.append(center)
        frame_size = _get_frame_size(sensor)
        frustum = _compute_camera_frustum_lines(K, extrinsic, frame_size, depth=1.0)
        if frustum is not None:
            gt_frustums.append(frustum)

    for sensor in amc_sensors:
        sensor_type = sensor.get("type")
        if sensor_type is not None and sensor_type != "camera":
            continue
        matrices = _get_sensor_matrices(sensor)
        if matrices is None:
            continue
        K, extrinsic = matrices
        R = extrinsic[:, :3]
        t = extrinsic[:, 3]
        t_new = t + (R @ delta.reshape(3))
        extrinsic_new = np.hstack([R, t_new.reshape((3, 1))])
        center = _camera_center_from_extrinsic(extrinsic_new)
        if center is not None:
            amc_centers.append(center)
        frame_size = _get_frame_size(sensor)
        frustum = _compute_camera_frustum_lines(K, extrinsic_new, frame_size, depth=1.0)
        if frustum is not None:
            amc_frustums.append(frustum)

    if not gt_centers and not amc_centers:
        return

    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(7, 6))
    ax = fig.add_subplot(111, projection="3d")
    if gt_centers:
        pts = np.array(gt_centers)
        ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], s=30, c="#C44E52", label="GT cameras")
    if amc_centers:
        pts = np.array(amc_centers)
        ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], s=30, c="#8172B2", label="AMC cameras (GT world)")
    for frustum in gt_frustums:
        for p0, p1 in frustum:
            ax.plot([p0[0], p1[0]], [p0[1], p1[1]], [p0[2], p1[2]], color="#C44E52", linewidth=0.8)
    for frustum in amc_frustums:
        for p0, p1 in frustum:
            ax.plot([p0[0], p1[0]], [p0[1], p1[1]], [p0[2], p1[2]], color="#8172B2", linewidth=0.8)
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    ax.set_title("Camera Centers (GT world)")
    ax.legend(loc="best", fontsize=8)
    if gt_centers or amc_centers:
        all_pts = np.array(gt_centers + amc_centers)
        mins = all_pts.min(axis=0)
        maxs = all_pts.max(axis=0)
        ax.set_xlim(mins[0], maxs[0])
        ax.set_ylim(mins[1], maxs[1])
        ax.set_zlim(mins[2], maxs[2])
        ax.set_box_aspect((maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2]))
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "camera_centers_3d.png"), dpi=150)
    plt.close(fig)


def _downsample_points(points: List[np.ndarray], max_points: int = 5000) -> List[np.ndarray]:
    """Uniformly subsample a point list to at most ``max_points`` entries."""
    if len(points) <= max_points:
        return points
    idx = np.linspace(0, len(points) - 1, max_points).astype(int)
    return [points[i] for i in idx]


def _load_calibration_json(path: str) -> dict:
    """Load and return the raw JSON dict from a calibration file."""
    with open(path, "r") as f:
        return json.load(f)


def _get_sensor_matrices(sensor: dict) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """Extract (K, extrinsic) from a sensor dict.

    Uses intrinsicMatrix/extrinsicMatrix when present; otherwise decomposes cameraMatrix
    via ``cv2.decomposeProjectionMatrix``.
    """
    intrinsic = sensor.get("intrinsicMatrix")
    extrinsic = sensor.get("extrinsicMatrix")
    if intrinsic is not None and extrinsic is not None:
        K = np.array(intrinsic, dtype=np.float64).reshape((3, 3))
        ext = np.array(extrinsic, dtype=np.float64).reshape((3, 4))
        return K, ext

    cam_matrix = sensor.get("cameraMatrix")
    if cam_matrix is None:
        return None
    P = np.array(cam_matrix, dtype=np.float64).reshape((3, 4))
    try:
        K, R, C_hom = cv2.decomposeProjectionMatrix(P)[:3]
    except cv2.error:
        return None
    if K[2, 2] != 0:
        K = K / K[2, 2]
    C = (C_hom[:3] / C_hom[3]).reshape((3, 1))
    t = -R @ C
    ext = np.hstack([R, t])
    return K, ext


def _compose_projection_matrix(K: np.ndarray, extrinsic: np.ndarray) -> np.ndarray:
    """Compose a 3x4 projection matrix P = K @ [R|t]."""
    return K @ extrinsic


def _write_caminfo_yaml(output_dir: str, cam_id: str, proj_mat: np.ndarray) -> None:
    """Write a single camera's 3x4 projection matrix to a YAML file."""
    out_path = os.path.join(output_dir, f"camInfo_{cam_id}.yaml")
    payload = {"projectionMatrix_3x4": proj_mat.tolist()}
    with open(out_path, "w") as f:
        yaml.safe_dump(payload, f, sort_keys=False, default_flow_style=False)


def _write_amc_in_gt_world_outputs(
    amc_calib_path: str,
    gt_translation: np.ndarray,
    amc_translation: np.ndarray,
    output_dir: str,
) -> None:
    """Re-express the AMC calibration in the GT world frame and write the result.

    Shifts each camera's extrinsic translation by the GT-AMC origin offset (XY only),
    recomputes projection matrices, and saves per-camera YAML and a combined JSON.
    """
    data = _load_calibration_json(amc_calib_path)
    sensors = data.get("sensors", [])
    if not isinstance(sensors, list):
        return
    delta = (gt_translation - amc_translation).astype(np.float64)
    if delta.size >= 3:
        delta = delta.copy()
        delta[2] = 0.0

    for sensor in sensors:
        sensor_type = sensor.get("type")
        if sensor_type is not None and sensor_type != "camera":
            continue
        cam_id = _normalize_cam_id(sensor.get("id"))
        matrices = _get_sensor_matrices(sensor)
        if matrices is None:
            continue
        K, extrinsic = matrices
        R = extrinsic[:, :3]
        t = extrinsic[:, 3]
        t_new = t + (R @ delta.reshape(3))
        extrinsic_new = np.hstack([R, t_new.reshape((3, 1))])
        P_new = _compose_projection_matrix(K, extrinsic_new)
        sensor["extrinsicMatrix"] = extrinsic_new.tolist()
        sensor["cameraMatrix"] = P_new.tolist()

        if cam_id is not None:
            _write_caminfo_yaml(output_dir, cam_id, P_new)

    base, _ = os.path.splitext(os.path.basename(amc_calib_path))
    out_json_path = os.path.join(output_dir, f"{base}_in_gt_world.json")
    with open(out_json_path, "w") as f:
        json.dump(data, f, indent=4)


def _write_3d_plot(
    output_dir: str,
    gt_map_points: List[np.ndarray],
    amc_map_points: List[np.ndarray],
    gt_translation: np.ndarray,
    amc_translation: np.ndarray,
    show_plot: bool,
) -> None:
    """Generate a 3D scatter plot of GT vs AMC map trajectories with world-origin axes."""
    import matplotlib.pyplot as plt
    gt_map_points = _downsample_points(gt_map_points)
    amc_map_points = _downsample_points(amc_map_points)

    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection="3d")

    if gt_map_points:
        pts = np.array(gt_map_points)
        ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], s=2, c="#C44E52", label="GT map")
    if amc_map_points:
        pts = np.array(amc_map_points)
        ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], s=2, c="#8172B2", label="AMC map")

    def _draw_axes(origin: np.ndarray, length: float, label: str, colors: Tuple[str, str, str]) -> None:
        ox, oy, oz = origin
        ax.quiver(ox, oy, oz, length, 0, 0, color=colors[0], linewidth=1)
        ax.quiver(ox, oy, oz, 0, length, 0, color=colors[1], linewidth=1)
        ax.quiver(ox, oy, oz, 0, 0, length, color=colors[2], linewidth=1)
        ax.text(ox, oy, oz, label, color="black", fontsize=8)

    all_points = []
    if gt_map_points:
        all_points.extend(gt_map_points)
    if amc_map_points:
        all_points.extend(amc_map_points)
    if all_points:
        pts = np.array(all_points)
        ranges = pts.max(axis=0) - pts.min(axis=0)
        base_size = float(np.max(ranges)) if np.all(np.isfinite(ranges)) else 1.0
    else:
        base_size = 1.0
    axis_len = max(base_size * 0.05, 0.5)
    _draw_axes(np.array([0.0, 0.0, 0.0]), axis_len, "Map axes", ("#000000", "#000000", "#000000"))
    _draw_axes(gt_translation, axis_len, "GT world axes", ("#4C72B0", "#55A868", "#C44E52"))
    _draw_axes(amc_translation, axis_len, "AMC world axes", ("#8172B2", "#CCB974", "#64B5CD"))

    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    ax.set_title("3D Map Trajectories")
    ax.legend(loc="best", fontsize=8)
    if all_points:
        pts = np.array(all_points)
        mins = pts.min(axis=0)
        maxs = pts.max(axis=0)
        ax.set_xlim(mins[0], maxs[0])
        ax.set_ylim(mins[1], maxs[1])
        ax.set_zlim(mins[2], maxs[2])
        ax.set_box_aspect((maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2]))
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "l2_3d_plot.png"), dpi=150)
    if show_plot:
        plt.show()
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for AMC calibration evaluation."""
    parser = argparse.ArgumentParser(
        description="Evaluate AMC calibration with GT trajectories",
        epilog=(
            "Args summary:\n"
            "  Required: --amc_calib, --gt_calib, --output_dir\n"
            "  GT-trajectory mode: --gt_traj (required unless --use_grid)\n"
            "  Grid mode: --use_grid (requires --layout_image_path; optional --grid_size)\n"
            "  ROI clipping (grid only): --use_roi (optional --roi_id)\n"
            "  Overlays: --layout_image_path enables overlay images\n"
            "  3D view: --show_3d (requires a display)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--amc_calib", required=True, help="Path to AMC calibration_xxx.json")
    parser.add_argument("--gt_calib", required=True, help="Path to ground-truth calibration.json")
    parser.add_argument("--gt_traj", default=None, help="Path to ground_truth.json")
    parser.add_argument("--output_dir", required=True, help="Output directory for evaluation results")
    parser.add_argument(
        "--camera_pairs",
        default=None,
        help='Optional camera pairs "cam_00,cam_01;cam_00,cam_02". Default: all pairs.',
    )
    parser.add_argument(
        "--skip_bounds_check",
        action="store_true",
        help="Skip 2D image bounds check for projected points.",
    )
    parser.add_argument(
        "--layout_image_path",
        default=None,
        help="Optional layout image path to plot GT vs AMC overlays in pixel space.",
    )
    parser.add_argument(
        "--overlay_transform",
        choices=["gt", "amc"],
        default="gt",
        help="Which calibration's scale/translation to use for pixel overlay.",
    )
    parser.add_argument(
        "--use_grid",
        action="store_true",
        help="Use generated grid points instead of ground_truth.json.",
    )
    parser.add_argument(
        "--grid_size",
        type=float,
        default=0.5,
        help="Grid spacing in map units when using --use_grid.",
    )
    parser.add_argument(
        "--use_roi",
        action="store_true",
        help="Clip grid points to ROI polygons from GT calibration.",
    )
    parser.add_argument(
        "--roi_id",
        default=None,
        help="Optional ROI id to select when using --use_roi (default: all).",
    )
    parser.add_argument(
        "--show_3d",
        action="store_true",
        help="Show the 3D plot window (requires a display).",
    )
    parser.add_argument(
        "--include_all_objects",
        action="store_true",
        help="Use all objects in ground_truth.json (disables human-only filter).",
    )
    parser.add_argument(
        "--object_types",
        default=None,
        help="Comma-separated object types to include (e.g., 'cardbox,person').",
    )
    parser.add_argument(
        "--use_bbox_visibility",
        action="store_true",
        help="Filter by presence of per-camera 2D boxes (prefers visible boxes).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    amc_calib_path = validate_input_file(args.amc_calib, "AMC calibration file")
    gt_calib_path = validate_input_file(args.gt_calib, "GT calibration file")
    gt_traj_path = None
    if args.gt_traj:
        gt_traj_path = validate_input_file(args.gt_traj, "GT trajectory file")
    output_dir = validate_output_directory(args.output_dir, "AMC evaluation output directory")
    layout_image_path = None
    if args.layout_image_path:
        layout_image_path = validate_input_file(args.layout_image_path, "layout image file")

    evaluate_amc(
        amc_calib_path=amc_calib_path,
        gt_calib_path=gt_calib_path,
        gt_traj_path=gt_traj_path,
        output_dir=output_dir,
        camera_pairs_arg=args.camera_pairs,
        skip_bounds_check=args.skip_bounds_check,
        layout_image_path=layout_image_path,
        overlay_transform=args.overlay_transform,
        include_all_objects=args.include_all_objects,
        object_types=args.object_types.split(",") if args.object_types else None,
        use_bbox_visibility=args.use_bbox_visibility,
        use_grid=args.use_grid,
        grid_size=args.grid_size,
        use_roi=args.use_roi,
        roi_id=args.roi_id,
        show_plot=args.show_3d,
    )
