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
Extract background images from video files for layout alignment.
This script extracts median background from each camera video and saves them
to the backgrounds folder for use in layout alignment before calibration.
"""

import numpy as np
import cv2
from core.utils.video_capture import open_video_capture
import argparse
import os
import yaml
import glob
import re
from tqdm import tqdm


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


def extract_background(input_file, num_frames=500, frame_step=30, scale=1.0):
    """ Extract a background image from a video file using median filtering

    Args:
        input_file(str): path to input video file
        num_frames(int): number of frames to use for background extraction
        frame_step(int): number of frames to skip between samples
        scale(float): image resize scale factor

    Returns:
        bg_img(ndarray): background image as numpy array
    """
    print(f'Extracting background from {input_file}...')
    
    # read video
    cap = open_video_capture(input_file)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video file: {input_file}")
    
    frames = []
    count = 0
    pbar = tqdm(total=num_frames, desc="Sampling frames")
    
    while cap.isOpened() and len(frames) < num_frames:
        count += 1
        ret, frame = cap.read()
        if not ret:
            break
        if count % frame_step == 0:
            if scale != 1.0:
                frame = cv2.resize(frame, None, fx=scale, fy=scale)
            frames.append(frame)
            pbar.update(1)

    pbar.close()
    cap.release()
    
    if len(frames) == 0:
        raise ValueError(f"No frames could be extracted from {input_file}")
    
    print(f"Extracted {len(frames)} frames, computing median...")
    
    # get background using median filter
    bg_img = np.median(frames, axis=0).astype(dtype=np.uint8)
    return bg_img


def find_camera_videos(video_dir, cam_dirs):
    """ Find video files for each camera directory

    Args:
        video_dir(str): directory containing video files
        cam_dirs(list): list of camera directory names (e.g., ['cam_00', 'cam_01'])

    Returns:
        dict: mapping from camera directory name to video file path
    """
    video_files = {}
    
    for cam_dir in cam_dirs:
        # Extract camera number from cam_dir (e.g., cam_00 -> 00)
        match = re.search(r'cam_(\d+)', cam_dir)
        if not match:
            print(f"Warning: Invalid camera directory format: {cam_dir}")
            continue
        
        cam_num = match.group(1)
        
        # Look for video file with this camera number
        patterns = [
            os.path.join(video_dir, f"*{cam_num}*.mp4"),
            os.path.join(video_dir, f"cam_{cam_num}.mp4"),
            os.path.join(video_dir, f"cam{cam_num}.mp4"),
        ]
        
        video_file = None
        for pattern in patterns:
            matches = glob.glob(pattern)
            if matches:
                video_file = matches[0]
                break
        
        if video_file:
            video_files[cam_dir] = video_file
            print(f"Found video for {cam_dir}: {os.path.basename(video_file)}")
        else:
            print(f"Warning: No video file found for {cam_dir}")
    
    return video_files


def main(args):
    """ Main function for background extraction

    Args:
        args: command line arguments
    """
    # Read config to get camera directories
    config = read_config(args.config)
    cam_dirs = config.get('cam_dir', [])
    
    if not cam_dirs:
        raise ValueError("No camera directories specified in config file")
    
    print(f"Processing {len(cam_dirs)} cameras: {cam_dirs}")
    
    # Find video files
    video_files = find_camera_videos(args.video_dir, cam_dirs)
    
    if not video_files:
        raise ValueError(f"No video files found in {args.video_dir}")
    
    # Create output directory
    backgrounds_dir = os.path.join(args.output, 'backgrounds')
    os.makedirs(backgrounds_dir, exist_ok=True)
    
    # Extract background for each camera
    for cam_dir, video_path in video_files.items():
        print(f"\n{'='*60}")
        print(f"Processing {cam_dir}")
        print(f"{'='*60}")
        
        # Create camera output directory
        cam_output_dir = os.path.join(backgrounds_dir, cam_dir)
        os.makedirs(cam_output_dir, exist_ok=True)
        
        # Extract background
        try:
            bg_img = extract_background(
                video_path,
                num_frames=args.num_frames,
                frame_step=args.frame_step,
                scale=1.0  # Always extract at full resolution
            )
            
            # Save background image
            output_path = os.path.join(cam_output_dir, 'background.jpg')
            cv2.imwrite(output_path, bg_img)
            print(f"Saved background to: {output_path}")
            print(f"Background shape: {bg_img.shape}")
            
        except Exception as e:
            print(f"Error processing {cam_dir}: {e}")
            continue
    
    print(f"\n{'='*60}")
    print("Background extraction complete!")
    print(f"Outputs saved to: {backgrounds_dir}")
    print(f"{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Extract background images from camera videos')
    parser.add_argument('-v', '--video_dir', type=str, required=True,
                        help='Directory containing input video files')
    parser.add_argument('-o', '--output', type=str, required=True,
                        help='Output directory')
    parser.add_argument('-c', '--config', type=str, required=True,
                        help='Path to mv_amc_config.yaml')
    parser.add_argument('--num_frames', type=int, default=500,
                        help='Number of frames to use for background extraction (default: 500)')
    parser.add_argument('--frame_step', type=int, default=30,
                        help='Number of frames to skip between samples (default: 30)')
    args = parser.parse_args()
    
    # Validate inputs
    if not os.path.isdir(args.video_dir):
        raise ValueError(f"Video directory does not exist: {args.video_dir}")
    if not os.path.isfile(args.config):
        raise ValueError(f"Config file does not exist: {args.config}")
    
    main(args)
