# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function


from calibration.server.projects.__tests__.project_tests import ProjectTests
from calibration.server.projects.__tests__.city_tests import CityTests
from calibration.server.projects.__tests__.intersection_tests import IntersectionTests
from calibration.server.projects.__tests__.sensor_tests import SensorTests
from calibration.server.projects.__tests__.utils_tests import UtilsTests

# Run project tests
ProjectTests

# Run project tests
CityTests

# Run intersection tests
IntersectionTests

# Run sensor tests
SensorTests

# Run utils tests
UtilsTests
