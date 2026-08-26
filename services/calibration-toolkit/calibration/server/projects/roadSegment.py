# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import subprocess
import os
import ntpath
import json
from tempfile import mkstemp
from shutil import move, copymode
from os import curdir, fdopen, remove
from calibration.server.server.settings import BASE_DIR,DATA_DIR
from calibration.server.projects.models import Intersection, Project
from calibration.server.projects.utils import convertLngToLon, convertLonToLng, ensure_dir
import logging

logger = logging.getLogger(__name__)

def getRoadSegment(url):
    """Calculate the road segment given line segments drawn for an intersection

    Args:
        url (str): url with the id of the intersection to get the network for
    """
    lineSegmentJSON = os.path.join(DATA_DIR, "line_segment.json")

    # curDir = os.getcwd()
    # networkDir = os.path.join(curDir,"data")
    roadNetworkJSON = os.path.join(DATA_DIR, "roadNetwork.json")
    logger.debug(f"roadNetworkJSON : {roadNetworkJSON}")
    ensure_dir(roadNetworkJSON)

    intersectionId = os.path.normpath(url).split(os.path.sep)[-1]
    intersection = Intersection.objects.get(id=intersectionId)

    updateLocalConfig(intersection)

    intersectionSegments = getIntersectionSegments(intersection)
    lineSegmentData = {"intersections": [intersectionSegments]}
    logger.debug(f"lineSegmentData : {lineSegmentData}")
    with open(lineSegmentJSON, 'w') as output:
        json.dump(lineSegmentData, output, indent=4)

    runNetworkGen(intersection)

    parseRoadNetworkJSON(intersectionId, roadNetworkJSON)
    os.remove(lineSegmentJSON)
    os.remove(roadNetworkJSON)


def parseRoadNetworkJSON(intersectionId, inputFile):
    """ Parse the generated network.json and save the data to the intersection

    Args:
        intersectionId (int): id of the intersection to save the data to
        inputFile (str): filepath of the network.json input file
    """
    with open(inputFile, 'r') as networkJSON:
        intersectionSegments = json.load(networkJSON)
    intersection = Intersection.objects.get(id=intersectionId)

    roadSegments = []
    for segment in intersectionSegments['intersections'][0]['segments']:
        points = []
        for point in segment['points']:
            points.append(convertLonToLng(point))
        segmentData = {
            'id': segment['id'],
            'direction': segment['direction'],
            'category': 'road_links',
            'points': points
        }
        roadSegments.append(segmentData)
    intersection.roadLinks = json.dumps(roadSegments)
    logger.debug(f"intersection road link: {intersection.roadLinks}")
    intersection.save()


def getIntersectionSegments(intersection):
    """ Gets the line segments for a given intersection

    Args:
        intersection (QuerySet): the intersection called from the backend

    Returns:
        dict: the organized name and line segments of the intersection
    """
    segments = []

    name = intersection.name
    lineSegments = json.loads(intersection.lineSegments)
    for link in lineSegments:
        start = convertLngToLon(link['points'][0])
        end = convertLngToLon(link['points'][-1])
        direction = ""

        linkData = {'start': start,
                    'direction': direction,
                    'end': end}
        segments.append(linkData)

    intersectionData = {
        "name": name,
        "segments": segments}

    return intersectionData


def getOSMFileFromLocalConfig():
    """ Update the local-config.txt file (required by java binary)

    Args:
        intersection (QuerySet): intersection data to use to update local-config
    """
    local_config_file_path = os.path.join(DATA_DIR, 'local-config.txt')
    
    osmFilePath = ''
    with open(local_config_file_path) as file:
        for line in file:
            if 'osmFile=' in line:
                osmFilePath=line.split("=")[1].strip()
                break

    logger.info(f"getting osm file : config-local - {local_config_file_path},  osmFile - {osmFilePath}")                
    return osmFilePath       
   


def updateLocalConfig(intersection):
    """ Update the local-config.txt file (required by java binary)

    Args:
        intersection (QuerySet): intersection data to use to update local-config
    """
    projectName = intersection.project.name
    mapFile = intersection.project.mapFile
    filename = ntpath.basename(mapFile)
    setOSMAndCache(os.path.join(DATA_DIR, 'local-config.txt'),
                   os.path.join(DATA_DIR, filename),
                   os.path.join(DATA_DIR, projectName, 'mapmatchingtest-ch'))


def setOSMAndCache(filePath, newFileOSM, newFileCache):
    """ Set the osmFile and osmFileCache settings in the local config

    Args:
        filePath (str): path to original local-config.txt file
        newFileOSM (str): path to openstreemap file
        newFileCache (str): path to map matching cache folder
    """
    logger.info(f"setting osm cache : filePath - {filePath}, newFileCache - {newFileCache}, newFileOSM - {newFileOSM}")
    fh, abs_path = mkstemp()
    with fdopen(fh, 'w') as new_file:
        with open(filePath) as old_file:
            for line in old_file:
                if 'osmFile=' in line:
                    new_file.write('osmFile=' + newFileOSM + '\n')
                elif 'osmFileCache=' in line:
                    new_file.write('osmFileCache=' + newFileCache + '\n')
                else:
                    new_file.write(line)

    copymode(filePath, abs_path)
    remove(filePath)
    move(abs_path, filePath)



def runNetworkGen(intersection):

    """ Run the network generation python script"""

    input_file = os.path.join(DATA_DIR, "line_segment.json")
    config_file = os.path.join(DATA_DIR, "local-config.txt")
    logger.info(f"config_path : {config_file}")
    
    custom_crs_config_path = os.path.join(DATA_DIR, "crs_config.json")
    default_crs_config_path = os.path.join("/", "crs_config.json")
    # Initilialze to default file.
    if os.path.exists(default_crs_config_path):
        crs_config_path = default_crs_config_path
    # If custom exist use the custom file
    if os.path.exists(custom_crs_config_path):
        crs_config_path = custom_crs_config_path
    
    if not os.path.exists(crs_config_path):
        logging.error(
            f"ERROR: The indicated config file `{crs_config_path}` does NOT exist.")
        exit(1)

    #line_segment_path = 'tests/resources/line_segment_v0.3.json'
    from mdx.analytics.core.utils.crs import CoordinateReferenceSystem as crs
    from mdx.analytics.core.schema.config import AppConfig, AppCoordinateReferenceSystemConfig
    from mdx.analytics.core.utils.io_utils import ValidateFile, validate_file_path, load_json_from_file

    
    #output_path = os.path.join(DATA_DIR, "/outputs/")
    valid_crs_config_path = validate_file_path(crs_config_path)
    if not os.path.exists(valid_crs_config_path):
        logging.error(
            f"ERROR: The indicated config file `{valid_crs_config_path}` does NOT exist.")
        exit(1)
    config = AppCoordinateReferenceSystemConfig(**load_json_from_file(valid_crs_config_path))
    logger.debug(f"config : {config}")
    config.roadNetwork.visualization.visualizationGraphShowGraph = False
    
    ## test case parameters
    
    if config.roadNetwork.graph.osmLoadMethod == "from_point":
        config.roadNetwork.graph.osmQueryPoint.lat = intersection.originLat
        config.roadNetwork.graph.osmQueryPoint.lon = intersection.originLng

    elif config.roadNetwork.graph.osmLoadMethod == "from_file": 
        config.roadNetwork.graph.osmQueryFile = getOSMFileFromLocalConfig()

    config.roadNetwork.graph.osmSimplify = False
    config.roadNetwork.roadNetworkUseCRSCartesian = False
    config.roadNetwork.segmentShiftDistanceMeters = 5

    logger.debug(f"new config : {config}")

    from mdx.analytics.core.utils.crs import CoordinateReferenceSystem as crs
    crs_mdx = crs(config)
    output_path = os.path.join(DATA_DIR, "roadNetwork.json")
    logger.debug(f"CRS object created")
    crs_mdx.create_network_json_file(input_file, output_path)
    logger.info(f"json file created at : {output_path}")


if __name__ == "__main__":
    getRoadSegment()
