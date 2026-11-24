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
This script is used to visualize the VGGT calibration results.
"""
import os
import numpy as np
import cv2
import matplotlib.pyplot as plt
from typing import Optional

colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0), 
          (0, 255, 255), (255, 0, 255), (255, 255, 0),
          (0, 0, 0), (128, 128, 128), (128, 0, 0),
          (128, 128, 0), (0, 128, 0), (0, 0, 128),
          (128, 0, 128), (0, 128, 128), (128, 128, 128)]

def visualize_cameras(camera_info:dict, T_c0_w:np.array, map_img:np.array) -> None:
    """
    Visualize the camera positions on the map image.
    """
    for cam_id in camera_info.keys():
        C = camera_info[cam_id]['C'].reshape(3, 1) # camera center in camera 0 coordinate system
        C_h = np.concatenate((C, np.array([[1]])), axis=0)
        C_w = T_c0_w @ C_h
        C_w = C_w[:3, 0] / C_w[3, 0]
        cv2.circle(map_img, (int(C_w[0]), int(C_w[1])), 5, colors[cam_id % len(colors)], -1)
        cv2.circle(map_img, (int(C_w[0]), int(C_w[1])), 10, (0, 0, 0), 2)
        cv2.putText(map_img, f'Cam_{cam_id:02d}', (int(C_w[0])-40, int(C_w[1])+20), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)
    return map_img

def subsample_points(points, max_points=50000, stride=None):
    """Subsample points for visualization performance."""
    if points.ndim == 4:
        S, H, W, _ = points.shape
    elif points.ndim == 3:
        S, H, W = points.shape
    else:
        raise ValueError(f"Invalid dimensions of points: {points.shape}")
    
    total_points = S * H * W
    
    if stride is None:
        stride = max(1, int(np.sqrt(total_points / max_points)))

    if points.ndim == 4:
        subsampled = points[:, ::stride, ::stride, :]
    elif points.ndim == 3:
        subsampled = points[:, ::stride, ::stride]
    else:
        raise ValueError(f"Invalid dimensions of points: {points.shape}")
    
    return subsampled, stride

def visualize_3d_points(predictions:dict, T_c0_w:np.array, map_img:np.array,  
                        enable_subsampling=True, max_points=30000):
    """
    Main function to visualize the 3D points.
    """
    world_pts = predictions["world_points_from_depth"] # [V, H, W, 3]

    if enable_subsampling:
        world_pts_sampled, _ = subsample_points(world_pts, max_points)

    else:
        world_pts_sampled = world_pts
 
    # draw 3D plot of the 3D points and the ground plane
    fig = plt.figure(figsize=(10, 10))
    ax = fig.add_subplot(111, projection='3d')

    for i in range(world_pts_sampled.shape[0]):
        pts = world_pts_sampled[i].reshape(-1, 3)
        pts_h = np.concatenate((pts, np.ones((pts.shape[0], 1))), axis=1)
        pts_w = T_c0_w @ pts_h.T
        pts_w = pts_w[:3, :] / pts_w[3, :]
        h_max = np.max(pts_w[2, :])
        h_min = np.min(pts_w[2, :])
        for pt in pts_w.T:
            if (pt[2] > -0.10 * (h_max - h_min)): # only points within 10% of the height range from the ground plane
                cv2.circle(map_img, (int(pt[0]), int(pt[1])), 2, colors[i % len(colors)], -1)
        ax.scatter(pts_w[0, :], pts_w[1, :], pts_w[2, :])
    #plt.show()
    plt.close()

    return map_img


def visualize_results(camera_info:dict, predictions:dict, T_c0_w:np.array, 
                      map_img:np.array, out_dir:str) -> Optional[None]:
    """
    Visualize the calibration results.

    Args:
        camera_info: dictionary of camera information
        predictions: predictions from VGGT
        T_c0_w: transformation matrix from camera 0 to world
        map_img: floorplan map image in world coordinate system
        out_dir: output directory
    Returns:
        Optional[None]: None if successful, otherwise raises an error
    """
    if not os.path.exists(out_dir):
        raise FileNotFoundError(f"Output directory {out_dir} does not exist")

    map_img = visualize_cameras(camera_info, T_c0_w, map_img)

    map_img = visualize_3d_points(predictions, T_c0_w, map_img)

    # save the map image
    cv2.imwrite(os.path.join(out_dir, 'map_with_cameras_3d_points.jpg'), map_img)
    print(f"💾 Map image with cameras and 3D points saved to {os.path.join(out_dir, 'map_with_cameras_3d_points.jpg')}")
    return None