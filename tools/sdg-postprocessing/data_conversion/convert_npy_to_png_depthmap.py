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

import os
import glob
import argparse
import numpy as np
from PIL import Image
from concurrent.futures import ThreadPoolExecutor
from typing import NoReturn

def npy_to_png(npy_file: str, output_dir: str) -> None:
    """
    Convert a single NumPy depth map file to PNG format.

    Args:
        npy_file (str): Path to the input .npy depth map file
        output_dir (str): Directory where the output PNG file will be saved

    The function:
    1. Loads the depth data from the .npy file
    2. Scales the values by 1000 and converts to uint16
    3. Creates a PIL Image from the array
    4. Saves the image as PNG with the same base filename
    """
    depth_data = np.load(npy_file)
    depth_data = (depth_data * 1000).astype(np.uint16)
    img = Image.fromarray(depth_data)
    
    filename = os.path.basename(npy_file).replace('.npy', '.png')
    output_path = os.path.join(output_dir, filename)
    img.save(output_path)
    print(f"Image saved at: {output_path}")

def process_subdir(subdir: str) -> None:
    """
    Process all .npy files in a subdirectory, converting them to PNG format.

    Args:
        subdir (str): Path to the subdirectory containing .npy files

    The function:
    1. Creates an output directory for PNG files
    2. Finds all .npy files in the input directory
    3. Converts each .npy file to PNG format
    """
    output_dir = os.path.join(os.path.dirname(subdir), 'distance_to_image_plane_png')
    os.makedirs(output_dir, exist_ok=True)
    npy_files = glob.glob(os.path.join(subdir, '*.npy'))
    
    for npy_file in npy_files:
        npy_to_png(npy_file, output_dir)

def process_directory(base_dir: str) -> None:
    """
    Process all subdirectories containing depth map data in the base directory.

    Args:
        base_dir (str): Base directory containing subdirectories with .npy files

    The function:
    1. Finds all subdirectories named 'distance_to_image_plane'
    2. Uses ThreadPoolExecutor to process subdirectories in parallel
    3. Converts all .npy files to PNG format in each subdirectory
    """
    subdirs = [os.path.join(root) for root, dirs, files in os.walk(base_dir) if os.path.basename(root) == 'distance_to_image_plane']

    with ThreadPoolExecutor(max_workers=20) as executor:
        executor.map(process_subdir, subdirs)

if __name__ == "__main__":
    '''
    python npy_to_npg_depthmaps.py /path/to/your/folder
    '''
    parser = argparse.ArgumentParser(description="Convert .npy depth maps to .png depth maps.")
    parser.add_argument("base_dir", type=str, help="Path to your dataset folder")
    args = parser.parse_args()

    process_directory(args.base_dir)