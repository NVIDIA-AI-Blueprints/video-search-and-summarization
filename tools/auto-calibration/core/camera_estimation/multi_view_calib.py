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
from core.utils.video_capture import open_video_capture, open_video_writer
import json
import numpy as np
import sys
from tqdm import tqdm
import matplotlib.pyplot as plt
import logging
import time
from dtaidistance import dtw_ndim
import argparse
# Add the current directory to Python path to allow importing local modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(project_root)

from core.utils.path_validation import validate_output_directory, validate_input_directory

import config
from config import extract_cam_number
from camera_data import CameraData
from core.evaluation.evaluate import project_3d_point_to_image_3x4_proj_mat
from homography_pose_estimation import estimate_pose_from_homography, estimate_pose_from_homography_normalized, select_best_solution, get_projection_matrices, compute_reprojection_error
from tracklet_matching import filter_tracklet, tracklet_matching, test_candidate_matching, find_initial_matching, get_global_tracklet_id
from math_utils import umeyama_alignment, robust_umeyama_alignment

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
    file_handler = logging.FileHandler(os.path.join(logs_dir, f'multi_view_calib_{time.strftime("%Y%m%d_%H%M%S")}.log'))
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
    parser = argparse.ArgumentParser(description='Multi-cam calibration')
    parser.add_argument('-c', '--config', type=str,
                        help='Path to the config file')
    parser.add_argument('-i', '--input_base_dir', type=str, required=True,
                        help='Base directory for single view calibration outputs')
    parser.add_argument('-o', '--output_base_dir', type=str, required=True,
                        help='Base directory for output')
    parser.add_argument('-d', '--debug', action=argparse.BooleanOptionalAction, 
                        default=False, 
                        help='Debug mode')
    return parser.parse_args()

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

def transform(pt3, R, t):
    # transform the point by R and t
    pt3_transformed = np.dot(R, pt3) + t
    return pt3_transformed

class MultiViewCalib:
    def __init__(self, config_file):
        self.camera_data = []

    def add_camera_data(self, camera_data:CameraData):
        """Add camera data to the multi-view calibration.

        Args:
            camera_data (CameraData): Camera data object containing intrinsic parameters,
                tracking data and other camera information.
        """
        self.camera_data.append(camera_data)
    
    def find_matching_points(self, cam_id1, cam_id2, track_id1, track_id2, 
                             matching_points_2d, matching_points_3d_frame):
        """ Find matching points between two cameras' tracklets

        Args:
            cam_id1(int): the id of the first camera
            cam_id2(int): the id of the second camera
            track_id1(int): the id of the first tracklet
            track_id2(int): the id of the second tracklet
            matching_points_2d(list): the matching points per frame. 
            matching_points_3d_frame(list): the matching points per frame. 

        Returns:
            matching_points_3d(list): the matching points in 3D space.
            match_per_frame(list): the matching points per frame.
        """
        # find matching points between two cameras' tracklets
        # return a list of matching points
        data1 = self.camera_data[cam_id1].tracking_data
        data2 = self.camera_data[cam_id2].tracking_data
        n_frame1 = len(data1)
        n_frame2 = len(data2)
        matching_points_3d = []
        match_per_frame = []
        for fid in range(min(n_frame1, n_frame2)):
            if track_id1 in data1[fid].keys() and track_id2 in data2[fid].keys():
                matching_points_3d.append((data1[fid][track_id1]['foot_world'], 
                                        data2[fid][track_id2]['foot_world']))
                matching_points_2d[fid].append((data1[fid][track_id1]['foot_image'], 
                                        data2[fid][track_id2]['foot_image']))
                matching_points_3d_frame[fid].append((data1[fid][track_id1]['foot_world'], 
                                        data2[fid][track_id2]['foot_world']))
                match_per_frame.append({"fid": fid, "id_A": track_id1, "id_B": track_id2, 
                                        "world_position_A": data1[fid][track_id1]['foot_world'], 
                                        "world_position_B": data2[fid][track_id2]['foot_world'], 
                                        "image_position_A": data1[fid][track_id1]['foot_image'], 
                                        "image_position_B": data2[fid][track_id2]['foot_image']})
        return matching_points_3d, match_per_frame

    def get_score_matrix(self, cam_id1, cam_id2, R_12, t_12):
        """ Get the score matrix between the tracklets of two cameras. 
        The score is the average distance of points that transformed from cam 1 
        to cam 2.

        Args:
            cam_id1 (int): The ID of the first camera.
            cam_id2 (int): The ID of the second camera.
            R_12 (np.ndarray): The rotation matrix from cam 1 to cam 2.
            t_12 (np.ndarray): The translation vector from cam 1 to cam 2.

        Returns:
            score_matrix (np.ndarray): The score matrix between the tracklets of 
            the two cameras.
        """
        tracking_data1 = self.camera_data[cam_id1].tracking_data
        tracking_data2 = self.camera_data[cam_id2].tracking_data
        n_frame1 = len(tracking_data1)
        n_frame2 = len(tracking_data2)
        track_ids1 = self.camera_data[cam_id1].track_ids
        track_ids2 = self.camera_data[cam_id2].track_ids
        # score dictionary for each track id pair
        # key: (track_id1, track_id2)
        score_dict = {}
        count_dict = {}
        for tid1 in track_ids1:
            for tid2 in track_ids2:
                score_dict[(tid1, tid2)] = 0
                count_dict[(tid1, tid2)] = 0

        for fid in range(min(n_frame1, n_frame2)):
            for tid1 in tracking_data1[fid].keys():
                for tid2 in tracking_data2[fid].keys():
                    if tid1 in track_ids1 and tid2 in track_ids2:
                        # transform the point in cam 1 to cam 2 by R and t
                        pt3_cam1 = tracking_data1[fid][tid1]['foot_world']
                        pt3_cam1 = np.array([pt3_cam1[0], pt3_cam1[1], 0])
                        pt3_cam2 = tracking_data2[fid][tid2]['foot_world']
                        pt3_cam2 = np.array([pt3_cam2[0], pt3_cam2[1], 0])
                        pt3_cam1_transformed = transform(pt3_cam1, R_12, t_12)
                        dist = np.linalg.norm(pt3_cam1_transformed - pt3_cam2)
                        score_dict[(tid1, tid2)] += dist
                        count_dict[(tid1, tid2)] += 1
        # average distance 
        for tid1 in track_ids1:
            for tid2 in track_ids2:
                if count_dict[(tid1, tid2)] > 0:
                    score_dict[(tid1, tid2)] = score_dict[(tid1, tid2)] / count_dict[(tid1, tid2)]
                else:
                    score_dict[(tid1, tid2)] = 1000000
        return score_dict

    def estimate_transform(self, matching_points:list, use_head_points:bool=False):
        """ Estimate rotation R and translation t such that Q ≈ R @ P + t
        P and Q are Nx3 numpy arrays of corresponding 3D points
        matching_points: list of tuples, each tuple contains two points
        each point is a tuple of (x, y)
        estimate transform from cam 1 to cam 2

        Args:
            matching_points(list): the matching points.
            use_head_points(bool): whether to use head points.

        Returns:
            R(np.ndarray): the rotation matrix.
            t(np.ndarray): the translation vector.
        """
        # Convert matching points to numpy arrays
        height = 1.75 # unit: meter
        pts1 = np.array([[p[0][0], p[0][1], 0] for p in matching_points])
        if use_head_points:
            pts1 = np.concatenate((pts1, np.array([[p[0][0], p[0][1], height] for p in matching_points])))
        pts2 = np.array([[p[1][0], p[1][1], 0] for p in matching_points])
        if use_head_points:
            pts2 = np.concatenate((pts2, np.array([[p[1][0], p[1][1], height] for p in matching_points])))

        # Ensure we have enough points
        if len(pts1) < 4:
            return None, None

        # Calculate centroid and normalize points
        centroid1 = np.mean(pts1, axis=0)
        centroid2 = np.mean(pts2, axis=0)
        
        # Center the points
        pts1_centered = pts1 - centroid1
        pts2_centered = pts2 - centroid2

        # Calculate H matrix using SVD
        H = np.dot(pts1_centered.T, pts2_centered)
        U, S, Vt = np.linalg.svd(H)
        
        # Calculate rotation matrix
        R = Vt.T @ U.T

        # Handle reflection case (if determinant is -1)
        if np.linalg.det(R) < 0:
            Vt[2, :] *= -1
            R = Vt.T @ U.T

        # Calculate translation vector
        t = centroid2 - np.dot(R, centroid1)

        return R, t

    def evaluate_transform(self, cam_id1, cam_id2, R, t):
        """ Evaluate transform
        score is the number of points that transformed from cam 1 to cam 2 and 
        have close points in cam 2 
        use R, t to transform the points in the camera_data[cam_id1]
        count the number of points that have been transformed within 0.1m of 
        the points in the camera_data[cam_id2]
 
        Args:
            cam_id1(int): the id of the first camera
            cam_id2(int): the id of the second camera
            R(np.ndarray): the rotation matrix
            t(np.ndarray): the translation vector

        Returns:
            count(int): the number of points that transformed from cam 1 to 
            cam 2 and have close points in cam 2 
       """
        data1 = self.camera_data[cam_id1].tracking_data 
        data2 = self.camera_data[cam_id2].tracking_data
        n_frame1 = len(data1)
        n_frame2 = len(data2)

        count = 0
        dist_th = 0.1 # unit: meter #TODO: to be a relative threshold
        for fid in range(min(n_frame1, n_frame2)):
            for tid1 in data1[fid].keys(): # track ids in cam 1
                pt3_cam1 = data1[fid][tid1]['foot_world']
                pt3_cam1 = np.array([pt3_cam1[0], pt3_cam1[1], 0])
                # transform the point in cam 1 to cam 2
                pt3_cam2 = np.dot(R, pt3_cam1) + t
                # find the point closed to the transformed point in cam 2
                for tid2 in data2[fid].keys():
                    pt3 = data2[fid][tid2]['foot_world']
                    pt3 = np.array([pt3[0], pt3[1], 0])
                    dist = np.linalg.norm(pt3_cam2 - pt3)
                    if dist < dist_th:
                        count += 1
                        break
        return count

    def match_tracklets(self, score_dict:dict):
        # use Hungarian algorithm to match the tracklets
        # return a list of matched tracklets
        # each matched tracklet is a tuple of (track_id1, track_id2)
        # the tracklets are matched by the track ids
        # scores: a dictionary of a tuple of (track_id1, track_id2) and a score
        # Convert scores list to cost matrix for Hungarian algorithm
        track_ids1 = list(set(pair[0] for pair in score_dict.keys()))
        track_ids2 = list(set(pair[1] for pair in score_dict.keys()))
        
        n1, n2 = len(track_ids1), len(track_ids2)
        cost_matrix = np.zeros((n1, n2))
        max_score = max(score_dict.values()) if score_dict else float('inf')
        
        # Fill cost matrix - convert scores to costs (higher score = lower cost)
        for i, id1 in enumerate(track_ids1):
            for j, id2 in enumerate(track_ids2):
                if (id1, id2) in score_dict:
                    # Normalize and invert scores to get costs
                    cost_matrix[i,j] = max_score - score_dict[(id1, id2)]
                else:
                    cost_matrix[i,j] = max_score # Maximum cost for unmatched pairs
                    
        # Apply Hungarian algorithm
        from scipy.optimize import linear_sum_assignment
        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        
        # Convert results back to track ID pairs
        matches = []
        for i, j in zip(row_ind, col_ind):
            # Only include matches that had an original score
            if (track_ids1[i], track_ids2[j]) in score_dict: 
                matches.append((track_ids1[i], track_ids2[j]))
        return matches

    def calibrate_2_cams_using_3D_points(self, cam_id1:int, cam_id2:int):
        """Calibrate two cameras using 3D matching points.

        This method finds corresponding tracklets between two cameras and uses 
        them to estimate the rotation matrix and translation vector that 
        transform points from camera 1's coordinate system to camera 2's 
        coordinate system.

        Note: the 3D points are in the world coordinates defined by SV3DT.
        Note: this is an experimental function. Not using this function for now.

        Args:
            cam_id1 (int): ID of the first camera
            cam_id2 (int): ID of the second camera

        Returns:
            tuple: A tuple containing:
                - best_R (ndarray): 3x3 rotation matrix from cam 1 to cam 2
                - best_t (ndarray): 3x1 translation vector from cam 1 to cam 2
                - best_id1 (int): Track ID from camera 1 that gave initial match
                - best_id2 (int): Track ID from camera 2 that gave initial match
                - matches (list): List of matched track ID pairs between cameras
                - matching_points_3d (list): List of corresponding 3D points 
                                             used for estimation
                - matching_points_2d (list): List of corresponding 2D points per 
                                             frame
        """
        # estimate camera matrix
        cam_data1 = self.camera_data[cam_id1]
        cam_data2 = self.camera_data[cam_id2]
        track_ids1 = cam_data1.track_ids
        track_ids2 = cam_data2.track_ids
        track_ids1_filtered = filter_tracklet(track_ids1, cam_data1.tracking_data)
        track_ids2_filtered = filter_tracklet(track_ids2, cam_data2.tracking_data)
        track_ids1 = track_ids1_filtered
        track_ids2 = track_ids2_filtered
        logging.info(f"track_ids1_filtered: {track_ids1_filtered}")
        logging.info(f"track_ids2_filtered: {track_ids2_filtered}")
        
        best_score = 0
        best_R = None
        best_t = None
        best_id1 = None
        best_id2 = None
        scores = {}
        # find the best matching tracklets
        data1 = self.camera_data[cam_id1].tracking_data
        n_frame1 = len(data1)
        matching_points_2d = [[] for _ in range(n_frame1)]
        matching_points_3d_frame = [[] for _ in range(n_frame1)]
        for track_id1 in track_ids1:
            for track_id2 in track_ids2:
                matching_points_3d, _ = self.find_matching_points(cam_id1, cam_id2, track_id1, track_id2, matching_points_2d, matching_points_3d_frame)
                if len(matching_points_3d) < 100: #TODO: to config parameter?
                    #print(f"Not enough matching points between camera {cam_id1}'s tracklet {track_id1} and camera {cam_id2}'s tracklet {track_id2}")
                    continue
                R, t = self.estimate_transform(matching_points_3d, use_head_points=True)
                #print(f"R: {R}, t: {t}")
                score_count = self.evaluate_transform(cam_id1, cam_id2, R, t)
                #print(f"score: {score}")
                if score_count < 50: #TODO: to be a relative threshold
                    continue
                if score_count > best_score:
                    best_score = score_count
                    best_R = R
                    best_t = t
                    best_id1 = track_id1
                    best_id2 = track_id2
                scores[(track_id1, track_id2)] = score_count
        # sort the scores by value
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        logging.info(f"sorted_scores: {sorted_scores}")
        if len(sorted_scores) < 5:
            print(f"Not enough matching points between camera {cam_id1}'s tracklets and camera {cam_id2}'s tracklets")
            return None, None, None, None, [], [], [], []
        # use only top 5 matches #TODO: to be a config parameter
        scores = dict(sorted_scores[:10]) #TODO: to be a config parameter
        matches = self.match_tracklets(scores)
        # accumulate matching points from the matched tracklets
        matching_points_3d = []
        matching_points_2d = [[] for _ in range(n_frame1)]
        matching_points_3d_frame = [[] for _ in range(n_frame1)]
        matching_data = [{"time": i, "id_map_A2B": {}, "id_map_B2A": {}, 
                          "world_sv3dt_A": {}, "world_sv3dt_B": {},
                          "world_cam_A": {},
                          "image_position_A": {}, "image_position_B": {}} 
                          for i in range(n_frame1)] # JSON format for saving
        for match in matches: # tracklet ids (track_idA, track_idB)
            pts_3d, match_per_frame = self.find_matching_points(cam_id1, cam_id2, match[0], match[1], matching_points_2d, matching_points_3d_frame)
            matching_points_3d.extend(pts_3d)
            for m in match_per_frame:
                # filter out the points that are too low
                #bottom_A = m['image_position_A'][1]
                #bottom_B = m['image_position_B'][1]
                #if bottom_A > self.camera_data[0].res_y * 0.9 or bottom_B > self.camera_data[1].res_y * 0.9:
                #    continue
                fid = m["fid"]
                id_A = match[0]
                id_B = match[1]
                matching_data[fid]["id_map_A2B"][id_A] = id_B
                matching_data[fid]["id_map_B2A"][id_B] = id_A
                matching_data[fid]["world_sv3dt_A"][id_A] = m["world_position_A"]
                matching_data[fid]["world_sv3dt_B"][id_B] = m["world_position_B"]
                matching_data[fid]["image_position_A"][id_A] = m["image_position_A"]
                matching_data[fid]["image_position_B"][id_B] = m["image_position_B"]

        #print(f"matching_points: {matching_points}")
        R, t = self.estimate_transform(matching_points_3d, use_head_points=False)
        print(f"R: {R}, t: {t}")
        score_count = self.evaluate_transform(cam_id1, cam_id2, R, t)
        print(f"score: {score_count}")
        best_R = R
        best_t = t
        return best_R, best_t, best_id1, best_id2, matches, matching_points_3d, matching_points_2d, matching_points_3d_frame, matching_data

    def estimate_R_t_from_2D_matching_points(self, matching_points_2d):
        """ estimate the rotation matrix and translation vector from the matching 
        points using essential matrix

        Note: this is an experimental function.
        
        Args:
          matching_points_2d(list): the matching points per frame. 
          each point is a tuple of [(x_cam1, y_cam1), (x_cam2, y_cam2)]
        
        Returns:
          R(np.ndarray): the rotation matrix
          t(np.ndarray): the translation vector
        """
        # intrinsic matrix
        K1 = self.camera_data[0].int_mat
        K2 = self.camera_data[1].int_mat

        # pts1, pts2: Nx2 numpy arrays with matching points in both images
        #pts1 = np.array([[x11, y11], [x12, y12], ...])
        #pts2 = np.array([[x21, y21], [x22, y22], ...])
        pts1 = []
        pts2 = []
        for i in range(len(matching_points_2d)):
            for j in range(len(matching_points_2d[i])):
                pts1.append(matching_points_2d[i][j][0])
                pts2.append(matching_points_2d[i][j][1])
        pts1 = np.array(pts1, dtype=np.float64)
        pts2 = np.array(pts2, dtype=np.float64)

        # Intrinsic parameters (camera 1 and camera 2):
        #K1 = np.array([[fx1,  0,  cx1],
        #               [0,   fy1, cy1],
        #               [0,    0,   1]])

        #K2 = np.array([[fx2,  0,  cx2],
        #               [0,   fy2, cy2],
        #               [0,    0,   1]])

        # Normalize points
        pts1_norm = cv2.undistortPoints(np.expand_dims(pts1, axis=1), K1, None)
        pts2_norm = cv2.undistortPoints(np.expand_dims(pts2, axis=1), K2, None)

        # Estimate Essential Matrix from normalized points directly
        E, mask = cv2.findEssentialMat(pts1_norm, pts2_norm, focal=1.0, pp=(0., 0.), 
                                       method=cv2.RANSAC, prob=0.999, threshold=0.001)
        print("Essential Matrix:", repr(E))

        # Recover R and t
        # t is the translation vector. It is up to scale
        _, R, t, mask_pose = cv2.recoverPose(E, pts1_norm, pts2_norm)
        
        print("Rotation Matrix R:", repr(R))
        print("Translation Vector t (up to scale):", repr(t))
        return R, t

    def calibrate_2_cams_using_2D_matching_points_fundamental_matrix(self, matching_points_2d):
        """ Calibrate two cameras using 2D matching points and fundamental matrix.

        This method estimates the rotation matrix and translation vector that 
        transform points from camera 1's coordinate system to camera 2's 
        coordinate system.
        Note: the 2D points are in the image coordinates.

        Args:
            matching_points_2d(list): the matching points per frame. 
                each point is a tuple of [(x_cam1, y_cam1), (x_cam2, y_cam2)]

        Returns:
            tuple: A tuple containing:
                - R(np.ndarray): the rotation matrix
                - t(np.ndarray): the translation vector
        """
        # intrinsic matrix
        K1 = self.camera_data[0].int_mat
        K2 = self.camera_data[1].int_mat

        # pts1, pts2: Nx2 numpy arrays with matching points in both images
        #pts1 = np.array([[x11, y11], [x12, y12], ...])
        #pts2 = np.array([[x21, y21], [x22, y22], ...])
        pts1 = []
        pts2 = []
        for i in range(len(matching_points_2d)):
            for j in range(len(matching_points_2d[i])):
                pts1.append(matching_points_2d[i][j][0])
                pts2.append(matching_points_2d[i][j][1])
        pts1 = np.array(pts1, dtype=np.float64)
        pts2 = np.array(pts2, dtype=np.float64)

        pts1_norm = cv2.undistortPoints(np.expand_dims(pts1, axis=1), cameraMatrix=K1, distCoeffs=None)
        pts2_norm = cv2.undistortPoints(np.expand_dims(pts2, axis=1), cameraMatrix=K2, distCoeffs=None)

        # --- Step 1: Compute Fundamental Matrix (F) ---
        F, mask = cv2.findFundamentalMat(pts1, pts2, method=cv2.FM_RANSAC)
        print("Fundamental Matrix:", repr(F))

        # --- Step 2: Compute Essential Matrix (E) from Fundamental Matrix ---
        E = K2.T @ F @ K1
        print("Essential Matrix (E):", repr(E))

        # --- Step 3: Decompose Essential Matrix into Rotation (R) and Translation (t) ---
        # Note: Translation t is unit vector (direction only)
        _, R, t, mask_pose = cv2.recoverPose(E, pts1_norm, pts2_norm)
        print("Rotation Matrix (R):", repr(R))
        print("Translation Vector t (up to scale):", repr(t))
        return R, t
 
    def reconstruct_3d_points(self, R, t, pts1, pts2):
        """ reconstruct the 3D points from the matching points

        Args:
          R(np.ndarray): the rotation matrix
          t(np.ndarray): the translation vector
          pts1(np.ndarray): the points in the first camera
          pts2(np.ndarray): the points in the second camera
        
        Returns:
          pts3d(np.ndarray): the 3D points in the world coordinate
          reproj1(np.ndarray): the reprojected points in the first camera
          reproj2(np.ndarray): the reprojected points in the second camera
        """

        # Projection matrices
        K1 = self.camera_data[0].int_mat
        K2 = self.camera_data[1].int_mat
        P1 = K1 @ np.hstack((np.eye(3), np.zeros((3, 1))))
        P2 = K2 @ np.hstack((R, t))

        # Reshape to (2, N) for triangulation
        pts1_h = pts1.T
        pts2_h = pts2.T

        # Triangulate
        points_4d_hom = cv2.triangulatePoints(P1, P2, pts1_h, pts2_h)  # shape (4, N)
        points_3d = (points_4d_hom[:3] / points_4d_hom[3]).T  # shape (N, 3)

        # === REPROJECT TO IMAGE 1 AND 2 ===
        # cv2.projectPoints expects 3D points in shape (N, 1, 3)
        pts3d_obj = points_3d  # Shape: (N, 3)

        # Reproject to both images
        pts_3d_hom = np.concatenate((pts3d_obj, np.ones((pts3d_obj.shape[0], 1))), axis=1)
        reproj1_hom = (P1 @ pts_3d_hom.T)
        reproj2_hom = (P2 @ pts_3d_hom.T)
        reproj1 = reproj1_hom[:2] / reproj1_hom[2]
        reproj2 = reproj2_hom[:2] / reproj2_hom[2]
        reproj1 = reproj1.T
        reproj2 = reproj2.T
        return points_3d, reproj1, reproj2

def get_random_colors():
    """ Generate random colors for each match pair """
    colors = {}
    for id1 in range(30):
        color = tuple(np.random.randint(0, 255, 3).tolist())
        colors[id1] = color
    return colors

def visualize_matches(cam_id1:int, cam_id2:int, video_path1:str, video_path2:str, 
                      cam_data1:CameraData, cam_data2:CameraData, matches:list,
                      R:np.ndarray, t:np.ndarray, 
                      tracklet_to_global:dict, out_path:str):
    """ Visualize the matched tracklets with the same colors.
    
    Transform the points in the first video to the second video and 
    draw the points in the second video with the same color as the tracklet.
    Output video is saved in the output directory with the name 
    'matching_tracklets.mp4'.

    Args: 
        cam_id1(int): the ID of the first camera
        cam_id2(int): the ID of the second camera
        video_path1(str): the path to the first video
        video_path2(str): the path to the second video
        cam_data1(CameraData): the camera data of the first video
        cam_data2(CameraData): the camera data of the second video
        matches(list): the matches between the two videos
        R(np.ndarray): the rotation matrix
        t(np.ndarray): the translation vector
        tracklet_to_global(dict): the tracklet to global ID mapping {(cam_id, tracklet_id): global_id}
        out_path(str): the output path

    Returns:
        None
    """
    # Create video captures
    cap1 = open_video_capture(video_path1)
    cap2 = open_video_capture(video_path2)

    colors = get_random_colors()

    # read the first frame
    ret1, frame1 = cap1.read()
    ret2, frame2 = cap2.read()

    # get fps from the video
    fps1 = cap1.get(cv2.CAP_PROP_FPS)
    fps2 = cap2.get(cv2.CAP_PROP_FPS)

    # get the width and height of the video
    width1 = int(cap1.get(cv2.CAP_PROP_FRAME_WIDTH))
    height1 = int(cap1.get(cv2.CAP_PROP_FRAME_HEIGHT))
    width2 = int(cap2.get(cv2.CAP_PROP_FRAME_WIDTH))
    height2 = int(cap2.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # video writer
    out_video = open_video_writer(out_path, fps1, (width1 + width2, height1))

    fid = 0
    while True:
        frame_num = int(cap1.get(cv2.CAP_PROP_POS_FRAMES))
        if (frame_num >= len(cam_data1.tracking_data) or \
            frame_num >= len(cam_data2.tracking_data)):
            break

        # Draw bounding boxes for each matched pair
        for (id1, id2) in matches:
            global_id = tracklet_to_global[(cam_id1, id1)]

            # Get bounding boxes for current frame if they exist
            if id1 in cam_data1.tracking_data[frame_num].keys():
                bbox1 = cam_data1.tracking_data[frame_num][id1]['bbox']
                foot_image = cam_data1.tracking_data[frame_num][id1]['foot_image']
                x1, y1, w1, h1 = map(int, bbox1)
                cv2.rectangle(frame1, (x1, y1), (x1 + w1, y1 + h1), 
                              colors[global_id%len(colors)], 2)
                cv2.circle(frame1, (foot_image[0], foot_image[1]), 10, 
                           colors[global_id % len(colors)], -1)
                cv2.putText(frame1, f"ID: {global_id}", (x1, y1 + h1 + 20), 
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, 
                            colors[global_id%len(colors)], 2)

            if id2 in cam_data2.tracking_data[frame_num].keys():
                bbox2 = cam_data2.tracking_data[frame_num][id2]['bbox']
                foot_image = cam_data2.tracking_data[frame_num][id2]['foot_image']
                x2, y2, w2, h2 = map(int, bbox2)
                cv2.rectangle(frame2, (x2, y2), (x2 + w2, y2 + h2), 
                              colors[global_id % len(colors)], 2)
                cv2.circle(frame2, (foot_image[0], foot_image[1]), 10, 
                           colors[global_id % len(colors)], -1)
                cv2.putText(frame2, f"ID: {global_id}", (x2, y2 + h2 + 20), 
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, 
                            colors[global_id%len(colors)], 2)

        # transform the points in the second video
        #for  tid in cam_data1.tracking_data[frame_num].keys():
        #    pt3_cam1 = cam_data1.tracking_data[frame_num][tid]['foot_world']
        #    pt3_cam1 = np.array([pt3_cam1[0], pt3_cam1[1], 0]).reshape(3, 1)
        #    pt3_cam2 = np.dot(R, pt3_cam1) + t.reshape(3, 1)
        #    # project the point to the second video
        #    pt2_cam2 = project_3d_to_2d(pt3_cam2, cam_data2.proj_mat)
        #    x2, y2 = map(int, pt2_cam2[:2])
        #    cv2.circle(frame2, (x2, y2), 10, colors[tid % len(colors)], -1)
        #    # project the point to the first video
        #    pt2_cam1 = project_3d_to_2d(pt3_cam1, cam_data1.proj_mat)
        #    x1, y1 = map(int, pt2_cam1[:2])
        #    cv2.circle(frame1, (x1, y1), 10, colors[tid % len(colors)], -1)

        # Stack frames horizontally
        combined_frame = np.hstack((frame1, frame2))
        
        out_video.write(combined_frame)
        fid += 1
        ret1, frame1 = cap1.read()
        ret2, frame2 = cap2.read()
        
        if not ret1 or not ret2:
            break

    cap1.release()
    cap2.release()
    out_video.release()
    cv2.destroyAllWindows()

def project_3d_to_2d(pt3d, proj_mat):
    if pt3d.shape != (3,1):
        pt3d = pt3d.reshape(3, 1)
    pt3d_h = np.concatenate((pt3d, np.ones((1, 1))), axis=0)
    pt2d_h = np.dot(proj_mat, pt3d_h)
    pt2d = pt2d_h[:2] / pt2d_h[2]
    return pt2d

def unit_test():
    config_file = 'config.yaml'
    multi_view_calib = MultiViewCalib(config_file)
    # Generate random rotation matrix using random Euler angles
    angles = np.array([0, 0, np.random.uniform(0, 2*np.pi, 1)[0]])  # Random angles in radians
    Rx = np.array([[1, 0, 0],
                   [0, np.cos(angles[0]), -np.sin(angles[0])],
                   [0, np.sin(angles[0]), np.cos(angles[0])]])
    Ry = np.array([[np.cos(angles[1]), 0, np.sin(angles[1])],
                   [0, 1, 0],
                   [-np.sin(angles[1]), 0, np.cos(angles[1])]])
    Rz = np.array([[np.cos(angles[2]), -np.sin(angles[2]), 0],
                   [np.sin(angles[2]), np.cos(angles[2]), 0],
                   [0, 0, 1]])
    R_gt = np.dot(Rz, np.dot(Ry, Rx))
    # Generate random translation vector
    t_gt = np.random.uniform(-5, 5, 3)  # Random translation components between -5 and 5
    t_gt[2] = 0
    # generate 10 random points in the first camera
    pts_cam1 = np.random.uniform(-5, 5, (10, 2))
    # transform the points to the second camera
    pts_cam2 = np.dot(R_gt, np.concatenate((pts_cam1, np.zeros((10, 1))), axis=1).T) + t_gt.reshape(3, -1)
    pts_cam2 = pts_cam2.T[:, :2]
    matching_points = [(pts_cam1[i], pts_cam2[i]) for i in range(pts_cam1.shape[0])]
    # transform from cam 1 to cam 2
    R12, t12 = multi_view_calib.estimate_transform(matching_points)
    print(f"R_gt: {R_gt}, t_gt: {t_gt}")
    print(f"R12: {R12}, t12: {t12}")
    for pt in matching_points:
        pt1 = np.array([pt[0][0], pt[0][1], 0])
        pt2 = np.dot(R12, pt1) + t12
        print(f"pt1: {pt1}, pt2: {pt2}")
    print('ground truth matching points:')
    print(matching_points)

def get_circle_points(x0, y0, proj_mat):
    radius = 0.35 
    height = 1.75
    # top circle
    top_circle_points = []
    for angle in np.arange(0, 2 * np.pi, 0.1):
        x = x0 + radius * np.cos(angle)
        y = y0 + radius * np.sin(angle)
        top_circle_points.append((x, y, height))

    # bottom circle
    bottom_circle_points = []
    for angle in np.arange(0, 2 * np.pi, 0.1):
        x = x0 + radius * np.cos(angle)
        y = y0 + radius * np.sin(angle)
        bottom_circle_points.append((x, y, 0))

    return top_circle_points, bottom_circle_points

def draw_cylinder(image, top_circle_points, bottom_circle_points):
    for i in range(len(top_circle_points)-1):
        cv2.line(image, (int(top_circle_points[i][0]), int(top_circle_points[i][1])), 
                 (int(top_circle_points[i+1][0]), int(top_circle_points[i+1][1])), (0, 0, 255), 2)
        cv2.line(image, (int(bottom_circle_points[i][0]), int(bottom_circle_points[i][1])), 
                 (int(bottom_circle_points[i+1][0]), int(bottom_circle_points[i+1][1])), (0, 0, 255), 2)
    left_idx = len(top_circle_points) // 4
    right_idx = len(top_circle_points) * 3 // 4
    cv2.line(image, (int(top_circle_points[left_idx][0]), int(top_circle_points[left_idx][1])), 
             (int(bottom_circle_points[left_idx][0]), int(bottom_circle_points[left_idx][1])), (0, 0, 255), 2)
    cv2.line(image, (int(top_circle_points[right_idx][0]), int(top_circle_points[right_idx][1])), 
             (int(bottom_circle_points[right_idx][0]), int(bottom_circle_points[right_idx][1])), (0, 0, 255), 2)

def visualize_cylinders(img_path1:str, img_path2:str, 
                        cam_data1:CameraData, cam_data2:CameraData,
                        R:np.ndarray, t:np.ndarray, out_dir:str):
    """ Visualize the cylinders.
    
    Draw the reprojected cylinders in the first image and the second image for 
    evaluation.
    The cylinders are located in the camera 1's world coordinate.
    The cylinders are projected to the first image by camera 1's projection matrix.
    The reprojected cylinders are drawn in the first image.
    The cylinders are transformed to the second camera's world coordinate by the 
    rotation matrix R and translation vector t.
    The transformed cylinders are projected to the second image by camera 2's 
    projection matrix.
    The reprojected cylinders are drawn in the second image.

    Args:
        img_path1(str): the path to the first image
        img_path2(str): the path to the second image
        cam_data1(CameraData): the camera data of the first image
        cam_data2(CameraData): the camera data of the second image
        R(np.ndarray): the rotation matrix
        t(np.ndarray): the translation vector
        out_dir(str): the output directory

    Returns:
        None
    """
    # load the images
    img1 = cv2.imread(img_path1)
    img2 = cv2.imread(img_path2)

    grid_size = 4 # 4m x 4m
    min_x = -10
    max_x = 10
    min_y = -10
    max_y = 10

    for x in np.arange(min_x, max_x, grid_size):
        for y in np.arange(min_y, max_y, grid_size):
            pt3d = np.array([x, y, 0])
            # get the points of top circle and bottom circle
            top_circle_points, bottom_circle_points = get_circle_points(x, y, cam_data1.proj_mat)

            top3d = [project_3d_point_to_image_3x4_proj_mat(pt, cam_data1.proj_mat) for pt in top_circle_points]
            bottom3d = [project_3d_point_to_image_3x4_proj_mat(pt, cam_data1.proj_mat) for pt in bottom_circle_points]

            draw_cylinder(img1, top3d, bottom3d)   
            
            # transform top3d and bottom3d to the second camera
            top_circle_points_cam2 = [np.dot(R, pt) + t for pt in top_circle_points]
            bottom_circle_points_cam2 = [np.dot(R, pt) + t for pt in bottom_circle_points]
            top3d_cam2 = [project_3d_point_to_image_3x4_proj_mat(pt, cam_data2.proj_mat) for pt in top_circle_points_cam2]   
            bottom3d_cam2 = [project_3d_point_to_image_3x4_proj_mat(pt, cam_data2.proj_mat) for pt in bottom_circle_points_cam2]
            draw_cylinder(img2, top3d_cam2, bottom3d_cam2)

    cv2.imwrite(f'{out_dir}/cylinders_cam2.png', img2)
    cv2.imwrite(f'{out_dir}/cylinders_cam1.png', img1)

def visualize_apriltag(img_path1:str, img_path2:str, 
                       cam_data1:CameraData, cam_data2:CameraData,
                       R:np.ndarray, t:np.ndarray, out_dir:str):
    """ Visualize the apriltag's centers.
    
    Draw the apriltag's centers in the first image and the second image.
    The apriltag is located in the camera 1's world coordinate.
    The apriltag is projected to the first image by camera 1's projection matrix.
    The apriltag is transformed to the second camera's world coordinate by the 
    rotation matrix R and translation vector t.
    The transformed apriltag is projected to the second image by camera 2's 
    projection matrix.

    Args:
        img_path1(str): the path to the first image
        img_path2(str): the path to the second image
        cam_data1(CameraData): the camera data of the first image
        cam_data2(CameraData): the camera data of the second image
        R(np.ndarray): the rotation matrix
        t(np.ndarray): the translation vector
        out_dir(str): the output directory

    Returns:
        None
    """
    # read images
    img1 = cv2.imread(img_path1)
    img2 = cv2.imread(img_path2)

    tag_centers_global = np.array([[0, 0, 0], [2, 0, 0], [0, -3, 0], [2, -3, 0]]) 
    for i in range(4):
        pt3d = tag_centers_global[i]
        pt2d = project_3d_point_to_image_3x4_proj_mat(pt3d, cam_data1.proj_mat)
        cv2.circle(img1, (int(pt2d[0]), int(pt2d[1])), 5, (0, 0, 255), -1)

        # transform top3d and bottom3d to the second camera
        pt3d_cam2 = np.dot(R, pt3d) + t
        pt2d_cam2 = project_3d_point_to_image_3x4_proj_mat(pt3d_cam2, cam_data2.proj_mat)
        cv2.circle(img2, (int(pt2d_cam2[0]), int(pt2d_cam2[1])), 5, (0, 0, 255), -1)

    cv2.imwrite(f'{out_dir}/apriltag_cam2.png', img2)
    cv2.imwrite(f'{out_dir}/apriltag_cam1.png', img1)

def visualize_matching_points(video_path1:str, video_path2:str, 
                              matching_points_2d:list, out_dir:str):
    """ Visualize the matching points.
    
    Draw the foot points of the matching tracklets in the first video and the 
    second video. The matched points are linked by a line.
    Output video is saved in the output directory with the name 
    'matching_points.mp4'.
    
    Args:
        video_path1(str): the path to the first video
        video_path2(str): the path to the second video
        matching_points_2d(list): the matching points between the two videos
        out_dir(str): the output directory

    Returns:
        None
    """
    # load the videos
    cap1 = open_video_capture(video_path1)
    cap2 = open_video_capture(video_path2)

    # read the first frame
    ret1, frame1 = cap1.read()
    ret2, frame2 = cap2.read()

    # get fps from the video
    fps1 = cap1.get(cv2.CAP_PROP_FPS)
    fps2 = cap2.get(cv2.CAP_PROP_FPS)

    # get the width and height of the video
    width1 = int(cap1.get(cv2.CAP_PROP_FRAME_WIDTH))
    height1 = int(cap1.get(cv2.CAP_PROP_FRAME_HEIGHT))
    width2 = int(cap2.get(cv2.CAP_PROP_FRAME_WIDTH))
    height2 = int(cap2.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # video writer
    out_video_path = f'{out_dir}/matching_points.mp4'
    out_video = open_video_writer(out_video_path, fps1, (width1 + width2, height1))

    colors = get_random_colors()

    fid = 0
    while ret1 and ret2:
        combined_frame = np.hstack((frame1, frame2))
        # draw the matching points
        for i in range(len(matching_points_2d[fid])):
            pt1 = matching_points_2d[fid][i][0]
            pt2 = matching_points_2d[fid][i][1]
            cv2.circle(combined_frame, (int(pt1[0]), int(pt1[1])), 5, colors[i % len(colors)], -1)
            cv2.circle(combined_frame, (int(pt2[0]) + width1, int(pt2[1])), 5, colors[i % len(colors)], -1)
            # concatenate the frames
            cv2.line(combined_frame, (int(pt1[0]), int(pt1[1])), 
                     (int(pt2[0]) + width1, int(pt2[1])), colors[i % len(colors)], 2)
        out_video.write(combined_frame)
        fid += 1
        # read the next frame
        ret1, frame1 = cap1.read()
        ret2, frame2 = cap2.read()
    out_video.release()
    cap1.release()
    cap2.release()


def reconstruct_3d(K1, K2, points1, points2):
    """ Reconstruct the 3D points from the matching points using essential matrix
    
    Args:
        K1(np.ndarray): the intrinsic matrix of the first camera
        K2(np.ndarray): the intrinsic matrix of the second camera
        points1(np.ndarray): the points in the first camera
        points2(np.ndarray): the points in the second camera
    
    Returns:
        points3D(np.ndarray): the 3D points in the world coordinate
        R(np.ndarray): the rotation matrix
        t(np.ndarray): the translation vector
        error1(float): the reprojection error in the first camera
        error2(float): the reprojection error in the second camera
    """
    # Convert to numpy arrays
    points1 = np.array(points1, dtype=np.float64)
    points2 = np.array(points2, dtype=np.float64)

    # Compute the essential matrix
    E, inlier_mask = cv2.findEssentialMat(points1, points2, K1, method=cv2.RANSAC, threshold=1.0)
    n_inliers = int(sum(inlier_mask))
    n_outliers = int(len(inlier_mask)) - n_inliers
    print(f"#Inliers:{n_inliers}, #Outliers{n_outliers}, Ratio: {n_inliers/n_outliers*100} ")

    # Recover relative camera pose from the essential matrix
    _, R, t, mask = cv2.recoverPose(E, points1, points2, K1)

    # Projection matrices
    P1 = K1 @ np.hstack((np.eye(3), np.zeros((3,1))))         # [I | 0]
    P2 = K2 @ np.hstack((R, t))                               # [R | t]

    # Triangulate points
    points4D_hom = cv2.triangulatePoints(P1, P2, points1.T, points2.T)
    points3D = (points4D_hom / points4D_hom[3])[:3].T         # Convert from homogeneous to 3D

    # Reprojection into both cameras
    def reprojection_error(P, K, points_2d, points_3d):
        projected = (P @ np.vstack((points_3d.T, np.ones(points_3d.shape[0])))).T
        projected = projected[:, :2] / projected[:, 2:3]
        error = np.linalg.norm(projected - points_2d, axis=1)
        return np.mean(error)
    
    def project(P, K, points_3d):
        projected = (P @ np.vstack((points_3d.T, np.ones(points_3d.shape[0])))).T
        projected = projected[:, :2] / projected[:, 2:3]
        return projected

    # Project 3D points to image 1 and 2
    error1 = reprojection_error(P1, K1, points1, points3D)
    error2 = reprojection_error(P2, K2, points2, points3D)

    logging.info(f"Reprojection error in camera 1: {error1}")
    logging.info(f"Reprojection error in camera 2: {error2}")
    
    projected1 = project(P1, K1, points3D)
    projected2 = project(P2, K2, points3D)
    return points3D, R, t, error1, error2, projected1, projected2


def reconstruct_3d_normalized(K1, K2, points1, points2):
    """ Reconstruct the 3D points from the matching points using normalized 
    points andessential matrix
    
    Args:
        K1(np.ndarray): the intrinsic matrix of the first camera
        K2(np.ndarray): the intrinsic matrix of the second camera
        points1(np.ndarray): the points in the first camera
        points2(np.ndarray): the points in the second camera
    
    Returns:
        points3D(np.ndarray): the 3D points in the world coordinate
        R(np.ndarray): the rotation matrix
        t(np.ndarray): the translation vector
        error1(float): the reprojection error in the first camera
        error2(float): the reprojection error in the second camera
        projected1(np.ndarray): the reprojected points in the first camera
        projected2(np.ndarray): the reprojected points in the second camera
    """
    # Convert to numpy arrays
    points1 = np.array(points1, dtype=np.float64)
    points2 = np.array(points2, dtype=np.float64)

    # Normalize points using the inverse of the intrinsic matrices
    points1_norm = cv2.undistortPoints(np.expand_dims(points1, axis=1), K1, None).reshape(-1, 2)
    points2_norm = cv2.undistortPoints(np.expand_dims(points2, axis=1), K2, None).reshape(-1, 2)

    # Compute the essential matrix
    E, inlier_mask = cv2.findEssentialMat(points1_norm, points2_norm, method=cv2.RANSAC, threshold=1.0005)

    n_inliers = int(sum(inlier_mask))
    n_outliers = int(len(inlier_mask)) - n_inliers
    print(f"#Inliers:{n_inliers}, #Outliers{n_outliers}, Ratio: {n_inliers/n_outliers*100} ")


    # Recover relative camera pose from the essential matrix
    # Now R and t represent the relative rotation and translation from camera 1 to camera 2 [1]
    _, R, t, mask = cv2.recoverPose(E, points1_norm, points2_norm, mask=inlier_mask)


    # Projection matrices
    P1 = K1 @ np.hstack((np.eye(3), np.zeros((3,1))))         # [I | 0]
    P2 = K2 @ np.hstack((R, t))                               # [R | t]

    P1 = np.hstack((np.eye(3), np.zeros((3,1))))         # [I | 0]
    P2 = np.hstack((R, t))                               # [R | t]

    # Triangulate points
    points4D_hom = cv2.triangulatePoints(P1, P2, points1_norm.T, points2_norm.T)
    points3D = (points4D_hom / points4D_hom[3])[:3].T         # Convert from homogeneous to 3D

    # Reprojection into both cameras
    def reprojection_error(P, K, points_2d, points_3d):
        projected = (P @ np.vstack((points_3d.T, np.ones(points_3d.shape[0])))).T
        projected = projected[:, :2] / projected[:, 2:3]
        error = np.linalg.norm(projected - points_2d, axis=1)
        return np.mean(error)
    
    def project(P, K, points_3d):
        projected = (P @ np.vstack((points_3d.T, np.ones(points_3d.shape[0])))).T
        projected = projected[:, :2] / projected[:, 2:3]
        return projected

    error1 = reprojection_error(P1, None, points1_norm, points3D)
    error2 = reprojection_error(P2, None, points2_norm, points3D)
    logging.info(f"Reprojection error in camera 1: {error1}")
    logging.info(f"Reprojection error in camera 2: {error2}")
    
    P1 = K1 @ np.hstack((np.eye(3), np.zeros((3,1))))         # [I | 0]
    P2 = K2 @ np.hstack((R, t))                               # [R | t]
    projected1 = project(P1, None, points3D)
    projected2 = project(P2, None, points3D)
    return points3D, R, t, error1, error2, projected1, projected2


def reconstruct_3d_fundamental_matrix(K1, K2, points1, points2):
    """ Reconstruct the 3D points from the matching points using fundamental matrix
    
    Args:
        K1(np.ndarray): the intrinsic matrix of the first camera
        K2(np.ndarray): the intrinsic matrix of the second camera
        points1(np.ndarray): the points in the first camera
        points2(np.ndarray): the points in the second camera
    
    Returns:
        points3D(np.ndarray): the 3D points in the world coordinate
        R(np.ndarray): the rotation matrix
        t(np.ndarray): the translation vector
        error1(float): the reprojection error in the first camera
        error2(float): the reprojection error in the second camera
        projected1(np.ndarray): the reprojected points in the first camera
        projected2(np.ndarray): the reprojected points in the second camera
    """
    cfg = config.get_config()
    ransac_threshold = cfg['ransac_threshold']
    # Convert to numpy arrays
    points1 = np.array(points1, dtype=np.float64)
    points2 = np.array(points2, dtype=np.float64)

    # Step 1: Estimate Fundamental Matrix
    F, mask = cv2.findFundamentalMat(points1, points2, cv2.FM_RANSAC, 
                                     ransacReprojThreshold=ransac_threshold)
    n_inliers = int(sum(mask))
    n_outliers = int(len(mask)) - n_inliers
    print(f"#Inliers:{n_inliers}, #Outliers{n_outliers}, Ratio: {n_inliers/n_outliers*100} ")

    # Step 2: Compute Essential Matrix from F
    E = K2.T @ F @ K1

    # Step 3: Recover pose
    _, R, t, mask = cv2.recoverPose(E, points1, points2, K1, K2, mask=mask)

    # Projection matrices
    P1 = K1 @ np.hstack((np.eye(3), np.zeros((3,1))))         # [I | 0]
    P2 = K2 @ np.hstack((R, t))                               # [R | t]

    # Triangulate points
    points4D_hom = cv2.triangulatePoints(P1, P2, points1.T, points2.T)
    points3D = (points4D_hom / points4D_hom[3])[:3].T         # Convert from homogeneous to 3D

    # Reprojection into both cameras
    def reprojection_error(P, K, points_2d, points_3d):
        projected = (P @ np.vstack((points_3d.T, np.ones(points_3d.shape[0])))).T
        projected = projected[:, :2] / projected[:, 2:3]
        error = np.linalg.norm(projected - points_2d, axis=1)
        return np.mean(error)
    
    def project(P, K, points_3d):
        projected = (P @ np.vstack((points_3d.T, np.ones(points_3d.shape[0])))).T
        projected = projected[:, :2] / projected[:, 2:3]
        return projected


    # Project 3D points to image 1 and 2
    error1 = reprojection_error(P1, K1, points1, points3D)
    error2 = reprojection_error(P2, K2, points2, points3D)

    logging.info(f"Reprojection error in camera 1: {error1}")
    logging.info(f"Reprojection error in camera 2: {error2}")
    
    projected1 = project(P1, K1, points3D)
    projected2 = project(P2, K2, points3D)
    return points3D, R, t, error1, error2, projected1, projected2

def reconstruct_3d_homography(K1, K2, points1, points2, pts3d_sv3dt, verbose:bool=False):
    """ Reconstruct the 3D points from the matching points using homography matrix
    
    Args:
        K1(np.ndarray): the intrinsic matrix of the first camera
        K2(np.ndarray): the intrinsic matrix of the second camera
        points1(np.ndarray): the points in the first camera
        points2(np.ndarray): the points in the second camera
        verbose(bool): whether to print verbose output
    
    Returns:
        P1(np.ndarray): the projection matrix of the first camera
        P2(np.ndarray): the projection matrix of the second camera
        R(np.ndarray): the rotation matrix
        t(np.ndarray): the translation vector
        pts3d(np.ndarray): the 3D points in the world coordinate
        projected1(np.ndarray): the reprojected points in the first camera
        projected2(np.ndarray): the reprojected points in the second camera
    """
    # estimate pose using homography matrix
    #solutions, H = estimate_pose_from_homography(K1, K2, points1, points2)
    # estimate pose using homography matrix and normalized points
    solutions, H = estimate_pose_from_homography_normalized(K1, K2, points1, points2, verbose)
    pose, pts3d_best = select_best_solution(solutions, K1, K2, points1, points2, 
                                            pts3d_sv3dt)

    # check the result 
    # check if the distance between two cameras is too small
    dist = np.linalg.norm(pose[1])
    if dist < 0.01:
        print(f"Distance between two cameras is too small: {dist}")
        print("Estimate pose using homography matrix without normalization")
        solutions, H = estimate_pose_from_homography(K1, K2, points1, points2)
        pose, pts3d_best = select_best_solution(solutions, K1, K2, 
                                                points1, points2, pts3d_sv3dt)
    # check the alignment error
    scale, R_align, t_align = umeyama_alignment(pts3d_sv3dt, pts3d_best)
    if scale is None:
        return None, None, None, None, None, None, None, None, None
    X_recon_refined = (scale * R_align @ pts3d_best.T).T + t_align 
    refined_distances = np.linalg.norm(X_recon_refined - pts3d_sv3dt, axis=1)
    mean_refined_distances = np.mean(refined_distances)
    #print(f"Alignment error: {mean_refined_distances}")
    if mean_refined_distances > 1.0 or scale < 0.0001:
        #print(f"Alignment distance is too large: {mean_refined_distances}")
        #print("Estimate pose using homography matrix without normalization")
        solutions, H = estimate_pose_from_homography(K1, K2, points1, points2)
        pose_h, pts3d_best_h = select_best_solution(solutions, K1, K2, 
                                                    points1, points2, pts3d_sv3dt)
        scale_h, R_align_h, t_align_h = umeyama_alignment(pts3d_sv3dt, pts3d_best_h)
        if scale_h is not None:
            X_recon_refined = (scale_h * R_align_h @ pts3d_best_h.T).T + t_align_h 
            refined_distances_h = np.linalg.norm(X_recon_refined - pts3d_sv3dt, axis=1)
            mean_refined_distances_h = np.mean(refined_distances_h)
            #print(f"Alignment error: {mean_refined_distances}")
            if mean_refined_distances_h < mean_refined_distances:
                pose = pose_h
                pts3d_best = pts3d_best_h
                scale = scale_h
                R_align = R_align_h
                t_align = t_align_h
                mean_refined_distances = mean_refined_distances_h
 
    if pose is None:
        return None, None, None, None, None, None, None, None, None
    R_best = pose[0]
    t_best = pose[1]

    P1, P2 = get_projection_matrices(K1, K2, R_best, t_best)
    #print("Best Rotation:", repr(R_best))
    #print("Best Translation:", repr(t_best))
    if verbose:
        print("Projection Matrix P1:", repr(P1))
        print("Projection Matrix P2:", repr(P2))
    err1, err2 = compute_reprojection_error(P1, P2, 
                                            np.array(points1, dtype=np.float64), 
                                            np.array(points2, dtype=np.float64), 
                                            pts3d_best)

    def project(P, K, points_3d):
        projected = (P @ np.vstack((points_3d.T, np.ones(points_3d.shape[0])))).T
        projected = projected[:, :2] / projected[:, 2:3]
        return projected
    projected1 = project(P1, None, pts3d_best)
    projected2 = project(P2, None, pts3d_best)
    return P1, P2, R_best, t_best, pts3d_best, projected1, projected2, err1, err2 

def visualize_calibration_results(multi_view_calib:MultiViewCalib, 
                              video_path1:str, video_path2:str, 
                              matching_points_2d:list, R_est_2d:np.ndarray, 
                              t_est_2d:np.ndarray, out_dir:str):
    """Visualize 3D reconstruction results.
    
    This function reconstructs 3D points from matching 2D points and 
    visualizes the reprojection results. It creates 'reproj_method2.mp4' which 
    shows original points and reprojected points using R_est_2d and t_est_2d.
    Also, it calculates the mean reprojection error.
    
    Args:
        video_path1(str): Path to first video
        video_path2(str): Path to second video  
        matching_points_2d(list): List of matching points between videos
        R_est_2d(np.ndarray): Estimated rotation matrix
        t_est_2d(np.ndarray): Estimated translation vector
        out_dir(str): Output directory for visualization results
    """
    # reconstruct 3D points
    # video writer
    cap1 = open_video_capture(video_path1)
    cap2 = open_video_capture(video_path2)
    fps1 = cap1.get(cv2.CAP_PROP_FPS)
    width1 = int(cap1.get(cv2.CAP_PROP_FRAME_WIDTH))
    height1 = int(cap1.get(cv2.CAP_PROP_FRAME_HEIGHT))
    width2 = int(cap2.get(cv2.CAP_PROP_FRAME_WIDTH))
    height2 = int(cap2.get(cv2.CAP_PROP_FRAME_HEIGHT))
    out_video_path = f'{out_dir}/reproj_method2.mp4'
    out_video = open_video_writer(out_video_path, fps1, (width1 + width2, height1))
    print("Reconstruct 3D points...")
    reproj_error1_list = []
    reproj_error2_list = []
    points1 = []
    points2 = []
    frame_idx = []
    for fid in tqdm(range(len(matching_points_2d))):
        ret1, frame1 = cap1.read()
        ret2, frame2 = cap2.read()
        if not ret1 or not ret2:
            break
        if len(matching_points_2d[fid]) == 0:
            combined_frame = np.hstack((frame1, frame2))
            out_video.write(combined_frame)
            continue
        # matching_points_2d is a list of tuples, each tuple contains two points
        # [([x1, y1], [x2, y2]), ([x3, y3], [x4, y4]), ...]
        pts1 = np.array([pt[0] for pt in matching_points_2d[fid]], dtype=np.float64)
        pts2 = np.array([pt[1] for pt in matching_points_2d[fid]], dtype=np.float64)
        for pt in matching_points_2d[fid]:
            points1.append(pt[0])
            points2.append(pt[1])
            frame_idx.append(fid)
        pts3d, reproj1, reproj2 = multi_view_calib.reconstruct_3d_points(
            R_est_2d, t_est_2d, pts1, pts2)
        #print(f"pts3d: {pts3d}, reproj1: {reproj1}, reproj2: {reproj2}")
        # computer reprojection error
        reproj_error1 = np.linalg.norm(pts1 - reproj1, axis=1)
        reproj_error2 = np.linalg.norm(pts2 - reproj2, axis=1)
        #print(f"reproj_error1: {reproj_error1}, reproj_error2: {reproj_error2}")
        reproj_error1_list.extend(reproj_error1)
        reproj_error2_list.extend(reproj_error2)
        
        # visualize the projected points
        for i in range(len(pts3d)):
            cv2.circle(frame1, (int(reproj1[i][0]), int(reproj1[i][1])), 5, (0, 0, 255), -1)
            cv2.circle(frame2, (int(reproj2[i][0]), int(reproj2[i][1])), 5, (0, 0,255), -1)
        for i in range(len(pts1)):
            cv2.circle(frame1, (int(pts1[i][0]), int(pts1[i][1])), 10, (0, 255, 255), -1)
            cv2.circle(frame2, (int(pts2[i][0]), int(pts2[i][1])), 10, (0, 255, 255), -1)
        combined_frame = np.hstack((frame1, frame2))
        out_video.write(combined_frame)
    out_video.release()
    cap1.release()
    cap2.release()

    # mean reprojection error
    mean_reproj_error1 = np.mean(reproj_error1_list)
    mean_reproj_error2 = np.mean(reproj_error2_list)
    logging.info(f"mean_reproj_error1: {mean_reproj_error1}, mean_reproj_error2: {mean_reproj_error2}")

def calibrate_2_cams_method3(video_path1:str, video_path2:str, 
                             cam_data1:CameraData, cam_data2:CameraData,
                             matching_points_2d:list, out_dir:str, 
                             debug:bool=False):
    """Calibrate two cameras using 2D matching points.
    
    This function calibrates two cameras using 2D matching points.
    It first estimates the rotation and translation matrices using 2D matching points.
    Then, it visualizes the results.

    matching_points_2d: list of tuples, each tuple contains two points
    [(pt0, pt1), (pt2, pt3), ...]

    Args:
        video_path1(str): Path to first video
        video_path2(str): Path to second video
        cam_data1(CameraData): Camera data of the first video
        cam_data2(CameraData): Camera data of the second video
        matching_points_2d(list): List of matching points between videos
        out_dir(str): Output directory for visualization results
        debug(bool): Whether to visualize the reprojection 

    Returns:
        points3D(np.ndarray): the 3D points in the world coordinate
        R(np.ndarray): the rotation matrix
        t(np.ndarray): the translation vector
    """
    # reconstruct 3D points
    # video writer
    cap1 = open_video_capture(video_path1)
    cap2 = open_video_capture(video_path2)
    fps1 = cap1.get(cv2.CAP_PROP_FPS)
    width1 = int(cap1.get(cv2.CAP_PROP_FRAME_WIDTH))
    height1 = int(cap1.get(cv2.CAP_PROP_FRAME_HEIGHT))
    width2 = int(cap2.get(cv2.CAP_PROP_FRAME_WIDTH))

    points1 = []
    points2 = []
    frame_idx = []
    for fid in tqdm(range(len(matching_points_2d))):
        if len(matching_points_2d[fid]) == 0:
            continue
        # matching_points_2d is a list of tuples, each tuple contains two points
        # [([x1, y1], [x2, y2]), ([x3, y3], [x4, y4]), ...]
        for pt in matching_points_2d[fid]:
            points1.append(pt[0])
            points2.append(pt[1])
            frame_idx.append(fid)

    ######################################################################## 
    #""" experimental code
    # estimate R and t using essential matrix 
    #print('---------------------------------------')
    #print("estimate R and t using essential matrix")
    #print('---------------------------------------\n')
    #points3D, R, t, error1, error2, projected1, projected2 = \
    #    reconstruct_3d(cam_data1.int_mat, cam_data2.int_mat, points1, points2)
    ######################################################################## 

    ######################################################################## 
    #""" experimental code
    # estimate R and t using fundamental matrix 
    #print('---------------------------------------')
    #print("estimate R and t using fundamental matrix")
    #print('---------------------------------------\n')
    #points3D, R, t, error1, error2, projected1, projected2 = \
    #    reconstruct_3d_fundamental_matrix(cam_data1.int_mat, cam_data2.int_mat, points1, points2)
    ######################################################################## 

    ######################################################################## 
    #""" experimental code
    # I think reconstruct_3d-normalized should be more accurate. 
    # But it is worse. 
    # TODO: need to investigate.
    #points3D, R, t, error1, error2, projected1, projected2 = \
    #    reconstruct_3d_normalized(cam_data1.int_mat, cam_data2.int_mat, 
    #                              points1, points2)
    ######################################################################## 

    ######################################################################## 
    # experimental code
    # pose estimation using homography instead of essential matrix
    #print('---------------------------------------')
    #print("estimate R and t using homography")
    #print('---------------------------------------\n')
    P1, P2, R_best, t_best, pts3d_best, projected1, projected2, reproj_err1, reproj_err2 = \
        reconstruct_3d_homography(cam_data1.int_mat, cam_data2.int_mat, 
                                  points1, points2)
    points3D = pts3d_best
    R = R_best
    t = t_best.reshape(3, 1)
    ######################################################################## 

    #if debug:
    #    cap1 = open_video_capture(video_path1)
    #    cap2 = open_video_capture(video_path2)
    #    out_video_path = f'{out_dir}/reproj_method3.mp4'
    #    out_video = cv2.VideoWriter(out_video_path, cv2.VideoWriter_fourcc(*'mp4v'), 
    #                                fps1, (width1 + width2, height1))
    #    idx = 0
    #    for fid in tqdm(range(len(matching_points_2d))):
    #        ret1, frame1 = cap1.read()
    #        ret2, frame2 = cap2.read()
    #        if not ret1 or not ret2:
    #            break
    #        while idx < len(frame_idx) and fid == frame_idx[idx]:
    #            cv2.circle(frame1, (int(projected1[idx][0]), int(projected1[idx][1])), 10, (0, 0, 255), -1)
    #            cv2.circle(frame2, (int(projected2[idx][0]), int(projected2[idx][1])), 10, (0, 0, 255), -1)
    #            idx += 1
    #            if idx >= len(frame_idx):
    #                break
    #        combined_frame = np.hstack((frame1, frame2))
    #        out_video.write(combined_frame)
    #    out_video.release()
    #    cap1.release()
    #    cap2.release()

    return points3D, R, t, P1, P2

def get_points_by_matches(matching_data, match_idx): 
    """ Get 2D points and 3D points of match_idx from the matching data
    
    Args:
        matching_data(list): List of matching data
        match_idx(list): List of match index

    Returns:
        res(dict): Dictionary containing the results
        res['points1'](list): List of 2D points of match_idx
        res['points2'](list): List of 2D points of match_idx
        res['frame_idx'](list): List of frame index of match_idx
        res['pts3_sv3dt'](list): List of 3D points of match_idx
    """
    points1 = []
    points2 = []
    frame_idx = []
    pts3_sv3dt = []
    n_frame = len(matching_data)
    for fid in range(n_frame):
        #if fid not in matching_data.keys():
        #    continue
        for tid1 in matching_data[fid]["id_map_A2B"].keys():
            tid2 = matching_data[fid]["id_map_A2B"][tid1]
            if [tid1, tid2] in match_idx:
                # TODO: experiment
                # distance between the point and the camera center
                #dist = np.linalg.norm(matching_data[fid]["world_sv3dt_A"][tid1])
                #if dist > 10.0:
                #    continue
                points1.append(matching_data[fid]["image_position_A"][tid1])
                points2.append(matching_data[fid]["image_position_B"][tid2])
                frame_idx.append(fid)
                pts3_sv3dt.append([matching_data[fid]["world_sv3dt_A"][tid1][0], 
                                   matching_data[fid]["world_sv3dt_A"][tid1][1], 0.0])
    res = {}
    res['points1'] = np.array(points1, dtype=np.float64)
    res['points2'] = np.array(points2, dtype=np.float64)
    res['frame_idx'] = frame_idx
    res['pts3_sv3dt'] = pts3_sv3dt
    return res

# validate matching data for the pose estimation step
def validate_matching_data(matches_filtered, points1, points2):
    """ Validate matching data for the pose estimation step
    
    The number of matched tracklets should be greater than a threshold
    The number of points should be greater than a threshold
    
    Args:
        matches_filtered(list): List of matches after filtering
        points1(list): List of 2D points of the matches
        points2(list): List of 2D points of the matches

    Returns:
        bool: True if the matching data is valid, False otherwise
    """
    cfg = config.get_config()
    cfg_robust_two_view_calib = cfg.get('robust_two_view_calib', {})
    min_matched_points = cfg_robust_two_view_calib.get('min_matched_points', 100)
    # min_matched_tracklets = cfg_robust_two_view_calib.get('min_matched_tracklets', 3)
    # check if the points are valid
    if len(points1) < min_matched_points or len(points2) < min_matched_points:
        return False
    # # check if the matches are valid
    # if len(matches_filtered) < min_matched_tracklets:
    #     return False
    return True
 
def calibrate_2_cams_robust(video_path1:str, video_path2:str, 
                             cam_data1:CameraData, cam_data2:CameraData,
                             matching_points_2d:list, out_dir:str,
                             matches:list, matching_data:list, debug:bool=False):
    """Calibrate two cameras using 2D matching points.
    
    This function calibrates two cameras using 2D matching points.
    It first estimates the rotation and translation matrices using 2D matching points.
    Then, it visualizes the results.

    matching_points_2d: list of tuples, each tuple contains two points
    [(pt0, pt1), (pt2, pt3), ...]

    Args:
        video_path1(str): Path to first video
        video_path2(str): Path to second video
        cam_data1(CameraData): Camera data of the first video
        cam_data2(CameraData): Camera data of the second video
        matching_points_2d(list): List of matching points between videos
        out_dir(str): Output directory for visualization results
        matches(list): List of matches
        matching_data(list): List of matching data
        debug(bool): Whether to visualize the reprojection 

    Returns:
        res(dict): Dictionary containing the results
            - res['points3D'](np.ndarray): the 3D points in the world coordinate
            - res['R'](np.ndarray): the rotation matrix
            - res['t'](np.ndarray): the translation vector
            - res['P1'](np.ndarray): the projection matrix of the first camera
            - res['P2'](np.ndarray): the projection matrix of the second camera
            - res['pts3_sv3dt'](list): List of 3D points of the matching data
            - res['matches_filtered'](list): List of matches after filtering
            - res['matches_outliers'](list): List of matches after filtering
    """
    logging.info("calibrate_2_cams_robust method")
    cfg = config.get_config()
    cfg_robust_two_view_calib = cfg.get('robust_two_view_calib', {})
    reproj_err_threshold = cfg_robust_two_view_calib.get('reproj_err_threshold', 10.0)
    max_iterations = cfg_robust_two_view_calib.get('max_iterations', 100)
    inlier_threshold = cfg_robust_two_view_calib.get('inlier_threshold', 20.0)
    min_inliers = cfg_robust_two_view_calib.get('min_inliers', 0.6)
    min_samples = cfg_robust_two_view_calib.get('min_samples', 3)
    # Range checks
    if not (0.0 < min_inliers <= 1.0):
        raise ValueError(f"min_inliers must be between 0 and 1, got {min_inliers}")
    if min_samples < 3:
        raise ValueError(f"min_samples must be >= 3 for homography estimation, got {min_samples}")

    n_matches = len(matches)
    n_inliers = 0
    n_iterations = 0
    best_n_inliers = 0
    best_n_outliers = 0
    best_P1 = None
    best_P2 = None
    best_in_out_ratio = 0
    R_best = None
    t_best = None
    # Debug counters
    n_validate_failed = 0
    n_homography_failed = 0

    # get 2D points from all matches
    logging.info(f"[calibrate_2_cams_robust] matching_data size: {len(matching_data)} frames, matches: {len(matches)}, max_iterations: {max_iterations}")
    res_pts = get_points_by_matches(matching_data, matches)
    all_points1 = res_pts['points1']
    all_points2 = res_pts['points2']
    logging.info(f"[calibrate_2_cams_robust] Starting RANSAC loop: all_points1={len(all_points1)}, all_points2={len(all_points2)}")

    # check if the matches are valid
    if n_matches < min_samples:
        logging.error(f"Number of matching tracklet candidates is less than {min_samples}")
        exit()

    while n_iterations < max_iterations:
        n_iterations += 1
        # randomly sample 3 matches 
        sample_indices = np.random.choice(n_matches, min_samples, replace=False)
        sample_matches = [matches[i] for i in sample_indices]   
        #print(f"sample_matches: {sample_matches}")

        # get 2D points from the sample matches
        res_pts = get_points_by_matches(matching_data, sample_matches)
        points1 = res_pts['points1']
        points2 = res_pts['points2']
       
        if not validate_matching_data(sample_matches, points1, points2):
            n_validate_failed += 1
            logging.debug(f"[RANSAC] validate_matching_data failed: points1={len(points1)}, points2={len(points2)}, sample_matches={len(sample_matches)}")
            continue

        # estimate R and t using the sample matches
        P1, P2, R_est, t_est, pts3d_est, projected1, projected2, reproj_err1, reproj_err2 = \
            reconstruct_3d_homography(cam_data1.int_mat, cam_data2.int_mat, 
                                      points1, points2, res_pts['pts3_sv3dt']) 
        if P1 is None:
            n_homography_failed += 1
            logging.debug("[RANSAC] reconstruct_3d_homography returned None, skipping iteration")
            continue
        # reprojection error for all points
        pts3d_h = cv2.triangulatePoints(P1, P2, (all_points1).T,(all_points2).T)
        pts3d = (pts3d_h / pts3d_h[3])[:3].T
        reproj_err1, reproj_err2 = compute_reprojection_error(P1, P2, 
                                    all_points1, all_points2, pts3d)
        # count inliers and outliers
        inliers1 = reproj_err1 < inlier_threshold
        inliers2 = reproj_err2 < inlier_threshold
        inliers = inliers1 & inliers2
        n_inliers = np.sum(inliers)
        n_outliers = reproj_err1.shape[0] - n_inliers
        #print(f"n_inliers: {n_inliers}, n_outliers: {n_outliers}")
        in_out_ratio = float(n_inliers) / float(n_inliers + n_outliers)
        if in_out_ratio > best_in_out_ratio:
            best_in_out_ratio = in_out_ratio
            best_n_inliers = n_inliers
            best_n_outliers = n_outliers
            best_reproj_err1 = reproj_err1
            best_reproj_err2 = reproj_err2
            best_P1 = P1
            best_P2 = P2
            R_best = R_est
            t_best = t_est
            
    logging.info(f"[calibrate_2_cams_robust] Completed {n_iterations} iterations")
    logging.info(f"[calibrate_2_cams_robust] Failures: validate_failed={n_validate_failed}, homography_failed={n_homography_failed}, successful={n_iterations - n_validate_failed - n_homography_failed}")
    logging.debug(f"best_in_out_ratio: {best_in_out_ratio}, best_n_inliers: {best_n_inliers}, best_n_outliers: {best_n_outliers}")
    logging.debug(f"best_reproj_err1: {np.mean(best_reproj_err1)}, best_reproj_err2: {np.mean(best_reproj_err2)}")

    if best_in_out_ratio < min_inliers:
        logging.error(f"Failed to estimate R and t after {max_iterations} iterations")
        return None, None, None, None, None

    # compute reprojection errors for all matches
    matches_filtered = []
    matches_outliers = []
    for m in matches:
        res_pts = get_points_by_matches(matching_data, [m])
        pts1 = res_pts['points1']
        pts2 = res_pts['points2']
        if len(pts1) == 0 or len(pts2) == 0:
            continue
        logging.debug(f"m: {m}")
        pts3d_h = cv2.triangulatePoints(best_P1, best_P2, (pts1).T,(pts2).T)
        pts3d = (pts3d_h / pts3d_h[3])[:3].T
        reproj_err1, reproj_err2 = compute_reprojection_error(best_P1, best_P2, 
                                    pts1, pts2, pts3d)
        logging.debug(f"mean reproj_err1: {np.mean(reproj_err1)}, reproj_err2: {np.mean(reproj_err2)}")
        # filter out the matches with large reprojection errors
        #if (np.mean(reproj_err1) > reproj_err_threshold or 
        #    np.mean(reproj_err2) > reproj_err_threshold):
        if (np.mean(reproj_err1) + np.mean(reproj_err2)  > reproj_err_threshold): 
            matches_outliers.append(m)
        else:
            matches_filtered.append(m)
    logging.info(f"matches_filtered: {len(matches_filtered)}, matches_outliers: {len(matches_outliers)}")
    logging.debug(f"matches_filtered: {matches_filtered}")
    logging.debug(f"matches_outliers: {matches_outliers}")

    # compute reprojection errors for the filtered matches
    res_pts = get_points_by_matches(matching_data, matches_filtered)
    points1 = res_pts['points1']
    points2 = res_pts['points2']
    frame_idx = res_pts['frame_idx']

    # check if the matches are valid
    if len(matches_filtered) < min_samples:
        logging.error(f"Number of filtered matching tracklet candidates is less than {min_samples}")
        exit()
    
    if not validate_matching_data(matches_filtered, points1, points2):
        logging.error("[ERROR] Not enough matching data for pose estimation. Need to collect longer videos with more people")
        exit()

    # reconstruct 3D points
    # video writer
    cap1 = open_video_capture(video_path1)
    cap2 = open_video_capture(video_path2)
    fps1 = cap1.get(cv2.CAP_PROP_FPS)
    width1 = int(cap1.get(cv2.CAP_PROP_FRAME_WIDTH))
    height1 = int(cap1.get(cv2.CAP_PROP_FRAME_HEIGHT))
    width2 = int(cap2.get(cv2.CAP_PROP_FRAME_WIDTH))

    ######################################################################## 
    #""" experimental code
    # estimate R and t using essential matrix 
    #print('---------------------------------------')
    #print("estimate R and t using essential matrix")
    #print('---------------------------------------\n')
    #points3D, R, t, error1, error2, projected1, projected2 = \
    #    reconstruct_3d(cam_data1.int_mat, cam_data2.int_mat, points1, points2)
    ######################################################################## 

    ######################################################################## 
    #""" experimental code
    # estimate R and t using fundamental matrix 
    #print('---------------------------------------')
    #print("estimate R and t using fundamental matrix")
    #print('---------------------------------------\n')
    #points3D, R, t, error1, error2, projected1, projected2 = \
    #    reconstruct_3d_fundamental_matrix(cam_data1.int_mat, cam_data2.int_mat, points1, points2)
    ######################################################################## 

    ######################################################################## 
    #""" experimental code
    # I think reconstruct_3d-normalized should be more accurate. 
    # But it is worse. 
    # TODO: need to investigate.
    #points3D, R, t, error1, error2, projected1, projected2 = \
    #    reconstruct_3d_normalized(cam_data1.int_mat, cam_data2.int_mat, 
    #                              points1, points2)
    ######################################################################## 

    ######################################################################## 
    # experimental code
    # pose estimation using homography instead of essential matrix
    P1, P2, R_best, t_best, pts3d_best, projected1, projected2, reproj_err1, reproj_err2 = \
        reconstruct_3d_homography(cam_data1.int_mat, cam_data2.int_mat, 
                                  points1, points2, res_pts['pts3_sv3dt'])
    if P1 is None:
        ValueError("Failed to estimate R and t using homography")
        exit()

    points3D = pts3d_best
    R = R_best
    t = t_best.reshape(3, 1)
    ######################################################################## 
    
    # visualize the feature points
    cap1 = open_video_capture(video_path1)
    cap2 = open_video_capture(video_path2)
    ret1, frame1 = cap1.read()
    ret2, frame2 = cap2.read()
    c = (0, 255, 255)
    for i in range(points1.shape[0]):
        pt1 = points1[i]
        pt2 = points2[i]
        cv2.circle(frame1, (int(pt1[0]), int(pt1[1])), 2, c, -1)
        cv2.circle(frame2, (int(pt2[0]), int(pt2[1])), 2, c, -1)
    cv2.imwrite(f'{out_dir}/cam0_points.jpg', frame1)
    cv2.imwrite(f'{out_dir}/cam1_points.jpg', frame2)
    cv2.destroyAllWindows()
    cap1.release()
    cap2.release()

    
    #if debug:
    #    cap1 = open_video_capture(video_path1)
    #    cap2 = open_video_capture(video_path2)
    #    out_video_path = f'{out_dir}/reproj_method3.mp4'
    #    out_video = cv2.VideoWriter(out_video_path, cv2.VideoWriter_fourcc(*'mp4v'), 
    #                                fps1, (width1 + width2, height1))
    #    idx = 0
    #    for fid in tqdm(range(len(matching_points_2d))):
    #        ret1, frame1 = cap1.read()
    #        ret2, frame2 = cap2.read()
    #        if not ret1 or not ret2:
    #            break
    #        while idx < len(frame_idx) and fid == frame_idx[idx]:
    #            cv2.circle(frame1, (int(projected1[idx][0]), int(projected1[idx][1])), 10, (0, 0, 255), -1)
    #            cv2.circle(frame2, (int(projected2[idx][0]), int(projected2[idx][1])), 10, (0, 0, 255), -1)
    #            idx += 1
    #            if idx >= len(frame_idx):
    #                break
    #        combined_frame = np.hstack((frame1, frame2))
    #        out_video.write(combined_frame)
    #    out_video.release()
    #    cap1.release()
    #    cap2.release()

    res = {}
    res['points3D'] = points3D
    res['R'] = R
    res['t'] = t
    res['P1'] = P1
    res['P2'] = P2
    res['pts3_sv3dt'] = res_pts['pts3_sv3dt']
    res['matches_filtered'] = matches_filtered
    res['matches_outliers'] = matches_outliers
    return res

# Umeyama algorithm functions moved to core.utils.math_utils

def save_calib_results(cam_data1:CameraData, cam_data2:CameraData, 
                       R:np.ndarray, t:np.ndarray, scale:float,
                       matches:list, out_dir:str):
    """ Save the calibration results to JSON file.

    - focal length
    - R, t, scale
    - 2D matching points
    - 3D matching points

    Args:
        cam_data1(CameraData): The first camera data
        cam_data2(CameraData): The second camera data
        R(np.ndarray): The rotation matrix
        t(np.ndarray): The translation vector
        scale(float): The scale factor
        matches(list): The matches between the two cameras
        out_dir(str): The output directory

    JSON schema:
    {
        "cameras":[
            {
              "id": 0,
              "f": 60,
              "R": [[...], [...], [...]],      // 3x3 rotation matrix
              "t": [x, y, z]                   // translation vector (length 3)
            },
            {
              "id": 1,
              "f": 60,
              "R": [[...], [...], [...]],
              "t": [x, y, z]
            }
        ],
        "matches": [ 
            {
                "time": 1,
                "id_map_A2B":[0: 1, 1: 2, ...],
                "id_map_B2A":[1: 0, 2: 1, ...],
                "world_sv3dt_A": { 0: [0, 0, 0], 1: [0, 0, 0], ...},
                "world_sv3dt_B": { 0: [0, 0, 0], 1: [0, 0, 0], ...},
                "world_cam_A": { 0: [0, 0, 0], 1: [0, 0, 0], ...},
                "image_position_A": { 0: [0, 0], 1: [0, 0], ...},
                "image_position_B": { 0: [0, 0], 1: [0, 0], ...},
            },
            {
                "time": 2,
                "id_map_A2B":[0: 1, 1: 2, ...],
                "id_map_B2A":[1: 0, 2: 1, ...],
                "world_sv3dt_A": { 0: [0, 0, 0], 1: [0, 0, 0], ...},
                "world_sv3dt_B": { 0: [0, 0, 0], 1: [0, 0, 0], ...},
                "world_cam_A": { 0: [0, 0, 0], 1: [0, 0, 0], ...},
                "image_position_A": { 0: [0, 0], 1: [0, 0], ...},
                "image_position_B": { 0: [0, 0], 1: [0, 0], ...},
            },
            ...
        ]
    }
    """
    results = {
        "cameras": [
            {
                "id": cam_data1.id,
                "f": cam_data1.int_mat[0, 0],
                "R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                "t": [0, 0, 0]
            },
            {
                "id": cam_data2.id,
                "f": cam_data2.int_mat[0, 0],
                "R": R.tolist(),
                "t": (t.squeeze() * scale).tolist()
            }
        ],
        "matches": matches 
    }
    # save the results to JSON file
    with open(f'{out_dir}/calib_results.json', 'w') as f:
        json.dump(results, f)

# convert world position in SV3DT to the first camera coordinate
def convert_sv3dt_to_cam0(cam_data1:CameraData, cam_data2:CameraData, 
                          matching_data:list, R:np.ndarray, t:np.ndarray):
    P1 = cam_data1.int_mat @ np.hstack((np.eye(3), np.zeros((3,1))))         # [I | 0]
    P2 = cam_data2.int_mat @ np.hstack((R, t))                               # [R | t]
    for match in matching_data:
        for id, pt in match["image_position_A"].items():
            points1 = np.array(pt, dtype=np.float64)
            id2 = match["id_map_A2B"][id]
            points2 = np.array(match["image_position_B"][id2], dtype=np.float64)
            # Triangulate points
            points4D_hom = cv2.triangulatePoints(P1, P2, points1.T, points2.T)
            point3D = (points4D_hom / points4D_hom[3])[:3].T         # Convert from homogeneous to 3D
            match["world_cam_A"][id] = point3D[0].tolist()

def get_matching_tracklets(config:dict, cid0:int, cid1:int, 
                          tracking_data0:list, tracking_data1:list, output_base_dir:str):
    """ Get matching data (matching points and matching tracklets) from 
    sorted_tracklet_matches.json file 

    Args:
        config (dict): config dictionary
        cid0 (int): camera id 0
        cid1 (int): camera id 1
        tracking_data0 (list): list of dicts, each dict contains the tracking 
                               data for a frame [{tid: [x, y]}, {tid: [x, y]}, ...]
        tracking_data1 (list): list of dicts, each dict contains the tracking 
                               data for a frame [{tid: [x, y]}, {tid: [x, y]}, ...]

    Returns:
        matches (list): list of tuples, each tuple contains two track ids
        matching_points_2d (list): list of tuples, each tuple contains two 2D points
        matching_data (list): list of dicts, each dict contains the matching data 
                        for a frame
    """
    out_base_dir = output_base_dir
    # from JSON file
    with open(f'{out_base_dir}/sorted_tracklet_matches.json', 'r') as f:
        sorted_tracklet_matches = json.load(f)

    camera_pairs = config['camera_pairs']
    max_matched_tracklets_dist = config['max_matched_tracklets_dist']
    index = {}
    for i in range(len(camera_pairs)):
        index[(camera_pairs[i][0], camera_pairs[i][1])] = i
    if (cid0, cid1) not in index.keys():
        logging.error(f"Error: camera pair ({cid0}, {cid1}) is not in the config")
        return None, None, None
    logging.debug(f"sorted_tracklet_matches: {sorted_tracklet_matches[index[(cid0, cid1)]]}")
    auto_matches = []
    tid1_greedy_list = []
    for data in sorted_tracklet_matches[index[(cid0, cid1)]]:
        if data[1] <= max_matched_tracklets_dist:
            if data[0][0] not in tid1_greedy_list:
                auto_matches.append(data[0])
                tid1_greedy_list.append(data[0][0])
            else:
                continue
        else:
            continue
    matches = auto_matches 

    # to dictionary {tid0: tid1}
    match_dict = {}
    for match in matches:
        match_dict[match[0]] = match[1]

    match_points_2d_all_frames = []
    n_frame = min(len(tracking_data0), len(tracking_data1))
    matching_data = [{"time": i, "id_map_A2B": {}, "id_map_B2A": {}, 
                      "world_sv3dt_A": {}, "world_sv3dt_B": {},
                      "world_cam_A": {},
                      "image_position_A": {}, "image_position_B": {}} 
                      for i in range(n_frame)] # JSON format for saving
    video_resolution = config['video_resolution']
    image_bottom_boundary = config['image_bottom_boundary']
    image_side_boundary = config['image_side_boundary']
    min_x = video_resolution[0] * image_side_boundary
    max_x = video_resolution[0] * (1 - image_side_boundary)
    # filter out the tracklets that are too close to the bottom of the image
    max_y = video_resolution[1] * image_bottom_boundary

    # last positions of the tracklets
    last_positions_A = {}
    last_positions_B = {}
    
    for fid in range(n_frame):
        tids0 = tracking_data0[fid].keys()
        tids1 = tracking_data1[fid].keys()
        match_points_2d = []
        for tid0 in tids0:
            if tid0 in match_dict.keys():
                tid1 = match_dict[tid0]
                if tid1 in tids1:
                    current_position_A = np.array(tracking_data0[fid][tid0]['foot_image'])
                    current_position_B = np.array(tracking_data1[fid][tid1]['foot_image'])
                    # add last position of the tracklet
                    if tid0 in last_positions_A.keys() and tid1 in last_positions_B.keys():
                        # distance between last position and current position
                        dist_A = np.linalg.norm(last_positions_A[tid0] - current_position_A)
                        dist_B = np.linalg.norm(last_positions_B[tid1] - current_position_B)
                        # skip if the distance is too small
                        if dist_A < config['min_tracklet_dist'] and dist_B < config['min_tracklet_dist']:
                            continue
                    last_positions_A[tid0] = current_position_A
                    last_positions_B[tid1] = current_position_B
                    pt0 = tracking_data0[fid][tid0]['foot_image']
                    pt1 = tracking_data1[fid][tid1]['foot_image']
                    if (pt0[0] < min_x) or (pt0[0] > max_x) or (pt1[0] < min_x) or (pt1[0] > max_x):
                        continue
                    if (pt0[1] > max_y) or (pt1[1] > max_y): 
                        continue
                    match_points_2d.append((pt0, pt1))
                    # add matching data
                    matching_data[fid]["id_map_A2B"][tid0] = tid1
                    matching_data[fid]["id_map_B2A"][tid1] = tid0
                    matching_data[fid]["world_sv3dt_A"][tid0] = tracking_data0[fid][tid0]['foot_world']
                    matching_data[fid]["world_sv3dt_B"][tid1] = tracking_data1[fid][tid1]['foot_world']
                    if config['use_smoothed_tracklets']:
                        matching_data[fid]["image_position_A"][tid0] = tracking_data0[fid][tid0]['foot_image_smooth']
                        matching_data[fid]["image_position_B"][tid1] = tracking_data1[fid][tid1]['foot_image_smooth']
                    else:
                        matching_data[fid]["image_position_A"][tid0] = tracking_data0[fid][tid0]['foot_image']
                        matching_data[fid]["image_position_B"][tid1] = tracking_data1[fid][tid1]['foot_image']
        match_points_2d_all_frames.append(match_points_2d)
    return matches, match_points_2d_all_frames, matching_data

   
def remove_outliers_from_matching_data(matching_data:list, matches_outliers:list):
    """ Remove outliers from matching data
    Args:
        matching_data(list): list of dicts, each dict contains the matching data 
                            for a frame
        matches_outliers(list): list of tuples, each tuple contains two track ids

    Returns:
        matching_data_filtered(list): list of dicts, each dict contains the matching data 
                            for a frame
    """
    matching_data_filtered = []
    for fid in range(len(matching_data)):
        new_data = {"time": fid, "id_map_A2B": {}, "id_map_B2A": {}, 
                    "world_sv3dt_A": {}, "world_sv3dt_B": {},
                    "world_cam_A": {},
                    "image_position_A": {}, "image_position_B": {}} 
        for tid1 in matching_data[fid]["id_map_A2B"].keys():
            tid2 = matching_data[fid]["id_map_A2B"][tid1]
            if (tid1, tid2) in matches_outliers:
                continue
            new_data["id_map_A2B"][tid1] = tid2
            new_data["id_map_B2A"][tid2] = tid1
            new_data["world_sv3dt_A"][tid1] = matching_data[fid]["world_sv3dt_A"][tid1]
            new_data["world_sv3dt_B"][tid2] = matching_data[fid]["world_sv3dt_B"][tid2]
            new_data["image_position_A"][tid1] = matching_data[fid]["image_position_A"][tid1]
            new_data["image_position_B"][tid2] = matching_data[fid]["image_position_B"][tid2]
        matching_data_filtered.append(new_data)
    return matching_data_filtered

def two_view_calib(config:dict, cam_id1:int, cam_id2:int, 
                   cam_param_dir1:str, cam_param_dir2:str, 
                   tracklet_to_global:dict, out_dir:str,
                   output_base_dir:str, debug=False):
    """ Two view calibration main function

    Args:
        config(dict): config dictionary
        cam_id1 (int): camera id 1
        cam_id2 (int): camera id 2
        cam_param_dir1 (str): path to the camera parameter directory 1
        cam_param_dir2 (str): path to the camera parameter directory 2
        out_dir (str): path to the output directory
    
    Returns:
        None
    """
    cfg_robust_two_view_calib = config.get('robust_two_view_calib', {})
    robust_two_view_calib_enabled = cfg_robust_two_view_calib.get('enabled', False)
    # Select which camera pair to use
    cam_file1 = f'{cam_param_dir1}/camInfo_hyper_00_opencv.yaml'
    cam_file2 = f'{cam_param_dir2}/camInfo_hyper_00_opencv.yaml'
    tracking_file1 = f'{cam_param_dir1}/trajDump_Stream_0_3d.txt'
    tracking_file2 = f'{cam_param_dir2}/trajDump_Stream_0_3d.txt'
    video_path1 = f'{cam_param_dir1}/rectified.mp4'
    video_path2 = f'{cam_param_dir2}/rectified.mp4'

    cam_data1 = CameraData(config, cam_file1, tracking_file1, cam_id1)
    cam_data2 = CameraData(config, cam_file2, tracking_file2, cam_id2)

    matches, matching_points_2d, matching_data = \
        get_matching_tracklets(config, cam_id1, cam_id2, 
                                      cam_data1.tracking_data, 
                                      cam_data2.tracking_data, output_base_dir)
    logging.debug(f"matches: {matches}")

    # --- Step 2:  estimate R, t using 2D matching points ---
    if robust_two_view_calib_enabled:
        logging.info("--- Method 3: estimate R, t using 2D matching points (robust method) ---")
        res = calibrate_2_cams_robust(video_path1, video_path2, 
                                      cam_data1, cam_data2, 
                                      matching_points_2d, out_dir, 
                                      matches, matching_data, debug)
        points3D = res['points3D']
        R_est = res['R']
        t_est = res['t']
        P1 = res['P1']
        P2 = res['P2']
        pts3D_SV3DT = res['pts3_sv3dt']
        matches_filtered = res['matches_filtered']
        matches_outliers = res['matches_outliers']
        
        # remove outliers from matching data
        matching_data_filtered = remove_outliers_from_matching_data(matching_data, matches_outliers)
    else:
        logging.info("--- Method 3: estimate R, t using 2D matching points (standard method) ---")
        points3D, R_est, t_est, P1, P2 = calibrate_2_cams_method3(video_path1, video_path2, cam_data1, cam_data2, 
                             matching_points_2d, out_dir, debug)

    logging.info(f"R: {R_est}, t: {t_est}")

    # --- Step 3: align the points using Umeyama algorithm in order to get scale ---
    # pts3D_SV3DT: 3D points in the first camera from SV3DT
    # pts3D_recon: reconstructed 3D points in the first camera coordinate ([I|0])
    #pts3D_recon = []
    pts3D_recon = points3D
    if not robust_two_view_calib_enabled:
        pts3D_SV3DT = []
        for data in matching_data:
            for tid, pt in data["world_sv3dt_A"].items():
                pts3D_SV3DT.append([pt[0], pt[1], 0.0])

    if len(pts3D_SV3DT) != len(pts3D_recon):
        print(f"Error: pts3D_SV3DT and pts3D_recon have different length")
        return

    # Use robust scale estimation if enabled
    # config parameter is already the configuration dictionary
    robust_config = config.get('robust_scale_estimation', {})
    
    if robust_config.get('enabled', True):
        logging.info("Aligning points using Robust Umeyama algorithm (RANSAC)")
        scale, R_align, t_align, inliers, n_inliers = robust_umeyama_alignment(
            pts3D_SV3DT, pts3D_recon,
            max_iterations=robust_config.get('max_iterations', 1000),
            inlier_threshold=robust_config.get('inlier_threshold', 0.5),
            min_inliers=robust_config.get('min_inliers', 0.6),
            min_samples=robust_config.get('min_samples', 3),
            random_seed=robust_config.get('random_seed', 42)
        )
        logging.info(f"Robust alignment: scale={scale:.3f}, inliers={n_inliers}/{len(pts3D_SV3DT)} ({n_inliers/len(pts3D_SV3DT):.1%})")
        
        # Log outliers for debugging
        if n_inliers < len(pts3D_SV3DT):
            outlier_indices = np.where(~inliers)[0]
            logging.info(f"Outlier point indices: {outlier_indices.tolist()}")
    else:
        logging.info("Aligning points using standard Umeyama algorithm")
        scale, R_align, t_align = umeyama_alignment(pts3D_SV3DT, pts3D_recon)
    logging.info(f"Final alignment: scale={scale:.3f}, R_align shape={R_align.shape}, t_align shape={t_align.shape}")

    # visualize the matching tracklets
    if debug:
        visualize_matches(cam_id1, cam_id2, video_path1, video_path2, 
                          cam_data1, cam_data2, matches, 
                          R_est, t_est, tracklet_to_global, 
                          f'{out_dir}/matching_tracklets_all.mp4')

    # align the 3D points using scale and rotation
    pts3D_recon_aligned = (scale * R_align @ pts3D_recon.T).T + t_align

    # --- Step 4: visualize the results ---
    # Pass inlier information if available
    inlier_info = None
    if robust_config.get('enabled', True) and 'inliers' in locals():
        inlier_info = inliers
    plot_results(pts3D_SV3DT, pts3D_recon, pts3D_recon_aligned, R_est, t_est, 
                 scale, out_dir, debug, inlier_info)
    # plot 3D points in SV3DT
    plot_3d_points_for_2_cams(matching_data_filtered, 
                              f'{out_dir}/3d_points_in_sv3dt.png', debug)
    convert_sv3dt_to_cam0(cam_data1, cam_data2, matching_data_filtered, 
                          R_est, t_est * scale)

    # save the results
    save_calib_results(cam_data1, cam_data2, R_est, t_est, scale, 
                       matching_data_filtered, out_dir)

    return matches, matching_data_filtered

def plot_results(pts3D_SV3DT, pts3D_recon, pts3D_recon_aligned, R_est, t_est, 
                 scale, out_dir, debug, inlier_mask=None):
    """ Plot the results of the multi-view calibration
    Args:
        pts3D_SV3DT (list): list of 3D points in the SV3DT coordinate
        pts3D_recon (list): list of 3D points in the first camera coordinate
        pts3D_recon_aligned (list): list of 3D points in the first camera coordinate after alignment
        R_est (np.ndarray): rotation matrix of the estimated transformation
        t_est (np.ndarray): translation vector of the estimated transformation
        scale (float): scale factor of the estimated transformation
        out_dir (str): output directory for the plot
        debug (bool): debug mode that shows the intermediate results
        inlier_mask (np.ndarray): boolean mask indicating inlier points (for robust alignment)
    Returns:
        None
    """
    # Plotting
    fig = plt.figure(figsize=(15, 5))
    ax = fig.add_subplot(121, projection='3d')
    pts3D_SV3DT = np.asarray(pts3D_SV3DT)
    pts3D_recon = np.asarray(pts3D_recon)

    # Get the range of points in each dimension
    all_points = np.vstack([pts3D_SV3DT, pts3D_recon, pts3D_recon_aligned])
    max_range = np.max(all_points.max(axis=0) - all_points.min(axis=0))

    # Set equal aspect ratio for all axes
    mid_x = np.mean([all_points[:,0].min(), all_points[:,0].max()])
    mid_y = np.mean([all_points[:,1].min(), all_points[:,1].max()]) 
    mid_z = np.mean([all_points[:,2].min(), all_points[:,2].max()])

    ax.set_xlim(mid_x - max_range/2, mid_x + max_range/2)
    ax.set_ylim(mid_y - max_range/2, mid_y + max_range/2)
    ax.set_zlim(mid_z - max_range/2, mid_z + max_range/2)

    # Color points based on inlier/outlier status if available
    if inlier_mask is not None:
        inlier_indices = np.where(inlier_mask)[0]
        outlier_indices = np.where(~inlier_mask)[0]
        
        # Plot inliers
        if len(inlier_indices) > 0:
            ax.scatter(*pts3D_SV3DT[inlier_indices].T, label='True Points (Inliers)', 
                      c='blue', s=50, marker='.')
            ax.scatter(*pts3D_recon[inlier_indices].T, label='Reconstructed (Inliers)', 
                      c='red', s=50, marker='.')
            ax.scatter(*pts3D_recon_aligned[inlier_indices].T, label='Aligned (Inliers)', 
                      c='green', s=50, marker='.')
        
        # Plot outliers
        if len(outlier_indices) > 0:
            ax.scatter(*pts3D_SV3DT[outlier_indices].T, label='True Points (Outliers)', 
                      c='lightblue', s=50, marker='x')
            ax.scatter(*pts3D_recon[outlier_indices].T, label='Reconstructed (Outliers)', 
                      c='pink', s=50, marker='x')
            ax.scatter(*pts3D_recon_aligned[outlier_indices].T, label='Aligned (Outliers)', 
                      c='lightgreen', s=50, marker='x')
    else:
        # Standard visualization without inlier/outlier distinction
        ax.scatter(*pts3D_SV3DT.T, label='True Points', c='blue', s=50, marker='.')
        ax.scatter(*pts3D_recon.T, label='Reconstructed (Unscaled)', c='red', s=50, 
                   marker='.')
        ax.scatter(*pts3D_recon_aligned.T, label='Reconstructed (Aligned)', 
                   c='green', s=50, marker='.')
    # Draw first points
    ax.scatter(pts3D_SV3DT[0][0], pts3D_SV3DT[0][1], pts3D_SV3DT[0][2], 
               label='First Point', c='orange', s=400, marker='*')
    ax.scatter(pts3D_recon[0][0], pts3D_recon[0][1], pts3D_recon[0][2], 
               c='orange', s=400, marker='*')
    ax.scatter(pts3D_recon_aligned[0][0], pts3D_recon_aligned[0][1], 
               pts3D_recon_aligned[0][2], c='orange', s=400, marker='*')

    # Draw last points  
    ax.scatter(pts3D_SV3DT[-1][0], pts3D_SV3DT[-1][1], pts3D_SV3DT[-1][2], 
               label='Last Point', c='yellow', s=400, marker='o')
    ax.scatter(pts3D_recon[-1][0], pts3D_recon[-1][1], pts3D_recon[-1][2], 
               c='yellow', s=400, marker='o')
    ax.scatter(pts3D_recon_aligned[-1][0], pts3D_recon_aligned[-1][1], 
               pts3D_recon_aligned[-1][2], c='yellow', s=400, marker='o')
    ax.legend()

    # draw camera poses
    draw_camera(ax, np.eye(3), np.zeros((3,1)), 0.5, 'blue', 'cam_0')
    draw_camera(ax, R_est, t_est, 0.5, 'blue', 'cam_1')

    ax.set_title("3D Point Alignment with Scale and Rotation")

    ax = fig.add_subplot(122, projection='3d')
    # draw camera poses
    ax.scatter(*pts3D_recon.T * scale, label='Reconstructed (scaled)', 
               c='red', s=50, marker='.')
    draw_camera(ax, np.eye(3), np.zeros((3,1)), 0.5, 'blue', 'cam_0')
    draw_camera(ax, R_est, t_est*scale, 0.5, 'blue', 'cam_1')
    if debug:
        plt.show()
    # save the plot
    plt.tight_layout()
    fig.savefig(f'{out_dir}/3d_point_alignment.png')
    #plt.close()

def plot_3d_points_for_2_cams(matching_data, out_path:str, debug):
    """ Plot 3D points from two cameras and save the plot to the given path """
    pts3d_1 = []
    pts3d_2 = []
    for data in matching_data:
        for id, pt in data["world_sv3dt_A"].items():
            pts3d_1.append([pt[0], pt[1], 0])
        for id, pt in data["world_sv3dt_B"].items():
            pts3d_2.append([pt[0], pt[1], 0])

    # Plotting
    fig = plt.figure(figsize=(15,5))
    ax = fig.add_subplot(121, projection='3d')
    pts3d_1 = np.asarray(pts3d_1)
    pts3d_2 = np.asarray(pts3d_2)

    # Get the range of points in each dimension
    all_points = pts3d_1
    max_range = np.max(all_points.max(axis=0) - all_points.min(axis=0))

    # Set equal aspect ratio for all axes
    mid_x = np.mean([all_points[:,0].min(), all_points[:,0].max()])
    mid_y = np.mean([all_points[:,1].min(), all_points[:,1].max()]) 
    mid_z = np.mean([all_points[:,2].min(), all_points[:,2].max()])

    ax.set_xlim(mid_x - max_range/2, mid_x + max_range/2)
    ax.set_ylim(mid_y - max_range/2, mid_y + max_range/2)
    ax.set_zlim(mid_z - max_range/2, mid_z + max_range/2)

    ax.scatter(*pts3d_1.T, label='True Points', c='blue', s=50, marker='.')
    # Draw first points
    ax.scatter(pts3d_1[0][0], pts3d_1[0][1], pts3d_1[0][2], label='First Point', 
               c='orange', s=400, marker='*')
    # Draw last points  
    ax.scatter(pts3d_1[-1][0], pts3d_1[-1][1], pts3d_1[-1][2], label='Last Point', 
               c='yellow', s=400, marker='o')
    ax.legend()
    ax.set_title("3D Point from Camera 1")

    ax2 = fig.add_subplot(122, projection='3d')
    ax2.scatter(*pts3d_2.T, label='True Points', c='blue', s=50, marker='.')
    ax2.legend()
    ax2.set_title("3D Point from Camera 2")
    if debug:
        plt.show()
    # save the plot
    plt.tight_layout()
    fig.savefig(out_path)

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

def read_data(config:dict, input_base_dir:str):
    """
    Read data from single view calibration output directories

    Args:
        config(dict): config dictionary
        input_base_dir(str): base directory for single view calibration outputs
    Returns:
        cam_data_dict(dict): dictionary mapping camera IDs to CameraData objects
    """
    
    sv_calib_dirs = get_single_view_calib_dirs(config, input_base_dir)
    cam_dirs = config['cam_dir']
    cam_data_dict = {}
    
    for sv_calib_dir, cam_dir in zip(sv_calib_dirs, cam_dirs):
        cam_id = extract_cam_number(cam_dir)
        cam_file = f'{sv_calib_dir}/camInfo_hyper_00_opencv.yaml'
        tracking_file = f'{sv_calib_dir}/trajDump_Stream_0_3d.txt'
        cam_data = CameraData(config, cam_file, tracking_file, cam_id)
        cam_data_dict[cam_id] = cam_data
    return cam_data_dict

def main(config:dict, debug=False, input_base_dir:str=None, output_base_dir:str=None):
    """
    Main function for multi-view calibration
    Args:
        config(dict): config dictionary
        debug(bool): debug mode that shows the intermediate results
        input_base_dir(str): base directory for single view calibration outputs
    Returns:
        None
    """
    # parameters
    # Validate and sanitize directory paths
    if input_base_dir:
        input_base_dir = validate_input_directory(input_base_dir, "input base directory")
    if output_base_dir:
        output_base_dir = validate_output_directory(output_base_dir, "output base directory")
    
    # single view calibration output
    sv_calib_dirs_list = get_single_view_calib_dirs(config, input_base_dir)
    # Create mapping from camera ID to directory path
    cam_dirs = config['cam_dir']
    sv_calib_dirs = {}
    for sv_calib_dir, cam_dir in zip(sv_calib_dirs_list, cam_dirs):
        cam_id = extract_cam_number(cam_dir)
        sv_calib_dirs[cam_id] = sv_calib_dir
    
    # output directory for multi-view calibration results
    out_base_dir = output_base_dir
    camera_pairs = config['camera_pairs']

    # --- Step 1: read single view calib output ---
    logging.info('-'*100)
    logging.info('Step 1: read single view calib output')
    logging.info('-'*100)
    cam_data_dict = read_data(config, input_base_dir)

    # --- Step 2: tracklet matching ---
    logging.info('-'*100)
    logging.info('Step 2: tracklet matching')
    logging.info('-'*100)
    sorted_tracklet_matches = []
    for pair in camera_pairs:
        cam_id1, cam_id2 = pair 
        logging.info(f"Processing camera pair: {pair}")
        cam_data1 = cam_data_dict[cam_id1]
        cam_data2 = cam_data_dict[cam_id2]
        out_dir_pair = f'{out_base_dir}/cam{cam_id1}_cam{cam_id2}'
        out_dir_pair = validate_output_directory(out_dir_pair, f"camera pair {cam_id1}_{cam_id2} output directory")
        sorted_scored_dict = tracklet_matching(config, cam_data1, cam_data2, out_dir_pair)
        sorted_tracklet_matches.append(sorted_scored_dict)
    logging.debug(f"Sorted tracklet matches: {sorted_tracklet_matches}")

    # save the sorted tracklet matches to JSON file
    with open(f'{out_base_dir}/sorted_tracklet_matches.json', 'w') as f:
        json.dump(sorted_tracklet_matches, f, indent=4)

    # find a mapping from tracklet id to global tracklet id
    tracklet_to_global = get_global_tracklet_id(f'{out_base_dir}/sorted_tracklet_matches.json',
                                                camera_pairs)
    logging.debug('Tracklet to global mapping: %s', tracklet_to_global)

    # Convert tuple keys to strings for JSON serialization
    serializable_dict = {str(k): v for k, v in tracklet_to_global.items()}
    
    # Save tracklet_to_global mapping to a JSON file
    output_file = f"{out_base_dir}/tracklet_to_global.json"
    with open(output_file, "w") as f:
        json.dump(serializable_dict, f, indent=4)
    logging.info("Saved tracklet to global ID mapping to %s", output_file)

    # --- Step 3: multi-view calibration ---
    logging.info('-'*100)
    logging.info('Step 3: multi-view calibration')
    logging.info('-'*100)
    matches_list = []
    matching_data_list = []
    for pair in camera_pairs:
        logging.info('='*100)
        logging.info(f'Processing camera pair: {pair}')
        cam_id1, cam_id2 = pair 
        sv_calib_dir1 = sv_calib_dirs[cam_id1]
        sv_calib_dir2 = sv_calib_dirs[cam_id2]
        out_dir = f'{out_base_dir}/cam{cam_id1}_cam{cam_id2}'
        out_dir = validate_output_directory(out_dir, f"camera pair {cam_id1}_{cam_id2} output directory")
        matches, matching_data = two_view_calib(config, cam_id1, cam_id2, 
                       sv_calib_dir1, sv_calib_dir2, tracklet_to_global, 
                       out_dir, output_base_dir, debug)
        matches_list.append(matches)
        matching_data_list.append(matching_data)

def dtw_test(config:dict, tid1:int):
    """
    Unit test for dtw distance
    This function is used to show the dtw distances between tracklet (tid1) in 
    camera 1 and all other tracklets in camera 2.

    Args:
        config(dict): config dictionary
        tid1(int): tracklet id in cam_id1
    Returns:
        None
    """
    # single view calibration output - this test function needs a base directory
    # For testing purposes, you would need to provide a base directory
    raise NotImplementedError("dtw_test function requires input_base_dir parameter to be added")

    camera_pairs = [(2, 3)] 
    cam_id1, cam_id2 = camera_pairs[0]
    cam_file1 = f'{sv_calib_dirs[cam_id1]}/camInfo_hyper_00_opencv.yaml'
    cam_file2 = f'{sv_calib_dirs[cam_id2]}/camInfo_hyper_00_opencv.yaml'
    tracking_file1 = f'{sv_calib_dirs[cam_id1]}/trajDump_Stream_0_3d.txt'
    tracking_file2 = f'{sv_calib_dirs[cam_id2]}/trajDump_Stream_0_3d.txt'
    config_file = None
    cam_data1 = CameraData(config_file, cam_file1, tracking_file1, cam_id1)
    cam_data2 = CameraData(config_file, cam_file2, tracking_file2, cam_id2)

    # read tracking data
    tracking_data1 = cam_data1.tracking_data
    tracking_data2 = cam_data2.tracking_data
    track_ids2 = cam_data2.track_ids

    def get_tracklets(tid1, tid2, tracking_data1, tracking_data2):
        tracklet1 = []
        tracklet2 = []
        for fid in range(len(tracking_data1)):
            data1 = tracking_data1[fid]
            data2 = tracking_data2[fid]
            if tid1 in data1.keys() and tid2 in data2.keys():
                tracklet1.append(data1[tid1]["foot_world"])
                tracklet2.append(data2[tid2]["foot_world"])
        return np.array(tracklet1), np.array(tracklet2)

    for tid2 in track_ids2:
        tracklet1, tracklet2 = get_tracklets(tid1, tid2, tracking_data1, tracking_data2)
        # compute DTW distance
        dtw_distance = dtw_ndim.distance(tracklet1, tracklet2)
        print(f"DTW distance between tracklet {tid1} and {tid2}: {dtw_distance}")

def test_tracklet_matching(config:dict, cam_id1:int, cam_id2:int):
    """
    Unit test for tracklet matching
    This function is used to test the tracklet matching between two cameras.
    """
    # single view calibration output
    sv_calib_dirs_list = get_single_view_calib_dirs(config)
    # Create mapping from camera ID to directory path
    cam_dirs = config['cam_dir']
    sv_calib_dirs = {}
    for sv_calib_dir, cam_dir in zip(sv_calib_dirs_list, cam_dirs):
        cam_id = extract_cam_number(cam_dir)
        sv_calib_dirs[cam_id] = sv_calib_dir

    cam_file1 = f'{sv_calib_dirs[cam_id1]}/camInfo_hyper_00_opencv.yaml'
    cam_file2 = f'{sv_calib_dirs[cam_id2]}/camInfo_hyper_00_opencv.yaml'
    tracking_file1 = f'{sv_calib_dirs[cam_id1]}/trajDump_Stream_0_3d.txt'
    tracking_file2 = f'{sv_calib_dirs[cam_id2]}/trajDump_Stream_0_3d.txt'
    config_file = None
    cam_data1 = CameraData(config_file, cam_file1, tracking_file1, cam_id1)
    cam_data2 = CameraData(config_file, cam_file2, tracking_file2, cam_id2)

    # read tracking data
    tracking_data1 = cam_data1.tracking_data
    tracking_data2 = cam_data2.tracking_data
    track_ids1 = cam_data1.track_ids
    track_ids2 = cam_data2.track_ids

    test_candidate_matching(track_ids1, track_ids2, tracking_data1, tracking_data2)

    sorted_scored_dict = find_initial_matching(track_ids1, track_ids2, tracking_data1, tracking_data2)
    return sorted_scored_dict[:10]

if __name__ == "__main__":
    args = parse_args()

    # read config file
    config.load_config(args.config)
    cfg = config.get_config()

    # Call this function before your logging statements
    setup_logging(cfg['logging_level'])

    logging.info(f"cfg: {cfg}")

    # unit tests    
    #unit_test()
    #dtw_test(cfg, 6) # unit test for dtw distance
    #test_tracklet_matching(cfg, 0, 1)

    # main function
    main(cfg, args.debug, args.input_base_dir, args.output_base_dir)
