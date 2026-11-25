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
Utility modules for ROI/Tripwire support
"""

from .annotation_storage import AnnotationStorage, resolve_camera_annotation_block
from .calibration_exporter import (
    CalibrationExporter,
    export_calibration_for_project,
    export_image_calibration_for_project,
)
from .state_manager import StateManager
from .vios_client import ViosClient, ViosClientError

__all__ = [
    'AnnotationStorage',
    'resolve_camera_annotation_block',
    'CalibrationExporter',
    'export_calibration_for_project',
    'export_image_calibration_for_project',
    'StateManager',
    'ViosClient',
    'ViosClientError',
]

