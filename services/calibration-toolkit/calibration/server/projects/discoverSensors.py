# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import json
import ntpath
import shutil
import requests
import re
from urllib.parse import quote
from io import BytesIO
# import uuid

from calibration.server.server.settings import DATA_DIR, BASE_DIR, MEDIA_ROOT
from calibration.server.projects.models import Project,Sensor
from calibration.server.projects.rtspScreenshot import getFrameFromStream
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.base import ContentFile
from django.http import HttpResponse
from calibration.server.projects.syncFloorPlan import syncFloorPlanFiles
import logging

logger = logging.getLogger(__name__)

def getSensors(url):
    """ Imports the Sensors from the Metropolis Media Server

    Args:
        url (str): url with the API to MMS
    """
    try:
        projectId = os.path.normpath(url).split(os.path.sep)[-1]
        project = Project.objects.get(id=projectId)
        mmsURL = project.mmsURL
        logger.debug("Sensor import URL: {}".format(mmsURL))
        mmsType = determineMediaSourceType(mmsURL)

        getSensorsUrl = f"{mmsURL}api/v1/{mmsType}/streams" if mmsURL.endswith("/") else f"{mmsURL}/api/v1/{mmsType}/streams"
        logger.debug(getSensorsUrl)

        try:
            response = requests.get(getSensorsUrl, timeout=10)  # Add timeout
            logger.debug("response.status_code: {}".format(response.status_code))
            
            if response.status_code == 401:
                logger.error("Unauthorized access to MMS API")
                return HttpResponse('Unauthorized access to MMS API. Please check credentials.', status=401)
            elif response.status_code == 404:
                logger.error("MMS API endpoint not found: {}".format(getSensorsUrl))
                return HttpResponse('MMS API endpoint not found.', status=404)
            elif response.status_code == 403:
                logger.error("Forbidden access to MMS API")
                return HttpResponse('Access forbidden to MMS API endpoint.', status=403)
            elif response.status_code != 200:
                logger.error("Failed to get sensors. Status code: {}".format(response.status_code))
                return HttpResponse('Failed to get sensors from MMS ', status=500)

            response_dict = response.json()
            logger.debug("response_dict: %s", response_dict)

            if not response_dict:
                logger.warning("No sensors found in MMS response")
                return HttpResponse('No sensors found in MMS', status=200)

            duplicate_sensor = {}
            bad_sensors = []
            sensors_created = 0
            sensors_failed = 0

            for item in response_dict:
                try:
                    sensor_deviceId = None
                    sensor_sensorId = None
                    for sensor, val in item.items():
                        if val:
                            sensor_deviceId = val[0]['streamId']
                            sensor_sensorId = val[0]['name']

                    if not (sensor_sensorId and sensor_deviceId):
                        logger.warning("Missing sensor ID or device ID in response")
                        continue

                    if (Sensor.objects.filter(sensorId=sensor_sensorId, project=project).exists()):
                        if (Sensor.objects.filter(sensorId=sensor_sensorId, deviceId=sensor_deviceId, project=project).exists()):
                            logger.debug("Sensor exists")
                        else:
                            logger.debug("Sensor ID exists, but device ID's are different")
                            duplicate_sensor[sensor_sensorId] = sensor_deviceId
                    else:
                        logger.debug("Processing new sensor: {}".format(sensor_sensorId))
                        result, item = validateSensor(val[0])
                        
                        if not result:
                            logger.warning("Invalid sensor data for sensor: {}".format(sensor_sensorId))
                            bad_sensors.append(sensor_sensorId)
                            continue

                        getSensorDetailsURL = f"{mmsURL}api/v1/sensor/{sensor_deviceId}/info" if mmsURL.endswith("/") else f"{mmsURL}/api/v1/sensor/{sensor_deviceId}/info"
                        logger.debug(getSensorDetailsURL)
                        
                        try:
                            sensor_response = requests.get(getSensorDetailsURL, timeout=5)
                            if sensor_response.status_code != 200:
                                logger.error("Failed to get sensor details for sensor: {}".format(sensor_deviceId))
                                sensors_failed += 1
                                continue
                                
                            sensor_response_dict = sensor_response.json()
                            if not sensor_response_dict:
                                logger.warning("No sensor details found for sensor: {}".format(sensor_deviceId))
                                continue

                            originLat = sensor_response_dict.get('position', {}).get('geoLocation', {}).get('latitude', 0.0)
                            originLng = sensor_response_dict.get('position', {}).get('geoLocation', {}).get('longitude', 0.0)

                            newSensor = Sensor(
                                sensorId=val[0]['name'],
                                rtspURL=val[0]['url'],
                                deviceId=val[0]['streamId'],
                                mmsInfo_protocol="webrtc",
                                mmsInfo_host=mmsURL,
                                mmsInfo_type="vst",
                                project=project,
                                sensorPolygon='[]',
                                depth=sensor_response_dict.get('position', {}).get('depth', 0),
                                fieldOfView=sensor_response_dict.get('position', {}).get('fieldOfView', 0),
                                direction=sensor_response_dict.get('position', {}).get('direction', 0),
                                originLat=project.originLat,
                                originLng=project.originLng,
                                calibrationType=project.calibrationType
                            )

                            # Handle image capture and saving
                            imgName = '{}.png'.format(sensor_sensorId)
                            try:
                                framePIL, newSensor.width, newSensor.height = getFrameFromStream(
                                    val[0]['url'], mmsURL, sensor_deviceId, mmsType)
                                buffer = BytesIO()
                                framePIL.save(fp=buffer, format='PNG')
                                frameSave = ContentFile(buffer.getvalue())
                                newSensor.imageUrl.save(imgName, InMemoryUploadedFile(
                                    frameSave,
                                    None,
                                    imgName,
                                    'image/png',
                                    frameSave.tell,
                                    None
                                ))
                            except Exception as e:
                                logger.error("Error capturing image for sensor {}: {}".format(sensor_sensorId, str(e)))
                                sensors_failed += 1
                                continue

                            try:
                                newSensor.save()
                                sensors_created += 1
                                logger.debug("Sensor {} created successfully".format(sensor_sensorId))
                            except Exception as e:
                                logger.error("Error saving sensor {}: {}".format(sensor_sensorId, str(e)))
                                sensors_failed += 1
                                continue

                        except requests.exceptions.RequestException as e:
                            logger.error("Network error while getting sensor details: {}".format(str(e)))
                            sensors_failed += 1
                            continue

                except Exception as e:
                    logger.error("Error processing sensor: {}".format(str(e)))
                    sensors_failed += 1
                    continue

            # Handle floor plan sync for mtmc calibration
            if project.calibrationType == "mtmc":
                if project.floorPlanImageUrl:
                    logger.debug("Syncing Floorplan Images")
                    syncFloorPlanFiles(url)
                else:
                    logger.warning("No Floorplan available for project")
                    return HttpResponse('Sensors Imported but Missing Floorplan', status=200)

            # Prepare response message
            response_message = f'Sensors Imported: {sensors_created} created, {sensors_failed} failed'
            if duplicate_sensor:
                response_message += f', {len(duplicate_sensor)} duplicates found'
            if bad_sensors:
                response_message += f', {len(bad_sensors)} invalid sensors'

            return HttpResponse(response_message, status=200)

        except requests.exceptions.RequestException as e:
            logger.error("Network error while accessing MMS: {}".format(str(e)))
            return HttpResponse('Network error while accessing MMS. Please check the connection.', status=503)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON response from MMS: {}".format(str(e)))
            return HttpResponse('Invalid response from MMS. Please check the API format.', status=500)

    except Project.DoesNotExist:
        logger.error("Project not found: {}".format(projectId))
        return HttpResponse('Project not found', status=404)
    except Exception as e:
        logger.error("Unexpected error in getSensors: {}".format(str(e)))
        return HttpResponse('An unexpected error occurred while importing sensors', status=500)

def validateSensor(item):
    """Validate Sensor

    Args:
        item (object): Sensor object

    Returns:
        result: regex match
        item: Sensor object
    """
    logger.debug(item)
    id_pattern = '^[a-zA-Z0-9 \\-]*$'
    live_url_pattern = 'rtsp:(.*)'
    # name_pattern = "[a-zA-Z0-9!@#$&()_\-`.+,/\" ]*"
    for key,val in item.items():
        if key == 'streamId':
            result = re.match(id_pattern, val)
            if (result == False):
                logger.debug("id is bad")
        elif key == 'url':
            result = re.match(live_url_pattern, val)
            if (result == False):
                logger.debug("live url is bad")

    if result:
        logger.debug("Item valid")
        return result, item
    else:
        logger.debug("search unsucessful")
        return result, item


def determineMediaSourceType(url):
    """Determine MMS Source type

    Args:
        url (string): url of MMS

    Returns:
        string: keyword used for which MMS
    """
    nvstreamer_url = ["nvstreamer", "31000"]
    vms_url = ["vms", "30000", "30080", "30888"]

    if any(item in url for item in nvstreamer_url):
        logger.debug("MMS is using nvstreamer")
        return "replay"

    elif any(item in url for item in vms_url):
        logger.debug("MMS is using vms")
        return "live"
