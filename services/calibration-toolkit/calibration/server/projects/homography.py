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

import time
from calibration.server.projects.models import Sensor
from calibration.server.projects.homographyUtils import GetXYMatrix, ReprojectionError

import logging


logger = logging.getLogger(__name__)

def getHomographyMatrix(url):
    """Estimate the homography matrix of a particular sensor

    Args:
         url (str): The string url called by the frontend to indicate sensor Id
    """
    # logging.debug(url)
    sensorId = os.path.normpath(url).split(os.path.sep)[-1]
    sensor = Sensor.objects.get(id=sensorId)
    sensor.homography = getHomography(sensor.sensorPolygon, sensor.gisPolygon, sensor.calibrationType)
    sensor.save()
    time.sleep(0.1)


def getHomography(sp, gp, calibrationType):
    """Get the homography matrix of a particular sensor based on the two drawn polygons

    Args:
         sp (json): The drawn polygon on the camera image
         gp (json): The drawn polygon on the GIS map
    Return:
        H (str): Homography Matrix as a string
    """
    sensorPolygon = json.loads(sp)[0]["points"]
    gisPolygon = json.loads(gp)[0]["points"]

    sensorArray = GetXYMatrix(sensorPolygon)
    gisArray = GetXYMatrix(gisPolygon)

    H, _ = cv.findHomography(
        sensorArray, gisArray, method=cv.RANSAC, ransacReprojThreshold=3
    )

    reproj_error = ReprojectionError(sensorArray, gisArray, H, calibrationType)

    # #Uncomment to print statistics of calibration
    # logging.debug('Homography', H)
    # logging.debug('Num points: ', len(sensorArray[:, 0]))
    # logging.debug('Max: ', np.amax(reproj_error))
    # logging.debug('Sum: ', np.sum(reproj_error))
    # logging.debug('Avg: ', np.average(reproj_error))

    H = H.tolist()
    return str(H)

