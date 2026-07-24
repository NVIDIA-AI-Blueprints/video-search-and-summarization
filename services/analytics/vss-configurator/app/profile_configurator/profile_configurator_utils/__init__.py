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
Profile Configurator Utilities

This package provides utility functions for configuration file operations
including YAML, JSON, and text file handling.
"""

from .utils import (
    read_yaml_file,
    write_yaml_file,
    read_text_file,
    write_text_file,
    read_json_file,
    write_json_file,
    ensure_directory_exists,
    file_exists,
    update_config_parameters,
    append_if_missing,
    text_replace,
    set_flow_for_lists,
    # expand_list_anchors,
)

__all__ = [
    'read_yaml_file',
    'write_yaml_file',
    'read_text_file',
    'write_text_file',
    'read_json_file',
    'write_json_file',
    'ensure_directory_exists',
    'file_exists',
    'update_config_parameters',
    'append_if_missing',
    'text_replace',
    'set_flow_for_lists',
    # 'expand_list_anchors',
    'YAMLListMerger',
]

