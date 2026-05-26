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
import argparse
import numpy as np
from tqdm import tqdm
from collections import Counter
from typing import List, Set, Any, Dict, Tuple, Optional
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from collections import defaultdict
from scipy.spatial.transform import Rotation
from collections import ChainMap, Counter
from pyquaternion import Quaternion
from scipy.spatial.transform import Rotation as R
from scipy.optimize import linear_sum_assignment
from pxr import Gf
import random
import glob
import hashlib

object_id_map = None
class_name_map = {
    "character": "person", 
    "iw_hub": "transporter",
}

# ======== BBOX FILTERING CONFIGURATION ========
BBOX_FILTER_CONFIG = {
    "enable_outlier_filtering": True,      # Enable/disable outlier bbox filtering
    "max_size_threshold": 2.0,            # Maximum allowed dimension (m) - adjust based on your scene
    "volume_threshold": 15.0,              # Maximum allowed volume (m³) - None = auto-calculate via IQR
    "use_iqr_filtering": True,             # Use statistical IQR method for additional filtering
    "iqr_factor": 1.5,                     # IQR factor (1.5=standard, 3.0=conservative)
    "selection_method": "clustering",       # 'clustering', 'median', 'outlier_detection', 'original'
    "tolerance": 1e-6                      # Tolerance for similarity comparisons
}


class utils_for_data_parse:
    def extent_dimension(self, extents: Dict[str, Any]) -> Dict[str, np.ndarray]:
        """extend the demension of input data to fit the format of helper method parameter"""
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

    def get_bbox_3d_scale(self, corners: np.ndarray) -> Tuple[float, float, float]:
        """calculate bbox 3d's scale information"""
        x_min, x_max = min(corners[0][:, 0]), max(corners[0][:, 0])
        y_min, y_max = min(corners[0][:, 1]), max(corners[0][:, 1])
        z_min, z_max = min(corners[0][:, 2]), max(corners[0][:, 2])

        scale_x = np.around(x_max - x_min, 6)
        scale_y = np.around(y_max - y_min, 6)
        scale_z = np.around(z_max - z_min, 6)

        return (scale_x, scale_y, scale_z)

    def bbox_to_translate_orient_scale(self, bbox_3d: Dict[str, Any], key: str, x: Optional[float] = None, y: Optional[float] = None, z: Optional[float] = None, axis_order: Optional[str] = None) -> Tuple[Gf.Vec3d, Quaternion, np.ndarray]:
        """
        Convert a 3D bbox description into translation, orientation and scale.

        Args:
            bbox_3d (dict): Bbox fields with x/y/z_min/max and a 4x4 transform matrix.
            key (str): Branch key, e.g. 'humanoid', 'AMRs', 'custom', 'other'.
            x (float|None): Optional X-axis rotation in degrees.
            y (float|None): Optional Y-axis rotation in degrees.
            z (float|None): Optional Z-axis rotation in degrees.
            axis_order (str|None): Axis order for scale base, e.g. 'xyz', 'xzy', etc.

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

            # ➕ Add X-axis rotation
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

            # ➕ Add X-axis rotation
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
                # ➕ Add X-axis rotation
                angle_degrees = -x
                rotation_x_mat = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), angle_degrees))

                # Multiply with X rotation
                combined_transform = rotation_x_mat * combined_transform

            if y != None:
                # ➕ Add Y-axis rotation
                angle_degrees = -y
                rotation_y_mat = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 1, 0), angle_degrees))

                # Multiply with Y rotation
                combined_transform = rotation_y_mat * combined_transform

            if z != None:
                # ➕ Add Z-axis rotation
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
                # ➕ Add X-axis rotation
                angle_degrees = -x
                rotation_y_mat = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), angle_degrees))

                # Multiply with X rotation
                combined_transform = rotation_y_mat * combined_transform

            if y != None:
                # ➕ Add Y-axis rotation
                angle_degrees = -y
                rotation_y_mat = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 1, 0), angle_degrees))

                # Multiply with Y rotation
                combined_transform = rotation_y_mat * combined_transform
            
            if z != None:
                # ➕ Add Z-axis rotation
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
     

    def get_bbox_3d_corners(self, extents: Dict[str, Any]) -> np.ndarray:
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

    def compare_dicts_with_tolerance(self, dict1: Dict[str, Any], dict2: Dict[str, Any], tolerance: float = 1e-6) -> bool:
        """
        Compare two dictionaries with numerical values, allowing for a tolerance.

        Args:
            dict1 (dict): First dictionary to compare.
            dict2 (dict): Second dictionary to compare.
            tolerance (float): Tolerance for numerical differences.

        Returns:
            bool: True if dictionaries are equal within the tolerance, False otherwise.
        """
        if dict1.keys() != dict2.keys():
            return False

        for key in dict1:
            value1, value2 = dict1[key], dict2[key]
            if isinstance(value1, list) or isinstance(value1, np.ndarray):  # For lists or arrays
                if not np.allclose(value1, value2, atol=tolerance):
                    return False
            elif isinstance(value1, (float, int)):  # For numerical values
                if not abs(value1 - value2) <= tolerance:
                    return False
            elif value1 != value2:  # For other types
                return False

        return True

    def find_best_bbox_by_clustering(self, bbox_3d_list: List[Dict[str, Any]], tolerance: float = 1e-6) -> int:
        """
        Find the best bbox by clustering similar bboxes and selecting from the largest cluster.
        
        Args:
            bbox_3d_list (list): List of 3D bbox dictionaries
            tolerance (float): Tolerance for bbox similarity
            
        Returns:
            int: Index of the best bbox in the original list
        """
        if not bbox_3d_list:
            return 0
        
        if len(bbox_3d_list) == 1:
            return 0
            
        # Group similar bboxes into clusters
        clusters = []
        assigned = [False] * len(bbox_3d_list)
        
        for i, bbox in enumerate(bbox_3d_list):
            if assigned[i]:
                continue
                
            # Start a new cluster
            cluster = [i]
            assigned[i] = True
            
            # Find all bboxes similar to this one
            for j in range(i + 1, len(bbox_3d_list)):
                if not assigned[j] and self.compare_dicts_with_tolerance(bbox, bbox_3d_list[j], tolerance):
                    cluster.append(j)
                    assigned[j] = True
            
            clusters.append(cluster)
        
        # Return the first bbox from the largest cluster
        largest_cluster = max(clusters, key=len)
        return largest_cluster[0]

    def find_best_bbox_by_median(self, bbox_3d_list: List[Dict[str, Any]]) -> int:
        """
        Find the best bbox by computing median values and finding the closest bbox.
        
        Args:
            bbox_3d_list (list): List of 3D bbox dictionaries
            
        Returns:
            int: Index of the bbox closest to median values
        """
        if not bbox_3d_list:
            return 0
        
        if len(bbox_3d_list) == 1:
            return 0
            
        # Extract numerical values for each key
        keys = bbox_3d_list[0].keys()
        medians = {}
        
        for key in keys:
            values = []
            for bbox in bbox_3d_list:
                if key in bbox:
                    val = bbox[key]
                    if isinstance(val, (int, float)):
                        values.append(val)
                    elif isinstance(val, (list, np.ndarray)):
                        values.extend(val)
            
            if values:
                medians[key] = np.median(values)
        
        # Find the bbox closest to median values
        min_distance = float('inf')
        best_index = 0
        
        for i, bbox in enumerate(bbox_3d_list):
            distance = 0
            for key in medians:
                if key in bbox:
                    val = bbox[key]
                    if isinstance(val, (int, float)):
                        distance += abs(val - medians[key])
                    elif isinstance(val, (list, np.ndarray)):
                        distance += sum(abs(v - medians[key]) for v in val)
            
            if distance < min_distance:
                min_distance = distance
                best_index = i
                
        return best_index

    def find_best_bbox_with_outlier_detection(self, bbox_3d_list: List[Dict[str, Any]], tolerance: float = 1e-6) -> int:
        """
        Find the best bbox by removing outliers first, then selecting from remaining bboxes.
        
        Args:
            bbox_3d_list (list): List of 3D bbox dictionaries
            tolerance (float): Tolerance for outlier detection
            
        Returns:
            int: Index of the best bbox in the original list
        """
        if not bbox_3d_list:
            return 0
        
        if len(bbox_3d_list) <= 2:
            return 0
            
        # Calculate pairwise similarities
        similarity_scores = []
        for i, bbox in enumerate(bbox_3d_list):
            score = 0
            for j, other_bbox in enumerate(bbox_3d_list):
                if i != j and self.compare_dicts_with_tolerance(bbox, other_bbox, tolerance):
                    score += 1
            similarity_scores.append((score, i))
        
        # Sort by similarity score (higher score = more similar to others)
        similarity_scores.sort(reverse=True)
        
        # Return the bbox with highest similarity to others
        return similarity_scores[0][1]

    def filter_outlier_bboxes(self, bbox_3d_list: List[Dict[str, Any]], max_size_threshold: float = 50.0, volume_threshold: Optional[float] = None, use_iqr: Optional[bool] = None):
        """
        Filter out abnormally large bboxes before selection.
        
        Args:
            bbox_3d_list (list): List of 3D bbox dictionaries
            max_size_threshold (float): Maximum allowed size for any dimension (width/height/depth)
            volume_threshold (float): Maximum allowed volume. If None, calculated automatically
            use_iqr (bool): Use IQR method for outlier detection
            
        Returns:
            tuple: (filtered_bbox_list, valid_indices)
        """
        if not bbox_3d_list or len(bbox_3d_list) <= 1:
            return bbox_3d_list, list(range(len(bbox_3d_list)))
        
        # Use config value if not specified
        if use_iqr is None:
            use_iqr = BBOX_FILTER_CONFIG["use_iqr_filtering"]
        
        valid_indices = []
        volumes = []
        sizes = []
        
        # Calculate volumes and sizes for all bboxes
        for i, bbox in enumerate(bbox_3d_list):
            try:
                # Extract dimensions from bbox
                width = abs(bbox.get('x_max', 0) - bbox.get('x_min', 0))
                height = abs(bbox.get('y_max', 0) - bbox.get('y_min', 0)) 
                depth = abs(bbox.get('z_max', 0) - bbox.get('z_min', 0))
                
                max_dimension = max(width, height, depth)
                volume = width * height * depth
                
                # Filter by absolute size threshold
                if max_dimension <= max_size_threshold:
                    volumes.append(volume)
                    sizes.append(max_dimension)
                    valid_indices.append(i)
                else:
                    print(f"Filtering out bbox {i}: max dimension {max_dimension:.3f} > threshold {max_size_threshold}")
                    
            except (KeyError, TypeError, ValueError) as e:
                print(f"Error processing bbox {i}: {e}")
                continue
        
        if not valid_indices:
            print("Warning: All bboxes were filtered out, returning original list")
            return bbox_3d_list, list(range(len(bbox_3d_list)))
        
        # Apply IQR filtering on remaining bboxes if requested
        if use_iqr and len(valid_indices) > 3:
            final_indices = self.filter_by_iqr(volumes, valid_indices, factor=BBOX_FILTER_CONFIG["iqr_factor"])
            if final_indices:
                valid_indices = final_indices
        
        # Apply volume threshold if specified
        if volume_threshold is not None:
            final_indices = []
            for i, idx in enumerate(valid_indices):
                if volumes[i] <= volume_threshold:
                    final_indices.append(idx)
                else:
                    print(f"Filtering out bbox {idx}: volume {volumes[i]:.3f} > threshold {volume_threshold}")
            if final_indices:
                valid_indices = final_indices
        
        # Return filtered bbox list
        filtered_bboxes = [bbox_3d_list[i] for i in valid_indices]
        print(f"Filtered {len(bbox_3d_list)} bboxes down to {len(filtered_bboxes)} valid ones")
        
        return filtered_bboxes, valid_indices
    
    def filter_by_iqr(self, values: List[float], indices: List[int], factor: float = 1.5) -> List[int]:
        """
        Filter indices using IQR (Interquartile Range) method for outlier detection.
        
        Args:
            values (list): Values to analyze
            indices (list): Corresponding indices
            factor (float): IQR factor (1.5 is standard, 3.0 is more conservative)
            
        Returns:
            list: Filtered indices
        """
        if len(values) < 4:
            return indices
            
        # Calculate quartiles
        sorted_values = sorted(values)
        n = len(sorted_values)
        q1 = sorted_values[n // 4]
        q3 = sorted_values[3 * n // 4]
        iqr = q3 - q1
        
        # Define outlier bounds
        lower_bound = q1 - factor * iqr
        upper_bound = q3 + factor * iqr
        
        # Filter indices
        filtered_indices = []
        for i, val in enumerate(values):
            if lower_bound <= val <= upper_bound:
                filtered_indices.append(indices[i])
            else:
                print(f"IQR filtering out bbox {indices[i]}: value {val:.3f} outside bounds [{lower_bound:.3f}, {upper_bound:.3f}]")
        
        return filtered_indices if filtered_indices else indices

    def select_best_bbox(self, bbox_3d_list: List[Dict[str, Any]], method: str = 'clustering', tolerance: float = 1e-6, 
                        filter_outliers: bool = True, max_size_threshold: float = 50.0, volume_threshold: Optional[float] = None) -> int:
        """
        Select the best bbox using different strategies, with optional outlier filtering.
        
        Args:
            bbox_3d_list (list): List of 3D bbox dictionaries
            method (str): Selection method - 'clustering', 'median', 'outlier_detection', or 'original'
            tolerance (float): Tolerance for similarity comparisons
            filter_outliers (bool): Whether to filter out abnormally large bboxes first
            max_size_threshold (float): Maximum allowed size for any dimension
            volume_threshold (float): Maximum allowed volume
            
        Returns:
            int: Index of the best bbox in the original list
        """
        if not bbox_3d_list:
            return 0
        
        if len(bbox_3d_list) == 1:
            return 0
        
        # Filter outliers first if requested
        if filter_outliers:
            filtered_bboxes, valid_indices = self.filter_outlier_bboxes(
                bbox_3d_list, max_size_threshold, volume_threshold)
            
            if not filtered_bboxes:
                print("Warning: No valid bboxes after filtering, using original list")
                filtered_bboxes = bbox_3d_list
                valid_indices = list(range(len(bbox_3d_list)))
        else:
            filtered_bboxes = bbox_3d_list
            valid_indices = list(range(len(bbox_3d_list)))
        
        # Select best from filtered bboxes
        if len(filtered_bboxes) == 1:
            return valid_indices[0]
        
        if method == 'clustering':
            filtered_index = self.find_best_bbox_by_clustering(filtered_bboxes, tolerance)
        elif method == 'median':
            filtered_index = self.find_best_bbox_by_median(filtered_bboxes)
        elif method == 'outlier_detection':
            filtered_index = self.find_best_bbox_with_outlier_detection(filtered_bboxes, tolerance)
        elif method == 'original':
            # Original logic for comparison
            results = [self.compare_dicts_with_tolerance(filtered_bboxes[0], bbox, tolerance) for bbox in filtered_bboxes]
            counts = Counter(results)
            majority_value = True if counts[True] >= counts[False] else False
            filtered_index = results.index(majority_value)
        else:
            raise ValueError(f"Unknown method: {method}")
        
        # Return the original index
        return valid_indices[filtered_index]

    def compare_lists_with_tolerance(self, list1: List[Any], list2: List[Any], tolerance: float = 1e-6) -> bool:
        """
        Compare two lists element-wise with a tolerance for numerical differences.

        Args:
            list1 (list): First list to compare.
            list2 (list): Second list to compare.
            tolerance (float): Tolerance for numerical differences.

        Returns:
            bool: True if lists are equal within the tolerance, False otherwise.
        """
        if len(list1) != len(list2):
            return False

        for v1, v2 in zip(list1, list2):
            if isinstance(v1, list) or isinstance(v1, np.ndarray):  # For nested lists or arrays
                if not np.allclose(v1, v2, atol=tolerance):
                    return False
            elif isinstance(v1, (float, int)):  # For numerical values
                if not abs(v1 - v2) <= tolerance:
                    return False
            elif v1 != v2:  # For other types
                return False

        return True
    
    def check_for_overflow(self, bbox_3d: Dict[str, Any]) -> bool:
        """
        Check for overflow values in bounding box coordinates and transform matrices.
        
        Args:
            bbox_3d (dict): JSON data from the object detection file.
            
        Returns:
            bool: True if overflow values are detected, False otherwise.
        """
        max_allowed_value = 1e10  # Set a reasonable threshold
        
        # Check bounding box coordinates
        for coord in ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"]:
            if coord in bbox_3d and abs(bbox_3d[coord]) > max_allowed_value:
                return True
        
        # Check transform matrix
        if "transform" in bbox_3d:
            # Flatten the nested list
            flat_transform = [item for sublist in bbox_3d["transform"] for item in sublist]
            if any(math.isinf(x) for x in flat_transform) or any(abs(x) > max_allowed_value for x in flat_transform):
                return True
        
        return False

    def box3d_to_corners(self, box3d: np.ndarray) -> np.ndarray:
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
    
    def best_corners_mapping(self, corners: np.ndarray, corners_from_ori: np.ndarray) -> Tuple[List[int], float, float]:
        """
        Compute best 1-1 mapping between two 8-corner sets using Hungarian algorithm.

        Args:
            corners (np.ndarray): Shape (8,3) corner set A.
            corners_from_ori (np.ndarray): Shape (8,3) corner set B.

        Returns:
            tuple: (indices mapping list from A->B, total distance, max pair distance)
        """
        cost_matrix = np.linalg.norm(corners[:, None, :] - corners_from_ori[None, :, :], axis=2)
        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        total_dist = cost_matrix[row_ind, col_ind].sum()
        max_dist = cost_matrix[row_ind, col_ind].max()
        return col_ind.tolist(), total_dist, max_dist

    def corners_comparison(self, corners_comparison_dict: Dict[str, Any], corners: np.ndarray, corners_from_ori: np.ndarray, xform_info: Dict[str, Any], key: str, yaw: Optional[float] = None, pitch: Optional[float] = None, roll: Optional[float] = None) -> None:
        """
        Compare predicted corners to original corners and record discrepancies.

        Args:
            corners_comparison_dict (dict): Accumulator dict to record inconsistencies.
            corners (np.ndarray): Predicted corners (1,8,3) or compatible.
            corners_from_ori (np.ndarray): Original corners (1,8,3) or compatible.
            xform_info (dict): Per-object transform/rotation configuration.
            key (str): Object key/name.
            yaw (float|None): Optional yaw.
            pitch (float|None): Optional pitch.
            roll (float|None): Optional roll.

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

        mapping, total_dist, max_dist = self.best_corners_mapping(corners, corners_from_ori)
        if total_dist > 1e-1 and max_dist > 1e-2:
            # print(f"max_dist: {max_dist}, total_dist: {total_dist}")
            if key not in corners_comparison_dict:
                corners_comparison_dict[key] = {}

    def rename_camera(self, camera_id: str) -> str:
        """
        Normalize camera id like 'Camera_1' to 'Camera_01'/'Camera', keeping naming consistent.

        Args:
            camera_id (str): Original camera id.

        Returns:
            str: Normalized camera id.
        """
        if camera_id.startswith("Camera_"):
            num_str = camera_id[len("Camera_"):]
            num = int(num_str)
            if num == 0:
                return "Camera"
            elif num < 10:
                return f"Camera_0{num}"
            else:
                return f"Camera_{num}"
        return camera_id
    
    def parse_json_data(self, merged_data: Dict[str, Any], rotation_dict: Dict[str, Any], corners_comparison_dict: Dict[str, Any], xform_info: Dict[str, Any], prim_rotate_dict: Dict[str, Any], prim_extreme_dict: Dict[str, Any], rename_format_enable: bool, frame_idx: int):
        """
        Parse merged per-camera JSON annotations into per-frame object lists and 3D box corners.

        Args:
            merged_data (dict): {camera_id: {object_id: annotation_dict}} for the frame.
            rotation_dict (dict): Accumulator for objects with non-zero pitch/roll.
            corners_comparison_dict (dict): Accumulator for corners comparison logs.
            xform_info (dict): Per-object extra transform configuration.
            prim_rotate_dict (dict): Object-specific rotation overrides.
            prim_extreme_dict (dict): Extreme-value handling config per object.
            rename_format_enable (bool): Whether to output renaming-format in parallel.
            frame_idx (int): Current frame index.

        Returns:
            tuple: If rename_format_enable=True, (object_list, object_list_renaming, box_corners_dict),
                   else (object_list, box_corners_dict).
        """
        #Count how many different characters there are across multiple cameras
        object_ids = set()
        camera_ids = set()

        for camera_id in merged_data:
            camera_ids.add(camera_id)
            for object_id in merged_data[camera_id]:
                object_data = merged_data[camera_id][object_id]
                object_class = object_data['label']['class']
                object_ids.add(object_id)
        
        object_ids = sorted(object_ids, reverse=False)
        camera_ids = sorted(camera_ids, reverse=False)
 
        object_list = []
        object_list_renaming = []
        _3d_bounding_box_list = dict()
        for obj_id in object_ids:
            bbox_2d_dict = dict()
            bbox_2d_vis_dict = dict()

            if rename_format_enable:
                bbox_2d_dict_renaming = dict()
                bbox_2d_vis_dict_renaming = dict()   

            bbox_3d_list = []

            object_id = object_id_map[obj_id]
            object_name = obj_id
            class_name = object_label_map[obj_id]
            
            if class_name in class_name_map:
                class_name = class_name_map[class_name]

            for camera_id in camera_ids:
                if rename_format_enable:
                    camera_id_renaming = self.rename_camera(camera_id)

                for char_per_cam_key in merged_data[camera_id]:
                    char_per_cam = merged_data[camera_id][char_per_cam_key]
                    character_label = char_per_cam_key
                    
                    if isinstance(char_per_cam['label'], dict) and character_label == obj_id:
                        if "bounding_box_2d_tight_fast" in char_per_cam["annotators"]:
                            bbox_2d_vis = char_per_cam["annotators"]["bounding_box_2d_tight_fast"]
                            filtered_bbox = [v for k, v in bbox_2d_vis.items() if k not in ['semanticId', 'occlusionRatio']]
                            bbox_2d_vis_dict[camera_id] = filtered_bbox
                            if rename_format_enable:
                                bbox_2d_vis_dict_renaming[camera_id_renaming] = filtered_bbox
                        
                        if "bounding_box_2d_loose_fast" in char_per_cam["annotators"]:
                            bbox_2d = char_per_cam["annotators"]["bounding_box_2d_loose_fast"]
                            filtered_bbox = [v for k, v in bbox_2d.items() if k not in ['semanticId', 'occlusionRatio']]
                            bbox_2d_dict[camera_id] = filtered_bbox
                            if rename_format_enable:
                                bbox_2d_dict_renaming[camera_id_renaming] = filtered_bbox

                        if "bounding_box_3d_fast" in char_per_cam["annotators"]:
                            bbox_3d = char_per_cam["annotators"]["bounding_box_3d_fast"]
                            # Removing 'semanticId' and 'occlusionRatio' from comparison
                            filtered_bbox = {k: v for k, v in bbox_3d.items() if k not in ['semanticId', 'occlusionRatio']}
                            bbox_3d_list.append(filtered_bbox)  

            if bbox_3d_list:                
                # Find the best bbox using improved selection method with configurable outlier filtering
                majority_index = self.select_best_bbox(
                    bbox_3d_list, 
                    method=BBOX_FILTER_CONFIG["selection_method"], 
                    tolerance=BBOX_FILTER_CONFIG["tolerance"],
                    filter_outliers=BBOX_FILTER_CONFIG["enable_outlier_filtering"],
                    max_size_threshold=BBOX_FILTER_CONFIG["max_size_threshold"],
                    volume_threshold=BBOX_FILTER_CONFIG["volume_threshold"]
                )
                bbox_3d_per_frame = bbox_3d_list[majority_index]
                
                has_extreme_values = False
                if self.check_for_overflow(bbox_3d_per_frame):
                    has_extreme_values = True

                if class_name == "gr1_t2" or class_name == "agility_digit": 
                    translate, orient, scale = self.bbox_to_translate_orient_scale(bbox_3d_per_frame, 'humanoid')
                elif class_name == "lr_600s_rx" or class_name == "lr_1200h_r50" or class_name == "lr_600_sx":
                    translate, orient, scale = self.bbox_to_translate_orient_scale(bbox_3d_per_frame, 'AMRs')
                else:
                    # key = "/".join(object_name.split("/")[:-1])
                    key = object_name
                    if key in xform_info:
                        if xform_info[key]['rotate'] is not None:
                            x,y,z = xform_info[key]['rotate']
                            z = None #z is not used in xform_info
                        else:
                            x,y,z = None, None, None
                    else:
                        x,y,z = None, None, None
                    
                    if key in prim_extreme_dict and has_extreme_values:
                        # print(f"key in prim_extreme_dict: {key} and {object_name}")
                        x, y, z, axis_order = prim_extreme_dict[key]
                        translate, orient, scale = self.bbox_to_translate_orient_scale(bbox_3d_per_frame, 'custom', x=x, y=y, z=z, axis_order=axis_order)

                    if key in prim_rotate_dict:
                        # print(f"key in prim_rotate_dict: {key} and {object_name}")
                        x, y, z, axis_order = prim_rotate_dict[key]
                        translate, orient, scale = self.bbox_to_translate_orient_scale(bbox_3d_per_frame, 'custom', x=x, y=y, z=z, axis_order=axis_order)
                    else:
                        # print(f"key in other: {key} and {object_name}")
                        translate, orient, scale = self.bbox_to_translate_orient_scale(bbox_3d_per_frame, 'other', x=x, y=y, z=z)
                

                corners_from_ori = self.get_bbox_3d_corners(self.extent_dimension(bbox_3d_per_frame))

                if self.check_for_overflow(bbox_3d_per_frame):
                    print(f"object {object_name} exists extreme values")
                    scale = self.get_bbox_3d_scale(corners_from_ori)

                # get 3d foot location of bbox in the world 3D coordinates
                _3d_location = tuple(translate)

                # get 3d bounding box scale, where x = width, y = length, and z = high
                _3d_bounding_box_scale = tuple(scale)

                # get 3d bounding box rotation
                yaw, pitch, roll = orient.yaw_pitch_roll
                rotation = [pitch, roll, yaw]
                rotate_in_degree = tuple(rotation)

                if abs(pitch) > 1e-2 or abs(roll) > 1e-2:
                    if object_name not in rotation_dict:
                        rotation_dict[object_name] = []
                    rotation_dict[object_name].append([pitch, roll, yaw])
                
                X, Y, Z = translate
                W, L, H = scale
                box3d = np.array([[X, Y, Z, W, L, H, yaw]])
                corners = self.box3d_to_corners(box3d)
                self.corners_comparison(corners_comparison_dict, corners, corners_from_ori, xform_info, object_name, yaw, pitch, roll)

                character_data = {
                    "object type": class_name,
                    "object id": object_id,
                    "object name": object_name,
                    "3d location": _3d_location,
                    "3d bounding box scale": _3d_bounding_box_scale,
                    "3d bounding box rotation": rotate_in_degree,
                    "2d bounding box": bbox_2d_dict,
                    "2d bounding box visible": bbox_2d_vis_dict,
                }

                if rename_format_enable:
                    character_data_renaming = {
                        "object type": class_name,
                        "object id": object_id,
                        "object name": object_name,
                        "3d location": _3d_location,
                        "3d bounding box scale": _3d_bounding_box_scale,
                        "3d bounding box rotation": rotate_in_degree,
                        "2d bounding box": bbox_2d_dict_renaming,
                        "2d bounding box visible": bbox_2d_vis_dict_renaming,
                    } 

                _3d_bounding_box_list[object_name] = corners_from_ori.tolist()
                object_list.append(character_data)
                if rename_format_enable:
                    object_list_renaming.append(character_data_renaming)             

        if rename_format_enable:
            return object_list, object_list_renaming, _3d_bounding_box_list
        else:
            return object_list, _3d_bounding_box_list

    def convert_camera_string(self, camera_str: str) -> str:
        """
        Normalize folder names like '_World_Cameras_Camera' or '_World_Cameras_Metro_Camera'
        to 'Camera' or 'Camera_XX'.

        Args:
            camera_str (str): Original folder name.

        Returns:
            str: Normalized camera name, e.g. 'Camera' or 'Camera_01'.
        """
        match = re.search(r'_World_Cameras_(?:Metro_)?Camera(?:_(\d+))?', camera_str)
        if match:
            if match.group(1):
                num = int(match.group(1))
                formatted_num = f"{num:02d}" if num < 10 else f"{num}"
                return f"Camera_{formatted_num}"
            else:
                return "Camera"
        return camera_str

    def rename_camera_folders(self, scene_path: str) -> None:
        """
        Rename camera subfolders under a scene directory to the normalized 'Camera*' naming.

        Args:
            scene_path (str): Path to the scene directory.

        Returns:
            None
        """
        camera_folders = sorted([
            d for d in os.listdir(scene_path)
            if d.startswith("_World_Cameras_Camera") or d.startswith("_World_Cameras_Metro_Camera")
        ])
        for folder_name in camera_folders:
            old_folder_path = os.path.join(scene_path, folder_name)
            new_folder_name = self.convert_camera_string(folder_name)
            new_folder_path = os.path.join(scene_path, new_folder_name)
            os.rename(old_folder_path, new_folder_path)

    def initialize_object_id_map(self, object_label_map: Dict[str, Any]) -> None:
        """
        Initialize the global object_id_map from object_label_map.

        Args:
            object_label_map (Dict[str, Any]): Map of object_id to class or metadata.

        Returns:
            None
        """
        global object_id_map
        object_id_map = {object_id: idx for idx, object_id in enumerate(object_label_map, start=0)}

    def generate_ground_truth_for_scene(
        self,
        scene_dir: str, 
        output_directory: str, 
        exclude_class: List[str], 
        exclude_prim: List[str], 
        xform_info_path: str, 
        prim_rotate_dict: Dict[str, Any], 
        prim_extreme_dict: Dict[str, Any], 
        object_detection_name: str, 
        rename_format_enable: bool) -> Tuple[str, str]:
        """
        Process a directory of scenes to generate consolidated ground truth JSON files.

        Args:
            scene_dir (str): Path to the directory containing scene subfolders.
            output_directory (str): Path to save the processed ground truth JSON.

        Returns:
            str, str: Paths to the saved JSON files for _3d_bounding_box_list and frame_data.
        """
        utils_vis = utils_for_vis()

        subfolders = [f.path for f in os.scandir(scene_dir) if f.is_dir() and f.name.startswith("Camera")]

        # Check if all subfolders have the same frame count
        frame_counts = [
                            len([
                                f for f in os.listdir(os.path.join(subfolder, object_detection_name)) 
                                if f.endswith('.json')
                            ])
                            for subfolder in subfolders
                            if os.path.basename(subfolder).startswith("Camera")
                        ]

        if not all(count == frame_counts[0] for count in frame_counts):
            raise ValueError("Mismatch in frame counts across subfolders. Please ensure all subfolders have the same number of frames.")

        frame_count = frame_counts[0]
        ground_truth_path = os.path.join(output_directory, 'ground_truth.json')
        bounding_box_path = os.path.join(output_directory, 'bounding_boxes.json')

        if rename_format_enable:
            ground_truth_renaming_path = os.path.join(output_directory, 'ground_truth_renaming.json')   

        # Initialize character_count as a list to store dictionaries
        global object_label_map
        object_label_map = {}

        for frame_idx in range(frame_count):
            # Process each subfolder to load frame data
            for subfolder in subfolders:
                if os.path.basename(subfolder).startswith("Camera"):
                    json_files = sorted([
                        f for f in os.listdir(os.path.join(subfolder, object_detection_name))
                        if f.endswith('.json')
                    ])
                    json_file_name = json_files[frame_idx]
                    json_file_path = os.path.join(subfolder, object_detection_name, json_file_name)

                    with open(json_file_path, 'r') as json_file:
                        data = json.load(json_file)
                    
                    object_with_no_class = []
                    # Merge data into character_count list as dictionaries
                    for category, objects in data.items():
                        if objects:
                            for object_id, object_data in objects.items():
                                object_class = object_data['label'].get('class')
                                if object_class is None:
                                    object_with_no_class.append([object_id, category])
                                    # print(f"Exception at object_id={object_id}: No 'class' field in label")
                                elif object_class not in exclude_class and object_id not in exclude_prim:
                                    object_label_map[object_id] = object_class
        
        
        self.initialize_object_id_map(object_label_map)

        print("object_with_no_class:", object_with_no_class)
        print("----------------------------------------------------------------")
        print("object_label_map:", object_label_map, len(object_label_map))
        print("----------------------------------------------------------------")
        print("object_id_map:", object_id_map, len(object_id_map))
        print(f"initialize_object_ids finished")

        rotation_dict = {}
        corners_comparison_dict = {}
        xform_info = utils_vis.load_json_from_file(xform_info_path) if xform_info_path else {}

        try:
            if rename_format_enable:
                gt_file = open(ground_truth_path, 'w')
                bbox_file = open(bounding_box_path, 'w')
                gt_renaming_file = open(ground_truth_renaming_path, 'w')
                # files = (gt_file, bbox_file, gt_ori_file)
            else:
                gt_file = open(ground_truth_path, 'w')
                bbox_file = open(bounding_box_path, 'w')
                # files = (gt_file, bbox_file)

            gt_file.write('{\n')
            bbox_file.write('{\n')
            if rename_format_enable:
                gt_renaming_file.write('{\n')

            for frame_idx in range(frame_count):
                merged_data = {}

                # Process each subfolder to load frame data
                for subfolder in subfolders:
                    if os.path.basename(subfolder).startswith("Camera"):
                        json_files = sorted([
                                        f for f in os.listdir(os.path.join(subfolder, object_detection_name))
                                        if f.endswith('.json')
                                    ])
                                    
                        json_file_name = json_files[frame_idx]
                        json_file_path = os.path.join(subfolder, object_detection_name, json_file_name)

                        with open(json_file_path, 'r') as json_file:
                            data = json.load(json_file)

                        # Merge data into merged_data dictionary
                        data_per_frame = {}
                        for category, objects in data.items():
                            if objects:
                                entity_new = objects.copy()
                                for object_id, object_data in objects.items():
                                    object_class = object_data['label'].get('class')
                                    if object_class is None:
                                        value = entity_new.pop(object_id)
                                        # print(f"Exception at {object_id} at {category}: No 'class' key")
                                        continue
                                    
                                    if object_class in exclude_class or object_id in exclude_prim:
                                        value = entity_new.pop(object_id)
                                        # print(f"Exception at {object_id} at {category}: Exclude class or prim")
                                        continue

                                data_per_frame.update(entity_new)

                        merged_data[os.path.basename(subfolder)] = data_per_frame

                # Parse the frame data
                if rename_format_enable:
                    character_cur_frame, character_cur_frame_renaming, _3d_bounding_box_list = self.parse_json_data(merged_data, rotation_dict, corners_comparison_dict, xform_info, prim_rotate_dict, prim_extreme_dict, rename_format_enable, frame_idx)
                else:
                    character_cur_frame, _3d_bounding_box_list = self.parse_json_data(merged_data, rotation_dict, corners_comparison_dict, xform_info, prim_rotate_dict, prim_extreme_dict, rename_format_enable, frame_idx)

                # Write frame data incrementally
                gt_file.write(f'"{frame_idx}": ')
                json.dump(character_cur_frame, gt_file)
                if frame_idx < frame_count - 1:
                    gt_file.write(',\n')  # Add a comma and newline for all except the last entry

                # Write bounding box data incrementally
                bbox_file.write(f'"{frame_idx}": ')
                json.dump(_3d_bounding_box_list, bbox_file)
                if frame_idx < frame_count - 1:
                    bbox_file.write(',\n')  # Add a comma and newline for all except the last entry

                if rename_format_enable:
                    gt_renaming_file.write(f'"{frame_idx}": ')
                    json.dump(character_cur_frame_renaming, gt_renaming_file)
                    if frame_idx < frame_count - 1:
                        gt_renaming_file.write(',\n')  # Add a comma and newline for all except the last entry

            gt_file.write('\n}\n')
            bbox_file.write('\n}\n')
            if rename_format_enable:
                gt_renaming_file.write('\n}\n')

        except Exception as e:
            print(f"Error processing directory: {e}")
            raise
        
        rotation_keys_with_rot = []
        for key, rot_list in rotation_dict.items():
            unique_rots = [list(x) for x in set(tuple(x) for x in rot_list)]
            rotation_keys_with_rot.append({"object_name": key, "rotations": unique_rots})
        
        with open(f"{output_directory}/rotation_keys_with_rot.json", "w") as f:
            json.dump(rotation_keys_with_rot, f, indent=2)
        print("The number of rotation is: ", len(rotation_keys_with_rot))

        with open(f"{output_directory}/corners_comparison_dict.json", "w") as f:
            json.dump(corners_comparison_dict, f, indent=2)
        print(f"Inconsistent corners: {len(corners_comparison_dict)}")
        
        print(f"Ground truth JSON written to {ground_truth_path}")
        print(f"Bounding box JSON written to {bounding_box_path}")

        return ground_truth_path, bounding_box_path

class utils_for_vis:

    def validate_file_path(self, input_string: str) -> str:
        """
        Validates whether the input string matches a file path pattern

        :param str input_string: input string
        :return: validated file path
        :rtype: str
        ::

            file_path = validate_file_path(input_string)
        """
        file_path_pattern = r"^[a-zA-Z0-9_\-\/.#]+$"
        if re.match(file_path_pattern, input_string):
            return input_string
        else:
            raise ValueError(f"Invalid file path: {input_string}")

    def load_json_from_file(self, file_path: str) -> Any:
        """
        Loads JSON data from a file

        :param str file_path: file path
        :return: data in the file
        :rtype: Any
        ::

            data = load_json_from_file(file_path)
        """
        valid_file_path = self.validate_file_path(file_path)
        with open(valid_file_path, "r") as f:
            data = json.load(f)
        return data

    def plot_rect3d_on_img(self, img: np.ndarray, num_rects: int, rect_corners: np.ndarray, rect_texts: Optional[List[str]] = None, color=(0, 255, 0), thickness: int = 1, fontscale: float = 1.0, shade_heading: bool = True) -> np.ndarray:
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
                    # Blend with original image (alpha=0.3 for 30% opacity)
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

    def get_cam_params_from_calib_intr_extr(self, calibration: Dict[str, Any], idx: int) -> np.ndarray:
        """Compose world-to-image matrix from calibration intrinsics and extrinsics.

        Args:
            calibration: Calibration dict with 'sensors' list.
            idx: Sensor index within calibration['sensors'].

        Returns:
            np.ndarray: 4x4 projection matrix K @ [R|t].
        """
        intrinsic = np.eye(4)
        intrinsic[:3, :3] = np.array(calibration['sensors'][idx]["intrinsicMatrix"])
        extrinsic = np.eye(4)
        extrinsic[:3] = np.array(calibration['sensors'][idx]["extrinsicMatrix"])

        world2img_rt = intrinsic @ extrinsic

        return world2img_rt

    def process_video_across_cameras(self, scene_dir: str, calibration_path: str, output_directory: str, max_frames: Optional[int] = None) -> None:
        """
        Process per-camera videos, overlaying 3D boxes on each frame.

        Args:
            scene_dir (str): Path to the scene directory.
            output_directory (str): Path to the output directory.
            cam_mode (str): Camera mode (default: 'cali').
            camera_name (str): Name of the camera folder (default: 'Camera_0001').
        """

        utils_parser = utils_for_data_parse()

        # Load ground truth JSON file
        ground_truth_path = os.path.join(output_directory, 'ground_truth.json')

        with open(ground_truth_path, 'r') as gt_file:
            frame_data = json.load(gt_file)

        # Load camera parameters once
        camera_param = self.load_json_from_file(calibration_path)

        subfolders = [f.path for f in os.scandir(scene_dir) if f.is_dir() and f.name.startswith("Camera")]
        for camera_path in subfolders:
            camera_name = os.path.basename(camera_path)
            if camera_name == 'Camera':
                idx = 0
            else:
                idx = int(camera_name.split("_")[1])
            world2img_rt = self.get_cam_params_from_calib_intr_extr(camera_param, idx)

            # Maintain a stable color per object id within this video
            object_id_to_color = {}

            def _bright_color_component(x: int) -> int:
                """Map a byte to a brighter 0-255 component avoiding very dark colors."""
                return int(min(255, 64 + (x / 255.0) * 191))

            def _color_for_object_id(object_id_value: Any) -> tuple:
                """Derive a stable BGR color from an object id via md5 digest."""
                digest = hashlib.md5(str(object_id_value).encode('utf-8')).digest()
                b, g, r = digest[0], digest[1], digest[2]
                return (
                    _bright_color_component(b),
                    _bright_color_component(g),
                    _bright_color_component(r),
                )

        
            # Process video (assuming video file exists in the scene directory)
            video_path = os.path.join(scene_dir, f'{camera_name}/video.mp4')
            cap = cv2.VideoCapture(video_path)

            output_video_path = os.path.join(output_directory, f'{camera_name}_ploted_video.mp4')
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(
                output_video_path,
                fourcc,
                30.0,
                (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            )

            frame_id = 0
            while cap.isOpened():
                if max_frames is not None and frame_id >= max_frames:
                    break
                ret, frame = cap.read()
                if not ret:
                    break

                # Iterate over bounding boxes for the current frame
                # for char_name, char_dict in zip(bbox_data[str(frame_id)], frame_data[str(frame_id)]):
                for char_dict in frame_data[str(frame_id)]:
                    # Load camera parameters for the current frame
                    location = char_dict['3d location']
                    scale = char_dict['3d bounding box scale']
                    rotation = char_dict['3d bounding box rotation']
                    object_id_value = char_dict.get('object id', char_dict.get('object name'))
                    
                    pitch, roll, yaw = rotation 
                    X, Y, Z = location
                    W, L, H = scale

                    box3d = np.array([[X, Y, Z, W, L, H, yaw]])
                    corners = utils_parser.box3d_to_corners(box3d)
                    
                    pts_4d = np.concatenate([np.array(corners).reshape(-1, 3), np.ones((8, 1))], axis=-1)
                    pts_2d = pts_4d @ world2img_rt.T

                    pts_2d[:, 2] = np.clip(pts_2d[:, 2], a_min=1e-5, a_max=1e5)
                    pts_2d[:, 0] /= pts_2d[:, 2]
                    pts_2d[:, 1] /= pts_2d[:, 2]
                    imgfov_pts_2d = pts_2d[..., :2].reshape(8, 2)
                    
                    corners_camera = np.array(imgfov_pts_2d).astype(np.int64)[None, :, :]
                    num_rects = len(corners_camera)

                    # Get stable color for this object id within the current video
                    if object_id_value not in object_id_to_color:
                        object_id_to_color[object_id_value] = _color_for_object_id(object_id_value)
                    
                    frame = self.plot_rect3d_on_img(
                        frame,
                        num_rects,
                        corners_camera,
                        color=object_id_to_color[object_id_value],
                        thickness=3,
                    )

                out.write(frame)
                frame_id += 1

            cap.release()
            out.release()
            print(f"Processed video saved to {output_video_path}")

    def process_image(self, scene_dir: str, calibration_path: str, output_directory: str, frame_id_list: List[int]) -> None:
        """
        Process a image, overlaying 2D and 3D bounding boxes on each frame.

        Args:
            scene_dir (str): Path to the scene directory.
            output_directory (str): Path to the output directory.
            cam_mode (str): Camera mode (default: 'cali').
            camera_name (str): Name of the camera folder (default: 'Camera_0001').
        """
        utils_parser = utils_for_data_parse()

        # Load ground truth JSON file
        ground_truth_path = os.path.join(output_directory, 'ground_truth.json')

        with open(ground_truth_path, 'r') as gt_file:
            frame_data = json.load(gt_file)

        #Load camera parameters
        camera_param = self.load_json_from_file(calibration_path)

        subfolders = [f.path for f in os.scandir(scene_dir) if f.is_dir() and f.name.startswith("Camera")]
        # print("subfolders:", subfolders)
        for camera_path in subfolders:
            camera_name = os.path.basename(camera_path)
            if camera_name == 'Camera':
                idx = 0
            else:
                idx = int(camera_name.split("_")[1])
            world2img_rt = self.get_cam_params_from_calib_intr_extr(camera_param, idx)
        
            for frame_id in frame_id_list:

                image_path = os.path.join(scene_dir, f'{camera_name}/rgb/rgb_{frame_id:05d}.jpg')
                frame = cv2.imread(image_path)

                output_path = os.path.join(output_directory, f'{camera_name}/rgb_{frame_id:05d}_ploted.jpg')

                # Iterate over bounding boxes for the current frame
                for char_dict in frame_data[str(frame_id)]:
                    # Load camera parameters for the current frame
                    location = char_dict['3d location']
                    scale = char_dict['3d bounding box scale']
                    rotation = char_dict['3d bounding box rotation']
                    class_name = char_dict['object type']
                    
                    pitch, roll, yaw = rotation 
                    X, Y, Z = location
                    W, L, H = scale

                    box3d = np.array([[X, Y, Z, W, L, H, yaw]])
                    corners = utils_parser.box3d_to_corners(box3d)
                    
                    pts_4d = np.concatenate([np.array(corners).reshape(-1, 3), np.ones((8, 1))], axis=-1)
                    pts_2d = pts_4d @ world2img_rt.T

                    pts_2d[:, 2] = np.clip(pts_2d[:, 2], a_min=1e-5, a_max=1e5)
                    pts_2d[:, 0] /= pts_2d[:, 2]
                    pts_2d[:, 1] /= pts_2d[:, 2]
                    imgfov_pts_2d = pts_2d[..., :2].reshape(8, 2)
                    
                    corners_camera = np.array(imgfov_pts_2d).astype(np.int64)[None, :, :]
                    num_rects = len(corners_camera)

                    color = (int(random.random()*255), int(random.random()*255), int(random.random()*255))
                    frame = self.plot_rect3d_on_img(
                            frame, 
                            num_rects, 
                            corners_camera,
                            color=color,
                            thickness=1
                        )

                cv2.imwrite(output_path, frame)
                print(f"Processed image saved to {output_path}")

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process and rename camera folders in dataset scenes.")
    parser.add_argument("input", type=str, help="Path to the root directory containing scene folders.")
    parser.add_argument("--calibration", type=str, help="Path to the calibration file.") 
    parser.add_argument("--output", type=str, help="Path to the output directory where processed data will be saved.")
    parser.add_argument("--max_frames", type=int, default=None, help="Max frames to render per camera video (omit for all frames)")
    parser.add_argument("--xform_info", type=str, default=None, help="Optional path to xform_info file (omit if not needed).")
    return parser

def main() -> None:
    # Parse command-line arguments
    parser = build_arg_parser()
    args = parser.parse_args()

    root_directory = args.input
    output_directory = args.output
    xform_info_path = args.xform_info
    calibration_path = args.calibration
    max_frames = args.max_frames

    # ======= USER ATTENTION REQUIRED: =======
    # Please customize the following configuration variables as needed for your application:
    
    # List of class names to exclude from processing (e.g., objects that are not to be annotated)
    # TODO: Adjust this list according to the classes you want to filter out in your scene.
    exclude_class = []
    # Example: ["tray", "trolley", "magazine", "barrel", ...]
    
    # List of specific primitive paths to exclude from processing
    # TODO: Fill or adjust according to specific primitives to ignore.
    exclude_prim = []
    # Example: ["/World/SomeObject/", ...]
    
    # Set this to True to enable renaming the annotation format, or False to use the default
    # TODO: Set this flag depending on desired annotation format.
    rename_format_enable = False
    
    # Name of the folder containing 2D object detection annotations in each camera folder
    # TODO: Modify if your detection output folder name is different.
    object_detection_name = "object_detection"
    
    # Dictionary mapping specific primitive paths to rotation corrections (if any).
    # TODO: Fill with any special-case rotations needed for your dataset.
    prim_rotate_dict = {}
    # Example:
    # prim_rotate_dict = {"/World/Object_XYZ": [90, 0, 0, "xyz"], ...}
    
    # Dictionary mapping specific primitive paths that require extreme value handling.
    # TODO: Fill with cases needing special handling for extreme values.
    prim_extreme_dict = {}
    # Example:
    # prim_extreme_dict = {"/World/Object_XYZ": [90, 0, 0, "xyz"], ...}
    # ======= END OF USER ATTENTION REQUIRED: =======
    
    # Check if root_directory exists
    if not os.path.exists(root_directory):
        print(f"Error: Root directory '{root_directory}' does not exist.")
        return
    
    # Create output_directory if it does not exist
    os.makedirs(output_directory, exist_ok=True)

    # Iterate through scenes in root_directory
    if os.path.isdir(root_directory):
        scene = os.path.basename(root_directory)
        print(f"Processing scene: {scene}")

        utils_parser = utils_for_data_parse()
        utils_vis = utils_for_vis()

        # Rename camera folders
        print("Start to rename camera folder")
        utils_parser.rename_camera_folders(root_directory)
        print("Done")

        # Convert annotation format
        print("Start to convert format")
        ground_truth_path, bounding_box_path = utils_parser.generate_ground_truth_for_scene(
            root_directory,
            output_directory,
            exclude_class,
            exclude_prim,
            xform_info_path,
            prim_rotate_dict,
            prim_extreme_dict,
            object_detection_name,
            rename_format_enable,
        )  
        print("Done")
        
        print("Start to generate demo images")
        utils_vis.process_image(root_directory, calibration_path, output_directory, frame_id_list=[0, 150, 200, 300, 500, 750, 1000, 1500, 2000, 3000])
        print("Done")
        
        print("Start to generate demo videos")
        utils_vis.process_video_across_cameras(root_directory, calibration_path, output_directory, max_frames=max_frames)
        print("Image and video processing completed.")

if __name__ == "__main__":
    main()
