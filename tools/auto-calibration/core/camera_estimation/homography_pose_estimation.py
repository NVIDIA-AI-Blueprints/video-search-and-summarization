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
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from scipy.optimize import least_squares
import logging

import config
from math_utils import umeyama_alignment

def estimate_pose_from_homography(K1, K2, points1, points2):
    cfg = config.get_config()
    ransac_threshold = cfg['ransac_threshold']
    points1 = np.array(points1, dtype=np.float64)
    points2 = np.array(points2, dtype=np.float64)
    H, mask = cv2.findHomography(points1, points2, method=cv2.RANSAC, 
                                 ransacReprojThreshold=ransac_threshold) 
    #logging.info(f"Estimated Homography:\n {H}")
    H_norm = np.linalg.inv(K2) @ H @ K1
    num_solutions, Rs, ts, normals = cv2.decomposeHomographyMat(H_norm, np.eye(3))
    #logging.info(f"Found {num_solutions} solutions.")
    solutions = []
    for i in range(num_solutions):
        R = Rs[i]
        t = ts[i].reshape(3)
        n = normals[i].reshape(3)
        solutions.append((R, t, n))
        #logging.debug(f"Solution {i+1}:\nRotation:\n{R}\nTranslation:\n{t}\nPlane normal:\n{n}\n")
    return solutions, H

def estimate_pose_from_homography_normalized(K1, K2, points1, points2, 
                                             verbose:bool=False):
    cfg = config.get_config()
    ransac_threshold = cfg['ransac_threshold']
    points1 = np.array(points1, dtype=np.float64)
    points2 = np.array(points2, dtype=np.float64)

    pts1_norm = cv2.undistortPoints(np.expand_dims(points1, axis=1), K1, None)
    pts2_norm = cv2.undistortPoints(np.expand_dims(points2, axis=1), K2, None)

    H, mask = cv2.findHomography(pts1_norm, pts2_norm, method=cv2.RANSAC, 
                                 ransacReprojThreshold=0.001) 
    if verbose:
        logging.info(f"Estimated Homography:\n {H}")
    #H_norm = np.linalg.inv(K2) @ H @ K1
    num_solutions, Rs, ts, normals = cv2.decomposeHomographyMat(H, np.eye(3))
    if verbose:
        logging.info(f"Found {num_solutions} solutions.")
    solutions = []
    for i in range(num_solutions):
        R = Rs[i]
        t = ts[i].reshape(3)
        n = normals[i].reshape(3)
        solutions.append((R, t, n))
        #logging.debug(f"Solution {i+1}:\nRotation:\n{R}\nTranslation:\n{t}\nPlane normal:\n{n}\n")
    return solutions, H


def select_best_solution(solutions, K1, K2, points1, points2, pt3_sv3dt=None):
    """
    Select the best solution from the given solutions.

    Args:
        solutions(list): list of solutions
        K1(np.ndarray): intrinsic matrix of camera 1
        K2(np.ndarray): intrinsic matrix of camera 2
        points1(np.ndarray): points in camera 1
        points2(np.ndarray): points in camera 2
        pt3_sv3dt(np.ndarray): 3D points in the camera 0 coordinate

    Returns:
        best_solution(tuple): best solution
        best_pts3d(np.ndarray): 3D points in the world coordinate
    """
    points1 = np.array(points1, dtype=np.float64)
    points2 = np.array(points2, dtype=np.float64)
    max_positive_depth = -1
    best_solution = None
    best_pts3d = None
    mean_dist = np.inf
    for i, (R, t, n) in enumerate(solutions):
        #print(f"Solution {i+1}:\nRotation:\n{R}\nTranslation:\n{t}\n")
        P1 = K1 @ np.hstack((np.eye(3), np.zeros((3, 1))))
        P2 = K2 @ np.hstack((R, t.reshape(3, 1)))
        pts4d_hom = cv2.triangulatePoints(P1, P2, points1.T, points2.T)
        pts3d = (pts4d_hom / pts4d_hom[3])[:3].T
        pts_cam1 = pts3d[:, 2]
        pts_cam2 = (R @ pts3d.T + t.reshape(3, 1))[2]
        positive_depth = np.sum((pts_cam1 > 0) & (pts_cam2 > 0))
        #print(f"Positive depth: {positive_depth}")
         
        if positive_depth == max_positive_depth:
            # positive depth is the same as the best solution
            # then compare the mean distance between the reconstructed 3D points and SV3DT points
            if pt3_sv3dt is not None:
                scale, R_align, t_align = umeyama_alignment(pt3_sv3dt, pts3d)
                if scale is None:
                    continue
                X_recon_refined = (scale * R_align @ pts3d.T).T + t_align 
                refined_distances = np.linalg.norm(X_recon_refined - pt3_sv3dt, axis=1)
                mean_refined_distances = np.mean(refined_distances)
                if mean_refined_distances < mean_dist:
                    # update the best solution
                    mean_dist = mean_refined_distances
                    best_solution = (R, t)
                    best_pts3d = pts3d
        else:
            if positive_depth > max_positive_depth:
                # update the best solution
                max_positive_depth = positive_depth
                best_solution = (R, t)
                best_pts3d = pts3d
                if pt3_sv3dt is not None:
                    # calculate the mean distance between the reconstructed 3D points and SV3DT points
                    scale, R_align, t_align = umeyama_alignment(pt3_sv3dt, pts3d)
                    if scale is None:
                        continue
                    X_recon_refined = (scale * R_align @ pts3d.T).T + t_align 
                    refined_distances = np.linalg.norm(X_recon_refined - pt3_sv3dt, axis=1)
                    mean_refined_distances = np.mean(refined_distances)
                    mean_dist = mean_refined_distances
 
    
    #logging.info(f"Best solution has {max_positive_depth} points of {len(points1)} points in front of both cameras.")
    if max_positive_depth != len(points1):
        logging.warning(f"Camera pose estimation failed. Not all points are in front of both cameras.")
        #return None, None
    return best_solution, best_pts3d


def get_projection_matrices(K1, K2, R, t):
    P1 = K1 @ np.hstack((np.eye(3), np.zeros((3, 1))))
    P2 = K2 @ np.hstack((R, t.reshape(3, 1)))
    return P1, P2


def compute_reprojection_error(P1, P2, points1, points2, pts3d):
    num_points = pts3d.shape[0]
    pts3d_hom = np.hstack((pts3d, np.ones((num_points, 1)))).T
    proj1 = (P1 @ pts3d_hom).T
    proj1 = proj1[:, :2] / proj1[:, 2:]
    proj2 = (P2 @ pts3d_hom).T
    proj2 = proj2[:, :2] / proj2[:, 2:]
    error1 = np.linalg.norm(proj1 - points1, axis=1)
    error2 = np.linalg.norm(proj2 - points2, axis=1)
    #mean_error1 = np.mean(error1)
    #mean_error2 = np.mean(error2)
    #logging.info(f"Reprojection error in camera 1: {mean_error1:.4f} pixels")
    #logging.info(f"Reprojection error in camera 2: {mean_error2:.4f} pixels")
    return error1, error2


def robust_filter(points1, points2, error1, error2, threshold=5.0):
    mask = (error1 < threshold) & (error2 < threshold)
    return points1[mask], points2[mask], mask


def full_bundle_adjustment(K1, K2, points1, points2, R_init, t_init, pts3d_init):
    def residuals(params, K1, K2, points1, points2):
        rot_vec = params[:3]
        t = params[3:6]
        pts3d = params[6:].reshape(-1, 3)
        R, _ = cv2.Rodrigues(rot_vec)
        P1 = K1 @ np.hstack((np.eye(3), np.zeros((3, 1))))
        P2 = K2 @ np.hstack((R, t.reshape(3, 1)))
        num_points = pts3d.shape[0]
        pts3d_hom = np.hstack((pts3d, np.ones((num_points, 1)))).T
        proj1 = (P1 @ pts3d_hom).T
        proj1 = proj1[:, :2] / proj1[:, 2:]
        proj2 = (P2 @ pts3d_hom).T
        proj2 = proj2[:, :2] / proj2[:, 2:]
        res1 = (proj1 - points1).ravel()
        res2 = (proj2 - points2).ravel()
        return np.hstack([res1, res2])
    rot_init, _ = cv2.Rodrigues(R_init)
    param_init = np.hstack([rot_init.ravel(), t_init.ravel(), pts3d_init.ravel()])
    result = least_squares(residuals, param_init, args=(K1, K2, points1, points2))
    rot_vec_opt = result.x[:3]
    t_opt = result.x[3:6]
    pts3d_opt = result.x[6:].reshape(-1, 3)
    R_opt, _ = cv2.Rodrigues(rot_vec_opt)
    return R_opt, t_opt, pts3d_opt


def visualize_points(pts3d):
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')
    ax.scatter(pts3d[:, 0], pts3d[:, 1], pts3d[:, 2], c='b', marker='o')
    ax.set_title("Reconstructed 3D Points")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    plt.show()


def plot_reprojection_errors(errors1, errors2):
    plt.figure(figsize=(10,5))
    plt.hist(errors1, bins=20, alpha=0.5, label="Camera 1")
    plt.hist(errors2, bins=20, alpha=0.5, label="Camera 2")
    plt.legend()
    plt.title("Reprojection Error Distribution")
    plt.xlabel("Error (pixels)")
    plt.ylabel("Frequency")
    plt.show()


if __name__ == "__main__":
    K1 = np.array([[1000, 0, 640], [0, 1000, 360], [0, 0, 1]])
    K2 = np.array([[950, 0, 620], [0, 950, 340], [0, 0, 1]])
    points1 = [(650, 370), (660, 360), (640, 380), (630, 365), (655, 375), (645, 355), (625, 370), (635, 360)]
    points2 = [(648, 368), (658, 358), (638, 378), (628, 363), (653, 373), (643, 353), (623, 368), (633, 358)]
    solutions, H = estimate_pose_from_homography(K1, K2, points1, points2)
    (R_best, t_best), pts3d_best = select_best_solution(solutions, K1, K2, points1, points2)
    P1, P2 = get_projection_matrices(K1, K2, R_best, t_best)
    print("\nBest Rotation:\n", R_best)
    print("Best Translation:\n", t_best)
    print("\nProjection Matrix P1:\n", P1)
    print("Projection Matrix P2:\n", P2)
    err1, err2 = compute_reprojection_error(P1, P2, np.array(points1), np.array(points2), pts3d_best)
    plot_reprojection_errors(err1, err2)
    p1_filtered, p2_filtered, mask = robust_filter(np.array(points1), np.array(points2), err1, err2)
    pts3d_filtered = pts3d_best[mask]
    R_opt, t_opt, pts3d_opt = full_bundle_adjustment(K1, K2, p1_filtered, p2_filtered, R_best, t_best, pts3d_filtered)
    P1_opt, P2_opt = get_projection_matrices(K1, K2, R_opt, t_opt)
    err1_opt, err2_opt = compute_reprojection_error(P1_opt, P2_opt, p1_filtered, p2_filtered, pts3d_opt)
    plot_reprojection_errors(err1_opt, err2_opt)
    visualize_points(pts3d_opt)
