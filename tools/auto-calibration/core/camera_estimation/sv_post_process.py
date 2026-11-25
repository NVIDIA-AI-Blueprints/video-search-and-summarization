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
Single-view post-processing: construct a world-frame projection matrix (P_world)
from the SV calibration output and user-provided image-to-layout correspondences.

This is the SV-only alternative to layout_alignment_post_process.py, which requires
multi-view triangulation.  Here we use a homography-based approach that works with
a single camera under the flat ground-plane assumption.
"""

import argparse
import json
import os
import sys

import cv2
import numpy as np
import yaml

# Allow importing sibling modules (math_utils) when invoked from any cwd
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from math_utils import umeyama_alignment

SV_HEIGHT_COL = 2  # column index of the "up" axis in P_sv (Z-up in SV local frame)


def load_sv_projection_matrix(cam_info_path: str) -> np.ndarray:
    """Load the SV projection matrix from camInfo_hyper_00_opencv.yaml."""
    with open(cam_info_path, "r") as f:
        data = yaml.safe_load(f)
    flat = data["projectionMatrix_3x4_w2p"]
    P = np.array(flat, dtype=np.float64).reshape(3, 4)
    return P


def compute_image_to_map_homography(alignment_data: list) -> np.ndarray:
    """Compute the 3x3 homography from image pixels to layout-map pixels.

    Args:
        alignment_data: list of [[cam_x, cam_y], [layout_x, layout_y]] pairs.

    Returns:
        H_img2map (3x3): maps image pixel -> map pixel.
    """
    img_pts = np.array([pt[0] for pt in alignment_data], dtype=np.float32)
    map_pts = np.array([pt[1] for pt in alignment_data], dtype=np.float32)
    H, mask = cv2.findHomography(img_pts, map_pts, method=cv2.RANSAC,
                                  ransacReprojThreshold=5.0)
    if H is None:
        raise RuntimeError("findHomography failed — check correspondence quality")
    n_inliers = int(mask.sum()) if mask is not None else len(img_pts)
    print(f"  Homography inliers: {n_inliers}/{len(img_pts)}")
    return H


def compute_world_projection_matrix(
    P_sv: np.ndarray,
    H_img2map: np.ndarray,
    ppm: float,
) -> tuple[np.ndarray, float]:
    """Build P_world (3x4) from the SV projection matrix and correspondences.

    Columns 0, 1, 3 come from the world-floor-to-image homography
    (derived from correspondences + ppm).
    Column 2 (height) comes from P_sv, scaled to reconcile the SV local
    metric with the world metric.

    Returns:
        P_world (3x4), scale_factor s
    """
    # World-floor (meters) -> map (pixels)
    T_world2map = np.array([
        [ppm,    0.0, 0.0],
        [0.0, -ppm,  0.0],
        [0.0,    0.0, 1.0],
    ], dtype=np.float64)

    # World-floor -> image  (3x3)
    H_wf2img = np.linalg.inv(H_img2map) @ T_world2map

    # --- Scale factor s  (SV-local-meters per world-meter) ---
    # SV local floor -> image (drop the height column from P_sv)
    floor_cols = [c for c in range(4) if c != SV_HEIGHT_COL]
    G_sv = P_sv[:, floor_cols]  # 3x3: local floor -> image

    # local floor -> world floor
    H_lf2wf = np.linalg.inv(T_world2map) @ H_img2map @ G_sv
    # Extract scale from the 2x2 linear part (should be ~similarity)
    U, S, Vt = np.linalg.svd(H_lf2wf[:2, :2])
    s = float(np.sqrt(S[0] * S[1]))
    print(f"  Scale factor s (local-meters / world-meter): {s:.6f}")
    if abs(S[0] / S[1] - 1.0) > 0.3:
        print(f"  Warning: singular values differ significantly "
              f"({S[0]:.4f}, {S[1]:.4f}) — floor transform may not be a "
              f"pure similarity. Check correspondence quality.")

    # --- Assemble P_world ---
    P_world = np.zeros((3, 4), dtype=np.float64)
    P_world[:, 0] = H_wf2img[:, 0]
    P_world[:, 1] = H_wf2img[:, 1]
    P_world[:, 2] = s * P_sv[:, SV_HEIGHT_COL]
    P_world[:, 3] = H_wf2img[:, 2]

    return P_world, s


def verify_reprojection(
    P_world: np.ndarray,
    traj_path: str,
    G_sv: np.ndarray,
    H_lf2wf: np.ndarray,
    rectified_img_path: str,
    output_path: str,
) -> None:
    """Round-trip reprojection test: SV-local floor -> world floor -> image.

    Reads 3D trajectory foot positions (fields[7], fields[8] in
    trajDump_Stream_0_3d.txt), transforms through the homography chain,
    and draws the reprojected points on the rectified image.
    """
    if not os.path.exists(traj_path):
        print(f"  Trajectory file not found — skipping verification: {traj_path}")
        return
    if not os.path.exists(rectified_img_path):
        print(f"  Rectified image not found — skipping verification: {rectified_img_path}")
        return

    img = cv2.imread(rectified_img_path)
    if img is None:
        print(f"  Failed to load rectified image — skipping verification")
        return

    H_wf2img = P_world[:, [0, 1, 3]]  # world floor -> image (Z=0)

    with open(traj_path, "r") as f:
        lines = f.readlines()

    n_points = 0
    current_target = None
    prev_px = None
    for line in lines:
        fields = line.strip().split(",")
        if len(fields) < 9:
            continue
        feet_x = float(fields[7])
        feet_y = float(fields[8])
        target_id = int(fields[1])

        # SV local floor -> world floor
        pos_sv = np.array([feet_x, feet_y, 1.0])
        pos_world = H_lf2wf @ pos_sv
        pos_world /= pos_world[2]

        # world floor -> image
        pixel = H_wf2img @ pos_world
        x, y = pixel[0] / pixel[2], pixel[1] / pixel[2]
        ix, iy = int(round(x)), int(round(y))

        if 0 <= ix < img.shape[1] and 0 <= iy < img.shape[0]:
            cv2.circle(img, (ix, iy), 2, (0, 0, 255), -1)
            if current_target is not None and current_target == target_id and prev_px is not None:
                cv2.line(img, prev_px, (ix, iy), (0, 0, 255), 1)
        current_target = target_id
        prev_px = (ix, iy)
        n_points += 1

    cv2.imwrite(output_path, img)
    print(f"  Verification overlay saved ({n_points} points): {output_path}")


def save_projection_matrix(P_world: np.ndarray, output_path: str) -> None:
    """Save P_world in the same yaml format as the MV pipeline."""
    data = {"projectionMatrix_3x4": P_world.flatten().tolist()}
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        yaml.dump(data, f)
    print(f"  Saved world projection matrix: {output_path}")


def compute_world_to_map_transform(
    P_world: np.ndarray,
    alignment_data: list,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute the 3D similarity transform: world coords -> map pixels.

    SV equivalent of the MV pipeline's get_transform_from_cam0_to_layout()
    called with global projection matrices (layout_alignment_post_process.py
    lines 1146-1158).  Instead of triangulating from two cameras, we
    unproject each image point onto the ground plane (Z=0) through P_world.

    Args:
        P_world: 3x4 world-frame projection matrix.
        alignment_data: list of [[img_x, img_y], [map_x, map_y]] pairs.

    Returns:
        (scale, R, t) such that  map_pixel ≈ scale * R @ world_pt + t
    """
    H_wf2img = P_world[:, [0, 1, 3]]          # world floor (Z=0) -> image
    H_img2wf = np.linalg.inv(H_wf2img)        # image -> world floor

    world_pts_3d = []
    map_pts_3d = []
    for img_pt, map_pt in alignment_data:
        p_img = np.array([img_pt[0], img_pt[1], 1.0])
        p_wf = H_img2wf @ p_img
        p_wf /= p_wf[2]
        world_pts_3d.append([p_wf[0], p_wf[1], 0.0])
        map_pts_3d.append([map_pt[0], map_pt[1], 0.0])

    world_pts_3d = np.array(world_pts_3d, dtype=np.float64)
    map_pts_3d = np.array(map_pts_3d, dtype=np.float64)

    # Umeyama convention: X_true = scale * R @ X_recon + t
    #   X_true  = map pixels,  X_recon = world 3D
    scale, R, t = umeyama_alignment(map_pts_3d, world_pts_3d)
    if scale is None:
        raise RuntimeError("Umeyama alignment failed for world-to-map transform")

    return scale, R, t


def save_world_to_map_transform(
    scale: float,
    R: np.ndarray,
    t: np.ndarray,
    ppm: float,
    output_path: str,
) -> None:
    """Save transform_world_to_map.json with computed and expected values.

    The expected values come from the analytical T_world2map relationship:
      scale = ppm,  R = diag(1, -1, -1),  t = 0.
    The 3rd diagonal of R is -1 (not +1) because Umeyama enforces det(R)=+1,
    and the Y-flip (det=-1 in 2D) is compensated by flipping Z as well.
    """
    expected_scale = ppm
    expected_R = np.diag([1.0, -1.0, -1.0])
    expected_t = np.zeros(3)

    transform = {
        "scale": float(scale),
        "R": R.tolist(),
        "t": t.tolist(),
        "expected": {
            "scale": expected_scale,
            "R": expected_R.tolist(),
            "t": expected_t.tolist(),
        },
    }
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(transform, f, indent=2)
    print(f"  Saved world-to-map transform: {output_path}")

    # Diagnostics
    scale_err = abs(scale - expected_scale) / max(abs(expected_scale), 1e-12)
    R_err = np.linalg.norm(R - expected_R, ord="fro")
    t_err = np.linalg.norm(t - expected_t)
    print(f"  Deviation from expected:  "
          f"scale={scale_err:.6f} (rel),  "
          f"||R-R_exp||_F={R_err:.6f},  "
          f"||t-t_exp||={t_err:.6f}")
    if scale_err > 0.05:
        print(f"  Warning: scale deviates >{5}% from ppm — check correspondences")


def main():
    parser = argparse.ArgumentParser(
        description="SV-only post-processing: build world-frame projection matrix"
    )
    parser.add_argument("-i", "--sv_results_dir", required=True,
                        help="Single-view camera output directory (e.g. single_view_results/cam_00)")
    parser.add_argument("-a", "--alignment_base_dir", required=True,
                        help="Base directory containing alignment_data/ folder")
    parser.add_argument("-o", "--output_dir", required=True,
                        help="Output directory for P_world and verification")
    parser.add_argument("-c", "--config", required=True,
                        help="Path to mv_amc_config.yaml (used to find eval_config.yaml)")
    parser.add_argument("-l", "--layout_image_path", required=False, default=None,
                        help="Path to layout image (for verification overlay on map)")
    args = parser.parse_args()

    # --- Load inputs ---
    cam_info_path = os.path.join(args.sv_results_dir, "camInfo_hyper_00_opencv.yaml")
    if not os.path.exists(cam_info_path):
        print(f"Error: SV camera info not found: {cam_info_path}")
        sys.exit(1)

    alignment_data_path = os.path.join(args.alignment_base_dir,
                                        "alignment_data", "alignment_data.json")
    if not os.path.exists(alignment_data_path):
        print(f"Error: alignment_data.json not found: {alignment_data_path}")
        sys.exit(1)

    eval_config_path = os.path.join(os.path.dirname(args.config), "eval_config.yaml")
    if not os.path.exists(eval_config_path):
        print(f"Error: eval_config.yaml not found: {eval_config_path}")
        sys.exit(1)

    print("=" * 60)
    print("SV-Only Post-Processing")
    print("=" * 60)

    # Load P_sv
    print(f"\nLoading SV projection matrix from {cam_info_path}")
    P_sv = load_sv_projection_matrix(cam_info_path)
    print(f"  P_sv shape: {P_sv.shape}")

    # Load alignment data (single-cam format)
    print(f"\nLoading alignment data from {alignment_data_path}")
    with open(alignment_data_path, "r") as f:
        alignment_data = json.load(f)
    print(f"  {len(alignment_data)} correspondence pairs loaded")

    # Load ppm
    with open(eval_config_path, "r") as f:
        eval_cfg = yaml.safe_load(f)
    ppm = float(eval_cfg.get("layout_px_per_m", 1.0))
    print(f"  layout_px_per_m: {ppm}")

    # --- Compute P_world ---
    print("\nComputing image-to-map homography...")
    H_img2map = compute_image_to_map_homography(alignment_data)

    print("\nConstructing world projection matrix...")
    P_world, s = compute_world_projection_matrix(P_sv, H_img2map, ppm)
    print(f"\n  P_world:\n{P_world}")

    # --- Save output ---
    os.makedirs(args.output_dir, exist_ok=True)
    output_yaml = os.path.join(args.output_dir, "camInfo_world.yaml")
    save_projection_matrix(P_world, output_yaml)

    # --- World-to-map transform (consumed by roi_verification, calibration_exporter, etc.) ---
    print("\nComputing world-to-map transform...")
    w2m_scale, w2m_R, w2m_t = compute_world_to_map_transform(
        P_world, alignment_data)
    print(f"  scale: {w2m_scale:.6f}")
    print(f"  R:\n{w2m_R}")
    print(f"  t: {w2m_t}")

    w2m_output = os.path.join(args.output_dir, "transform_world_to_map.json")
    save_world_to_map_transform(w2m_scale, w2m_R, w2m_t, ppm, w2m_output)

    # --- Verification ---
    print("\nRunning verification reprojection test...")
    traj_path = os.path.join(args.sv_results_dir, "trajDump_Stream_0_3d.txt")
    rectified_img_path = os.path.join(args.sv_results_dir, "rectified.jpg")
    verification_output = os.path.join(args.output_dir, "verification_overlay.png")

    T_world2map = np.array([
        [ppm, 0.0, 0.0],
        [0.0, -ppm, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    floor_cols = [c for c in range(4) if c != SV_HEIGHT_COL]
    G_sv = P_sv[:, floor_cols]
    H_lf2wf = np.linalg.inv(T_world2map) @ H_img2map @ G_sv

    verify_reprojection(P_world, traj_path, G_sv, H_lf2wf,
                        rectified_img_path, verification_output)

    # Also save verification on the layout map if provided
    if args.layout_image_path and os.path.exists(args.layout_image_path):
        layout_img = cv2.imread(args.layout_image_path)
        if layout_img is not None:
            map_output = os.path.join(args.output_dir, "verification_map_overlay.png")
            verify_on_map(traj_path, G_sv, H_lf2wf, T_world2map,
                          layout_img, map_output)

    print("\n" + "=" * 60)
    print("SV-Only Post-Processing Complete!")
    print("=" * 60)
    print(f"  P_world:              {output_yaml}")
    print(f"  transform_world_map:  {w2m_output}")
    print(f"  Verification:         {verification_output}")


def verify_on_map(
    traj_path: str,
    G_sv: np.ndarray,
    H_lf2wf: np.ndarray,
    T_world2map: np.ndarray,
    layout_img: np.ndarray,
    output_path: str,
) -> None:
    """Draw trajectory on the layout map for visual verification."""
    img = layout_img.copy()
    with open(traj_path, "r") as f:
        lines = f.readlines()

    current_target = None
    prev_px = None
    for line in lines:
        fields = line.strip().split(",")
        if len(fields) < 9:
            continue
        feet_x = float(fields[7])
        feet_y = float(fields[8])
        target_id = int(fields[1])

        pos_sv = np.array([feet_x, feet_y, 1.0])
        pos_world = H_lf2wf @ pos_sv
        pos_world /= pos_world[2]
        pos_map = T_world2map @ pos_world
        x, y = pos_map[0] / pos_map[2], pos_map[1] / pos_map[2]
        ix, iy = int(round(x)), int(round(y))

        if 0 <= ix < img.shape[1] and 0 <= iy < img.shape[0]:
            cv2.circle(img, (ix, iy), 2, (0, 0, 255), -1)
            if current_target is not None and current_target == target_id and prev_px is not None:
                cv2.line(img, prev_px, (ix, iy), (0, 0, 255), 1)
        current_target = target_id
        prev_px = (ix, iy)

    cv2.imwrite(output_path, img)
    print(f"  Map verification overlay saved: {output_path}")


if __name__ == "__main__":
    main()
