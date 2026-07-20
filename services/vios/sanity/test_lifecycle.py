# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import provision
from run_sanity import _cleanup_transient_artifacts, _run_plans


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


if __name__ == "__main__":
    unittest.main()
