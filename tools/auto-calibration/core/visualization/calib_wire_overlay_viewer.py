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

"""Tiled calibration floor-grid + reconstructed-pole viewer (browser UI).

Projects a virtual world floor grid (z=0) into each camera view using the
multi-view (MV) calibration and plays the rectified videos underneath. For each
2D person detection it reconstructs a vertical pole by back-projecting the
detection anchor (keypoint foot/head when available, else bbox center) onto the
floor plane and re-projecting the (X,Y,0)-(X,Y,h) segment. Per-camera pose
perturbations can be injected to visualise calibration-error sensitivity.

World geometry is defined in the MV ``results_ba_scaled_world`` coordinate frame.
Detections come from ``trajDump_Stream_0_3d.txt`` (bbox + body keypoints) when
present, else ``Det-bboxes.log`` / ``Det-Stream_0.log`` (bbox only).

The virtual object drawn per detection is selectable (pole / cylinder / 3D box).

Usage:
    python calib_wire_overlay_viewer.py /path/to/project/output
    python calib_wire_overlay_viewer.py /path/to/output --video peoplenet_3d.mp4 --port 8010
    python calib_wire_overlay_viewer.py /path/to/output --bbox-snap top --grid-spacing 1.5
    python calib_wire_overlay_viewer.py /path/to/output --virtual-object cylinder --cylinder-radius 0.3
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
import webbrowser
from collections import defaultdict
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
import yaml

_MY_UTILS = Path(__file__).resolve().parent
if str(_MY_UTILS) not in sys.path:
    sys.path.insert(0, str(_MY_UTILS))
from extract_mv_camera_params import (  # noqa: E402
    extract_from_caminfo_yaml,
    params_from_projection_matrix,
)

logger = logging.getLogger("calib_wire_overlay_viewer")

CHUNK = 1024 * 512
VIDEO_CANDIDATES = ("rectified.mp4", "peoplenet_3d.mp4", "cam_{id}.mp4")

# CSS colors for overlays (UI uses these directly).
_COLOR_MV_GRID = "#7CFC00"   # lawn green (floor grid)
_COLOR_BBOX = "#FFD400"      # yellow (2D det bbox)
_COLOR_RECON_POLE = "#FF3B30"  # red (reconstructed pole from back-projection)
_COLOR_VIRTUAL_OBJ = "#FF00FF"  # magenta (cylinder / 3D box virtual object)
_COLOR_EXTENDED_BBOX = "#00E5FF"  # cyan (recovered/extended bbox for occluded people)
_COLOR_TRACE = "#00FFA3"  # spring green (foot-location trace / trail)


def _load_projection_matrix(path: Path, key: str) -> np.ndarray:
    """Load a 3x4 world->pixel projection matrix from YAML."""
    data = yaml.safe_load(path.read_text())
    mat = np.asarray(data[key], dtype=np.float64)
    return mat.reshape(3, 4)


def _find_mv_camparams_dir(root: Path, override: Path | None) -> Path:
    """Locate MV ``results_ba_scaled_world`` camera-parameter directory."""
    if override is not None:
        if sorted(override.glob("camInfo_*.yaml")):
            return override
        raise FileNotFoundError(f"No camInfo_*.yaml in {override}")
    for d in sorted(root.glob("**/results_ba_scaled_world")):
        if d.is_dir() and sorted(d.glob("camInfo_*.yaml")):
            return d
    raise FileNotFoundError(
        f"Could not find **/results_ba_scaled_world/camInfo_*.yaml under {root}"
    )


def _find_sv_results_dir(root: Path, override: Path | None) -> Path | None:
    """Locate ``single_view_results`` (or explicit override)."""
    if override is not None:
        return override if override.is_dir() else None
    cand = root / "single_view_results"
    if cand.is_dir():
        return cand
    for p in sorted(root.glob("**/single_view_results")):
        if p.is_dir():
            return p
    return None


def _cam_index_from_name(name: str) -> int:
    m = re.search(r"(\d+)", name)
    if not m:
        raise ValueError(f"Cannot parse camera index from {name!r}")
    return int(m.group(1))


def _project_point(P: np.ndarray, xyz: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Project a 3D world point; return ((u,v), depth) or None if behind camera."""
    pt = P @ np.array([xyz[0], xyz[1], xyz[2], 1.0], dtype=np.float64)
    if abs(pt[2]) < 1e-9:
        return None
    depth = float(pt[2])
    if depth <= 1e-6:
        return None
    uv = pt[:2] / pt[2]
    return uv, depth


def _clip_segment(
    p0: np.ndarray,
    p1: np.ndarray,
    w: int,
    h: int,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Cohen–Sutherland clip of a 2D segment to [0,w] x [0,h]."""
    xmin, ymin, xmax, ymax = 0.0, 0.0, float(w - 1), float(h - 1)

    def code(p: np.ndarray) -> int:
        x, y = float(p[0]), float(p[1])
        c = 0
        if x < xmin:
            c |= 1
        elif x > xmax:
            c |= 2
        if y < ymin:
            c |= 4
        elif y > ymax:
            c |= 8
        return c

    x0, y0 = float(p0[0]), float(p0[1])
    x1, y1 = float(p1[0]), float(p1[1])
    c0, c1 = code(p0), code(p1)
    while True:
        if c0 == 0 and c1 == 0:
            return np.array([x0, y0]), np.array([x1, y1])
        if c0 & c1:
            return None
        c_out = c0 if c0 else c1
        if c_out & 8:
            x = x0 + (x1 - x0) * (ymax - y0) / (y1 - y0 + 1e-12)
            y = ymax
        elif c_out & 4:
            x = x0 + (x1 - x0) * (ymin - y0) / (y1 - y0 + 1e-12)
            y = ymin
        elif c_out & 2:
            y = y0 + (y1 - y0) * (xmax - x0) / (x1 - x0 + 1e-12)
            x = xmax
        else:
            y = y0 + (y1 - y0) * (xmin - x0) / (x1 - x0 + 1e-12)
            x = xmin
        if c_out == c0:
            x0, y0 = x, y
            c0 = code(np.array([x0, y0]))
        else:
            x1, y1 = x, y
            c1 = code(np.array([x1, y1]))


def _project_segment(
    P: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
    width: int,
    height: int,
) -> list[float] | None:
    """Project a 3D world segment to clipped 2D image pixels."""
    pa = _project_point(P, a)
    pb = _project_point(P, b)
    if pa is None or pb is None:
        return None
    clipped = _clip_segment(pa[0], pb[0], width, height)
    if clipped is None:
        return None
    q0, q1 = clipped
    return [float(q0[0]), float(q0[1]), float(q1[0]), float(q1[1])]


def _decompose_projection(P: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decompose 3x4 world->pixel matrix into K(3x3), R(3x3), camera centre C(3,)."""
    K_raw, R_raw, t_h, _, _, _, _ = cv2.decomposeProjectionMatrix(P.astype(np.float64))
    K_arr = np.asarray(K_raw, dtype=np.float64)
    R_arr = np.asarray(R_raw, dtype=np.float64)
    t_vec = np.asarray(t_h, dtype=np.float64).reshape(-1)
    if K_arr.shape[0] == 4:
        K = (K_arr[:3, :3] / K_arr[2, 2]).copy()
    else:
        K = (K_arr / K_arr[2, 2]).copy()
    if R_arr.shape[0] == 4:
        scale = float(R_arr[3, 3]) if abs(R_arr[3, 3]) > 1e-12 else 1.0
        R = (R_arr[:3, :3] / scale).copy()
    else:
        R = R_arr.copy()
    C = (t_vec[:3] / t_vec[3]).ravel().astype(np.float64)
    return K, R, C


def _build_projection(K: np.ndarray, R: np.ndarray, C: np.ndarray) -> np.ndarray:
    """Compose 3x4 P from intrinsics, world-to-camera rotation, and camera centre."""
    t = -R @ C.reshape(3, 1)
    return (K @ np.hstack([R, t])).astype(np.float64)


def _rotation_matrix_xyz_deg(rx_deg: float, ry_deg: float, rz_deg: float) -> np.ndarray:
    """Small rotation in camera frame: R_delta = Rx @ Ry @ Rz."""
    rx, ry, rz = map(math.radians, (rx_deg, ry_deg, rz_deg))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    Rx = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]])
    Ry = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    Rz = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
    return Rx @ Ry @ Rz


def perturb_projection_matrix(
    P: np.ndarray,
    *,
    dx: float = 0.0,
    dy: float = 0.0,
    dz: float = 0.0,
    drx: float = 0.0,
    dry: float = 0.0,
    drz: float = 0.0,
) -> np.ndarray:
    """Apply pose perturbation to a projection matrix.

    Translation ``(dx, dy, dz)`` shifts the camera centre in MV world metres.
    Rotations ``(drx, dry, drz)`` are degrees applied in the *camera* frame by
    left-multiplying the world-to-camera ``R`` (``R_new = R_delta @ R``):

    * ``drx`` = pitch / tilt (about camera x-axis)
    * ``dry`` = yaw / pan   (about camera y-axis)
    * ``drz`` = roll        (about camera optical z-axis)
    """
    K, R, C = _decompose_projection(P)
    C_new = C + np.array([dx, dy, dz], dtype=np.float64)
    R_new = _rotation_matrix_xyz_deg(drx, dry, drz) @ R
    return _build_projection(K, R_new, C_new)


def _apply_pose_perturbations(
    proj_ctxs: list[dict[str, Any]],
    cam_indices: list[int],
    perturbations: dict[int, dict[str, float]],
) -> None:
    """Mutate projection contexts in-place for perturbed cameras."""
    for i, cam_idx in enumerate(cam_indices):
        pert = perturbations.get(cam_idx)
        if not pert:
            continue
        ctx = proj_ctxs[i]
        ctx["P_mv"] = perturb_projection_matrix(ctx["P_mv"], **pert)


def _azimuth_deg_from_projection(P: np.ndarray) -> float:
    """Camera heading (pan) in world XY plane, degrees, from optical axis."""
    _, R, _ = _decompose_projection(P)
    view = R.T @ np.array([0.0, 0.0, 1.0])
    return float(np.degrees(np.arctan2(view[1], view[0])))


def _ground_basis_from_projection(P: np.ndarray) -> list[float]:
    """Ground-plane basis for a fronto-parallel box, from the projection matrix.

    Box local x-axis = camera image-right direction projected onto the world
    floor (z=0) and normalised; local y-axis = 90° rotation of x on the floor.
    This keeps the box front face parallel to the camera image plane
    ("x aligns with view x").

    Returns:
        ``[ux, uy, vx, vy]`` — the two ground-plane unit vectors (u = box x,
        v = box y), each in world XY.
    """
    _, R, _ = _decompose_projection(P)
    right_world = R.T @ np.array([1.0, 0.0, 0.0])  # camera +x in world
    u = np.array([right_world[0], right_world[1]], dtype=np.float64)
    norm = float(np.linalg.norm(u))
    if norm < 1e-9:  # degenerate (camera right is world-vertical); fall back
        u = np.array([1.0, 0.0], dtype=np.float64)
    else:
        u = u / norm
    v = np.array([-u[1], u[0]], dtype=np.float64)  # perpendicular on ground
    return [float(u[0]), float(u[1]), float(v[0]), float(v[1])]


def _camera_centers_world(cam_files: list[Path], key: str) -> list[np.ndarray]:
    centers = []
    for cf in cam_files:
        P = _load_projection_matrix(cf, key)
        _, _, t_h, _, _, _, _ = cv2.decomposeProjectionMatrix(P)
        centers.append((t_h[:3] / t_h[3]).ravel())
    return centers


def _auto_bounds(centers: list[np.ndarray], margin: float = 12.0) -> tuple[int, int, int, int]:
    """Integer XY bounds for floor grid from camera centres."""
    xs = [c[0] for c in centers]
    ys = [c[1] for c in centers]
    x_min = int(math.floor(min(xs) - margin))
    x_max = int(math.ceil(max(xs) + margin))
    y_min = int(math.floor(min(ys) - margin))
    y_max = int(math.ceil(max(ys) + margin))
    return x_min, x_max, y_min, y_max


def _line_coords(lo: float, hi: float, spacing: float) -> list[float]:
    """Grid-line coordinates from ``lo`` up to ``hi`` at ``spacing`` intervals."""
    n = int(math.floor((hi - lo) / spacing + 1e-9))
    coords = [lo + k * spacing for k in range(n + 1)]
    if not coords or coords[-1] < hi - 1e-6:
        coords.append(hi)
    return coords


def build_world_segments(
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    grid_spacing: float = 1.0,
    subdiv: float = 1.0,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Build 3D floor-grid line segments in MV world (z=0).

    Person poles are NOT built here — they are reconstructed per-detection from
    2D boxes/keypoints at runtime in the browser.

    Args:
        x_min, x_max, y_min, y_max: Floor bounds in metres.
        grid_spacing: Distance between grid lines in metres.
        subdiv: Sub-segment length in metres for projecting each grid line, so a
            single endpoint behind the camera only drops that piece.

    Returns:
        List of (start, end) 3D point pairs for the floor grid.
    """
    grid_segs: list[tuple[np.ndarray, np.ndarray]] = []
    step = max(min(subdiv, grid_spacing), 1e-3)
    xs = _line_coords(x_min, x_max, grid_spacing)
    ys = _line_coords(y_min, y_max, grid_spacing)

    # Vertical grid lines (constant x), split into `step`-length pieces along y.
    for x in xs:
        for y0 in np.arange(y_min, y_max, step):
            y1 = min(float(y0) + step, y_max)
            grid_segs.append(
                (np.array([x, float(y0), 0.0]), np.array([x, y1, 0.0]))
            )
    # Horizontal grid lines (constant y), split into `step`-length pieces along x.
    for y in ys:
        for x0 in np.arange(x_min, x_max, step):
            x1 = min(float(x0) + step, x_max)
            grid_segs.append(
                (np.array([float(x0), y, 0.0]), np.array([x1, y, 0.0]))
            )
    return grid_segs


def _segments_for_camera(
    P: np.ndarray,
    grid_segs: list[tuple[np.ndarray, np.ndarray]],
    width: int,
    height: int,
) -> dict[str, list[list[float]]]:
    """Project floor-grid segments for one camera. Each out seg is [x0,y0,x1,y1]."""
    out_grid: list[list[float]] = []
    for a, b in grid_segs:
        seg = _project_segment(P, a, b, width, height)
        if seg is not None:
            out_grid.append(seg)
    return {"grid": out_grid}


def _resolve_video(cam_dir: Path, cam_idx: int, video_name: str | None, video_dir: Path | None) -> Path:
    if video_dir is not None:
        for pat in (video_name, f"cam_{cam_idx:02d}.mp4", "rectified.mp4", "peoplenet_3d.mp4"):
            if pat is None:
                continue
            p = video_dir / pat
            if p.is_file():
                return p
    if video_name and (cam_dir / video_name).is_file():
        return cam_dir / video_name
    for pat in VIDEO_CANDIDATES:
        p = cam_dir / pat.format(id=f"{cam_idx:02d}")
        if p.is_file():
            return p
    raise FileNotFoundError(f"No video found for {cam_dir}")


def _find_det_log(cam_dir: Path) -> Path | None:
    """Locate per-camera 2D detection log (Det-bboxes.log or Det-Stream_0.log)."""
    for name in ("Det-bboxes.log", "Det-Stream_0.log"):
        p = cam_dir / name
        if p.is_file():
            return p
    return None


def _find_det_source(cam_dir: Path) -> tuple[Path, str] | None:
    """Locate the richest per-camera detection source.

    Prefers ``trajDump_Stream_0_3d.txt`` (bbox + body keypoints), falling back
    to ``Det-bboxes.log`` / ``Det-Stream_0.log`` (bbox only).

    Returns:
        ``(path, kind)`` where kind is ``"trajdump"`` or ``"detlog"``, or None.
    """
    traj = cam_dir / "trajDump_Stream_0_3d.txt"
    if traj.is_file():
        return traj, "trajdump"
    log = _find_det_log(cam_dir)
    if log is not None:
        return log, "detlog"
    return None


# ---------------------------------------------------------------------------
# Body-keypoint foot/head derivation (ported from AMC
# core/gt_trajectory_tools/convert_amc_dump_to_2d_detections.py so this viewer
# stays self-contained).
# ---------------------------------------------------------------------------

JOINT = {"pelvis": 0, "left_ankle": 7, "right_ankle": 8, "nose": 15}
FOOT_METHODS = ("line_intersect", "ankle_mean")


def parse_trajdump_line(line: str) -> dict | None:
    """Parse one ``trajDump_Stream_0_3d.txt`` row.

    Layout (comma-separated)::

        0:frame 1:local_id 2:bx 3:by 4:bw 5:bh 6:const 7:wx 8:wy 9:-1 10:1
        11:conf 12:px 13:py 14:<hull u|v...> 15:-1 16:<keypoints x|y|z|conf> | -1

    The keypoint block may be truncated mid-joint, so only floor(len/4) full
    joints are trusted; it is absent on some rows (trailing ``-1,-1``).

    Returns:
        ``{"frame", "local_id", "bbox"=(x,y,w,h), "conf", "kpts"=(J,4)|None}``
        or None if unparseable.
    """
    c = line.rstrip("\n").split(",")
    if len(c) < 14:
        return None
    try:
        frame = int(c[0])
        local_id = int(c[1])
        bbox = (float(c[2]), float(c[3]), float(c[4]), float(c[5]))
        conf = float(c[11]) if len(c) > 11 else 1.0
    except (ValueError, IndexError):
        return None
    pipe_fields = [i for i, f in enumerate(c) if "|" in f]
    kpts = None
    if len(pipe_fields) >= 2:  # [0]=hull, [1]=keypoints
        toks = c[pipe_fields[1]].split("|")
        n_full = len(toks) // 4
        if n_full >= 1:
            try:
                vals = [float(t) for t in toks[: n_full * 4]]
                kpts = np.asarray(vals, dtype=np.float64).reshape(n_full, 4)
            except ValueError:
                kpts = None
    return {"frame": frame, "local_id": local_id, "bbox": bbox,
            "conf": conf, "kpts": kpts}


def null_stale_keypoints(records: list[dict]) -> int:
    """In-place: null keypoints that verbatim-repeat the tracklet's prev frame.

    AMC's BodyPose3DNet refreshes the pose at a lower rate than the tracker and
    repeats the last pose in between; those held copies are stale (person moved,
    joints frozen), so we null them and let the frame-correct bbox take over.
    """
    by_lid: dict[int, list[dict]] = defaultdict(list)
    for r in records:
        by_lid[r["local_id"]].append(r)
    n_nulled = 0
    for recs in by_lid.values():
        recs.sort(key=lambda r: r["frame"])
        prev: np.ndarray | None = None
        for r in recs:
            k = r["kpts"]
            stale = (
                k is not None and prev is not None
                and k.shape == prev.shape and np.array_equal(k, prev)
            )
            if stale:
                r["kpts"] = None
                n_nulled += 1
            if k is not None:
                prev = k
    return n_nulled


def _line_intersection(
    p1: np.ndarray, p2: np.ndarray, p3: np.ndarray, p4: np.ndarray,
) -> np.ndarray | None:
    """Intersection of line(p1,p2) and line(p3,p4) in 2D, or None if parallel."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return None
    a = x1 * y2 - y1 * x2
    b = x3 * y4 - y3 * x4
    px = (a * (x3 - x4) - (x1 - x2) * b) / den
    py = (a * (y3 - y4) - (y1 - y2) * b) / den
    return np.array([px, py], dtype=np.float64)


def foot_and_head(
    bbox: tuple[float, float, float, float],
    kpts: np.ndarray | None,
    min_conf: float,
    foot_method: str = "line_intersect",
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Derive the POC 2D foot (floor body-center) and head (nose) points.

    ``head`` = nose keypoint, else bbox top-center. ``foot`` = line-intersection
    of inter-ankle line and nose→pelvis axis (or ankle midpoint), else bbox
    bottom-center. Keypoint results must land in the bbox lower region.

    Returns ``(foot_xy, head_xy, used_keypoints)``.
    """
    bx, by, bw, bh = bbox
    foot_fallback = np.array([bx + bw / 2.0, by + bh], dtype=np.float64)
    head_fallback = np.array([bx + bw / 2.0, by], dtype=np.float64)

    def joint(name: str) -> np.ndarray | None:
        if kpts is None:
            return None
        idx = JOINT[name]
        if idx >= kpts.shape[0] or kpts[idx, 3] < min_conf:
            return None
        return kpts[idx, :2].astype(np.float64)

    la, ra = joint("left_ankle"), joint("right_ankle")
    nose, pelvis = joint("nose"), joint("pelvis")
    head = nose if nose is not None else head_fallback

    def in_region(pt: np.ndarray) -> bool:
        return (
            (bx - 0.3 * bw) <= pt[0] <= (bx + 1.3 * bw)
            and (by + 0.3 * bh) <= pt[1] <= (by + 1.3 * bh)
        )

    ankle_mid = (la + ra) / 2.0 if (la is not None and ra is not None) else None
    used_kp_head = nose is not None

    if foot_method == "ankle_mean":
        if ankle_mid is not None and in_region(ankle_mid):
            return ankle_mid, head, True
        return foot_fallback, head, used_kp_head

    if la is not None and ra is not None and nose is not None and pelvis is not None:
        inter = _line_intersection(la, ra, nose, pelvis)
        if inter is not None and in_region(inter):
            return inter, head, True
    if ankle_mid is not None and in_region(ankle_mid):
        return ankle_mid, head, True
    return foot_fallback, head, used_kp_head


def gate_foot_y(
    kpts: np.ndarray | None,
    min_conf: float,
) -> float | None:
    """Lowest trusted ankle image-y for occlusion-extension gating.

    Returns the largest (lowest on screen) ``y`` among trusted ankle joints, or
    ``None`` when neither ankle passes ``min_conf`` / no keypoints exist. This is
    the pose evidence for "how far down the body actually reaches", compared
    against the geometry-expected foot to decide whether to extend (see §7.8).
    """
    if kpts is None:
        return None
    ys: list[float] = []
    for name in ("left_ankle", "right_ankle"):
        idx = JOINT[name]
        if idx < kpts.shape[0] and kpts[idx, 3] >= min_conf:
            ys.append(float(kpts[idx, 1]))
    return max(ys) if ys else None


def load_trajdump_detections(
    traj_log: Path, min_conf: float = 0.0, *,
    min_kpt_conf: float = 0.3, foot_method: str = "line_intersect",
    null_stale: bool = False,
) -> dict[str, Any]:
    """Load trajDump detections with bbox + keypoint-derived foot/head.

    Args:
        null_stale: If True, drop keypoints that verbatim-repeat the tracklet's
            previous frame (AMC BodyPose3DNet holds a pose ~20 frames between
            refreshes). Off by default here so keypoints are used on every frame
            they exist — the held pose is a far better head/foot anchor than the
            bbox for near-stationary people, though it lags for fast movers.

    Returns dict with ``frames`` mapping frame index (str) -> list of
    ``{"b": [x,y,w,h], "f": [fx,fy], "h": [hx,hy], "k": 0|1, "gy": ankle_y|null}``
    where ``f``/``h`` are the (keypoint-preferred) foot/head anchors, ``k`` flags
    keypoint use, and ``gy`` is the lowest trusted ankle image-y used to gate
    occlusion extension (§7.8).
    """
    records = [r for r in (parse_trajdump_line(ln)
                           for ln in traj_log.read_text().splitlines())
               if r is not None]
    if null_stale:
        null_stale_keypoints(records)
    frames: dict[str, list[dict[str, Any]]] = {}
    n_kpt = 0
    for r in records:
        if r["conf"] < min_conf:
            continue
        foot, head, used_kp = foot_and_head(
            r["bbox"], r["kpts"], min_kpt_conf, foot_method
        )
        bx, by, bw, bh = r["bbox"]
        gy = gate_foot_y(r["kpts"], min_kpt_conf)
        frames.setdefault(str(r["frame"]), []).append({
            "b": [int(bx), int(by), int(bw), int(bh)],
            "f": [float(foot[0]), float(foot[1])],
            "h": [float(head[0]), float(head[1])],
            "k": 1 if used_kp else 0,
            "gy": gy,
            "id": int(r["local_id"]),
        })
        if used_kp:
            n_kpt += 1
    return {"frames": frames, "has_kpts": n_kpt > 0}


def load_det_bboxes(det_log: Path, min_conf: float = 0.0) -> dict[str, Any]:
    """Load AMC Det-*.log detections (bbox only) indexed by frame number.

    Log format per line: ``frame, track_id, x, y, w, h, confidence, ...``

    Returns:
        Dict with ``frames`` mapping frame index (str) -> list of
        ``{"b": [x, y, w, h]}`` (no keypoints).
    """
    frames: dict[str, list[dict[str, Any]]] = {}
    with det_log.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 7:
                continue
            try:
                frame = int(parts[0])
                track_id = int(float(parts[1]))
                x = int(float(parts[2]))
                y = int(float(parts[3]))
                w = int(float(parts[4]))
                h = int(float(parts[5]))
                conf = float(parts[6])
            except ValueError:
                continue
            if conf < min_conf:
                continue
            frames.setdefault(str(frame), []).append({"b": [x, y, w, h], "id": track_id})
    return {"frames": frames, "has_kpts": False}


def load_detections(
    src: Path, kind: str, min_conf: float = 0.0, *,
    min_kpt_conf: float = 0.3, foot_method: str = "line_intersect",
    null_stale: bool = False,
) -> dict[str, Any]:
    """Dispatch detection loading by source kind (trajdump | detlog)."""
    if kind == "trajdump":
        return load_trajdump_detections(
            src, min_conf, min_kpt_conf=min_kpt_conf, foot_method=foot_method,
            null_stale=null_stale,
        )
    return load_det_bboxes(src, min_conf)


def _camera_proj_contexts(
    mv_files: list[Path],
    mv_key: str,
) -> list[dict[str, Any]]:
    """Per-camera projection context (MV projection matrix) for reprojection."""
    ctxs: list[dict[str, Any]] = []
    for mv_yml in mv_files:
        P_mv = _load_projection_matrix(mv_yml, mv_key)
        ctxs.append({"P_mv": P_mv})
    return ctxs


def reproject_overlays(
    proj_ctxs: list[dict[str, Any]],
    camera_dims: list[tuple[int, int]],
    bounds: dict[str, float],
    *,
    grid_spacing: float,
    person_height: float,
) -> list[dict[str, dict[str, list[list[float]]]]]:
    """Recompute the MV floor-grid overlay for all cameras."""
    grid_segs = build_world_segments(
        bounds["x_min"],
        bounds["x_max"],
        bounds["y_min"],
        bounds["y_max"],
        grid_spacing=grid_spacing,
    )
    overlays: list[dict[str, dict[str, list[list[float]]]]] = []
    for ctx, (w, h) in zip(proj_ctxs, camera_dims):
        mv_overlay = _segments_for_camera(ctx["P_mv"], grid_segs, w, h)
        overlays.append({"overlay_mv": mv_overlay})
    return overlays


def _load_static_background(cam_dir: Path, cam_idx: int) -> np.ndarray:
    """Load the staged still, falling back to frame zero of the staged video."""
    still = cam_dir / "rectified.jpg"
    if still.is_file():
        image = cv2.imread(str(still))
        if image is not None:
            return image

    video = _resolve_video(cam_dir, cam_idx, None, None)
    cap = cv2.VideoCapture(str(video))
    try:
        ok, image = cap.read()
    finally:
        cap.release()
    if not ok or image is None:
        raise ValueError(f"Could not read a background image or first frame for {cam_dir}")
    return image


def render_grid_pole_overlays(
    output_folder: Path,
    *,
    sv_results_dir: Path | None = None,
    camparams_dir: Path | None = None,
    output_dir: Path | None = None,
    person_height: float = 1.75,
    grid_spacing: float = 1.0,
    grid_opacity: float = 0.5,
    x_min: float | None = None,
    x_max: float | None = None,
    y_min: float | None = None,
    y_max: float | None = None,
    margin: float = 12.0,
    line_width: int = 2,
) -> list[Path]:
    """Render static floor-grid + pole images without detection inputs.

    One vertical pole is anchored at every floor-grid intersection. This is an
    additive, non-interactive use of the viewer geometry; the browser viewer
    and its detection-driven reconstruction path are unchanged.
    """
    output_folder = output_folder.expanduser().resolve()
    mv_dir = _find_mv_camparams_dir(output_folder, camparams_dir)
    mv_files = sorted(mv_dir.glob("camInfo_*.yaml"))
    if not mv_files:
        raise FileNotFoundError(f"No camInfo_*.yaml found in {mv_dir}")

    sv_root = _find_sv_results_dir(output_folder, sv_results_dir)
    if sv_root is None:
        raise FileNotFoundError(
            "Could not find single_view_results; pass --sv-results-dir explicitly"
        )

    centers = _camera_centers_world(mv_files, "projectionMatrix_3x4")
    if x_min is None or x_max is None or y_min is None or y_max is None:
        ax0, ax1, ay0, ay1 = _auto_bounds(centers, margin=margin)
        x_min = ax0 if x_min is None else x_min
        x_max = ax1 if x_max is None else x_max
        y_min = ay0 if y_min is None else y_min
        y_max = ay1 if y_max is None else y_max

    if person_height <= 0 or grid_spacing <= 0:
        raise ValueError("person_height and grid_spacing must be > 0")
    if not 0.0 <= grid_opacity <= 1.0:
        raise ValueError("grid_opacity must be between 0 and 1")

    grid_segments = build_world_segments(
        x_min, x_max, y_min, y_max, grid_spacing=grid_spacing
    )
    intersections = [
        (x, y)
        for x in _line_coords(x_min, x_max, grid_spacing)
        for y in _line_coords(y_min, y_max, grid_spacing)
    ]

    target_dir = (
        output_dir.expanduser().resolve()
        if output_dir is not None
        else output_folder / "virtual_gt_overlays"
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    grid_color = (0, 252, 124)  # _COLOR_MV_GRID (#7CFC00), in BGR
    pole_color = (48, 59, 255)  # _COLOR_RECON_POLE (#FF3B30), in BGR
    thickness = max(1, int(line_width))

    for mv_yml in mv_files:
        cam_idx = _cam_index_from_name(mv_yml.stem)
        cam_dir = sv_root / f"cam_{cam_idx:02d}"
        if not cam_dir.is_dir():
            raise FileNotFoundError(f"Missing {cam_dir}")

        background = _load_static_background(cam_dir, cam_idx)
        height, width = background.shape[:2]
        P = _load_projection_matrix(mv_yml, "projectionMatrix_3x4")

        # Blend only the grid; poles remain fully opaque.
        grid_layer = background.copy()
        projected_grid = _segments_for_camera(
            P, grid_segments, width, height
        )["grid"]
        for x0, y0, x1, y1 in projected_grid:
            cv2.line(
                grid_layer,
                (int(round(x0)), int(round(y0))),
                (int(round(x1)), int(round(y1))),
                grid_color,
                thickness,
                cv2.LINE_AA,
            )
        rendered = cv2.addWeighted(
            grid_layer, grid_opacity, background, 1.0 - grid_opacity, 0.0
        )

        pole_count = 0
        for x, y in intersections:
            segment = _project_segment(
                P,
                np.array([x, y, 0.0], dtype=np.float64),
                np.array([x, y, person_height], dtype=np.float64),
                width,
                height,
            )
            if segment is None:
                continue
            x0, y0, x1, y1 = segment
            cv2.line(
                rendered,
                (int(round(x0)), int(round(y0))),
                (int(round(x1)), int(round(y1))),
                pole_color,
                thickness + 1,
                cv2.LINE_AA,
            )
            pole_count += 1

        destination = target_dir / f"cam_{cam_idx:02d}.png"
        if not cv2.imwrite(str(destination), rendered):
            raise OSError(f"Could not write {destination}")
        logger.info(
            "  cam_%02d: wrote %s (grid=%d segments, poles=%d)",
            cam_idx,
            destination,
            len(projected_grid),
            pole_count,
        )
        written.append(destination)

    return written


def _video_meta(path: Path) -> tuple[int, int, float, float]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {path}")
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = n / fps if fps > 0 else 0.0
    cap.release()
    return w, h, dur, fps


_SUPPORTED_LOOKUP_FORMATS = ("expected-foot-y-map-v1", "expected-foot-y-map-v2")


def _load_expected_foot_y_manifest(map_dir: Path | None) -> tuple[Path, dict[int, dict[str, Any]]] | None:
    """Load a generated lookup-map manifest, indexed by camera id."""
    if map_dir is None:
        return None
    map_dir = map_dir.expanduser().resolve()
    manifest_path = map_dir / "expected_foot_y_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing expected-foot map manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") not in _SUPPORTED_LOOKUP_FORMATS:
        raise ValueError(f"Unsupported expected-foot map format in {manifest_path}")
    by_id = {int(cam["id"]): cam for cam in manifest.get("cameras", [])}
    for cam in by_id.values():
        cam["person_height"] = float(manifest["person_height"])
    return map_dir, by_id


def _projection_matches_entry(entry: dict[str, Any], P: np.ndarray) -> bool:
    expected_P = np.asarray(entry.get("projection_matrix", []), dtype=np.float64)
    return (
        expected_P.size == 12
        and np.allclose(expected_P.reshape(3, 4), P, rtol=1e-8, atol=1e-8)
    )


def _resolve_map_path(map_root: Path, relative: str) -> Path:
    map_path = (map_root / relative).resolve()
    if map_root not in map_path.parents and map_path != map_root:
        raise ValueError(f"Lookup map path escapes map directory: {map_path}")
    return map_path


def _load_expected_foot_y_map(
    map_root: Path,
    entry: dict[str, Any],
    *,
    width: int,
    height: int,
    P: np.ndarray,
) -> np.ndarray | None:
    """Load and validate one float32 expected-foot-y TIFF."""
    if not _projection_matches_entry(entry, P):
        return None
    map_path = _resolve_map_path(map_root, str(entry["file"]))
    image = cv2.imread(str(map_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise OSError(f"Could not read expected-foot map: {map_path}")
    if image.ndim != 2 or image.shape != (height, width):
        raise ValueError(
            f"Expected-foot map {map_path} has shape {image.shape}; "
            f"expected {(height, width)}"
        )
    return np.asarray(image, dtype=np.float32)


def _load_center_ground_xyz_map(
    map_root: Path,
    entry: dict[str, Any],
    *,
    width: int,
    height: int,
    P: np.ndarray,
) -> np.ndarray | None:
    """Load center-snap float32 (X,Y,0) ground lookup (.npy), or None if absent."""
    # Optional (v1 manifests / older dirs may only have expected-foot-y).
    ground_file = entry.get("ground_xyz_file")
    if not ground_file:
        return None
    if not _projection_matches_entry(entry, P):
        return None
    map_path = _resolve_map_path(map_root, str(ground_file))
    if not map_path.is_file():
        raise FileNotFoundError(f"Missing center ground XYZ map: {map_path}")
    image = np.load(map_path)
    if image.ndim != 3 or image.shape != (height, width, 3):
        raise ValueError(
            f"Center ground XYZ map {map_path} has shape {image.shape}; "
            f"expected {(height, width, 3)}"
        )
    return np.asarray(image, dtype=np.float32)


def discover_scene(
    output_folder: Path,
    *,
    sv_results_dir: Path | None = None,
    camparams_dir: Path | None = None,
    video_dir: Path | None = None,
    video_name: str | None = None,
    person_height: float = 1.75,
    x_min: int | None = None,
    x_max: int | None = None,
    y_min: int | None = None,
    y_max: int | None = None,
    margin: float = 12.0,
    grid_spacing: float = 1.0,
    det_min_conf: float = 0.0,
    bbox_snap: str = "center",
    grid_opacity: float = 0.35,
    virtual_object: str = "pole",
    cylinder_radius: float = 0.3,
    extend_occluded: bool = False,
    kpt_gate_extend: bool = False,
    kpt_gate_tol: float = 0.2,
    trace_secs: float = 2.0,
    min_kpt_conf: float = 0.3,
    foot_method: str = "line_intersect",
    null_stale: bool = False,
    perturbations: dict[int, dict[str, float]] | None = None,
    expected_foot_y_dir: Path | None = None,
) -> dict:
    """Build viewer state: videos, overlays, metadata."""
    output_folder = output_folder.expanduser().resolve()
    mv_dir = _find_mv_camparams_dir(output_folder, camparams_dir)
    mv_key = "projectionMatrix_3x4"
    mv_files = sorted(mv_dir.glob("camInfo_*.yaml"))

    sv_root = _find_sv_results_dir(output_folder, sv_results_dir)

    centers = _camera_centers_world(mv_files, mv_key)
    if x_min is None or x_max is None or y_min is None or y_max is None:
        ax0, ax1, ay0, ay1 = _auto_bounds(centers, margin=margin)
        x_min = ax0 if x_min is None else x_min
        x_max = ax1 if x_max is None else x_max
        y_min = ay0 if y_min is None else y_min
        y_max = ay1 if y_max is None else y_max

    grid_segs = build_world_segments(
        x_min, x_max, y_min, y_max, grid_spacing=grid_spacing
    )

    proj_ctxs = _camera_proj_contexts(mv_files, mv_key)
    cam_indices = [_cam_index_from_name(mv_yml.stem) for mv_yml in mv_files]
    foot_map_manifest = _load_expected_foot_y_manifest(expected_foot_y_dir)
    if perturbations:
        _apply_pose_perturbations(proj_ctxs, cam_indices, perturbations)
        for cam_idx, pert in sorted(perturbations.items()):
            logger.info(
                "  cam_%02d perturb: dpos=(%.3f, %.3f, %.3f) m  "
                "drot=(%.2f, %.2f, %.2f)°",
                cam_idx,
                pert.get("dx", 0.0),
                pert.get("dy", 0.0),
                pert.get("dz", 0.0),
                pert.get("drx", 0.0),
                pert.get("dry", 0.0),
                pert.get("drz", 0.0),
            )
    cameras: list[dict] = []
    videos: list[Path] = []
    det_srcs: list[tuple[Path, str] | None] = []
    det_fps: list[float] = []
    camera_dims: list[tuple[int, int]] = []
    expected_foot_y_maps: list[np.ndarray | None] = []
    center_ground_xyz_maps: list[np.ndarray | None] = []
    any_kpts = False
    for i, mv_yml in enumerate(mv_files):
        idx = _cam_index_from_name(mv_yml.stem)
        cam_dir = sv_root / f"cam_{idx:02d}" if sv_root else None
        if cam_dir is None or not cam_dir.is_dir():
            raise FileNotFoundError(f"Missing single_view_results/cam_{idx:02d}")
        vid = _resolve_video(cam_dir, idx, video_name, video_dir)
        w, h, dur, fps = _video_meta(vid)
        videos.append(vid)
        camera_dims.append((w, h))

        foot_y_map = None
        ground_xyz_map = None
        foot_map_height = None
        if foot_map_manifest is not None:
            map_root, maps_by_id = foot_map_manifest
            entry = maps_by_id.get(idx)
            if entry is None:
                logger.warning("    no expected-foot map for cam_%02d; using live geometry", idx)
            else:
                foot_y_map = _load_expected_foot_y_map(
                    map_root, entry, width=w, height=h, P=proj_ctxs[i]["P_mv"]
                )
                ground_xyz_map = _load_center_ground_xyz_map(
                    map_root, entry, width=w, height=h, P=proj_ctxs[i]["P_mv"]
                )
                foot_map_height = float(entry["person_height"])
                if foot_y_map is None:
                    logger.warning(
                        "    expected-foot map projection mismatch for cam_%02d; "
                        "using live geometry",
                        idx,
                    )
                else:
                    logger.info("    expected-foot map: %s", entry["file"])
                if ground_xyz_map is None:
                    if entry.get("ground_xyz_file"):
                        logger.warning(
                            "    center ground XYZ map unavailable for cam_%02d; "
                            "using live back-projection",
                            idx,
                        )
                else:
                    logger.info(
                        "    center ground XYZ map: %s", entry["ground_xyz_file"]
                    )
        expected_foot_y_maps.append(foot_y_map)
        center_ground_xyz_maps.append(ground_xyz_map)

        det_src = _find_det_source(cam_dir)
        det_srcs.append(det_src)
        det_fps.append(fps)
        cam_has_kpts = False
        if det_src is not None:
            src_path, kind = det_src
            logger.info("    det source: %s (%s)", src_path.name, kind)
            if kind == "trajdump":
                # Probe once so the UI knows whether keypoints exist.
                probe = load_detections(
                    src_path, kind, det_min_conf,
                    min_kpt_conf=min_kpt_conf, foot_method=foot_method,
                    null_stale=null_stale,
                )
                cam_has_kpts = bool(probe.get("has_kpts"))
                any_kpts = any_kpts or cam_has_kpts

        mv_overlay = _segments_for_camera(proj_ctxs[i]["P_mv"], grid_segs, w, h)

        C = centers[len(cameras)]
        mv_params = extract_from_caminfo_yaml(mv_yml)
        params = asdict(mv_params)
        params_orig = dict(params)
        params_orig["azimuth"] = _azimuth_deg_from_projection(proj_ctxs[i]["P_mv"]) \
            if not (perturbations and idx in perturbations) else None
        applied_perturb = None
        if perturbations and idx in perturbations:
            # params_orig should reflect the *unperturbed* camera for delta display.
            f0, rx0, rz0, h0 = params_from_projection_matrix(
                _load_projection_matrix(mv_yml, mv_key)
            )
            az0 = _azimuth_deg_from_projection(_load_projection_matrix(mv_yml, mv_key))
            params_orig.update(focal_length=f0, rotation_x=rx0, rotation_z=rz0,
                               mounting_height=h0, azimuth=az0)
            f, rx, rz, height = params_from_projection_matrix(proj_ctxs[i]["P_mv"])
            params["focal_length"] = f
            params["rotation_x"] = rx
            params["rotation_z"] = rz
            params["mounting_height"] = height
            params["azimuth"] = _azimuth_deg_from_projection(proj_ctxs[i]["P_mv"])
            params["source"] = f"{mv_params.source}+perturb"
            _, _, C_pert = _decompose_projection(proj_ctxs[i]["P_mv"])
            C = C_pert
            applied_perturb = perturbations[idx]
        else:
            params["azimuth"] = params_orig["azimuth"]
        cameras.append({
            "id": idx,
            "name": f"cam_{idx:02d}",
            "video": vid.name,
            "width": w,
            "height": h,
            "duration": dur,
            "fps": fps,
            "has_detections": det_src is not None,
            "has_kpts": cam_has_kpts,
            "has_expected_foot_y_map": foot_y_map is not None,
            "has_center_ground_xyz_map": ground_xyz_map is not None,
            "expected_foot_y_person_height": foot_map_height,
            "det_kind": det_src[1] if det_src is not None else None,
            "center_world": [float(C[0]), float(C[1]), float(C[2])],
            "params": params,
            "params_orig": params_orig,
            "perturbed": bool(perturbations and idx in perturbations),
            "perturb": applied_perturb,
            "P_mv": [float(x) for x in proj_ctxs[i]["P_mv"].ravel()],
            "ground_basis": _ground_basis_from_projection(proj_ctxs[i]["P_mv"]),
            "overlay_mv": mv_overlay,
        })
        logger.info(
            "  %s: %dx%d  grid=%d seg (MV)  kpts=%s  "
            "f=%.0f rx=%.1f° rz=%.1f° h=%.2fm",
            cameras[-1]["name"], w, h,
            len(mv_overlay["grid"]),
            "yes" if cam_has_kpts else "no",
            mv_params.focal_length, mv_params.rotation_x,
            mv_params.rotation_z, mv_params.mounting_height,
        )

    return {
        "project": output_folder.name,
        "mv_camparams": str(mv_dir),
        "bounds": {"x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max},
        "grid_spacing": grid_spacing,
        "person_height": person_height,
        "has_detections": any(p is not None for p in det_srcs),
        "has_kpts": any_kpts,
        "min_kpt_conf": min_kpt_conf,
        "foot_method": foot_method,
        "null_stale": null_stale,
        "det_min_conf": det_min_conf,
        "bbox_snap": bbox_snap,
        "virtual_object": virtual_object,
        "cylinder_radius": float(cylinder_radius),
        "extend_occluded": bool(extend_occluded),
        "kpt_gate_extend": bool(kpt_gate_extend),
        "kpt_gate_tol": float(kpt_gate_tol),
        "trace_secs": float(trace_secs),
        "perturbations": {
            str(k): v for k, v in (perturbations or {}).items()
        },
        "colors": {
            "mv_grid": _COLOR_MV_GRID,
            "bbox": _COLOR_BBOX,
            "recon_pole": _COLOR_RECON_POLE,
            "virtual_obj": _COLOR_VIRTUAL_OBJ,
            "extended_bbox": _COLOR_EXTENDED_BBOX,
            "trace": _COLOR_TRACE,
        },
        "bbox_opacity": 0.9,
        "grid_opacity": float(grid_opacity),
        "line_width": 2.0,
        "cameras": cameras,
        "videos": videos,
        "_proj": proj_ctxs,
        "camera_dims": camera_dims,
        "det_srcs": [[str(s[0]), s[1]] if s else None for s in det_srcs],
        "det_fps": det_fps,
        "expected_foot_y_maps": expected_foot_y_maps,
        "center_ground_xyz_maps": center_ground_xyz_maps,
    }


INDEX_HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Calib Wire Overlay Viewer</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family: system-ui, sans-serif; background:#121212; color:#eee; }
  header { padding:10px 14px; background:#1c1c1c; border-bottom:1px solid #333;
           display:flex; gap:10px; align-items:center; flex-wrap:wrap; position:sticky; top:0; z-index:20; }
  button, select { background:#2d6cdf; color:#fff; border:0; padding:8px 12px; border-radius:6px;
           cursor:pointer; font-size:14px; }
  button.secondary { background:#444; }
  button:disabled { opacity:.5; cursor:default; }
  label { font-size:13px; color:#bbb; display:flex; gap:6px; align-items:center; }
  #grid { display:grid; gap:8px; padding:8px; }
  .tile { background:#000; border:1px solid #333; border-radius:6px; overflow:hidden; position:relative; }
  .stage { position:relative; width:100%; aspect-ratio: var(--ar, 16/9); background:#000; }
  .stage video { width:100%; height:100%; display:block; object-fit:contain; background:#000; }
  .stage canvas { position:absolute; left:0; top:0; width:100%; height:100%; pointer-events:none; }
  .tilebar { padding:6px 8px; background:#1a1a1a; font-size:12px; color:#9cd; }
  .legend { font-size:12px; color:#aaa; padding:0 8px 8px; display:flex; gap:12px; flex-wrap:wrap; }
  .swatch { display:inline-block; width:12px; height:3px; vertical-align:middle; margin-right:4px; }
  #time { font-variant-numeric: tabular-nums; min-width:150px; }
  #meta { font-size:12px; color:#888; }
  input[type=range] { flex:1; min-width:200px; }
  #settings { padding:8px 14px; background:#181818; border-bottom:1px solid #333;
              display:flex; gap:12px 16px; align-items:center; flex-wrap:wrap; font-size:12px; }
  #settings label { color:#aaa; }
  #settings input[type=number] { width:72px; background:#222; color:#eee; border:1px solid #444;
                                 border-radius:4px; padding:4px 6px; }
  #settings input[type=range] { width:100px; min-width:80px; }
  #settings .val { color:#7cf; min-width:36px; font-variant-numeric:tabular-nums; }
  #settings_status { color:#888; font-size:11px; }
</style>
</head>
<body>
<header>
  <button id="playpause">▶ Play</button>
  <button id="record" class="secondary" title="Record the current tiled view (half resolution, with overlays) to a WebM file while it plays">● Record</button>
  <label>speed
    <select id="speed">
      <option value="1">1x</option>
      <option value="2">2x</option>
      <option value="4">4x</option>
    </select>
  </label>
  <label>view
    <select id="view_mode">
      <option value="all">All cameras</option>
      <option value="compare">Compare occlusion recovery</option>
    </select>
  </label>
  <label id="compare_cam_wrap" style="display:none">camera
    <select id="compare_cam"></select>
  </label>
  <button id="dump" class="secondary" title="Save the current tiled view (with overlays) as a PNG at half resolution">⤓ Save PNG</button>
  <input id="seek" type="range" min="0" max="1000" value="0" step="1">
  <span id="time">t = 0.00 / 0.00 s</span>
  <span id="meta"></span>
</header>
<div id="settings">
  <label>grid-spacing (m)
    <input type="number" id="grid_spacing" min="0.1" max="20" step="0.1">
  </label>
  <label>person-height (m)
    <input type="number" id="person_height" min="0.5" max="3" step="0.05">
  </label>
  <label id="grid_opacity_wrap">grid-opacity <span class="val" id="grid_opacity_val"></span>
    <input type="range" id="grid_opacity" min="0" max="1" step="0.01">
  </label>
  <label>line-width <span class="val" id="line_width_val"></span>
    <input type="range" id="line_width" min="0.5" max="8" step="0.5">
  </label>
  <label id="trace_secs_wrap">foot-trace (s)
    <input type="number" id="trace_secs" min="0" max="30" step="0.5"
           title="Fading trail of each person's reconstructed foot (object bottom) over the last N seconds; 0 = off">
  </label>
  <label id="bbox_snap_wrap">bbox-snap
    <select id="bbox_snap">
      <option value="center">center → Z=h/2 (bbox mid)</option>
      <option value="bottom">bottom → Z=0 (foot)</option>
      <option value="top">top → Z=h (head)</option>
    </select>
  </label>
  <label id="extend_occluded_wrap"
         title="center-snap add-on: for bottom-occluded people, back-project the bbox top-center to Z=person_height, drop to Z=0, and extend the bbox bottom to that expected foot before taking the center">
    <input type="checkbox" id="extend_occluded">
    extend occluded → foot
  </label>
  <label id="kpt_gate_extend_wrap"
         title="center-snap add-on (requires trajDump keypoints): gate geometry extension by pose evidence — skip when the lowest trusted ankle already reaches near the geometry-expected foot; extend when it sits well above (occlusion/truncation). WARNING: per-frame ankle keypoints are noisy, so the recovered (cyan) box jitters more than pure geometry — a trade-off for occlusion awareness. Lower gate-tol toward 0 to converge to the checkbox-off (pure-geometry) behavior and stabilize the box; higher gate-tol suppresses more extensions and flickers most near the threshold.">
    <input type="checkbox" id="kpt_gate_extend">
    gate extension with keypoints
  </label>
  <label id="kpt_gate_tol_wrap">gate-tol <span class="val" id="kpt_gate_tol_val"></span>
    <input type="range" id="kpt_gate_tol" min="0" max="0.6" step="0.01"
           title="Tolerance as fraction of bbox height: skip extension iff lowest ankle y >= expected_foot_y - tol*bbox_h (larger => extend less)">
  </label>
  <label id="virtual_object_wrap">object
    <select id="virtual_object">
      <option value="pole">pole</option>
      <option value="cylinder">cylinder</option>
      <option value="box">3D box</option>
    </select>
  </label>
  <label id="cyl_radius_wrap">radius (m) <span class="val" id="cylinder_radius_val"></span>
    <input type="range" id="cylinder_radius" min="0.05" max="2" step="0.05">
  </label>
  <label id="bbox_opacity_wrap">bbox-opacity <span class="val" id="bbox_opacity_val"></span>
    <input type="range" id="bbox_opacity" min="0" max="1" step="0.01">
  </label>
  <span id="settings_status"></span>
</div>
<div class="legend" id="legend"></div>
<div id="grid"></div>
<script>
let CFG = null;
let VIDS = [];
let playing = false;
let commonLen = 0;
let lastDriftCheck = 0;
let geomTimer = null;
let detTimer = null;
let viewMode = 'all';   // 'all' = every camera; 'compare' = one camera, w/ vs w/o occlusion recovery
let compareCam = 0;     // camera index shown in compare mode

function fmt(x){ return x.toFixed(2); }

// ---- Reconstruction path: back-project bbox anchor to a Z=z0 plane, then
// project the vertical (X,Y,0)-(X,Y,h) segment through the same (perturbed) P.
function mat3Inv(m){
  // m is row-major length-9
  const a=m[0],b=m[1],c=m[2],d=m[3],e=m[4],f=m[5],g=m[6],h=m[7],i=m[8];
  const A=e*i-f*h, B=-(d*i-f*g), C=d*h-e*g;
  const det=a*A+b*B+c*C;
  if (Math.abs(det) < 1e-12) return null;
  const id=1/det;
  return [
    A*id, (c*h-b*i)*id, (b*f-c*e)*id,
    B*id, (a*i-c*g)*id, (c*d-a*f)*id,
    C*id, (b*g-a*h)*id, (a*e-b*d)*id,
  ];
}
function mat3Vec(m, v){
  return [m[0]*v[0]+m[1]*v[1]+m[2]*v[2],
          m[3]*v[0]+m[4]*v[1]+m[5]*v[2],
          m[6]*v[0]+m[7]*v[1]+m[8]*v[2]];
}
// P is 3x4 row-major length-12. Homography for plane Z=z0: cols [P0, P1, P2*z0+P3].
function planeHomographyInv(P, z0){
  const H = [
    P[0], P[1], P[2]*z0 + P[3],
    P[4], P[5], P[6]*z0 + P[7],
    P[8], P[9], P[10]*z0 + P[11],
  ];
  return mat3Inv(H);
}
// Project a world point (X,Y,Z) via P; returns [u,v] or null if behind camera.
function projectWorld(P, X, Y, Z){
  const x = P[0]*X + P[1]*Y + P[2]*Z + P[3];
  const y = P[4]*X + P[5]*Y + P[6]*Z + P[7];
  const wv= P[8]*X + P[9]*Y + P[10]*Z + P[11];
  if (wv <= 1e-6) return null;
  return [x/wv, y/wv];
}
// For one anchor pixel, back-project to the anchor plane and return the world
// floor footprint (X,Y). snap='bottom' -> Z=0 (foot); snap='top' -> Z=h (head);
// snap='center' -> Z=h/2 (bbox mid). The virtual object always spans Z=0..h.
// When a center-snap ground XYZ lookup map is loaded and person-height matches,
// center snap samples that map instead of inverting a plane homography.
function reconstructGround(P, u, t, snap, h, groundMap){
  if (snap === 'center' && groundMap
      && Math.abs(h - groundMap.personHeight) < 1e-9){
    const g = sampleGroundXYZ(groundMap, u, t);
    if (g) return g;
  }
  const z0 = (snap === 'top') ? h : (snap === 'center') ? h * 0.5 : 0.0;
  const Hinv = planeHomographyInv(P, z0);
  if (!Hinv) return null;
  const w = mat3Vec(Hinv, [u, t, 1.0]);
  if (Math.abs(w[2]) < 1e-9) return null;
  const X = w[0]/w[2], Y = w[1]/w[2];
  if (!isFinite(X) || !isFinite(Y)) return null;
  const b = CFG.bounds;
  const pad = Math.max(b.x_max-b.x_min, b.y_max-b.y_min);   // skip wild grazing rays
  if (X < b.x_min-pad || X > b.x_max+pad || Y < b.y_min-pad || Y > b.y_max+pad) return null;
  return {X, Y};
}
// Bilinear sample of the center-snap ground XYZ map (channels: X,Y,Z=0).
function sampleGroundXYZ(groundMap, x, y){
  if (x < 0 || y < 0 || x > groundMap.width-1 || y > groundMap.height-1) return null;
  const x0 = Math.floor(x), y0 = Math.floor(y);
  const x1 = Math.min(x0 + 1, groundMap.width - 1);
  const y1 = Math.min(y0 + 1, groundMap.height - 1);
  const fx = x - x0, fy = y - y0;
  const w = groundMap.width;
  const idx = (yy, xx) => (yy * w + xx) * 3;
  const i00 = idx(y0, x0), i10 = idx(y0, x1), i01 = idx(y1, x0), i11 = idx(y1, x1);
  const d = groundMap.data;
  const Xa = d[i00], Ya = d[i00+1];
  const Xb = d[i10], Yb = d[i10+1];
  const Xc = d[i01], Yc = d[i01+1];
  const Xd = d[i11], Yd = d[i11+1];
  if (![Xa,Ya,Xb,Yb,Xc,Yc,Xd,Yd].every(Number.isFinite)) return null;
  const X = (1-fy)*((1-fx)*Xa+fx*Xb) + fy*((1-fx)*Xc+fx*Xd);
  const Y = (1-fy)*((1-fx)*Ya+fx*Yb) + fy*((1-fx)*Yc+fx*Yd);
  return {X, Y};
}
// --- Virtual-object builders: each returns a list of 2D segments [x0,y0,x1,y1].
// A point behind the camera drops just the segments that touch it.
function buildPole(P, X, Y, h){
  const p0 = projectWorld(P, X, Y, 0.0);
  const p1 = projectWorld(P, X, Y, h);
  if (!p0 || !p1) return [];
  return [[p0[0], p0[1], p1[0], p1[1]]];
}
function buildCylinder(P, X, Y, h, r, camCenter){
  // Sample the circle in 3D and project each point; the projection yields the
  // correct ellipse without any analytic conic math. Only the top/bottom
  // circles and the two silhouette (outer) lines are drawn — no vertical seams.
  const N = 32;
  const bottom = [], top = [];
  for (let i = 0; i < N; i++){
    const a = 2*Math.PI*i/N;
    const wx = X + r*Math.cos(a), wy = Y + r*Math.sin(a);
    bottom.push(projectWorld(P, wx, wy, 0.0));
    top.push(projectWorld(P, wx, wy, h));
  }
  const segs = [];
  for (let i = 0; i < N; i++){
    const j = (i+1) % N;
    if (bottom[i] && bottom[j]) segs.push([bottom[i][0],bottom[i][1],bottom[j][0],bottom[j][1]]);
    if (top[i] && top[j])       segs.push([top[i][0],top[i][1],top[j][0],top[j][1]]);
  }
  // Silhouette generators: the two vertical lines tangent to the tube, at the
  // ground points offset perpendicular to the camera→centre direction.
  if (camCenter){
    let dx = X - camCenter[0], dy = Y - camCenter[1];
    const len = Math.hypot(dx, dy);
    if (len > 1e-6){
      const px = -dy/len, py = dx/len;   // unit perpendicular on the ground
      for (const s of [1, -1]){
        const gx = X + s*r*px, gy = Y + s*r*py;
        const b = projectWorld(P, gx, gy, 0.0);
        const t = projectWorld(P, gx, gy, h);
        if (b && t) segs.push([b[0], b[1], t[0], t[1]]);
      }
    }
  }
  return segs;
}
function buildBox(P, X, Y, h, r, basis){
  // Square footprint of half-side r, oriented by the per-camera ground basis
  // (box x = camera image-right on the floor). 8 corners, 12 edges.
  const ux = basis[0], uy = basis[1], vx = basis[2], vy = basis[3];
  const signs = [[+1,+1],[+1,-1],[-1,-1],[-1,+1]];
  const foot = [], head = [];
  for (const [su, sv] of signs){
    const wx = X + su*r*ux + sv*r*vx;
    const wy = Y + su*r*uy + sv*r*vy;
    foot.push(projectWorld(P, wx, wy, 0.0));
    head.push(projectWorld(P, wx, wy, h));
  }
  const segs = [];
  for (let i = 0; i < 4; i++){
    const j = (i+1) % 4;
    if (foot[i] && foot[j]) segs.push([foot[i][0],foot[i][1],foot[j][0],foot[j][1]]);
    if (head[i] && head[j]) segs.push([head[i][0],head[i][1],head[j][0],head[j][1]]);
    if (foot[i] && head[i]) segs.push([foot[i][0],foot[i][1],head[i][0],head[i][1]]);
  }
  return segs;
}
// Expected image-y of the foot (Z=0) for a detection, assuming the bbox TOP
// edge center is the head at Z=person_height. Back-project top-center to the
// Z=h plane -> head world (X,Y,h); drop to (X,Y,0); reproject. Returns the
// image y of that expected foot, or null if the geometry is degenerate.
// Used by the "extend occluded" center-snap add-on to recover a plausible foot
// for people whose lower body is occluded / cut off by the frame edge.
function expectedFootY(P, d, h, footMap){
  const topc = [d.b[0] + d.b[2]*0.5, d.b[1]];
  // Fast path: bilinear lookup in the precomputed dense map. A map is valid
  // only for the person height recorded when it was generated.
  if (footMap && Math.abs(h - footMap.personHeight) < 1e-9){
    const x = topc[0], y = topc[1];
    if (x >= 0 && y >= 0 && x <= footMap.width-1 && y <= footMap.height-1){
      const x0 = Math.floor(x), y0 = Math.floor(y);
      const x1 = Math.min(x0 + 1, footMap.width - 1);
      const y1 = Math.min(y0 + 1, footMap.height - 1);
      const fx = x - x0, fy = y - y0;
      const a = footMap.data[y0*footMap.width+x0];
      const b = footMap.data[y0*footMap.width+x1];
      const c = footMap.data[y1*footMap.width+x0];
      const e = footMap.data[y1*footMap.width+x1];
      if ([a,b,c,e].every(Number.isFinite)){
        return (1-fy)*((1-fx)*a+fx*b) + fy*((1-fx)*c+fx*e);
      }
    }
  }
  const gHead = reconstructGround(P, topc[0], topc[1], 'top', h);  // Z=h plane
  if (!gHead) return null;
  const foot = projectWorld(P, gHead.X, gHead.Y, 0.0);
  if (!foot) return null;
  return foot[1];
}
// Decide whether geometry-based downward bbox extension should run, given the
// geometry-expected foot image-y ``efy``. When keypoint gating is on and a
// trusted ankle exists (``d.gy``), extend only if the pose foot sits well ABOVE
// the expected foot (ankle y < efy - tol*bbox_h) — i.e. the visible body does
// NOT reach full height, evidence of occlusion/truncation. If the ankle already
// reaches near the expected foot, the box is trustworthy → skip extension.
// BodyPose3DNet hallucinates occluded legs *inside* the box with high
// confidence, so a bbox-containment test cannot detect occlusion; comparing the
// pose foot against the geometry foot can. No trusted ankle → trust geometry.
function allowOcclusionExtend(d, extend, kptGate, efy, tol){
  if (!extend || efy === null) return false;
  if (!kptGate) return true;
  if (d.gy === null || d.gy === undefined) return true;
  const bh = d.b[3] || 1;
  return d.gy < efy - tol * bh;
}
// Anchor pixel for a detection given snap mode:
//   top    -> head        (keypoint nose if available, else bbox top-center)
//   bottom -> foot         (keypoint foot-center if available, else bbox bottom-center)
//   center -> bbox center  (dead center of the 2D box; no keypoint override)
// Keypoint anchors (d.f / d.h) override bbox geometry whenever present.
// `extend` (center snap only): if the geometry-expected foot lands BELOW the
// bbox bottom (bottom-occluded person), extend the box bottom down to it and
// take the center of that expanded box. Returns `extBottom` = expanded bottom
// image-y when an extension was applied (for drawing), else null.
function detAnchor(d, snap, P, h, extend, kptGate, tol, footMap){
  if (snap === 'center'){
    let bottomY = d.b[1] + d.b[3];
    let extBottom = null;
    if (extend){
      const efy = expectedFootY(P, d, h, footMap);
      // Only ever extend DOWNWARD (occluded/cut-off bottom); never shrink.
      if (efy !== null && efy > bottomY
          && allowOcclusionExtend(d, extend, kptGate, efy, tol)){
        bottomY = efy; extBottom = efy;
      }
    }
    const cx = d.b[0] + d.b[2]*0.5;
    const cy = (d.b[1] + bottomY) * 0.5;
    return {pt: [cx, cy], kp: false, extBottom};
  }
  if (snap === 'top'){
    if (d.h) return {pt: d.h, kp: !!d.k};
    return {pt: [d.b[0] + d.b[2]*0.5, d.b[1]], kp: false};
  }
  if (d.f) return {pt: d.f, kp: !!d.k};
  return {pt: [d.b[0] + d.b[2]*0.5, d.b[1] + d.b[3]], kp: false};
}
// Returns [{segs:[[x0,y0,x1,y1],...], anchor:[u,t], kp:bool}, ...] for the
// currently selected virtual object (pole | cylinder | box).
function reconstructObjects(cam, dets, snap, extendFlag){
  const out = [];
  if (!cam.P_mv || !dets || !dets.length) return out;
  const h = CFG.person_height;
  const obj = CFG.virtual_object || 'pole';
  const r = CFG.cylinder_radius ?? 0.3;
  // "extend occluded" is a center-snap-only add-on. `extendFlag` lets a single
  // tile override the global toggle (used by the compare view).
  const extend = !!extendFlag && snap === 'center';
  const kptGate = !!CFG.kpt_gate_extend;
  const tol = CFG.kpt_gate_tol ?? 0.2;
  for (const d of dets){
    const a = detAnchor(d, snap, cam.P_mv, h, extend, kptGate, tol, cam.footYMap);
    const g = reconstructGround(cam.P_mv, a.pt[0], a.pt[1], snap, h, cam.groundXYZMap);
    if (!g) continue;
    let segs;
    if (obj === 'cylinder') segs = buildCylinder(cam.P_mv, g.X, g.Y, h, r, cam.center_world);
    else if (obj === 'box')  segs = buildBox(cam.P_mv, g.X, g.Y, h, r, cam.ground_basis);
    else                     segs = buildPole(cam.P_mv, g.X, g.Y, h);
    if (!segs || !segs.length) continue;
    // Expanded axis endpoints (reconstructed foot Z=0 and head Z=h) in image px.
    const foot = projectWorld(cam.P_mv, g.X, g.Y, 0.0);
    const head = projectWorld(cam.P_mv, g.X, g.Y, h);
    out.push({segs, anchor: a.pt, kp: a.kp, foot, head,
              extBottom: a.extBottom, box: d.b});
  }
  return out;
}

// Reconstructed foot (object bottom, Z=0) pixel for one detection, honoring the
// same snap / extend logic as the virtual object. Used for the foot trace.
function detFoot(cam, d, snap, extend, kptGate, tol, h){
  const a = detAnchor(d, snap, cam.P_mv, h, extend, kptGate, tol, cam.footYMap);
  const g = reconstructGround(cam.P_mv, a.pt[0], a.pt[1], snap, h, cam.groundXYZMap);
  if (!g) return null;
  return projectWorld(cam.P_mv, g.X, g.Y, 0.0);
}

// Draw a fading per-person trail of foot locations over the last `traceSecs`
// seconds. Points are grouped by track id (when available) into polylines;
// sources without ids fall back to plain dots.
function drawFootTrace(ctx, v, snap, extend, kptGate, tol, lw){
  const traceSecs = CFG.trace_secs ?? 0;
  if (!(traceSecs > 0) || !v.dets) return;
  const cam = v.cam;
  const h = CFG.person_height;
  const fps = v.dets.fps || v.cam.fps || 30;
  const curFrame = Math.max(0, Math.round(v.el.currentTime * fps));
  const startFrame = Math.max(0, curFrame - Math.round(traceSecs * fps));
  const span = Math.max(1, curFrame - startFrame);
  // id -> [{f, uv}] ordered by frame; null-id samples collected as loose dots.
  const tracks = new Map();
  const loose = [];
  for (let f = startFrame; f <= curFrame; f++){
    const boxes = v.dets.frames[String(f)];
    if (!boxes) continue;
    for (const d of boxes){
      const uv = detFoot(cam, d, snap, extend, kptGate, tol, h);
      if (!uv) continue;
      if (d.id === undefined || d.id === null){
        loose.push({f, uv});
      } else {
        if (!tracks.has(d.id)) tracks.set(d.id, []);
        tracks.get(d.id).push({f, uv});
      }
    }
  }
  ctx.save();
  ctx.strokeStyle = CFG.colors.trace;
  ctx.fillStyle = CFG.colors.trace;
  ctx.lineWidth = lw;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  // Per-track fading polylines: newer segments more opaque.
  for (const pts of tracks.values()){
    for (let i = 1; i < pts.length; i++){
      const a = pts[i - 1], b = pts[i];
      if (b.f - a.f > fps * 0.5) continue;  // break long gaps (missed frames)
      const alpha = 0.12 + 0.78 * ((b.f - startFrame) / span);
      ctx.globalAlpha = alpha;
      ctx.beginPath(); ctx.moveTo(a.uv[0], a.uv[1]); ctx.lineTo(b.uv[0], b.uv[1]); ctx.stroke();
    }
    // Small dot at the newest sample.
    if (pts.length){
      const last = pts[pts.length - 1];
      ctx.globalAlpha = 0.9;
      ctx.beginPath(); ctx.arc(last.uv[0], last.uv[1], lw + 1, 0, 2*Math.PI); ctx.fill();
    }
  }
  for (const p of loose){
    ctx.globalAlpha = 0.12 + 0.78 * ((p.f - startFrame) / span);
    ctx.beginPath(); ctx.arc(p.uv[0], p.uv[1], lw, 0, 2*Math.PI); ctx.fill();
  }
  ctx.restore();
}

function currentBboxSnap(){
  return CFG.bbox_snap || document.getElementById('bbox_snap').value || 'center';
}

function updateMeta(){
  if (!CFG) return;
  const b = CFG.bounds;
  document.getElementById('meta').textContent =
    `grid x∈[${b.x_min},${b.x_max}] y∈[${b.y_min},${b.y_max}]  spacing=${CFG.grid_spacing}m  h=${CFG.person_height}m`;
}

function syncSettingsFromCfg(){
  if (!CFG) return;
  document.getElementById('grid_spacing').value = CFG.grid_spacing;
  document.getElementById('person_height').value = CFG.person_height;
  document.getElementById('grid_opacity').value = CFG.grid_opacity ?? 0.35;
  document.getElementById('grid_opacity_val').textContent = (CFG.grid_opacity ?? 0.35).toFixed(2);
  document.getElementById('line_width').value = CFG.line_width ?? 2.0;
  document.getElementById('line_width_val').textContent = (CFG.line_width ?? 2.0).toFixed(1);
  document.getElementById('trace_secs').value = CFG.trace_secs ?? 2.0;
  document.getElementById('bbox_opacity').value = CFG.bbox_opacity ?? 0.9;
  document.getElementById('bbox_opacity_val').textContent = (CFG.bbox_opacity ?? 0.9).toFixed(2);
  document.getElementById('bbox_snap').value = CFG.bbox_snap || 'center';
  document.getElementById('extend_occluded').checked = !!CFG.extend_occluded;
  document.getElementById('kpt_gate_extend').checked = !!CFG.kpt_gate_extend;
  document.getElementById('kpt_gate_tol').value = CFG.kpt_gate_tol ?? 0.2;
  document.getElementById('kpt_gate_tol_val').textContent = (CFG.kpt_gate_tol ?? 0.2).toFixed(2);
  document.getElementById('virtual_object').value = CFG.virtual_object || 'pole';
  document.getElementById('cylinder_radius').value = CFG.cylinder_radius ?? 0.3;
  document.getElementById('cylinder_radius_val').textContent = (CFG.cylinder_radius ?? 0.3).toFixed(2);
  // Detection-dependent controls hidden when there is no detection source.
  const det = !!CFG.has_detections;
  for (const id of ['trace_secs_wrap', 'bbox_snap_wrap', 'bbox_opacity_wrap',
                    'virtual_object_wrap', 'cyl_radius_wrap', 'extend_occluded_wrap']){
    document.getElementById(id).style.display = det ? '' : 'none';
  }
  document.getElementById('kpt_gate_extend_wrap').style.display =
      (det && !!(CFG.has_kpts)) ? '' : 'none';
  updateObjControls();
  updateSnapControls();
  updateMeta();
}

// Radius only matters for cylinder/box; hide it for the plain pole.
function updateObjControls(){
  const obj = document.getElementById('virtual_object').value;
  const show = !!(CFG && CFG.has_detections) && obj !== 'pole';
  document.getElementById('cyl_radius_wrap').style.display = show ? '' : 'none';
}

// "extend occluded" is an add-on to center snap only; hide it otherwise.
function updateSnapControls(){
  const snap = document.getElementById('bbox_snap').value;
  const show = !!(CFG && CFG.has_detections) && snap === 'center';
  document.getElementById('extend_occluded_wrap').style.display = show ? '' : 'none';
  const kptGateShow = show && !!(CFG && CFG.has_kpts);
  document.getElementById('kpt_gate_extend_wrap').style.display = kptGateShow ? '' : 'none';
  // gate-tol slider only when the keypoint gate itself is available and enabled.
  const tolShow = kptGateShow && document.getElementById('kpt_gate_extend').checked;
  document.getElementById('kpt_gate_tol_wrap').style.display = tolShow ? '' : 'none';
}

function readClientParams(){
  CFG.grid_opacity = parseFloat(document.getElementById('grid_opacity').value);
  CFG.line_width = parseFloat(document.getElementById('line_width').value);
  CFG.trace_secs = parseFloat(document.getElementById('trace_secs').value) || 0;
  CFG.bbox_opacity = parseFloat(document.getElementById('bbox_opacity').value);
  CFG.bbox_snap = document.getElementById('bbox_snap').value;
  CFG.extend_occluded = document.getElementById('extend_occluded').checked;
  CFG.kpt_gate_extend = document.getElementById('kpt_gate_extend').checked;
  CFG.kpt_gate_tol = parseFloat(document.getElementById('kpt_gate_tol').value);
  CFG.virtual_object = document.getElementById('virtual_object').value;
  CFG.cylinder_radius = parseFloat(document.getElementById('cylinder_radius').value);
  document.getElementById('grid_opacity_val').textContent = CFG.grid_opacity.toFixed(2);
  document.getElementById('line_width_val').textContent = CFG.line_width.toFixed(1);
  document.getElementById('bbox_opacity_val').textContent = CFG.bbox_opacity.toFixed(2);
  document.getElementById('kpt_gate_tol_val').textContent = CFG.kpt_gate_tol.toFixed(2);
  document.getElementById('cylinder_radius_val').textContent = CFG.cylinder_radius.toFixed(2);
}

async function applyGeometryParams(){
  const gs = parseFloat(document.getElementById('grid_spacing').value);
  const ph = parseFloat(document.getElementById('person_height').value);
  if (!(gs > 0) || !(ph > 0)) return;
  if (gs === CFG.grid_spacing && ph === CFG.person_height) return;
  const status = document.getElementById('settings_status');
  status.textContent = 'reprojecting…';
  try {
    const q = new URLSearchParams({grid_spacing: String(gs), person_height: String(ph)});
    const resp = await fetch('overlays?' + q);
    if (!resp.ok) throw new Error('overlays failed');
    const data = await resp.json();
    CFG.grid_spacing = data.grid_spacing;
    CFG.person_height = data.person_height;
    // Update per-camera overlays by camera index; VIDS[*].cam are references
    // into CFG.cameras, so tiles (incl. duplicated compare tiles) pick these up.
    for (let i = 0; i < CFG.cameras.length; i++){
      CFG.cameras[i].overlay_mv = data.overlays[i].overlay_mv;
    }
    updateMeta();
    status.textContent = '';
    refreshOverlays();
  } catch (e){
    status.textContent = 'reproject failed';
  }
}

function scheduleGeometry(){
  clearTimeout(geomTimer);
  geomTimer = setTimeout(() => applyGeometryParams(), 400);
}

function clearAllDets(){
  for (const v of VIDS) v.dets = null;
}

async function reloadAllDets(){
  clearAllDets();
  for (const v of VIDS) await ensureDets(v, v.idx);
  refreshOverlays();
}

function drawOverlay(v){
  const cam = v.cam;
  const ctx = v.canvas.getContext('2d');
  const w = v.canvas.width, h = v.canvas.height;
  ctx.clearRect(0,0,w,h);
  const hasDets = !!v.dets;
  let detBoxes = null;
  if (hasDets){
    const fps = v.dets.fps || v.cam.fps || 30;
    const frame = Math.max(0, Math.round(v.el.currentTime * fps));
    detBoxes = v.dets.frames[String(frame)] || [];
  }
  // Floor grid (MV), dimmed via grid-opacity.
  const gop = (CFG.grid_opacity ?? 0.35);
  const lw = (CFG.line_width ?? 2.0);
  const grid = cam.overlay_mv;
  if (grid && gop > 0){
    ctx.lineWidth = lw;
    ctx.strokeStyle = CFG.colors.mv_grid;
    ctx.globalAlpha = gop;
    for (const s of grid.grid){
      ctx.beginPath(); ctx.moveTo(s[0],s[1]); ctx.lineTo(s[2],s[3]); ctx.stroke();
    }
    ctx.globalAlpha = 1.0;
  }
  // Reconstructed per-person virtual objects (MV back-projection of the anchor).
  // pole -> red; cylinder / 3D box -> magenta.
  // Snap + per-tile extend override (compare view) are shared by the trace and
  // the current-frame object reconstruction.
  const snap = currentBboxSnap();
  const extend = (v.extendOverride != null) ? v.extendOverride : !!CFG.extend_occluded;
  const kptGate = !!CFG.kpt_gate_extend;
  const tol = CFG.kpt_gate_tol ?? 0.2;
  // Foot-location trail (drawn under the current-frame objects).
  drawFootTrace(ctx, v, snap, extend, kptGate, tol, lw);
  let objs = null;
  if (detBoxes && detBoxes.length){
    objs = reconstructObjects(cam, detBoxes, snap, extend);
    const objColor = (CFG.virtual_object && CFG.virtual_object !== 'pole')
        ? CFG.colors.virtual_obj : CFG.colors.recon_pole;
    ctx.save();
    ctx.strokeStyle = objColor;
    ctx.fillStyle = objColor;
    ctx.lineWidth = lw + 1;
    for (const o of objs){
      ctx.globalAlpha = 1.0;
      for (const s of o.segs){
        ctx.beginPath(); ctx.moveTo(s[0],s[1]); ctx.lineTo(s[2],s[3]); ctx.stroke();
      }
      // Reconstructed axis endpoints: foot (Z=0) and head (Z=h) as filled dots.
      for (const p of [o.foot, o.head]){
        if (!p) continue;
        ctx.beginPath(); ctx.arc(p[0], p[1], lw + 2, 0, 2*Math.PI); ctx.fill();
      }
      // Anchor marker: filled dot when keypoint-derived, hollow when bbox.
      ctx.beginPath(); ctx.arc(o.anchor[0], o.anchor[1], lw + 2, 0, 2*Math.PI);
      if (o.kp) ctx.fill(); else ctx.stroke();
    }
    ctx.restore();
  }
  // Raw 2D detection boxes + (when applicable) the cyan recovered/extended box.
  // Both share the bbox-opacity slider and the same line width.
  if (detBoxes && detBoxes.length){
    const op = (CFG.bbox_opacity ?? 0.9);
    if (op > 0){
      ctx.save();
      ctx.globalAlpha = op;
      ctx.lineWidth = lw;
      // Original detector boxes (yellow, solid).
      ctx.strokeStyle = CFG.colors.bbox;
      for (const d of detBoxes){
        const b = d.b;
        ctx.strokeRect(b[0], b[1], b[2], b[3]);
      }
      // Recovered/extended boxes (cyan, dashed) for bottom-occluded people.
      if (objs){
        ctx.strokeStyle = CFG.colors.extended_bbox;
        ctx.setLineDash([6, 5]);
        for (const o of objs){
          if (o.extBottom != null && o.box){
            ctx.strokeRect(o.box[0], o.box[1], o.box[2], o.extBottom - o.box[1]);
          }
        }
        ctx.setLineDash([]);
      }
      ctx.restore();
    }
  }
  if (cam.params) {
    const p = cam.params;
    const fmtSigned = (x, d=2) => (x >= 0 ? '+' : '') + x.toFixed(d);
    const lines = [
      cam.name + (cam.perturbed ? '  [perturbed]' : ''),
      `f = ${p.focal_length.toFixed(1)} px`,
      `tilt (rx) = ${p.rotation_x.toFixed(2)}°`,
      `roll (rz) = ${p.rotation_z.toFixed(2)}°`,
      `azim = ${(p.azimuth ?? 0).toFixed(2)}°`,
      `height = ${p.mounting_height.toFixed(2)} m`,
    ];
    if (cam.perturbed && cam.perturb){
      const d = cam.perturb, o = cam.params_orig || {};
      lines.push('— applied Δ —');
      lines.push(`Δpos = (${fmtSigned(d.dx,2)}, ${fmtSigned(d.dy,2)}, ${fmtSigned(d.dz,2)}) m`);
      lines.push(`Δrot = (${fmtSigned(d.drx,1)}, ${fmtSigned(d.dry,1)}, ${fmtSigned(d.drz,1)})°`);
      const dAz = (p.azimuth ?? 0) - (o.azimuth ?? 0);
      const dTilt = p.rotation_x - (o.rotation_x ?? p.rotation_x);
      const dRoll = p.rotation_z - (o.rotation_z ?? p.rotation_z);
      lines.push(`eff Δ: tilt ${fmtSigned(dTilt,2)}° roll ${fmtSigned(dRoll,2)}° azim ${fmtSigned(dAz,2)}°`);
    }
    ctx.save();
    ctx.globalAlpha = 1.0;
    ctx.font = '30px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textBaseline = 'top';
    let y = 14;
    for (const line of lines) {
      ctx.strokeStyle = '#000';
      ctx.lineWidth = 7;
      ctx.strokeText(line, 14, y);
      ctx.fillStyle = '#fff';
      ctx.fillText(line, 14, y);
      y += 38;
    }
    ctx.restore();
  }
}

function refreshOverlays(){
  VIDS.forEach(v => drawOverlay(v));
}

// Grid layout for a composite of all tiles, each scaled by `scale` (0.5 = half
// resolution). Uniform cells sized to the largest camera so tiles stay aligned.
function compositeLayout(scale){
  const n = VIDS.length;
  const cols = Math.ceil(Math.sqrt(n));
  const rows = Math.ceil(n / cols);
  const gap = 4;
  let maxW = 0, maxH = 0;
  for (const v of VIDS){ maxW = Math.max(maxW, v.cam.width); maxH = Math.max(maxH, v.cam.height); }
  const cellW = Math.round(maxW * scale), cellH = Math.round(maxH * scale);
  return {n, cols, rows, gap, cellW, cellH, scale,
          W: cols * cellW + (cols + 1) * gap,
          H: rows * cellH + (rows + 1) * gap};
}
// Draw the current video frame + overlay canvas for every tile into `c`.
function drawComposite(c, lay){
  c.fillStyle = '#121212'; c.fillRect(0, 0, lay.W, lay.H);
  for (let i = 0; i < lay.n; i++){
    const v = VIDS[i];
    const col = i % lay.cols, row = Math.floor(i / lay.cols);
    const x = lay.gap + col * (lay.cellW + lay.gap);
    const y = lay.gap + row * (lay.cellH + lay.gap);
    const w = Math.round(v.cam.width * lay.scale), h = Math.round(v.cam.height * lay.scale);
    try { c.drawImage(v.el, x, y, w, h); } catch (e) { /* frame not ready */ }
    c.drawImage(v.canvas, x, y, w, h);
  }
}

// Composite the current tiled view (video frame + overlay canvas per camera)
// into a single PNG at half the original resolution, laid out like the grid,
// and trigger a download. Overlays are redrawn first so the dump matches the
// current frame exactly.
function dumpTiles(){
  if (!VIDS.length) return;
  refreshOverlays();
  const lay = compositeLayout(0.5);
  const out = document.createElement('canvas');
  out.width = lay.W; out.height = lay.H;
  drawComposite(out.getContext('2d'), lay);
  out.toBlob((blob) => {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const t = (VIDS[0] ? VIDS[0].el.currentTime : 0).toFixed(2);
    a.href = url;
    a.download = `${CFG && CFG.project ? CFG.project : 'calib'}_overlay_t${t}s.png`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, 'image/png');
}

// ---- Recording: capture the composite (half-res, with overlays) to WebM.
let recording = false;
let mediaRecorder = null;
let recordChunks = [];
let recordCanvas = null;
let recordRAF = 0;

function pickRecordMime(){
  const cands = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'];
  for (const m of cands){
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(m)) return m;
  }
  return '';
}
function syncRecordBtn(){
  const btn = document.getElementById('record');
  if (!btn) return;
  btn.textContent = recording ? '■ Stop' : '● Record';
  btn.style.background = recording ? '#c0392b' : '';
}
function startRecording(){
  if (recording || !VIDS.length) return;
  if (!window.MediaRecorder){ alert('MediaRecorder is not supported in this browser.'); return; }
  // Layout is fixed for the whole recording (stream dimensions can't change).
  const lay = compositeLayout(0.5);
  recordCanvas = document.createElement('canvas');
  recordCanvas.width = lay.W; recordCanvas.height = lay.H;
  const rctx = recordCanvas.getContext('2d');
  drawComposite(rctx, lay);   // prime one frame before capture
  const stream = recordCanvas.captureStream(30);
  const mime = pickRecordMime();
  try {
    mediaRecorder = new MediaRecorder(stream, mime ? {mimeType: mime} : undefined);
  } catch (e){ alert('Could not start recorder: ' + e); return; }
  recordChunks = [];
  mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size) recordChunks.push(e.data); };
  mediaRecorder.onstop = () => {
    const blob = new Blob(recordChunks, {type: mediaRecorder.mimeType || 'video/webm'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const ts = new Date().toISOString().replace(/[:.]/g, '-');
    a.href = url;
    a.download = `${CFG && CFG.project ? CFG.project : 'calib'}_record_${ts}.webm`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  recording = true;
  mediaRecorder.start(200);   // flush chunks periodically
  const loop = () => {
    if (!recording) return;
    drawComposite(rctx, lay);
    recordRAF = requestAnimationFrame(loop);
  };
  recordRAF = requestAnimationFrame(loop);
  syncRecordBtn();
}
function stopRecording(){
  if (!recording) return;
  recording = false;
  cancelAnimationFrame(recordRAF);
  try { mediaRecorder.stop(); } catch (e){ /* already stopped */ }
  syncRecordBtn();
}
function toggleRecord(){ if (recording) stopRecording(); else startRecording(); }

async function ensureDets(v, idx){
  if (v.dets || !v.cam.has_detections) return;
  const mc = CFG.det_min_conf ?? 0;
  v.dets = await (await fetch(`detections?i=${idx}&min_conf=${mc}`)).json();
}

async function ensureFootYMap(cam, idx){
  if (cam.footYMap || !cam.has_expected_foot_y_map) return;
  const resp = await fetch(`expected-foot-y-map?i=${idx}`);
  if (!resp.ok) return;
  const buf = await resp.arrayBuffer();
  if (buf.byteLength !== cam.width * cam.height * 4) return;
  cam.footYMap = {
    data: new Float32Array(buf),
    width: cam.width,
    height: cam.height,
    personHeight: cam.expected_foot_y_person_height,
  };
}

async function ensureGroundXYZMap(cam, idx){
  // Center-snap only: dense (X,Y,0) lookup for bbox mid → waist plane.
  if (cam.groundXYZMap || !cam.has_center_ground_xyz_map) return;
  const resp = await fetch(`center-ground-xyz-map?i=${idx}`);
  if (!resp.ok) return;
  const buf = await resp.arrayBuffer();
  if (buf.byteLength !== cam.width * cam.height * 3 * 4) return;
  cam.groundXYZMap = {
    data: new Float32Array(buf),
    width: cam.width,
    height: cam.height,
    personHeight: cam.expected_foot_y_person_height,
  };
}

function seekAll(t){
  for (const v of VIDS){
    if (Math.abs(v.el.currentTime - t) > 0.03) v.el.currentTime = t;
  }
}

function currentT(){
  if (playing && VIDS.length) return VIDS[0].el.currentTime;
  return parseFloat(document.getElementById('seek').value)/1000 * commonLen;
}

function updateTimeReadout(t){
  document.getElementById('time').textContent = `t = ${fmt(t)} / ${fmt(commonLen)} s`;
}

function tick(){
  if (!playing) return;
  let t = VIDS.length ? VIDS[0].el.currentTime : 0;
  if (t >= commonLen){ t = commonLen; pauseAll(); }
  const now = performance.now();
  if (now - lastDriftCheck > 1000){
    lastDriftCheck = now;
    // Keep loose sync without forcing expensive per-frame video seeks.
    for (let i=1; i<VIDS.length; i++){
      const v = VIDS[i];
      if (Math.abs(v.el.currentTime - t) > 0.50) v.el.currentTime = t;
    }
  }
  // Redraw overlays (reconstructed poles + boxes follow the current frame).
  for (const v of VIDS){
    drawOverlay(v);
  }
  const seek = document.getElementById('seek');
  seek.value = commonLen>0 ? Math.round(t/commonLen*1000) : 0;
  updateTimeReadout(t);
  if (playing) requestAnimationFrame(tick);
}

function syncPlayPauseBtn(){
  const btn = document.getElementById('playpause');
  if (btn) btn.textContent = playing ? '⏸ Pause' : '▶ Play';
}

function playAll(){
  if (!VIDS.length) return;
  let t = currentT();
  if (t >= commonLen) t = 0;
  seekAll(t);
  playing = true;
  lastDriftCheck = performance.now();
  VIDS.forEach(v => { v.el.playbackRate = playbackRate; v.el.play().catch(()=>{}); });
  syncPlayPauseBtn();
  requestAnimationFrame(tick);
}

function pauseAll(){
  playing = false;
  VIDS.forEach(v => v.el.pause());
  syncPlayPauseBtn();
}

function togglePlay(){
  if (playing) pauseAll(); else playAll();
}

let playbackRate = 1;
function applyPlaybackRate(){
  playbackRate = parseFloat(document.getElementById('speed').value) || 1;
  VIDS.forEach(v => { v.el.playbackRate = playbackRate; });
}

async function build(){
  CFG = await (await fetch('config')).json();
  syncSettingsFromCfg();
  const leg = document.getElementById('legend');
  leg.innerHTML = `
    <span><i class="swatch" style="background:${CFG.colors.mv_grid}"></i>MV floor grid</span>
    ${CFG.has_detections ? `<span><i class="swatch" style="background:${CFG.colors.bbox}"></i>2D det bbox</span>
    <span><i class="swatch" style="background:${CFG.colors.recon_pole}"></i>reconstructed pole</span>
    <span><i class="swatch" style="background:${CFG.colors.virtual_obj}"></i>cylinder / 3D box</span>
    <span><i class="swatch" style="background:${CFG.colors.extended_bbox}"></i>recovered bbox (occluded)</span>
    <span><i class="swatch" style="background:${CFG.colors.trace}"></i>foot trace</span>` : ''}`;
  // Populate the compare-mode camera picker.
  const csel = document.getElementById('compare_cam');
  csel.innerHTML = CFG.cameras.map((c, i) => `<option value="${i}">${c.name}</option>`).join('');
  compareCam = 0;
  await buildGrid();
}

// (Re)build the tile grid for the current view mode. In 'all' mode there is one
// tile per camera (global occlusion toggle applies). In 'compare' mode there
// are exactly two tiles of the selected camera: occlusion recovery OFF vs ON.
async function buildGrid(){
  pauseAll();
  if (recording) stopRecording();
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  VIDS = [];

  // Each spec becomes one tile: which camera + optional per-tile extend override.
  let specs;
  if (viewMode === 'compare'){
    specs = [
      {camIndex: compareCam, extendOverride: false, label: 'occlusion recovery: OFF'},
      {camIndex: compareCam, extendOverride: true,  label: 'occlusion recovery: ON'},
    ];
  } else {
    specs = CFG.cameras.map((_, i) => ({camIndex: i, extendOverride: null, label: null}));
  }

  const cols = Math.ceil(Math.sqrt(specs.length));
  grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;

  for (let s = 0; s < specs.length; s++){
    const spec = specs[s];
    const cam = CFG.cameras[spec.camIndex];
    const tile = document.createElement('div'); tile.className = 'tile';
    const stage = document.createElement('div'); stage.className = 'stage';
    stage.style.setProperty('--ar', `${cam.width}/${cam.height}`);
    const v = document.createElement('video');
    v.src = 'video?i=' + spec.camIndex; v.muted = true; v.preload = 'auto'; v.playsInline = true;
    v.playbackRate = playbackRate;
    const c = document.createElement('canvas');
    c.width = cam.width; c.height = cam.height;
    stage.append(v, c);
    const bar = document.createElement('div'); bar.className = 'tilebar';
    bar.textContent = `${cam.name}  (${cam.width}×${cam.height})`
        + (spec.label ? `  —  ${spec.label}` : `  world C≈(${cam.center_world.map(x=>x.toFixed(1)).join(', ')})`);
    tile.append(stage, bar);
    grid.append(tile);
    const rec = {el:v, canvas:c, cam:cam, dur:cam.duration || 0, idx:spec.camIndex,
                 dets:null, extendOverride:spec.extendOverride};
    VIDS.push(rec);
    v.addEventListener('loadedmetadata', () => {
      if ((v.duration && v.duration < rec.dur) || !rec.dur) rec.dur = v.duration;
      commonLen = Math.max(...VIDS.map(x => x.dur || 0));
      updateTimeReadout(0);
    });
    if (cam.has_detections) await ensureDets(rec, spec.camIndex);
    await ensureFootYMap(cam, spec.camIndex);
    await ensureGroundXYZMap(cam, spec.camIndex);
    drawOverlay(rec);
  }
  commonLen = Math.max(...VIDS.map(x => x.dur || 0), 0);
  updateTimeReadout(0);
}

document.getElementById('playpause').addEventListener('click', togglePlay);
document.getElementById('record').addEventListener('click', toggleRecord);
document.getElementById('speed').addEventListener('change', applyPlaybackRate);
document.getElementById('view_mode').addEventListener('change', () => {
  viewMode = document.getElementById('view_mode').value;
  document.getElementById('compare_cam_wrap').style.display =
      viewMode === 'compare' ? '' : 'none';
  // Occlusion recovery only differs under center snap; switch to it so the two
  // compare tiles actually diverge.
  if (viewMode === 'compare'){
    document.getElementById('bbox_snap').value = 'center';
    readClientParams();
    updateSnapControls();
  }
  buildGrid();
});
document.getElementById('compare_cam').addEventListener('change', () => {
  compareCam = parseInt(document.getElementById('compare_cam').value, 10) || 0;
  if (viewMode === 'compare') buildGrid();
});
document.getElementById('dump').addEventListener('click', dumpTiles);
document.getElementById('seek').addEventListener('input', () => {
  const t = parseFloat(document.getElementById('seek').value)/1000 * commonLen;
  seekAll(t); updateTimeReadout(t);
  refreshOverlays();
});
document.getElementById('bbox_opacity').addEventListener('input', () => {
  readClientParams();
  refreshOverlays();
});
document.getElementById('grid_opacity').addEventListener('input', () => {
  readClientParams();
  refreshOverlays();
});
document.getElementById('line_width').addEventListener('input', () => {
  readClientParams();
  refreshOverlays();
});
document.getElementById('grid_spacing').addEventListener('input', scheduleGeometry);
document.getElementById('person_height').addEventListener('input', scheduleGeometry);
document.getElementById('trace_secs').addEventListener('input', () => {
  readClientParams();
  refreshOverlays();
});
document.getElementById('bbox_snap').addEventListener('change', () => {
  readClientParams();
  updateSnapControls();
  refreshOverlays();
});
document.getElementById('extend_occluded').addEventListener('change', () => {
  readClientParams();
  refreshOverlays();
});
document.getElementById('kpt_gate_extend').addEventListener('change', () => {
  readClientParams();
  updateSnapControls();
  refreshOverlays();
});
document.getElementById('kpt_gate_tol').addEventListener('input', () => {
  readClientParams();
  refreshOverlays();
});
document.getElementById('virtual_object').addEventListener('change', () => {
  readClientParams();
  updateObjControls();
  refreshOverlays();
});
document.getElementById('cylinder_radius').addEventListener('input', () => {
  readClientParams();
  refreshOverlays();
});
build();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        state: dict = self.server.state  # type: ignore[attr-defined]
        if route == "/":
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif route == "/config":
            skip = (
                "videos", "detections", "_proj", "camera_dims", "det_srcs",
                "det_fps", "expected_foot_y_maps", "center_ground_xyz_maps",
            )
            payload = {k: v for k, v in state.items() if k not in skip}
            self._send(200, json.dumps(payload).encode(), "application/json")
        elif route == "/overlays":
            self._serve_overlays(parse_qs(parsed.query), state)
        elif route == "/detections":
            self._serve_detections(parse_qs(parsed.query), state)
        elif route == "/expected-foot-y-map":
            self._serve_expected_foot_y_map(parse_qs(parsed.query), state)
        elif route == "/center-ground-xyz-map":
            self._serve_center_ground_xyz_map(parse_qs(parsed.query), state)
        elif route == "/video":
            self._serve_video(parse_qs(parsed.query), state)
        else:
            self._send(404, b"not found", "text/plain")

    def _serve_overlays(self, qs: dict[str, list[str]], state: dict) -> None:
        try:
            grid_spacing = float(qs.get("grid_spacing", [state["grid_spacing"]])[0])
            person_height = float(qs.get("person_height", [state["person_height"]])[0])
        except (TypeError, ValueError):
            self._send(400, b"bad params", "text/plain")
            return
        if grid_spacing <= 0 or person_height <= 0:
            self._send(400, b"grid_spacing and person_height must be > 0", "text/plain")
            return
        overlays = reproject_overlays(
            state["_proj"],
            [tuple(d) for d in state["camera_dims"]],
            state["bounds"],
            grid_spacing=grid_spacing,
            person_height=person_height,
        )
        state["grid_spacing"] = grid_spacing
        state["person_height"] = person_height
        for i, ov in enumerate(overlays):
            state["cameras"][i]["overlay_mv"] = ov["overlay_mv"]
        payload = {
            "grid_spacing": grid_spacing,
            "person_height": person_height,
            "overlays": overlays,
        }
        self._send(200, json.dumps(payload).encode(), "application/json")

    def _serve_detections(self, qs: dict[str, list[str]], state: dict) -> None:
        try:
            idx = int(qs.get("i", ["-1"])[0])
            min_conf = float(qs.get("min_conf", [state.get("det_min_conf", 0.0)])[0])
            src = state["det_srcs"][idx]
        except (ValueError, IndexError, KeyError, TypeError):
            self._send(404, b"{}", "application/json")
            return
        if not src:
            self._send(404, b"{}", "application/json")
            return
        src_path, kind = Path(src[0]), src[1]
        dets = load_detections(
            src_path, kind, min_conf=min_conf,
            min_kpt_conf=state.get("min_kpt_conf", 0.3),
            foot_method=state.get("foot_method", "line_intersect"),
            null_stale=state.get("null_stale", False),
        )
        dets["fps"] = state["det_fps"][idx]
        state["det_min_conf"] = min_conf
        self._send(200, json.dumps(dets).encode(), "application/json")

    def _serve_expected_foot_y_map(
        self, qs: dict[str, list[str]], state: dict
    ) -> None:
        try:
            idx = int(qs.get("i", ["-1"])[0])
            if idx < 0:
                raise IndexError
            image = state["expected_foot_y_maps"][idx]
        except (ValueError, IndexError, KeyError, TypeError):
            self._send(404, b"", "application/octet-stream")
            return
        if image is None:
            self._send(404, b"", "application/octet-stream")
            return
        body = np.asarray(image, dtype="<f4").tobytes(order="C")
        self._send(200, body, "application/octet-stream")

    def _serve_center_ground_xyz_map(
        self, qs: dict[str, list[str]], state: dict
    ) -> None:
        try:
            idx = int(qs.get("i", ["-1"])[0])
            if idx < 0:
                raise IndexError
            image = state["center_ground_xyz_maps"][idx]
        except (ValueError, IndexError, KeyError, TypeError):
            self._send(404, b"", "application/octet-stream")
            return
        if image is None:
            self._send(404, b"", "application/octet-stream")
            return
        # Interleaved float32 HxWx3 in channel order (X, Y, Z=0).
        body = np.asarray(image, dtype="<f4").tobytes(order="C")
        self._send(200, body, "application/octet-stream")

    def _serve_video(self, qs: dict, state: dict) -> None:
        try:
            idx = int(qs.get("i", ["-1"])[0])
            path: Path = state["videos"][idx]
        except (ValueError, IndexError, KeyError):
            self._send(404, b"bad index", "text/plain")
            return
        size = path.stat().st_size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    buf = f.read(min(CHUNK, remaining))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    remaining -= len(buf)
        except (BrokenPipeError, ConnectionResetError):
            pass


def _parse_perturbations(rows: list[list[str]] | None) -> dict[int, dict[str, float]]:
    """Parse repeated ``--perturb CAM DX DY DZ DRX DRY DRZ`` CLI tuples."""
    out: dict[int, dict[str, float]] = {}
    if not rows:
        return out
    keys = ("dx", "dy", "dz", "drx", "dry", "drz")
    for row in rows:
        if len(row) != 7:
            raise ValueError(
                f"--perturb expects 7 values, got {len(row)}: {row!r}"
            )
        cam = int(row[0])
        out[cam] = {k: float(v) for k, v in zip(keys, row[1:])}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("output_folder", type=Path,
                        help="AMC project output folder (or variant subfolder)")
    parser.add_argument("--sv-results-dir", type=Path, default=None,
                        help="Override path to single_view_results/")
    parser.add_argument("--camparams-dir", type=Path, default=None,
                        help="Override MV camInfo directory (results_ba_scaled_world)")
    parser.add_argument(
        "--dump-virtual-gt",
        action="store_true",
        help="Write static per-camera floor-grid + grid-intersection pole PNGs "
             "and exit; does not require detections",
    )
    parser.add_argument(
        "--virtual-gt-output-dir",
        type=Path,
        default=None,
        help="Static dump destination (default: <output_folder>/virtual_gt_overlays)",
    )
    parser.add_argument("--video-dir", type=Path, default=None,
                        help="External directory with per-camera videos")
    parser.add_argument("--video", type=str, default=None,
                        help="Video filename inside each cam_XX/ (default: rectified.mp4)")
    parser.add_argument(
        "--expected-foot-y-dir",
        type=Path,
        default=None,
        help="Directory generated by generate_expected_foot_y_maps.py; "
             "loads expected-foot-y and center-snap ground (X,Y,0) maps",
    )
    parser.add_argument("--person-height", type=float, default=1.75,
                        help="Person pole height in metres (default: 1.75)")
    parser.add_argument("--grid-spacing", type=float, default=1.0,
                        help="Floor grid line spacing in metres (default: 1.0)")
    parser.add_argument("--x-min", type=float, default=None)
    parser.add_argument("--x-max", type=float, default=None)
    parser.add_argument("--y-min", type=float, default=None)
    parser.add_argument("--y-max", type=float, default=None)
    parser.add_argument("--margin", type=float, default=12.0,
                        help="Auto-bounds margin around camera centres (m)")
    parser.add_argument("--bbox-snap", choices=("bottom", "top", "center"),
                        default="center",
                        help="Recon anchor: bottom=foot (keypoint foot-center or "
                             "bbox bottom) → Z=0; top=head (keypoint nose or bbox "
                             "top) → Z=h; center=bbox dead-center → Z=h/2 "
                             "(no keypoint) (default: center)")
    parser.add_argument("--det-min-conf", type=float, default=0.0,
                        help="Min detection confidence (default: 0)")
    parser.add_argument("--min-kpt-conf", type=float, default=0.3,
                        help="Per-joint confidence floor to trust a keypoint "
                             "(default: 0.3)")
    parser.add_argument("--foot-method", choices=("line_intersect", "ankle_mean"),
                        default="line_intersect",
                        help="Keypoint foot construction (default: line_intersect)")
    parser.add_argument("--null-stale-kpts", dest="null_stale", action="store_true",
                        help="Drop held/repeated poses (AMC refreshes ~every N "
                             "frames); off by default so keypoints are used on "
                             "every frame they exist")
    parser.add_argument("--grid-opacity", type=float, default=0.35,
                        help="Floor-grid opacity (default: 0.35)")
    parser.add_argument("--virtual-object", choices=("pole", "cylinder", "box"),
                        default="pole",
                        help="Virtual object reconstructed per detection: pole "
                             "(red), cylinder or 3D box (magenta) (default: pole)")
    parser.add_argument("--cylinder-radius", type=float, default=0.3,
                        help="Cylinder radius / box half-footprint in metres "
                             "(default: 0.3)")
    parser.add_argument("--trace-secs", type=float, default=2.0,
                        help="Draw a fading trail of each person's reconstructed "
                             "foot (object bottom, Z=0) over the last N seconds; "
                             "0 disables (default: 2.0)")
    parser.add_argument("--extend-occluded", dest="extend_occluded",
                        action="store_true",
                        help="center-snap add-on: for bottom-occluded people, "
                             "back-project the bbox top-center to Z=person-height, "
                             "drop to Z=0, and extend the bbox bottom down to that "
                             "expected foot before taking the center (only affects "
                             "--bbox-snap center)")
    parser.add_argument("--kpt-gate-extend", dest="kpt_gate_extend",
                        action="store_true",
                        help="center-snap add-on (trajDump keypoints required): "
                             "gate geometry extension by pose evidence. Skip "
                             "extension when the lowest trusted ankle reaches "
                             "near the geometry-expected foot; extend when the "
                             "ankle sits well above it (occlusion/truncation)")
    parser.add_argument("--kpt-gate-tol", dest="kpt_gate_tol", type=float,
                        default=0.2,
                        help="Gate tolerance as a fraction of bbox height: skip "
                             "extension iff lowest ankle y >= expected_foot_y - "
                             "tol*bbox_h (default: 0.2; larger => extend less)")
    parser.add_argument(
        "--perturb",
        action="append",
        nargs=7,
        metavar=("CAM", "DX", "DY", "DZ", "DRX", "DRY", "DRZ"),
        help="Pose error on one camera (repeatable): cam index, translation "
             "dx dy dz in metres, rotation drx=tilt dry=pan drz=roll in degrees "
             "(camera frame)",
    )
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--no-open", action="store_true",
                        help="Do not open a browser tab automatically")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.dump_virtual_gt:
        logger.info("Rendering static virtual-GT overlays under %s …", args.output_folder)
        written = render_grid_pole_overlays(
            args.output_folder,
            sv_results_dir=args.sv_results_dir,
            camparams_dir=args.camparams_dir,
            output_dir=args.virtual_gt_output_dir,
            person_height=args.person_height,
            grid_spacing=args.grid_spacing,
            grid_opacity=args.grid_opacity,
            x_min=args.x_min,
            x_max=args.x_max,
            y_min=args.y_min,
            y_max=args.y_max,
            margin=args.margin,
        )
        logger.info("Wrote %d virtual-GT overlay image(s).", len(written))
        return

    perturbations = _parse_perturbations(args.perturb)
    logger.info("Discovering scene under %s …", args.output_folder)
    state = discover_scene(
        args.output_folder,
        sv_results_dir=args.sv_results_dir,
        camparams_dir=args.camparams_dir,
        video_dir=args.video_dir,
        video_name=args.video,
        person_height=args.person_height,
        x_min=args.x_min,
        x_max=args.x_max,
        y_min=args.y_min,
        y_max=args.y_max,
        margin=args.margin,
        grid_spacing=args.grid_spacing,
        det_min_conf=args.det_min_conf,
        bbox_snap=args.bbox_snap,
        grid_opacity=args.grid_opacity,
        virtual_object=args.virtual_object,
        cylinder_radius=args.cylinder_radius,
        extend_occluded=args.extend_occluded,
        kpt_gate_extend=args.kpt_gate_extend,
        kpt_gate_tol=args.kpt_gate_tol,
        trace_secs=args.trace_secs,
        min_kpt_conf=args.min_kpt_conf,
        foot_method=args.foot_method,
        null_stale=args.null_stale,
        perturbations=perturbations or None,
        expected_foot_y_dir=args.expected_foot_y_dir,
    )
    if not state["has_detections"]:
        logger.warning("No detection source found; reconstructed poles disabled "
                        "(floor grid only)")
    b = state["bounds"]
    logger.info(
        "World grid: x=[%.1f,%.1f] y=[%.1f,%.1f]  spacing=%.2f m  pole h=%.2f m  (%d cameras)",
        b["x_min"], b["x_max"], b["y_min"], b["y_max"],
        state["grid_spacing"], state["person_height"], len(state["cameras"]),
    )
    logger.info("MV calib: %s", state["mv_camparams"])

    url = f"http://{args.host}:{args.port}/"
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.state = state  # type: ignore[attr-defined]
    logger.info("Open %s  (Ctrl+C to stop)", url)
    if not args.no_open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
