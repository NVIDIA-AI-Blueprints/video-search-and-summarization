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
Constants Module

This module defines global constants used throughout the spatialai_data_utils package.
It includes default values for image properties, video parameters, and object class
definitions for various detection and tracking tasks.

Constants:
- IMAGE_SIZE: Default image resolution (width, height) in pixels
- FPS: Default video frame rate in frames per second
- CLASS_LIST: List of supported object classes
- SUB_CLASS_DICT: Mapping of main classes to sub-class variants

Object Classes:
The module defines a hierarchical class structure for warehouse and industrial
environments, including:
- person: Human workers
- humanoid: Humanoid robots (various models)
- nova_carter: NVIDIA Nova Carter robot platform
- transporter: Transport vehicles
- forklift: Forklift vehicles
- box: Various box types
- pallet: Pallet types
- crate: Crate containers
- basket: Basket containers

Sub-Classes:
Each main class can have multiple sub-classes representing specific variants
or models. The SUB_CLASS_DICT provides fine-grained classification while
maintaining compatibility with the main class categories.

Usage:
Import these constants in other modules to ensure consistent configuration
across the entire package. Modify values here to change global defaults.
"""

IMAGE_SIZE = (1920, 1080)
FPS = 30.0

CLASS_LIST = [
    "person",
    "humanoid",
    "nova_carter",
    "transporter",
    "forklift",
    "box",
    "pallet",
    "crate",
    "basket",
]
SUB_CLASS_DICT = {
    "humanoid": [
        "humanoid",
        "gr1_t2",
        "agility_digit",
    ],
    "box": [
        "box",
        "flatbox",
        "multidepthbox",
        "whitecorrugatedbox",
        "printersbox",
        "cardbox",
        # "officepaperbox",
        "cubebox",
        "longbox",
    ],
    "pallet": [
        "pallet",
        "blockpallet",
        # "wooddrumpallet",
        # "rackablepallet",
        "exportpallet",
    ],
    "crate": [
        "crate",
        "woodencrate",
    ],
}
MAP_SUB_CLASS_TO_CLASS_DICT = {}
for c in SUB_CLASS_DICT.keys():
    for sub_c in SUB_CLASS_DICT[c]:
        MAP_SUB_CLASS_TO_CLASS_DICT[sub_c] = c

MAP_CLASS_NAMES = {
    "person": "Person",
    "humanoid": "Humanoid",
    "nova_carter": "NovaCarter",
    "transporter": "Transporter",
    "forklift": "Forklift",
    "box": "Box",
    "pallet": "Pallet",
    "crate": "Crate",
    "basket": "Basket",
}
ATTRIBUTE_NAMES = {
    "person": "person.moving",
    "humanoid": "humanoid.moving",
    "nova_carter": "nova_carter.moving",
    "transporter": "transporter.moving",
    "forklift": "forklift.moving",
    "box": "box.static",
    "pallet": "pallet.static",
    "crate": "crate.static",
    "basket": "basket.static",
}
CLASS_MAPPING_DICT = {}
for cid, c in enumerate(CLASS_LIST):
    CLASS_MAPPING_DICT[c] = cid

CLASS_LIST_VIZ = [
    "Person",
    "Humanoid",
    "NovaCarter",
    "Transporter",
    "Forklift",
    "Box",
    "Pallet",
    "Crate",
    "Basket",
]
CLASS_ID2NAME_DICT_VIZ = {}
for cid, c in enumerate(CLASS_LIST_VIZ):
    CLASS_ID2NAME_DICT_VIZ[cid] = c

# x_min, x_max, y_min, y_max
BEV_BOUND_DICT = {
    "full": [-26, 5, -23, 31],
    "hospital": [-48, 27, -4, 37],
    "retail": [-19, 19, -22, 24],
    "warehouse": [-11, 10, -13, 19],
    "sequence_1": [-2.4, 2.4, -7.2, 7.2],  # for wildtrack
}

# ---------------------------------------------------------------------------
# Dictionary key constants
# ---------------------------------------------------------------------------
# These string literals are used as dictionary keys throughout the package
# (calibration data, detection/tracking results, pkl info entries).  Centralising
# them here avoids typos and makes it easy to rename the underlying schema.

# Detection / tracking object dict keys
KEY_OBJECT_ID = "object id"
KEY_PERSON_ID = "person id"
KEY_CONFIDENCE = "confidence"
KEY_TYPE = "type"
KEY_LOCATION_3D = "3d location"
KEY_BBOX_SCALE_3D = "3d bounding box scale"
KEY_BBOX_ROTATION_3D = "3d bounding box rotation"

# Calibration dict keys
#
# Naming legend (3 matrix flavors + image size):
#   K   — 3x3 intrinsic matrix
#   W2C — 4x4 world-to-camera **extrinsic** transform (NOT a projection;
#         takes 3D world coords → 3D camera coords as a rigid-body move)
#   W2P — 4x4 world-to-pixel **projection** matrix (= K_4x4 @ W2C; takes
#         3D world coords → homogeneous 2D pixel coords after divide)
#
# Pre-rename these were ``KEY_INTRINSIC`` / ``KEY_PROJECTION_W2C`` /
# ``KEY_PROJECTION_W2P`` / ``KEY_IMAGE_SIZE`` with values
# ``"intrinsic matrix"`` / ``"projection matrix w2c"`` /
# ``"projection matrix w2p"`` / ``"image size"`` (note the embedded
# spaces, awkward in JSON / dict access).  Two issues:
#
# 1. Calling the **extrinsic** ``W2C`` a "projection matrix" was
#    semantically wrong — the projection happens in ``W2P``, not ``W2C``.
# 2. Spaces in dict keys are clumsy.
#
# Read-side legacy fallback (so pre-rename calibration dicts still
# load) lives in
# :func:`spatialai_data_utils.core.cameras.utils.get_calib_field` —
# this file holds *constants only*, no behavioural helpers.
KEY_INTRINSIC_MATRIX = "intrinsic_matrix"
KEY_W2C_MATRIX = "w2c_matrix"
KEY_W2P_MATRIX = "w2p_matrix"
KEY_IMAGE_SIZE = "image_size"

# Camera-group metadata keys
KEY_ORIGIN = "origin"
KEY_DIMENSIONS = "dimensions"

# Data-pkl info entry keys
KEY_CAMS = "cams"
KEY_DATA_PATH = "data_path"
KEY_FRAME_IDX = "frame_idx"
KEY_CAM_INTRINSIC = "cam_intrinsic"
KEY_SENSOR2WORLD = "sensor2world_transform"

# Legacy gt_json_aicity keys (kept for backwards compatibility with modules that
# still consume the gt_json_aicity format, e.g. ``visualization/points.py``).
KEY_VERTICES_2D = "2d vertices of 3d bounding box"
KEY_BBOX_2D = "2d bounding box"

# Raw NVSchema object / frame field names.  Used by the split
# visualization pipeline (``project_bev_objects_bbox_in_image`` / ``draw_bev_objects_bbox_in_image``)
# and by ``load_nvschema`` to work with objects in their native format.
#
# Raw NVSchema object schema:
#   {"id": str, "type": str, "confidence": float,
#    "coordinate": {"x", "y", "z"},
#    "bbox3d": {"coordinates": [x,y,z,w,l,h,pitch,roll,yaw],
#               "embedding": [...], "confidence": float}}
#
# The viz pipeline populates the native ``Bbox3d.info`` ``map<string,
# string>`` on each visible object with the camera-projected 2D corners
# of the 3D cuboid (the existing ``coordinates`` / ``embedding`` /
# ``confidence`` fields are left untouched):
#   "bbox3d": {"coordinates": [x, y, z, w, l, h, pitch, roll, yaw],
#              "embedding":   [...],
#              "confidence":  float,
#              "info":        {"sensorId": "Camera_01",
#                              "vertices": "[[x0, y0], ..., [x7, y7]]"}}
#
# ``info`` values are strings (per the NVSchema proto
# ``map<string, string>``).  ``sensorId`` mirrors the top-level
# NVSchema frame's camelCase convention and is a plain string;
# ``vertices`` is the ``json.dumps``-serialised 8 × 2 corner array
# (the 2D projection of the 3D cuboid's corners) and must be
# ``json.loads``-ed before numeric use.  The key is short (not
# ``bbox3d_vertices``) because ``info`` is already scoped inside the
# ``bbox3d`` block.  Any pre-existing ``info`` entries on the input
# are preserved (the pipeline only writes the two keys listed above).
KEY_NVSCHEMA_ID = "id"
KEY_BBOX3D = "bbox3d"
KEY_COORDINATES = "coordinates"
KEY_VERTICES = "vertices"
KEY_SENSOR_ID = "sensorId"
KEY_EMBEDDING = "embedding"
KEY_INFO = "info"
