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
Unit tests for vss_perf_lvs_adapter: execution_results_to_test_cases.
"""

import json

import pytest


@pytest.fixture
def adapter_module():
    import vss_perf_lvs_adapter as m

    return m


@pytest.fixture
def dummy_execution_results_file_burst():
    return {
        "scenario_dir": "/tmp/scenario",
        "benchmark_mode": "file_burst",
        "test_cases": [
            {
                "success": True,
                "test_case_id": "file_burst_video1_30sec",
                "video_url": "/path/to/video1.mp4",
                "chunk_size": 30,
                "concurrency_levels_tested": [1, 2, 4],
                "target_latency_seconds": 60.0,
                "concurrency_results": [
                    {
                        "concurrency_level": 1,
                        "e2e_latency_seconds": 100.0,
                        "completed_files": 10,
                        "failed_files": 0,
                        "throughput_files_per_second": 0.1,
                        "avg_latency": 10.0,
                        "p90_latency": 12.0,
                        "vlm_gpu_usage_mean": 70.0,
                        "vlm_gpu_memory_mean": 60.0,
                        "llm_gpu_usage_mean": 5.0,
                        "llm_gpu_memory_mean": 4.0,
                    },
                    {
                        "concurrency_level": 4,
                        "e2e_latency_seconds": 80.0,
                        "completed_files": 10,
                        "failed_files": 0,
                        "throughput_files_per_second": 0.125,
                        "avg_latency": 8.0,
                        "p90_latency": 9.5,
                        "vlm_gpu_usage_mean": 88.0,
                        "vlm_gpu_memory_mean": 72.0,
                        "llm_gpu_usage_mean": 10.0,
                        "llm_gpu_memory_mean": 8.0,
                    },
                ],
                "optimal_target_concurrency": {
                    "concurrency_level": 4,
                    "throughput_files_per_second": 0.125,
                    "avg_latency_seconds": 8.0,
                    "p90_latency_seconds": 9.5,
                },
            },
        ],
    }


@pytest.fixture
def dummy_execution_results_single_file(tmp_path):
    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()
    tc_id = "single_file_video1_30sec"
    iter_dir = scenario_dir / tc_id / "iteration_1"
    iter_dir.mkdir(parents=True)
    (iter_dir / "summarize_response.json").write_text(
        json.dumps({"usage": {"total_chunks_processed": 100}})
    )
    return {
        "scenario_dir": str(scenario_dir),
        "benchmark_mode": "single_file",
        "test_cases": [
            {
                "success": True,
                "test_case_id": tc_id,
                "video_url": "/path/to/video1.mp4",
                "chunk_size": 30,
                "iteration_results": [
                    {
                        "success": True,
                        "iteration": 1,
                        "wall_clock_seconds": 40.0,
                        "api_metrics": {
                            "vlm_pipeline_latency_seconds_latest": 35.0,
                            "vlm_latency_seconds_latest": 30.0,
                            "decode_latency_seconds_latest": 2.0,
                            "ca_rag_latency_seconds_latest": 3.0,
                            "vlm_input_tokens_per_chunk_sum": 40000,
                            "vlm_output_tokens_per_chunk_sum": 10000,
                            "vlm_input_tokens_per_chunk_count": 100,
                        },
                    },
                ],
            },
        ],
    }


class TestAdapterFileBurst:
    def test_file_burst_produces_concurrency_structure(
        self, adapter_module, dummy_execution_results_file_burst
    ):
        test_cases = adapter_module.execution_results_to_test_cases(
            None, dummy_execution_results_file_burst, validate=True
        )
        assert len(test_cases) == 1
        tc = test_cases[0]
        assert tc["test_case_id"] == "file_burst_video1_30sec"
        assert "concurrency_results" in tc["metrics"]
        assert len(tc["metrics"]["concurrency_results"]) == 2
        assert tc["metrics"]["optimal_target_concurrency"]["concurrency_level"] == 4

    def test_file_burst_binary_search_optimal_shape(self, adapter_module):
        execution_results = {
            "scenario_dir": "/tmp/scenario",
            "benchmark_mode": "file_burst",
            "test_cases": [
                {
                    "success": True,
                    "test_case_id": "file_burst_video1_30sec",
                    "video_url": "/path/to/video1.mp4",
                    "chunk_size": 30,
                    "concurrency_levels_tested": [40, 46],
                    "target_latency_seconds": 60.0,
                    "concurrency_results": [
                        {
                            "concurrency_level": 46,
                            "e2e_latency_seconds": 80.0,
                            "completed_files": 46,
                            "failed_files": 0,
                            "throughput_files_per_second": 0.575,
                            "avg_latency": 58.0,
                            "p90_latency": 59.5,
                        }
                    ],
                    "optimal_target_concurrency": {
                        "target_latency_seconds": 60.0,
                        "estimated_concurrency": 46,
                        "actual_result": {
                            "concurrency_level": 46,
                            "throughput_files_per_second": 0.575,
                            "avg_latency": 58.0,
                            "p90_latency": 59.5,
                        },
                    },
                }
            ],
        }
        test_cases = adapter_module.execution_results_to_test_cases(
            None, execution_results, validate=True
        )
        optimal = test_cases[0]["metrics"]["optimal_target_concurrency"]
        assert optimal["concurrency_level"] == 46
        assert optimal["avg_latency_seconds"] == 58.0
        assert optimal["p90_latency_seconds"] == 59.5
        assert optimal["throughput_files_per_second"] == 0.575


class TestAdapterSingleFile:
    def test_single_file_produces_metrics_shape(
        self, adapter_module, dummy_execution_results_single_file
    ):
        class MockBenchmark:
            def process_gpu_stats(self, path):
                return {
                    "vlm_gpu_usage_mean": 75.0,
                    "vlm_gpu_memory_mean": 62.0,
                    "llm_gpu_usage_mean": 8.0,
                    "llm_gpu_memory_mean": 6.0,
                }

        test_cases = adapter_module.execution_results_to_test_cases(
            MockBenchmark(), dummy_execution_results_single_file, validate=True
        )
        assert len(test_cases) == 1
        tc = test_cases[0]
        assert tc["status"] == "PASS"
        assert tc["config"]["video_duration"] == "video1"
        m = tc["metrics"]
        assert set(m.keys()) == {"latency", "gpu"}
        assert m["latency"]["e2e_seconds"] == 40.0
        assert m["gpu"]["vlm_gpu_usage_mean"] == 75.0

    def test_single_file_feed_to_build_result(
        self, adapter_module, dummy_execution_results_single_file
    ):
        import vss_perf_common as common

        class MockBenchmark:
            def process_gpu_stats(self, path):
                return {
                    "vlm_gpu_usage_mean": 70,
                    "vlm_gpu_memory_mean": 60,
                    "llm_gpu_usage_mean": 0,
                    "llm_gpu_memory_mean": 0,
                }

        test_cases = adapter_module.execution_results_to_test_cases(
            MockBenchmark(), dummy_execution_results_single_file, validate=True
        )
        result = common.build_result(
            "LVS",
            "lvs_tp1_20250203_120000",
            test_cases,
            timestamp="2025-02-03T12:00:00Z",
            benchmark_mode="single_file",
        )
        assert result["schema_version"] == "0.1"
        assert result["test_cases"][0]["metrics"]["latency"]["e2e_seconds"] == 40.0
