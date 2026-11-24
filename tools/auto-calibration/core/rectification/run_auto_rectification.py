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

import numpy as np
import cv2
from core.utils.video_capture import open_video_capture, open_video_writer
import argparse
import os
import yaml
import json
import re
from tqdm import tqdm 
import sys

# Add current directory to Python path for local imports.
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# my package
import auto_rectification as ar
import distortion_models

def read_config(filename):
    """ Read configuration file

    Args:
        filename(str): configuration file path

    Returns:
        config(dict): configuration dictionary
    """
    with open(filename, 'r') as f:
        config = yaml.safe_load(f)
    return config

def extract_background(input_file, config, scale):
    """ Extract a background image from a video file

    Args:
        input_file(str): path to input video file
        config(dict): configuration dictionary
        scale(float): image resize scale factor

    Returns:
        bg_img(ndarray): background image as numpy array
    """
    print('Extracting background...')
    num_frames = config['preprocess']['num_frames']
    frame_step = config['preprocess']['frame_step']

    # read video
    cap = open_video_capture(input_file)
    frames = []
    count = 0
    pbar = tqdm(total=num_frames)
    while(cap.isOpened() and len(frames) < num_frames):
        count += 1
        ret, frame = cap.read()
        if not ret:
            break
        if count % frame_step == 0:
            frame = cv2.resize(frame, None, fx=scale, fy=scale)
            frames.append(frame)
            pbar.update(1)

    pbar.close()
    cap.release()
    try:
        cv2.destroyAllWindows()
    except cv2.error:
        # Headless OpenCV builds have no GUI backend.
        pass
    if not frames:
        raise RuntimeError(f"Could not extract background frames from {input_file}")
    # get background using median filter
    bg_img = np.median(frames, axis=0).astype(dtype=np.uint8)
    return bg_img

def rectify_image(image, lens_param, config, remap=None):
    """ Rectify image
    
    Args:
        image(ndarray): input image as numpy array
        lens_param(dict): lens parameters
        config(dict): configuration dictionary
    
    Returns:
        frame_rectified(ndarray): rectified image as numpy array
    """
    model, k1, k2 = distortion_models.normalize_lens_param(lens_param)
    frame_rectified = distortion_models.rectify_image_bgr(
        image, model, k1, k2, remap=remap
    )
    width = image.shape[1]
    height = image.shape[0]
    if config['output']['crop']: 
        # crop the image to the original size
        xc_u = frame_rectified.shape[1] // 2
        yc_u = frame_rectified.shape[0] // 2
        w = width // 2
        h = height // 2
        frame_rectified = frame_rectified[yc_u-h:yc_u+h, xc_u-w:xc_u+w]
    else:
        # resize 
        frame_rectified = cv2.resize(frame_rectified, (width, height))

    # Cropping yields a non-contiguous view; force a dense buffer so image/video
    # writers see the correct stride.
    return np.ascontiguousarray(frame_rectified)

def rectify_video(input_file, lens_param, config, out_path):
    """ Rectify video with lens parameters
    
    Args:
        input_file(str): path to input video file
        lens_param(dict): lens parameters
        config(dict): configuration dictionary
        out_path(str): path to output folder
    """
    print('\nRectifying video...')
    # read video
    cap = open_video_capture(input_file)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) 
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print('Image resolution: ', width, height)

    out_file = out_path + "/" + 'rectified.mp4'
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 30.0
    out = open_video_writer(out_file, fps, (width, height))

    # Camera geometry and lens parameters are fixed for the entire video.
    # Building these full-frame maps once avoids repeating the expensive
    # meshgrid and distortion calculations for every decoded frame.
    model, k1, k2 = distortion_models.normalize_lens_param(lens_param)
    remap = distortion_models.build_rectification_maps(
        width, height, model, k1, k2
    )

    # capture the frames
    for i in tqdm(range(n_frames)):
        ret, frame = cap.read()
        if not ret:
            break
        
        frame_rectified = rectify_image(frame, lens_param, config, remap=remap)
        
        # write the frame to the output video file
        out.write(frame_rectified)
    out.release()
    cap.release()


def extract_camera_index_from_path(output_path):
    """Extract camera index from output path or marker file.
    
    First tries to extract from path (e.g., 'single_view_results/cam_00' -> 0).
    If not found, looks for marker files like '.cam_00_marker' in the directory.
    This fallback is needed for docker mode where mount paths lose camera info.
    
    Args:
        output_path(str): output directory path containing camera name
        
    Returns:
        int: camera index (0, 1, 2, ...) or None if not found
    """
    print(f'DEBUG extract_camera_index: output_path={output_path}')
    
    # First, try to extract from path
    match = re.search(r'cam_(\d+)', output_path)
    if match:
        print(f'DEBUG: Found cam index {match.group(1)} from path')
        return int(match.group(1))
    
    # Fallback: look for marker file in the directory (for docker mode)
    print(f'DEBUG: Path does not contain cam_XX, checking for marker file...')
    is_dir = os.path.isdir(output_path)
    print(f'DEBUG: os.path.isdir({output_path}) = {is_dir}')
    if is_dir:
        files = os.listdir(output_path)
        print(f'DEBUG: Files in directory: {files}')
        for filename in files:
            marker_match = re.search(r'\.cam_(\d+)_marker', filename)
            if marker_match:
                print(f'DEBUG: Found marker file {filename}, cam index = {marker_match.group(1)}')
                return int(marker_match.group(1))
    
    print(f'DEBUG: No marker file found')
    return None


def generate_alignment_overlays(output_base_dir):
    """Generate overlay images showing alignment points on camera and layout images.
    
    Creates 5 overlay images:
    - cam0_orig_overlay.jpg: Points from alignment_data_orig on cam0 background
    - cam1_orig_overlay.jpg: Points from alignment_data_orig on cam1 background
    - cam0_rectified_overlay.jpg: Points from alignment_data on cam0 rectified
    - cam1_rectified_overlay.jpg: Points from alignment_data on cam1 rectified
    - layout_overlay.png: Layout points from alignment_data on layout image
    
    Args:
        output_base_dir(str): base output directory
    """
    alignment_data_dir = os.path.join(output_base_dir, 'alignment_data')
    alignment_orig_path = os.path.join(alignment_data_dir, 'alignment_data_orig.json')
    alignment_rect_path = os.path.join(alignment_data_dir, 'alignment_data.json')
    layout_path = os.path.join(alignment_data_dir, 'layout.png')
    
    # Check required files exist
    if not os.path.exists(alignment_orig_path) or not os.path.exists(alignment_rect_path):
        print('Alignment data files not found. Skipping overlay generation.')
        return
    
    # Load alignment data
    with open(alignment_orig_path, 'r') as f:
        orig_data = json.load(f)
    with open(alignment_rect_path, 'r') as f:
        rect_data = json.load(f)
    
    # Colors for each point set (consistent across all images)
    colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (255, 255, 0),
              (255, 0, 255), (0, 255, 255), (128, 0, 255), (255, 128, 0)]
    
    def draw_points_on_image(img, points, point_indices):
        """Draw numbered points on image.
        
        Args:
            img: Image to draw on (modified in place)
            points: List of [x, y] coordinates
            point_indices: List of point indices (for consistent numbering)
        """
        for idx, pt in zip(point_indices, points):
            color = colors[idx % len(colors)]
            x, y = int(pt[0]), int(pt[1])
            # Draw filled circle with white outline
            cv2.circle(img, (x, y), 12, color, -1)
            cv2.circle(img, (x, y), 14, (255, 255, 255), 2)
            # Draw point number
            cv2.putText(img, str(idx + 1), (x + 18, y + 8),
                       cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
        return img
    
    print('\nGenerating alignment overlay images...')
    
    # Camera paths
    # Use full-resolution backgrounds from backgrounds/ folder (created by extract_backgrounds.py)
    # NOT single_view_results/ which are scaled down for rectification search
    cam0_bg_path = os.path.join(output_base_dir, 'backgrounds', 'cam_00', 'background.jpg')
    cam1_bg_path = os.path.join(output_base_dir, 'backgrounds', 'cam_01', 'background.jpg')
    # Rectified images are at full resolution
    cam0_rect_path = os.path.join(output_base_dir, 'single_view_results', 'cam_00', 'rectified.jpg')
    cam1_rect_path = os.path.join(output_base_dir, 'single_view_results', 'cam_01', 'rectified.jpg')
    
    # Extract points for each image
    cam0_orig_pts = [pt[0] for pt in orig_data]  # cam0 points from original
    cam1_orig_pts = [pt[1] for pt in orig_data]  # cam1 points from original
    cam0_rect_pts = [pt[0] for pt in rect_data]  # cam0 points from rectified
    cam1_rect_pts = [pt[1] for pt in rect_data]  # cam1 points from rectified
    layout_pts = [pt[2] for pt in rect_data]     # layout points
    point_indices = list(range(len(orig_data)))
    
    # Generate overlays
    overlay_count = 0
    
    # 1. Cam0 original background with original points
    if os.path.exists(cam0_bg_path):
        img = cv2.imread(cam0_bg_path)
        print(f'  Cam0 background size: {img.shape}')
        print(f'  Cam0 orig points: {cam0_orig_pts}')
        img = draw_points_on_image(img, cam0_orig_pts, point_indices)
        out_path = os.path.join(alignment_data_dir, 'cam0_orig_overlay.jpg')
        cv2.imwrite(out_path, img)
        print(f'  Created: {out_path}')
        overlay_count += 1
    else:
        print(f'  Warning: {cam0_bg_path} not found')
    
    # 2. Cam1 original background with original points
    if os.path.exists(cam1_bg_path):
        img = cv2.imread(cam1_bg_path)
        print(f'  Cam1 background size: {img.shape}')
        print(f'  Cam1 orig points: {cam1_orig_pts}')
        img = draw_points_on_image(img, cam1_orig_pts, point_indices)
        out_path = os.path.join(alignment_data_dir, 'cam1_orig_overlay.jpg')
        cv2.imwrite(out_path, img)
        print(f'  Created: {out_path}')
        overlay_count += 1
    else:
        print(f'  Warning: {cam1_bg_path} not found')
    
    # 3. Cam0 rectified with rectified points
    if os.path.exists(cam0_rect_path):
        img = cv2.imread(cam0_rect_path)
        print(f'  Cam0 rectified size: {img.shape}')
        print(f'  Cam0 rect points: {cam0_rect_pts}')
        img = draw_points_on_image(img, cam0_rect_pts, point_indices)
        out_path = os.path.join(alignment_data_dir, 'cam0_rectified_overlay.jpg')
        cv2.imwrite(out_path, img)
        print(f'  Created: {out_path}')
        overlay_count += 1
    else:
        print(f'  Warning: {cam0_rect_path} not found')
    
    # 4. Cam1 rectified with rectified points
    if os.path.exists(cam1_rect_path):
        img = cv2.imread(cam1_rect_path)
        print(f'  Cam1 rectified size: {img.shape}')
        print(f'  Cam1 rect points: {cam1_rect_pts}')
        img = draw_points_on_image(img, cam1_rect_pts, point_indices)
        out_path = os.path.join(alignment_data_dir, 'cam1_rectified_overlay.jpg')
        cv2.imwrite(out_path, img)
        print(f'  Created: {out_path}')
        overlay_count += 1
    else:
        print(f'  Warning: {cam1_rect_path} not found')
    
    # 5. Layout with layout points
    if os.path.exists(layout_path):
        img = cv2.imread(layout_path)
        print(f'  Layout image size: {img.shape}')
        print(f'  Layout points: {layout_pts}')
        img = draw_points_on_image(img, layout_pts, point_indices)
        out_path = os.path.join(alignment_data_dir, 'layout_overlay.png')
        cv2.imwrite(out_path, img)
        print(f'  Created: {out_path}')
        overlay_count += 1
    else:
        print(f'  Warning: {layout_path} not found')
    
    print(f'Generated {overlay_count}/5 alignment overlay images.')


def generate_annotations_overlays(output_base_dir):
    """Generate overlay images showing annotation ROIs and tripwires on camera images.
    
    For each camera in annotations, creates:
    - cam{N}_annotations_orig_overlay.jpg: Annotations on original background
    - cam{N}_annotations_overlay.jpg: Annotations on rectified image
    
    Args:
        output_base_dir(str): base output directory
    """
    alignment_data_dir = os.path.join(output_base_dir, 'alignment_data')
    annotations_orig_path = os.path.join(alignment_data_dir, 'annotations_orig.json')
    annotations_rect_path = os.path.join(alignment_data_dir, 'annotations.json')
    
    # Check required files exist
    if not os.path.exists(annotations_orig_path) or not os.path.exists(annotations_rect_path):
        print('Annotations files not found. Skipping annotations overlay generation.')
        return
    
    # Load annotations data
    with open(annotations_orig_path, 'r') as f:
        orig_data = json.load(f)
    with open(annotations_rect_path, 'r') as f:
        rect_data = json.load(f)
    
    # Colors for different annotation types
    roi_color = (0, 255, 0)        # Green for ROIs
    tripwire_color = (0, 0, 255)   # Red for tripwire lines
    tripdir_color = (0, 255, 255)  # Yellow for tripwire directions
    
    def draw_annotations_on_image(img, cam_annotations):
        """Draw ROIs, tripwire lines, and tripwire directions on image.
        
        Args:
            img: Image to draw on (modified in place)
            cam_annotations: Dict with 'rois', 'tripwireLines', 'tripwireDirections'
        """
        # Draw ROIs (polygons)
        for roi in cam_annotations.get('rois', []):
            points = roi.get('points', [])
            if len(points) >= 3:
                pts = np.array([[int(p['x']), int(p['y'])] for p in points], np.int32)
                pts = pts.reshape((-1, 1, 2))
                cv2.polylines(img, [pts], isClosed=True, color=roi_color, thickness=3)
                # Draw vertices
                for p in points:
                    cv2.circle(img, (int(p['x']), int(p['y'])), 6, roi_color, -1)
        
        # Draw tripwire lines
        for tripwire in cam_annotations.get('tripwireLines', []):
            start = tripwire.get('start', {})
            end = tripwire.get('end', {})
            if 'x' in start and 'y' in start and 'x' in end and 'y' in end:
                pt1 = (int(start['x']), int(start['y']))
                pt2 = (int(end['x']), int(end['y']))
                cv2.line(img, pt1, pt2, tripwire_color, thickness=4)
                cv2.circle(img, pt1, 8, tripwire_color, -1)
                cv2.circle(img, pt2, 8, tripwire_color, -1)
        
        # Draw tripwire directions (with arrow)
        for tripdir in cam_annotations.get('tripwireDirections', []):
            start = tripdir.get('start', {})
            end = tripdir.get('end', {})
            if 'x' in start and 'y' in start and 'x' in end and 'y' in end:
                pt1 = (int(start['x']), int(start['y']))
                pt2 = (int(end['x']), int(end['y']))
                cv2.arrowedLine(img, pt1, pt2, tripdir_color, thickness=3, tipLength=0.1)
        
        return img
    
    print('\nGenerating annotations overlay images...')
    overlay_count = 0
    
    # Get all camera keys from both orig and rect data
    all_cam_keys = set(orig_data.keys()) | set(rect_data.keys())
    
    for cam_key in sorted(all_cam_keys):
        # Extract camera index from key (e.g., "cam_00.mp4" -> 0)
        match = re.search(r'cam_(\d+)', cam_key)
        if not match:
            continue
        cam_idx = int(match.group(1))
        
        # Build image paths for this camera
        bg_path = os.path.join(output_base_dir, 'backgrounds', f'cam_{cam_idx:02d}', 'background.jpg')
        rect_path = os.path.join(output_base_dir, 'single_view_results', f'cam_{cam_idx:02d}', 'rectified.jpg')
        
        # Generate original overlay
        if cam_key in orig_data and os.path.exists(bg_path):
            img = cv2.imread(bg_path)
            if img is not None:
                img = draw_annotations_on_image(img, orig_data[cam_key])
                out_path = os.path.join(alignment_data_dir, f'cam{cam_idx}_annotations_orig_overlay.jpg')
                cv2.imwrite(out_path, img)
                print(f'  Created: {out_path}')
                overlay_count += 1
        elif cam_key in orig_data:
            print(f'  Warning: Background not found for {cam_key}: {bg_path}')
        
        # Generate rectified overlay
        if cam_key in rect_data and os.path.exists(rect_path):
            img = cv2.imread(rect_path)
            if img is not None:
                img = draw_annotations_on_image(img, rect_data[cam_key])
                out_path = os.path.join(alignment_data_dir, f'cam{cam_idx}_annotations_overlay.jpg')
                cv2.imwrite(out_path, img)
                print(f'  Created: {out_path}')
                overlay_count += 1
        elif cam_key in rect_data:
            print(f'  Warning: Rectified image not found for {cam_key}: {rect_path}')
    
    print(f'Generated {overlay_count} annotations overlay images.')


def _process_annotations_data(cam_idx, annotations_orig_path, annotations_output_path,
                               lens_param, width, height, skip_rectification, finalize_now=False):
    """Rectify annotations for this camera using its lens parameters.
    
    Each camera rectifies its own annotations and updates the output file.
    Always reads from orig to get un-rectified points, merges with existing output.
    
    Args:
        cam_idx(int): camera index (0, 1, 2, ...)
        annotations_orig_path(str): path to annotations_orig.json
        annotations_output_path(str): path to output file
        lens_param(dict): lens distortion parameters for THIS camera
        width(int): image width
        height(int): image height
        skip_rectification(bool): True if rectification is skipped
        finalize_now(bool): True if overlays should be generated
    """
    if not os.path.exists(annotations_orig_path):
        # No annotations file - this is optional, just skip silently
        return
    
    # Camera key in annotations (e.g., "cam_00")
    cam_key = f'cam_{cam_idx:02d}'
    
    # Always load from ORIGINAL to get this camera's un-rectified points
    with open(annotations_orig_path, 'r') as f:
        orig_annotations = json.load(f)
    
    # Check if this camera has annotations
    if cam_key not in orig_annotations:
        # No per-stream ROIs/tripwires for this camera. Still copy layout globals into
        # alignment_data/annotations.json when present so globals-only projects match the
        # on-disk layout produced after the first cam_* save + rectification.
        GLOBAL_RT_KEY = "global_rois_tripwires"
        if GLOBAL_RT_KEY not in orig_annotations:
            return
        if os.path.exists(annotations_output_path):
            try:
                with open(annotations_output_path, "r", encoding="utf-8") as f:
                    annotations_data = json.load(f)
            except Exception:
                annotations_data = {}
        else:
            annotations_data = {}
        annotations_data[GLOBAL_RT_KEY] = json.loads(
            json.dumps(orig_annotations[GLOBAL_RT_KEY])
        )
        os.makedirs(os.path.dirname(annotations_output_path), exist_ok=True)
        with open(annotations_output_path, "w", encoding="utf-8") as f:
            json.dump(annotations_data, f, indent=2)
        print(
            f"\nNo {cam_key} in annotations_orig — wrote {GLOBAL_RT_KEY} to {annotations_output_path}"
        )
        return
    
    print(f'\nProcessing annotations for camera {cam_idx}...')
    
    # Load existing output file if it exists (to preserve other cameras' rectified data)
    if os.path.exists(annotations_output_path):
        with open(annotations_output_path, 'r') as f:
            annotations_data = json.load(f)
    else:
        # Start fresh from original
        annotations_data = json.loads(json.dumps(orig_annotations))  # deep copy
    
    # Get this camera's ORIGINAL annotations (always rectify from original)
    cam_annotations = json.loads(json.dumps(orig_annotations[cam_key]))  # deep copy
    
    if skip_rectification:
        print(f'Rectification skipped for camera {cam_idx} - keeping original annotation coordinates')
    else:
        print(f'Transforming annotation coordinates for {cam_key}...')
        
        def rectify_point(pt):
            """Rectify a single point {x, y}."""
            pts_array = np.array([[[pt['x'], pt['y']]]], dtype=np.float64)
            rectified = distortion_models.undistort_points(pts_array, width, height, lens_param)
            return {'x': float(rectified[0][0]), 'y': float(rectified[0][1])}
        
        # Process ROIs
        for roi in cam_annotations.get('rois', []):
            roi['points'] = [rectify_point(pt) for pt in roi['points']]
        
        # Process tripwire lines
        for tripwire in cam_annotations.get('tripwireLines', []):
            tripwire['start'] = rectify_point(tripwire['start'])
            tripwire['end'] = rectify_point(tripwire['end'])
        
        # Process tripwire directions
        for tripdir in cam_annotations.get('tripwireDirections', []):
            tripdir['start'] = rectify_point(tripdir['start'])
            tripdir['end'] = rectify_point(tripdir['end'])
    
    # Update this camera's entry in the output data
    annotations_data[cam_key] = cam_annotations
    
    # Always save to output file (each camera updates the file when it finishes)
    with open(annotations_output_path, 'w') as f:
        json.dump(annotations_data, f, indent=2)
    print(f'Updated annotations.json with {cam_key} rectified data')
    
    # Generate overlays when alignment is complete (so we have all rectified images available)
    if finalize_now:
        alignment_data_dir = os.path.dirname(annotations_output_path)
        output_base_dir = os.path.dirname(alignment_data_dir)
        generate_annotations_overlays(output_base_dir)


def process_alignment_data(output_base_dir, lens_param, width, height, skip_rectification, camera_output_path):
    """Process alignment_data_orig.json and annotations_orig.json incrementally for each camera.
    
    Each camera only rectifies its own points. Uses a partial file to track progress.
    Final alignment_data.json and annotations.json are created when both cam0 and cam1 are processed.
    
    Args:
        output_base_dir(str): base output directory containing alignment_data_orig.json
        lens_param(dict): lens distortion parameters for THIS camera
        width(int): image width
        height(int): image height
        skip_rectification(bool): True if rectification is skipped for THIS camera
        camera_output_path(str): output path for current camera (to extract camera index)
    """
    # Determine which camera we're processing
    cam_idx = extract_camera_index_from_path(camera_output_path)
    if cam_idx is None:
        print(f'Warning: Could not determine camera index from path: {camera_output_path}')
        print('Skipping alignment data processing.')
        return
    
    # Paths
    alignment_data_dir = os.path.join(output_base_dir, 'alignment_data')
    alignment_orig_path = os.path.join(alignment_data_dir, 'alignment_data_orig.json')
    alignment_partial_path = os.path.join(alignment_data_dir, 'alignment_data_partial.json')
    alignment_output_path = os.path.join(alignment_data_dir, 'alignment_data.json')
    
    # Annotations paths (ROI/tripwire data)
    annotations_orig_path = os.path.join(alignment_data_dir, 'annotations_orig.json')
    annotations_output_path = os.path.join(alignment_data_dir, 'annotations.json')
    
    # Alignment data only has cam0 and cam1, but annotations can have any camera
    # Process alignment only for cam0/cam1, but always process annotations
    alignment_all_processed = False
    
    if cam_idx not in [0, 1]:
        # Camera not in alignment data - skip alignment processing but continue to annotations
        print(f'Camera {cam_idx} is not in alignment data (only cam0 and cam1). Skipping alignment.')
        # Check if alignment is already complete (for finalize_now decision)
        alignment_all_processed = os.path.exists(alignment_output_path)
    elif os.path.exists(alignment_output_path):
        print(f'alignment_data.json already exists at {alignment_output_path}')
        print('Skipping alignment data processing (already complete).')
        alignment_all_processed = True
    elif not os.path.exists(alignment_orig_path):
        print(f'No alignment_data_orig.json found at {alignment_orig_path}')
        print('Layout alignment was not run before calibration. Skipping alignment.')
    else:
        # Process alignment data for cam0/cam1
        print(f'\nProcessing alignment data for camera {cam_idx}...')
        
        # Load existing partial data or start from original
        if os.path.exists(alignment_partial_path):
            print(f'Loading partial alignment data from: {alignment_partial_path}')
            with open(alignment_partial_path, 'r') as f:
                partial_data = json.load(f)
            alignment_data = partial_data['data']
            processed_cameras = set(partial_data.get('processed_cameras', []))
        else:
            print(f'Loading original alignment data from: {alignment_orig_path}')
            with open(alignment_orig_path, 'r') as f:
                alignment_data = json.load(f)
            processed_cameras = set()
        
        # Check if this camera was already processed
        if cam_idx not in processed_cameras:
            # Process this camera's points
            # alignment_data format: [[cam0_xy, cam1_xy, layout_xy], ...]
            # cam_idx 0 -> index 0, cam_idx 1 -> index 1
            
            if skip_rectification:
                print(f'Rectification skipped for camera {cam_idx} - keeping original coordinates')
                # Points stay the same, just mark as processed
            else:
                print(f'Transforming camera {cam_idx} coordinates from distorted to rectified space...')
                
                for point_set in alignment_data:
                    cam_pt = point_set[cam_idx]  # [x, y] in distorted image
                    
                    # Transform this camera's point from distorted to rectified space
                    cam_pts_array = np.array([[cam_pt]], dtype=np.float64)
                    cam_rectified = distortion_models.undistort_points(cam_pts_array, width, height, lens_param)
                    
                    # Update in place
                    point_set[cam_idx] = [int(cam_rectified[0][0]), int(cam_rectified[0][1])]
            
            # Mark this camera as processed
            processed_cameras.add(cam_idx)
            
            # Check if all required cameras are processed (cam0 and cam1)
            required_cameras = {0, 1}
            alignment_all_processed = required_cameras.issubset(processed_cameras)
            
            if alignment_all_processed:
                # Save final alignment_data.json
                with open(alignment_output_path, 'w') as f:
                    json.dump(alignment_data, f, indent=2)
                print(f'\nAll cameras processed! Saved final alignment_data.json to: {alignment_output_path}')
                
                # Clean up partial file
                if os.path.exists(alignment_partial_path):
                    os.remove(alignment_partial_path)
                    print(f'Removed partial file: {alignment_partial_path}')
                
                # Log the transformation summary
                with open(alignment_orig_path, 'r') as f:
                    orig_data = json.load(f)
                print('\nCoordinate transformation summary:')
                for i, (orig, rect) in enumerate(zip(orig_data, alignment_data)):
                    print(f'  Point {i+1}:')
                    print(f'    cam0: {orig[0]} -> {rect[0]}')
                    print(f'    cam1: {orig[1]} -> {rect[1]}')
                    print(f'    layout: {orig[2]} (unchanged)')
                
                # Generate overlay visualizations
                generate_alignment_overlays(output_base_dir)
            else:
                # Save partial progress
                partial_data = {
                    'data': alignment_data,
                    'processed_cameras': list(processed_cameras)
                }
                with open(alignment_partial_path, 'w') as f:
                    json.dump(partial_data, f, indent=2)
                
                remaining = required_cameras - processed_cameras
                print(f'\nCamera {cam_idx} processed. Saved partial data to: {alignment_partial_path}')
                print(f'Waiting for cameras {remaining} to complete.')
        else:
            print(f'Camera {cam_idx} already processed. Skipping alignment.')
            alignment_all_processed = os.path.exists(alignment_output_path)
    
    # Process annotations (ROI/tripwire data) for ANY camera
    # This runs even for cameras not in alignment data (cam2, cam3, etc.)
    _process_annotations_data(
        cam_idx, annotations_orig_path, annotations_output_path,
        lens_param, width, height, skip_rectification, finalize_now=alignment_all_processed
    )

def auto_rectification(input_file, config, args):
    """ Auto rectification main function
    
    Args:
        input_file(str): path to input video file
        config(dict): configuration dictionary
        args(argparse): command line arguments
    """
    # Get video dimensions for alignment processing
    cap = open_video_capture(input_file)
    video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    
    k1_file = getattr(args, 'k1_file', None)

    if k1_file:
        # External k1 mode: use pre-estimated k1 from model runner (GeoCalib),
        # skip the line-based grid search entirely.
        print(f'\nUsing external k1 from: {k1_file}\n')
        with open(k1_file) as f:
            dist_data = yaml.safe_load(f) or {}
        model, k1, k2 = distortion_models.normalize_lens_param(dist_data)
        param_est = distortion_models.params_dict(model, k1, k2)
        print(f'External distortion (AMC convention, full video resolution): {param_est}')

        cap = open_video_capture(input_file)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        print('Image resolution: ', width, height)

        resize_width = config['preprocess']['resize_width']
        resize_height = config['preprocess']['resize_height']
        resize_scale = 1.0
        if width > resize_width and height > resize_height:
            resize_scale = float(resize_width) / float(width)

        img = extract_background(input_file, config, resize_scale)
        cv2.imwrite(f'{args.output}/background.jpg', img)

        result_dir = f"{args.output}"
        if not os.path.exists(result_dir):
            os.makedirs(result_dir)

        result_param = distortion_models.params_dict(model, k1, k2)
        with open(f'{result_dir}/distortion.yaml', "w") as f:
            yaml.dump(result_param, f, sort_keys=False)

        rectify_video(input_file, param_est, config, args.output)
        print('\nAuto rectification (external k1) is done...\n')

        img = cv2.resize(img, None, fx=2, fy=2)
        img_u = rectify_image(img, param_est, config)
        cv2.imwrite(f'{result_dir}/rectified.jpg', img_u)

        return

    print('\nStart Auto Rectification...\n')
    param_space = {'k1': np.linspace(config['param_space']['k1_min'],
                                     config['param_space']['k1_max'], 
                                     config['param_space']['k1_step'] 
                                    )}

    # calculate a scale factor to resize images
    cap = open_video_capture(input_file)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) 
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) 
    print('Image resolution: ', width, height)

    resize_width = config['preprocess']['resize_width']
    resize_height = config['preprocess']['resize_height']
    resize_scale = 1.0
    if width > resize_width and height > resize_height:
        resize_scale = float(resize_width) / float(width)
 
    # background image
    img = extract_background(input_file, config, resize_scale)
    # save the frame
    cv2.imwrite(f'{args.output}/background.jpg', img)

    file_name = input_file.split('/')[-1].split('.')[0]
    result_dir = f"{args.output}"
    if not os.path.exists(result_dir):
        os.makedirs(result_dir)

    # run auto rectification with the image
    print('\nRunning auto rectification...')
    param_est, img_u, best_k1s, score_metrics =\
        ar.auto_rectification(img, param_space, False, None, out_dir=result_dir) 
    img_u = cv2.cvtColor(img_u, cv2.COLOR_BGR2RGB)

    # save results to file
    with open(f'{result_dir}/distortion_search_results.txt', 'w') as f:
        f.write('Best param_est: '+ str(param_est) + '\n')
        f.write('Best k1s: '+ str(best_k1s) + '\n')
        f.write('Score metrics: '+ str(score_metrics) + '\n')
    print(f'{result_dir}/distortion_search_results.txt')

    # scale up the estimated parameters with image scale factor
    param_est['k1'] = param_est['k1'] / ((1.0 / resize_scale)**2)
    print('param est(scaled up): ', param_est)

    model, k1, k2 = distortion_models.normalize_lens_param(param_est)
    result_param = distortion_models.params_dict(model, k1, k2)
    with open(f'{result_dir}/distortion.yaml', "w") as f:
        yaml.dump(result_param, f, sort_keys=False)

    # rectify the input video
    rectify_video(input_file, param_est, config, args.output)
    print('\nAuto rectification is done...\n')

    # rectify the background image
    img = cv2.resize(img, None, fx=2, fy=2)
    print('img shape: ', img.shape)
    img_u = rectify_image(img, param_est, config)
    print('img_u shape: ', img_u.shape)
    cv2.imwrite(f'{result_dir}/rectified.jpg', img_u)
    
if __name__ == '__main__':
    # parser
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input', type=str, default='./input.mp4',
                        help='input video')
    parser.add_argument('-c', '--config', type=str, default='./config.yaml',
                        help='config file')
    parser.add_argument('-o', '--output', type=str, default='./output',
                        help='output folder')
    parser.add_argument('-d', '--debug', default=False, action=argparse.BooleanOptionalAction,
                        help='bool for debugging')
    parser.add_argument('--k1-file', type=str, default=None,
                        help='Path to geocalib_distortion.yaml with pre-estimated k1 (skips grid search)')
    args = parser.parse_args()

    config = read_config(args.config)

    auto_rectification(args.input, config, args)
