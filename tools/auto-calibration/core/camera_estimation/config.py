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

import yaml

_config = None

def extract_cam_number(cam_dir_name):
    """
    Extract camera number from camera directory name.
    
    Args:
        cam_dir_name (str): Camera directory name (e.g., 'cam_00', 'cam_06')
        
    Returns:
        int: Camera number (e.g., 0, 6)
    """
    if cam_dir_name.startswith('cam_'):
        return int(cam_dir_name.split('_')[1])
    # Handle other formats if needed
    return int(cam_dir_name.replace('cam', ''))

def generate_camera_pairs_from_cam_dirs(cam_dirs):
    """
    Generate consecutive camera pairs from camera directory list.
    
    Args:
        cam_dirs (list): List of camera directory names
        
    Returns:
        tuple: (camera_pairs, cam_numbers) where camera_pairs is list of [cam_A, cam_B] pairs
               and cam_numbers is list of extracted camera numbers
    """
    if len(cam_dirs) <= 1:
        return [], []
    
    cam_numbers = [extract_cam_number(cam_dir) for cam_dir in cam_dirs]
    camera_pairs = [[cam_numbers[i], cam_numbers[i+1]] for i in range(len(cam_numbers) - 1)]
    
    return camera_pairs, cam_numbers

def load_config(path):
    global _config
    with open(path, "r") as f:
        _config = yaml.safe_load(f)
    
    # Auto-generate camera_pairs from cam_dir if not explicitly provided
    if 'cam_dir' in _config:
        cam_dirs = _config['cam_dir']
        auto_camera_pairs, cam_numbers = generate_camera_pairs_from_cam_dirs(cam_dirs)
        
        if auto_camera_pairs:  # Only proceed if we have valid pairs
            # If camera_pairs is not in config or is empty, use auto-generated pairs
            if 'camera_pairs' not in _config or not _config['camera_pairs']:
                _config['camera_pairs'] = auto_camera_pairs
                print(f"Auto-generated camera_pairs from cam_dir: {auto_camera_pairs}")
                print(f"Camera directories: {cam_dirs} -> Camera numbers: {cam_numbers}")
            else:
                # If camera_pairs exists, validate it matches the expected pattern
                existing_pairs = _config['camera_pairs']
                if existing_pairs != auto_camera_pairs:
                    print(f"Warning: Existing camera_pairs {existing_pairs} differs from auto-generated {auto_camera_pairs}")
                    print("Using existing camera_pairs from config file")

def get_config():
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config(path) first.")
    return _config
