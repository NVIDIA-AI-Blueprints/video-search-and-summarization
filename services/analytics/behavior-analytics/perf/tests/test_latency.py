# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from latency import build_result, correct_topics, parse_latency_report

CORE = Path(__file__).resolve().parents[1] / "benchmark" / "core" / "vss_perf_benchmark.py"
REPO_ROOT = Path(__file__).resolve().parents[5]

REPORT = """
RAW MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p95(s) |     p99(s) |
| sensor-a        |          10 |      0.010 |      0.030 |      0.020 |       1.000 |      0.020 |      0.028 |      0.030 |
FRAMES MESSAGES
| sensor-a        |          10 |      0.110 |      0.230 |      0.160 |       2.000 |      0.150 |      0.210 |      0.220 |
BEHAVIOR MESSAGES
| sensor-a        |           4 |      0.200 |      0.500 |      0.300 |       1.500 |      0.280 |      0.450 |      0.490 |
| sensor-b        |           6 |      0.100 |      0.400 |      0.200 |       1.500 |      0.180 |      0.350 |      0.390 |
EVENTS MESSAGES
| sensor-a        | No messages received |
INCIDENTS MESSAGES
| sensor-a        |           2 |      0.400 |      0.800 |      0.600 |       3.000 |      0.500 |      0.700 |      0.780 |
"""


class LatencyCorrectionTest(unittest.TestCase):
    def test_parse_and_weight_sensor_rows(self) -> None:
        topics = parse_latency_report(REPORT)
        self.assertEqual(topics["behavior"]["count"], 10)
        self.assertAlmostEqual(topics["behavior"]["avg_s"], 0.24)
        self.assertAlmostEqual(topics["behavior"]["min_s"], 0.1)
        self.assertAlmostEqual(topics["behavior"]["max_s"], 0.5)
        self.assertNotIn("events", topics)

    def test_subtract_raw_from_output_topics(self) -> None:
        result = correct_topics(parse_latency_report(REPORT))
        frames = result["topics"]["frames"]["corrected"]
        self.assertTrue(result["success"])
        self.assertAlmostEqual(frames["avg_s"], 0.14)
        self.assertAlmostEqual(frames["p95_s"], 0.182)
        self.assertAlmostEqual(result["topics"]["behavior"]["corrected"]["avg_s"], 0.22)
        self.assertFalse(result["topics"]["events"]["present"])

    def test_negative_correction_fails(self) -> None:
        topics = parse_latency_report(REPORT)
        topics["frames"]["avg_s"] = 0.001
        result = correct_topics(topics)
        self.assertFalse(result["success"])
        self.assertIn("frames.avg_s", result["error"])

    def test_missing_raw_fails(self) -> None:
        result = correct_topics({"frames": {"count": 1, "avg_s": 1}})
        self.assertFalse(result["success"])
        self.assertIn("raw", result["error"])

    def test_result_file_matches_expected_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            result = build_result(
                REPORT,
                artifact_ref="nvcr.io/example/behavior-analytics:test",
                scenario="quick_test",
                config_id="warehouse_2d-kafka-playback",
                duration_seconds=60,
                warmup_seconds=10,
            )
            output.write_text(json.dumps(result))
            saved = json.loads(output.read_text())
        expected = json.loads(
            (Path(__file__).parent / "expected_result.json").read_text()
        )
        self.assertEqual(saved["schema_version"], expected["schema_version"])
        self.assertEqual(saved["service"], expected["service"])
        self.assertEqual(saved["setup"], expected["setup"])
        self.assertEqual(saved["correction"], expected["correction"])
        self.assertTrue(saved["success"])

    def test_jenkins_entrypoint_exists_and_accepts_core_flags(self) -> None:
        self.assertTrue(CORE.is_file())
        env = {**os.environ, "PYTHONPATH": str(CORE.parent.parent)}
        completed = subprocess.run(
            [
                "python3",
                str(CORE),
                "--help",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("--config-id", completed.stdout)
        self.assertIn("--output-json", completed.stdout)
        self.assertIn("--upload", completed.stdout)

    def test_result_dir_honors_output_dir_env(self) -> None:
        spec = importlib.util.spec_from_file_location("ba_core_entrypoint", CORE)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            os.environ["OUTPUT_DIR"] = directory
            try:
                args = module.parse_args(
                    ["--scenario", "quick_test", "--config-id", "warehouse_2d-kafka-playback"]
                )
                self.assertEqual(module.result_dir(args), Path(directory))
            finally:
                os.environ.pop("OUTPUT_DIR", None)

    def test_generate_env_sources_under_nounset(self) -> None:
        script = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "tests"
            / "integration"
            / "generate_env.sh"
        )
        completed = subprocess.run(
            [
                "bash",
                "-c",
                "set -euo pipefail; "
                f'SCRIPT_DIR={str(script.parent)!r}; '
                "PROFILE1=warehouse_2d; PROFILE2=kafka; "
                f"source {str(script)!r}; "
                'test -n "$INFRA_DIR"; test -f "$ENV_FILE"',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Generating environment variables", completed.stdout)

    def test_deploy_builds_infra_images_not_behavior_analytics(self) -> None:
        deploy = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "perf"
            / "scripts"
            / "deploy.sh"
        ).read_text()
        self.assertNotIn("--no-build", deploy)
        self.assertIn("profile_services=", deploy)
        self.assertIn("compose up -d --build $profile_services analytics-stream", deploy)
        up_line = next(line for line in deploy.splitlines() if "compose up -d --build" in line)
        self.assertNotIn("playback", up_line)
        apps = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "tests"
            / "integration"
            / "docker_compose"
            / "apps"
            / "mdx-apps.yml"
        ).read_text()
        stream = apps.split("\n  analytics-stream:\n", 1)[1]
        self.assertNotIn("\n    build:", stream)
        self.assertIn("image: ${BEHAVIOR_ANALYTICS_IMAGE", stream)
        infra = (
            REPO_ROOT / "deploy" / "docker" / "services" / "infra" / "compose.yml"
        ).read_text()
        self.assertIn("\n  broker-health-check:", infra)
        self.assertIn("dockerfile: Dockerfiles/${STREAM_TYPE}-health-check.Dockerfile", infra)

    def test_repo_root_jenkins_facade_and_compose_mirror_exist(self) -> None:
        self.assertTrue((REPO_ROOT / "perf" / "benchmark" / "core" / "vss_perf_benchmark.py").is_file())
        self.assertTrue((REPO_ROOT / "perf" / "configs" / "perf-configs.yaml").is_file())
        self.assertTrue((REPO_ROOT / "perf" / "configs" / "config.yaml").is_file())
        for name in ("pre_flight.sh", "deploy.sh", "post_deploy.sh", "teardown.sh"):
            self.assertTrue((REPO_ROOT / "perf" / "scripts" / name).is_file())
        self.assertTrue(
            (REPO_ROOT / "compose" / "docker" / "services" / "infra" / "compose.yml").is_file()
        )


if __name__ == "__main__":
    unittest.main()
