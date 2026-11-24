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


class CameraData:
    def __init__(self, projection_matrix, human_model_data):
        self.proj_mat_world2cam = projection_matrix
        self.human_model = human_model_data
        self.image_width = 1920 # TODO: move to config 
        self.image_height = 1080
        self.m_HomographyMat_CamOpticalCenter2WorldWaistPlane = np.zeros((3, 3))
        self.init_camera_data()

    def init_camera_data(self):
        homog_mat = np.zeros((3, 3))
        homog_mat[:3, :2] = self.proj_mat_world2cam[:3, :2]
        homog_mat[:3, -1:] = self.proj_mat_world2cam[:3, -1:]
        homog_mat[0, 2] += self.proj_mat_world2cam[0, 2] * self.human_model.height / 2
        homog_mat[1, 2] += self.proj_mat_world2cam[1, 2] * self.human_model.height / 2
        homog_mat[2, 2] += self.proj_mat_world2cam[2, 2] * self.human_model.height / 2
        self.m_HomographyMat_CamOpticalCenter2WorldWaistPlane = np.linalg.inv(homog_mat)


class CameraExec:

    def __init__(self, cc_dict):
        """
        :param cc_dict: camera config dict
        """
        if "human" not in cc_dict:
            print(cc_dict)
        self.radius = cc_dict["human"]["radius"]
        self.height = cc_dict["human"]["height"]
        self.fl = cc_dict["focal_length"]
        self.rx = cc_dict["rotation"]["x"]
        self.ry = cc_dict["rotation"]["y"]
        self.rz = cc_dict["rotation"]["z"]
        self.tx = cc_dict["translation"]["x"]
        self.ty = cc_dict["translation"]["y"]
        self.tz = cc_dict["translation"]["z"]

    def run(self, bbox_gt):
        mat = self.make_matrix_from_params()
        cam_data = CameraData(mat, self)

        def fun_get_bbox_from_point(point):
            return compute_bbox_from_waist_point(cam_data, point)

        bboxes = generate_all_bbox(bbox_gt, fun_get_bbox_from_point)
        return bboxes

    def make_matrix_from_params(self):
        mat_focal_length = np.identity(4)
        mat_focal_length[0][0] = self.fl 
        mat_focal_length[1][1] = self.fl 
        mat_rotation_translation = make_rotation_translation_matrix(
            (self.rx, self.ry, self.rz),
            (self.tx, self.ty, self.tz)
        )
        res = np.matmul(mat_focal_length, mat_rotation_translation)
        return res



    def get_cylinder_circles(self, bbox_gt):
        mat = self.make_matrix_from_params()
        cam_data = CameraData(mat, self)

        head_circles = []
        feet_circles = []
        for box in bbox_gt:
            center = get_center_from_bbox(box)
            head_pts, feet_pts = \
                calculate_projected_cylinder_circles_from_bbox_center(cam_data, center)
            head_circles.append(head_pts)
            feet_circles.append(feet_pts)
        return head_circles, feet_circles



def square_iou(bbox1, bbox2):
    res = compute_iou(bbox1, bbox2)
    if res is None:
        return 0
    return res ** 2

def weighted_square_iou(bbox_gt, bbox):
    res = compute_iou(bbox_gt, bbox)
    if res is None:
        return 0
    b1w = bbox_gt[2] - bbox_gt[0]
    b1h = bbox_gt[3] - bbox_gt[1]
    if b1w < 100 and b1h <200:
        res *= 20
    return res ** 2


EVAL_FUNCTIONS = {
    "square_iou": square_iou, #TODO experimental 
    "weighted_square_iou": weighted_square_iou
}


class CameraEval:
    def __init__(self, config):
        function_name = config["bbox_matching_function"]
        self.bbox_function = EVAL_FUNCTIONS[function_name]

    def run(self, bbox_gt, candidate_bboxes):
        scores = compute_2bbox_list(bbox_gt, candidate_bboxes, self.bbox_function)
        # TODO: this is experimental code. need to remove
        # add weight
        #weighted_scores = self.multiply_weight(bbox_gt, scores)
        #return np.array(weighted_scores).mean()
        return np.array(scores).mean()
    
    def has_sample_box(self, image_dim, subregion_dim, bbox_sample):
        cols = subregion_dim[0]
        rows = subregion_dim[1]
        region_width = image_dim[0] // cols
        region_height = image_dim[1] // rows

        filled_regions = np.zeros(subregion_dim, dtype=bool)
        for bbox in bbox_sample:
            cx = (bbox[0] + bbox[2]) // 2
            cy = (bbox[1] + bbox[3]) // 2
            region_x = cx // region_width
            region_y = cy // region_height
            filled_regions[region_x, region_y] = True
        return filled_regions

    def multiply_weight(self, bbox_gt, scores):
        """ multiply scores by weight """
        image_dim = (1920,1080)
        subregion_dim = (21, 11)
        # find if each region has a sample box 
        has_sample = self.has_sample_box(image_dim, subregion_dim, bbox_gt)
        print('has_sample: ', has_sample)

        # compute weights for each region according to the neighbors
        has_sample_pad = np.zeros((subregion_dim[0]+2, subregion_dim[1]+2), dtype=bool)
        has_sample_pad[1:-1, 1:-1] = has_sample
        weights = np.zeros(subregion_dim, dtype=np.float32)
        sum_weights = 1.0
        for c in range(subregion_dim[0]):
            for r in range(subregion_dim[1]):
                if has_sample_pad[c+1, r+1]:
                    #weights[c, r] = 1.0 / (np.sum(has_sample_pad[c:c+2, r:r+2])*10)
                    if np.sum(has_sample_pad[c:c+2, r:r+2]) < 5:
                        weights[c, r] = 20
        #sum_weights = np.sum(weights)
        weights /= sum_weights
        
        cols = subregion_dim[0]
        rows = subregion_dim[1]
        region_width = image_dim[0] // cols
        region_height = image_dim[1] // rows
        assert len(bbox_gt) == len(scores)
        weighted_scores = []
        for i, bbox in enumerate(bbox_gt):
            cx = (bbox[0] + bbox[2]) // 2
            cy = (bbox[1] + bbox[3]) // 2
            region_x = cx // region_width
            region_y = cy // region_height
            weighted_scores.append(scores[i] * weights[region_x, region_y])
        return weighted_scores 

def compute_optic_center(pt_img):
    return pt_img[0] / pt_img[2], pt_img[1] / pt_img[2]


def calculate_projected_cylinder_from_bbox_center(camera_data: CameraData, img_waist_point):
    angle_num = 20

    projected_cylinder_pt_unsorted = []

    # Convert camera image coordinates to be based on the camera's
    # optical center, instead of the left-top pt
    pt_img_waist_optical_center = (
        img_waist_point[0] - camera_data.image_width / 2,
        img_waist_point[1] - camera_data.image_height / 2,
        1
    )

    pt_world_homogeneous = np.matmul(
        camera_data.m_HomographyMat_CamOpticalCenter2WorldWaistPlane,
        pt_img_waist_optical_center
    )

    pt_world = compute_optic_center(pt_world_homogeneous)

    step = 360 / angle_num
    pi_deg = 0
    while pi_deg < 360:
        pt_world_head_circle = (
            pt_world[0] + camera_data.human_model.radius * np.cos(pi_deg * np.pi / 180),
            pt_world[1] + camera_data.human_model.radius * np.sin(pi_deg * np.pi / 180),
            camera_data.human_model.height,
            1
        )

        pt_world_feet_circle = (
            pt_world_head_circle[0],
            pt_world_head_circle[1],
            0,
            1
        )
        pt_img_head_circle_opticCenter_homogeneous = np.matmul(
            camera_data.proj_mat_world2cam, pt_world_head_circle
        )
        pt_img_feet_circle_opticCenter_homogeneous = np.matmul(
            camera_data.proj_mat_world2cam, pt_world_feet_circle
        )

        pt_img_head_circle_opticCenter = compute_optic_center(
            pt_img_head_circle_opticCenter_homogeneous
        )

        pt_img_feet_circle_opticCenter = compute_optic_center(
            pt_img_feet_circle_opticCenter_homogeneous
        )

        # Convert the camera optical center coordinate into the camera image coordinate by translation
        pt_img_feet_circle = (
            pt_img_feet_circle_opticCenter[0] + camera_data.image_width / 2,
            pt_img_feet_circle_opticCenter[1] + camera_data.image_height / 2
        )

        pt_img_head_circle = (
            pt_img_head_circle_opticCenter[0] + camera_data.image_width / 2,
            pt_img_head_circle_opticCenter[1] + camera_data.image_height / 2
        )

        projected_cylinder_pt_unsorted.append(pt_img_feet_circle)
        projected_cylinder_pt_unsorted.append(pt_img_head_circle)
        pi_deg += step

    projected_cylinder_pt_unsorted = np.array(
        projected_cylinder_pt_unsorted).reshape((-1, 2)).astype(np.float32)

    return projected_cylinder_pt_unsorted


def calculate_projected_cylinder_circles_from_bbox_center(camera_data: CameraData, img_waist_point):
    angle_num = 20

    projected_cylinder_head_circle = []
    projected_cylinder_feet_circle = []

    # Convert camera image coordinates to be based on the camera's
    # optical center, instead of the left-top pt
    pt_img_waist_optical_center = (
        img_waist_point[0] - camera_data.image_width / 2,
        img_waist_point[1] - camera_data.image_height / 2,
        1
    )
    pt_world_homogeneous = np.matmul(
        camera_data.m_HomographyMat_CamOpticalCenter2WorldWaistPlane,
        pt_img_waist_optical_center
    )

    pt_world = compute_optic_center(pt_world_homogeneous)

    step = 360 / angle_num
    pi_deg = 0
    while pi_deg < 360:
        pt_world_head_circle = (
            pt_world[0] + camera_data.human_model.radius * np.cos(pi_deg * np.pi / 180),
            pt_world[1] + camera_data.human_model.radius * np.sin(pi_deg * np.pi / 180),
            camera_data.human_model.height,
            1
        )

        pt_world_feet_circle = (
            pt_world_head_circle[0],
            pt_world_head_circle[1],
            0,
            1
        )
        pt_img_head_circle_opticCenter_homogeneous = np.matmul(
            camera_data.proj_mat_world2cam, pt_world_head_circle
        )
        pt_img_feet_circle_opticCenter_homogeneous = np.matmul(
            camera_data.proj_mat_world2cam, pt_world_feet_circle
        )

        pt_img_head_circle_opticCenter = compute_optic_center(
            pt_img_head_circle_opticCenter_homogeneous
        )

        pt_img_feet_circle_opticCenter = compute_optic_center(
            pt_img_feet_circle_opticCenter_homogeneous
        )

        # Convert the camera optical center coordinate into the camera image coordinate by translation
        pt_img_feet_circle = (
            pt_img_feet_circle_opticCenter[0] + camera_data.image_width / 2,
            pt_img_feet_circle_opticCenter[1] + camera_data.image_height / 2
        )

        pt_img_head_circle = (
            pt_img_head_circle_opticCenter[0] + camera_data.image_width / 2,
            pt_img_head_circle_opticCenter[1] + camera_data.image_height / 2
        )

        projected_cylinder_feet_circle.append(pt_img_feet_circle)
        projected_cylinder_head_circle.append(pt_img_head_circle)
        pi_deg += step

    return projected_cylinder_head_circle, projected_cylinder_feet_circle

def compute_bbox_from_waist_point(camera_data: CameraData, point):
    point_list = calculate_projected_cylinder_from_bbox_center(camera_data, point)
    return cv2.boundingRect(point_list)


def make_rot_matrix_from_angles(angles):
    """Create rotation matrix from angles
    
    Rotation order: Z->Y->X
    Extrinsic rotation order 

    Args:
        angles (tuple): (rx, ry, rz)
    
    Returns:
        np.array: 3x3 rotation matrix
    """
    yaw, roll, pitch = angles # names ar wrong
    x = -(pitch + 90) * np.pi / 180 # x axis in camera cooridnate system
    y = (roll + 90) * np.pi / 180 # 
    z = -yaw * np.pi / 180 # roll???
    cos = np.cos
    sin = np.sin
    matrix_rotation_x = np.array([
        [1, 0, 0],
        [0, cos(x), -sin(x)],
        [0, sin(x), cos(x)]
    ])
    matrix_rotation_y = np.array([
        [cos(y), 0, sin(y)],
        [0, 1, 0],
        [-sin(y), 0, cos(y)]
    ])
    matrix_rotation_z = np.array([
        [cos(z), -sin(z), 0],
        [sin(z), cos(z), 0],
        [0, 0, 1]
    ])
    res1 = np.matmul(matrix_rotation_y, matrix_rotation_z)
    res = np.matmul(res1, matrix_rotation_x)
    return res

# experiment
def make_rot_matrix_from_angles3(angles):
    """Create rotation matrix from angles
    
    Rotation order: Z->Y->X
    Extrinsic rotation order 

    Args:
        angles (tuple): (rx, ry, rz)
    
    Returns:
        np.array: 3x3 rotation matrix
    """
    yaw, roll, pitch = angles # names ar wrong
    x = (pitch + 90) * np.pi / 180 # x axis in camera cooridnate system
    y = (-roll - 90) * np.pi / 180 # 
    z = -yaw * np.pi / 180 # roll???
    cos = np.cos
    sin = np.sin
    matrix_rotation_x = np.array([
        [1, 0, 0],
        [0, cos(x), -sin(x)],
        [0, sin(x), cos(x)]
    ])
    matrix_rotation_y = np.array([
        [cos(y), 0, sin(y)],
        [0, 1, 0],
        [-sin(y), 0, cos(y)]
    ])
    matrix_rotation_z = np.array([
        [cos(z), -sin(z), 0],
        [sin(z), cos(z), 0],
        [0, 0, 1]
    ])
    res1 = np.matmul(matrix_rotation_y, matrix_rotation_z)
    res = np.matmul(res1, matrix_rotation_x)
    return res

def make_rot_matrix_from_angles2(angles):
    """Create rotation matrix from angles
    
    Rotation order: X->Y->Z

    Args:
        angles (tuple): (rx, ry, rz)
    
    Returns:
        np.array: 3x3 rotation matrix
    """
    rx, ry, rz= angles # names ar wrong
    x = (0) * np.pi / 180 # roll. x axis in camera cooridnate system
    y = (ry + 90) * np.pi / 180 # pitch
    z = (rz + 90) * np.pi / 180 # roll???

    cos = np.cos
    sin = np.sin
    matrix_rotation_x = np.array([
        [1, 0, 0],
        [0, cos(x), -sin(x)],
        [0, sin(x), cos(x)]
    ])
    matrix_rotation_y = np.array([
        [cos(y), 0, sin(y)],
        [0, 1, 0],
        [-sin(y), 0, cos(y)]
    ])
    matrix_rotation_z = np.array([
        [cos(z), -sin(z), 0],
        [sin(z), cos(z), 0],
        [0, 0, 1]
    ])
    res1 = np.matmul(matrix_rotation_y, matrix_rotation_x)
    res = np.matmul(res1, matrix_rotation_z)
    return res


def make_translation_matrix(coord):
    x, y, z = coord
    res = np.array([-z, x, y])
    return res


def make_rotation_translation_matrix(rotation, translation):
    translation = np.array(translation)
    res = np.array([
        [0.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0]
    ])
    matrix_rotation = make_rot_matrix_from_angles(rotation) # experiment
    res[:3, :3] = matrix_rotation
    matT = make_translation_matrix(translation)
    side = np.matmul(-matrix_rotation, matT)
    res[0:3, 3] = side
    return res


def get_center_from_bbox(bbox):
    xmin, ymin, xmax, ymax = bbox
    x_center = xmin + (xmax - xmin) // 2
    y_center = ymin + (ymax - ymin) // 2
    return x_center, y_center


def get_new_projected_bbox(bbox_gt, fun_get_bbox_from_point):
    center_list = [get_center_from_bbox(elm) for elm in bbox_gt]
    computed_bbox = [fun_get_bbox_from_point(elm) for elm in center_list]
    changed_bbox = [(elm[0], elm[1], elm[0] + elm[2], elm[1] + elm[3]) for elm in computed_bbox]
    return changed_bbox


def compute_2bbox_list(bbox_list1, bbox_list2, function_bbox):
    return [
        function_bbox(bbox_list1[i], bbox_list2[i])
        for i in range(len(bbox_list1))
    ]


def generate_all_bbox(bbox_gt, fun_get_bbox_from_point):
    proj_bbox = get_new_projected_bbox(bbox_gt, fun_get_bbox_from_point)
    if proj_bbox is None:
        return None
    if None in proj_bbox:
        proj_bbox = [elm for elm in proj_bbox if elm]
    return proj_bbox


def overlap_1d(x1min, x1max, x2min, x2max):
    """ Return the overlap distance between 2 segments. """
    if x1min > x2min:
        x1min, x2min = x2min, x1min
        x1max, x2max = x2max, x1max
    return 0 if x1max < x2min else min(x1max, x2max) - x2min


def compute_iou(bbox1, bbox2):
    """ Compute the result of the intersection over union of 2 boxes. """
    overlap_x = overlap_1d(
        bbox1[0], bbox1[2], bbox2[0], bbox2[2],
    )
    overlap_y = overlap_1d(
        bbox1[1], bbox1[3], bbox2[1], bbox2[3],
    )
    b1w = bbox1[2] - bbox1[0]
    b1h = bbox1[3] - bbox1[1]
    b2w = bbox2[2] - bbox2[0]
    b2h = bbox2[3] - bbox2[1]
    area_1 = b1w * b1h
    area_2 = b2w * b2h
    intersection = overlap_x * overlap_y
    union = area_1 + area_2 - intersection
    return 0 if union == 0 else intersection / union
