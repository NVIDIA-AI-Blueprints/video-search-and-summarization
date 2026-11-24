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
from scipy.optimize import least_squares
import matplotlib.pyplot as plt
import json
import os
import logging
import argparse
import yaml
import cv2


# my modules
import config
from multi_view_calib import umeyama_alignment
from tracklet_matching import load_tracklet_mapping
from visualize_2_view_results import get_global_pose, COLOR_TABLE, \
    apply_transform, plot_cameras, visualize_cameras_points, compute_global_pose
from bundle_adjustment import save_results


def parse_args():
    parser = argparse.ArgumentParser(description='Optimize 2-view calibration results')
    parser.add_argument('-c', '--config', type=str,
                        help='Path to the config file')
    parser.add_argument('-o', '--output_base_dir', type=str, required=True,
                        help='Base directory for output')
    return parser.parse_args()

def read_data(data_path):
    with open(data_path, "r") as f:
        data = json.load(f)
    return data

def get_3D_points_of_same_tracklets(camera_pairs, data_list, 
                                    tracklet_to_global, Rglob_0i, tglob_0i):
    """ Get 3D points of the same tracklets in the camera pairs 
    Args:
        camera_pairs: list of camera pairs
        data_list: list of data
        tracklet_to_global: dict of tracklet to global
        Rglob_0i: list of rotation matrices
        tglob_0i: list of translation vectors
    Returns:
        pts3d_dict: dict of 3D points
    """
    pts3d_dict = {} # (time, global_tracklet_id): list of 3D points
    for i in range(len(camera_pairs)):
        data = data_list[i]
        cam_id = camera_pairs[i][0]
        for frame_data in data["matches"]:
            tid_list = frame_data["id_map_A2B"].keys()
            for tid in tid_list:
                # 3D points in camera A(the first of the pair)'s frame estimated 
                # by triangulation 
                pt3d_A = frame_data["world_cam_A"][tid] 
                pt3d_A = np.array(pt3d_A).reshape(3, 1)
                # transform the 3D points to the global coordinate system(in cam 0)
                pt3d_global = apply_transform(Rglob_0i[cam_id].T, 
                    -np.dot(Rglob_0i[cam_id].T, tglob_0i[cam_id].reshape(3, 1)), 
                    pt3d_A)
                # add the 3D points to the dictionary
                global_tid = tracklet_to_global[(cam_id, int(tid))]
                if (frame_data["time"], global_tid) not in pts3d_dict:
                    pts3d_dict[(frame_data["time"], global_tid)] = []
                pts3d_dict[(frame_data["time"], global_tid)].append(pt3d_global)

    return pts3d_dict

def get_relative_pose(data_list):
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
    return Rrel, trel

def get_measurement_consistency(pts3d_dict):
    """ 
    Measure how close the measurements of the same real point are to each other.
    Each item in pts3d_dict contains multiple measurements of the same real point.
    
    Args:
        pts3d_dict: dict of 3D points, format: {(time, tid): [pt3d_1, pt3d_2, ...]}
                   where each list contains multiple measurements of the same real point
    Returns:
        measurement_consistency: dict containing consistency metrics for each tracklet
    """
    measurement_consistency = {}

    for (time, tid), pts3d in pts3d_dict.items():
        if len(pts3d) < 2:
            continue
            
        # Convert to numpy array
        pts3d_array = np.array(pts3d).reshape(-1, 3)
        
        # Calculate centroid (mean position) of all measurements
        centroid = np.mean(pts3d_array, axis=0)
        
        # Calculate distances from each measurement to the centroid
        distances_to_centroid = np.linalg.norm(pts3d_array - centroid, axis=1)
        
        # Calculate pairwise distances between all measurements
        pairwise_distances = []
        for i in range(len(pts3d_array)):
            for j in range(i + 1, len(pts3d_array)):
                dist = np.linalg.norm(pts3d_array[i] - pts3d_array[j])
                pairwise_distances.append(dist)
        
        # Calculate standard deviation along each axis
        std_xyz = np.std(pts3d_array, axis=0)
        
        # Calculate overall standard deviation (3D)
        std_3d = np.std(distances_to_centroid)
        
        # Calculate maximum distance between any two measurements
        max_pairwise_distance = np.max(pairwise_distances) if pairwise_distances else 0
        
        # Calculate mean distance between measurements
        mean_pairwise_distance = np.mean(pairwise_distances) if pairwise_distances else 0
        
        # Calculate the radius of the smallest sphere containing all measurements
        # This is the maximum distance from centroid to any measurement
        max_distance_from_centroid = np.max(distances_to_centroid)
        
        # Calculate the radius of the sphere containing 95% of measurements
        sorted_distances = np.sort(distances_to_centroid)
        percentile_95_idx = int(0.95 * len(sorted_distances))
        radius_95_percentile = sorted_distances[percentile_95_idx] if percentile_95_idx < len(sorted_distances) else max_distance_from_centroid
        
        measurement_consistency[(time, tid)] = {
            'num_measurements': len(pts3d_array),
            'centroid': centroid,
            'std_xyz': std_xyz,  # Standard deviation along each axis
            'std_3d': std_3d,    # Overall 3D standard deviation
            'mean_distance_to_centroid': np.mean(distances_to_centroid),
            'max_distance_to_centroid': max_distance_from_centroid,
            'radius_95_percentile': radius_95_percentile,
            'mean_pairwise_distance': mean_pairwise_distance,
            'max_pairwise_distance': max_pairwise_distance,
            'min_pairwise_distance': np.min(pairwise_distances) if pairwise_distances else 0,
            'std_pairwise_distance': np.std(pairwise_distances) if pairwise_distances else 0,
            'measurement_spread': max_distance_from_centroid,  # Diameter of measurement cloud
            'measurement_density': len(pts3d_array) / (4/3 * np.pi * max_distance_from_centroid**3) if max_distance_from_centroid > 0 else 0
        }
    return measurement_consistency


def analyze_measurement_precision(pts3d_dict):
    """
    Comprehensive analysis of measurement precision for all tracklets.
    
    Args:
        pts3d_dict: dict of 3D points, format: {(time, tid): [pt3d_1, pt3d_2, ...]}
        
    Returns:
        precision_stats: dict containing overall precision statistics
    """
    consistency_data = get_measurement_consistency(pts3d_dict)
    
    if not consistency_data:
        return {}
    
    # Collect all precision metrics
    all_std_3d = [data['std_3d'] for data in consistency_data.values()]
    all_mean_distances = [data['mean_distance_to_centroid'] for data in consistency_data.values()]
    all_max_distances = [data['max_distance_to_centroid'] for data in consistency_data.values()]
    all_pairwise_distances = [data['mean_pairwise_distance'] for data in consistency_data.values()]
    all_measurement_counts = [data['num_measurements'] for data in consistency_data.values()]
    
    # Overall statistics
    precision_stats = {
        'total_tracklets': len(consistency_data),
        'total_measurements': sum(all_measurement_counts),
        'avg_measurements_per_tracklet': np.mean(all_measurement_counts),
        
        # 3D standard deviation statistics
        'std_3d_mean': np.mean(all_std_3d),
        'std_3d_median': np.median(all_std_3d),
        'std_3d_std': np.std(all_std_3d),
        'std_3d_min': np.min(all_std_3d),
        'std_3d_max': np.max(all_std_3d),
        
        # Distance to centroid statistics
        'mean_distance_to_centroid_mean': np.mean(all_mean_distances),
        'mean_distance_to_centroid_median': np.median(all_mean_distances),
        'mean_distance_to_centroid_std': np.std(all_mean_distances),
        
        # Maximum distance statistics
        'max_distance_mean': np.mean(all_max_distances),
        'max_distance_median': np.median(all_max_distances),
        'max_distance_std': np.std(all_max_distances),
        
        # Pairwise distance statistics
        'pairwise_distance_mean': np.mean(all_pairwise_distances),
        'pairwise_distance_median': np.median(all_pairwise_distances),
        'pairwise_distance_std': np.std(all_pairwise_distances),
        
        # Quality indicators
        'high_precision_tracklets': sum(1 for std in all_std_3d if std < np.median(all_std_3d)),
        'low_precision_tracklets': sum(1 for std in all_std_3d if std > np.percentile(all_std_3d, 75)),
        'consistency_data': consistency_data
    }
    
    return precision_stats


def print_precision_summary(precision_stats):
    """
    Print a summary of measurement precision statistics.
    
    Args:
        precision_stats: output from analyze_measurement_precision
    """
    if not precision_stats:
        print("No precision statistics available")
        return
    
    print("=" * 60)
    print("MEASUREMENT PRECISION ANALYSIS")
    print("=" * 60)
    print(f"Total tracklets analyzed: {precision_stats['total_tracklets']}")
    print(f"Total measurements: {precision_stats['total_measurements']}")
    print(f"Average measurements per tracklet: {precision_stats['avg_measurements_per_tracklet']:.2f}")
    print()
    
    print("3D Standard Deviation Statistics:")
    print(f"  Mean: {precision_stats['std_3d_mean']:.4f}")
    print(f"  Median: {precision_stats['std_3d_median']:.4f}")
    print(f"  Std Dev: {precision_stats['std_3d_std']:.4f}")
    print(f"  Range: [{precision_stats['std_3d_min']:.4f}, {precision_stats['std_3d_max']:.4f}]")
    print()
    
    print("Distance to Centroid Statistics:")
    print(f"  Mean: {precision_stats['mean_distance_to_centroid_mean']:.4f}")
    print(f"  Median: {precision_stats['mean_distance_to_centroid_median']:.4f}")
    print(f"  Std Dev: {precision_stats['mean_distance_to_centroid_std']:.4f}")
    print()
    
    print("Maximum Distance Statistics:")
    print(f"  Mean: {precision_stats['max_distance_mean']:.4f}")
    print(f"  Median: {precision_stats['max_distance_median']:.4f}")
    print(f"  Std Dev: {precision_stats['max_distance_std']:.4f}")
    print()
    
    print("Pairwise Distance Statistics:")
    print(f"  Mean: {precision_stats['pairwise_distance_mean']:.4f}")
    print(f"  Median: {precision_stats['pairwise_distance_median']:.4f}")
    print(f"  Std Dev: {precision_stats['pairwise_distance_std']:.4f}")
    print()
    
    print("Quality Indicators:")
    print(f"  High precision tracklets (std < median): {precision_stats['high_precision_tracklets']}")
    print(f"  Low precision tracklets (std > 75th percentile): {precision_stats['low_precision_tracklets']}")
    print("=" * 60)

def residual_fn(params, n_cams, camera_pairs, data_list, tracklet_to_global):
    """ Residual function for camera pose optimization
    Args:
        params: parameters to optimize
        n_cams: number of cameras
        camera_pairs: list of camera pairs
        data_list: list of data
        tracklet_to_global: dict of tracklet to global
    Returns:
        residuals: list of residuals
    """
    camera_params = params[:(n_cams-1) * 6].reshape((n_cams-1, 6))
    Rrel = [np.eye(3)]
    trel = [np.array([0, 0, 0])]
    for i in range(n_cams-1):
        R, _ = cv2.Rodrigues(camera_params[i, :3])
        Rrel.append(R)
        trel.append(camera_params[i, 3:6])
    Rglob_0i, tglob_0i = compute_global_pose(Rrel, trel)
    pts3d_dict = get_3D_points_of_same_tracklets(camera_pairs, data_list,
                                                  tracklet_to_global,
                                                  Rglob_0i, tglob_0i)

    # Get measurement consistency data
    consistency_data = get_measurement_consistency(pts3d_dict)
    
    # Extract 3D standard deviations as residuals
    residuals = []
    for (time, tid), data in consistency_data.items():
        # Use 3D standard deviation as the main residual
        residuals.append(data['max_distance_to_centroid'])
        # Optionally add other metrics as additional residuals
        # residuals.append(data['mean_distance_to_centroid'])
        # residuals.append(data['max_distance_to_centroid'])
    
    return residuals

def run_optimization(n_cams, camera_pairs, data_list, tracklet_to_global, initial_camera_poses):
    init_cam_params = []
    for R, t in initial_camera_poses:
        rvec, _ = cv2.Rodrigues(R)
        init_cam_params.append(np.hstack([rvec.ravel(), t.ravel()]))

    init_cam_params = np.array(init_cam_params).reshape(-1)
    x0 = init_cam_params[6:] # remove the first camera pose

    res = least_squares(
        residual_fn,
        x0,
        verbose=2,
        x_scale='jac',
        #loss='huber',
        #loss='soft_l1',
        #f_scale=10.0,
        max_nfev=100,
        ftol=1e-4, #xtol=1e-4, gtol=1e-4,
        method='trf',
        args=(n_cams, camera_pairs, data_list, tracklet_to_global)
    )

    refined_params = res.x[:(n_cams-1) * 6].reshape((n_cams-1, 6))
    refined_camera_poses = [(np.eye(3), np.array([0, 0, 0]))]
    for i in range(n_cams-1):
        rvec = refined_params[i, :3].reshape(3, 1)
        tvec = refined_params[i, 3:6].reshape(3, 1)
        R, _ = cv2.Rodrigues(rvec)
        refined_camera_poses.append((R, tvec))
    return refined_camera_poses

def get_camera_params(data_list):
    n_cams = len(data_list) + 1
    K_all = [None] * n_cams
    initial_camera_poses = [None] * n_cams
    
    # Initialize first camera as identity
    initial_camera_poses[0] = (np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64),
                             np.array([0, 0, 0], dtype=np.float64).reshape(3, 1))
    
    f = data_list[0]['cameras'][0]['f']
    K_all[0] = np.array([[f, 0, 1920//2], 
                         [0, f, 1080//2], 
                         [0, 0, 1]], dtype=np.float64)
    
    Rglob_0i, tglob_0i = get_global_pose(data_list)
    Rrel, trel = get_relative_pose(data_list)

    # Loop through camera pairs to set up K matrices and poses
    for i in range(n_cams-1):
        # Get focal length from current camera pair
        f = data_list[i]['cameras'][1]['f']
        
        # Set K matrix for current camera
        K_all[i+1] = np.array([[f, 0, 1920//2], 
                              [0, f, 1080//2], 
                              [0, 0, 1]], dtype=np.float64)
        
        # Get and set global camera pose
        R = Rglob_0i[i+1]
        t = tglob_0i[i+1]
        initial_camera_poses[i+1] = (np.array(R, dtype=np.float64),
                                       np.array(t, dtype=np.float64).reshape(3, 1))
    return K_all, Rrel, trel, Rglob_0i, tglob_0i

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
    
    # get the camera parameters
    K_all, Rrel, trel, Rglob_0i, tglob_0i = get_camera_params(data_list)
    print('Rglob:', repr(Rglob_0i))
    print('tglob:', repr(tglob_0i))
    init_cam_poses_global = [(Rglob_0i[i], tglob_0i[i]) for i in range(len(Rglob_0i))]
    # distance between cameras
    for i in range(len(Rglob_0i)-1):
        dist = np.linalg.norm(-Rglob_0i[i+1].T @ tglob_0i[i+1] - -Rglob_0i[i].T @ tglob_0i[i])
        print(f'distance between camera {i} and camera {i+1}: {dist}')

    colors = COLOR_TABLE

    # visualize the 3D points and the camera poses 
    # list of 3D points
    # (camera_id, tracklet_id) -> (3D points)
    pts3d_dict = {} # dict of 3D points
    for i in range(len(camera_pairs)): 
        data = data_list[i]
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
                pt3d_global = apply_transform(Rglob_0i[cam_id].T, 
                                              -np.dot(Rglob_0i[cam_id].T, tglob_0i[cam_id].reshape(3, 1)), 
                                              pt3d_cam0)
                #pt3d_global = apply_transform(Rglob[cam_id-1], tglob[cam_id-1], pt3d_cam0.flatten())
                #assert pt3d_global.shape == (3, 1), f"Dimension mismatch: pt3d_global.shape: {pt3d_global.shape}"
                
                if (cam_id, int(tid)) not in pts3d_dict:
                    pts3d_dict[(cam_id, int(tid))] = []
                pts3d_dict[(cam_id, int(tid))].append(pt3d_global)
    visualize_cameras_points(Rglob_0i, tglob_0i, pts3d_dict, tracklet_to_global, colors)
    plot_cameras(Rglob_0i, tglob_0i)

    # run optimization
    #Rrel, trel = get_relative_pose(data_list)
    init_cam_poses_rel = [(Rrel[i], trel[i]) for i in range(len(Rrel))]
    refined_cam_poses_rel = run_optimization(len(camera_pairs)+1, camera_pairs, 
                                            data_list, tracklet_to_global,
                                            init_cam_poses_rel)
    print('refined_cam_poses_rel:', repr(refined_cam_poses_rel))
    
    for i in range(len(refined_cam_poses_rel)):
        R, t = refined_cam_poses_rel[i]
        Rrel[i] = R
        trel[i] = t.flatten()

    Rglob_0i, tglob_0i = compute_global_pose(Rrel, trel)
    pts3d_dict = {} # dict of 3D points
    for i in range(len(camera_pairs)): 
        data = data_list[i]
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
                pt3d_global = apply_transform(Rglob_0i[cam_id].T, 
                                              -np.dot(Rglob_0i[cam_id].T, tglob_0i[cam_id].reshape(3, 1)), 
                                              pt3d_cam0)
                #pt3d_global = apply_transform(Rglob[cam_id-1], tglob[cam_id-1], pt3d_cam0.flatten())
                #assert pt3d_global.shape == (3, 1), f"Dimension mismatch: pt3d_global.shape: {pt3d_global.shape}"
                
                if (cam_id, int(tid)) not in pts3d_dict:
                    pts3d_dict[(cam_id, int(tid))] = []
                pts3d_dict[(cam_id, int(tid))].append(pt3d_global)
 
    visualize_cameras_points(Rglob_0i, tglob_0i, pts3d_dict, tracklet_to_global, colors)
    plot_cameras(Rglob_0i, tglob_0i)

    # save the refined camera poses
    refined_cam_poses_global = [(Rglob_0i[i], tglob_0i[i]) for i in range(len(Rglob_0i))]
    save_results(K_all, init_cam_poses_global, refined_cam_poses_global, None, None,  
                 f"{two_view_calib_output_dir}/results_pose_optimization")
    # save the refined camera poses for BA
    for i in range(len(data_list)):
        data_list[i]["cameras"][1]["R"] = Rrel[i+1].tolist()
        data_list[i]["cameras"][1]["t"] = trel[i+1].tolist()
        out_dir = f"{two_view_calib_output_dir}/results_pose_optimization/refined/cam{i}_cam{i+1}"
        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(f"{out_dir}/calib_results.json", 'w') as f:
            json.dump(data_list[i], f)


if __name__ == "__main__":
    args = parse_args()
    # read config file
    config.load_config(args.config)
    cfg = config.get_config()
    
    main(cfg, args.output_base_dir)
