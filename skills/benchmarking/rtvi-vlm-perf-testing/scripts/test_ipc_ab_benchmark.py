######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
######################################################################################################

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import ipc_ab_benchmark


def valid_plan(root: Path):
    return {
        "schema_version": 1,
        "benchmark_id": "ipc-ab-test",
        "changed_variable": "decode_path",
        "common": {
            "code_commit": "abc1234",
            "cv_image_digest": "sha256:" + "1" * 64,
            "vlm_image_digest": "sha256:" + "2" * 64,
            "model_revision": "model-a",
            "hardware": "H100",
            "gpu_uuid": "GPU-123",
            "media_sha256": "3" * 64,
            "prompt_sha256": "4" * 64,
            "cache_policy": "mm-shm-1gb",
            "chunk_duration_seconds": 2,
            "request_duration_seconds": 40,
            "request_count": 4,
            "runtime_policy": "fresh_per_arm_repetition",
        },
        "execution": {
            "output_root": str(root / "output"),
            "repetitions": 2,
            "arm_order": ["direct", "ipc"],
            "alternate_order": True,
        },
        "arms": {
            "direct": {
                "decode_path": "direct_rtsp",
                "command": ["runner", "--arm", "{arm}", "--output", "{output_dir}"],
            },
            "ipc": {
                "decode_path": "rt_cv_nvunixfd",
                "docker_ipc_mode": "private",
                "socket_transport": "bind_mount",
                "command": ["runner", "--arm", "{arm}", "--output", "{output_dir}"],
            },
        },
    }


def valid_result(plan, arm, scale=1.0):
    topology = {
        "decode_path": ipc_ab_benchmark.DECODE_PATHS[arm],
        "cv_rtsp_connections": 1 if arm == "ipc" else 0,
        "vlm_rtsp_connections": 0 if arm == "ipc" else 4,
        "ipc_socket_count": 1 if arm == "ipc" else 0,
        "ipc_cli_flags_present": arm == "ipc",
        "ipc_source_log_marker_count": 1 if arm == "ipc" else 0,
    }
    return {
        "schema_version": 1,
        "arm": arm,
        "runtime_fresh": True,
        "identity": dict(plan["common"]),
        "topology": topology,
        "outcomes": {
            "offered_requests": 4,
            "successful_requests": 4,
            "failed_requests": 0,
            "nonempty_chunks": 20,
            "empty_chunks": 0,
        },
        "throughput_chunks_per_second": 2.0 * scale,
        "latency_samples_ms": {
            "chunk_latency_ms": [100.0 * scale, 200.0 * scale],
            "decode_latency_ms": [40.0 * scale, 60.0 * scale],
            "vlm_latency_ms": [50.0 * scale, 100.0 * scale],
        },
        "resource_samples": {
            "gpu_util_pct": [50.0 * scale, 70.0 * scale],
            "gpu_memory_mib": [1000.0, 1100.0],
            "decoder_util_pct": [10.0, 20.0],
            "cpu_pct": [80.0, 100.0],
            "container_memory_mib": [500.0, 600.0],
            "power_w": [200.0, 300.0],
        },
        "cleanup": {
            "containers_remaining": 0,
            "sockets_remaining": 0,
            "host_shm_objects_remaining": 0,
        },
        "fatal_markers": [],
    }


class IpcAbBenchmarkTests(unittest.TestCase):
    def test_validates_and_alternates_arm_order(self):
        with tempfile.TemporaryDirectory() as td:
            plan = valid_plan(Path(td))
            ipc_ab_benchmark.validate_plan(plan)
            runs = ipc_ab_benchmark.render_runs(plan)
            self.assertEqual([run["arm"] for run in runs], ["direct", "ipc", "ipc", "direct"])
            self.assertIn("direct", runs[0]["argv"])
            self.assertIn("rep-01", str(runs[0]["output_dir"]))

    def test_rejects_unsafe_ipc_or_identity_drift(self):
        with tempfile.TemporaryDirectory() as td:
            plan = valid_plan(Path(td))
            plan["arms"]["ipc"]["docker_ipc_mode"] = "host"
            with self.assertRaisesRegex(ValueError, "must be private"):
                ipc_ab_benchmark.validate_plan(plan)

            plan = valid_plan(Path(td))
            result = valid_result(plan, "ipc")
            result["identity"]["model_revision"] = "different"
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                ipc_ab_benchmark._check_result(plan, result, "ipc")

            plan = valid_plan(Path(td))
            plan["arms"]["ipc"]["env"] = {"NGC_API_KEY": "do-not-store-this"}
            with self.assertRaisesRegex(ValueError, "secret-like"):
                ipc_ab_benchmark.validate_plan(plan)

    def test_rejects_ipc_rtsp_fallback_and_cleanup_residue(self):
        with tempfile.TemporaryDirectory() as td:
            plan = valid_plan(Path(td))
            result = valid_result(plan, "ipc")
            result["topology"]["vlm_rtsp_connections"] = 1
            with self.assertRaisesRegex(ValueError, "zero VLM RTSP"):
                ipc_ab_benchmark._check_result(plan, result, "ipc")

            result = valid_result(plan, "ipc")
            result["cleanup"]["host_shm_objects_remaining"] = 1
            with self.assertRaisesRegex(ValueError, "must be zero"):
                ipc_ab_benchmark._check_result(plan, result, "ipc")

            result = valid_result(plan, "ipc")
            result["topology"]["ipc_cli_flags_present"] = False
            with self.assertRaisesRegex(ValueError, "CLI flags"):
                ipc_ab_benchmark._check_result(plan, result, "ipc")

    def test_aggregates_and_writes_all_report_formats(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = valid_plan(root)
            paths = []
            for arm, scale in (("direct", 1.0), ("ipc", 0.5)):
                for repetition in (1, 2):
                    path = root / f"{arm}-{repetition}.json"
                    path.write_text(json.dumps(valid_result(plan, arm, scale)), encoding="utf-8")
                    paths.append(path)
            summary = ipc_ab_benchmark.aggregate(plan, paths)
            throughput = next(
                row for row in summary["comparison"] if row["metric"] == "throughput_chunks_per_second"
            )
            self.assertEqual(throughput["direct"], 2.0)
            self.assertEqual(throughput["ipc"], 1.0)
            self.assertEqual(throughput["ipc_vs_direct_pct"], -50.0)

            report = root / "report"
            ipc_ab_benchmark.write_report(summary, report)
            for name in (
                "ipc-ab-summary.json",
                "ipc-ab-comparison.tsv",
                "ipc-ab-comparison.md",
                "ipc-ab-comparison.xlsx",
            ):
                self.assertGreater((report / name).stat().st_size, 0)
            with zipfile.ZipFile(report / "ipc-ab-comparison.xlsx") as workbook:
                self.assertIn("xl/worksheets/sheet1.xml", workbook.namelist())
                self.assertIn("Throughput (chunks/s)", workbook.read("xl/worksheets/sheet1.xml").decode())

            # Missing telemetry stays blank in XLSX instead of being reported as measured zero.
            for path in paths:
                result = json.loads(path.read_text(encoding="utf-8"))
                result["resource_samples"]["power_w"] = []
                path.write_text(json.dumps(result), encoding="utf-8")
            summary = ipc_ab_benchmark.aggregate(plan, paths)
            ipc_ab_benchmark.write_report(summary, report)
            with zipfile.ZipFile(report / "ipc-ab-comparison.xlsx") as workbook:
                sheet = workbook.read("xl/worksheets/sheet1.xml").decode()
                self.assertNotIn(">None<", sheet)


if __name__ == "__main__":
    unittest.main()
