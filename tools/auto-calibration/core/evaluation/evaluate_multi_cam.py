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

import cv2
from core.utils.video_capture import open_video_capture
import numpy as np
import json
import os
from tqdm import tqdm
import yaml
import glob
import sys
import pandas as pd
import matplotlib.pyplot as plt
import argparse

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(current_dir)
sys.path.append(project_root)

from core.utils.path_validation import validate_output_directory, validate_input_directory, validate_input_file
from core.camera_estimation import config
from core.camera_estimation.multi_view_calib import umeyama_alignment
from core.camera_estimation.bundle_adjustment import triangulate_points, draw_camera
from core.camera_estimation.visualize_2_view_results import plot_camera_axes


def detect_tags(image):
    """ Test funtion for AprilTag detection """
    image = image.copy()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = apriltag.Detector()
    tags = detector.detect(gray)

    print(f"AprilTag detection: Found {len(tags)} tags")
    for tag in tags:
        print(f"  - Tag ID: {tag.tag_id}, Center: ({tag.center[0]:.1f}, {tag.center[1]:.1f})")

    # draw tags on image
    for tag in tags:
        # draw center of tag
        cx = int(tag.center[0])
        cy = int(tag.center[1])
        cv2.circle(image, (cx, cy), 5, (0, 0, 255), -1)
        cv2.putText(image, str(tag.tag_id), (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 
                    0.5, (0, 0, 255), 2)
        # draw corners of tag
        for corner in tag.corners:
            cv2.circle(image, (int(corner[0]), int(corner[1])), 5, (0, 255, 0), -1)
    # save image
    #cv2.imwrite("tmp/detected_tags_03.png", image)
    return tags

def load_cam_param_gt(cam_param_path):
    with open(cam_param_path, "r") as f:
        cam_param = json.load(f)
    return cam_param

def load_obj_det_data(obj_det_path:str, i:int):
    """
    Load object detection data

    Args:
        obj_det_path (str): path to object detection data
        i (int): index of the image
    Returns:
        obj_det_data (list): list of object detection data
        [{
            "id": id,
            "bbox_2d": bounding box in image coordinates
            "pos_3d": foot location in world coordinates
        }, ...]
    """
    with open(f"{obj_det_path}/object_detection_{i:05d}.json", "r") as f:
        obj_det = json.load(f)

    obj_det_data = []
    for key, value in obj_det["objects"].items():
        id = key.split("/")[3] # NOTE: this id is critical for the evaluation. Be careful when changing this.
        if "bounding_box_2d_tight_fast" in value["annotators"]:
            #bbox_2d = value["annotators"]["bounding_box_2d_loose_fast"]
            bbox_2d = value["annotators"]["bounding_box_2d_tight_fast"]
            if "bounding_box_3d_fast" in value["annotators"]:
                pos_3d = np.array(value["annotators"]["bounding_box_3d_fast"]["transform"][3])[:3]
                obj_det_data.append({
                    "id": id,
                    "bbox_2d": bbox_2d,
                    "pos_3d": pos_3d
                })
    return obj_det_data

def load_obj_det_data_new_sdg_format(obj_data: dict, cid: int, i: int):
    """
    Load object detection data from new SDG format (ground_truth.json).

    The new SDG format stores a single ground_truth.json at the dataset root with
    per-frame object lists, and per-camera 2D boxes under "2d bounding box visible".

    Args:
        obj_det_path (str): path under a specific camera directory (e.g. .../_World_Cameras_Camera_01/object_detection)
        cid (int): camera index
        i (int): frame index

    Returns:
        list[dict]: list of objects with keys "id", "bbox_2d", "pos_3d"
    """
    if cid == 0:
        camera_id = "Camera"
    else:
        camera_id = f"Camera_{cid:02d}"

    frame_key = str(i)
    objects = obj_data.get(frame_key, [])

    obj_det_data = []
    for obj in objects:
        # Ensure bbox exists for this camera
        bbox_map = obj.get("2d bounding box visible", {})
        if camera_id not in bbox_map:
            continue

        bbox_val = bbox_map[camera_id]
        if isinstance(bbox_val, dict):
            bbox_2d = bbox_val
        else:
            if not isinstance(bbox_val, (list, tuple)) or len(bbox_val) < 4:
                continue
            x1, y1, x2, y2 = bbox_val[:4]
            bbox_2d = {
                "x_min": int(x1),
                "y_min": int(y1),
                "x_max": int(x2),
                "y_max": int(y2),
            }

        pos_3d_list = obj.get("3d location", [0.0, 0.0, 0.0])
        pos_3d = np.array(pos_3d_list[:3])

        # Build a stable id consistent with legacy logic
        obj_name = obj.get("object name")
        if isinstance(obj_name, str) and len(obj_name.split("/")) >= 4:
            obj_id_str = obj_name.split("/")[3]  # e.g., "Character_04" or "Character"
        else:
            # Fallback using numeric id if available
            obj_id_num = obj.get("object id", None)
            if obj_id_num is not None:
                if int(obj_id_num) == 0:
                    obj_id_str = "Character"
                else:
                    obj_id_str = f"Character_{int(obj_id_num):02d}"
            else:
                obj_id_str = str(obj.get("id", "unknown"))

        obj_det_data.append({
            "id": obj_id_str,
            "bbox_2d": bbox_2d,
            "pos_3d": pos_3d
        })

    return obj_det_data

def project_3d_point_to_image_Omniverse(pt3d:np.ndarray, 
                                        cam_view_transform:np.ndarray, 
                                        cam_projection_transform:np.ndarray, 
                                        screen_width:int, screen_height:int,
                                        return_ndc:bool=False):
    """ Project 3D point to image plane for Omniverse camera parameters

    Args:
        pt3d (np.ndarray): 3D point in world coordinates 
        cam_view_transform (np.ndarray): camera view transform from world to camera
        cam_projection_transform (np.ndarray): camera projection transform from camera to image
    Returns:
        pt2d (np.ndarray): 2D point in image coordinates 
    """
    # Transform world to camera space
    world_homogeneous = np.array([pt3d[0], pt3d[1], pt3d[2], 1]).T
    camera_homogeneous = np.dot(cam_view_transform, world_homogeneous)
    # Projection transformation
    clip_space = np.dot(cam_projection_transform, camera_homogeneous)
    # Normalize Device Coordinates (NDC)
    ndc = clip_space[:3] / clip_space[3]
    # Map NDC to screen space
    screen_point = ((ndc[0] + 1) * screen_width / 2, (1 - ndc[1]) * screen_height / 2)

    # get projection matrix from Omniverse camera parameters
    proj_mat = get_projection_matrix(cam_view_transform, cam_projection_transform, 
                                     screen_width, screen_height)

    pt2d = np.dot(proj_mat, np.array([pt3d[0], pt3d[1], pt3d[2], 1]).T)
    pt2d = pt2d[:2] / pt2d[2]

    if np.linalg.norm(pt2d - screen_point) > 2:
        print(f"pt2d: {pt2d}")
        print(f"screen_point: {screen_point}")
        print(f"pt3d: {pt3d}")
        print(f"cam_view_transform: {cam_view_transform}")
        print(f"cam_projection_transform: {cam_projection_transform}")
        print(f"proj_mat: {proj_mat}")
    return screen_point

def project_3d_point_to_image_3x4_proj_mat(pt3d:np.ndarray, proj_mat:np.ndarray):
    """ Project 3D point to image plane using 3x4 projection matrix
    Args:
        pt3d (np.ndarray): 3D point in world coordinates
        proj_mat (np.ndarray): 3x4 projection matrix
    Returns:
        pt2d (np.ndarray): 2D point in image coordinates
    """
    pt2d = np.dot(proj_mat, np.array([pt3d[0], pt3d[1], pt3d[2], 1]).T)
    pt2d = pt2d[:2] / pt2d[2]
    return pt2d

# visualization functions
def draw_obj_det_data(image:np.ndarray, 
                      obj_det_data:list, 
                      cam_view_transform:np.ndarray, 
                      cam_projection_transform:np.ndarray):
    """ Draw object detection data on image """
    screen_width = image.shape[1]
    screen_height = image.shape[0]
    for obj in obj_det_data:
        # draw 2D bounding box
        cv2.rectangle(image, 
                      (int(obj["bbox_2d"]["x_min"]), int(obj["bbox_2d"]["y_min"])), 
                      (int(obj["bbox_2d"]["x_max"]), int(obj["bbox_2d"]["y_max"])), 
                      (0, 0, 255), 2)
        # draw the projection of foot location on the floor
        # project 3D point to image plane
        pt2d = project_3d_point_to_image_Omniverse(obj["pos_3d"], cam_view_transform, 
                                         cam_projection_transform, 
                                         screen_width, screen_height)
        cv2.circle(image, (int(pt2d[0]), int(pt2d[1])), 5, (0, 0, 255), -1)
    return image

def draw_obj_det_data_proj_mat(image:np.ndarray, 
                      obj_det_data:list, 
                      proj_mat:np.ndarray):
    """ Draw object detection data on image """
    screen_width = image.shape[1]
    screen_height = image.shape[0]
    for obj in obj_det_data:
        # draw 2D bounding box
        cv2.rectangle(image, 
                      (int(obj["bbox_2d"]["x_min"]), int(obj["bbox_2d"]["y_min"])), 
                      (int(obj["bbox_2d"]["x_max"]), int(obj["bbox_2d"]["y_max"])), 
                      (0, 0, 255), 2)
        # draw the projection of foot location on the floor
        # project 3D point to image plane
        pt2d = project_3d_point_to_image_3x4_proj_mat(obj["pos_3d"], proj_mat)
        cv2.circle(image, (int(pt2d[0]), int(pt2d[1])), 5, (0, 0, 255), -1)
    return image


def get_projection_matrix(cam_view_transform:np.ndarray, 
                          cam_projection_transform:np.ndarray, 
                          screen_width:int, screen_height:int):
    """ Get projection matrix from Omniverse camera parameters 
    Omniverse camera parameters are not common. 
    This function is to convert Omniverse camera parameters to common 
    camera parameters

    Args:
        cam_view_transform (np.ndarray): camera view transform from world to camera
        cam_projection_transform (np.ndarray): camera projection transform from 
        camera to image
        screen_width (int): screen width
        screen_height (int): screen height
    Returns:
        proj_mat (np.ndarray): 3x4 projection matrix
    """
    int_mat = np.array([[screen_width/2, 0, screen_width/2], 
                        [0, -screen_height/2, screen_height/2], 
                        [0, 0, 1]]) # this is not true instrisics matrix. 
                                    #only for Omniverse camera parameters
    M_w2c = np.dot(cam_projection_transform, cam_view_transform) # 4x4
    M_w2c = np.concatenate([M_w2c[:2, :], M_w2c[3:, :]], axis=0) # 3x4
    proj_mat = np.dot(int_mat, M_w2c) # 3x4
    return proj_mat

def calculate_error(gt_proj_mat0, gt_proj_mat1, 
                    est_proj_mat0, est_proj_mat1, 
                    obj_det0, obj_det1,
                    scale_est, R_est, t_est,
                    scale_gt, R_gt, t_gt,
                    screen_width, screen_height, eval_params):
    """ Calculate L2 distance between ground truth and feet location estimated 
    by camera parameters and camera to world transform
    
    Args:
        gt_proj_mat0 (np.ndarray): projection matrix of ground truth camera 0
        gt_proj_mat1 (np.ndarray): projection matrix of ground truth camera 1
        est_proj_mat0 (np.ndarray): projection matrix of estimated camera 0
        est_proj_mat1 (np.ndarray): projection matrix of estimated camera 1
        obj_det0 (list): object detection data of camera 0
        obj_det1 (list): object detection data of camera 1
        scale_est (float): scale of the transformation
        R_est (np.ndarray): rotation matrix of the transformation
        t_est (np.ndarray): translation vector of the transformation
        scale_gt (float): scale of the transformation
        R_gt (np.ndarray): rotation matrix of the transformation
        t_gt (np.ndarray): translation vector of the transformation
        gt_cam_view_transform0 (np.ndarray): camera view transform of ground truth camera 0
        gt_cam_view_transform1 (np.ndarray): camera view transform of ground truth camera 1
        gt_cam_projection_transform0 (np.ndarray): camera projection transform of ground truth camera 0
        gt_cam_projection_transform1 (np.ndarray): camera projection transform of ground truth camera 1
        screen_width (int): screen width
        screen_height (int): screen height
        eval_params (dict): evaluation parameters
    
    Returns:
        l2_dist_list (list): list of L2 distance between ground truth and estimated feet location
        reproj_error0_list (list): list of reprojection error of camera 0
        reproj_error1_list (list): list of reprojection error of camera 1
        pt2d0_gt_list (list): list of ground truth 2D feet location of camera 0
        pt2d1_gt_list (list): list of ground truth 2D feet location of camera 1
        pt2d0_est_list (list): list of estimated 2D feet location of camera 0
        pt2d1_est_list (list): list of estimated 2D feet location of camera 1
        pt3d_gt_list (list): list of ground truth 3D feet location (filtered for evaluation)
        pt3d_est_list (list): list of estimated 3D feet location (filtered for evaluation)
        pt3d_est_w_list (list): list of estimated 3D feet location in world coordinates (filtered for evaluation)
        pt3d_gt_all_viz (list): list of all ground truth 3D feet location (for visualization)
        pt3d_est_w_all_viz (list): list of all estimated 3D feet location in world coordinates (for visualization)
    """
    anchor = eval_params['anchor']
    min_bbox_w = eval_params['min_bbox_w']
    min_bbox_h = eval_params['min_bbox_h']
    min_boundary_x = eval_params['min_boundary_x']
    min_boundary_y = eval_params['min_boundary_y']
    layout_px_per_m = eval_params['layout_px_per_m']

    # Convert to dictionary for faster lookup
    obj_det0_dict = {obj["id"]: obj for obj in obj_det0}
    obj_det1_dict = {obj["id"]: obj for obj in obj_det1}

    # Initialize result lists for evaluation (with strict filtering)
    l2_dist_list = []
    reproj_error0_list = []
    reproj_error1_list = []
    pt2d0_gt_list = []
    pt2d1_gt_list = []
    pt2d0_est_list = []
    pt2d1_est_list = []
    pt3d_gt_list = []
    pt3d_est_list = []
    pt3d_est_w_list = []
    
    # Initialize result lists for visualization (with minimal filtering)
    pt3d_gt_all_viz = []
    pt3d_est_w_all_viz = []
    
    for obj_id in obj_det0_dict.keys():
        if obj_id not in obj_det1_dict.keys():
            continue
            
        # Get object data
        obj0 = obj_det0_dict[obj_id]
        obj1 = obj_det1_dict[obj_id]
        
        # Check if the object is visible in both images
        #pt2d_gt0 = project_3d_point_to_image_Omniverse(obj0["pos_3d"], 
        #                                               gt_cam_view_transform0, 
        #                                               gt_cam_projection_transform0, 
        #                                               screen_width, screen_height,
        #                                               return_ndc=True)
        pt2d_gt0 = project_3d_point_to_image_3x4_proj_mat(obj0["pos_3d"], gt_proj_mat0)
        #pt2d_gt1 = project_3d_point_to_image_Omniverse(obj1["pos_3d"], 
        #                                               gt_cam_view_transform1, 
        #                                               gt_cam_projection_transform1, 
        #                                               screen_width, screen_height,
        #                                               return_ndc=True)
        pt2d_gt1 = project_3d_point_to_image_3x4_proj_mat(obj1["pos_3d"], gt_proj_mat1)
        
        # Use ground truth 2D foot positions for triangulation
        foot_2d_0 = np.array(pt2d_gt0, dtype=np.float64)
        foot_2d_1 = np.array(pt2d_gt1, dtype=np.float64)
        
        # Triangulate 3D points
        pt3d_gt = triangulate_points(gt_proj_mat0, gt_proj_mat1, foot_2d_0, foot_2d_1)
        pt3d_est = triangulate_points(est_proj_mat0, est_proj_mat1, foot_2d_0, foot_2d_1)
        
        # For visualization: Add all successfully triangulated points (minimal filtering)
        if pt3d_est is not None and pt3d_gt is not None:
            # Apply scale, R, t transformation
            pt3d_est_w = scale_est * np.dot(R_est, pt3d_est.flatten()) + t_est

            if anchor == 'layout' or anchor == 'gt_cam0':
                pt3d_gt_w = scale_gt * np.dot(R_gt, pt3d_gt.flatten()) + t_gt
                pt3d_gt_w = pt3d_gt_w / layout_px_per_m # in meters
                pt3d_est_w = pt3d_est_w / layout_px_per_m # in meters
            else:
                pt3d_gt_w = pt3d_gt
            
            # Only skip extreme cases for visualization
            if np.linalg.norm(pt3d_est_w) < 1e6 and np.linalg.norm(pt3d_gt_w) < 1e6:
                pt3d_gt_all_viz.append(pt3d_gt_w)
                pt3d_est_w_all_viz.append(pt3d_est_w)
        
        # For evaluation: Apply strict filtering (original logic)
        # Skip if object is outside image boundaries
        if (pt2d_gt0[0] < 0 or pt2d_gt0[0] > screen_width or 
            pt2d_gt0[1] < 0 or pt2d_gt0[1] > screen_height or 
            pt2d_gt1[0] < 0 or pt2d_gt1[0] > screen_width or 
            pt2d_gt1[1] < 0 or pt2d_gt1[1] > screen_height):
            continue
        
        # Get bounding boxes
        bbox_2d_0 = obj0["bbox_2d"]
        bbox_2d_1 = obj1["bbox_2d"]
        
        # Check if bounding boxes are too close to image edges
        if (bbox_2d_0["x_min"] < min_boundary_x or 
            bbox_2d_0["x_max"] > screen_width - min_boundary_x or 
            bbox_2d_0["y_min"] < min_boundary_y or 
            bbox_2d_0["y_max"] > screen_height - min_boundary_y or 
            bbox_2d_1["x_min"] < min_boundary_x or 
            bbox_2d_1["x_max"] > screen_width - min_boundary_x or 
            bbox_2d_1["y_min"] < min_boundary_y or 
            bbox_2d_1["y_max"] > screen_height - min_boundary_y):
            continue
            
        # Check if bounding boxes are too small
        bbox_w0 = bbox_2d_0["x_max"] - bbox_2d_0["x_min"]
        bbox_h0 = bbox_2d_0["y_max"] - bbox_2d_0["y_min"]
        bbox_w1 = bbox_2d_1["x_max"] - bbox_2d_1["x_min"]
        bbox_h1 = bbox_2d_1["y_max"] - bbox_2d_1["y_min"]
        
        if (bbox_w0 < min_bbox_w or bbox_h0 < min_bbox_h or 
            bbox_w1 < min_bbox_w or bbox_h1 < min_bbox_h):
            continue
            
        # Check if bounding boxes are too large
        if (bbox_w0 > screen_width - min_boundary_x or 
            bbox_h0 > screen_height - min_boundary_y or 
            bbox_w1 > screen_width - min_boundary_x or 
            bbox_h1 > screen_height - min_boundary_y):
            continue
        
        if pt3d_est is None or pt3d_gt is None:
            continue
            
        # Debug: Check W values for numerical stability
        pt3d_h_gt = cv2.triangulatePoints(gt_proj_mat0, gt_proj_mat1, 
                                          foot_2d_0.reshape(1, 2).T, 
                                          foot_2d_1.reshape(1, 2).T)
        pt3d_h_est = cv2.triangulatePoints(est_proj_mat0, est_proj_mat1, 
                                          foot_2d_0.reshape(1, 2).T, 
                                          foot_2d_1.reshape(1, 2).T)
        
        w_gt = pt3d_h_gt[3, 0]
        w_est = pt3d_h_est[3, 0]
        
        # Debug output for near-zero W values
        if abs(w_est) < 1e-3 or abs(w_gt) < 1e-3:
            print(f"\n=== DEBUG: W values near zero ===")
            print(f"2D points: A={foot_2d_0}, B={foot_2d_1}")
            print(f"W values: GT={w_gt:.2e}, EST={w_est:.2e}")
            print(f"GT projection matrices:")
            print(f"  P0: {gt_proj_mat0}")
            print(f"  P1: {gt_proj_mat1}")
            print(f"EST projection matrices:")
            print(f"  P0: {est_proj_mat0}")
            print(f"  P1: {est_proj_mat1}")
            
            # Check camera centers and baseline
            C_gt0 = -np.linalg.inv(gt_proj_mat0[:, :3]) @ gt_proj_mat0[:, 3]
            C_gt1 = -np.linalg.inv(gt_proj_mat1[:, :3]) @ gt_proj_mat1[:, 3]
            C_est0 = -np.linalg.inv(est_proj_mat0[:, :3]) @ est_proj_mat0[:, 3]
            C_est1 = -np.linalg.inv(est_proj_mat1[:, :3]) @ est_proj_mat1[:, 3]
            
            baseline_gt = np.linalg.norm(C_gt1 - C_gt0)
            baseline_est = np.linalg.norm(C_est1 - C_est0)
            print(f"Camera centers:")
            print(f"  GT: C0={C_gt0}, C1={C_gt1}, baseline={baseline_gt:.3f}")
            print(f"  EST: C0={C_est0}, C1={C_est1}, baseline={baseline_est:.3f}")
            print(f"=====================================\n")
        
        # Debug: Print triangulation details for large errors
        if np.any(np.abs(pt3d_est) > 1e6):
            print(f"\n=== DEBUG: Large triangulation values ===")
            print(f"2D points: A={foot_2d_0}, B={foot_2d_1}")
            print(f"GT projection matrices:")
            print(f"  P0: {gt_proj_mat0}")
            print(f"  P1: {gt_proj_mat1}")
            print(f"EST projection matrices:")
            print(f"  P0: {est_proj_mat0}")
            print(f"  P1: {est_proj_mat1}")
            print(f"Triangulated points:")
            print(f"  GT: {pt3d_gt}")
            print(f"  EST: {pt3d_est}")
            print(f"  EST magnitude: {np.linalg.norm(pt3d_est)}")
            #print(f"Scale: {scale}")
            #print(f"Transform R: {R}")
            #print(f"Transform t: {t}")
            print(f"=====================================\n")
        
        # apply scale, R, t
        pt3d_est_w = scale_est * np.dot(R_est, pt3d_est.flatten()) + t_est
        if anchor == 'layout' or anchor == 'gt_cam0':
            pt3d_gt_w = scale_gt * np.dot(R_gt, pt3d_gt.flatten()) + t_gt
        else:
            pt3d_gt_w = pt3d_gt
 
        # calculate l2 distance 
        if anchor == 'layout' or anchor == 'gt_cam0':
            pt3d_gt_w = pt3d_gt_w / layout_px_per_m # in meters
            pt3d_est_w = pt3d_est_w / layout_px_per_m # in meters
        
        l2_dist = np.linalg.norm(pt3d_gt_w - pt3d_est_w)

        if l2_dist >5000:
            print(f"L2 distance is too large: {l2_dist}")
            print(f"pt3d_gt: {pt3d_gt}")
            print(f"pt3d_est_w: {pt3d_est_w}")
            print(f"pt3d_est: {pt3d_est}")
            print(f"foot_2d_0: {foot_2d_0}")
            print(f"foot_2d_1: {foot_2d_1}")
            print(f"obj_id: {obj_id}")
            print(f"bbox_2d_0: {bbox_2d_0}")
            print(f"bbox_2d_1: {bbox_2d_1}")
            print("--------------------------------")


        # calculate reprojection error 
        pt2d0_gt = project_3d_point_to_image_3x4_proj_mat(pt3d_gt.flatten(), 
                                                          gt_proj_mat0)
        pt2d1_gt = project_3d_point_to_image_3x4_proj_mat(pt3d_gt.flatten(), 
                                                          gt_proj_mat1)
        pt2d0_est = project_3d_point_to_image_3x4_proj_mat(pt3d_est.flatten(), 
                                                           est_proj_mat0)
        pt2d1_est = project_3d_point_to_image_3x4_proj_mat(pt3d_est.flatten(), 
                                                           est_proj_mat1)

        reproj_error0 = np.linalg.norm(pt2d0_gt - pt2d0_est)
        reproj_error1 = np.linalg.norm(pt2d1_gt - pt2d1_est)
        reproj_error0_list.append(reproj_error0)
        reproj_error1_list.append(reproj_error1)
        pt2d0_gt_list.append(pt2d0_gt)
        pt2d1_gt_list.append(pt2d1_gt)
        pt2d0_est_list.append(pt2d0_est)
        pt2d1_est_list.append(pt2d1_est)
        l2_dist_list.append(l2_dist)
        pt3d_gt_list.append(pt3d_gt_w)
        pt3d_est_list.append(pt3d_est)
        pt3d_est_w_list.append(pt3d_est_w)

    return l2_dist_list, reproj_error0_list, reproj_error1_list, \
           pt2d0_gt_list,pt2d1_gt_list, pt2d0_est_list, pt2d1_est_list, \
           pt3d_gt_list, pt3d_est_list, pt3d_est_w_list, \
           pt3d_gt_all_viz, pt3d_est_w_all_viz

def evaluate(cid0, cid1, gt_proj_mat0, gt_proj_mat1, 
             est_proj_mat0, est_proj_mat1, 
             obj_det_path0, obj_det_path1, video_path0, video_path1, 
             scale_est, R_est, t_est, 
             scale_gt, R_gt, t_gt,
             eval_params, output_path, obj_data=None, legacy_format=False):
    """ Evaluate camera parameters 
    
    Args:
        gt_proj_mat0 (np.ndarray): projection matrix of ground truth camera 0
        gt_proj_mat1 (np.ndarray): projection matrix of ground truth camera 1
        est_proj_mat0 (np.ndarray): projection matrix of estimated camera 0
        est_proj_mat1 (np.ndarray): projection matrix of estimated camera 1
        obj_det_path0 (str): path to the object detection data of camera 0
        obj_det_path1 (str): path to the object detection data of camera 1
        video_path0 (str): path to the video of camera 0
        video_path1 (str): path to the video of camera 1
        scale_est (float): scale of the transformation
        R_est (np.ndarray): rotation matrix of the transformation
        t_est (np.ndarray): translation vector of the transformation
        scale_gt (float): scale of the transformation
        R_gt (np.ndarray): rotation matrix of the transformation
        t_gt (np.ndarray): translation vector of the transformation
        eval_params (dict): evaluation parameters
        output_path (str): output path
        obj_data (dict): object detection data
        legacy_format (bool): whether to use legacy format
    Returns:
        df (pd.DataFrame): dataframe of the evaluation results
        pt3d_gt_all (list): list of ground truth 3D feet location
        pt3d_est_all (list): list of estimated 3D feet location
        pt3d_est_w_all (list): list of estimated 3D feet location in world coordinates
    """
    # create output path
    os.makedirs(f"{output_path}/res_imgs", exist_ok=True)

    # decompose projection matrix
    out_gt0 = cv2.decomposeProjectionMatrix(gt_proj_mat0)
    out_gt1 = cv2.decomposeProjectionMatrix(gt_proj_mat1)
    out_est0 = cv2.decomposeProjectionMatrix(est_proj_mat0)
    out_est1 = cv2.decomposeProjectionMatrix(est_proj_mat1)

    def print_cam_param(param, suffix): #TODO: move this to the caller function
        print(f'f_{suffix}: {param[0][0][0]:.2f}  ', end='')
        #print(f'R_{suffix}:', param[1])
        rvec, _ = cv2.Rodrigues(param[1])
        rvec = rvec.flatten()
        print(f'rvec_{suffix}: [{rvec[0]:.2f}, {rvec[1]:.2f}, {rvec[2]:.2f}]  ', end='')
        t = (param[2][:3]/param[2][3]).flatten()
        print(f't_{suffix}: [{t[0]:.2f}, {t[1]:.2f}, {t[2]:.2f}]  ', end='')
        print()

    print_cam_param(out_gt0, 'gt0')
    print_cam_param(out_gt1, 'gt1')
    print_cam_param(out_est0, 'est0')
    print_cam_param(out_est1, 'est1')

    print('est_proj_mat0: ', est_proj_mat0)
    print('est_proj_mat1: ', est_proj_mat1)
    print('--------------------------------')

    # get the number of frames in the video
    cap0 = open_video_capture(video_path0)
    cap1 = open_video_capture(video_path1)
    num_frames0 = int(cap0.get(cv2.CAP_PROP_FRAME_COUNT))
    num_frames1 = int(cap1.get(cv2.CAP_PROP_FRAME_COUNT))
    num_images = min(num_frames0, num_frames1)
    screen_width = int(cap0.get(cv2.CAP_PROP_FRAME_WIDTH))
    screen_height = int(cap0.get(cv2.CAP_PROP_FRAME_HEIGHT))

    l2_dist_all = []
    reproj_error0_all = []
    reproj_error1_all = []
    pt3d_gt_all = []
    pt3d_est_all = []
    pt3d_est_w_all = []
    # Visualization data (all triangulated points)
    pt3d_gt_all_viz = []
    pt3d_est_w_all_viz = []
    
    print(f"Evaluating {num_images} images...")
    
    for i in tqdm(range(num_images)): 
        # load ground truth data
        if legacy_format:
            obj_det0 = load_obj_det_data(obj_det_path0, i)
            obj_det1 = load_obj_det_data(obj_det_path1, i)
        else:
            obj_det0 = load_obj_det_data_new_sdg_format(obj_data, cid0, i)
            obj_det1 = load_obj_det_data_new_sdg_format(obj_data, cid1, i)
        
        # process each image
        l2_dist_frame, reproj_error0_frame, reproj_error1_frame, \
            pt2d0_gt_frame, pt2d1_gt_frame, pt2d0_est_frame, pt2d1_est_frame, \
            pt3d_gt_frame, pt3d_est_frame, pt3d_est_w_frame, \
            pt3d_gt_all_viz_frame, pt3d_est_w_all_viz_frame = \
                  calculate_error(gt_proj_mat0, gt_proj_mat1, 
                                  est_proj_mat0, est_proj_mat1,
                                  obj_det0, obj_det1,
                                  scale_est, R_est, t_est,
                                  scale_gt, R_gt, t_gt,
                                  screen_width, screen_height, eval_params)
        l2_dist_all.extend(l2_dist_frame)
        reproj_error0_all.extend(reproj_error0_frame)
        reproj_error1_all.extend(reproj_error1_frame)
        pt3d_gt_all.extend(pt3d_gt_frame)
        pt3d_est_all.extend(pt3d_est_frame)
        pt3d_est_w_all.extend(pt3d_est_w_frame)
        # Collect visualization data
        pt3d_gt_all_viz.extend(pt3d_gt_all_viz_frame)
        pt3d_est_w_all_viz.extend(pt3d_est_w_all_viz_frame)
        # read image from video
        ret0, image0 = cap0.read()
        ret1, image1 = cap1.read()
        if i % 10 == 0:
            res_img0 = draw_obj_det_data_proj_mat(image0, obj_det0, gt_proj_mat0)
            res_img1 = draw_obj_det_data_proj_mat(image1, obj_det1, gt_proj_mat1)
            for j in range(len(pt2d0_gt_frame)):
                # ground truth (green)
                cv2.circle(res_img0, (int(pt2d0_gt_frame[j][0]), 
                                      int(pt2d0_gt_frame[j][1])), 5, (0, 255, 0), -1)
                cv2.circle(res_img1, (int(pt2d1_gt_frame[j][0]), 
                                      int(pt2d1_gt_frame[j][1])), 5, (0, 255, 0), -1)
                # estimated (yellow)
                cv2.circle(res_img0, (int(pt2d0_est_frame[j][0]), 
                                      int(pt2d0_est_frame[j][1])), 5, (0, 255, 255), -1)
                cv2.circle(res_img1, (int(pt2d1_est_frame[j][0]), 
                                      int(pt2d1_est_frame[j][1])), 5, (0, 255, 255), -1)
            stitch_img = np.hstack((res_img0, res_img1))
            cv2.imwrite(f"{output_path}/res_imgs/obj_det_{i:05d}.jpg", stitch_img)
    cap0.release()
    cap1.release()
    # pandas dataframe
    df = pd.DataFrame({'l2_dist': l2_dist_all, 'reproj_error0': reproj_error0_all, 'reproj_error1': reproj_error1_all})
    
    print("✅ Evaluation complete:")
    print(f"  - Filtered points for evaluation: {len(pt3d_est_w_all)}")
    print(f"  - Total points for visualization: {len(pt3d_est_w_all_viz)}")
    
    return df, pt3d_gt_all, pt3d_est_all, pt3d_est_w_all, pt3d_gt_all_viz, pt3d_est_w_all_viz

def get_transform_from_cam0_to_omniworld(image0, image1, proj_mat0, proj_mat1):
    """ Get transform from cam 0 to Omniverse world using apriltag detection

    Args:
        image0 (np.ndarray): image 0
        image1 (np.ndarray): image 1
        proj_mat0 (np.ndarray): projection matrix of camera 0
        proj_mat1 (np.ndarray): projection matrix of camera 1
    Returns:
        scale (float): scale of the transformation
        R (np.ndarray): rotation matrix of the transformation
        t (np.ndarray): translation vector of the transformation
    """
    tags0 = detect_tags(image0)
    tags1 = detect_tags(image1)

    # Check if enough tags are detected
    if len(tags0) < 4 or len(tags1) < 4:
        error_msg = f"""
ERROR: AprilTag anchor requires 4 AprilTags in both camera images.
Found: image0={len(tags0)} tags, image1={len(tags1)} tags

Solutions:
1. Use background.jpg images with 4 visible AprilTags
2. Change anchor to 'layout' in eval_config.yaml
3. Capture new background images with AprilTag setup
"""
        raise ValueError(error_msg)

    # tag centers
    tag_centers0 = np.array([[tag.center[0], tag.center[1]] for tag in tags0])
    tag_centers1 = np.array([[tag.center[0], tag.center[1]] for tag in tags1])
    
    # tag centers in local 3D world coordinates
    tag_centers_local = []
    for i in range(4):
        pt2d_0 = tag_centers0[i]
        pt2d_1 = tag_centers1[i]
        pt3d = triangulate_points(proj_mat0, proj_mat1, pt2d_0, pt2d_1)
        tag_centers_local.append(pt3d[0])
    tag_centers_local = np.array(tag_centers_local)

    # tag centers in global world coordinates
    # Top-left: (0, 0, 0)
    # Top-right: (2, 0, 0)
    # Bottom-left: (0, -3, 0)
    # Bottom-right: (2, -3, 0)
    # homogeneous coordinates in XY plane
    tag_centers_global = np.array([[0, 0, 0], [2, 0, 0], [0, -3, 0], [2, -3, 0]]) 
    scale, R, t = umeyama_alignment(tag_centers_global, tag_centers_local)
    return scale, R, t

def get_transform_from_cam0_to_layout(alignment_data, proj_mat0, proj_mat1):
    """ Get transform from cam 0 to layout frame using alignment data

    Args:
        alignment_data (list): alignment data [(x, y) of camera 0, (x, y) of camera 1, (x, y) of layout]
        proj_mat0 (np.ndarray): projection matrix of camera 0
        proj_mat1 (np.ndarray): projection matrix of camera 1
    Returns:
        scale (float): scale of the transformation
        R (np.ndarray): rotation matrix of the transformation
        t (np.ndarray): translation vector of the transformation
    """
    keypoints_cam0 = []
    keypoints_layout = []
    for i in range(len(alignment_data)):
        pt2d_0 = alignment_data[i][0] # (x, y) of camera 0
        pt2d_1 = alignment_data[i][1] # (x, y) of camera 1
        pt2d_layout = alignment_data[i][2] # (x, y) of layout
        pt3d = triangulate_points(proj_mat0, proj_mat1, pt2d_0, pt2d_1)
        keypoints_cam0.append(pt3d[0])
        keypoints_layout.append([pt2d_layout[0], pt2d_layout[1], 0])
    keypoints_cam0 = np.array(keypoints_cam0, dtype=np.float64)
    keypoints_layout = np.array(keypoints_layout, dtype=np.float64)
    scale, R, t = umeyama_alignment(keypoints_layout, keypoints_cam0)
    return scale, R, t


def read_gt_cam_param(gt_dirs):
    screen_width = 1920
    screen_height = 1080
    proj_mats_gt = {}
    cam_view_transforms_gt = {}
    cam_projection_transforms_gt = {}
    
    # Handle both dictionary and list formats for backward compatibility
    if isinstance(gt_dirs, dict):
        for cam_id, gt_dir in gt_dirs.items():
            # Load ground truth camera parameters
            cam_param = load_cam_param_gt(f"{gt_dir}/camera_params/camera_params_00000.json")
            # Transpose is needed for the row-column-major conversion
            cam_view_transform = np.array(cam_param["cameraViewTransform"]).reshape((4, 4))
            cam_view_transform = cam_view_transform.T
            cam_projection_transform = np.array(cam_param["cameraProjection"]).reshape((4, 4))
            cam_projection_transform = cam_projection_transform.T
            # ground truth projection matrix
            proj_mat_gt = get_projection_matrix(cam_view_transform, 
                                                cam_projection_transform, 
                                                screen_width, screen_height)
            proj_mats_gt[cam_id] = proj_mat_gt
            cam_view_transforms_gt[cam_id] = cam_view_transform
            cam_projection_transforms_gt[cam_id] = cam_projection_transform
    else:
        # Legacy list format
        for i in range(len(gt_dirs)):
            # Load ground truth camera parameters
            cam_param = load_cam_param_gt(f"{gt_dirs[i]}/camera_params/camera_params_00000.json")
            # Transpose is needed for the row-column-major conversion
            cam_view_transform = np.array(cam_param["cameraViewTransform"]).reshape((4, 4))
            cam_view_transform = cam_view_transform.T
            cam_projection_transform = np.array(cam_param["cameraProjection"]).reshape((4, 4))
            cam_projection_transform = cam_projection_transform.T
            # ground truth projection matrix
            proj_mat_gt = get_projection_matrix(cam_view_transform, 
                                                cam_projection_transform, 
                                                screen_width, screen_height)
            proj_mats_gt[i] = proj_mat_gt
            cam_view_transforms_gt[i] = cam_view_transform
            cam_projection_transforms_gt[i] = cam_projection_transform
    
    return proj_mats_gt, cam_view_transforms_gt, cam_projection_transforms_gt

def read_gt_cam_param_new_sdg_format(id2index, gt_calibration_path):
    """ Read ground truth camera parameters in new SDG format
    Args:
        id2index (dict): dictionary mapping camera id to index(Camera -> 0, Camera_01 -> 1, ...)
        gt_calibration_path (str): path to ground truth calibration file
    Returns:
        proj_mats_gt (dict): dictionary mapping index to projection matrix
    """
    proj_mats_gt = {}
    
    with open(gt_calibration_path, 'r') as f:
        calibration_data = json.load(f)
        for sensor in calibration_data.get('sensors', []):
            camera_id = sensor.get('id')
            index = id2index[camera_id]
            intrinsic = np.array(sensor['intrinsicMatrix'], dtype=np.float32)
            extrinsic = np.array(sensor['extrinsicMatrix'], dtype=np.float32)
            proj_mat_gt = np.array(sensor['cameraMatrix'], dtype=np.float32)
            proj_mats_gt[index] = proj_mat_gt
    return proj_mats_gt


def read_est_cam_param(est_paths):
    cam_pose_data = {}
    proj_mats_est = {}
    K = {}
    R = {}
    t = {}
    
    # Handle both dictionary and list formats for backward compatibility
    if isinstance(est_paths, dict):
        for cam_id, path in est_paths.items():
            with open(path, 'r') as f:
                cam_pose_data[cam_id] = yaml.load(f, Loader=yaml.FullLoader)
                proj_mats_est[cam_id] = np.array(cam_pose_data[cam_id]["projectionMatrix_3x4"]).reshape((3, 4))
                K[cam_id] = np.array(cam_pose_data[cam_id]["K"])
                R[cam_id] = np.array(cam_pose_data[cam_id]["R"])
                t[cam_id] = np.array(cam_pose_data[cam_id]["t"])
    else:
        # Legacy list format
        for i in range(len(est_paths)):
            with open(est_paths[i], 'r') as f:
                cam_pose_data[i] = yaml.load(f, Loader=yaml.FullLoader)
                proj_mats_est[i] = np.array(cam_pose_data[i]["projectionMatrix_3x4"]).reshape((3, 4))
                K[i] = np.array(cam_pose_data[i]["K"])
                R[i] = np.array(cam_pose_data[i]["R"])
                t[i] = np.array(cam_pose_data[i]["t"])
    
    return proj_mats_est, K, R, t

def analyze_results(df, output_path, no_plot=False):
    """ Analyze results """
    # Check if DataFrame is empty
    if df.empty:
        print("Warning: DataFrame is empty, no plots will be generated")
        return
    
    # Calculate statistics for each metric
    metrics = {
        'l2_dist': 'L2 distance(m)',
        'reproj_error0': 'reprojection error 0(px)', 
        'reproj_error1': 'reprojection error 1(px)'
    }
    
    # Print and write statistics for each metric
    with open(f'{output_path}/statistics.txt', 'w') as f:
        for col, name in metrics.items():
            mean_val = np.mean(df[col])
            std_val = np.std(df[col])
            max_val = np.max(df[col])
            min_val = np.min(df[col])
            
            # Print to console
            print('-------------------------------------')
            print(f'{name}:')
            print(f"Average ({mean_val:.2f}), ", end='')
            print(f"Standard deviation ({std_val:.2f}), ", end='')
            print(f"Max ({max_val:.2f}), ", end='')
            print(f"Min ({min_val:.2f})")
            
            # Write to file
            
            f.write(f"Average {name}: {mean_val:.2f}\n")
            f.write(f"Standard deviation of {name}: {std_val:.2f}\n")
            f.write(f"Max {name}: {max_val:.2f}\n")
            f.write(f"Min {name}: {min_val:.2f}\n")
    print('-------------------------------------')

    # plot 3 histograms in one figure
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    
    # plot histogram of l2 distance
    axes[0].hist(df['l2_dist'], bins=100)
    axes[0].set_title('L2 Distance Histogram')
    axes[0].set_xlabel('L2 Distance')
    axes[0].set_ylabel('Frequency')
    
    # plot histogram of reprojection error 0
    axes[1].hist(df['reproj_error0'], bins=100)
    axes[1].set_title('Reprojection Error 0 Histogram')
    axes[1].set_xlabel('Reprojection Error 0')
    axes[1].set_ylabel('Frequency')
    
    # plot histogram of reprojection error 1
    axes[2].hist(df['reproj_error1'], bins=100)
    axes[2].set_title('Reprojection Error 1 Histogram')
    axes[2].set_xlabel('Reprojection Error 1')
    axes[2].set_ylabel('Frequency')
    
    plt.tight_layout()
    # Save the plot first, then show it
    plt.savefig(f'{output_path}/combined_histograms.png', dpi=300, bbox_inches='tight')
    # Show plot if not disabled
    if not no_plot:
        plt.show()
    plt.close()

def homography_test():
    """ Test homography """
    pts_gt = np.array([[2.12, -5.78], [4.4, 1.7], [-8.62, -5.88], [-5.48, 27.32], [-22.34, 1.34], [-21.74, 10.84]])
    pts_est = np.array([[2.0, -5.6], [4.25, 1.78], [-8.07, -5.77], [-5.2, 30.89], [-20.44, 0.11], [-20.34, 9.21]])
    H, _ = cv2.findHomography(pts_est, pts_gt)
    print('H', H)
    return H

def apply_homography(pts3d, H):
    """ Apply homography to estimated 3D points """
    pts2d = np.array(pts3d)[:, :2]
    pts2d_homo = np.hstack((pts2d, np.ones((pts2d.shape[0], 1))))
    pts2d_homo_trans = H @ pts2d_homo.T
    pts2d_homo_trans = pts2d_homo_trans.T
    pts2d_homo_trans = pts2d_homo_trans / pts2d_homo_trans[:, 2:3]
    for i in range(len(pts3d)):
        pts3d[i][:2] = pts2d_homo_trans[i, :2]
    return pts3d

def draw_camera_points(Rglob, tglob, pts3d_list):
    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111, projection='3d')
    # Plot each camera's axes
    for idx, (R, t) in enumerate(zip(Rglob, tglob)):
        R0i = R
        t0i = t
        # we need Ri0, ti0 to visualize the camera
        Ri0 = R0i.T
        ti0 = -np.dot(Ri0, t0i)
        plot_camera_axes(Ri0, ti0, ax, scale=1.0)
        ax.text(ti0[0], ti0[1], ti0[2] + 0.05, f'Cam{idx}', fontsize=10)

    colors = ['r', 'g', 'b', 'y', 'c', 'm']
    # plot the 3D points
    all_points = []
    for i, pts3d in enumerate(pts3d_list):
        pts3d = np.array(pts3d).squeeze()
        all_points.append(pts3d)
        c = colors[i % len(colors)]
        ax.scatter(pts3d[:, 0], pts3d[:, 1], pts3d[:, 2], c=c, marker='.')
    all_points = np.concatenate(all_points, axis=0)
    max_range = np.max(all_points.max(axis=0) - all_points.min(axis=0))
    # Set equal aspect ratio for all axes
    mid_x = np.mean([all_points[:,0].min(), all_points[:,0].max()])
    mid_y = np.mean([all_points[:,1].min(), all_points[:,1].max()]) 
    mid_z = np.mean([all_points[:,2].min(), all_points[:,2].max()])

    ax.set_aspect('equal') 
    ax.set_xlim(mid_x - max_range/2, mid_x + max_range/2)
    ax.set_ylim(mid_y - max_range/2, mid_y + max_range/2)
    ax.set_zlim(mid_z - max_range/2, mid_z + max_range/2)
    
    plt.show()
    
def get_single_view_calib_dirs(config:dict, input_base_dir=None):
    """
    Get single view calibration directories by building from base_dir + cam_dir
    
    Args:
        config(dict): config dictionary
        input_base_dir(str): optional base directory override (for evaluation, uses config if available)
    Returns:
        list: list of single view calibration directory paths
    """
    if input_base_dir:
        base_dir = input_base_dir
    elif 'single_view_calib_output_base_dir' in config:
        base_dir = config['single_view_calib_output_base_dir']
    else:
        raise ValueError("Either input_base_dir must be provided or single_view_calib_output_base_dir must be in config")
    
    cam_dirs = config['cam_dir']
    return [os.path.join(base_dir, cam_dir) for cam_dir in cam_dirs]

def save_camera_params(proj_mats, output_path):
    """ Save camera parameters """
    for cam_id, proj_mat in proj_mats.items():
        cam_param = {}
        cam_param['projectionMatrix_3x4'] = proj_mat.tolist()
        with open(f'{output_path}/camInfo_{cam_id:02d}.yaml', 'w') as f:
            yaml.dump(cam_param, f)
            #json.dump(cam_param, f, indent=2)
            #        with open(f'{output_dir}/refined/camInfo_{i:02d}.yaml', 'w') as f:

def save_transform(scale, R, t, output_path):
    """ Save transform """
    transform = {}
    transform['scale'] = scale.tolist()
    transform['R'] = R.tolist()
    transform['t'] = t.tolist()
    with open(f'{output_path}', 'w') as f:
        json.dump(transform, f, indent=2)

def main(camera_pairs, gt_dirs, est_paths, apriltag_image_idx, eval_params, 
         output_path, viz_mode='all', anchor='layout', alignment_data=None, 
         video_paths=None, apriltag_img_paths=None, layout_img_path=None,
         no_plot=False, legacy_format=False):
    """ Main function for evaluation

    Args:
        camera_pairs (list): list of camera pairs
        gt_dirs (list): list of ground truth directories
        est_paths (list): list of estimated camera parameters
        apriltag_image_idx (int): index of the apriltag image
        eval_params (dict): evaluation parameters
        output_path (str): output path
        viz_mode (str): visualization mode ('inliers' or 'all')
        anchor (str): anchor point for the transformation ('layout' or 'apriltag')
        alignment_data (list): alignment data [(x, y) of camera 0, (x, y) of camera 1, (x, y) of layout]
        video_paths (list): list of video paths
        apriltag_img_paths (list): list of apriltag image paths
        layout_img_path (str): path to the layout image
        legacy_format (bool): whether to use legacy format
    """
    print("\n================================================")
    print("🚀 Starting multi-camera calibration evaluation")
    print("================================================")
    # read estimated projection matrices
    print('\n📂 Reading estimated camera parameters')
    est_proj_mats, K, Rglob, tglob = read_est_cam_param(est_paths)

    print('📷 Projeciton matrices:')
    for cam_id in K.keys():
        print(f'Cam {cam_id}: {est_proj_mats[cam_id]}')
        # for verification
        #proj_mat = np.dot(K[cam_id], np.concatenate((Rglob[cam_id], tglob[cam_id]), axis=1))
        #print('proj_mat: ', proj_mat)
        print('--------------------------------')

    # read ground truth projection matrices
    if legacy_format:
        print('\n📂 Reading ground truth camera parameters in legacy format')
        gt_proj_mats, _, _ = read_gt_cam_param(gt_dirs)
    else: 
        # read ground truth projection matrices in new SDG format
        print('\n📂 Reading ground truth camera parameters in new SDG format')
        id2index = {}
        for i, cam_id in enumerate(gt_dirs.keys()):
            if i == 0:
                id2index['Camera'] = 0
            else:
                id2index[f'Camera_{i:02d}'] = i
        print(f'Ground truth camera id to index mapping: {id2index}')
        gt_calibration_path = f'{gt_dirs[0]}/calibration.json' 
        gt_proj_mats = read_gt_cam_param_new_sdg_format(id2index, gt_calibration_path)
        print('📷 Ground truth projection matrices:')
        for cam_id in gt_proj_mats.keys():
            print(f'Cam {cam_id}: {gt_proj_mats[cam_id]}')
            print('--------------------------------')

    # save ground truth camera parameters
    os.makedirs(f'{output_path}/gt_camera_params', exist_ok=True)
    save_camera_params(gt_proj_mats, f'{output_path}/gt_camera_params')

    print('\n==============================================')
    print('🔢 Transforming estimated cameras using GT camera 00\'s transform')
    print('==============================================')
    # Transform estimated cameras using GT camera 00's transform
    # Extract R and T from GT camera 00's view transform
    sorted_cam_ids = sorted(K.keys())
    cam_id_0 = sorted_cam_ids[0]

    # GT camera 00's view transform contains the world-to-camera transformation
    param_gt = cv2.decomposeProjectionMatrix(gt_proj_mats[cam_id_0])
    gt_R_00_W = param_gt[1]
    gt_C_00_W = (param_gt[2][:3]/param_gt[2][3]).flatten()
    
    print('GT Camera 00 transform:')
    print('K:', param_gt[0])
    print('R:', gt_R_00_W)
    print('C:', gt_C_00_W)
    print('--------------------------------')
    
    param_est_00 = cv2.decomposeProjectionMatrix(est_proj_mats[cam_id_0])
    est_K_00 = param_est_00[0]
    est_R_00_00 = param_est_00[1]
    est_C_00_00 = (param_est_00[2][:3]/param_est_00[2][3]).flatten()
    
    # Transform all estimated cameras
    K_transformed = {}
    Rglob_transformed = {}
    tglob_transformed = {}
    est_proj_mats_transformed = {}
    
    # Create 4x4 transformation matrix for GT camera 00
    T_gt_00_W = np.eye(4)
    T_gt_00_W[:3, :3] = gt_R_00_W
    T_gt_00_W[:3, 3] = -gt_R_00_W @ gt_C_00_W
    
    scale = 1.0
    print('--------------------------------')
    print('Transformed estimated cameras:')
    for cam_id in K.keys():
        param_gt = cv2.decomposeProjectionMatrix(gt_proj_mats[cam_id])
        gt_R_0x_W = param_gt[1]
        gt_C_0x_W = (param_gt[2][:3]/param_gt[2][3]).flatten()

        param_est = cv2.decomposeProjectionMatrix(est_proj_mats[cam_id])
        est_K_0x = param_est[0]
        est_R_0x_00 = param_est[1]
        est_C_0x_00 = (param_est[2][:3]/param_est[2][3]).flatten()

        # Calculate scale using the second camera
        if cam_id == sorted_cam_ids[1]:  # Second element
            len_est = np.linalg.norm(est_C_0x_00 - est_C_00_00)
            len_gt = np.linalg.norm(gt_C_0x_W - gt_C_00_W)
            scale = len_est/len_gt

        # Keep intrinsic matrix K unchanged
        K_transformed[cam_id] = est_K_0x.copy()
        
        # Create 4x4 transformation matrix for estimated camera (scaled)
        T_est_0x_00 = np.eye(4)
        T_est_0x_00[:3, :3] = est_R_0x_00
        # T_est_0x_00[:3, 3] = -est_R_0x_00 @ est_C_0x_00
        T_est_0x_00[:3, 3] = -est_R_0x_00 @ ((est_C_0x_00 - est_C_00_00) / scale + est_C_00_00)

        # Transform: T_est_transformed = T_est * T_gt_00_W
        T_est_0x_W = T_est_0x_00 @ T_gt_00_W
        
        # Extract R and t from transformed matrix
        Rglob_transformed[cam_id] = T_est_0x_W[:3, :3]
        tglob_transformed[cam_id] = T_est_0x_W[:3, 3].reshape(-1, 1)

        # Recompose projection matrix
        est_proj_mats_transformed[cam_id] = K_transformed[cam_id] @ np.concatenate(
            (Rglob_transformed[cam_id], tglob_transformed[cam_id]), axis=1)

        # for comparison to GT
        param_est_transformed = cv2.decomposeProjectionMatrix(est_proj_mats_transformed[cam_id])
        est_transformed_K = param_est_transformed[0] / param_est_transformed[0][2, 2]
        est_transformed_R = param_est_transformed[1]
        est_transformed_C = (param_est_transformed[2][:3]/param_est_transformed[2][3]).flatten()

        print(f'Cam {cam_id} estimation:')
        print(' K:', est_transformed_K)
        print(' R:', est_transformed_R)
        print(' T:', -est_transformed_R @ est_transformed_C)
        print(' C:', est_transformed_C)
        print('--------------------------------')

        print(f'Cam {cam_id} GT:')
        print(' K:', param_gt[0])
        print(' R:', gt_R_0x_W)
        print(' T:', -gt_R_0x_W @ gt_C_0x_W)
        print(' C:', gt_C_0x_W)
        print('--------------------------------')

    #print('Transformed estimated cameras to GT camera 00 coordinate system')
    print('--------------------------------')
    
    # Save transformed estimated cameras to separate folder
    os.makedirs(f'{output_path}/est_camera_params_in_world', exist_ok=True)
    
    # Save the complete transformed camera parameters (K, R, projectionMatrix_3x4, t)
    for cam_id in K_transformed.keys():
        cam_param = {}
        # Save in the same order as your example: K, R, projectionMatrix_3x4, t
        cam_param['K'] = K_transformed[cam_id].tolist()
        cam_param['R'] = Rglob_transformed[cam_id].tolist()
        cam_param['projectionMatrix_3x4'] = est_proj_mats_transformed[cam_id].tolist()
        cam_param['t'] = tglob_transformed[cam_id].tolist()
        with open(f'{output_path}/est_camera_params_in_world/camInfo_{cam_id:02d}.yaml', 'w') as f:
            yaml.dump(cam_param, f)
    
    print(f'Saved transformed estimated cameras to: {output_path}/est_camera_params_in_world/')
    print('Note: Transformed cameras saved for external use, evaluation continues with original estimated cameras')
    print('--------------------------------')

    # get a transform from cam 0 to Omniverse world
    # Get the first two camera IDs (sorted to ensure consistency)
    sorted_cam_ids = sorted(est_proj_mats.keys())
    cam_id_0 = sorted_cam_ids[0]
    cam_id_1 = sorted_cam_ids[1]
    
    proj_mat0 = est_proj_mats[cam_id_0]
    proj_mat1 = est_proj_mats[cam_id_1]
    if anchor == 'layout':
        scale_est, R_est, t_est = get_transform_from_cam0_to_layout(alignment_data, 
                                                        proj_mat0, proj_mat1)
        print('--------------------------------')
        print('Transform from cam 0 to layout')
        print('scale', scale_est)
        print('R local to global', R_est)
        print('t local to global', t_est)
        scale_gt, R_gt, t_gt = get_transform_from_cam0_to_layout(alignment_data, 
                                                        gt_proj_mats[cam_id_0], gt_proj_mats[cam_id_1])
        print('--------------------------------')
        print('Transform from cam 0 to layout (ground truth)')
        print('scale', scale_gt)
        print('R local to global', R_gt)
        print('t local to global', t_gt)
        save_transform(scale_est, R_est, t_est, f'{output_path}/transform_cam0_to_map.json')
        save_transform(scale_gt, R_gt, t_gt, f'{output_path}/transform_world_to_map.json')
        print(f'Saved transform from cam 0 to layout to: {output_path}/transform_cam0_to_map.json')
        print(f'Saved transform from world to layout to: {output_path}/transform_world_to_map.json')
    elif anchor == 'apriltag':
        print('Apriltag anchor is not supported')
        exit()
        # temporarily disable this since it is not installed in Colossus machine(Ubuntu 24.04)
        #image0 = cv2.imread(apriltag_img_paths[0])
        #image1 = cv2.imread(apriltag_img_paths[1])
        #scale_est, R_est, t_est = get_transform_from_cam0_to_omniworld(image0, image1, 
        #                                                   proj_mat0, proj_mat1)
        #print('--------------------------------')
        #print('Transform from cam 0 to Omniworld')
        #print('scale', scale_est)
        #print('R local to global', R_est)
        #print('t local to global', t_est)
        #scale_gt, R_gt, t_gt = None, None, None
    elif anchor == 'gt_cam0':
        scale_gt, R_gt, t_gt = get_transform_from_cam0_to_layout(alignment_data, 
                                                        gt_proj_mats[cam_id_0], gt_proj_mats[cam_id_1])
        # use transformed estimated camera parameters. So, scale, R, t are the same as the ground truth
        scale_est = scale_gt
        R_est = R_gt
        t_est = t_gt
        print('--------------------------------')
        print('Transform from cam 0 to layout (ground truth)')
        print('scale', scale_gt)
        print('R local to global', R_gt)
        print('t local to global', t_gt)
        save_transform(scale_est, R_est, t_est, f'{output_path}/transform_cam0_to_map.json')
        save_transform(scale_gt, R_gt, t_gt, f'{output_path}/transform_world_to_map.json')
        print(f'Saved transform from cam 0 to layout to: {output_path}/transform_cam0_to_map.json')
        print(f'Saved transform from world to layout to: {output_path}/transform_world_to_map.json')
        # override layout_px_per_m with the scale of the ground truth
        eval_params['layout_px_per_m'] = scale_gt

        # replace est_proj_mats with est_proj_mats_transformed
        est_proj_mats = est_proj_mats_transformed
    else:
        raise ValueError(f"Invalid anchor: {anchor}")
    if not legacy_format:
        gt_path = os.path.join(gt_dirs[0], "ground_truth.json")
        with open(gt_path, "r") as f:
            obj_data = json.load(f)
    else:
        obj_data = None

    pts3d_gt_all = []
    pts3d_est_all = []
    pts3d_est_w_all = []
    # Visualization data (all triangulated points)
    pts3d_gt_all_viz = []
    pts3d_est_w_all_viz = []
    df_all = {}
    for cam_pair in camera_pairs:
        print("================================================")
        print(f"🔍 Evaluating camera pair {cam_pair}")
        print("================================================")
        cam_id0 = cam_pair[0]
        cam_id1 = cam_pair[1]
        gt_dir0 = gt_dirs[cam_id0]
        gt_dir1 = gt_dirs[cam_id1]
        if legacy_format:
            obj_det_path0 = f"{gt_dir0}/object_detection"
            obj_det_path1 = f"{gt_dir1}/object_detection"
        else:
            obj_det_path0 = f"{gt_dir0}/../object_detection"
            obj_det_path1 = f"{gt_dir1}/../object_detection"
        video_path0 = video_paths[cam_id0]
        video_path1 = video_paths[cam_id1]
        output_path_cam_pair = f'{output_path}/{cam_id0}_{cam_id1}'
        # Validate the camera pair output directory
        output_path_cam_pair = validate_output_directory(output_path_cam_pair, f"camera pair {cam_id0}_{cam_id1} output directory")

        df, pts3d_gt, pts3d_est, pts3d_est_w, pts3d_gt_viz, pts3d_est_w_viz = \
            evaluate(cam_id0, cam_id1, gt_proj_mats[cam_id0], 
                     gt_proj_mats[cam_id1], 
                     est_proj_mats[cam_id0], est_proj_mats[cam_id1], 
                     obj_det_path0, obj_det_path1, 
                     video_path0, video_path1,
                     scale_est, R_est, t_est, 
                     scale_gt, R_gt, t_gt,
                     eval_params,
                     output_path_cam_pair,
                     obj_data,
                     legacy_format)
        # apply homography to estimated 3D points
        #pts3d_est_w = apply_homography(pts3d_est_w, H)

        pts3d_gt_all.append(pts3d_gt)
        pts3d_est_all.append(pts3d_est)
        pts3d_est_w_all.append(pts3d_est_w)
        # Collect visualization data
        pts3d_gt_all_viz.append(pts3d_gt_viz)
        pts3d_est_w_all_viz.append(pts3d_est_w_viz)
        
        # save to csv
        df.to_csv(f'{output_path_cam_pair}/l2_dist.csv', index=False)
        df.to_csv(f'{output_path_cam_pair}/reproj_error0.csv', index=False)
        df.to_csv(f'{output_path_cam_pair}/reproj_error1.csv', index=False) 

        df_all[cam_pair] = df

        # analysis
        analyze_results(df, output_path_cam_pair, no_plot)
        # draw top-down view of 3D points - visualization mode controlled by user
        draw_3d_points(None, pts3d_gt_all_viz, pts3d_est_w_all_viz, output_path_cam_pair,
                      pts3d_gt_all, pts3d_est_w_all, viz_mode)
    
    # camera poses
    camera_poses = [(np.eye(3), np.zeros((3, 1)))]
    #Rglob = [np.eye(3)]
    #tglob = [np.array([0, 0, 0])]
    for cam_pair in camera_pairs:
        cam_id0 = cam_pair[0]
        cam_id1 = cam_pair[1]
        proj_mat = est_proj_mats[cam_id1]
        # get R and t from proj_mat
        param = cv2.decomposeProjectionMatrix(proj_mat)
        print("decomposed projection matrix: \n")
        f = param[0][0][0]
        R = param[1]
        rvec, _ = cv2.Rodrigues(R)
        rvec = rvec.flatten()
        t = (param[2][:3]/param[2][3]).flatten()
        print(f"f: {f}")
        print(f"rvec: {rvec}")
        print(f"t: {t}")
        K = np.array([[f, 0, 960], [0, f, 540], [0, 0, 1]]) # TODO: should get the values from the config 
        proj_mat = np.dot(K, np.concatenate((R, t.reshape(3, 1)), axis=1))
        print('proj_mat: ', proj_mat)
        print('--------------------------------')
        camera_poses.append((R, t))
        #Rglob.append(R)
        #tglob.append(t)

    plt.show() 
    #draw_camera_points(Rglob, tglob, pts3d_est_all)

    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(121, projection='3d')
    for i, (R, t) in enumerate(camera_poses):
        draw_camera(ax, R, t, color='blue', label=f'Init C{i}')
    # set same aspect ratio
    ax.set_aspect('equal')
    ax.set_title("Camera Poses")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    ax.legend()
    
    ax = fig.add_subplot(122, projection='3d')
    for cam_id in sorted(Rglob.keys()):
        R = Rglob[cam_id]
        t = tglob[cam_id]
        draw_camera(ax, R, t, color='blue', label=f'Init C{cam_id}')
    for i in range(len(pts3d_est_all)):
        pts3d_est = np.array(pts3d_est_all[i]).squeeze()
        ax.scatter(pts3d_est[:, 0], pts3d_est[:, 1], pts3d_est[:, 2],
                   c='green', marker='.', label='Refined 3D Points')
    # set same aspect ratio
    ax.set_aspect('equal')
    ax.set_title("Camera Poses")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    ax.legend()
    plt.tight_layout()
    plt.show()
    
    # save pts3d_gt_all
    for i in range(len(pts3d_gt_all)):
        np.save(f'{output_path}/pts3d_gt_{i}.npy', pts3d_gt_all[i])

    plot_all_results(df_all, output_path, no_plot)

    print("================================================")
    print("📊 Total accuracy of all results")
    print("================================================")
    # concatenate data in df_all
    df_all_concat = pd.concat(df_all.values())
    # save to csv
    df_all_concat.to_csv(f'{output_path}/all_results.csv', index=False)
    analyze_results(df_all_concat, output_path, no_plot)

    # draw top-down view of 3D points in layout
    if anchor == 'layout' or anchor == 'gt_cam0':
        layout_px_per_m = eval_params['layout_px_per_m']
        img = cv2.imread(layout_img_path)
        colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255), (255, 255, 0), (255, 0, 255)] 
        for i in range(len(pts3d_gt_all)):
            pts3d_gt_px = np.array(pts3d_gt_all[i])[:, :2] * layout_px_per_m
            pts3d_est_w_px = np.array(pts3d_est_w_all[i])[:, :2] * layout_px_per_m
            img = draw_points_in_layout(img, pts3d_gt_px, 
                                        (255, 255, 255), 3)
            img = draw_points_in_layout(img, pts3d_est_w_px, 
                                        colors[i], 2)
        cv2.imwrite(f'{output_path}/results_layout_3d_points.png', img)


def draw_points_in_layout(img, pts2d, color, size=5):
    """
    Draws 2D points on a 2D XY plane (Bird's Eye View) image.

    Args:
        img (np.ndarray): The image to draw on.
        pts2d (list or np.ndarray): List or array of 2D points (N x 2).
        color (tuple): BGR color tuple for the points.
        size (int): size of the points.

    Returns:
        np.ndarray: The image with points drawn.
    """
    if pts2d.ndim == 1:
        pts2d = pts2d.reshape(-1, 2)
    for pt in pts2d:
        x, y = int(pt[0]), int(pt[1])
        if 0 <= x < img.shape[1] and 0 <= y < img.shape[0]:
            cv2.circle(img, (x, y), size, color, -1)
    return img


def plot_all_results(df_all, output_path, no_plot=False):
    """ Plot all results """
    # plot all results in one figure
    # plot box plot of l2 distance, reprojection error 0, reprojection error 1 
    # for each camera pair with separate box plots
    fig, ax = plt.subplots(1, 1, figsize=(10, 6))
    l2_dist_data = [df['l2_dist'] for df in df_all.values()]
    cam_pair_labels = [str(cam_pair) for cam_pair in df_all.keys()]
    ax.boxplot(l2_dist_data, labels=cam_pair_labels)
    ax.set_title('L2 Distance Box Plot for All Camera Pairs')
    ax.set_xlabel('Camera Pair')
    ax.set_ylabel('L2 Distance')
    plt.tight_layout()
    plt.savefig(f'{output_path}/all_results_l2_dist.png')
    if not no_plot:
        plt.show()
    plt.close()

    fig, ax = plt.subplots(1, 1, figsize=(10, 6))
    reproj_error0_data = [df['reproj_error0'] for df in df_all.values()]
    cam_pair_labels = [str(cam_pair) for cam_pair in df_all.keys()]
    ax.boxplot(reproj_error0_data, labels=cam_pair_labels)
    ax.set_title('Reprojection Error 0 Box Plot for All Camera Pairs')
    ax.set_xlabel('Camera Pair')
    ax.set_ylabel('Reprojection Error 0')
    plt.tight_layout()
    plt.savefig(f'{output_path}/all_results_reproj_error0.png')
    if not no_plot:
        plt.show()
    plt.close()

    fig, ax = plt.subplots(1, 1, figsize=(10, 6))
    reproj_error1_data = [df['reproj_error1'] for df in df_all.values()]
    cam_pair_labels = [str(cam_pair) for cam_pair in df_all.keys()]
    ax.boxplot(reproj_error1_data, labels=cam_pair_labels)
    ax.set_title('Reprojection Error 1 Box Plot for All Camera Pairs')
    ax.set_xlabel('Camera Pair')
    ax.set_ylabel('Reprojection Error 1')
    plt.tight_layout()
    plt.savefig(f'{output_path}/all_results_reproj_error1.png')
    if not no_plot:
        plt.show()
    plt.close()



def draw_3d_points(camera_poses,pts3d_gt_all_viz, pts3d_est_w_all_viz, output_path_cam_pair, 
                   pts3d_gt_all_inliers=None, pts3d_est_w_all_inliers=None, viz_mode='all',
                   no_plot=False):
    """ Draw 3D points with different visualization modes
    
    Args:
        camera_poses: Camera poses
        pts3d_gt_all_viz: All ground truth 3D points (inliers + outliers)
        pts3d_est_w_all_viz: All estimated 3D points (inliers + outliers)  
        output_path_cam_pair: Output path for saving the plot
        pts3d_gt_all_inliers: Ground truth inlier points (for transparency determination)
        pts3d_est_w_all_inliers: Estimated inlier points (for transparency determination)
        viz_mode: Visualization mode ('inliers' or 'all')
        no_plot: Whether to plot the figure
    """
    colors = ['r', 'g', 'b', 'y', 'c', 'm']
    # draw top-down view of 3D points
    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111, projection='3d')

    # draw cameras
    if camera_poses is not None:
        for i, (R, t) in enumerate(camera_poses):
            draw_camera(ax, R, t, color='blue', label=f'Ref C{i}')

    if viz_mode == 'inliers':
        # Mode 1: Plot only inliers (clean view)
        print(f"Visualization mode: Inliers only")
        
        # Use filtered inlier data if available, otherwise use all data
        pts3d_gt_data = pts3d_gt_all_inliers if pts3d_gt_all_inliers is not None else pts3d_gt_all_viz
        pts3d_est_data = pts3d_est_w_all_inliers if pts3d_est_w_all_inliers is not None else pts3d_est_w_all_viz
        
        for i in range(len(pts3d_gt_data)):
            # Ground truth points (black)
            pts3d_gt = np.array(pts3d_gt_data[i]).squeeze()
            if pts3d_gt.ndim == 1:
                pts3d_gt = pts3d_gt.reshape(1, -1)
            if len(pts3d_gt) > 0:
                ax.scatter(pts3d_gt[:, 0], pts3d_gt[:, 1], pts3d_gt[:, 2], 
                          c='k', marker='.', alpha=1.0, s=20, label='GT Inliers' if i == 0 else '')
            
            # Estimated points (colored)
            pts3d_est = np.array(pts3d_est_data[i]).squeeze()
            if pts3d_est.ndim == 1:
                pts3d_est = pts3d_est.reshape(1, -1)
            if len(pts3d_est) > 0:
                ax.scatter(pts3d_est[:, 0], pts3d_est[:, 1], pts3d_est[:, 2], 
                          c=colors[i], marker='.', alpha=1.0, s=20, 
                          label=f'Est Inliers Pair {i}' if i == 0 else '')
        
        ax.set_title('3D Points: Inliers Only (High Quality Points)')
        
    else:  # viz_mode == 'all'
        # Mode 2: Plot all points with outliers as transparent
        print(f"Visualization mode: All points (outliers transparent)")
        
        def points_to_set(points_list):
            """Convert list of points to set of tuples for fast lookup"""
            point_set = set()
            for pts in points_list:
                pts_array = np.array(pts).squeeze()
                if pts_array.ndim == 1:
                    pts_array = pts_array.reshape(1, -1)
                for pt in pts_array:
                    # Round to avoid floating point precision issues
                    point_set.add(tuple(np.round(pt, 6)))
            return point_set
        
        # Create sets for fast inlier lookup
        gt_inliers_set = set()
        est_inliers_set = set()
        
        if pts3d_gt_all_inliers is not None:
            gt_inliers_set = points_to_set(pts3d_gt_all_inliers)
        if pts3d_est_w_all_inliers is not None:
            est_inliers_set = points_to_set(pts3d_est_w_all_inliers)
        
        for i in range(len(pts3d_gt_all_viz)):
            # Ground truth points (black)
            pts3d_gt = np.array(pts3d_gt_all_viz[i]).squeeze()
            if pts3d_gt.ndim == 1:
                pts3d_gt = pts3d_gt.reshape(1, -1)
                
            # Separate inliers and outliers for GT
            gt_inliers = []
            gt_outliers = []
            
            for pt in pts3d_gt:
                pt_tuple = tuple(np.round(pt, 6))
                if pt_tuple in gt_inliers_set:
                    gt_inliers.append(pt)
                else:
                    gt_outliers.append(pt)
            
            # Plot GT inliers (full opacity)
            if len(gt_inliers) > 0:
                gt_inliers = np.array(gt_inliers)
                ax.scatter(gt_inliers[:, 0], gt_inliers[:, 1], gt_inliers[:, 2], 
                          c='k', marker='.', alpha=1.0, s=20, label='GT Inliers' if i == 0 else '')
            
            # Plot GT outliers (half transparency)
            if len(gt_outliers) > 0:
                gt_outliers = np.array(gt_outliers)
                ax.scatter(gt_outliers[:, 0], gt_outliers[:, 1], gt_outliers[:, 2], 
                          c='k', marker='x', alpha=0.5, s=15, label='GT Outliers' if i == 0 else '')
            
            # Estimated points (colored)
            pts3d_est = np.array(pts3d_est_w_all_viz[i]).squeeze()
            if pts3d_est.ndim == 1:
                pts3d_est = pts3d_est.reshape(1, -1)
                
            # Separate inliers and outliers for estimated points
            est_inliers = []
            est_outliers = []
            
            for pt in pts3d_est:
                pt_tuple = tuple(np.round(pt, 6))
                if pt_tuple in est_inliers_set:
                    est_inliers.append(pt)
                else:
                    est_outliers.append(pt)
            
            # Plot estimated inliers (full opacity)
            if len(est_inliers) > 0:
                est_inliers = np.array(est_inliers)
                ax.scatter(est_inliers[:, 0], est_inliers[:, 1], est_inliers[:, 2], 
                          c=colors[i], marker='.', alpha=1.0, s=20, 
                          label=f'Est Inliers Pair {i}' if i == 0 else '')
            
            # Plot estimated outliers (half transparency)
            if len(est_outliers) > 0:
                est_outliers = np.array(est_outliers)
                ax.scatter(est_outliers[:, 0], est_outliers[:, 1], est_outliers[:, 2], 
                          c=colors[i], marker='x', alpha=0.5, s=15, 
                          label=f'Est Outliers Pair {i}' if i == 0 else '')
        
        ax.set_title('3D Points: Inliers (solid) vs Outliers (transparent)')
    
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')
    ax.set_zlim(-5.0, 5.0)
    ax.legend()
    plt.savefig(f'{output_path_cam_pair}/3d_points.png', dpi=300, bbox_inches='tight')
    # Show plot if not disabled
    if not no_plot:
        plt.show()
    plt.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Evaluate multi-cam calibration')
    parser.add_argument('-c', '--config', type=str,
                        help='Path to the config file')
    parser.add_argument('-e', '--eval_config', type=str,
                        help='Path to evalutaion config file')
    parser.add_argument('-v', '--viz_mode', type=str, 
                        choices=['inliers', 'all'], default='inliers',
                        help='Visualization mode: "inliers" (filtered points only) or "all" (all triangulated points with outliers as transparent). Default: all')
    parser.add_argument('--no-plot', action='store_true',
                        help='Disable interactive plotting (plt.show() calls). Plots will still be saved to files.')
    parser.add_argument('-i', '--input_base_dir', type=str,
                        help='Base directory for single view calibration outputs (overrides config)')
    parser.add_argument('-o', '--output_base_dir', type=str, required=True,
                        help='Base directory for AMC output')
    parser.add_argument('-g', '--gt_base_dir', type=str, required=True,
                        help='Directory containing ground truth data')
    parser.add_argument('-l', '--layout_image_path', type=str, required=True,
                        help='Path to layout image file')
    parser.add_argument('--legacy_format', action='store_true', 
                        help='Use legacy format for ground truth data') # for backward compatibility with old SDG format
    args = parser.parse_args()
    print("\n================================================")
    print("🛠️ Evaluation parameters")
    print("================================================")
    print(f'Config file: {args.config}')
    print(f'Visualization mode: {args.viz_mode}')
    print(f'No plot mode: {args.no_plot}')
    print(f'Legacy format mode: {args.legacy_format}')

    # read config file
    config.load_config(args.config)
    cfg = config.get_config()

    # evaluation parameters
    with open(args.eval_config, "r") as f:
        eval_params = yaml.safe_load(f)

    # Validate and sanitize input paths
    dataset_root = validate_input_directory(args.gt_base_dir, "ground truth base directory")
    output_base_root = validate_input_directory(args.output_base_dir, "AMC output base directory")
    mv_calib_root = validate_input_directory(f"{output_base_root}/multi_view_results", 
                                             "multi-view calibration output directory")
    
    # Paths for ground truthdata and configuration
    anchor = eval_params['anchor']
    sv_out_dir = get_single_view_calib_dirs(cfg, args.input_base_dir)
    ba_output_path = f"{mv_calib_root}/BA_output/results_ba/refined"
    output_path = f"{mv_calib_root}/evaluation"
    # Validate the evaluation output directory
    output_path = validate_output_directory(output_path, "evaluation output directory")
    
    # camera_pairs are auto-generated by config.load_config() if not provided
    camera_pairs = [(cam_ids[0], cam_ids[1]) for cam_ids in cfg['camera_pairs']]
    
    print(f'Dataset root: {dataset_root}')
    print(f'MV calib root: {mv_calib_root}')
    print(f'BA output path: {ba_output_path}')
    print(f'Anchor: {anchor}')

    # Extract unique camera IDs from camera pairs
    unique_cam_ids = set()
    for cam_pair in camera_pairs:
        unique_cam_ids.add(cam_pair[0])
        unique_cam_ids.add(cam_pair[1])
    unique_cam_ids = sorted(list(unique_cam_ids))
    
    print(f'Camera pairs: {camera_pairs}')
    print(f"Unique camera IDs from pairs: {unique_cam_ids}")
    
    # Create mapping from camera ID to sv_out_dir index for proper list access
    cam_id_to_index = {}
    gt_dirs = {}
    est_paths = {}
    
    # sv_out_dir is built from config['cam_dir'] which preserves original order
    for i, sv_dir in enumerate(sv_out_dir):
        if sv_dir.endswith('/'):
            cam_id = sv_dir.split('/')[-2].split('_')[-1]
        else:
            cam_id = sv_dir.split('/')[-1].split('_')[-1]
        
        # Convert cam_id to integer for consistent indexing
        cam_id_int = int(cam_id)
        cam_id_to_index[cam_id_int] = i
        if args.legacy_format:
            gt_dirs[cam_id_int] = f"{dataset_root}/_World_Cameras_Camera_{cam_id}"
        else:
            gt_dirs[cam_id_int] = f"{dataset_root}"
        # Bundle adjustment now saves files using original camera IDs (fixed indexing)
        est_paths[cam_id_int] = f'{ba_output_path}/camInfo_{cam_id_int:02d}.yaml'

    print(f"Camera ID to sv_out_dir index mapping: {cam_id_to_index}")
    print(f"Expected camera files: {list(est_paths.values())}")

    # image idx for tag detection
    APRILTAG_IMAGE_IDX = 0
    
    # alighment data using layout
    if anchor == 'layout' or anchor == 'gt_cam0':
        if os.path.exists(f'{output_base_root}/alignment_data/alignment_data.json'):
            with open(f'{output_base_root}/alignment_data/alignment_data.json', 'r') as f:
                alignment_data = json.load(f)
        else:
            print(f'No alignment data found in {output_base_root}/alignment_data/alignment_data.json')
            exit()
    else:
        alignment_data = None
    
    # video paths
    sv_out_dir = get_single_view_calib_dirs(cfg, args.input_base_dir)
    video_paths = {}
    for cam_id_int, i in cam_id_to_index.items():
        video_paths[cam_id_int] = f'{sv_out_dir[i]}/rectified.mp4'
    
    if anchor == 'apriltag':    
        # apriltag image paths - use first two cameras from the pairs
        first_pair = camera_pairs[0]  # e.g., (0, 1)
        cam_0_idx = cam_id_to_index[first_pair[0]]
        cam_1_idx = cam_id_to_index[first_pair[1]]
        apriltag_img_paths = [f'{sv_out_dir[cam_0_idx]}/background.jpg', 
                              f'{sv_out_dir[cam_1_idx]}/background.jpg']
        layout_img_path = None
    else:
        apriltag_img_paths = None
        layout_img_path = validate_input_file(args.layout_image_path, "layout image file")
    
    # create output path (already validated and created above)
    main(camera_pairs, gt_dirs, est_paths, APRILTAG_IMAGE_IDX, eval_params, 
         output_path, args.viz_mode, anchor, alignment_data, video_paths,
         apriltag_img_paths, layout_img_path, args.no_plot, args.legacy_format)
