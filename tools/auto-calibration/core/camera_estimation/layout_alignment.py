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
Layout Alignment - Linear-media landmark selection UI

This script runs after optional rectification and before calibration. Camera
images must already be linear. The output alignment_data.json is therefore in
the same linear pixel space consumed by calibration; no later remapping occurs.

Usage:
    python layout_alignment.py -v <video_dir> -o <output_dir> -l <layout.png>
"""

import cv2
import numpy as np
import os
import sys
import argparse
import json
import yaml
import glob
import re
import shutil

# Add the current directory to Python path to allow importing local modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(project_root)

# Global variables for GUI
g_landmarks = []  # landmarks selected by user


def select_landmark(event, x, y, flags, param):
    """Mouse callback function for selecting landmarks"""
    global g_landmarks
    
    if event == cv2.EVENT_LBUTTONDOWN:
        g_landmarks.append(np.array([x, y]))


def run_landmark_selection_ui(stacked_img):
    """Run landmark selection UI for manual alignment
    
    Args:
        stacked_img: horizontally stacked image of [cam0, cam1, layout]
    
    Instructions displayed to user:
    - Click on corresponding points in cam0, cam1, and layout (in that order)
    - Select at least 4 sets of corresponding points (12 clicks total)
    - Press 'q' to quit when done
    """
    cv2.namedWindow('Select landmarks', cv2.WINDOW_NORMAL)
    cv2.setMouseCallback('Select landmarks', select_landmark)
    
    # Resize window to match image size (can be larger than screen - user can move/scroll)
    img_height, img_width = stacked_img.shape[:2]
    cv2.resizeWindow('Select landmarks', img_width, img_height)
    
    global g_landmarks
    g_landmarks = []
    
    # Colors for each set of 3 points (cam0, cam1, layout)
    colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (255, 255, 0),
              (255, 0, 255), (0, 255, 255), (128, 0, 255), (255, 128, 0)]
    
    # Create a copy for drawing
    display_img = stacked_img.copy()
    
    print("\n" + "="*60)
    print("LAYOUT ALIGNMENT - Landmark Selection")
    print("="*60)
    print(f"Window size: {img_width} x {img_height} pixels")
    print("(Window may be larger than screen - drag to move)")
    print("")
    print("Instructions:")
    print("  1. Click on a landmark in Camera 0 image (left)")
    print("  2. Click on the SAME landmark in Camera 1 image (middle)")
    print("  3. Click on the SAME landmark in Layout image (right)")
    print("  4. Repeat for at least 4 landmarks (12 clicks minimum)")
    print("  5. Press 'q' to quit when done")
    print("="*60 + "\n")

    while True:
        # Draw landmarks on the display image
        display_img = stacked_img.copy()
        for i, pt in enumerate(g_landmarks):
            # Color based on which set of 3 this point belongs to
            color_idx = (i // 3) % len(colors)
            # Larger circles for better visibility at 50% scale
            cv2.circle(display_img, (int(pt[0]), int(pt[1])), 10, colors[color_idx], -1)
            cv2.circle(display_img, (int(pt[0]), int(pt[1])), 12, (255, 255, 255), 2)
            # Add point number
            point_num = i // 3 + 1
            cv2.putText(display_img, str(point_num), (int(pt[0]) + 15, int(pt[1]) + 5),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        # Show progress - larger text for visibility
        num_complete_sets = len(g_landmarks) // 3
        remaining_in_set = len(g_landmarks) % 3
        status_text = f"Complete sets: {num_complete_sets} | "
        if remaining_in_set == 0:
            status_text += "Click on Cam0"
        elif remaining_in_set == 1:
            status_text += "Click on Cam1"
        else:
            status_text += "Click on Layout"
        
        cv2.putText(display_img, status_text, (10, 40), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)
        cv2.putText(display_img, "Press 'q' to finish", (10, 80),
                   cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)

        cv2.imshow('Select landmarks', display_img)
        key = cv2.waitKey(50) & 0xFF
        if key == ord('q'):
            break
    
    cv2.destroyAllWindows()


def find_background_images(backgrounds_dir, cam_dirs):
    """Find background images for each camera
    
    Args:
        backgrounds_dir: path to backgrounds folder
        cam_dirs: list of camera directory names ['cam_00', 'cam_01', ...]
    
    Returns:
        dict: mapping from camera index to background image path
    """
    bg_images = {}
    
    for i, cam_dir in enumerate(cam_dirs):
        bg_path = os.path.join(backgrounds_dir, cam_dir, 'background.jpg')
        if os.path.exists(bg_path):
            bg_images[i] = bg_path
        else:
            print(f"Warning: Background not found for {cam_dir}: {bg_path}")
    
    return bg_images


def read_config(config_path):
    """Read configuration file"""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def main(args):
    """Main function for layout alignment UI
    
    Args:
        args: command line arguments with video_dir, output_dir, layout_image_path
    """
    global g_landmarks
    
    # Read config to get camera directories and video resolution
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, '../../configs/config_AutoMagicCalib/mv_amc_config.yaml')
    
    if not os.path.exists(config_path):
        # Try alternative path for docker
        config_path = '/auto-magic-calib/configs/config_AutoMagicCalib/mv_amc_config.yaml'
    
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    cfg = read_config(config_path)
    cam_dirs = cfg.get('cam_dir', ['cam_00', 'cam_01'])
    video_resolution = cfg.get('video_resolution', [1920, 1080])
    img_w = video_resolution[0]
    
    print(f"Camera directories: {cam_dirs}")
    print(f"Video resolution: {video_resolution}")
    
    # Alignment is collected directly in linear image space.
    alignment_data_dir = os.path.join(args.output_dir, 'alignment_data')
    os.makedirs(alignment_data_dir, exist_ok=True)
    alignment_data_path = os.path.join(alignment_data_dir, 'alignment_data.json')

    # Check if alignment_data.json exists alongside the layout image (pre-populated)
    layout_dir = os.path.dirname(args.layout_image_path)
    source_alignment_path = os.path.join(layout_dir, 'alignment_data.json')
    if os.path.exists(source_alignment_path):
        shutil.copy(source_alignment_path, alignment_data_path)
        print(f"Copied pre-existing alignment data from {source_alignment_path} to {alignment_data_path}")
        layout_copy_path = os.path.join(alignment_data_dir, 'layout.png')
        shutil.copy(args.layout_image_path, layout_copy_path)
    
    # Annotations are also already expressed in linear image space.
    source_annotations_path = os.path.join(layout_dir, 'annotations.json')
    if os.path.exists(source_annotations_path):
        dest_annotations_path = os.path.join(alignment_data_dir, 'annotations.json')
        shutil.copy(source_annotations_path, dest_annotations_path)
        print(f"Copied pre-existing annotations from {source_annotations_path} to {dest_annotations_path}")
    
    if os.path.exists(alignment_data_path):
        print(f"\nFound existing alignment_data.json at {alignment_data_path}")
        print("Skipping manual alignment UI. Delete the file to re-run alignment.")
        return
    
    # Find background images
    backgrounds_dir = os.path.join(args.output_dir, 'backgrounds')
    if not os.path.exists(backgrounds_dir):
        raise FileNotFoundError(f"Backgrounds directory not found: {backgrounds_dir}")
    
    bg_images = find_background_images(backgrounds_dir, cam_dirs)
    
    if len(bg_images) < 2:
        raise ValueError(f"Need at least 2 background images, found {len(bg_images)}")
    
    # Load images
    scale = 0.5  # Scale for display
    
    # Load layout image
    if not os.path.exists(args.layout_image_path):
        raise FileNotFoundError(f"Layout image not found: {args.layout_image_path}")
    
    layout_img_orig = cv2.imread(args.layout_image_path)
    if layout_img_orig is None:
        raise ValueError(f"Failed to load layout image: {args.layout_image_path}")
    # Keep original layout for resize_ratio calculation (do NOT pre-scale!)
    layout_img = layout_img_orig.copy()
    
    # Load camera 0 background
    cam0_path = bg_images.get(0)
    if cam0_path is None:
        raise FileNotFoundError(f"Background for camera 0 not found")
    cam0_img = cv2.imread(cam0_path)
    if cam0_img is None:
        raise ValueError(f"Failed to load camera 0 background: {cam0_path}")
    cam0_img = cv2.resize(cam0_img, None, fx=scale, fy=scale)
    
    # Load camera 1 background
    cam1_path = bg_images.get(1)
    if cam1_path is None:
        raise FileNotFoundError(f"Background for camera 1 not found")
    cam1_img = cv2.imread(cam1_path)
    if cam1_img is None:
        raise ValueError(f"Failed to load camera 1 background: {cam1_path}")
    cam1_img = cv2.resize(cam1_img, None, fx=scale, fy=scale)
    
    # Resize layout to match camera image height if needed
    print(f"\nDEBUG: Original layout size: {layout_img.shape}")
    print(f"DEBUG: Scaled camera size: {cam0_img.shape}")
    if cam0_img.shape[0] != layout_img.shape[0]:
        resize_ratio = cam0_img.shape[0] / layout_img.shape[0]
        print(f"DEBUG: resize_ratio = {cam0_img.shape[0]} / {layout_img.shape[0]} = {resize_ratio}")
        layout_img = cv2.resize(layout_img, None, fx=resize_ratio, fy=resize_ratio)
    else:
        resize_ratio = 1.0
        print(f"DEBUG: Heights match, resize_ratio = 1.0")
    
    # Stack images horizontally: [cam0 | cam1 | layout]
    # Add labels - larger text for better visibility
    cv2.putText(cam0_img, "Camera 0", (20, cam0_img.shape[0] - 30),
               cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)
    cv2.putText(cam1_img, "Camera 1", (20, cam1_img.shape[0] - 30),
               cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)
    cv2.putText(layout_img, "Layout", (20, layout_img.shape[0] - 30),
               cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)
    
    merged_img = np.concatenate((cam0_img, cam1_img, layout_img), axis=1)
    
    # Draw vertical separator lines between images
    cam0_width = cam0_img.shape[1]
    cam1_width = cam1_img.shape[1]
    cv2.line(merged_img, (cam0_width, 0), (cam0_width, merged_img.shape[0]), (255, 255, 0), 3)
    cv2.line(merged_img, (cam0_width + cam1_width, 0), (cam0_width + cam1_width, merged_img.shape[0]), (255, 255, 0), 3)
    
    print(f"\nImage sizes (scaled):")
    print(f"  Camera 0: {cam0_img.shape}")
    print(f"  Camera 1: {cam1_img.shape}")
    print(f"  Layout: {layout_img.shape}")
    print(f"  Merged: {merged_img.shape}")
    
    # Run landmark selection UI
    run_landmark_selection_ui(merged_img)
    
    # Check if enough landmarks were selected
    if len(g_landmarks) < 12:  # Need at least 4 complete sets
        print(f"\nError: Need at least 4 landmark sets (12 clicks), got {len(g_landmarks)}")
        print("Please run alignment again and select more landmarks.")
        sys.exit(1)
    
    # Convert landmarks to alignment data format
    # Format: [[cam0_xy, cam1_xy, layout_xy], ...]
    alignment_data = np.array(g_landmarks, dtype=np.float64)
    alignment_data = alignment_data.reshape(-1, 3, 2)
    
    # Adjust coordinates to account for horizontal stacking
    # cam1 x-coordinates need to subtract cam0 width
    # layout x-coordinates need to subtract cam0 + cam1 width
    cam0_width_scaled = cam0_img.shape[1]
    cam1_width_scaled = cam1_img.shape[1]
    
    print(f"\nDEBUG: scale = {scale}, resize_ratio = {resize_ratio}")
    print(f"DEBUG: cam0_width_scaled = {cam0_width_scaled}, cam1_width_scaled = {cam1_width_scaled}")
    
    for i in range(alignment_data.shape[0]):
        # Adjust cam1 x-coordinate
        alignment_data[i][1][0] = alignment_data[i][1][0] - cam0_width_scaled
        # Adjust layout x-coordinate (and account for layout resize ratio)
        old_layout_x = alignment_data[i][2][0] - cam0_width_scaled - cam1_width_scaled
        old_layout_y = alignment_data[i][2][1]
        alignment_data[i][2][0] = old_layout_x / resize_ratio
        alignment_data[i][2][1] = old_layout_y / resize_ratio
        if i == 0:
            print(f"DEBUG: Point 0 layout: clicked=({old_layout_x}, {old_layout_y}) -> original=({alignment_data[i][2][0]}, {alignment_data[i][2][1]})")
    
    # Scale back to original resolution
    alignment_data[:, 0:2, :] /= scale  # Scale cam0 and cam1 coordinates
    alignment_data = alignment_data.astype(np.int64)
    
    print(f"\nAlignment data (linear image coordinates):")
    for i, pts in enumerate(alignment_data):
        print(f"  Point {i+1}: cam0={pts[0].tolist()}, cam1={pts[1].tolist()}, layout={pts[2].tolist()}")
    
    # Save alignment data
    with open(alignment_data_path, 'w') as f:
        json.dump(alignment_data.tolist(), f, indent=2)
    
    # Copy layout image to alignment_data directory
    layout_copy_path = os.path.join(alignment_data_dir, 'layout.png')
    shutil.copy(args.layout_image_path, layout_copy_path)
    
    print(f"\nAlignment data saved to: {alignment_data_path}")
    print(f"Layout image copied to: {layout_copy_path}")
    print("\nLayout alignment complete!")
    print("The coordinates are in LINEAR image space; no further transform is required.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Layout Alignment - Linear-media landmark selection')
    parser.add_argument('-v', '--video_dir', type=str,
                        help='Directory containing input video files (not used, kept for compatibility)')
    parser.add_argument('-o', '--output_dir', type=str, required=True,
                        help='Output directory (should contain backgrounds/ folder)')
    parser.add_argument('-l', '--layout_image_path', type=str, required=True,
                        help='Path to layout image file')
    args = parser.parse_args()
    
    # Validate output directory
    if not os.path.isdir(args.output_dir):
        os.makedirs(args.output_dir, exist_ok=True)
    
    main(args)
