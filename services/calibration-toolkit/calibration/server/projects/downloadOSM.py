# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import ntpath
import shutil
import urllib.request
from calibration.server.server.settings import DATA_DIR, BASE_DIR
from calibration.server.projects.models import Project
import logging

logger = logging.getLogger(__name__)

def getOSMFile(url):
    """ Downloads the OSM file based on the download url in the City object

    Args:
        url (str): url with the id of the city to download the OSM file for
    """
    projectId = os.path.normpath(url).split(os.path.sep)[-1]
    project = Project.objects.get(id=projectId)

    mapFile = project.mapFile
    filename = ntpath.basename(mapFile)
    saveFile = os.path.join(DATA_DIR, filename)

    if not os.path.exists(saveFile):

        with urllib.request.urlopen(mapFile) as response, open(saveFile, 'wb') as output:
            shutil.copyfileobj(response, output)
        logger.debug(f"osm file downloaded at : {saveFile}")
    else:
        logger.debug(f"osm file already present at : {saveFile}")
        
    mapCacheDir = os.path.join(
       DATA_DIR,  project.name, 'mapmatchingtest-ch')
    if os.path.isdir(mapCacheDir):
        shutil.rmtree(mapCacheDir)
        os.mkdir(mapCacheDir)

    shutil.copyfile(os.path.join(BASE_DIR, "data", "local-config.txt"),
                    os.path.join(DATA_DIR, "local-config.txt"))