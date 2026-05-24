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
import json
import numpy as np
import cv2
import random
import argparse
from pxr import Gf
import matplotlib.pyplot as plt
from pyquaternion import Quaternion
from mpl_toolkits.mplot3d import Axes3D
from matplotlib.patches import ConnectionPatch
from scipy.optimize import linear_sum_assignment
from typing import Any, Dict, List, Optional, Tuple

def get_projection_matrix(cam_folder: str, calibration: Dict[str, Any]) -> np.ndarray:
    """Build world-to-image projection matrix for a given camera.

    This multiplies the 4x4 intrinsics (K) with the 4x4 extrinsics ([R|t])
    to produce a 4x4 projection matrix usable for homogeneous coordinates.

    Args:
        cam_folder: Camera identifier string (e.g., 'Camera', 'Camera_01').
        calibration: Calibration JSON dict containing a 'sensors' list with
            'id', 'intrinsicMatrix', and 'extrinsicMatrix' per camera.

    Returns:
        np.ndarray: A 4x4 projection matrix mapping homogeneous world coords to image plane.
    """
    sensors: List[Dict[str, Any]] = calibration.get('sensors', [])
    sensor = next((s for s in sensors if s.get('id') == cam_folder), None)
    if sensor is None:
        raise KeyError(f"Camera '{cam_folder}' not found in calibration")

    intrinsic = np.eye(4)
    intrinsic[:3, :3] = np.array(sensor["intrinsicMatrix"], dtype=float)
    extrinsic = np.eye(4)
    extrinsic[:3] = np.array(sensor["extrinsicMatrix"], dtype=float)
    return intrinsic @ extrinsic

def corners_projection(corners: np.ndarray, world2img_rt: np.ndarray) -> np.ndarray:
    """Project 3D corners to 2D image plane using a 4x4 projection matrix.

    Args:
        corners: Corner coordinates of shape (1, 8, 3) or (8, 3).
        world2img_rt: Projection matrix of shape (4, 4).

    Returns:
        np.ndarray: Array of shape (1, 8, 2) with integer pixel coordinates.
    """
    pts_4d = np.concatenate([np.array(corners).reshape(-1, 3), np.ones((8, 1))], axis=-1)
    pts_2d = pts_4d @ world2img_rt.T

    pts_2d[:, 2] = np.clip(pts_2d[:, 2], a_min=1e-5, a_max=1e5)
    pts_2d[:, 0] /= pts_2d[:, 2]
    pts_2d[:, 1] /= pts_2d[:, 2]
    imgfov_pts_2d = pts_2d[..., :2].reshape(8, 2)
    imgfov_pts_2d = imgfov_pts_2d.astype(np.int64)[None, :, :]

    return imgfov_pts_2d

def plot_rect3d_on_img(img: np.ndarray, num_rects: int, rect_corners: np.ndarray, rect_texts: Optional[List[str]] = None, color=(0, 255, 0), thickness: int = 1, fontscale: float = 1.0, shade_heading: bool = True) -> np.ndarray:
    """Plot the boundary lines of 3D rectangular on 2D images.

    Args:
        img (numpy.array): The numpy array of image.
        num_rects (int): Number of 3D rectangulars.
        rect_corners (numpy.array): Coordinates of the corners of 3D
            rectangulars. Should be in the shape of [num_rect, 8, 2].
        color (tuple[int], optional): The color to draw bboxes.
            Default: (0, 255, 0).
        thickness (int, optional): The thickness of bboxes. Default: 1.
        shade_heading (bool, optional): Whether to shade the heading direction face.
            Default: True.

    Returns:
        np.ndarray: Image array with 3D boxes rendered.
    """
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

    # First, draw shaded heading faces for all boxes
    if shade_heading:
        for i in range(num_rects):
            corners = np.clip(rect_corners[i], -1e4, 1e5).astype(np.int32)
            # Front face (heading direction): corners 1, 5, 4, 0
            # Heading is negative Y direction: when yaw=0, faces -Y
            # Ordered as: top-left, top-right, bottom-right, bottom-left
            heading_face = np.array(
                [corners[1], corners[5], corners[4], corners[0]], dtype=np.int32
            )

            # Check if all corners are within reasonable bounds
            valid_corners = True
            for corner in heading_face:
                if (
                    corner[0] < -1e4
                    or corner[0] > 1e4
                    or corner[1] < -1e4
                    or corner[1] > 1e4
                ):
                    valid_corners = False
                    break

            if valid_corners:
                # Get the color for this box
                if isinstance(color[0], int):
                    box_color = color
                else:
                    box_color = color[i]

                # Create semi-transparent overlay
                overlay = img.copy()
                # Fill the polygon with the bbox color
                cv2.fillPoly(overlay, [heading_face], box_color)
                # Blend with original image (semi-transparent overlay)
                alpha = 0.5
                img = cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0)

    # Then draw the wireframe on top
    for i in range(num_rects):
        corners = np.clip(rect_corners[i], -1e4, 1e5).astype(np.int32)
        for start, end in line_indices:
            if (
                (corners[start, 1] >= h or corners[start, 1] < 0)
                or (corners[start, 0] >= w or corners[start, 0] < 0)
            ) and (
                (corners[end, 1] >= h or corners[end, 1] < 0)
                or (corners[end, 0] >= w or corners[end, 0] < 0)
            ):
                continue
            if isinstance(color[0], int):
                cv2.line(
                    img,
                    (corners[start, 0], corners[start, 1]),
                    (corners[end, 0], corners[end, 1]),
                    color,
                    thickness,
                    cv2.LINE_AA,
                )
            else:
                cv2.line(
                    img,
                    (corners[start, 0], corners[start, 1]),
                    (corners[end, 0], corners[end, 1]),
                    color[i],
                    thickness,
                    cv2.LINE_AA,
                )

        # print text for each box
        if rect_texts is not None:
            cv2.putText(
                img,
                rect_texts[i],
                corners[3],
                cv2.FONT_HERSHEY_SIMPLEX,
                fontscale,  # scale
                (255, 255, 255),
                thickness,  # thickness
                cv2.LINE_AA,
            )

    return img.astype(np.uint8)

def load_calibration(calib_path: str) -> Dict[str, Any]:
    """Load calibration JSON from file.

    Args:
        calib_path: Filesystem path to calibration JSON.

    Returns:
        Dict[str, Any]: Parsed calibration dictionary.
    """
    with open(calib_path, 'r') as f:
        return json.load(f)

def load_xform_info(xform_json_path: Optional[str]) -> Dict[str, Any]:
    """Load optional xform correction JSON.

    Args:
        xform_json_path: Optional path to JSON with per-prim rotation info.

    Returns:
        Dict[str, Any]: Parsed dictionary or empty dict if path is None/empty.
    """
    if not xform_json_path:
        return {}
    with open(xform_json_path, 'r') as f:
        return json.load(f)

def check_nan(dict_nan: Dict[str, Any], cam_folder: str, frame_idx: int, X: float, Y: float, Z: float, W: float, L: float, H: float, pitch: float, roll: float, yaw: float, key: str) -> None:
    """Record entries that contain NaNs for later inspection.

    Args:
        dict_nan: Accumulator {cam_folder: {frame_idx: {key: [...]}}}.
        cam_folder: Camera folder name.
        frame_idx: Frame index.
        X: Center X in world coordinates.
        Y: Center Y in world coordinates.
        Z: Center Z in world coordinates.
        W: Width of the 3D box.
        L: Length of the 3D box.
        H: Height of the 3D box.
        pitch: Pitch angle in radians.
        roll: Roll angle in radians.
        yaw: Yaw angle in radians.
        key: Object identifier / prim path.

    Returns:
        None
    """
    if np.isnan(X) or np.isnan(Y) or np.isnan(Z) or np.isnan(W) or np.isnan(L) or np.isnan(H) or np.isnan(pitch) or np.isnan(roll) or np.isnan(yaw):
        if cam_folder not in dict_nan:
            dict_nan[cam_folder] = {}
        if frame_idx not in dict_nan[cam_folder]:
            dict_nan[cam_folder][frame_idx] = {}
        dict_nan[cam_folder][frame_idx][key] = [X, Y, Z, W, L, H, pitch, roll, yaw]

def check_rotation(dict_rotation: Dict[str, Any], pitch: float, roll: float, yaw: float, key: str) -> None:
    """Track objects with non-trivial pitch/roll (possible mis-rotations).

    Args:
        dict_rotation: Accumulator dict for objects with pitch/roll.
        pitch: Pitch angle in radians.
        roll: Roll angle in radians.
        yaw: Yaw angle in radians.
        key: Object identifier / prim path.

    Returns:
        None
    """
    if abs(pitch) > 1e-3 or abs(roll) > 1e-3:
        if key not in dict_rotation:
            dict_rotation[key] = []
        # dict_rotation[key].append([pitch, roll, yaw])
        dict_rotation[key] = []

def best_corners_mapping(corners: np.ndarray, corners_from_ori: np.ndarray) -> Tuple[List[int], float, float]:
    """Compute optimal 1-1 corner mapping and distances between two 8-corner sets.

    Args:
        corners: Array of shape (8, 3) for set A.
        corners_from_ori: Array of shape (8, 3) for set B.

    Returns:
        tuple: (mapping_indices, total_distance, max_pair_distance)
    """
    cost_matrix = np.linalg.norm(corners[:, None, :] - corners_from_ori[None, :, :], axis=2)
    row_ind, col_ind = linear_sum_assignment(cost_matrix)
    total_dist = cost_matrix[row_ind, col_ind].sum()
    max_dist = cost_matrix[row_ind, col_ind].max()
    return col_ind.tolist(), total_dist, max_dist

def corners_comparison(corners_comparison_dict: Dict[str, Any], corners: np.ndarray, corners_from_ori: np.ndarray, xform_info: Dict[str, Any], key: str, save_path: str, yaw: Optional[float] = None, pitch: Optional[float] = None, roll: Optional[float] = None) -> None:
    """Compare predicted versus original 3D box corners and optionally dump visuals.

    Records entries whose corner distance is above thresholds and saves a side-by-side
    plot for visual inspection.

    Args:
        corners_comparison_dict: Output accumulator for inconsistencies.
        corners: Predicted corners, shape (1, 8, 3) or compatible.
        corners_from_ori: Reference/original corners, shape (1, 8, 3) or compatible.
        xform_info: Optional per-prim rotation information used upstream.
        key: Prim path or object identifier.
        save_path: Directory to save comparison images.
        yaw: Optional yaw angle for logging.
        pitch: Optional pitch angle for logging.
        roll: Optional roll angle for logging.

    Returns:
        None
    """
    corners = np.array(corners[0])
    corners_from_ori = np.array(corners_from_ori[0])
    
    if key in xform_info:
        if xform_info[key]['rotate'] is not None:
            rotate = list(xform_info[key]['rotate'])
        else:
            rotate = list([])
    else:
        rotate = list([])

    mapping, total_dist, max_dist = best_corners_mapping(corners, corners_from_ori)
    if total_dist > 1e-1 and max_dist > 1e-2:
        # print(f"max_dist: {max_dist}, total_dist: {total_dist}")
        if key not in corners_comparison_dict:   
            corners_comparison_dict[key] = {}

        plot_corners(corners, corners_from_ori, key, save_path=f"{save_path}/{('_').join(key.split('/'))}.jpg")

def get_bbox_3d_corners(extents: Dict[str, Any]) -> np.ndarray:
    """Return transformed points in the following order: [LDB, RDB, LUB, RUB, LDF, RDF, LUF, RUF]
    where R=Right, L=Left, D=Down, U=Up, B=Back, F=Front and LR: x-axis, UD: y-axis, FB: z-axis.

    Args:
        extents (numpy.ndarray): A structured numpy array containing the fields: [`x_min`, `y_min`,
            `x_max`, `y_max`, `transform`.

    Returns:
        (numpy.ndarray): Transformed corner coordinates with shape `(N, 8, 3)`.
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
    """Expand scalars to a batch dimension expected by corner helper.

    Args:
        extents: Dict with keys x_min, x_max, y_min, y_max, z_min, z_max, transform.

    Returns:
        Dict[str, np.ndarray]: Same keys with values expanded to shape (1, ...).
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

def get_bbox_3d_scale(corners: np.ndarray) -> Tuple[float, float, float]:
    """Calculate 3D bounding box extent (W, L, H) from its corners.

    Args:
        corners: Array of shape (1, 8, 3) representing the box corners.

    Returns:
        tuple: (width_x, length_y, height_z)
    """
    x_min, x_max = min(corners[0][:, 0]), max(corners[0][:, 0])
    y_min, y_max = min(corners[0][:, 1]), max(corners[0][:, 1])
    z_min, z_max = min(corners[0][:, 2]), max(corners[0][:, 2])

    scale_x = x_max - x_min
    scale_y = y_max - y_min
    scale_z = z_max - z_min

    return (scale_x, scale_y, scale_z)

def box3d_to_corners(box3d: np.ndarray) -> np.ndarray:
    """
    Convert (X,Y,Z,W,L,H,yaw) parameterization into 8 corner points per box.

    Args:
        box3d (np.ndarray): Array of shape (N, 11) where indices 0..6 store X,Y,Z,W,L,H,yaw.

    Returns:
        np.ndarray: Corner coordinates of shape (N, 8, 3).
    """
    X, Y, Z, W, L, H, SIN_YAW, COS_YAW, VX, VY, VZ = list(range(11))  # undecoded
    CNS, YNS = 0, 1  # centerness and yawness indices in qulity
    YAW = 6  # decoded

    corners_norm = np.stack(np.unravel_index(np.arange(8), [2] * 3), axis=1)
    corners_norm = corners_norm[[0, 1, 2, 3, 4, 5, 6, 7]]
    # use relative origin [0.5, 0.5, 0]
    corners_norm = corners_norm - np.array([0.5, 0.5, 0.5])
    corners = box3d[:, None, [W, L, H]] * corners_norm.reshape([1, 8, 3])

    # rotate around z axis
    rot_cos = np.cos(box3d[:, YAW])
    rot_sin = np.sin(box3d[:, YAW])
    rot_mat = np.tile(np.eye(3)[None], (box3d.shape[0], 1, 1))
    rot_mat[:, 0, 0] = rot_cos
    rot_mat[:, 0, 1] = -rot_sin
    rot_mat[:, 1, 0] = rot_sin
    rot_mat[:, 1, 1] = rot_cos
    corners = (rot_mat[:, None] @ corners[..., None]).squeeze(axis=-1)
    corners += box3d[:, None, :3]
    return corners

def get_bbox_3d_location(corners: np.ndarray) -> Tuple[float, float, float]:
    """Compute the geometric center (x, y, z) from the 8 corners of a 3D box.

    Args:
        corners: Array of shape (1, 8, 3) representing the box corners.

    Returns:
        Tuple of (x, y, z) center coordinates.
    """
    x_min, x_max = min(corners[0][:, 0]), max(corners[0][:, 0])
    y_min, y_max = min(corners[0][:, 1]), max(corners[0][:, 1])
    z_min, z_max = min(corners[0][:, 2]), max(corners[0][:, 2])

    x = np.around((x_max + x_min) / 2, 6)
    y = np.around((y_max + y_min) / 2, 6)
    z = np.around((z_max + z_min) / 2, 6)

    return (x, y, z)


def bbox_to_translate_orient_scale(bbox_3d: Dict[str, Any], key: str, x: Optional[float] = None, y: Optional[float] = None, z: Optional[float] = None, axis_order: Optional[str] = None):
    """Compute USD-style translate, orientation, and scale from extents.

    Different object categories require axis alignment/rotation adjustments.

    Args:
        bbox_3d: Dict with x/y/z min/max and a 4x4 'transform' matrix.
        key: One of {'humanoid', 'AMRs', 'custom', 'other'} determining logic.
        x: Optional X-axis rotation in degrees (applied for 'custom'/'other').
        y: Optional Y-axis rotation in degrees (applied for 'custom'/'other').
        z: Optional Z-axis rotation in degrees (applied for 'custom'/'other').
        axis_order: Axis order for base scale, e.g. 'xyz', 'xzy', etc. Only used
            for 'custom'.

    Returns:
        tuple: (translate: Gf.Vec3d, orient: Quaternion, scale: np.ndarray)
    """
    if key == "humanoid":
        offset = ((bbox_3d["x_max"] + bbox_3d["x_min"]) / 2, (bbox_3d["y_max"] + bbox_3d["y_min"]) / 2, (bbox_3d["z_max"] + bbox_3d["z_min"]) / 2)

        translate_offset = Gf.Vec3d(offset)
        translate_mat = Gf.Matrix4d(Gf.Matrix4d().SetIdentity())
        translate_mat.SetTranslate(translate_offset)

        # Apply original transform first
        original_transform = Gf.Matrix4d(bbox_3d["transform"])
        combined_transform = translate_mat * original_transform

        # Add an X-axis rotation for humanoid alignment
        angle_degrees = -90
        rotation_x_mat = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), angle_degrees))

        # Multiply with X rotation
        combined_transform = rotation_x_mat * combined_transform

            # Get final transform
        transform = Gf.Transform(combined_transform)
        translate = transform.GetTranslation()

        orient = transform.GetRotation().GetQuat()
        scale = transform.GetScale()
        base_scale = np.array((bbox_3d["x_max"] - bbox_3d["x_min"], bbox_3d["z_max"] - bbox_3d["z_min"],  bbox_3d["y_max"] - bbox_3d["y_min"]))
    elif key == "AMRs":
        offset = ((bbox_3d["x_max"] + bbox_3d["x_min"]) / 2, (bbox_3d["y_max"] + bbox_3d["y_min"]) / 2, (bbox_3d["z_max"] + bbox_3d["z_min"]) / 2)

        translate_offset = Gf.Vec3d(offset)
        translate_mat = Gf.Matrix4d(Gf.Matrix4d().SetIdentity())
        translate_mat.SetTranslate(translate_offset)

        # Apply original transform first
        original_transform = Gf.Matrix4d(bbox_3d["transform"])
        combined_transform = translate_mat * original_transform

        # Add a Z-axis rotation for AMRs alignment
        angle_degrees = -90
        rotation_x_mat = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), angle_degrees))

        # Multiply with X rotation
        combined_transform = rotation_x_mat * combined_transform

        # Get final transform
        transform = Gf.Transform(combined_transform)
        translate = transform.GetTranslation()

        orient = transform.GetRotation().GetQuat()
        scale = transform.GetScale()
        # base_scale = np.array((bbox_3d["x_max"] - bbox_3d["x_min"], bbox_3d["z_max"] - bbox_3d["z_min"],  bbox_3d["y_max"] - bbox_3d["y_min"]))
        base_scale = np.array((bbox_3d["y_max"] - bbox_3d["y_min"], bbox_3d["x_max"] - bbox_3d["x_min"], bbox_3d["z_max"] - bbox_3d["z_min"]))
        # base_scale = np.array((bbox_3d["x_max"] - bbox_3d["x_min"], bbox_3d["y_max"] - bbox_3d["y_min"], bbox_3d["z_max"] - bbox_3d["z_min"])) 
    elif key == "custom":
        offset = ((bbox_3d["x_max"] + bbox_3d["x_min"]) / 2, (bbox_3d["y_max"] + bbox_3d["y_min"]) / 2, (bbox_3d["z_max"] + bbox_3d["z_min"]) / 2)

        translate_offset = Gf.Vec3d(offset)
        translate_mat = Gf.Matrix4d(Gf.Matrix4d().SetIdentity())
        translate_mat.SetTranslate(translate_offset)

        # Apply original transform first
        original_transform = Gf.Matrix4d(bbox_3d["transform"])
        combined_transform = translate_mat * original_transform

        if x != None:
            # Optional X-axis rotation
            angle_degrees = -x
            rotation_x_mat = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), angle_degrees))

            # Multiply with X rotation
            combined_transform = rotation_x_mat * combined_transform

        if y != None:
            # Optional Y-axis rotation
            angle_degrees = -y
            rotation_y_mat = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 1, 0), angle_degrees))

            # Multiply with Y rotation
            combined_transform = rotation_y_mat * combined_transform

        if z != None:
            # Optional Z-axis rotation
            angle_degrees = -z
            rotation_z_mat = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), angle_degrees))

            # Multiply with Z rotation
            combined_transform = rotation_z_mat * combined_transform


        # Get final transform
        transform = Gf.Transform(combined_transform)
        translate = transform.GetTranslation()

        orient = transform.GetRotation().GetQuat()
        scale = transform.GetScale()

        if axis_order == "xyz":
            base_scale = np.array((bbox_3d["x_max"] - bbox_3d["x_min"],  bbox_3d["y_max"] - bbox_3d["y_min"], bbox_3d["z_max"] - bbox_3d["z_min"])) 
        elif axis_order == "xzy":
            base_scale = np.array((bbox_3d["x_max"] - bbox_3d["x_min"],  bbox_3d["z_max"] - bbox_3d["z_min"], bbox_3d["y_max"] - bbox_3d["y_min"]))
        elif axis_order == "yzx":
            base_scale = np.array((bbox_3d["y_max"] - bbox_3d["y_min"], bbox_3d["z_max"] - bbox_3d["z_min"], bbox_3d["x_max"] - bbox_3d["x_min"])) 
        elif axis_order == "yxz":
            base_scale = np.array((bbox_3d["y_max"] - bbox_3d["y_min"], bbox_3d["x_max"] - bbox_3d["x_min"], bbox_3d["z_max"] - bbox_3d["z_min"])) 
        elif axis_order == "zyx":
            base_scale = np.array((bbox_3d["z_max"] - bbox_3d["z_min"], bbox_3d["y_max"] - bbox_3d["y_min"], bbox_3d["x_max"] - bbox_3d["x_min"])) 
        elif axis_order == "zxy":
            base_scale = np.array((bbox_3d["z_max"] - bbox_3d["z_min"], bbox_3d["x_max"] - bbox_3d["x_min"], bbox_3d["y_max"] - bbox_3d["y_min"]))  
        else:
            raise ValueError(f"Invalid axis order: {axis_order}")
    else:
        offset = ((bbox_3d["x_max"] + bbox_3d["x_min"]) / 2, (bbox_3d["y_max"] + bbox_3d["y_min"]) / 2, (bbox_3d["z_max"] + bbox_3d["z_min"]) / 2)

        translate_offset = Gf.Vec3d(offset)
        translate_mat = Gf.Matrix4d(Gf.Matrix4d().SetIdentity())
        translate_mat.SetTranslate(translate_offset)

        # Apply original transform first
        original_transform = Gf.Matrix4d(bbox_3d["transform"])
        combined_transform = translate_mat * original_transform

        if x != None:
            # Optional X-axis rotation
            angle_degrees = -x
            rotation_y_mat = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), angle_degrees))

            # Multiply with X rotation
            combined_transform = rotation_y_mat * combined_transform

        if y != None:
            # Optional Y-axis rotation
            angle_degrees = -y
            rotation_y_mat = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 1, 0), angle_degrees))

            # Multiply with Y rotation
            combined_transform = rotation_y_mat * combined_transform
        
        if z != None:
            # Optional Z-axis rotation
            angle_degrees = -z
            rotation_z_mat = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), angle_degrees))

            # Multiply with Z rotation
            combined_transform = rotation_z_mat * combined_transform
        # Get final transform
        transform = Gf.Transform(combined_transform)
        translate = transform.GetTranslation()

        orient = transform.GetRotation().GetQuat()
        scale = transform.GetScale()
        base_scale = np.array((bbox_3d["x_max"] - bbox_3d["x_min"], bbox_3d["y_max"] - bbox_3d["y_min"], bbox_3d["z_max"] - bbox_3d["z_min"])) 

    return translate , Quaternion(orient.GetReal(), *orient.GetImaginary()),  scale * base_scale

def plot_corners(corners: np.ndarray, corners_from_ori: np.ndarray, key: str, save_path: Optional[str] = None) -> None:
    """Visualize two sets of 3D corners with indices for comparison.

    Produces a 1x2 subplot: predicted corners on the left (in red), original on the right (in blue).
    Optionally saves to disk instead of showing the figure.

    Args:
        corners: Predicted corners, array of shape (8, 3).
        corners_from_ori: Reference/original corners, array of shape (8, 3).
        key: Title label (typically the prim path or object id).
        save_path: If given, path where the figure is saved; otherwise shown interactively.

    Returns:
        None
    """
    c = np.array(corners)
    c_ori = np.array(corners_from_ori)
    colors = plt.cm.viridis(np.linspace(0, 1, 8))

    fig = plt.figure(figsize=(12, 6))

    all_points = np.vstack([c, c_ori])
    x_min, x_max = all_points[:,0].min(), all_points[:,0].max()
    y_min, y_max = all_points[:,1].min(), all_points[:,1].max()
    z_min, z_max = all_points[:,2].min(), all_points[:,2].max()

    margin = 0.05
    x_range = x_max - x_min
    y_range = y_max - y_min
    z_range = z_max - z_min
    x_min, x_max = x_min - margin*x_range, x_max + margin*x_range
    y_min, y_max = y_min - margin*y_range, y_max + margin*y_range
    z_min, z_max = z_min - margin*z_range, z_max + margin*z_range

    ax1 = fig.add_subplot(121, projection='3d')
    ax1.scatter(c[:,0], c[:,1], c[:,2], c='r', label='Error corners')
    for i in range(8):
        ax1.text(c[i,0], c[i,1], c[i,2], str(i), color='k')
    # ax1.set_title('Error corners')
    ax1.legend()
    ax1.set_xlim(x_min, x_max)
    ax1.set_ylim(y_min, y_max)
    ax1.set_zlim(z_min, z_max)

    ax2 = fig.add_subplot(122, projection='3d')
    ax2.scatter(c_ori[:,0], c_ori[:,1], c_ori[:,2], c='b', label='Correct corners')
    for i in range(8):
        ax2.text(c_ori[i,0], c_ori[i,1], c_ori[i,2], str(i), color='k')
    # ax2.set_title('Correct corners')
    ax2.legend()
    ax2.set_xlim(x_min, x_max)
    ax2.set_ylim(y_min, y_max)
    ax2.set_zlim(z_min, z_max)

    for i in range(8):
        xyA = (c[i,0], c[i,1])
        xyB = (c_ori[i,0], c_ori[i,1])
        con = ConnectionPatch(
            xyA=xyA, xyB=xyB,
            coordsA="data", coordsB="data",
            axesA=ax1, axesB=ax2,
            color=colors[i], linestyle='--', linewidth=2, alpha=0
        )
        fig.add_artist(con)

    fig.suptitle(key)
    plt.tight_layout()
    if save_path is not None:
        plt.savefig(save_path)
        plt.close(fig)
    else:
        plt.show()

def main(root_dir: str, calib_path: str, xform_json_path: Optional[str], frame_idx_list: List[int], save_path: str, prim_rotate_dict: Dict[str, Tuple[float, float, float, str]], prim_extreme_dict: Dict[str, Tuple[float, float, float, str]], prim_key: Optional[List[str]] = None, object_detection_name: str = "object_detection_fixed", exclude_class: Optional[List[str]] = None, exclude_prim: Optional[List[str]] = None):
    """Entry point: project 3D bounding boxes to images and save diagnostics.

    Args:
        root_dir: Root directory containing camera folders.
        calib_path: Path to calibration JSON.
        xform_json_path: Optional path to xform/rotation JSON.
        frame_idx_list: List of frame indices to process.
        save_path: Directory to save corner comparison images.
        prim_rotate_dict: Per-prim rotation overrides: {prim: [x,y,z,axis_order]}.
        prim_extreme_dict: Per-prim extreme-value handling: {prim: [x,y,z,axis_order]}.
        prim_key: Optional whitelist of prims to render.
        object_detection_name: Subfolder name containing detection JSONs.
        exclude_class: Optional class-name exclusion list.
        exclude_prim: Optional prim-path exclusion list.

    Returns:
        None
    """
    # Ensure comparison image output directory exists
    os.makedirs(save_path, exist_ok=True)

    calibration = load_calibration(calib_path)
    xform_info = load_xform_info(xform_json_path)

    dict_nan = {}
    dict_rotation = {}
    corners_comparison_dict = {}
    for cam_folder in sorted([d for d in os.listdir(root_dir) if d.startswith("Camera")]):
        cam_path = os.path.join(root_dir, cam_folder)
        if not os.path.isdir(cam_path):
            continue
        
        obj_det_dir = os.path.join(cam_path, object_detection_name)
        if not os.path.exists(obj_det_dir):
            continue
        
        if "_World_Cameras_Camera" in cam_folder or "_World_Cameras_Metro_Camera" in cam_folder:
            if "_World_Cameras_Camera" == cam_folder or "_World_Cameras_Metro_Camera" == cam_folder:
                cam_folder = "Camera"
            else:
                cam_folder = "Camera_{}".format(cam_folder.split('_')[-1])
        
        world2img_rt = get_projection_matrix(cam_folder, calibration)
        

        for frame_idx in frame_idx_list:
        # for frame_idx in range(0, 2999):
            output_path = os.path.join(cam_path, f"bbox_3d_direction_{frame_idx:05d}.jpg")
            obj_det_path = os.path.join(cam_path, object_detection_name, f"object_detection_{frame_idx:05d}.json")
            rgb_path = os.path.join(cam_path, "rgb", f"rgb_{frame_idx:05d}.jpg")
            if not os.path.exists(obj_det_path) or not os.path.exists(rgb_path):
                print(f"File not found: {obj_det_path} or {rgb_path}")
                continue

            with open(obj_det_path, 'r') as f:
                objects = json.load(f)
            image = cv2.imread(rgb_path)
            
            for key_obj in objects:
                for key, obj in objects[key_obj].items():
                    object_class = obj['label'].get('class')
                    if object_class is not None:
                        label_name = object_class
                    else:
                        print(f"No 'class' key at {key}")
                        continue

                    if exclude_class is not None and label_name in exclude_class:
                        continue

                    if exclude_prim is not None and key in exclude_prim:
                        continue

                    if prim_key is not None and key not in prim_key:
                        continue

                    if "bounding_box_3d_fast" not in obj["annotators"] or "bounding_box_2d_tight_fast" not in obj["annotators"]:
                        continue
                    
                    bbox_3d = obj["annotators"]["bounding_box_3d_fast"]
                    corners_from_ori = get_bbox_3d_corners(extent_dimension(bbox_3d))

                    max_allowed_value = 1e10  # Set a reasonable threshold
                    has_extreme_values = False
                    for coord in ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"]:
                        if abs(bbox_3d[coord]) > max_allowed_value:
                            has_extreme_values = True

                    if label_name == "gr1_t2" or label_name == "agility_digit":
                        translate, orient, scale = bbox_to_translate_orient_scale(bbox_3d, 'humanoid')
                    elif label_name == "lr_600s_rx" or label_name == "lr_1200h_r50" or label_name == "lr_600_sx":
                        translate, orient, scale = bbox_to_translate_orient_scale(bbox_3d, 'AMRs')
                    else:
                        if key in xform_info:
                            if xform_info[key]['rotate'] is not None:
                                x,y,z = xform_info[key]['rotate']
                                z = None #z is not used in xform_info
                            else:
                                x,y,z = None, None, None
                        else:
                            x,y,z = None, None, None
                        
                        if key in prim_extreme_dict and has_extreme_values:
                            x, y, z, axis_order = prim_extreme_dict[key]
                            translate, orient, scale = bbox_to_translate_orient_scale(bbox_3d, 'custom', x=x, y=y, z=z, axis_order=axis_order)

                        if key in prim_rotate_dict:
                            x, y, z, axis_order = prim_rotate_dict[key]
                            translate, orient, scale = bbox_to_translate_orient_scale(bbox_3d, 'custom', x=x, y=y, z=z, axis_order=axis_order)
                        else:
                            translate, orient, scale = bbox_to_translate_orient_scale(bbox_3d, 'other', x=x, y=y, z=z)

                    if has_extreme_values:
                        print(f"Object with extreme values in bounding box: {key} in {cam_folder} at frame {frame_idx}")
                        scale = get_bbox_3d_scale(corners_from_ori)

                    yaw, pitch, roll = orient.yaw_pitch_roll
                    rotation = [pitch, roll, yaw]
                    X, Y, Z = translate
                    W, L, H = scale
             
                    #Check if the data exists nan
                    check_nan(dict_nan, cam_folder, frame_idx, X, Y, Z, W, L, H, pitch, roll, yaw, key)
                    check_rotation(dict_rotation, pitch, roll, yaw, key)

                    box3d = np.array([[X, Y, Z, W, L, H, yaw]])
                    corners = box3d_to_corners(box3d)

                    corners_comparison(corners_comparison_dict, corners, corners_from_ori, xform_info, key, save_path, yaw, pitch, roll)   

                    imgfov_pts_2d = corners_projection(corners, world2img_rt)

                    num_rects = len(imgfov_pts_2d)
                    color = (int(random.random()*255), int(random.random()*255), int(random.random()*255))

                    image = plot_rect3d_on_img(
                            image, 
                            num_rects, 
                            imgfov_pts_2d,
                            color=color,
                            thickness=1
                        )
                
            cv2.imwrite(output_path, image)
    
    with open(f"{root_dir}/bbox_3d_nan.json", "w") as f:
        json.dump(dict_nan, f, indent=2)
    print("The number of nan is: ", len(dict_nan))
    with open(f"{root_dir}/bbox_3d_direction_rotation.json", "w") as f:
        json.dump(dict_rotation, f, indent=2)
    print("The number of rotation is: ", len(dict_rotation))
    with open(f"{root_dir}/bbox_3d_corners_comparison.json", "w") as f:
        json.dump(corners_comparison_dict, f, indent=2)
    print(f"Inconsistent corners: {len(corners_comparison_dict)}")
    print(f"Saved bbox_3d_corners_comparison.json to {root_dir}/bbox_3d_corners_comparison.json")
    print(f"Saved bbox_3d_nan.json to {root_dir}/bbox_3d_nan.json")
    print(f"Saved bbox_3d_direction_rotation.json to {root_dir}/bbox_3d_direction_rotation.json")
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Draw 3D bbox projection for a specific frame with xform correction.")
    parser.add_argument('--root_dir', type=str, required=True, help='Root directory containing Camera folders')
    parser.add_argument('--calibration', type=str, required=True, help='Path to calibration file')
    parser.add_argument('--save_path', type=str, required=True, help='Path to save exceptional bounding box data')
    parser.add_argument('--frame_idx_list', type=int, nargs='+', required=True, help='Frame indices to process, e.g. --frame_idx_list 0 100 200')
    parser.add_argument('--prim_key', type=str, nargs='+', default=None, help='Only draw bbox for this prim key (object name)')
    parser.add_argument('--xform_json_path', type=str, default=None, help='Optional path to output_xform.json')
    args = parser.parse_args()

    # ======= USER ATTENTION REQUIRED: =======
    # Please customize the following configuration variables as needed for your application:

    # List of class names to exclude from processing (e.g., objects that are not to be annotated)
    # TODO: Adjust this list according to the classes you want to filter out in your scene.
    exclude_class = []
    # Example: ["tray", "trolley", "magazine"]

    # List of specific primitive paths to exclude from processing
    # TODO: Fill or adjust according to specific primitives to ignore.
    exclude_prim = []
    # Example: ["/World/SomeObject/", ...]

    # Name of the folder containing 2D/3D object detection annotations inside each camera folder
    # TODO: Modify if your detection output folder name is different.
    object_detection_name = "object_detection"
    # Example: "object_detection" or "object_detection_fixed"

    # Dictionary mapping specific primitive paths to rotation corrections (if any).
    # Each value is [x_deg, y_deg, z_deg, axis_order]. Axis order only applies to scale basis.
    # TODO: Fill with any special-case rotations needed for your dataset.
    prim_rotate_dict = {}
    # Example: {"/World/Object_XYZ": [90, 0, 0, "xyz"]}

    # Dictionary mapping specific primitive paths that require extreme value handling.
    # Used when bbox values overflow; applies custom rotation and axis order for scale basis.
    # TODO: Fill with cases needing special handling for extreme values.
    prim_extreme_dict = {}
    # Example: {"/World/Object_XYZ": [0, 90, 0, "xzy"]}
    # ======= END OF USER ATTENTION REQUIRED: =======
    
    main(
        root_dir=args.root_dir,
        calib_path=args.calibration,
        xform_json_path=args.xform_json_path,
        frame_idx_list=args.frame_idx_list,
        save_path=args.save_path,
        prim_rotate_dict=prim_rotate_dict,
        prim_extreme_dict=prim_extreme_dict,
        prim_key=args.prim_key,
        object_detection_name=object_detection_name,
        exclude_class=exclude_class,
        exclude_prim=exclude_prim
    )
