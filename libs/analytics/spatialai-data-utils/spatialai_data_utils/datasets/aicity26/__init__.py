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
AICity Challenge 2026 dataset utilities.

Sibling of :mod:`spatialai_data_utils.datasets.aicity25`, for the 2026
edition of the AICity Challenge MTMC (Multi-Camera 3D People Tracking)
task.  This package only hosts AICity'26-specific data — the
``scenes/scene_id_to_name.json`` mapping (``Warehouse_023``-
``Warehouse_025``) and the :mod:`spec` module with the 2026 class-id
table (the 2025 set plus ``PalletTruck`` at ID 6) — together with the
scene-id / scene-name lookup helpers.

The 2025 edition's table remains frozen under
:mod:`spatialai_data_utils.datasets.aicity25` so existing 2025
consumers (the eval module, the converters under ``tools/aicity25/``,
and any year-pinned visualizers) keep their stable reference.
Downstream code that needs to evaluate 2026 submissions should import
from here instead of :mod:`...aicity25`.
"""

from .scene_utils import (
    get_default_scene_id_to_name_path,
    load_default_scene_id_to_name,
)

__all__ = [
    "get_default_scene_id_to_name_path",
    "load_default_scene_id_to_name",
]
