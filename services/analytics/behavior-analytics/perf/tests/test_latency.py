# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from kpis import DEFAULT_KPIS_PATH, _load_simple_yaml, evaluate_kpis, load_kpis
from latency import build_result, correct_topics, parse_latency_report
from minio_upload import minio_settings, publish_result
from scenario_config import scenario_settings
from safe_fs import safe_write_text

OVERLAY = (
    Path(__file__).resolve().parents[1] / "scripts" / "overlay_app_config.py"
)

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

    def test_parse_keeps_complete_dump_if_final_report_is_partial(self) -> None:
        partial = (
            CUMULATIVE_REPORT
            + "\nStatistics Report - 2026-10-18 22:48:00 - Batch ID: 19\n"
            + "RAW MESSAGES\n"
            + "| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p95(s) |     p99(s) |\n"
            + "| Camera          |        2000 |      0.000 |      0.003 |      0.000 |       0.333 |      0.000 |      0.001 |      0.001 |\n"
            + "FRAMES MESSAGES\n"
            + "| Camera          |        2000 |      0.001 |      0.019 |      0.007 |       1.001 |      0.007 |      0.012 |      0.012 |\n"
        )
        topics = parse_latency_report(partial)
        self.assertEqual(topics["raw"]["count"], 5649)
        self.assertEqual(topics["behavior"]["count"], 7)
        self.assertEqual(topics["incidents"]["count"], 6110)

    def test_parse_uses_latest_finished_dump_when_topic_has_no_messages(self) -> None:
        latest = CUMULATIVE_REPORT.replace(
            "| Camera          |        2049 |      0.001 |      0.013 |      0.007 |       0.127 |      0.007 |      0.012 |      0.013 |\n"
            "| Camera_01       |        2122 |      0.002 |      0.014 |      0.008 |       0.130 |      0.008 |      0.012 |      0.013 |\n"
            "| Camera_02       |        1939 |      0.002 |      0.014 |      0.008 |       0.129 |      0.008 |      0.013 |      0.014 |",
            "| Camera          | No messages received |",
        ).replace("| Camera          |        1883 |", "| Camera          |        2883 |")

        topics = parse_latency_report(latest)

        self.assertEqual(topics["raw"]["count"], 6649)
        self.assertNotIn("incidents", topics)

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
        self.assertFalse(result["success"], result.get("error"))
        self.assertEqual(result["raw"]["count"], 5649)
        self.assertEqual(result["topics"]["frames"]["total"]["count"], 5670)
        self.assertAlmostEqual(result["topics"]["frames"]["corrected"]["avg_s"], 0.007333, places=5)
        self.assertFalse(result["kpis"]["passed"], result["kpis"])
        self.assertTrue(
            any("behavior.corrected.p50_s" in item for item in result["kpis"]["violations"]),
            result["kpis"],
        )

    def test_parse_maps_percentile_columns_from_header(self) -> None:
        report = """
RAW MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p99(s) |
| Camera          |          10 |      0.010 |      0.030 |      0.020 |       1.000 |      0.020 |      0.030 |
FRAMES MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p99(s) |
| Camera          |          10 |      0.110 |      0.230 |      0.160 |       2.000 |      0.150 |      0.220 |
BEHAVIOR MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p99(s) |
| Camera          |           4 |      0.200 |      0.500 |      0.300 |       1.500 |      0.280 |      0.490 |
EVENTS MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p99(s) |
| Camera          |          10 |      0.120 |      0.240 |      0.170 |       1.200 |      0.160 |      0.230 |
INCIDENTS MESSAGES
| Sensor          |       Count |     Min(s) |     Max(s) |     Avg(s) |    Size(KB) |     p50(s) |     p99(s) |
| Camera          |           2 |      0.400 |      0.800 |      0.600 |       3.000 |      0.500 |      0.780 |
"""
        topics = parse_latency_report(report)
        self.assertEqual(topics["raw"]["p50_s"], 0.02)
        self.assertEqual(topics["raw"]["p99_s"], 0.03)
        self.assertNotIn("p95_s", topics["raw"])
        self.assertEqual(topics["frames"]["p99_s"], 0.22)
        corrected = correct_topics(topics)
        self.assertEqual(corrected["topics"]["frames"]["corrected"]["p99_s"], 0.19)
        self.assertIsNone(corrected["topics"]["frames"]["corrected"]["p95_s"])

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
        self.assertEqual(
            simple["scenarios"]["quick_test"]["warehouse_2d-kafka-playback"]["frames"]["max_p50_s"],
            spec["frames"]["max_p50_s"],
        )
        self.assertEqual(spec["frames"]["max_p50_s"], 0.02)
        self.assertEqual(spec["frames"]["max_p99_s"], 0.05)
        self.assertEqual(spec["incidents"]["max_p50_s"], 0.03)
        self.assertEqual(spec["incidents"]["max_p99_s"], 0.06)
        self.assertEqual(spec["behavior"]["max_p50_s"], 0.06)
        self.assertEqual(spec["behavior"]["max_p99_s"], 0.12)
        self.assertEqual(spec["events"]["max_p50_s"], 0.06)
        self.assertEqual(spec["events"]["max_p99_s"], 0.12)
        self.assertEqual(spec["required_topics"], ["raw", "frames", "behavior", "events", "incidents"])
        measured = build_result(
            CUMULATIVE_REPORT,
            artifact_ref="nvcr.io/example/behavior-analytics:test",
            scenario="quick_test",
            config_id="warehouse_2d-kafka-playback",
            duration_seconds=60,
            warmup_seconds=10,
        )
        self.assertFalse(measured["success"], measured.get("error"))
        self.assertTrue(
            any("behavior.corrected.p50_s" in item for item in measured["kpis"]["violations"]),
            measured["kpis"],
        )
        in_process = CUMULATIVE_REPORT
        for token in ("6.011", "6.012", "6.013", "6.015", "6.016", "6.017", "6.018", "6.020"):
            in_process = in_process.replace(token, "0.030")
        passing = build_result(
            in_process,
            artifact_ref="nvcr.io/example/behavior-analytics:test",
            scenario="quick_test",
            config_id="warehouse_2d-kafka-playback",
            duration_seconds=60,
            warmup_seconds=10,
        )
        self.assertTrue(passing["success"], passing.get("error"))
        self.assertTrue(passing["kpis"]["passed"], passing["kpis"])
        tight = {
            "scenarios": {
                "quick_test": {
                    "warehouse_2d-kafka-playback": {
                        "required_topics": ["raw", "frames"],
                        "frames": {"max_p50_s": 0.001},
                    }
                }
            }
        }
        failed = evaluate_kpis(measured, tight)
        self.assertFalse(failed["passed"])

    def test_kpis_fail_when_configured_percentile_is_missing(self) -> None:
        payload = {
            "scenario": "quick_test",
            "config_id": "warehouse_2d-kafka-playback",
            "raw": {"count": 2000, "avg_s": 0.0},
            "topics": {
                "frames": {
                    "present": True,
                    "total": {"count": 2000},
                    "corrected": {"avg_s": 0.01, "p50_s": None, "p99_s": 0.02},
                },
                "behavior": {
                    "present": True,
                    "total": {"count": 10},
                    "corrected": {"avg_s": 0.01, "p50_s": 0.02, "p99_s": 0.03},
                },
                "events": {
                    "present": True,
                    "total": {"count": 20},
                    "corrected": {"avg_s": 0.01, "p50_s": 0.02, "p99_s": 0.03},
                },
                "incidents": {
                    "present": True,
                    "total": {"count": 2000},
                    "corrected": {"avg_s": 0.01, "p50_s": 0.02, "p99_s": 0.03},
                },
            },
        }
        result = evaluate_kpis(payload, load_kpis())
        self.assertFalse(result["passed"], result)
        self.assertTrue(
            any("frames.corrected.p50_s missing" in item for item in result["violations"]),
            result["violations"],
        )

    def test_minio_publish_uses_config_and_credentials(self) -> None:
        config = (
            Path(__file__).resolve().parents[1] / "configs" / "config.yaml"
        )
        settings = minio_settings(config)
        self.assertEqual(settings["endpoint"], "10.111.52.248:9000")
        self.assertEqual(settings["service"], "Behavior-Analytics")

        def fake_publish(local_file, service, **kwargs):
            if not os.environ.get("MINIO_ACCESS_KEY") or not os.environ.get("MINIO_SECRET_KEY"):
                raise ValueError("MinIO credentials are required for upload")
            return {"object": f"{service}/{local_file.name}", "uri": "s3://perf-results-dev/result"}

        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "behavior-analytics_cfg_20261023_121500.json"
            result_path.write_text('{"success": true}\n')
            with patch("minio_upload.publish_result_file", side_effect=fake_publish) as put:
                with patch.dict(
                    os.environ,
                    {"MINIO_ACCESS_KEY": "", "MINIO_SECRET_KEY": ""},
                    clear=False,
                ):
                    with self.assertRaises(ValueError):
                        publish_result(
                            result_path,
                            config_path=config,
                            config_id="warehouse_2d-kafka-playback",
                        )
                with patch.dict(
                    os.environ,
                    {"MINIO_ACCESS_KEY": "ak", "MINIO_SECRET_KEY": "sk"},
                    clear=False,
                ):
                    published = publish_result(
                        result_path,
                        config_path=config,
                        config_id="warehouse_2d-kafka-playback",
                    )
            self.assertEqual(published["object"], f"Behavior-Analytics/{result_path.name}")
            self.assertEqual(put.call_args.args[0], result_path)
            self.assertEqual(put.call_count, 1)

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
                sys.executable,
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
                self.assertEqual(
                    module.result_dir(args), Path(directory) / "vss-perf-results"
                )
                named = module.parse_args(["--output-json", str(Path(directory) / "benchmark.json")])
                self.assertEqual(module.result_file(named), Path(directory) / "benchmark.json")
            finally:
                os.environ.pop("OUTPUT_DIR", None)

    def test_entrypoint_replaces_stale_result_and_uploads_failure(self) -> None:
        spec = importlib.util.spec_from_file_location("ba_core_entrypoint", CORE)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            output.write_text('{"success": true, "stale": true}\n')
            completed = subprocess.CompletedProcess([], 1)
            with patch.object(module.subprocess, "run", return_value=completed):
                with patch.object(
                    module, "_upload", return_value={"uri": "s3://failure"}
                ) as upload:
                    code = module.main(["--output-json", str(output), "--upload"])
            payload = json.loads(output.read_text())
            self.assertEqual(code, 1)
            self.assertEqual(payload["summary"]["overall_status"], "FAIL")
            self.assertEqual(payload["test_cases"][0]["status"], "FAIL")
            upload.assert_called_once_with(output, ANY)

    def test_entrypoint_uploads_explicit_stage_failure(self) -> None:
        spec = importlib.util.spec_from_file_location("ba_core_entrypoint", CORE)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            with patch.object(
                module, "_upload", return_value={"uri": "s3://failure"}
            ) as upload:
                code = module.main(
                    [
                        "--output-json",
                        str(output),
                        "--stage-failure",
                        "deploy",
                        "--failure-message",
                        "boom",
                        "--upload",
                    ]
                )
            self.assertEqual(code, 1)
            payload = json.loads(output.read_text())
            self.assertEqual(
                payload["test_cases"][0]["error"], {"stage": "deploy", "message": "boom"}
            )
            upload.assert_called_once_with(output, ANY)

    def test_entrypoint_passes_selected_config_to_runner(self) -> None:
        spec = importlib.util.spec_from_file_location("ba_core_entrypoint", CORE)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "custom.yaml"
            config.write_text("test_scenarios: {}\n")
            output = Path(directory) / "result.json"

            def fake_run(_cmd, *, env, check):
                self.assertEqual(env["PERF_CONFIG_FILE"], str(config.resolve()))
                self.assertEqual(env["PERF_NATIVE_ONLY"], "true")
                native = Path(env["OUTPUT_DIR"]) / "execution_summary.json"
                native.write_text(
                    json.dumps({"success": True, "setup": "warehouse_2d_kafka_playback"})
                )
                return subprocess.CompletedProcess([], 0)

            with patch.object(module.subprocess, "run", side_effect=fake_run):
                code = module.main(
                    ["--config", str(config), "--output-json", str(output)]
                )
            self.assertEqual(code, 0)
            result = json.loads(output.read_text())
            self.assertEqual(result["summary"]["overall_status"], "PASS")
            self.assertEqual(result["metadata"]["service"], "Behavior-Analytics")

    def test_safe_write_replaces_symlink_instead_of_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "outside.txt"
            target.write_text("secret\n")
            dest = Path(directory) / "result.json"
            dest.symlink_to(target)
            safe_write_text(dest, '{"ok": true}\n')
            self.assertFalse(dest.is_symlink())
            self.assertEqual(dest.read_text(), '{"ok": true}\n')
            self.assertEqual(target.read_text(), "secret\n")

    def test_scenario_config_loads_playback_settings(self) -> None:
        settings = scenario_settings(
            Path(__file__).resolve().parents[1] / "configs" / "config.yaml",
            "quick_test",
        )
        self.assertEqual(settings["duration_seconds"], 60)
        self.assertEqual(settings["warmup_seconds"], 10)
        self.assertEqual(settings["percentiles"], [50, 95, 99])
        self.assertIn("behavior", settings["topics"])

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
        checked_in_env = script.parent / "docker_compose" / "infra" / ".env"
        before = checked_in_env.read_text() if checked_in_env.is_file() else None
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "infra" / ".env"
            completed = subprocess.run(
                [
                    "bash",
                    "-c",
                    "set -euo pipefail; "
                    f'SCRIPT_DIR={str(script.parent)!r}; '
                    f'ENV_FILE={str(env_file)!r}; '
                    "PROFILE1=warehouse_2d; PROFILE2=kafka; "
                    f"source {str(script)!r}; "
                    'test -n "$INFRA_DIR"; test -f "$ENV_FILE"',
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(env_file.is_file())
            isolated = subprocess.run(
                [
                    "bash",
                    "-c",
                    "set -euo pipefail; "
                    f'SCRIPT_DIR={str(script.parent)!r}; '
                    f'ENV_FILE={str(env_file)!r}; '
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
        after = checked_in_env.read_text() if checked_in_env.is_file() else None
        self.assertEqual(before, after)

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
        self.assertIn("overlay_app_config.py", deploy)
        common = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "perf"
            / "scripts"
            / "common.sh"
        ).read_text()
        self.assertIn("export BA_PERF_DATA_DIR", common)
        self.assertIn("export BA_PERF_OVERLAY_STAMP", common)
        self.assertIn("BA_PERF_BEHAVIOR_EMIT_ONCE", deploy)
        self.assertIn('cp -a "$app_src" "$app_dst"', deploy)
        self.assertIn('export MDX_SAMPLE_APPS_DIR="$BA_PERF_DATA_DIR"', deploy)
        post_deploy = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "perf"
            / "scripts"
            / "post_deploy.sh"
        ).read_text()
        self.assertIn("behaviorEmitOnce", post_deploy)
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
        self.assertNotIn("CHECKOUT_DATA_LOG", teardown)
        self.assertNotIn("docker rm -f elasticsearch", teardown)
        self.assertNotIn("docker rm -f kafka", teardown)
        self.assertNotIn("docker rm -f mdx-analytics", teardown)
        common = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "perf"
            / "scripts"
            / "common.sh"
        ).read_text()
        self.assertIn("scenario_config.py", common)
        self.assertIn("PERF_CONFIG_FILE", common)
        self.assertIn("Refusing to delete symlink", common)
        service_run = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "perf"
            / "run_benchmark.sh"
        ).read_text()
        for stage in ("pre_flight.sh", "deploy.sh", "post_deploy.sh", "measure.sh"):
            self.assertIn(stage, service_run)
        self.assertIn('PERF_NATIVE_ONLY:-false', service_run)
        self.assertIn('benchmark/core/vss_perf_benchmark.py', service_run)
        self.assertIn('PERF_VENV_DIR', service_run)
        self.assertIn('benchmark/requirements.txt', service_run)
        self.assertIn('exec "$PERF_VENV_DIR/bin/python"', service_run)
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

    def test_overlay_turns_emit_once_off_without_editing_fixture(self) -> None:
        spec = importlib.util.spec_from_file_location("overlay_app_config", OVERLAY)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        fixture = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "tests"
            / "integration"
            / "docker_compose"
            / "apps"
            / "warehouse_2d"
            / "analytics-stream-kafka-config.json"
        )
        original = json.loads(fixture.read_text())
        self.assertEqual(
            next(item["value"] for item in original["app"] if item["name"] == "behaviorEmitOnce"),
            "true",
        )
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "analytics-stream-kafka-config.json"
            stamp = Path(tmp) / "perf_app_overlay.json"
            work.write_text(fixture.read_text())
            applied = module.overlay_config(work, {"behaviorEmitOnce": "false"}, stamp=stamp)
            patched = json.loads(work.read_text())
            self.assertEqual(applied["behaviorEmitOnce"], "false")
            self.assertEqual(
                next(item["value"] for item in patched["app"] if item["name"] == "behaviorEmitOnce"),
                "false",
            )
            self.assertEqual(json.loads(stamp.read_text())["app"]["behaviorEmitOnce"], "false")
            self.assertEqual(
                next(
                    item["value"]
                    for item in json.loads(fixture.read_text())["app"]
                    if item["name"] == "behaviorEmitOnce"
                ),
                "true",
            )
        with tempfile.TemporaryDirectory() as tmp:
            stamp = Path(tmp) / "perf_app_overlay.json"
            stamp.write_text(json.dumps({"app": {"behaviorEmitOnce": "false"}}))
            env = {
                "BA_PERF_DATA_DIR": tmp,
                "BA_PERF_OVERLAY_STAMP": str(stamp),
            }
            with patch.dict(os.environ, env, clear=False):
                result = build_result(
                    REPORT,
                    artifact_ref="nvcr.io/example/behavior-analytics:test",
                    scenario="quick_test",
                    config_id="warehouse_2d-kafka-playback",
                    duration_seconds=60,
                    warmup_seconds=10,
                    apply_kpis=False,
                )
            self.assertEqual(result["app_overlay"]["app"]["behaviorEmitOnce"], "false")
        with tempfile.TemporaryDirectory() as tmp:
            stamp = Path(tmp) / "perf_app_overlay.json"
            stamp.write_text(json.dumps({"app": {"behaviorEmitOnce": "false"}}))
            extra = os.environ.copy()
            extra.pop("BA_PERF_OVERLAY_STAMP", None)
            extra["BA_PERF_DATA_DIR"] = tmp
            with patch.dict(os.environ, extra, clear=True):
                result = build_result(
                    REPORT,
                    artifact_ref="nvcr.io/example/behavior-analytics:test",
                    scenario="quick_test",
                    config_id="warehouse_2d-kafka-playback",
                    duration_seconds=60,
                    warmup_seconds=10,
                    apply_kpis=False,
                )
            self.assertEqual(result["app_overlay"]["app"]["behaviorEmitOnce"], "false")

    def test_measure_uses_latency_monitor_placeholder(self) -> None:
        deploy = (
            REPO_ROOT
            / "services"
            / "analytics"
            / "behavior-analytics"
            / "perf"
            / "scripts"
            / "deploy.sh"
        ).read_text()
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
        self.assertIn('--user "$(id -u):$(id -g)"', measure)
        self.assertNotIn("chmod 0777", measure)
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
        self.assertIn("ensure_host_dir", pre_flight)
        self.assertNotIn("chmod 0777", pre_flight)
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
        deploy_infra = REPO_ROOT / "deploy" / "docker" / "services" / "infra" / "compose.yml"
        compose_infra = REPO_ROOT / "compose" / "docker" / "services" / "infra" / "compose.yml"
        infra_path = deploy_infra if deploy_infra.is_file() else compose_infra
        self.assertTrue(
            infra_path.is_file(),
            f"missing infra compose at {deploy_infra} or {compose_infra}",
        )
        infra = infra_path.read_text()
        self.assertIn("\n  broker-health-check:", infra)
        self.assertIn("dockerfile: Dockerfiles/${STREAM_TYPE}-health-check.Dockerfile", infra)

    def test_perf_config_compose_path_exists_for_jenkins(self) -> None:
        marker = "composePath:"
        service_root = REPO_ROOT / "services" / "analytics" / "behavior-analytics"
        rel = Path("perf/configs/perf-configs.yaml")
        config_path = service_root / rel
        compose_path = None
        for line in config_path.read_text().splitlines():
            if line.lstrip().startswith(marker):
                compose_path = line.split(":", 1)[1].strip()
                break
        self.assertTrue(compose_path, f"{rel} is missing composePath")
        self.assertTrue(
            (service_root / compose_path).is_file(),
            f"{rel} composePath {compose_path} is missing",
        )
        text = config_path.read_text()
        for key in (
            "checkoutPaths:",
            "lfsPaths:",
            "vlmGpus:",
            "llmGpus:",
            "vlmModel:",
            "llmModel:",
            "vision_input_tokens:",
        ):
            self.assertIn(key, text, f"{rel} is missing {key} for Jenkins")
        self.assertIn("- libs/vss-perf-reporting/", text)

    def test_service_owned_jenkins_package_and_shared_infra_exist(self) -> None:
        perf_root = REPO_ROOT / "services" / "analytics" / "behavior-analytics" / "perf"
        self.assertTrue((perf_root / "benchmark" / "core" / "vss_perf_benchmark.py").is_file())
        self.assertTrue((perf_root / "benchmark" / "requirements.txt").is_file())
        self.assertTrue((perf_root / "configs" / "perf-configs.yaml").is_file())
        self.assertTrue((perf_root / "configs" / "config.yaml").is_file())
        self.assertIn("minio:", (perf_root / "configs" / "config.yaml").read_text())
        for name in ("pre_flight.sh", "deploy.sh", "post_deploy.sh", "teardown.sh"):
            self.assertTrue((perf_root / "scripts" / name).is_file())
        self.assertTrue(
            (REPO_ROOT / "deploy" / "docker" / "services" / "infra" / "compose.yml").is_file()
        )


if __name__ == "__main__":
    unittest.main()
