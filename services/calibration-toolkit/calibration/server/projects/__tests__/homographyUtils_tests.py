# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from django.test import TestCase
import numpy as np
from homographyUtils import (
    GetXYMatrix,
    Haversine,
    ConvertToHomogeneous,
    ReprojectionError
    )

class TestGetXYMatrix(TestCase):

    def test_GetXYMatrix(self):
        # Define a sample list of lat/lng coordinate dictionaries
        json_latlng_coords = [{"lng": 10, "lat": 20}, {"lng": 30, "lat": 40}]

        # Call the function to convert lat/lng to XY numpy array
        xy_matrix = GetXYMatrix(json_latlng_coords)

        # Define the expected XY matrix based on the sample data
        expected_xy_matrix = np.array([[10, 20], [30, 40]])

        # Assert that the function returns the expected XY matrix
        np.testing.assert_array_equal(xy_matrix, expected_xy_matrix)

    def test_convert_to_homogeneous(self):
        # Test case 1
        xy_vec = np.array([3, 4])  # Input vector
        expected_result = np.array([3, 4, 1])  # Expected output

        result = ConvertToHomogeneous(xy_vec)
        np.testing.assert_array_equal(result, expected_result)

        # Test case 2
        xy_vec = np.array([[1, 2], [3, 4]])  # Input matrix
        expected_result = np.array([[1, 2, 1], [3, 4, 1]])  # Expected output

        result = ConvertToHomogeneous(xy_vec)
        np.testing.assert_array_equal(result, expected_result)


    def test_reprojection_error(self):
        # Test case 1
        xy_src = np.array([[0, 0], [1, 1]])  # Source points
        xy_dst = np.array([[0, 0], [2, 2]])  # Destination points
        H = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]])  # Homography matrix
        method = "euclidean"  # Distance calculation method
        expected_errors = np.array([[0], [1414.21356237]])  # Expected errors

        result = ReprojectionError(xy_src, xy_dst, H, method)
        np.testing.assert_array_almost_equal(result, expected_errors, decimal=6)

    def test_haversine_distance(self):
        # Test case 1
        lon1, lat1 = -122.4194, 37.7749  # San Francisco coordinates
        lon2, lat2 = -118.2437, 34.0522  # Los Angeles coordinates
        expected_distance = 559.1  # Expected distance in kilometers

        result = Haversine(lon1, lat1, lon2, lat2)
        self.assertAlmostEqual(result, expected_distance, delta=0.1)

        # Test case 2
        lon1, lat1 = 0, 0  # Equator coordinates
        lon2, lat2 = 0, 90  # North Pole coordinates
        expected_distance = 10007.5  # Expected distance in kilometers

        result = Haversine(lon1, lat1, lon2, lat2)
        self.assertAlmostEqual(result, expected_distance, delta=0.1)


if __name__ == '__main__':
    unittest.main()