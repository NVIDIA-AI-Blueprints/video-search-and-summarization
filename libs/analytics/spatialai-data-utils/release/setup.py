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
- Version: 2.0.0 (with optional suffix from VERSION_SUFFIX env var)
- Python: >=3.10
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

from setuptools import setup, find_packages

__version__ = "2.0.0"


def get_version():
    """
    Get the package version with optional suffix from environment variable.

    Checks for VERSION_SUFFIX environment variable and appends it to the base
    version if present. This is useful for development, pre-release, or custom
    builds.

    :return: Version string (e.g., "2.0.0" or "2.0.0+dev").
    :rtype: str

    Example:
        >>> # Without suffix
        >>> get_version()
        '2.0.0'

        >>> # With suffix (when VERSION_SUFFIX="+dev" is set)
        >>> get_version()
        '2.0.0+dev'
    """
    global __version__
    suffix = os.getenv("VERSION_SUFFIX")
    if suffix:
        print("Received suffix {}".format(suffix))
        __version__ = __version__ + suffix
    print("returning {}".format(__version__))
    return __version__


def readme():
    """
    Read and return the contents of README.md file.

    Loads the package README file which contains detailed documentation,
    usage examples, and project information. This content is used as the
    long description for the package on PyPI or other repositories.

    :return: Content of README.md file as string.
    :rtype: str

    Example:
        >>> content = readme()
        >>> print(content[:50])
        '# SpatialAI Data Utils\n\nComprehensive utilities...'
    """
    with open("README.md", encoding="utf-8") as f:
        content = f.read()
    return content


def get_requirements(filename="Pipfile"):
    """
    Parse and return list of package dependencies from Pipfile.

    Reads the [packages] section of the Pipfile and returns a list of
    package requirement strings for use with setuptools install_requires.
    Skips packages that use non-version specifiers (e.g. git sources)
    since those cannot be expressed as simple requirement strings.

    :param filename: Name of the Pipfile. Defaults to "Pipfile".
    :type filename: str
    :return: List of package requirement strings.
    :rtype: list of str

    Example:
        >>> reqs = get_requirements()
        >>> print(reqs[:3])
        ['lap==0.5.12', 'tqdm==4.67.1', 'scipy==1.15.3']
    """
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    here = os.path.dirname(os.path.realpath(__file__))
    root_dir = os.path.abspath(os.path.join(here, ".."))
    pipfile_path = os.path.join(root_dir, filename)

    with open(pipfile_path, "rb") as f:
        data = tomllib.load(f)

    version_prefixes = ("==", ">=", "<=", "~=", "!=", ">", "<")
    requires = []
    for name, info in data.get("packages", {}).items():
        if isinstance(info, dict):
            version = info.get("version")
        else:
            version = info
        if version is None:
            continue
        version = version.strip('"').strip("'")
        if version == "*":
            requires.append(name)
        elif version.startswith(version_prefixes):
            requires.append(f"{name}{version}")
    return requires


if __name__ == "__main__":
    setup(
        name="spatialai-data-utils",
        version=get_version(),
        author="",
        author_email="",
        description="Utility functions for SpatialAI Datasets",
        long_description=readme(),
        long_description_content_type="text/markdown",
        url="",
        project_urls={
            "Bug Tracker": "",
        },
        classifiers=[
            "Programming Language :: Python :: 3",
            "License :: OSI Approved :: Apache Software License",
            "Operating System :: Ubuntu",
        ],
        license_files=[
            "NOTICE",
            "3rdParty_Licenses.md",
        ],
        keywords="3d object perception, multi-target multi-camera tracking, warehouse, retail store, hospital",
        packages=find_packages(
            where="..", include=["spatialai_data_utils", "spatialai_data_utils.*"]
        ),
        package_dir={"": ".."},
        include_package_data=True,
        python_requires=">=3.10",
        install_requires=get_requirements(),
        extras_require={
            "torch": ["torch>=2.10.0"],
            "full": [
                "torch>=2.10.0",
                "pytorch3d @ git+https://github.com/facebookresearch/pytorch3d.git@33824be#egg=pytorch3d",
            ],
        },
    )
