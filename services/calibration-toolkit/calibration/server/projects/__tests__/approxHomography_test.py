# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from django.test import TestCase
import numpy as np
from approxHomography import get_original_from_projected

class TestGetOriginalFromProjected(TestCase):
    def test_get_original_from_projected(self):
        # Define a sample homography matrix H and transformed points
        H = "[[1, 0, 0], [0, 1, 0], [0, 0, 1]]"  # Sample homography matrix
        transformed_points = [[1, 1], [2, 2], [3, 3]]  # Sample transformed points

        # Call the function to get the original points
        original_points = get_original_from_projected(H, transformed_points)

        # Define the expected original points based on the sample data
        expected_original_points = [{"x": 1, "y": 1}, {"x": 2, "y": 2}, {"x": 3, "y": 3}]

        # Assert that the function returns the expected original points
        self.assertEqual(original_points, expected_original_points)

