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

import bisect
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Behavior, Message, ObjectState, Coordinate
from mdx.analytics.core.schema.trajectory.trajectory_base import TrajectoryBase
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase
from mdx.analytics.core.utils.crp import CRP
from mdx.analytics.core.utils.schema_util import get_sensor_id_from_behavior_id, model_to_embeddings

logger = logging.getLogger(__name__)

TAIL_CAP = 16  # max tail_ts entries — bounds bisect-insert memory at sampling == 1


class StateMgmtBase:
    """
    Base class for object tracking over a period of time, used for metadata in protobuf.

    This class provides core functionality for:
    - Object state management and tracking
    - Behavior state updates and maintenance
    - Sensor timestamp management
    - Object type scoring and clustering
    - Trajectory and behavior generation

    :ivar AppConfig config: Configuration object for the application.
    :ivar CalibrationBase calibration: Calibration object for the application.
    :ivar dict[str, ObjectState] state: Dictionary to store object states.
    :ivar dict[str, datetime] sensor_latest_timestamp: Dictionary mapping sensor IDs to their latest timestamps.

    Examples::
        >>> state_manager = StateMgmtBase(config, calibration)
        >>> print(f"Initialized state management with {len(state_manager.state)} states")
    """

    def __init__(self, config: AppConfig, calibration: CalibrationBase) -> None:
        self.config: AppConfig = config
        self.calibration: CalibrationBase = calibration
        self.state: dict[str, ObjectState] = dict()
        self.sensor_latest_timestamp: dict[str, datetime] = dict()

    def _get_current_timestamp(self, sensorId: str) -> datetime | None:
        """
        Get the current timestamp for a sensor.

        In simulation mode, returns the latest timestamp for the sensor.
        Otherwise, returns the current UTC time.

        :param str sensorId: The sensor ID to get timestamp for.
        :return datetime | None: Current timestamp for the sensor, or None if not found in simulation mode.
        :raises ValueError: If in simulation mode and no timestamp exists for sensor.

        Examples::
            >>> state_manager = StateMgmtBase(config)
            >>> timestamp = state_manager._get_current_timestamp("sensor1")
            >>> print(f"Current timestamp: {timestamp}")
        """
        if not self.config.in_simulation_mode:
            return datetime.now(timezone.utc)
        return self.sensor_latest_timestamp.get(sensorId)

    def _update_sensor_latest_timestamp(self, messages: list[Message]) -> None:
        """
        Update the latest timestamp for each sensor based on incoming messages.

        :param list[Message] messages: List of messages containing sensor timestamps.
        :return: None

        Examples::
            >>> state_manager = StateMgmtBase(config)
            >>> messages = [Message(sensor=Sensor(id="sensor1"), timestamp=datetime.now())]
            >>> state_manager._update_sensor_latest_timestamp(messages)
        """
        for msg in messages:
            if (msg.sensor.id not in self.sensor_latest_timestamp) or (
                msg.timestamp > self.sensor_latest_timestamp[msg.sensor.id]
            ):
                logger.info(f"Updating sensor latest timestamp: {msg.sensor.id} to {msg.timestamp}")
                self.sensor_latest_timestamp[msg.sensor.id] = msg.timestamp

    def _delete_expired_object_state(self) -> None:
        """
        Delete object states that have not been updated within the behavior state timeout period.

        :return: None

        Examples::
            >>> state_manager = StateMgmtBase(config)
            >>> state_manager._delete_expired_object_state()
            >>> print(f"Remaining states: {len(state_manager.state)}")
        """
        for behavior_id in list(self.state.keys()):
            sensor_id = get_sensor_id_from_behavior_id(behavior_id)
            current_timestamp = self._get_current_timestamp(sensor_id)
            if not current_timestamp:
                continue
            if (current_timestamp - self.state[behavior_id].end).total_seconds() > self.config.behavior_state_timeout:
                logger.info(f"Deleting expired state: {behavior_id}")
                del self.state[behavior_id]

    def _is_valid_state(self, old_state: ObjectState, new_state: ObjectState, interval: int) -> bool:
        """
        Check if the state transition is valid based on the time interval.

        :param ObjectState old_state: Old state stored in memory.
        :param ObjectState new_state: New generated object state.
        :param int interval: Maximum allowed interval in seconds.
        :return bool: True if the state transition is valid, False otherwise.

        Examples::
            >>> state_manager = StateMgmtBase(config)
            >>> old_state = ObjectState(end=datetime.now())
            >>> new_state = ObjectState(start=datetime.now() + timedelta(seconds=2))
            >>> is_valid = state_manager._is_valid_state(old_state, new_state)
            >>> print(f"State transition valid: {is_valid}")
        """
        valid = (new_state.start - old_state.end).total_seconds() < interval and new_state.start >= old_state.end
        if not valid:
            logger.info(
                f"invalid old state, id: {old_state.id}, old state end: {old_state.end}, new state start: {new_state.start}"
            )
        return valid

    def _create_trajectory(self, id: str, start: datetime, end: datetime, points: list[Coordinate]) -> TrajectoryBase:
        """Create a trajectory instance. Override in child classes for specific trajectory types."""
        raise NotImplementedError("create_trajectory is not implemented")

    def _update_object_state_model(self, state: ObjectState, embeddings: list[list[float]]) -> None:
        """Update the clustering model of an object state. Override in child classes if needed."""
        if state.model:
            clustering_model = CRP().update_model(state.model, embeddings, self.config.cluster_threshold)
        else:
            clustering_model = CRP().cluster(embeddings, self.config.cluster_threshold)
        state.model = clustering_model
    
    def _get_object_state_and_message(
        self, message_key: str, messages: list[Message]
    ) -> tuple[ObjectState | None, Message | None]:
        """
        Get new state of an object and last message.

        This method processes messages to create or update an object state, including:
        - Filtering messages by time threshold
        - Extracting coordinates and embeddings
        - Computing object type scores
        - Creating or updating clustering model
        - Managing state transitions

        :param str message_key: Key for the message (sensor ID + object ID).
        :param list[Message] messages: List of messages to process.
        :return tuple[ObjectState | None, Message | None]: Tuple containing the object state and last message, or (None, None) if invalid.

        Examples::
            >>> state_manager = StateMgmtBase(config)
            >>> messages = [Message(sensor=Sensor(id="sensor1"), timestamp=datetime.now())]
            >>> state, msg = state_manager._get_object_state_and_message("sensor1_obj1", messages)
            >>> print(f"Created state with {len(state.points)} points")
        """
        state, _, last_message = self._get_object_trip_state_and_message(message_key, messages)
        return state, last_message

    def _get_object_trip_state_and_message(
        self, message_key: str, messages: list[Message]
    ) -> tuple[ObjectState | None, ObjectState | None, Message | None]:
        """
        Get new state of an object, trip state and last message.

        This method processes messages to create or update object and trip states, including:
        - Filtering messages by time threshold
        - Computing trip states with minimum points
        - Managing state transitions and sampling
        - Updating clustering models

        :param str message_key: Key for the message (sensor ID + object ID).
        :param list[Message] messages: List of messages to process.
        :return tuple[ObjectState | None, ObjectState | None, Message | None]: Tuple containing the object state, trip state and last message, or (None, None, None) if invalid.

        Examples::
            >>> state_manager = StateMgmtBase(config)
            >>> messages = [Message(sensor=Sensor(id="sensor1"), timestamp=datetime.now())]
            >>> state, trip_state, msg = state_manager._get_object_trip_state_and_message("sensor1_obj1", messages)
            >>> print(f"Created states with {len(state.points)} points")
        """
        # Skip invalid or dummy messages
        if not message_key or not messages or message_key.endswith("dummy"):
            return None, None, None

        # Configure trip tracking parameters
        sensor_id = messages[0].sensor.id
        tripwire_min_points = self.config.sensor_tripwire_min_points(sensor_id)
        min_trip_length = tripwire_min_points * 2
        min_trip_length_minus_one = min_trip_length - 1

        # Filter messages in stages
        sorted_messages = sorted(list(messages), key=lambda x: x.timestamp)
        time_threshold = sorted_messages[-1].timestamp - timedelta(seconds=self.config.behavior_water_mark)
        state = self.state.get(message_key)

        # 1) Drop messages outside time window or before global behavior threshold
        filtered_by_time = [
            msg
            for msg in sorted_messages
            if msg.timestamp >= time_threshold and msg.timestamp > self.config.behavior_time_threshold
        ]
        dropped_by_time = len(sorted_messages) - len(filtered_by_time)
        if dropped_by_time:
            logger.warning(
                f"{dropped_by_time} message(s) filtered out (older than {self.config.behavior_water_mark}s window or "
                f"before behavior_time_threshold {self.config.behavior_time_threshold}) for {message_key}"
            )

        # 2) Widened cutoff + split into in-order / in-tolerance in a single pass.
        in_order_msgs: list[Message] = []
        in_tolerance_msgs: list[Message] = []
        if state is not None:
            cutoff = state.end - timedelta(seconds=self.config.behavior_state_end_tolerance_sec)
            dropped = 0
            for msg in filtered_by_time:
                if msg.timestamp > state.end:
                    in_order_msgs.append(msg)
                elif msg.timestamp > cutoff:
                    in_tolerance_msgs.append(msg)
                else:
                    dropped += 1
            if dropped:
                logger.warning(
                    f"{dropped} message(s) filtered out (older than {cutoff}, "
                    f"tolerance {self.config.behavior_state_end_tolerance_sec}s) for {message_key}"
                )
        else:
            in_order_msgs = filtered_by_time

        if not in_order_msgs:
            if in_tolerance_msgs:
                logger.debug(
                    f"Tolerance-only batch for {message_key}: "
                    f"{len(in_tolerance_msgs)} late message(s) dropped"
                )
            return None, None, None

        coordinates = [msg.object.coordinate for msg in in_order_msgs]
        embeddings = [
            msg.object.embedding.vector
            for msg in (in_order_msgs + in_tolerance_msgs)
            if msg.object.confidence >= self.config.object_confidence_threshold
            and msg.object.embedding and msg.object.embedding.vector
        ]
        last_x_points = coordinates[-min_trip_length_minus_one:]

        new_state = ObjectState(
            id=message_key,
            start=in_order_msgs[0].timestamp,
            end=in_order_msgs[-1].timestamp,
            points=coordinates,
            lastXpoints=last_x_points,
            tail_ts=[m.timestamp for m in in_order_msgs[-TAIL_CAP:]],
        )

        # If no old state or invalid transition, use new state for both
        if not state or not self._is_valid_state(state, new_state, self.config.behavior_state_valid_interval):
            self.state[message_key] = new_state
            self._update_object_state_model(new_state, embeddings)
            logger.info(f"Created new Object State: {message_key}\n"
                        f"  Start: {new_state.start}\n"
                        f"  End: {new_state.end}\n"
                        f"  Points: {len(new_state.points)}\n"
                        f"  TimeInterval: {new_state.end - new_state.start}\n"
                        f"  Length: {len(new_state.points)}")
            return new_state, new_state, in_order_msgs[-1]

        # === Update existing state ===
        
        # Prepare trip data (combination of old and new)
        trip_points = state.lastXpoints + new_state.points
        if new_state.time_interval != 0 and len(new_state.points) > 1:
            interval = new_state.time_interval / (len(new_state.points) - 1)
        else:
            interval = (new_state.start - state.end).total_seconds()
        trip_start = new_state.start - timedelta(seconds=len(state.lastXpoints) * interval)

        # Cross-batch phase counter — per-batch [::sampling] retained 100% of 1-point batches.
        for msg in in_order_msgs:
            if state.sample_phase == 0:
                state.points.append(msg.object.coordinate)
                if state.sampling == 1:
                    state.tail_ts.append(msg.timestamp)
            state.sample_phase = (state.sample_phase + 1) % state.sampling

        if state.sampling == 1 and len(state.tail_ts) > TAIL_CAP:
            state.tail_ts = state.tail_ts[-TAIL_CAP:]

        # Tolerance bisect-insert is only correct at sampling == 1; skip at higher strides.
        if state.sampling == 1:
            for msg in in_tolerance_msgs:
                ts = msg.timestamp
                if not state.tail_ts or ts < state.tail_ts[0]:
                    logger.warning(
                        f"Tolerance-window message (ts={ts}) precedes tracked tail window for {message_key}; dropping"
                    )
                    continue
                rel_idx = bisect.bisect_right(state.tail_ts, ts)
                abs_idx = len(state.points) - len(state.tail_ts) + rel_idx
                state.points.insert(abs_idx, msg.object.coordinate)
                state.tail_ts.insert(rel_idx, ts)

        # Halving: phase parity shift preserves exact 1-in-N continuity across the boundary.
        if len(state.points) > self.config.behavior_max_points:
            j = len(state.points) - (0 if state.sample_phase == 0 else 1)
            state.sample_phase += state.sampling * (j % 2)
            state.sampling *= 2
            state.points = state.points[::2]
            state.tail_ts = []

        state.end = new_state.end
        state.lastXpoints = trip_points[-min_trip_length_minus_one:]
        self._update_object_state_model(state, embeddings)

        # Create trip state
        trip_state = ObjectState(
            id=message_key,
            start=trip_start,
            end=new_state.end,
            points=trip_points,
        )

        self.state[message_key] = state
        logger.info(f"Updated Object State: {message_key}\n"
                    f"  Start: {state.start}\n"
                    f"  End: {state.end}\n"
                    f"  Points: {len(state.points)}\n"
                    f"  TimeInterval: {state.end - state.start}\n"
                    f"  Length: {len(state.points)}")
        return state, trip_state, in_order_msgs[-1]

    def _get_behavior(self, state: ObjectState, tr: TrajectoryBase, message: Message) -> Behavior:
        """
        Get behavior from object state, trajectory and message.

        :param ObjectState state: Updated object state.
        :param TrajectoryBase tr: Trajectory of the behavior.
        :param Message message: Last message containing object information.
        :return Behavior: Updated behavior object.

        Examples::
            >>> state_manager = StateMgmtBase(config)
            >>> state = ObjectState(points=[...])
            >>> trajectory = TrajectoryBase(direction_index=1)
            >>> message = Message(sensor=Sensor(id="sensor1"))
            >>> behavior = state_manager._get_behavior(state, trajectory, message)
            >>> print(f"Created behavior with direction {behavior.direction}")
        """

        return Behavior(
            id=state.id,
            timestamp=state.start,
            end=state.end,
            timeInterval=state.time_interval,
            embeddings=model_to_embeddings(state.model),
            locations=tr.geo_location,
            smoothLocations=tr.smooth_geo_location,
            distance=tr.distance,
            speed=tr.speed,
            speedOverTime=tr.speed_over_time,
            bearing=tr.bearing,
            direction=tr.direction,
            length=len(tr.points),
            place=message.place,
            sensor=message.sensor,
            object=message.object,
            event=message.event,
            videoPath=message.videoPath,
            info={
                "cluster.modelVersion": "directionBasedModel",
                "cluster.index": str(tr.direction_index)
            }
        )

    def update_behavior(self, message_key: str, messages: list[Message], **kwargs) -> Any:
        """
        Update behavior based on messages.

        Default implementation: updates sensor timestamps, gets/updates object and trip state,
        prunes expired state, builds trajectories, and returns (behavior, trip_behavior).
        Subclasses may override or call super() and adapt the return value.

        :param str message_key: Key for the message (sensor ID + object ID).
        :param list[Message] messages: List of messages to process.
        :return Any: Tuple (behavior, trip_behavior) or (None, None) when no valid state.

        Examples::
            >>> state_manager = StateMgmtBase(config)
            >>> messages = [Message(sensor=Sensor(id="sensor1"))]
            >>> behavior = state_manager.update_behavior("sensor1_obj1", messages)
            >>> print(f"Updated behavior: {behavior}")
        """
        self._update_sensor_latest_timestamp(messages)
        state, trip_state, last_message = self._get_object_trip_state_and_message(message_key, messages)
        self._delete_expired_object_state()
        if not state or not trip_state or not last_message:
            return None, None

        # Build trajectories
        behavior_traj = self._create_trajectory(state.id, state.start, state.end, state.points)
        trip_traj = self._create_trajectory(trip_state.id, trip_state.start, trip_state.end, trip_state.points)

        behaviorMessage = self._get_behavior(state, behavior_traj, last_message)
        tripMessage = self._get_behavior(trip_state, trip_traj, last_message)

        return behaviorMessage, tripMessage
