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
from mdx.analytics.core.stream.state.frame.frame_state_management import FrameStateMgmt
from mdx.analytics.core.stream.state.video_embedding.video_embedding_state_mgmt import VideoEmbeddingStateMgmt
from mdx.analytics.core.utils.schema_util import group_frames_by_sensor_id, group_video_embeddings_by_sensor_id, messages_to_map, nv_frame_to_messages
from mdx.analytics.core.utils.processing_stats import BatchStats


logger = logging.getLogger(__name__)


class SearchAndAlertsApp(BaseApp):
    """
    Search and Alerts app that combines incident detection with behavior creation and video embedding processing.

    Runs three processing paths in parallel:

    **Incident path** (raw frames → incidents):
        Read raw frames, filter by sensor, transform via calibration, write enhanced frames,
        update per-sensor frame state, detect violations (proximity, restricted area, confined
        area, FOV count), write incidents.

    **Behavior path** (raw frames → behaviors):
        Read raw frames, filter by sensor, convert to messages, transform via calibration,
        process the batch through behavior state management, write its output behaviors.

    **Embedding path** (video embeddings → output):
        Read video embeddings, group by sensor ID, process per-sensor via video embedding
        state manager, write processed embeddings.

    Configuration (see AppConfig):
        - numWorkersForIncidentGeneration: Worker count for incident pipeline (default: "1")
        - numWorkersForBehaviorCreation: Worker count for behavior pipeline (default: "1")
        - numWorkersForEmbedFiltering: Worker count for embedding pipeline (default: "1")
        - Plus standard incident toggles (proximityIncidentEnable, restrictedAreaIncidentEnable, etc.)

    :ivar FrameStateMgmt frame_state_mgmt: Per-sensor frame state manager for incident detection
    :ivar StateMgmtE state_mgmt: Per-sensor behavior state manager
    :ivar VideoEmbeddingStateMgmt _vid_embed_state_mgmt: Per-sensor video embedding state manager
    """

    def __init__(self, config: AppConfig, calibration_path: str | None) -> None:
        """
        Initialize the SearchAndAlertsApp.

        :param AppConfig config: Application configuration
        :param str | None calibration_path: Path to calibration file
        """
        super().__init__(config, calibration_path)

        self.frame_state_mgmt = FrameStateMgmt(self.config)
        self.state_mgmt = StateMgmtE(self.config, self.calibration)
        self._vid_embed_state_mgmt = VideoEmbeddingStateMgmt(self.config.video_embedding)

        self.register_processor(
            self.read_raw,
            self.generate_incidents,
            int(self.config.get_app_config("numWorkersForIncidentGeneration", "0"))
        )
        self.register_processor(
            self.read_raw,
            self.create_behaviors,
            int(self.config.get_app_config("numWorkersForBehaviorCreation", "0"))
        )
        self.register_processor(
            self.read_embed,
            self.process_chunk_embeddings,
            int(self.config.get_app_config("numWorkersForEmbedFiltering", "0"))
        )

    def generate_incidents(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:
        """
        Process frames to detect violations and generate incidents.

        :param list[nvSchema.Frame] frames: Raw frames to process
        :param BatchStats stats: Batch processing statistics
        """
        enhanced_frames = [self.calibration.transform_frame(frame) for frame in frames]
        self.write_frames(enhanced_frames)

        frames_map = group_frames_by_sensor_id(enhanced_frames)
        for sensor_id, sensor_frames in frames_map.items():
            self.frame_state_mgmt.update_frames(sensor_id, sensor_frames)
            incidents = self.frame_state_mgmt.get_incidents(sensor_id)
            logger.info(f"Batch {stats.batch_id} - Created a total of {len(incidents)} incident(s) for sensor {sensor_id}")
            self.write_incidents(incidents)

    def create_behaviors(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:
        """
        Build behaviors from a batch of raw frames.

        Filters frames by sensor ID, converts frames to messages (using state_mgmt_filter),
        transforms messages via calibration, groups them by track key, and processes the whole
        batch in one call. ``BehaviorBatch.behaviors_to_write`` is written to the behavior stream.

        :param list[nvSchema.Frame] frames: Raw frame batch from read_raw
        :param BatchStats stats: Batch processing statistics (e.g. batch_id)
        """
        batch_messages = [
            msg
            for frame in frames
            for msg in nv_frame_to_messages(frame, object_filter=self.config.state_mgmt_filter)
        ]

        if not batch_messages:
            logger.debug(f"Batch {stats.batch_id} - No messages to process in batch.")

        else:
            logger.info(f"Batch {stats.batch_id} - Transformed {len(frames)} frame(s) to {len(batch_messages)} message(s)")

            updated_messages = [self.calibration.transform(msg) for msg in batch_messages]
            updated_messages_map = messages_to_map(updated_messages)

            batch = self.state_mgmt.process_batch(updated_messages_map)

            logger.info(f"Batch {stats.batch_id} - Created a total of {len(batch.active_behaviors)} behavior(s), "
                        f"writing {len(batch.behaviors_to_write)}")

            self.write_behaviors(batch.behaviors_to_write)

    def process_chunk_embeddings(self, video_embeddings: list[nvSchema.VisionLLM], stats: BatchStats) -> None:
        """
        Process a batch of video embeddings and write to the embed-filtered output stream.

        Groups embeddings by sensor ID, runs each sensor through the video embedding state
        manager (downsampling / filtering), collects processed results, and writes them.

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

        Fetches any pending video embeddings from the per-sensor state manager, writes them
        via write_embed_filtered, writes behaviors still held back by emit-once mode,
        then calls the base close().
        """
        pending = self._vid_embed_state_mgmt.get_pending_video_embeddings()

        logger.info(f"Flushing any pending video embeddings - found {len(pending)}.")
        self.write_embed_filtered(pending)

        pending_behaviors = self.state_mgmt.flush_behaviors()

        if pending_behaviors:
            logger.info(f"Flushing {len(pending_behaviors)} pending behavior(s) before shutdown.")
            self.write_behaviors(pending_behaviors)

        super().close()


if __name__ == '__main__':
    from mdx.analytics.core.app.app_runner import run

    run(SearchAndAlertsApp)
