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
from collections import deque
from datetime import datetime, timezone

from mdx.analytics.core.schema.config import VideoEmbeddingConfig
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.stream.state.video_embedding.downsampling.downsampler_base import EmbeddingDownsampler

logger = logging.getLogger(__name__)


class SlidingWindowEmbeddingDownsampler(EmbeddingDownsampler):
    """
    Sliding window downsampler for video embeddings with neighbor-based novelty detection.

    This class implements a sliding window compression algorithm that detects and stores novel
    or transitional embedding vectors by comparing them to recent historical context. Unlike
    SDT's interpolation-based approach, this algorithm uses a neighbor-counting strategy to
    identify points that represent new patterns or state transitions. Suited for cyclical
    data with repeated patterns; automatically detects when the pattern changes or new
    patterns emerge.

    Algorithm Overview:
        The sliding window algorithm maintains a fixed-size buffer of recent embeddings and
        uses backward neighbor counting to determine if a new point should be stored:
        
        1. **Window Buffer**: Maintains last N embedding points in a sliding window
        2. **Backward Search**: For each new point, searches backward through the window
        3. **Neighbor Counting**: Counts consecutive window points (from most recent backward)
           that are similar to the current point
        4. **Novelty Detection**: Stores point if that count is fewer than min_neighbours
        
        Decision Logic:
        - **Novel Point**: If fewer than min_neighbours consecutive similar points found → STORE
          (Point represents new pattern not recently seen)
        - **Redundant Point**: If at least min_neighbours consecutive similar points found → SKIP
          (Point is redundant, similar to recent history)
        - **Break on Dissimilar**: Search stops when encountering a dissimilar point
          (Ensures neighbors are consecutive/recent, not scattered throughout window)

    Tolerance Modes:
        Two metrics are supported for measuring similarity to neighbors:
        
        - **Distance mode**: Uses Euclidean distance between normalized vectors.
          The current point is compared to each window point; a window point is counted as
          a neighbor if distance <= threshold.
          Threshold values: 0.32 (strict) to 0.55 (loose), typical: 0.45
        
        - **Cosine mode**: Uses cosine similarity between vectors. The current point
          is compared to each window point; a window point is counted as a neighbor if
          similarity >= threshold.
          Threshold values: 0.85 (loose) to 0.95 (strict), typical: 0.90

    Configuration Parameters:
        From VideoEmbeddingConfig (e.g. AppConfig.video_embedding): base fields
        (downsample_tolerance_mode, downsample_*_threshold, downsample_max_interval_sec)
        plus downsample_window_size and downsample_min_neighbours.

    State Management:
        - Maintains sliding window across resample() calls for continuous stream processing
        - Window automatically evicts oldest points when full (FIFO behavior)
        - Tracks last saved timestamp for max interval enforcement
        - No force_save() needed (window-based, no pending candidates)

    :ivar deque[tuple[float, np.ndarray, nvSchema.VisionLLM]] _window: Sliding window of recent points
    :ivar int _window_size: Maximum number of points to keep in window
    :ivar int _min_neighbours: Minimum consecutive similar neighbors required to skip point
    :ivar float | None _last_saved_time: Timestamp (milliseconds) of last saved embedding for max-interval checks

    """

    def __init__(self, config: VideoEmbeddingConfig):
        """
        Initialize sliding window downsampler and buffer.

        :param VideoEmbeddingConfig config: Video embedding config with downsampling parameters
        """
        super().__init__(config)
        self._window_size = config.downsample_window_size
        self._min_neighbours = config.downsample_min_neighbours

        self._window: deque[tuple[float, np.ndarray, nvSchema.VisionLLM]] = deque(maxlen=self._window_size)
        self._last_saved_time: float | None = None


    def resample(self, vid_embeddings: list[nvSchema.VisionLLM]) -> list[nvSchema.VisionLLM]:
        """
        Apply sliding window algorithm to downsample video embeddings.

        Processes embeddings sequentially. For each point: if the window is empty or the
        time since the last saved point exceeds max interval, the point is forced-saved;
        otherwise the number of consecutive similar neighbors (window points, from most
        recent backward) is counted, and the point is saved if that count is fewer than
        min_neighbours. After the decision, the point is appended to the window buffer
        (oldest evicted when full). State persists across calls.

        :param list[nvSchema.VisionLLM] vid_embeddings: Video embedding messages to process
        :return: Selected embeddings representing novel or transitional patterns
        :rtype: list[nvSchema.VisionLLM]
        """
        results: list[nvSchema.VisionLLM] = []

        for vid_embed in vid_embeddings:

            if not vid_embed.llm or not vid_embed.llm.visionEmbeddings:
                logger.warning(f"Missing embeddings, skipping record @ {vid_embed.end.ToDatetime()}")
                continue

            embedding_vector = self._normalize(list(vid_embed.llm.visionEmbeddings[0].vector))
            timestamp = vid_embed.end.ToMilliseconds()

            if len(self._window) == 0 \
                or (self._max_interval and self._last_saved_time and (timestamp - self._last_saved_time) >= self._max_interval):
                self._last_saved_time = timestamp
                results.append(vid_embed)

                logger.info(f"Forced save, chunk timestamp={vid_embed.end.ToDatetime()}, last_saved_time updated.")

            elif self._should_save(timestamp, embedding_vector):
                self._last_saved_time = timestamp
                results.append(vid_embed)

            self._window.append((timestamp, embedding_vector, vid_embed))

        return results


    def _should_save(self, timestamp: float, embedding_vector: np.ndarray) -> bool:
        """
        Test if point is novel via consecutive neighbor counting.

        Compares the current point to window entries in reverse order (most recent first).
        Counts how many consecutive window points are similar to the current point; stops
        on first dissimilar point so that only temporally clustered neighbors are counted.
        Returns True (save) if count < min_neighbours, False (skip) if count >= min_neighbours.

        :param float timestamp: Timestamp of current point (milliseconds)
        :param np.ndarray embedding_vector: Normalized embedding vector of current point
        :return: True to save (novel/transitional), False to skip (redundant)
        :rtype: bool
        """
        num_neighbours = 0
        arr = []

        for ts, anchor_v, _ in reversed(self._window):

            if self._tolerance_mode == 'distance':
                distance = self._euclidean_distance(embedding_vector, anchor_v)

                if logger.isEnabledFor(logging.DEBUG):
                    arr.append((datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), distance))

                if distance <= self._distance_threshold:
                    num_neighbours += 1
                else:
                    # num_neighbours = 0  # reset, todo: discuss reset vs break
                    break
            else:
                similarity = self._cosine_similarity(embedding_vector, anchor_v)

                if logger.isEnabledFor(logging.DEBUG):
                    arr.append((datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), similarity))

                if similarity >= self._similarity_threshold:
                    num_neighbours += 1
                else:
                    # num_neighbours = 0  # reset, todo: discuss reset vs break
                    break

            if num_neighbours == self._min_neighbours:
                break

        save = num_neighbours < self._min_neighbours

        logger.debug(f"At={timestamp}, Neighbours={num_neighbours}, Arr={arr}, save={save}")
        return save
