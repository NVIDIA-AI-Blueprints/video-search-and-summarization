# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
from mdx.analytics.core.transform.calibration.calibration_dynamic import CalibrationType
from mdx.analytics.core.schema.models import Behavior
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.stream.state.behavior.state_management import StateMgmt
from mdx.analytics.core.stream.state.behavior.state_management_i import StateMgmtI
from mdx.analytics.core.transform.detection.collision_detection import CollisionDetection
from mdx.analytics.core.utils.anomaly_util import AnomalyDetector
from mdx.analytics.core.utils.crs import CoordinateReferenceSystem
from mdx.analytics.core.utils.processing_stats import BatchStats
from mdx.analytics.core.utils.schema_util import messages_to_map, nv_frame_to_messages, group_messages_by_frame_id

# logging.getLogger("smart_city_app").setLevel(logging.INFO)
logger = logging.getLogger(__name__)


class SmartCityApp(BaseApp):

    def __init__(
        self,
        config: AppConfig,
        calibration_path: str | None
    ) -> None:

        super().__init__(config, calibration_path)

        # Determine calibration type from file (defaults to IMAGE when None/unknown)
        self.calibration_type = self.calibration.get_calibration_type(calibration_path)
        self.init_coordinate_system(config)

        self.anomaly_detector = AnomalyDetector(self.config, self.calibration_type)

        collision_detection_config = self.config.get_sensor_anomaly_config().collisionDetection
        if collision_detection_config.enable:
            self.collision_detection = CollisionDetection(collision_detection_config, self.calibration_type)
        else:
            self.collision_detection = None

        self.register_processor(self.read_raw, self.process, int(self.config.get_app_config("numWorkersForBehaviorCreation")))
        if self.config.inference.enable:
            self.register_processor(self.read_behavior, self.process_behavior_clustering, int(self.config.get_app_config("numWorkersForBehaviorClustering")))

    def init_coordinate_system(self, config: AppConfig) -> None:
        """
        Initialize state management based on calibration type.
        Calibration types: IMAGE, CARTESIAN, GEO.
        IMAGE: State management is in image space.
        CARTESIAN: Not supported in this build.
        GEO: State management is in geo space.

        :param AppConfig config: Configuration object for the application.
        :return: None

        Examples::
            >>> def __init__(self, config: AppConfig, calibration_path: str | None) -> None:
            >>>     super().__init__(config, calibration_path)
            >>>     self.init_coordinate_system(config) 
            >>>     print(f"Initialized coordinate system and state management")
            >>> smart_city_app = SmartCityApp(config, calibration_path)
        """
        calibration_type = self.calibration_type
        if calibration_type == CalibrationType.IMAGE:
            self.state_mgmt = StateMgmtI(self.config, self.calibration)  # type: ignore
        elif calibration_type == CalibrationType.GEO:
            self.state_mgmt = StateMgmt(self.config, self.calibration)  # type: ignore
        else:
            raise NotImplementedError("CARTESIAN not supported in this build")
        self.crs = CoordinateReferenceSystem(config.coordinateReferenceSystem)


    def process(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:

        batch_frames_filtered = self.calibration.filter_frames_by_sensor_id(frames) 

        batch_messages = [ msg for frame in batch_frames_filtered for msg in nv_frame_to_messages(frame) ]

        if not batch_messages:
            logger.debug(f"[Batch {stats.batch_id}] - No messages to process in batch.")
        else:
            logger.info(f"[Batch {stats.batch_id}] Transformed {len(frames)} frame(s) to {len(batch_messages)} message(s)")

            updated_messages = [ self.calibration.transform(msg) for msg in batch_messages ]
            if self.calibration_type == CalibrationType.GEO:
                updated_messages = self.calibration.filter_messages_by_roi(updated_messages) 
            elif self.calibration_type == CalibrationType.IMAGE:
                logger.info("Messages won't be filtered by ROI in Image coordinate system")

            if self.collision_detection:
                transformed_frames = group_messages_by_frame_id(updated_messages)
            else:
                transformed_frames = {}

            updated_messages_map = messages_to_map(updated_messages)
            behaviors = []
    
            for sensor_id, msgs in updated_messages_map.items():
                if (behavior := self.state_mgmt.update_behavior(message_key=sensor_id, messages=msgs, crs=self.crs)):
                    behaviors.append(behavior)

            logger.info(f"[Batch {stats.batch_id}] created a total of {len(behaviors)} behavior(s)")

            self.anomaly_detector.stop_detection.update_frames(transformed_frames)
            potential_collisions, anomalies = self.anomaly_detector.detect_batch(behaviors, self.crs)
            logger.info(f"[Batch {stats.batch_id}] a total of {len(anomalies)} anomaly(s) detected")

            collision_incidents = []
            if self.collision_detection:
                self.collision_detection.update_behaviors(behaviors)
                self.collision_detection.update_frames(transformed_frames)
                for object_id, sensor_id, behavior, trigger_modules in potential_collisions:
                    self.collision_detection.update_potential_collision(object_id, sensor_id, behavior, trigger_modules)
                
                collision_incidents = self.collision_detection.get_collision_alerts()

            if self.collision_detection and collision_incidents:
                logger.info(f"[Batch {stats.batch_id}] a total of {len(collision_incidents)} incident(s) detected")

            live_objects = list(self.state_mgmt.state.keys())
            self.anomaly_detector.stop_detection.update_live_object(live_objects)

            self.write_behaviors(behaviors)
            self.write_anomalies(anomalies)
            self.write_incidents([ incident for incident, _ in collision_incidents ])


    def process_behavior_clustering(self, behaviors: list[Behavior], _: BatchStats) -> None:

        self.write_behaviors_with_clustering(behaviors)


if __name__ == '__main__':

    from mdx.analytics.core.app.app_runner import run

    run(SmartCityApp)
