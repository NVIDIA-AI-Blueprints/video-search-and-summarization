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
import cv2
import numpy as np
import json
import yaml
import matplotlib.pyplot as plt
import argparse

from tracklet_matching import load_tracklet_mapping
from core.camera_estimation import config

COLOR_TABLE = ['red', 'green', 'blue', 'y', 'magenta', 'cyan', 'black', 
               'orange', 'purple', 'brown', 'pink', 'gray', 'olive', 
               'teal', 'lime', 'navy', 'maroon', 'coral', 'crimson', 
               'cyan', 'gold', 'indigo', 'ivory', 'khaki', 'lavender'] 

def parse_args():
    parser = argparse.ArgumentParser(description='Visualize 2-view calibration results')
    parser.add_argument('-c', '--config', type=str,
                        help='Path to the config file')
    parser.add_argument('-o', '--output_base_dir', type=str, required=True,
                        help='Base directory for output')
    return parser.parse_args()

def read_data(data_path):
    with open(data_path, "r") as f:
        data = json.load(f)
    return data

class Camera:
    def __init__(self, id, focal_length, R, t):
        self.id = id
        self.focal_length = focal_length
        self.R = R
        self.t = t

    def transform_to_global(self, points):
        return np.dot(self.R, points) + self.t


def apply_transform(R, t, points):
    return np.dot(R, points) + t

def transform_camera_pose(R, t, R_prime, t_prime):
    """Transform a camera pose (R,t) by another transform (R',t')
    
    Args:
        R (ndarray): 3x3 rotation matrix of camera pose
        t (ndarray): 3x1 translation vector of camera pose 
        R_prime (ndarray): 3x3 rotation matrix to transform by
        t_prime (ndarray): 3x1 translation vector to transform by
        
    Returns:
        tuple: (R_new, t_new) transformed camera pose
    """
    R_new = R_prime @ R
    t_new = R_prime @ t + t_prime
    return R_new, t_new


def compute_global_pose(Rrel, trel):
    # Rrel[i] is Rᵢ←ᵢ₋₁, for i=1…N.
    # trel[i] is tᵢ←ᵢ₋₁, for i=1…N.
    #
    # We'll store Rglob[i] = Rᵢ←₀, tglob[i] = tᵢ←₀.

    # Example: say N = 3
    # Rrel = [ None, R1, R2, R3 ]
    # trel = [ None, t1, t2, t3 ]
    # (Index 0 is unused for rel; index 1 means camera1←0, etc.)

    N = len(Rrel) - 1

    Rglob = [None] * (N+1)
    tglob = [None] * (N+1)

    Rglob[0] = np.eye(3)
    tglob[0] = np.zeros(3)

    for i in range(1, N+1):
        R_i_to_im1 = Rrel[i]    # Rᵢ←ᵢ₋₁
        t_i_to_im1 = trel[i]    # tᵢ←ᵢ₋₁
        R_prev     = Rglob[i-1] # Rᵢ₋₁←₀
        t_prev     = tglob[i-1] # tᵢ₋₁←₀

        Rglob[i] = R_i_to_im1 @ R_prev
        tglob[i] = R_i_to_im1 @ t_prev + t_i_to_im1

    # Now Rglob[i], tglob[i] are the pose of camera i in cam0's frame.
    return Rglob, tglob


def compute_global_pose_hard_coded_2(Rrel, trel):
    R01 = Rrel[1]
    t01 = trel[1]
    R12 = Rrel[2]
    t12 = trel[2]
    R23 = Rrel[3]
    t23 = trel[3]
    R34 = Rrel[4]
    t34 = trel[4]
    R45 = Rrel[5]
    t45 = trel[5]
    # (1) cam0 in its own coords:
    R0_0 = np.diag([1,1,1])
    t0_0 = np.array([0,0,0])

    # (2) cam0 → cam1 (given):
    #     R01 and t01 are already known
    R0_1 = R01
    t0_1 = t01

    # (3) cam0 → cam2:
    R0_2 = R12.dot(R0_1)
    t0_2 = R12.dot(t0_1) + t12

    # (4) cam0 → cam3:
    R0_3 = R23.dot(R0_2)
    t0_3 = R23.dot(t0_2) + t23

    # (5) cam0 → cam4:
    R0_4 = R34.dot(R0_3)
    t0_4 = R34.dot(t0_3) + t34

    # (6) cam0 → cam5:
    R0_5 = R45.dot(R0_4)
    t0_5 = R45.dot(t0_4) + t45

    Rglob = [R0_0, R0_1, R0_2, R0_3, R0_4, R0_5]
    tglob = [t0_0, t0_1, t0_2, t0_3, t0_4, t0_5]
    return Rglob, tglob


def plot_camera_axes(R, t, ax, scale=0.2, cam_name=None):
    """
    Plot the coordinate axes of a camera given its rotation and translation.
    
    Parameters:
    - R: 3x3 rotation matrix
    - t: length-3 translation vector
    - ax: a matplotlib 3D Axes object
    - scale: length of the axes to draw
    """
    origin = t
    # Camera axes in world coordinates
    x_axis = R[:, 0]
    y_axis = R[:, 1]
    z_axis = R[:, 2]
    
    # Plot X-axis (red)
    ax.plot([origin[0], origin[0] + scale * x_axis[0]],
            [origin[1], origin[1] + scale * x_axis[1]],
            [origin[2], origin[2] + scale * x_axis[2]], color='red', linewidth=2)
    
    # Plot Y-axis (green)
    ax.plot([origin[0], origin[0] + scale * y_axis[0]],
            [origin[1], origin[1] + scale * y_axis[1]],
            [origin[2], origin[2] + scale * y_axis[2]], color='green', linewidth=2)
    
    # Plot Z-axis (blue)
    ax.plot([origin[0], origin[0] + scale * z_axis[0]],
            [origin[1], origin[1] + scale * z_axis[1]],
            [origin[2], origin[2] + scale * z_axis[2]], color='blue', linewidth=2)
    
    # Mark camera origin
    ax.scatter(origin[0], origin[1], origin[2], marker='o', s=20)
    if cam_name is not None:
        ax.text(origin[0], origin[1], origin[2] + 0.05, f'{cam_name}', fontsize=10)

def plot_cameras(R_list, t_list):
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')

    # Plot each camera's axes
    for idx, (R, t) in enumerate(zip(R_list, t_list)):
        R0i = R
        t0i = t
        # we need Ri0, ti0 to visualize the camera
        Ri0 = R0i.T
        ti0 = -np.dot(Ri0, t0i)
        plot_camera_axes(Ri0, ti0, ax, scale=1.0, cam_name=f'Cam{idx}')

    # Set labels
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')

    # Make aspect ratio equal
    ax.set_box_aspect([1, 1, 1])
    ax.set_aspect('equal')

    # Adjust the limits to fit all cameras
    all_pts = np.vstack(t_list)
    min_vals = all_pts.min(axis=0) - 0.5
    max_vals = all_pts.max(axis=0) + 0.5
    ax.set_xlim(min_vals[0], max_vals[0])
    ax.set_ylim(min_vals[1], max_vals[1])
    ax.set_zlim(min_vals[2], max_vals[2])

    plt.show()

def visualize_cameras_points(Rglob, tglob, pts3d_dict, tracklet_to_global, colors):
    # visualize the 3D points and the camera poses 
    fig = plt.figure()
    ax = fig.add_subplot(121, projection='3d')

    # Plot each camera's axes
    for idx, (R, t) in enumerate(zip(Rglob, tglob)):
        R0i = R
        t0i = t
        # we need Ri0, ti0 to visualize the camera
        Ri0 = R0i.T
        ti0 = -np.dot(Ri0, t0i)
        plot_camera_axes(Ri0, ti0, ax, scale=1.0)
        ax.text(ti0[0], ti0[1], ti0[2] + 0.05, f'Cam{idx}', fontsize=10)


    # plot the 3D points
    all_points = []
    for (cam_id, tid), pts3d in pts3d_dict.items():
        pts3d = np.array(pts3d)
        all_points.append(pts3d)
        c = colors[tracklet_to_global[(cam_id, tid)] % len(COLOR_TABLE)]
        ax.scatter(pts3d[:, 0], pts3d[:, 1], pts3d[:, 2], c=c, marker='.')
    all_points = np.concatenate(all_points, axis=0)
    # Set equal aspect ratio for all axes
    #mid_x = np.mean([all_points[:,0].min(), all_points[:,0].max()])
    #mid_y = np.mean([all_points[:,1].min(), all_points[:,1].max()]) 
    #mid_z = np.mean([all_points[:,2].min(), all_points[:,2].max()])
    
    # Set equal aspect ratio for all axes
    ranges = [all_points[:,0].max() - all_points[:,0].min(),
              all_points[:,1].max() - all_points[:,1].min(),
              all_points[:,2].max() - all_points[:,2].min()]
    max_range = max(ranges)
    mid_x = (all_points[:,0].max() + all_points[:,0].min()) * 0.5
    mid_y = (all_points[:,1].max() + all_points[:,1].min()) * 0.5
    mid_z = (all_points[:,2].max() + all_points[:,2].min()) * 0.5
    ax.set_xlim(mid_x - max_range/2, mid_x + max_range/2)
    ax.set_ylim(mid_y - max_range/2, mid_y + max_range/2)
    ax.set_zlim(mid_z - max_range/2, mid_z + max_range/2)
    ax.set_title("3D Points and Camera Poses (Global Tracklet Coloring)")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
 
    ax = fig.add_subplot(122, projection='3d')

    # Plot each camera's axes
    for idx, (R, t) in enumerate(zip(Rglob, tglob)):
        R0i = R
        t0i = t
        # we need Ri0, ti0 to visualize the camera
        Ri0 = R0i.T
        ti0 = -np.dot(Ri0, t0i)
        plot_camera_axes(Ri0, ti0, ax, scale=1.0)
        ax.text(ti0[0], ti0[1], ti0[2] + 0.05, f'Cam{idx}', fontsize=10)

    # plot the 3D points
    all_points = []
    for (cam_id, tid), pts3d in pts3d_dict.items():
        pts3d = np.array(pts3d)
        all_points.append(pts3d)
        c = colors[cam_id % len(COLOR_TABLE)]
        ax.scatter(pts3d[:, 0], pts3d[:, 1], pts3d[:, 2], c=c, marker='.')
    all_points = np.concatenate(all_points, axis=0)
    # Set equal aspect ratio for all axes
    ranges = [all_points[:,0].max() - all_points[:,0].min(),
              all_points[:,1].max() - all_points[:,1].min(),
              all_points[:,2].max() - all_points[:,2].min()]
    max_range = max(ranges)
    mid_x = (all_points[:,0].max() + all_points[:,0].min()) * 0.5
    mid_y = (all_points[:,1].max() + all_points[:,1].min()) * 0.5
    mid_z = (all_points[:,2].max() + all_points[:,2].min()) * 0.5
    ax.set_xlim(mid_x - max_range/2, mid_x + max_range/2)
    ax.set_ylim(mid_y - max_range/2, mid_y + max_range/2)
    ax.set_zlim(mid_z - max_range/2, mid_z + max_range/2)
    ax.set_title("3D Points and Camera Poses (Camera Pair Coloring)")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    plt.show()

def gen_color_table(n_colors=10):
    # generate color table
    color_table = {}
    for i in range(n_colors): 
        color_table[i] = (np.random.randint(0, 255), 
                          np.random.randint(0, 255), 
                          np.random.randint(0, 255))
    return color_table

def get_global_pose(data_list):
    # data contains camera id, focal lenth, R, t
    # matching data contains time, id map, and 3D points
    # we need to transform the 3D points to the global coordinate system (in cam 0)
    # then we can visualize the 3D points in the global coordinate system
    # R, t from data_list is from camera A to camera B
    Rrel = [np.eye(3)] # 
    trel = [np.array([0, 0, 0])]
    for i, data in enumerate(data_list):
        R_AB = np.array(data['cameras'][1]['R']).reshape(3, 3) # R camera A to camera B
        t_AB = np.array(data['cameras'][1]['t']).reshape(3, 1) # t camera A to camera B
        R_BA = np.array(data['cameras'][1]['R']).reshape(3, 3).T
        t_BA = -np.dot(R_BA, np.array(data['cameras'][1]['t']).reshape(3, 1))
        scale = 1.0
        t_AB = t_AB * scale

        Rrel.append(R_AB)
        trel.append(t_AB.flatten())
        cam_id0 = data['cameras'][0]['id']
        cam_id1 = data['cameras'][1]['id']
        dist = np.linalg.norm(t_AB)
        print(f'distance between camera {cam_id0} and camera {cam_id1}: {dist}')

    Rglob_0i, tglob_0i = compute_global_pose(Rrel, trel)
    return Rglob_0i, tglob_0i

def main(cfg, output_base_dir):
    camera_pairs = cfg['camera_pairs']
    two_view_calib_output_dir = output_base_dir
    tracklet_to_global_file = f"{two_view_calib_output_dir}/tracklet_to_global.json"

    # load tracklet_to_global
    # (camera_id, tracklet_id) -> (global_id)
    tracklet_to_global = load_tracklet_mapping(tracklet_to_global_file)
    print(tracklet_to_global)

    # read the 2-view calibration data from the json file
    data_list = []
    for cam_A, cam_B in camera_pairs:
        two_view_calib_dir = f'{two_view_calib_output_dir}/cam{cam_A}_cam{cam_B}'
        data_path = os.path.join(two_view_calib_output_dir, f'{two_view_calib_dir}/calib_results.json') 
        # read the 2-view calibration data from the json file
        data = read_data(data_path)
        data_list.append(data)    
    
    # get the global pose of the cameras
    Rglob_0i, tglob_0i = get_global_pose(data_list)
    print('Rglob: ', Rglob_0i)
    print('tglob: ', tglob_0i)
    # distance between cameras
    for i in range(len(Rglob_0i)-1):
        dist = np.linalg.norm(-Rglob_0i[i+1].T @ tglob_0i[i+1] - -Rglob_0i[i].T @ tglob_0i[i])
        print(f'distance between camera {i} and camera {i+1}: {dist}')

    colors = COLOR_TABLE

    # visualize the 3D points and the camera poses 
    # list of 3D points
    # (camera_id, tracklet_id) -> (3D points)
    pts3d_dict = {} # dict of 3D points
    #for data in data_list:
    for i in range(len(camera_pairs)): 
        data = data_list[i]
        #cam_id = data["cameras"][0]["id"] 
        cam_id = camera_pairs[i][0]
        scale = 1.0
        for frame_data in data["matches"]:
            tid_list = frame_data["id_map_A2B"].keys()
            for tid in tid_list:
                # 3D points in camera A(the first of the pair)'s frame estimated 
                # by triangulation 
                pt3d_cam0 = frame_data["world_cam_A"][tid] 
                pt3d_cam0 = np.array(pt3d_cam0).reshape(3, 1) * scale
                # transform the 3D points to the global coordinate system(in cam 0)
                pt3d_global = apply_transform(Rglob_0i[i].T, 
                                              -np.dot(Rglob_0i[i].T, tglob_0i[i].reshape(3, 1)), 
                                              pt3d_cam0)
                #pt3d_global = apply_transform(Rglob[cam_id-1], tglob[cam_id-1], pt3d_cam0.flatten())
                #assert pt3d_global.shape == (3, 1), f"Dimension mismatch: pt3d_global.shape: {pt3d_global.shape}"
                
                if (cam_id, int(tid)) not in pts3d_dict:
                    pts3d_dict[(cam_id, int(tid))] = []
                pts3d_dict[(cam_id, int(tid))].append(pt3d_global)
    visualize_cameras_points(Rglob_0i, tglob_0i, pts3d_dict, tracklet_to_global, colors)
    plot_cameras(Rglob_0i, tglob_0i)


if __name__ == "__main__":
    args = parse_args()

    # read config file
    config.load_config(args.config)
    cfg = config.get_config()

    main(cfg, args.output_base_dir)
