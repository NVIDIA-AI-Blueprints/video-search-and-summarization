# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
#from distutils.command.config import config

import os
import json
import ntpath
import shutil
import requests
import ast
import re
from urllib.parse import quote
from io import BytesIO
import pandas as pd


from calibration.server.server.settings import DATA_DIR, BASE_DIR, MEDIA_ROOT
from calibration.server.projects.models import City, Sensor, Project
from calibration.server.projects.utils import ensure_dir

from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.base import ContentFile
from django.http import JsonResponse
import logging

logger = logging.getLogger(__name__)

def uploadData(url):
    """ Imports the Sensors from the Metropolis Media Server

    Args:
        url (str): url with the API to MMS
    """
    projectId = os.path.normpath(url).split(os.path.sep)[-1]
    project = Project.objects.get(id=projectId)
    webApiUrl = project.webApiUrl
    uploadUrl = webApiUrl + "/config/upload-file/"
    logging.debug(uploadUrl)

    fileDir = MEDIA_ROOT + "/files/"
    ensure_dir(fileDir)

    calibrationJson = project.calibrationJson
    logging.debug(f"calibrationJson : {calibrationJson} ")

    temp = json.loads(calibrationJson)
    # logging.debug(calibration)
    calibrationType = temp['calibrationType']
    calibrationJsonFile = os.path.join(fileDir, "calibration.json")
    with open(calibrationJsonFile, 'w') as cjoutfile:
        # json.dump(calibrationJson, cjoutfile, ensure_ascii=False, indent=4)
        cjoutfile.write(calibrationJson)
    
    files = [('configFiles', open(calibrationJsonFile, 'rb'))]
    logging.debug("uploading calibration", files)
    uploadCalibURL = uploadUrl + "calibration"
    try:
        r1 = requests.post(uploadCalibURL, files=files)
        
        if r1.status_code == 201:
            logging.debug("Uploaded Calibration.json Successfully!")
        else:
            logging.error(f"Error while uploading Calibration.json : {json.loads(r1.text)}")
            response_text = json.loads(r1.text)
            return JsonResponse(response_text,  status=r1.status_code)
    except Exception as e:
        logger.error(f"Error in uploading Calibration.json : {str(e)}")
        return JsonResponse({"data": str(e)},  status=500)

    # sensorMetadataCsv = project.sensorMetadataCsv
    # logging.debug(sensorMetadataCsv)

    # sensorMetadataCsvFile = os.path.join(fileDir,"sensorMetadata.csv")
    # sensorMetadataCsvArray = ast.literal_eval(sensorMetadataCsv)

    # logging.debug(sensorMetadataCsvArray[1:])
    # # creating the dataframe
    # df = pd.json_normalize(sensorMetadataCsvArray[1:])
    # df.head()
    # # converted a file to csv
    # df.to_csv(sensorMetadataCsvFile, encoding='utf-8', index=False)

    #create road network json
    if (calibrationType == "geo"):
        roadNetworkJson = project.roadNetworkJson
        roadNetworkJsonFile = os.path.join(DATA_DIR, "roadNetwork.json")
        logging.debug(f"roadNetworkJson: {roadNetworkJson}")
        with open(roadNetworkJsonFile, 'w') as rnoutfile:
            # json.dump(roadNetworkJson, rnoutfile, ensure_ascii=False, indent=4)
            rnoutfile.write(roadNetworkJson)
    
        roadNetworkJsonFile = os.path.join(DATA_DIR, "roadNetwork.json")
        uploadNetworkURL = uploadUrl + "road-network"
        logger.debug(f"uploadNetworkURL : {uploadNetworkURL}")
        files2 = [('configFiles',open(roadNetworkJsonFile,'rb'))]
        # logger.debug ("request 2 json", files2)
        try: 
            r2 = requests.post(uploadNetworkURL, files=files2 )
            
            if r2.status_code == 201:
                logging.debug("Uploaded roadNetwork.json Successfully!")
            else:
                logging.error(f"Error while uploading roadNetwork.json : {r2.text}")
                response_text = json.loads(r2.text)
                return JsonResponse(response_text,  status=r2.status_code)
        except Exception as e:
            logger.error(f"Error in uploading roadNetwork.json : {str(e)}")
            return JsonResponse({"data": str(e)},  status=500)

    return JsonResponse({"data": "Data Uploaded successfully"},  status=201)
    # get response from API
    # response = requests.get(getSensorsUrl)
#     response_dict = json.loads(response.text)

#     duplicate_sensor = {}
#     bad_sensors = []
#     for item in response_dict:
#         sensor_deviceId = item['id']
#         sensor_sensorId = item['name']

#         if (Sensor.objects.filter(sensorId = sensor_sensorId).exists()):
#             #check for device id matching
#             #TODO come back to this
#             if (Sensor.objects.filter(sensorId = sensor_sensorId, deviceId = sensor_deviceId).exists()):
#                 print ("Sensor exists")
#             else:
#                 print ("Sensor ID exists, but device ID's are different")
#                 duplicate_sensor[sensor_sensorId] = sensor_deviceId
#         else:
#             # print (item["id"], item["live_url"], item["name"])
#             logging.debug("sensor does not exist")

#             result, item = validateSensor(item)
#             # if result:
#             # try:
#             getSensorDetailsURL = mmsUrl + "/api/device/" + item['id'] + "/info/"
#             sensor_response = requests.get(getSensorDetailsURL)
#             sensor_response_dict = json.loads(sensor_response.text)
#             print (sensor_response_dict)
#             logging.debug(sensor_response_dict['position']['gps'])
#             # for i in sensor_response_dict:

#             originLat = sensor_response_dict['position']['gps']['latitude']
#             originLng = sensor_response_dict['position']['gps']['longitude']

#             if originLat == "":
#                 originLat = 0.0
#             if originLng == "":
#                 originLng = 0.0

#             newSensor = Sensor(
#                 sensorId = item["name"],
#                 rtspURL = item["live_url"],
#                 # sensorName= item["name"],
#                 deviceId = item['id'],
#                 mmsInfo_protocol = "webrtc",
#                 mmsInfo_host = mmsUrl,
#                 mmsInfo_type = "nvMms",
#                 project=project,
#                 city=city,
#                 sensorPolygon = '[]',
#                 depth = sensor_response_dict['position']['depth'],
#                 fieldOfView = sensor_response_dict['position']['field_of_view'],
#                 direction = sensor_response_dict['position']['direction'],
#                 originLat = originLat,
#                 originLng = originLng
#                 )


#             imgName = '{}.png'.format(item["name"])
#             imageUrl = newSensor.imageUrl
#             imgPath = MEDIA_ROOT + imgName
#             try:
#                 logging.debug(item["live_url"])
#                 framePIL = getFrameFromStream(item["live_url"], mmsUrl, item['id'])
#                 buffer = BytesIO()
#                 framePIL.save(fp=buffer, format='PNG')
#                 frameSave = ContentFile(buffer.getvalue())
#                 imageUrl.save(imgName, InMemoryUploadedFile(
#                     frameSave,
#                     None,
#                     imgName,
#                     'image/png',
#                     frameSave.tell,
#                     None
#                 ))
#             except Exception as e:
#                 logging.debug("Error in rtsp url")
#                 logging.debug(e)
#             try:
#                 newSensor.save()
#             except:
#                 print ("error in sensor")
#                 continue

#             logging.debug("Sensor {} created".format(newSensor.sensorId))
#             # except:

#             # else:
#             #     bad_sensors.append(item)
#     # mapCacheDir = os.path.join(
#     #    DATA_DIR, city.project.name, 'mapmatchingtest-ch')
#     # if os.path.isdir(mapCacheDir):
#     #     shutil.rmtree(mapCacheDir)
#     #     os.mkdir(mapCacheDir)

#     # shutil.copyfile(os.path.join(BASE_DIR,"data","local-config.txt"),os.path.join(DATA_DIR,"local-config.txt"))
# def validateSensor(item):
#     print (item)
#     id_pattern = '^[a-zA-Z0-9 \-]*$'
#     live_url_pattern = 'rtsp:(.*)'
#     name_pattern = "[a-zA-Z0-9!@#$&()_\-`.+,/\" ]*"
#     for key,val in item.items():
#         if key == 'id':
#             result = re.match(id_pattern, val)
#             if (result == False):
#                 logging.debug("id is bad")
#         elif key == 'live_url':
#             result = re.match(live_url_pattern,val)
#             if (result == False):
#                 logging.debug("live url is bad")


#     if result:
#         logging.debug("Item valid")
#         return result, item
#     else:
#         print ("search unsucessful")
#         return result, item

