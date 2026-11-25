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
Single-View Layout Alignment - Post-calibration landmark selection UI

Single-camera variant of layout_alignment.py.  The user clicks corresponding
floor-level landmarks on ONE camera image and the layout/floor plan.

Unlike the MV version (which runs BEFORE calibration on raw/distorted images),
this SV version runs AFTER SV calibration and uses the rectified image.
Coordinates are therefore already in rectified space and the output goes
directly to alignment_data.json (no distorted-to-rectified transform needed).

Output: alignment_data.json in the single-cam format:
    [[[cam_x, cam_y], [layout_x, layout_y]], ...]

Usage:
    python sv_layout_alignment.py -o <output_dir> -l <layout.png> [-b <rectified.jpg>]
"""

import argparse
import json
import os
import shutil
import sys

import cv2
import numpy as np
import yaml

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(project_root)

g_landmarks = []


def select_landmark(event, x, y, flags, param):
    """Mouse callback function for selecting landmarks."""
    global g_landmarks
    if event == cv2.EVENT_LBUTTONDOWN:
        g_landmarks.append(np.array([x, y]))


def run_landmark_selection_ui(stacked_img):
    """Run landmark selection UI for single-camera alignment.

    Args:
        stacked_img: horizontally stacked image of [camera | layout]
    """
    cv2.namedWindow('Select landmarks', cv2.WINDOW_NORMAL)
    cv2.setMouseCallback('Select landmarks', select_landmark)

    img_height, img_width = stacked_img.shape[:2]
    cv2.resizeWindow('Select landmarks', img_width, img_height)

    global g_landmarks
    g_landmarks = []

    colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (255, 255, 0),
              (255, 0, 255), (0, 255, 255), (128, 0, 255), (255, 128, 0)]

    print("\n" + "=" * 60)
    print("SV LAYOUT ALIGNMENT - Landmark Selection")
    print("=" * 60)
    print(f"Window size: {img_width} x {img_height} pixels")
    print("")
    print("Instructions:")
    print("  1. Click on a floor landmark in the Camera image (left)")
    print("  2. Click on the SAME landmark in the Layout image (right)")
    print("  3. Repeat for at least 4 landmarks (8 clicks minimum)")
    print("  4. Press 'q' to quit when done")
    print("=" * 60 + "\n")

    while True:
        display_img = stacked_img.copy()
        for i, pt in enumerate(g_landmarks):
            color_idx = (i // 2) % len(colors)
            cv2.circle(display_img, (int(pt[0]), int(pt[1])), 10, colors[color_idx], -1)
            cv2.circle(display_img, (int(pt[0]), int(pt[1])), 12, (255, 255, 255), 2)
            point_num = i // 2 + 1
            cv2.putText(display_img, str(point_num),
                        (int(pt[0]) + 15, int(pt[1]) + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        num_complete = len(g_landmarks) // 2
        remaining = len(g_landmarks) % 2
        status = f"Complete pairs: {num_complete} | "
        status += "Click on Camera" if remaining == 0 else "Click on Layout"

        cv2.putText(display_img, status, (10, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)
        cv2.putText(display_img, "Press 'q' to finish", (10, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)

        cv2.imshow('Select landmarks', display_img)
        key = cv2.waitKey(50) & 0xFF
        if key == ord('q'):
            break

    cv2.destroyAllWindows()


def main(args):
    global g_landmarks

    alignment_data_dir = os.path.join(args.output_dir, 'alignment_data')
    os.makedirs(alignment_data_dir, exist_ok=True)
    # Output directly as alignment_data.json (coords are already in rectified space)
    alignment_data_path = os.path.join(alignment_data_dir, 'alignment_data.json')

    # Check for pre-existing alignment data alongside the layout image
    layout_dir = os.path.dirname(args.layout_image_path)
    source_alignment_path = os.path.join(layout_dir, 'alignment_data.json')
    if os.path.exists(source_alignment_path):
        shutil.copy(source_alignment_path, alignment_data_path)
        print(f"Copied pre-existing alignment data from {source_alignment_path}")
        layout_copy_path = os.path.join(alignment_data_dir, 'layout.png')
        shutil.copy(args.layout_image_path, layout_copy_path)

    if os.path.exists(alignment_data_path):
        print(f"\nFound existing alignment_data.json at {alignment_data_path}")
        print("Skipping manual alignment UI. Delete the file to re-run.")
        return

    # --- Load images ---
    if not os.path.exists(args.layout_image_path):
        raise FileNotFoundError(f"Layout image not found: {args.layout_image_path}")

    layout_img_orig = cv2.imread(args.layout_image_path)
    if layout_img_orig is None:
        raise ValueError(f"Failed to load layout image: {args.layout_image_path}")
    layout_img = layout_img_orig.copy()

    # Find the camera background image
    cam_img_path = _find_camera_image(args)
    cam_img_orig = cv2.imread(cam_img_path)
    if cam_img_orig is None:
        raise ValueError(f"Failed to load camera image: {cam_img_path}")

    scale = 0.5
    cam_img = cv2.resize(cam_img_orig, None, fx=scale, fy=scale)

    # Resize layout to match camera image height
    if cam_img.shape[0] != layout_img.shape[0]:
        resize_ratio = cam_img.shape[0] / layout_img.shape[0]
        layout_img = cv2.resize(layout_img, None, fx=resize_ratio, fy=resize_ratio)
    else:
        resize_ratio = 1.0

    # Stack: [camera | layout]
    cv2.putText(cam_img, "Camera", (20, cam_img.shape[0] - 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)
    cv2.putText(layout_img, "Layout", (20, layout_img.shape[0] - 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)

    cam_width = cam_img.shape[1]
    merged_img = np.concatenate((cam_img, layout_img), axis=1)
    cv2.line(merged_img, (cam_width, 0), (cam_width, merged_img.shape[0]),
             (255, 255, 0), 3)

    print(f"\nImage sizes (scaled):")
    print(f"  Camera: {cam_img.shape}")
    print(f"  Layout: {layout_img.shape}")
    print(f"  Merged: {merged_img.shape}")

    # --- Run UI ---
    run_landmark_selection_ui(merged_img)

    if len(g_landmarks) < 8:
        print(f"\nError: Need at least 4 pairs (8 clicks), got {len(g_landmarks)}")
        sys.exit(1)

    # Convert to alignment data: pairs of [cam_xy, layout_xy]
    landmarks = np.array(g_landmarks, dtype=np.float64).reshape(-1, 2, 2)

    cam_width_scaled = cam_img.shape[1]
    for i in range(landmarks.shape[0]):
        # Layout x: subtract camera width, undo layout resize
        old_layout_x = landmarks[i][1][0] - cam_width_scaled
        old_layout_y = landmarks[i][1][1]
        landmarks[i][1][0] = old_layout_x / resize_ratio
        landmarks[i][1][1] = old_layout_y / resize_ratio

    # Scale camera coords back to full resolution (rectified image resolution)
    landmarks[:, 0, :] /= scale
    alignment_data = landmarks.astype(np.int64)

    print(f"\nAlignment data (rectified image coordinates):")
    for i, pts in enumerate(alignment_data):
        print(f"  Point {i+1}: cam={pts[0].tolist()}, layout={pts[1].tolist()}")

    # Save
    with open(alignment_data_path, 'w') as f:
        json.dump(alignment_data.tolist(), f, indent=2)

    layout_copy_path = os.path.join(alignment_data_dir, 'layout.png')
    shutil.copy(args.layout_image_path, layout_copy_path)

    print(f"\nSaved: {alignment_data_path}")
    print(f"Layout copied to: {layout_copy_path}")
    print("\nSV layout alignment complete!")
    print("Coordinates are in RECTIFIED image space (no further transform needed).")


def _find_camera_image(args):
    """Locate the camera background image from available sources."""
    # Explicit path takes priority
    if args.background_image and os.path.exists(args.background_image):
        return args.background_image

    # Try backgrounds/ directory (created by launch_LayoutAlignment.sh)
    backgrounds_dir = os.path.join(args.output_dir, 'backgrounds')
    if os.path.isdir(backgrounds_dir):
        # Read config to find cam_00 directory name
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(
            script_dir, '../../configs/config_AutoMagicCalib/mv_amc_config.yaml')
        if not os.path.exists(config_path):
            config_path = '/auto-magic-calib/configs/config_AutoMagicCalib/mv_amc_config.yaml'
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            cam_dir = cfg.get('cam_dir', ['cam_00'])[0]
        else:
            cam_dir = 'cam_00'

        bg_path = os.path.join(backgrounds_dir, cam_dir, 'background.jpg')
        if os.path.exists(bg_path):
            return bg_path

    raise FileNotFoundError(
        "Camera background image not found. Provide -b <path> or ensure "
        "backgrounds/<cam_dir>/background.jpg exists in the output directory."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='SV Layout Alignment - Single-camera landmark selection')
    parser.add_argument('-o', '--output_dir', type=str, required=True,
                        help='Output directory')
    parser.add_argument('-l', '--layout_image_path', type=str, required=True,
                        help='Path to layout image file')
    parser.add_argument('-b', '--background_image', type=str, default=None,
                        help='Path to camera background image (optional, auto-detected if omitted)')
    args = parser.parse_args()

    if not os.path.isdir(args.output_dir):
        os.makedirs(args.output_dir, exist_ok=True)

    main(args)
