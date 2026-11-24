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
import logging

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


def umeyama_alignment(X_true, X_recon):
    """
    Umeyama algorithm for alignment of two sets of points from X_recon to X_true.
    
    This function computes the optimal similarity transformation (scale, rotation, translation)
    that aligns the reconstructed points X_recon to the true points X_true.
    
    The transformation follows: X_true = scale * R @ X_recon + t
    
    Args:
        X_true(np.ndarray): True points (N x 3)
        X_recon(np.ndarray): Reconstructed points (N x 3)
        
    Returns:
        scale(float): Scale factor
        R(np.ndarray): Rotation matrix (3 x 3)
        t(np.ndarray): Translation vector (3,)
    """
    X_true = np.asarray(X_true)
    X_recon = np.asarray(X_recon)
    
    mu_true = X_true.mean(axis=0)
    mu_recon = X_recon.mean(axis=0)

    X_true_centered = X_true - mu_true
    X_recon_centered = X_recon - mu_recon

    cov_matrix = X_recon_centered.T @ X_true_centered / len(X_true)
    try: 
        U, S, Vt = np.linalg.svd(cov_matrix)
    except np.linalg.LinAlgError:
        logging.error("[ERROR] Singular value decomposition failed in Umeyama algorithm")
        return None, None, None
    
    R = Vt.T @ U.T

    if np.linalg.det(R) < 0:
        Vt[-1, :] *= -1
        R = Vt.T @ U.T

    var_recon = np.sum(X_recon_centered**2) / len(X_recon)
    scale = np.sum(S) / var_recon
    t = mu_true - scale * R @ mu_recon

    return scale, R, t
