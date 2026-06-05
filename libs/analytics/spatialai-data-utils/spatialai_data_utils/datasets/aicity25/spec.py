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
AICity'25 (Multi-Camera 3D People Tracking) spec metadata.

Holds *only* the pinned values from the official challenge
specification at https://www.aicitychallenge.org/2025-track1/ — the
canonical class-id → class-name table and the row-field count of the
submission text format.  Anything algorithmic (HOTA orchestration,
MOT conversion, presentation, persistence) lives under
:mod:`spatialai_data_utils.eval.tracking.aicity_mtmc_eval`.

The eval module, the submission-converter scripts under
``tools/aicity25/``, and any future AICity'25 consumers (visualizers,
comparators, drift-analysis tools, ...) should all import the spec
table from here instead of redefining it, so a spec change has
exactly one source of truth.

The AICity'25 challenge has multiple tracks but only Track 1 (the
Multi-Camera 3D People Tracking task) overlaps with SDU's MTMC scope,
so this module is namespace-flat rather than carrying a ``track1``
prefix on every symbol.
"""

from typing import Dict


# Class-id table is verbatim from the official AICity'25 spec
# (https://www.aicitychallenge.org/2025-track1/): "Person→0, Forklift→1,
# NovaCarter→2, Transporter→3, FourierGR1T2→4, AgilityDigit→5."
#
# AICity'25 has exactly these six classes — anything outside the table
# is out-of-spec and should be rejected.  The class names are the
# official display labels; downstream code that reports per-class
# metrics or writes per-class directory names is expected to use them
# verbatim rather than the SDU-internal underscored spellings
# (``Nova_Carter`` / ``Fourier_GR1_T2_Humanoid`` / etc.).
CLASS_ID_TO_NAME: Dict[int, str] = {
    0: "Person",
    1: "Forklift",
    2: "NovaCarter",
    3: "Transporter",
    4: "FourierGR1T2",
    5: "AgilityDigit",
}


# Number of whitespace-separated fields in one row of the AICity'25
# submission / ground-truth text format::
#
#     <scene_id> <class_id> <object_id> <frame_id> <x> <y> <z>
#     <w> <l> <h> <yaw>
#
# Rows with any other field count are malformed and must be flagged
# (hard error for submissions, warning for ground truth) by any tool
# that consumes the format.
NUM_FIELDS: int = 11
