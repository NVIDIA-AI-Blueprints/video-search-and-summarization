# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dump_loader import is_lfs_pointer, load_file
from http_latency import build_result, percentile, summarize
from kpis import DEFAULT_KPIS_PATH, evaluate_kpis, load_kpis
from minio_upload import minio_settings
from scenario_config import scenario_settings

PERF = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[5]
ROOT_RUN = REPO / "perf" / "run_benchmark.sh"
PRE_FLIGHT = PERF / "scripts" / "pre_flight.sh"
TEARDOWN = PERF / "scripts" / "teardown.sh"


class HttpLatencyTests(unittest.TestCase):
    def test_percentile_interpolates(self) -> None:
        values = [0.01, 0.02, 0.03, 0.04, 0.10]
        self.assertAlmostEqual(percentile(values, 50) or 0, 0.03)
        self.assertGreater(percentile(values, 99) or 0, percentile(values, 50) or 0)

    def test_kpis_yaml_loads_and_gates_result(self) -> None:
        self.assertTrue(DEFAULT_KPIS_PATH.is_file())
        kpis = load_kpis()
        spec = kpis["scenarios"]["quick_test"]["warehouse-es-dump"]
        self.assertEqual(spec["livez"]["max_p99_s"], 1.0)
        self.assertNotIn("max_p99_s", spec["frames"])
        measured = {
            "schema_version": "0.1",
            "service": "video-analytics-api",
            "scenario": "quick_test",
            "config_id": "warehouse-es-dump",
            "endpoints": {
                name: summarize([0.01, 0.02, 0.03], 20, 20)
                for name in spec["required_endpoints"]
            },
        }
        for name in spec["required_endpoints"]:
            measured["endpoints"][name]["present"] = True
        verdict = evaluate_kpis(measured, kpis)
        self.assertTrue(verdict["passed"], verdict)

    def test_missing_endpoint_fails(self) -> None:
        kpis = load_kpis()
        payload = build_result(
            {"livez": summarize([0.01], 1, 1)},
            artifact_ref="nvcr.io/example/video-analytics-api:test",
            scenario="quick_test",
            config_id="warehouse-es-dump",
            request_count=1,
            warmup_requests=0,
        )
        self.assertFalse(payload["success"])
        self.assertTrue(payload["kpis"]["violations"])

    def test_dump_loader_rejects_lfs_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mdx-frames.json"
            path.write_text(
                "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n"
            )
            self.assertTrue(is_lfs_pointer(path))
            with self.assertRaises(RuntimeError):
                load_file("http://127.0.0.1:19200", path)

    def test_scenario_config_reads_request_count(self) -> None:
        settings = scenario_settings(PERF / "configs" / "config.yaml", "quick_test")
        self.assertEqual(settings["request_count"], 20)
        self.assertEqual(settings["sensor_id"], "Camera")

    def test_pre_flight_does_not_chmod_0777(self) -> None:
        text = PRE_FLIGHT.read_text()
        self.assertNotIn("0777", text)
        self.assertNotIn("chmod 777", text)

    def test_teardown_is_project_scoped(self) -> None:
        text = TEARDOWN.read_text()
        self.assertIn("va-api-perf", text)
        self.assertNotIn("docker rm -f elasticsearch", text)

    def test_root_trampoline_points_at_api_package(self) -> None:
        text = ROOT_RUN.read_text()
        self.assertIn("video-analytics-api/perf/run_benchmark.sh", text)
        self.assertNotIn("behavior-analytics/perf/run_benchmark.sh", text)

    def test_minio_service_name(self) -> None:
        settings = minio_settings(PERF / "configs" / "config.yaml")
        self.assertEqual(settings["service"], "video-analytics-api")
        self.assertEqual(settings["bucket"], "perf-results-dev")

    def test_stage_failure_still_uploads_when_requested(self) -> None:
        core = PERF / "benchmark" / "core" / "vss_perf_benchmark.py"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "result.json"
            env = os.environ.copy()
            env["UPLOAD_TO_MINIO"] = "true"
            env["MINIO_ACCESS_KEY"] = "test"
            env["MINIO_SECRET_KEY"] = "test"
            with patch("minio_upload.publish_result", return_value={"uri": "s3://bucket/obj"}):
                import importlib.util

                spec = importlib.util.spec_from_file_location("va_core", core)
                self.assertIsNotNone(spec)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)  # type: ignore[union-attr]
                with patch.object(module, "_upload", return_value={"uri": "s3://bucket/obj"}):
                    rc = module.main(
                        [
                            "--output-json",
                            str(dest),
                            "--stage-failure",
                            "deploy",
                            "--failure-message",
                            "compose failed",
                            "--upload",
                        ]
                    )
                self.assertEqual(rc, 1)
                payload = json.loads(dest.read_text())
                self.assertFalse(payload["success"])
                self.assertEqual(payload["service"], "video-analytics-api")


if __name__ == "__main__":
    unittest.main()
