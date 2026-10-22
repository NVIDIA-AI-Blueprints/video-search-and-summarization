# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Caller-installed decoder requirement; packaging changes are future work."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


def test_native_decoders_are_not_mandatory_wheel_dependencies() -> None:
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    dependency_names = {
        re.split(r"[<>=!~;\[ ]", dependency, maxsplit=1)[0].lower().replace("_", "-")
        for dependency in project["dependencies"]
    }
    assert not dependency_names & {
        "opencv-python",
        "opencv-python-headless",
        "pynvvideocodec",
    }, (
        "Clients install their decoder separately; remove native decoder requirements "
        "from the base distribution when implementing processing"
    )
