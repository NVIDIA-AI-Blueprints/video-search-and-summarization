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

from mdx.analytics.core.schema.models import Bbox, Coordinate, Location, Bbox3d
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationType
from mdx.analytics.core.utils.distance_util import get_lat_lon

logger = logging.getLogger(__name__)


class CalibrationI(CalibrationBase):
    """
    CalibrationI class that reads the calibration information from a JSON config file
    and instantiates the transform matrix, ROI, etc., per sensor.

    This class extends CalibrationBase to provide specific implementations for:
    - Image coordinate transformations without perspective transformation
    - Basic ROI handling
    - Frame processing with minimal transformations

    Unlike other calibration classes, this implementation does not use transform matrices
    and works directly with image coordinates. It is typically used when only basic
    coordinate mapping is required without perspective correction.

    :ivar AppConfig config: Configuration object for the application
    :ivar str calibration_file_path: Path to the calibration configuration file
    :ivar dict[str, Any] calibration_info: Dictionary containing the parsed calibration configuration
    :ivar list[SensorInfo] sensors: List of sensors read from the calibration config file
    :ivar list[ROI] global_rois: List of global ROIs read from the calibration config file
    :ivar list[Tripwire] global_tripwires: List of global tripwires read from the calibration config file
    :ivar dict[str, SensorInfo] sensor_map: Map of sensorId to sensor
    :ivar dict[str, ROI] global_roi_map: Map of ROI IDs to ROI objects
    :ivar dict[str, Tripwire] global_tripwire_map: Map of tripwire IDs to Tripwire objects

    Examples::
        >>> config = AppConfig()
        >>> calibration = CalibrationI(config, "calibration.json")
        >>> print(f"Initialized image-based calibration with {len(calibration.sensors)} sensors")
    """

    def transform_bbox(self, bbox: Bbox | Bbox3d, sensor_id: str) -> tuple[Coordinate, Location]:
        """
        Transform image 2d bounding box coordinates to coordinate and location. 3d bounding box currently not supported for image-based calibration.

        This method performs a basic transformation of 2d bounding box coordinates without perspective correction.
        The location point calculation mode is controlled by the config.image_location_mode setting (image coordinate system specific):
        - "center": Uses the center point of the bounding box (center X, center Y) to calculate location
        - "bottom_center": Uses the center of the bbox bottom edge (center X, bottom Y) to calculate location [default]

        :param Bbox | Bbox3d bbox: The bounding box to transform.
        :param str sensor_id: The sensor ID to use for transformation.
        :return tuple[Coordinate, Location]: Tuple containing the transformed coordinate and location.

        Examples::
            >>> calibration = CalibrationI(config, calibration_file_path)
            >>> bbox = Bbox(leftX=100, rightX=200, topY=50, bottomY=150)
            >>> coord, loc = calibration.transform_bbox(bbox, "sensor1")
            >>> print(f"Transformed to: {coord.x}, {coord.y} at {loc.latitude}, {loc.longitude}")
        """
        if not isinstance(bbox, Bbox):
            raise ValueError(f"CalibrationI only supports 2D bounding boxes (Bbox), got {type(bbox).__name__}")
        latlon_origin = self.sensor_map[sensor_id].origin if self.contains(sensor_id) else Location(lat=0.0, lon=0.0)
        
        # Calculate px (center X) - always the same
        px = (bbox.rightX + bbox.leftX) / 2.0
        
        # Calculate py based on configuration (image coordinate system specific)
        # This determines which point from the bbox is used to calculate the location
        image_location_mode = self.config.image_location_mode
        if image_location_mode == "center":
            # Use center of bbox (center X, center Y)
            py = (bbox.topY + bbox.bottomY) / 2.0
        else:
            # Default to bottom center (center X, bottom Y)
            # This maintains backward compatibility with previous behavior
            py = bbox.bottomY
        
        coordinate = Coordinate(x=px, y=py, z=0)
        latlon = get_lat_lon(latlon_origin, coordinate)
        return coordinate, latlon

    def filter_frames_by_sensor_id(self, input_frames: list[nvSchema.Frame]) -> list[nvSchema.Frame]:
        """
        Return all input frames without filtering.
        
        Unlike other calibration classes, CalibrationI does not filter frames by sensor ID.
        All input frames are returned as-is.

        :param list[nvSchema.Frame] input_frames: List of frames to process.
        :return list[nvSchema.Frame]: All input frames, unfiltered.
        """
        logger.info("Frames won't be filtered by sensor ID in CalibrationI")
        return input_frames

    @property
    def calibration_type(self) -> CalibrationType:
        """Return IMAGE calibration type."""
        return CalibrationType.IMAGE