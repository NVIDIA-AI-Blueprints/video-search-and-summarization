# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PERF = Path(__file__).resolve().parents[1]
BENCH = PERF / "benchmark"
sys.path.insert(0, str(BENCH))

from http_latency import percentile, summarize  # noqa: E402
from kpis import DEFAULT_KPIS_PATH, evaluate_kpis, load_kpis  # noqa: E402
from latency import correct_topics, parse_latency_report  # noqa: E402
from scenario_config import scenario_settings  # noqa: E402

SCRIPTS = [
    PERF / "run_benchmark.sh",
    PERF / "scripts" / "pre_flight.sh",
    PERF / "scripts" / "deploy.sh",
    PERF / "scripts" / "post_deploy.sh",
    PERF / "scripts" / "measure.sh",
    PERF / "scripts" / "teardown.sh",
    PERF / "scripts" / "common.sh",
]


def load_overlay():
    path = PERF / "scripts" / "overlay_env.py"
    spec = importlib.util.spec_from_file_location("overlay_env", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SAMPLE_REPORT = """
Statistics Report
RAW MESSAGES
| Sensor | count | min(s) | max(s) | avg(s) | size(kb) | p50(s) | p95(s) | p99(s) |
| Camera | 200 | 0.010 | 0.040 | 0.020 | 1.0 | 0.018 | 0.030 | 0.035 |
FRAMES MESSAGES
| Sensor | count | min(s) | max(s) | avg(s) | size(kb) | p50(s) | p95(s) | p99(s) |
| Camera | 200 | 0.020 | 0.050 | 0.030 | 1.0 | 0.028 | 0.040 | 0.045 |
BEHAVIOR MESSAGES
| Sensor | count | min(s) | max(s) | avg(s) | size(kb) | p50(s) | p95(s) | p99(s) |
| Camera | 20 | 0.030 | 0.080 | 0.050 | 1.0 | 0.048 | 0.070 | 0.075 |
EVENTS MESSAGES
| Sensor | count | min(s) | max(s) | avg(s) | size(kb) | p50(s) | p95(s) | p99(s) |
| Camera | 10 | 0.030 | 0.070 | 0.045 | 1.0 | 0.040 | 0.060 | 0.065 |
INCIDENTS MESSAGES
| Sensor | count | min(s) | max(s) | avg(s) | size(kb) | p50(s) | p95(s) | p99(s) |
| Camera | 10 | 0.025 | 0.060 | 0.040 | 1.0 | 0.035 | 0.050 | 0.055 |
"""


class OverlayEnvTests(unittest.TestCase):
    def test_upsert_replaces_and_appends(self) -> None:
        overlay = load_overlay()
        text = "MODE=3d\nHOST_IP='<HOST_IP>'\n"
        updated = overlay.upsert_env(text, {"MODE": "2d", "VSS_DATA_DIR": "/tmp/data"})
        self.assertIn("MODE=2d", updated)
        self.assertNotIn("MODE=3d", updated)
        self.assertIn("VSS_DATA_DIR=/tmp/data", updated)


class LatencyTests(unittest.TestCase):
    def test_parse_and_correct(self) -> None:
        topics = parse_latency_report(SAMPLE_REPORT)
        self.assertEqual(topics["raw"]["count"], 200)
        correction = correct_topics(topics)
        self.assertTrue(correction["success"], correction)
        frames = correction["topics"]["frames"]["corrected"]
        self.assertAlmostEqual(frames["p50_s"], 0.01)
        self.assertGreaterEqual(frames["min_s"], 0.0)


class HttpTests(unittest.TestCase):
    def test_percentile_and_summarize(self) -> None:
        values = [0.01, 0.02, 0.03, 0.04, 0.10]
        self.assertAlmostEqual(percentile(values, 50) or 0, 0.03)
        summary = summarize(values, 5, 5)
        self.assertTrue(summary["present"])
        self.assertEqual(summary["ok_count"], 5)


class KpiTests(unittest.TestCase):
    def test_kpis_yaml_gates_combined_result(self) -> None:
        self.assertTrue(DEFAULT_KPIS_PATH.is_file())
        kpis = load_kpis()
        correction = correct_topics(parse_latency_report(SAMPLE_REPORT))
        measured = {
            "schema_version": "0.1",
            "service": "warehouse-bp",
            "scenario": "quick_test",
            "config_id": "warehouse-2d-kafka",
            "behavior_analytics": {
                "raw": correction["raw"],
                "topics": correction["topics"],
            },
            "video_analytics_api": {
                "endpoints": {
                    name: summarize([0.001, 0.002, 0.003], 20, 20)
                    for name in ("livez", "frames", "behavior", "events", "incidents")
                },
            },
        }
        for name, samples in (
            ("livez", [0.0006, 0.0007, 0.001]),
            ("frames", [0.018, 0.022, 0.030]),
            ("behavior", [0.016, 0.020, 0.030]),
            ("events", [0.017, 0.019, 0.030]),
            ("incidents", [0.015, 0.016, 0.030]),
        ):
            measured["video_analytics_api"]["endpoints"][name] = summarize(samples, 20, 20)
        verdict = evaluate_kpis(measured, kpis)
        self.assertTrue(verdict["passed"], verdict)

    def test_missing_ba_raw_fails(self) -> None:
        kpis = load_kpis()
        measured = {
            "scenario": "quick_test",
            "config_id": "warehouse-2d-kafka",
            "behavior_analytics": {"raw": {}, "topics": {}},
            "video_analytics_api": {
                "endpoints": {
                    name: summarize([0.001], 20, 20)
                    for name in ("livez", "frames", "behavior", "events", "incidents")
                }
            },
        }
        verdict = evaluate_kpis(measured, kpis)
        self.assertFalse(verdict["passed"])
        self.assertTrue(any("ba.raw" in item for item in verdict["violations"]))


class ScenarioTests(unittest.TestCase):
    def test_quick_test_settings(self) -> None:
        settings = scenario_settings(PERF / "configs" / "config.yaml", "quick_test")
        self.assertEqual(settings["duration_seconds"], 60)
        self.assertEqual(settings["sensor_id"], "Camera")
        self.assertIn("behavior", settings["topics"])
        self.assertIn("frames", settings["endpoints"])


class ScriptTests(unittest.TestCase):
    def test_shell_syntax(self) -> None:
        for script in SCRIPTS:
            completed = subprocess.run(
                ["bash", "-n", str(script)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, f"{script}: {completed.stderr}")

    def test_spdx_headers(self) -> None:
        for script in SCRIPTS:
            text = script.read_text()
            self.assertIn("SPDX-FileCopyrightText", text)
            self.assertIn("SPDX-License-Identifier: Apache-2.0", text)

    def test_pre_flight_reports_missing_files(self) -> None:
        text = (PERF / "scripts" / "pre_flight.sh").read_text()
        self.assertIn("pre-flight: missing file:", text)
        self.assertIn("need_file \"$WAREHOUSE_DIR/compose.yml\"", text)
        self.assertIn("materialize_warehouse_compose", text)
        self.assertIn("materializing deploy/docker from git", text)

    def test_deploy_handles_local_bringup_holes(self) -> None:
        common = (PERF / "scripts" / "common.sh").read_text()
        post = (PERF / "scripts" / "post_deploy.sh").read_text()
        self.assertIn("ghcr.io/nvidia-ai-blueprints/vss", common)
        self.assertNotIn("nvcr.io/nvstaging/vss-core", common)
        self.assertIn("BP_CONFIGURATOR_ENV_FILE=$GENERATED_ENV", common)
        self.assertIn("normalize_warehouse_app_data", common)
        self.assertIn("chmod -R a+rX", common)
        self.assertIn("wait_kafka_raw", post)
        self.assertIn("mdx-raw", post)

    def test_flatten_nested_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app-data"
            inner = root / "vss-warehouse-app-data"
            (inner / "videos").mkdir(parents=True)
            video = inner / "videos" / "cam.mp4"
            video.write_bytes(b"x")
            os.chmod(inner, 0o750)
            os.chmod(video, 0o640)
            completed = subprocess.run(
                ["bash", str(PERF / "scripts" / "common.sh"), "normalize-app-data", str(root)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            flattened = root / "videos" / "cam.mp4"
            self.assertTrue(flattened.is_file())
            self.assertFalse((root / "vss-warehouse-app-data").exists())
            self.assertTrue(flattened.stat().st_mode & stat.S_IROTH)

    def test_flatten_tarball_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app-data"
            staging = Path(tmp) / "src" / "vss-warehouse-app-data" / "videos"
            staging.mkdir(parents=True)
            (staging / "cam.mp4").write_bytes(b"x")
            root.mkdir()
            archive = root / "vss-warehouse-app-data.tar.gz"
            packed = subprocess.run(
                [
                    "tar",
                    "-czf",
                    str(archive),
                    "-C",
                    str(Path(tmp) / "src"),
                    "vss-warehouse-app-data",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(packed.returncode, 0, packed.stderr)
            completed = subprocess.run(
                ["bash", str(PERF / "scripts" / "common.sh"), "normalize-app-data", str(root)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue((root / "videos" / "cam.mp4").is_file())

    def test_overlay_cli(self) -> None:
        overlay = PERF / "scripts" / "overlay_env.py"
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "overrides.env"
            dest = Path(tmp) / "generated.env"
            source.write_text("MODE=3d\nHOST_IP='<HOST_IP>'\n")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(overlay),
                    "--source",
                    str(source),
                    "--dest",
                    str(dest),
                    "MODE=2d",
                    "HOST_IP=10.0.0.1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            text = dest.read_text()
            self.assertIn("MODE=2d", text)
            self.assertIn("HOST_IP=10.0.0.1", text)


if __name__ == "__main__":
    unittest.main()
