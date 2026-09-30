# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import cv2
import numpy as np
from math import radians, cos, sin, asin, sqrt
import logging

logger = logging.getLogger(__name__)

def get_estimated_mappolygon(image_polygon, transformed_polygon):
    # Assuming points a and points c are in the format
    # [(x1, y1), (x2, y2), ...]
    # Convert points a and c to numpy arrays
    points_a = np.array([(x, y) for x, y in image_polygon], dtype=np.float32)
    points_c = np.array([(x, y) for x, y in transformed_polygon], dtype=np.float32)

    # Find the homography matrix H using points a and points c
    H, _ = cv2.findHomography(points_a, points_c, cv2.RANSAC)

    # Use the inverse of the homography matrix to transform points c back
    # to points b
    H_inv = np.linalg.inv(H)
    points_b_homogeneous = np.dot(
        H_inv, np.vstack((points_c.T, np.ones(points_c.shape[0])))
    )
    points_b_homogeneous = (
        points_b_homogeneous / points_b_homogeneous[2]
    )  # Homogeneous coordinates normalization
    points_b = points_b_homogeneous[:2].T  # Convert back to (x, y) format

    return points_b

def GetXYMatrix(json_latlng_coords):
    """Convert a lat/lng dictionary object to an XY numpy array

    Args:
        json_latlng_coords (list): List of lat/lng coordinate dictionaries

    Returns:
        np.array: mx2 array of coordinates for each [lng,lat] point
    """
    num_points = len(json_latlng_coords)
    array_xy_coords = np.zeros((num_points, 2))

    for ii in range(num_points):
        # recall x is lng and y is lat
        array_xy_coords[ii, :] = np.array(
            [json_latlng_coords[ii]["lng"], json_latlng_coords[ii]["lat"]]
        )

    return array_xy_coords

def ConvertToHomogeneous(xy_vec):
    """Convert a point to homogeneous form

    Args:
        xy_vec (np.array): in the form of [x,y]

    Returns:
        np.array: in the form of [x,y,1]
    """
    if len(xy_vec.shape) == 1:
        return np.append(xy_vec, np.array([1]))

    num_points = len(xy_vec[:, 0])
    return np.append(xy_vec, np.ones((num_points, 1)), axis=1)


def ReprojectionError(xy_src, xy_dst, H, calibrationType, method="haversine"):
    """Calculate the reprojection error based on the selected distance type

    Args:
        xy_src (np.array): source points to be converted
        xy_dst (np.array): target points to use as ground truth
        H (np.array): calculated homography matrix
        method (str): distance calculation method (default="haversine")

    Returns:
        np.array: array of reprojection errors for each point
    """

    if calibrationType == "cartesian":
        method = "euclidean"
    num_points = len(xy_src[:, 0])
    error_mat = np.zeros((num_points, 1))

    if len(xy_src[0, :]) == 2:
        xy_src = ConvertToHomogeneous(xy_src)

    for ii in range(num_points):
        estimate = H @ xy_src[ii, :]
        estimate = (estimate / estimate[2])[:2]

        if method == "haversine":
            error_mat[ii] = (
                Haversine(
                    float(xy_dst[ii, 0]),
                    float(xy_dst[ii, 1]),
                    float(estimate[0]),
                    float(estimate[1]),
                )
                * 1000
            )
        elif method == "euclidean":
            error_mat[ii] = np.linalg.norm(xy_dst[ii, :] - estimate) * 0.01
            logger.debug(f"error_mat: {error_mat[ii]}")
        else:
            raise ValueError(
                "{} is not a valid method for reprojection error.".format(method)
            )

    return error_mat



def Haversine(lon1, lat1, lon2, lat2):
    """Calculate the great circle distance between two points on the earth

    Args:
        lon1 (float): longitude value of point 1
        lat1 (float): latitude value of point 1
        lon2 (float): longitude value of point 2
        lat2 (float): latitidue value of point 2

    Returns:
        float: distance between the two points
    """
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    r = 6371  # Radius of earth in kilometers
    return c * r

