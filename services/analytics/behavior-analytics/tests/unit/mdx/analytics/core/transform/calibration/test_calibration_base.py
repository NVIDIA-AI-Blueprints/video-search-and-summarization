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

import unittest
from datetime import datetime
import numpy as np
from unittest.mock import MagicMock, patch

from mdx.analytics.core.constants import (
    CALIBRATION_ACTION_DELETE,
    CALIBRATION_ACTION_UPSERT,
    CALIBRATION_ACTION_UPSERT_ALL,
    SENSOR_TYPE_CAMERA,
    SENSOR_TYPE_GROUP,
)
from mdx.analytics.core.schema.models import (
    Bbox, Coordinate, Location, Message, Sensor, SensorInfo, ROI, Point2D
)
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase, CalibrationType
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.utils.io_utils import load_json_from_file


class TestCalibrationBase(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.config = AppConfig(**load_json_from_file("tests/unit/resources/test_config.json"))
        calibration_path = "tests/unit/resources/calibration_building_k.json"
        self.calibration = CalibrationBase(self.config, calibration_path)

    def test_initialization(self):
        """Test CalibrationBase initialization."""
        self.assertIsInstance(self.calibration, CalibrationBase)
        self.assertEqual(self.calibration.config, self.config)

    def test_contains(self):
        """Test contains method."""
        # Test with non-existent sensor
        self.assertFalse(self.calibration.contains("non_existent_sensor"))

        # Test with existing sensor
        sensor_id = "test_sensor"
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.homography = None
        mock_sensor.imageCoordinates = []
        mock_sensor.globalCoordinates = []
        mock_sensor.roiPolygons = {}

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            self.assertTrue(self.calibration.contains(sensor_id))

    def test_point_in_polygon(self):
        """Test point_in_polygon method."""
        sensor_id = "test_sensor"
        roi_id = "test_roi"
        point = MagicMock(x=100, y=100)

        # Mock sensor and ROI polygon
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.homography = None
        mock_sensor.imageCoordinates = []
        mock_sensor.globalCoordinates = []

        mock_polygon = MagicMock()
        mock_polygon.contains_point.return_value = True
        mock_sensor.roiPolygons = {roi_id: mock_polygon}

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            # Test point inside ROI
            self.assertTrue(self.calibration.point_in_polygon(point, sensor_id, roi_id))
            mock_polygon.contains_point.assert_called_once_with((point.x, point.y))

            # Test point outside ROI
            mock_polygon.contains_point.return_value = False
            self.assertFalse(self.calibration.point_in_polygon(point, sensor_id, roi_id))

    def test_get_roi_metrics(self):
        """Test get_roi_metrics method."""
        sensor_id = "test_sensor"
        points: list[tuple[str, str, nvSchema.Coordinate]] = [
            ("obj1", "person", nvSchema.Coordinate(x=100, y=100)),
            ("obj2", "car", nvSchema.Coordinate(x=200, y=200)),
        ]

        # Mock sensor and ROI polygon
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.homography = None
        mock_sensor.imageCoordinates = []
        mock_sensor.globalCoordinates = []

        mock_polygon = MagicMock()
        mock_polygon.contains_point.return_value = True
        mock_sensor.roiPolygons = {"roi1": mock_polygon}

        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            # Test ROI metrics
            metrics = self.calibration.get_roi_metrics(points, sensor_id)
            self.assertEqual(len(metrics), 2)  # One for each object type

            # Check both metrics without assuming order
            metric_types = {m.type for m in metrics}
            self.assertEqual(metric_types, {"person", "car"})

            # Check counts
            for metric in metrics:
                if metric.type == "person":
                    self.assertEqual(metric.count, 1)
                elif metric.type == "car":
                    self.assertEqual(metric.count, 1)

    def test_get_fov(self):
        """Test get_fov method."""
        points: list[tuple[str, str, nvSchema.Coordinate]] = [
            ("obj1", "person", nvSchema.Coordinate(x=100, y=100)),
            ("obj2", "person", nvSchema.Coordinate(x=200, y=200)),
            ("obj3", "car", nvSchema.Coordinate(x=300, y=300)),
        ]

        # Test FOV metrics
        metrics = self.calibration.get_fov(points)
        self.assertEqual(len(metrics), 2)  # One for each object type
        self.assertEqual(metrics[0].type, "person")
        self.assertEqual(metrics[0].count, 2)
        self.assertEqual(metrics[1].type, "car")
        self.assertEqual(metrics[1].count, 1)

    def test_update_calibration_info(self):
        """Test update_calibration_info method."""
        # Test upsert-all action
        new_info = {"sensors": [{"id": "sensor1", "type": "camera"}]}
        self.calibration.update_calibration_info(new_info, CALIBRATION_ACTION_UPSERT_ALL)
        self.assertEqual(self.calibration.calibration_info, new_info)

        # Test upsert action -- replace existing sensor
        self.calibration.calibration_info = {"sensors": [{"id": "sensor1", "type": "camera"}]}
        new_info = {"sensors": [{"id": "sensor1", "type": "camera", "new_field": "value"}]}
        self.calibration.update_calibration_info(new_info, CALIBRATION_ACTION_UPSERT)
        self.assertEqual(self.calibration.calibration_info["sensors"][0]["new_field"], "value")

        # Test upsert action -- add brand-new sensor (must not be silently dropped)
        self.calibration.calibration_info = {"sensors": [{"id": "sensor1", "type": "camera"}]}
        new_info = {"sensors": [{"id": "sensor2", "type": "camera"}]}
        self.calibration.update_calibration_info(new_info, CALIBRATION_ACTION_UPSERT)
        sensor_ids = sorted(s["id"] for s in self.calibration.calibration_info["sensors"])
        self.assertEqual(sensor_ids, ["sensor1", "sensor2"])

        # Test upsert action -- mix of replace + add in one payload
        self.calibration.calibration_info = {"sensors": [{"id": "sensor1", "type": "camera"}]}
        new_info = {"sensors": [
            {"id": "sensor1", "type": "camera", "updated": True},
            {"id": "sensor2", "type": "camera"},
        ]}
        self.calibration.update_calibration_info(new_info, CALIBRATION_ACTION_UPSERT)
        by_id = {s["id"]: s for s in self.calibration.calibration_info["sensors"]}
        self.assertEqual(by_id["sensor1"].get("updated"), True)
        self.assertIn("sensor2", by_id)

        # Test delete action -- remove existing sensor
        self.calibration.calibration_info = {"sensors": [{"id": "sensor1", "type": "camera"}]}
        new_info = {"sensors": [{"id": "sensor1", "type": "camera"}]}
        self.calibration.update_calibration_info(new_info, CALIBRATION_ACTION_DELETE)
        self.assertEqual(len(self.calibration.calibration_info["sensors"]), 0)

        # Test delete action -- unknown sensor id is a silent no-op
        self.calibration.calibration_info = {"sensors": [{"id": "sensor1", "type": "camera"}]}
        new_info = {"sensors": [{"id": "ghost"}]}
        self.calibration.update_calibration_info(new_info, CALIBRATION_ACTION_DELETE)
        self.assertEqual(
            [s["id"] for s in self.calibration.calibration_info["sensors"]],
            ["sensor1"],
        )

    def test_transform_message(self):
        """Test transform method with Message."""
        # Create test message
        sensor = Sensor(id="test_sensor")
        message = Message(messageid="test_message", timestamp=datetime.now(), sensor=sensor)

        # Mock transform_bbox method
        with patch.object(self.calibration, "transform_bbox") as mock_transform:
            mock_transform.return_value = (Coordinate(x=1, y=2), Location(lat=3, lon=4))
            transformed_message = self.calibration.transform(message)

            self.assertEqual(transformed_message.sensor.id, "test_sensor")
            mock_transform.assert_not_called()  # Should not be called since no bbox in message

    def test_transform_bbox(self):
        """Test transform_bbox method."""
        bbox = Bbox(leftX=100, topY=100, rightX=130, bottomY=140)
        sensor_id = "test_sensor"

        with self.assertRaises(NotImplementedError):
            self.calibration.transform_bbox(bbox, sensor_id)

    def test_reprojection_error(self):
        """Test reprojection_error method."""
        image_p = MagicMock()
        expected = MagicMock()
        h_matrix = MagicMock()

        error = self.calibration.reprojection_error(image_p, expected, h_matrix)
        self.assertEqual(error, 0.0)  # Default implementation returns 0.0

    def test_file_monitor_initialization(self):
        """Test CalibrationFileMonitor initialization."""
        monitor = CalibrationBase.CalibrationFileMonitor(self.calibration)
        self.assertEqual(monitor.calibration, self.calibration)

    def test_handler_does_not_define_on_created(self):
        """on_created is intentionally not implemented -- non-atomic writes are not supported."""
        self.assertNotIn(
            "on_created", CalibrationBase.CalibrationFileMonitor.__dict__
        )

    def test_on_moved_dispatches_dest_path(self):
        """on_moved (atomic-rename path) routes to reload_data with dest_path."""
        monitor = CalibrationBase.CalibrationFileMonitor(self.calibration)
        mock_event = MagicMock()
        mock_event.is_directory = False
        mock_event.dest_path = "/test/path/upsert-calibration-x.json"

        with patch.object(self.calibration, 'reload_data') as mock_reload:
            monitor.on_moved(mock_event)
            mock_reload.assert_called_once_with(mock_event.dest_path)

    def test_on_moved_skips_directories(self):
        monitor = CalibrationBase.CalibrationFileMonitor(self.calibration)
        mock_event = MagicMock()
        mock_event.is_directory = True
        mock_event.dest_path = "/test/path/some-subdir"

        with patch.object(self.calibration, 'reload_data') as mock_reload:
            monitor.on_moved(mock_event)
            mock_reload.assert_not_called()

    def test_on_moved_filters_dotfiles(self):
        """Listener-staged ``.upsert-calibration-x.json.tmp`` (dotfile) is ignored."""
        monitor = CalibrationBase.CalibrationFileMonitor(self.calibration)
        evt = MagicMock()
        evt.is_directory = False
        evt.dest_path = "/test/path/.upsert-calibration-x.json.tmp"
        with patch.object(self.calibration, 'reload_data') as mock_reload:
            monitor.on_moved(evt)
            mock_reload.assert_not_called()

    def test_on_moved_filters_non_json(self):
        """Non-.json files are ignored."""
        monitor = CalibrationBase.CalibrationFileMonitor(self.calibration)
        evt = MagicMock()
        evt.is_directory = False
        evt.dest_path = "/test/path/upsert-calibration-x.txt"
        with patch.object(self.calibration, 'reload_data') as mock_reload:
            monitor.on_moved(evt)
            mock_reload.assert_not_called()

    def test_on_moved_swallows_filenotfound(self):
        """If the file vanished between event and read (pruner race), log and move on."""
        monitor = CalibrationBase.CalibrationFileMonitor(self.calibration)
        evt = MagicMock()
        evt.is_directory = False
        evt.dest_path = "/test/path/gone.json"
        with patch.object(self.calibration, 'reload_data', side_effect=FileNotFoundError):
            with self.assertLogs(
                "mdx.analytics.core.transform.calibration.calibration_base",
                level="WARNING",
            ) as ctx:
                monitor.on_moved(evt)
            self.assertTrue(any("disappeared during apply" in line for line in ctx.output))

    def test_on_moved_swallows_other_exceptions(self):
        """A reload_data exception is logged via .exception() -- never killing the watchdog thread."""
        monitor = CalibrationBase.CalibrationFileMonitor(self.calibration)
        evt = MagicMock()
        evt.is_directory = False
        evt.dest_path = "/test/path/upsert-calibration-x.json"
        with patch.object(self.calibration, 'reload_data', side_effect=ValueError("boom")):
            with self.assertLogs(
                "mdx.analytics.core.transform.calibration.calibration_base",
                level="ERROR",
            ) as ctx:
                monitor.on_moved(evt)
            self.assertTrue(any("failed to apply calibration" in line for line in ctx.output))

    @patch('mdx.analytics.core.transform.calibration.calibration_base.Observer')
    @patch('pathlib.Path.mkdir')
    def test_start_listen(self, mock_mkdir, mock_observer):
        """Test start_listen method."""
        mock_observer_instance = MagicMock()
        mock_observer.return_value = mock_observer_instance
        
        self.calibration.start_listen()
        
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_observer_instance.schedule.assert_called_once()
        mock_observer_instance.start.assert_called_once()
        self.assertEqual(self.calibration.observer, mock_observer_instance)

    def test_close(self):
        """Test close method."""
        mock_observer = MagicMock()
        # Observer terminated within timeout -> no warning path.
        mock_observer.is_alive.return_value = False
        self.calibration.observer = mock_observer

        self.calibration.close()

        mock_observer.stop.assert_called_once()
        mock_observer.join.assert_called_once_with(timeout=5.0)

    def test_close_observer_join_timeout_logs_warning(self):
        """If observer.join times out, a warning is logged but close() returns."""
        mock_observer = MagicMock()
        mock_observer.is_alive.return_value = True  # didn't terminate within timeout
        self.calibration.observer = mock_observer

        with self.assertLogs(
            "mdx.analytics.core.transform.calibration.calibration_base",
            level="WARNING",
        ) as ctx:
            self.calibration.close()

        mock_observer.stop.assert_called_once()
        self.assertTrue(any("did not terminate within 5s" in line for line in ctx.output))

    def test_close_without_start_listen(self):
        """Test close method when start_listen was never called.
        
        This tests the case where close() is called on a CalibrationBase instance
        that never had start_listen() called, ensuring no AttributeError is raised.
        """
        # Create a fresh calibration instance - observer should be None by default
        calibration = CalibrationBase(self.config, "tests/unit/resources/calibration_building_k.json")
        
        # Verify observer is None (not started)
        self.assertIsNone(calibration.observer)
        
        # close() should not raise an AttributeError
        try:
            calibration.close()
        except AttributeError as e:
            self.fail(f"close() raised AttributeError when observer was not initialized: {e}")

    @patch('mdx.analytics.core.transform.calibration.calibration_base.load_json_from_file')
    def test_reload_data(self, mock_load_json):
        """Test reload_data method.

        Uses a ``delete-calibration-`` filename so the action parses to
        ``delete`` (the schema validator gates per-action; the mocked payload
        below has only ``sensors[*].id``, which is what the delete schema
        requires and what web-api actually emits for deletes).
        """
        mock_load_json.return_value = {"sensors": [{"id": "new_sensor", "type": "camera"}]}

        with patch.object(self.calibration, 'update_calibration_info') as mock_update:
            with patch.object(self.calibration, '_load_data') as mock_load_data:
                self.calibration.reload_data("/path/to/delete-calibration-2026-05-15T10_00_00Z.json")
                
                mock_update.assert_called_once()
                mock_load_data.assert_called_once()

    def test_filter_frames_by_sensor_id(self):
        """Test filter_frames_by_sensor_id method."""
        frame1 = nvSchema.Frame(sensorId="valid_sensor")
        frame2 = nvSchema.Frame(sensorId="invalid_sensor")
        frames = [frame1, frame2]
        
        with patch.object(self.calibration, 'contains') as mock_contains:
            with patch('mdx.analytics.core.utils.util.extract_sensor_id', side_effect=lambda x: x):
                mock_contains.side_effect = lambda x: x == "valid_sensor"
                
                filtered_frames = self.calibration.filter_frames_by_sensor_id(frames)
                
                self.assertEqual(len(filtered_frames), 1)
                self.assertEqual(filtered_frames[0].sensorId, "valid_sensor")

    def test_filter_messages_by_roi(self):
        """Test filter_messages_by_roi method."""
        from mdx.analytics.core.schema.models import Place, Sensor, Object
        from datetime import datetime

        sensor = Sensor(id="test_sensor")
        object_obj = Object(id="obj1", coordinate=Coordinate(x=100, y=200))

        # Create valid Message objects with all required fields
        message1 = Message(
            messageid="msg1",
            timestamp=datetime.now(),
            sensor=sensor,
            object=object_obj,
            place=Place()
        )
        message2 = Message(
            messageid="msg2", 
            timestamp=datetime.now(),
            sensor=sensor,
            object=object_obj,
            place=Place()
        )
        messages = [message1, message2]

        with patch.object(self.calibration, 'contains', return_value=True):
            with patch.object(self.calibration, 'point_in_polygons', side_effect=[True, False]):
                filtered_messages = self.calibration.filter_messages_by_roi(messages)
                
                self.assertEqual(len(filtered_messages), 1)

    def test_point_in_polygons_true(self):
        """Test point_in_polygons method when point is inside a polygon."""
        sensor_id = "test_sensor"
        point = MagicMock(x=100, y=100)
        
        mock_polygon1 = MagicMock()
        mock_polygon1.contains_point.return_value = False
        mock_polygon2 = MagicMock()
        mock_polygon2.contains_point.return_value = True
        
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.roiPolygons = {"roi1": mock_polygon1, "roi2": mock_polygon2}
        
        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            result = self.calibration.point_in_polygons(point, sensor_id)
            self.assertTrue(result)

    def test_point_in_polygons_false(self):
        """Test point_in_polygons method when point is outside all polygons."""
        sensor_id = "test_sensor"
        point = MagicMock(x=100, y=100)
        
        mock_polygon1 = MagicMock()
        mock_polygon1.contains_point.return_value = False
        mock_polygon2 = MagicMock()
        mock_polygon2.contains_point.return_value = False
        
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.roiPolygons = {"roi1": mock_polygon1, "roi2": mock_polygon2}
        
        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            result = self.calibration.point_in_polygons(point, sensor_id)
            self.assertFalse(result)



    @patch('cv2.findHomography')
    def test_compute_hmatrix_success(self, mock_find_homography):
        """Test _compute_hmatrix method with successful computation."""
        mock_find_homography.return_value = (np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]]), None)
        
        image_coords = [Coordinate(x=0, y=0), Coordinate(x=1, y=0), 
                       Coordinate(x=1, y=1), Coordinate(x=0, y=1)]
        global_coords = [Coordinate(x=0, y=0), Coordinate(x=10, y=0), 
                        Coordinate(x=10, y=10), Coordinate(x=0, y=10)]
        
        result = self.calibration._compute_hmatrix(image_coords, global_coords)
        self.assertIsNotNone(result)
        if result:
            self.assertEqual(len(result), 3)
            self.assertEqual(len(result[0]), 3)

    def test_compute_hmatrix_mismatched_coordinates(self):
        """Test _compute_hmatrix with mismatched coordinate lengths."""
        image_coords = [Coordinate(x=0, y=0), Coordinate(x=1, y=0)]
        global_coords = [Coordinate(x=0, y=0)]
        
        result = self.calibration._compute_hmatrix(image_coords, global_coords)
        self.assertIsNone(result)

    def test_compute_hmatrix_insufficient_coordinates(self):
        """Test _compute_hmatrix with insufficient coordinates."""
        image_coords = [Coordinate(x=0, y=0), Coordinate(x=1, y=0)]
        global_coords = [Coordinate(x=0, y=0), Coordinate(x=10, y=0)]
        
        result = self.calibration._compute_hmatrix(image_coords, global_coords)
        self.assertIsNone(result)

    def test_perspective_transform_success(self):
        """Test perspective_transform method with valid homography."""
        homography = [[1.0, 0.0, 5.0], [0.0, 1.0, 10.0], [0.0, 0.0, 1.0]]
        result = self.calibration.perspective_transform(100, 200, homography)
        
        self.assertIsNotNone(result)
        self.assertEqual(result, (105.0, 210.0))

    def test_perspective_transform_no_homography(self):
        """Test perspective_transform method with None homography."""
        result = self.calibration.perspective_transform(100, 200, None)
        self.assertIsNone(result)

    def test_perspective_transform_zero_division(self):
        """Test perspective_transform method with zero division."""
        homography = [[1.0, 0.0, 5.0], [0.0, 1.0, 10.0], [0.0, 0.0, 0.0]]
        result = self.calibration.perspective_transform(100, 200, homography)
        self.assertIsNone(result)

    def test_get_roi_polygons(self):
        """Test _get_roi_polygons method."""
        roi1 = ROI(
            id="roi1", 
            roiCoordinates=[Point2D(x=0, y=0), Point2D(x=10, y=0), 
                           Point2D(x=10, y=10), Point2D(x=0, y=10)],
            sensors=[],
            groups=[],
            restrictedObjectTypes=[],
            confinedObjectTypes=[]
        )
        roi2 = ROI(
            id="roi2", 
            roiCoordinates=[Point2D(x=20, y=20), Point2D(x=30, y=20), 
                           Point2D(x=30, y=30), Point2D(x=20, y=30)],
            sensors=[],
            groups=[],
            restrictedObjectTypes=[],
            confinedObjectTypes=[]
        )
        
        result = self.calibration._get_roi_polygons([roi1, roi2])
        
        self.assertEqual(len(result), 2)
        self.assertIn("roi1", result)
        self.assertIn("roi2", result)

    def test_roi_restricted_types(self):
        """Test _roi_restricted_types method."""
        sensor_id = "test_sensor"
        
        roi1 = MagicMock()
        roi1.id = "roi1"
        roi1.restrictedObjectTypes = ["person", "car"]
        
        roi2 = MagicMock()
        roi2.id = "roi2"
        roi2.restrictedObjectTypes = ["truck"]
        
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.rois = [roi1, roi2]
        
        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            result = self.calibration.roi_restricted_types(sensor_id)
            
            self.assertEqual(len(result), 2)
            self.assertEqual(result["roi1"], ["person", "car"])
            self.assertEqual(result["roi2"], ["truck"])

    def test_roi_restricted_types_no_sensor(self):
        """Test _roi_restricted_types with non-existent sensor."""
        result = self.calibration.roi_restricted_types("non_existent")
        self.assertEqual(result, {})

    def test_roi_confined_types(self):
        """Test _roi_confined_types method."""
        sensor_id = "test_sensor"
        
        roi1 = MagicMock()
        roi1.id = "roi1"
        roi1.confinedObjectTypes = ["person"]
        
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.rois = [roi1]
        
        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            result = self.calibration._roi_confined_types(sensor_id)
            
            self.assertEqual(len(result), 1)
            self.assertEqual(result["roi1"], ["person"])

    def test_roi_confined_types_no_rois(self):
        """Test _roi_confined_types with sensor having no ROIs."""
        sensor_id = "test_sensor"
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.rois = None
        
        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            result = self.calibration._roi_confined_types(sensor_id)
            self.assertEqual(result, {})

    def test_get_fov_objects_by_type(self):
        """Test _get_fov_objects_by_type method."""
        fov_metrics = [
            nvSchema.TypeMetrics(type="person", objectIds=["obj1", "obj2"]),
            nvSchema.TypeMetrics(type="car", objectIds=["obj3"])
        ]
        
        result = self.calibration._get_fov_objects_by_type(fov_metrics, "person")
        self.assertEqual(result, {"obj1", "obj2"})
        
        result = self.calibration._get_fov_objects_by_type(fov_metrics, "truck")
        self.assertEqual(result, set())

    def test_get_roi_objects_by_type(self):
        """Test _get_roi_objects_by_type method."""
        roi_metrics = [
            nvSchema.TypeMetrics(id="roi1", type="person", objectIds=["obj1", "obj2"]),
            nvSchema.TypeMetrics(id="roi2", type="car", objectIds=["obj3"]),
            nvSchema.TypeMetrics(id="roi1", type="car", objectIds=["obj4"])
        ]
        
        roi_confined_types = {"roi1": ["person", "car"], "roi2": ["car"]}
        
        result = self.calibration._get_roi_objects_by_type(roi_metrics, "person", roi_confined_types)
        self.assertEqual(result, {"obj1", "obj2"})

    def test_get_group_place(self):
        """Test get_group_place method."""
        group_id = "test_group"
        
        # Use real SensorInfo objects instead of MagicMocks to avoid type errors
        from typing import cast
        sensor1 = cast(SensorInfo, MagicMock(spec=SensorInfo))
        sensor1.group = {"name": "other_group"}
        sensor1.place = [{"name": "city", "value": "NYC"}]
        
        sensor2 = cast(SensorInfo, MagicMock(spec=SensorInfo))
        sensor2.group = {"name": "test_group"}
        sensor2.place = [{"name": "city", "value": "SF"}, {"name": "building", "value": "Main"}]
        
        sensors = [sensor1, sensor2]
        
        result = self.calibration.get_group_place(group_id, sensors)
        expected = [{"name": "city", "value": "SF"}, {"name": "building", "value": "Main"}]
        self.assertEqual(result, expected)

    def test_get_group_place_not_found(self):
        """Test get_group_place with non-existent group."""
        sensors = []
        result = self.calibration.get_group_place("non_existent", sensors)
        self.assertEqual(result, [])

    def test_update_roi_info(self):
        """Test update_roi_info method."""
        sensor_id = "test_sensor"
        
        roi_metric1 = nvSchema.TypeMetrics(id="roi1", type="person")
        roi_metric2 = nvSchema.TypeMetrics(id="roi2", type="car")
        rois = [roi_metric1, roi_metric2]
        
        with patch.object(self.calibration, 'roi_restricted_types') as mock_restricted:
            mock_restricted.return_value = {"roi1": ["person"], "roi2": ["truck"]}
            
            self.calibration.update_roi_info(rois, sensor_id)
            
            self.assertEqual(roi_metric1.info["restrictedAreaViolation"], "true")
            self.assertEqual(roi_metric2.info["restrictedAreaViolation"], "false")

    @patch('cv2.solvePnP')
    def test_get_cam_params_success(self, mock_solve_pnp):
        """Test get_cam_params method with successful camera calibration."""
        mock_solve_pnp.return_value = (True, np.array([[0.1], [0.2], [0.3]]), np.array([[1], [2], [3]]))
        
        with patch('cv2.Rodrigues') as mock_rodrigues:
            mock_rodrigues.return_value = (np.eye(3), None)
            
            sensor_id = "test_sensor"
            mock_sensor = MagicMock(spec=SensorInfo)
            mock_sensor.id = sensor_id
            mock_sensor.imageCoordinates = [Coordinate(x=0, y=0), Coordinate(x=1, y=0)]
            mock_sensor.globalCoordinates = [Coordinate(x=0, y=0), Coordinate(x=10, y=0)]
            mock_sensor.intrinsicMatrix = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            
            with patch.object(self.calibration, 'sensors', [mock_sensor]):
                result = self.calibration.get_cam_params(sensor_id)
                
                self.assertIn("camera_position", result)
                self.assertIn("rotation_vector", result)
                self.assertIn("translation_vector", result)

    def test_get_cam_params_no_sensor(self):
        """Test get_cam_params with non-existent sensor."""
        result = self.calibration.get_cam_params("non_existent")
        self.assertEqual(result, {})

    def test_transform_message_basic(self):
        """Test transform method basic functionality."""
        from mdx.analytics.core.schema.models import Place
        
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = "test_sensor"
        mock_sensor.place = [{"name": "city", "value": "SF"}]
        
        message = Message(
            messageid="test_msg",
            timestamp=datetime.now(),
            sensor=Sensor(id="test_sensor"), 
            object=None,
            place=Place()
        )
        
        with patch.object(self.calibration, 'sensor_map', {"test_sensor": mock_sensor}):
            result = self.calibration.transform(message)
            
            self.assertEqual(result.sensor.id, "test_sensor")
            self.assertIsNone(result.object)



    def test_point_in_polygon_with_missing_roi(self):
        """Test point_in_polygon with missing ROI."""
        sensor_id = "test_sensor"
        roi_id = "missing_roi"
        point = MagicMock(x=100, y=100)
        
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.roiPolygons = {}
        
        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            result = self.calibration.point_in_polygon(point, sensor_id, roi_id)
            self.assertFalse(result)

    def test_get_roi_metrics_no_sensor(self):
        """Test get_roi_metrics with non-existent sensor."""
        points = [("obj1", "person", nvSchema.Coordinate(x=100, y=100))]
        result = self.calibration.get_roi_metrics(points, "non_existent")
        self.assertEqual(result, [])

    def test_get_roi_metrics_no_roi_polygons(self):
        """Test get_roi_metrics with sensor having no ROI polygons."""
        sensor_id = "test_sensor"
        points = [("obj1", "person", nvSchema.Coordinate(x=100, y=100))]
        
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.roiPolygons = None
        
        with patch.object(self.calibration, 'sensor_map', {sensor_id: mock_sensor}):
            result = self.calibration.get_roi_metrics(points, sensor_id)
            self.assertEqual(result, [])

    def test_load_data(self):
        """Test _load_data method."""
        mock_sensor_map = {"sensor1": MagicMock()}
        mock_roi_map = {"roi1": MagicMock()}
        mock_tripwire_map = {"tripwire1": MagicMock()}
        
        with patch.object(self.calibration, '_load_sensors') as mock_load_sensors:
            mock_load_sensors.return_value = (mock_sensor_map, mock_roi_map, mock_tripwire_map)
            
            self.calibration._load_data()
            
            self.assertEqual(self.calibration.sensor_map, mock_sensor_map)
            self.assertEqual(self.calibration.global_roi_map, mock_roi_map)
            self.assertEqual(self.calibration.global_tripwire_map, mock_tripwire_map)
            self.assertEqual(len(self.calibration.sensors), 1)
            self.assertEqual(len(self.calibration.global_rois), 1)
            self.assertEqual(len(self.calibration.global_tripwires), 1)

    @patch('cv2.solvePnP')  
    def test_get_cam_params_with_coordinates(self, mock_solve_pnp):
        """Test get_cam_params with different coordinate types."""
        mock_solve_pnp.return_value = (True, np.array([[0.1], [0.2], [0.3]]), np.array([[1], [2], [3]]))
        
        with patch('cv2.Rodrigues') as mock_rodrigues:
            mock_rodrigues.return_value = (np.eye(3), None)
            
            sensor_id = "test_sensor"
            mock_sensor = MagicMock(spec=SensorInfo)
            mock_sensor.id = sensor_id
            mock_sensor.imageCoordinates = [Coordinate(x=0, y=0)]
            mock_sensor.globalCoordinates = [Coordinate(x=0, y=0, z=0)]
            mock_sensor.intrinsicMatrix = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            
            with patch.object(self.calibration, 'sensors', [mock_sensor]):
                result = self.calibration.get_cam_params(sensor_id)
                
                self.assertIn("camera_position", result)

    @patch('cv2.solvePnP')
    def test_get_cam_params_failed_solve(self, mock_solve_pnp):
        """Test get_cam_params when solvePnP fails."""
        mock_solve_pnp.return_value = (False, None, None)
        
        sensor_id = "test_sensor"
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.id = sensor_id
        mock_sensor.imageCoordinates = [Coordinate(x=0, y=0)]
        mock_sensor.globalCoordinates = [Coordinate(x=0, y=0)]
        mock_sensor.intrinsicMatrix = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        
        with patch.object(self.calibration, 'sensors', [mock_sensor]):
            result = self.calibration.get_cam_params(sensor_id)
            
            self.assertEqual(result, {})

    def test_compute_camera_projection_matrix_3d(self):
        """Test _compute_camera_projection_matrix_3d method."""
        image_coords = [Coordinate(x=0, y=0), Coordinate(x=1, y=0), 
                       Coordinate(x=1, y=1), Coordinate(x=0, y=1),
                       Coordinate(x=0.5, y=0.5), Coordinate(x=1.5, y=1.5)]
        global_coords = [Coordinate(x=0, y=0, z=0), Coordinate(x=10, y=0, z=0), 
                        Coordinate(x=10, y=10, z=0), Coordinate(x=0, y=10, z=0),
                        Coordinate(x=5, y=5, z=0), Coordinate(x=15, y=15, z=0)]
        
        result = self.calibration._compute_camera_projection_matrix_3d(image_coords, global_coords)
        
        self.assertEqual(result.shape, (3, 4))

    def test_update_h_matrix_camera_with_homography(self):
        """Test _update_h_matrix with camera sensor having homography."""
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.homography = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        
        result = self.calibration._update_h_matrix(mock_sensor)
        
        # When homography exists, it should be returned directly without calling inv
        self.assertIsNotNone(result)
        self.assertEqual(result, [[1, 0, 0], [0, 1, 0], [0, 0, 1]])

    def test_update_h_matrix_camera_no_homography(self):
        """Test _update_h_matrix with camera sensor without homography."""
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.type = SENSOR_TYPE_CAMERA
        mock_sensor.homography = None
        mock_sensor.imageCoordinates = [Coordinate(x=0, y=0)]
        mock_sensor.globalCoordinates = [Coordinate(x=0, y=0)]
        
        with patch.object(self.calibration, '_compute_hmatrix') as mock_compute:
            mock_compute.return_value = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            
            result = self.calibration._update_h_matrix(mock_sensor)
            
            self.assertIsNotNone(result)
            mock_compute.assert_called_once()

    def test_update_h_matrix_non_camera(self):
        """Test _update_h_matrix with non-camera sensor."""
        mock_sensor = MagicMock(spec=SensorInfo)
        mock_sensor.type = "other"
        
        result = self.calibration._update_h_matrix(mock_sensor)
        
        self.assertIsNone(result)

    @patch('mdx.analytics.core.transform.calibration.calibration_base.load_json_from_file')
    def test_read_config(self, mock_load):
        """Test _read_config method."""
        test_data = {"test": "data"}
        mock_load.return_value = test_data
        
        result = self.calibration._read_config("test_path.json")
        
        self.assertEqual(result, test_data)
        mock_load.assert_called_once_with("test_path.json")

    def test_update_roi_info_no_violation(self):
        """Test update_roi_info method with no violations."""
        roi1 = nvSchema.TypeMetrics(id="roi1", type="Dog", count=1)
        rois = [roi1]
        
        restricted_types = {
            "roi1": ["Person", "Vehicle"]  # Dog is not restricted
        }
        
        with patch.object(self.calibration, 'roi_restricted_types', return_value=restricted_types):
            self.calibration.update_roi_info(rois, "sensor1")
            
            self.assertEqual(roi1.info["restrictedAreaViolation"], "false")

    def test_filter_messages_by_roi_no_sensor(self):
        """Test filter_messages_by_roi with sensor not in calibration."""
        from mdx.analytics.core.schema.models import Place, Sensor, Object
        from datetime import datetime

        sensor = Sensor(id="unknown_sensor")
        object_obj = Object(id="obj1", coordinate=Coordinate(x=100, y=200))

        message = Message(
            messageid="msg1",
            timestamp=datetime.now(),
            sensor=sensor,
            object=object_obj,
            place=Place()
        )

        with patch.object(self.calibration, 'contains', return_value=False):
            filtered_messages = self.calibration.filter_messages_by_roi([message])
            self.assertEqual(len(filtered_messages), 0)

    def test_load_sensors_with_city(self):
        """Test _load_sensors method with city information."""
        calibration_info = {
            "city": "San Francisco",
            "sensors": [
                {
                    "id": "cam1",
                    "type": "camera",
                    "place": [{"name": "building", "value": "HQ"}]
                }
            ]
        }
        
        self.calibration.calibration_info = calibration_info
        
        with patch.object(self.calibration, '_update_h_matrix', return_value=None):
            with patch.object(self.calibration, '_get_roi_polygons', return_value={}):
                with patch.object(self.calibration, 'get_group_place', return_value=[]):
                    sensor_map, roi_map, trip_map = self.calibration._load_sensors()
                    
                    # Check that city was added to place
                    sensor = sensor_map["cam1"]
                    places = sensor.place
                    city_place = next((p for p in places if p["name"] == "city"), None)
                    self.assertIsNotNone(city_place)
                    self.assertEqual(city_place["value"], "San Francisco")

    def test_load_sensors_with_group_sensor(self):
        """Test _load_sensors method creating group sensors."""
        calibration_info = {
            "sensors": [
                {
                    "id": "cam1",
                    "type": "camera",
                    "group": {"name": "parking_lot"}
                }
            ]
        }
        
        self.calibration.calibration_info = calibration_info
        
        with patch.object(self.calibration, '_update_h_matrix', return_value=None):
            with patch.object(self.calibration, '_get_roi_polygons', return_value={}):
                with patch.object(self.calibration, 'get_group_place', return_value=[]):
                    sensor_map, roi_map, trip_map = self.calibration._load_sensors()
                    
                    # Should have both the camera and group sensor
                    self.assertIn("cam1", sensor_map)
                    self.assertIn("parking_lot", sensor_map)
                    self.assertEqual(sensor_map["parking_lot"].type, SENSOR_TYPE_GROUP)

    def test_load_sensors_global_roi_new_sensor(self):
        """Test _load_sensors with global ROI creating new sensor entry."""
        calibration_info = {
            "sensors": [{"id": "cam1", "type": "camera"}],
            "rois": [
                {
                    "id": "global_roi1",
                    "sensors": ["cam2"],  # Sensor not in sensors list
                    "groups": [],
                    "roiCoordinates": []
                }
            ]
        }
        
        self.calibration.calibration_info = calibration_info
        
        with patch.object(self.calibration, '_update_h_matrix', return_value=None):
            with patch.object(self.calibration, '_get_roi_polygons', return_value={}):
                with patch.object(self.calibration, 'get_group_place', return_value=[]):
                    sensor_map, roi_map, trip_map = self.calibration._load_sensors()
                    
                    # Should create new sensor entry for cam2
                    self.assertIn("cam2", sensor_map)
                    self.assertEqual(sensor_map["cam2"].type, SENSOR_TYPE_CAMERA)

    def test_calibration_type_raises_not_implemented(self):
        """Test that CalibrationBase raises NotImplementedError for calibration_type."""
        with self.assertRaises(NotImplementedError):
            _ = self.calibration.calibration_type

    def test_calibration_type_enum_values(self):
        """Test CalibrationType enum values."""
        self.assertEqual(CalibrationType.IMAGE.value, "image")
        self.assertEqual(CalibrationType.CARTESIAN.value, "cartesian")
        self.assertEqual(CalibrationType.GEO.value, "geo")


if __name__ == "__main__":
    unittest.main()
