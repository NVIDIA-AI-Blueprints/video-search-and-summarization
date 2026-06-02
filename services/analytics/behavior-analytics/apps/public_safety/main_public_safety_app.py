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
from mdx.analytics.core.stream.state.behavior.state_management_e import StateMgmtEWithTripwire
from mdx.analytics.core.stream.state.frame.frame_state_management import FrameStateMgmt
from mdx.analytics.core.transform.event.roi_event import ROIEvent
from mdx.analytics.core.transform.event.tripwire_event import TripwireEvent
from mdx.analytics.core.utils.schema_util import messages_to_map, nv_frame_to_messages, group_frames_by_sensor_id
from mdx.analytics.core.utils.processing_stats import BatchStats


logger = logging.getLogger(__name__)


class PublicSafetyApp(BaseApp):

    def __init__(self, config: AppConfig, calibration_path: str | None) -> None:

        super().__init__(config, calibration_path)
        self.state_mgmt = StateMgmtEWithTripwire(self.config, self.calibration)
        self.frame_state_mgmt = FrameStateMgmt(self.config)
        self.roi_event = ROIEvent(self.config, self.calibration)
        self.tripwire_event = TripwireEvent(self.config, self.calibration)

        self.register_processor(self.read_raw, self.create_behaviors, int(self.config.get_app_config("numWorkersForBehaviorCreation")))
        self.register_processor(self.read_raw, self.enhance_frames, int(self.config.get_app_config("numWorkersForFrameEnhancement")))

    def enhance_frames(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:

        frames = self.calibration.filter_frames_by_sensor_id(frames)
        enhanced_frames = [ self.calibration.transform_frame(frame) for frame in frames ]
        self.write_frames(enhanced_frames)

        frames_map = group_frames_by_sensor_id(enhanced_frames)
        for sensor_id, sensor_frames in frames_map.items():
            self.frame_state_mgmt.update_frames(sensor_id, sensor_frames)
            incidents = self.frame_state_mgmt.get_incidents(sensor_id)
            logger.info(f"Batch {stats.batch_id} - Created a total of {len(incidents)} incident(s) for sensor {sensor_id}")
            self.write_incidents(incidents)

    def create_behaviors(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:

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
            events = []

            for sensor_id, msgs in updated_messages_map.items():

                behavior, trip = self.state_mgmt.update_behavior(message_key=sensor_id, messages=msgs)

                if behavior:
                    behaviors.append(behavior)

                if trip:
                    events.extend(self.tripwire_event.get_events(trip))
                    events.extend(self.roi_event.get_events(trip))

            logger.info(f"Batch {stats.batch_id} - Created a total of {len(behaviors)} behavior(s)")
            logger.info(f"Batch {stats.batch_id} - Created a total of {len(events)} event(s)")

            self.write_behaviors(behaviors)
            self.write_events(events)


if __name__ == '__main__':

    from mdx.analytics.core.app.app_runner import run

    run(PublicSafetyApp)
