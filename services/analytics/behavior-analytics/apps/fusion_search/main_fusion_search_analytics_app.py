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

from mdx.analytics.core.app.app_base import BaseApp
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.stream.state.behavior.state_management_e import StateMgmtE
from mdx.analytics.core.stream.state.video_embedding.video_embedding_state_mgmt import VideoEmbeddingStateMgmt
from mdx.analytics.core.utils.schema_util import messages_to_map, nv_frame_to_messages
from mdx.analytics.core.utils.processing_stats import BatchStats
from mdx.analytics.core.utils.schema_util import group_video_embeddings_by_sensor_id

logger = logging.getLogger(__name__)


class FusionSearchAnalyticsApp(BaseApp):
    """
    Fusion search analytics application for behavior creation and video embedding processing.

    Runs two processing paths in parallel:

    **Behavior path** (raw frames → behaviors):
        Read raw frames, filter by sensor, convert to messages, transform via calibration,
        update per-sensor behavior state, write behaviors to output.

    **Embedding path** (video embeddings → output):
        Read video embeddings. When enableDownsampling is True: group by sensor ID,
        process per-sensor via video embedding state manager, write processed embeddings.
        When False: pass embeddings through unchanged and write. Both modes write to
        the embed-filtered output stream.

    Configuration (see AppConfig):
        - enableDownsampling: If True, run downsampling on embeddings; if False, pass-through (default: config-dependent)
        - numWorkersForBehaviorCreation: Worker count for the behavior pipeline (default: "1")
        - numWorkersForEmbedFiltering: Worker count for the embedding pipeline (default: "1")
        - Plus sensor-specific and state management parameters (e.g. state_mgmt_filter).

    :ivar StateMgmtE state_mgmt: Per-sensor behavior state manager
    :ivar VideoEmbeddingStateMgmt _vid_embed_state_mgmt: Per-sensor video embedding state manager
    :ivar VideoEmbeddingConfig _video_embed_config: Video embedding config (enableDownsampling, downsampler, etc.)
    """

    def __init__(self, config: AppConfig, calibration_path: str | None) -> None:
        """
        Initialize fusion search analytics application.

        Reads embed-filtering settings from config.video_embedding (VideoEmbeddingConfig).
        Registers two processors: behavior creation (read_raw → create_behaviors) and embedding
        handling (read_embed → process_chunk_embeddings).

        :param AppConfig config: Application configuration (video_embedding supplies embed-filtering params)
        :param str | None calibration_path: Optional path to calibration data
        """
        super().__init__(config, calibration_path)
        self.state_mgmt = StateMgmtE(self.config, self.calibration)
        self._vid_embed_state_mgmt: VideoEmbeddingStateMgmt = VideoEmbeddingStateMgmt(self.config.video_embedding)

        self.register_processor(
            self.read_raw,
            self.create_behaviors,
            int(self.config.get_app_config("numWorkersForBehaviorCreation", "1"))
        )

        self.register_processor(
            self.read_embed,
            self.process_chunk_embeddings,
            int(self.config.get_app_config("numWorkersForEmbedFiltering", "1"))
        )


    def create_behaviors(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:
        """
        Build behaviors from a batch of raw frames.

        Filters frames by sensor ID, converts frames to messages (using state_mgmt_filter),
        transforms messages via calibration, groups by sensor, and updates behavior state
        per sensor. Non-None behaviors are written to the behavior output stream.

        :param list[nvSchema.Frame] frames: Raw frame batch from read_raw
        :param BatchStats stats: Batch processing statistics (e.g. batch_id)
        """
        frames = self.calibration.filter_frames_by_sensor_id(frames)
        batch_messages = [
            msg
            for frame in frames
            for msg in nv_frame_to_messages(frame, object_filter = self.config.state_mgmt_filter)
        ]

        if not batch_messages:
            logger.debug(f"Batch {stats.batch_id} - No messages to process in batch.")

        else:
            logger.info(f"Batch {stats.batch_id} - Transformed {len(frames)} frame(s) to {len(batch_messages)} message(s)")

            updated_messages = [ self.calibration.transform(msg) for msg in batch_messages ]
            updated_messages_map = messages_to_map(updated_messages)

            behaviors = []

            for sensor_id, msgs in updated_messages_map.items():

                behavior = self.state_mgmt.update_behavior(message_key=sensor_id, messages=msgs)

                if behavior:
                    behaviors.append(behavior)

            logger.info(f"Batch {stats.batch_id} - Created a total of {len(behaviors)} behavior(s)")

            self.write_behaviors(behaviors)


    def process_chunk_embeddings(self, video_embeddings: list[nvSchema.VisionLLM], stats: BatchStats) -> None:
        """
        Process a batch of video embeddings and write to the embed-filtered output stream.

        When _video_embed_config.enable_downsampling is True: groups embeddings by sensor ID, runs each
        sensor through the video embedding state manager (downsampling / filtering), collects processed results, and writes them. When False: passes
        the batch through unchanged and writes it.

        :param list[nvSchema.VisionLLM] video_embeddings: Raw video embedding batch from read_embed
        :param BatchStats stats: Batch processing statistics (e.g. batch_id)
        """
        results = []

        for sensor_id, vid_embeddings in group_video_embeddings_by_sensor_id(video_embeddings).items():

            processed = self._vid_embed_state_mgmt.update_video_embeddings(sensor_id, vid_embeddings)
            results.extend(processed)

            logger.info(f"Batch {stats.batch_id}, sensor {sensor_id} - Video embeddings: received={len(vid_embeddings)}, final={len(processed)}")

        self.write_embed_filtered(results)


    def close(self) -> None:
        """
        Shutdown handler to flush pending state before exit.

        If _video_embed_config.enable_downsampling is True: fetches any pending video embeddings from the
        per-sensor state manager, writes them via write_embed_filtered.
        Then calls the base close().
        """
        pending = self._vid_embed_state_mgmt.get_pending_video_embeddings()

        logger.info(f"Flushing any pending video embeddings - found {len(pending)}.")
        self.write_embed_filtered(pending)

        super().close()


if __name__ == '__main__':
    # Use the standard app runner to launch the application
    from mdx.analytics.core.app.app_runner import run
    
    run(FusionSearchAnalyticsApp)
