# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
from datetime import datetime
from typing import Any

import numpy as np

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Behavior, Coordinate, Location, Message, ObjectState
from mdx.analytics.core.schema.trajectory.trajectory import Trajectory
from mdx.analytics.core.transform.calibration.calibration import Calibration
from mdx.analytics.core.stream.state.behavior.state_management_base import StateMgmtBase
from mdx.analytics.core.utils.crs import CoordinateReferenceSystem
from mdx.analytics.core.utils.distance_util import haversine_distance
from mdx.analytics.core.utils.schema_util import model_to_embeddings

logger = logging.getLogger(__name__)


class StateMgmt(StateMgmtBase):
    """
    Module to manage behavior state in euclidean coordinate systems.

    This class extends StateMgmtBase to provide specific implementations for:
    - Map matching and road network integration
    - Edge-based speed calculations
    - Trajectory smoothing and subsampling
    - Behavior state updates with map context

    :ivar bool input_data_in_cartesian: Whether input data is in cartesian coordinates.
    :ivar Dict anomaly_config: Configuration for anomaly detection.
    :ivar int mapmatching_success_cnt: Counter for successful map matching operations.
    :ivar int mapmatching_total_cnt: Total counter for map matching operations.

    Examples::
        >>> config = AppConfig()
        >>> state_manager = StateMgmt(config)
        >>> print(f"Initialized state management with map matching capabilities")
    """

    def __init__(self, config: AppConfig, calibration: Calibration) -> None:
        super().__init__(config=config, calibration=calibration)
        # for debug
        self.mapmatching_success_cnt = 0
        self.mapmatching_total_cnt = 0

    def _update_object_state_model(self, state: ObjectState, embeddings: list[list[float]]) -> None:
        """No-op for geographic coordinates - model with embeddings updates not needed."""
        return

    def _create_trajectory(self, id: str, start: datetime, end: datetime, 
                          points: list[Coordinate]) -> Trajectory:
        """Returns IncrementalTrajectory with geographic/direction configuration."""
        return Trajectory(
            id=id,
            start=start,
            end=end,
            points=points,
            enable_geo=self.config.traj_geo_coord_enable,
            smooth_min_points=self.config.traj_smooth_min_points,
            smooth_window_size=self.config.traj_smooth_window_size,
            distance_stride=self.config.traj_distance_stride,
            speed_segment_size=self.config.traj_speed_segment_size,
            direction_mode=self.config.traj_direction_mode,
            direction_based_cluster_mode=self.config.traj_direction_cluster_mode,
        )

    def update_behavior(
        self, message_key: str, messages: list[Message], **kwargs
    ) -> Behavior | None:
        """
        Update behavior state in geographic coordinate system.

        This method:
        1. Updates sensor timestamps
        2. Gets object state and last message
        3. Deletes expired object states
        4. Creates trajectory with smoothing and subsampling
        5. Generates behavior with map matching context

        :param str message_key: Key for the message (sensor ID + object ID).
        :param list[Message] messages: List of messages to process.
        :param kwargs: Additional keyword arguments. Currently supported:
            - CoordinateReferenceSystem crs: Coordinate reference system for map matching.
        :return Behavior | None: Updated behavior object or None if no valid state/message.

        Examples::
            >>> state_manager = StateMgmt(config)
            >>> messages = [Message(sensor=Sensor(id="sensor1"), timestamp=datetime.now())]
            >>> behavior = state_manager.update_behavior("sensor1_obj1", messages)
            >>> if behavior:
            ...     print(f"Updated behavior with {len(behavior.locations)} locations")
        """
        crs = kwargs.get("crs")
        self._update_sensor_latest_timestamp(messages)
        state, last_message = self._get_object_state_and_message(message_key, messages)
        self._delete_expired_object_state()

        # When getting no new behavior
        if not state or not last_message:
            return None

        behavior_traj = self._create_trajectory(state.id, state.start, state.end, state.points)
        behaviorMessage = self._get_behavior(state, behavior_traj, last_message, crs)
        return behaviorMessage
        
    def _get_behavior(
        self, state: ObjectState, tr: Trajectory, message: Message, crs: CoordinateReferenceSystem | None = None
    ) -> Behavior:
        """
        Get behavior from object state, trajectory and message.

        This method:
        1. Creates info dictionary with cluster information
        2. Performs map matching if object type is configured for it
        3. Calculates speed over edges for matched trajectories
        4. Constructs and returns behavior object with all attributes

        :param ObjectState state: Updated object state containing points and metadata.
        :param Trajectory tr: Processed trajectory with smoothing and subsampling.
        :param Message message: Last message containing sensor and object information.
        :param CoordinateReferenceSystem crs: Optional coordinate reference system for map matching.
        :return Behavior: Behavior object containing all processed information.

        Examples::
            >>> state = ObjectState(id="obj1", points=[...])
            >>> tr = Trajectory(id="obj1", points=[...])
            >>> message = Message(sensor=Sensor(id="sensor1"))
            >>> behavior = state_manager._get_behavior(state, tr, message)
            >>> print(f"Created behavior with {len(behavior.locations)} locations")
        """
        # get edges and speed over edge
        edges = []
        obj_type = message.object.type
        if obj_type in self.config.map_matching_classes:
            trajectory = self._subsample_for_mapmatching(tr.smooth_trajectory, self.config.map_matching_max_points)
            trajectory = [Location(lat=coord.y, lon=coord.x) for coord in trajectory]
            _, _, matched_lattice = crs.road_network.map_matching_for_edge(trajectory)
            edges, _ = self._speed_over_edge(tr, matched_lattice)
            # speed_over_time = speed_over_edge  # choose between speed_over_edge or speed_over_time since for now there is no extra field for speed over edge
            # snapped trajectory (not used in behavior output atm)

        return Behavior(
            id=tr.id,
            timestamp=tr.start,
            end=tr.end,
            locations=tr.geo_location,
            smoothLocations=tr.smooth_geo_location,
            distance=tr.distance,
            speed=tr.speed,
            speedOverTime=tr.speed_over_time,
            timeInterval=tr.time_interval,
            bearing=tr.bearing,
            direction=tr.direction,
            length=len(state.points),
            place=message.place,
            sensor=message.sensor,
            object=message.object,
            event=message.event,
            videoPath=message.videoPath,
            embeddings=model_to_embeddings(state.model),
            edges=edges,
            info=(
                {}
                if self.config.inference.enable
                else {
                    "cluster.modelVersion": "directionBasedModel",
                    "cluster.index": str(tr.direction_based_cluster_index),
                }
            ),
        )

    def _subsample_for_mapmatching(self, points: list[Any], target_point: int) -> list[Any]:
        """
        Subsample points for map matching to reduce computational complexity.

        This method:
        1. Returns original points if length is less than target or target is invalid
        2. Otherwise, evenly samples points to match target count

        :param list[Any] points: List of points to subsample.
        :param int target_point: Target number of points after subsampling.
        :return list[Any]: List of subsampled points.

        Examples::
            >>> points = [Point(x=1, y=1), Point(x=2, y=2), Point(x=3, y=3)]
            >>> subsampled = state_manager._subsample_for_mapmatching(points, 2)
            >>> print(f"Subsampled to {len(subsampled)} points")
        """
        points_subsample = []
        if (len(points) <= target_point) or (target_point <= 0):
            points_subsample = points
        else:
            points_subsample = [
                points[int(i)] for i in np.linspace(0, len(points) - 1, num=target_point, endpoint=True)
            ]

        return points_subsample

    def _speed_over_edge(self, tr: Trajectory, matched_lattice) -> tuple[list[str], list[float]]:
        """
        Calculate speed over matched road network edges.

        This method:
        1. Maps trajectory points to road network edges
        2. Calculates speed for each edge segment
        3. Converts speeds from km/h to mph
        4. Returns edge IDs and corresponding speeds

        :param Trajectory tr: Processed trajectory with smoothing.
        :param matched_lattice: Matched road network lattice from map matching.
        :return tuple[list[str], list[float]]: Tuple containing list of edge IDs and corresponding speeds.

        Examples::
            >>> tr = Trajectory(id="obj1", points=[...])
            >>> edges, speeds = state_manager._speed_over_edge(tr, matched_lattice)
            >>> print(f"Calculated speeds for {len(edges)} edges")
        """
        kmph_to_mph = 0.621371
        map_edge_to_traj = {}

        for idx, m in enumerate(matched_lattice):
            edge_id = m.edge_m.label
            # edge_o_p1_loc = Location(lat=m.edge_o.p1[0],lon=m.edge_o.p1[1])
            # traj_idx = tr.smooth_geo_trajectory.index(edge_o_p1_loc)
            edge_o_p1_coord = Coordinate(x=m.edge_o.p1[1], y=m.edge_o.p1[0])
            traj_idx = tr.smooth_trajectory.index(edge_o_p1_coord)
            if not m.edge_o.is_point():
                # edge_o_p2_loc = Location(lat=m.edge_o.p2[0],lon=m.edge_o.p2[1])
                # traj_idx = tr.smooth_geo_trajectory.index(edge_o_p2_loc)
                edge_o_p2_coord = Coordinate(x=m.edge_o.p2[1], y=m.edge_o.p2[0])
                traj_idx = tr.smooth_trajectory.index(edge_o_p2_coord)
            if edge_id in map_edge_to_traj:
                map_edge_to_traj[edge_id].append(traj_idx)
            else:
                map_edge_to_traj[edge_id] = [traj_idx]

        edges = []
        speed_over_edge = []
        for edge_id, traj_point_idx in map_edge_to_traj.items():
            edges.append(edge_id)
            # If the trajectory has only one point, skip the speed calculation for this edge
            if len(tr.smooth_trajectory) > 1:
                from_idx = max(0, traj_point_idx[0] - 1)
                to_idx = traj_point_idx[-1]
                if from_idx == to_idx:
                    to_idx += 1
                slice = tr.smooth_trajectory[from_idx : to_idx + 1]
                interval = (tr.end - tr.start).total_seconds() * (len(slice) - 1) / (len(tr.smooth_trajectory) - 1)

                # If interval is 0, skip the speed calculation for this edge
                if interval == 0:
                    continue

                speed = (
                    kmph_to_mph
                    * sum(
                        haversine_distance(
                            Location(lat=slice[i].y, lon=slice[i].x), Location(lat=slice[i + 1].y, lon=slice[i + 1].x)
                        )
                        for i in range(len(slice) - 1)
                    )
                    * 3.6
                    / interval
                )
                speed_over_edge.append(round(speed, 2))

        return edges, speed_over_edge
