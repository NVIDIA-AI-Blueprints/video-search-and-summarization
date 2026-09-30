# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'
    name = 'calibration.server.projects'
