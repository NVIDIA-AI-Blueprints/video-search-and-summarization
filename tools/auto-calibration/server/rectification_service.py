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

"""Rectification helpers for the FastAPI server (preview + camera metadata).

This module is imported at uvicorn startup and uses the readable core modules.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import yaml

from utils.video_capture import open_video_capture

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_RECT_DIR = _REPO_ROOT / "core" / "rectification"
if str(_RECT_DIR) not in sys.path:
    sys.path.insert(0, str(_RECT_DIR))

import distortion_models  # noqa: E402


def _read_yaml_params(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    model, k1, k2 = distortion_models.normalize_lens_param(data)
    return distortion_models.params_dict(model, k1, k2)


def _camera_lens_params(camera_dir: Path) -> Optional[Dict[str, Any]]:
    """Committed distortion first, then GeoCalib estimate; None means identity."""
    for relative in (
        Path("distortion.yaml"),
        Path("distortion_estimation") / "geocalib_distortion.yaml",
        Path("geocalib_distortion.yaml"),
    ):
        params = _read_yaml_params(camera_dir / relative)
        if params is not None:
            return params
    return None


def _camera_image_size(camera_dir: Path) -> Tuple[int, int]:
    rectified = camera_dir / "rectified.jpg"
    if rectified.is_file():
        image = cv2.imread(str(rectified))
        if image is not None:
            return int(image.shape[1]), int(image.shape[0])
    raise FileNotFoundError(f"Missing rectified.jpg under {camera_dir}")


def materialize_rectified_alignment(
    output_dir: Path,
    *,
    is_single_cam: bool,
) -> Path:
    """Convert alignment_data_orig.json → alignment_data.json in rectified space.

    Layout / BEV points are left unchanged. Missing per-camera distortion params
    are treated as identity (already-linear media).
    """
    import copy
    import json

    alignment_dir = output_dir / "alignment_data"
    orig_path = alignment_dir / "alignment_data_orig.json"
    rect_path = alignment_dir / "alignment_data.json"
    if not orig_path.is_file():
        raise FileNotFoundError(f"Missing original alignment file: {orig_path}")

    with open(orig_path, "r", encoding="utf-8") as handle:
        alignment_data = json.load(handle)
    if not isinstance(alignment_data, list):
        raise ValueError("alignment_data_orig.json must be a list of point sets")

    sv_root = output_dir / "single_view_results"
    camera_indices = [0] if is_single_cam else [0, 1]
    rectified = copy.deepcopy(alignment_data)

    for cam_idx in camera_indices:
        camera_dir = sv_root / f"cam_{cam_idx:02d}"
        width, height = _camera_image_size(camera_dir)
        lens_param = _camera_lens_params(camera_dir)
        if lens_param is None:
            continue
        for point_set in rectified:
            if not isinstance(point_set, list) or cam_idx >= len(point_set):
                raise ValueError(
                    f"Point set missing camera index {cam_idx} while materializing alignment"
                )
            cam_pt = point_set[cam_idx]
            undistorted = distortion_models.undistort_points(
                np.array([[cam_pt]], dtype=np.float64),
                width,
                height,
                lens_param,
            )
            point_set[cam_idx] = [
                int(round(float(undistorted[0][0]))),
                int(round(float(undistorted[0][1]))),
            ]

    alignment_dir.mkdir(parents=True, exist_ok=True)
    with open(rect_path, "w", encoding="utf-8") as handle:
        json.dump(rectified, handle, indent=2)
    partial = alignment_dir / "alignment_data_partial.json"
    if partial.is_file():
        partial.unlink()
    return rect_path


def _video_dimensions(video_path: Path) -> Tuple[int, int]:
    """Probe width/height via the MS-safe capture path (NVIDIA GStreamer).

    Raw ``cv2.VideoCapture(path)`` in the release image often returns 0x0 when
    OpenCV's default GStreamer demux fails (qtdemux stream errors), which then
    crashes ``auto_param_ranges`` with ZeroDivisionError. Prefer
    ``open_video_capture`` and fall back to reading one frame.
    """
    cap = open_video_capture(str(video_path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video: {video_path}")
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if w > 0 and h > 0:
            return w, h
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(
                f"Could not read frame to determine dimensions: {video_path}"
            )
        return int(frame.shape[1]), int(frame.shape[0])
    finally:
        cap.release()


def _apply_output_geometry(
    image: np.ndarray,
    target_width: int,
    target_height: int,
    config: Dict[str, Any],
) -> np.ndarray:
    """Match run_auto_rectification.rectify_image crop/resize behavior."""
    frame = image
    output_cfg = (config or {}).get("output", {}) if isinstance(config, dict) else {}
    if output_cfg.get("crop"):
        xc_u = frame.shape[1] // 2
        yc_u = frame.shape[0] // 2
        w = target_width // 2
        h = target_height // 2
        frame = frame[yc_u - h : yc_u + h, xc_u - w : xc_u + w]
    elif frame.shape[1] != target_width or frame.shape[0] != target_height:
        frame = cv2.resize(frame, (target_width, target_height))
    return np.ascontiguousarray(frame)


def build_camera_states(
    video_dir: Path,
    output_root: Path,
    video_names: List[str],
) -> List[Dict[str, Any]]:
    cameras: List[Dict[str, Any]] = []
    for video_name in video_names:
        cam_id = video_name.replace(".mp4", "")
        video_path = video_dir / video_name
        if not video_path.is_file():
            continue
        width, height = _video_dimensions(video_path)
        cx, cy = width // 2, height // 2
        k1_lim, k2_lim = distortion_models.auto_param_ranges(width, height)
        cam_dir = output_root / cam_id
        committed = _read_yaml_params(cam_dir / "distortion.yaml")
        auto_est = _read_yaml_params(cam_dir / "distortion_estimation" / "geocalib_distortion.yaml")
        if auto_est is None:
            auto_est = _read_yaml_params(cam_dir / "geocalib_distortion.yaml")
        lines = distortion_models.build_grid_lines(width, height)
        grid = [ln.round(1).tolist() for ln in lines]
        cameras.append(
            {
                "camera_id": cam_id,
                "video_name": video_name,
                "width": width,
                "height": height,
                "cx": float(cx),
                "cy": float(cy),
                "k1_min": -k1_lim,
                "k1_max": k1_lim,
                "k2_min": -k2_lim,
                "k2_max": k2_lim,
                "committed": committed,
                "auto_estimate": auto_est,
                "grid_lines": grid,
                "has_rectified_preview": (cam_dir / "rectified.jpg").is_file(),
            }
        )
    return cameras


def preview_rectified_frame_png(
    video_path: Path,
    params: Dict[str, Any],
    config_path: Path,
    frame_index: int = 0,
) -> bytes:
    cap = open_video_capture(str(video_path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video: {video_path}")
        if frame_index > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, float(frame_index))
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        raise RuntimeError(f"Could not read frame from {video_path}")

    model, k1, k2 = distortion_models.normalize_lens_param(params)
    rectified = distortion_models.rectify_image_bgr(frame, model, k1, k2)
    config: Dict[str, Any] = {}
    if config_path.is_file():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    rectified = _apply_output_geometry(
        rectified, frame.shape[1], frame.shape[0], config
    )
    ok_enc, buf = cv2.imencode(".png", rectified)
    if not ok_enc:
        raise RuntimeError("Failed to encode rectified preview")
    return buf.tobytes()
