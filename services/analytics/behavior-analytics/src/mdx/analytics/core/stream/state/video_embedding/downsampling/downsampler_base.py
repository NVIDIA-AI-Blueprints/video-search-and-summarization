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
from abc import ABC, abstractmethod

from mdx.analytics.core.schema.config import VideoEmbeddingConfig
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema

logger = logging.getLogger(__name__)


class EmbeddingDownsampler(ABC):
    """
    Abstract base class for video embedding downsampling algorithms.

    This class provides common functionality for downsampling video embeddings, including
    tolerance metric computation (distance vs cosine similarity), vector normalization,
    and configuration management. Subclasses implement specific compression strategies
    like SDT (interpolation-based) or sliding window (neighbor-based).

    Tolerance Modes:
        Two metrics are supported for measuring similarity between embedding vectors:
        
        - **Distance mode** (recommended): Euclidean distance in normalized vector space.
          Creates spherical tolerance regions. Lower distance = more similar.
          Typical threshold: 0.15 for SDT, 0.45 for sliding window.
        
        - **Cosine mode** (legacy): Cosine similarity between normalized vectors.
          Creates angular tolerance cones. Higher similarity = more similar.
          Typical threshold: 0.91 for SDT, 0.90 for sliding window.

    Configuration:
        Parameters from VideoEmbeddingConfig (e.g. AppConfig.video_embedding):
        downsample_tolerance_mode, downsample_distance_threshold,
        downsample_similarity_threshold, downsample_max_interval_sec.

    :ivar str _tolerance_mode: Active tolerance mode ("distance" or "cosine")
    :ivar int _max_interval: Maximum interval in milliseconds before forced save
    :ivar float _distance_threshold: Distance threshold (if distance mode)
    :ivar float _similarity_threshold: Similarity threshold (if cosine mode)

    """

    def __init__(self, config: VideoEmbeddingConfig):
        """
        Initialize downsampler with tolerance mode and thresholds from configuration.

        :param VideoEmbeddingConfig config: Video embedding config with downsampling parameters
        """
        self._tolerance_mode = config.downsample_tolerance_mode
        self._max_interval = config.downsample_max_interval_sec * 1000

        if self._tolerance_mode == 'cosine':
            self._similarity_threshold = config.downsample_similarity_threshold
        else:
            self._distance_threshold = config.downsample_distance_threshold

        logger.info(f"Downsampler={self.__class__.__name__}, tolerance_mode={self._tolerance_mode}")


    @abstractmethod
    def resample(self, vid_embeddings: list[nvSchema.VisionLLM]) -> list[nvSchema.VisionLLM]:
        """
        Downsample video embeddings using algorithm-specific strategy.

        Subclasses must implement their compression logic here. Algorithm maintains state
        across calls for continuous stream processing.

        :param list[nvSchema.VisionLLM] vid_embeddings: Video embedding messages to process
        :return: Selected subset of embeddings based on algorithm criteria
        :rtype: list[nvSchema.VisionLLM]
        """
        pass


    def force_save(self) -> nvSchema.VisionLLM | None:
        """
        Flush pending state at stream end (optional, algorithm-specific).

        Default implementation returns None. Subclasses override if they maintain
        pending candidates that need flushing (e.g., SDT's candidate point).

        :return: Pending embedding if exists, None otherwise
        :rtype: nvSchema.VisionLLM | None
        """
        return None


    def _normalize(self, embedding_vector: list[float] | np.ndarray) -> np.ndarray:
        """
        Normalize embedding vector to unit length.

        Projects vector onto unit sphere for fair geometric comparison regardless of
        magnitude. Handles zero vectors by returning them unchanged.

        :param list[float] | np.ndarray embedding_vector: Raw embedding vector
        :return: Normalized vector with unit length (or zero vector if input is zero)
        :rtype: np.ndarray
        """
        embedding_vector = np.asarray(embedding_vector, dtype=np.float32, copy=True)
        norm = np.linalg.norm(embedding_vector)
        return embedding_vector if norm < 1e-10 else embedding_vector / norm


    def _euclidean_distance(self, embedding_v1: np.ndarray, embedding_v2: np.ndarray) -> float:
        """
        Compute Euclidean distance between two embedding vectors.

        Measures spatial deviation in vector space. For normalized vectors, distance
        ranges from 0 (identical) to 2 (opposite directions). Lower values indicate
        greater similarity.

        :param np.ndarray embedding_v1: First embedding vector
        :param np.ndarray embedding_v2: Second embedding vector
        :return: Euclidean distance (L2 norm of difference)
        :rtype: float
        """
        return float(np.linalg.norm(embedding_v1 - embedding_v2))


    def _cosine_similarity(self, embedding_v1: np.ndarray, embedding_v2: np.ndarray) -> float:
        """
        Compute cosine similarity between two embedding vectors.

        Measures angular similarity regardless of magnitude. Returns dot product divided
        by product of norms. Ranges from -1 (opposite) to 1 (identical). Higher values
        indicate greater similarity. Handles zero vectors by returning 0.

        :param np.ndarray embedding_v1: First embedding vector
        :param np.ndarray embedding_v2: Second embedding vector
        :return: Cosine similarity, clipped to [-1, 1]
        :rtype: float
        """
        dot_product = np.dot(embedding_v1, embedding_v2)
        norm_v1 = np.linalg.norm(embedding_v1)
        norm_v2 = np.linalg.norm(embedding_v2)
        
        if norm_v1 < 1e-10 or norm_v2 < 1e-10:
            return 0.0
        
        similarity = dot_product / (norm_v1 * norm_v2)
        return np.clip(similarity, -1.0, 1.0)
