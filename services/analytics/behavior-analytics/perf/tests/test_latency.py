# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from kpis import DEFAULT_KPIS_PATH, _load_simple_yaml, evaluate_kpis, load_kpis
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

CUMULATIVE_REPORT = """
Statistics Report - 2026-10-18 22:45:04 - Batch ID: 2
=========================================================================================================================

Statistics Report - 2026-10-18 22:46:59 - Batch ID: 13
=========================================================================================================================

RAW MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p95(s) |     p99(s) |
| Camera          |         298 |      0.000 |      0.003 |      0.000 |       0.294 |      0.000 |      0.001 |      0.001 |
| Camera_01       |         298 |      0.000 |      0.003 |      0.000 |       0.329 |      0.000 |      0.001 |      0.001 |
FRAMES MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p95(s) |     p99(s) |
| Camera          |         301 |      0.001 |      0.019 |      0.007 |       0.900 |      0.007 |      0.012 |      0.012 |

Statistics Report - 2026-10-18 22:47:52 - Batch ID: 18
=========================================================================================================================

RAW MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p95(s) |     p99(s) |
| Camera          |        1883 |      0.000 |      0.003 |      0.000 |       0.333 |      0.000 |      0.001 |      0.001 |
| Camera_01       |        1883 |      0.000 |      0.003 |      0.000 |       0.356 |      0.000 |      0.001 |      0.001 |
| Camera_02       |        1883 |      0.000 |      0.003 |      0.000 |       0.366 |      0.000 |      0.001 |      0.001 |
FRAMES MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p95(s) |     p99(s) |
| Camera          |        1890 |      0.001 |      0.019 |      0.007 |       1.001 |      0.007 |      0.012 |      0.012 |
| Camera_01       |        1890 |      0.001 |      0.019 |      0.007 |       1.056 |      0.007 |      0.012 |      0.013 |
| Camera_02       |        1890 |      0.002 |      0.019 |      0.008 |       1.072 |      0.008 |      0.013 |      0.013 |
BEHAVIOR MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p95(s) |     p99(s) |
| Camera          |           3 |      6.011 |      6.017 |      6.013 |       7.275 |      6.012 |      6.017 |      6.017 |
| Camera_01       |           2 |      6.015 |      6.016 |      6.015 |       6.636 |      6.015 |      6.016 |      6.016 |
| Camera_02       |           2 |      6.015 |      6.020 |      6.018 |       7.413 |      6.015 |      6.020 |      6.020 |
EVENTS MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p95(s) |     p99(s) |
| Camera          |          16 |      0.010 |      0.074 |      0.022 |       0.446 |      0.019 |      0.074 |      0.074 |
| Camera_01       |          18 |      0.010 |      0.030 |      0.019 |       0.451 |      0.020 |      0.030 |      0.030 |
| Camera_02       |          16 |      0.011 |      0.027 |      0.018 |       0.452 |      0.017 |      0.027 |      0.027 |
INCIDENTS MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p95(s) |     p99(s) |
| Camera          |        2049 |      0.001 |      0.013 |      0.007 |       0.127 |      0.007 |      0.012 |      0.013 |
| Camera_01       |        2122 |      0.002 |      0.014 |      0.008 |       0.130 |      0.008 |      0.012 |      0.013 |
| Camera_02       |        1939 |      0.002 |      0.014 |      0.008 |       0.129 |      0.008 |      0.013 |      0.014 |
"""


class LatencyCorrectionTest(unittest.TestCase):
    def test_parse_and_weight_sensor_rows(self) -> None:
        topics = parse_latency_report(REPORT)
        self.assertEqual(topics["behavior"]["count"], 10)
        self.assertAlmostEqual(topics["behavior"]["avg_s"], 0.24)
        self.assertAlmostEqual(topics["behavior"]["min_s"], 0.1)
        self.assertAlmostEqual(topics["behavior"]["max_s"], 0.5)
        self.assertNotIn("events", topics)

    def test_parse_uses_last_cumulative_dump_only(self) -> None:
        topics = parse_latency_report(CUMULATIVE_REPORT)
        self.assertEqual(topics["raw"]["count"], 5649)
        self.assertEqual(topics["frames"]["count"], 5670)
        self.assertEqual(topics["behavior"]["count"], 7)
        self.assertEqual(topics["events"]["count"], 50)
        self.assertEqual(topics["incidents"]["count"], 6110)
        result = build_result(
            CUMULATIVE_REPORT,
            artifact_ref="nvcr.io/example/behavior-analytics:test",
            scenario="quick_test",
            config_id="warehouse_2d-kafka-playback",
            duration_seconds=60,
            warmup_seconds=10,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["raw"]["count"], 5649)
        self.assertEqual(result["topics"]["frames"]["total"]["count"], 5670)
        self.assertAlmostEqual(result["topics"]["frames"]["corrected"]["avg_s"], 0.007333, places=5)
        self.assertTrue(result["kpis"]["passed"], result["kpis"])

    def test_subtract_raw_from_output_topics(self) -> None:
        result = correct_topics(parse_latency_report(REPORT))
        frames = result["topics"]["frames"]["corrected"]
        self.assertTrue(result["success"])
        self.assertAlmostEqual(frames["avg_s"], 0.14)
        self.assertAlmostEqual(frames["p95_s"], 0.182)
        self.assertAlmostEqual(result["topics"]["behavior"]["corrected"]["avg_s"], 0.22)
        self.assertFalse(result["topics"]["events"]["present"])

    def test_kpis_yaml_loads_and_gates_result(self) -> None:
        self.assertTrue(DEFAULT_KPIS_PATH.is_file())
        kpis = load_kpis()
        simple = _load_simple_yaml(DEFAULT_KPIS_PATH.read_text())
        spec = kpis["scenarios"]["quick_test"]["warehouse_2d-kafka-playback"]
        self.assertEqual(simple["scenarios"]["quick_test"]["warehouse_2d-kafka-playback"]["frames"]["max_p95_s"], spec["frames"]["max_p95_s"])
        self.assertEqual(spec["required_topics"], ["raw", "frames", "behavior", "events", "incidents"])
        passing = build_result(
            CUMULATIVE_REPORT,
            artifact_ref="nvcr.io/example/behavior-analytics:test",
            scenario="quick_test",
            config_id="warehouse_2d-kafka-playback",
            duration_seconds=60,
            warmup_seconds=10,
        )
        self.assertTrue(passing["success"], passing.get("error"))
        tight = {
            "scenarios": {
                "quick_test": {
                    "warehouse_2d-kafka-playback": {
                        "required_topics": ["raw", "frames"],
                        "frames": {"max_p95_s": 0.001},
                    }
                }
            }
        }
        failed = evaluate_kpis(passing, tight)
        self.assertFalse(failed["passed"])
        self.assertTrue(any("frames.corrected.p95_s" in item for item in failed["violations"]))
        missing_events = build_result(
            REPORT,
            artifact_ref="nvcr.io/example/behavior-analytics:test",
            scenario="quick_test",
            config_id="warehouse_2d-kafka-playback",
            duration_seconds=60,
            warmup_seconds=10,
        )
        self.assertFalse(missing_events["success"])
        self.assertIn("events missing", missing_events["kpis"]["violations"])

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
                apply_kpis=False,
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
        self.assertIn("--vlm-gpus", completed.stdout)
        spec = importlib.util.spec_from_file_location("ba_core_entrypoint", CORE)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        args = module.parse_args(
            [
                "--compose-file",
                "infra/compose.yml",
                "--vlm-gpus",
                "unused",
                "--llm-gpus",
                "unused",
                "--vlm-model",
                "unused",
                "--llm-model",
                "unused",
                "--vision-input-tokens",
                "unused",
                "--gpu-model",
                "RTXPRO6000BW-SE",
                "--pipeline-url",
                "https://example.invalid",
                "--future-jenkins-flag",
                "ignored",
            ]
        )
        self.assertEqual(args.vlm_gpus, "unused")
        self.assertEqual(args.gpu_model, "RTXPRO6000BW-SE")

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
        isolated = subprocess.run(
            [
                "bash",
                "-c",
                "set -euo pipefail; "
                f'SCRIPT_DIR={str(script.parent)!r}; '
                "PROFILE1=warehouse_2d; PROFILE2=kafka; "
                "BA_PERF_DATA_DIR=/tmp/ba-perf-mdx; "
                f"source {str(script)!r}; "
                'test "$VSS_DATA_DIR" = /tmp/ba-perf-mdx; '
                'test "$MDX_DATA_DIR" != "$VSS_DATA_DIR"; '
                'test "$COMPOSE_PROJECT_NAME" = ba-perf',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(isolated.returncode, 0, isolated.stderr)

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
        self.assertIn('chmod -R a+rwX "$BA_PERF_DATA_DIR"', deploy)
        self.assertIn("export BA_PERF_DATA_DIR", deploy)
        teardown = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "perf"
            / "scripts"
            / "teardown.sh"
        ).read_text()
        self.assertIn("compose down --remove-orphans -v", teardown)
        self.assertIn("remove_root_tree", teardown)
        generate_env = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "tests"
            / "integration"
            / "generate_env.sh"
        ).read_text()
        self.assertIn("BA_PERF_DATA_DIR", generate_env)
        self.assertIn("profile_services=", deploy)
        self.assertIn("compose up -d --build --force-recreate $profile_services analytics-stream", deploy)
        self.assertIn("docker volume rm -f", deploy)
        measure = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "perf"
            / "scripts"
            / "measure.sh"
        ).read_text()
        self.assertIn("starting playback", measure)
        self.assertIn("--entrypoint python3", measure)
        self.assertIn("tools/latency/latency_monitor.py", measure)
        pre_flight = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "perf"
            / "scripts"
            / "pre_flight.sh"
        ).read_text()
        self.assertIn("git-lfs.github.com", pre_flight)
        self.assertIn('configs/kpis.yaml', pre_flight)
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

    def test_perf_config_compose_path_exists_for_jenkins(self) -> None:
        marker = "composePath:"
        for rel in (
            Path("perf/configs/perf-configs.yaml"),
            Path("services/analytics/behavior-analytics/perf/configs/perf-configs.yaml"),
        ):
            compose_path = None
            for line in (REPO_ROOT / rel).read_text().splitlines():
                if line.lstrip().startswith(marker):
                    compose_path = line.split(":", 1)[1].strip()
                    break
            self.assertTrue(compose_path, f"{rel} is missing composePath")
            self.assertTrue(
                (REPO_ROOT / compose_path).is_file(),
                f"{rel} composePath {compose_path} is missing",
            )
            text = (REPO_ROOT / rel).read_text()
            for key in (
                "vlmGpus:",
                "llmGpus:",
                "vlmModel:",
                "llmModel:",
                "vision_input_tokens:",
            ):
                self.assertIn(key, text, f"{rel} is missing {key} for Jenkins set -u")

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
