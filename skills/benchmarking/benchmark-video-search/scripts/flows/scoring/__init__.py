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

"""Pluggable scoring backends for the eval.

``SCORING_BACKENDS`` maps the ``--scoring`` name to the class. It lives in this
sub-package (not ``flows/__init__``) deliberately: the backends import ``flows``
themselves, so putting the registry in the top-level ``__init__`` would make
``import flows`` pull in the scoring package before ``flows`` finishes
initialising. Access it as ``flows.scoring.SCORING_BACKENDS``.
"""

from __future__ import annotations

from .base import ScoringBackend
from .clip_relevance import ClipRelevanceScoring
from .drive import DriveScoring
from .segment_overlap import SegmentOverlapScoring

#: Scoring backends by ``--scoring`` name. ``segment-overlap`` is the eval's
#: existing model; ``clip-relevance`` and ``drive`` are the clip-level variants
#: ported from the eval-replication bundle (the drive variant loads the original
#: Drive scorer module for byte-level parity).
SCORING_BACKENDS: dict[str, type] = {
    SegmentOverlapScoring.name: SegmentOverlapScoring,
    ClipRelevanceScoring.name: ClipRelevanceScoring,
    DriveScoring.name: DriveScoring,
}

#: The default when ``--scoring`` is not given. Its output is the legacy shape.
DEFAULT_SCORING = SegmentOverlapScoring.name

__all__ = [
    "DEFAULT_SCORING",
    "SCORING_BACKENDS",
    "ClipRelevanceScoring",
    "DriveScoring",
    "ScoringBackend",
    "SegmentOverlapScoring",
]
