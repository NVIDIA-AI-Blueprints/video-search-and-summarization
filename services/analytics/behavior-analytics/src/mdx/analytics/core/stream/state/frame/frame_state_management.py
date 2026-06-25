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

from typing import Any
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
import json

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.models import IncidentState, FrameState, Incident, Place, IncidentCategory
from mdx.analytics.core.utils.schema_util import get_timestamp_from_proto_ts
from mdx.analytics.core.utils.util import str_to_bool, convert_datetime_to_iso_8601_with_z_suffix

logger = logging.getLogger(__name__)

# Internal tracking field that should be excluded from incident output
_INTERNAL_TRACKING_FIELD = "lastReportedEndTs"

# Violation state info field names
_INFO_FIELD_IS_COMPLETE = "isComplete"
_INFO_VALUE_COMPLETE = "true"


class FrameStateMgmt:
    """
    Module to manage frames.

    This class provides functionality for:
    - Frame state management and tracking
    - Frame history maintenance
    - Frame sorting and filtering
    - Sensor-specific frame configuration

    :ivar AppConfig config: Configuration object for the application.
    :ivar dict[str, FrameState] state: Dictionary to store frame states.
    :ivar dict[str, list[IncidentState]] completed_states: Dictionary mapping sensor IDs to completed violation states.

    Examples::
        >>> config = AppConfig()
        >>> frame_manager = FrameStateMgmt(config)
        >>> print(f"Initialized frame state management with {len(frame_manager.state)} states")
    """

    def __init__(self, config: AppConfig) -> None:
        self.config: AppConfig = config
        self.state: dict[str, FrameState] = dict()
        self.completed_states: dict[str, list[IncidentState]] = dict()  # sensor_id -> completed states

    def _merge_object_ids(self, existing_ids: list[str], new_ids: list[str]) -> list[str]:
        """Combine IDs while preserving the first ID (primary object) at position 0."""
        primary_id = existing_ids[0]
        merged = set(existing_ids)
        merged.update(new_ids)
        merged.remove(primary_id)  # Remove primary from set to avoid duplication
        return [primary_id] + list(merged)

    def _update_object_state(
        self,
        state: IncidentState,
        object_ids: list[str],
        timestamp: datetime,
        expiration_window: float,
    ) -> None:
        """
        Track when each object appears during a violation window as multiple intervals.
        If the gap between observations exceeds expiration_window, start a new interval.
        
        Cleans up data older than config.incident_object_ttl to prevent memory leaks.
        Objects with no data within the TTL window are removed from object_presence and object_ids.
        """
        object_ttl = self.config.incident_object_ttl
        cutoff_time = timestamp - timedelta(seconds=object_ttl)
        
        # Update intervals for current objects
        for object_id in object_ids:
            intervals = state.object_presence.get(object_id, [])
            if intervals and (timestamp - intervals[-1]["end"]).total_seconds() <= expiration_window:
                # Extend the current interval if within the allowed gap
                intervals[-1]["end"] = timestamp
            else:
                # Start a new interval
                intervals.append({"start": timestamp, "end": timestamp})
            state.object_presence[object_id] = intervals
        
        # Clean up old data outside the store window
        stale_object_ids = []
        for obj_id, intervals in state.object_presence.items():
            # Filter intervals to keep only those within the store window
            fresh_intervals = [i for i in intervals if i["end"] >= cutoff_time]
            if fresh_intervals:
                state.object_presence[obj_id] = fresh_intervals
            else:
                stale_object_ids.append(obj_id)
        
        # Remove stale objects from object_presence and object_ids
        for obj_id in stale_object_ids:
            del state.object_presence[obj_id]
        if stale_object_ids:
            stale_set = set(stale_object_ids)
            state.object_ids = [oid for oid in state.object_ids if oid not in stale_set]

    def _process_violation_state(
        self,
        sensor_id: str,
        primary_object_id: str | None,
        object_ids: list[str],
        frame: nvSchema.Frame,
        state_dict: dict[str, IncidentState],
        expiration_window: float,
        category: IncidentCategory,
        info: dict[str, Any] | None = None,
        additional_validation: Callable[[IncidentState], bool] | None = None,
        track_object_presence: bool = False
    ) -> None:
        """
        Generic method to process violation states.
        
        :param str sensor_id: Sensor ID for the violation.
        :param str | None primary_object_id: Primary object ID involved in the violation (can be None for aggregate violations).
        :param list[str] object_ids: List of all object IDs involved.
        :param nvSchema.Frame frame: Current frame being processed.
        :param dict[str, IncidentState] state_dict: Dictionary to store violation states.
        :param float expiration_window: Time window for expiration of violations.
        :param IncidentCategory category: Category of the incident.
        :param dict | None info: Additional info to store with the violation state.
        :param callable | None additional_validation: Additional validation function for existing state.
        :param bool track_object_presence: Track per-object start/end during violation window (also accumulates IDs).
        """
        info = info or {}
        # Generate violation ID - for FOV count violations (no primary object), use sensor ID only
        if primary_object_id is None:
            violation_id = sensor_id
        else:
            violation_id = sensor_id + " #-# " + primary_object_id
        
        # Build new state
        new_state = IncidentState(
            sensor_id=sensor_id,
            primary_object_id=primary_object_id or "",  # Use empty string if None
            object_ids=object_ids,
            start=get_timestamp_from_proto_ts(frame.timestamp),
            end=get_timestamp_from_proto_ts(frame.timestamp),
            category=category,
            info=info
        )
        if track_object_presence and object_ids:
            self._update_object_state(new_state, object_ids, new_state.start, expiration_window)
        
        state = state_dict.get(violation_id)

        # Use new state if either:
        # 1. No existing state exists
        # 2. Additional validation fails (e.g., ROI changed for restricted area)
        if not state or (additional_validation and not additional_validation(state)):
            state_dict[violation_id] = new_state
            return
        
        # Update existing state (state is guaranteed to be not None here)
        state.end = new_state.end
        if track_object_presence:
            state.object_ids = self._merge_object_ids(state.object_ids, object_ids)
        else:
            state.object_ids = object_ids
        state.info.update(info)
        if track_object_presence and object_ids:
            self._update_object_state(state, object_ids, new_state.start, expiration_window)

    def _get_safety_violation_state(self, sensor_id: str, object_ids: str, frame: nvSchema.Frame, frame_state: FrameState) -> None:
        """
        Extract and process safety violation state from frame data.
        
        Safety violations occur when multiple objects are in proximity.
        Requires at least 2 objects to constitute a violation.
        
        :param str sensor_id: Sensor identifier.
        :param str object_ids: Comma-separated list of object IDs involved in violation.
        :param nvSchema.Frame frame: Frame containing the violation data.
        :param FrameState frame_state: Current frame state to update.
        :return: None
        """
        if not object_ids:
            return

        object_id_list = object_ids.split(",")
        if not object_id_list or len(object_id_list) <= 1:
            return

        primary_object_id = object_id_list[0]
        
        self._process_violation_state(
            sensor_id=sensor_id,
            primary_object_id=primary_object_id,
            object_ids=object_id_list,
            frame=frame,
            state_dict=frame_state.safety_violation_states,
            expiration_window=self.config.proximity_violation_incident_expiration_window,
            category=IncidentCategory.PROXIMITY_VIOLATION,
            track_object_presence=True
        )

    def _get_restricted_area_violation_state(self, sensor_id: str, roi_id: str, object_id: str, frame: nvSchema.Frame, frame_state: FrameState) -> None:
        """
        Extract and process restricted area violation state from frame data.
        
        Restricted area violations occur when objects enter prohibited zones (ROIs).
        Each violation is tracked per object and ROI combination.
        
        :param str sensor_id: Sensor identifier.
        :param str roi_id: Region of Interest identifier where violation occurred.
        :param str object_id: ID of object violating the restricted area.
        :param nvSchema.Frame frame: Frame containing the violation data.
        :param FrameState frame_state: Current frame state to update.
        :return: None
        """
        if not roi_id or not object_id:
            return
        
        # Additional validation to check if ROI ID has changed
        roi_validation = lambda state: state.info.get("roiId") == roi_id
        
        self._process_violation_state(
            sensor_id=sensor_id,
            primary_object_id=object_id,
            object_ids=[object_id],
            frame=frame,
            state_dict=frame_state.restricted_area_violation_states,
            expiration_window=self.config.restricted_area_violation_incident_expiration_window,
            category=IncidentCategory.RESTRICTED_AREA_VIOLATION,
            info={"roiId": roi_id},
            additional_validation=roi_validation
        )
    
    def _get_confined_area_violation_state(self, sensor_id: str, object_id: str, frame: nvSchema.Frame, frame_state: FrameState) -> None:
        """
        Extract and process confined area violation state from frame data.
        
        Confined area violations occur when objects leave designated safe zones.
        Each violation is tracked per object.
        
        :param str sensor_id: Sensor identifier.
        :param str object_id: ID of object violating the confined area.
        :param nvSchema.Frame frame: Frame containing the violation data.
        :param FrameState frame_state: Current frame state to update.
        :return: None
        """
        if not object_id:
            return
        
        self._process_violation_state(
            sensor_id=sensor_id,
            primary_object_id=object_id,
            object_ids=[object_id],
            frame=frame,
            state_dict=frame_state.confined_area_violation_states,
            expiration_window=self.config.confined_area_violation_incident_expiration_window,
            category=IncidentCategory.CONFINED_AREA_VIOLATION
        )
    
    def _get_fov_count_violation_state(self, sensor_id: str, frame: nvSchema.Frame, frame_state: FrameState, fov_metric: nvSchema.TypeMetrics) -> None:
        """
        Process FOV count violation state.
        
        FOV count violations occur when the number of objects of the configured type
        in the field of view exceeds the configured threshold.
        
        :param str sensor_id: Sensor identifier.
        :param list[str] object_ids: IDs of objects exceeding threshold.
        :param nvSchema.Frame frame: Current frame being processed.
        :param FrameState frame_state: Current frame state to update.
        :return: None
        """
        if fov_metric.count < self.config.fov_count_violation_incident_object_threshold:
            return
        
        # For FOV count violations, we use a single-entry dict to leverage the existing _process_violation_state logic
        # The key is just the sensor_id since there's only one FOV count violation per sensor
        temp_dict = {sensor_id: frame_state.fov_count_violation_state} if frame_state.fov_count_violation_state else {}
        
        self._process_violation_state(
            sensor_id=sensor_id,
            primary_object_id=None,  # No primary object for aggregate violations
            object_ids=list(fov_metric.objectIds),
            frame=frame,
            state_dict=temp_dict,
            expiration_window=self.config.fov_count_violation_incident_expiration_window,
            category=IncidentCategory.FOV_COUNT_VIOLATION,
            track_object_presence=True
        )
        
        # Update the frame state with the result
        frame_state.fov_count_violation_state = temp_dict.get(sensor_id)

    def update_frames(self, sensor_id: str, frames: list[nvSchema.Frame]) -> None:
        """
        Update frame state with new frames and maintain history.

        This method:
        1. Sorts frames by timestamp
        2. Maintains last X frames based on sensor configuration
        3. Combines new frames with existing frame history
        4. Updates frame state for the sensor

        :param str sensor_id: Sensor ID for frame tracking.
        :param list[nvSchema.Frame] frames: List of frames to process.
        :return: None

        Examples::
            >>> frame_manager = FrameStateMgmt(config)
            >>> frames = [nvSchema.Frame(timestamp=Timestamp(seconds=1, nanos=0))]
            >>> frame_manager.update_frames("sensor1", frames)
        """
        if not sensor_id or not frames:
            return

        sensor_min_frames = self.config.sensor_min_frames(sensor_id)
        sorted_frames = sorted(frames, key=lambda x: get_timestamp_from_proto_ts(x.timestamp))

        # Filter frames by time threshold to handle perception layer restarts with duplicate IDs
        time_threshold = get_timestamp_from_proto_ts(sorted_frames[-1].timestamp) - timedelta(seconds=self.config.behavior_water_mark)
        
        # Keep only frames that are:
        # 1. More recent than the time threshold (within water mark window)
        # 2. More recent than the absolute behavior time threshold
        filtered_frames = [
            frame
            for frame in sorted_frames
            if get_timestamp_from_proto_ts(frame.timestamp) >= time_threshold and get_timestamp_from_proto_ts(frame.timestamp) > self.config.behavior_time_threshold
        ]

        if not filtered_frames:
            return

        if len(frames) != len(filtered_frames):
            logger.warning(
                f"{len(frames) - len(filtered_frames)} frames older than {self.config.behavior_water_mark} secs and older than "
                f"{self.config.behavior_time_threshold} are filtered out"
            )

        state = FrameState(id=sensor_id, last_x_frames=filtered_frames[-sensor_min_frames:])

        if old_state := self.state.get(sensor_id):
            combined_frames = old_state.last_x_frames + filtered_frames
            state.last_x_frames = combined_frames[-sensor_min_frames:]
            state.safety_violation_states = old_state.safety_violation_states
            state.restricted_area_violation_states = old_state.restricted_area_violation_states
            state.confined_area_violation_states = old_state.confined_area_violation_states
            state.fov_count_violation_state = old_state.fov_count_violation_state

        self.state[sensor_id] = state

        for frame in filtered_frames:
            # Check and move completed violations for this frame
            frame_timestamp = get_timestamp_from_proto_ts(frame.timestamp)
            self._check_and_complete_violations(sensor_id, state, frame_timestamp)
            
            # Add new violations or update existing violations for this frame
            # Only process violations for enabled incident types to avoid unbounded state growth
            if self.config.proximity_violation_incident_enable and frame.socialDistancing:
                for object_ids in frame.socialDistancing.info.get("proximityViolationObjects", "").split("|"):
                    self._get_safety_violation_state(sensor_id, object_ids, frame, state)
            if self.config.restricted_area_violation_incident_enable:
                for roi in frame.rois:
                    if not str_to_bool(roi.info.get("restrictedAreaViolation", "false")):
                        continue
                    for object_id in roi.objectIds:
                        self._get_restricted_area_violation_state(sensor_id, roi.id, object_id, frame, state)
            if self.config.confined_area_violation_incident_enable:
                for objects in frame.info.get("confinedAreaViolationObjects", "").split("|"):
                    for object_id in objects.split(","):
                        self._get_confined_area_violation_state(sensor_id, object_id, frame, state)
            if self.config.fov_count_violation_incident_enable:
                for fov_metric in frame.fov:
                    if not fov_metric.type == self.config.fov_count_violation_incident_object_type:
                        continue
                    self._get_fov_count_violation_state(sensor_id, frame, state, fov_metric)

    def _complete_and_move_violations(
        self,
        sensor_id: str,
        violation_states: dict[str, IncidentState],
        expiration_window: float,
        current_timestamp: datetime,
        violation_type: str
    ) -> None:
        """
        Check violations, mark as complete and move to completed_states if time gap exceeded.
        
        Note: Completed states are moved to self.completed_states and will persist there
        until get_incidents() is called for this sensor. If get_incidents() is not invoked
        regularly, completed_states can grow unbounded.
        
        :param str sensor_id: Sensor ID for tracking completed states.
        :param dict[str, IncidentState] violation_states: Dictionary of violation states to check.
        :param float expiration_window: Time window for expiration.
        :param datetime current_timestamp: Current timestamp for comparison.
        :param str violation_type: Type of violation for logging.
        """
        completed_ids = []
        for violation_id, state in violation_states.items():
            if (current_timestamp - state.end).total_seconds() > expiration_window:
                logger.debug(f"Completing {violation_type} violation state: {violation_id}")
                state.info[_INFO_FIELD_IS_COMPLETE] = _INFO_VALUE_COMPLETE
                completed_ids.append(violation_id)
        
        if completed_ids:
            if sensor_id not in self.completed_states:
                self.completed_states[sensor_id] = []
            for violation_id in completed_ids:
                self.completed_states[sensor_id].append(violation_states[violation_id])
                del violation_states[violation_id]

    def _check_and_complete_violations(
        self,
        sensor_id: str,
        state: FrameState,
        frame_timestamp: datetime
    ) -> None:
        """
        Check all violation types and move completed ones to completed_states.
        
        :param str sensor_id: Sensor ID for tracking.
        :param FrameState state: Current frame state containing violation states.
        :param datetime frame_timestamp: Timestamp of current frame for comparison.
        """
        self._complete_and_move_violations(
            sensor_id, state.safety_violation_states,
            self.config.proximity_violation_incident_expiration_window,
            frame_timestamp, "safety"
        )
        self._complete_and_move_violations(
            sensor_id, state.restricted_area_violation_states,
            self.config.restricted_area_violation_incident_expiration_window,
            frame_timestamp, "restricted area"
        )
        self._complete_and_move_violations(
            sensor_id, state.confined_area_violation_states,
            self.config.confined_area_violation_incident_expiration_window,
            frame_timestamp, "confined area"
        )
        # Handle FOV count violation state (single state)
        if state.fov_count_violation_state:
            violation = state.fov_count_violation_state
            if (frame_timestamp - violation.end).total_seconds() > self.config.fov_count_violation_incident_expiration_window:
                logger.debug(f"Completing FOV count violation state: {sensor_id}")
                violation.info[_INFO_FIELD_IS_COMPLETE] = _INFO_VALUE_COMPLETE
                if sensor_id not in self.completed_states:
                    self.completed_states[sensor_id] = []
                self.completed_states[sensor_id].append(violation)
                state.fov_count_violation_state = None

    def get_state(self, sensor_id: str | None = None) -> FrameState | None | dict[str, FrameState]:
        """
        Get frame state for a sensor or all sensors.

        This method:
        1. Returns state for specific sensor if sensor_id provided
        2. Returns all sensor states if no sensor_id provided
        3. Returns None if sensor_id not found

        :param str sensor_id: Optional sensor ID to get state for.
        :return FrameState | dict[str, FrameState]: Frame state for specific sensor or all sensors.

        Examples::
            >>> frame_manager = FrameStateMgmt(config)
            >>> # Get state for specific sensor
            >>> sensor_state = frame_manager.get_state("sensor1")
            >>> # Get all states
            >>> all_states = frame_manager.get_state()
            >>> print(f"Found {len(all_states)} sensor states")
        """
        if sensor_id:
            return self.state.get(sensor_id)
        else:
            return self.state

    def _get_violation_incidents(
        self,
        sensor_id: str,
        violation_states: dict[str, IncidentState],
        incident_threshold: float,
        last_x_frames: list[nvSchema.Frame]
    ) -> list[Incident]:
        """
        Generic method to get violation incidents.
        
        Prevents duplicate incidents by tracking the last reported end time.
        Only generates a new incident if the end time has changed since the last report.
        
        :param str sensor_id: Sensor ID for the violations.
        :param dict[str, IncidentState] violation_states: Dictionary of violation states.
        :param float incident_threshold: Time threshold for incidents.
        :param list[nvSchema.Frame] last_x_frames: Last X frames for place information.
        :return: List of incidents.
        """
        incidents = []
        for _, violation_state in violation_states.items():
            if violation_state.time_interval >= incident_threshold:
                # Skip duplicate check for completed violations - they're one-shot
                # and should always generate incidents if they meet threshold
                is_completed = violation_state.info.get(_INFO_FIELD_IS_COMPLETE) == _INFO_VALUE_COMPLETE
                
                if not is_completed:
                    # For active violations, check if we've already reported an incident
                    last_reported_end_ts = violation_state.info.get("lastReportedEndTs")
                    current_end_ts = violation_state.end.timestamp()
                    # If end time hasn't changed, skip to avoid duplicate
                    if current_end_ts == last_reported_end_ts:
                        continue
                
                # Start with primary object ID
                info = {}
                if violation_state.primary_object_id:
                    info["primaryObjectId"] = violation_state.primary_object_id
                # Include additional info from violation state (e.g., roiId, isComplete), excluding internal tracking fields
                if violation_state.info:
                    info.update({
                        key: value
                        for key, value in violation_state.info.items()
                        if key != _INTERNAL_TRACKING_FIELD
                    })
                if violation_state.object_presence:
                    timeline = {
                        object_id: [
                            {
                                "start": convert_datetime_to_iso_8601_with_z_suffix(interval["start"]),
                                "end": convert_datetime_to_iso_8601_with_z_suffix(interval["end"])
                            }
                            for interval in intervals
                        ]
                        for object_id, intervals in violation_state.object_presence.items()
                    }
                    info["objectTimeline"] = json.dumps(timeline)
                
                incidents.append(Incident(
                    timestamp=violation_state.start,
                    end=violation_state.end,
                    sensorId=sensor_id,
                    objectIds=violation_state.object_ids,
                    category=violation_state.category.value,
                    place=Place(name=last_x_frames[-1].info.get("place", "") if last_x_frames else ""),
                    info=info
                ))
                
                # Track the last reported end time (as timestamp) to prevent duplicates
                if not is_completed:
                    violation_state.info["lastReportedEndTs"] = violation_state.end.timestamp()
        return incidents

    def get_proximity_violation_incidents(self, sensor_id: str) -> list[Incident]:
        """
        Get safety violation incidents for a sensor.

        Safety violations that exceed the configured incident threshold duration
        are converted to incidents for reporting and alerting.

        :param str sensor_id: Sensor identifier to get incidents for.
        :return list[Incident]: List of safety violation incidents exceeding threshold.
        """
        state = self.get_state(sensor_id)
        if not state:
            return []

        return self._get_violation_incidents(
            sensor_id=sensor_id,
            violation_states=state.safety_violation_states,
            incident_threshold=self.config.proximity_violation_incident_threshold,
            last_x_frames=state.last_x_frames
        )

    def get_restricted_area_violation_incidents(self, sensor_id: str) -> list[Incident]:
        """
        Get restricted area violation incidents for a sensor.

        :param str sensor_id: ID of the sensor to get incidents for.
        :return list[Incident]: List of restricted area violation incidents.
        """
        state = self.get_state(sensor_id)
        if not state:
            return []

        return self._get_violation_incidents(
            sensor_id=sensor_id,
            violation_states=state.restricted_area_violation_states,
            incident_threshold=self.config.restricted_area_violation_incident_threshold,
            last_x_frames=state.last_x_frames
        )

    def get_confined_area_violation_incidents(self, sensor_id: str) -> list[Incident]:
        """
        Get confined area violation incidents for a sensor.

        :param str sensor_id: ID of the sensor to get incidents for.
        :return list[Incident]: List of confined area violation incidents.
        """
        state = self.get_state(sensor_id)
        if not state:
            return []

        return self._get_violation_incidents(
            sensor_id=sensor_id,
            violation_states=state.confined_area_violation_states,
            incident_threshold=self.config.confined_area_violation_incident_threshold,
            last_x_frames=state.last_x_frames
        )
    
    def get_fov_count_violation_incidents(self, sensor_id: str) -> list[Incident]:
        """
        Get FOV count violation incidents for a sensor.
        
        FOV count violations that exceed the configured incident threshold duration
        are converted to incidents for reporting and alerting.
        
        :param str sensor_id: Sensor identifier to get incidents for.
        :return list[Incident]: List of FOV count violation incidents exceeding threshold.
        """
        state = self.get_state(sensor_id)
        if not state or not state.fov_count_violation_state:
            return []
        
        # Create a temporary dict to use with _get_violation_incidents
        temp_dict = {sensor_id: state.fov_count_violation_state}
        
        return self._get_violation_incidents(
            sensor_id=sensor_id,
            violation_states=temp_dict,
            incident_threshold=self.config.fov_count_violation_incident_threshold,
            last_x_frames=state.last_x_frames
        )
    
    def _get_completed_incidents(
        self,
        sensor_id: str,
        category: IncidentCategory,
        incident_threshold: float,
        last_x_frames: list[nvSchema.Frame]
    ) -> list[Incident]:
        """
        Get incidents from completed violation states for a specific category.
        
        Note: This method removes all completed states of the specified category from
        self.completed_states, regardless of whether they meet the incident_threshold.
        Short-lived violations (time_interval < incident_threshold) are intentionally
        dropped without generating incidents. This is a one-shot processing model.
        
        :param str sensor_id: ID of the sensor to get incidents for.
        :param IncidentCategory category: Category of violations to get.
        :param float incident_threshold: Time threshold for incidents.
        :param list[nvSchema.Frame] last_x_frames: Last X frames for place information.
        :return list[Incident]: List of incidents from completed states.
        """
        if sensor_id not in self.completed_states:
            return []
        
        # Filter completed states by category and convert to dict
        states_dict: dict[str, IncidentState] = {}
        remaining_states: list[IncidentState] = []
        
        for i, completed_state in enumerate(self.completed_states[sensor_id]):
            if completed_state.category == category:
                states_dict[str(i)] = completed_state
            else:
                remaining_states.append(completed_state)
        
        # Update completed_states with remaining (unprocessed) states
        if remaining_states:
            self.completed_states[sensor_id] = remaining_states
        elif sensor_id in self.completed_states:
            del self.completed_states[sensor_id]
        
        if not states_dict:
            return []
        
        return self._get_violation_incidents(
            sensor_id=sensor_id,
            violation_states=states_dict,
            incident_threshold=incident_threshold,
            last_x_frames=last_x_frames
        )
    
    def get_incidents(self, sensor_id: str) -> list[Incident]:
        """
        Get incidents for a sensor.
        
        Important: This is the only method that clears completed_states for a sensor.
        It must be called regularly to prevent unbounded growth of completed_states.
        Completed violation states are removed from completed_states after this call,
        regardless of whether they generated incidents (short-lived violations that
        don't meet incident_threshold are dropped without generating incidents).

        :param str sensor_id: ID of the sensor to get incidents for.
        :return list[Incident]: List of all incidents for the sensor.
        """
        incidents = []
        state = self.get_state(sensor_id)
        last_x_frames = state.last_x_frames if state else []

        # Get all incident types - if disabled, no states exist so these return empty lists
        safety_incidents = self.get_proximity_violation_incidents(sensor_id)
        safety_incidents.extend(self._get_completed_incidents(
            sensor_id, IncidentCategory.PROXIMITY_VIOLATION,
            self.config.proximity_violation_incident_threshold, last_x_frames
        ))
        if safety_incidents:
            logger.info(f"Created {len(safety_incidents)} safety proximity violation incidents for sensor {sensor_id}")
        incidents.extend(safety_incidents)
        
        restricted_incidents = self.get_restricted_area_violation_incidents(sensor_id)
        restricted_incidents.extend(self._get_completed_incidents(
            sensor_id, IncidentCategory.RESTRICTED_AREA_VIOLATION,
            self.config.restricted_area_violation_incident_threshold, last_x_frames
        ))
        if restricted_incidents:
            logger.info(f"Created {len(restricted_incidents)} restricted area violation incidents for sensor {sensor_id}")
        incidents.extend(restricted_incidents)
        
        confined_incidents = self.get_confined_area_violation_incidents(sensor_id)
        confined_incidents.extend(self._get_completed_incidents(
            sensor_id, IncidentCategory.CONFINED_AREA_VIOLATION,
            self.config.confined_area_violation_incident_threshold, last_x_frames
        ))
        if confined_incidents:
            logger.info(f"Created {len(confined_incidents)} confined area violation incidents for sensor {sensor_id}")
        incidents.extend(confined_incidents)
        
        fov_incidents = self.get_fov_count_violation_incidents(sensor_id)
        fov_incidents.extend(self._get_completed_incidents(
            sensor_id, IncidentCategory.FOV_COUNT_VIOLATION,
            self.config.fov_count_violation_incident_threshold, last_x_frames
        ))
        if fov_incidents:
            logger.info(f"Created {len(fov_incidents)} FOV count violation incidents for sensor {sensor_id}")
        incidents.extend(fov_incidents)
            
        return incidents
