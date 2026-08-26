# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import uuid
import json
import os

from calibration.server.projects.approxHomography import get_original_from_projected
import logging

logger = logging.getLogger(__name__)

def get_uuid_id():
    return str(uuid.uuid4())


def convertLngToLon(point):
    """Convert ways of writing longitude between lng and lon

    Args:
        point (dict): a lat/lng coordinate

    Returns:
        dict: a lat/lon coordinate, where lng=lon
    """
    return {"lat": point["lat"], "lon": point["lng"]}


def convertLonToLng(point):
    """Convert a lat/lon point to a lat/lng point

    Args:
        point (dict): lat/lon coordinate to be converted

    Returns:
        (dict): lat/lng coordinate where lng=lon
    """
    return {"lat": point["lat"], "lng": point["lon"]}


def convertXYtoLatLng(polygon):
    """Convert a X/Y point to a lat/lng point

    Args:
        point (dict): lat/lon coordinate to be converted

    Returns:
        (dict): lat/lng coordinate where lng=lon
    """
    latLngPolygon = []
    for v in polygon:
        latLngPolygon.append({"lat": v["y"], "lng": v["x"]})
    # logging.debug(latLngPolygon)
    return latLngPolygon


def convertLatLngtoXY(polygon):
    """Convert a lat/lon point to a X/Y point

    Args:
        point (dict): lat/lon coordinate to be converted

    Returns:
        (dict): X/Y coordinate where lat=X, lng=Y
    """
    XYPolygon = []
    for v in polygon:
        XYPolygon.append({"y": v["lat"], "x": v["lng"]})
    return XYPolygon




def convertGlobaltoEdgeLengths(polygon):
    """Convert a lat/lon point to a lat/lng point

    Args:
        point (dict): lat/lon coordinate to be converted

    Returns:
        (dict): lat/lng coordinate where lng=lon
    """
    latLngPolygon = []
    for v in polygon:
        latLngPolygon.append({"lat": v["y"], "lng": v["x"]})
    return str(latLngPolygon)


def unscaleCoordinates(coordinates, scaleFactor):
    newCoordinates = []
    for point in coordinates:
        newPoint = {}
        newPoint["x"] = (point["x"]) * scaleFactor
        newPoint["y"] = (point["y"]) * scaleFactor
        newCoordinates.append(newPoint)
    return newCoordinates


def getPoints(coordinates, scaleFactor):
    points = []
    unscaledCoordinates = unscaleCoordinates(coordinates, scaleFactor)
    points = convertXYtoLatLng(unscaledCoordinates)
    return points


def getWirePoints(coordinates, scale_factor):
    points = []
    grouped_coords = [coordinates["p1"], coordinates["p2"]]
    unscaled_coords = unscaleCoordinates(grouped_coords, scale_factor)
    points = convertXYtoLatLng(unscaled_coords)
    return points


def unpad_points(coordinates):
    output = []
    origin = coordinates[0]
    for point in coordinates:
        output.append(
            {"lat": point["lat"] - origin["lat"], "lng": point["lng"] - origin["lng"]}
        )
    return output, origin

def flipCoordY(coordinates, height):
    output = []
    for point in coordinates:
        output.append(
            {"lat": height - point["lat"], "lng": point["lng"] }
        )
    return output



# Tripwire/Dir/ EdgePolygons need to be converted from XY to Lat/LNG
# Scale Factor needs to be applied (multiplied)
def importImagePolygon(input, labelId):
    output = []
    polygon = {}
    polygon["id"] = get_uuid_id()
    polygon["type"] = "polygon"
    polygon["points"] = convertXYtoLatLng(input)
    polygon["class"] = labelId
    output.append(polygon)
    return json.dumps(output, separators=(",", ":"))


def importEdgeLengths(input, scaleFactor):
    output = []
    unscaledPoints = getPoints(input, scaleFactor)
    output, origin = unpad_points(unscaledPoints)
    return json.dumps(output, separators=(",", ":")), origin["lng"], origin["lat"]


def importPolygon(input, labelId, scaleFactor, color=None, height=None):
    output = []
    polygon = {}
    polygon["id"] = get_uuid_id()
    polygon["type"] = "polygon"
    polygon["points"] = getPoints(input, scaleFactor)
    polygon["category"] = labelId
    if height is not None:
        polygon["points"] = flipCoordY(polygon["points"], height)
    if color is not None:
        polygon["color"] = color
    output.append(polygon)
    return json.dumps(output, separators=(",", ":"))




def importCoordinates(input, sensorId, scaleFactor, color, height):
    output = []
    polygon = {}
    polygon["id"] = sensorId
    polygon["type"] = "point"
    polygon["points"] = getPoints(input, scaleFactor)
    polygon["class"] = sensorId
    polygon["color"] = color
    output.append(polygon)
    return json.dumps(output, separators=(",", ":"))


def importROI(input, labelId, scaleFactor, color, height=None):
    output = []
    for i in input:
        polygon = {}
        polygon["id"] = get_uuid_id()
        polygon["type"] = "polygon"
        polygon["points"] = getPoints(i["roiCoordinates"], scaleFactor)
        if height is not None:
            polygon["points"] = flipCoordY(polygon["points"], height)
        polygon["category"] = labelId
        polygon["color"] = color
        output.append(polygon)
    return json.dumps(output, separators=(",", ":"))


def importTripwires(input, labelId, scaleFactor, colors, height=None):
    tripwire = []
    tripDir = []
    for i in input:
        wire_polyline = {}
        wire_polyline["id"] = get_uuid_id()
        wire_polyline["type"] = "polyline"
        wire_polyline["points"] = getWirePoints(i["wire"], scaleFactor)
        wire_polyline["class"] = labelId[0]
        wire_polyline["color"] = colors[0]
        if height is not None:
            wire_polyline["points"] = flipCoordY(wire_polyline["points"], height)
        tripwire.append(wire_polyline)
        dir_polyline = {}
        dir_polyline["id"] = get_uuid_id()
        dir_polyline["type"] = "polyline"
        dir_polyline["points"] = getWirePoints(i["direction"], scaleFactor)
        dir_polyline["class"] = labelId[1]
        dir_polyline["color"] = colors[1]
        if height is not None:
            dir_polyline["points"] = flipCoordY(dir_polyline["points"], height)
        tripDir.append(dir_polyline)
    return json.dumps(tripwire, separators=(",", ":")), json.dumps(
        tripDir, separators=(",", ":")
    )


def getOriginalTripwires(input, H):
    wires = json.loads(input)
    for w in wires:
        newPoints = convertXYtoLatLng(get_original_from_projected(H, w["points"]))
        w["points"] = newPoints
    return json.dumps(wires, separators=(",", ":"))


def ensure_dir(file_path):
    directory = os.path.dirname(file_path)
    if not os.path.exists(directory):
        logger.debug(f"Making Directory: {directory}")
        os.makedirs(directory)
    else:
        logger.debug("Directory exists")
