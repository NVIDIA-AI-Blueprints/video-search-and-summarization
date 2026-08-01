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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Behavior, Message, ObjectState, Coordinate
from mdx.analytics.core.schema.trajectory.trajectory import Trajectory
from mdx.analytics.core.stream.state.behavior import point_sampling
from mdx.analytics.core.stream.state.behavior.behavior_holdback import BehaviorHoldback
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase
from mdx.analytics.core.utils.crp import CRP
from mdx.analytics.core.utils.schema_util import get_sensor_id_from_behavior_id, model_to_embeddings

logger = logging.getLogger(__name__)


@dataclass
class BehaviorBatch:
    """
    Everything one batch of messages produced.

    A plain dataclass rather than a pydantic model: this never crosses a wire, and it is built once
    per batch on the hot path. The lists hold live references, so enriching an entry of
    ``active_behaviors`` in place -- adding anomaly info, compacting -- is visible to whatever
    is written. Replacing the objects instead of mutating them would break that.

    :ivar list[Behavior] active_behaviors: One per track that this batch updated. Feed these to
        detection and enrich them in place.
    :ivar list[Behavior] trip_behaviors: One per track, covering the previous tail plus this batch's
        points. Used for tripwire and ROI events; always empty in geographic coordinates, which does
        not track trips.
    :ivar list[Behavior] behaviors_to_write: What to write to the behavior stream. The emission
        policy is already resolved here, so no caller needs to know whether ``behaviorEmitOnce`` is
        on: it is either the active behaviors or the tracks that just ended.

    Examples::
        >>> behavior_batch = state_manager.process_batch(messages_map)
        >>> app.write_behaviors(behavior_batch.behaviors_to_write)
    """

    active_behaviors: list[Behavior] = field(default_factory=list)
    trip_behaviors: list[Behavior] = field(default_factory=list)
    behaviors_to_write: list[Behavior] = field(default_factory=list)


class StateMgmt:
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
    :ivar BehaviorHoldback behavior_holdback: Holds behaviors back when ``behaviorEmitOnce`` is enabled.

    Examples::
        >>> state_manager = StateMgmt(config, calibration)
        >>> behavior_batch = state_manager.process_batch(messages_map)
        >>> print(f"Written {len(behavior_batch.behaviors_to_write)} behavior(s)")
    """

    def __init__(self, config: AppConfig, calibration: CalibrationBase) -> None:
        self.config: AppConfig = config
        self.calibration: CalibrationBase = calibration
        self.state: dict[str, ObjectState] = dict()
        self.sensor_latest_timestamp: dict[str, datetime] = dict()
        self.behavior_holdback: BehaviorHoldback = BehaviorHoldback()

    def process_batch(self, messages_map: dict[str, list[Message]]) -> BehaviorBatch:
        """
        Process one whole batch of messages, grouped by track key.

        Taking the entire batch rather than a key at a time is deliberate. Deciding which tracks have
        fallen silent is only sound once every message in the batch has reached its state and every
        sensor clock has advanced; judging it key by key would end tracks whose own messages are
        still sitting later in the same batch. Owning the loop here makes that impossible to get
        wrong from the outside -- one call is one batch.

        :param dict[str, list[Message]] messages_map: Messages grouped by key (sensor ID + object ID),
            as produced by ``messages_to_map``.
        :return BehaviorBatch: Behaviors updated by this batch, their trip states, and what to write.

        Examples::
            >>> behavior_batch = state_manager.process_batch(messages_to_map(messages))
            >>> app.write_behaviors(behavior_batch.behaviors_to_write)
        """
        batch = BehaviorBatch()

        for message_key, messages in messages_map.items():
            behavior, trip_behavior = self._process_key(message_key, messages)
            if behavior:
                batch.active_behaviors.append(behavior)
            if trip_behavior:
                batch.trip_behaviors.append(trip_behavior)

        # Retain before ending, so a track that both produced a behavior and fell silent in the same
        # batch still reaches the stream: the sweep below releases whatever was just retained.
        if self.config.behavior_emit_once:
            self.behavior_holdback.retain(batch.active_behaviors)

        # Runs in both modes -- ending a track is what reclaims its state, so per-batch mode depends
        # on it too, even though it holds nothing back.
        self._end_inactive_behaviors()

        if self.config.behavior_emit_once:
            batch.behaviors_to_write = self.behavior_holdback.take_ended()
        else:
            batch.behaviors_to_write = (
                self._carry_over_held_behaviors(batch.active_behaviors) + batch.active_behaviors
            )

        return batch

    def _carry_over_held_behaviors(self, active_behaviors: list[Behavior]) -> list[Behavior]:
        """
        Hand over anything still held back, once, after ``behaviorEmitOnce`` is switched off.

        ``behaviorEmitOnce`` is runtime-updatable, so tracks can be left held when it flips. They
        cannot simply be dropped -- a track that fell silent around the flip would never be written
        at all -- but neither can they trickle out as they end: per-batch output resumes immediately
        and writes progressively more complete behaviors for the same IDs, so a snapshot released
        later would arrive with an older ``end`` than one already sent and regress it downstream.

        So the holdback is emptied in one go, minus any track already producing fresh output this
        batch. After that it stays empty and this is a no-op.

        :param list[Behavior] active_behaviors: Behaviors this batch produced, whose tracks already
            have fresher output and so need nothing carried over.
        :return list[Behavior]: Held behaviors worth writing, ahead of this batch's own output.
        """
        if not self.behavior_holdback.pending and not self.behavior_holdback.ended:
            return []

        fresh_ids = {behavior.id for behavior in active_behaviors}
        carried = [behavior for behavior in self.behavior_holdback.flush() if behavior.id not in fresh_ids]
        logger.info(f"behaviorEmitOnce switched off; handing over {len(carried)} held behavior(s)")

        return carried

    def flush_behaviors(self) -> list[Behavior]:
        """
        Return every behavior still held back, ended or not, and drop it.

        Intended for shutdown, where tracks that were still live would otherwise be lost. Returns an
        empty list unless emit-once is enabled, since nothing is ever held back in per-batch mode.

        :return list[Behavior]: Behaviors to write before the app stops.

        Examples::
            >>> app.write_behaviors(state_manager.flush_behaviors())
        """
        return self.behavior_holdback.flush()

    def live_object_ids(self) -> list[str]:
        """
        Keys of the tracks currently held in state, for detectors that need to know what is live.

        :return list[str]: Track keys (sensor ID + object ID).

        Examples::
            >>> detector.update_live_object(state_manager.live_object_ids())
        """
        return list(self.state.keys())

    def _get_current_timestamp(self, sensorId: str) -> datetime | None:
        """
        Get the current timestamp for a sensor.

        In simulation mode, returns the latest timestamp for the sensor.
        Otherwise, returns the current UTC time.

        :param str sensorId: The sensor ID to get timestamp for.
        :return datetime | None: Current timestamp for the sensor, or None if not found in simulation mode.
        :raises ValueError: If in simulation mode and no timestamp exists for sensor.

        Examples::
            >>> state_manager = StateMgmt(config)
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
            >>> state_manager = StateMgmt(config)
            >>> messages = [Message(sensor=Sensor(id="sensor1"), timestamp=datetime.now())]
            >>> state_manager._update_sensor_latest_timestamp(messages)
        """
        for msg in messages:
            if (msg.sensor.id not in self.sensor_latest_timestamp) or (
                msg.timestamp > self.sensor_latest_timestamp[msg.sensor.id]
            ):
                logger.info(f"Updating sensor latest timestamp: {msg.sensor.id} to {msg.timestamp}")
                self.sensor_latest_timestamp[msg.sensor.id] = msg.timestamp

    def _end_inactive_behaviors(self) -> None:
        """
        End tracks that have been quiet for at least ``behaviorStateValidInterval`` seconds.

        That gap is the same threshold :meth:`_is_valid_state` uses to reject a continuation, so once
        it passes the track can no longer be extended -- any later observation starts a new one, which
        makes it the point at which the track is provably over. Ending is therefore also when the
        object state is reclaimed: nothing further can be added to it, and a returning object ID
        simply starts a fresh track.

        Inactivity is measured per sensor in *event* time (``sensor_latest_timestamp``), not against
        the wall clock, for two reasons. A sensor's clock must only advance on its own data, so busy
        sensors cannot age out the tracks of a sensor whose batch is still in flight. And because the
        threshold is short, wall-clock comparison would misread ingestion lag or backpressure as
        silence and cut live tracks short. This mirrors :meth:`_is_valid_state`, which also compares
        event timestamps.

        A sensor that stops streaming freezes its own clock, so its tracks are never ended and their
        state is retained. That residue is bounded -- a silent sensor contributes no new object IDs --
        and it is released when the app stops.

        Only correct at end of batch, once every key has been through :meth:`_process_key` and all
        sensor clocks are current -- see :meth:`process_batch`, its only caller.

        :return: None
        """
        for behavior_id in list(self.state.keys()):
            sensor_timestamp = self.sensor_latest_timestamp.get(get_sensor_id_from_behavior_id(behavior_id))
            if not sensor_timestamp:
                continue
            if (sensor_timestamp - self.state[behavior_id].end).total_seconds() >= self.config.behavior_state_valid_interval:
                logger.info(f"Track ended, releasing state: {behavior_id}")
                del self.state[behavior_id]
                self.behavior_holdback.end_track(behavior_id, reason="track inactive")

    def _is_valid_state(self, old_state: ObjectState, new_state: ObjectState, interval: int) -> bool:
        """
        Check if the state transition is valid based on the time interval.

        :param ObjectState old_state: Old state stored in memory.
        :param ObjectState new_state: New generated object state.
        :param int interval: Maximum allowed interval in seconds.
        :return bool: True if the state transition is valid, False otherwise.

        Examples::
            >>> state_manager = StateMgmt(config)
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

    def _create_trajectory(self, id: str, start: datetime, end: datetime,
                          points: list[Coordinate]) -> Trajectory:
        """
        Build the trajectory for a track, in cartesian or image coordinates.

        ``Trajectory`` gates bearing and speed on the calibration type, which is read here rather
        than captured, so a calibration switch is picked up on the next batch. Geographic
        coordinates need map matching and a different distance metric -- see :class:`StateMgmtG`.

        :param str id: Track key the trajectory belongs to.
        :param datetime start: Start of the trajectory.
        :param datetime end: End of the trajectory.
        :param list[Coordinate] points: Accumulated points.
        :return Trajectory: Trajectory for this coordinate system.
        """
        return Trajectory(
            id=id,
            start=start,
            end=end,
            points=points,
            smooth_min_points=self.config.traj_smooth_min_points,
            smooth_window_size=self.config.traj_smooth_window_size,
            distance_stride=self.config.traj_distance_stride,
            speed_segment_size=self.config.traj_speed_segment_size,
            calibration_type=self.calibration.calibration_type,
        )

    def _update_object_state_model(self, state: ObjectState, embeddings: list[list[float]]) -> None:
        """Update the clustering model of an object state. Override in child classes if needed."""
        if state.model:
            clustering_model = CRP().update_model(state.model, embeddings, self.config.cluster_threshold)
        else:
            clustering_model = CRP().cluster(embeddings, self.config.cluster_threshold)
        state.model = clustering_model
    
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
            >>> state_manager = StateMgmt(config)
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
        # Per-frame bboxes kept aligned 1:1 with ``coordinates`` (and thus ``points``).
        bboxes = [msg.object.bbox for msg in in_order_msgs]
        embeddings = [
            msg.object.embedding.vector
            for msg in (in_order_msgs + in_tolerance_msgs)
            if msg.object.confidence >= self.config.object_confidence_threshold
            and msg.object.embedding and msg.object.embedding.vector
        ]
        last_x_points = coordinates[-min_trip_length_minus_one:]
        last_x_bboxes = bboxes[-min_trip_length_minus_one:]

        new_state = ObjectState(
            id=message_key,
            start=in_order_msgs[0].timestamp,
            end=in_order_msgs[-1].timestamp,
            points=coordinates,
            bboxes=bboxes,
            lastXpoints=last_x_points,
            lastXbboxes=last_x_bboxes,
            tail_ts=[m.timestamp for m in in_order_msgs[-point_sampling.TAIL_CAP:]],
        )

        # If no old state or invalid transition, use new state for both
        if not state or not self._is_valid_state(state, new_state, self.config.behavior_state_valid_interval):
            if state:
                # The gap proves the previous track ended, so its retained behavior is final; ending it
                # here -- before this batch retains the replacement under the same key -- keeps at most
                # one pending behavior per key.
                self.behavior_holdback.end_track(message_key, reason="track replaced after discontinuity")
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
        trip_bboxes = state.lastXbboxes + new_state.bboxes
        if new_state.time_interval != 0 and len(new_state.points) > 1:
            interval = new_state.time_interval / (len(new_state.points) - 1)
        else:
            interval = (new_state.start - state.end).total_seconds()
        trip_start = new_state.start - timedelta(seconds=len(state.lastXpoints) * interval)

        point_sampling.append_sampled(state, in_order_msgs)
        point_sampling.insert_tolerance_messages(state, in_tolerance_msgs, message_key)
        point_sampling.halve_if_needed(state, self.config.behavior_max_points)

        state.end = new_state.end
        state.lastXpoints = trip_points[-min_trip_length_minus_one:]
        state.lastXbboxes = trip_bboxes[-min_trip_length_minus_one:]
        self._update_object_state_model(state, embeddings)

        # Create trip state
        trip_state = ObjectState(
            id=message_key,
            start=trip_start,
            end=new_state.end,
            points=trip_points,
            bboxes=trip_bboxes,
        )

        self.state[message_key] = state
        logger.info(f"Updated Object State: {message_key}\n"
                    f"  Start: {state.start}\n"
                    f"  End: {state.end}\n"
                    f"  Points: {len(state.points)}\n"
                    f"  TimeInterval: {state.end - state.start}\n"
                    f"  Length: {len(state.points)}")
        return state, trip_state, in_order_msgs[-1]

    def _get_behavior(self, state: ObjectState, tr: Trajectory, message: Message) -> Behavior:
        """
        Get behavior from object state, trajectory and message.

        :param ObjectState state: Updated object state.
        :param Trajectory tr: Trajectory of the behavior.
        :param Message message: Last message containing object information.
        :return Behavior: Updated behavior object.

        Examples::
            >>> state_manager = StateMgmt(config)
            >>> state = ObjectState(points=[...])
            >>> trajectory = Trajectory(direction_index=1)
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
            locationsBboxes=state.bboxes,
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

    def _process_key(self, message_key: str, messages: list[Message]) -> tuple[Behavior | None, Behavior | None]:
        """
        Advance one track with its share of the current batch.

        Updates the sensor clock, folds the messages into the track's object and trip state, and
        builds a behavior for each. Called only by :meth:`process_batch`, which owns the batch-wide
        steps -- expiry and the emission policy -- that are only sound once every key is done.

        Shared by every coordinate system. What differs between them is which trajectory to build
        (:meth:`_create_trajectory`), how to turn it into a behavior (:meth:`_get_behavior`), and
        whether trips are tracked at all (:meth:`_build_trip_behavior`) -- so subclasses override
        those rather than this.

        :param str message_key: Key for the message (sensor ID + object ID).
        :param list[Message] messages: Messages for this key in the current batch.
        :return tuple[Behavior | None, Behavior | None]: The track's behavior and trip behavior, or
            ``(None, None)`` when the messages yielded no valid state.
        """
        self._update_sensor_latest_timestamp(messages)
        state, trip_state, last_message = self._get_object_trip_state_and_message(message_key, messages)
        if not state or not last_message:
            return None, None

        behavior_traj = self._create_trajectory(state.id, state.start, state.end, state.points)

        return (
            self._get_behavior(state, behavior_traj, last_message),
            self._build_trip_behavior(trip_state, last_message),
        )

    def _build_trip_behavior(self, trip_state: ObjectState | None, last_message: Message) -> Behavior | None:
        """
        Build the short-window trip behavior that tripwire and ROI detection consume.

        Skipped when the sensor has no tripwires and no ROIs, because the only two consumers --
        :class:`TripwireEvent` and :class:`ROIEvent` -- both return no events for such a sensor. The
        object is not cheap: it smooths a second trajectory and derives distance, speed and
        speed-over-time from it, per track, per batch. Doing that for a sensor with no geometry to
        cross is pure waste, and an app that defines no calibration has every sensor in that state.

        Overridden to ``None`` by coordinate systems that do not track trips, which is cheaper than
        duplicating :meth:`_process_key` just to skip these two objects.

        :param ObjectState | None trip_state: Trip state for this batch, if one was produced.
        :param Message last_message: Last message of the batch, for sensor and object context.
        :return Behavior | None: The trip behavior, or ``None`` when there is no trip state or the
            sensor has nothing for a trip to cross.
        """
        if not trip_state or not self._sensor_has_trip_geometry(last_message.sensor.id):
            return None

        trip_traj = self._create_trajectory(trip_state.id, trip_state.start, trip_state.end, trip_state.points)

        return self._get_behavior(trip_state, trip_traj, last_message)

    def _sensor_has_trip_geometry(self, sensor_id: str) -> bool:
        """
        Whether the sensor defines any tripwire or ROI for a trip behavior to be tested against.

        Read from the calibration on every call rather than cached, so a calibration pushed at runtime
        starts producing trips on the next batch.

        :param str sensor_id: Sensor to check.
        :return bool: True when the sensor has at least one tripwire or ROI.
        """
        sensor = self.calibration.sensor_map.get(sensor_id)

        return bool(sensor and (sensor.tripwires or sensor.rois))
