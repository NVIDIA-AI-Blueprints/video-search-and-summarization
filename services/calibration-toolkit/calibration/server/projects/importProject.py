# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import json
import zipfile
from urllib.parse import quote
from io import BytesIO
from PIL import Image
import numpy as np
import time


from django.core.files import File
from calibration.server.server.settings import DATA_DIR, BASE_DIR, MEDIA_ROOT
from calibration.server.projects.models import Project, Sensor
from calibration.server.projects.homography import getHomography
from calibration.server.projects.approxHomography import getApproxHomography
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.base import ContentFile
from calibration.server.projects.utils import (
    convertXYtoLatLng,
    importImagePolygon,
    ensure_dir,
    importPolygon,
    importROI,
    importTripwires,
    get_uuid_id,
    importCoordinates,
    importEdgeLengths,
    getOriginalTripwires,
)
from calibration.server.projects.constants import *
from calibration.server.server import settings
from calibration.server.projects.homography import getHomography

import logging
logger = logging.getLogger(__name__)


def importProject(url):
    """Imports the Sensors from the Metropolis Media Server

    Args:
        url (str): url with the API to MMS
    """
    logging.debug("Importing project %s", url)
    projectId = os.path.normpath(url).split(os.path.sep)[-1]
    logging.debug(projectId)
    project = Project.objects.get(id=projectId)

    calibrationJsonTemp = json.loads(project.calibrationJsonTemp)
    imageMetaDataJsonTemp = json.loads(project.imageMetaDataJsonTemp)
    imageFiles = project.imageFiles

    logging.debug("cjson", calibrationJsonTemp)
    logging.debug("imageMetaDataJsonTemp", imageMetaDataJsonTemp)

    temp_dir = settings.MEDIA_ROOT + "/{}/temp/".format(projectId)
    ensure_dir(temp_dir)
    logging.debug("Extracting images")
    try:
        with zipfile.ZipFile(imageFiles, "r") as zip_ref:
            zip_ref.info = 3
            zip_ref.extractall(temp_dir)
            time.sleep(1)
    except Exception as e:
        logging.error("Issue unzipping Images file {}".format(imageFiles), e)

    logging.debug("Img Files: %s", str(os.listdir(temp_dir)))
    temp_img_dir = os.path.join(temp_dir, "Images")
    ensure_dir(temp_img_dir)
    logging.debug("Creating Project")

    try:
        create_project(
            calibrationJsonTemp, imageMetaDataJsonTemp, project, temp_img_dir
        )
        return True
    except Exception as e:
        logging.error("Error in creating project", e)
        project.delete()
        logging.error("error in project")
        time.sleep(0.1)
        return False


def determine_calibration(cjson, ijson):
    calibration_type = cjson["calibrationType"]
    if calibration_type:
        logging.debug("calibration_type :{}".format(calibration_type))
        return calibration_type
    else:
        if ".bz2" in cjson["osmURL"]:
            return "geo"
        else:
            for entry in ijson["images"]:
                if entry["view"] == "plan-view" and "place" in entry.keys():
                    logging.debug("mtmc")
                    return "mtmc"
                elif entry["view"] == "warped-camera-view" and "sensorId" in entry.keys():
                    logging.debug("Cartesian")
                    return "cartesian"
            return "image"


# Function to get camera-view fileName based on sensorId
def get_sensor_image(sensor_id, data):
    for image in data["images"]:
        if (
            "sensorId" in image
            and image["sensorId"] == sensor_id
            and image["view"] == "camera-view"
        ):
            return image.get("fileName", None)
    return None


# Function to get warped-camera-view fileName based on sensorId
def get_sensor_warped_image(sensor_id, data):
    for image in data["images"]:
        if (
            "sensorId" in image
            and image["sensorId"] == sensor_id
            and image["view"] == "warped-camera-view"
        ):
            return image.get("fileName", None)
    return None


# Function to get plan-view fileName based on project
def get_floorplan_image(data):
    for image in data["images"]:
        if "place" in image and image["view"] == "plan-view":
            return image.get("fileName", None)
    return None


def get_im_size(imageUrl):
    pilImage = Image.open(imageUrl)
    cv2Image = np.array(pilImage)
    # if invImWidth == 0 or invImHeight == 0:
    imHeight = int(cv2Image.shape[0])
    imWidth = int(cv2Image.shape[1])
    return imWidth, imHeight


def create_project(cjson, ijson, project, temp_img_dir):
    project.calibrationType = determine_calibration(cjson, ijson)
    logging.debug("project.calibrationType", project.calibrationType)
    # project.name = get_uuid_id()
    place_list = []
    logging.debug("determined calibration type: {}".format(project.calibrationType))
    logging.debug("Get Sensors")

    for idx, sensor in enumerate(cjson["sensors"]):
        try:    
            logging.debug(sensor)
            #logging.debug("Creating sensor: ", sensor["id"])
            logging.debug("Get Attributes")
            attributes = {item["name"]: item["value"] for item in sensor["attributes"]}
            logging.debug("Set Sensor Attributes")

            newSensor = Sensor(
                sensorId=sensor["id"],
                # rtspURL=sensor["live_url"],
                fps=attributes["fps"],
                depth=attributes["depth"],
                fieldOfView=attributes["fieldOfView"],
                direction=attributes["direction"],
                mmsInfo_type=attributes["source"],
                project=project,
                deviceId=sensor["id"],
                type=sensor["type"],
                scaleFactor=sensor["scaleFactor"],
                isCalibrated=True,
                isValidated=True,
                originLat=sensor["origin"]["lat"],
                originLng=sensor["origin"]["lng"],
                height=attributes["frameHeight"],
                width=attributes["frameWidth"],
                calibrationType=project.calibrationType,
                # originLat = sensor['geoLocation']['lat']
                # originLng = sensor['geoLocation']['lng']
            )
            logging.debug("Get places")
            for item in sensor["place"]:
                place_list.append(item)
            logging.debug("Get sensor images")
            sensor_image_path = get_sensor_image(sensor["id"], ijson)
            if sensor_image_path:
                newSensor.imageUrl.save(
                    sensor_image_path,
                    File(open(os.path.join(temp_img_dir, sensor_image_path), "rb")),
                )
            else:
                logging.error("No Sensor Image found for sensor: {}".format(sensor["id"]))

            logging.debug("Project: %s", project.calibrationType)
            # newSensor.save()
            if project.calibrationType == "geo":
                # newSensor.gisPolygon = convertXYtoLatLng(sensor['globalCoordinates'])

                newSensor.sensorPolygon = importPolygon(
                    sensor["imageCoordinates"], calibId, 1
                )
                newSensor.gisPolygon = importPolygon(sensor['globalCoordinates'], calibId, 1)
                logger.debug(f"gis Polygon : {newSensor.gisPolygon}")
                newSensor.homography = getHomography(newSensor.sensorPolygon, newSensor.gisPolygon, newSensor.calibrationType)
                newSensor.roiPolygon = importROI(
                    sensor["rois"], roiId, sensor["scaleFactor"], colors[1]
                )
                logger.debug(f"roiPolygon : {newSensor.roiPolygon}")

            elif project.calibrationType == "cartesian":
                logging.debug("Get Warped Sensor Image")
                sensor_warped_image_path = get_sensor_warped_image(sensor["id"], ijson)
                if sensor_warped_image_path:
                    newSensor.invertImageUrl.save(
                        sensor_warped_image_path,
                        File(open(os.path.join(temp_img_dir, sensor_warped_image_path), "rb")),
                    )
                    newSensor.invertImWidth, newSensor.invertImHeight = get_im_size(
                        os.path.join(temp_img_dir, sensor_warped_image_path)
                    )
                else:
                    logging.debug("No Warped Sensor Image found for sensor: {}".format(sensor["id"]))
                
                logging.debug("Get Sensor Polygon Image")
                newSensor.sensorPolygon = importPolygon(
                    sensor["imageCoordinates"], cartCalibId, 1
                )
                logging.debug("Get EdgeLengths")

                (
                    newSensor.edgeLengths,
                    newSensor.invertImXPad,
                    newSensor.invertImYPad,
                ) = importEdgeLengths(sensor["globalCoordinates"], sensor["scaleFactor"])
                logging.debug("Get Homography")
                newSensor.homography, newSensor.imHomography = getApproxHomography(
                    newSensor
                )
                # check the ids for this
                logging.debug("Get ROIs")
                roiPolygon = importROI(
                    sensor["rois"], cartRoiId, sensor["scaleFactor"], colors[1]
                )
                newSensor.roiPolygon = getOriginalTripwires(roiPolygon, newSensor.homography)
                logging.debug("Get Tripwires and Directions")
                if "tripwires" in sensor:
                    tripwireLines, tripDirLines = importTripwires(
                        sensor["tripwires"],
                        [cartTripwireId, cartTripDirId],
                        sensor["scaleFactor"],
                        [colors[2], colors[3]],
                    )
                    newSensor.tripwireLines = getOriginalTripwires(tripwireLines, newSensor.homography)
                    newSensor.tripDirLines = getOriginalTripwires(tripDirLines, newSensor.homography)

            elif project.calibrationType == "floorplan":
                newSensor.sensorPolygon = importPolygon(
                    sensor["imageCoordinates"],
                    floorPlanCalibId,
                    1
                )
                newSensor.floorPlanPolygon = importPolygon(
                    sensor["globalCoordinates"],
                    floorPlanCalibMapId,
                    sensor["scaleFactor"],
                    colors[1]
                )
                newSensor.homography = getHomography(
                    newSensor.sensorPolygon, newSensor.floorPlanPolygon, newSensor.calibrationType
                )
            elif project.calibrationType == "image":
                newSensor.sensorPolygon = importPolygon(
                    sensor["imageCoordinates"], calibId, 1
                )
                newSensor.edgeLengths = "[]"
                logging.debug("Get ROIs")
                newSensor.roiPolygon = importROI(
                    sensor["rois"], roiId, sensor["scaleFactor"], colors[1]
                )
                logging.debug("Get Tripwires and Directions")
                if "tripwires" in sensor:
                    newSensor.tripwireLines, newSensor.tripDirLines = importTripwires(
                        sensor["tripwires"],
                        [tripwireId, tripDirId],
                        sensor["scaleFactor"],
                        [colors[2], colors[3]],
                    )
            elif project.calibrationType == "mtmc":
                logging.debug("Get FP Sensor Image")
                image_path = get_floorplan_image(ijson)
                if image_path:
                    newSensor.floorPlanImageUrl.save(
                        "{}_fp".format(newSensor.sensorId),
                        File(open(os.path.join(temp_img_dir, image_path), "rb")),
                    )
                # newSensor.floorPlanImHeight = 0
                # newSensor.floorPlanImWidth = 0
                    newSensor.floorPlanImWidth, newSensor.floorPlanImHeight = get_im_size(
                        os.path.join(temp_img_dir, image_path)
                    )
                else:
                    logging.error("No Floorplan Image found for sensor: {}".format(sensor["id"]))

                logging.debug("Get Image Polygon")
                newSensor.sensorPolygon = importPolygon(
                    sensor["imageCoordinates"], floorPlanCalibId, 1
                )
                logging.debug("Get Global Coordinates Polygon")
                newSensor.gisPolygon = importPolygon(
                    sensor["globalCoordinates"],
                    floorPlanCalibMapId,
                    sensor["scaleFactor"],
                    colors[1],
                    newSensor.floorPlanImHeight
                )
                logging.debug("Get Homography")
                newSensor.homography = getHomography(
                    newSensor.sensorPolygon, newSensor.gisPolygon, newSensor.calibrationType
                )
                # newSensor.floorPlanPolygon = \
                #     convertGlobaltoEdgeLengths(sensor['globalCoordinates'])
                logging.debug("Get Coordinates")
                newSensor.coordinates = importCoordinates(
                    [sensor["coordinates"]],
                    sensor["id"],
                    sensor["scaleFactor"],
                    colors[idx],
                    newSensor.floorPlanImHeight
                )
                logging.debug("Get ROIs")
                newSensor.roiPolygon = importROI(
                    sensor["rois"],
                    floorPlanRoiId,
                    sensor["scaleFactor"],
                    colors[2],
                    newSensor.floorPlanImHeight
                )
                logging.debug("Get Tripwires and Directions")
                if "tripwires" in sensor:
                    newSensor.tripwireLines, newSensor.tripDirLines = importTripwires(
                        sensor["tripwires"],
                        [floorPlanTripwireId, floorPlanTripDirId],
                        sensor["scaleFactor"],
                        [colors[3], colors[4]],
                        newSensor.floorPlanImHeight
                    )
                # newSensor.coordinates = convertXYtoLatLng(sensor['coordinates'])
            # else:
            # newSensor.edgeLengths = "[]"

            try:
                newSensor.save()
                time.sleep(0.1)
            except Exception as e:
                logging.error("sensor not saved")
                logging.error(e)
        except Exception as e:
            logging.error("Error in creating sensor: {}".format(sensor["id"]), e)
            
    unique_places = {(place["name"], place["value"]) for place in place_list}
    logging.debug(f"number of Unique Places {unique_places}")
    for place in unique_places:
        name, value = place  # Unpack the tuple into name and value
        if name == "building":
            if Project.objects.filter(name=value).exists():
                project.name = value + "_" + get_uuid_id()
            else:
                project.name = value
            # logging.debug(f"Found building: {value}")
        elif name == "room":
            project.roomPlace = value
        elif name == "city":
            project.cityPlace = value
        else:
            logging.debug("unexpected place found in unique_places.")
    if project.calibrationType == "geo":
        project.mapFile = cjson["osmURL"]
        try:
            project.save()
            time.sleep(0.1)
        except Exception as e:
            logging.error(e)
            project.delete()
            time.sleep(0.1)
            logging.error("error in project")
    elif project.calibrationType == "cartesian":
        try:
            project.save()
            time.sleep(0.1)
        except Exception as e:
            logging.error(e)
            project.delete()
            logging.error("error in project")
    elif project.calibrationType == "mtmc":
        try:
            image_path = get_floorplan_image(ijson)
            if image_path:
                project.floorPlanImageUrl.save(
                    image_path, File(open(os.path.join(temp_img_dir, image_path), "rb"))
                )
            else:
                logging.error("No Floorplan Image found for project")
            project.save()
            time.sleep(0.1)
        except Exception as e:
            logging.error(e)
            project.delete()
            time.sleep(0.1)
            logging.error("error in project")
    elif project.calibrationType == "image":
        try:
            project.save()
        except Exception as e:
            logging.error(e)
            project.delete()
            time.sleep(0.1)
            logging.error("error in project")
    else:
        project.delete()
        time.sleep(0.1)
        logging.error("Calibration Type not in project")


def createGeoProject(project, cjson, ijson):
    for sensor in cjson.sensors:
        attributes = {}

        for name, value in sensor.attributes.items():
            attributes[name] = value

    try:
        project.save()
        time.sleep(0.1)
    except:
        project.delete()
        logging.debug("error in project")
