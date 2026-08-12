# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the direct-mode overlay webhook receiver."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import requests

BDD_ROOT = Path(__file__).resolve().parents[1] / "test/bdd_tests"
sys.path.insert(0, str(BDD_ROOT))

from scripts.overlay.metadata_service import OverlayWebhookServer  # noqa: E402


class OverlayWebhookServerTests(unittest.TestCase):
    def setUp(self):
        self.streaming = []
        self.removed = []
        self.server = OverlayWebhookServer(
            "127.0.0.1",
            0,
            lambda sensor_id, url, codec: self.streaming.append((sensor_id, url, codec)),
            lambda sensor_id: self.removed.append(sensor_id),
        ).start()
        self.base_url = f"http://127.0.0.1:{self.server.port}"

    def tearDown(self):
        self.server.stop()

    def test_camera_streaming_starts_rtsp_worker(self):
        response = requests.put(
            f"{self.base_url}/bdd/webhooks/camera/streaming",
            json={"event": {
                "change": "camera_streaming",
                "camera_id": "4e8bd6de-17f3-42dd-8bba-bb06249733be",
                "camera_name": "warehouse-1",
                "camera_type": "rtsp",
                "camera_url": "rtsp://127.0.0.1:30554/live/warehouse-1",
                "metadata": {"codec": "H265"},
            }},
            timeout=5,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.streaming,
            [("warehouse-1", "rtsp://127.0.0.1:30554/live/warehouse-1", "H265")],
        )

    def test_camera_remove_stops_worker(self):
        response = requests.delete(
            f"{self.base_url}/bdd/webhooks/camera/remove",
            json={"event": {
                "change": "camera_remove",
                "camera_id": "4e8bd6de-17f3-42dd-8bba-bb06249733be",
                "camera_name": "warehouse-1",
            }},
            timeout=5,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.removed, ["warehouse-1"])

    def test_streaming_acknowledges_file_without_worker(self):
        file_response = requests.put(
            f"{self.base_url}/bdd/webhooks/camera/streaming",
            json={"event": {
                "change": "camera_streaming",
                "camera_id": "file-uuid",
                "camera_name": "warehouse-file",
                "camera_type": "file",
                "camera_url": "file:///tmp/warehouse.mp4",
            }},
            timeout=5,
        )
        self.assertEqual(file_response.status_code, 200)
        self.assertEqual(self.streaming, [])

    def test_streaming_rejects_wrong_event(self):
        wrong_response = requests.delete(
            f"{self.base_url}/bdd/webhooks/camera/remove",
            json={"event": {"change": "camera_streaming", "camera_id": "warehouse-1"}},
            timeout=5,
        )
        self.assertEqual(wrong_response.status_code, 400)
        self.assertEqual(self.streaming, [])
        self.assertEqual(self.removed, [])


if __name__ == "__main__":
    unittest.main()
