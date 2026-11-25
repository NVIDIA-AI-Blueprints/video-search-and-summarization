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

"""Precompute per-pixel expected-foot-y and center-snap ground XYZ lookup maps.

For every image pixel treated as the top-center of a standing person's 2D
bounding box, this script stores the image-y coordinate where the foot of a
person with a fixed world height would project. One float32 TIFF is written per
camera, together with a JSON manifest used for compatibility checks by
``calib_wire_overlay_viewer.py``.

It also writes a second per-camera 3-channel float32 NumPy array (``.npy``)
that maps each pixel (interpreted as a bbox-center / waist pixel) to the
recovered world ground point ``(X, Y, 0)`` used as the pole / cylinder / box
bottom. See ``build_center_ground_xyz_map`` — currently **center snap only**
(back-project at ``Z = person_height / 2``). Top/bottom snap maps are not
generated yet.

The expected-foot-y mapping is the plane-induced homography

    foot_pixel ~ H(Z=0) @ inv(H(Z=person_height)) @ head_pixel

evaluated densely over the camera image. Invalid rays are stored as NaN.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from calib_wire_overlay_viewer import (
    _cam_index_from_name,
    _find_mv_camparams_dir,
    _find_sv_results_dir,
    _load_projection_matrix,
    _resolve_video,
    _video_meta,
)

logger = logging.getLogger("generate_expected_foot_y_maps")

MANIFEST_NAME = "expected_foot_y_manifest.json"
MAP_PATTERN = "cam_{cam_id:02d}_expected_foot_y.tiff"
# Center-snap only for now: pixel = bbox mid → back-project at Z=h/2 → (X,Y,0).
# Stored as HxWx3 float32 .npy (OpenCV multi-channel float TIFF is unreliable).
GROUND_XYZ_PATTERN = "cam_{cam_id:02d}_center_ground_xyz.npy"
MANIFEST_FORMAT = "expected-foot-y-map-v2"


def _plane_homography(P: np.ndarray, z: float) -> np.ndarray:
    """Return the world-plane ``Z=z`` to image homography."""
    return np.column_stack((P[:, 0], P[:, 1], P[:, 2] * z + P[:, 3]))


def build_expected_foot_y_map(
    P: np.ndarray,
    width: int,
    height: int,
    person_height: float,
    *,
    chunk_rows: int = 256,
) -> np.ndarray:
    """Build a dense top-center-pixel to expected-foot-y lookup image.

    Args:
        P: World-to-image projection matrix with shape ``(3, 4)``.
        width: Camera image width in pixels.
        height: Camera image height in pixels.
        person_height: Assumed standing-person height in world metres.
        chunk_rows: Number of image rows evaluated per vectorized chunk.

    Returns:
        Float32 image with shape ``(height, width)``. Each valid entry stores
        expected foot image-y; invalid/behind-camera mappings are NaN.

    Raises:
        ValueError: If dimensions or person height are invalid, or the head
            plane homography is singular.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid image dimensions: {width}x{height}")
    if person_height <= 0:
        raise ValueError("person_height must be positive")

    P = np.asarray(P, dtype=np.float64).reshape(3, 4)
    H_head = _plane_homography(P, person_height)
    try:
        H_head_inv = np.linalg.inv(H_head)
    except np.linalg.LinAlgError as exc:
        raise ValueError("Head-plane homography is singular") from exc

    result = np.full((height, width), np.nan, dtype=np.float32)
    u = np.arange(width, dtype=np.float64)[None, :]

    for row0 in range(0, height, chunk_rows):
        row1 = min(row0 + chunk_rows, height)
        v = np.arange(row0, row1, dtype=np.float64)[:, None]

        # Back-project every head pixel in this row block to Z=person_height.
        q0 = H_head_inv[0, 0] * u + H_head_inv[0, 1] * v + H_head_inv[0, 2]
        q1 = H_head_inv[1, 0] * u + H_head_inv[1, 1] * v + H_head_inv[1, 2]
        q2 = H_head_inv[2, 0] * u + H_head_inv[2, 1] * v + H_head_inv[2, 2]
        valid = np.abs(q2) > 1e-9

        with np.errstate(divide="ignore", invalid="ignore"):
            X = q0 / q2
            Y = q1 / q2
            head_depth = (
                P[2, 0] * X
                + P[2, 1] * Y
                + P[2, 2] * person_height
                + P[2, 3]
            )
            foot_depth = P[2, 0] * X + P[2, 1] * Y + P[2, 3]
            foot_y = (P[1, 0] * X + P[1, 1] * Y + P[1, 3]) / foot_depth

        valid &= head_depth > 1e-6
        valid &= foot_depth > 1e-6
        valid &= np.isfinite(X) & np.isfinite(Y) & np.isfinite(foot_y)
        block = np.full((row1 - row0, width), np.nan, dtype=np.float32)
        block[valid] = foot_y[valid].astype(np.float32)
        result[row0:row1] = block

    return result


def build_center_ground_xyz_map(
    P: np.ndarray,
    width: int,
    height: int,
    person_height: float,
    *,
    chunk_rows: int = 256,
) -> np.ndarray:
    """Build dense center-snap world-ground lookup: pixel → (X, Y, 0).

    NOTE: Center / waist snap only for now. Each image pixel is treated as a
    bbox mid-point and back-projected onto the plane ``Z = person_height / 2``.
    The recovered ``(X, Y)`` is the pole / cylinder / box bottom on the floor
    (``Z = 0``). Top-snap (``Z = h``) and bottom-snap (``Z = 0``) maps are not
    generated yet — add them later if those snap modes need LUT acceleration.

    Args:
        P: World-to-image projection matrix with shape ``(3, 4)``.
        width: Camera image width in pixels.
        height: Camera image height in pixels.
        person_height: Assumed standing-person height in world metres.
        chunk_rows: Number of image rows evaluated per vectorized chunk.

    Returns:
        Float32 image with shape ``(height, width, 3)``. Channels are
        ``(X, Y, Z)`` with ``Z`` always ``0`` when valid; invalid rays are NaN
        in all channels.

    Raises:
        ValueError: If dimensions or person height are invalid, or the waist
            plane homography is singular.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid image dimensions: {width}x{height}")
    if person_height <= 0:
        raise ValueError("person_height must be positive")

    P = np.asarray(P, dtype=np.float64).reshape(3, 4)
    # Center snap: bbox mid → mid-height plane Z = h/2.
    z_waist = 0.5 * person_height
    H_waist = _plane_homography(P, z_waist)
    try:
        H_waist_inv = np.linalg.inv(H_waist)
    except np.linalg.LinAlgError as exc:
        raise ValueError("Waist-plane (Z=h/2) homography is singular") from exc

    result = np.full((height, width, 3), np.nan, dtype=np.float32)
    u = np.arange(width, dtype=np.float64)[None, :]

    for row0 in range(0, height, chunk_rows):
        row1 = min(row0 + chunk_rows, height)
        v = np.arange(row0, row1, dtype=np.float64)[:, None]

        q0 = H_waist_inv[0, 0] * u + H_waist_inv[0, 1] * v + H_waist_inv[0, 2]
        q1 = H_waist_inv[1, 0] * u + H_waist_inv[1, 1] * v + H_waist_inv[1, 2]
        q2 = H_waist_inv[2, 0] * u + H_waist_inv[2, 1] * v + H_waist_inv[2, 2]
        valid = np.abs(q2) > 1e-9

        with np.errstate(divide="ignore", invalid="ignore"):
            X = q0 / q2
            Y = q1 / q2
            waist_depth = (
                P[2, 0] * X + P[2, 1] * Y + P[2, 2] * z_waist + P[2, 3]
            )

        valid &= waist_depth > 1e-6
        valid &= np.isfinite(X) & np.isfinite(Y)
        block = np.full((row1 - row0, width, 3), np.nan, dtype=np.float32)
        block[valid, 0] = X[valid].astype(np.float32)
        block[valid, 1] = Y[valid].astype(np.float32)
        block[valid, 2] = 0.0  # floor / object bottom
        result[row0:row1] = block

    return result


def generate_maps(
    output_folder: Path,
    output_dir: Path,
    *,
    person_height: float,
    sv_results_dir: Path | None = None,
    camparams_dir: Path | None = None,
    video_dir: Path | None = None,
    video_name: str | None = None,
) -> dict[str, Any]:
    """Generate all camera maps and return their manifest."""
    output_folder = output_folder.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    mv_dir = _find_mv_camparams_dir(output_folder, camparams_dir)
    sv_root = _find_sv_results_dir(output_folder, sv_results_dir)
    if sv_root is None:
        raise FileNotFoundError(f"Could not find single_view_results under {output_folder}")

    output_dir.mkdir(parents=True, exist_ok=True)
    cameras: list[dict[str, Any]] = []
    for caminfo_path in sorted(mv_dir.glob("camInfo_*.yaml")):
        cam_id = _cam_index_from_name(caminfo_path.stem)
        cam_dir = sv_root / f"cam_{cam_id:02d}"
        if not cam_dir.is_dir():
            raise FileNotFoundError(f"Missing camera directory: {cam_dir}")
        video_path = _resolve_video(cam_dir, cam_id, video_name, video_dir)
        width, height, _, _ = _video_meta(video_path)
        P = _load_projection_matrix(caminfo_path, "projectionMatrix_3x4")

        logger.info("cam_%02d: generating %dx%d maps", cam_id, width, height)
        foot_y_map = build_expected_foot_y_map(P, width, height, person_height)
        # Center-snap ground XYZ only (pixel = waist → Z=h/2 → foot (X,Y,0)).
        ground_xyz_map = build_center_ground_xyz_map(
            P, width, height, person_height
        )

        map_name = MAP_PATTERN.format(cam_id=cam_id)
        map_path = output_dir / map_name
        if not cv2.imwrite(str(map_path), foot_y_map):
            raise OSError(f"Failed to write map image: {map_path}")

        ground_name = GROUND_XYZ_PATTERN.format(cam_id=cam_id)
        ground_path = output_dir / ground_name
        # .npy preserves float32 NaNs; OpenCV cannot reliably write HxWx3 float TIFF.
        np.save(ground_path, ground_xyz_map)

        cameras.append(
            {
                "id": cam_id,
                "file": map_name,
                "ground_xyz_file": ground_name,
                "width": width,
                "height": height,
                "projection_matrix": [float(x) for x in P.ravel()],
            }
        )
        logger.info(
            "  wrote %s (%d valid pixels)",
            map_path,
            int(np.count_nonzero(np.isfinite(foot_y_map))),
        )
        logger.info(
            "  wrote %s (%d valid pixels; center-snap (X,Y,0))",
            ground_path,
            int(np.count_nonzero(np.isfinite(ground_xyz_map[..., 0]))),
        )

    manifest: dict[str, Any] = {
        "format": MANIFEST_FORMAT,
        "dtype": "float32",
        "invalid_value": "NaN",
        "person_height": float(person_height),
        # Documents that ground_xyz maps are for bbox-center → Z=h/2 only.
        "ground_xyz_snap": "center",
        "ground_xyz_channels": ["X", "Y", "Z"],
        "mv_camparams": str(mv_dir),
        "cameras": cameras,
    }
    manifest_path = output_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote manifest: %s", manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_folder", type=Path, help="AMC project output folder")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Map output directory (default: OUTPUT_FOLDER/expected_foot_y_maps)",
    )
    parser.add_argument(
        "--person-height",
        type=float,
        default=1.75,
        help="Standing-person height in metres (default: 1.75)",
    )
    parser.add_argument("--sv-results-dir", type=Path, default=None)
    parser.add_argument("--camparams-dir", type=Path, default=None)
    parser.add_argument("--video-dir", type=Path, default=None)
    parser.add_argument("--video", type=str, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output_dir = args.output_dir or (args.output_folder / "expected_foot_y_maps")
    generate_maps(
        args.output_folder,
        output_dir,
        person_height=args.person_height,
        sv_results_dir=args.sv_results_dir,
        camparams_dir=args.camparams_dir,
        video_dir=args.video_dir,
        video_name=args.video,
    )


if __name__ == "__main__":
    main()
