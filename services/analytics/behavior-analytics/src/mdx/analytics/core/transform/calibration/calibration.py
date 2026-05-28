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


from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Bbox, Coordinate, Location
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase, CalibrationType
from mdx.analytics.core.utils.distance_util import lonlat_to_xy, xy_to_lonlat
from mdx.analytics.core.utils.schema_util import coordinate_to_nv_coordinate, location_to_nv_location, nv_bbox_to_bbox
from mdx.analytics.core.utils.util import extract_sensor_id


class Calibration(CalibrationBase):
    """
    Calibration class that reads the calibration information from a JSON calibration file
    and instantiates the transform matrix, ROI, etc., per sensor.
    It can deal with different coordinate reference system defined in config file, mainly latlons.

    This class extends CalibrationBase to provide specific implementations for:
    - Coordinate system transformations (latlon to cartesian and vice versa)
    - Custom origin handling for cartesian coordinates
    - Bounding box transformations with coordinate system awareness

    :ivar AppConfig config: Configuration object for the application.
    :ivar str calibration_file_path: Path to the calibration configuration file.
    :ivar str crs_latlon: Coordinate reference system for lat/lon coordinates.
    :ivar str crs_cartesian: Coordinate reference system for cartesian coordinates.
    :ivar bool input_data_in_cartesian: Whether input data is in cartesian coordinates.
    :ivar bool use_calibration_origin: Whether to use per-sensor calibration origin.
    :ivar tuple[float, float] crs_cartesian_custom_origin_cartesian: Custom origin for cartesian coordinates.

    Examples::
        >>> config = AppConfig()
        >>> calibration = Calibration(config, "calibration.json")
    """

    def __init__(self, config: AppConfig, calibration_file_path: str) -> None:
        """
        Initialize the Calibration instance with coordinate system settings.

        :param AppConfig config: Configuration object containing coordinate system settings.
        :param str calibration_file_path: Path to the calibration configuration file.
        :return: None

        Examples::
            >>> config = AppConfig()
            >>> calibration = Calibration(config, "calibration.json")
        """
        super().__init__(config, calibration_file_path)
        self.crs_latlon = self.config.coordinateReferenceSystem.crsLatLon
        self.crs_cartesian = self.config.coordinateReferenceSystem.crsCartesian
        self.input_data_in_cartesian = self.config.coordinateReferenceSystem.inputDataInCRSCartesian
        self.use_calibration_origin = self.config.coordinateReferenceSystem.crsCartesianEnablePerSensorOrigin
        self.crs_cartesian_custom_origin_cartesian = None

        if self.config.coordinateReferenceSystem.crsCartesianCustomOrigin.enable and not self.use_calibration_origin:
            lat = self.config.coordinateReferenceSystem.crsCartesianCustomOrigin.lat
            lon = self.config.coordinateReferenceSystem.crsCartesianCustomOrigin.lon
            x, y = lonlat_to_xy(lon, lat, crs_lonlat=self.crs_latlon, crs_xy=self.crs_cartesian)
            self.crs_cartesian_custom_origin_cartesian = (x, y)

    def transform_bbox(self, bbox: Bbox, sensor_id: str) -> tuple[Coordinate, Location]:
        """
        Transform image coordinates bbox to real-world coordinates and location.

        This method handles four different coordinate transformation cases:
        1. Input in latlon, output in latlon
        2. Input in latlon, output in cartesian
        3. Input in cartesian, output in latlon
        4. Input in cartesian, output in cartesian

        :param Bbox bbox: The bounding box to transform
        :param str sensor_id: The sensor ID to use for transformation
        :return tuple[Coordinate, Location]: Tuple containing the transformed coordinate and location

        Examples::
            >>> calibration = Calibration(config, calibration_file_path)
            >>> bbox = Bbox(leftX=100, rightX=200, topY=50, bottomY=150)
            >>> coord, loc = calibration.transform_bbox(bbox, "sensor1")
            >>> print(f"Transformed to: {coord.x}, {coord.y} at {loc.latitude}, {loc.longitude}")
        """

        # NOTE: the return coordinate and latlon have identical values in all cases.
        # The existence of both is for historical reasons.
        px, py = ((bbox.rightX + bbox.leftX) / 2.0, max(bbox.topY, bbox.bottomY))
        if self.contains(sensor_id):
            latlon_origin, h_matrix = self.sensor_map[sensor_id].origin, self.sensor_map[sensor_id].homography
            global_coords = self.perspective_transform(px, py, h_matrix)
            if global_coords:
                lon, lat = global_coords
            else:
                lon, lat = px, py
            # Case1&2: input is in latlon
            if not self.input_data_in_cartesian:
                if self.config.traj_geo_coord_enable:
                    # Case1: input is in latlon and output is in latlon

                    coordinate = Coordinate(x=lon, y=lat)
                    latlon = Location(lat=lat, lon=lon)
                else:
                    # Case2: input is in latlon and output is in xy
                    global_coord_x, global_coord_y = lonlat_to_xy(
                        lon, lat, crs_lonlat=self.crs_latlon, crs_xy=self.crs_cartesian
                    )
                    origin_crs = None
                    if self.use_calibration_origin:
                        x, y = lonlat_to_xy(
                            latlon_origin.lon, latlon_origin.lat, crs_lonlat=self.crs_latlon, crs_xy=self.crs_cartesian
                        )
                        origin_crs = (x, y)
                    else:
                        if self.crs_cartesian_custom_origin_cartesian:
                            origin_crs = self.crs_cartesian_custom_origin_cartesian
                    if origin_crs:
                        global_coord_x += -origin_crs[0]
                        global_coord_y += -origin_crs[1]
                    coordinate = Coordinate(x=global_coord_x, y=global_coord_y)
                    latlon = Location(lat=global_coord_y, lon=global_coord_x)
            # Case3&4: input is in xy
            else:

                if self.config.traj_geo_coord_enable:
                    # Case3: input is in xy and output is in latlon
                    global_coord_x, global_coord_y = lon, lat
                    origin_crs = None
                    if self.use_calibration_origin:
                        x, y = lonlat_to_xy(
                            latlon_origin.lon, latlon_origin.lat, crs_lonlat=self.crs_latlon, crs_xy=self.crs_cartesian
                        )
                        origin_crs = (x, y)
                    else:
                        if self.crs_cartesian_custom_origin_cartesian:
                            origin_crs = self.crs_cartesian_custom_origin_cartesian
                    if origin_crs:
                        global_coord_x += origin_crs[0]
                        global_coord_y += origin_crs[1]
                    lon, lat = xy_to_lonlat(
                        global_coord_x, global_coord_y, crs_lonlat=self.crs_latlon, crs_xy=self.crs_cartesian
                    )
                    coordinate = Coordinate(x=lon, y=lat)
                    latlon = Location(lat=lat, lon=lon)
                else:
                    # Case4: input is in xy and output is in xy
                    global_coord_x, global_coord_y = lon, lat
                    coordinate = Coordinate(x=global_coord_x, y=global_coord_y)
                    latlon = Location(lat=global_coord_y, lon=global_coord_x)
        else:
            coordinate = Coordinate(x=px, y=py, z=0)
            latlon = Location(lat=0, lon=0)

        return coordinate, latlon

    def transform_frame(self, frame: nvSchema.Frame) -> nvSchema.Frame:
        """
        Transform and enhance frames with ROI count and coordinate system information.

        This method:
        1. Transforms object coordinates based on the configured coordinate system
        2. Updates ROI metrics for the frame
        3. Creates field of view (FOV) information
        4. Optionally creates compact frame with only ROI-contained objects

        :param nvSchema.Frame frame: The frame to transform and enhance
        :return nvSchema.Frame: Updated frame with transformed coordinates and enhanced information

        Examples::
            >>> calibration = Calibration(config, calibration_file_path)
            >>> frame = nvSchema.Frame(sensorId="sensor1", objects=[...])
            >>> transformed_frame = calibration.transform_frame(frame)
            >>> print(f"Transformed frame for sensor {transformed_frame.sensorId}")
        """
        sensor_id = frame.sensorId
        sensor_id = extract_sensor_id(sensor_id)

        updated_objects = []
        for obj in frame.objects:
            coor, loc = self.transform_bbox(nv_bbox_to_bbox(obj.bbox), sensor_id)
            obj.coordinate.CopyFrom(coordinate_to_nv_coordinate(coor))
            obj.location.CopyFrom(location_to_nv_location(loc))
            updated_objects.append(obj)

        # object id, type, bbox coordinate
        points = [(obj.id, obj.type, obj.coordinate) for obj in updated_objects]

        # Get rois
        rois = self.get_roi_metrics(points, sensor_id)

        # Get fov
        fov = self.get_fov(points)

        # Creating the 'info' dictionary including place
        if self.contains(sensor_id):
            sensor_c = self.sensor_map[sensor_id]
            place_name = "/".join([f"{x['name']}={x['value']}" for x in sensor_c.place])
            info = {"place": place_name}
        else:
            info = {"place": ""}

        if self.config.compact_frame:
            # Include objects inside rois for compact objects
            object_ids = {id for roi in rois for id in roi.objectIds}
            compact_objects = [obj for obj in updated_objects if obj.id in object_ids]
        else:
            compact_objects = updated_objects

        # Return the Frame object
        return nvSchema.Frame(
            version=frame.version,
            id=frame.id,
            timestamp=frame.timestamp,
            sensorId=sensor_id,
            objects=compact_objects,
            fov=fov,
            rois=rois,
            info=info,
        )

    @property
    def calibration_type(self) -> CalibrationType:
        """Return GEO calibration type."""
        return CalibrationType.GEO
