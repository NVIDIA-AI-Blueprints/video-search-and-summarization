# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.

import os
import zipfile
from io import BytesIO

from calibration.server.server import settings
from calibration.server.projects.models import City,Sensor

import urllib.request
from django.http import FileResponse
from django.http import HttpResponse
from wsgiref.util import FileWrapper
import logging

logger = logging.getLogger(__name__)
def getWarpedFiles(request):
    logging.debug("do i get here")
    # Files (local path) to put in the .zip
    # FIXME: Change this (get paths from DB etc)
    url = request.path
    project_id = os.path.normpath(url).split(os.path.sep)[-1]
    validated_sensors = Sensor.objects.filter(isValidated = True, project_id=project_id)

    invImgDir = settings.MEDIA_ROOT + "/" + str(project_id) + "/" + "inverted/"
    filenames = []
    for i in validated_sensors.iterator():
        dirp, fn = os.path.split(i.invertImageUrl.url)
        filenames.append(os.path.join(invImgDir,fn))


    # Folder name in ZIP archive which contains the above files
    # E.g [thearchive.zip]/somefiles/file2.txt
    # FIXME: Set this to something better
    zip_subdir = "Warped Images"
    zip_filename = "%s.zip" % zip_subdir

    # # The zip compressor
    with zipfile.ZipFile(zip_filename, "w") as zf:
        for fpath in filenames:
            # Calculate path for file in zip
            fdir, fname = os.path.split(fpath)
            zip_path = os.path.join(zip_subdir, fname)

            # # Add file, at correct path
            zf.write(fpath, zip_path)

    wrapper = FileWrapper(open(zip_filename, 'rb'))
    content_type = 'application/zip'
    content_disposition = 'attachment; filename=%s.zip' % zip_subdir

    response = HttpResponse(wrapper, content_type=content_type)
    response['Content-Disposition'] = content_disposition
    return response