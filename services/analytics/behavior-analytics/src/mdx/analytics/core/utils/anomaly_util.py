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

from mdx.analytics.core.schema.config import (
    AbnormalMovementConfig,
    AppConfig,
    CollisionDetectionConfig,
    SpeedViolationConfig,
    UnexpectedStopConfig,
)
from mdx.analytics.core.transform.calibration.calibration_dynamic import CalibrationType
from mdx.analytics.core.schema.models import AnalyticsModule, Behavior, Coordinate, Location
from mdx.analytics.core.schema.trajectory.trajectory import Trajectory
from mdx.analytics.core.utils.crs import CoordinateReferenceSystem
from mdx.analytics.core.utils.distance_util import bearing, euclidean_distance, haversine_distance
from mdx.analytics.core.transform.detection.stop_detection import StopDetection


logger = logging.getLogger(__name__)


class AnomalyDetector:
    """
    A class for detecting various types of anomalies in object behavior.

    This class provides functionality to detect multiple types of anomalies including:
    1. Speed violations - when objects exceed configured speed thresholds
    2. Unexpected stops - when objects remain stationary for too long
    3. Abnormal movements - when objects move in unexpected patterns
    4. Collision detection - when objects show signs of potential collisions

    The detection is based on configurable thresholds and parameters that can be
    customized per sensor and object type.

    :ivar AppConfig app_config: Configuration object containing anomaly detection parameters.
    :ivar CalibrationType calibration_type: Calibration type for spatial calculations.
    :ivar StopDetection stop_detection: Stop detection module for unexpected stop detection.
    """

    def __init__(self, config: AppConfig, calibration_type: CalibrationType) -> None:
        """
        Initialize the AnomalyDetector with configuration parameters.

        :param AppConfig config: Configuration object containing anomaly detection parameters
        """
        self.app_config = config
        self.calibration_type = calibration_type
        self.stop_detection = StopDetection(calibration_type=self.calibration_type)

    def detect_batch(
        self, list_of_behavior: list[Behavior], crs: CoordinateReferenceSystem | None
    ) -> tuple[list[tuple[str, str, Behavior, list[str]]], list[Behavior]]:
        """
        Process a batch of behaviors to detect anomalies.

        This method processes multiple behaviors in sequence and detects various types
        of anomalies for each behavior. It returns both potential collision information
        and anomaly messages for all detected issues.

        :param list[Behavior] list_of_behavior: List of behaviors to analyze
        :param CoordinateReferenceSystem crs: Coordinate reference system for spatial calculations
        :return tuple[list[tuple[str, str, Behavior, list[str]]], list[Behavior]]: Tuple containing:
            - List of tuples with collision information (object_id, sensor_id, behavior, trigger_modules)
            - List of behaviors with detected anomalies

        Examples::
            >>> behaviors = [Behavior(id="obj1", speed=30, timeInterval=10)]
            >>> crs = CoordinateReferenceSystem()
            >>> collisions, anomalies = detector.detect_batch(behaviors, crs)
            >>> print(f"Found {len(collisions)} potential collisions and {len(anomalies)} anomalies")
        """
        potential_collisions = []
        anomalyMessages_all = []
        for behavior in list_of_behavior:
            collision_detection_information, anomalyMessages = self.detect(behavior, crs)

            anomalyMessages_all.extend(anomalyMessages)
            if collision_detection_information["flag"]:
                potential_collisions.append(
                    (
                        behavior.id.split(" #-# ")[1],
                        behavior.sensor.id,
                        behavior,
                        collision_detection_information["trigger_modules"],
                    )
                )
        return potential_collisions, anomalyMessages_all

    def detect(self, behavior: Behavior, crs: CoordinateReferenceSystem | None) -> tuple[dict, list[Behavior]]:
        """
        Detect anomalies for a single behavior.

        This method analyzes a behavior for various types of anomalies including:
        - Speed violations
        - Unexpected stops
        - Abnormal movements
        - Potential collisions

        :param Behavior behavior: The behavior to analyze
        :param CoordinateReferenceSystem crs: Coordinate reference system for spatial calculations
        :return tuple[Dict, list[Behavior]]: Tuple containing:
            - Dictionary with collision detection information
            - List of behaviors with detected anomalies

        Examples::
            >>> behavior = Behavior(id="obj1", speed=30, timeInterval=10)
            >>> crs = CoordinateReferenceSystem()
            >>> collision_info, anomalies = detector.detect(behavior, crs)
            >>> print(f"Collision detected: {collision_info['flag']}")
            >>> print(f"Found {len(anomalies)} anomalies")
        """
        sensor_id = behavior.sensor.id
        obj_type = behavior.object.type
        collision_detection_information = {"trigger_modules": [], "flag": False}
        sensor_anomaly_config = self.app_config.get_sensor_anomaly_config(sensor_id)

        anomalyMessages = []

        if (obj_type in sensor_anomaly_config.classes) and (
            sensor_id not in sensor_anomaly_config.ignoreSensors
        ):
            self.stop_detection.set_time_window_seconds(sensor_anomaly_config.collisionDetection.stopTimeByDistanceThreshold)
            if self.calibration_type == CalibrationType.GEO:
                self.stop_detection.set_stop_distance_threshold(sensor_anomaly_config.collisionDetection.stopDistanceGEOThreshold)
            elif self.calibration_type == CalibrationType.IMAGE:
                self.stop_detection.set_stop_distance_threshold(sensor_anomaly_config.collisionDetection.stopDistanceImageThreshold)

            speed_violation_flag, speed_violation_description = self.speed_violation(
                behavior, sensor_anomaly_config.speedViolation
            )
            unexpected_stop_flag, unexpected_stop_description = self.unexpected_stop(
                behavior, sensor_anomaly_config.unexpectedStop
            )
            abnormal_movement_flag, abnormal_movement_description = self.abnormal_movement(
                behavior, crs, sensor_anomaly_config.abnormalMovement
            )
            collision_detection_flag, trigger_modules = self.collision_detection(
                behavior, abnormal_movement_flag, sensor_anomaly_config.collisionDetection
            )

            collision_detection_information = {"trigger_modules": trigger_modules, "flag": collision_detection_flag}

            if speed_violation_flag:
                behavior_speed_violation = behavior.model_copy()
                behavior_speed_violation.analyticsModule = AnalyticsModule(
                    id="Speed Anomaly Module",
                    description=speed_violation_description,
                    source="mdx",
                    version="3.0",
                    info={"clusterIndex": "-1"},
                )
                anomalyMessages.append(behavior_speed_violation)

            if unexpected_stop_flag:
                behavior_unexpected_stop = behavior.model_copy()
                behavior_unexpected_stop.analyticsModule = AnalyticsModule(
                    id="Stop Anomaly Module",
                    description=unexpected_stop_description,
                    source="mdx",
                    version="3.0",
                    info={"clusterIndex": "-1"},
                )
                anomalyMessages.append(behavior_unexpected_stop)

            if abnormal_movement_flag:
                behavior_abnormal_movement = behavior.model_copy()
                behavior_abnormal_movement.analyticsModule = AnalyticsModule(
                    id="Movement Anomaly Module",
                    description=abnormal_movement_description,
                    source="mdx",
                    version="3.0",
                    info={"clusterIndex": "-1"},
                )
                anomalyMessages.append(behavior_abnormal_movement)

        return collision_detection_information, anomalyMessages

    def speed_violation(self, behavior: Behavior, config: SpeedViolationConfig) -> tuple[bool, str]:
        """
        Detect if an object's speed exceeds the configured threshold.

        This method checks if an object's speed is above the configured threshold
        for a minimum duration.

        :param Behavior behavior: The behavior to check
        :param SpeedViolationConfig config: Configuration parameters for speed violation detection
        :return tuple[bool, str]: Tuple containing:
            - Boolean indicating if speed violation was detected
            - Description of the violation if detected

        Examples::
            >>> behavior = Behavior(id="obj1", speed=30, timeInterval=10)
            >>> config = SpeedViolationConfig(enable=True, mphThreshold=25, timeIntervalSecThreshold=5)
            >>> violation, description = detector.speed_violation(behavior, config)
            >>> print(f"Speed violation: {violation}")
            >>> print(f"Description: {description}")
        """
        description = ""
        speed_violation = False

        if (
            config.enable
            and behavior.speed > config.mphThreshold
            and behavior.timeInterval > config.timeIntervalSecThreshold
        ):
            speed_violation = True
            description = f"Speed Violation {behavior.id}, {behavior.speed} mph"
        return speed_violation, description

    def unexpected_stop(self, behavior: Behavior, config: UnexpectedStopConfig) -> tuple[bool, str]:
        """
        Detect if an object has stopped unexpectedly.

        This method checks if an object's speed is below the configured threshold
        for a minimum duration, indicating an unexpected stop.

        :param Behavior behavior: The behavior to check
        :param UnexpectedStopConfig config: Configuration parameters for unexpected stop detection
        :return tuple[bool, str]: Tuple containing:
            - Boolean indicating if unexpected stop was detected
            - Description of the stop if detected

        Examples::
            >>> behavior = Behavior(id="obj1", speed=0, timeInterval=30)
            >>> config = UnexpectedStopConfig(enable=True, mphThreshold=1, timeIntervalSecThreshold=20)
            >>> stopped, description = detector.unexpected_stop(behavior, config)
            >>> print(f"Unexpected stop: {stopped}")
            >>> print(f"Description: {description}")
        """
        description = ""
        unexpected_stop = False
        if config.enable:
            unexpected_stop = self.stop_detection.is_vehicle_stopped_by_speed(behavior, config.mphThreshold, config.timeIntervalSecThreshold)
            if unexpected_stop:
                description = f"Unexpected Stop {behavior.id}, {behavior.timeInterval} seconds"
        return unexpected_stop, description

    def collision_detection(
        self,
        behavior: Behavior,
        abnormal_movement_flag: bool,
        config: CollisionDetectionConfig,
    ) -> tuple[bool, list[str]]:
        """
        Detect potential collision events based on behavior flags and configuration.

        This method analyzes behavior patterns to detect potential collisions by checking:
        1. Stop (by distance or speed)
        2. Abnormal movements

        :param Behavior behavior: The behavior to analyze
        :param bool abnormal_movement_flag: Flag indicating abnormal movement detected
        :param CollisionDetectionConfig config: Configuration parameters for collision detection
        :return tuple[bool, list[str]]: Tuple containing:
            - Boolean indicating if collision was detected
            - List of triggered detection modules

        Examples::
            >>> behavior = Behavior(id="obj1", speed=0, timeInterval=5)
            >>> config = CollisionDetectionConfig(enable=True, stopSpeedThreshold=1, stopTimeThreshold=3)
            >>> collision, modules = detector.collision_detection(behavior, False, config)
            >>> print(f"Collision detected: {collision}")
            >>> print(f"Triggered modules: {modules}")
        """
        collision_detection_flag = False
        trigger_modules = []
        if config.enable:
            stop_by_distance_flag = False
            stop_by_speed_flag = False
            if config.useDistanceStopCondition and \
                    self.stop_detection.is_vehicle_stopped_by_distance(behavior.sensor.id, behavior.object.id):
                stop_by_distance_flag = True
                trigger_module = "Stop by distance"
                trigger_modules.append(trigger_module)
            abnormal_movement_triggered = False
            if config.useAbnormalMovementCondition and abnormal_movement_flag:
                abnormal_movement_triggered = True
                trigger_module = "Abnormal Movement"
                trigger_modules.append(trigger_module)
            if config.useSpeedStopCondition and \
                    self.stop_detection.is_vehicle_stopped_by_speed(behavior, config.stopSpeedThreshold, config.stopTimeBySpeedThreshold):
                stop_by_speed_flag = True
                trigger_module = "Stop by speed"
                trigger_modules.append(trigger_module)

            if (abnormal_movement_triggered or stop_by_distance_flag or stop_by_speed_flag):
                collision_detection_flag = True

        return collision_detection_flag, trigger_modules

    def abnormal_movement(
        self, behavior: Behavior, crs: CoordinateReferenceSystem | None, config: AbnormalMovementConfig
    ) -> tuple[bool, str]:
        """
        Detect abnormal movement patterns in object behavior.

        This method analyzes the trajectory of an object to detect abnormal movement
        patterns by:
        1. Calculating deviation from expected path
        2. Checking for sudden direction changes
        3. Comparing against configured thresholds

        :param Behavior behavior: The behavior to analyze
        :param CoordinateReferenceSystem crs: Coordinate reference system for spatial calculations
        :param AbnormalMovementConfig config: Configuration parameters for abnormal movement detection
        :return tuple[bool, str]: Tuple containing:
            - Boolean indicating if abnormal movement was detected
            - Description of the abnormal movement if detected

        Examples::
            >>> behavior = Behavior(id="obj1", timeInterval=10, distance=20)
            >>> crs = CoordinateReferenceSystem()
            >>> config = AbnormalMovementConfig(enable=True, timeIntervalSecThreshold=5, distanceMetersThreshold=10)
            >>> abnormal, description = detector.abnormal_movement(behavior, crs, config)
            >>> print(f"Abnormal movement: {abnormal}")
            >>> print(f"Description: {description}")
        """
        description = ""
        abnormal_movement = False

        if config.enable:
            distance = behavior.distance
            if config.useLinearDistance:
                head = behavior.smoothLocations.coordinates[0].point
                last = behavior.smoothLocations.coordinates[-1].point
                if self.app_config.traj_geo_coord_enable:
                    distance = haversine_distance(
                        Location(lat=head[1], lon=head[0]), Location(lat=last[1], lon=last[0])
                    )
                else:
                    distance = euclidean_distance(Coordinate(x=head[0], y=head[1]), Coordinate(x=last[0], y=last[1]))

            if behavior.timeInterval > config.timeIntervalSecThreshold and distance > config.distanceMetersThreshold:

                # construct forward and reverse trajectory and calculate the deviation
                deviation, rDeviation, actualT = self.calculateDeviation(
                    behavior,
                    crs,
                    step=config.strideDeviationComputation,
                    distanceThreshold=config.distanceMetersThresholdDeviationComputation,
                )

                # deviation is within a range
                b = (
                    (rDeviation != 0)
                    and (deviation > config.abnormalRelativeThreshold * rDeviation)
                    and (deviation < config.abnormalRelativeMaxThreshold * rDeviation)
                )

                chInDir = False
                diff = 0.0
                if b and config.abnormalRelativeDirectionChangeEnable:
                    chInDir, diff = self.changeInDirection(
                        actualT, changeInDirectionDegree=config.changeInDirectionDegree
                    )

                relativeDeviation = None
                try:
                    relativeDeviation = round(deviation / rDeviation, 2)
                except (ZeroDivisionError, TypeError) as e:
                    logger.debug(f"relativeDeviation calculation skipped: {e}")
                relativeChDir = round(diff, 2)

                detail = f"deviation {relativeDeviation}, direction change {relativeChDir}"
                description = f"Abnormal Movement {behavior.id}, {behavior.timeInterval} seconds, {detail}"

                abnormal_movement = b and not chInDir

        return abnormal_movement, description

    def calculateDeviation(
        self,
        behavior: Behavior,
        crs: CoordinateReferenceSystem | None = None,
        step: int = 5,
        distanceThreshold: float = 10.0
    ) -> tuple[float, float, Trajectory]:
        """
        Calculate the deviation of a trajectory from its expected path.

        This method:
        1. Samples points from the trajectory at regular intervals
        2. Constructs forward and reverse trajectories
        3. Calculates deviation using map matching if available

        :param Behavior behavior: The behavior containing the trajectory
        :param CoordinateReferenceSystem crs: Optional coordinate reference system for map matching
        :param int step: Step size for sampling trajectory points
        :param float distanceThreshold: Minimum distance threshold for point selection
        :return tuple[float, float, Trajectory]: Tuple containing:
            - Deviation value
            - Reverse deviation value
            - Actual trajectory object

        Examples::
            >>> behavior = Behavior(id="obj1")
            >>> crs = CoordinateReferenceSystem()
            >>> deviation, r_deviation, trajectory = detector.calculateDeviation(behavior, crs, step=5)
            >>> print(f"Deviation: {deviation}, Reverse deviation: {r_deviation}")
        """
        slicePoints = [
            behavior.smoothLocations.coordinates[i].point
            for i in range(0, len(behavior.smoothLocations.coordinates), step)
        ] + [behavior.smoothLocations.coordinates[-1].point]
        # val slicePoints = smPoints.grouped(step).map(x => x.head).toList.:+(smPoints.last)

        candidatePoints = []  # list[Coordinate]
        candidatePoints.append(slicePoints[0])

        prev_pt = candidatePoints[0]
        for i in range(len(slicePoints) - 1):
            curr_pt = slicePoints[i + 1]
            # if distance between current and previous is > distanceThreshold (20 meters)
            # keep the point, else discard the point
            if self.app_config.traj_geo_coord_enable:
                d = haversine_distance(
                    Location(lat=prev_pt[1], lon=prev_pt[0]), Location(lat=curr_pt[1], lon=curr_pt[0])
                )
            else:
                d = euclidean_distance(Coordinate(x=prev_pt[0], y=prev_pt[1]), Coordinate(x=curr_pt[0], y=curr_pt[1]))

            if d > distanceThreshold:
                candidatePoints.append(curr_pt)
                prev_pt = curr_pt

        # Construct forward and reverse trajectory.
        candidatePoints = [Coordinate(x=point[0], y=point[1]) for point in candidatePoints]
        actualT = Trajectory(
            id=behavior.id,
            start=behavior.timestamp,
            end=behavior.end,
            points=candidatePoints,
            smooth_min_points=len(candidatePoints) + 1,
            distance_stride=1,
            enable_geo=self.app_config.traj_geo_coord_enable,
        )
        candidatePointsR = candidatePoints[::-1]
        reverseT = Trajectory(
            id=behavior.id,
            start=behavior.timestamp,
            end=behavior.end,
            points=candidatePointsR,
            smooth_min_points=len(candidatePointsR) + 1,
            distance_stride=1,
            enable_geo=self.app_config.traj_geo_coord_enable,
        )

        deviation = 0.0
        rDeviation = 0.0

        if crs and self.app_config.traj_geo_coord_enable:
            actualT_trajectory = [(coord.y, coord.x) for coord in actualT.smooth_trajectory]
            actualT_snappedTrajectory = crs.map_matching_latlon(actualT_trajectory, exclude_non_emitting_state=False)
            reverseT_trajectory = [(coord.y, coord.x) for coord in reverseT.smooth_trajectory]
            reverseT_snappedTrajectory = crs.map_matching_latlon(reverseT_trajectory, exclude_non_emitting_state=False)
            try:
                deviation = (
                    sum(
                        [
                            min(
                                [
                                    haversine_distance(
                                        Location(
                                            lat=actualT.smooth_trajectory[i].y, lon=actualT.smooth_trajectory[i].x
                                        ),
                                        Location(
                                            lat=actualT_snappedTrajectory[k][0], lon=actualT_snappedTrajectory[k][1]
                                        ),
                                    )
                                    for k in range(len(actualT_snappedTrajectory))
                                ]
                            )
                            for i in range(len(actualT.smooth_trajectory))
                        ]
                    )
                    / actualT.distance
                )

                rDeviation = (
                    sum(
                        [
                            min(
                                [
                                    haversine_distance(
                                        Location(
                                            lat=reverseT.smooth_trajectory[i].y, lon=reverseT.smooth_trajectory[i].x
                                        ),
                                        Location(
                                            lat=reverseT_snappedTrajectory[k][0], lon=reverseT_snappedTrajectory[k][1]
                                        ),
                                    )
                                    for k in range(len(reverseT_snappedTrajectory))
                                ]
                            )
                            for i in range(len(reverseT.smooth_trajectory))
                        ]
                    )
                    / reverseT.distance
                )
            except Exception as e:
                logger.debug(f"deviation/rDeviation calculation failed (likely mapmatching has no candidates): {e}")

        return deviation, rDeviation, actualT

    def changeInDirection(self, tr: Trajectory, changeInDirectionDegree: float) -> tuple[bool, float]:
        """
        Detect significant changes in direction along a trajectory.

        :param Trajectory tr: The trajectory to analyze
        :param float changeInDirectionDegree: Threshold for significant direction change
        :return tuple[bool, float]: Tuple containing:
            - Boolean indicating if significant direction change was detected
            - Maximum direction change in degrees

        Examples::
            >>> trajectory = Trajectory(id="tr1", points=[Point2D(x=0, y=0), Point2D(x=1, y=1)])
            >>> changed, max_change = detector.changeInDirection(trajectory, changeInDirectionDegree=45)
            >>> print(f"Direction changed: {changed}")
            >>> print(f"Maximum change: {max_change} degrees")
        """
        chInDir = False
        diff = 0.0
        diff, dList = self.consecutiveBearingDiff(tr)
        if diff > changeInDirectionDegree:
            chInDir = True
        return chInDir, diff

    def consecutiveBearingDiff(self, tr: Trajectory) -> tuple[float, list[float]]:
        """
        Calculate bearing differences between consecutive points in a trajectory.

        :param Trajectory tr: The trajectory to analyze
        :return tuple[float, list[float]]: Tuple containing:
            - Maximum bearing difference in degrees
            - List of all bearing differences

        Examples::
            >>> trajectory = Trajectory(id="tr1", points=[Point2D(x=0, y=0), Point2D(x=1, y=1)])
            >>> max_diff, diffs = detector.consecutiveBearingDiff(trajectory)
            >>> print(f"Maximum bearing difference: {max_diff} degrees")
            >>> print(f"All differences: {diffs}")
        """
        diff = 0.0
        dList = []
        brngs = [
            bearing(
                Location(lat=tr.points[i].y, lon=tr.points[i].x),
                Location(lat=tr.points[i + 1].y, lon=tr.points[i + 1].x),
            )
            for i in range(len(tr.points) - 1)
        ]
        # at least 3 bearings
        if len(brngs) < 3:
            diff = 0.1
            dList = [0.1]
        else:
            # min = min(brngs)
            dList = []
            for i in range(len(brngs) - 1):
                b1 = brngs[i]
                b2 = brngs[i + 1]
                d = abs(b1 - b2)
                d = d if d < 180 else 360 - d
                dList.append(d)

            diff = max(dList)

        return diff, dList
