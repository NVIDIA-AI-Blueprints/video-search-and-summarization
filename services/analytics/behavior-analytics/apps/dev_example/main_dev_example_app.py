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
from mdx.analytics.core.stream.state.frame.frame_state_management import FrameStateMgmt
from mdx.analytics.core.utils.schema_util import group_frames_by_sensor_id
from mdx.analytics.core.utils.processing_stats import BatchStats


logger = logging.getLogger(__name__)


class DevExampleApp(BaseApp):
    """
    Development example app that demonstrates FOV count violation and restricted area violation detections.
    
    This app uses the standard FrameStateMgmt including FOV count violation and restricted area violation support.
    
    Configure detection through AppConfig:
    
    Restricted Area Violations:
    - restrictedAreaViolationIncidentEnable: Enable/disable restricted area violations
    - restrictedAreaViolationIncidentThreshold: Minimum duration for incident (default: 0.1)
    - restrictedAreaViolationIncidentExpirationWindow: Max gap between detections (default: 0.5)
    - restrictedAreaViolationIncidentRetentionWindow: State cleanup delay (default: 3)
    
    FOV Count Violations:
    - fovCountViolationIncidentEnable: Enable/disable FOV count violations
    - fovCountViolationIncidentObjectThreshold: Object count threshold (default: 1)
    - fovCountViolationIncidentThreshold: Minimum duration for incident (default: 0.1)
    - fovCountViolationIncidentExpirationWindow: Max gap between detections (default: 0.5)
    - fovCountViolationIncidentObjectType: Object type to monitor (default: "Person")
    """

    def __init__(self, config: AppConfig, calibration_path: str | None) -> None:
        """
        Initialize the DevExampleApp.
        
        :param AppConfig config: Application configuration
        :param str | None calibration_path: Path to calibration file
        """
        super().__init__(config, calibration_path)
        
        # Initialize frame state management
        self.frame_state_mgmt = FrameStateMgmt(self.config)
        
        # Register the processor
        self.register_processor(
            self.read_raw, 
            self.generate_incidents, 
            int(self.config.get_app_config("numWorkersForIncidentGeneration", default_value="1"))
        )
    
    def generate_incidents(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:
        """
        Process frames to detect violations and generate incidents.
        
        :param list[nvSchema.Frame] frames: Raw frames to process
        :param BatchStats stats: Batch processing statistics
        """
        enhanced_frames = [self.calibration.transform_frame(frame) for frame in frames]
        
        frames_map = group_frames_by_sensor_id(enhanced_frames)
        for sensor_id, sensor_frames in frames_map.items():
            self.frame_state_mgmt.update_frames(sensor_id, sensor_frames)
            incidents = self.frame_state_mgmt.get_incidents(sensor_id)
            logger.info(f"Batch {stats.batch_id} - Created a total of {len(incidents)} incident(s) for sensor {sensor_id}")
            self.write_incidents(incidents)


if __name__ == '__main__':
    # Use the standard app runner to launch the application
    from mdx.analytics.core.app.app_runner import run
    
    run(DevExampleApp)
