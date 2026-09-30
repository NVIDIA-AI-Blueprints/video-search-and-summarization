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
from calibration.server.projects.models import Sensor
from calibration.server.server import settings
from calibration.server.projects.utils import ensure_dir
from PIL import Image
import numpy as np
import ast
import time
import logging

logger = logging.getLogger(__name__)

def createInvertedImage(url):
    """ Get inverted Image to use for cartesian validation

    Args:
        url (str): url with the id of the sensor to create the inverted image for
    """

    # set up sensor
    sensorId = os.path.normpath(url).split(os.path.sep)[-1]
    sensor = Sensor.objects.get(id=sensorId)
    sensorId = sensor.sensorId
    homography = sensor.homography
    logging.debug("Homography: %s", str(homography))
    invImWidth = sensor.invertImWidth
    invImHeight = sensor.invertImHeight
    projectId = sensor.project.id


    # set up image
    imgName = '{}.png'.format(sensorId)
    imageUrl = sensor.imageUrl
    imgPath = settings.MEDIA_ROOT + imgName


    # Set up inverted image
    invImgName = '{}_warped.png'.format(sensorId)
    invImageUrl = sensor.invertImageUrl
    invImgDir = settings.MEDIA_ROOT + "/{}/inverted/".format(projectId)
    ensure_dir(invImgDir)
    # if (os.path.exists)
    invImgPath = settings.MEDIA_ROOT + "/{}/inverted/{}".format(projectId,invImgName)

    # Invert Image

    invImWidth, invImHeight, outputPIL = getInvertedImage(imageUrl, homography, invImWidth, invImHeight)
    buffer = BytesIO()
    outputPIL.save(fp=buffer, format='PNG')
    frameSave = ContentFile(buffer.getvalue())

    # Save image
    invImageUrl.save(invImgName, InMemoryUploadedFile(
        frameSave,
        None,
        invImgName,
        'image/png',
        frameSave.tell,
        None
    ))

    sensor.invertImHeight = invImHeight
    sensor.invertImWidth = invImWidth
    sensor.save()
    time.sleep(0.1)

def getInvertedImage(imageUrl, homography, invImWidth, invImHeight):
    """ Get the frame from the rtspURL

    Args:
        rtspURL (str): rtsp url to call to capture a screenshot from
    """
    pilImage = Image.open(imageUrl)
    cv2Image = np.array(pilImage)
    if invImWidth == 0 or invImHeight == 0:
        invImWidth = int(cv2Image.shape[0])
        invImHeight = int(cv2Image.shape[1])

    M = np.array(ast.literal_eval(homography))

    # print (invImWidth,invImHeight)
    logging.debug("invImWidth: %s, invImHeight: %s", invImWidth, invImHeight)
    invImg = cv2.warpPerspective(cv2Image, M, (int(invImWidth), int(invImHeight)))
    warped_flip = np.flip(invImg, axis=0).copy()

    outputPIL = Image.fromarray(warped_flip)

    return invImWidth, invImHeight, outputPIL


