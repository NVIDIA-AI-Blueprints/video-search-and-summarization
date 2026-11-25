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

"""AMC / GeoCalib forward distortion models and inverse remap (shared by tuner + rectification)."""

from __future__ import annotations

from typing import Dict, List, Tuple, Union

import cv2
import numpy as np
import yaml

MODELS: Tuple[str, ...] = (
    "pinhole",
    "simple_radial",
    "radial",
    "simple_divisional",
)

MANUAL_MODELS: Tuple[str, ...] = ("simple_radial", "radial", "simple_divisional")


def radial_scale(r2: np.ndarray, model: str, k1: float, k2: float) -> np.ndarray:
    if model == "pinhole":
        return np.ones_like(r2, dtype=np.float64)
    if model == "simple_radial":
        return 1.0 + k1 * r2
    if model == "radial":
        return 1.0 + k1 * r2 + k2 * r2 * r2
    if model == "simple_divisional":
        denom = 2.0 * k1 * r2
        inside = np.clip(1.0 - 4.0 * k1 * r2, 0.0, None)
        safe_denom = np.where(np.abs(denom) < 1e-30, 1.0, denom)
        return np.where(
            np.abs(denom) < 1e-30, 1.0, (1.0 - np.sqrt(inside)) / safe_denom
        )
    raise ValueError(f"Unknown model: {model!r}. Valid: {MODELS}")


def forward_distort(
    xu: np.ndarray,
    yu: np.ndarray,
    cx: float,
    cy: float,
    model: str,
    k1: float,
    k2: float,
) -> Tuple[np.ndarray, np.ndarray]:
    xn = xu - cx
    yn = yu - cy
    r2 = xn * xn + yn * yn
    s = radial_scale(r2, model, k1, k2)
    return xn * s + cx, yn * s + cy


def corner_r2(width: int, height: int) -> float:
    cx, cy = width // 2, height // 2
    dx = max(cx, width - cx)
    dy = max(cy, height - cy)
    return float(dx * dx + dy * dy)


def auto_param_ranges(
    width: int,
    height: int,
    max_corner_scale: float = 0.4,
) -> Tuple[float, float]:
    r2 = corner_r2(width, height)
    if r2 <= 0:
        raise ValueError(
            f"Invalid video dimensions for param ranges: {width}x{height}"
        )
    k1_limit = max_corner_scale / r2
    k2_limit = max_corner_scale / (r2 * r2)
    return k1_limit, k2_limit


def build_grid_lines(
    width: int,
    height: int,
    n_vertical: int = 14,
    n_horizontal: int = 9,
    samples_per_line: int = 200,
    margin_frac: float = 0.2,
) -> List[np.ndarray]:
    mx = margin_frac * width
    my = margin_frac * height
    x0, x1 = -mx, width + mx
    y0, y1 = -my, height + my
    lines: List[np.ndarray] = []
    ys = np.linspace(y0, y1, samples_per_line)
    for xv in np.linspace(x0, x1, n_vertical):
        lines.append(np.stack([np.full_like(ys, xv), ys], axis=1))
    xs = np.linspace(x0, x1, samples_per_line)
    for yh in np.linspace(y0, y1, n_horizontal):
        lines.append(np.stack([xs, np.full_like(xs, yh)], axis=1))
    return lines


def normalize_lens_param(params: Dict) -> Tuple[str, float, float]:
    """Return (model, k1, k2) from a lens param dict or YAML-loaded mapping."""
    model = params.get("model", "simple_radial")
    if model not in MODELS:
        model = "simple_radial"
    k1 = float(params.get("k1", 0.0))
    k2 = float(params.get("k2", 0.0)) if model == "radial" else 0.0
    return model, k1, k2


def params_dict(model: str, k1: float, k2: float) -> Dict[str, Union[float, str]]:
    data: Dict[str, Union[float, str]] = {"model": model, "k1": float(k1)}
    if model == "radial":
        data["k2"] = float(k2)
    return data


def load_distortion_yaml(path: str) -> Dict:
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    model, k1, k2 = normalize_lens_param(data)
    return params_dict(model, k1, k2)


def build_rectification_maps(
    width: int,
    height: int,
    model: str,
    k1: float,
    k2: float,
    cx: float | None = None,
    cy: float | None = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Build the fixed OpenCV source-coordinate maps for one camera geometry."""
    if cx is None:
        cx = width // 2
    if cy is None:
        cy = height // 2
    xs, ys = np.meshgrid(
        np.arange(width, dtype=np.float64),
        np.arange(height, dtype=np.float64),
    )
    xd, yd = forward_distort(xs, ys, cx, cy, model, k1, k2)
    return xd.astype(np.float32), yd.astype(np.float32)


def rectify_image_bgr(
    img_bgr: np.ndarray,
    model: str,
    k1: float,
    k2: float,
    cx: float | None = None,
    cy: float | None = None,
    remap: Tuple[np.ndarray, np.ndarray] | None = None,
) -> np.ndarray:
    """Undistort by inverse forward-distort map (matches k1_overlay_tool)."""
    h, w = img_bgr.shape[:2]
    if remap is None:
        remap = build_rectification_maps(w, h, model, k1, k2, cx=cx, cy=cy)
    map_x, map_y = remap
    if map_x.shape != (h, w) or map_y.shape != (h, w):
        raise ValueError(
            f"Remap shape must match image {w}x{h}; got "
            f"{map_x.shape[::-1]} and {map_y.shape[::-1]}"
        )
    return cv2.remap(
        img_bgr, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0
    )


def undistort_points(
    pts: List,
    width: int,
    height: int,
    params: Dict,
    max_iter: int = 50,
    tolerance: float = 1e-4,
) -> np.ndarray:
    """Map points from distorted image coords to rectified (same space as rectify_image_bgr)."""
    model, k1, k2 = normalize_lens_param(params)
    cx, cy = width // 2, height // 2
    pts_arr = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    out = np.empty_like(pts_arr)
    for i, (xd, yd) in enumerate(pts_arr):
        xu, yu = xd, yd
        for _ in range(max_iter):
            fx, fy = forward_distort(
                np.array([xu]), np.array([yu]), cx, cy, model, k1, k2
            )
            err_x = float(fx[0] - xd)
            err_y = float(fy[0] - yd)
            if abs(err_x) < tolerance and abs(err_y) < tolerance:
                break
            xu -= err_x
            yu -= err_y
        out[i, 0] = xu
        out[i, 1] = yu
    return out
