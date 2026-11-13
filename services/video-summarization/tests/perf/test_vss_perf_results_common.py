# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Unit tests for vss_perf_common and vss_perf_lvs_adapter.
Goal: assert that generated JSON (from build_result + adapter) matches the expected mock JSON.
"""

import json
import re
from pathlib import Path

import pytest


def _norm_equal(a, b):
    """Recursively compare; treat int/float as equal when values match."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return set(a) == set(b) and all(_norm_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_norm_equal(x, y) for x, y in zip(a, b))
    return a == b


@pytest.fixture
def common_module():
    import vss_perf_common as m

    return m


@pytest.fixture
def dummy_single_file_test_cases():
    """Minimal test_cases list as produced by vss_perf_lvs_adapter (single_file shape)."""
    return [
        {
            "test_case_id": "single_file_video1_30sec",
            "description": "video1 video summarization",
            "status": "PASS",
            "config": {"video_duration": "video1"},
            "metrics": {
                "latency": {
                    "e2e_seconds": 45.2,
                    "vlm_pipeline_seconds": 38.1,
                    "ca_rag_seconds": 4.0,
                    "decode_seconds": 2.1,
                },
                "gpu": {
                    "vlm_gpu_usage_mean": 78.5,
                    "vlm_gpu_memory_mean": 65.0,
                    "llm_gpu_usage_mean": 12.0,
                    "llm_gpu_memory_mean": 8.0,
                },
            },
        },
    ]


@pytest.fixture
def dummy_file_burst_test_cases():
    """Minimal test_cases list as produced by vss_perf_lvs_adapter (file_burst shape)."""
    return [
        {
            "test_case_id": "file_burst_video1_30sec",
            "description": "video1.mp4 @ 30s",
            "config": {
                "video_url": "/path/to/video1.mp4",
                "chunk_size_seconds": 30,
                "concurrency_levels_tested": [1, 2, 4, 8],
                "target_latency_seconds": 60.0,
            },
            "metrics": {
                "concurrency_results": [
                    {
                        "concurrency_level": 1,
                        "e2e_seconds": 120.0,
                        "completed_files": 10,
                        "failed_files": 0,
                        "throughput_files_per_second": 0.083,
                        "avg_latency": 12.0,
                        "p90_latency": 14.0,
                        "gpu": {
                            "vlm_gpu_usage_mean": 65.0,
                            "vlm_gpu_memory_mean": 60.0,
                            "llm_gpu_usage_mean": 10.0,
                            "llm_gpu_memory_mean": 8.0,
                        },
                    },
                    {
                        "concurrency_level": 4,
                        "e2e_seconds": 95.0,
                        "completed_files": 10,
                        "failed_files": 0,
                        "throughput_files_per_second": 0.105,
                        "avg_latency": 9.5,
                        "p90_latency": 11.0,
                        "gpu": {
                            "vlm_gpu_usage_mean": 85.0,
                            "vlm_gpu_memory_mean": 70.0,
                            "llm_gpu_usage_mean": 12.0,
                            "llm_gpu_memory_mean": 9.0,
                        },
                    },
                ],
                "optimal_target_concurrency": {
                    "concurrency_level": 4,
                    "throughput_files_per_second": 0.105,
                    "avg_latency_seconds": 9.5,
                    "p90_latency_seconds": 11.0,
                },
            },
        },
    ]


class TestGenerateRunId:
    def test_format(self, common_module):
        run_id = common_module.generate_run_id("LVS", "tp1-9k")
        assert run_id.startswith("lvs_tp1-9k_")
        assert re.match(r"lvs_tp1-9k_\d{8}_\d{6}$", run_id)

    def test_suffix(self, common_module):
        run_id = common_module.generate_run_id("LVS", "tp1", suffix="abc")
        assert run_id.endswith("_abc")


class TestBuildResult:
    def test_build_result_single_file_shape(self, common_module, dummy_single_file_test_cases):
        result = common_module.build_result(
            service="LVS",
            run_id="lvs_tp1_20250203_120000",
            test_cases=dummy_single_file_test_cases,
            timestamp="2025-02-03T12:00:00Z",
            platform={"gpu": {"model": "H100", "count": 4}},
            config={"config_id": "tp1-9k"},
            benchmark_name="single_file_test",
            benchmark_mode="single_file",
            triggered_by="manual",
            duration_seconds=60.5,
            release="EA2",
        )
        assert result["schema_version"] == "0.1"
        assert result["metadata"]["service"] == "LVS"
        assert result["summary"]["overall_status"] == "PASS"
        assert len(result["test_cases"]) == 1
        assert result["test_cases"][0]["metrics"]["latency"]["e2e_seconds"] == 45.2

    def test_build_result_file_burst_shape(self, common_module, dummy_file_burst_test_cases):
        result = common_module.build_result(
            service="LVS",
            run_id="lvs_burst_20250203_120000",
            test_cases=dummy_file_burst_test_cases,
            timestamp="2025-02-03T12:00:00Z",
            benchmark_mode="file_burst",
        )
        assert result["schema_version"] == "0.1"
        assert result["metadata"]["benchmark_mode"] == "file_burst"
        tc = result["test_cases"][0]
        assert "concurrency_results" in tc["metrics"]
        assert len(tc["metrics"]["concurrency_results"]) == 2

    def test_build_result_summary_partial(self, common_module, dummy_single_file_test_cases):
        result = common_module.build_result(
            service="LVS",
            run_id="lvs_20250203_120000",
            test_cases=dummy_single_file_test_cases,
            passed=1,
            failed=1,
        )
        assert result["summary"]["total_tests"] == 2
        assert result["summary"]["overall_status"] == "PARTIAL"


class TestBuildAndSave:
    def test_build_and_save_creates_valid_json(
        self, common_module, dummy_single_file_test_cases, tmp_path
    ):
        out = tmp_path / "out"
        path = common_module.build_and_save(
            out,
            "LVS",
            dummy_single_file_test_cases,
            config_id="tp1",
            benchmark_name="scenario1",
            benchmark_mode="single_file",
        )
        assert path.exists()
        assert path.suffix == ".json"
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["schema_version"] == "0.1"
        assert len(loaded["test_cases"]) == 1

    def test_dummy_json_snapshot_single_file(
        self, common_module, dummy_single_file_test_cases, tmp_path
    ):
        path = common_module.build_and_save(
            tmp_path / "dummy_single_file.json",
            "LVS",
            dummy_single_file_test_cases,
            config_id="tp1-9k",
            platform={"gpu": {"model": "H100", "count": 4}},
            config={"config_id": "tp1-9k"},
            benchmark_name="single_file_test",
            benchmark_mode="single_file",
        )
        with open(path) as f:
            doc = json.load(f)
        assert set(doc["test_cases"][0]["metrics"].keys()) == {"latency", "gpu"}

    def test_generated_json_matches_expected_mock(
        self, common_module, dummy_single_file_test_cases, dummy_file_burst_test_cases
    ):
        """Generated result from build_result must match expected_result.json (mock)."""
        expected_path = Path(__file__).resolve().parent / "expected_result.json"
        with open(expected_path) as f:
            expected = json.load(f)
        combined = dummy_single_file_test_cases + dummy_file_burst_test_cases
        # Use full platform.gpu shape so generated matches expected_result.json
        platform = {
            "gpu": {
                "model": "H100",
                "full_name": "",
                "count": 4,
                "memory_gb": 0.0,
                "driver_version": "",
                "cuda_version": "",
            },
            "cpu": {"model": "Intel Xeon", "cores": 64},
            "memory_gb": 512.0,
            "os": "Ubuntu 22.04",
        }
        generated = common_module.build_result(
            "LVS",
            "lvs_tp1-9k_20250203_120000",
            combined,
            timestamp="2025-02-03T12:00:00Z",
            platform=platform,
            config={"config_id": "tp1-9k"},
            benchmark_name="sample_run",
            benchmark_mode="single_file",
            duration_seconds=120.0,
            release="EA2",
        )
        assert _norm_equal(generated, expected), "Generated JSON should match expected_result.json"
