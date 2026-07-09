# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
SpatialAI Data Utils Package Setup Script

This module provides the package configuration and installation setup for
spatialai-data-utils, a comprehensive utility library for 3D object perception,
multi-target multi-camera tracking, and BEV (Bird's Eye View) based systems
in warehouse, retail, and hospital environments.

Package Information:
- Name: spatialai-data-utils
- Version: 2.0.1 (with optional suffix from VERSION_SUFFIX env var)
- Python: >=3.11
- License: Apache-2.0

Key Components:
- Camera calibration and grouping utilities
- BEV group origin calculation
- Multi-camera tracking evaluation
- 3D/2D bounding box processing
- Video processing and visualization tools
- Data loaders for various formats (NVSchema, Sparse4D)
- Ground truth conversion utilities

Main Functions:
- get_version: Retrieve package version with optional suffix
- readme: Load README.md content for package description
- get_requirements: Parse requirements.txt for dependencies

Setup Configuration:
- Automatically discovers packages in spatialai_data_utils namespace
- Includes package data files
- Defines project metadata and classifiers
- Installs dependencies from requirements.txt

Installation:
    # Standard installation
    pip install .

    # Development installation (editable)
    pip install -e .

    # With version suffix
    VERSION_SUFFIX="+dev" pip install .

Usage:
This script is typically invoked via pip or setuptools. Direct execution
will trigger the package installation process.
"""
import os

from setuptools import setup

_BASE_VERSION = "2.0.1"

suffix = os.getenv("VERSION_SUFFIX", "")
version = _BASE_VERSION + suffix
if suffix:
    print(f"Received suffix {suffix}")
print(f"returning {version}")

setup(version=version)
