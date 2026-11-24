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
from hyperopt import tpe, hp, fmin, Trials

import camera_matcher as cm

class CameraParamEstimator(object):
    """ Camera Param Estimator class

    Estimates camera parameters from detection bounding boxes using Bayesian 
    Optimization (BBO) method.

    Args:
        det_boxes (list): List of detection bounding boxes
        vp_scene (list): List of vanishing points in the scene
        conf (Config): Configuration parameters
    """
    def __init__(self, det_boxes, vp_scene, conf):
        self.det_boxes = det_boxes
        self.vp_scene = vp_scene
        self.conf = conf
        print('conf: ', self.conf)

    def define_search_space(self):
        print('Define Search Space....')
        space = {
            'fl': hp.uniform('fl', self.conf.focal_length[0], self.conf.focal_length[1]),
            'rx': hp.uniform('rx', self.conf.rotation_x[0], self.conf.rotation_x[1]),
            'rz': hp.uniform('rz', self.conf.rotation_z[0], self.conf.rotation_z[1]),
            'ty': hp.uniform('ty', self.conf.translation_y[0], self.conf.translation_y[1]),
            'hr': hp.uniform('hr', self.conf.human_radius[0], self.conf.human_radius[1]),
        }
        return space

    def cost_fn(self, params):
        fl = params['fl']
        rx = params['rx']
        rz = params['rz']
        ty = params['ty']
        hr = params['hr']
        
        cam_config = {}
        #cam_config["human"] = {"radius": hr , "height": 175} # TODO: refactor this
        cam_config["human"] = {"radius": hr , "height": 1.75} # TODO: refactor this
        cam_config["focal_length"] = fl 
        cam_config["rotation"] = {"x": rx,  "y": 0.0, "z": rz}
        cam_config["translation"] = {"x": 27.46,  "y": ty, "z":1120.624} # TODO: what is this?

        # detect vanishing points from projected cylinders
        vp_det_box, head_circles, feet_circles, line_points = \
            detect_vanishing_point_from_detection(cam_config, self.det_boxes)

        # compute vanishing point score (0~, 0: best) 
        vp_score = compute_vp_score(self.vp_scene, vp_det_box)

        # compute IOU score (0~1, 1: best)
        iou_score = compute_iou_score(cam_config, self.det_boxes)

        w1 = self.conf.weight_iou # weight of iou score 
        w2 = 1.0 - w1 # weight of vp score
        total_score = w1 * (1.0-iou_score) + w2 * vp_score
        return total_score

    def run_parameter_search(self):
        # search space
        param_space = self.define_search_space()

        # optimization
        trials = Trials()
        best = fmin(
            fn = self.cost_fn, # Objective Function to optimize
            space = param_space, # Hyperparameter's Search Space
            algo = tpe.suggest, # Optimization algorithm
            max_evals = self.conf.iterations, # Number of optimization attempts 
            trials=trials
            )
        print('best: ', best)
        best_score = trials.best_trial['result']['loss']
        losses = trials.losses()
        return best, best_score, losses

def get_circle_center(pts):
    """ Get the center of the circle
    
    Args:
        pts(ndarray): points of a circle
    
    Returns:
        center(ndarray): center of the circle
    """
    return np.mean(pts, axis=0)

def line_equation(line):
    """
    Convert a line from two points to the general form Ax + By + C = 0
    """
    (x1, y1), (x2, y2) = line
    A = y2 - y1
    B = x1 - x2
    C = x2 * y1 - x1 * y2
    return A, B, C

def find_least_squares_intersection(lines):
    """
    Find the intersection point of multiple lines using least squares
    """
    A = []
    B = []
    for line in lines:
        a, b, c = line_equation(line)
        A.append([a, b])
        B.append([-c])
    A = np.array(A)
    B = np.array(B)

    # Solve the least squares problem
    intersection_point = np.linalg.lstsq(A, B, rcond=None)[0]
    return intersection_point.flatten()

def plot_lines_and_intersection(image, lines, intersection):
    """
    Plot lines and their intersection point on the image
    """
    plt.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    for line in lines:
        (x1, y1), (x2, y2) = line
        plt.plot([x1, x2], [y1, y2], 'b-')
    plt.plot(intersection[0], intersection[1], 'ro')
    plt.show()

def detect_vanishing_point_from_detection(cam_config, det_boxes):
    """ Detect a vanishing point from detection boxes
    
    Args:
        cam_proj_mat (np.array): Camera projection matrix
        det_boxes (np.array): Detections in the format [x1, y1, x2, y2]
    
    Returns:
        vp (np.array): 2D coordinates of the vanishing point
    """
    cam_exec = cm.CameraExec(cam_config)

    # circles of projected cylinders 
    head_circles, feet_circles = cam_exec.get_cylinder_circles(det_boxes)

    # get lines that connect the centers of the head and bottom of cylinders
    line_points = []
    for i in range(len(head_circles)):
        head_center = get_circle_center(head_circles[i])
        feet_center = get_circle_center(feet_circles[i])
        line_points.append((head_center, feet_center))
    
    # detect vanishing points from these lines 
    vp = find_least_squares_intersection(line_points)
    return vp, head_circles, feet_circles, line_points

def compute_iou_score(cam_config, det_boxes):
    """ Compute the iou score for a given set of boxes

    Args:
        cam_config (dict): Camera configuration parameters
        det_boxes (np.array): Detections in the format [x1, y1, x2, y2]
    
    Returns:
        iou_score (float): Intersection over Union score for the given set of boxes
    """
    config_eval = {'bbox_matching_function': 'square_iou'}
    cam_exec = cm.CameraExec(cam_config)
    cam_eval = cm.CameraEval(config_eval)
    bbox_pred = cam_exec.run(det_boxes)
    iou_score = cam_eval.run(det_boxes, bbox_pred)
    return iou_score

def compute_vp_score(vp_scene, vp_det_box):
    """Compute score for vanishing points
    
    Args:
        vp_scene (np.array): 2D coordinates of the vanishing point from the scene
        vp_det_box (np.array): 2D coordinates of the vanishing point from detection boxes
    
    Returns:
        score (float): Score for vanishing points
    """
    # Eucledian distance
    score = np.linalg.norm(vp_scene - vp_det_box)  
    # Normalize the score to be between 0 and 1
    score = score / np.linalg.norm(vp_scene)  
    return score
