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

from datetime import timedelta
import logging

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.models import AmrState, FrameState, RoiState
from mdx.analytics.core.transform.calibration.calibration_e import CalibrationE
from mdx.analytics.core.utils.schema_util import get_timestamp_from_proto_ts
from mdx.analytics.core.utils.util import str_to_bool

logger = logging.getLogger(__name__)


class AmrStateMgmt:
    """
    Module to manage AMR states.

    This class provides functionality for:
    - AMR state management and tracking
    - AMR history maintenance
    - AMR sorting and filtering
    - Sensor-specific AMR configuration

    :ivar AppConfig config: Configuration object for the application.
    :ivar CalibrationE calibration: Calibration object for sensor configuration.
    :ivar dict[str, FrameState] states: Dictionary to store frame states.

    Examples::
        >>> config = AppConfig()
        >>> frame_manager = FrameStateMgmt(config)
        >>> print(f"Initialized frame state management with {len(frame_manager.state)} states")
    """

    def __init__(self, config: AppConfig, calibration: CalibrationE) -> None:
        self.config: AppConfig = config
        self.calibration: CalibrationE = calibration
        self.states: dict[str, FrameState] = dict()

    def _get_ocr_id(self, object_id: str, frame: nvSchema.Frame) -> str:
        for object in frame.objects:
            if object.id == object_id:
                return object.info.get("ocrId", "")
        return ""

    def update_amr_states(self, sensor_id: str, frames: list[nvSchema.Frame]) -> list[AmrState]:
        """
        Update frame state with new frames and maintain history.

        This method:
        1. Sorts frames by timestamp
        2. Combines new frames with existing frame history
        3. Updates frame state for the sensor

        :param str sensor_id: Sensor ID for frame tracking.
        :param list[nvSchema.Frame] frames: List of frames to process.
        :return list[AmrState]: List of AMR states that have changed.

        Examples::
            >>> amr_manager = AmrStateMgmt(config)
            >>> frames = [nvSchema.Frame(timestamp=Timestamp(seconds=1, nanos=0))]
            >>> amr_manager.update_amr_states("sensor1", frames)
        """
        if not frames:
            return []

        # Filter frames by time threshold to handle perception layer restarts with duplicate IDs
        sorted_frames = sorted(frames, key=lambda x: get_timestamp_from_proto_ts(x.timestamp))
        time_threshold = get_timestamp_from_proto_ts(sorted_frames[-1].timestamp) - timedelta(seconds=self.config.behavior_water_mark)
        filtered_frames = [
            frame
            for frame in sorted_frames
            if get_timestamp_from_proto_ts(frame.timestamp) >= time_threshold and get_timestamp_from_proto_ts(frame.timestamp) > self.config.behavior_time_threshold
        ]
        if not filtered_frames:
            return []
        
        last_frame = filtered_frames[-1]
        sensor_state = self.states.get(sensor_id, FrameState(id=sensor_id))

        # roi to object types map
        restricted_types_map = self.calibration.roi_restricted_types(sensor_id)

        # roi to restricted state
        roi_to_restricted_state = {}
        for roi in last_frame.rois:
            restricted_types = restricted_types_map.get(roi.id, [])
            if roi.type in restricted_types:
                roi_to_restricted_state[roi.id] = not str_to_bool(roi.info.get("restrictedAreaViolation", "false"))

        for roi in last_frame.rois:
            if roi.type == "AMR":
                for object_id in roi.objectIds:
                    ocr_id = self._get_ocr_id(object_id, last_frame)
                    state = sensor_state.amr_states.get(ocr_id, AmrState(
                        id=ocr_id,
                        roi_id=roi.id,
                        sensor_id=sensor_id,
                        object_id=object_id,
                        mute=True,
                        mute_state_changed=False
                    ))
                    restricted_state = roi_to_restricted_state.get(roi.id, True)
                    if state.mute != restricted_state:
                        state.mute = restricted_state
                        state.mute_state_changed = True
                    sensor_state.amr_states[ocr_id] = state

        self.states[sensor_id] = sensor_state
        states = [ state for state in list(sensor_state.amr_states.values()) if state.mute_state_changed ]
        # Reset the changed flag after returning
        for state in states:
            state.mute_state_changed = False
        return states

    def update_roi_states(self, sensor_id: str, frames: list[nvSchema.Frame]) -> list[RoiState]:
        """
        Update roi state with new frames and maintain history.

        This method:
        1. Sorts frames by timestamp
        2. Combines new frames with existing frame history
        3. Updates roi state for the sensor

        :param str sensor_id: Sensor ID for frame tracking.
        :param list[nvSchema.Frame] frames: List of frames to process.
        :return list[RoiState]: List of ROI states that have changed.

        Examples::
            >>> manager = AmrStateMgmt(config)
            >>> frames = [nvSchema.Frame(timestamp=Timestamp(seconds=1, nanos=0))]
            >>> manager.update_roi_states("sensor1", frames)
        """
        if not frames:
            return []

        # Filter frames by time threshold to handle perception layer restarts with duplicate IDs
        sorted_frames = sorted(frames, key=lambda x: get_timestamp_from_proto_ts(x.timestamp))
        time_threshold = get_timestamp_from_proto_ts(sorted_frames[-1].timestamp) - timedelta(seconds=self.config.behavior_water_mark)
        filtered_frames = [
            frame
            for frame in sorted_frames
            if get_timestamp_from_proto_ts(frame.timestamp) >= time_threshold and get_timestamp_from_proto_ts(frame.timestamp) > self.config.behavior_time_threshold
        ]
        if not filtered_frames:
            return []
        
        last_frame = filtered_frames[-1]
        sensor_state = self.states.get(sensor_id, FrameState(id=sensor_id))

        # roi to object types map
        restricted_types_map = self.calibration.roi_restricted_types(sensor_id)

        # roi to restricted state
        roi_to_restricted_state = {}
        for roi in last_frame.rois:
            restricted_types = restricted_types_map.get(roi.id, [])
            if roi.type in restricted_types:
                roi_to_restricted_state[roi.id] = not str_to_bool(roi.info.get("restrictedAreaViolation", "false"))

        for roi in self.calibration.sensor_map[sensor_id].rois:
            state = sensor_state.roi_states.get(roi.id, RoiState(
                id=roi.id,
                sensor_id=sensor_id,
                mute=True,
                mute_state_changed=False
            ))
            restricted_state = roi_to_restricted_state.get(roi.id, True)
            if state.mute != restricted_state:
                state.mute = restricted_state
                state.mute_state_changed = True
            sensor_state.roi_states[roi.id] = state

        self.states[sensor_id] = sensor_state
        states = [ state for state in list(sensor_state.roi_states.values()) if state.mute_state_changed ]
        # Reset the changed flag after returning
        for state in states:
            state.mute_state_changed = False
        return states
