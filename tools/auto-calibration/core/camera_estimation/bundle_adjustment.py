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
from scipy.optimize import least_squares
import matplotlib.pyplot as plt
import json
import os
import sys
import logging
import argparse
import yaml
import time
# Add the current directory to Python path to allow importing local modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(project_root)

import config
from math_utils import umeyama_alignment
from tracklet_matching import load_tracklet_mapping
from visualize_2_view_results import get_global_pose
from scipy.linalg import expm, logm
from core.utils.path_validation import validate_output_directory, validate_input_directory, validate_directory_path

"""
Bundle adjustment for multi-camera calibration using non-linear optimization.

This module implements bundle adjustment to refine camera poses and 3D point positions
by minimizing reprojection error across multiple cameras.

Two optimization methods are available:
1. 'euclidean': Standard least-squares optimization in Euclidean space (default)
2. 'liegroup': Manifold optimization respecting SE(3) Lie group structure

Usage Examples:
--------------

# Standard Euclidean optimization (original method)
refined_points, refined_poses = run_bundle_adjustment(
    n_cams, n_points, initial_poses, initial_points_3d, 
    K_all, camera_indices, point_indices, points_2d
)

# Lie group manifold optimization  
refined_points, refined_poses = run_bundle_adjustment(
    n_cams, n_points, initial_poses, initial_points_3d, 
    K_all, camera_indices, point_indices, points_2d,
    optimization_method='liegroup'
)

# Compare both methods
results = compare_bundle_adjustment_methods(
    n_cams, n_points, initial_poses, initial_points_3d, 
    K_all, camera_indices, point_indices, points_2d
)
"""

# Set up logging configuration
def setup_logging(logging_level):
    # Get the root logger
    logger = logging.getLogger()
    
    # Check if handlers are already configured to avoid duplicates
    if logger.handlers:
        return
    
    # Create a formatter
    formatter = logging.Formatter('%(levelname)s - %(message)s')
    
    # Set up file handler with DEBUG level
    # Create Logs directory if it doesn't exist
    logs_dir = os.path.join(args.output_base_dir, 'Logs')
    os.makedirs(logs_dir, exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(logs_dir, f'bundle_adjustment_{time.strftime("%Y%m%d_%H%M%S")}.log'))
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)  # Set DEBUG level for file
    
    # Set up console handler with INFO level
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    if logging_level == 'DEBUG':
        console_handler.setLevel(logging.DEBUG)  # Set DEBUG level for console
    else:
        console_handler.setLevel(logging.INFO)  # Set INFO level for console
    
    # Set root logger to DEBUG to capture all messages
    logger.setLevel(logging.DEBUG)
    
    # Add both handlers to the logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    # Suppress DEBUG messages from other packages
    logging.getLogger('cv2').setLevel(logging.WARNING)
    logging.getLogger('numpy').setLevel(logging.WARNING)
    logging.getLogger('matplotlib').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)
    logging.getLogger('tqdm').setLevel(logging.WARNING)
    logging.getLogger('dtaidistance').setLevel(logging.WARNING)
    logging.getLogger('scipy').setLevel(logging.WARNING)
    logging.getLogger('sklearn').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)


def parse_args():
    parser = argparse.ArgumentParser(description='Bundle adjustment')
    parser.add_argument('-c', '--config', type=str,
                        help='Path to the config file')
    parser.add_argument('-i', '--input_base_dir', type=str, required=True,
                        help='Base directory for single view calibration outputs')
    parser.add_argument('-o', '--output_base_dir', type=str, required=True,
                        help='Base directory for output')
    parser.add_argument('--no-plot', action='store_true',
                        help='Disable interactive plotting (plt.show() calls). Plots will still be saved to files.')
    return parser.parse_args()

def project_point(point_3d, rvec, tvec, K):

    point_3d = np.array(point_3d, dtype=np.float64).reshape(1, 1, 3)
    rvec = np.array(rvec, dtype=np.float64).reshape(3, 1)
    tvec = np.array(tvec, dtype=np.float64).reshape(3, 1)
    proj_point, _ = cv2.projectPoints(point_3d, rvec, tvec, K, None)
    return proj_point.ravel()


def bundle_adjustment_residual(params, n_cams, n_points, camera_indices, 
                               point_indices, points_2d, K_all,
                               constraint_pairs=None, 
                               constraint_distances=None):
    cfg = config.get_config()
    # Handle missing distance_weight parameter with default value
    dist_weight = cfg.get('distance_weight', 5.0)
    camera_params = params[:n_cams * 6].reshape((n_cams, 6))
    points_3d = params[n_cams * 6:].reshape((n_points, 3))
    residuals = []

    for cam_idx, pt_idx, obs in zip(camera_indices, point_indices, points_2d):
        rvec = camera_params[cam_idx, :3]
        tvec = camera_params[cam_idx, 3:6]
        K = K_all[cam_idx]
        proj = project_point(points_3d[pt_idx], rvec, tvec, K) 
        residuals.append(proj - obs)

    # Optional soft penalty for distance constraint between 3D points
    if constraint_pairs is not None and constraint_distances is not None:
        weight = dist_weight # adjust strength of the penalty
        for (i, j), constraint_dist in zip(constraint_pairs, constraint_distances):
            dist = np.linalg.norm(points_3d[i] - points_3d[j])
            dist_penalty = weight * (dist - constraint_dist)
            residuals.append(np.array([dist_penalty], dtype=np.float64))
    return np.concatenate(residuals)

def get_plane_constraint(points_3d):
    """
    Estimate a fitting plane for 3D points using RANSAC and return the average distance.
    
    Args:
        points_3d: Nx3 array of 3D points
        
    Returns:
        avg_dist: Average distance of points to the fitted plane
        plane_model: [a, b, c, d] where ax + by + cz + d = 0 is the plane equation
    """
    if len(points_3d) < 3:
        return 0.0, np.array([0, 0, 1, 0])
    
    # Convert to numpy array if needed
    points_3d = np.array(points_3d, dtype=np.float64)
    
    # Use RANSAC to find the best fitting plane
    best_plane = None
    best_inliers = 0
    best_avg_dist = float('inf')
    
    n_iterations = 100
    threshold = 0.1  # Distance threshold for inliers
    
    for _ in range(n_iterations):
        # Randomly sample 3 points
        if len(points_3d) >= 3:
            sample_indices = np.random.choice(len(points_3d), 3, replace=False)
            p1, p2, p3 = points_3d[sample_indices]
            
            # Calculate plane equation: ax + by + cz + d = 0
            # Normal vector = (p2 - p1) × (p3 - p1)
            v1 = p2 - p1
            v2 = p3 - p1
            normal = np.cross(v1, v2)
            
            # Normalize the normal vector
            norm_magnitude = np.linalg.norm(normal)
            if norm_magnitude < 1e-8:  # Points are collinear
                continue
                
            normal = normal / norm_magnitude
            
            # Calculate d: d = -normal · p1
            d = -np.dot(normal, p1)
            plane_model = np.array([normal[0], normal[1], normal[2], d])
            
            # Calculate distances from all points to the plane
            distances = np.abs(np.dot(points_3d, normal) + d)
            
            # Count inliers
            inliers = np.sum(distances < threshold)
            
            if inliers > best_inliers:
                best_inliers = inliers
                best_plane = plane_model
                best_avg_dist = np.mean(distances)
    
    # If no good plane found, use least squares fitting
    if best_plane is None:
        # Use SVD to find the best fitting plane
        # Center the points
        centroid = np.mean(points_3d, axis=0)
        centered_points = points_3d - centroid
        
        # SVD to find the normal vector (smallest singular value)
        U, S, Vt = np.linalg.svd(centered_points)
        normal = Vt[2]  # Last row corresponds to smallest singular value
        
        # Calculate d
        d = -np.dot(normal, centroid)
        best_plane = np.array([normal[0], normal[1], normal[2], d])
        
        # Calculate average distance
        distances = np.abs(np.dot(points_3d, normal) + d)
        best_avg_dist = np.mean(distances)
    
    return best_avg_dist, best_plane

def fit_plane_ransac(points_3d, n_iterations=100, threshold=0.1, min_inliers_ratio=0.5):
    """
    Fit a plane to 3D points using RANSAC algorithm.
    
    Args:
        points_3d: Nx3 array of 3D points
        n_iterations: Number of RANSAC iterations
        threshold: Distance threshold for inliers
        min_inliers_ratio: Minimum ratio of inliers to total points
        
    Returns:
        plane_model: [a, b, c, d] where ax + by + cz + d = 0 is the plane equation
        inliers: Boolean array indicating which points are inliers
        avg_distance: Average distance of inliers to the plane
    """
    if len(points_3d) < 3:
        return np.array([0, 0, 1, 0]), np.zeros(len(points_3d), dtype=bool), 0.0
    
    points_3d = np.array(points_3d, dtype=np.float64)
    best_plane = None
    best_inliers = None
    best_inlier_count = 0
    best_avg_distance = float('inf')
    
    for _ in range(n_iterations):
        # Randomly sample 3 points
        sample_indices = np.random.choice(len(points_3d), 3, replace=False)
        p1, p2, p3 = points_3d[sample_indices]
        
        # Calculate plane equation: ax + by + cz + d = 0
        v1 = p2 - p1
        v2 = p3 - p1
        normal = np.cross(v1, v2)
        
        # Check if points are collinear
        norm_magnitude = np.linalg.norm(normal)
        if norm_magnitude < 1e-8:
            continue
            
        normal = normal / norm_magnitude
        d = -np.dot(normal, p1)
        plane_model = np.array([normal[0], normal[1], normal[2], d])
        
        # Calculate distances from all points to the plane
        distances = np.abs(np.dot(points_3d, normal) + d)
        
        # Find inliers
        inliers = distances < threshold
        inlier_count = np.sum(inliers)
        
        if inlier_count > best_inlier_count:
            best_inlier_count = inlier_count
            best_plane = plane_model
            best_inliers = inliers
            best_avg_distance = np.mean(distances[inliers])
    
    # If RANSAC didn't find enough inliers, use least squares
    if best_plane is None or best_inlier_count < len(points_3d) * min_inliers_ratio:
        return fit_plane_least_squares(points_3d)
    
    return best_plane, best_inliers, best_avg_distance


def fit_plane_least_squares(points_3d):
    """
    Fit a plane to 3D points using least squares (SVD method).
    
    Args:
        points_3d: Nx3 array of 3D points
        
    Returns:
        plane_model: [a, b, c, d] where ax + by + cz + d = 0 is the plane equation
        inliers: Boolean array (all True for least squares)
        avg_distance: Average distance of points to the plane
    """
    if len(points_3d) < 3:
        return np.array([0, 0, 1, 0]), np.zeros(len(points_3d), dtype=bool), 0.0
    
    points_3d = np.array(points_3d, dtype=np.float64)
    
    # Center the points
    centroid = np.mean(points_3d, axis=0)
    centered_points = points_3d - centroid
    
    # Use SVD to find the normal vector (smallest singular value)
    U, S, Vt = np.linalg.svd(centered_points)
    normal = Vt[2]  # Last row corresponds to smallest singular value
    
    # Calculate d
    d = -np.dot(normal, centroid)
    plane_model = np.array([normal[0], normal[1], normal[2], d])
    
    # Calculate distances
    distances = np.abs(np.dot(points_3d, normal) + d)
    avg_distance = np.mean(distances)
    
    # All points are considered inliers for least squares
    inliers = np.ones(len(points_3d), dtype=bool)
    
    return plane_model, inliers, avg_distance


def point_to_plane_distance(point, plane_model):
    """
    Calculate the distance from a point to a plane.
    
    Args:
        point: 3D point [x, y, z]
        plane_model: [a, b, c, d] where ax + by + cz + d = 0 is the plane equation
        
    Returns:
        distance: Signed distance from point to plane
    """
    a, b, c, d = plane_model
    normal = np.array([a, b, c])
    normal_norm = np.linalg.norm(normal)
    
    if normal_norm < 1e-8:
        return 0.0
    
    # Normalize the normal vector
    normal = normal / normal_norm
    d_normalized = d / normal_norm
    
    # Calculate signed distance
    distance = np.dot(point, normal) + d_normalized
    
    return distance


def visualize_plane_fitting(points_3d, plane_model, output_path=None):
    """
    Visualize the plane fitting results.
    
    Args:
        points_3d: Nx3 array of 3D points
        plane_model: [a, b, c, d] plane equation
        output_path: Optional path to save the plot
    """
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D
    
    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection='3d')
    
    # Plot the 3D points
    ax.scatter(points_3d[:, 0], points_3d[:, 1], points_3d[:, 2], 
               c='blue', alpha=0.6, label='3D Points')
    
    # Create a grid for the plane
    a, b, c, d = plane_model
    normal = np.array([a, b, c])
    normal = normal / np.linalg.norm(normal)
    
    # Find the centroid of points for plane center
    centroid = np.mean(points_3d, axis=0)
    
    # Create a grid around the centroid
    x_range = np.linspace(points_3d[:, 0].min() - 1, points_3d[:, 0].max() + 1, 20)
    y_range = np.linspace(points_3d[:, 1].min() - 1, points_3d[:, 1].max() + 1, 20)
    X, Y = np.meshgrid(x_range, y_range)
    
    # Calculate Z for the plane: ax + by + cz + d = 0 -> z = -(ax + by + d)/c
    if abs(c) > 1e-8:
        Z = -(a * X + b * Y + d) / c
    else:
        # If c is very small, use a different approach
        Z = np.zeros_like(X)
    
    # Plot the plane
    ax.plot_surface(X, Y, Z, alpha=0.3, color='red', label='Fitted Plane')
    
    # Plot the normal vector
    normal_length = np.max(np.ptp(points_3d, axis=0)) * 0.2
    normal_end = centroid + normal * normal_length
    ax.quiver(centroid[0], centroid[1], centroid[2],
              normal[0], normal[1], normal[2],
              length=normal_length, color='green', arrow_length_ratio=0.2,
              label='Normal Vector')
    
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')
    ax.set_title('Plane Fitting Results')
    ax.legend()
    
    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
    
    # plt.show() is commented out - this function doesn't currently show plots


def bundle_adjustment_residual_with_plane_constraint(params, n_cams, n_points, camera_indices, 
                               point_indices, points_2d, K_all,
                               constraint_pairs=None, 
                               constraint_distances=None):
    camera_params = params[:n_cams * 6].reshape((n_cams, 6))
    points_3d = params[n_cams * 6:].reshape((n_points, 3))
    residuals = []

    for cam_idx, pt_idx, obs in zip(camera_indices, point_indices, points_2d):
        rvec = camera_params[cam_idx, :3]
        tvec = camera_params[cam_idx, 3:6]
        K = K_all[cam_idx]
        proj = project_point(points_3d[pt_idx], rvec, tvec, K) 
        residuals.append(proj - obs)

    # Optional soft penalty for plane constraint of 3D points
    avg_dist, plane_model = get_plane_constraint(points_3d)
    residuals.append(np.array([avg_dist], dtype=np.float64))
    return np.concatenate(residuals)


def bundle_adjustment_residual_weighted(params, n_cams, n_points, camera_indices, 
                               point_indices, points_2d, K_all,
                               constraint_pairs=None, 
                               constraint_distances=None):
    cfg = config.get_config()
    distance_weight = cfg['distance_weight']

    camera_params = params[:n_cams * 6].reshape((n_cams, 6))
    points_3d = params[n_cams * 6:].reshape((n_points, 3))
    residuals = []

    for cam_idx, pt_idx, obs in zip(camera_indices, point_indices, points_2d):
        rvec = camera_params[cam_idx, :3]
        tvec = camera_params[cam_idx, 3:6]
        K = K_all[cam_idx]
        proj = project_point(points_3d[pt_idx], rvec, tvec, K) 
        residual = proj - obs
        # weight the residual by the distance between the camera and the point
        if cam_idx == 3 or cam_idx == 4:
            #dist = np.linalg.norm(points_3d[pt_idx] - camera_params[cam_idx, 3:6])
            #dist = np.linalg.norm(points_3d[pt_idx])
            #weight = dist * 0.1
            weight = 10.0
            residuals.append(np.array(residual * weight, dtype=np.float64))
        else:
            #weight = 1.0
            residuals.append(residual)

        #dist = np.linalg.norm(points_3d[pt_idx] - camera_params[cam_idx, 3:6])
        ##dist = np.linalg.norm(points_3d[pt_idx])
        #weight = dist * distance_weight
        #residuals.append(np.array(residual * weight, dtype=np.float32))

    # Optional soft penalty for distance constraint between 3D points
    if constraint_pairs is not None and constraint_distances is not None:
        weight = 1.0 # adjust strength of the penalty
        for (i, j), constraint_dist in zip(constraint_pairs, constraint_distances):
            dist = np.linalg.norm(points_3d[i] - points_3d[j])
            dist_penalty = weight * (dist - constraint_dist)
            residuals.append(np.array([dist_penalty]))
    return np.concatenate(residuals)


def bundle_adjustment_residual_fixed_cam0(params, n_cams, n_points, 
                                          camera_indices, point_indices, 
                                          points_2d, K_all,
                                          constraint_pairs=None, 
                                          constraint_pts3=None):
    cfg = config.get_config()
    weight_manual_constraint = cfg['weight_manual_constraint']

    camera_params = params[:(n_cams - 1) * 6].reshape((n_cams - 1, 6))
    points_3d = params[(n_cams - 1) * 6:].reshape((n_points, 3))
    residuals = []

    for cam_idx, pt_idx, obs in zip(camera_indices, point_indices, points_2d):
        if cam_idx== 0:
            rvec = np.zeros(3)
            tvec = np.zeros(3)
        else:
            rvec = camera_params[cam_idx - 1, :3]
            tvec = camera_params[cam_idx - 1, 3:6]
        K = K_all[cam_idx]
        proj = project_point(points_3d[pt_idx], rvec, tvec, K) 
        residuals.append(proj - obs)
    
    if constraint_pairs is not None and constraint_pts3 is not None:
        for (cam_idx1, cam_idx2), pts2d_list in constraint_pairs.items():
            K1 = K_all[cam_idx1].copy()
            if cam_idx1 == 0:
                rvec1 = np.zeros(3)
                tvec1 = np.zeros(3)
            else:
               rvec1 = camera_params[cam_idx1 - 1, :3]
               tvec1 = camera_params[cam_idx1 - 1, 3:6]
            R1, _ = cv2.Rodrigues(rvec1)
            P1 = K1 @ np.hstack((R1, tvec1.reshape(3, 1)))
    
            K2 = K_all[cam_idx2].copy()
            rvec2 = camera_params[cam_idx2 - 1, :3]
            tvec2 = camera_params[cam_idx2 - 1, 3:6]
            R2, _ = cv2.Rodrigues(rvec2)
            P2 = K2 @ np.hstack((R2, tvec2.reshape(3, 1)))
            for i, (pt2d_1, pt2d_2) in enumerate(pts2d_list):
                pt3d_est = triangulate_points(P1, P2, pt2d_1, pt2d_2)
                if pt3d_est is None:
                    continue
                pt3d_constraint = constraint_pts3[(cam_idx1, cam_idx2)][i]
                error = np.linalg.norm(pt3d_est- pt3d_constraint)
                residuals.append(weight_manual_constraint * np.array([error], dtype=np.float64))
    return np.concatenate(residuals).astype(np.float64)

def bundle_adjustment_residual_fixed_cam0_with_f(params, n_cams, n_points, 
                                          camera_indices, point_indices, 
                                          points_2d, K_all,
                                          constraint_pairs=None, 
                                          constraint_distances=None):

    cfg = config.get_config()
    dist_weight = cfg['distance_weight']
    camera_params = params[:(n_cams - 1) * 7].reshape((n_cams - 1, 7))
    points_3d = params[(n_cams - 1) * 7:].reshape((n_points, 3))
    residuals = []

    for cam_idx, pt_idx, obs in zip(camera_indices, point_indices, points_2d):
        if cam_idx== 0:
            rvec = np.zeros(3)
            tvec = np.zeros(3)
            K = K_all[cam_idx].copy()
        else:
            rvec = camera_params[cam_idx - 1, :3]
            tvec = camera_params[cam_idx - 1, 3:6]
            f = camera_params[cam_idx - 1, 6]
            K = K_all[cam_idx].copy()
            K[0, 0] = f
            K[1, 1] = f
        
        proj = project_point(points_3d[pt_idx], rvec, tvec, K) 
        #if proj[0] < 0 or proj[0] > 1920 or proj[1] < 0 or proj[1] > 1080:
        #    print(f'proj: {proj}')
        #    print(f'obs: {obs}')
        #    print(f'pt_idx: {pt_idx}')
        #    print(f'cam_idx: {cam_idx}')
        #    print(f'points_3d: {points_3d[pt_idx]}')
        #    print(f'rvec: {rvec}')
        residuals.append(proj - obs)
    
    # Optional soft penalty for distance constraint between 3D points
    if constraint_pairs is not None and constraint_distances is not None:
        weight = dist_weight # adjust strength of the penalty
        for (i, j), constraint_dist in zip(constraint_pairs, constraint_distances):
            dist = np.linalg.norm(points_3d[i] - points_3d[j])
            dist_penalty = weight * (dist - constraint_dist)
            residuals.append(np.array([dist_penalty], dtype=np.float64))
    return np.concatenate(residuals)

def bundle_adjustment_residual_fixed_cam012_with_f(params, n_cams, n_points, 
                                          camera_indices, point_indices, 
                                          points_2d, K_all,
                                          initial_camera_poses,
                                          constraint_pair=None, 
                                          constraint_distance=None):
    camera_params = params[:(n_cams - 3) * 7].reshape((n_cams - 3, 7))
    points_3d = params[(n_cams - 3) * 7:].reshape((n_points, 3))
    residuals = []

    for cam_idx, pt_idx, obs in zip(camera_indices, point_indices, points_2d):
        if cam_idx== 0 or cam_idx== 1 or cam_idx== 2:
            #rvec = np.zeros(3)
            R = initial_camera_poses[cam_idx][0]
            rvec, _ = cv2.Rodrigues(R)
            tvec = initial_camera_poses[cam_idx][1].reshape(3, 1)
            K = K_all[cam_idx]
        else:
            rvec = camera_params[cam_idx - 3, :3]
            tvec = camera_params[cam_idx - 3, 3:6]
            f = camera_params[cam_idx - 3, 6]
            K = K_all[cam_idx]
            K[0, 0] = f
            K[1, 1] = f
        
        proj = project_point(points_3d[pt_idx], rvec, tvec, K) 
        residuals.append(proj - obs)
    
    # Optional soft penalty for distance constraint between 3D points
    #constraint_pair = None # TODO: temporary hardcoded
    if constraint_pair is not None and constraint_distance is not None:
        i, j = constraint_pair
        weight = 1000000.0 # adjust strength of the penalty
        dist = np.linalg.norm(points_3d[i] - points_3d[j])
        dist_penalty = weight * (dist - constraint_distance)
        residuals.append(np.array([dist_penalty]))
    return np.concatenate(residuals)



def bundle_adjustment_residual_fixed_cam0_and_1(params, n_cams, n_points, 
                                          camera_indices, point_indices, 
                                          points_2d, K_all,
                                          constraint_pair=None, 
                                          constraint_distance=None):
    """ 
    This function is used to fix the first two cameras and optimize the rest 
    of the cameras and 3D points because of scale ambiguity
    """
    camera_params = params[:(n_cams - 2) * 6].reshape((n_cams - 2, 6))
    points_3d = params[(n_cams - 2) * 6:].reshape((n_points, 3))
    residuals = []

    for cam_idx, pt_idx, obs in zip(camera_indices, point_indices, points_2d):
        if cam_idx== 0 or cam_idx== 1:
            rvec = np.zeros(3)
            tvec = np.zeros(3)
        else:
            rvec = camera_params[cam_idx - 2, :3]
            tvec = camera_params[cam_idx - 2, 3:6]
        K = K_all[cam_idx]
        proj = project_point(points_3d[pt_idx], rvec, tvec, K) 
        residuals.append(proj - obs)
    
    # Optional soft penalty for distance constraint between 3D points
    #constraint_pair = None # TODO: temporary hardcoded
    if constraint_pair is not None and constraint_distance is not None:
        i, j = constraint_pair
        weight = 1000000.0 # adjust strength of the penalty
        dist = np.linalg.norm(points_3d[i] - points_3d[j])
        dist_penalty = weight * (dist - constraint_distance)
        residuals.append(np.array([dist_penalty]))
    return np.concatenate(residuals)


def run_bundle_adjustment(n_cams, n_points, initial_camera_poses, 
                          initial_points_3d, K_all, camera_indices, 
                          point_indices, points_2d, optimization_method='euclidean'):
    """Bundle adjustment with choice of optimization method
    
    Args:
        n_cams: Number of cameras
        n_points: Number of 3D points
        initial_camera_poses: List of (R, t) initial camera poses
        initial_points_3d: Initial 3D points
        K_all: Camera intrinsic matrices
        camera_indices: Camera indices for observations
        point_indices: Point indices for observations
        points_2d: Observed 2D points
        optimization_method: 'euclidean' (default) or 'liegroup' for manifold optimization
        
    Returns:
        refined_points_3d: Optimized 3D points
        refined_camera_poses: Optimized camera poses as (R, t) list
    """
    
    # Route to appropriate optimization method
    if optimization_method.lower() == 'liegroup':
        print("Using Lie Group manifold optimization...")
        return run_bundle_adjustment_liegroup(
            n_cams, n_points, initial_camera_poses, initial_points_3d, 
            K_all, camera_indices, point_indices, points_2d
        )
    elif optimization_method.lower() == 'euclidean':
        print("Using Euclidean optimization...")
        # Continue with original implementation below
    else:
        raise ValueError(f"Unknown optimization method: {optimization_method}. Choose 'euclidean' or 'liegroup'")
    
    # Original Euclidean implementation
    init_cam_params = []
    for R, t in initial_camera_poses:
        rvec, _ = cv2.Rodrigues(R)
        init_cam_params.append(np.hstack([rvec.ravel(), t.ravel()]))

    # distance constraints between 3D points # experiment
    constraint_pairs = []
    constraint_distances = []
    for i in range(len(initial_points_3d)-1):
        constraint_pairs.append((i, i+1))
        constraint_distances.append(np.linalg.norm(initial_points_3d[i] - initial_points_3d[i+1]))
    #constraint_pairs = None
 
    init_cam_params = np.array(init_cam_params).reshape(-1)
    init_points_3d = initial_points_3d.reshape(-1)
    x0 = np.hstack([init_cam_params, init_points_3d])

     # Generate Jacobian sparsity pattern
    from scipy.sparse import lil_matrix
    def create_sparsity(n_cams, n_points, camera_indices, point_indices):
        m = len(camera_indices) * 2  # 2 residuals per observation
        n = n_cams * 6 + n_points * 3
        A = lil_matrix((m, n), dtype=int)

        for i in range(len(camera_indices)):
            cam_idx = camera_indices[i]
            pt_idx = point_indices[i]
            A[2 * i, cam_idx * 6 : cam_idx * 6 + 6] = 1
            A[2 * i + 1, cam_idx * 6 : cam_idx * 6 + 6] = 1
            A[2 * i, n_cams * 6 + pt_idx * 3 : n_cams * 6 + pt_idx * 3 + 3] = 1
            A[2 * i + 1, n_cams * 6 + pt_idx * 3 : n_cams * 6 + pt_idx * 3 + 3] = 1

        return A

    sparsity = create_sparsity(n_cams, n_points, camera_indices, point_indices)

    res = least_squares(
        bundle_adjustment_residual,
        #bundle_adjustment_residual_weighted, # TODO: experiment
        #bundle_adjustment_residual_with_plane_constraint,
        x0,
        verbose=2,
        x_scale='jac',
        #jac_sparsity=sparsity,
        #loss='huber',
        #loss='soft_l1',
        #f_scale=10.0,
        max_nfev=200,
        ftol=1e-4, #xtol=1e-4, gtol=1e-4,
        method='trf',
        args=(n_cams, n_points, camera_indices, point_indices, points_2d, K_all, 
              constraint_pairs, constraint_distances)
    )

    refined_params = res.x[:n_cams * 6].reshape((n_cams, 6))
    refined_points_3d = res.x[n_cams * 6:].reshape((n_points, 3))

    refined_camera_poses = []
    for i in range(n_cams):
        rvec = refined_params[i, :3].reshape(3, 1)
        tvec = refined_params[i, 3:6].reshape(3, 1)
        R, _ = cv2.Rodrigues(rvec)
        refined_camera_poses.append((R, tvec))

    # Optional post-processing: apply scale correction using known distance
    # Method 1: use the distance between the first two points
    if False:
        constraint_pair = None #(1, 2)
        init_points_3d = initial_points_3d.reshape(-1, 3)
        constraint_distance = np.linalg.norm(init_points_3d[1] - init_points_3d[2])
        print('init point:', repr(init_points_3d[1]))
        print('init point:', repr(init_points_3d[2]))
        print('refined point:', repr(refined_points_3d[1]))
        print('refined point:', repr(refined_points_3d[2]))
        print('constraint distance:', repr(constraint_distance))
        if constraint_pair is not None and constraint_distance is not None:
            i, j = constraint_pair
            dist_raw = np.linalg.norm(refined_points_3d[i] - refined_points_3d[j])
            if dist_raw > 1e-8:
                scale = constraint_distance / dist_raw
                print(f"Applying scale correction: scale = {scale:.6f}")
                refined_points_3d *= scale
                for k in range(len(refined_camera_poses)):
                    R, t = refined_camera_poses[k]
                    refined_camera_poses[k] = (R, t * scale)
    
    # Method 2: use Umeyama's algorithm to find the best rigid transformation
    # between the initial and refined 3D points
    if False:
        init_points_sub = initial_points_3d[:]
        refined_points_sub = refined_points_3d[:]
        scale, R, t = umeyama_alignment(init_points_sub, refined_points_sub)
        print('scale:', repr(scale))
        print('R:', repr(R))
        print('t:', repr(t))
        #scale = 1.1 # TODO: experiment
        refined_points_3d = scale * R @ refined_points_3d.T + t.reshape((3, 1))
        refined_points_3d = refined_points_3d.T
        for k in range(len(refined_camera_poses)):
            R, t = refined_camera_poses[k]
            refined_camera_poses[k] = (R, t * scale)

    return refined_points_3d, refined_camera_poses

def prepare_constraints(manual_adjustment_data, skip_data=50):
    constraint_pairs = {}
    constraint_pts3 = {}
    for i, pair_data in enumerate(manual_adjustment_data):
        constraint_pairs[(i, i+1)] = []
        constraint_pts3[(i, i+1)] = []
        for j, pt_data in enumerate(pair_data):
            if j % skip_data != 0:
                continue
            pt2_1 = np.array(pt_data['feature2d_1'], dtype=np.float64)
            pt2_2 = np.array(pt_data['feature2d_2'], dtype=np.float64)
            pts3 = np.array(pt_data['pts3d_adjusted'], dtype=np.float64)
            constraint_pairs[(i, i+1)].append((pt2_1, pt2_2)) # image coordinates
            constraint_pts3[(i, i+1)].append(pts3) # adjusted 3D points
    return constraint_pairs, constraint_pts3


def run_bundle_adjustment_fixed_cam0(n_cams, n_points, initial_camera_poses, 
                                     initial_points_3d, K_all, camera_indices, 
                                     point_indices, points_2d,
                                     manual_adjustment_data=None,
                                     constraint_pair=None, 
                                     constraint_distance=None):
    cfg = config.get_config()
    enable_manual_adjustment = cfg.get('enable_manual_adjustment', False)
    manual_adjustment_data_source = cfg.get('manual_adjustment_data_source', 'hardcoded')
    init_cam_params = []
    for i, (R, t) in enumerate(initial_camera_poses):
        if i == 0: 
            continue # fixed camera
        rvec, _ = cv2.Rodrigues(R)
        init_cam_params.append(np.hstack([rvec.ravel(), t.ravel()]))

    init_cam_params = np.array(init_cam_params).reshape(-1)
    init_points_3d = initial_points_3d.reshape(-1)
    x0 = np.hstack([init_cam_params, init_points_3d])

    if enable_manual_adjustment and manual_adjustment_data_source == 'file':
        print('Using manual adjustment data from file')
        constraint_pairs, constraint_pts3 = prepare_constraints(manual_adjustment_data)
    else:
        constraint_pairs = None
        constraint_pts3 = None
    
    logging.debug(f'Constraint pairs: {constraint_pairs}')
    logging.debug(f'Constraint pts3: {constraint_pts3}')

    # for debugging
    #TODO: remove
    if enable_manual_adjustment and manual_adjustment_data is not None:
        constraint_pairs_ma, constraint_pts3_ma = prepare_constraints(manual_adjustment_data)
        # plot the constraint points
        pts3 = []
        for pts3_list in constraint_pts3.values():
            for pt3 in pts3_list:
                pt3 = pt3.reshape(-1)
                pts3.append(pt3)
        pts3 = np.array(pts3)
    
        fig = plt.figure(figsize=(10, 8))
        ax = fig.add_subplot(111, projection='3d')
        ax.scatter(pts3[:, 0], pts3[:, 1], pts3[:, 2], c='r', label='hardcoded')
        pts3 = []
        for pts3_list in constraint_pts3_ma.values():
            for pt3 in pts3_list:
                pt3 = pt3.reshape(-1)
                pts3.append(pt3)
        pts3 = np.array(pts3)
        ax.scatter(pts3[:, 0], pts3[:, 1], pts3[:, 2], marker='x',c='b', label='manual adjustment')
        ax.set_xlabel('X')
        ax.set_ylabel('Y')
        ax.set_zlabel('Z')
        ax.legend()
        #plt.show()

    res = least_squares(
        bundle_adjustment_residual_fixed_cam0,
        x0,
        verbose=2,
        x_scale='jac',
        ftol=1e-4, #xtol=1e-4, gtol=1e-4,
        method='trf',
        args=(n_cams, n_points, camera_indices, point_indices, points_2d, K_all, 
              constraint_pair, constraint_distance)
    )
    refined_params = res.x[:(n_cams - 1) * 6].reshape((n_cams - 1, 6))
    refined_points_3d = res.x[(n_cams - 1) * 6:].reshape((n_points, 3))

    refined_camera_poses = []
    R0 = np.eye(3)
    t0 = np.zeros((3, 1))
    refined_camera_poses.append((R0, t0))
    for i in range(n_cams - 1):
        rvec = refined_params[i, :3].reshape(3, 1)
        tvec = refined_params[i, 3:6].reshape(3, 1)
        R, _ = cv2.Rodrigues(rvec)
        refined_camera_poses.append((R, tvec))

    return refined_points_3d, refined_camera_poses


def run_bundle_adjustment_fixed_cam0_with_f(n_cams, n_points, initial_camera_poses, 
                                     initial_points_3d, K_all, camera_indices, 
                                     point_indices, points_2d,
                                     constraint_pair=None, 
                                     constraint_distance=None):
    init_cam_params = []
    for i, (R, t) in enumerate(initial_camera_poses):
        if i == 0: 
            continue # fixed camera
        rvec, _ = cv2.Rodrigues(R)
        f = K_all[i][0, 0]
        init_cam_params.append(np.hstack([rvec.ravel(), t.ravel(), f]))

    init_cam_params = np.array(init_cam_params).reshape(-1)
    init_points_3d = initial_points_3d.reshape(-1)
    x0 = np.hstack([init_cam_params, init_points_3d])

    # distance constraints between 3D points # experiment
    constraint_pairs = []
    constraint_distances = []
    for i in range(len(initial_points_3d)-1):
        constraint_pairs.append((i, i+1))
        constraint_distances.append(np.linalg.norm(initial_points_3d[i] - initial_points_3d[i+1]))
    #constraint_pairs = None


    res = least_squares(
        bundle_adjustment_residual_fixed_cam0_with_f,
        x0,
        verbose=2,
        x_scale='jac',
        ftol=1e-4, #xtol=1e-4, gtol=1e-4,
        method='trf',
        args=(n_cams, n_points, camera_indices, point_indices, points_2d, K_all, 
              constraint_pair, constraint_distance)
    )
    refined_params = res.x[:(n_cams - 1) * 7].reshape((n_cams - 1, 7))
    refined_points_3d = res.x[(n_cams - 1) * 7:].reshape((n_points, 3))

    refined_camera_poses = []
    R0 = np.eye(3)
    t0 = np.zeros((3, 1))
    refined_camera_poses.append((R0, t0))
    for i in range(n_cams - 1):
        rvec = refined_params[i, :3].reshape(3, 1)
        tvec = refined_params[i, 3:6].reshape(3, 1)
        f = refined_params[i, 6]
        K = K_all[i+1]
        K[0, 0] = f
        K[1, 1] = f
        R, _ = cv2.Rodrigues(rvec)
        refined_camera_poses.append((R, tvec))

    return refined_points_3d, refined_camera_poses, K_all

def run_bundle_adjustment_fixed_cam012_with_f(n_cams, n_points, initial_camera_poses, 
                                     initial_points_3d, K_all, camera_indices, 
                                     point_indices, points_2d,
                                     constraint_pair=None, 
                                     constraint_distance=None):
    init_cam_params = []
    for i, (R, t) in enumerate(initial_camera_poses):
        if i == 0 or i == 1 or i == 2: 
            continue # fixed camera
        rvec, _ = cv2.Rodrigues(R)
        f = K_all[i][0, 0]
        init_cam_params.append(np.hstack([rvec.ravel(), t.ravel(), f]))

    init_cam_params = np.array(init_cam_params).reshape(-1)
    init_points_3d = initial_points_3d.reshape(-1)
    x0 = np.hstack([init_cam_params, init_points_3d])

    #constraint_pair = (0, 1) # TODO: temporary hardcoded
    constraint_pair = None
    constraint_distance = np.linalg.norm(init_points_3d[0] - init_points_3d[1])
    res = least_squares(
        bundle_adjustment_residual_fixed_cam012_with_f,
        x0,
        verbose=2,
        x_scale='jac',
        ftol=1e-4, #xtol=1e-4, gtol=1e-4,
        method='trf',
        args=(n_cams, n_points, camera_indices, point_indices, points_2d, K_all, 
              initial_camera_poses,
              constraint_pair, constraint_distance)
    )
    refined_params = res.x[:(n_cams - 3) * 7].reshape((n_cams - 3, 7))
    refined_points_3d = res.x[(n_cams - 3) * 7:].reshape((n_points, 3))

    refined_camera_poses = []
    for i in range(3):
        (R, t) = initial_camera_poses[i]
        refined_camera_poses.append((R, t))

    for i in range(n_cams - 3):
        rvec = refined_params[i, :3].reshape(3, 1)
        tvec = refined_params[i, 3:6].reshape(3, 1)
        f = refined_params[i, 6]
        K = K_all[i+3]
        K[0, 0] = f
        K[1, 1] = f
        R, _ = cv2.Rodrigues(rvec)
        refined_camera_poses.append((R, tvec))

    return refined_points_3d, refined_camera_poses, K_all


def run_bundle_adjustment_fixed_cam0_and_1(n_cams, n_points, initial_camera_poses, 
                                     initial_points_3d, K_all, camera_indices, 
                                     point_indices, points_2d,
                                     constraint_pair=None, 
                                     constraint_distance=None):
    init_cam_params = []
    for i, (R, t) in enumerate(initial_camera_poses):
        if i == 0 or i == 1: 
            continue # fixed camera
        rvec, _ = cv2.Rodrigues(R)
        init_cam_params.append(np.hstack([rvec.ravel(), t.ravel()]))

    init_cam_params = np.array(init_cam_params).reshape(-1)
    init_points_3d = initial_points_3d.reshape(-1)
    x0 = np.hstack([init_cam_params, init_points_3d])

    #constraint_pair = (0, 1) # TODO: temporary hardcoded
    #constraint_distance = np.linalg.norm(init_points_3d[0] - init_points_3d[1])
    res = least_squares(
        bundle_adjustment_residual_fixed_cam0_and_1,
        x0,
        verbose=2,
        x_scale='jac',
        ftol=1e-4, #xtol=1e-4, gtol=1e-4,
        method='trf',
        args=(n_cams, n_points, camera_indices, point_indices, points_2d, K_all, 
              constraint_pair, constraint_distance)
    )
    refined_params = res.x[:(n_cams - 2) * 6].reshape((n_cams - 2, 6))
    refined_points_3d = res.x[(n_cams - 2) * 6:].reshape((n_points, 3))

    refined_camera_poses = []
    R0 = np.eye(3)
    t0 = np.zeros((3, 1))
    refined_camera_poses.append((R0, t0))
    R1 = initial_camera_poses[1][0]
    t1 = initial_camera_poses[1][1]
    refined_camera_poses.append((R1, t1))
    for i in range(n_cams - 2):
        rvec = refined_params[i, :3].reshape(3, 1)
        tvec = refined_params[i, 3:6].reshape(3, 1)
        R, _ = cv2.Rodrigues(rvec)
        refined_camera_poses.append((R, tvec))

    return refined_points_3d, refined_camera_poses


def draw_camera(ax, R, t, scale=0.5, color='black', label=None):
    t = t.reshape(3)
    cam_center = -R.T @ t
    cam_center = cam_center.reshape(3)

    pyramid = np.array([
        [0, 0, 0],
        [-1, -0.75, 1.5],
        [1, -0.75, 1.5],
        [1, 0.75, 1.5],
        [-1, 0.75, 1.5]
    ]) * scale

    pyramid_world = (R.T @ pyramid.T).T + cam_center

    ax.plot([cam_center[0], pyramid_world[1, 0]], [cam_center[1], pyramid_world[1, 1]], [cam_center[2], pyramid_world[1, 2]], color=color)
    ax.plot([cam_center[0], pyramid_world[2, 0]], [cam_center[1], pyramid_world[2, 1]], [cam_center[2], pyramid_world[2, 2]], color=color)
    ax.plot([cam_center[0], pyramid_world[3, 0]], [cam_center[1], pyramid_world[3, 1]], [cam_center[2], pyramid_world[3, 2]], color=color)
    ax.plot([cam_center[0], pyramid_world[4, 0]], [cam_center[1], pyramid_world[4, 1]], [cam_center[2], pyramid_world[4, 2]], color=color)

    for i in range(1, 5):
        ax.plot([pyramid_world[i, 0], pyramid_world[(i % 4) + 1, 0]],
                [pyramid_world[i, 1], pyramid_world[(i % 4) + 1, 1]],
                [pyramid_world[i, 2], pyramid_world[(i % 4) + 1, 2]], color=color)
    # draw axis
    axis = np.array([
        [0, 0, 0],
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1]
    ]) * scale * 2

    axis_world = (R.T @ axis.T).T + cam_center
    ax.plot([cam_center[0], axis_world[1, 0]], 
            [cam_center[1], axis_world[1, 1]], 
            [cam_center[2], axis_world[1, 2]], color='r')
    ax.plot([cam_center[0], axis_world[2, 0]], 
            [cam_center[1], axis_world[2, 1]], 
            [cam_center[2], axis_world[2, 2]], color='g')
    ax.plot([cam_center[0], axis_world[3, 0]], 
            [cam_center[1], axis_world[3, 1]], 
            [cam_center[2], axis_world[3, 2]], color='b')

    if label:
        ax.text(cam_center[0], cam_center[1], cam_center[2], label, color=color)
    

def ba_procedure(n_cams, n_points, initial_camera_poses, initial_points_3d, K_all, 
         camera_indices, point_indices, points_2d, manual_adjustment_data, output_dir,
         no_plot=False):
    """ Main function for bundle adjustment """
    #refined_points_3d, refined_camera_poses = run_bundle_adjustment(
    #    n_cams, n_points, initial_camera_poses, initial_points_3d,
    #    K_all, np.array(camera_indices), np.array(point_indices), points_2d
    #    # , optimization_method='liegroup'
    #)

    # fix cam 0
    refined_points_3d, refined_camera_poses = run_bundle_adjustment_fixed_cam0(
        n_cams, n_points, initial_camera_poses, initial_points_3d,
        K_all, np.array(camera_indices), np.array(point_indices), points_2d,
        manual_adjustment_data
    )
    
    # fix cam 0 with focal length
    #refined_points_3d, refined_camera_poses, refined_K_all = run_bundle_adjustment_fixed_cam0_with_f(
    #    n_cams, n_points, initial_camera_poses, initial_points_3d,
    #    K_all, np.array(camera_indices), np.array(point_indices), points_2d
    #)


    # fix cam 0 and 1
    #refined_points_3d, refined_camera_poses = run_bundle_adjustment_fixed_cam0_and_1(
    #    n_cams, n_points, initial_camera_poses, initial_points_3d,
    #    K_all, np.array(camera_indices), np.array(point_indices), points_2d
    #)

    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection='3d')
    ax.scatter(initial_points_3d[:, 0], initial_points_3d[:, 1], initial_points_3d[:, 2],
               c='red', label='Initial 3D Points')
    ax.scatter(refined_points_3d[:, 0], refined_points_3d[:, 1], refined_points_3d[:, 2],
               c='green', label='Refined 3D Points')
    ax.scatter(initial_points_3d[1:3, 0], initial_points_3d[1:3, 1], initial_points_3d[1:3, 2],
               c='yellow', marker='o', label='Initial 3D Points')
    ax.scatter(refined_points_3d[1:3, 0], refined_points_3d[1:3, 1], refined_points_3d[1:3, 2],
               c='orange', marker='o', label='Refined 3D Points')
    for i, (R, t) in enumerate(initial_camera_poses):
        draw_camera(ax, R, t, color='blue', label=f'Init C{i}')
    for i, (R, t) in enumerate(refined_camera_poses):
        draw_camera(ax, R, t, color='black', label=f'Ref C{i}')
    # set same aspect ratio
    ax.set_aspect('equal')
    ax.set_title("Bundle Adjustment without Intrinsics")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    ax.legend()
    plt.tight_layout()
    plt.savefig(f"{output_dir}/bundle_adjustment_no_intrinsics.png")
    #if not no_plot:
    #    plt.show()

    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection='3d')
    ax.scatter(refined_points_3d[:, 0], refined_points_3d[:, 1], refined_points_3d[:, 2],
               c='green', label='Refined 3D Points')
    for i, (R, t) in enumerate(initial_camera_poses):
        draw_camera(ax, R, t, color='blue', label=f'Init C{i}')
    for i, (R, t) in enumerate(refined_camera_poses):
        draw_camera(ax, R, t, color='black', label=f'Ref C{i}')
    # set same aspect ratio
    ax.set_aspect('equal')
    ax.set_title("Bundle Adjustment without Intrinsics")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    ax.legend()
    plt.tight_layout()
    plt.savefig(f"{output_dir}/bundle_adjustment_no_intrinsics.png")
    #if not no_plot:
    #    plt.show()
    return refined_points_3d, refined_camera_poses

def read_data(data_path):
    with open(data_path, "r") as f:
        data = json.load(f)
    return data

def apply_transform(R, t, points): #TODO: move to utils.py
    return np.dot(R, points) + t

def calculate_reproj_error_with_projection_matrix(pt3d, image_position, P):
    """
    Calculate reprojection error using projection matrix directly.
    
    Args:
        pt3d: 3D point (N, 3) or (3,) 
        image_position: 2D image point (2,) or (N, 2)
        P: 3x4 projection matrix
        
    Returns:
        reproj_error: reprojection error in pixels
        pt2d_proj: projected 2D point
    """
    # Ensure proper shapes
    if pt3d.ndim == 1:
        pt3d = pt3d.reshape(1, 3)
    if image_position.ndim == 1:
        image_position = image_position.reshape(1, 2)
    
    # Convert to homogeneous coordinates
    pt3d_h = np.concatenate([pt3d, np.ones((pt3d.shape[0], 1))], axis=1)
    
    # Project using projection matrix
    pt2d_h = (P @ pt3d_h.T).T
    pt2d_proj = pt2d_h[:, :2] / pt2d_h[:, 2:3]
    
    # Calculate reprojection error
    reproj_error = np.linalg.norm(pt2d_proj - image_position, axis=1)
    
    return reproj_error[0], pt2d_proj[0] 

def calculate_reproj_error(pt3d_cam_A, image_position, R, t, K):
    rvec, _ = cv2.Rodrigues(R)
    if t.ndim == 1:
        t = t.reshape(3, 1)
    pt2d_proj, _ = cv2.projectPoints(pt3d_cam_A.reshape(1, 1, 3), rvec, t, K, None)
    pt2d_proj = pt2d_proj.ravel()
    reproj_error = np.linalg.norm(pt2d_proj - image_position)
    return reproj_error, pt2d_proj

def is_in_image(pt2d_proj, image_size):
    return (pt2d_proj[0] > 0 and pt2d_proj[0] < image_size[0] and 
            pt2d_proj[1] > 0 and pt2d_proj[1] < image_size[1])

def convert_data_for_ba(two_view_results, Rglob_0i, tglob_0i, tracklet_to_global, 
                        skip_frame=3, output_dir=None, no_plot=False):
    """ Convert the data to the format of bundle_adjustment

    Args:
        two_view_results(list): the list of data
        Rglob_0i(list): the global rotation matrices
        tglob_0i(list): the global translation vectors
        tracklet_to_global(dict): the tracklet to global id mapping
        skip_frame(int): the skip frame

    Returns:
        cam_ids_sorted(list): sorted list of original camera IDs
        n_points(int): the number of points
        initial_camera_poses_dict(dict): the initial camera poses by camera ID
        initial_points_3d(list): the initial 3D points
        K_all_dict(dict): the camera intrinsics by camera ID
        camera_indices(list): the camera indices (original camera IDs)
        point_indices(list): the point indices
        points_2d(list): the 2D points
    """
    cfg = config.get_config()
    img_w = cfg['video_resolution'][0]
    img_h = cfg['video_resolution'][1]
    # cameras
    cam_ids = set({})
    n_camera_pair = len(two_view_results)
    for data in two_view_results:
        cams = data["cameras"]
        for cam in cams:
            cam_ids.add(cam["id"])
    
    # Keep original camera IDs in sorted order for consistency
    cam_ids_sorted = sorted(list(cam_ids))
    logging.info(f"Camera IDs (preserving original indices): {cam_ids_sorted}")
    
    # Create mapping from camera ID to global pose index
    # First camera (cam_ids_sorted[0]) is at index 0 (identity)
    # Subsequent cameras follow the order they appear in camera pairs
    cam_id_to_global_index = {cam_ids_sorted[0]: 0}  # First camera is identity
    for i in range(n_camera_pair):
        cam_id_B = two_view_results[i]['cameras'][1]['id']
        cam_id_to_global_index[cam_id_B] = i + 1
    
    # Use dictionaries to preserve original camera IDs
    K_all_dict = {}
    initial_camera_poses_dict = {}
    
    # Initialize first camera as identity (use first camera ID in sorted order)
    first_cam_id = cam_ids_sorted[0]
    initial_camera_poses_dict[first_cam_id] = (np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64),
                                              np.array([0, 0, 0], dtype=np.float64).reshape(3, 1))
    
    f = two_view_results[0]['cameras'][0]['f']
    K_all_dict[first_cam_id] = np.array([[f, 0, img_w//2], 
                                        [0, f, img_h//2], 
                                        [0, 0, 1]], dtype=np.float64)
    
    # Loop through camera pairs to set up K matrices and poses
    for i in range(n_camera_pair):
        # Get camera IDs 
        cam_id_B = two_view_results[i]['cameras'][1]['id']
        
        # Get focal length from current camera pair
        f = two_view_results[i]['cameras'][1]['f']
        
        # Set K matrix for current camera using original camera ID
        K_all_dict[cam_id_B] = np.array([[f, 0, img_w//2], 
                                        [0, f, img_h//2], 
                                        [0, 0, 1]], dtype=np.float64)
        
        # Get and set global camera pose using original camera ID
        R = Rglob_0i[i+1]
        t = tglob_0i[i+1]
        initial_camera_poses_dict[cam_id_B] = (np.array(R, dtype=np.float64),
                                              np.array(t, dtype=np.float64).reshape(3, 1))

    # points
    initial_points_3d = []
    camera_indices = [] # observations made by cameras
    point_indices = []  # which 3D point they correspond to
    points_2d = [] # 2D image locations
    frame_ids = [] # frame ids of the matches

    pts3d_vis = {} # 3D points to visualize

    matches_0 = two_view_results[0]["matches"]
    pt_count = [0] * n_camera_pair

    n_frame = len(matches_0)
    for fid in range(n_frame):
        if fid % skip_frame !=0:
            continue
        avg_points_3d = {} # average 3D points of the matched points
        indices_3d = []
        points_2d_dict = {}
        for i, two_view_result in enumerate(two_view_results):
            cam_id_A = two_view_result["cameras"][0]["id"]
            cam_id_B = two_view_result["cameras"][1]["id"]
            if fid >= len(two_view_result["matches"]):
                continue
            frame_match_data = two_view_result["matches"][fid]
            tids_A = frame_match_data["id_map_A2B"].keys() # track ids in cam A in the current frame
            for tid_A in tids_A:
                id_A = str(tid_A)
                id_B = str(frame_match_data["id_map_A2B"][tid_A])
                image_position_A = frame_match_data["image_position_A"][id_A]
                image_position_B = frame_match_data["image_position_B"][id_B]
                pt3d_cam_A = np.array(frame_match_data["world_cam_A"][id_A]).reshape(3, 1) # 3D point in cam A
                
                # TODO: experiment. 
                # filter out the points with large reprojection error
                reproj_error_A, pt2d_proj_A = calculate_reproj_error(pt3d_cam_A, image_position_A, 
                                                        np.eye(3), np.zeros((3, 1), dtype=np.float64), K_all_dict[cam_id_A])
                if reproj_error_A > 20: #TODO: revisit this threshold
                    #print(f'reproj_error_A: {reproj_error_A}')
                    continue
                R_B = np.array(two_view_result["cameras"][1]["R"], dtype=np.float64)
                t_B = np.array(two_view_result["cameras"][1]["t"], dtype=np.float64).reshape(3, 1)
                reproj_error_B, pt2d_proj_B = calculate_reproj_error(pt3d_cam_A, image_position_B, 
                                                        R_B, t_B, K_all_dict[cam_id_B])
                if reproj_error_B > 20: #TODO: revisit this threshold
                    #print(f'reproj_error_B: {reproj_error_B}')
                    continue
                
                if not is_in_image(pt2d_proj_A, (img_w, img_h)) or not is_in_image(pt2d_proj_B, (img_w, img_h)):
                    logging.debug(f'pt2d_proj_A: {pt2d_proj_A}, pt2d_proj_B: {pt2d_proj_B}')
                    continue


                global_idx_A = cam_id_to_global_index[cam_id_A]
                pt3d_global = apply_transform(Rglob_0i[global_idx_A].T, 
                                              -np.dot(Rglob_0i[global_idx_A].T, tglob_0i[global_idx_A].reshape(3, 1)), 
                                              pt3d_cam_A) # 3D point in global coordinate system
                if (cam_id_A, cam_id_B) not in pts3d_vis:
                    pts3d_vis[(cam_id_A, cam_id_B)] = []
                pts3d_vis[(cam_id_A, cam_id_B)].append(pt3d_global)

                # get a dictionary of global id to 
                # point_3d, point_indices, points_2d
                # points_2d_dict: {global_id: {(cam_id, tid): point_2d}}
                global_id = tracklet_to_global[(cam_id_A, int(id_A))]
                
                if global_id not in avg_points_3d:
                    avg_points_3d[global_id] = []
                    points_2d_dict[global_id] = {}
                avg_points_3d[global_id].append(pt3d_global)
                points_2d_dict[global_id][(cam_id_A, id_A)] = image_position_A
                points_2d_dict[global_id][(cam_id_B, id_B)] = image_position_B
                pt_count[i] += 1

        # get the average 3D points
        for global_id in avg_points_3d.keys():
            # TODO: experiment. use the average point as the initial 3D point
            avg_point_3d = np.mean(avg_points_3d[global_id], axis=0)
            initial_points_3d.append(avg_point_3d)

            # TODO: experiment. use the first point as the initial 3D point
            #first_point_3d = avg_points_3d[global_id][0]
            #initial_points_3d.append(first_point_3d)

            idx = len(initial_points_3d) - 1
            frame_ids.append(fid)
            for cam_id, tid in points_2d_dict[global_id].keys():
                camera_indices.append(cam_id)  # Use original camera ID
                point_indices.append(idx)
                points_2d.append(points_2d_dict[global_id][(cam_id, tid)])
    # print the number of points for each camera pair
    for i in range(n_camera_pair):
        cam_id_A = two_view_results[i]["cameras"][0]["id"]
        cam_id_B = two_view_results[i]["cameras"][1]["id"]
        logging.info(f'cam{cam_id_A}-{cam_id_B} has {pt_count[i]} points')

    # plot the 3d points using matplotlib
    fig = plt.figure(figsize=(15,5))
    ax = fig.add_subplot(121, projection='3d')
    colors = ['red', 'green', 'blue', 'y', 'purple', 'orange', 'brown', 'pink', 'gray', 'cyan']
    for (cam_id_A, cam_id_B), pts3d_list in pts3d_vis.items():
        pts3d = np.array(pts3d_list)
        color_idx = cam_ids_sorted.index(cam_id_A) if cam_id_A in cam_ids_sorted else 0
        ax.scatter(pts3d[:, 0], pts3d[:, 1], pts3d[:, 2], c=colors[color_idx % len(colors)], s=50, marker='.')
    # Set the view angle: elev (vertical), azim (horizontal)
    ax.view_init(elev=45, azim=90)  # You can adjust these values as needed
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')
    ax = fig.add_subplot(122, projection='3d')
    for (cam_id_A, cam_id_B), pts3d_list in pts3d_vis.items():
        pts3d = np.array(pts3d_list)
        color_idx = cam_ids_sorted.index(cam_id_A) if cam_id_A in cam_ids_sorted else 0
        ax.scatter(pts3d[:, 0], pts3d[:, 1], pts3d[:, 2], c=colors[color_idx % len(colors)], s=50, marker='.')
    # Set the view angle: elev (vertical), azim (horizontal)
    ax.view_init(elev=0, azim=0)  # You can adjust these values as needed
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')
    # write the number of points for each camera pair in the figure 
    n_points = 0
    for i in range(n_camera_pair):
        cam_id_A = two_view_results[i]["cameras"][0]["id"]
        cam_id_B = two_view_results[i]["cameras"][1]["id"]
        fig.text(0.15, 0.92 - i*0.04, f'cam{cam_id_A}-{cam_id_B}: {pt_count[i]} points', fontsize=12)
        n_points += pt_count[i]
    ax.set_title(f'Total points: {n_points} points')
    plt.tight_layout()
    plt.savefig(f'{output_dir}/3d_points.png')
    if not no_plot:
        plt.show()

    n_points = len(initial_points_3d)
    points_2d = np.array(points_2d, dtype=np.float64)
    initial_points_3d = np.array(initial_points_3d, dtype=np.float64)
    initial_points_3d = initial_points_3d.reshape((n_points, 3))
    return cam_ids_sorted, n_points, initial_camera_poses_dict, initial_points_3d, K_all_dict, camera_indices, point_indices, points_2d, frame_ids


def visualize_data(data_list, video_path_0, video_path_1, video_path_3=None, 
                   refined_camera_poses=None, frame_ids=None, 
                   refined_points_3d=None, output_dir=None, no_plot=False):
    cfg = config.get_config()
    img_w = cfg['video_resolution'][0]
    img_h = cfg['video_resolution'][1]
    data_0 = data_list[0]
    data_1 = data_list[1]
    f_0 = data_list[0]['cameras'][0]['f']
    f_1 = data_list[0]['cameras'][1]['f']
    f_3 = data_list[1]['cameras'][1]['f']
    K_0 = np.array([[f_0, 0, img_w//2], [0, f_0, img_h//2], [0, 0, 1]], dtype=np.float64)
    K_1 = np.array([[f_1, 0, img_w//2], [0, f_1, img_h//2], [0, 0, 1]], dtype=np.float64)
    K_3 = np.array([[f_3, 0, img_w//2], [0, f_3, img_h//2], [0, 0, 1]], dtype=np.float64)
    R_0 = np.array(data_list[0]['cameras'][0]['R'], dtype=np.float64)
    t_0 = np.array(data_list[0]['cameras'][0]['t'], dtype=np.float64).reshape(3, 1)
    R_1 = np.array(data_list[0]['cameras'][1]['R'], dtype=np.float64)
    t_1 = np.array(data_list[0]['cameras'][1]['t'], dtype=np.float64).reshape(3, 1)
    R_3 = np.array(data_list[1]['cameras'][1]['R'], dtype=np.float64)
    t_3 = np.array(data_list[1]['cameras'][1]['t'], dtype=np.float64).reshape(3, 1)
    cap_0 = open_video_capture(video_path_0)
    cap_1 = open_video_capture(video_path_1)
    cap_3 = open_video_capture(video_path_3)
    # video writer
    out_0 = open_video_writer(f'{output_dir}/output_0.mp4', 30.0, (img_w, img_h))
    out_1 = open_video_writer(f'{output_dir}/output_1.mp4', 30.0, (img_w, img_h))
    out_3 = open_video_writer(f'{output_dir}/output_3.mp4', 30.0, (img_w, img_h))
    #while cap_0.isOpened() and cap_1.isOpened() and cap_3.isOpened():

    def draw_match(frame, match, K, R, t, which_camera, c):
        id_map_A2B = match["id_map_A2B"]
        world_cam_A = match["world_cam_A"]
        if which_camera == 'A':
            pts2d = match["image_position_A"]
        else:
            pts2d = match["image_position_B"]
        for id_A in id_map_A2B.keys():
            id_B = str(id_map_A2B[id_A])
            if which_camera == 'A':
                cv2.circle(frame, pts2d[id_A], 5, (0, 0, 255), -1)
            else:
                cv2.circle(frame, pts2d[id_B], 5, (0, 0, 255), -1)
            pt3d = np.array(world_cam_A[id_A], dtype=np.float64).reshape(1, 1, 3)
            pt2d, _ = cv2.projectPoints(pt3d, cv2.Rodrigues(R)[0], t, K, None)
            cv2.circle(frame, (int(pt2d.ravel()[0]), int(pt2d.ravel()[1])), 5, c, -1)
    
    for fid, match_0 in enumerate(data_0["matches"]):
        ret_0, frame_0 = cap_0.read()
        ret_1, frame_1 = cap_1.read()
        ret_3, frame_3 = cap_3.read()
        if not ret_0 or not ret_1 or not ret_3:
            break
        draw_match(frame_0, match_0, K_0, R_0, t_0, 'A', (0, 255, 0))
        draw_match(frame_1, match_0, K_1, R_1, t_1, 'B', (0, 255, 0))
        match_1 = data_1["matches"][fid]
        draw_match(frame_3, match_1, K_3, R_3, t_3, 'B', (0, 255, 0))

        for idx in range(len(frame_ids)):
            if fid != frame_ids[idx]:
                continue
            (R_0_refined, t_0_refined) = refined_camera_poses[0]
            (R_1_refined, t_1_refined) = refined_camera_poses[1]
            if len(refined_camera_poses) == 3:
                (R_3_refined, t_3_refined) = refined_camera_poses[2]
            pt3d = refined_points_3d[idx].reshape(1, 1, 3)
            pt2d, _ = cv2.projectPoints(pt3d, cv2.Rodrigues(R_0_refined)[0], t_0_refined, K_0, None)
            cv2.circle(frame_0, (int(pt2d.ravel()[0]), int(pt2d.ravel()[1])), 10, (0, 255, 255), -1)
            pt2d, _ = cv2.projectPoints(pt3d, cv2.Rodrigues(R_1_refined)[0], t_1_refined, K_1, None)
            cv2.circle(frame_1, (int(pt2d.ravel()[0]), int(pt2d.ravel()[1])), 10, (0, 255, 255), -1)
            if len(refined_camera_poses) == 3:
                pt2d, _ = cv2.projectPoints(pt3d, cv2.Rodrigues(R_3_refined)[0], t_3_refined, K_3, None)
                cv2.circle(frame_3, (int(pt2d.ravel()[0]), int(pt2d.ravel()[1])), 10, (0, 255, 255), -1)

        out_0.write(frame_0)
        out_1.write(frame_1)
        out_3.write(frame_3)

    cap_0.release()
    cap_1.release()
    cap_3.release()
    out_0.release()
    out_1.release()
    out_3.release()
    cv2.destroyAllWindows()

def visualize_results(n_cams:int,data_list:list, video_paths:list, 
                      K_all:list, camera_poses:list, 
                      tracklet_to_global:dict, points_3d:np.ndarray, 
                      frame_ids:list, output_dir:str, cam_ids_sorted:list=None):
    cfg = config.get_config()
    skip_frame = cfg['skip_frame']
    # make dirs using original camera IDs if available
    if cam_ids_sorted is not None:
        for cam_id in cam_ids_sorted:
            os.makedirs(f'{output_dir}/output_{cam_id:02d}', exist_ok=True)
    else:
        # fallback to sequential indexing for backward compatibility
        for i in range(n_cams):
            os.makedirs(f'{output_dir}/output_{i:02d}', exist_ok=True)

    # initialize video captures and writers
    cap_list = []
    for i in range(n_cams):
        video_path = video_paths[i]
        cap = open_video_capture(video_path)
        cap_list.append(cap)

    # draw the 3D points on the image plane of each camera
    n_frames = int(cap_list[0].get(cv2.CAP_PROP_FRAME_COUNT))
    for fid in range(n_frames):
        if fid % skip_frame != 0:
            continue
        frame_list = [] 
        is_end = False
        for i in range(n_cams):
            cap = cap_list[i]
            ret, frame = cap.read()
            if not ret:
                is_end = True
                break
            frame_list.append(frame)
        if is_end:
            break

        for idx in range(len(frame_ids)): # frame_ids: frame id of the 3D points
            if fid != frame_ids[idx]:
                continue
            # project the 3D points to the image plane of each camera
            for cam_id in range(n_cams):
                (R, t) = camera_poses[cam_id]
                pt3d = points_3d[idx].reshape(1, 1, 3)
                pt2d, _ = cv2.projectPoints(pt3d, cv2.Rodrigues(R)[0], t, K_all[cam_id], None)
                cv2.circle(frame_list[cam_id], 
                           (int(pt2d.ravel()[0]), int(pt2d.ravel()[1])), 10, 
                           (0, 255, 255), -1)

        # Save images using original camera IDs if available
        if cam_ids_sorted is not None:
            for i in range(n_cams):
                cam_id = cam_ids_sorted[i]
                cv2.imwrite(f'{output_dir}/output_{cam_id:02d}/{fid:06d}.jpg', frame_list[i])
        else:
            # fallback to sequential indexing for backward compatibility
            for i in range(n_cams):
                cv2.imwrite(f'{output_dir}/output_{i:02d}/{fid:06d}.jpg', frame_list[i])

    for cap in cap_list:
        cap.release()
    cv2.destroyAllWindows()


def save_results(K_all_dict, initial_camera_poses_dict, refined_camera_poses_dict, 
                 refined_points_3d, frame_ids, output_dir, cam_ids_sorted, all_original_points_3d=None):
    """
    Save calibration results to disk using original camera IDs.
    
    Args:
        K_all_dict: Camera intrinsic matrices by camera ID
        initial_camera_poses_dict: Original camera poses by camera ID
        refined_camera_poses_dict: Optimized camera poses by camera ID  
        refined_points_3d: Optimized 3D points (inliers only)
        frame_ids: Frame IDs for the points
        output_dir: Output directory
        cam_ids_sorted: Sorted list of original camera IDs
        all_original_points_3d: Complete original 3D points including outliers
    """
    os.makedirs(f'{output_dir}/initial', exist_ok=True)
    os.makedirs(f'{output_dir}/refined', exist_ok=True)

    if initial_camera_poses_dict is not None:
        # Use dictionary with original camera IDs as keys for initial poses too
        initial_camera_info = {}
        for cam_id in cam_ids_sorted:
            K = K_all_dict[cam_id]
            R = initial_camera_poses_dict[cam_id][0]
            t = initial_camera_poses_dict[cam_id][1].reshape(3, 1)
            proj_mat = np.dot(K, np.concatenate((R, t), axis=1))
            initial_camera_info[cam_id] = {
                'K': K.tolist(),
                'R': R.tolist(),
                't': t.tolist(),
                "projectionMatrix_3x4": proj_mat.tolist()
            }
            with open(f'{output_dir}/initial/camInfo_{cam_id:02d}.yaml', 'w') as f:
                yaml.dump(initial_camera_info[cam_id], f)
   
    # Use dictionary with original camera IDs as keys instead of list
    camera_info = {}
    for cam_id in cam_ids_sorted:
        K = K_all_dict[cam_id]
        R = refined_camera_poses_dict[cam_id][0]
        t = refined_camera_poses_dict[cam_id][1].reshape(3, 1)
        proj_mat = np.dot(K, np.concatenate((R, t), axis=1))
        camera_info[cam_id] = {
            'K': K.tolist(),
            'R': R.tolist(),
            't': t.tolist(),
            "projectionMatrix_3x4": proj_mat.tolist()
        }
        with open(f'{output_dir}/refined/camInfo_{cam_id:02d}.yaml', 'w') as f:
            yaml.dump(camera_info[cam_id], f)
    
    if refined_points_3d is not None:
        # Prepare results with refined points (inliers only)
        results = {
            'cameras': camera_info,
            'points_3d': refined_points_3d.tolist(),
            'frame_ids': frame_ids
        }
        
        # If we have the complete original dataset, include it for visualization
        if all_original_points_3d is not None:
            results['points_3d_complete'] = all_original_points_3d.tolist()
            logging.info(f"Saved {len(refined_points_3d)} refined points and {len(all_original_points_3d)} complete original points")
        
        with open(f'{output_dir}/bundle_adjustment_results.json', 'w') as f:
            json.dump(results, f, indent=2)

def check_triangulation_quality(P_A, P_B, pt2d_A, pt2d_B):
    """
    Check the quality of triangulation before performing it.
    
    Args:
        P_A, P_B: 3x4 projection matrices
        pt2d_A, pt2d_B: 2D points in homogeneous coordinates
        
    Returns:
        quality_score: Higher is better, < 0.1 indicates poor triangulation
    """
    # Convert to homogeneous coordinates if needed
    if pt2d_A.shape[-1] == 2:
        pt2d_A_h = np.concatenate([pt2d_A, np.ones((pt2d_A.shape[0], 1))], axis=1)
        pt2d_B_h = np.concatenate([pt2d_B, np.ones((pt2d_B.shape[0], 1))], axis=1)
    else:
        pt2d_A_h = pt2d_A
        pt2d_B_h = pt2d_B
    
    # Get camera centers
    C_A = -np.linalg.inv(P_A[:, :3]) @ P_A[:, 3]
    C_B = -np.linalg.inv(P_B[:, :3]) @ P_B[:, 3]
    
    # Get ray directions
    ray_A = np.linalg.inv(P_A[:, :3]) @ pt2d_A_h.T
    ray_B = np.linalg.inv(P_B[:, :3]) @ pt2d_B_h.T
    
    # Normalize rays
    ray_A = ray_A / np.linalg.norm(ray_A, axis=0, keepdims=True)
    ray_B = ray_B / np.linalg.norm(ray_B, axis=0, keepdims=True)
    
    # Calculate angle between rays
    cos_angle = np.sum(ray_A * ray_B, axis=0)
    cos_angle = np.clip(cos_angle, -1, 1)  # Avoid numerical issues
    angle = np.arccos(np.abs(cos_angle))
    
    # Quality score: angle should be large (rays should intersect well)
    quality_score = np.sin(angle)
    
    return quality_score

def triangulate_points_robust(P_A, P_B, pt2d_A, pt2d_B):
    """
    More robust triangulation that handles parallel rays better.
    
    Args:
        P_A, P_B: 3x4 projection matrices
        pt2d_A, pt2d_B: 2D points
        
    Returns:
        pt3d: 3D points
    """
    # Convert to homogeneous coordinates
    pt2d_A_h = np.concatenate([pt2d_A, np.ones((pt2d_A.shape[0], 1))], axis=1)
    pt2d_B_h = np.concatenate([pt2d_B, np.ones((pt2d_B.shape[0], 1))], axis=1)
    
    # Get camera centers
    C_A = -np.linalg.inv(P_A[:, :3]) @ P_A[:, 3]
    C_B = -np.linalg.inv(P_B[:, :3]) @ P_B[:, 3]
    
    # Get ray directions
    ray_A = np.linalg.inv(P_A[:, :3]) @ pt2d_A_h.T
    ray_B = np.linalg.inv(P_B[:, :3]) @ pt2d_B_h.T
    
    # Normalize rays
    ray_A = ray_A / np.linalg.norm(ray_A, axis=0, keepdims=True)
    ray_B = ray_B / np.linalg.norm(ray_B, axis=0, keepdims=True)
    
    # Find closest point between skew lines
    # This is more robust than standard triangulation
    n = np.cross(ray_A.T, ray_B.T)  # Normal to both rays
    n = n / np.linalg.norm(n, axis=1, keepdims=True)
    
    # Distance between rays
    d = np.abs(np.sum((C_B - C_A) * n, axis=1))
    
    # If rays are too parallel, use midpoint
    parallel_threshold = 0.01
    parallel_mask = d < parallel_threshold
    
    pt3d = np.zeros((pt2d_A.shape[0], 3))
    
    for i in range(pt2d_A.shape[0]):
        if parallel_mask[i]:
            # Use midpoint between camera centers for parallel rays
            pt3d[i] = (C_A + C_B) / 2
        else:
            # Use standard triangulation
            pt3d_h = cv2.triangulatePoints(P_A, P_B, pt2d_A[i:i+1].T, pt2d_B[i:i+1].T)
            pt3d[i] = (pt3d_h[:3] / pt3d_h[3]).flatten()
    
    return pt3d

def triangulate_points(P_A, P_B, pt2d_A, pt2d_B):
    """
    Triangulate 3D points from 2D correspondences.
    
    Args:
        P_A: 3x4 projection matrix for camera A
        P_B: 3x4 projection matrix for camera B  
        pt2d_A: Nx2 array of 2D points in camera A
        pt2d_B: Nx2 array of 2D points in camera B
        
    Returns:
        pt3d: Nx3 array of triangulated 3D points
    """
    # Ensure inputs are in correct format
    P_A = np.array(P_A, dtype=np.float64)
    P_B = np.array(P_B, dtype=np.float64)
    pt2d_A = np.array(pt2d_A, dtype=np.float64)
    pt2d_B = np.array(pt2d_B, dtype=np.float64)
    
    # Handle single point case
    if pt2d_A.ndim == 1:
        pt2d_A = pt2d_A.reshape(1, 2)
        pt2d_B = pt2d_B.reshape(1, 2)
    
    # Check triangulation quality
    quality_scores = check_triangulation_quality(P_A, P_B, pt2d_A, pt2d_B)
    poor_quality = quality_scores < 0.1
    
    if np.any(poor_quality):
        #print(f"Warning: {np.sum(poor_quality)} points have poor triangulation quality (parallel rays)")
        #print(f"Quality scores: {quality_scores}")
        return None
    
    # Triangulate points
    pt3d_h = cv2.triangulatePoints(P_A, P_B, pt2d_A.T, pt2d_B.T)
    
    # Check for points at infinity (W ≈ 0)
    w_values = pt3d_h[3]
    infinity_points = np.abs(w_values) < 1e-6
    
    if np.any(infinity_points):
        print(f"Warning: {np.sum(infinity_points)} points triangulated at infinity (W ≈ 0)")
        print(f"W values: {w_values}")
        print(f"Min W: {np.min(np.abs(w_values))}")
    
    # Convert from homogeneous to 3D coordinates
    # Add small epsilon to avoid division by zero
    epsilon = 1e-10
    pt3d = (pt3d_h[:3] / (pt3d_h[3] + epsilon)).T
    
    # Check for extreme values that might indicate numerical issues
    large_values = np.abs(pt3d) > 1e6
    if np.any(large_values):
        print(f"Warning: {np.sum(large_values)} coordinates have extreme values (>1e6)")
        print(f"Max values: {np.max(np.abs(pt3d), axis=0)}")
        return None
    
    return pt3d

def evaluate_results(data_list, refined_camera_poses, tracklet_to_global, 
                    video_paths, output_dir):
    """
    Evaluate the results of the bundle adjustment.
    - evaluate results
    - calculate reprojection error
    - visualize reprojection of 3D points in the global coordinate system
    - visualize 3D points in the global coordinate system
    """
    # calculate reprojection error
    points_3d_global = {}
    reproj_error = 0.0
    for data in data_list:
        cam_id_A = data["cameras"][0]["id"]
        cam_id_B = data["cameras"][1]["id"]
        K_A = data["cameras"][0]["K"]
        K_B = data["cameras"][1]["K"]
        R_A = refined_camera_poses[cam_id_A][0]
        t_A = refined_camera_poses[cam_id_A][1]
        R_B = refined_camera_poses[cam_id_B][0]
        t_B = refined_camera_poses[cam_id_B][1]
        P_A = K_A @ np.hstack((R_A, t_A.reshape(3,1)))
        P_B = K_B @ np.hstack((R_B, t_B.reshape(3,1)))
        for frame_data in data["matches"]:
            tid_list = frame_data["id_map_A2B"].keys()
            for tid_A in tid_list:
                tid_B = frame_data["id_map_A2B"][tid_A]
                pt2d_A = frame_data["image_position_A"][tid_A]
                pt2d_B = frame_data["image_position_B"][tid_B]
                # triangulate the 3D points
                pt3d_global = triangulate_points(P_A, P_B, pt2d_A, pt2d_B)
                global_tid = tracklet_to_global[(cam_id_A, int(tid_A))]
                if global_tid not in points_3d_global:
                    points_3d_global[global_tid] = []
                points_3d_global[global_tid].append(pt3d_global)
    

def calculate_reprojection_errors(camera_poses, points_3d, K_all,
                                  camera_indices, point_indices, points_2d):
    """Calculate reprojection error for each observation."""
    errors = []
    for i in range(len(camera_indices)):
        cam_idx = camera_indices[i]
        pt_idx = point_indices[i]
        
        # Skip if point index is out of bounds (e.g., filtered out)
        if pt_idx >= len(points_3d):
            errors.append(np.inf)
            continue
            
        obs_2d = points_2d[i]
        R, t = camera_poses[cam_idx]
        pt_3d = points_3d[pt_idx]
        K = K_all[cam_idx]
        
        rvec, _ = cv2.Rodrigues(R)
        
        # Project point and calculate error
        proj_2d = project_point(pt_3d, rvec, t, K)
        error = np.linalg.norm(proj_2d - obs_2d)
        errors.append(error)
        
    return np.array(errors)


def run_iterative_bundle_adjustment(
    n_cams, n_points, initial_camera_poses, initial_points_3d, K_all,
    camera_indices, point_indices, points_2d, frame_ids,
    max_iterations=3, outlier_threshold_pixels=5.0,
    output_dir_base="output/iterative_ba",
    manual_adjustment_data=None,
    no_plot=False
):
    """
    Performs bundle adjustment iteratively, removing outliers at each step.
    
    Returns both refined results and complete original dataset for visualization.
    """
    # Make copies to avoid modifying original data
    active_cam_indices = np.array(camera_indices)
    active_pt_indices = np.array(point_indices)
    active_pts_2d = np.array(points_2d)

    # These are updated in each iteration
    current_poses = initial_camera_poses
    current_points_3d = initial_points_3d

    # Keep track of original data for final visualization
    original_points_3d = initial_points_3d.copy()
    original_camera_poses = [pose for pose in initial_camera_poses]

    for i in range(max_iterations):
        logging.info(f"--- Iteration {i+1}/{max_iterations} ---")
        logging.info(f"Active observations: {len(active_cam_indices)}")
        
        # Create output directory for this iteration
        iter_output_dir = f"{output_dir_base}/iteration_{i+1}"
        iter_output_dir = validate_output_directory(iter_output_dir, f"iteration {i+1} output directory")
        
        # Run bundle adjustment on current active data
        current_points_3d, current_poses = ba_procedure(
            n_cams, n_points, current_poses, current_points_3d, K_all,
            active_cam_indices, active_pt_indices, active_pts_2d,
            manual_adjustment_data,
            iter_output_dir, no_plot
        )
        
        # Calculate reprojection errors for all active observations
        errors = calculate_reprojection_errors(
            current_poses, current_points_3d, K_all,
            active_cam_indices, active_pt_indices, active_pts_2d
        )
        
        # Identify outliers
        inlier_mask = errors <= outlier_threshold_pixels
        n_outliers = np.sum(~inlier_mask)
        
        logging.info(f"Reprojection errors: mean={np.mean(errors):.3f}, "
                    f"std={np.std(errors):.3f}, max={np.max(errors):.3f}")
        logging.info(f"Outliers (>{outlier_threshold_pixels}px): {n_outliers}/{len(errors)}")
        
        if n_outliers == 0:
            logging.info("Convergence: No more outliers found.")
            break

        # Filter the observations for the next iteration
        active_cam_indices = active_cam_indices[inlier_mask]
        active_pt_indices = active_pt_indices[inlier_mask]
        active_pts_2d = active_pts_2d[inlier_mask]
    
    logging.info("--- Iterative Bundle Adjustment Finished ---")
    
    # Return both refined results and complete original dataset
    return {
        'refined_points_3d': current_points_3d,
        'refined_camera_poses': current_poses,
        'original_points_3d': original_points_3d,
        'original_camera_poses': original_camera_poses,
        'frame_ids': frame_ids
    }

def read_manual_adjustment_data(manual_adjustment_dir):
    # Validate the manual adjustment directory path
    manual_adjustment_dir = validate_input_directory(manual_adjustment_dir, "manual adjustment directory")
    
    data_list = []
    files = os.listdir(manual_adjustment_dir)
    files.sort()
    for file in files:
        if file.startswith('manual_adjustment_') and file.endswith('.json'):
            with open(os.path.join(manual_adjustment_dir, file), 'r') as f:
                data = json.load(f)
                data_list.append(data)
    return data_list

def get_single_view_calib_dirs(config:dict, input_base_dir:str):
    """
    Get single view calibration directories by building from base_dir + cam_dir
    
    Args:
        config(dict): config dictionary
        input_base_dir(str): base directory for single view calibration outputs
    Returns:
        list: list of single view calibration directory paths
    """
    cam_dirs = config['cam_dir']
    return [os.path.join(input_base_dir, cam_dir) for cam_dir in cam_dirs]

def main(cfg:dict, no_plot=False, input_base_dir:str=None, output_base_dir:str=None):
    # Validate and sanitize directory paths
    if output_base_dir:
        output_base_dir = validate_output_directory(output_base_dir, "output base directory")
    if input_base_dir:
        input_base_dir = validate_input_directory(input_base_dir, "input base directory")
    
    pairwise_calib_output_dir = output_base_dir
    sv_calib_output_dir = get_single_view_calib_dirs(cfg, input_base_dir)
    camera_pairs = cfg['camera_pairs']
    ba_method = cfg['ba_method']
    enable_manual_adjustment = cfg['enable_manual_adjustment']
    manual_adjustment_data_source = cfg['manual_adjustment_data_source']

    # create output directories
    output_base_dir = f"{pairwise_calib_output_dir}/BA_output"
    os.makedirs(f"{output_base_dir}/results_ba", exist_ok=True)
    os.makedirs(f"{output_base_dir}/results_local_ba", exist_ok=True)
    os.makedirs(f"{output_base_dir}/results_ba_scaled", exist_ok=True)
    os.makedirs(f"{output_base_dir}/results_iterative_ba", exist_ok=True)

    # video files
    video_paths = []
    for i in range(len(sv_calib_output_dir)):
        video_paths.append(f'{sv_calib_output_dir[i]}/rectified.mp4')
    
    # read manual adjustment data
    manual_adjustment_data = None
    if enable_manual_adjustment and manual_adjustment_data_source == 'file':
        print('Manual adjustment data source: file')
        manual_adjustment_data = read_manual_adjustment_data(pairwise_calib_output_dir+'/manual_adjustment')
    elif manual_adjustment_data_source == 'hardcoded':
        print('Manual adjustment data source: hardcoded')
        manual_adjustment_data = read_manual_adjustment_data(pairwise_calib_output_dir+'/manual_adjustment')
    else:
        ValueError('Invalid manual adjustment data source')

    # read the 2-view calibration data from the json file
    data_list = []
    for cam_A, cam_B in camera_pairs:
        data_path = os.path.join(pairwise_calib_output_dir, 
                    f'cam{cam_A}_cam{cam_B}/calib_results.json') 
        data = read_data(data_path)
        data_list.append(data)

    # load tracklet_to_global
    tracklet_to_global = load_tracklet_mapping(f"{pairwise_calib_output_dir}/tracklet_to_global.json")
    print(tracklet_to_global)

    # get the global poses of the cameras from relative poses
    Rglob_0i, tglob_0i = get_global_pose(data_list)
    #logging.debug('Rglob:', repr(Rglob_0i))
    #logging.debug('tglob:', repr(tglob_0i))

    logging.info('-' * 80)
    logging.info('Step 2: Global BA')
    logging.info('Converting data...')
    cam_ids_sorted, n_points, initial_camera_poses_dict, initial_points_3d, \
        K_all_dict, camera_indices, point_indices, points_2d, frame_ids = \
            convert_data_for_ba(data_list, Rglob_0i, tglob_0i, tracklet_to_global,
                               cfg['skip_frame'], f"{output_base_dir}/results_ba", 
                               no_plot)
    n_cams = len(cam_ids_sorted)
    logging.info(f'n_cams: {n_cams}, n_points: {n_points}')
    if n_points == 0:
        ValueError('No points for BA')
        exit()

    logging.info('Initial K_all_dict:\n', {cam_id: np.round(K_all_dict[cam_id], 2).tolist() for cam_id in cam_ids_sorted})
    
    # if there is only one camera pair, we need to use the two view calibration data
    if len(camera_pairs) == 1:
        # save the refined results - NOW INCLUDING COMPLETE ORIGINAL DATASET
        save_results(K_all_dict, initial_camera_poses_dict, initial_camera_poses_dict, 
                     None, frame_ids, 
                     f"{output_base_dir}/results_ba", cam_ids_sorted,
                     all_original_points_3d=initial_points_3d)
        return

   
    # Convert dictionaries to lists for existing BA functions
    K_all_list = [K_all_dict[cam_id] for cam_id in cam_ids_sorted]
    initial_camera_poses_list = [initial_camera_poses_dict[cam_id] for cam_id in cam_ids_sorted]
    
    # Convert camera_indices from camera IDs to sequential indices for BA functions
    cam_id_to_seq_index = {cam_id: i for i, cam_id in enumerate(cam_ids_sorted)}
    camera_indices_seq = [cam_id_to_seq_index[cam_id] for cam_id in camera_indices]
    
    if ba_method == 'iterative':
        logging.info('BA method: iterative')
        results = run_iterative_bundle_adjustment(
            n_cams, n_points, initial_camera_poses_list, initial_points_3d, K_all_list,
            camera_indices_seq, point_indices, points_2d, frame_ids,
            max_iterations=3,
            outlier_threshold_pixels=5.0,
            output_dir_base=f"{output_base_dir}/results_iterative_ba",
            manual_adjustment_data=manual_adjustment_data,
            no_plot=no_plot
        )
    
        # Extract results from the dictionary
        refined_points_3d = results['refined_points_3d']
        refined_camera_poses_list = results['refined_camera_poses']
        original_points_3d = results['original_points_3d']
        frame_ids = results['frame_ids']
    elif ba_method == 'standard':
        logging.info('BA method: standard')
        # refined_points_3d, refined_camera_poses = main(n_cams, n_points, initial_camera_poses, initial_points_3d, K_all, 
        # camera_indices, point_indices, points_2d, f"{output_base_dir}/results_ba")
        refined_points_3d, refined_camera_poses_list = ba_procedure(n_cams, n_points, 
                                                               initial_camera_poses_list, 
                                                               initial_points_3d, 
                                                               K_all_list, 
                                                               camera_indices_seq, 
                                                               point_indices, 
                                                               points_2d, 
                                                               manual_adjustment_data,
                                                               f"{output_base_dir}/results_ba")
        original_points_3d = None
    else:
        ValueError('Invalid BA method')
        exit()
    
    # Convert refined camera poses back to dictionaries
    refined_camera_poses_dict = {}
    for i, cam_id in enumerate(cam_ids_sorted):
        refined_camera_poses_dict[cam_id] = refined_camera_poses_list[i]

    # distance between the cameras
    def get_camera_center(R, t):
        C = -R.T @ t
        return C
    
    def dist_between_cameras(pose1, pose2):
        C1 = get_camera_center(pose1[0], pose1[1])
        C2 = get_camera_center(pose2[0], pose2[1])
        return np.linalg.norm(C1 - C2)

    # distance between the cameras
    for i in range(n_cams-1):
        cam_id_i = cam_ids_sorted[i]
        cam_id_next = cam_ids_sorted[i+1]
        dist = dist_between_cameras(initial_camera_poses_dict[cam_id_i], initial_camera_poses_dict[cam_id_next])
        logging.debug(f'distance between camera {cam_id_i} and {cam_id_next} (initial): {dist}')
        dist = dist_between_cameras(refined_camera_poses_dict[cam_id_i], refined_camera_poses_dict[cam_id_next])
        logging.debug(f'distance between camera {cam_id_i} and {cam_id_next} (refined): {dist}')

   
    # evaluate results
    # calculate reprojection error
    # visualize reprojection of 3D points in the global coordinate system
    # visualize 3D points in the global coordinate system
    #evaluate_results(data_list, refined_camera_poses, tracklet_to_global, 
    #                video_paths, f"{output_base_dir}/results_ba")

    # save the refined results - NOW INCLUDING COMPLETE ORIGINAL DATASET
    save_results(K_all_dict, initial_camera_poses_dict, refined_camera_poses_dict, 
                 refined_points_3d, frame_ids, 
                 f"{output_base_dir}/results_ba", cam_ids_sorted, all_original_points_3d=original_points_3d)
    if not no_plot:
        visualize_results(n_cams, data_list, video_paths, K_all_list, refined_camera_poses_list, 
                          tracklet_to_global, refined_points_3d, frame_ids, 
                          f"{output_base_dir}/results_ba", cam_ids_sorted)

    #logging.info('-' * 80)
    #logging.info('Step 3: find scale factor and apply to the refined results')
    # find scale factor
    # refined_points_3d_local is the points in the local BA between camera 0 and 1
    # refined_points_3d is the points in the global BA
    # find the scale factor by minimizing the distance between the points in the local BA and the points in the global BA
    # apply the scale factor to the refined_points_3d

    return

def skew_symmetric(w):
    """Create a 3x3 skew-symmetric matrix from a 3D vector
    
    Args:
        w: 3D vector
        
    Returns:
        3x3 skew-symmetric matrix
    """
    return np.array([[0, -w[2], w[1]],
                     [w[2], 0, -w[0]],
                     [-w[1], w[0], 0]])

def hat(xi):
    """Convert 6D twist coordinates to 4x4 se(3) generator matrix
    
    Args:
        xi: 6D twist coordinates [rho; omega] where rho is translation, omega is rotation
        
    Returns:
        4x4 se(3) matrix
    """
    rho = xi[:3]  # translation part
    omega = xi[3:6]  # rotation part (Rodrigues vector)
    
    # Skew-symmetric matrix for rotation
    omega_hat = skew_symmetric(omega)
    
    # 4x4 se(3) generator
    xi_hat = np.zeros((4, 4))
    xi_hat[:3, :3] = omega_hat
    xi_hat[:3, 3] = rho
    
    return xi_hat

def vee(xi_hat):
    """Convert 4x4 se(3) generator matrix to 6D twist coordinates
    
    Args:
        xi_hat: 4x4 se(3) matrix
        
    Returns:
        6D twist coordinates [rho; omega]
    """
    rho = xi_hat[:3, 3]
    omega = np.array([xi_hat[2, 1], xi_hat[0, 2], xi_hat[1, 0]])
    return np.hstack([rho, omega])

def se3_exp(xi):
    """SE(3) exponential map: se(3) -> SE(3)
    
    Args:
        xi: 6D twist coordinates
        
    Returns:
        4x4 SE(3) transformation matrix
    """
    rho = xi[:3]
    omega = xi[3:6]
    theta = np.linalg.norm(omega)
    
    if theta < 1e-8:
        # Small angle approximation
        R = np.eye(3) + skew_symmetric(omega)
        V = np.eye(3)
    else:
        # Rodrigues formula for rotation
        omega_hat = skew_symmetric(omega)
        R = np.eye(3) + np.sin(theta)/theta * omega_hat + (1-np.cos(theta))/(theta**2) * omega_hat @ omega_hat
        
        # V matrix for translation
        V = (np.eye(3) + (1-np.cos(theta))/(theta**2) * omega_hat + 
             (theta-np.sin(theta))/(theta**3) * omega_hat @ omega_hat)
    
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = V @ rho
    
    return T

def se3_log(T):
    """SE(3) logarithmic map: SE(3) -> se(3)
    
    Args:
        T: 4x4 SE(3) transformation matrix
        
    Returns:
        6D twist coordinates
    """
    R = T[:3, :3]
    t = T[:3, 3]
    
    # Extract rotation angle
    theta = np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1))
    
    if theta < 1e-8:
        # Small angle approximation
        omega = np.array([R[2, 1], R[0, 2], R[1, 0]]) / 2
        V_inv = np.eye(3)
    else:
        # Extract rotation axis
        omega_hat = (theta / (2 * np.sin(theta))) * (R - R.T)
        omega = np.array([omega_hat[2, 1], omega_hat[0, 2], omega_hat[1, 0]])
        
        # V^{-1} matrix
        omega_hat_norm = skew_symmetric(omega)
        V_inv = (np.eye(3) - 0.5 * omega_hat_norm + 
                (1/(theta**2)) * (1 - theta*np.sin(theta/2)/(2*np.cos(theta/2))) * 
                omega_hat_norm @ omega_hat_norm)
    
    rho = V_inv @ t
    return np.hstack([rho, omega])

def se3_compose(T1, T2):
    """Compose two SE(3) transformations"""
    return T1 @ T2

def pose_to_se3(R, t):
    """Convert (R, t) to SE(3) matrix"""
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = t.ravel()
    return T

def se3_to_pose(T):
    """Convert SE(3) matrix to (R, t)"""
    R = T[:3, :3]
    t = T[:3, 3].reshape(3, 1)
    return R, t

def bundle_adjustment_residual_liegroup(tangent_vectors, camera_poses_se3, n_cams, n_points, 
                                       camera_indices, point_indices, points_2d, K_all,
                                       constraint_pairs=None, constraint_distances=None):
    """Residual function for Lie group bundle adjustment
    
    Args:
        tangent_vectors: Flat array of tangent space updates [camera_tangents, points_3d]
        camera_poses_se3: List of current SE(3) camera poses
        n_cams: Number of cameras
        n_points: Number of 3D points
        camera_indices: Camera indices for observations
        point_indices: Point indices for observations  
        points_2d: Observed 2D points
        K_all: Camera intrinsic matrices
        constraint_pairs: Optional distance constraint pairs
        constraint_distances: Optional constraint distances
        
    Returns:
        Residual vector
    """
    cfg = config.get_config()
    # Handle missing distance_weight parameter with default value
    dist_weight = cfg.get('distance_weight', 5.0)
    
    # Extract camera tangent vectors and 3D points
    camera_tangents = tangent_vectors[:n_cams * 6].reshape((n_cams, 6))
    points_3d = tangent_vectors[n_cams * 6:].reshape((n_points, 3))
    
    # Update camera poses using exponential map
    updated_poses = []
    for i in range(n_cams):
        xi = camera_tangents[i]
        delta_T = se3_exp(xi)
        T_updated = se3_compose(camera_poses_se3[i], delta_T)
        updated_poses.append(T_updated)
    
    residuals = []
    
    # Reprojection residuals
    for cam_idx, pt_idx, obs in zip(camera_indices, point_indices, points_2d):
        T = updated_poses[cam_idx]
        R, t = se3_to_pose(T)
        
        # Convert to Rodrigues for projection
        rvec, _ = cv2.Rodrigues(R)
        proj = project_point(points_3d[pt_idx], rvec, t.ravel(), K_all[cam_idx])
        residuals.append(proj - obs)
    
    # Distance constraints (same as original)
    if constraint_pairs is not None and constraint_distances is not None:
        weight = dist_weight
        for (i, j), constraint_dist in zip(constraint_pairs, constraint_distances):
            dist = np.linalg.norm(points_3d[i] - points_3d[j])
            dist_penalty = weight * (dist - constraint_dist)
            residuals.append(np.array([dist_penalty], dtype=np.float64))
    
    return np.concatenate(residuals)

def run_bundle_adjustment_liegroup(n_cams, n_points, initial_camera_poses, 
                                  initial_points_3d, K_all, camera_indices, 
                                  point_indices, points_2d, max_iterations=20,
                                  ftol=1e-5, verbose=True):
    """Bundle adjustment using an iterative Lie group manifold optimization.
    
    This implementation iteratively solves for updates in the tangent space of SE(3)
    and applies them to the camera poses, which is the correct way to handle
    the non-linear geometry of rotations.
    
    Args:
        n_cams: Number of cameras
        n_points: Number of 3D points
        initial_camera_poses: List of (R, t) initial camera poses
        initial_points_3d: Initial 3D points
        K_all: Camera intrinsic matrices
        camera_indices: Camera indices for observations
        point_indices: Point indices for observations
        points_2d: Observed 2D points
        max_iterations: Maximum number of outer optimization iterations
        ftol: Relative tolerance for convergence check on cost change and update norm
        verbose: Print iteration info
        
    Returns:
        refined_points_3d: Optimized 3D points
        refined_camera_poses: Optimized camera poses as (R, t) list
    """
    
    if verbose:
        print("Starting Iterative Lie Group Bundle Adjustment...")
        print(f"Cameras: {n_cams}, Points: {n_points}, Observations: {len(camera_indices)}")
    
    # Convert initial poses to SE(3) - these will be our iterative estimates
    current_camera_poses_se3 = [pose_to_se3(R, t) for R, t in initial_camera_poses]
    current_points_3d = np.copy(initial_points_3d)

    # Distance constraints (same as original)
    constraint_pairs = []
    constraint_distances = []
    for i in range(len(initial_points_3d)-1):
        constraint_pairs.append((i, i+1))
        constraint_distances.append(np.linalg.norm(initial_points_3d[i] - initial_points_3d[i+1]))

    prev_cost = np.inf
    
    for i in range(max_iterations):
        # The optimization variable for the subproblem is x = [tangent_updates, full_3d_points].
        # We solve for the tangent space update `xi` for the current poses, and the
        # full 3D point coordinates for the next iteration.
        x0_iter = np.zeros(n_cams * 6 + n_points * 3)
        x0_iter[n_cams * 6:] = current_points_3d.ravel()
        
        # Define the residual function for the current linearization point
        def residual_wrapper_iter(x):
            return bundle_adjustment_residual_liegroup(
                x, current_camera_poses_se3, n_cams, n_points,
                camera_indices, point_indices, points_2d, K_all,
                constraint_pairs, constraint_distances
            )

        # Solve the linearized subproblem to find the optimal update
        res = least_squares(
            residual_wrapper_iter,
            x0_iter,
            verbose=0,  # Verbose output is handled by the outer loop
            method='trf',
            x_scale='jac',
            ftol=1e-4,  # Standard tolerance for the subproblem
            max_nfev=200
        )
        
        cost = 0.5 * np.sum(res.fun**2)
        
        # Extract the tangent vector updates and the new 3D points
        camera_tangents_update = res.x[:n_cams * 6].reshape((n_cams, 6))
        current_points_3d = res.x[n_cams * 6:].reshape((n_points, 3))
        
        # Update camera poses by applying the tangent vector (retraction)
        for j in range(n_cams):
            delta_T = se3_exp(camera_tangents_update[j])
            current_camera_poses_se3[j] = se3_compose(current_camera_poses_se3[j], delta_T)
        
        update_norm = np.linalg.norm(camera_tangents_update)
        
        if verbose:
            print(f"Iter {i+1:02d}/{max_iterations}, Cost: {cost:.6f}, Update norm: {update_norm:.6f}")
        
        # Check for convergence
        if update_norm < ftol:
            if verbose:
                print("Convergence: Tangent update norm is small.")
            break
        
        if i > 0 and abs(prev_cost - cost) < ftol * prev_cost:
            if verbose:
                print("Convergence: Cost reduction is minimal.")
            break
        prev_cost = cost

    # Convert final SE(3) poses back to (R, t) for output
    refined_camera_poses = [se3_to_pose(T) for T in current_camera_poses_se3]

    if verbose:
        print(f"Optimization completed after {i+1} iterations.")
        print(f"Final cost: {cost:.6f}")

    return current_points_3d, refined_camera_poses

def compare_bundle_adjustment_methods(n_cams, n_points, initial_camera_poses, 
                                     initial_points_3d, K_all, camera_indices, 
                                     point_indices, points_2d):
    """Compare Euclidean vs Lie Group bundle adjustment methods
    
    Args:
        Same as run_bundle_adjustment
        
    Returns:
        dict: Results from both methods for comparison
    """
    print("="*60)
    print("BUNDLE ADJUSTMENT METHOD COMPARISON")
    print("="*60)
    
    import time
    
    # Method 1: Euclidean optimization
    print("\n1. EUCLIDEAN OPTIMIZATION:")
    print("-"*30)
    start_time = time.time()
    points_euclidean, poses_euclidean = run_bundle_adjustment(
        n_cams, n_points, initial_camera_poses, initial_points_3d, 
        K_all, camera_indices, point_indices, points_2d, 
        optimization_method='euclidean'
    )
    euclidean_time = time.time() - start_time
    
    # Method 2: Lie Group optimization  
    print("\n2. LIE GROUP MANIFOLD OPTIMIZATION:")
    print("-"*35)
    start_time = time.time()
    points_liegroup, poses_liegroup = run_bundle_adjustment(
        n_cams, n_points, initial_camera_poses, initial_points_3d, 
        K_all, camera_indices, point_indices, points_2d, 
        optimization_method='liegroup'
    )
    liegroup_time = time.time() - start_time
    
    # Compute final costs for comparison
    print("\n3. COMPARISON:")
    print("-"*15)
    
    # Euclidean final cost
    euclidean_params = []
    for R, t in poses_euclidean:
        rvec, _ = cv2.Rodrigues(R)
        euclidean_params.extend([rvec.ravel(), t.ravel()])
    euclidean_params = np.hstack(euclidean_params)
    
    euclidean_residuals = bundle_adjustment_residual(
        np.hstack([euclidean_params, points_euclidean.ravel()]),
        n_cams, n_points, camera_indices, point_indices, points_2d, K_all
    )
    euclidean_cost = 0.5 * np.sum(euclidean_residuals**2)
    
    # Lie group final cost (need to convert poses to SE(3) format)
    camera_poses_se3 = [pose_to_se3(R, t) for R, t in poses_liegroup]
    liegroup_residuals = bundle_adjustment_residual_liegroup(
        np.hstack([np.zeros(n_cams * 6), points_liegroup.ravel()]),
        camera_poses_se3, n_cams, n_points, camera_indices, point_indices, points_2d, K_all
    )
    liegroup_cost = 0.5 * np.sum(liegroup_residuals**2)
    
    print(f"Euclidean method:")
    print(f"  - Final cost: {euclidean_cost:.6f}")
    print(f"  - Time: {euclidean_time:.3f} seconds")
    
    print(f"Lie Group method:")
    print(f"  - Final cost: {liegroup_cost:.6f}")
    print(f"  - Time: {liegroup_time:.3f} seconds")
    
    # Check if poses are different
    pose_diff = 0
    for (R1, t1), (R2, t2) in zip(poses_euclidean, poses_liegroup):
        pose_diff += np.linalg.norm(R1 - R2) + np.linalg.norm(t1 - t2)
    
    print(f"Total pose difference: {pose_diff:.6f}")
    print(f"Point difference: {np.linalg.norm(points_euclidean - points_liegroup):.6f}")
    
    if liegroup_cost < euclidean_cost:
        print("✓ Lie Group method achieved lower cost")
    elif euclidean_cost < liegroup_cost:
        print("✓ Euclidean method achieved lower cost")
    else:
        print("≈ Both methods achieved similar cost")
    
    print("="*60)
    
    return {
        'euclidean': {
            'points': points_euclidean,
            'poses': poses_euclidean,
            'cost': euclidean_cost,
            'time': euclidean_time
        },
        'liegroup': {
            'points': points_liegroup,
            'poses': poses_liegroup,
            'cost': liegroup_cost,
            'time': liegroup_time
        }
    }


if __name__ == "__main__":
    #main_sdg_v2()

    args = parse_args()
    # read config file
    config.load_config(args.config)
    cfg = config.get_config()

    # Call this function before your logging statements
    setup_logging(cfg['logging_level'])
    main(cfg, args.no_plot, args.input_base_dir, args.output_base_dir)
