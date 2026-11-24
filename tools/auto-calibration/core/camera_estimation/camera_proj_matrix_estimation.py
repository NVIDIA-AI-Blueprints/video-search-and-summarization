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

import argparse
import yaml
import numpy as np
import matplotlib.pyplot as plt
from lu_vp_detect import VPDetection
import cv2

from parameter_search import CameraParamEstimator
from launch_camera_estimator_tool import update_translation_rotation_to_look_at_map_center, make_matrix_from_params, make_matrix_from_params_opencv   

class CamProjMatConfig:
    def __init__(self, config):
        self.focal_length = config["focal_length"]
        self.rotation_x = config["rotation_x"]
        self.rotation_z = config["rotation_z"]
        self.translation_y = config["translation_y"]
        self.human_radius = config["human_radius"]
        self.weight_iou = config["weight_iou"]
        self.iterations = config["iterations"]
        self.init_f = config["focal_length_init"]

def load_det_boxes(box_path):
    """ Load bboxes from a file """
    boxes = np.loadtxt(box_path, dtype=np.float32, delimiter=',')
    out = []
    # convert format from (x, y, w, h) to (x1, y1, x2, y2)
    for i in range(boxes.shape[0]):
        x1, y1, w, h = boxes[i, 2:6]
        out.append([x1, y1, x1+w, y1+h])
    return out 

def detect_vanishing_point_from_scene(image_path, conf):
    """ Detect vanishing point from scene
    
    Args:
        image: path to an image
        conf: config
    
    Returns:
        vp (np.array): 2D coordinates of the vanishing point of vertical lines
    """
    img = cv2.imread(image_path)
    length_thresh = 60 # Minimum length of the line in pixels
    principal_point = (img.shape[1]//2, img.shape[0]//2) # Specify a list or tuple of two coordinates
                                # First value is the x or column coordinate
                                # Second value is the y or row coordinate
    focal_length = conf.init_f # Specify focal length in pixels
    seed = None # Or specify whatever ID you want (integer)
    
    vpd = VPDetection(length_thresh, principal_point, focal_length, seed) 
    
    # Run detection
    vps = vpd.find_vps(image_path)
    
    # Display vanishing points
    print(vps)
    
    vps_2D = vpd.vps_2D
    print(vps_2D)
    
    # You can also access the VPs directly as a property
    # Only works when you run the algorithm first
    # vps = vpd.vps
    
    # You can also access the image coordinates for the vanishing points
    # Only works when you run the algorithm first
    # vps_2D = vpd.vps_2D

    # find the vanishing point of vertical lines
    # TODO: This is heuristic method. Need better algorithm
    if vps_2D[0, 0] < -1000:
        vps_2D[0] = [1, 1]
    if vps_2D[1, 0] < -1000:
        vps_2D[1] = [1, 1]
    if vps_2D[2, 0] < -1000:
        vps_2D[2] = [1, 1]
    idx = np.argmax(vps_2D[:, 1], axis=0)
    print('vertical VP: ', vps_2D[idx])
    # return only vertical vanishing point
    return vps_2D[idx] 


def write_output_file(output_path, dict_result_node, center_view_on_origin, format=""):
    """ Write yaml output file with camera projection matrix """
    fl = float(dict_result_node["focal_length"])
    translation = (
        0, 
        float(dict_result_node["translation_y"]),
        0 
    )
    rot = (
        dict_result_node["rotation_x"],
        0, 
        dict_result_node["rotation_z"],
    )
    if center_view_on_origin:
        # change translation and rotation to look at the scene center
        translation, rot = update_translation_rotation_to_look_at_map_center(translation, rot)

    rx, ry, rz = rot
    tx, ty, tz = translation
    if format == "opencv":
        proj_mat = make_matrix_from_params_opencv(fl, rx, ry, rz, tx, ty, tz)
    else:
        proj_mat = make_matrix_from_params(fl, rx, ry, rz, tx, ty, tz)

    res_yaml = {
        "cameraInfo": {
            "focalLength": fl,
            "yaw": rx,
            "roll": ry,
            "pitch": rz,
            "Tx": tx,
            "Ty": ty,
            "Tz": tz,
        },
        "H_Cam2MappedImage": {
            "PosOffset_x": -0.19,
            "PosOffset_y": 0.11,
            "Scale": 6400
        },
        "modelInfo": {
            "height": float(dict_result_node["human_height"]),
            "radius": float(dict_result_node["human_radius"]),
        },
        "best_param": {
            "focal_length": fl,
            "rotation_x": rx,
            "rotation_z": rz,
            "translation_y": ty,
        }
    }
    if format == "opencv":
        res_yaml["projectionMatrix_3x4_w2p"] = proj_mat
    else:
        res_yaml["projectionMatrix_3x4"] = proj_mat

    with open(output_path, "w") as f:
        f.write("# File generated by Camera Model Estimator Tool\n")
        score = dict_result_node["score"]
        f.write(f"# score: {score}\n")
        f.write(yaml.dump(res_yaml, sort_keys=False))
        print(f"Calibration file was saved to {output_path}")

def estimate_camera_proj_matrix(det_box_path, image_path, output_path, conf):
    """ Estimate camera projection matrix using BBO method
    
    Args:
        det_box_path: Path to the detection bounding box file
        image_path: Path to the input image for vanishing point detection
        output_path: Output directory path
        conf: Configuration parameters for BBO optimization
    """
    # load detection boxes
    det_boxes = load_det_boxes(det_box_path)
    # detect vanishing points from the scene
    vp_scene = detect_vanishing_point_from_scene(image_path, conf)

    # BBO optimzer
    bbo = CameraParamEstimator(det_boxes, vp_scene, conf)
    # run BBO optimization
    best_param, best_score, losses = bbo.run_parameter_search()

    cam_config = {} # TODO: remove
    #cam_config["human"] = {"radius": 34, "height": 175} # TODO: remove
    cam_config["human"] = {"radius": 0.34, "height": 1.75} # TODO: what is this? remove

    val = {}
    val['focal_length'] = best_param['fl']
    val['translation_y'] = best_param['ty']
    val['rotation_x'] = best_param['rx']
    val['rotation_z'] = best_param['rz']
    val['human_height'] = cam_config['human']['height']
    val['human_radius'] = cam_config['human']['radius']
    val["score"] = best_score
    write_output_file(f'{output_path}/camInfo_hyper_00.yaml', val, True)
    write_output_file(f'{output_path}/camInfo_hyper_00_opencv.yaml', val, True, "opencv")

    # plot losses
    plt.figure()
    plt.title('Loss Function')
    plt.xlabel('Iteration')
    plt.ylabel('Loss')
    plt.grid()
    plt.yscale('log')
    plt.legend(['Loss'])
    plt.semilogy(losses)
    plt.savefig(f'{output_path}/loss_function.png')
    print(f'Loss function plot saved to {output_path}/loss_function.png')
    plt.plot(losses)

def make_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', type=str, default='config.yaml',
                        help='config file')
    parser.add_argument('-b', '--box', type=str, default='bbox.txt',
                        help='input detection bounding box file')
    parser.add_argument('-i', '--image', type=str, default='image.jpg',
                        help='input image for vanishing point detection')
    parser.add_argument('-o', '--output', type=str, default='output',
                        help='output foler')
    parser.add_argument('-d', '--debug', default=False, action=argparse.BooleanOptionalAction,
                        help='bool for debugging mode')
    return parser

def get_config_data(config_path) -> CamProjMatConfig:
    with open(config_path, "r") as f:
        yamlfile = yaml.safe_load(f)
    bbox_config = CamProjMatConfig(yamlfile)
    return bbox_config

def main():
    """ Main function for camera projection matrix estimation
        Input: sampled bounding boxes
        Output: estimated camera projection matrix
    """
    print('Running hyper-parameter search using BBO')
    parser = make_parser()
    args = parser.parse_args()

    # config 
    config_path = args.config
    bbox_path = args.box
    image_path = args.image
    output_path = args.output 

    bb_cfg = get_config_data(config_path)
    estimate_camera_proj_matrix(bbox_path, image_path, output_path, bb_cfg)

if __name__ == '__main__':
    main()
