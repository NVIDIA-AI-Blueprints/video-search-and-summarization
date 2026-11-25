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
Calibration Export Utility
Exports calibration results to JSON format with proper matrix decomposition
and coordinate transformations for ROIs and tripwires.
"""

import json
import yaml
import numpy as np
from .video_capture import open_video_capture
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# Annotations key for layout-space ROIs/tripwires (Parameters UI global mode)
GLOBAL_ROIS_TRIPWIRES_KEY = "global_rois_tripwires"

# Prefix so API handlers can map this to HTTP 400 instead of a generic 500.
INCOMPLETE_TRIPWIRE_MESSAGE_PREFIX = "Incomplete tripwire(s)."


class IncompleteTripwireError(Exception):
    """A tripwire line exists without a matching direction, or the reverse."""


def _finished_annotation_items(items: Optional[List]) -> List[Dict]:
    """Drop in-progress 'drawing' placeholders; those are not saved tripwires."""
    finished: List[Dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if item.get("id") == "drawing":
            continue
        finished.append(item)
    return finished


def _tripwire_segment_points(item: Dict) -> Optional[List]:
    if isinstance(item.get("points"), list):
        pts = item["points"]
    elif "start" in item and "end" in item:
        pts = [item["start"], item["end"]]
    else:
        return None
    if len(pts) < 2:
        return None
    return pts


def incomplete_tripwire_errors(
    source: str,
    tripwire_lines: Optional[List],
    tripwire_directions: Optional[List],
) -> List[str]:
    """One message per incomplete tripwire at ``source`` (camera id or layout)."""
    lines = _finished_annotation_items(tripwire_lines)
    directions = _finished_annotation_items(tripwire_directions)
    errors: List[str] = []
    for idx, wire in enumerate(lines):
        label = wire.get("id") or f"line {idx + 1}"
        if _tripwire_segment_points(wire) is None:
            errors.append(f"{source}: tripwire has an invalid line")
            continue
        if idx >= len(directions):
            errors.append(
                f"{source}: tripwire has no matching direction"
            )
            continue
        if _tripwire_segment_points(directions[idx]) is None:
            errors.append(
                f"{source}: tripwire has an invalid or incomplete direction"
            )
    extra = len(directions) - len(lines)
    if extra > 0:
        errors.append(
            f"{source}: {extra} tripwire direction(s) have no matching line"
        )
    return errors


def collect_incomplete_tripwire_errors(
    annotations_data: Dict, camera_ids: List[str]
) -> List[str]:
    errors: List[str] = []
    for cam_id in camera_ids:
        block = cam_annotations_for_sensor(annotations_data, cam_id)
        errors.extend(
            incomplete_tripwire_errors(
                cam_id,
                block.get("tripwireLines"),
                block.get("tripwireDirections"),
            )
        )
    global_ann = annotations_data.get(GLOBAL_ROIS_TRIPWIRES_KEY) or {}
    if isinstance(global_ann, dict):
        errors.extend(
            incomplete_tripwire_errors(
                "layout (global)",
                global_ann.get("tripwireLines"),
                global_ann.get("tripwireDirections"),
            )
        )
    return errors


def format_incomplete_tripwire_message(errors: List[str]) -> str:
    listed = " ".join(errors)
    return (
        f"{INCOMPLETE_TRIPWIRE_MESSAGE_PREFIX} Each tripwire needs a line and a "
        f"direction. Add the missing part or delete the extra one, then retry. {listed}"
    )

def strip_video_filename_extension(name: str) -> str:
    """
    Return the basename without its final suffix (last ``.`` and following segment).

    Examples: ``cam_00.mp4`` -> ``cam_00``, ``Cam_0.mov`` -> ``Cam_0``, ``cam_00`` -> ``cam_00``.
    Uses the last path segment only (``Path(...).stem``).
    """
    p = Path(str(name).strip())
    return p.stem if p.suffix else str(p)


def has_file_extension(name: str) -> bool:
    """True if the basename has a file extension (non-empty ``Path.suffix``)."""
    return bool(Path(str(name).strip()).suffix)


def cam_annotations_for_sensor(annotations_data: Dict, cam_id: str) -> Dict:
    """Return the per-camera annotations dict for internal id ``cam_id`` (e.g. ``cam_00``)."""
    if cam_id in annotations_data:
        block = annotations_data.get(cam_id)
        return block if isinstance(block, dict) else {}
    return {}


def sensor_id_for_export(display_name: str) -> str:
    """Sensor ``id`` for exported JSON: basename with final ``.suffix`` removed (see ``strip_video_filename_extension``)."""
    return strip_video_filename_extension(display_name)


def frame_dimensions_from_video_metadata(video_metadata: Dict) -> Tuple[int, int]:
    """
    Parse (frameWidth, frameHeight) from exporter-style video metadata dict.
    Returns (0, 0) if missing or invalid (caller should skip image clipping).
    """
    try:
        w = int(str(video_metadata.get("frameWidth", "0")).strip())
        h = int(str(video_metadata.get("frameHeight", "0")).strip())
    except (TypeError, ValueError):
        return 0, 0
    if w <= 0 or h <= 0:
        return 0, 0
    return w, h


def _clip_segment_to_rect(
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Liang–Barsky clip of segment p0–p1 to the closed axis-aligned rectangle. None if empty."""
    x0, y0 = float(p0[0]), float(p0[1])
    x1, y1 = float(p1[0]), float(p1[1])
    dx = x1 - x0
    dy = y1 - y0
    p = (-dx, dx, -dy, dy)
    q = (x0 - xmin, xmax - x0, y0 - ymin, ymax - y0)
    u1, u2 = 0.0, 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1e-15:
            if qi < 0:
                return None
            continue
        r = qi / pi
        if pi < 0:
            if r > u2:
                return None
            u1 = max(u1, r)
        else:
            if r < u1:
                return None
            u2 = min(u2, r)
    if u1 > u2 + 1e-12:
        return None
    return (
        (x0 + u1 * dx, y0 + u1 * dy),
        (x0 + u2 * dx, y0 + u2 * dy),
    )


def _clip_polygon_to_rect(
    poly: List[Tuple[float, float]],
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
) -> List[Tuple[float, float]]:
    """Sutherland–Hodgman clip of a polygon to an axis-aligned rectangle (inclusive bounds)."""

    def clip_one(
        poly_in: List[Tuple[float, float]],
        inside,
        intersect,
    ) -> List[Tuple[float, float]]:
        if not poly_in:
            return []
        out: List[Tuple[float, float]] = []
        n = len(poly_in)
        for i in range(n):
            cur = poly_in[i]
            prev = poly_in[i - 1]
            cur_in = inside(cur)
            prev_in = inside(prev)
            if cur_in:
                if not prev_in:
                    ipt = intersect(prev, cur)
                    if ipt is not None:
                        out.append(ipt)
                out.append(cur)
            elif prev_in:
                ipt = intersect(prev, cur)
                if ipt is not None:
                    out.append(ipt)
        return out

    def inter_x(s: Tuple[float, float], e: Tuple[float, float], x_const: float):
        sx, sy = s
        ex, ey = e
        if abs(ex - sx) < 1e-14:
            return None
        t = (x_const - sx) / (ex - sx)
        if -1e-8 <= t <= 1.0 + 1e-8:
            return (x_const, sy + t * (ey - sy))
        return None

    def inter_y(s: Tuple[float, float], e: Tuple[float, float], y_const: float):
        sx, sy = s
        ex, ey = e
        if abs(ey - sy) < 1e-14:
            return None
        t = (y_const - sy) / (ey - sy)
        if -1e-8 <= t <= 1.0 + 1e-8:
            return (sx + t * (ex - sx), y_const)
        return None

    p = list(poly)
    p = clip_one(p, lambda pt: pt[0] >= xmin, lambda a, b: inter_x(a, b, xmin))
    p = clip_one(p, lambda pt: pt[0] <= xmax, lambda a, b: inter_x(a, b, xmax))
    p = clip_one(p, lambda pt: pt[1] >= ymin, lambda a, b: inter_y(a, b, ymin))
    p = clip_one(p, lambda pt: pt[1] <= ymax, lambda a, b: inter_y(a, b, ymax))
    return p


def _clip_polygon_camera_space_z_ge(
    vertices_cam: List[np.ndarray],
    z_min: float,
) -> List[np.ndarray]:
    """
    Sutherland–Hodgman clip of a closed 3D polygon (camera coordinates) to half-space Z >= z_min.
    Vertices must be ordered; the polygon may be non-planar in camera space (ground ROI in world).
    """
    if len(vertices_cam) < 3:
        return []

    def inside(p: np.ndarray) -> bool:
        return float(p[2]) >= z_min

    def intersect_segment_z_plane(s: np.ndarray, e: np.ndarray) -> Optional[np.ndarray]:
        zs, ze = float(s[2]), float(e[2])
        if abs(ze - zs) < 1e-18:
            return None
        t = (z_min - zs) / (ze - zs)
        if t < -1e-9 or t > 1.0 + 1e-9:
            return None
        t = max(0.0, min(1.0, t))
        return s + t * (e - s)

    inp = vertices_cam
    out: List[np.ndarray] = []
    s = inp[-1]
    for e in inp:
        s_in, e_in = inside(s), inside(e)
        if e_in:
            if not s_in:
                ipt = intersect_segment_z_plane(s, e)
                if ipt is not None:
                    out.append(ipt)
            out.append(np.asarray(e, dtype=np.float64))
        elif s_in:
            ipt = intersect_segment_z_plane(s, e)
            if ipt is not None:
                out.append(ipt)
        s = e
    return out


def _clip_polygon_halfspace(
    vertices: List[np.ndarray],
    plane_value,
) -> List[np.ndarray]:
    """
    Sutherland–Hodgman clip to plane_value(p) >= 0.
    ``plane_value`` maps a vertex to a signed distance (float).
    """
    if len(vertices) < 2:
        return []

    def inside(p: np.ndarray) -> bool:
        return float(plane_value(p)) >= 0.0

    def intersect(s: np.ndarray, e: np.ndarray) -> Optional[np.ndarray]:
        ds, de = float(plane_value(s)), float(plane_value(e))
        denom = de - ds
        if abs(denom) < 1e-18:
            return None
        t = -ds / denom
        if t < -1e-9 or t > 1.0 + 1e-9:
            return None
        t = max(0.0, min(1.0, t))
        return s + t * (e - s)

    out: List[np.ndarray] = []
    s = vertices[-1]
    for e in vertices:
        s_in, e_in = inside(s), inside(e)
        if e_in:
            if not s_in:
                ipt = intersect(s, e)
                if ipt is not None:
                    out.append(ipt)
            out.append(np.asarray(e, dtype=np.float64))
        elif s_in:
            ipt = intersect(s, e)
            if ipt is not None:
                out.append(ipt)
        s = e
    return out


def _clip_segment_halfspace(
    a: np.ndarray,
    b: np.ndarray,
    plane_value,
) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """Clip open segment a–b to plane_value >= 0. None if empty."""
    da, db = float(plane_value(a)), float(plane_value(b))
    a_in, b_in = da >= 0.0, db >= 0.0
    if a_in and b_in:
        return (np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64))
    if not a_in and not b_in:
        return None
    denom = db - da
    if abs(denom) < 1e-18:
        return None
    t = -da / denom
    t = max(0.0, min(1.0, t))
    ipt = a + t * (b - a)
    if a_in:
        return (np.asarray(a, dtype=np.float64), ipt)
    return (ipt, np.asarray(b, dtype=np.float64))


def _single_cam_ground_H(
    P: np.ndarray,
    frame_width: int = 0,
    frame_height: int = 0,
) -> np.ndarray:
    """
    Ground-plane homography H = P[:, [0, 1, 3]] with chirality so that points in front of
    the camera have positive homogeneous depth w (third coordinate of H @ [wx, wy, 1]).

    SV ``camInfo_world`` is assembled from a floor homography (not a full K[R|t]), so multi-cam
    RQ decomposition is unreliable; callers must project with this H and clip on w / the image
    frustum instead.
    """
    P_arr = np.asarray(P, dtype=np.float64)
    if P_arr.ndim == 1 and P_arr.size >= 12:
        P_arr = P_arr.reshape(3, 4)
    H = np.asarray(P_arr[:3, [0, 1, 3]], dtype=np.float64).copy()
    ref_w: Optional[float] = None
    if frame_width > 0 and frame_height > 0:
        try:
            H_inv = np.linalg.inv(H)
            # Lower-center of the frame is almost always on the visible floor.
            pref = H_inv @ np.array(
                [0.5 * float(frame_width), 0.75 * float(frame_height), 1.0],
                dtype=np.float64,
            )
            if abs(float(pref[2])) > 1e-15:
                pref = pref / float(pref[2])
                ref_w = float((H @ np.array([pref[0], pref[1], 1.0], dtype=np.float64))[2])
        except np.linalg.LinAlgError:
            ref_w = None
    if ref_w is None:
        ref_w = float(H[2, 2])
    if ref_w < 0.0:
        H = -H
    return H


def _clip_homog_polygon_to_image_frustum(
    vertices_h: List[np.ndarray],
    frame_width: int,
    frame_height: int,
    z_min: float = 1e-3,
) -> List[np.ndarray]:
    """
    Clip homogeneous image points (X, Y, w) from H @ [wx, wy, 1] to w >= z_min and, when
    frame size is known, to the image rectangle 0..W × 0..H (planes X/w, Y/w).
    """
    if len(vertices_h) < 3:
        return []
    v = [np.asarray(p, dtype=np.float64).reshape(3) for p in vertices_h]
    v = _clip_polygon_halfspace(v, lambda p: float(p[2]) - z_min)
    if frame_width > 0 and frame_height > 0 and len(v) >= 3:
        W = float(frame_width)
        Ht = float(frame_height)
        v = _clip_polygon_halfspace(v, lambda p: float(p[0]))  # u >= 0
        v = _clip_polygon_halfspace(v, lambda p: float(p[2]) * W - float(p[0]))  # u <= W
        v = _clip_polygon_halfspace(v, lambda p: float(p[1]))  # v >= 0
        v = _clip_polygon_halfspace(v, lambda p: float(p[2]) * Ht - float(p[1]))  # v <= H
    return v if len(v) >= 3 else []


def _clip_homog_segment_to_image_frustum(
    a_h: np.ndarray,
    b_h: np.ndarray,
    frame_width: int,
    frame_height: int,
    z_min: float = 1e-3,
) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """Clip homogeneous segment to w >= z_min and optional image frustum. None if empty."""
    seg: Optional[Tuple[np.ndarray, np.ndarray]] = (
        np.asarray(a_h, dtype=np.float64).reshape(3),
        np.asarray(b_h, dtype=np.float64).reshape(3),
    )
    planes = [lambda p, z=z_min: float(p[2]) - z]
    if frame_width > 0 and frame_height > 0:
        W = float(frame_width)
        Ht = float(frame_height)
        planes.extend(
            [
                lambda p: float(p[0]),
                lambda p, W=W: float(p[2]) * W - float(p[0]),
                lambda p: float(p[1]),
                lambda p, Ht=Ht: float(p[2]) * Ht - float(p[1]),
            ]
        )
    for plane in planes:
        if seg is None:
            return None
        seg = _clip_segment_halfspace(seg[0], seg[1], plane)
    return seg


def _homog_to_image_xy(p_h: np.ndarray) -> Optional[Tuple[float, float]]:
    w = float(p_h[2])
    if abs(w) < 1e-12:
        return None
    return (float(p_h[0]) / w, float(p_h[1]) / w)


def _clip_roi_image_points_to_frame(
    img_pts: List[Dict[str, float]],
    frame_width: int,
    frame_height: int,
) -> List[Dict[str, float]]:
    """Clip projected ROI polygon to [0,W]×[0,H]; returns [] if nothing visible (fewer than 3 vertices)."""
    if frame_width <= 0 or frame_height <= 0 or len(img_pts) < 3:
        return img_pts
    poly = [(float(p["x"]), float(p["y"])) for p in img_pts]
    clipped = _clip_polygon_to_rect(poly, 0.0, 0.0, float(frame_width), float(frame_height))
    if len(clipped) < 3:
        return []
    return [{"x": float(x), "y": float(y)} for x, y in clipped]


def _clip_wire_dict_to_frame(
    p1: Dict[str, float],
    p2: Dict[str, float],
    frame_width: int,
    frame_height: int,
) -> Optional[Tuple[Dict[str, float], Dict[str, float]]]:
    if frame_width <= 0 or frame_height <= 0:
        return (
            {"x": float(p1["x"]), "y": float(p1["y"])},
            {"x": float(p2["x"]), "y": float(p2["y"])},
        )
    seg = _clip_segment_to_rect(
        (float(p1["x"]), float(p1["y"])),
        (float(p2["x"]), float(p2["y"])),
        0.0,
        0.0,
        float(frame_width),
        float(frame_height),
    )
    if seg is None:
        return None
    (a, b) = seg
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx * dx + dy * dy < 1e-20:
        return None
    return ({"x": float(a[0]), "y": float(a[1])}, {"x": float(b[0]), "y": float(b[1])})


def _sorted_cam_mp4_paths(project_path: Path) -> List[Path]:
    """Return cam_XX.mp4 files under project_path sorted by camera index."""

    def sort_key(p: Path) -> Tuple[int, str]:
        stem = p.stem
        parts = stem.split("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return (int(parts[1]), stem)
        return (10**9, stem)

    return sorted(project_path.glob("cam_*.mp4"), key=sort_key)


def layout_pixels_to_world(px: float, py: float, world_transform: Dict) -> Tuple[float, float]:
    """
    Inverse of roi_verification.transform_world_coords (layout pixels -> world XY on ground plane).

    Forward: pixel = scale * (R @ [wx, wy, 0])[:2] + t[:2]
    """
    scale = float(world_transform.get("scale", 1.0))
    if abs(scale) < 1e-15:
        raise ValueError("transform_world_to_map scale is zero; cannot invert layout->world")
    R = np.array(world_transform.get("rotation", [[1, 0, 0], [0, 1, 0], [0, 0, 1]]), dtype=np.float64)
    t = np.array(world_transform.get("translation", [0.0, 0.0, 0.0]), dtype=np.float64)
    b = np.array([(px - t[0]) / scale, (py - t[1]) / scale], dtype=np.float64)
    A = R[:2, :2]
    det = float(np.linalg.det(A))
    if abs(det) < 1e-14:
        raise ValueError("singular rotation 2x2 block; cannot invert layout->world")
    wx, wy = np.linalg.solve(A, b)
    return float(wx), float(wy)


def _resolve_is_single_camera(project_path: Path, is_single_camera: Optional[bool] = None) -> bool:
    """
    Resolve whether the project is single-camera or multi-camera.
    If is_single_camera is provided (e.g. from main.py via video_files_count == 1), use it.
    Otherwise infer from filesystem: single if single_view_results/cam_00 exists with results,
    else multi if multi_view results dir exists.
    """
    if is_single_camera is not None:
        return is_single_camera
    multi_dir = project_path / "output" / "multi_view_results" / "BA_output" / "results_ba_scaled_world"
    single_dir = project_path / "output" / "single_view_results" / "cam_00"
    if multi_dir.exists():
        return False
    if single_dir.exists() and (single_dir / "camInfo_world.yaml").exists():
        return True
    return False


class CalibrationExporter:
    """Handles export of calibration data to standardized JSON format."""
    
    def __init__(self, project_path: Path, result_type: str = "amc", camera_name_map: Dict[str, str] = None,
                 is_single_camera: Optional[bool] = None):
        """
        Initialize calibration exporter.

        Args:
            project_path: Path to project directory (e.g., amc_data/sample3)
            result_type: Type of results to export - "amc" or "vggt"
            camera_name_map: Maps internal names (cam_00) to display names
            is_single_camera: If True/False, use that; if None, infer from output dirs.
        """
        self.project_path = Path(project_path)
        self.project_name = self.project_path.name
        self.result_type = result_type
        self.camera_name_map = camera_name_map or {}
        self.is_single_camera = _resolve_is_single_camera(self.project_path, is_single_camera)
        
        # Select results path based on single vs multi and result type
        if self.is_single_camera and result_type != "vggt":
            self.results_path = self.project_path / "output" / "single_view_results" / "cam_00"
        elif result_type == "vggt":
            self.results_path = (
                self.project_path / "output" / "vggt_results" / "multi_view_results" / 
                "BA_output" / "results_ba_scaled_world"
            )
        else:
            self.results_path = (
                self.project_path / "output" / "multi_view_results" / 
                "BA_output" / "results_ba_scaled_world"
            )
        
        self.annotations_path = self.project_path / "output" / "alignment_data" / "annotations.json"
    
    def decompose_projection_matrix(self, P: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Decompose projection matrix P (3x4) into K, R, t.
        P = K[R|t]
        
        Uses proper RQ decomposition to ensure K is upper triangular
        and R is orthogonal (rotation matrix).
        
        Args:
            P: 3x4 projection matrix
            
        Returns:
            Tuple of (K, R, t) where:
                K: 3x3 intrinsic matrix (upper triangular)
                R: 3x3 rotation matrix (orthogonal)
                t: 3x1 translation vector
        """
        # Extract M (first 3x3 part) and p4 (last column)
        M = P[:, :3]
        p4 = P[:, 3]
        
        # Try to use scipy.linalg.rq if available (preferred method)
        try:
            from scipy.linalg import rq
            K, R = rq(M)
        except ImportError:
            # Fallback: Manual RQ decomposition
            # Use QR decomposition on reversed/transposed matrix
            # M = K*R => flip(M)^T = flip(R)^T * flip(K)^T
            # Apply QR: flip(R)^T * flip(K)^T = Q * R_qr
            P_reversed = np.fliplr(np.flipud(M))
            Q, R_temp = np.linalg.qr(P_reversed.T)
            R = np.flipud(np.fliplr(Q.T))
            K = np.flipud(np.fliplr(R_temp.T))
        
        # Check if K[2,2] is valid (non-zero)
        if abs(K[2, 2]) < 1e-10:
            raise ValueError(f"Invalid projection matrix: K[2,2] is too small ({K[2, 2]}). Matrix may be degenerate.")
        
        # Normalize K so K[2,2] = 1
        K = K / K[2, 2]
        
        # Ensure positive diagonal elements in K
        T = np.diag(np.sign(np.diag(K)))
        if np.linalg.det(T) < 0:
            T[1, 1] *= -1
        
        K = K @ T
        R = T @ R
        
        # Ensure R is a proper rotation (det(R) = 1)
        det_R = np.linalg.det(R)
        if abs(det_R) < 0.5:
            raise ValueError(f"Invalid rotation matrix: det(R) = {det_R}. Matrix may be degenerate.")
        
        if det_R < 0:
            R = -R
            K = -K
        
        # Compute translation: t = K^(-1) * p4
        K_det = np.linalg.det(K)
        if abs(K_det) < 1e-10:
            raise ValueError(f"Intrinsic matrix K is singular: det(K) = {K_det}")
        
        t = np.linalg.inv(K) @ p4
        
        return K, R, t
    
    def compute_homography(self, K: np.ndarray, R: np.ndarray, t: np.ndarray) -> np.ndarray:
        """
        Compute homography matrix for ground plane (z=0).
        H = K[r1 r2 t]
        
        Args:
            K: 3x3 intrinsic matrix
            R: 3x3 rotation matrix
            t: 3x1 translation vector
            
        Returns:
            H: 3x3 homography matrix
        """
        # Extract first two columns of R
        r1 = R[:, 0]
        r2 = R[:, 1]
        
        # Create [r1 r2 t] matrix (3x3)
        Rt_ground = np.column_stack([r1, r2, t])
        
        # Compute homography: H = K * [r1 r2 t]
        H = K @ Rt_ground
        
        return H
    
    def _project_image_to_world_homography(self, image_points: List[Dict], P: np.ndarray) -> List[Dict]:
        """Same world frame as sv_post_process: inv(P[:, [0,1,3]]) @ image (matches transform_world_to_map)."""
        H_wf2img = P[:, [0, 1, 3]]
        if H_wf2img.shape != (3, 3):
            H_wf2img = np.asarray(H_wf2img).reshape(3, 3)
        H_img2wf = np.linalg.inv(H_wf2img)
        world_points = []
        for img_pt in image_points:
            p_img = np.array([img_pt['x'], img_pt['y'], 1.0], dtype=np.float64)
            p_wf = H_img2wf @ p_img
            if abs(p_wf[2]) < 1e-12:
                world_points.append({'x': 0.0, 'y': 0.0})
                continue
            p_wf /= p_wf[2]
            world_points.append({'x': float(p_wf[0]), 'y': float(p_wf[1])})
        return world_points

    def project_image_to_world(self, image_points: List[Dict], P: np.ndarray, z: float = 0.0) -> List[Dict]:
        """
        Single-camera: homography unproject (P[:, [0,1,3]]) so export matches transform_world_to_map.
        Multi-camera: K[R|t] ray intersection with z=0.
        """
        if self.is_single_camera and P is not None:
            P_arr = np.asarray(P)
            if P_arr.ndim == 1 and P_arr.size >= 12:
                P_arr = P_arr.reshape(3, 4)
            if P_arr.ndim == 2 and P_arr.shape[0] >= 3 and P_arr.shape[1] >= 4:
                return self._project_image_to_world_homography(image_points, P_arr[:3, :4])

        world_points = []
        K, R, t = self.decompose_projection_matrix(P)
        for img_pt in image_points:
            img_h = np.array([img_pt['x'], img_pt['y'], 1.0])
            ray_dir = np.linalg.inv(K) @ img_h
            ray_world = R.T @ ray_dir
            cam_center = -R.T @ t
            if abs(ray_world[2]) > 1e-6:
                lambda_val = (z - cam_center[2]) / ray_world[2]
                world_pt = cam_center + lambda_val * ray_world
                world_points.append({'x': float(world_pt[0]), 'y': float(world_pt[1])})
            else:
                world_points.append({'x': 0.0, 'y': 0.0})
        return world_points

    def project_world_ground_to_image(
        self,
        P: np.ndarray,
        wx: float,
        wy: float,
        wz: float = 0.0,
        frame_width: int = 0,
        frame_height: int = 0,
    ) -> Optional[Tuple[float, float]]:
        """
        World ground point -> image pixels (unbounded; not clipped to the image rectangle).
        Single-camera: homography P[:, [0,1,3]] @ [wx,wy,1] with positive-w chirality (points
        behind the camera / across the vanishing line return None). Multi-camera: K(RX+t) with
        camera depth > 0 (points behind the camera return None).

        Global ROIs that cross the horizon use project_global_rois_to_image_space instead,
        which clips in camera / homogeneous space before projecting.
        """
        P_arr = np.asarray(P, dtype=np.float64)
        if P_arr.ndim == 1 and P_arr.size >= 12:
            P_arr = P_arr.reshape(3, 4)
        if P_arr.ndim != 2 or P_arr.shape[0] < 3 or P_arr.shape[1] < 4:
            return None

        if self.is_single_camera:
            H = _single_cam_ground_H(P_arr, frame_width, frame_height)
            p_img = H @ np.array([wx, wy, 1.0], dtype=np.float64)
            # Reject behind-camera / near-vanishing-line samples (wrong-side or ~0 w).
            if float(p_img[2]) <= 1e-6:
                return None
            return (float(p_img[0] / p_img[2]), float(p_img[1] / p_img[2]))

        K, R, t = self.decompose_projection_matrix(P_arr)
        tv = np.asarray(t, dtype=np.float64).reshape(3)
        X = np.array([wx, wy, wz], dtype=np.float64)
        x_cam = R @ X + tv
        if float(x_cam[2]) <= 1e-6:
            return None
        p = K @ x_cam
        if abs(float(p[2])) < 1e-12:
            return None
        return (float(p[0] / p[2]), float(p[1] / p[2]))

    def project_global_rois_to_image_space(
        self,
        global_rois: List[Dict],
        P: np.ndarray,
        frame_width: int = 0,
        frame_height: int = 0,
    ) -> List[Dict]:
        """
        Root-level world ROIs -> image pixels.

        Multi-camera: the polygon is clipped in **camera space** to Z >= z_near (in front of
        the camera) before perspective projection, so edges that cross the horizon get boundary
        vertices on the near plane instead of bogus (u,v) from negative depth. Result is then
        clipped to [0,W]×[0,H] when frame dimensions are set.

        Single-camera: SV world P is a floor homography (not full K[R|t]). Clip the polygon in
        homogeneous image space (H @ [wx,wy,1]) to w >= z_near and the image frustum so edges
        that cross the vanishing line do not wrap through infinity.
        """
        projected: List[Dict] = []
        P_arr = np.asarray(P, dtype=np.float64)
        if P_arr.ndim == 1 and P_arr.size >= 12:
            P_arr = P_arr.reshape(3, 4)

        if self.is_single_camera:
            H = _single_cam_ground_H(P_arr, frame_width, frame_height)
            z_near = 1e-3
            for roi in global_rois:
                coords = roi.get("roiCoordinates") or []
                if len(coords) < 3:
                    continue
                hom_pts: List[np.ndarray] = []
                for pt in coords:
                    # Ground ROIs; ignore z (SV H maps the floor plane only).
                    hom_pts.append(
                        H
                        @ np.array(
                            [float(pt["x"]), float(pt["y"]), 1.0], dtype=np.float64
                        )
                    )
                clipped_h = _clip_homog_polygon_to_image_frustum(
                    hom_pts, frame_width, frame_height, z_near
                )
                if len(clipped_h) < 3:
                    continue
                img_pts: List[Dict[str, float]] = []
                for ph in clipped_h:
                    xy = _homog_to_image_xy(ph)
                    if xy is not None:
                        img_pts.append({"x": xy[0], "y": xy[1]})
                if len(img_pts) < 3:
                    continue
                # Frustum clip already restricts to the frame when dims are set; still run
                # 2D clip for numerical cleanup / when dims were 0 during H-space clip.
                clipped = _clip_roi_image_points_to_frame(img_pts, frame_width, frame_height)
                if len(clipped) >= 3:
                    projected.append(
                        {"id": str(roi.get("id", "")), "roiCoordinates": clipped}
                    )
            return projected

        # Multi-camera: clip ground polygon in camera space, then project (correct horizon crossing)
        K, R, t = self.decompose_projection_matrix(P_arr)
        tv = np.asarray(t, dtype=np.float64).reshape(3)
        z_near = 1e-3  # meters; must be > 0 so projection is finite

        for roi in global_rois:
            coords = roi.get("roiCoordinates") or []
            cam_pts: List[np.ndarray] = []
            for pt in coords:
                wz = float(pt.get("z", 0.0))
                X = np.array([float(pt["x"]), float(pt["y"]), wz], dtype=np.float64)
                x_cam = R @ X + tv
                cam_pts.append(np.asarray(x_cam, dtype=np.float64))
            if len(cam_pts) < 3:
                continue
            clipped_3d = _clip_polygon_camera_space_z_ge(cam_pts, z_near)
            if len(clipped_3d) < 3:
                continue
            img_pts: List[Dict[str, float]] = []
            for xc in clipped_3d:
                p = K @ xc
                pz = float(p[2])
                if abs(pz) < 1e-14:
                    continue
                img_pts.append({"x": float(p[0] / pz), "y": float(p[1] / pz)})
            if len(img_pts) < 3:
                continue
            clipped = _clip_roi_image_points_to_frame(img_pts, frame_width, frame_height)
            if len(clipped) >= 3:
                projected.append(
                    {"id": str(roi.get("id", "")), "roiCoordinates": clipped}
                )
        return projected

    def project_global_tripwires_to_image_space(
        self,
        global_tripwires: List[Dict],
        P: np.ndarray,
        frame_width: int = 0,
        frame_height: int = 0,
    ) -> List[Dict]:
        """
        Root-level world tripwires -> image space; skip wire if an endpoint is not visible.
        Direction segment included when both endpoints project. When frame dimensions
        are > 0, wire and direction segments are clipped to [0,W]×[0,H] (clipped only).

        Single-camera: clip each segment in homogeneous H-space (w / image frustum) so wires
        that cross the vanishing line stay finite, matching ROI horizon handling.
        """
        if self.is_single_camera:
            return self._project_global_tripwires_to_image_space_single(
                global_tripwires, P, frame_width, frame_height
            )

        projected: List[Dict] = []
        for tw in global_tripwires:
            wire = tw.get("wire") or {}
            direction = tw.get("direction") or {}
            p1w, p2w = wire.get("p1"), wire.get("p2")
            if not p1w or not p2w:
                continue
            w1 = self.project_world_ground_to_image(
                P, float(p1w["x"]), float(p1w["y"]), 0.0
            )
            w2 = self.project_world_ground_to_image(
                P, float(p2w["x"]), float(p2w["y"]), 0.0
            )
            if w1 is None or w2 is None:
                continue
            wire_clip = _clip_wire_dict_to_frame(
                {"x": w1[0], "y": w1[1]},
                {"x": w2[0], "y": w2[1]},
                frame_width,
                frame_height,
            )
            if wire_clip is None:
                continue
            wp1, wp2 = wire_clip
            obj: Dict = {
                "id": str(tw.get("id", "")),
                "wire": {"p1": wp1, "p2": wp2},
            }
            d1w, d2w = direction.get("p1"), direction.get("p2")
            if d1w and d2w:
                d1 = self.project_world_ground_to_image(
                    P, float(d1w["x"]), float(d1w["y"]), 0.0
                )
                d2 = self.project_world_ground_to_image(
                    P, float(d2w["x"]), float(d2w["y"]), 0.0
                )
                if d1 is not None and d2 is not None:
                    dir_clip = _clip_wire_dict_to_frame(
                        {"x": d1[0], "y": d1[1]},
                        {"x": d2[0], "y": d2[1]},
                        frame_width,
                        frame_height,
                    )
                    if dir_clip is not None:
                        dp1, dp2 = dir_clip
                        obj["direction"] = {"p1": dp1, "p2": dp2}
            projected.append(obj)
        return projected

    def _project_global_tripwires_to_image_space_single(
        self,
        global_tripwires: List[Dict],
        P: np.ndarray,
        frame_width: int = 0,
        frame_height: int = 0,
    ) -> List[Dict]:
        """Single-cam tripwires: clip H @ endpoints in homogeneous frustum, then image-clip."""
        P_arr = np.asarray(P, dtype=np.float64)
        if P_arr.ndim == 1 and P_arr.size >= 12:
            P_arr = P_arr.reshape(3, 4)
        H = _single_cam_ground_H(P_arr, frame_width, frame_height)
        z_near = 1e-3
        projected: List[Dict] = []

        def _seg_to_image(
            p1w: Dict, p2w: Dict
        ) -> Optional[Tuple[Dict[str, float], Dict[str, float]]]:
            a_h = H @ np.array(
                [float(p1w["x"]), float(p1w["y"]), 1.0], dtype=np.float64
            )
            b_h = H @ np.array(
                [float(p2w["x"]), float(p2w["y"]), 1.0], dtype=np.float64
            )
            seg = _clip_homog_segment_to_image_frustum(
                a_h, b_h, frame_width, frame_height, z_near
            )
            if seg is None:
                return None
            xy1 = _homog_to_image_xy(seg[0])
            xy2 = _homog_to_image_xy(seg[1])
            if xy1 is None or xy2 is None:
                return None
            return _clip_wire_dict_to_frame(
                {"x": xy1[0], "y": xy1[1]},
                {"x": xy2[0], "y": xy2[1]},
                frame_width,
                frame_height,
            )

        for tw in global_tripwires:
            wire = tw.get("wire") or {}
            direction = tw.get("direction") or {}
            p1w, p2w = wire.get("p1"), wire.get("p2")
            if not p1w or not p2w:
                continue
            wire_clip = _seg_to_image(p1w, p2w)
            if wire_clip is None:
                continue
            wp1, wp2 = wire_clip
            obj: Dict = {
                "id": str(tw.get("id", "")),
                "wire": {"p1": wp1, "p2": wp2},
            }
            d1w, d2w = direction.get("p1"), direction.get("p2")
            if d1w and d2w:
                dir_clip = _seg_to_image(d1w, d2w)
                if dir_clip is not None:
                    dp1, dp2 = dir_clip
                    obj["direction"] = {"p1": dp1, "p2": dp2}
            projected.append(obj)
        return projected

    def convert_roi_to_world(self, roi: Dict, P: np.ndarray, index: int) -> Dict:
        """
        Convert ROI from image to world coordinates.
        
        Args:
            roi: ROI dict with 'points' in image space
            P: 3x4 projection matrix
            index: Sequential index for the ROI (1-based)
            
        Returns:
            ROI dict with roiCoordinates in world space
        """
        # Handle both 'points' (from annotations.json) and 'roiCoordinates' (legacy)
        image_coords = roi.get('points', roi.get('roiCoordinates', []))
        world_coords = self.project_image_to_world(image_coords, P)
        
        return {
            'id': f'roi-id-{index}',
            'roiCoordinates': world_coords,
            'restrictedObjectTypes': roi.get('restrictedObjectTypes', []),
            'confinedObjectTypes': roi.get('confinedObjectTypes', [])
        }
    
    def convert_tripwire_to_world(self, tripwire: Dict, P: np.ndarray) -> Dict:
        """
        Convert tripwire from image to world coordinates.
        
        Args:
            tripwire: Tripwire dict with wire and direction in image space
            P: 3x4 projection matrix
            
        Returns:
            Tripwire dict with wire and direction in world space
        """
        # Convert wire points
        wire_img = [tripwire['wire']['p1'], tripwire['wire']['p2']]
        wire_world = self.project_image_to_world(wire_img, P)
        
        # Convert direction points
        dir_img = [tripwire['direction']['p1'], tripwire['direction']['p2']]
        dir_world = self.project_image_to_world(dir_img, P)
        
        return {
            'id': tripwire['id'],
            'wire': {
                'p1': wire_world[0],
                'p2': wire_world[1]
            },
            'direction': {
                'p1': dir_world[0],
                'p2': dir_world[1]
            }
        }
    
    def pair_and_convert_tripwires(self, tripwire_lines: List[Dict], 
                                   tripwire_directions: List[Dict], 
                                   P: np.ndarray) -> List[Dict]:
        """
        Pair tripwire lines with directions and convert to world coordinates.
        Pairs by index: first line with first direction, second line with second direction, etc.
        
        Args:
            tripwire_lines: List of tripwire line dicts with 'start'/'end' OR 'points' array
            tripwire_directions: List of tripwire direction dicts with 'start'/'end' OR 'points' array
            P: 3x4 projection matrix
            
        Returns:
            List of paired tripwires with wire and direction in world coordinates
        """
        paired_tripwires = []
        
        # Iterate through tripwire lines (using 1-based sequential indexing)
        for idx, wire in enumerate(tripwire_lines):
            # Use sequential index (1-based)
            wire_id = str(idx + 1)
            
            # Handle both formats: 'points' array or 'start'/'end' objects
            if 'points' in wire:
                wire_points_img = wire['points']
            elif 'start' in wire and 'end' in wire:
                wire_points_img = [wire['start'], wire['end']]
            else:
                continue  # Skip invalid format
            
            if len(wire_points_img) < 2:
                continue  # Skip invalid wires
            
            # Transform wire points to world coordinates
            wire_points_world = self.project_image_to_world(wire_points_img, P)
            
            # Build tripwire object
            tripwire_obj = {
                "id": f"tripwire-id-{wire_id}",
                "wire": {
                    "p1": {
                        "x": wire_points_world[0]['x'],
                        "y": wire_points_world[0]['y']
                    },
                    "p2": {
                        "x": wire_points_world[1]['x'],
                        "y": wire_points_world[1]['y']
                    }
                }
            }
            
            # Check if there's a matching direction at the same index
            if idx < len(tripwire_directions):
                direction = tripwire_directions[idx]
                
                # Handle both formats for direction too
                if 'points' in direction:
                    dir_points_img = direction['points']
                elif 'start' in direction and 'end' in direction:
                    dir_points_img = [direction['start'], direction['end']]
                else:
                    dir_points_img = []
                
                if len(dir_points_img) >= 2:
                    dir_points_world = self.project_image_to_world(dir_points_img, P)
                    tripwire_obj["direction"] = {
                        "p1": {
                            "x": dir_points_world[0]['x'],
                            "y": dir_points_world[0]['y']
                        },
                        "p2": {
                            "x": dir_points_world[1]['x'],
                            "y": dir_points_world[1]['y']
                        }
                    }
            
            paired_tripwires.append(tripwire_obj)
        
        return paired_tripwires

    @staticmethod
    def _point_xy_dict(p: Dict) -> Dict[str, float]:
        return {"x": float(p["x"]), "y": float(p["y"])}

    @staticmethod
    def _assign_sequential_roi_ids_sensor_level(sensors: List[Dict]) -> None:
        """Force each camera's rois to id roi-id-1, roi-id-2, ... (overwrites UI/timestamp ids)."""
        for sensor in sensors:
            if sensor.get("type") != "camera":
                continue
            rois = sensor.get("rois")
            if not isinstance(rois, list):
                continue
            seq = 0
            for roi in rois:
                if not isinstance(roi, dict):
                    continue
                seq += 1
                roi["id"] = f"roi-id-{seq}"

    @staticmethod
    def _assign_sequential_roi_ids_root(root_rois: List[Dict]) -> None:
        """Force top-level rois[] to roi-id-1, roi-id-2, ..."""
        for idx, roi in enumerate(root_rois, start=1):
            if isinstance(roi, dict):
                roi["id"] = f"roi-id-{idx}"

    def pair_tripwires_image_pixels(
        self, tripwire_lines: List[Dict], tripwire_directions: List[Dict]
    ) -> List[Dict]:
        """Pair tripwires like pair_and_convert_tripwires but keep wire/direction in image pixels."""
        paired: List[Dict] = []
        for idx, wire in enumerate(tripwire_lines):
            wire_id = str(idx + 1)
            if "points" in wire:
                wire_points_img = wire["points"]
            elif "start" in wire and "end" in wire:
                wire_points_img = [wire["start"], wire["end"]]
            else:
                continue
            if len(wire_points_img) < 2:
                continue
            tripwire_obj: Dict = {
                "id": f"tripwire-id-{wire_id}",
                "wire": {
                    "p1": self._point_xy_dict(wire_points_img[0]),
                    "p2": self._point_xy_dict(wire_points_img[1]),
                },
            }
            if idx < len(tripwire_directions):
                direction = tripwire_directions[idx]
                if "points" in direction:
                    dir_points_img = direction["points"]
                elif "start" in direction and "end" in direction:
                    dir_points_img = [direction["start"], direction["end"]]
                else:
                    dir_points_img = []
                if len(dir_points_img) >= 2:
                    tripwire_obj["direction"] = {
                        "p1": self._point_xy_dict(dir_points_img[0]),
                        "p2": self._point_xy_dict(dir_points_img[1]),
                    }
            paired.append(tripwire_obj)
        return paired

    def convert_roi_to_image_pixels(self, roi: Dict, index: int) -> Dict:
        """ROI export shape with roiCoordinates in image pixel space (x, y, z=0)."""
        image_coords = roi.get("points", roi.get("roiCoordinates", []))
        roi_coordinates = [
            {"x": float(p["x"]), "y": float(p["y"]), "z": 0.0} for p in image_coords
        ]
        return {
            "id": f"roi-id-{index}",
            "roiCoordinates": roi_coordinates,
            "restrictedObjectTypes": roi.get("restrictedObjectTypes", []),
            "confinedObjectTypes": roi.get("confinedObjectTypes", []),
        }

    def build_global_root_rois_pixels(self, global_ann: Dict) -> List[Dict]:
        """Root-level rois: layout pixel coordinates with z=0; ids roi-id-1, roi-id-2, ..."""
        root: List[Dict] = []
        seq = 0
        for roi in global_ann.get("rois", []):
            if roi.get("id") == "drawing":
                continue
            image_coords = roi.get("points", roi.get("roiCoordinates", []))
            if len(image_coords) < 3:
                continue
            seq += 1
            roi_coordinates = [
                {"x": float(p["x"]), "y": float(p["y"]), "z": 0.0} for p in image_coords
            ]
            root.append(
                {
                    "id": f"roi-id-{seq}",
                    "roiCoordinates": roi_coordinates,
                    "sensors": [],
                }
            )
        return root

    def pair_global_tripwires_layout_pixels(
        self, tripwire_lines: List[Dict], tripwire_directions: List[Dict]
    ) -> List[Dict]:
        """Global tripwires on layout image; coordinates stay in pixel space."""
        paired: List[Dict] = []
        for idx, wire in enumerate(tripwire_lines):
            wire_id = str(idx + 1)
            if "points" in wire:
                wire_points_img = wire["points"]
            elif "start" in wire and "end" in wire:
                wire_points_img = [wire["start"], wire["end"]]
            else:
                continue
            if len(wire_points_img) < 2:
                continue
            tripwire_obj: Dict = {
                "id": f"tripwire-id-{wire_id}",
                "wire": {
                    "p1": self._point_xy_dict(wire_points_img[0]),
                    "p2": self._point_xy_dict(wire_points_img[1]),
                },
            }
            if idx < len(tripwire_directions):
                direction = tripwire_directions[idx]
                if "points" in direction:
                    dir_points_img = direction["points"]
                elif "start" in direction and "end" in direction:
                    dir_points_img = [direction["start"], direction["end"]]
                else:
                    dir_points_img = []
                if len(dir_points_img) >= 2:
                    tripwire_obj["direction"] = {
                        "p1": self._point_xy_dict(dir_points_img[0]),
                        "p2": self._point_xy_dict(dir_points_img[1]),
                    }
            paired.append(tripwire_obj)
        return paired

    def layout_image_points_to_world(
        self, image_points: List[Dict], world_transform: Dict
    ) -> List[Dict]:
        """Map layout pixel {x,y} points to world {x,y} (same frame as per-sensor export)."""
        out: List[Dict] = []
        for img_pt in image_points:
            wx, wy = layout_pixels_to_world(
                float(img_pt["x"]), float(img_pt["y"]), world_transform
            )
            out.append({"x": wx, "y": wy})
        return out

    def pair_global_tripwires_layout_to_world(
        self,
        tripwire_lines: List[Dict],
        tripwire_directions: List[Dict],
        world_transform: Dict,
    ) -> List[Dict]:
        """Pair global tripwires drawn on layout; convert to world (root-level export shape)."""
        paired: List[Dict] = []
        for idx, wire in enumerate(tripwire_lines):
            wire_id = str(idx + 1)
            if "points" in wire:
                wire_points_img = wire["points"]
            elif "start" in wire and "end" in wire:
                wire_points_img = [wire["start"], wire["end"]]
            else:
                continue
            if len(wire_points_img) < 2:
                continue
            wire_world = self.layout_image_points_to_world(wire_points_img, world_transform)
            tripwire_obj: Dict = {
                "id": f"tripwire-id-{wire_id}",
                "wire": {
                    "p1": {"x": wire_world[0]["x"], "y": wire_world[0]["y"]},
                    "p2": {"x": wire_world[1]["x"], "y": wire_world[1]["y"]},
                },
            }
            if idx < len(tripwire_directions):
                direction = tripwire_directions[idx]
                if "points" in direction:
                    dir_points_img = direction["points"]
                elif "start" in direction and "end" in direction:
                    dir_points_img = [direction["start"], direction["end"]]
                else:
                    dir_points_img = []
                if len(dir_points_img) >= 2:
                    dir_world = self.layout_image_points_to_world(
                        dir_points_img, world_transform
                    )
                    tripwire_obj["direction"] = {
                        "p1": {"x": dir_world[0]["x"], "y": dir_world[0]["y"]},
                        "p2": {"x": dir_world[1]["x"], "y": dir_world[1]["y"]},
                    }
            paired.append(tripwire_obj)
        return paired

    def build_global_root_rois(
        self, global_ann: Dict, world_transform: Dict
    ) -> List[Dict]:
        """
        Root-level rois from global layout annotations: id, roiCoordinates (x,y,z), sensors [].
        """
        root: List[Dict] = []
        seq = 0
        for roi in global_ann.get("rois", []):
            if roi.get("id") == "drawing":
                continue
            image_coords = roi.get("points", roi.get("roiCoordinates", []))
            if len(image_coords) < 3:
                continue
            seq += 1
            world_xy = self.layout_image_points_to_world(image_coords, world_transform)
            roi_coordinates = [
                {"x": p["x"], "y": p["y"], "z": 0.0} for p in world_xy
            ]
            root.append(
                {
                    "id": f"roi-id-{seq}",
                    "roiCoordinates": roi_coordinates,
                    "sensors": [],
                }
            )
        return root

    def load_projection_matrix(self, yaml_path: Path) -> np.ndarray:
        """Load projection matrix from camInfo YAML file. Handles both nested (multi-view) and flat (single-view) format."""
        with open(yaml_path, 'r') as f:
            data = yaml.safe_load(f)
        
        P = np.array(data['projectionMatrix_3x4'])
        if P.ndim == 1:
            P = P.reshape(3, 4)
        return P
    
    def load_annotations(self) -> Dict:
        """Load ROIs and tripwires from annotations.json. alignment_data first, then project root."""
        for path in (self.annotations_path, self.project_path / "annotations.json"):
            if path.exists():
                with open(path, 'r') as f:
                    return json.load(f)
        return {}

    def load_annotations_project_root_only(self) -> Dict:
        """Load annotations from ``<project_dir>/annotations.json`` only (image-mode export)."""
        path = self.project_path / "annotations.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def load_transform_data(self) -> Optional[Dict]:
        """Load world transform from transform_world_to_map.json (same rules as roi_verification.load_world_transform)."""
        transform_path = self.results_path / "transform_world_to_map.json"
        if not transform_path.exists():
            return None
        with open(transform_path, 'r') as f:
            data = json.load(f)
        return {
            "scale": data.get("scale", 1.0),
            "rotation": data.get("R", [[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
            "translation": data.get("t", [0, 0, 0]),
        }
    
    def get_video_metadata(self, video_path: Path) -> Dict:
        """
        Extract video metadata (fps, resolution).
        Falls back to defaults if cv2 not available.
        """
        try:
            import cv2
            cap = open_video_capture(str(video_path))
            metadata = {
                "fps": str(int(cap.get(cv2.CAP_PROP_FPS))),
                "frameWidth": str(int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))),
                "frameHeight": str(int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            }
            cap.release()
            return metadata
        except ImportError:
            # Default values if cv2 not available
            return {
                "fps": "30",
                "frameWidth": "1920",
                "frameHeight": "1080"
            }
        except Exception:
            return {
                "fps": "30",
                "frameWidth": "1920",
                "frameHeight": "1080"
            }
    
    def create_sensor_entry(self, cam_id: str, P: np.ndarray, 
                           annotations_data: Dict, video_metadata: Dict,
                           scale_factor: float = -1.0) -> Dict:
        """Create a single sensor entry in the calibration JSON."""
        # Decompose projection matrix
        K, R, t = self.decompose_projection_matrix(P)
        
        # Create extrinsic matrix [R|t]
        extrinsic = np.hstack([R, t.reshape(3, 1)])
        
        # Compute homography for ground plane
        H = self.compute_homography(K, R, t)

        # Find inverse of homography
        H_inv = np.linalg.inv(H)
        
        # Get annotations for this camera (internal id key, no video extension)
        cam_annotations = cam_annotations_for_sensor(annotations_data, cam_id)
        
        # Debug logging
        # print(f"DEBUG: Processing {video_name}")
        # print(f"  - Found {len(cam_annotations.get('rois', []))} ROIs")
        # print(f"  - Found {len(cam_annotations.get('tripwireLines', []))} tripwire lines")
        # print(f"  - Found {len(cam_annotations.get('tripwireDirections', []))} tripwire directions")
        
        # Convert ROIs to world coordinates (using 1-based sequential indexing; skip in-progress polygon)
        rois_img = [
            r for r in cam_annotations.get("rois", []) if r.get("id") != "drawing"
        ]
        rois_world = [self.convert_roi_to_world(roi, P, idx + 1) for idx, roi in enumerate(rois_img)]
        
        # Pair tripwire lines with directions and convert to world coordinates
        tripwire_lines = cam_annotations.get("tripwireLines", [])
        tripwire_directions = cam_annotations.get("tripwireDirections", [])
        tripwires_world = self.pair_and_convert_tripwires(tripwire_lines, tripwire_directions, P)
        
        # print(f"  - Exported {len(rois_world)} ROIs")
        # print(f"  - Exported {len(tripwires_world)} paired tripwires")
        
        # Use display name if available, otherwise use internal cam_id
        display_name = self.camera_name_map.get(cam_id, cam_id)
        export_sensor_id = sensor_id_for_export(display_name)

        # Get layout image height
        layout_image_path = self.project_path / "manual_adjustment" / "layout.png"
        layout_image_height = None
        if layout_image_path.exists():
            try:
                import cv2
                layout_img = cv2.imread(str(layout_image_path))
                if layout_img is not None:
                    layout_image_height = layout_img.shape[0]
                    print(f"Layout image height: {layout_image_height}")
            except Exception as e:
                print(f"Warning: Could not read layout image dimensions: {e}")
            
        sensor = {
            "type": "camera",
            "id": export_sensor_id,
            "scaleFactor": scale_factor,
            "origin": {
                "lat": 0.0,
                "lng": 0.0
            },
            "geoLocation": {
                "lat": 0.0,
                "lng": 0.0
            },
            "coordinates": {
                "x": 0.0,
                "y": 0.0
            },
            "translationToGlobalCoordinates": {
                "x": 0.0,
                "y": layout_image_height / scale_factor if layout_image_height else 0.0
            },
            "cameraMatrix": P.tolist(),
            "intrinsicMatrix": K.tolist(),
            "extrinsicMatrix": extrinsic.tolist(),
            "homography": H_inv.tolist(),
            "attributes": [
                {"name": "fps", "value": video_metadata["fps"]},
                {"name": "depth", "value": ""},
                {"name": "fieldOfView", "value": ""},
                {"name": "direction", "value": ""},
                {"name": "direction3d", "value": ""},
                {"name": "source", "value": "vst"},
                {"name": "frameWidth", "value": video_metadata["frameWidth"]},
                {"name": "frameHeight", "value": video_metadata["frameHeight"]}
            ],
            "place": [],
            "imageCoordinates": [],
            "globalCoordinates": [],
            "tripwires": tripwires_world,
            "rois": rois_world
        }
        
        return sensor

    def create_image_mode_sensor_entry(self, cam_id: str, annotations_data: Dict) -> Dict:
        """
        Emit the required sensor fields for image-mode calibration while keeping
        calibration-specific matrices out of the pixel-coordinate export.
        """
        cam_annotations = cam_annotations_for_sensor(annotations_data, cam_id)
        rois_img = cam_annotations.get("rois", [])
        rois_pixel = [
            self.convert_roi_to_image_pixels(roi, idx + 1)
            for idx, roi in enumerate(rois_img)
            if roi.get("id") != "drawing"
        ]
        tripwire_lines = cam_annotations.get("tripwireLines", [])
        tripwire_directions = cam_annotations.get("tripwireDirections", [])
        tripwires_pixel = self.pair_tripwires_image_pixels(tripwire_lines, tripwire_directions)

        display_name = self.camera_name_map.get(cam_id, cam_id)
        export_sensor_id = sensor_id_for_export(display_name)

        return {
            "type": "camera",
            "id": export_sensor_id,
            # The shared calibration schema requires numeric values for these
            # fields even when the image-mode export has no world calibration.
            "scaleFactor": 0.0,
            "origin": {"lat": 0.0, "lng": 0.0},
            "geoLocation": {"lat": 0.0, "lng": 0.0},
            "coordinates": {"x": 0.0, "y": 0.0},
            "attributes": [],
            "place": [],
            "imageCoordinates": [],
            "globalCoordinates": [],
            "tripwires": tripwires_pixel,
            "rois": rois_pixel,
        }

    def export_image_calibration_data(self, project_id: str) -> Tuple[bool, str, Optional[Path]]:
        """
        Export image-mode JSON with camera-image and global-layout pixel
        annotations. ``scaleFactor``, ``origin``, ``geoLocation``, and
        ``coordinates`` use zero placeholders; Cartesian matrices and
        ``translationToGlobalCoordinates`` are omitted. Other unused schema
        fields remain empty.
        """
        try:
            cam_files = _sorted_cam_mp4_paths(self.project_path)
            if not cam_files:
                return False, "No cam_*.mp4 videos found in project directory", None

            annotations_data = self.load_annotations_project_root_only()
            camera_ids = [video_path.stem for video_path in cam_files]
            tripwire_errors = collect_incomplete_tripwire_errors(annotations_data, camera_ids)
            if tripwire_errors:
                raise IncompleteTripwireError(format_incomplete_tripwire_message(tripwire_errors))
            sensors: List[Dict] = []
            for video_path in cam_files:
                cam_id = video_path.stem
                sensor = self.create_image_mode_sensor_entry(cam_id, annotations_data)
                sensors.append(sensor)

            global_ann = annotations_data.get(GLOBAL_ROIS_TRIPWIRES_KEY) or {}
            root_rois = self.build_global_root_rois_pixels(global_ann)
            root_tripwires = self.pair_global_tripwires_layout_pixels(
                global_ann.get("tripwireLines", []),
                global_ann.get("tripwireDirections", []),
            )

            self._assign_sequential_roi_ids_sensor_level(sensors)
            self._assign_sequential_roi_ids_root(root_rois)

            calibration_export = {
                "version": "1.0",
                "osmURL": "",
                "calibrationType": "image",
                "sensors": sensors,
                "rois": root_rois,
                "tripwires": root_tripwires,
            }

            output_path = self.project_path / f"{project_id}_image_mode_exported.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(calibration_export, f, indent=2)

            msg = (
                f"Successfully exported image-mode calibration for {len(sensors)} camera(s)"
                f"; global: {len(root_rois)} ROI(s), {len(root_tripwires)} tripwire(s)"
            )
            return True, msg, output_path

        except IncompleteTripwireError as e:
            return False, str(e), None
        except Exception as e:
            import traceback

            return False, f"Image export failed: {str(e)}\n\n{traceback.format_exc()}", None

    def export_calibration_data(self, output_stem: str = "exported") -> Tuple[bool, str, Optional[Path]]:
        """
        Export calibration data to JSON file.

        Args:
            output_stem: Filename part before optional _vggt suffix, e.g. "exported" or "projected".

        Returns:
            Tuple of (success, message, output_path)
        """
        try:
            # Check if results directory exists
            if not self.results_path.exists():
                return False, f"Results directory not found: {self.results_path}", None
            
            # Load world transform to get scale factor
            world_transform = self.load_transform_data()
            scale_factor = world_transform["scale"] if world_transform else -1.0
            
            # Load annotations
            annotations_data = self.load_annotations()

            # Resolve camInfo file(s): single-cam uses camInfo_world.yaml, multi uses camInfo_*.yaml
            if self.is_single_camera:
                single_cam_file = self.results_path / "camInfo_world.yaml"
                if not single_cam_file.exists():
                    return False, "No camInfo files found in results directory", None
                caminfo_files = [single_cam_file] if single_cam_file.exists() else []
            else:
                caminfo_files = sorted(self.results_path.glob("camInfo_*.yaml"))
            
            if not caminfo_files:
                return False, "No camInfo files found in results directory", None

            camera_ids = []
            for caminfo_file in caminfo_files:
                if self.is_single_camera:
                    camera_ids.append("cam_00")
                else:
                    cam_num = caminfo_file.stem.split("_")[1]
                    camera_ids.append(f"cam_{cam_num}")
            tripwire_errors = collect_incomplete_tripwire_errors(annotations_data, camera_ids)
            if tripwire_errors:
                raise IncompleteTripwireError(format_incomplete_tripwire_message(tripwire_errors))
            
            # Create sensors array (keep P per sensor for global layout back-projection)
            sensors = []
            sensor_matrices: List[np.ndarray] = []
            sensor_frame_sizes: List[Tuple[int, int]] = []
            failed_cameras = []
            
            for caminfo_file in caminfo_files:
                try:
                    if self.is_single_camera:
                        cam_id = "cam_00"
                    else:
                        cam_num = caminfo_file.stem.split('_')[1]
                        cam_id = f"cam_{cam_num}"
                    
                    # Load projection matrix
                    P = self.load_projection_matrix(caminfo_file)
                    
                    # Get video metadata
                    video_path = self.project_path / f"{cam_id}.mp4"
                    video_metadata = self.get_video_metadata(video_path)
                    
                    # Create sensor entry with scale factor
                    sensor = self.create_sensor_entry(
                        cam_id, P, annotations_data, video_metadata, scale_factor
                    )
                    sensors.append(sensor)
                    sensor_matrices.append(P)
                    sensor_frame_sizes.append(
                        frame_dimensions_from_video_metadata(video_metadata)
                    )

                except ValueError as ve:
                    # Matrix decomposition failed for this camera
                    failed_cameras.append(f"{cam_id}: {str(ve)}")
                except Exception as e:
                    # Other error for this camera
                    failed_cameras.append(f"{cam_id}: {str(e)}")
            
            if not sensors:
                error_msg = "Failed to export any cameras. Errors:\n" + "\n".join(failed_cameras)
                return False, error_msg, None

            # Root-level global ROIs / tripwires (layout pixels -> world via transform_world_to_map)
            global_ann = annotations_data.get(GLOBAL_ROIS_TRIPWIRES_KEY) or {}
            has_global_drawn = bool(
                global_ann.get("rois") or global_ann.get("tripwireLines")
            )
            root_rois: List[Dict] = []
            root_tripwires: List[Dict] = []
            global_warnings: List[str] = []

            if has_global_drawn:
                if not world_transform:
                    global_warnings.append(
                        "Global ROIs/tripwires were skipped: transform_world_to_map.json not found."
                    )
                else:
                    try:
                        root_rois = self.build_global_root_rois(
                            global_ann, world_transform
                        )
                        root_tripwires = self.pair_global_tripwires_layout_to_world(
                            global_ann.get("tripwireLines", []),
                            global_ann.get("tripwireDirections", []),
                            world_transform,
                        )
                    except Exception as ge:
                        global_warnings.append(
                            f"Global ROIs/tripwires export failed: {ge}"
                        )

            self._assign_sequential_roi_ids_sensor_level(sensors)
            self._assign_sequential_roi_ids_root(root_rois)

            # Per-sensor image-space projection of global ROIs/tripwires (clipped to video frame)
            for sensor, P, (fw, fh) in zip(sensors, sensor_matrices, sensor_frame_sizes):
                sensor["projectedGlobalRois"] = self.project_global_rois_to_image_space(
                    root_rois, P, fw, fh
                )
                sensor["projectedGlobalTripwires"] = (
                    self.project_global_tripwires_to_image_space(root_tripwires, P, fw, fh)
                )

            # Root global entries: only list sensors where this ROI/tripwire actually projects
            # (omit cameras with empty projection, e.g. behind camera or degenerate).
            for r in root_rois:
                if not isinstance(r, dict):
                    continue
                rid = str(r.get("id", ""))
                r["sensors"] = [
                    s["id"]
                    for s in sensors
                    if any(str(x.get("id", "")) == rid for x in (s.get("projectedGlobalRois") or []))
                ]

            for tw in root_tripwires:
                if not isinstance(tw, dict):
                    continue
                tid = str(tw.get("id", ""))
                tw["sensors"] = [
                    s["id"]
                    for s in sensors
                    if any(
                        str(x.get("id", "")) == tid
                        for x in (s.get("projectedGlobalTripwires") or [])
                    )
                ]

            # Final deliverable (_exported.json): omit per-sensor image projections; they stay only in _projected.json.
            if output_stem == "projected":
                sensors_out: List[Dict] = sensors
            else:
                sensors_out = []
                for s in sensors:
                    d = {k: v for k, v in s.items() if k not in ("projectedGlobalRois", "projectedGlobalTripwires")}
                    sensors_out.append(d)

            # Create final calibration export (without worldTransform)
            calibration_export = {
                "version": "1.0",
                "osmURL": "",
                "calibrationType": "cartesian",
                "sensors": sensors_out,
                "rois": root_rois,
                "tripwires": root_tripwires,
            }

            # Save to file in project directory (default stem "exported"; use "projected" for verification snapshot)
            suffix = f"_{self.result_type}" if self.result_type == "vggt" else ""
            output_path = self.project_path / f"{self.project_name}_{output_stem}{suffix}.json"
            with open(output_path, 'w') as f:
                json.dump(calibration_export, f, indent=2)

            success_msg = f"Successfully exported calibration data for {len(sensors)} cameras"
            if root_rois or root_tripwires:
                success_msg += f"; global: {len(root_rois)} ROI(s), {len(root_tripwires)} tripwire(s)"
            if failed_cameras:
                success_msg += f"\n⚠️ Warning: {len(failed_cameras)} cameras failed:\n" + "\n".join(failed_cameras[:3])
            for gw in global_warnings:
                success_msg += f"\n⚠️ Warning: {gw}"

            return True, success_msg, output_path
        
        except IncompleteTripwireError as e:
            return False, str(e), None
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            return False, f"Export failed: {str(e)}\n\nDetails:\n{error_detail}", None


def export_image_calibration_for_project(
    project_path: Path,
    camera_name_map: Dict[str, str] = None,
    project_id: Optional[str] = None,
) -> Tuple[bool, str, Optional[Path]]:
    """Write ``{project_id}_image_mode_exported.json`` (pixel ROIs/tripwires; other fields empty)."""
    pid = project_id if project_id is not None else Path(project_path).name
    exporter = CalibrationExporter(
        project_path, result_type="amc", camera_name_map=camera_name_map, is_single_camera=None
    )
    return exporter.export_image_calibration_data(pid)


def export_calibration_for_project(
    project_path: Path,
    result_type: str = "amc",
    camera_name_map: Dict[str, str] = None,
    is_single_camera: Optional[bool] = None,
    output_stem: str = "exported",
) -> Tuple[bool, str, Optional[Path]]:
    """
    Convenience function to export calibration data for a project.

    Args:
        project_path: Path to project directory
        result_type: Type of results to export - "amc" or "vggt"
        camera_name_map: Maps internal names (cam_00) to display names
        is_single_camera: If True/False use that; if None, infer from output dirs.
        output_stem: "exported" for full export file, "projected" for verification snapshot.

    Returns:
        Tuple of (success, message, output_path)
    """
    exporter = CalibrationExporter(project_path, result_type, camera_name_map, is_single_camera=is_single_camera)
    return exporter.export_calibration_data(output_stem=output_stem)
