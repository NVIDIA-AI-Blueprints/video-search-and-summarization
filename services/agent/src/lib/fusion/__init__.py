# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Public API for the pure fusion library."""

from lib.fusion.algorithms import apply_global_filters
from lib.fusion.algorithms import apply_per_space_filter
from lib.fusion.algorithms import bucketize
from lib.fusion.algorithms import compute_score_threshold
from lib.fusion.algorithms import fuse
from lib.fusion.algorithms import merge_adjacent_rows
from lib.fusion.algorithms import rows_to_segments
from lib.fusion.algorithms import run_fusion
from lib.fusion.fusion_models import DEFAULT_RRF_K
from lib.fusion.fusion_models import Aggregation
from lib.fusion.fusion_models import FiniteFloat
from lib.fusion.fusion_models import FiniteNonNegFloat
from lib.fusion.fusion_models import FusedRow
from lib.fusion.fusion_models import FusedSegment
from lib.fusion.fusion_models import FusionInput
from lib.fusion.fusion_models import FusionMethod
from lib.fusion.fusion_models import FusionOutput
from lib.fusion.fusion_models import _SharedFusionParams
from lib.fusion.ranking_models import DEFAULT_CHUNK_SECONDS
from lib.fusion.ranking_models import ChunkKey
from lib.fusion.ranking_models import EmbeddingSpaceName
from lib.fusion.ranking_models import FusableSearchOutput
from lib.fusion.ranking_models import RankedChunk
from lib.fusion.ranking_models import RankedList
from lib.fusion.ranking_models import snap
from lib.fusion.ranking_models import validate_chunk_seconds

__all__ = [
    "DEFAULT_CHUNK_SECONDS",
    "DEFAULT_RRF_K",
    "Aggregation",
    "ChunkKey",
    "EmbeddingSpaceName",
    "FiniteFloat",
    "FiniteNonNegFloat",
    "FusableSearchOutput",
    "FusedRow",
    "FusedSegment",
    "FusionInput",
    "FusionMethod",
    "FusionOutput",
    "RankedChunk",
    "RankedList",
    "_SharedFusionParams",
    "apply_global_filters",
    "apply_per_space_filter",
    "bucketize",
    "compute_score_threshold",
    "fuse",
    "merge_adjacent_rows",
    "rows_to_segments",
    "run_fusion",
    "snap",
    "validate_chunk_seconds",
]
