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
AICity Challenge 2025 dataset utilities.

Generic split loaders (``load_split_from_yaml`` / ``_json`` / ``_py``)
live in :mod:`spatialai_data_utils.datasets.splits`; this package only
hosts AICity'25-specific data (e.g. the packaged
``scenes/scene_id_to_name.json``) together with the object-class
config re-export and the scene-id / scene-name lookup helpers.
"""

from .object_class_utils import load_class_config_from_file
from .scene_utils import (
    get_default_scene_id_to_name_path,
    load_default_scene_id_to_name,
)

__all__ = [
    "load_class_config_from_file",
    "get_default_scene_id_to_name_path",
    "load_default_scene_id_to_name",
]
