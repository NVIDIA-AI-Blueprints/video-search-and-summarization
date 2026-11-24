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
import os
import sys
import argparse

# Add the current directory to Python path to allow importing local modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(project_root)

import config
from config import extract_cam_number
from bundle_adjustment import triangulate_points
from visualize_2_view_results import COLOR_TABLE
from core.evaluation.evaluate_multi_cam import * 


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

def prepare_data_for_adjustment_from_mv_calib( est_proj_mat1, est_proj_mat2,
                                               pairwise_calib_output_dir,
                                               camera_pair,
                                               scale, R, t):
    """ Prepare data for manual adjustment from MV calibration output

    Args:
        est_proj_mat1 (np.ndarray): projection matrix of camera 1
        est_proj_mat2 (np.ndarray): projection matrix of camera 2
        pairwise_calib_output_dir (str): path to the pairwise calibration output
        camera_pair (tuple): camera pair
        scale (float): scale of the transformation
        R (np.ndarray): rotation matrix of the transformation
        t (np.ndarray): translation vector of the transformation

    Returns:
        pt3d_est_list (np.ndarray): 3D points estimated by MV calibration output
        pt3d_est_w_list (np.ndarray): 3D points estimated by MV calibration output 
                                      in world frame (layout)
        pts2d_list (np.ndarray): 2D points in image frame
    """
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

def save_data(feature2d_list, pts3d_c0_adjusted_list, output_path):
    """ Save data for manual adjustment """
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

def draw_cameras(img, camera_pairs, R_est, t_est, scale_c2w, R_c2w, t_c2w):
    """ Draw cameras on the layout image
    """
    cam_ids = [cam_pair[0] for cam_pair in camera_pairs] 
    cam_ids.append(camera_pairs[-1][1])
    print(f'cam_ids: {cam_ids}')
    colors = [
        (0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255),
        (255, 255, 0), (255, 0, 255), (0, 128, 255), (128, 0, 255),
        (255, 128, 0), (0, 255, 128), (128, 255, 0), (255, 0, 128),
    ]
    for i, cam_id in enumerate(cam_ids):
        # Camera rigs can contain more cameras than the fixed visualization
        # palette. Reuse palette entries rather than failing the whole
        # post-processing pipeline while rendering the layout overlay.
        color = colors[i % len(colors)]
        R_est0 = R_est[cam_id]
        t_est0 = t_est[cam_id]
        cam_center = -R_est0.T @ t_est0
        cam_center = cam_center.reshape(3)
        cam_center_w = scale_c2w * np.dot(R_c2w, cam_center) + t_c2w
        img = cv2.circle(img, (int(cam_center_w[0]), int(cam_center_w[1])), 
                         5, color, -1)
        # black border
        img = cv2.circle(img, (int(cam_center_w[0]), int(cam_center_w[1])), 
                         10, (0, 0, 0), 2)
        offset_x = -40
        offset_y = 20
        cv2.putText(img, f'cam{cam_id:02d}', (int(cam_center_w[0]) + offset_x, 
                    int(cam_center_w[1]) + offset_y), 
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
        cv2.putText(img, f'cam{cam_id:02d}', (int(cam_center_w[0]) + offset_x, 
                    int(cam_center_w[1]) + offset_y), 
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 1)
    return img


def overlay_visualization(cfg, mv_calib_root, camera_pairs, est_paths, 
                          alignment_data, output_path, show_trajectory=False):
    """ Main function for overlay visualization

    Args:
        cfg (dict): configuration
        camera_pairs (list): list of camera pairs
        est_paths (list): list of estimated camera parameters
        alignment_data (list): alignment data
        output_path (str): output path
        show_trajectory (bool): whether to show trajectory
    """
    # read estimated projection matrices
    est_proj_mats, K_est, R_est, t_est = read_est_cam_param(est_paths)
    
    # get transform from camera 0 to layout (world frame)
    proj_mat0 = est_proj_mats[camera_pairs[0][0]]
    proj_mat1 = est_proj_mats[camera_pairs[0][1]]
    scale, R, t = get_transform_from_cam0_to_layout(alignment_data, 
                                                    proj_mat0, 
                                                    proj_mat1)
    print("Transform from cam 0 to layout:")
    print(f'scale: {scale}')
    print(f'R local to global: {R}')
    print(f't local to global: {t}')
    
    # read layout image
    layout_img = cv2.imread(cfg['layout_image_path'])

    # draw cameras on the layout image
    layout_img = draw_cameras(layout_img, camera_pairs, R_est, t_est, scale, R, t)

    colors = [
        (0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255),
        (255, 255, 0), (255, 0, 255), (0, 128, 255), (128, 0, 255),
        (255, 128, 0), (0, 255, 128), (128, 255, 0), (255, 0, 128),
    ]
    overlay_out_path = output_path
    if not show_trajectory:
        # save the layout image with camera locations only
        for i in range(len(camera_pairs)):
            cam_id0 = camera_pairs[i][0]
            cam_id1 = camera_pairs[i][1]
            layout_img =cv2.putText(layout_img, 
                                     f'cam{cam_id0:02d}-cam{cam_id1:02d}', 
                                     (10, i*25+20), 
                                     cv2.FONT_HERSHEY_SIMPLEX, 1.0, colors[i % len(colors)], 2)
            cv2.imwrite(f'{overlay_out_path}/overlay_img_{i:02d}.png', layout_img)
            print(f'Saved overlay image to {overlay_out_path}/overlay_img_{i:02d}.png')
        return

    # adjust each camera pair individually
    pts3d_w_adjusted_list = []
    feature2d_list = []
    pts2d_bev_list = []
    for cam_pair in camera_pairs:
        print("================================================")
        print(f"Adjusting camera pair {cam_pair}")
        print("================================================")
        cam_id0 = cam_pair[0]
        cam_id1 = cam_pair[1]
        pairwise_calib_output_dir = mv_calib_root 

        # get 3D points from pairwise calibration output
        # pts3d_est: 3D points estimated by pairwise calibration output.
        # pts3d_est_w: 3D points estimated by pairwise calibration output in 
        #              world frame (layout)
        # pts2d_list: 2D points in image frame.
        _, pts3d_est_w, _ = \
            prepare_data_for_adjustment_from_mv_calib(est_proj_mats[cam_id0], 
                                                      est_proj_mats[cam_id1],
                                                      pairwise_calib_output_dir,
                                                      cam_pair,
                                                      scale, R, t)
        pts2d_bev = pts3d_est_w[:, :2]
        pts2d_bev_list.append(pts2d_bev)
        
    # draw BEV view with the adjusted points. The view is saved as 
    # overlay_img_{i:02d}.png
    overlay_img = layout_img.copy()
    for i in range(len(pts2d_bev_list)):
        cam_id0 = camera_pairs[i][0]
        cam_id1 = camera_pairs[i][1]
        pts2d_bev = pts2d_bev_list[i]
        overlay_img = draw_points(overlay_img, pts2d_bev, colors[i], size=2)
        overlay_img =cv2.putText(overlay_img, 
                                 f'cam{cam_id0:02d}-cam{cam_id1:02d}', 
                                 (10, i*25+20), 
                                 cv2.FONT_HERSHEY_SIMPLEX, 1.0, colors[i % len(colors)], 2)
        cv2.imwrite(f'{overlay_out_path}/overlay_img_{i:02d}.png', overlay_img)
        print(f'Saved overlay image to {overlay_out_path}/overlay_img_{i:02d}.png')

    # convert 3D points in world frame (layout) to camera 0 frame for BA
    scale_c2w = scale.astype(np.float64)
    R_c2w = R.astype(np.float64)
    R_inv = np.linalg.inv(R_c2w)
    pts3d_c0_adjusted_list = []
    for i in range(len(pts3d_w_adjusted_list)):
        pts3d_w = pts3d_w_adjusted_list[i]
        if pts3d_w is None:
            pts3d_c0_adjusted_list.append(None)
            continue
        # world to camera 0 frame
        pts3d_c0_adjusted = 1.0/scale_c2w * np.dot(R_inv, (pts3d_w- t).T)
        pts3d_c0_adjusted_list.append(pts3d_c0_adjusted.T)

    # save 2D features, adjusted 3D points for BA
    save_data(feature2d_list, pts3d_c0_adjusted_list, output_path)
 
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Manual adjustment step')
    parser.add_argument('-c', '--config', type=str, 
                        default='config/automagiccalib_multi_config.yaml', 
                        help='Path to the config file')
    parser.add_argument('-i', '--input_base_dir', type=str, required=True,
                        help='Base directory for single view calibration outputs')
    parser.add_argument('-o', '--output_base_dir', type=str, required=True,
                        help='Base directory for output')
    parser.add_argument('-l', '--layout_image_path', type=str, required=True,
                        help='Path to layout image file')
    
    args = parser.parse_args()
    print(f'Config file: {args.config}')

    # read config file
    config.load_config(args.config)
    cfg = config.get_config()
    cfg['layout_image_path'] = args.layout_image_path

    camera_pairs = cfg['camera_pairs']

    # parameters
    # Validate and sanitize directory paths
    if args.input_base_dir:
        input_base_dir = validate_input_directory(args.input_base_dir, "input base directory")
    if args.output_base_dir:
        output_base_dir = validate_output_directory(args.output_base_dir, "output base directory")

    # single view calibration output
    sv_calib_dirs_list = get_single_view_calib_dirs(cfg, input_base_dir)
    # Create mapping from camera ID to directory path
    cam_dirs = cfg['cam_dir']
    sv_calib_dirs = {}
    for sv_calib_dir, cam_dir in zip(sv_calib_dirs_list, cam_dirs):
        cam_id = extract_cam_number(cam_dir)
        sv_calib_dirs[cam_id] = sv_calib_dir
    
    # output directory for multi-view calibration results
    out_base_dir = output_base_dir


    # Paths for ground truthdata and configuration
    mv_calib_root = args.output_base_dir + "/multi_view_results"
    ba_output_path = f"{mv_calib_root}/BA_output/results_ba/refined"
    alignment_path = args.output_base_dir + "/alignment_data"
    output_path = args.output_base_dir + "/multi_view_results/manual_adjustment"
    camera_pairs = [(cam_ids[0], cam_ids[1]) for cam_ids in cfg['camera_pairs']]
    est_paths = {}
    for c0, c1 in camera_pairs:
        est_paths[c0] = f'{ba_output_path}/camInfo_{c0:02d}.yaml'
        est_paths[c1] = f'{ba_output_path}/camInfo_{c1:02d}.yaml'
    
    # validate pairwise calibration results (cam?_cam?/calib_results.json)
    show_trajectory = True
    for cam_pair in camera_pairs:
        cam_id0 = cam_pair[0]
        cam_id1 = cam_pair[1]
        if not os.path.exists(f'{mv_calib_root}/cam{cam_id0}_cam{cam_id1}/calib_results.json'):
            print("") 
            print(f"Warning: Pairwise calibration not completed for camera pair {cam_pair}.")
            print("Only camera locations will be shown on the layout image.")
            show_trajectory = False
 
    # read alignment data from json file    
    # check if the file exists
    if os.path.exists(f'{alignment_path}/alignment_data.json'):
        with open(f'{alignment_path}/alignment_data.json', 'r') as f:
            alignment_data = json.load(f)
    else:
        raise ValueError(f'Alignment data file not found: {alignment_path}/alignment_data.json')

    # create output path
    os.makedirs(output_path, exist_ok=True)
    overlay_visualization(cfg, mv_calib_root, camera_pairs, est_paths, 
                          alignment_data, output_path, show_trajectory)

