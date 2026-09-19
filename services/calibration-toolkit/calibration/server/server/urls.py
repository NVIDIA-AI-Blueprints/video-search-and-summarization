# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from django.contrib import admin
from django.urls import include, path
from django.conf.urls.static import static
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('calibration.server.projects.urls')),

]
if settings.DEBUG:
    logging.info("MediaURL are available True")
    urlpatterns += static(settings.MEDIA_URL,
                          document_root=settings.MEDIA_ROOT)

urlpatterns += [path('', include('calibration.server.frontend.urls'))]
