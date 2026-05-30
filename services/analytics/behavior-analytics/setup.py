# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Shim so CI can inject a VERSION_SUFFIX at build time.

All other metadata lives in pyproject.toml. VERSION_SUFFIX is set by the
Jenkins multi-arch pipeline (e.g. ``.dev42``) and appended to the base
version when building release candidates.
"""
import os

from setuptools import setup

_BASE_VERSION = "3.2.0"

suffix = os.getenv("VERSION_SUFFIX", "")
version = _BASE_VERSION + suffix
if suffix:
    print(f"Received suffix {suffix}")
print(f"returning {version}")

setup(version=version)
