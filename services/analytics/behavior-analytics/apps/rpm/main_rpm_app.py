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
from mdx.analytics.core.schema.models import Behavior
from mdx.analytics.core.stream.state.behavior.state_management import StateMgmt
from mdx.analytics.core.utils.processing_stats import BatchStats
from mdx.analytics.core.utils.schema_util import messages_to_map, nv_frame_to_messages
from mdx.analytics.core.transform.detection.anomaly_action_detection import AnomalyActionDetection


logger = logging.getLogger(__name__)


class RPMApp(BaseApp):
    """
    Controller module for Behavior Analytics of RPM

    :param AppConfig config: app config
    :param str calibration_path: path to the calibration file in JSON format
    ::

        rpm_app = RPMApp(config)
    """

    def __init__(
        self,
        config: AppConfig,
        calibration_path: str | None
    ) -> None:
        super().__init__(config, calibration_path)

        self.state_mgmt = StateMgmt(self.config, self.calibration)
        self.anomaly_action_detector = AnomalyActionDetection(self.config)
        
        num_workers = int(self.config.get_app_config("numWorkersForBehaviorCreation", "1"))
        self.register_processor(self.read_raw, self.process, num_workers)

    def compact_behaviors(self, behaviors: list[Behavior]) -> list[Behavior]:
        """
        Filter information of behaviors to only include main information of objects.
        :param list[Behavior] behaviors: list of behaviors
        :return: list of behaviors
        ::

            behaviors = app.compact_behaviors(behaviors)
        """
        for behavior in behaviors:
            behavior.locations = []
            behavior.smoothLocations = []
            behavior.speedOverTime = []
        return behaviors

    def process(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:
        """
        Get messages from protobuf frames.
        Group messages by key(sensorId + "#-#"" + objectId), and get behaviors from grouped messages.
        Output behaviors to kafka behavior topic.

        :param list[nvSchema.Frame] frames: list of protobuf frames
        :param BatchStats stats: batch stats
        :return: None
        ::

            app.process(frames, stats)
        """
       
        batch_messages = [msg for frame in frames for msg in nv_frame_to_messages(frame)]
        
        updated_messages_map = messages_to_map(batch_messages)
        
        if not batch_messages:
            logger.debug(f"[Batch {stats.batch_id}] - No messages to process in batch.")
        else:
            logger.info(f"[Batch {stats.batch_id}] Transformed {len(frames)} frame(s) to {len(batch_messages)} message(s)")

            batch = self.state_mgmt.process_batch(updated_messages_map)

            logger.info(f"[Batch {stats.batch_id}] created a total of {len(batch.active_behaviors)} behavior(s), "
                        f"writing {len(batch.behaviors_to_write)}")

            # Both detectors enrich the behaviors in place, so the edits reach whatever is written.
            incidents, _ = self.anomaly_action_detector.detect_batch(batch.active_behaviors, frames)
            self.compact_behaviors(batch.active_behaviors)
            for incident in incidents:
                logger.info(f"[Batch {stats.batch_id}] - Incident: {incident.objectIds}, {incident.analyticsModule.id}")

            self.anomaly_action_detector.update_live_object(self.state_mgmt.live_object_ids())
            self.write_behaviors(batch.behaviors_to_write)
            self.write_incidents(incidents)

    def close(self) -> None:
        """
        Write behaviors still held back by emit-once mode, then release resources.

        :return: None
        """
        pending = self.state_mgmt.flush_behaviors()

        if pending:
            logger.info(f"Flushing {len(pending)} pending behavior(s) before shutdown.")
            self.write_behaviors(pending)

        super().close()


if __name__ == '__main__':

    from mdx.analytics.core.app.app_runner import run

    run(RPMApp)

