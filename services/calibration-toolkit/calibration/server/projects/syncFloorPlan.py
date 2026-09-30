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

logger = logging.getLogger(__name__)

def syncFloorPlanFiles(url):
    """ Get inverted Image to use for cartesian validation

    Args:
        url (str): url with the id of the sensor to create the inverted image for
    """

    # set up sensor
    projectId = os.path.normpath(url).split(os.path.sep)[-1]
    project = Project.objects.get(id=projectId)
    logging.debug(print("projectId   ", (projectId, project, project.sensor_set.all())))
    floorPlanImageUrl = project.floorPlanImageUrl
    logging.debug("floorplanimgurl %s", floorPlanImageUrl)
    floorPlanImHeight = project.floorPlanImHeight
    floorPlanImWidth = project.floorPlanImWidth
    sensors = project.sensor_set.all()
    fpImgDir = settings.MEDIA_ROOT + f"/{projectId}/floorplan/"
    scaleFactor = project.scaleFactor
    # logging.debug(sensors)
    for s in sensors:
        # logging.debug(s)
        s.floorPlanImWidth = floorPlanImWidth
        s.floorPlanImHeight = floorPlanImHeight
        s.scaleFactor = scaleFactor
        fpImageName = '{}_fp.png'.format(s.sensorId)
        # logging.debug(s.sensorId, s.floorPlanImageUrl)
        fpImageFile = os.path.join(fpImgDir, fpImageName)

        if os.path.exists(fpImageFile):
            logging.debug("Remove Sensor {} Floorplan".format(s.sensorId))
            os.remove(fpImageFile)
            s.floorPlanImageUrl.delete()
        pilImage = Image.open(floorPlanImageUrl)
        buffer = BytesIO()
        pilImage.save(fp=buffer, format='PNG')
        frameSave = ContentFile(buffer.getvalue())

        # # Save image
        s.floorPlanImageUrl.save(fpImageName, InMemoryUploadedFile(
            frameSave,
            None,
            fpImageName,
            'image/png',
            frameSave.tell,
            None
        ))
        s.save()
        # sensor = Sensor.objects.get(id=s)
        # id = sensor.sensorId

        # sensorId = sensor.sensorId
        # homography = sensor.imHomography
        # # logging.debug(homography)
        # invImWidth = sensor.invertImWidth
        # invImHeight = sensor.invertImHeight



        # # set up image
        # imgName = '{}.png'.format(sensorId)
        # imageUrl = sensor.imageUrl
        # imgPath = settings.MEDIA_ROOT + imgName


        # # Set up inverted image
        # invImgName = '{}.png'.format(sensorId)
        # invImageUrl = sensor.invertImageUrl
        # invImgDir = settings.MEDIA_ROOT + "/floorplan/"
        # ensure_dir(invImgDir)
        # # if (os.path.exists)
        # invImgPath = settings.MEDIA_ROOT + "/inverted/" + invImgName

        # # Invert Image

        # invImWidth, invImHeight, outputPIL = getInvertedImage(imageUrl, homography, invImWidth, invImHeight)
        # buffer = BytesIO()
        # outputPIL.save(fp=buffer, format='PNG')
        # frameSave = ContentFile(buffer.getvalue())

        # # Save image
        # invImageUrl.save(invImgName, InMemoryUploadedFile(
        #     frameSave,
        #     None,
        #     invImgName,
        #     'image/png',
        #     frameSave.tell,
        #     None
        # ))

        # sensor.invertImHeight = invImHeight
        # sensor.invertImWidth = invImWidth
        # sensor.save()
        time.sleep(0.1)
        logging.debug(f"Sensor {s.sensorId} Floorplan updated")
    logging.debug(f"Floorplan updated for all sensors")

# def getInvertedImage(imageUrl, homography, invImWidth, invImHeight):
#     """ Get the frame from the rtspURL

#     Args:
#         rtspURL (str): rtsp url to call to capture a screenshot from
#     """
#     pilImage = Image.open(imageUrl)
#     cv2Image = np.array(pilImage)
#     if invImWidth == 0 or invImHeight == 0:
#         invImWidth = int(cv2Image.shape[0])
#         invImHeight = int(cv2Image.shape[1])

#     M = np.array(ast.literal_eval(homography))

#     # logging.debug(invImWidth,invImHeight)
#     invImg = cv2.warpPerspective(cv2Image, M, (int(invImWidth), int(invImHeight)))
#     outputPIL = Image.fromarray(invImg)
#     return invImWidth, invImHeight, outputPIL




# def ensure_dir(file_path):
#     directory = os.path.dirname(file_path)
#     if not os.path.exists(directory):
#         os.makedirs(directory)