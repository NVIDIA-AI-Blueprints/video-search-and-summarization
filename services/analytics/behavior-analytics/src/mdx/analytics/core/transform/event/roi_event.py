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


from mdx.analytics.core.constants import ROIDirection
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import ROI, Point2D
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase
from mdx.analytics.core.transform.event.base_event import BaseEvent


class ROIEvent(BaseEvent[ROI]):
    """
    Event detection class for Region of Interest (ROI) interactions.

    This class extends BaseEvent to detect when objects enter or exit ROIs.
    It uses polygon-based detection to determine if points are inside ROIs
    and tracks transitions between inside/outside states.

    :ivar AppConfig config: Configuration object for the application.
    :ivar CalibrationBase calibration: Calibration object containing ROI definitions.

    Examples::
        >>> config = AppConfig()
        >>> calibration = CalibrationBase(config, "calibration.json")
        >>> roi_detector = ROIEvent(config, calibration)
        >>> behavior = Behavior(
        ...     id="obj1",
        ...     locations=GeoLocation(coordinates=[
        ...         Coordinate(point=[1.0, 1.0]),
        ...         Coordinate(point=[2.0, 2.0])
        ...     ]),
        ...     sensor=SensorInfo(id="sensor1")
        ... )
        >>> events = roi_detector.get_events(behavior)
        >>> for event in events:
        ...     print(f"ROI event: {event.event.type} at {event.timestamp}")
    """

    def __init__(self, config: AppConfig, calibration: CalibrationBase) -> None:
        """
        Initialize the ROIEvent detector.

        :param AppConfig config: Configuration object for the application
        :param CalibrationBase calibration: Calibration object containing ROI definitions

        Examples::
            >>> config = AppConfig()
            >>> calibration = CalibrationBase(config, "calibration.json")
            >>> roi_detector = ROIEvent(config, calibration)
        """
        super().__init__(config, calibration, ROIDirection, "roi", "ROIEvent")

    def _check_point(self, point: Point2D, sensor_id: str, obj_id: str) -> bool:
        """
        Check if a point is inside a specific ROI.

        :param Point2D point: The point to check
        :param str sensor_id: ID of the sensor associated with the ROI
        :param str obj_id: ID of the ROI to check against
        :return bool: True if the point is inside the ROI, False otherwise

        Examples::
            >>> point = Point2D(x=1.0, y=1.0)
            >>> is_inside = roi_detector._check_point(point, "sensor1", "roi1")
            >>> print(f"Point is {'inside' if is_inside else 'outside'} ROI")
        """
        return self.calibration.point_in_polygon(point, sensor_id, obj_id)

    def _get_objects(self, sensor_id: str) -> list[ROI]:
        """
        Get all ROIs associated with a sensor.

        :param str sensor_id: ID of the sensor to get ROIs for
        :return list[ROI]: List of ROIs associated with the sensor

        Examples::
            >>> rois = roi_detector._get_objects("sensor1")
            >>> print(f"Found {len(rois)} ROIs for sensor1")
            >>> for roi in rois:
            ...     print(f"ROI ID: {roi.id}")
        """
        return self.calibration.sensor_map[sensor_id].rois

    def _intersect(self, trip: list[Point2D], sensor_id: str, obj_id: str) -> bool:
        """
        Check if a trajectory intersects with an ROI by checking if the start and end points
        are on different sides of the ROI boundary.

        :param list[Point2D] trip: The trajectory to check
        :param str sensor_id: ID of the sensor associated with the ROI
        :param str obj_id: ID of the ROI to check against
        :return bool: True if the trajectory crosses the ROI boundary, False otherwise

        Examples::
            >>> trip = [Point2D(x=1.0, y=1.0), Point2D(x=2.0, y=2.0)]
            >>> intersects = roi_detector._intersect(trip, "sensor1", "roi1")
            >>> print(f"Trajectory {'intersects' if intersects else 'does not intersect'} ROI")
        """
        start_position = self._check_point(trip[0], sensor_id, obj_id)
        end_position = self._check_point(trip[-1], sensor_id, obj_id)
        return start_position != end_position
