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
import sys
import logging
import json
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from dtaidistance import dtw_ndim
import pickle
from scipy.optimize import linear_sum_assignment

# Add the current directory to Python path to allow importing local modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(project_root)

from core.utils.path_validation import validate_input_file
import config
from camera_data import CameraData
from math_utils import umeyama_alignment, apply_umeyama_transform

#def setup_logging():
#    logging.basicConfig(level=logging.INFO)
#    logger = logging.getLogger(__name__)
#    return logger
#
#setup_logging()

# Camera 0 tracklets
# Format: (camera_id, tracklet_id)
cam0 = [(0, 1), (0, 2), (0, 3)]
# Camera 1 tracklets
cam1 = [(1, 101), (1, 102), (1, 103), (1, 104)]
# Camera 2 tracklets
cam2 = [(2, 201), (2, 202), (2, 203), (2, 204), (2, 205)]
# Camera 3 tracklets
cam3 = [(3, 301), (3, 302), (3, 303), (3, 304), (3, 305)]

"""
    A program to register tracklets among cameras to global tracklets
    
    Input:
        tracklet matches among cameras
        Format: ((camera_id, tracklet_id), (camera_id, tracklet_id))
        tracklet_matches = [
            ((0, 1), (1, 101)),
            ((0, 3), (1, 102)),
            ((1, 101), (2, 202)),
            ((1, 102), (2, 203)),
            ((2, 201), (3, 301)),
            ((2, 203), (3, 305)),
        ]

    Output:
        tracklet_to_global = {
            (camera_id, local_track_id): global_track_id
        }
"""

class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x, y):
        self.parent[self.find(x)] = self.find(y)

def register_tracklets(tracklet_matches):
    # Initialize UnionFind data structure
    uf = UnionFind()
    
    # First pass: Create unions between matched tracklets
    for match in tracklet_matches:
        tracklet1, tracklet2 = match
        uf.union(tracklet1, tracklet2)

    # Second pass: Assign global IDs
    # Keep track of root -> global_id mapping
    root_to_global = {}
    next_global_id = 0
    
    tracklet_to_global = {}
    
    # Process all tracklets that appear in matches
    for match in tracklet_matches:
        for tracklet in match:
            if tracklet not in tracklet_to_global:
                root = uf.find(tracklet)
                if root not in root_to_global:
                    root_to_global[root] = next_global_id
                    next_global_id += 1
                tracklet_to_global[tracklet] = root_to_global[root]
    
    return tracklet_to_global

def test():
    tracklet_matches = [
        ((0, 1), (1, 101)),
        ((0, 3), (1, 102)),
        ((1, 101), (2, 202)),
        ((1, 102), (2, 203)),
        ((2, 201), (3, 301)),
        ((2, 203), (3, 305)),
    ]

    tracklet_to_global = register_tracklets(tracklet_matches)
    print(tracklet_to_global)

def get_global_tracklet_id(tracklet_match_path:str, camera_pairs:list):
    cfg = config.get_config()
    min_matched_tracklets = cfg['min_matched_tracklets']
    max_matched_tracklets_dist = cfg['max_matched_tracklets_dist']
    # from JSON file
    with open(tracklet_match_path, 'r') as f:
        sorted_scored_dict_list = json.load(f)
    multi_view_output = []
    for i in range(len(camera_pairs)):
        matches = []
        tid1_greedy_list = []
        for data in sorted_scored_dict_list[i]:
            #if data[1] >= min_matched_tracklets:
            if data[1] <= max_matched_tracklets_dist:
                if data[0][0] not in tid1_greedy_list:
                    matches.append(data[0])
                    tid1_greedy_list.append(data[0][0])
                else:
                    continue
            else:
                continue
        multi_view_output.append(matches)

    # convert multi_view_output to tracklet_matches
    tracklet_matches = []
    for i, (cam1, cam2) in enumerate(camera_pairs):
        for match in multi_view_output[i]:
            tracklet_matches.append(((cam1, match[0]), (cam2, match[1])))
    logging.info('tracklet_matches: %s', tracklet_matches)

    tracklet_to_global = register_tracklets(tracklet_matches)
    return tracklet_to_global

# def main_sdg_v3():
#     tracklet_to_global = get_global_tracklet_id(tracklet_match_path)
#     logging.info('Tracklet to global mapping: %s', tracklet_to_global)

#     # Convert tuple keys to strings for JSON serialization
#     serializable_dict = {str(k): v for k, v in tracklet_to_global.items()}
    
#     # Save tracklet_to_global mapping to a JSON file
#     output_file = "output/tracklet_to_global.json"
#     with open(output_file, "w") as f:
#         json.dump(serializable_dict, f, indent=4)
#     logging.info("Saved tracklet to global ID mapping to %s", output_file)

# Add this new function to load the JSON file and convert back to tuples
def load_tracklet_mapping(filename="output/tracklet_to_global.json"):
    # Validate the file path to prevent path traversal attacks
    filename = validate_input_file(filename, "tracklet mapping file")
    
    with open(filename, "r") as f:
        serialized_dict = json.load(f)
    
    # Validate that the loaded data is a dictionary
    if not isinstance(serialized_dict, dict):
        raise ValueError(f"Invalid tracklet mapping file format: expected dictionary, got {type(serialized_dict)}")
    
    # Safely convert string keys back to tuples without using eval
    result = {}
    for k, v in serialized_dict.items():
        try:
            # Parse tuple string safely - expect format like "(1, 2)"
            if k.startswith('(') and k.endswith(')'):
                # Remove parentheses and split by comma
                inner = k[1:-1].strip()
                if inner:  # Handle non-empty tuples
                    # Split by comma and convert each element to int
                    parts = [part.strip() for part in inner.split(',')]
                    tuple_key = tuple(int(part) for part in parts)
                else:  # Handle empty tuple
                    tuple_key = ()
            else:
                # If not in expected tuple format, skip this entry with warning
                print(f"Warning: Skipping malformed key '{k}' in tracklet mapping")
                continue
            result[tuple_key] = v
        except (ValueError, TypeError) as e:
            # If conversion fails, skip this entry with warning
            print(f"Warning: Failed to parse key '{k}' in tracklet mapping: {e}")
            continue
    return result

def filter_tracklet(cfg:dict, track_ids:list, tracking_data:dict):
    """ Filter out tracklets by the following rules
    
    1. Tracklet length is less than a threshold
    2. The moving distance is too small

    Args:
        cfg: config dictionary
        tracklet_list: list of tracklets
        tracking_data: tracking data
    Returns:
        filtered_tracklet_list: list of filtered tracklets
    """
    min_length = cfg['tracklet_filter']['min_length'] # minimum length of the tracklet
    min_moving_dist = cfg['tracklet_filter']['min_moving_dist'] # minimum moving distance of the tracklet
    min_mean_dist = cfg['tracklet_filter']['min_mean_moving_dist'] # mean distance threshold to remove long-standing objects

    track_length = {}
    track_moving_distance = {}
    track_current_loc = {}
    for frame in tracking_data:
        for tid in frame.keys():
            if tid not in track_length:
                track_length[tid] = 1
                track_moving_distance[tid] = 0
                track_current_loc[tid] = frame[tid]["foot_world"]
            else:
                track_length[tid] += 1
                track_moving_distance[tid] += np.linalg.norm(np.array(frame[tid]["foot_world"]) - np.array(track_current_loc[tid]))
                track_current_loc[tid] = frame[tid]["foot_world"]

    filtered_tracklet_list = []

    for tid in track_ids:
        if track_length[tid] < min_length:
            continue
        if track_moving_distance[tid] < min_moving_dist:
            continue
        mean_dist = float(track_moving_distance[tid]) / float(track_length[tid])
        if mean_dist < min_mean_dist: # remove tracklets with very small moving distance 
            continue
        filtered_tracklet_list.append(tid)

    return filtered_tracklet_list

def find_matching_points_in_frame(track_id1, track_id2, tracking_data1, tracking_data2, 
                         matching_points_2d=None, matching_points_3d_frame=None,
                         use_smoothed_tracklets=False):
    """
    Find matching points between two cameras' tracklets

    Args:
        track_id1: track id in camera 1
        track_id2: track id in camera 2
        tracking_data1: tracking data in camera 1
        tracking_data2: tracking data in camera 2
        matching_points_2d: matching points in 2D
        matching_points_3d_frame: matching points in 3D
        use_smoothed_tracklets: whether to use smoothed tracklets

    Returns:
        matching_points_3d_1(np.ndarray): matching points in 3D for cam 1
        matching_points_3d_2(np.ndarray): matching points in 3D for cam 2
        match_per_frame: matching points in each frame
    """
    # find matching points between two cameras' tracklets
    # return a list of matching points
    cfg = config.get_config()
    video_resolution = cfg['video_resolution']
    image_bottom_boundary = cfg['image_bottom_boundary']
    image_side_boundary = cfg['image_side_boundary']
    max_y = video_resolution[1] * image_bottom_boundary
    min_x = video_resolution[0] * image_side_boundary
    max_x = video_resolution[0] * (1 - image_side_boundary)
    data1 = tracking_data1
    data2 = tracking_data2
    n_frame1 = len(data1)
    n_frame2 = len(data2)
    matching_points_3d_1 = []
    matching_points_3d_2 = []
    match_per_frame = []
    for fid in range(min(n_frame1, n_frame2)):
        if track_id1 in data1[fid].keys() and track_id2 in data2[fid].keys():
            foot_1 = data1[fid][track_id1]['foot_image'] 
            foot_2 = data2[fid][track_id2]['foot_image']
            if foot_1[0] < min_x or foot_1[0] > max_x or foot_2[0] < min_x or foot_2[0] > max_x:
                continue
            if foot_1[1] > max_y or foot_2[1] > max_y:
                continue
            foot_w_1 = data1[fid][track_id1]['foot_world']
            foot_w_2 = data2[fid][track_id2]['foot_world']
            matching_points_3d_1.append([foot_w_1[0], foot_w_1[1], 0.0])
            matching_points_3d_2.append([foot_w_2[0], foot_w_2[1], 0.0])
            if use_smoothed_tracklets:
                image_position_A = data1[fid][track_id1]['foot_image_smooth']
                image_position_B = data2[fid][track_id2]['foot_image_smooth']
            else:
                image_position_A = data1[fid][track_id1]['foot_image']
                image_position_B = data2[fid][track_id2]['foot_image']
            match_per_frame.append({"fid": fid, 
                                    "id_A": track_id1, 
                                    "id_B": track_id2, 
                                    "world_position_A": data1[fid][track_id1]['foot_world'], 
                                    "world_position_B": data2[fid][track_id2]['foot_world'], 
                                    "image_position_A": image_position_A, 
                                    "image_position_B": image_position_B})
    matching_points_3d_1 = np.array(matching_points_3d_1, dtype=np.float64)
    matching_points_3d_2 = np.array(matching_points_3d_2, dtype=np.float64)
    return matching_points_3d_1, matching_points_3d_2, match_per_frame

def get_all_points(tracking_data:list, track_ids:list): 
    """ Get all points from tracking data for the given track ids
    Args:
        tracking_data (list): list of tracking data
        track_ids (list): list of track ids
    Returns:
        all_points (np.ndarray): array of points
    """
    data = tracking_data
    n_frame = len(data)
    all_points = []
    for fid in range(n_frame):
        for tid in track_ids:
            if tid not in data[fid].keys():
                continue
            foot_1 = data[fid][tid]['foot_image'] 
            if foot_1[0] > 1920 * 0.9 or foot_1[1] > 1080 * 0.9:
                continue
            all_points.append([data[fid][tid]['foot_world'][0], 
                               data[fid][tid]['foot_world'][1], 
                               0.0]) 
    return np.array(all_points)


def estimate_transform(matching_points, use_head_points=False):
    # Estimate rotation R and translation t such that Q ≈ R @ P + t
    # P and Q are Nx3 numpy arrays of corresponding 3D points
    # matching_points: list of tuples, each tuple contains two points
    # each point is a tuple of (x, y)
    # estimate transform from cam 1 to cam 2
    # return R, t
    # Convert matching points to numpy arrays
    height = 1.75 # unit: meter
    pts1 = np.array([[p[0][0], p[0][1], 0] for p in matching_points])
    if use_head_points:
        pts1 = np.concatenate((pts1, np.array([[p[0][0], p[0][1], height] 
                                               for p in matching_points])))
    pts2 = np.array([[p[1][0], p[1][1], 0] for p in matching_points])
    if use_head_points:
        pts2 = np.concatenate((pts2, np.array([[p[1][0], p[1][1], height] 
                                               for p in matching_points])))

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

def transform_points(points, R, t):
    return np.dot(R, points.T).T + t


# Umeyama algorithm functions moved to core.utils.math_utils

def test_candidate_matching(track_ids1:list, track_ids2:list, 
                  tracking_data1:list, tracking_data2:list):
    """ Test function for matching tracklets

    - filter trackelt by length and moving distance
    - estimate transform R, t using one candidate pair of tracklets
    - transform tracklet1 to tracklet2 using the estimated transform
    - plot tracklet1 and tracklet2
    - plot all points in tracklet1, tracklet2, and the transformed tracklet1

    Args:
        track_ids1: list of track ids in camera 1
        track_ids2: list of track ids in camera 2
        tracking_data1: list of tracking data in camera 1
        tracking_data2: list of tracking data in camera 2
    """
    alignment_method = "umeyama"

    # filter tracklets by length and moving distance
    filtered_tracklet1 = filter_tracklet(track_ids1, tracking_data1)
    filtered_tracklet2 = filter_tracklet(track_ids2, tracking_data2)
    print(f"Filtered tracklet1: {filtered_tracklet1}")
    print(f"Filtered tracklet2: {filtered_tracklet2}")

    # candidate pair of tracklets
    tid1 = 9
    tid2 = 12 

    # find matching points in each frame
    matching_pts3d, match_per_frame = find_matching_points_in_frame(tid1, tid2, 
                                                tracking_data1, tracking_data2)

    # estimate transform R, t
    if alignment_method == "umeyama":
        pts3d_1 = matching_pts3d[0]
        pts3d_2 = matching_pts3d[1]
        scale, R, t = umeyama_alignment(pts3d_2, pts3d_1)
        print(f"scale: {scale}, R: {R}, t: {t}")
    else:
        R, t = estimate_transform(matching_pts3d, use_head_points=True)
        print(f"R: {R}, t: {t}")

    # transform tracklet1 to tracklet2
    tracklet1_orig = matching_pts3d[0]
    tracklet2_orig = matching_pts3d[1]
    if alignment_method == "umeyama":
        tracklet1_transformed = apply_umeyama_transform(tracklet1_orig, scale, R, t)
    else:
        tracklet1_transformed = transform_points(tracklet1_orig, R, t)
    
    score = get_score_candidate_matching(filtered_tracklet1, filtered_tracklet2, 
                                 tracking_data1, tracking_data2, 
                                 scale, R, t, min_matching_points=100)
    print(f"score: {score}")

    # plot tracklet1 and tracklet2
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')
    ax.scatter(tracklet1_orig[:, 0], tracklet1_orig[:, 1], tracklet1_orig[:, 2], 
               c='r', marker='.', label='tracklet1_orig')
    ax.scatter(tracklet2_orig[:, 0], tracklet2_orig[:, 1], tracklet2_orig[:, 2], 
               c='b', marker='.', label='tracklet2_orig')
    ax.scatter(tracklet1_transformed[:, 0], 
               tracklet1_transformed[:, 1], 
               tracklet1_transformed[:, 2], c='g', marker='.', label='tracklet1_transformed')
    ax.scatter(tracklet1_orig[0, 0], tracklet1_orig[0, 1], tracklet1_orig[0, 2], 
               c='y', marker='o', label='start point', s=100)
    ax.scatter(tracklet1_orig[-1, 0], tracklet1_orig[-1, 1], tracklet1_orig[-1, 2], 
               c='c', marker='o', label='end point', s=100)
    ax.scatter(tracklet2_orig[0, 0], tracklet2_orig[0, 1], tracklet2_orig[0, 2], 
               c='y', marker='o', label='start point', s=100)
    ax.scatter(tracklet2_orig[-1, 0], tracklet2_orig[-1, 1], tracklet2_orig[-1, 2], 
               c='c', marker='o', label='end point', s=100)
    ax.scatter(tracklet1_transformed[0, 0], tracklet1_transformed[0, 1], tracklet1_transformed[0, 2], 
               c='y', marker='o', label='start point', s=100)
    ax.scatter(tracklet1_transformed[-1, 0], tracklet1_transformed[-1, 1], tracklet1_transformed[-1, 2], 
               c='c', marker='o', label='end point', s=100)
    ax.set_zlim(-1, 1)
    ax.legend()
    plt.show()

    all_points1 = get_all_points(tracking_data1, filtered_tracklet1)
    all_points2 = get_all_points(tracking_data2, filtered_tracklet2)
    if alignment_method == "umeyama":
        all_points1_transformed = apply_umeyama_transform(all_points1, scale, R, t)
    else:
        all_points1_transformed = transform_points(all_points1, R, t)

    # plot tracklet1 and tracklet2
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')
    ax.scatter(all_points1[:, 0], all_points1[:, 1], all_points1[:, 2], 
               c='r', marker='.', label='all_points1')
    ax.scatter(all_points2[:, 0], all_points2[:, 1], all_points2[:, 2], 
               c='b', marker='.', label='all_points2')
    ax.scatter(all_points1_transformed[:, 0], all_points1_transformed[:, 1], all_points1_transformed[:, 2], 
               c='g', marker='.', label='all_points1_transformed')
    ax.set_zlim(-1, 1)
    ax.legend()
    plt.show()

def get_distance_score(pts3d_1, pts3d_2):
    # get distance score between two tracklets
    # return a score
    dists = np.linalg.norm(pts3d_1 - pts3d_2, axis=1)
    return dists.mean()

def get_cosine_similarity_score(pts3d_1, pts3d_2):
    # get cosine similarity score between the velocity of two tracklets
    # return a score
    # score is the cosine similarity between the velocity of the two tracklets
    velocity_1 = pts3d_1[1:] - pts3d_1[:-1]
    velocity_2 = pts3d_2[1:] - pts3d_2[:-1]
    return np.dot(velocity_1, velocity_2) / (np.linalg.norm(velocity_1) * np.linalg.norm(velocity_2))

def get_dtw_score(pts3d_1, pts3d_2):
    # get dtw score between two tracklets
    # return a score
    # score is the dtw distance between the two tracklets
    dtw_distance = dtw_ndim.distance(pts3d_1, pts3d_2)
    return dtw_distance

def get_score_candidate_matching(track_ids1, track_ids2, 
                                 matching_pts3d_dict, 
                                 scale, R, t, min_matching_points=10):
    """ Get score for a candidate matching
    
    - use R, t to transform each point in tracklet1
    - calculate the score between the transformed tracklet1 and tracklet2
    - if the score is less than a threshold, count+=1

    Args:
        track_ids1: list of track ids in camera 1
        track_ids2: list of track ids in camera 2
        matching_pts3d_dict: dictionary of matching points in 3D for each tracklet pair
        R_cand: rotation matrix
        t_cand: translation vector

    Returns:
            score: score for the candidate matching
    """
    cfg = config.get_config()
    min_distance_score_threshold = cfg['min_distance_score_threshold']
    count = 0
    for tid1 in track_ids1:
        for tid2 in track_ids2:
            matching_pts3d_1, matching_pts3d_2 = matching_pts3d_dict[(tid1, tid2)]
            if matching_pts3d_1.shape[0] < min_matching_points:
                #print(f"not enough matching points: {len(matching_pts3d)}")
                continue
            
            pts3d_1 = matching_pts3d_1
            pts3d_2 = matching_pts3d_2
            pts3d_1_transformed = apply_umeyama_transform(pts3d_1, scale, R, t)
            score = get_distance_score(pts3d_1_transformed, pts3d_2)
            #print(f"tid1: {tid1}, tid2: {tid2}, score: {score}")
            #dtw_score = get_dtw_score(pts3d_1_transformed, pts3d_2)
            if score < min_distance_score_threshold: # and dtw_score < 15:
                count += 1 
    return count


def find_initial_matching(cfg:dict, track_ids1:list, track_ids2:list, 
                     tracking_data1:list, tracking_data2:list):
    """ Find initial matching for tracklets in two cameras

    Args:
        cfg: config dictionary
        track_ids1(list): list of track ids in camera 1
        track_ids2(list): list of track ids in camera 2
        tracking_data1(list): list of tracking data in camera 1
        tracking_data2(list): list of tracking data in camera 2

    Returns:
        sorted_scored_dict: list of tuples, each tuple contains (tracklet_id1, tracklet_id2, score)
        matching_pts3d_dict: dictionary of matching points in 3D for each tracklet pair
    """
    cfg = config.get_config()
    # parameters
    min_matching_points = cfg['min_matching_points']
    min_scale = cfg['min_scale']
    max_scale = cfg['max_scale']
    min_dtw_score_threshold = cfg['min_dtw_score_threshold']
    min_matched_tracklets = cfg['min_matched_tracklets']
    use_smoothed_tracklets = cfg['use_smoothed_tracklets']

    alignment_method = "umeyama"

    # filter tracklets by length and moving distance
    track_ids1 = filter_tracklet(cfg, track_ids1, tracking_data1)
    track_ids2 = filter_tracklet(cfg, track_ids2, tracking_data2)

    # get matching points for the matching tracklets 
    matching_pts3d_dict = {}
    for tid1 in track_ids1:
       for tid2 in track_ids2:
           #print(f"matching tracklet {tid1} and {tid2}")
            matching_pts3d_1, matching_pts3d_2, match_per_frame = find_matching_points_in_frame(tid1, tid2, 
                                                tracking_data1, tracking_data2,
                                                use_smoothed_tracklets=use_smoothed_tracklets)
            matching_pts3d_dict[(tid1, tid2)] = (matching_pts3d_1, matching_pts3d_2)
    
    # initial matching for all tracklets
    score_dict = {}
    for tid1 in track_ids1:
        for tid2 in track_ids2:
            #print(f"matching tracklet {tid1} and {tid2}")
            matching_pts3d = matching_pts3d_dict[(tid1, tid2)]

            if matching_pts3d[0].shape[0] < min_matching_points:
                #print(f"{tid1}-{tid2}: not enough matching points: {len(matching_pts3d)}. skip.")
                continue

            # estimate transform R, t
            if alignment_method == "umeyama":
                pts3d_1 = matching_pts3d[0]
                pts3d_2 = matching_pts3d[1]
                scale, R_cand, t_cand = umeyama_alignment(pts3d_2, pts3d_1)
                #print(f"scale: {scale}, R: {R_cand}, t: {t_cand}")
            else:
                R_cand, t_cand = estimate_transform(matching_pts3d, use_head_points=True)
                #print(f"R: {R_cand}, t: {t_cand}")
            if scale < min_scale or scale > max_scale:
                #print(f"{tid1}-{tid2}: scale: {scale}. skip.")
                continue

            pts3d_1_transformed = apply_umeyama_transform(pts3d_1, scale, R_cand, t_cand)
            dtw_score = get_dtw_score(pts3d_1_transformed, pts3d_2)
            if dtw_score > min_dtw_score_threshold:
                #print(f"{tid1}-{tid2}: dtw_score: {dtw_score}. skip.")
                continue

            score = get_score_candidate_matching(track_ids1, track_ids2, 
                                                 matching_pts3d_dict, 
                                                 scale, R_cand, t_cand)
            #print(f"score: {score}")
            #print(f"dtw_score: {dtw_score}")
            if score >= min_matched_tracklets:
                score_dict[(tid1, tid2)] = score 
    
    sorted_score_dict = sorted(score_dict.items(), key=lambda x: x[1], reverse=True)
    logging.debug(f"Initial matching. sorted_score_dict: {sorted_score_dict}")

    # Hungarian algorithm to find the best matching
    # Only process if we have valid score_dict entries
    if not score_dict:
        logging.error("No valid tracklet matches found")
        return sorted_score_dict
    """ 
    # Create a mapping from track IDs to matrix indices
    track_ids1_list = sorted(list(set([pair[0] for pair in score_dict.keys()])))
    track_ids2_list = sorted(list(set([pair[1] for pair in score_dict.keys()])))
    
    nA = len(track_ids1_list)
    nB = len(track_ids2_list)
    
    # Create cost matrix with finite values
    max_score = max(score_dict.values()) if score_dict else 1
    cost = np.full((nA, nB), max_score + 1)  # Use finite penalty for unmatched pairs
    
    # Map track IDs to matrix indices
    id_to_idx_A = {tid: idx for idx, tid in enumerate(track_ids1_list)}
    id_to_idx_B = {tid: idx for idx, tid in enumerate(track_ids2_list)}
    
    # Fill the cost matrix
    for (tid1, tid2), score in score_dict.items():
        i = id_to_idx_A[tid1]
        j = id_to_idx_B[tid2]
        cost[i, j] = -score  # Negative because we want to maximize the score
    
    try:
        row_ind, col_ind = linear_sum_assignment(cost)
        # Map back to original track IDs
        matched_pairs = []
        for i, j in zip(row_ind, col_ind):
            tid1 = track_ids1_list[i]
            tid2 = track_ids2_list[j]
            if (tid1, tid2) in score_dict:  # Only include actual matches
                matched_pairs.append((tid1, tid2))
        print(f"Hungarian algorithm matched pairs: {matched_pairs}")
        print(f"row_ind: {row_ind}, col_ind: {col_ind}")
        score_dict_hungarian = {pair: score_dict[pair] for pair in matched_pairs}
        sorted_score_dict_hungarian = sorted(score_dict_hungarian.items(), key=lambda x: x[1], reverse=True)
        print(f"sorted_score_dict_hungarian: {sorted_score_dict_hungarian}")
    except ValueError as e:
        print(f"Hungarian algorithm failed: {e}")
        print("Falling back to sorted results only")
    """ 
    
    return sorted_score_dict, matching_pts3d_dict


def find_all_matching(cfg:dict, track_ids1:list, track_ids2:list, 
                     tracking_data1:list, tracking_data2:list,
                     matching_tracklets:list,
                     matching_pts3d_dict:dict,
                     out_dir_pair:str):
    """ Find all matching for all tracklets in two cameras 

    1. estimate transform using the matching tracklets
    2. Transform the tracklets in camera 1 to camera 2 using the matching tracklets
    3. Calculate the scores for the transformed tracklets and the tracklets in camera 2
    4. Find the best matching using Hungarian algorithm
    5. Return the best mathcing with scores

    Args:
        cfg: config dictionary
        track_ids1(list): list of track ids in camera 1
        track_ids2(list): list of track ids in camera 2
        tracking_data1(list): list of tracking data in camera 1
        tracking_data2(list): list of tracking data in camera 2
        matching_tracklets(list): list of matching tracklets
        matching_pts3d_dict(dict): dictionary of matching points in 3D for each tracklet pair

    Returns:
        sorted_scored_dict: list of tuples, each tuple contains (tracklet_id1, tracklet_id2, score)
    """
    cfg = config.get_config()
    # parameters
    min_matching_points = cfg['min_matching_points']
    min_scale = cfg['min_scale']
    max_scale = cfg['max_scale']
    min_dtw_score_threshold = cfg['min_dtw_score_threshold']
    min_distance_score_threshold = cfg['min_distance_score_threshold']
    max_distance_score_hungarian_threshold = cfg['max_distance_score_hungarian_threshold']
    alignment_method = "umeyama"

    # filter tracklets by length and moving distance
    track_ids1 = filter_tracklet(cfg, track_ids1, tracking_data1)
    track_ids2 = filter_tracklet(cfg, track_ids2, tracking_data2)
    
    #1. estimate transform using the matching tracklets
    score_dict = {}
    pts3d_1 = np.zeros((0, 3), dtype=np.float64)
    pts3d_2 = np.zeros((0, 3), dtype=np.float64)
    for (tid1, tid2), score in matching_tracklets:
        #print(f"matching tracklet {tid1} and {tid2}")
        matching_pts3d_1, matching_pts3d_2 = matching_pts3d_dict[(tid1, tid2)]
        if matching_pts3d_1.shape[0] < min_matching_points:
            #print(f"{tid1}-{tid2}: not enough matching points: {len(matching_pts3d)}. skip.")
            continue
        pts3d_1 = np.concatenate((pts3d_1, matching_pts3d_1), axis=0)
        pts3d_2 = np.concatenate((pts3d_2, matching_pts3d_2), axis=0)

    # 2. estimate transform R, t
    scale, R_cand, t_cand = umeyama_alignment(pts3d_2, pts3d_1)
    #print(f"scale: {scale}, R: {R_cand}, t: {t_cand}")
    #R_cand, t_cand = estimate_transform(matching_pts3d, use_head_points=True)
    
    #if scale < min_scale or scale > max_scale:
    #    print(f"{tid1}-{tid2}: scale: {scale}. skip.")
    #    continue

    # 3. Transform all tracklets in camera 1 to camera 2 using the matching tracklets
    all_pts_1 = get_all_points(tracking_data1, track_ids1)
    all_pts_2 = get_all_points(tracking_data2, track_ids2)
    all_pts_1_transformed = apply_umeyama_transform(all_pts_1, scale, R_cand, t_cand)

    #dtw_score = get_dtw_score(pts3d_1_transformed, pts3d_2)
    #if dtw_score > min_dtw_score_threshold:
    #    print(f"{tid1}-{tid2}: dtw_score: {dtw_score}. skip.")
    #    continue

    #        score = get_score_candidate_matching(track_ids1, track_ids2, tracking_data1, tracking_data2, 
    #                          scale, R_cand, t_cand)
    #        #print(f"score: {score}")
    #        #print(f"dtw_score: {dtw_score}")
    #        score_dict[(tid1, tid2)] = score 
    def plot_tracklets(pts3d_1, pts3d_2, pts3d_1_transformed):
        # plot tracklet1 and tracklet2
        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        ax.scatter(pts3d_1[:, 0], pts3d_1[:, 1], pts3d_1[:, 2], 
                   c='r', marker='.', label='all_points1')
        ax.scatter(pts3d_2[:, 0], pts3d_2[:, 1], pts3d_2[:, 2], 
                   c='b', marker='.', label='all_points2')
        ax.scatter(pts3d_1_transformed[:, 0], pts3d_1_transformed[:, 1], pts3d_1_transformed[:, 2], 
                   c='g', marker='.', label='all_points1_transformed')
        ax.set_zlim(-1, 1)
        ax.legend()
        plt.show()
    
    def plot_tracklets_multi(pts3d_1, pts3d_2, pts3d_1_transformed):
        """
        Plot tracklets in 2x2 synchronized views with synchronized mouse interactions.
        - Top-left: Original tracklet 1 (camera 1)
        - Top-right: Original tracklet 2 (camera 2)
        - Bottom-left: Transformed tracklet 1 (aligned to camera 2)
        - Bottom-right: Overlay of all three
        
        Mouse interactions (rotate, pan, zoom) in any subplot apply to all 4 plots.
        """
        fig = plt.figure(figsize=(16, 12))
        
        # Calculate common bounds for consistent scaling
        all_data = np.vstack([pts3d_1, pts3d_2, pts3d_1_transformed])
        x_min, x_max = all_data[:, 0].min(), all_data[:, 0].max()
        y_min, y_max = all_data[:, 1].min(), all_data[:, 1].max()
        z_min, z_max = all_data[:, 2].min(), all_data[:, 2].max()
        
        # Add margin
        x_margin = (x_max - x_min) * 0.1
        y_margin = (y_max - y_min) * 0.1
        z_margin = (z_max - z_min) * 0.1
        
        # Create first subplot and share its view with others
        ax1 = fig.add_subplot(221, projection='3d')
        ax2 = fig.add_subplot(222, projection='3d', sharez=ax1, sharex=ax1, sharey=ax1)
        ax3 = fig.add_subplot(223, projection='3d', sharez=ax1, sharex=ax1, sharey=ax1)
        ax4 = fig.add_subplot(224, projection='3d', sharez=ax1, sharex=ax1, sharey=ax1)
        
        axes = [ax1, ax2, ax3, ax4]
        
        # Plot 1: Original tracklet 1 (camera 1)
        ax1.scatter(pts3d_1[:, 0], pts3d_1[:, 1], pts3d_1[:, 2], 
                    c='red', marker='o', s=20, alpha=0.6, label='Camera 1 Original')
        ax1.plot(pts3d_1[:, 0], pts3d_1[:, 1], pts3d_1[:, 2], 
                 'r-', alpha=0.3, linewidth=1)
        ax1.set_title('Camera 1 - Original Tracklet', fontsize=12, fontweight='bold')
        ax1.set_xlabel('X (m)')
        ax1.set_ylabel('Y (m)')
        ax1.set_zlabel('Z (m)')
        ax1.legend(loc='upper right')
        
        # Plot 2: Original tracklet 2 (camera 2)
        ax2.scatter(pts3d_2[:, 0], pts3d_2[:, 1], pts3d_2[:, 2], 
                    c='blue', marker='o', s=20, alpha=0.6, label='Camera 2 Original')
        ax2.plot(pts3d_2[:, 0], pts3d_2[:, 1], pts3d_2[:, 2], 
                 'b-', alpha=0.3, linewidth=1)
        ax2.set_title('Camera 2 - Original Tracklet', fontsize=12, fontweight='bold')
        ax2.set_xlabel('X (m)')
        ax2.set_ylabel('Y (m)')
        ax2.set_zlabel('Z (m)')
        ax2.legend(loc='upper right')
        
        # Plot 3: Transformed tracklet 1 (aligned to camera 2)
        ax3.scatter(pts3d_1_transformed[:, 0], pts3d_1_transformed[:, 1], pts3d_1_transformed[:, 2], 
                    c='green', marker='o', s=20, alpha=0.6, label='Camera 1 Transformed')
        ax3.plot(pts3d_1_transformed[:, 0], pts3d_1_transformed[:, 1], pts3d_1_transformed[:, 2], 
                 'g-', alpha=0.3, linewidth=1)
        ax3.set_title('Camera 1 - Transformed to Camera 2 Frame', fontsize=12, fontweight='bold')
        ax3.set_xlabel('X (m)')
        ax3.set_ylabel('Y (m)')
        ax3.set_zlabel('Z (m)')
        ax3.legend(loc='upper right')
        
        # Plot 4: Overlay of all three
        ax4.scatter(pts3d_1[:, 0], pts3d_1[:, 1], pts3d_1[:, 2], 
                    c='red', marker='o', s=15, alpha=0.4, label='Cam1 Original')
        ax4.scatter(pts3d_2[:, 0], pts3d_2[:, 1], pts3d_2[:, 2], 
                    c='blue', marker='s', s=15, alpha=0.4, label='Cam2 Original')
        ax4.scatter(pts3d_1_transformed[:, 0], pts3d_1_transformed[:, 1], pts3d_1_transformed[:, 2], 
                    c='green', marker='^', s=15, alpha=0.4, label='Cam1 Transformed')
        ax4.plot(pts3d_1_transformed[:, 0], pts3d_1_transformed[:, 1], pts3d_1_transformed[:, 2], 
                 'g-', alpha=0.2, linewidth=1)
        ax4.plot(pts3d_2[:, 0], pts3d_2[:, 1], pts3d_2[:, 2], 
                 'b-', alpha=0.2, linewidth=1)
        ax4.set_title('Overlay - Alignment Quality', fontsize=12, fontweight='bold')
        ax4.set_xlabel('X (m)')
        ax4.set_ylabel('Y (m)')
        ax4.set_zlabel('Z (m)')
        ax4.legend(loc='upper right')
        
        # Calculate equal axis ranges for equal aspect ratio
        x_range = (x_max - x_min) + 2 * x_margin
        y_range = (y_max - y_min) + 2 * y_margin
        z_range = (z_max - z_min) + 2 * z_margin
        max_range = max(x_range, y_range, z_range)
        
        # Center each axis and use the same range for equal scaling
        x_center = (x_max + x_min) / 2
        y_center = (y_max + y_min) / 2
        z_center = (z_max + z_min) / 2
        
        # Set consistent limits with equal aspect ratio for all plots
        for ax in axes:
            ax.set_xlim(x_center - max_range/2, x_center + max_range/2)
            ax.set_ylim(y_center - max_range/2, y_center + max_range/2)
            ax.set_zlim(z_center - max_range/2, z_center + max_range/2)
            ax.set_box_aspect([1, 1, 1])  # Equal aspect ratio
            ax.grid(True, alpha=0.3)
        
        # Synchronize view angles across all subplots
        def on_move(event):
            """Synchronize the view angle when mouse moves in any subplot"""
            if event.inaxes in axes:
                # Get the elevation and azimuth from the subplot being interacted with
                elev = event.inaxes.elev
                azim = event.inaxes.azim
                
                # Apply to all other subplots
                for ax in axes:
                    if ax != event.inaxes:
                        ax.view_init(elev=elev, azim=azim)
                
                # Redraw the figure
                fig.canvas.draw_idle()
        
        # Keyboard shortcuts for view control
        def on_key_press(event):
            """Handle keyboard shortcuts for view control"""
            if event.key == 't':
                # Top view: looking down at XY plane (Z is vertical)
                print("Setting top view (XY plane, Z vertical)")
                for ax in axes:
                    ax.view_init(elev=90, azim=-90)
                fig.canvas.draw_idle()
            elif event.key == 's':
                # Side view: looking at XZ plane (Y is out of screen)
                print("Setting side view (XZ plane)")
                for ax in axes:
                    ax.view_init(elev=0, azim=-90)
                fig.canvas.draw_idle()
            elif event.key == 'f':
                # Front view: looking at YZ plane (X is out of screen)
                print("Setting front view (YZ plane)")
                for ax in axes:
                    ax.view_init(elev=0, azim=0)
                fig.canvas.draw_idle()
            elif event.key == 'i':
                # Isometric view: 3D perspective
                print("Setting isometric view")
                for ax in axes:
                    ax.view_init(elev=30, azim=-60)
                fig.canvas.draw_idle()
            elif event.key == 'h':
                # Print help
                print("\n" + "="*60)
                print("KEYBOARD SHORTCUTS:")
                print("="*60)
                print("  't' - Top view (XY plane, Z vertical)")
                print("  's' - Side view (XZ plane)")
                print("  'f' - Front view (YZ plane)")
                print("  'i' - Isometric view (3D perspective)")
                print("  'h' - Show this help")
                print("="*60 + "\n")
        
        # Connect event handlers
        fig.canvas.mpl_connect('motion_notify_event', on_move)
        fig.canvas.mpl_connect('key_press_event', on_key_press)
        
        # Print initial instructions
        print("\n" + "="*60)
        print("3D VIEW CONTROLS:")
        print("="*60)
        print("  Mouse: Drag to rotate, scroll to zoom")
        print("  't' - Top view (XY plane, Z vertical)")
        print("  's' - Side view (XZ plane)")
        print("  'f' - Front view (YZ plane)")
        print("  'i' - Isometric view (3D perspective)")
        print("  'h' - Show help")
        print("="*60 + "\n")
        
        # Maximize plot area by minimizing spacing
        plt.subplots_adjust(left=0.02, right=0.98, top=0.96, bottom=0.04, 
                           wspace=0.05, hspace=0.1)
        
        # Set top view (equivalent to pressing 't')
        print("Setting top view (XY plane, Z vertical)")
        for ax in axes:
            ax.view_init(elev=90, azim=-90)
        
        # Save the 2x2 combined plot
        combined_plot_path = os.path.join(out_dir_pair, "tracklets_alignment_2x2.png")
        fig.savefig(combined_plot_path, dpi=150, bbox_inches='tight')
        print(f"Saved 2x2 combined plot to: {combined_plot_path}")
        
        # Save each subplot as individual plot
        subplot_data = [
            (pts3d_1, 'red', 'Camera 1 - Original Tracklet', 'tracklet_cam1_original.png'),
            (pts3d_2, 'blue', 'Camera 2 - Original Tracklet', 'tracklet_cam2_original.png'),
            (pts3d_1_transformed, 'green', 'Camera 1 - Transformed to Camera 2 Frame', 'tracklet_cam1_transformed.png'),
            (None, None, 'Overlay - Alignment Quality', 'tracklet_overlay.png')  # Special case for overlay
        ]
        
        for idx, (data, color, title, filename) in enumerate(subplot_data):
            fig_single = plt.figure(figsize=(10, 8))
            ax_single = fig_single.add_subplot(111, projection='3d')
            
            if idx < 3:  # Individual tracklet plots
                ax_single.scatter(data[:, 0], data[:, 1], data[:, 2], 
                                c=color, marker='o', s=20, alpha=0.6)
                ax_single.plot(data[:, 0], data[:, 1], data[:, 2], 
                             color=color, alpha=0.3, linewidth=1)
            else:  # Overlay plot
                ax_single.scatter(pts3d_1[:, 0], pts3d_1[:, 1], pts3d_1[:, 2], 
                                c='red', marker='o', s=15, alpha=0.4, label='Cam1 Original')
                ax_single.scatter(pts3d_2[:, 0], pts3d_2[:, 1], pts3d_2[:, 2], 
                                c='blue', marker='s', s=15, alpha=0.4, label='Cam2 Original')
                ax_single.scatter(pts3d_1_transformed[:, 0], pts3d_1_transformed[:, 1], pts3d_1_transformed[:, 2], 
                                c='green', marker='^', s=15, alpha=0.4, label='Cam1 Transformed')
                ax_single.plot(pts3d_1_transformed[:, 0], pts3d_1_transformed[:, 1], pts3d_1_transformed[:, 2], 
                             'g-', alpha=0.2, linewidth=1)
                ax_single.plot(pts3d_2[:, 0], pts3d_2[:, 1], pts3d_2[:, 2], 
                             'b-', alpha=0.2, linewidth=1)
                ax_single.legend(loc='upper right')
            
            ax_single.set_title(title, fontsize=12, fontweight='bold')
            ax_single.set_xlabel('X (m)')
            ax_single.set_ylabel('Y (m)')
            ax_single.set_zlabel('Z (m)')
            
            # Set same limits and aspect ratio as the combined plot
            ax_single.set_xlim(x_center - max_range/2, x_center + max_range/2)
            ax_single.set_ylim(y_center - max_range/2, y_center + max_range/2)
            ax_single.set_zlim(z_center - max_range/2, z_center + max_range/2)
            ax_single.set_box_aspect([1, 1, 1])
            ax_single.grid(True, alpha=0.3)
            
            # Set top view
            ax_single.view_init(elev=90, azim=-90)
            
            # Save individual plot
            single_plot_path = os.path.join(out_dir_pair, filename)
            fig_single.savefig(single_plot_path, dpi=150, bbox_inches='tight')
            print(f"Saved individual plot to: {single_plot_path}")
            plt.close(fig_single)
        
        plt.close(fig)

    plot_tracklets_multi(all_pts_1, all_pts_2, all_pts_1_transformed)

    pts3d_1_transformed = apply_umeyama_transform(pts3d_1, scale, R_cand, t_cand)
    # plot_tracklets(pts3d_1, pts3d_2, pts3d_1_transformed)

    #sorted_score_dict = sorted(score_dict.items(), key=lambda x: x[1], reverse=True)
    #print(f"sorted_score_dict: {sorted_score_dict}")
    score_dict = {}
    for tid1 in track_ids1:
        for tid2 in track_ids2:
            matching_pts3d_1, matching_pts3d_2 = matching_pts3d_dict[(tid1, tid2)]
            if matching_pts3d_1.shape[0] < min_matching_points:
                #print(f"{tid1}-{tid2}: not enough matching points: {len(matching_pts3d)}. skip.")
                continue
            pts3d_1 = matching_pts3d_1
            pts3d_2 = matching_pts3d_2
            pts3d_1_transformed = apply_umeyama_transform(pts3d_1, scale, R_cand, t_cand)
            dtw_score = get_dtw_score(pts3d_1_transformed, pts3d_2)
            if dtw_score > min_dtw_score_threshold:
                #print(f"{tid1}-{tid2}: dtw_score: {dtw_score}. skip.")
                continue
            # distance score
            score = get_distance_score(pts3d_1_transformed, pts3d_2)
            #score = get_score_candidate_matching(track_ids1, track_ids2, tracking_data1, tracking_data2, 
            #                  scale, R_cand, t_cand)
            #print(f"score: {score}")
            #print(f"dtw_score: {dtw_score}")
            score_dict[(tid1, tid2)] = score 
    # Hungarian algorithm to find the best matching
    # Only process if we have valid score_dict entries
    #if not score_dict:
    #    print("No valid tracklet matches found")
    #    return sorted_score_dict
    
    # Create a mapping from track IDs to matrix indices
    track_ids1_list = sorted(list(set([pair[0] for pair in score_dict.keys()])))
    track_ids2_list = sorted(list(set([pair[1] for pair in score_dict.keys()])))
    
    nA = len(track_ids1_list)
    nB = len(track_ids2_list)
    
    # Create cost matrix with finite values
    max_score = max(score_dict.values()) if score_dict else 1
    cost = np.full((nA, nB), max_score + 1)  # Use finite penalty for unmatched pairs
    
    # Map track IDs to matrix indices
    id_to_idx_A = {tid: idx for idx, tid in enumerate(track_ids1_list)}
    id_to_idx_B = {tid: idx for idx, tid in enumerate(track_ids2_list)}
    
    # Fill the cost matrix
    for (tid1, tid2), score in score_dict.items():
        i = id_to_idx_A[tid1]
        j = id_to_idx_B[tid2]
        cost[i, j] = score  
    
    try:
        row_ind, col_ind = linear_sum_assignment(cost)
        # Map back to original track IDs
        matched_pairs = []
        for i, j in zip(row_ind, col_ind):
            tid1 = track_ids1_list[i]
            tid2 = track_ids2_list[j]
            if (tid1, tid2) in score_dict:  # Only include actual matches
                matched_pairs.append((tid1, tid2))
        logging.debug(f"Hungarian algorithm matched pairs: {matched_pairs}")
        #print(f"row_ind: {row_ind}, col_ind: {col_ind}")
        score_dict_hungarian = {pair: score_dict[pair] for pair in matched_pairs}
        sorted_score_dict_hungarian = sorted(score_dict_hungarian.items(), key=lambda x: x[1], reverse=False)
        # filter out the pairs with score higher than max_distance_score_hungarian_threshold
        sorted_score_dict_hungarian = [(pair[0], float(pair[1])) for pair in sorted_score_dict_hungarian if pair[1] < max_distance_score_hungarian_threshold]
        logging.debug(f"sorted_score_dict_hungarian: {sorted_score_dict_hungarian}")
    except ValueError as e:
        logging.error(f"Hungarian algorithm failed: {e}")
        logging.error("Falling back to sorted results only")
    
    return sorted_score_dict_hungarian

def tracklet_matching(cfg:dict, cam_data1:CameraData, cam_data2:CameraData, out_dir_pair:str):
    """ Tracklet matching for two cameras

    Args:
        cfg: config dictionary
        cam_data1: CameraData object for camera 1
        cam_data2: CameraData object for camera 2

    Returns:
        sorted_scored_dict: list of tuples, each tuple contains (tracklet_id1, tracklet_id2, score)
    """
    # read tracking data
    tracking_data1 = cam_data1.tracking_data
    tracking_data2 = cam_data2.tracking_data
    track_ids1 = cam_data1.track_ids
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

    # filter tracklets by length and moving distance
    filtered_tracklet1 = filter_tracklet(cfg, track_ids1, tracking_data1)
    filtered_tracklet2 = filter_tracklet(cfg, track_ids2, tracking_data2)
    logging.debug(f"Filtered tracklet1: {filtered_tracklet1}")
    logging.debug(f"Filtered tracklet2: {filtered_tracklet2}")

    sorted_scored_dict, matching_pts3d_dict = find_initial_matching(cfg, track_ids1, track_ids2, 
                                               tracking_data1, tracking_data2)
    if len(sorted_scored_dict) == 0:
        logging.error("No valid tracklet matches found")
        return None

    # select the top 2 matching tracklets
    matched_tracklets = sorted_scored_dict
    sorted_matching_hungarian = find_all_matching(cfg, track_ids1, track_ids2, 
                      tracking_data1, tracking_data2, 
                      matched_tracklets,
                      matching_pts3d_dict,
                      out_dir_pair
                      )
    return sorted_matching_hungarian

if __name__ == "__main__":
    test() # unit test

    # main_sdg_v3() # main funciton for sdg v3 dataset


    # load tracklet_to_global
    tracklet_to_global = load_tracklet_mapping()
    print(tracklet_to_global)
