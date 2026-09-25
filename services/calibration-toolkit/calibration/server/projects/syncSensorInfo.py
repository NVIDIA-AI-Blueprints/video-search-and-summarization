# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import cv2

from io import BytesIO
import os
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.base import ContentFile
from calibration.server.projects.models import Sensor, Project
from calibration.server.server import settings
from PIL import Image
import numpy as np
import ast
import time
import logging
import requests
logger = logging.getLogger(__name__)


def push_data(mmsInfo_host, deviceId, sensorId):
    data = {"name": str(sensorId)}
    logger.debug("updating %s ", data)
    # logger.debug("this is the data", mmsInfo_host, deviceId, sensorId)

    mmsURL = mmsInfo_host if mmsInfo_host.endswith("/") else mmsInfo_host + "/"
    logger.debug(f"MMS Sensor URL: {mmsURL}api/v1/sensor/{deviceId}/info")

    try:
        response = requests.post(f"{mmsURL}api/v1/sensor/{deviceId}/info", json=data)
    except Exception as e:
        logger.error("Error in Updating Sensor Name in MMS")

def syncSensorInfo(url):
    """ Sync Sensor Info with VMS

    Args:
        url (str): url with the id of the sensor to create the inverted image for
    """

    # set up sensor
    id = os.path.normpath(url).split(os.path.sep)[-1]
    sensor = Sensor.objects.get(id=id)
    mmsURL = sensor.project.mmsURL
    push_data(mmsURL, sensor.deviceId, sensor.sensorId)
