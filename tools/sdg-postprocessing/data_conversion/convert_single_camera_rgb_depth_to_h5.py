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
import h5py
import cv2
import numpy as np
import argparse
from typing import Tuple
import numpy as np

def read_image(filepath: str) -> Tuple[str, np.ndarray]:
    """
    Read an image file using OpenCV.

    Args:
        filepath (str): Path to the image file

    Returns:
        tuple: A tuple containing (filename, image_data)
            - filename (str): Base name of the image file
            - image_data (numpy.ndarray): Image data read by OpenCV
    """
    img = cv2.imread(filepath, -1)
    return os.path.basename(filepath), img

def convert_camera_to_h5(input_folder: str) -> None:
    """
    Convert RGB and depth images from a camera folder to an HDF5 file.

    Args:
        input_folder (str): Path to the camera folder containing 'rgb' and 
                          'distance_to_image_plane_png' subdirectories

    The function:
    1. Creates an HDF5 file named after the camera folder
    2. Creates separate groups for RGB and depth images
    3. Reads and compresses images using parallel processing
    4. Stores RGB images as uint8 and depth images as uint16
    5. Preserves original filenames as dataset names

    The output HDF5 file structure:
    - rgb/
        - image1.jpg: compressed RGB data
        - image2.jpg: compressed RGB data
        ...
    - distance_to_image_plane_png/
        - depth1.png: compressed depth data
        - depth2.png: compressed depth data
        ...
    """
    camera_name = os.path.basename(input_folder)
    base_name = os.path.dirname(input_folder)
    output_path = os.path.join(base_name, f"{camera_name}.h5")
    
    print(f"📦 Converting {camera_name} to {output_path}")
    rgb_folder = os.path.join(input_folder, "rgb")
    depth_folder = os.path.join(input_folder, "distance_to_image_plane_png")

    if not os.path.isdir(rgb_folder) or not os.path.isdir(depth_folder):
        print(f"Missing rgb or depth folder in {input_folder}")
        return

    rgb_files = sorted([f for f in os.listdir(rgb_folder) if f.endswith(".jpg")])
    depth_files = sorted([f for f in os.listdir(depth_folder) if f.endswith(".png")])

    if len(rgb_files) == 0 and len(depth_files) == 0:
        print(f"No valid images found in {input_folder}")
        return

    with h5py.File(output_path, "w") as h5f:
        rgb_group = h5f.create_group("rgb")
        depth_group = h5f.create_group("distance_to_image_plane_png")

        # Depth images
        print(f"📦 Reading depth maps from {camera_name}...")
        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = {
                executor.submit(read_image, os.path.join(depth_folder, f)): f
                for f in depth_files
            }
            for future in tqdm(as_completed(futures), total=len(futures), desc="Depth"):
                fname, img = future.result()
                if img is not None:
                    depth_group.create_dataset(fname, data=img, dtype=np.uint16, compression="gzip")

        # RGB images
        print(f"📦 Reading RGB images from {camera_name}...")
        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = {
                executor.submit(read_image, os.path.join(rgb_folder, f)): f
                for f in rgb_files
            }
            for future in tqdm(as_completed(futures), total=len(futures), desc="RGB"):
                fname, img = future.result()
                if img is not None:
                    rgb_group.create_dataset(fname, data=img, dtype=np.uint8, compression="gzip")

    print(f"✅ Saved HDF5 to {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert RGB and depth maps to HDF5 with original filenames")
    parser.add_argument("--input", required=True, help="Path to camera folder containing rgb and depth subfolders")
    args = parser.parse_args()
    convert_camera_to_h5(args.input)