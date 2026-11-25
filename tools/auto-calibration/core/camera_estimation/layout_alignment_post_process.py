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
This script is used to manually align camera frame with layout frame
"""
import cv2
import numpy as np
import os
import sys
import argparse

# Add the current directory to Python path to allow importing local modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(project_root)

import config
import json
import yaml
import cv2
import apriltag

from typing import Optional

from bundle_adjustment_utils import triangulate_points, umeyama_alignment
from overlay_visualization import prepare_data_for_adjustment_from_mv_calib, draw_cameras, read_est_cam_param

def validate_directory_path(path: str, 
                          path_type: str = "directory",
                          must_exist: bool = False,
                          create_if_missing: bool = False,
                          require_read: bool = False,
                          require_write: bool = False) -> str:
    if not path:
        raise ValueError(f"{path_type} path cannot be empty")
    
    # Resolve the path to prevent directory traversal attacks
    normalized_path = os.path.abspath(path)
    
    # Check if path exists
    path_exists = os.path.exists(normalized_path)
    
    if not path_exists:
        if must_exist:
            raise FileNotFoundError(f"{path_type} does not exist: {normalized_path}")
        elif create_if_missing:
            try:
                os.makedirs(normalized_path, exist_ok=True)
                path_exists = True
            except OSError as e:
                raise OSError(f"Cannot create {path_type}: {normalized_path}. Error: {e}")
    
    # If path exists, validate it's a directory
    if path_exists:
        if not os.path.isdir(normalized_path):
            raise NotADirectoryError(f"{path_type} is not a directory: {normalized_path}")
        
        # Check permissions
        if require_read and not os.access(normalized_path, os.R_OK):
            raise PermissionError(f"No read permission for {path_type}: {normalized_path}")
        
        if require_write and not os.access(normalized_path, os.W_OK):
            raise PermissionError(f"No write permission for {path_type}: {normalized_path}")
    
    return normalized_path


def validate_file_path(path: str,
                      path_type: str = "file",
                      must_exist: bool = False,
                      require_read: bool = False,
                      require_write: bool = False) -> str:
    if not path:
        raise ValueError(f"{path_type} path cannot be empty")
    
    # Resolve the path to prevent directory traversal attacks
    normalized_path = os.path.abspath(path)
    
    # Check if path exists
    if os.path.exists(normalized_path):
        if os.path.isdir(normalized_path):
            raise IsADirectoryError(f"{path_type} path points to a directory: {normalized_path}")
        
        # Check permissions
        if require_read and not os.access(normalized_path, os.R_OK):
            raise PermissionError(f"No read permission for {path_type}: {normalized_path}")
        
        if require_write and not os.access(normalized_path, os.W_OK):
            raise PermissionError(f"No write permission for {path_type}: {normalized_path}")
    
    elif must_exist:
        raise FileNotFoundError(f"{path_type} does not exist: {normalized_path}")
    
    return normalized_path


def validate_output_directory(path: str, path_type: str = "output directory") -> str:
    return validate_directory_path(
        path=path,
        path_type=path_type,
        create_if_missing=True,
        require_write=True
    )


def validate_input_directory(path: str, path_type: str = "input directory") -> str:
    return validate_directory_path(
        path=path,
        path_type=path_type,
        must_exist=True,
        require_read=True
    )


def validate_input_file(path: str, path_type: str = "input file") -> str:
    return validate_file_path(
        path=path,
        path_type=path_type,
        must_exist=True,
        require_read=True
    )

def get_single_view_calib_dirs(config:dict, input_base_dir=None):
    """
    Get single view calibration directories by building from base_dir + cam_dir
    
    Args:
        config(dict): config dictionary
        input_base_dir(str): optional base directory override (uses config if not provided)
    Returns:
        list: list of single view calibration directory paths
    """
    if input_base_dir:
        base_dir = input_base_dir
    elif 'single_view_calib_output_base_dir' in config:
        base_dir = config['single_view_calib_output_base_dir']
    else:
        raise ValueError("input_base_dir must be provided as single_view_calib_output_base_dir is no longer used in config")
    
    cam_dirs = config['cam_dir']
    return [os.path.join(base_dir, cam_dir) for cam_dir in cam_dirs] 

# global variables for GUI
control_points = [] # control points 
selected_point = -1 # index of the selected control point
g_landmarks = [] # landmarks

# Mouse callback function for selecting landmarks
def select_landmark(event, x, y, flags, param):
    """
    Mouse callback function for selecting landmarks 
    """
    global selected_point, control_points, g_landmarks
    
    if event == cv2.EVENT_LBUTTONDOWN:
        g_landmarks.append(np.array([x, y]))
    elif event == cv2.EVENT_LBUTTONUP:
        selected_point = -1

def convert_3D_points_to_bev(pts3d, scale, offset_x, offset_y):
    """ Convert 3D points to BEV view
    """
    # Parameters for BEV scaling and translation
    if pts3d.ndim == 1:
        pts3d = pts3d.reshape(-1, 3)
    pts2d_world = pts3d[:, :2]
    pts2d_bev = pts2d_world * [scale, -scale] + np.array([offset_x, offset_y])
    return pts2d_bev

def convert_bev_to_3D_points(pts2d_bev, scale, offset_x, offset_y):
    """ Convert BEV points to 3D points
    """
    # Parameters for BEV scaling and translation
    if pts2d_bev.ndim == 1:
        pts2d_bev = pts2d_bev.reshape(-1, 2)

    pts3d = (pts2d_bev - np.array([offset_x, offset_y])) / [scale, -scale]
    pts3d = np.hstack((pts3d, np.zeros((pts3d.shape[0], 1))))
    return pts3d

def draw_points(img, pts2d, color, size=5):
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

def calculate_homography(template, control_points):
    """ Calculate homography between control point template and current control 
    points
    """
    H, _ = cv2.findHomography(template, np.array(control_points, dtype=np.float64),
                               method=cv2.RANSAC, ransacReprojThreshold=5.0)
    return H

def apply_homography(H, pts):
    """ Apply homography to calculate new control points
    """
    pts_homogeneous = np.hstack((pts, np.ones((pts.shape[0], 1))))
    transformed_pts = H @ pts_homogeneous.T
    transformed_pts /= transformed_pts[-1, :]
    return transformed_pts[:2, :].T


def run_landmark_selection_ui(stacked_img):
    """
    Run landmark selection UI for the manual adjustment 
    """
    cv2.namedWindow('Select landmarks', cv2.WINDOW_NORMAL)
    cv2.setMouseCallback('Select landmarks', select_landmark)
    global g_landmarks
    g_landmarks = []
    
    colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (255, 255, 0)]

    while True:
        # draw landmarks on the image
        for i in range(len(g_landmarks)):
            cv2.circle(stacked_img, (int(g_landmarks[i][0]), int(g_landmarks[i][1])), 
                       5, colors[i // 3 % len(colors)], -1)
 
        cv2.imshow('Select landmarks', stacked_img)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            # Quit the tool
            break
    cv2.destroyAllWindows()


def check_if_object_is_valid(obj0, obj1, pt2d_gt0, pt2d_gt1, 
                             screen_width, screen_height, 
                             min_bbox_w, min_bbox_h, 
                             min_boundary_x, min_boundary_y):
    """
    Check if the object is valid
    """
    # Skip if object is outside image boundaries
    if (pt2d_gt0[0] < 0 or pt2d_gt0[0] > screen_width or 
        pt2d_gt0[1] < 0 or pt2d_gt0[1] > screen_height or 
        pt2d_gt1[0] < 0 or pt2d_gt1[0] > screen_width or 
        pt2d_gt1[1] < 0 or pt2d_gt1[1] > screen_height):
        return False
    
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
        return False
        
    # Check if bounding boxes are too small
    bbox_w0 = bbox_2d_0["x_max"] - bbox_2d_0["x_min"]
    bbox_h0 = bbox_2d_0["y_max"] - bbox_2d_0["y_min"]
    bbox_w1 = bbox_2d_1["x_max"] - bbox_2d_1["x_min"]
    bbox_h1 = bbox_2d_1["y_max"] - bbox_2d_1["y_min"]
    
    if (bbox_w0 < min_bbox_w or bbox_h0 < min_bbox_h or 
        bbox_w1 < min_bbox_w or bbox_h1 < min_bbox_h):
        return False
        
    # Check if bounding boxes are too large
    if (bbox_w0 > screen_width - min_boundary_x or 
        bbox_h0 > screen_height - min_boundary_y or 
        bbox_w1 > screen_width - min_boundary_x or 
        bbox_h1 > screen_height - min_boundary_y):
        return False 
    return True 

def get_3D_points_from_obj_det(gt_proj_mat0, gt_proj_mat1, 
                               gt_cam_view_transform0, gt_cam_view_transform1, 
                               gt_cam_projection_transform0, 
                               gt_cam_projection_transform1, 
                               obj_det_path0, obj_det_path1, 
                               image_path0, image_path1,
                               eval_params):
    """
    Get 3D points from object detection data
    """
    # find the number of images in the image_path
    image_files = glob.glob(image_path0 + "/*.png")
    num_images = len(image_files)
    image0 = cv2.imread(image_files[0])
    screen_width = image0.shape[1]
    screen_height = image0.shape[0]
    min_bbox_w = eval_params['min_bbox_w']
    min_bbox_h = eval_params['min_bbox_h']
    min_boundary_x = eval_params['min_boundary_x']
    min_boundary_y = eval_params['min_boundary_y']

    pt3d_gt_list = []
    for i in tqdm(range(num_images)): 
        # load ground truth data
        obj_det0 = load_obj_det_data(obj_det_path0, i)
        obj_det1 = load_obj_det_data(obj_det_path1, i)
        # Convert to dictionary for faster lookup
        obj_det0_dict = {obj["id"]: obj for obj in obj_det0}
        obj_det1_dict = {obj["id"]: obj for obj in obj_det1}

        for obj_id in obj_det0_dict.keys():
            if obj_id not in obj_det1_dict.keys():
                continue
            # Get object data
            obj0 = obj_det0_dict[obj_id]
            obj1 = obj_det1_dict[obj_id]
        
            # Check if the object is visible in both images
            pt2d_gt0 = project_3d_point_to_image_Omniverse(obj0["pos_3d"], 
                                                           gt_cam_view_transform0, 
                                                           gt_cam_projection_transform0, 
                                                           screen_width, screen_height,
                                                           return_ndc=True)
            pt2d_gt1 = project_3d_point_to_image_Omniverse(obj1["pos_3d"], 
                                                           gt_cam_view_transform1, 
                                                           gt_cam_projection_transform1, 
                                                           screen_width, screen_height,
                                                           return_ndc=True)
            # Use ground truth 2D foot positions for triangulation
            foot_2d_0 = np.array(pt2d_gt0)
            foot_2d_1 = np.array(pt2d_gt1)
            # Triangulate 3D points
            pt3d_gt = triangulate_points(gt_proj_mat0, gt_proj_mat1, foot_2d_0, foot_2d_1)
            if not check_if_object_is_valid(obj0, obj1, pt2d_gt0, pt2d_gt1, 
                                            screen_width, screen_height, 
                                            min_bbox_w, min_bbox_h, 
                                            min_boundary_x, min_boundary_y):
                continue

            if pt3d_gt is None:
                continue
            pt3d_gt_list.append(pt3d_gt)
    return pt3d_gt_list


def prepare_data_for_adjustment_from_gt(gt_proj_mat0, gt_proj_mat1, 
                                est_proj_mat0, est_proj_mat1,
                                gt_cam_view_transform0, gt_cam_view_transform1, 
                                gt_cam_projection_transform0, 
                                gt_cam_projection_transform1, 
                                obj_det_path0, obj_det_path1, 
                                image_path0, image_path1,
                                eval_params, scale, R, t):
    # find the number of images in the image_path
    image_files = glob.glob(image_path0 + "/*.png")
    image_files.sort()
    num_images = len(image_files)
    image0 = cv2.imread(image_files[0])
    screen_width = image0.shape[1]
    screen_height = image0.shape[0]
    min_bbox_w = eval_params['min_bbox_w']
    min_bbox_h = eval_params['min_bbox_h']
    min_boundary_x = eval_params['min_boundary_x']
    min_boundary_y = eval_params['min_boundary_y']

    pt3d_gt_list = []
    pt3d_est_list = []
    pt3d_est_w_list = []
    pts2d_list = []
    for i in tqdm(range(num_images)): 
        # load ground truth data
        obj_det0 = load_obj_det_data(obj_det_path0, i)
        obj_det1 = load_obj_det_data(obj_det_path1, i)
        # Convert to dictionary for faster lookup
        obj_det0_dict = {obj["id"]: obj for obj in obj_det0}
        obj_det1_dict = {obj["id"]: obj for obj in obj_det1}

        for obj_id in obj_det0_dict.keys():
            if obj_id not in obj_det1_dict.keys():
                continue
            # Get object data
            obj0 = obj_det0_dict[obj_id]
            obj1 = obj_det1_dict[obj_id]
        
            # Check if the object is visible in both images
            pt2d_gt0 = project_3d_point_to_image_Omniverse(obj0["pos_3d"], 
                                                           gt_cam_view_transform0, 
                                                           gt_cam_projection_transform0, 
                                                           screen_width, screen_height,
                                                           return_ndc=True)
            pt2d_gt1 = project_3d_point_to_image_Omniverse(obj1["pos_3d"], 
                                                           gt_cam_view_transform1, 
                                                           gt_cam_projection_transform1, 
                                                           screen_width, screen_height,
                                                           return_ndc=True)
            # Use ground truth 2D foot positions for triangulation
            foot_2d_0 = np.array(pt2d_gt0, dtype=np.float64)
            foot_2d_1 = np.array(pt2d_gt1, dtype=np.float64)
            # Triangulate 3D points
            pt3d_gt = triangulate_points(gt_proj_mat0, gt_proj_mat1, foot_2d_0, foot_2d_1)
            pt3d_est = triangulate_points(est_proj_mat0, est_proj_mat1, foot_2d_0, foot_2d_1)
            if not check_if_object_is_valid(obj0, obj1, pt2d_gt0, pt2d_gt1, 
                                            screen_width, screen_height, 
                                            min_bbox_w, min_bbox_h, 
                                            min_boundary_x, min_boundary_y):
                continue
            if pt3d_gt is None:
                continue
            if pt3d_est is not None and pt3d_gt is not None:
                # Apply scale, R, t transformation
                pt3d_est_w = scale * np.dot(R, pt3d_est.flatten()) + t

                # invert the scale, R, t transformation
                R_inv = np.linalg.inv(R)
                pt3d_est_inv = 1.0/scale * np.dot(R_inv, (pt3d_est_w.flatten()- t))
            else:
                continue
            pt3d_gt_list.append(pt3d_gt)
            pt3d_est_list.append(pt3d_est)
            pt3d_est_w_list.append(pt3d_est_w)
            pts2d_list.append((foot_2d_0, foot_2d_1))
    pt3d_gt_list = np.array(pt3d_gt_list, dtype=np.float64).squeeze()
    pt3d_est_list = np.array(pt3d_est_list, dtype=np.float64).squeeze()
    pt3d_est_w_list = np.array(pt3d_est_w_list, dtype=np.float64).squeeze()
    pts2d_list = np.array(pts2d_list, dtype=np.float64).squeeze()
    return pt3d_gt_list, pt3d_est_list, pt3d_est_w_list, pts2d_list

'''
def prepare_data_for_adjustment_from_mv_calib(cfg,
                                est_proj_mat1, est_proj_mat2,
                                pairwise_calib_output_dir,
                                camera_pair,
                                eval_params, scale, R, t):
    cam_A = camera_pair[0]
    cam_B = camera_pair[1]
    # read the 2-view calibration data from the json file
    data_path = os.path.join(pairwise_calib_output_dir, 
                    f'cam{cam_A}_cam{cam_B}/calib_results.json') 
    with open(data_path, "r") as f:
        data = json.load(f)
    num_images = len(data['matches'])

    pt3d_est_list = []
    pt3d_est_w_list = []
    pts2d_list = []
    for i in tqdm(range(num_images)): 
        # load pairwise calibration data
        frame_data = data['matches'][i]
        id_map_A2B = frame_data['id_map_A2B']

        for tid1 in id_map_A2B.keys():
            tid2 = str(id_map_A2B[tid1])
            # Use 2D foot positions for triangulation
            foot_2d_1= np.array(frame_data['image_position_A'][tid1], dtype=np.float64)
            foot_2d_2= np.array(frame_data['image_position_B'][tid2], dtype=np.float64)
        
            # Triangulate 3D points
            pt3d_est = triangulate_points(est_proj_mat1, est_proj_mat2, foot_2d_1, foot_2d_2)
            if pt3d_est is not None:
                # Apply scale, R, t transformation
                pt3d_est_w = scale * np.dot(R, pt3d_est.flatten()) + t
            else:
                continue
            pt3d_est_list.append(pt3d_est)
            pt3d_est_w_list.append(pt3d_est_w)
            pts2d_list.append((foot_2d_1, foot_2d_2))
    pt3d_est_list = np.array(pt3d_est_list, dtype=np.float64).squeeze()
    pt3d_est_w_list = np.array(pt3d_est_w_list, dtype=np.float64).squeeze()
    pts2d_list = np.array(pts2d_list, dtype=np.float64).squeeze()
    return pt3d_est_list, pt3d_est_w_list, pts2d_list
'''

def make_layout_image(camera_pairs:list, gt_dirs:list, gt_proj_mats:list, 
                      gt_cam_view_transforms:list, gt_cam_projection_transforms:list,
                      eval_params:dict):
    """ Make layout image for the BEV view from ground truth data
    
    This is for demo purpose. The layout image should be given by the user eventually.

    """
    img_w = 1000
    img_h = 1000
    layout_img = np.ones((img_h, img_w, 3), dtype=np.uint8) * 255 # white background

    pts3d_gt_all = []
    for cam_pair in camera_pairs:
        cam_id0 = cam_pair[0]
        cam_id1 = cam_pair[1]
        gt_dir0 = gt_dirs[cam_id0]
        gt_dir1 = gt_dirs[cam_id1]
        obj_det_path0 = f"{gt_dir0}/object_detection"
        obj_det_path1 = f"{gt_dir1}/object_detection"
        image_path0 = f"{gt_dir0}/rgb"
        image_path1 = f"{gt_dir1}/rgb"
    
        pts3d_gt = get_3D_points_from_obj_det(gt_proj_mats[cam_id0], 
                     gt_proj_mats[cam_id1], 
                     gt_cam_view_transforms[cam_id0], 
                     gt_cam_view_transforms[cam_id1], 
                     gt_cam_projection_transforms[cam_id0], 
                     gt_cam_projection_transforms[cam_id1], 
                     obj_det_path0, obj_det_path1, 
                     image_path0, image_path1,
                     eval_params)

        pts3d_gt_all.extend(pts3d_gt)

    pts3d_gt_all = np.array(pts3d_gt_all, dtype=np.float64).squeeze()
    # Parameters for BEV scaling and translation
    margin = 10 # pixels
    min_x = min(pts3d_gt_all[:, 0]) - margin
    max_x = max(pts3d_gt_all[:, 0]) + margin
    min_y = min(pts3d_gt_all[:, 1]) - margin
    max_y = max(pts3d_gt_all[:, 1]) + margin
    width = max_x - min_x
    height = max_y - min_y
    cx = (min_x + max_x) / 2
    cy = (min_y + max_y) / 2
    scale = min(img_w / width, img_h / height) * 0.8
    offset_x = img_w // 2 - cx * scale
    offset_y = img_h // 2 + cy * scale
    print(f'width: {width}, height: {height}, scale: {scale}')

    # draw layout image
    pts2d_bev = convert_3D_points_to_bev(pts3d_gt_all, scale, offset_x, offset_y)
    layout_img = draw_points(layout_img, pts2d_bev, (150, 150, 150), size=5)
    transform = {'scale': scale, 'offset_x': offset_x, 'offset_y': offset_y}
    return layout_img, transform

def save_results(feature2d_list, pts3d_c0_adjusted_list, output_path):
    for i in range(len(feature2d_list)):
        feature2d = feature2d_list[i]
        pts3d_c0_adjusted = pts3d_c0_adjusted_list[i]
        data = []
        for j in range(len(feature2d)):
            pt_data = {'feature2d_1': feature2d[j][0].tolist(), 
                       'feature2d_2': feature2d[j][1].tolist(),
                       'pts3d_adjusted': pts3d_c0_adjusted[j].tolist()}
            data.append(pt_data)
        with open(f'{output_path}/manual_adjustment_data_{i:02d}.json', 'w') as f:
            json.dump(data, f, indent=2)

def visualize_results(pts3d_gt_list, pts3d_w_list, pts3d_w_adjusted_list, 
                      pts3d_c0_list, pts3d_c0_adjusted_list, scale=0.01):
    # visualize for verification
    for i in range(len(pts3d_w_list)):
        if len(pts3d_gt_list) > 0:
            pts3d_gt = pts3d_gt_list[i]
        pts3d_w = pts3d_w_list[i] * scale
        if len(pts3d_w_adjusted_list) > 0:
            pts3d_w_adjusted = pts3d_w_adjusted_list[i] * scale
        pts3d_c0 = pts3d_c0_list[i] * scale
        if len(pts3d_w_adjusted_list) > 0:
            pts3d_c0_adjusted = pts3d_c0_adjusted_list[i] * scale
        # draw 3D plots
        fig = plt.figure()
        ax = fig.add_subplot(121, projection='3d')
        if len(pts3d_gt_list) > 0:
            ax.scatter(pts3d_gt[:, 0], pts3d_gt[:, 1], pts3d_gt[:, 2], c='g', marker='o')
        ax.scatter(pts3d_w[:, 0], pts3d_w[:, 1], pts3d_w[:, 2], c='r', marker='o')
        if len(pts3d_w_adjusted_list) > 0:
            ax.scatter(pts3d_w_adjusted[:, 0], pts3d_w_adjusted[:, 1], 
                       pts3d_w_adjusted[:, 2], c='b', marker='o')
        ax.set_xlabel('X')
        ax.set_ylabel('Y')
        ax.set_zlabel('Z')
        ax.set_zlim(-1, 1)
        ax.set_title('3D Points in World Frame')
        ax.legend(['Original', 'Adjusted'])

        ax = fig.add_subplot(122, projection='3d')
        if len(pts3d_c0_list) > 0:
            ax.scatter(pts3d_c0[:, 0], pts3d_c0[:, 1], pts3d_c0[:, 2], c='r', marker='o')
        if len(pts3d_c0_adjusted_list) > 0:
            ax.scatter(pts3d_c0_adjusted[:, 0], pts3d_c0_adjusted[:, 1], 
                       pts3d_c0_adjusted[:, 2], c='b', marker='o')
        ax.set_xlabel('X')
        ax.set_ylabel('Y')
        ax.set_zlabel('Z')
        ax.set_title('3D Points in Camera 0 Frame')
        ax.legend(['Original', 'Adjusted'])
        plt.show()

def detect_tags(image):
    """ Detect AprilTags in image """
    detector = apriltag.Detector()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    tags = detector.detect(gray)
    
    # Draw detected tags on image for debugging
    for tag in tags:
        for corner in tag.corners:
            cv2.circle(image, (int(corner[0]), int(corner[1])), 5, (0, 255, 0), -1)
    # save image
    #cv2.imwrite("tmp/detected_tags_03.png", image)
    return tags

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

def load_gt_camera_parameters(gt_dirs):
    """ Load ground truth camera parameters (for gt_cam0 anchor) """
    proj_mats_gt = {}
    
    # Handle both dictionary and list formats for backward compatibility
    if isinstance(gt_dirs, dict):
        for cam_id, gt_dir in gt_dirs.items():
            # Load ground truth camera parameters
            cam_param_path = f"{gt_dir}/camera_params/camera_params_00000.json"
            if os.path.exists(cam_param_path):
                with open(cam_param_path, "r") as f:
                    cam_param = json.load(f)
                # Transpose is needed for the row-column-major conversion
                cam_view_transform = np.array(cam_param["cameraViewTransform"]).reshape((4, 4))
                cam_view_transform = cam_view_transform.T
                cam_projection_transform = np.array(cam_param["cameraProjection"]).reshape((4, 4))
                cam_projection_transform = cam_projection_transform.T
                # ground truth projection matrix
                screen_width, screen_height = 1920, 1080
                proj_mat_gt = get_projection_matrix(cam_view_transform, 
                                                    cam_projection_transform, 
                                                    screen_width, screen_height)
                proj_mats_gt[cam_id] = proj_mat_gt
            else:
                print(f"Warning: GT camera params not found: {cam_param_path}")
    
    return proj_mats_gt

def get_projection_matrix(cam_view_transform, cam_projection_transform, screen_width, screen_height):
    """ Get projection matrix from camera transforms """
    # This function should match the one in evaluate_multi_cam.py
    # For now, return a placeholder - you may need to copy the full implementation
    return cam_projection_transform @ cam_view_transform

def save_transform(scale, R, t, H_cam0_layout, output_path):
    """ Save transform """
    transforms = {}
    transform_3d = {}
    transform_3d['scale'] = scale.tolist()
    transform_3d['R'] = R.tolist()
    transform_3d['t'] = t.tolist()
    transform_2d = {}
    transform_2d['H_cam0_layout'] = H_cam0_layout.tolist()
    transforms['transform_3d'] = transform_3d
    transforms['transform_2d'] = transform_2d
    with open(f'{output_path}', 'w') as f:
        json.dump(transforms, f, indent=2)

def get_homography_matrix(alignment_data):
    """ Calculate homography matrix from camera 0 to layout using alignment data
    
    Args:
        alignment_data (list): alignment data with 4 corresponding points
                              Each element: [(x, y) of camera 0, (x, y) of camera 1, (x, y) of layout]
    Returns:
        np.ndarray: 3x3 homography matrix from camera 0 to layout
    """
    # Extract camera 0 and layout points
    cam0_points = []
    layout_points = []
    
    for i in range(len(alignment_data)):
        pt2d_cam0 = alignment_data[i][0]  # (x, y) of camera 0
        pt2d_layout = alignment_data[i][2]  # (x, y) of layout
        
        cam0_points.append(pt2d_cam0)
        layout_points.append(pt2d_layout)
    
    # Convert to numpy arrays
    cam0_points = np.array(cam0_points, dtype=np.float32)
    layout_points = np.array(layout_points, dtype=np.float32)
    
    # Calculate homography matrix using OpenCV
    H, _ = cv2.findHomography(cam0_points, layout_points, method=cv2.RANSAC, ransacReprojThreshold=5.0)
    
    return H

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

def load_camera_parameters(mv_output_dir, cfg):
    """ Load camera parameters from bundle adjustment results (same as evaluation script) """
    proj_mats = {}
    
    # Use bundle adjustment results path (same as evaluation script)
    ba_output_path = f"{mv_output_dir}/BA_output/results_ba/refined"
    
    # Get camera directories from config (same as evaluation script)
    cam_dirs = cfg['cam_dir']
    
    for cam_dir in cam_dirs:
        # Extract camera ID from directory name (e.g., 'cam_00' -> 0)
        if cam_dir.startswith('cam_'):
            cam_id_str = cam_dir.split('_')[1]
            cam_id = int(cam_id_str)
        else:
            print(f"Warning: Invalid camera directory format: {cam_dir}")
            continue
        
        # Load bundle adjustment results (same path as evaluation script)
        cam_info_path = f'{ba_output_path}/camInfo_{cam_id:02d}.yaml'
        
        if os.path.exists(cam_info_path):
            with open(cam_info_path, 'r') as f:
                cam_info = yaml.safe_load(f)
            
            # Extract projection matrix (same as evaluation script)
            if 'projectionMatrix_3x4' in cam_info:
                proj_mat = np.array(cam_info['projectionMatrix_3x4']).reshape(3, 4)
                proj_mats[cam_id] = proj_mat
                print(f"Loaded camera {cam_id} parameters from {cam_info_path}")
            else:
                print(f"Error: projectionMatrix_3x4 not found in {cam_info_path}")
                print(f"Available keys: {list(cam_info.keys())}")
        else:
            print(f"Warning: Bundle adjustment result not found: {cam_info_path}")
    
    return proj_mats

def generate_transform_from_alignment_data(alignment_data_path, mv_output_dir, cfg, eval_config_path=None):
    """ Generate transform_cam0_to_map.json from existing alignment_data.json 
        transform_cam0_to_map.json contains
        - 3D transformation from camera 0 to layout frame: scale, R, t
        - 2D transformation from camera 0 to layout frame: homography matrix H

    Args:
        alignment_data_path (str): path to alignment_data.json
        mv_output_dir (str): path to multi-view calibration output directory
        cfg (dict): configuration
        eval_config_path (str): path to eval_config.yaml
    Returns:
        bool: True if transform generation is successful, False otherwise
    """
    
    # Load evaluation config to get anchor type
    if eval_config_path and os.path.exists(eval_config_path):
        with open(eval_config_path, 'r') as f:
            eval_params = yaml.safe_load(f)
        anchor = eval_params.get('anchor', 'layout')
    else:
        # Default to layout if no eval config provided
        anchor = 'layout'
        print("Warning: No eval config provided, defaulting to 'layout' anchor")
    
    print(f"Using anchor: {anchor}")
    
    # Load camera parameters from bundle adjustment results (same as evaluation script)
    proj_mats = load_camera_parameters(mv_output_dir, cfg)
    
    if len(proj_mats) < 2:
        print("Error: Need at least 2 cameras for transform generation")
        return False
    
    # Use same camera selection logic as evaluation script
    sorted_cam_ids = sorted(proj_mats.keys())
    cam_id_0 = sorted_cam_ids[0]
    cam_id_1 = sorted_cam_ids[1]
    
    proj_mat0 = proj_mats[cam_id_0]
    proj_mat1 = proj_mats[cam_id_1]
    
    print(f"Using cameras {cam_id_0} and {cam_id_1} for transform calculation")
    
    # Handle different anchor cases (same as evaluation script)
    if anchor == 'layout':
        # Load alignment data
        with open(alignment_data_path, 'r') as f:
            alignment_data = json.load(f)
        
        scale_est, R_est, t_est = get_transform_from_cam0_to_layout(alignment_data, proj_mat0, proj_mat1)
        print('--------------------------------')
        print('Transform from cam 0 to layout')
        print('scale', scale_est)
        print('R local to global', R_est)
        print('t local to global', t_est)

        H_cam0_layout = get_homography_matrix(alignment_data)
        print('Homography matrix from cam 0 to layout')
        print(H_cam0_layout)

    elif anchor == 'apriltag':
        # Load background images for AprilTag detection
        # Need to get input_base_dir from mv_output_dir structure
        input_base_dir = os.path.join(mv_output_dir, '..', 'single_view_results')
        sv_out = get_single_view_calib_dirs(cfg, input_base_dir=input_base_dir)
        
        # Map camera IDs to directory indices
        cam_id_to_index = {}
        for i, cam_dir in enumerate(cfg['cam_dir']):
            if cam_dir.startswith('cam_'):
                cam_id_str = cam_dir.split('_')[1]
                cam_id_int = int(cam_id_str)
                cam_id_to_index[cam_id_int] = i
        
        image0_path = f"{sv_out[cam_id_to_index[cam_id_0]]}/rectified.jpg"
        image1_path = f"{sv_out[cam_id_to_index[cam_id_1]]}/rectified.jpg"
        
        if not os.path.exists(image0_path) or not os.path.exists(image1_path):
            print(f"Error: Background images not found for AprilTag detection")
            print(f"Expected: {image0_path}, {image1_path}")
            return False
        
        image0 = cv2.imread(image0_path)
        image1 = cv2.imread(image1_path)
        
        scale_est, R_est, t_est = get_transform_from_cam0_to_omniworld(image0, image1, proj_mat0, proj_mat1)
        print('--------------------------------')
        print('Transform from cam 0 to Omniworld')
        print('scale', scale_est)
        print('R local to global', R_est)
        print('t local to global', t_est)
        
    elif anchor == 'gt_cam0':
        # Load alignment data and ground truth camera parameters
        with open(alignment_data_path, 'r') as f:
            alignment_data = json.load(f)
        
        # Need ground truth directory - this should be provided as parameter
        # For now, assume it's not available and show error
        print("Error: gt_cam0 anchor requires ground truth data which is not available in layout alignment context")
        print("This anchor is only supported during evaluation with ground truth data")
        return False
        
    else:
        print(f"Error: Invalid anchor '{anchor}'. Supported: layout, apriltag, gt_cam0")
        return False
    
    # Save transform
    transform_output_path = f'{mv_output_dir}/BA_output/results_ba_scaled_world/transform_cam0_to_map.json'
    os.makedirs(os.path.dirname(transform_output_path),  exist_ok=True)
    save_transform(scale_est, R_est, t_est, H_cam0_layout,transform_output_path)
    print(f'Saved transform from cam 0 to layout to: {transform_output_path}')
    
    return True

def find_scale_factor(alignment_data, proj_mat0, proj_mat1, map_px_per_m):
    """ Find scale factor from alignment data """
    # transform from camera 0(unknown scale) to layout(pixel)
    s_0_map, R_0_map, t_0_map = get_transform_from_cam0_to_layout(alignment_data, proj_mat0, proj_mat1)
    
    # 3D points in camera 0 (unknown scale)
    keypoints_cam0 = []
    #keypoints_layout = []
    for i in range(len(alignment_data)):
        pt2d_0 = alignment_data[i][0] # (x, y) of camera 0
        pt2d_1 = alignment_data[i][1] # (x, y) of camera 1
        #pt2d_layout = alignment_data[i][2] # (x, y) of layout
        pt3d = triangulate_points(proj_mat0, proj_mat1, pt2d_0, pt2d_1)
        keypoints_cam0.append(pt3d[0])
        #keypoints_layout.append([pt2d_layout[0], pt2d_layout[1], 0])
    keypoints_cam0 = np.array(keypoints_cam0, dtype=np.float64)
    #keypoints_layout = np.array(keypoints_layout, dtype=np.float64)
    
    # 3D points in map (pixel scale)
    keypoints_map = []
    for i in range(len(keypoints_cam0)):
        pt3d = keypoints_cam0[i]
        pt3d_map = s_0_map * np.dot(R_0_map, pt3d.flatten()) + t_0_map
        keypoints_map.append(pt3d_map)
    keypoints_map = np.array(keypoints_map, dtype=np.float64)
    
    # pixel to meter
    keypoints_map = keypoints_map / map_px_per_m
    
    scale_factor = []
    n = len(keypoints_map)
    for i in range(n):
        # distance between two points in map
        distance_map = np.linalg.norm(keypoints_map[i] - keypoints_map[(i+1)%n])

        # distance between two points in camera 0
        distance_cam0 = np.linalg.norm(keypoints_cam0[i] - keypoints_cam0[(i+1)%n])

        # scale factor
        scale_factor.append(distance_map / distance_cam0)
    scale_factor = np.array(scale_factor)
    mean_scale = np.mean(scale_factor)
    print(f'Scale factor: {mean_scale}')
    return mean_scale

def apply_scale_factor(proj_mats:dict, scale_factor:float):
    """ Apply scale factor to projection matrices """
    proj_mats_scaled = {}
    for cam_id, proj_mat in proj_mats.items():
        # decompose projection matrix
        param = cv2.decomposeProjectionMatrix(proj_mat)
        K = param[0]
        R = param[1]
        C = (param[2][:3]/param[2][3]).flatten()
        t = -R @ C
        # apply scale factor
        t_scaled = t * scale_factor
        # Recompose projection matrix
        proj_mat_scaled = K @ np.concatenate((R, t_scaled.reshape(-1, 1)), axis=1)
        proj_mats_scaled[cam_id] = proj_mat_scaled
    return proj_mats_scaled

def scale_projection_matrix(alignment_data, mv_output_dir, eval_config_path):
    """ Scale projection matrices using scale factor """
    # Load evaluation config to get anchor type
    if eval_config_path and os.path.exists(eval_config_path):
        with open(eval_config_path, 'r') as f:
            eval_params = yaml.safe_load(f)
        map_px_per_m= eval_params.get('layout_px_per_m', 1)
        print(f"Layout px per m: {map_px_per_m}")
    else:
        # Default to layout if no eval config provided
        map_px_per_m = 1
        print("Warning: No eval config provided, defaulting to 'layout_px_per_m' 1")
    
    # Load camera parameters from bundle adjustment results (same as evaluation script)
    proj_mats = load_camera_parameters(mv_output_dir, cfg) # dictionary
    
    if len(proj_mats) < 2:
        print("Error: Need at least 2 cameras for transform generation")
        return False
    
    # Use same camera selection logic as evaluation script
    sorted_cam_ids = sorted(proj_mats.keys())
    cam_id_0 = sorted_cam_ids[0]
    cam_id_1 = sorted_cam_ids[1]
    
    proj_mat0 = proj_mats[cam_id_0]
    proj_mat1 = proj_mats[cam_id_1]

    scale_factor = find_scale_factor(alignment_data, proj_mat0, proj_mat1, 
                                     map_px_per_m)
    proj_mats_scaled = apply_scale_factor(proj_mats, scale_factor)
    return proj_mats_scaled

def transform_projection_matrix_to_global(proj_mats_scaled, alignment_data, map_px_per_m):
    """ Transform projection matrices to the global frame """
    sorted_cam_ids = sorted(proj_mats_scaled.keys())
    cam_id_0 = sorted_cam_ids[0]
    cam_id_1 = sorted_cam_ids[1]
    
    proj_mat0 = proj_mats_scaled[cam_id_0]
    proj_mat1 = proj_mats_scaled[cam_id_1]
    
    # 3D points in camera 0 (scaled(meter) in camera 0 frame)
    keypoints_cam0_m = [] # meter
    keypoints_layout_px = [] # pixel 
    for i in range(len(alignment_data)):
        pt2d_0 = alignment_data[i][0] # (x, y) of camera 0
        pt2d_1 = alignment_data[i][1] # (x, y) of camera 1
        pt2d_layout = alignment_data[i][2] # (x, y) of layout
        pt3d = triangulate_points(proj_mat0, proj_mat1, pt2d_0, pt2d_1)
        keypoints_cam0_m.append(pt3d[0])
        keypoints_layout_px.append([pt2d_layout[0], pt2d_layout[1], 0])
    keypoints_cam0_m = np.array(keypoints_cam0_m, dtype=np.float64)
    # pixel to meter
    keypoints_layout_m = np.array(keypoints_layout_px, dtype=np.float64) / map_px_per_m

    # world x axis: right, y axis: up 
    # layout x axis: right, y axis: down 
    # y coordinate is flipped (y = -y) 
    keypoints_layout_m[:, 1] = -keypoints_layout_m[:, 1]

    scale, R, t = umeyama_alignment(keypoints_layout_m, keypoints_cam0_m)
    print('--------------------------------')
    print('Transform from cam 0(scaled, meter) to layout(meter)')
    print(f"Scale: {scale}, R: {R}, t: {t}")

    if scale > 1.1 or scale < 0.9:
        print('Warning: Scale factor is not within 10% of the expected value')

    T_cam0_layout_m = np.concatenate((np.concatenate((scale * R, t.reshape(-1, 1)), axis=1), 
                                    np.array([[0, 0, 0, 1]])), axis=0)
    def normalize_intrinsic_matrix(proj_mat:np.ndarray) -> np.ndarray:
        """
        Normalize intrinsic matrix of the projection matrix to make K[2,2] = 1

        Triangulation is not affected by the scale factor of the intrinsic matrix
        because the projeciton matrix is scale invariant and we use homogeneous coordinates.
        However, SV3DT and MV3DT are significantly affected by the scale factor of the intrinsic matrix.
        Therefore, we normalize the intrinsic matrix to make K[2,2] = 1.

        Args:
            proj_mat (np.ndarray): Projection matrix

        Returns:
            normalized_proj_mat (np.ndarray): Normalized projection matrix
        """
        # decompose projection matrix
        param = cv2.decomposeProjectionMatrix(proj_mat)
        K = param[0]
        R = param[1]
        C = (param[2][:3]/param[2][3]).flatten()
        t = -R @ C
        # normalize intrinsic matrix
        K_norm = K / K[2,2]
        # recompose projection matrix
        normalized_proj_mat = K_norm @ np.concatenate((R, t.reshape(-1, 1)), axis=1)
        return normalized_proj_mat

    proj_mats_scaled_global = {}
    for cam_id, proj_mat_cam0_image in proj_mats_scaled.items():
        P = proj_mat_cam0_image @ np.linalg.inv(T_cam0_layout_m)
        proj_mats_scaled_global[cam_id] = normalize_intrinsic_matrix(P)

    return proj_mats_scaled_global

def post_processing(alignment_data, mv_output_dir, eval_config_path):
    """
    Post-processing function to scale projection matrices and transform them to the global frame.

    Args:
        alignment_data (dict or np.ndarray): Alignment data for scaling.
        mv_output_dir (str): Output directory for multi-view calibration.
        eval_config_path (str): Path to the evaluation config file.

    Returns:
        proj_mats_scaled_global (dict): Scaled projection matrices in the global frame.

    """
    # Load evaluation config to get anchor type
    if eval_config_path and os.path.exists(eval_config_path):
        with open(eval_config_path, 'r') as f:
            eval_params = yaml.safe_load(f)
        map_px_per_m= eval_params.get('layout_px_per_m', 1)
        print(f"Layout px per m: {map_px_per_m}")
    else:
        # Default to layout if no eval config provided
        map_px_per_m = 1
        print("Warning: No eval config provided, defaulting to 'layout_px_per_m' 1")
    # Scale projection matrices using scale factor
    proj_mats_scaled = scale_projection_matrix(alignment_data, mv_output_dir, eval_config_path)
    if proj_mats_scaled:
        print("Projection matrices scaled successfully.")
        for cam_id in proj_mats_scaled.keys():
            cam_param = {}
            # Save in the same order as your example: K, R, projectionMatrix_3x4, t
            #cam_param['K'] = K_transformed[cam_id].tolist()
            #cam_param['R'] = Rglob_transformed[cam_id].tolist()
            cam_param['projectionMatrix_3x4'] = proj_mats_scaled[cam_id].tolist()
            #cam_param['t'] = tglob_transformed[cam_id].tolist()
            with open(f'{mv_output_dir}/BA_output/results_ba_scaled/camInfo_{cam_id:02d}.yaml', 'w') as f:
                yaml.dump(cam_param, f)
        # proj_mats_scaled is still in camera 0 frame
        # need to transform to the global(map) frame
        proj_mats_scaled_global = transform_projection_matrix_to_global(proj_mats_scaled, alignment_data, map_px_per_m)
        if proj_mats_scaled_global:
            if not os.path.exists(f'{mv_output_dir}/BA_output/results_ba_scaled_world'):
                os.makedirs(f'{mv_output_dir}/BA_output/results_ba_scaled_world', exist_ok=True)

            print("Projection matrices transformed to global frame successfully.")
            for cam_id in proj_mats_scaled_global.keys():
                cam_param = {}
                cam_param['projectionMatrix_3x4'] = proj_mats_scaled_global[cam_id].tolist()
                with open(f'{mv_output_dir}/BA_output/results_ba_scaled_world/camInfo_{cam_id:02d}.yaml', 'w') as f:
                    yaml.dump(cam_param, f)
        
        # save transform_world_to_map.json
        sorted_cam_ids = sorted(proj_mats_scaled_global.keys())
        cam_id_0 = sorted_cam_ids[0]
        cam_id_1 = sorted_cam_ids[1]
        proj_mat0_scaled_global = proj_mats_scaled_global[cam_id_0]
        proj_mat1_scaled_global = proj_mats_scaled_global[cam_id_1]
        scale_est, R_est, t_est = get_transform_from_cam0_to_layout(alignment_data, proj_mat0_scaled_global, proj_mat1_scaled_global)

        transform_3d = {}
        transform_3d['scale'] = scale_est.tolist()
        transform_3d['R'] = R_est.tolist()
        transform_3d['t'] = t_est.tolist()
        with open(f'{mv_output_dir}/BA_output/results_ba_scaled_world/transform_world_to_map.json', 'w') as f:
            json.dump(transform_3d, f, indent=2)

        print(f'Saved transform from world to layout to: {mv_output_dir}/BA_output/results_ba_scaled_world/transform_world_to_map.json')

    else:
        print("Error: Failed to scale projection matrices.")
        exit(1)
    
    return proj_mats_scaled_global


def validate_post_processing(proj_mats_scaled_global, mv_calib_root, 
                             camera_pairs, est_paths,eval_config_path, 
                             layout_img_path, alignment_data):
    """ Validate post-processing """
    # check if pairwise calibration successfully completed
    show_trajectory = True
    for cam_pair in camera_pairs:
        cam_id0 = cam_pair[0]
        cam_id1 = cam_pair[1]
        if not os.path.exists(f'{mv_calib_root}/cam{cam_id0}_cam{cam_id1}/calib_results.json'):
            print("") 
            print(f"Warning: Pairwise calibration not completed for camera pair {cam_pair}.")
            print("Only camera locations will be shown on the layout image.")
            show_trajectory = False
            break

    with open(eval_config_path, 'r') as f:
        eval_params = yaml.safe_load(f)
    map_px_per_m = eval_params.get('layout_px_per_m', 1)

    # read layout image
    if os.path.exists(layout_img_path):
        layout_img = cv2.imread(layout_img_path)
        # read estimated projection matrices
        est_proj_mats, K_est, R_est, t_est = read_est_cam_param(est_paths)
    
        # get transform from camera 0 to layout (world frame)
        proj_mat0 = est_proj_mats[camera_pairs[0][0]]
        proj_mat1 = est_proj_mats[camera_pairs[0][1]]
        scale, R, t = get_transform_from_cam0_to_layout(alignment_data, 
                                                        proj_mat0, 
                                                        proj_mat1)

        # draw cameras on the layout image
        layout_img = draw_cameras(layout_img, camera_pairs, R_est, t_est, scale, R, t)
        cv2.imwrite('layout_img_with_camera_locations.png', layout_img)
        print(f'Saved layout image with camera locations to layout_img_with_camera_locations.png')
  
    else:
        print(f"Layout image not found in {layout_img_path}. Please provide a layout image.")
        exit()
 
    # reconstruct 3D points from projection matrices using triangulation 
    # in global(map) frame
    pts2d_bev_list = []
    scale = map_px_per_m  
    # world x axis: right, y axis: up 
    # layout x axis: right, y axis: down 
    # flip y coordinate
    R = np.eye(3) 
    R[1, 1] = -1
    t = np.zeros(3)
    with open(f'{mv_calib_root}/BA_output/results_ba_scaled_world/transform_world_to_map.json', 'r') as f:
        transform_3d = json.load(f)
        scale = transform_3d['scale']
        R = transform_3d['R']
        t = transform_3d['t']

    # BGR overlay colors. Keep enough distinct colors for large camera rigs and
    # cycle them below when the number of camera pairs exceeds this palette.
    colors = [
        (0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255),
        (255, 255, 0), (255, 0, 255), (0, 128, 255), (128, 0, 255),
        (255, 128, 0), (0, 255, 128), (128, 255, 0), (255, 0, 128),
    ]
    if not show_trajectory:
        # save the layout image with camera locations only
        for i in range(len(camera_pairs)):
            cam_id0 = camera_pairs[i][0]
            cam_id1 = camera_pairs[i][1]
            layout_img =cv2.putText(layout_img, 
                                     f'cam{cam_id0:02d}-cam{cam_id1:02d}', 
                                     (10, i*25+20), 
                                     cv2.FONT_HERSHEY_SIMPLEX, 1.0, colors[i % len(colors)], 2)
            cv2.imwrite(f'{mv_calib_root}/BA_output/results_ba_scaled_world/overlay_img_{i:02d}.png', layout_img)
            print(f'Saved overlay image to {mv_calib_root}/BA_output/results_ba_scaled_world/overlay_img_{i:02d}.png')
        return


    for cam_pair in camera_pairs:
        print("=+++++===============================================")
        print(f"Reconstructing 3D points from camera pair {cam_pair}")
        print("=====+++++===========================================")
        cam_id0 = cam_pair[0]
        cam_id1 = cam_pair[1]

        # get 3D points from pairwise calibration output
        # pts3d_est: 3D points estimated by pairwise calibration output.
        # pts3d_est_w: 3D points estimated by pairwise calibration output in 
        #              world frame (layout)
        # pts2d_list: 2D points in image frame.
        _, pts3d_est_px, _ = \
            prepare_data_for_adjustment_from_mv_calib(proj_mats_scaled_global[cam_id0], 
                                                      proj_mats_scaled_global[cam_id1],
                                                      mv_calib_root,
                                                      cam_pair,
                                                      scale, R, t)
        #print(f'pts3d_est_px: {pts3d_est_px}')
        #print(f'pts3d_est_w: {pts3d_est_px / map_px_per_m}')
        pts2d_bev = pts3d_est_px[:, :2]
        pts2d_bev_list.append(pts2d_bev)

    # Draw 3D points using the shared large-rig palette above.
    for i in range(len(pts2d_bev_list)):
        cam_id0 = camera_pairs[i][0]
        cam_id1 = camera_pairs[i][1]
        pts2d_bev = pts2d_bev_list[i]
        color = colors[i % len(colors)]
        layout_img = draw_points(layout_img, pts2d_bev, color, size=2)
        layout_img =cv2.putText(layout_img, 
                                 f'cam{cam_id0:02d}-cam{cam_id1:02d}', 
                                 (10, i*25+20), 
                                 cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
        cv2.imwrite(f'{mv_calib_root}/BA_output/results_ba_scaled_world/overlay_img_{i:02d}.png', layout_img)
        cv2.imwrite(f'{mv_calib_root}/manual_adjustment/overlay_img_{i:02d}.png', layout_img)


def main(cfg, mv_output_dir, alignment_data_path, layout_image_path, eval_config_path,
          est_paths):
    """ Main function for post-processing alignment data
    
    This function reads existing alignment_data.json (created by layout_alignment.py
    and rectified during calibration) and performs post-processing:
    - Generate transform from camera 0 to layout
    - Scale projection matrices
    - Transform to global frame

    Args:
        cfg (dict): configuration
        mv_output_dir (str): multi-view calibration output directory
        alignment_data_path (str): path to alignment_data.json
        layout_image_path (str): path to layout image
        eval_config_path (str): path to evaluation config
        est_paths (list): list of estimated camera parameters (to camera 0)
    """
    # Get single view calibration directories
    sv_out = get_single_view_calib_dirs(cfg, input_base_dir=args.input_base_dir)
    
    # Verify alignment_data.json exists
    if not os.path.exists(alignment_data_path):
        print(f"Error: alignment_data.json not found at {alignment_data_path}")
        print("Layout alignment must be run before calibration using launch_LayoutAlignment.sh")
        print("Then calibration must be run to rectify coordinates.")
        exit(1)
    
    print(f"Found alignment_data.json at {alignment_data_path}")
    print("Running post-processing to generate transforms and scaled projection matrices...")
    
    # Create manual_adjustment directory for outputs
    manual_adjustment_dir = f"{mv_output_dir}/manual_adjustment"
    os.makedirs(manual_adjustment_dir, exist_ok=True)
    
    # Load alignment data
    with open(alignment_data_path, 'r') as f:
        alignment_data = json.load(f)
    
    # Generate transform from alignment data
    success = generate_transform_from_alignment_data(alignment_data_path, mv_output_dir, cfg, eval_config_path)
    if success:
        print("Transform generation completed successfully.")
        test_homography(alignment_data, mv_output_dir, layout_image_path, f"{sv_out[0]}/rectified.jpg")
        
        # Post-processing
        # - find a scale factor 
        # - apply scale factor to projection matrices
        # - transform to global frame
        P_global_m = post_processing(alignment_data, mv_output_dir, eval_config_path)
        
        # Validate post-processing with overlay visualization
        validate_post_processing(P_global_m, mv_output_dir, camera_pairs, 
                                 est_paths, eval_config_path, layout_image_path,
                                 alignment_data)
        
        print("")
        print("Post-processing completed successfully!")
        print(f"Outputs saved to:")
        print(f"  - {mv_output_dir}/BA_output/results_ba_scaled/")
        print(f"  - {mv_output_dir}/BA_output/results_ba_scaled_world/")
        print(f"  - {mv_output_dir}/BA_output/results_ba_scaled_world/transform_cam0_to_map.json")
    else:
        print("Error: Failed to generate transform from alignment data.")
        exit(1)
    
    return

def test_homography(alignment_data, mv_output_dir, layout_image_path, cam0_image_path):
    """ Test homography transformation by visualizing points on camera 0 and layout images
    
    Args:
        alignment_data (list): alignment data with corresponding points
        mv_output_dir (str): multi-view calibration output directory
        layout_image_path (str): path to layout image
        cam0_image_path (str): path to camera 0 background image
    """
    print("Testing homography transformation...")
    # Load the saved homography matrix from transform_cam0_to_map.json
    transform_path = f'{mv_output_dir}/BA_output/results_ba_scaled_world/transform_cam0_to_map.json'
    
    if not os.path.exists(transform_path):
        print(f"Error: Transform file not found at {transform_path}")
        return
    
    with open(transform_path, 'r') as f:
        transform_data = json.load(f)
    
    # Extract homography matrix
    H_cam0_layout = np.array(transform_data['transform_2d']['H_cam0_layout'])
    print(f"Loaded homography matrix: {H_cam0_layout.shape}")
    
    # Load images
    if not os.path.exists(cam0_image_path):
        print(f"Error: Camera 0 image not found at {cam0_image_path}")
        return
        
    if not os.path.exists(layout_image_path):
        print(f"Error: Layout image not found at {layout_image_path}")
        return
    
    cam0_img = cv2.imread(cam0_image_path)
    layout_img = cv2.imread(layout_image_path)
    
    if cam0_img is None:
        print(f"Error: Could not load camera 0 image from {cam0_image_path}")
        return
        
    if layout_img is None:
        print(f"Error: Could not load layout image from {layout_image_path}")
        return
    
    print(f"Camera 0 image shape: {cam0_img.shape}")
    print(f"Layout image shape: {layout_img.shape}")
    
    # Extract camera 0 points from alignment data
    cam0_points = []
    layout_points = []
    
    for i in range(len(alignment_data)):
        pt2d_cam0 = alignment_data[i][0]  # (x, y) of camera 0
        pt2d_layout = alignment_data[i][2]  # (x, y) of layout
        
        cam0_points.append(pt2d_cam0)
        layout_points.append(pt2d_layout)
    
    cam0_points = np.array(cam0_points, dtype=np.int32)
    layout_points = np.array(layout_points, dtype=np.int32)
    
    print(f"Camera 0 points: {cam0_points}")
    print(f"Layout points: {layout_points}")
    
    # Draw circles on camera 0 image
    cam0_vis = cam0_img.copy()
    colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (255, 255, 0)]  # BGR colors
    
    for i, point in enumerate(cam0_points):
        color = colors[i % len(colors)]
        cv2.circle(cam0_vis, tuple(point), 10, color, -1)
        cv2.circle(cam0_vis, tuple(point), 15, (255, 255, 255), 2)  # White border
        # Add point number
        cv2.putText(cam0_vis, str(i+1), (point[0] + 20, point[1] - 10), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    
    # Transform camera 0 points using homography matrix
    cam0_points_homogeneous = np.hstack((cam0_points, np.ones((cam0_points.shape[0], 1))))
    transformed_points_homogeneous = H_cam0_layout @ cam0_points_homogeneous.T
    transformed_points = transformed_points_homogeneous[:2, :] / transformed_points_homogeneous[2, :]
    transformed_points = transformed_points.T.astype(np.int32)
    
    print(f"Transformed points: {transformed_points}")
    
    # Draw transformed points on layout image
    layout_vis = layout_img.copy()
    
    for i, point in enumerate(transformed_points):
        color = colors[i % len(colors)]
        cv2.circle(layout_vis, tuple(point), 10, color, -1)
        cv2.circle(layout_vis, tuple(point), 15, (255, 255, 255), 2)  # White border
        # Add point number
        cv2.putText(layout_vis, str(i+1), (point[0] + 20, point[1] - 10), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    
    # Also draw the original layout points for comparison
    for i, point in enumerate(layout_points):
        cv2.circle(layout_vis, tuple(point), 8, (0, 255, 255), 2)  # Yellow circles for original points
        cv2.putText(layout_vis, f"{i+1}'", (point[0] + 20, point[1] + 20), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    
    # Save visualization images
    output_dir = f'{mv_output_dir}/manual_adjustment'
    os.makedirs(output_dir, exist_ok=True)
    
    cam0_output_path = f'{output_dir}/homography_test_cam0.jpg'
    layout_output_path = f'{output_dir}/homography_test_layout.jpg'
    
    cv2.imwrite(cam0_output_path, cam0_vis)
    cv2.imwrite(layout_output_path, layout_vis)
    
    print(f"Saved camera 0 visualization to: {cam0_output_path}")
    print(f"Saved layout visualization to: {layout_output_path}")
    
    # Calculate and print transformation errors
    print("\nTransformation accuracy:")
    for i in range(len(transformed_points)):
        error = np.linalg.norm(transformed_points[i] - layout_points[i])
        print(f"Point {i+1}: Error = {error:.2f} pixels")
    
    print("Homography test completed!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Post-processing for layout alignment')
    parser.add_argument('-c', '--config', type=str, 
                        default='config/automagiccalib_multi_config.yaml', 
                        help='Path to the config file')
    parser.add_argument('-i', '--input_base_dir', type=str, required=True,
                        help='Single-view calibration results directory')
    parser.add_argument('-o', '--output_base_dir', type=str, required=True,
                        help='Multi-view calibration output directory')
    parser.add_argument('-a', '--alignment_base_dir', type=str, required=True,
                        help='Base directory containing alignment_data/ folder (typically the main output dir)')
    parser.add_argument('-l', '--layout_image_path', type=str, required=True,
                        help='Path to layout image file')
    args = parser.parse_args()
    print(f'Config file: {args.config}')

    # read config file
    config.load_config(args.config)
    cfg = config.get_config()

    # Validate and sanitize input paths
    mv_calib_root = validate_input_directory(args.output_base_dir, "multi-view calibration output directory")
    alignment_base_dir = validate_input_directory(args.alignment_base_dir, "alignment base directory")
    layout_image_path = validate_input_file(args.layout_image_path, "layout image file")
    
    # Path to alignment_data.json (created by layout_alignment.py, rectified during calibration)
    alignment_data_path = os.path.join(alignment_base_dir, 'alignment_data', 'alignment_data.json')
    
    # Paths for configuration and output
    ba_output_path = f"{mv_calib_root}/BA_output/results_ba/refined"
    output_path = f"{mv_calib_root}/manual_adjustment"
    output_path = validate_output_directory(output_path, "manual adjustment output directory")
    ba_scaled_path = f"{mv_calib_root}/BA_output/results_ba_scaled"
    ba_scaled_path = validate_output_directory(ba_scaled_path, "scaled camInfo output directory")
    
    # Path to evaluation config
    eval_config_path = os.path.join(os.path.dirname(args.config), 'eval_config.yaml')

    # camera_pairs are auto-generated by config.load_config() if not provided
    camera_pairs = [(cam_ids[0], cam_ids[1]) for cam_ids in cfg['camera_pairs']]
    
    # Camera IDs
    cam_ids = [f"{ids[0]:02d}" for ids in camera_pairs]
    cam_ids.append(f"{camera_pairs[-1][1]:02d}")
    est_paths = []
    for i in range(len(cam_ids)):
        est_paths.append(f'{ba_output_path}/camInfo_{i:02d}.yaml')

    # Run post-processing
    main(cfg, mv_calib_root, alignment_data_path, layout_image_path, eval_config_path,
         est_paths)

    # Successful headless runs can segfault during OpenCV/AprilTag cleanup.
    # interpreter teardown. Do not call cv2.destroyAllWindows(): a native
    # segfault there cannot be caught by Python. Flush normal output, then
    # skip teardown after successful post-processing.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
