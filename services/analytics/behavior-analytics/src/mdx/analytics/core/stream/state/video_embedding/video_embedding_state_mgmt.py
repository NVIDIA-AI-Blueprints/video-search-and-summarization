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
import time
from collections import defaultdict

from mdx.analytics.core.schema.config import VideoEmbeddingConfig
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.stream.state.video_embedding.downsampling.downsampler_base import EmbeddingDownsampler
from mdx.analytics.core.stream.state.video_embedding.downsampling.downsampler_sdt import SDTEmbeddingDownsampler
from mdx.analytics.core.stream.state.video_embedding.downsampling.downsampler_window import SlidingWindowEmbeddingDownsampler

logger = logging.getLogger(__name__)


class VideoEmbeddingStateMgmt:
    """
    Manages per-sensor video embedding downsampling with automatic sensor lifecycle management.

    This class maintains separate downsampler instances for each sensor, enabling independent
    compression streams. It handles sensor creation on first access, automatic purging of
    inactive sensors, and pending state flushing at shutdown. Supports both SDT and sliding
    window downsampling algorithms.

    Configuration:
        Supplied via VideoEmbeddingConfig (e.g. from AppConfig.video_embedding):
        enable_downsampling (config name enableDownsampling), downsampler_type, sensor_ttl_sec, and downsampler-specific parameters
        (see downsampler_base and VideoEmbeddingConfig).

    Sensor Lifecycle:
        - **Creation**: Downsampler created on first embedding for sensor (lazy initialization)
        - **Updates**: Each embedding batch updates sensor's last-seen timestamp
        - **Purging**: Sensors inactive beyond TTL are automatically removed
        - **Shutdown**: Pending candidates flushed via get_pending_video_embeddings()

    :ivar VideoEmbeddingConfig _config: Video embedding configuration
    :ivar str _downsampler_type: Downsampler algorithm ("sdt" or "window")
    :ivar int _sensor_ttl: Sensor time-to-live in seconds
    :ivar dict[str, EmbeddingDownsampler] _downsamplers: Per-sensor downsampler instances
    :ivar dict[str, int] _sensor_last_updated: Per-sensor last update timestamps (seconds, from time.time())

    """

    def __init__(self, config: VideoEmbeddingConfig) -> None:
        """
        Initialize video embedding state manager with configuration.

        Creates empty sensor tracking structures; downsamplers are created lazily on first use per sensor.

        :param VideoEmbeddingConfig config: Video embedding config (e.g. from AppConfig.video_embedding)
        """
        self._config = config
        self._enable_downsampling = self._config.enable_downsampling
        self._downsampler_type = self._config.downsampler_type
        self._sensor_ttl = self._config.sensor_ttl_sec

        self._downsamplers: dict[str, EmbeddingDownsampler] = defaultdict(self._get_downsampler)
        self._sensor_last_updated: dict[str, int] = {}

        logger.info(f"Embed filtering mode - {'enabled' if self._enable_downsampling else 'pass-through'}")


    def update_video_embeddings(
        self,
        sensor_id: str,
        video_embeddings: list[nvSchema.VisionLLM]
    ) -> list[nvSchema.VisionLLM]:
        """
        Process video embeddings for a sensor through the processing pipeline.

        Note: Currently applies downsampling to reduce data volume. Additional processing stages
        may be added in the future. Manages sensor lifecycle (updates timestamp, purges
        inactive sensors).

        :param str sensor_id: Unique sensor identifier
        :param list[nvSchema.VisionLLM] video_embeddings: Raw video embedding messages
        :return: Processed embeddings after pipeline stages
        :rtype: list[nvSchema.VisionLLM]
        """
        if not self._enable_downsampling:
            return video_embeddings

        downsampled_vid_embeddings = self._downsample(sensor_id, video_embeddings) 
        self._manage_sensors(sensor_id)
        return downsampled_vid_embeddings


    def get_pending_video_embeddings(self) -> list[nvSchema.VisionLLM]:
        """
        Flush pending candidates from all sensor downsamplers at shutdown.

        Calls force_save() on each downsampler to retrieve any pending state. Critical
        for SDT algorithm which holds a candidate point. Window algorithm returns None.

        :return: List of pending embeddings across all sensors
        :rtype: list[nvSchema.VisionLLM]
        """

        pending = []

        if self._enable_downsampling:
            for downsampler in self._downsamplers.values():
                if (candidate := downsampler.force_save()):
                    pending.append(candidate)

        return pending


    def _manage_sensors(self, sensor_id: str) -> None:
        """
        Update sensor timestamp and purge inactive sensors beyond TTL.

        Uses wall clock time (not sensor/stream time) for lifecycle management. Removes
        downsampler and tracking state for sensors inactive beyond configured TTL.

        :param str sensor_id: Sensor being updated
        """
        curr_time_sec = int(time.time())
        self._sensor_last_updated[sensor_id] = curr_time_sec    # use clock time for purging, not the sensor/stream time

        to_purge = []

        for s_id, last_updated in self._sensor_last_updated.items():
            if (elapsed := (curr_time_sec - last_updated)) > self._sensor_ttl:
                logger.info(f"No chunks received for sensor {s_id} in {elapsed}s, purging state.")
                to_purge.append(s_id)

        for s_id in to_purge:
            self._downsamplers.pop(s_id, None)
            self._sensor_last_updated.pop(s_id, None)                


    def _downsample(self, sensor_id: str, video_embeddings: list[nvSchema.VisionLLM]) -> list[nvSchema.VisionLLM]:
        """
        Apply downsampling to filtered embeddings and manage sensor lifecycle.

        Retrieves or creates sensor-specific downsampler, filters invalid embeddings,
        applies algorithm, and updates sensor timestamps with purge check.

        :param str sensor_id: Sensor identifier
        :param list[nvSchema.VisionLLM] video_embeddings: Raw embeddings to downsample
        :return: Downsampled embeddings
        :rtype: list[nvSchema.VisionLLM]
        """
        downsampler = self._downsamplers[sensor_id]
        video_embeddings = [ vid_embed for vid_embed in video_embeddings if vid_embed.llm and vid_embed.llm.visionEmbeddings ]

        return downsampler.resample(video_embeddings) if len(video_embeddings) > 0 else []


    def _get_downsampler(self) -> SDTEmbeddingDownsampler | SlidingWindowEmbeddingDownsampler:
        """
        Factory method for creating downsampler instances (used by defaultdict).

        Creates downsampler based on configured type. Called automatically when new
        sensor accessed via dictionary.

        :return: New downsampler instance (SDT or Window)
        :rtype: SDTEmbeddingDownsampler | SlidingWindowEmbeddingDownsampler
        """
        return SlidingWindowEmbeddingDownsampler(self._config) if self._downsampler_type == 'window' \
            else SDTEmbeddingDownsampler(self._config)
