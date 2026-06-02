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
AICity'26 (Multi-Camera 3D People Tracking) spec metadata.

Holds *only* the pinned values from the AICity Challenge 2026 edition
of the MTMC track — the canonical class-id → class-name table and the
row-field count of the submission text format.  The submission text
format itself is unchanged from 2025 (still 11 whitespace-separated
fields, ``frame_id`` 0-indexed, ``yaw`` in radians), but the class
table is extended by one entry (``PalletTruck`` at ID 6).  Anything
algorithmic (HOTA orchestration, MOT conversion, presentation,
persistence) lives under
:mod:`spatialai_data_utils.eval.tracking.aicity_mtmc_eval`.

This module is a sibling of
:mod:`spatialai_data_utils.datasets.aicity25.spec`; the 2025 table
remains frozen there so existing 2025 consumers keep their stable
reference.  Downstream code that needs to evaluate 2026 submissions
should import from here.
"""

from typing import Dict


# Class-id table for the AICity Challenge 2026 MTMC track.
# - IDs 0-5 are inherited verbatim from the 2025 edition's six classes
#   (Person, Forklift, NovaCarter, Transporter, FourierGR1T2,
#   AgilityDigit).
# - ID 6 (PalletTruck) is the new class introduced for 2026.
#
# Anything outside this table is out-of-spec and is rejected for
# predictions (and warned + skipped for ground truth).  The class
# names are the official display labels; downstream code that reports
# per-class metrics or writes per-class directory names is expected
# to use them verbatim rather than the SDU-internal underscored
# spellings (``Nova_Carter`` / ``Fourier_GR1_T2_Humanoid`` / etc.).
CLASS_ID_TO_NAME: Dict[int, str] = {
    0: "Person",
    1: "Forklift",
    2: "NovaCarter",
    3: "Transporter",
    4: "FourierGR1T2",
    5: "AgilityDigit",
    6: "PalletTruck",
}


# Number of whitespace-separated fields in one row of the AICity'26
# submission / ground-truth text format::
#
#     <scene_id> <class_id> <object_id> <frame_id> <x> <y> <z>
#     <w> <l> <h> <yaw>
#
# Unchanged from the 2025 edition.  Rows with any other field count
# are malformed and must be flagged (hard error for submissions,
# warning for ground truth) by any tool that consumes the format.
NUM_FIELDS: int = 11
