# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import cv2 as cv
from PIL import Image
from io import BytesIO
import os
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.base import ContentFile
from calibration.server.projects.models import Sensor
from calibration.server.server import settings
from urllib.parse import quote, urlparse
import base64
import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
def getRtspScreenshot(url):
    """ Get screenshot from rtsp steam to use for sensor calibration

    Args:
        url (str): url with the id of the sensor to get the screenshot for
    """
    sensorId = os.path.normpath(url).split(os.path.sep)[-1]
    sensor = Sensor.objects.get(id=sensorId)
    sensorId = sensor.sensorId
    deviceId = sensor.deviceId
    mmsUrl = sensor.getMMS()
    imgName = '{}.png'.format(sensorId)
    imageUrl = sensor.imageUrl
    imgPath = settings.MEDIA_ROOT + imgName

    framePIL, frW, frH = getFrameFromStream(sensor.rtspURL, mmsUrl, deviceId)
    buffer = BytesIO()
    framePIL.save(fp=buffer, format='PNG')
    frameSave = ContentFile(buffer.getvalue())

    imageUrl.save(imgName, InMemoryUploadedFile(
        frameSave,
        None,
        imgName,
        'image/png',
        frameSave.tell,
        None
    ))
    sensor.width = frW
    sensor.height = frH
    sensor.sensorPolygon = '[]'
    sensor.save()


def getFrameFromStream(rtspURL, mmsUrl, deviceId, mmsType):
    """ Get the frame from the rtspURL

    Args:
        rtspURL (str): rtsp url to call to capture a screenshot from
    """
    # safe_rtspURL = fixURL(rtspURL)
    try:
        imageURL = f"{mmsUrl}api/v1/{mmsType}/stream/{deviceId}/picture" if mmsUrl.endswith("/") else f"{mmsUrl}/api/v1/{mmsType}/stream/{deviceId}/picture"

        if mmsType == "replay":
            current_time = datetime.now(timezone.utc)
            formatted_time = current_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')
            formatted_time = formatted_time.replace(':', '%3A')
            imageURL += f"?startTime={formatted_time}"
        else:
            imageURL += "/"

        logging.debug(imageURL)
        imagefile = requests.get(imageURL)
        stream = BytesIO(imagefile.content)
        framePIL = Image.open(stream)
        frW, frH = framePIL.size
        logging.debug("MMS image capture succeeds")
        return framePIL, frW, frH
    except Exception as e:
        logging.error(e)
        logging.error("MMS image capture failed")

    try:
        cap = cv.VideoCapture(rtspURL)
        _, frame = cap.read()
        cap.release()
        framePIL = Image.fromarray(cv.cvtColor(frame, cv.COLOR_BGR2RGB))
        logging.debug("opencv succeeds")
        frW, frH = framePIL.size
        return framePIL, frW, frH
    except Exception as e:
        logging.error(e)
        logging.error("OpenCV failed to capture")

    # try:
    #     imagefile = requests.get(mmsUrl + "/api/device/" + deviceId + "/picture/")
    #     stream = BytesIO(imagefile.content)
    #     framePIL = Image.open(stream)
    #     logging.debug("MMS succeeds")
    #     return framePIL
    # except Exception as e:
    #     logging.debug(e)
    #     logging.debug("MMS image failed")



def fixURL(rtspURL):
    """ Fix rtsp url with special characters

    Args:
        rtspURL (str): rtsp url to encode special characters
    """
    fixedUrl = replace_password(rtspURL)
    return fixedUrl


def replace_password(url):
    """ Fix url password with special characters
    Args:
        url (str): url to encode
    """

    parts = urlparse(url)
    if parts.password is not None:
        # split out the host portion manually. We could use
        # parts.hostname and parts.port, but then you'd have to check
        # if either part is None. The hostname would also be lowercased.
        host_info = parts.netloc.rpartition('@')[-1]
        usrPass = "{}:{}".format(parts.username,quote(parts.password)).encode("utf-8")
        # b64Val = base64.b64encode(usrPass)
        parts = parts._replace(netloc='{}@{}'.format(
            usrPass, host_info))

        url = parts.geturl()
    return url