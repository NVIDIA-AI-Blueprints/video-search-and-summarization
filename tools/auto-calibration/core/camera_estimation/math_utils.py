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
import logging

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


def robust_umeyama_alignment(X_true, X_recon, max_iterations=1000, inlier_threshold=0.5, 
                            min_inliers=0.6, min_samples=3, random_seed=None):
    """
    Robust Umeyama algorithm using RANSAC to handle outliers.
    
    Args:
        X_true(np.ndarray): True points (N x 3)
        X_recon(np.ndarray): Reconstructed points (N x 3)
        max_iterations(int): Maximum RANSAC iterations
        inlier_threshold(float): Distance threshold for inlier classification
        min_inliers(float): Minimum ratio of inliers required (0.0 - 1.0)
        min_samples(int): Minimum number of samples for alignment (3 for 3D similarity)
        random_seed(int): Random seed for reproducibility
        
    Returns:
        scale(float): Scale factor
        R(np.ndarray): Rotation matrix  
        t(np.ndarray): Translation vector
        inliers(np.ndarray): Boolean array indicating inlier points
        n_inliers(int): Number of inlier points
    """
    import random
    
    X_true = np.asarray(X_true)
    X_recon = np.asarray(X_recon)
    
    if len(X_true) != len(X_recon):
        raise ValueError("X_true and X_recon must have the same number of points")
    
    n_points = len(X_true)
    
    if n_points < min_samples:
        logging.warning(f"Not enough points ({n_points}) for robust alignment, using standard Umeyama")
        scale, R, t = umeyama_alignment(X_true, X_recon)
        return scale, R, t, np.ones(n_points, dtype=bool), n_points
    
    if random_seed is not None:
        random.seed(random_seed)
        np.random.seed(random_seed)
    
    best_scale = None
    best_R = None  
    best_t = None
    best_inliers = None
    best_n_inliers = 0
    
    min_inliers_count = max(min_samples, int(min_inliers * n_points))
    
    logging.info(f"Starting robust Umeyama alignment with {n_points} points")
    logging.info(f"RANSAC parameters: max_iter={max_iterations}, inlier_thresh={inlier_threshold}, min_inliers={min_inliers_count}")
    
    for iteration in range(max_iterations):
        # Randomly sample minimal set of points
        sample_indices = random.sample(range(n_points), min_samples)
        
        try:
            # Compute alignment on sample
            sample_scale, sample_R, sample_t = umeyama_alignment(
                X_true[sample_indices], X_recon[sample_indices]
            )
            
            if sample_scale is None:
                continue
                
            # Apply transformation to all points
            X_transformed = (sample_scale * sample_R @ X_recon.T).T + sample_t
            
            # Compute distances and identify inliers
            distances = np.linalg.norm(X_transformed - X_true, axis=1)
            inliers = distances < inlier_threshold
            n_inliers = np.sum(inliers)
            
            # Update best solution if we have more inliers
            if n_inliers > best_n_inliers and n_inliers >= min_inliers_count:
                best_scale = sample_scale
                best_R = sample_R
                best_t = sample_t
                best_inliers = inliers
                best_n_inliers = n_inliers
                
                logging.debug(f"Iteration {iteration}: Found solution with {n_inliers} inliers")
                
                # Early termination if we have enough inliers
                if n_inliers >= int(0.9 * n_points):
                    logging.info(f"Early termination: Found solution with {n_inliers} inliers ({n_inliers/n_points:.2%})")
                    break
                    
        except Exception as e:
            logging.debug(f"Iteration {iteration}: Failed to compute alignment: {e}")
            continue
    
    if best_scale is None:
        logging.warning("Robust alignment failed, falling back to standard Umeyama")
        scale, R, t = umeyama_alignment(X_true, X_recon)
        return scale, R, t, np.ones(n_points, dtype=bool), n_points
    
    logging.info(f"Robust alignment completed: {best_n_inliers} inliers ({best_n_inliers/n_points:.2%})")
    return best_scale, best_R, best_t, best_inliers, best_n_inliers


def apply_umeyama_transform(pts3d, scale, R, t):
    """
    Apply Umeyama transformation to 3D points.
    
    Args:
        pts3d(np.ndarray): 3D points (N x 3)
        scale(float): Scale factor
        R(np.ndarray): Rotation matrix (3 x 3)
        t(np.ndarray): Translation vector (3,)
        
    Returns:
        np.ndarray: Transformed points (N x 3)
    """
    pts3d_aligned = (scale * R @ pts3d.T).T + t
    return pts3d_aligned 