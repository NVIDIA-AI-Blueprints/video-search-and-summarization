# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import provision
from run_sanity import _cleanup_transient_artifacts, _run_plans, _start_metadata_service

_OVERLAY = Path(__file__).resolve().parents[1] / "test/bdd_tests/scripts/overlay"
sys.path.insert(0, str(_OVERLAY))
from fake_es_server import FakeESStore


class LifecycleTest(unittest.TestCase):
    def test_cleanup_removes_only_reproducible_transients(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            provision = root / "provision"
            provision.mkdir()
            (provision / "generated.mp4").write_bytes(b"generated")
            (root / "case_control.mp4").write_bytes(b"control")
            (root / "evidence.mp4").write_bytes(b"evidence")
            (root / "report.pdf").write_bytes(b"report")
            (root / "failed_cases.json").write_text("{}")

            stats = _cleanup_transient_artifacts(root)

            self.assertEqual(stats["files"], 2)
            self.assertFalse(provision.exists())
            self.assertFalse((root / "case_control.mp4").exists())
            self.assertTrue((root / "evidence.mp4").exists())
            self.assertTrue((root / "report.pdf").exists())
            self.assertTrue((root / "failed_cases.json").exists())

    def test_video_cleanup_preserves_unrelated_uploads(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            owned = root / "Warehouse_test_h264_1080p.mp4"
            unrelated = root / "my_ui_upload.mp4"
            owned.write_bytes(b"owned")
            unrelated.write_bytes(b"user")
            old_root = provision._NVS_VIDEOS
            try:
                provision._NVS_VIDEOS = root
                removed = provision.wipe_nvstreamer_videos("/inputs/Warehouse_test.mp4")
            finally:
                provision._NVS_VIDEOS = old_root
            self.assertEqual(removed, 1)
            self.assertFalse(owned.exists())
            self.assertTrue(unrelated.exists())

    def test_keep_deployment_rejects_multiple_enabled_plans(self):
        plans = Path(__file__).with_name("sanity_plans.yaml")
        with self.assertRaisesRegex(ValueError, "exactly one enabled plan/system"):
            _run_plans(str(plans), keep_deployment=True)

    def test_fake_es_retention_is_time_based_per_sensor(self):
        hour_ms = 60 * 60 * 1000
        store = FakeESStore(retention_hours=3)
        store.append([
            {"id": "a-old", "sensorId": "a", "_epoch_ms": 0},
            {"id": "b-only", "sensorId": "b", "_epoch_ms": 0},
            {"id": "a-new", "sensorId": "a", "_epoch_ms": 4 * hour_ms},
        ])

        hits = store.search(None, None, None, None, None)["hits"]["hits"]
        retained = {(hit["_source"]["sensorId"], hit["_id"]) for hit in hits}
        self.assertEqual(retained, {("a", "a-new"), ("b", "b-only")})

    def test_metadata_service_receives_requested_retention(self):
        ctx = SimpleNamespace(
            broker="redis",
            base_url="http://localhost:30888",
            nvstreamer_url="http://localhost:31000",
        )
        with mock.patch.dict("os.environ", {"VIOS_SANITY_ES_RETENTION_HOURS": "10"}):
            with mock.patch("subprocess.Popen") as popen:
                popen.return_value.poll.return_value = None
                _start_metadata_service(ctx, wait_s=0)

        command = popen.call_args.args[0]
        flag_index = command.index("--es-retention-hours")
        self.assertEqual(command[flag_index + 1], "10")


if __name__ == "__main__":
    unittest.main()
