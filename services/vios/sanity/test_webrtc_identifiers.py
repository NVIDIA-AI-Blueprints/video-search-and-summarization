# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for VIOS API sensor IDs versus VST-UI display names."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from usecases import webrtc_replay


class WebRTCIdentifierTests(unittest.TestCase):
    def test_replay_uses_uuid_for_timeline_and_name_for_ui(self):
        sensor_id = "4e8bd6de-17f3-42dd-8bba-bb06249733be"
        sensor_name = "warehouse-1"
        timeline_response = mock.Mock()
        timeline_response.json.return_value = {
            sensor_id: [{"startTime": "2026-07-28T16:00:00.000Z"}]
        }

        with tempfile.TemporaryDirectory() as tempdir:
            ctx = SimpleNamespace(
                base_url="http://localhost:30888",
                verify_ssl=False,
                out_dir=Path(tempdir),
                stream_names={sensor_id: sensor_name},
                publish=lambda path, name: f"http://artifacts/{name}",
            )
            with mock.patch("usecases.requests.get", return_value=timeline_response) as get:
                with mock.patch("usecases.subprocess.run") as run:
                    with mock.patch("usecases._frame_from_mp4", return_value=None):
                        with mock.patch("usecases._assert_panel_box"):
                            result = webrtc_replay(
                                ctx, target="rtsp", overlay=True, sensor=sensor_id
                            )

        self.assertEqual(result.status, "PASS")
        self.assertIn(sensor_id, timeline_response.json.return_value)
        self.assertTrue(get.call_args.args[0].endswith("/storage/timelines"))
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--stream-id") + 1], sensor_name)
        self.assertEqual(result.request["params"]["sensor_id"], sensor_id)
        self.assertEqual(result.request["params"]["sensor_name"], sensor_name)


if __name__ == "__main__":
    unittest.main()
