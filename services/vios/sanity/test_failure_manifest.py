# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from run_sanity import _dump_failures
from sanity_common import SanityContext, run_usecase


class FailureManifestTest(unittest.TestCase):
    def test_failure_keeps_request_and_utc_timestamps(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            ctx = SanityContext(
                base_url="http://test-host:30888",
                host_ip="10.0.0.8",
                share_dir=root / "share",
                out_dir=root / "out",
                file_server_base="http://10.0.0.8:18080",
            )

            def fail_after_request(context):
                context.active_request = {
                    "type": "http",
                    "method": "GET",
                    "url": "http://test-host:30888/vst/api/v1/example?overlay=true",
                    "query_params": {"overlay": "true"},
                    "headers": {"streamid": "camera-1"},
                    "body": None,
                }
                raise AssertionError("missing overlay")

            result = run_usecase("picture_recent_overlay", fail_after_request, ctx)
            result.plan = "Plan-1"
            result.group = "picture"
            manifest_path = root / "failed_cases.json"
            info = _dump_failures([result], ctx, manifest_path)
            manifest = json.loads(manifest_path.read_text())

            self.assertEqual(info["count"], 1)
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(manifest["summary"]["fail"], 1)
            self.assertEqual(manifest["run"]["started_at"], result.started_at)
            self.assertEqual(manifest["timezone"], {"name": "UTC", "utc_offset": "+0000"})
            self.assertTrue(manifest["generated_at"].endswith("Z"))
            self.assertTrue(result.started_at.endswith("Z"))
            self.assertTrue(result.finished_at.endswith("Z"))
            datetime.fromisoformat(result.started_at)
            datetime.fromisoformat(result.finished_at)
            failure = manifest["failed_cases"][0]
            self.assertEqual(failure["request"]["method"], "GET")
            self.assertIn("?overlay=true", failure["request"]["url"])
            self.assertEqual(failure["request"]["headers"]["streamid"], "camera-1")


if __name__ == "__main__":
    unittest.main()
