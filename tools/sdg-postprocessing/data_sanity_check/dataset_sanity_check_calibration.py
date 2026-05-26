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
import re
import cv2
import sys
import math
import json
import copy
import logging
import numpy as np
import argparse

from tqdm import tqdm
from datetime import datetime
from pydantic import BaseModel
from collections import defaultdict
from scipy.spatial.transform import Rotation
from typing import List, Dict, Tuple, Any, Optional

logging.basicConfig(format="%(asctime)s - %(message)s", datefmt="%y/%m/%d %H:%M:%S", level=logging.INFO)

def load_calibration_data(calibration_file: str) -> Dict[str, Any]:
    """
    Load calibration data from a JSON file.

    :param str calibration_file: Path to the calibration JSON file
    :return: Loaded calibration data as a dictionary
    :rtype: dict

    Examples::
        >>> calib_data = load_calibration_data('calibration.json')
        >>> print(calib_data['sensors'])
    """
    with open(calibration_file, 'r') as f:
        return json.load(f)

def get_bbox_3d_corners(extents: Dict[str, Any]) -> np.ndarray:
    """
    Return transformed points of a 3D bounding box in the following order: 
    [LDB, RDB, LUB, RUB, LDF, RDF, LUF, RUF]
    where R=Right, L=Left, D=Down, U=Up, B=Back, F=Front and LR: x-axis, UD: y-axis, FB: z-axis.

    :param numpy.ndarray extents: A structured numpy array containing the fields: 
        [`x_min`, `y_min`, `x_max`, `y_max`, `transform`]
    :return: Transformed corner coordinates with shape `(N, 8, 3)`
    :rtype: numpy.ndarray

    Examples::
        >>> bbox_extents = {
        ...     'x_min': 0, 'y_min': 0, 'z_min': 0,
        ...     'x_max': 1, 'y_max': 1, 'z_max': 1,
        ...     'transform': np.eye(4)
        ... }
        >>> corners = get_bbox_3d_corners(bbox_extents)
        >>> print(corners.shape)  # (N, 8, 3)
    """
    ldb = [extents["x_min"], extents["y_min"], extents["z_min"]]
    rdb = [extents["x_max"], extents["y_min"], extents["z_min"]]
    lub = [extents["x_min"], extents["y_max"], extents["z_min"]]
    rub = [extents["x_max"], extents["y_max"], extents["z_min"]]
    ldf = [extents["x_min"], extents["y_min"], extents["z_max"]]
    rdf = [extents["x_max"], extents["y_min"], extents["z_max"]]
    luf = [extents["x_min"], extents["y_max"], extents["z_max"]]
    ruf = [extents["x_max"], extents["y_max"], extents["z_max"]]
    tfs = extents["transform"]

    corners = np.stack((ldb, rdb, lub, rub, ldf, rdf, luf, ruf), 0)
    corners_homo = np.pad(corners, ((0, 0), (0, 1), (0, 0)), constant_values=1.0)

    return np.einsum("jki,ikl->ijl", corners_homo, tfs)[..., :3]

def extent_dimension(extents: Dict[str, Any]) -> Dict[str, np.ndarray]:
    """
    Extend the dimension of input data to fit the format of helper method parameter.

    :param dict extents: Dictionary containing bounding box extents and transform
    :return: Dictionary with expanded dimensions for batch processing
    :rtype: dict

    Examples::
        >>> extents = {
        ...     'x_min': 0, 'y_min': 0, 'z_min': 0,
        ...     'x_max': 1, 'y_max': 1, 'z_max': 1,
        ...     'transform': np.eye(4)
        ... }
        >>> batch_extents = extent_dimension(extents)
        >>> print(batch_extents['x_min'].shape)  # (1,)
    """
    extents_batch = {
        "x_min": np.expand_dims(extents["x_min"], axis=0),
        "x_max": np.expand_dims(extents["x_max"], axis=0),
        "y_min": np.expand_dims(extents["y_min"], axis=0),
        "y_max": np.expand_dims(extents["y_max"], axis=0),
        "z_min": np.expand_dims(extents["z_min"], axis=0),
        "z_max": np.expand_dims(extents["z_max"], axis=0),
        "transform": np.expand_dims(extents["transform"], axis=0),
    }
    return extents_batch

def world_to_image_helper_from_calibration(corner: np.ndarray, rotation_vector: np.ndarray, translation_vector: np.ndarray, intrinsic_matrix: np.ndarray, distortion_coefficients: np.ndarray) -> np.ndarray:
    """
    Project 3D world points to 2D image coordinates using camera calibration parameters.

    :param numpy.ndarray corner: 3D corner points in world coordinates
    :param numpy.ndarray rotation_vector: Camera rotation vector
    :param numpy.ndarray translation_vector: Camera translation vector
    :param numpy.ndarray intrinsic_matrix: Camera intrinsic matrix
    :param numpy.ndarray distortion_coefficients: Camera distortion coefficients
    :return: Projected 2D points in image coordinates
    :rtype: numpy.ndarray

    Examples::
        >>> corners_3d = np.array([[0, 0, 0], [1, 1, 1]])
        >>> R = np.zeros(3)
        >>> T = np.array([0, 0, -1])
        >>> K = np.eye(3)
        >>> D = np.zeros((1, 8))
        >>> points_2d = world_to_image_helper_from_calibration(corners_3d, R, T, K, D)
    """
    corners_camera, jacobian = cv2.projectPoints(corner, rotation_vector, translation_vector, intrinsic_matrix, distortion_coefficients)
    corners_camera = corners_camera[:,0,:].astype(int)
    return corners_camera

def get_cam_params_from_calib(sensor: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Extract camera parameters from calibration data.

    :param dict sensor: Sensor calibration data containing image and global coordinates
    :return: Tuple containing rotation vector, translation vector, intrinsic matrix, and distortion coefficients
    :rtype: tuple(numpy.ndarray, numpy.ndarray, numpy.ndarray, numpy.ndarray)

    Examples::
        >>> sensor_data = {
        ...     'imageCoordinates': [{'x': 0, 'y': 0}],
        ...     'globalCoordinates': [{'x': 0, 'y': 0, 'z': 0}],
        ...     'intrinsicMatrix': [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        ... }
        >>> R, T, K, D = get_cam_params_from_calib(sensor_data)
    """
    image_coordinates = sensor['imageCoordinates']
    global_coordinates = sensor['globalCoordinates']
    intrinsic_matrix = np.array(sensor['intrinsicMatrix'], dtype=np.float32)
    distortion_coefficients = np.array([[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]], dtype=np.float32)

    image_coordinate_array = []
    for coord in image_coordinates:
        image_coordinate_array.append((coord["x"], coord["y"]))
    image_coordinate_array = np.array(image_coordinate_array, dtype=np.float32)

    global_coordinate_array = []
    for coord in global_coordinates:
        global_coordinate_array.append((coord["x"], coord["y"], coord["z"]))
    global_coordinate_array = np.array(global_coordinate_array, dtype=np.float32)

    assert len(global_coordinate_array) == len(image_coordinate_array), \
        "Error: the number of 3D and 2D points are inconsistent"
    
    flag = cv2.SOLVEPNP_ITERATIVE
    success, rotation_vector, translation_vector = cv2.solvePnP(global_coordinate_array, image_coordinate_array,
                                                                intrinsic_matrix,
                                                                distortion_coefficients,
                                                                flags=flag)

    if success:
        rotation_matrix = cv2.Rodrigues(rotation_vector)[0]
        camera_position = -np.matrix(rotation_matrix).T * np.matrix(translation_vector)
        rotation =  Rotation.from_matrix(rotation_matrix)
        rotation_z, rotation_y, rotation_x = rotation.as_euler("zyx", degrees=True)

        camera_params = {
            "camera_position": camera_position.tolist(),
            "rotation_vector": rotation_vector.tolist(),
            "translation_vector": translation_vector.tolist(),
            "rotation_matrix": rotation_matrix.tolist(),
            "euler_angles": [[rotation_x], [rotation_y], [rotation_z]]
        }
    return rotation_vector, translation_vector, intrinsic_matrix, distortion_coefficients

def compute_camera_projection_matrix(three_d_points: np.ndarray, two_d_points: np.ndarray) -> np.ndarray:
    """
    Compute the camera projection matrix from corresponding 3D and 2D points.

    :param numpy.ndarray three_d_points: Array of 3D points in world coordinates
    :param numpy.ndarray two_d_points: Array of corresponding 2D points in image coordinates
    :return: 3x4 camera projection matrix
    :rtype: numpy.ndarray

    Examples::
        >>> points_3d = np.array([[0, 0, 0], [1, 1, 1]])
        >>> points_2d = np.array([[100, 100], [200, 200]])
        >>> P = compute_camera_projection_matrix(points_3d, points_2d)
        >>> print(P.shape)  # (3, 4)
    """
    three_d_points = np.array(three_d_points, dtype=np.float64)
    two_d_points = np.array(two_d_points, dtype=np.float64)
    num_points = three_d_points.shape[0]
    A = []
    for i in range(num_points):
        X, Y, Z = three_d_points[i, :]
        x, y = two_d_points[i, :]
        A.append([-X, -Y, -Z, -1, 0, 0, 0, 0, x * X, x * Y, x * Z, x])
        A.append([0, 0, 0, 0, -X, -Y, -Z, -1, y * X, y * Y, y * Z, y])
    A = np.array(A)
    U, S, Vh = np.linalg.svd(A)
    L = Vh[-1, :] / Vh[-1, -1]  # Normalize
    camera_projection_matrix = L.reshape(3, 4)
    return camera_projection_matrix

def compute_homography_matrix(proj_matrix: np.ndarray, platform_height: float = 0) -> np.ndarray:
    """
    Compute the homography matrix H from a given 3x4 projection matrix P and plane height h.
    
    :param numpy.ndarray proj_matrix: 3x4 projection matrix
    :param float platform_height: Height of the plane Z = h (default: 0)
    :return: 3x3 homography matrix H
    :rtype: numpy.ndarray
    :raises ValueError: If projection matrix P is not of shape (3,4)

    Examples::
        >>> P = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]])
        >>> H = compute_homography_matrix(P, platform_height=1.0)
        >>> print(H.shape)  # (3, 3)
    """
    if proj_matrix.shape != (3, 4):
        raise ValueError("Projection matrix P must be of shape (3,4)")

    # Extract columns
    P1, P2, P3, P4 = proj_matrix[:, 0], proj_matrix[:, 1], proj_matrix[:, 2], proj_matrix[:, 3]

    # Compute homography matrix H
    H = np.column_stack([P1, P2, P3 * platform_height + P4])  # Equivalent to [P1 P2 (P3*h + P4)]

    return H

def get_extrinsics(cam_params: Dict[str, Any]) -> np.ndarray:
    """
    Get camera extrinsic matrix from camera parameters.
    Depends on the physical location of the camera.
    Translates from world space to camera space.
    Origin is the location of the camera.

    :param dict cam_params: Camera parameters containing either 'cameraViewTransform' or 'camera_transform'
    :return: 4x4 extrinsic matrix
    :rtype: numpy.ndarray
    :raises NotImplementedError: If neither 'cameraViewTransform' nor 'camera_transform' is present

    Examples::
        >>> params = {'cameraViewTransform': np.eye(4).flatten().tolist()}
        >>> E = get_extrinsics(params)
        >>> print(E.shape)  # (4, 4)
    """
    if 'cameraViewTransform' in cam_params:
        cam_extrinsics = np.asarray(cam_params["cameraViewTransform"]).reshape(4, 4).T
    elif 'camera_transform' in cam_params:## issac version cam to world
        cam_extrinsics = np.linalg.inv(np.asarray(cam_params["camera_transform"]).reshape(4, 4)).T
    else:
        raise NotImplementedError

    cam_extrinsics[1, :]= -cam_extrinsics[1, :]
    cam_extrinsics[2, :]= -cam_extrinsics[2, :]

    return cam_extrinsics

def get_intrinsics(cam_params: Dict[str, Any]) -> np.ndarray:
    """
    Get camera intrinsic matrix from camera parameters.
    Depends on focal length and resolution of the camera.
    Translates from 3D camera space to 2D image space.

    :param dict cam_params: Camera parameters containing projection and resolution information
    :return: 4x4 intrinsic matrix
    :rtype: numpy.ndarray
    :raises NotImplementedError: If required parameters are not present in cam_params

    Examples::
        >>> params = {
        ...     'cameraProjection': np.eye(4).flatten().tolist(),
        ...     'renderProductResolution': [1920, 1080]
        ... }
        >>> K = get_intrinsics(params)
        >>> print(K.shape)  # (4, 4)
    """
    if 'cameraProjection' in cam_params:
        camera_intrinsics = np.asarray(cam_params["cameraProjection"]).reshape(4, 4).T
    elif 'camera_projection' in cam_params:
        camera_intrinsics = np.asarray(cam_params["camera_projection"]).reshape(4, 4).T
    else:
        raise NotImplementedError
    
    if 'renderProductResolution' in cam_params:
        camera_intrinsics[0, 2] = 1
        camera_intrinsics[1, 2] = 1
        camera_intrinsics[0, :] *= cam_params["renderProductResolution"][0] / 2
        camera_intrinsics[1, :] *= cam_params["renderProductResolution"][1] / 2
        camera_intrinsics = camera_intrinsics[:3, :3]
        camera_intrinsics[2, 2] = 1
    elif 'fx_fy_cx_cy' in cam_params:
        camera_intrinsics[0, 2] = 1
        camera_intrinsics[1, 2] = 1
        camera_intrinsics[0, :] *= cam_params["rp_width"] / 2
        camera_intrinsics[1, :] *= cam_params["rp_height"] / 2
        camera_intrinsics = camera_intrinsics[:3, :3]
        camera_intrinsics[2, 2] = 1
    else:
        raise NotImplementedError

    extended_intrinsic = np.eye(4)
    extended_intrinsic[:3,:3] = camera_intrinsics[:3,:3]

    return extended_intrinsic

def project_points(points_3d: np.ndarray,
                    proj_mat: np.ndarray,
                    with_depth: bool = False) -> np.ndarray:
    """
    Project 3D points to 2D using a projection matrix.

    :param numpy.ndarray points_3d: Array of 3D points to project
    :param numpy.ndarray proj_mat: 3x4 or 4x4 projection matrix
    :param bool with_depth: Whether to include depth values in output (default: False)
    :return: Array of projected 2D points (with optional depth values)
    :rtype: numpy.ndarray
    :raises AssertionError: If projection matrix dimensions are invalid

    Examples::
        >>> points = np.array([[1, 2, 3], [4, 5, 6]])
        >>> proj = np.eye(4)
        >>> points_2d = project_points(points, proj)
        >>> print(points_2d.shape)  # (2, 2)
    """
    points_shape = list(points_3d.shape)
    points_shape[-1] = 1

    assert len(proj_mat.shape) == 2, \
        'The dimension of the projection matrix should be 2 ' \
        f'instead of {len(proj_mat.shape)}.'
    d1, d2 = proj_mat.shape[:2]
    assert (d1 == 3 and d2 == 3) or (d1 == 3 and d2 == 4) or \
        (d1 == 4 and d2 == 4), 'The shape of the projection matrix ' \
        f'({d1}*{d2}) is not supported.'
    
    if d1 == 3:
        proj_mat_expanded = np.eye(4, dtype=proj_mat.dtype)
        proj_mat_expanded[:d1, :d2] = proj_mat
        proj_mat = proj_mat_expanded

    points_4d = np.concatenate([points_3d, np.ones(points_shape)], axis=-1)
    point_2d = points_4d @ proj_mat  # (Nx4)x (4x4) = (Nx4)
    point_2d_res = point_2d[..., :2] / point_2d[..., 2:3]

    if with_depth:
        point_2d_res = np.concatenate([point_2d_res, point_2d[..., 2:3]], axis=-1)
    return point_2d_res

def plot_rect3d_on_img(img: np.ndarray, corners: np.ndarray, text: Optional[str] = None, color: Tuple[int, int, int] = (0, 255, 0), thickness: int = 1) -> np.ndarray:
    """
    Plot the boundary lines of 3D rectangular on 2D images.

    :param numpy.ndarray img: Input image array
    :param numpy.ndarray corners: Coordinates of the corners of 3D rectangulars (shape: [8, 2])
    :param str text: Optional text to display on the box
    :param tuple color: RGB color tuple for drawing (default: (0, 255, 0))
    :param int thickness: Line thickness (default: 1)
    :return: Image with drawn 3D box
    :rtype: numpy.ndarray

    Examples::
        >>> image = np.zeros((480, 640, 3), dtype=np.uint8)
        >>> box_corners = np.array([[100, 100], [200, 100], [100, 200], [200, 200],
        ...                        [150, 150], [250, 150], [150, 250], [250, 250]])
        >>> result = plot_rect3d_on_img(image, box_corners, "Box", (255, 0, 0), 2)
    """
    img_draw = np.copy(img)
    line_indices = (
        (0, 1),
        (0, 2),
        (0, 4),
        (1, 3),
        (1, 5),
        (3, 2),
        (3, 7),
        (4, 5),
        (4, 6),
        (2, 6),
        (5, 7),
        (6, 7),
    )

    h, w = img.shape[:2]

    for start, end in line_indices:
        if (
            (corners[start, 1] >= h or corners[start, 1] < 0)
            or (corners[start, 0] >= w or corners[start, 0] < 0)
        ) and (
            (corners[end, 1] >= h or corners[end, 1] < 0)
            or (corners[end, 0] >= w or corners[end, 0] < 0)
        ):
            continue
        cv2.line(img_draw, (corners[start, 0], corners[start, 1]), (corners[end, 0], corners[end, 1]), color, thickness, cv2.LINE_AA)

    # print text for each box
    if text is not None:
        cv2.putText(img_draw, text, corners[5][:2], cv2.FONT_HERSHEY_SIMPLEX, 1.0,(255, 255, 255),3,cv2.LINE_AA)

    return img_draw

class CalibrationValidator:
    """
    Module to validate camera calibration by visualizing 3D bounding boxes using different projection methods.

    :param str base_dir: Base directory containing sensor data
    :param str calibration_path: Path to the calibration file in JSON format
    :param str output_dir_path: Path to the output directory for visualization results
    :param List exculde_object: List of object IDs to exclude from visualization
    :param List exculde_class: List of object classes to exclude from visualization

    Examples::
        >>> validator = CalibrationValidator(
        ...     base_dir='/data/sensors',
        ...     calibration_path='calibration.json',
        ...     output_dir_path='output',
        ...     exculde_object=[],
        ...     exculde_class=['rack', 'barrel']
        ... )
        >>> validator.plot_3dbbox_using_homography()
    """

    def __init__(self, base_dir: str, calibration_path: str, output_dir_path: str, exculde_object: List, exculde_class: List):
        self.base_dir = base_dir
        sensors = load_calibration_data(calibration_path)
        self.sensors = sensors['sensors']
        self.output_dir_path = output_dir_path
        self.exculde_object = exculde_object
        self.exculde_class = exculde_class
        
    def plot_3dbbox_using_homography_from_pair_points(self, platform_height: float = 0) -> None:
        """
        Plot 3D bounding boxes using homography matrix computed from paired points.

        :param float platform_height: Height of the ground plane (default: 0)
        :return: None

        This method:
        1. Computes homography from paired image-world coordinates
        2. Projects 3D bounding boxes to 2D using the computed homography
        3. Saves visualization results to output directory
        """
        for sensor in self.sensors:
            sensor_id = sensor['id']

            #Read image and object+detection
            input_image_path = os.path.join("{}/{}/rgb/rgb_00000.jpg".format(self.base_dir, sensor_id))
            input_obj_path = os.path.join("{}/{}/object_detection/object_detection_00000.json".format(self.base_dir, sensor_id))

            img_data = cv2.imread(input_image_path)
            with open(input_obj_path, 'r') as json_file:
                obj_data = json.load(json_file)

            #Get homography
            image_coordinates = sensor['imageCoordinates']
            global_coordinates = sensor['globalCoordinates']
            
            image_coordinate_array = []
            for coord in image_coordinates:
                image_coordinate_array.append((coord["x"], coord["y"]))
            image_coordinate_array = np.array(image_coordinate_array, dtype=np.float32)

            global_coordinate_array = []
            for coord in global_coordinates:
                global_coordinate_array.append((coord["x"], coord["y"], coord["z"]))
            global_coordinate_array = np.array(global_coordinate_array, dtype=np.float32)

            projection_matrix = compute_camera_projection_matrix(global_coordinate_array, image_coordinate_array)
            homography = compute_homography_matrix(projection_matrix, platform_height = 0)

            for entity_key, entity_value in obj_data.items():
                for prim_key, prim_value in entity_value.items():
                    if prim_key not in self.exculde_object and 'bounding_box_3d_fast' in prim_value['annotators'] and prim_value['label']['class'] not in self.exculde_class:
                        bbox_3d_dict = prim_value['annotators']['bounding_box_3d_fast']
                        corners = get_bbox_3d_corners(extent_dimension(bbox_3d_dict))

                        corner_2d = []
                        for corner in corners[0]:
                            world_points = np.array([corner[0], corner[1], 1])
                            projected = homography @ world_points.T
                            projected /= projected[2]
                            
                            pixel_coords = projected[:2].T
                            corner_2d.append(pixel_coords)
                        
                        corner_2d = np.array(corner_2d).astype(np.int64)
                        
                        img_data = plot_rect3d_on_img(img_data, corner_2d, text="", color=(220, 190, 255), thickness=4)
            
            cv2.imwrite(os.path.join(self.output_dir_path, sensor_id + "_homo_from_pp.png"), img_data)
    
    def plot_3dbbox_using_cameramatrix(self) -> None:
        """
        Plot 3D bounding boxes using camera matrix from calibration.

        :return: None

        This method:
        1. Uses the camera matrix directly from sensor calibration
        2. Projects 3D bounding boxes to 2D using the camera matrix
        3. Saves visualization results to output directory
        """
        for sensor in self.sensors:
            sensor_id = sensor['id']
            #Read image and object+detection
            input_image_path = os.path.join("{}/{}/rgb/rgb_00000.jpg".format(self.base_dir, sensor_id))
            input_obj_path = os.path.join("{}/{}/object_detection/object_detection_00000.json".format(self.base_dir, sensor_id))

            img_data = cv2.imread(input_image_path)
            with open(input_obj_path, 'r') as json_file:
                obj_data = json.load(json_file)

            #Get projection_matrix
            projection_matrix = sensor['cameraMatrix']

            for entity_key, entity_value in obj_data.items():
                for prim_key, prim_value in entity_value.items():
                    if prim_key not in self.exculde_object and 'bounding_box_3d_fast' in prim_value['annotators'] and prim_value['label']['class'] not in self.exculde_class:
                        bbox_3d_dict = prim_value['annotators']['bounding_box_3d_fast']
                        corners = get_bbox_3d_corners(extent_dimension(bbox_3d_dict))

                        points_3d_homo = np.hstack([corners[0], np.ones((corners[0].shape[0], 1))])
                        projected_points = (projection_matrix @ points_3d_homo.T).T
                        
                        # Normalize by the w-component
                        projected_points[:, 0] /= projected_points[:, 2]  # u = x/w
                        projected_points[:, 1] /= projected_points[:, 2]  # v = y/w

                        corner_2d = projected_points[:, :2]
                        corner_2d = np.array(corner_2d).astype(np.int64)

                        img_data = plot_rect3d_on_img(img_data, corner_2d, text="", color=(220, 190, 255), thickness=4)
            
            cv2.imwrite(os.path.join(self.output_dir_path, sensor_id + "_cam_mat.png"), img_data)
    
    def plot_3dbbox_using_pair_points(self) -> None:
        """
        Plot 3D bounding boxes using PnP solution from paired points.

        :return: None

        This method:
        1. Computes camera parameters (rotation, translation) using solvePnP
        2. Projects 3D bounding boxes to 2D using the computed parameters
        3. Saves visualization results to output directory
        """
        for sensor in self.sensors:
            sensor_id = sensor['id']

            #Read image and object+detection
            input_image_path = os.path.join("{}/{}/rgb/rgb_00000.jpg".format(self.base_dir, sensor_id))
            input_obj_path = os.path.join("{}/{}/object_detection/object_detection_00000.json".format(self.base_dir, sensor_id))

            img_data = cv2.imread(input_image_path)
            with open(input_obj_path, 'r') as json_file:
                obj_data = json.load(json_file)

            #Get rotation_vector, translation_vector, intrinsic_matrix, distortion_coefficients
            rotation_vector, translation_vector, intrinsic_matrix, distortion_coefficients = get_cam_params_from_calib(sensor)

            for entity_key, entity_value in obj_data.items():
                for prim_key, prim_value in entity_value.items():
                    if prim_key not in self.exculde_object and 'bounding_box_3d_fast' in prim_value['annotators'] and prim_value['label']['class'] not in self.exculde_class:
                        bbox_3d_dict = prim_value['annotators']['bounding_box_3d_fast']
                        corners = get_bbox_3d_corners(extent_dimension(bbox_3d_dict))

                        corners_camera = world_to_image_helper_from_calibration(corners, rotation_vector, translation_vector, intrinsic_matrix, distortion_coefficients)

                        img_data = plot_rect3d_on_img(img_data, corners_camera, text="", color=(220, 190, 255), thickness=4)
            
            cv2.imwrite(os.path.join(self.output_dir_path, sensor_id + "_pair_points.png"), img_data)

    def plot_3dbbox_using_homography(self) -> None:
        """
        Plot 3D bounding boxes using homography matrix from calibration.

        :return: None

        This method:
        1. Uses the homography matrix directly from sensor calibration
        2. Projects 3D bounding boxes to 2D using the homography
        3. Saves visualization results to output directory
        """
        for sensor in self.sensors:
            sensor_id = sensor['id']

            #Read image and object+detection
            input_image_path = os.path.join("{}/{}/rgb/rgb_00000.jpg".format(self.base_dir, sensor_id))
            input_obj_path = os.path.join("{}/{}/object_detection/object_detection_00000.json".format(self.base_dir, sensor_id))

            img_data = cv2.imread(input_image_path)
            with open(input_obj_path, 'r') as json_file:
                obj_data = json.load(json_file)

            #Get homography
            homography = sensor["homography"]

            for entity_key, entity_value in obj_data.items():
                for prim_key, prim_value in entity_value.items():
                    if prim_key not in self.exculde_object and 'bounding_box_3d_fast' in prim_value['annotators'] and prim_value['label']['class'] not in self.exculde_class:
                        bbox_3d_dict = prim_value['annotators']['bounding_box_3d_fast']
                        corners = get_bbox_3d_corners(extent_dimension(bbox_3d_dict))

                        corner_2d = []
                        for corner in corners[0]:
                            world_points = np.array([corner[0], corner[1], 1])
                            projected = homography @ world_points.T
                            projected /= projected[2]
                            
                            pixel_coords = projected[:2].T
                            corner_2d.append(pixel_coords)
                        
                        corner_2d = np.array(corner_2d).astype(np.int64)

                        img_data = plot_rect3d_on_img(img_data, corner_2d, text="", color=(220, 190, 255), thickness=4)
            
            cv2.imwrite(os.path.join(self.output_dir_path, sensor_id + "_homo.png"), img_data)

    def plot_3dbbox_using_intrinsic_extrinsic(self) -> None:
        """
        Plot 3D bounding boxes using intrinsic and extrinsic matrices from calibration.

        :return: None

        This method:
        1. Uses intrinsic and extrinsic matrices from sensor calibration
        2. Projects 3D bounding boxes to 2D using the combined projection matrix
        3. Saves visualization results to output directory
        """
        for sensor in self.sensors:
            sensor_id = sensor['id']
            #Read image and object+detection
            input_image_path = os.path.join("{}/{}/rgb/rgb_00000.jpg".format(self.base_dir, sensor_id))
            input_obj_path = os.path.join("{}/{}/object_detection/object_detection_00000.json".format(self.base_dir, sensor_id))

            img_data = cv2.imread(input_image_path)
            with open(input_obj_path, 'r') as json_file:
                obj_data = json.load(json_file)

            intrinsic = np.eye(4)
            intrinsic[:3, :3] = np.array(sensor["intrinsicMatrix"])
            extrinsic = np.eye(4)
            extrinsic[:3] = np.array(sensor["extrinsicMatrix"])
            
            world2img_rt = intrinsic @ extrinsic

            for entity_key, entity_value in obj_data.items():
                for prim_key, prim_value in entity_value.items():
                    if prim_key not in self.exculde_object and 'bounding_box_3d_fast' in prim_value['annotators'] and prim_value['label']['class'] not in self.exculde_class:
                        
                        bbox_3d_dict = prim_value['annotators']['bounding_box_3d_fast']
                        corners = get_bbox_3d_corners(extent_dimension(bbox_3d_dict))

                        pts_4d = np.concatenate([corners.reshape(-1, 3), np.ones((8, 1))], axis=-1)
                        pts_2d = pts_4d @ world2img_rt.T

                        pts_2d[:, 2] = np.clip(pts_2d[:, 2], a_min=1e-5, a_max=1e5)
                        pts_2d[:, 0] /= pts_2d[:, 2]
                        pts_2d[:, 1] /= pts_2d[:, 2]
                        imgfov_pts_2d = pts_2d[..., :2].reshape(8, 2)
                        imgfov_pts_2d = imgfov_pts_2d.astype(np.int64)

                        img_data = plot_rect3d_on_img(img_data, imgfov_pts_2d, text="", color=(220, 190, 255), thickness=4)

            cv2.imwrite(os.path.join(self.output_dir_path, sensor_id + "_intr_extr.png"), img_data)
    
    def plot_3dbbox_using_camera_params(self) -> None:
        """
        Plot 3D bounding boxes using camera parameters from camera_params file.

        :return: None

        This method:
        1. Loads camera parameters from a separate camera_params file
        2. Computes projection matrix from intrinsic and extrinsic parameters
        3. Projects 3D bounding boxes to 2D using the computed projection matrix
        4. Saves visualization results to output directory
        """
        for sensor in self.sensors:
            sensor_id = sensor['id']
            #Read image and object+detection
            input_image_path = os.path.join("{}/{}/rgb/rgb_00000.jpg".format(self.base_dir, sensor_id))
            input_obj_path = os.path.join("{}/{}/object_detection/object_detection_00000.json".format(self.base_dir, sensor_id))
            input_cp_path = os.path.join("{}/{}/camera_params/camera_params_00000.json".format(self.base_dir, sensor_id))

            img_data = cv2.imread(input_image_path)
            with open(input_obj_path, 'r') as json_file:
                obj_data = json.load(json_file)

            with open(input_cp_path, 'r') as json_file:
                cp_data = json.load(json_file)

            extrinsic = get_extrinsics(cp_data) #(4,4)
            intrinsic = get_intrinsics(cp_data) #(4,4)
            projection = intrinsic @ extrinsic
            
            world2img_rt = projection

            for entity_key, entity_value in obj_data.items():
                for prim_key, prim_value in entity_value.items():
                    if prim_key not in self.exculde_object and 'bounding_box_3d_fast' in prim_value['annotators'] and prim_value['label']['class'] not in self.exculde_class:
                        bbox_3d_dict = prim_value['annotators']['bounding_box_3d_fast']
                        corners = get_bbox_3d_corners(extent_dimension(bbox_3d_dict))

                        pts_4d = np.concatenate([corners.reshape(-1, 3), np.ones((8, 1))], axis=-1)
                        pts_2d = pts_4d @ world2img_rt.T

                        pts_2d[:, 2] = np.clip(pts_2d[:, 2], a_min=1e-5, a_max=1e5)
                        pts_2d[:, 0] /= pts_2d[:, 2]
                        pts_2d[:, 1] /= pts_2d[:, 2]
                        imgfov_pts_2d = pts_2d[..., :2].reshape(8, 2)
                        imgfov_pts_2d = imgfov_pts_2d.astype(np.int64)

                        img_data = plot_rect3d_on_img(img_data, imgfov_pts_2d, text="", color=(220, 190, 255), thickness=4)

            cv2.imwrite(os.path.join(self.output_dir_path, sensor_id + "_cam_params.png"), img_data)

    def plot_matched_coordinates(self) -> None:
        """
        Plot matched image and world coordinates for calibration verification.

        :return: None

        This method:
        1. Loads corresponding image and world coordinates from calibration
        2. Visualizes the matched points on both camera image and top-down view
        3. Draws connecting lines between matched points
        4. Saves visualization results to output directory

        The output image shows:
        - Camera view with numbered points in red
        - Top-down view with numbered points in green
        - Blue lines connecting corresponding points
        """
        # Read map image
        image_map = cv2.imread("{}/Top.png".format(self.base_dir))
        map_height, map_width, _ = image_map.shape

        for sensor in self.sensors:
            image_map_copy = image_map.copy()
            sensor_id = sensor['id']
            # Read frame image
            input_image_path = os.path.join("{}/{}.png".format(self.base_dir, sensor_id))
            if not os.path.exists(input_image_path):
                logging.error(f"ERROR: The input image path `{input_image_path}` does NOT exist.")
                exit(1)
            
            image = cv2.imread(input_image_path)
            height, width, _ = image.shape
           
            # Extract image and global coordinates
            image_coordinates: List[Tuple[int]] = list()
            global_coordinates: List[Tuple[int]] = list()
            
            scale_factor = sensor['scaleFactor']
            translation = sensor['translationToGlobalCoordinates']

            if (len(sensor['imageCoordinates']) == 0) or (len(sensor['imageCoordinates']) == 0):
                logging.error(f"ERROR: The length of image coordinates or global coordinates is empty.")
                exit(1)

            if len(sensor['imageCoordinates']) != len(sensor['globalCoordinates']):
                logging.error(f"ERROR: The lengths of image coordinates and global coordinates do NOT match -- "
                                f"{len(sensor['imageCoordinates'])} != {len(sensor['globalCoordinates'])}.")
                exit(1)

            for coord in sensor['imageCoordinates']:
                image_coordinates.append((int(coord["x"]), int(coord["y"])))

            for coord in sensor['globalCoordinates']:
                global_coordinates.append((int((coord["x"] + translation["x"]) * scale_factor), int(map_height - (coord["y"] + + translation["y"]) * scale_factor - 1)))

            if (len(image_coordinates) == 0) or (len(global_coordinates) == 0):
                logging.error(f"ERROR: No matched sensor ID {sensor_id} is found.")
                exit(1)

            for i in range(len(image_coordinates)):
                # Plot indices
                cv2.putText(image, str(i), image_coordinates[i], cv2.FONT_HERSHEY_DUPLEX,
                            2.0, (0, 0, 255), 2, cv2.LINE_AA)
                cv2.putText(image_map_copy, str(i), global_coordinates[i], cv2.FONT_HERSHEY_DUPLEX,
                            2.0, (0, 255, 0), 2, cv2.LINE_AA)

                # Plot coordinates
                cv2.circle(image, image_coordinates[i], 6, (255, 255, 255), -1)
                cv2.circle(image_map_copy, global_coordinates[i], 6, (255, 255, 255), -1)

            # Resize map image
            frame_scale = width / float(map_width)
            image_map_copy = cv2.resize(image_map_copy, (width, int(map_height * frame_scale)))

            # Concatenate images vertically
            image_output = cv2.vconcat([image, image_map_copy])

            # Plot lines between matches
            for i in range(len(image_coordinates)):
                x = int(global_coordinates[i][0] * frame_scale)
                y = int((global_coordinates[i][1] * frame_scale) + height)
                cv2.line(image_output, image_coordinates[i], (x, y), (255, 0, 0), 2)

            # Write output image
            cv2.imwrite(os.path.join(self.output_dir_path, sensor_id + "_vis.png"), image_output)



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base_dir", type=str, help="The input directory")
    parser.add_argument("--calibration", type=str, help="The input calibration file")
    parser.add_argument("--output_dir", type=str, help="The output directory")
    args = parser.parse_args()

    
    exculde_object = [] #["/Root/SM_RackFrame_168/SM_RackFrame_03/SM_RackFrame_03/Section1"]
    exculde_class = [] #["rack", "barel", "bottle","cart"]
    # Instantiate module
    calibration_validator = CalibrationValidator(args.base_dir, args.calibration, args.output_dir, exculde_object, exculde_class)

    #Plot matched coordinates
    calibration_validator.plot_matched_coordinates()

    calibration_validator.plot_3dbbox_using_intrinsic_extrinsic()

    calibration_validator.plot_3dbbox_using_homography()

    calibration_validator.plot_3dbbox_using_pair_points()

    calibration_validator.plot_3dbbox_using_cameramatrix()

    calibration_validator.plot_3dbbox_using_homography_from_pair_points(platform_height=0) # default 0 for general scenes. If your ground height is not 0, please set the platform_height to your ground height.

    calibration_validator.plot_3dbbox_using_camera_params()




