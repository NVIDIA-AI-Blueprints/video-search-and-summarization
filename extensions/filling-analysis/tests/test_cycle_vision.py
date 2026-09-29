# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Decision contracts use synthetic observations, not the evaluation labels."""
import unittest
from copy import deepcopy
from unittest.mock import patch

import cv2
import numpy as np

from backend.cycle_vision import analyze_video, inspect_cycle, load_calibration, measure_frame


def observation(t, level, *, flow=False, exterior=False, confidence=.95):
    return {"t": t, "level": level, "flow": flow, "overflow_visible": exterior,
            "confidence": confidence}


class CycleDecisionTests(unittest.TestCase):
    def setUp(self):
        self.calibration = deepcopy(load_calibration())

    def test_low_during_filling_is_not_underfill(self):
        samples = [observation(i/4, .2+i*.02, flow=True) for i in range(20)]
        result = inspect_cycle(samples, 1, self.calibration)
        self.assertEqual(result["status"], "uncertain")
        self.assertIsNone(result["final_level"])
        self.assertIsNone(result["fill_end_time"])

    def test_stopped_flow_and_settled_low_is_underfill(self):
        samples = [observation(i/4, .3, flow=i<8) for i in range(16)]
        result = inspect_cycle(samples, 1, self.calibration)
        self.assertEqual(result["status"], "underfill")
        self.assertGreaterEqual(result["measurement_time"], result["fill_end_time"])

    def test_high_fill_without_exterior_is_not_overflow(self):
        samples = [observation(i/4, .99, flow=i<8) for i in range(16)]
        self.assertEqual(inspect_cycle(samples, 1, self.calibration)["status"], "normal")

    def test_exterior_liquid_must_persist(self):
        samples = [observation(i/4, .95, flow=i<8, exterior=i==6) for i in range(16)]
        self.assertEqual(inspect_cycle(samples, 1, self.calibration)["status"], "normal")
        samples[7]["overflow_visible"] = True
        result = inspect_cycle(samples, 1, self.calibration)
        self.assertEqual(result["status"], "overflow")
        self.assertEqual(result["overflow_time"], 1.5)

    def test_bottle_with_no_observed_filling_is_uncertain(self):
        samples = [observation(i/4, .3) for i in range(20)]
        result = inspect_cycle(samples, 1, self.calibration)
        self.assertEqual(result["status"], "uncertain")
        self.assertIsNone(result["fill_start_time"])
        self.assertIsNone(result["fill_end_time"])

    def test_motion_or_occlusion_cannot_manufacture_final_height(self):
        samples = [observation(i/4, .3, flow=i<8, confidence=.1 if i>=8 else .95)
                   for i in range(16)]
        result = inspect_cycle(samples, 1, self.calibration)
        self.assertEqual(result["status"], "uncertain")
        self.assertIsNone(result["measurement_time"])

    def test_final_unstable_boundary_is_uncertain(self):
        samples = [observation(i/4, .2 if i%2 else .4, flow=i<8) for i in range(16)]
        self.assertEqual(inspect_cycle(samples, 1, self.calibration)["status"], "uncertain")

    def test_departing_bottle_has_no_stationary_measurement(self):
        image = np.full((360, 640, 3), 100, dtype=np.uint8)
        cv2.rectangle(image, (300, 105), (340, 120), (0, 200, 0), -1)
        cv2.rectangle(image, (295, 200), (345, 314), (0, 140, 255), -1)
        centered = measure_frame(image, self.calibration)
        self.assertTrue(centered["aligned"])
        self.assertIsNotNone(centered["level"])
        departed = np.roll(image, 50, axis=1)
        moved = measure_frame(departed, self.calibration)
        self.assertFalse(moved["aligned"])
        self.assertIsNone(moved["level"])

    def test_unknown_source_rejected_before_decode(self):
        with patch("backend.cycle_vision.file_sha256", return_value="0"*64):
            with self.assertRaisesRegex(ValueError, "calibrated only"):
                analyze_video("unreviewed.mp4")


if __name__ == "__main__":
    unittest.main()
