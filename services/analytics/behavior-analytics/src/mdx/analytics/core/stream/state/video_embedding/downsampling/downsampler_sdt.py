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

import logging
import numpy as np

from mdx.analytics.core.schema.config import VideoEmbeddingConfig
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.stream.state.video_embedding.downsampling.downsampler_base import EmbeddingDownsampler

logger = logging.getLogger(__name__)


class SDTEmbeddingDownsampler(EmbeddingDownsampler):
    """
    Swinging Door Trending (SDT) downsampler for video embeddings.

    This class implements the Swinging Door Trending algorithm adapted for high-dimensional
    video embedding vectors. SDT is a lossy compression technique that reduces data points
    while preserving trend information within configurable tolerance bounds.

    Algorithm Overview:
        The SDT algorithm maintains a three-point state machine:
        
        - **Anchor**: The last stored/committed embedding point (t0, v0)
        - **Candidate**: A look-ahead point pending storage decision (t1, v1)
        - **New Point**: The current incoming point (t2, v2)
        
        For each new point, the algorithm performs linear interpolation to determine where
        the candidate *should* be if it lies on the line from anchor to new point. It then
        compares the actual candidate position to this expected position:
        
        1. If within tolerance → Skip candidate, update candidate to new point
        2. If outside tolerance → Store candidate, make it anchor, new point becomes candidate
        
        This look-ahead strategy allows SDT to detect trend changes more accurately than
        greedy algorithms that only compare consecutive points.

    Tolerance Modes:
        Two metrics are supported for measuring deviation from expected trend:
        
        - **Distance mode**: Uses Euclidean distance between normalized vectors.
          Creates a spherical tolerance region, more faithful to classical SDT semantics.
          Threshold values: 0.05 (strict) to 0.30 (loose), typical: 0.15
        
        - **Cosine mode**: Uses cosine similarity between vectors. Creates an
          angular cone of tolerance. May not preserve classical SDT error bounds.
          Threshold values: 0.85 (loose) to 0.99 (strict), typical: 0.91

    Configuration Parameters:
        From VideoEmbeddingConfig (e.g. AppConfig.video_embedding): same as base
        (downsample_tolerance_mode, downsample_*_threshold, downsample_max_interval_sec).

    State Management:
        - The algorithm maintains internal state between resample() calls
        - Use force_save() at stream boundaries to flush pending candidates
        - State is reset when anchor is None

    :ivar tuple[float, np.ndarray, nvSchema.VisionLLM] | None _anchor: Last stored point (timestamp, vector, protobuf)
    :ivar tuple[float, np.ndarray, nvSchema.VisionLLM] | None _candidate: Pending candidate point (timestamp, vector, protobuf)

    """

    def __init__(self, config: VideoEmbeddingConfig):
        """
        Initialize SDT downsampler and internal state machine.

        :param VideoEmbeddingConfig config: Video embedding config with downsampling parameters
        """
        super().__init__(config)

        self._anchor: tuple[float, np.ndarray, nvSchema.VisionLLM] | None = None
        self._candidate: tuple[float, np.ndarray, nvSchema.VisionLLM] | None = None


    def resample(self, vid_embeddings: list[nvSchema.VisionLLM]) -> list[nvSchema.VisionLLM]:
        """
        Apply SDT algorithm to downsample video embeddings.

        Processes embeddings sequentially using interpolation-based tolerance checking.
        First point stored as anchor, second becomes candidate. For subsequent points:
        checks max interval override, then tests if candidate lies within tolerance of
        the interpolated line from anchor to new point. State persists across calls.

        :param list[nvSchema.VisionLLM] vid_embeddings: Video embedding messages to process
        :return: Selected embeddings that preserve trend within tolerance
        :rtype: list[nvSchema.VisionLLM]
        """
        results: list[nvSchema.VisionLLM] = []

        for vid_embed in vid_embeddings:

            if not vid_embed.llm or not vid_embed.llm.visionEmbeddings:
                logger.warning(f"Missing embeddings, skipping record @ {vid_embed.end.ToDatetime()}")
                continue

            embedding_vector = self._normalize(list(vid_embed.llm.visionEmbeddings[0].vector))
            timestamp = vid_embed.end.ToMilliseconds()

            if not self._anchor:
                self._anchor = (timestamp, embedding_vector, vid_embed)
                results.append(vid_embed)
                continue

            if not self._candidate:
                self._candidate = (timestamp, embedding_vector, vid_embed)
                continue

            candidate_record = self._candidate[2]

            if self._max_interval and (timestamp - self._anchor[0]) >= self._max_interval:  # last saved time = timestamp of anchor
                results.append(candidate_record)
                self._anchor = self._candidate
                self._candidate = (timestamp, embedding_vector, vid_embed)

                logger.info(f"Forced save, chunk timestamp={vid_embed.end.ToDatetime()}, last_saved_time updated.")
                continue

            if self._should_save(timestamp, embedding_vector, vid_embed):
                results.append(candidate_record)

        return results


    def _should_save(self, timestamp: float, embedding_vector: np.ndarray, vid_embed: nvSchema.VisionLLM) -> bool:
        """
        Test if candidate should be saved via interpolation-based tolerance check.

        Computes expected candidate position by linear interpolation from anchor to new point:
        expected = anchor + alpha * (new - anchor), where alpha = (t_cand - t_anch) / (t_new - t_anch).
        Measures deviation between actual and expected candidate using configured tolerance metric.
        
        If within tolerance: advance candidate to new point, return False (skip candidate).
        If outside tolerance: save candidate, promote to anchor, return True.

        :param float timestamp: Timestamp of new point (milliseconds)
        :param np.ndarray embedding_vector: Normalized embedding vector of new point
        :param nvSchema.VisionLLM vid_embed: Complete embedding message of new point
        :return: True to save candidate (outside tolerance), False to skip
        :rtype: bool
        """
        anchor_t, anchor_v = self._anchor[0], self._anchor[1]  # pyright: ignore[reportOptionalSubscript]
        candidate_t, candidate_v = self._candidate[0], self._candidate[1]  # pyright: ignore[reportOptionalSubscript]
        expected_v = None

        # interpolate
        if abs(timestamp - anchor_t) < 1e-10:
            expected_v = embedding_vector
        else:
            alpha = (candidate_t - anchor_t) / (timestamp - anchor_t)
            interpolated = anchor_v + alpha * (embedding_vector - anchor_v)
            expected_v = self._normalize(interpolated)

        if self._tolerance_mode == 'distance':
            distance = self._euclidean_distance(candidate_v, expected_v)
            within_tolerance = distance <= self._distance_threshold
        else:
            similarity = self._cosine_similarity(candidate_v, expected_v)
            within_tolerance = similarity >= self._similarity_threshold

        if within_tolerance:
            # skip, assign new candidate
            self._candidate = (timestamp, embedding_vector, vid_embed)

            logger.debug(f"Within tolerance - skip@time={timestamp}, new_candidate={timestamp}")
            return False
        else:
            self._anchor = self._candidate
            self._candidate = (timestamp, embedding_vector, vid_embed)

            logger.debug(f"Outside tolerance - save@time={timestamp}, new_anchor={self._anchor[0]}, new_candidate={timestamp}") # pyright: ignore[reportOptionalSubscript]
            return True


    def force_save(self) -> nvSchema.VisionLLM | None:
        """
        Flush pending candidate at stream end to avoid data loss.

        Promotes candidate to anchor and clears candidate state. Critical for preserving
        final point in stream.

        :return: Pending candidate embedding if exists, None otherwise
        :rtype: nvSchema.VisionLLM | None
        """
        if self._candidate:
            result = self._candidate[2]
            self._anchor = self._candidate
            self._candidate = None
            return result

        return None
