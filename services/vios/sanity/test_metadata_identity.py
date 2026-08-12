# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verify that every lifecycle transport uses camera names for overlay metadata."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BDD_ROOT = Path(__file__).resolve().parents[1] / "test/bdd_tests"
sys.path.insert(0, str(BDD_ROOT))

from scripts.overlay.deepstream_sim import _handle_event  # noqa: E402
from scripts.overlay.metadata_service import _discover_sensors  # noqa: E402


class MetadataIdentityTests(unittest.TestCase):
    def test_redis_event_prefers_camera_name_over_generated_uuid(self):
        observed = []
        payload = json.dumps({
            "event": {
                "change": "camera_streaming",
                "camera_id": "4e8bd6de-17f3-42dd-8bba-bb06249733be",
                "camera_name": "warehouse-1",
                "camera_url": "rtsp://127.0.0.1:30554/live/warehouse-1",
                "metadata": {"codec": "H265"},
            }
        })

        _handle_event(payload, lambda url, sensor, codec: observed.append((url, sensor, codec)))

        self.assertEqual(observed, [(
            "rtsp://127.0.0.1:30554/live/warehouse-1",
            "warehouse-1",
            "H265",
        )])

    def test_redis_event_keeps_camera_id_as_legacy_fallback(self):
        observed = []
        payload = json.dumps({
            "event": {
                "change": "camera_streaming",
                "camera_id": "legacy-sensor-id",
                "camera_url": "rtsp://127.0.0.1:30554/live/legacy-sensor",
            }
        })

        _handle_event(payload, lambda url, sensor, codec: observed.append((sensor, codec)))

        self.assertEqual(observed, [("legacy-sensor-id", "H264")])

    def test_reconciliation_uses_camera_name_not_generated_uuid(self):
        sensor_response = mock.Mock()
        sensor_response.json.return_value = [{
            "sensorId": "4e8bd6de-17f3-42dd-8bba-bb06249733be",
            "name": "warehouse-1",
            "type": "sensor_rtsp",
            "resolution": "1920x1080",
        }]
        redis_scan = SimpleNamespace(
            stdout='1) "rtsp://127.0.0.1:30554/live/warehouse-1"'
        )

        with mock.patch(
            "scripts.overlay.metadata_service.requests.get", return_value=sensor_response
        ):
            with mock.patch("subprocess.run", return_value=redis_scan):
                discovered = _discover_sensors("http://localhost:30888")

        self.assertEqual(discovered, [(
            "warehouse-1",
            "rtsp://127.0.0.1:30554/live/warehouse-1",
            "H264",
            1920,
            1080,
        )])


if __name__ == "__main__":
    unittest.main()
