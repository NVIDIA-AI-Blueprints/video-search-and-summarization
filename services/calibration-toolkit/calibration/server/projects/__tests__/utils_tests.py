# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from django.test import TestCase
from unittest.mock import patch
from calibration.server.projects.utils import (
    get_uuid_id,
    convertLngToLon,
    convertLonToLng,
    ensure_dir,
    unscaleCoordinates,
    getPoints,
    getWirePoints,
    unpad_points,
    flipCoordY,
    importImagePolygon,
    importEdgeLengths,
    importPolygon,
    importCoordinates,
    importROI,
    importTripwires,
    getOriginalTripwires
    )


class UtilsTests(TestCase):
    @patch('utils.uuid')
    def test_get_uuid_id(self, mock_uuid):
        mock_uuid.uuid4.return_value = 'mock_uuid_value'
        result = get_uuid_id()
        self.assertEqual(result, 'mock_uuid_value')

    def test_convertLngToLon(self):
        point1 = {'lat': 42, 'lng': -90}
        point2 = {'lat': 42, 'lon': -90}
        updatedPoint = convertLngToLon(point1)
        self.assertEquals(updatedPoint, point2)

    def test_convertLonToLng(self):
        point1 = {'lat': 42, 'lng': -90}
        point2 = {'lat': 42, 'lon': -90}
        updatedPoint = convertLonToLng(point2)
        self.assertEquals(updatedPoint, point1)

    def test_convertXYtoLatLng(self):
        polygon = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
        expected_result = [{"lat": 2, "lng": 1}, {"lat": 4, "lng": 3}]
        result = convertXYtoLatLng(polygon)
        self.assertEqual(result, expected_result)

    def test_convertLatLngtoXY(self):
        polygon = [{"lat": 2, "lng": 1}, {"lat": 4, "lng": 3}]
        expected_result = [{"y": 2, "x": 1}, {"y": 4, "x": 3}]
        result = convertLatLngtoXY(polygon)
        self.assertEqual(result, expected_result)

    def test_convertGlobaltoEdgeLengths(self):
        polygon = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
        expected_result = str([{"lat": 2, "lng": 1}, {"lat": 4, "lng": 3}])
        result = convertGlobaltoEdgeLengths(polygon)
        self.assertEqual(result, expected_result)

    @patch('utils.logger')
    @patch('utils.os')
    def test_ensure_dir(self, mock_os, mock_logger):
        file_path = '/calibration/server/media/test1/file.txt'
        mock_os.path.dirname.return_value = '/path/to/directory'
        mock_os.path.exists.return_value = False

        ensure_dir(file_path)
        mock_logger.debug.assert_called_with(f"Making Directory: /path/to/directory")
        mock_os.makedirs.assert_called_with('/path/to/directory')

    @patch('utils.os.makedirs')
    @patch('utils.logger.debug')
    @patch('utils.os.path.exists', return_value=False)
    def test_ensure_dir_directory_not_exists(self, mock_os_path_exists, mock_logger_debug, mock_os_makedirs):
        file_path = "/calibration/server/media/test1/"
        ensure_dir(file_path)
        mock_os_path_exists.assert_called_once_with(os.path.dirname(file_path))
        mock_logger_debug.assert_called_once_with(f"Making Directory: {os.path.dirname(file_path)}")
        mock_os_makedirs.assert_called_once_with(os.path.dirname(file_path))

    @patch('utils.logger.debug')
    @patch('utils.os.path.exists', return_value=True)
    def test_ensure_dir_directory_exists(self, mock_os_path_exists, mock_logger_debug):
        file_path = "/calibration/server/data/media/"
        ensure_dir(file_path)
        mock_os_path_exists.assert_called_once_with(os.path.dirname(file_path))
        mock_logger_debug.assert_called_once_with("Directory exists")


    def test_unscaleCoordinates(self):
        coordinates = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
        scaleFactor = 2
        expected_result = [{"x": 2, "y": 4}, {"x": 6, "y": 8}]
        result = unscaleCoordinates(coordinates, scaleFactor)
        self.assertEqual(result, expected_result)

    def test_getPoints(self):
        coordinates = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
        scaleFactor = 2
        expected_result = [{"lat": 2, "lng": 1}, {"lat": 4, "lng": 3}]
        result = getPoints(coordinates, scaleFactor)
        self.assertEqual(result, expected_result)

    def test_getWirePoints(self):
        coordinates = {"p1": {"x": 1, "y": 2}, "p2": {"x": 3, "y": 4}}
        scale_factor = 2
        expected_result = [{"lat": 2, "lng": 1}, {"lat": 4, "lng": 3}]
        result = getWirePoints(coordinates, scale_factor)
        self.assertEqual(result, expected_result)

    def test_unpad_points(self):
        coordinates = [{"lat": 3, "lng": 4}, {"lat": 5, "lng": 6}]
        expected_result = [{"lat": 0, "lng": 0}, {"lat": 2, "lng": 2}]
        result, _ = unpad_points(coordinates)
        self.assertEqual(result, expected_result)

    def test_flipCoordY(self):
        coordinates = [{"lat": 2, "lng": 1}, {"lat": 4, "lng": 3}]
        height = 10
        expected_result = [{"lat": 8, "lng": 1}, {"lat": 6, "lng": 3}]
        result = flipCoordY(coordinates, height)
        self.assertEqual(result, expected_result)

    def test_importImagePolygon(self):
        input_polygon = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
        label_id = "123"
        expected_result = '[{"id": "mock_uuid_value", "type": "polygon", "points": [{"lat": 2, "lng": 1}, {"lat": 4, "lng": 3}], "class": "123"}]'
        result = importImagePolygon(input_polygon, label_id)
        self.assertEqual(result, expected_result)

    def test_importEdgeLengths(self):
        input_points = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
        scale_factor = 2
        expected_result = '[[{"lat": 2, "lng": 1}, {"lat": 4, "lng": 3}], 1, 2]'
        result = importEdgeLengths(input_points, scale_factor)
        self.assertEqual(result, expected_result)

    def test_importPolygon(self):
        input_points = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
        label_id = "123"
        scale_factor = 2
        color = "red"
        height = 10
        expected_result = '[{"id": "mock_uuid_value", "type": "polygon", "points": [{"lat": 8, "lng": 1}, {"lat": 6, "lng": 3}], "class": "123", "color": "red"}]'
        result = importPolygon(input_points, label_id, scale_factor, color, height)
        self.assertEqual(result, expected_result)

    def test_importCoordinates(self):
        input_data = [{"x": 1, "y": 2}]
        sensor_id = "sensor1"
        scale_factor = 2
        color = "red"
        height = 100
        result = importCoordinates(input_data, sensor_id, scale_factor, color, height)
        self.assertEqual(result, '{"id": "sensor1", "type": "point", "points": [{"lat": 200, "lng": 100}], "class": "sensor1", "color": "red"}')

    def test_importROI(self):
        input_data = [{"roiCoordinates": [{"x": 1, "y": 2}]}]
        label_id = "label1"
        scale_factor = 2
        color = "blue"
        height = 150
        result = importROI(input_data, label_id, scale_factor, color, height)
        self.assertEqual(result, '[{"id": "some_uuid", "type": "polygon", "points": [{"lat": 2, "lng": 1}], "class": "label1", "color": "blue"}]')

    def test_importTripwires(self):
        input_data = [{"wire": {"p1": {"x": 1, "y": 2}, "p2": {"x": 3, "y": 4}}, "direction": {"p1": {"x": 5, "y": 6}, "p2": {"x": 7, "y": 8}}}]
        label_id = ["label1", "label2"]
        scale_factor = 2
        colors = ["green", "yellow"]
        height = 200
        result_tripwire, result_tripdir = importTripwires(input_data, label_id, scale_factor, colors, height)
        self.assertEqual(result_tripwire, '[{"id": "some_uuid", "type": "polyline", "points": [{"lat": 400, "lng": 200}, {"lat": 800, "lng": 600}], "class": "label1", "color": "green"}]')
        self.assertEqual(result_tripdir, '[{"id": "some_uuid", "type": "polyline", "points": [{"lat": 1000, "lng": 400}, {"lat": 1400, "lng": 800}], "class": "label2", "color": "yellow"}]')

    def test_getOriginalTripwires(self):
        input_data = '[{"points": [{"x": 1, "y": 2}]}]'
        H = "some_matrix"
        result = getOriginalTripwires(input_data, H)
        self.assertEqual(result, '[{"points": [{"lat": 2, "lng": 1}]}]')