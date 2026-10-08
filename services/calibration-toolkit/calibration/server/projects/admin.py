# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from django.contrib import admin

from calibration.server.projects.models import Intersection, Project, Sensor, City, Corridor

admin.site.register(Intersection)
admin.site.register(Project)
admin.site.register(Sensor)
admin.site.register(City)
admin.site.register(Corridor)
