# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import cv2 as cv
import numpy as np
import json
import os
import ast

import logging
import time
from calibration.server.projects.models import Sensor
from calibration.server.projects.homographyUtils import ReprojectionError, GetXYMatrix

logger = logging.getLogger(__name__)

def getApproxHomographyMatrix(url):
    """Estimate the homography matrix of a particular sensor

    Args:
         url (str): The string url called by the frontend to indicate sensor Id
    """
    sensorId = os.path.normpath(url).split(os.path.sep)[-1]
    sensor = Sensor.objects.get(id=sensorId)

    sensor.homography, sensor.imHomography = getApproxHomography(sensor)
    sensor.save()
    time.sleep(0.1)


def getApproxHomography(sensor):
    try:    
        invImXPad = sensor.invertImXPad
        invImYPad = sensor.invertImYPad
        invImHeight = sensor.invertImHeight

        sensorPolygon = json.loads(sensor.sensorPolygon)[0]["points"]
        calibrationType = sensor.calibrationType
        #logging.debug("calibrationType", calibrationType)

        edgeLengths = json.loads(sensor.edgeLengths)
        #logging.debug("edgeLengths", edgeLengths)

        sensorArray = GetXYMatrix(sensorPolygon)
        edgeArray = GetXYMatrixEdges(edgeLengths)
        #logging.debug("edgeArray", edgeArray)
        globalArray = edgeArray + np.array([invImXPad, invImYPad], dtype=np.float32)

        H, _ = cv.findHomography(
            sensorArray, globalArray, method=cv.RANSAC, ransacReprojThreshold=3
        )
        iH, _ = cv.findHomography(
            globalArray, sensorArray, method=cv.RANSAC, ransacReprojThreshold=3
        )

        reproj_error = ReprojectionError(sensorArray, globalArray, H, calibrationType)
        logger.debug("reproj_error:")
        logger.debug(reproj_error)

    except Exception as e:
        logging.error("Error in getApproxHomography: ", e)
        return None, None
    
    # #Uncomment to print statistics of calibration
    # logging.debug('Homography', H)
    # logging.debug('Num points: ', len(sensorArray[:, 0]))
    # logging.debug('Max: ', np.amax(reproj_error))
    # logging.debug('Sum: ', np.sum(reproj_error))
    # logging.debug('Avg: ', np.average(reproj_error))

    H = H.tolist()
    iH = iH.tolist()
    homography = str(H)
    imHomography = str(iH)
    return homography, imHomography


def GetXYMatrixEdges(edges):
    """Convert a lat/lng dictionary object to an XY numpy array

    Args:
        json_latlng_coords (list): List of lat/lng coordinate dictionaries

    Returns:
        np.array: mx2 array of coordinates for each [lng,lat] point
    """
    num_points = len(edges)
    array_xy_coords = np.zeros((num_points, 2))

    for ii in range(num_points):
        # recall x is lng and y is lat
        array_xy_coords[ii, :] = np.array(
            [float(edges[ii]["lng"]), float(edges[ii]["lat"])]
        )

    return array_xy_coords

def flipY(arr, invHeight):
    """Flip the Y value in an array of points based on image height

    Args:
        arr (array): list of points
        invHeight (int): height of image

    Returns:
        array: list of points with flipped y
    """
    flipped_arr = []
    for pt in arr:
        flipped = (pt[0], invHeight - pt[1])
        flipped_arr.append(flipped)
    return np.array(flipped_arr, dtype=np.float32)


def get_original_from_projected(H, transformed_points):
    """Do a reverse transformation of projected points using the homography to get the original points drawn.

    Args:
        h (matrix): Homography Matrix
        transformed_points (arr): array of transformed points

    Returns:
        array: list of original points
    """
    # Assuming you have points C and points_c_transformed
    # H is the original homography matrix mapping points C to points_c_transformed

    # Calculate the inverse of the homography matrix
    transformed_points_matrix = GetXYMatrix(transformed_points)
    npH = np.array(ast.literal_eval(H))
    H_inverse = np.linalg.inv(npH)
    # Transform points_c_transformed back to the original points C
    points_original_homogeneous = cv.perspectiveTransform(np.array([transformed_points_matrix], dtype=np.float32), H_inverse)
    points_original = points_original_homogeneous[0].tolist()  # Extract the original points C
    # points_c_original now contains the estimated original points C corresponding to points_c_transformed
    points_original_list = [{"x": point[0], "y": point[1]} for point in points_original]

    return points_original_list
