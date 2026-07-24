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

from mdx.analytics.core.constants import ROIDirection
from mdx.analytics.core.schema.config import AppConfig, ROI_EVENT_DETECTION_MODE_BBOX
from mdx.analytics.core.schema.models import ROI, Bbox, Point2D
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase, CalibrationType
from mdx.analytics.core.transform.event.base_event import BaseEvent

logger = logging.getLogger(__name__)


class ROIEvent(BaseEvent[ROI]):
    """
    Event detection class for Region of Interest (ROI) interactions.

    This class extends BaseEvent to detect when objects enter or exit ROIs, tracking transitions between
    inside/outside states. The inside/outside test depends on ``config.roi_event_detection_mode``: the
    default ``"coordinate"`` checks whether the object's representative point is inside the ROI polygon,
    while ``"bbox"`` (image calibration only) checks whether the object's per-frame bounding box overlaps
    the ROI polygon.

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
        # Latches the one-time warning emitted when bbox mode is requested on a
        # non-image calibration (see :meth:`_bbox_mode_enabled`).
        self._warned_unsupported_bbox = False

    def _bbox_mode_enabled(self) -> bool:
        """
        Decide whether ROI ENTRY/EXIT detection should use bbox-overlap instead of coordinate-inside.

        Bbox mode (``roiEventDetectionMode == "bbox"``) is supported only for image calibration
        (:class:`CalibrationType.IMAGE`), where ``object.bbox`` and the ROI polygon share image-pixel
        coordinates. For cartesian and geo calibration the trajectory point and ROI polygon live in world
        units (metres / lat-lon) while ``object.bbox`` stays in image pixels, so a bbox overlap would be a
        unit mismatch; those fall back to the coordinate-inside check and log a one-time warning.

        :return bool: True if bbox-overlap detection is enabled and valid for the current calibration.
        """
        if self.config.roi_event_detection_mode != ROI_EVENT_DETECTION_MODE_BBOX:
            return False
        if self.calibration.calibration_type == CalibrationType.IMAGE:
            return True
        if not self._warned_unsupported_bbox:
            logger.warning(
                "roiEventDetectionMode='bbox' is not supported for calibration type %s; only image "
                "calibration is supported. Falling back to the coordinate-inside check.",
                self.calibration.calibration_type.value,
            )
            self._warned_unsupported_bbox = True
        return False

    def _is_inside(
        self, point: Point2D, sensor_id: str, obj_id: str, bbox: Bbox | None = None
    ) -> bool:
        """
        Check if the object is inside a specific ROI at the given trajectory point.

        When ``config.roi_event_detection_mode`` is ``"coordinate"`` (the default) this checks whether
        ``point`` -- the object's representative coordinate -- lies inside the ROI polygon. When it is
        ``"bbox"`` and a per-frame ``bbox`` is supplied, it instead checks whether that bounding box
        overlaps the ROI polygon (:meth:`CalibrationBase.bbox_overlaps_polygon`). Bbox mode is supported
        only for image calibration; see :meth:`_bbox_mode_enabled`.

        :param Point2D point: The point to check
        :param str sensor_id: ID of the sensor associated with the ROI
        :param str obj_id: ID of the ROI to check against
        :param Bbox | None bbox: The object's per-frame bounding box at this point, used for the overlap
            test in bbox mode; ignored in the default coordinate mode.
        :return bool: True if the object is inside/overlapping the ROI, False otherwise

        Examples::
            >>> point = Point2D(x=1.0, y=1.0)
            >>> is_inside = roi_detector._is_inside(point, "sensor1", "roi1")
            >>> print(f"Point is {'inside' if is_inside else 'outside'} ROI")
        """
        if bbox is not None and self._bbox_mode_enabled():
            return self.calibration.bbox_overlaps_polygon(bbox, sensor_id, obj_id, point)
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

    def _crosses(
        self, trip: list[Point2D], sensor_id: str, obj_id: str, bboxes: list[Bbox] | None = None
    ) -> bool:
        """
        Check if a trajectory intersects with an ROI by checking if the start and end points
        are on different sides of the ROI boundary.

        The inside/outside test at the start and end points uses the same mode as
        :meth:`_is_inside` (coordinate-inside by default, bbox-overlap when configured), so the crossing
        check stays consistent with the per-point positions computed in ``get_events``. In bbox mode the
        start/end points use their own per-frame bboxes from ``bboxes``.

        :param list[Point2D] trip: The trajectory to check
        :param str sensor_id: ID of the sensor associated with the ROI
        :param str obj_id: ID of the ROI to check against
        :param list[Bbox] | None bboxes: Per-frame bboxes aligned 1:1 with ``trip``; the endpoints' boxes
            are forwarded to :meth:`_is_inside`.
        :return bool: True if the trajectory crosses the ROI boundary, False otherwise

        Examples::
            >>> trip = [Point2D(x=1.0, y=1.0), Point2D(x=2.0, y=2.0)]
            >>> intersects = roi_detector._crosses(trip, "sensor1", "roi1")
            >>> print(f"Trajectory {'intersects' if intersects else 'does not intersect'} ROI")
        """
        start_bbox = bboxes[0] if bboxes else None
        end_bbox = bboxes[-1] if bboxes else None
        start_position = self._is_inside(trip[0], sensor_id, obj_id, start_bbox)
        end_position = self._is_inside(trip[-1], sensor_id, obj_id, end_bbox)
        return start_position != end_position
