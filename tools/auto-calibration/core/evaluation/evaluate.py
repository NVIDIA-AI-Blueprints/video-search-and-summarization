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
import numpy as np
import json
import os
import argparse
from tqdm import tqdm
import yaml
import glob

def detect_tags(image):
    """ Test funtion for AprilTag detection """
    #image = cv2.imread(image_path)
    # resize image
    #image = cv2.resize(image, (640, 480))
    image = image.copy()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = apriltag.Detector()
    tags = detector.detect(gray)

    # draw tags on image
    for tag in tags:
        # draw center of tag
        cx = int(tag.center[0])
        cy = int(tag.center[1])
        cv2.circle(image, (cx, cy), 5, (0, 0, 255), -1)
        cv2.putText(image, str(tag.tag_id), (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        # draw corners of tag
        for corner in tag.corners:
            cv2.circle(image, (int(corner[0]), int(corner[1])), 5, (0, 255, 0), -1)
    return tags

def load_cam_param_gt(cam_param_path):
    with open(cam_param_path, "r") as f:
        cam_param = json.load(f)
    return cam_param

def load_cam_param_est(cam_param_path):
    with open(cam_param_path, "r") as f:
        cam_param = yaml.safe_load(f)
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
        if "bounding_box_2d_loose_fast" in value["annotators"]:
            bbox_2d = value["annotators"]["bounding_box_2d_loose_fast"]
            
            # Check available 3D bounding box keys
            available_3d_keys = [k for k in value["annotators"].keys() if "bounding_box_3d" in k]
            
            if len(available_3d_keys) == 0:
                print(f"Warning: No 3D bounding box data found for object {id}. Available keys: {list(value['annotators'].keys())}")
                continue
            
            # Use the first available 3D bounding box key
            bbox_3d_key = available_3d_keys[0]
            pos_3d = np.array(value["annotators"][bbox_3d_key]["transform"][3])[:3]
            
            obj_det_data.append({
                "id": id,
                "bbox_2d": bbox_2d,
                "pos_3d": pos_3d
            })
    return obj_det_data

def get_3D_point_from_2D_point_on_floor_local(pt2d:np.ndarray, cam_proj_mat:np.ndarray):
    """ get 3D point from 2D point on floor

    Args:
        pt2d (np.ndarray): 2D point on floor
        cam_proj_mat (np.ndarray): 3x4 camera projection matrix
    Returns:
        pt3d (np.ndarray): 3D point (3x1)
    """
    # convert 2D point to homogeneous coordinates
    pt2d_h = np.array([pt2d[0], pt2d[1], 1])
    # homography matrix from floor to image plane by setting z = 0
    H_f2i = np.zeros((3, 3))
    H_f2i[:3, :2] = cam_proj_mat[:3, :2]
    H_f2i[:, 2] = cam_proj_mat[:, 3]
    # homography matrix from image plane to floor 
    H_i2f = np.linalg.inv(H_f2i)
    pt3d_h = H_i2f @ pt2d_h
    pt3d = pt3d_h[:2] / pt3d_h[2]
    pt3d = np.array([pt3d[0], pt3d[1], 0])
    return pt3d

def get_3D_point_from_2D_point_on_floor_global(pt2d:np.ndarray, 
                                               cam_proj_mat:np.ndarray, 
                                               H_local2global:np.ndarray):
    """ get 3D point from 2D point on floor

    Args:
        pt2d (np.ndarray): 2D point on floor
        cam_proj_mat (np.ndarray): 3x4 camera projection matrix
        H_local2global (np.ndarray): 3x3 homography matrix from local world 
                                     to global world on floor
    Returns:
        pt3d (np.ndarray): 3D point (3x1)
    """
    pt3d_local = get_3D_point_from_2D_point_on_floor_local(pt2d, cam_proj_mat)
    # transform 3D point to world coordinates
    pt3d_global = H_local2global @ np.array([pt3d_local[0], pt3d_local[1], 1])
    pt3d_global = pt3d_global[:2] / pt3d_global[2]
    return np.array([pt3d_global[0], pt3d_global[1], 0])

def project_3d_point_to_image_Omniverse(pt3d:np.ndarray, cam_view_transform:np.ndarray, 
                              cam_projection_transform:np.ndarray, 
                              screen_width:int, screen_height:int):
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
    #print('screen_point', screen_point)
    #print('pt2d', pt2d)
    return screen_point

def get_homography_local2global(image:np.ndarray, cam_proj_mat:np.ndarray, output_dir:str=None):
    """ get homography matrix from local world to globl world 
    Local world is per-camera coordinate system in world coordinates
    Global world is global coordinate system in world coordinates
    We need this because camera calibration results is in the local world system

    Args:
        cam_proj_mat (np.ndarray): 3x4 camera projection matrix
        output_dir (str): directory to save detected tags image
    Returns:
        H_local2global (np.ndarray): 3x3 homography matrix from local world to global world
    """
    tags = detect_tags(image)
    
    # Save detected tags image if output directory is provided
    if output_dir is not None:
        # draw tags on image copy for saving
        image_copy = image.copy()
        gray = cv2.cvtColor(image_copy, cv2.COLOR_BGR2GRAY)
        detector = apriltag.Detector()
        tags_for_viz = detector.detect(gray)
        
        for tag in tags_for_viz:
            # draw center of tag
            cx = int(tag.center[0])
            cy = int(tag.center[1])
            cv2.circle(image_copy, (cx, cy), 5, (0, 0, 255), -1)
            cv2.putText(image_copy, str(tag.tag_id), (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
            # draw corners of tag
            for corner in tag.corners:
                cv2.circle(image_copy, (int(corner[0]), int(corner[1])), 5, (0, 255, 0), -1)
        cv2.imwrite(os.path.join(output_dir, "detected_tags.png"), image_copy)
    
    # tag centers
    tag_centers = np.array([[tag.center[0], tag.center[1]] for tag in tags])
    # tag centers in local world coordinates
    tag_centers_local = []
    for tag_center in tag_centers:
        pt3d = get_3D_point_from_2D_point_on_floor_local(tag_center, cam_proj_mat)
        tag_centers_local.append([pt3d[0], pt3d[1], 1])
    tag_centers_local = np.array(tag_centers_local)
    # tag centers in global world coordinates
    # Top-left: (0, 0, 0)
    # Top-right: (2, 0, 0)
    # Bottom-left: (0, -3, 0)
    # Bottom-right: (2, -3, 0)
    # homogeneous coordinates in XY plane
    tag_centers_global = np.array([[0, 0, 1], [2, 0, 1], [0, -3, 1], [2, -3, 1]]) 
    
    # homography matrix from local world to global world
    H_local2global, _ = cv2.findHomography(tag_centers_local, tag_centers_global)
    return H_local2global

def calculate_dist_error(proj_mat, H_local2global, obj_det):
    """ Calculate L2 distance between ground truth and feet location estimated 
    by camera parameters and camera to world transform
    
    Args:
        proj_mat (np.ndarray): projection matrix
        H_local2global (np.ndarray): homography matrix from local world to global world
        obj_det (list): object detection data
    
    Returns:
        sum_l2_dist (float): sum of L2 distance between ground truth and feet location
        count (int): number of objects
    """
    sum_l2_dist = 0
    count = 0
    for obj in obj_det:
        obj_id = obj["id"]
        bbox_2d = obj["bbox_2d"]
        pos_3d = obj["pos_3d"]
        foot_2d = np.array([(bbox_2d["x_min"] + bbox_2d["x_max"]) / 2, bbox_2d["y_max"]])
        foot_3d = get_3D_point_from_2D_point_on_floor_global(foot_2d, proj_mat, H_local2global)

        # calculate L2 distance
        l2_dist = np.linalg.norm(foot_3d - pos_3d)
        sum_l2_dist += l2_dist
        count += 1

    return sum_l2_dist, count

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

def calculate_reprojection_error(proj_mat_gt, proj_mat_est, obj_det):
    """ Calculate reprojection error of feet location 
    by comparing ground truth and estimated projection matrix
    Args:
        proj_mat_gt (np.ndarray): projection matrix of ground truth
        proj_mat_est (np.ndarray): projection matrix of estimated
        obj_det (list): object detection data
    
    Returns:
        sum_reproj_error (float): sum of reprojection error
        count (int): number of objects
    """
    sum_reproj_error = 0
    count = 0
    for obj in obj_det:
        obj_id = obj["id"]
        bbox_2d = obj["bbox_2d"]
        pos_3d = obj["pos_3d"]
        # calculate reprojection error
        foot_2d_gt = project_3d_point_to_image_3x4_proj_mat(pos_3d, proj_mat_gt)
        foot_2d_est = project_3d_point_to_image_3x4_proj_mat(pos_3d, proj_mat_est)
        reproj_error = np.linalg.norm(foot_2d_gt - foot_2d_est)
        sum_reproj_error += reproj_error
        count += 1
    return sum_reproj_error, count

def calculate_reprojection_error2(proj_mat, obj_det):
    """ Calculate reprojection error of feet location 
    by comparing ground truth and estimated projection matrix
    Args:
        proj_mat (np.ndarray): projection matrix 
        obj_det (list): object detection data
    
    Returns:
        sum_reproj_error (float): sum of reprojection error
        count (int): number of objects
    """
    sum_reproj_error = 0
    count = 0
    for obj in obj_det:
        obj_id = obj["id"]
        bbox_2d = obj["bbox_2d"]
        foot_2d = np.array([(bbox_2d["x_min"] + bbox_2d["x_max"]) / 2, bbox_2d["y_max"]])
        #pos_3d = obj["pos_3d"]
        # Homography matrix by setting z = 0
        H_f2i = np.zeros((3, 3))
        H_f2i[:3, :2] = proj_mat[:3, :2]
        H_f2i[:, 2] = proj_mat[:, 3]
        # homography matrix from image plane to floor 
        H_i2f = np.linalg.inv(H_f2i)
        pos_3d = np.dot(H_i2f, np.array([foot_2d[0], foot_2d[1], 1]))
        pos_3d = pos_3d[:2] / pos_3d[2]
        pos_3d = np.array([pos_3d[0], pos_3d[1], 0])
        # calculate reprojection error
        foot_2d_est = project_3d_point_to_image_3x4_proj_mat(pos_3d, proj_mat)
        reproj_error = np.linalg.norm(foot_2d - foot_2d_est)
        sum_reproj_error += reproj_error
        count += 1
    return sum_reproj_error, count

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
        cv2.circle(image, (int(pt2d[0]), int(pt2d[1])), 5, (0, 255, 0), -1)
    return image

def get_projection_matrix(cam_view_transform:np.ndarray, 
                          cam_projection_transform:np.ndarray, 
                          screen_width:int, screen_height:int):
    """ Get projection matrix from Omniverse camera parameters 
    Omniverse camera parameters are not common. 
    This function is to convert Omniverse camera parameters to common camera parameters

    Args:
        cam_view_transform (np.ndarray): camera view transform from world to camera
        cam_projection_transform (np.ndarray): camera projection transform from camera to image
        screen_width (int): screen width
        screen_height (int): screen height
    Returns:
        proj_mat (np.ndarray): 3x4 projection matrix
    """
    int_mat = np.array([[screen_width/2, 0, screen_width/2], 
                        [0, -screen_height/2, screen_height/2], 
                        [0, 0, 1]]) # this is not true instrisics matrix. only for Omniverse camera parameters
    M_w2c = np.dot(cam_projection_transform, cam_view_transform) # 4x4
    M_w2c = np.concatenate([M_w2c[:2, :], M_w2c[3:, :]], axis=0) # 3x4
    proj_mat = np.dot(int_mat, M_w2c) # 3x4
    return proj_mat

def main(gt_cam_param_path, est_cam_param_path, obj_det_path, image_path, image_idx, output_dir=None, results_file=None):
    # Create output directory if specified
    if output_dir is not None:
        os.makedirs(output_dir, exist_ok=True)
    
    # Load ground truth camera parameters
    cam_param = load_cam_param_gt(gt_cam_param_path)
    # Transpose is needed for the row-column-major conversion
    cam_view_transform = np.array(cam_param["cameraViewTransform"]).reshape((4, 4))
    cam_view_transform = cam_view_transform.T
    cam_projection_transform = np.array(cam_param["cameraProjection"]).reshape((4, 4))
    cam_projection_transform = cam_projection_transform.T

    # find transform from local world to global world
    image = cv2.imread(f"{image_path}/rgb_{image_idx:05d}.png")
    screen_width = image.shape[1]
    screen_height = image.shape[0]

    # ground truth projection matrix
    proj_mat_gt = get_projection_matrix(cam_view_transform, 
                                        cam_projection_transform, 
                                        screen_width, screen_height)
    print('proj_mat_gt:', repr(proj_mat_gt))
    # get homography matrix from local world to global world
    H_local2global_gt = get_homography_local2global(image, proj_mat_gt, output_dir)
    print('H_local2global_gt:', repr(H_local2global_gt))
    #H_local2global = np.eye(3) # TODO: temporary

    # Load estimated camera parameters
    cam_param = load_cam_param_est(est_cam_param_path)
    proj_mat_est = np.array(cam_param["projectionMatrix_3x4"]).reshape((3, 4))
    # print('proj_mat_est:', repr(proj_mat_est))
    #proj_mat_est[0, 3] += 10.0 # TODO: temporary
    #proj_mat_est[1, 3] += 10.0
    #proj_mat_est[0, 1] += 1.0
    # get homography matrix from local world to global world
    H_local2global_est = get_homography_local2global(image, proj_mat_est)
    print('H_local2global_est:', repr(H_local2global_est))

    # decompose projection matrix
    out_gt = cv2.decomposeProjectionMatrix(proj_mat_gt)
    out_est = cv2.decomposeProjectionMatrix(proj_mat_est)
    # print('out_gt:', repr(out_gt))
    print('out_est:', repr(out_est))
    # print('K_gt:', repr(out_gt[0]))
    # print('R_gt:', repr(out_gt[1]))
    # print('t_gt:', repr(out_gt[2][:3]/out_gt[2][3]))
    print('K_est:', repr(out_est[0]))
    print('R_est:', repr(out_est[1]))
    print('t_est:', repr(out_est[2][:3]/out_est[2][3]))
    
    # find the number of images in the image_path
    image_files = glob.glob(image_path + "/*.png")
    num_images = len(image_files)

    avg_l2_dist_gt = 0
    total_count_gt = 0
    avg_l2_dist_est = 0
    total_count_est = 0
    avg_reproj_error_gt = 0
    total_count_gt = 0
    avg_reproj_error_est = 0
    total_count_est = 0
    avg_reproj_error_est2 = 0
    total_count_est2 = 0
    for i in tqdm(range(num_images)): 
        #print(f"Processing image {i:05d}")
        # read image
        image = cv2.imread(f"{image_path}/rgb_{i:05d}.png")

        # load ground truth data
        obj_det = load_obj_det_data(obj_det_path, i)

        # draw object detection data on image
        res_img = draw_obj_det_data(image, obj_det, cam_view_transform, cam_projection_transform)
        if output_dir is not None:
            cv2.imwrite(os.path.join(output_dir, f"obj_det_{i:05d}.png"), res_img)

        # process each image
        l2_dist_gt, count_gt = calculate_dist_error(proj_mat_gt, H_local2global_gt, obj_det)
        avg_l2_dist_gt += l2_dist_gt
        total_count_gt += count_gt
        l2_dist_est, count_est = calculate_dist_error(proj_mat_est, H_local2global_est, obj_det)
        avg_l2_dist_est += l2_dist_est
        total_count_est += count_est

        reproj_error_gt, count_gt = calculate_reprojection_error(proj_mat_gt, proj_mat_gt, obj_det)
        avg_reproj_error_gt += reproj_error_gt
        total_count_gt += count_gt
        reproj_error_est, count_est = calculate_reprojection_error(proj_mat_gt, proj_mat_est, obj_det)
        avg_reproj_error_est += reproj_error_est
        total_count_est += count_est
        reproj_error_est2, count_est2 = calculate_reprojection_error2(proj_mat_est, obj_det)
        avg_reproj_error_est2 += reproj_error_est2
        total_count_est2 += count_est2

    avg_l2_dist_gt /= total_count_gt
    avg_l2_dist_est /= total_count_est
    avg_reproj_error_gt /= total_count_gt
    avg_reproj_error_est /= total_count_est
    avg_reproj_error_est2 /= total_count_est2
    
    # Prepare results
    results = {
        "avg_l2_dist_gt": avg_l2_dist_gt,
        "avg_l2_dist_est": avg_l2_dist_est,
        "avg_reproj_error_gt": avg_reproj_error_gt,
        "avg_reproj_error_est": avg_reproj_error_est,
        "avg_reproj_error_est2": avg_reproj_error_est2,
        "total_objects_processed": total_count_gt,
        "num_images_processed": num_images
    }
    
    # print average l2 distance
    print(f"Average L2 distance (GT): {avg_l2_dist_gt}")
    print(f"Average L2 distance (EST): {avg_l2_dist_est}")
    print(f"Average reprojection error (GT): {avg_reproj_error_gt}")
    print(f"Average reprojection error (EST): {avg_reproj_error_est}")
    print(f"Average reprojection error (EST2): {avg_reproj_error_est2}")
    
    # Save results to file if specified
    if results_file is not None:
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to: {results_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Evaluate camera calibration results')
    parser.add_argument('--gt-cam-param-path', type=str, required=True,
                        help='Path to ground truth camera directory (containing camera_params/)')
    parser.add_argument('--est-cam-param-path', type=str, required=True,
                        help='Path to estimated camera parameters file (YAML)')
    parser.add_argument('--image-idx', type=int, default=10,
                        help='Image index for tag detection (default: 10)')
    parser.add_argument('--output-dir', type=str, default=None,
                        help='Directory to save output images (default: no images saved)')
    parser.add_argument('--results-file', type=str, default=None,
                        help='File to save numerical results as JSON (default: print only)')
    
    args = parser.parse_args()
    
    # Convert relative paths to absolute paths
    camera_dir = os.path.abspath(args.gt_cam_param_path)
    EST_CAM_PARAM_PATH = os.path.abspath(args.est_cam_param_path)
    OUTPUT_DIR = os.path.abspath(args.output_dir) if args.output_dir else None
    RESULTS_FILE = os.path.abspath(args.results_file) if args.results_file else None
    
    # Construct full path to GT camera parameters JSON file
    GT_CAM_PARAM_PATH = os.path.join(camera_dir, "camera_params", "camera_params_00000.json")
    
    # Full paths to required files and directories
    OBJ_DET_PATH = os.path.join(camera_dir, "object_detection")
    IMAGE_PATH = os.path.join(camera_dir, "rgb")
    
    # Image idx for tag detection
    IMAGE_IDX = args.image_idx

    main(GT_CAM_PARAM_PATH, EST_CAM_PARAM_PATH, OBJ_DET_PATH, IMAGE_PATH, IMAGE_IDX, OUTPUT_DIR, RESULTS_FILE)
