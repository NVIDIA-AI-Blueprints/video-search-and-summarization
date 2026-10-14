# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests; unit fixtures are not GPU benchmark measurements."""

import argparse
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema.exceptions import ValidationError

CORE = Path(__file__).resolve().parents[1] / "benchmark/core/vss_perf_benchmark.py"
SPEC = importlib.util.spec_from_file_location("compose_benchmark", CORE)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class BenchmarkTests(unittest.TestCase):
    def result(self):
        args = argparse.Namespace(
            config_id="H100_Custom-ID",
            scenario="quick_test",
            triggered_by="artifact_promotion",
            release="3.3.0",
            rt_cv_gpus="0",
        )
        cases = [
            {
                "test_case_id": "real_case",
                "success": True,
                "metrics": {"throughput": {"fps": 30}},
            }
        ]
        with patch.object(
            benchmark,
            "discover_platform",
            return_value={"gpu": {"model": "H100", "count": 1}},
        ):
            return benchmark.build_result(
                args,
                {},
                {},
                cases,
                datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc),  # noqa: UP017 - exercise shared-runner Python 3.10 compatibility.
                15,
            )

    def test_fps_single_source_excludes_averages_and_headers(self):
        logs = "**PERF: FPS 0 (Avg)\t\n**PERF: 29.00000 (3.00000)\t30.00000 (4.00000)\n"
        self.assertEqual(benchmark.parse_fps(logs), [[29.0, 30.0]])

    def test_fps_multiuri_pairs_are_multiline_and_zero_is_preserved(self):
        logs = "Active sources : 2\n**PERF:\n25.00000 (24.00000)\tsource_id : 0 stream_name a\n0.00000 (24.00000)\tsource_id : 1 stream_name b\nActive sources : 1\nPERF(0): 30.00000 (23.00000)\n"
        self.assertEqual(benchmark.parse_fps(logs), [[25.0, 0.0], [30.0]])

    def test_empty_fps_does_not_manufacture_measurement(self):
        self.assertEqual(benchmark.parse_fps("**PERF: FPS 0 (Avg)\nserver healthy"), [])

    def test_empty_listing_is_successful_but_malformed_counts_fail(self):
        self.assertEqual(
            benchmark.checked_listing({"count": 0, "sensors": []}, "sensors"), 0
        )
        with self.assertRaises(benchmark.BenchmarkError):
            benchmark.checked_listing({"count": 5, "sensors": []}, "sensors")

    def test_result_contract_preserves_id_and_utc_filename(self):
        result = self.result()
        benchmark.validate_result(result)
        self.assertEqual(result["metadata"]["config"]["config_id"], "H100_Custom-ID")
        with tempfile.TemporaryDirectory() as directory:
            path = benchmark.write_result(result, Path(directory))
            self.assertEqual(
                path.name, "compose-base_H100_Custom-ID_20261014_123000.json"
            )
            self.assertEqual(json.loads(path.read_text()), result)
            with self.assertRaises(FileExistsError):
                benchmark.write_result(result, Path(directory))

    def test_result_counts_and_status_must_agree(self):
        result = self.result()
        result["test_cases"][0]["success"] = False
        with self.assertRaises(benchmark.BenchmarkError):
            benchmark.validate_result(result)

    def test_wrong_schema_cannot_be_called_v2_compliant(self):
        result = self.result()
        result["schema_version"] = "2.0"
        with self.assertRaises(ValidationError):
            benchmark.validate_result(result)

    def test_config_requires_unique_enabled_id_and_positive_duration(self):
        cfg = {
            "test_scenarios": {
                "quick_test": {"duration_seconds": 10, "interval_seconds": 1}
            }
        }
        matrix = {"perf_configs": [{"id": "exact_ID", "enabled": True}]}
        self.assertEqual(
            benchmark.check_config(cfg, matrix, "exact_ID", "quick_test")[0]["id"],
            "exact_ID",
        )
        for identifier in ("missing", "../outside"):
            with self.assertRaises(benchmark.BenchmarkError):
                benchmark.check_config(cfg, matrix, identifier, "quick_test")
        matrix["perf_configs"][0]["enabled"] = False
        with self.assertRaises(benchmark.BenchmarkError):
            benchmark.check_config(cfg, matrix, "exact_ID", "quick_test")
        matrix["perf_configs"][0]["enabled"] = True
        cfg["test_scenarios"]["quick_test"]["duration_seconds"] = float("nan")
        with self.assertRaises(benchmark.BenchmarkError):
            benchmark.check_config(cfg, matrix, "exact_ID", "quick_test")

    def test_cli_failure_does_not_retry_or_fall_back(self):
        with patch.object(benchmark.subprocess, "run") as invoke:
            invoke.return_value = argparse.Namespace(
                returncode=4, stdout="", stderr="service missing"
            )
            with self.assertRaisesRegex(benchmark.BenchmarkError, "exited 4"):
                benchmark.run_command(["vss", "vios", "list"])
            invoke.assert_called_once()

    def test_measurement_requires_real_fps_and_workload_inventory(self):
        scenario = {
            "duration_seconds": 0.000001,
            "interval_seconds": 1,
            "warmup_iterations": 0,
            "expected_streams": 1,
        }

        def commands(argv, **kwargs):
            if argv[0] == "nvidia-smi":
                return "GPU-real-id", 0.01
            if argv[:2] == ["docker", "logs"]:
                return "**PERF: 29.00000 (3.00000)\n", 0.01
            if argv[1:3] == ["configure", "check"]:
                self.assertFalse(kwargs.get("json_output", True))
                return (
                    "  version        3.3.0-rc0\nvios available; analytics available",
                    0.01,
                )
            key = "incidents" if argv[1:3] == ["analytics", "incidents"] else "sensors"
            rows = [] if key == "incidents" else [{"name": "test-stream"}]
            return {"count": len(rows), key: rows}, 0.01

        facts = {}
        with patch.object(benchmark, "run_command", side_effect=commands):
            cases = benchmark.measure(
                scenario, "real-container", deployment_facts=facts
            )
        self.assertEqual(facts["version"], "3.3.0-rc0")
        self.assertEqual(len(cases), 4)
        self.assertTrue(all(case["success"] for case in cases))
        self.assertEqual(cases[-1]["metrics"]["throughput"]["minimum_stream_fps"], 29)

    def test_typed_cli_failure_keeps_failure_and_does_not_retry(self):
        scenario = {
            "duration_seconds": 0.000001,
            "interval_seconds": 1,
            "warmup_iterations": 0,
            "expected_streams": 1,
        }
        seen = []

        def commands(argv, **kwargs):
            seen.append(argv)
            if argv[0] == "nvidia-smi":
                return "GPU-real-id", 0.01
            if argv[:2] == ["docker", "logs"]:
                return "**PERF: 29.00000 (3.00000)\n", 0.01
            if argv[1:3] == ["configure", "check"]:
                self.assertFalse(kwargs.get("json_output", True))
                return "vios available; analytics available", 0.01
            if argv[1] == "vios":
                raise benchmark.BenchmarkError(
                    "vss vios list exited 4: missing service"
                )
            key = "incidents" if argv[2] == "incidents" else "sensors"
            return {"count": 0, key: []}, 0.01

        with patch.object(benchmark, "run_command", side_effect=commands):
            cases = benchmark.measure(scenario, "real-container")
        self.assertFalse(cases[0]["success"])
        self.assertIn("exited 4", cases[0]["error"])
        self.assertEqual(sum(argv[1:2] == ["vios"] for argv in seen), 1)
        self.assertTrue(cases[2]["success"])  # empty incident query remains successful

    def test_teardown_failure_updates_same_result(self):
        result = self.result()
        with tempfile.TemporaryDirectory() as directory:
            path = benchmark.write_result(result, Path(directory))
            code = benchmark.main(
                [
                    "--config-id",
                    "H100_Custom-ID",
                    "--existing-result",
                    str(path),
                    "--stage-failure",
                    "teardown",
                    "--failure-message",
                    "compose down failed",
                ]
            )
            updated = json.loads(path.read_text())
            self.assertEqual(code, 1)
            self.assertEqual(updated["summary"]["overall_status"], "PARTIAL")
            self.assertEqual(updated["summary"]["failed"], 1)
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 1)

    def test_final_publication_records_teardown_once_before_upload(self):
        result = self.result()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            path = benchmark.write_result(result, output)
            config = output / "config.yaml"
            config.write_text("global: {}\n")
            for _ in range(2):
                with patch.object(benchmark, "upload_result") as upload:
                    code = benchmark.main(
                        [
                            "--upload-results-only",
                            "--config",
                            str(config),
                            "--config-id",
                            "H100_Custom-ID",
                            "--output-json",
                            directory,
                            "--stage-failure",
                            "teardown",
                            "--failure-message",
                            "cleanup failed",
                        ]
                    )
                    self.assertEqual(code, 0)
                    upload.assert_called_once()
                    stored = json.loads(path.read_text())
                    self.assertEqual(stored["summary"]["overall_status"], "PARTIAL")
                    self.assertEqual(stored["summary"]["total_tests"], 2)
            self.assertEqual(len(list(output.glob("*.json"))), 1)

    def test_final_publication_rejects_sample_and_missing_results(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            config = output / "config.yaml"
            config.write_text("global: {}\n")
            args = [
                "--upload-results-only",
                "--config",
                str(config),
                "--config-id",
                "H100_Custom-ID",
                "--output-json",
                directory,
            ]
            with patch.object(benchmark, "upload_result") as upload:
                self.assertEqual(benchmark.main(args), 1)
                result = self.result()
                result["metadata"]["config"]["sample_only"] = True
                benchmark.write_result(result, output)
                self.assertEqual(benchmark.main(args), 1)
                upload.assert_not_called()

    def test_existing_failure_reuploads_updated_result(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            config = output / "config.yaml"
            config.write_text("global: {}\n")
            path = benchmark.write_result(self.result(), output)
            with patch.object(benchmark, "upload_result") as upload:
                code = benchmark.main(
                    [
                        "--config",
                        str(config),
                        "--config-id",
                        "H100_Custom-ID",
                        "--existing-result",
                        str(path),
                        "--stage-failure",
                        "teardown",
                        "--upload",
                    ]
                )
                self.assertEqual(code, 1)
                upload.assert_called_once_with(path, {})
                self.assertEqual(json.loads(path.read_text())["summary"]["failed"], 1)

    def test_deployment_version_is_reported_fact_not_requested_release(self):
        fact = benchmark.deployment_version_fact(
            "  vst            ok  routed\n  version        3.3.0-rc0\ncommands:\n"
        )
        self.assertEqual(fact["version"], "3.3.0-rc0")
        self.assertEqual(fact["status"], "reported")
        self.assertEqual(fact["source"], "vss configure check")
        legacy = benchmark.deployment_version_fact(
            "  vst            ok  routed\ncommands:\n"
        )
        self.assertIsNone(legacy["version"])
        self.assertEqual(legacy["status"], "unavailable")
        missing = benchmark.deployment_version_fact(
            "  version        not reported  HTTP 404 — no agent\n"
        )
        self.assertIsNone(missing["version"])
        self.assertEqual(missing["status"], "not_reported")
        self.assertIn("HTTP 404", missing["detail"])
        self.assertEqual(self.result()["metadata"]["run_info"]["release"], "3.3.0")

    def test_runtime_provenance_requires_actual_fixture_and_images(self):
        images = [
            {
                "container_id": f"container{index}",
                "container_name": f"name{index}",
                "image_ref": "registry/example:version",
                "image_id": "sha256:" + "a" * 64,
                "repo_digests": ["registry/example@sha256:" + "b" * 64],
            }
            for index in range(3)
        ]
        metadata = {
            "fixture": {
                "kind": "video_file",
                "sha256": "c" * 64,
                "width": 1280,
                "height": 720,
                "fps": 29.97,
                "duration_seconds": 100,
                "codec": "h264",
                "secret": "excluded",
            },
            "deployed_images": images,
            "environment": {"secret": "excluded"},
            "compatibility_helpers": [
                {
                    "name": "rt-cv-model-downloader",
                    "sha256": "d" * 64,
                    "command": "excluded",
                }
            ],
            "model_assets": [
                {
                    "file_name": "rtdetr.fp16.onnx",
                    "kind": "onnx",
                    "sha256": "e" * 64,
                    "path": "excluded",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            settings = {"runtime_metadata_file": str(path)}
            with self.assertRaisesRegex(benchmark.BenchmarkError, "unavailable"):
                benchmark.load_runtime_metadata("fixture", settings)
            path.write_text(json.dumps(metadata))
            measured = benchmark.load_runtime_metadata("fixture", settings)
            self.assertEqual(measured["fixture"]["sha256"], "c" * 64)
            self.assertEqual(measured["fixture"]["width"], 1280)
            self.assertEqual(
                measured["deployed_images"][0]["image_id"], "sha256:" + "a" * 64
            )
            self.assertNotIn("secret", measured["fixture"])
            self.assertNotIn("environment", measured)
            self.assertEqual(
                measured["compatibility_helpers"],
                [{"name": "rt-cv-model-downloader", "sha256": "d" * 64}],
            )
            self.assertEqual(
                measured["model_assets"],
                [{"file_name": "rtdetr.fp16.onnx", "kind": "onnx", "sha256": "e" * 64}],
            )
            metadata["compatibility_helpers"][0]["name"] = "../../downloader"
            path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(
                benchmark.BenchmarkError, "without paths or commands"
            ):
                benchmark.load_runtime_metadata("fixture", settings)
            metadata["compatibility_helpers"][0]["name"] = "rt-cv-model-downloader"
            metadata["model_assets"][0]["sha256"] = "made-up"
            path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(benchmark.BenchmarkError, "actual SHA256"):
                benchmark.load_runtime_metadata("fixture", settings)
            metadata["model_assets"][0]["sha256"] = "e" * 64
            metadata["fixture"].pop("sha256")
            path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(benchmark.BenchmarkError, "fixture SHA256"):
                benchmark.load_runtime_metadata("fixture", settings)
            metadata["fixture"]["sha256"] = "c" * 64
            metadata["deployed_images"][0]["image_id"] = "invented"
            path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(
                benchmark.BenchmarkError, "actual Docker image IDs"
            ):
                benchmark.load_runtime_metadata("fixture", settings)

    def test_committed_sample_matches_contract(self):
        sample = json.loads(
            (Path(__file__).parent / "expected_result.json").read_text()
        )
        benchmark.validate_result(sample)


if __name__ == "__main__":
    unittest.main()
