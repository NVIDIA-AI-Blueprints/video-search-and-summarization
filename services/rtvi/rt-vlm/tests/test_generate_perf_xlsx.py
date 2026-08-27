######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import json
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parents[1] / "perf" / "benchmark"
sys.path.insert(0, str(BENCHMARK_DIR))

pd_stub = type(sys)("pandas")
pd_stub.__spec__ = ModuleSpec("pandas", loader=None)
pd_stub.DataFrame = object
sys.modules.setdefault("pandas", pd_stub)

from generate_perf_xlsx import (  # noqa: E402
    extract_concurrency_data,
    extract_e2e_data,
    extract_max_streams_data,
)


def test_extract_e2e_data_reads_file_burst_iteration_results(tmp_path):
    scenario_dir = tmp_path / "file_burst_100_token_2k"
    iter_dir = scenario_dir / "file_burst_traffic_10sec" / "iteration_1"
    iter_dir.mkdir(parents=True)
    (iter_dir / "file_burst_results.json").write_text(
        json.dumps(
            {
                "chunk_size": 10,
                "success": False,
                "concurrency_results": [
                    {
                        "concurrency_level": 64,
                        "completed_files": 63,
                        "failed_files": 1,
                        "latency_history": [10.0, 12.3, 14.0, 18.0],
                        "avg_latency": 12.3,
                        "p50_latency": 12.3,
                        "p75_latency": 14.0,
                        "p90_latency": 14.9,
                        "p95_latency": 15.7,
                        "p99_latency": 17.54,
                        "chunk_decode_latency_seconds_avg": 0.6,
                        "chunk_decode_latency_seconds_p50": 0.5,
                        "chunk_decode_latency_seconds_p75": 0.7,
                        "chunk_vlm_latency_seconds_p99": 8.8,
                        "throughput_files_per_second": 4.2,
                        "vlm_gpu_usage_mean": 88.0,
                        "vlm_gpu_memory_mean": 77.0,
                        "vlm_nvdec_usage_mean": 9.0,
                    }
                ],
            }
        )
    )

    defaults = {
        "release": "3.2",
        "model": "CR2-8B",
        "precision": "FP8",
        "engine": "vLLM",
        "isl_text": 100,
    }

    rows = extract_e2e_data(tmp_path, "H100", defaults)

    assert len(rows) == 1
    row = rows[0]
    assert row["Benchmark Mode"] == "file_burst"
    assert row["Scenario"] == "file_burst_100_token_2k"
    assert row["OSL"] == 100
    assert row["Concurrency"] == 64
    assert row["E2E Latency Min (s)"] == 10.0
    assert row["E2E Latency Avg (s)"] == 12.3
    assert row["E2E Latency Max (s)"] == 18.0
    assert row["E2E Latency p50 (s)"] == 12.3
    assert row["E2E Latency p75 (s)"] == 14.0
    assert row["E2E Latency p90 (s)"] == 14.9
    assert row["E2E Latency p95 (s)"] == 15.7
    assert row["E2E Latency p99 (s)"] == 17.54
    assert row["Decode Stage Latency Avg (s)"] == 0.6
    assert row["Decode Stage Latency p50 (s)"] == 0.5
    assert row["Decode Stage Latency p75 (s)"] == 0.7
    assert row["VLM Stage Latency p99 (s)"] == 8.8
    assert row["Throughput (files/sec)"] == 4.2
    assert row["Failed Requests"] == 1
    assert row["Error Rate (%)"] == 1.56


def test_extract_concurrency_data_includes_bcd_latency_and_error_fields(tmp_path):
    tc_dir = tmp_path / "concurrency_test_1_token_2k" / "10streams" / "iteration_1"
    tc_dir.mkdir(parents=True)
    summary_dir = tc_dir.parent
    (summary_dir / "test_case_summary.json").write_text(
        json.dumps(
            {
                "stream_count": 10,
                "chunk_size": 10,
                "iteration_results": [
                    {
                        "success": True,
                        "avg_latency": 2.0,
                        "min_latency": 1.1,
                        "max_latency": 3.4,
                        "p50_latency": 1.9,
                        "p75_latency": 2.4,
                        "p90_latency": 2.8,
                        "p95_latency": 3.1,
                        "p99_latency": 3.3,
                        "chunk_queue_latency_seconds_avg": 0.02,
                        "chunk_server_processing_latency_seconds_p75": 1.2,
                        "decode_latency_seconds_avg": 0.4,
                        "streams_with_errors": 1,
                    }
                ],
            }
        )
    )

    defaults = {
        "release": "3.2",
        "model": "CR2-8B",
        "precision": "FP8",
        "engine": "vLLM",
        "isl_text": 100,
    }

    rows = extract_concurrency_data(tmp_path, "H100", defaults)

    assert len(rows) == 1
    row = rows[0]
    assert row["Chunk E2E Latency Min (s)"] == 1.1
    assert row["Chunk E2E Latency Avg (s)"] == 2.0
    assert row["Chunk E2E Latency Max (s)"] == 3.4
    assert row["Chunk E2E Latency p50 (s)"] == 1.9
    assert row["Chunk E2E Latency p75 (s)"] == 2.4
    assert row["Chunk E2E Latency p90 (s)"] == 2.8
    assert row["Chunk E2E Latency p95 (s)"] == 3.1
    assert row["Chunk E2E Latency p99 (s)"] == 3.3
    assert row["Queue Stage Latency Avg (s)"] == 0.02
    assert row["Server Processing Stage Latency p75 (s)"] == 1.2
    assert row["Decode latency Avg (s)"] == 0.4
    assert row["Streams With Errors"] == 1
    assert row["Error Rate (%)"] == 10.0


def test_extract_max_streams_data_includes_p99_and_drop_fields(tmp_path):
    tc_dir = tmp_path / "max_live_streams_test_1_token_2k" / "warehouse"
    tc_dir.mkdir(parents=True)
    (tc_dir / "max_live_streams_results.json").write_text(
        json.dumps(
            {
                "success": True,
                "chunk_size": 10,
                "max_sustainable_streams": 144,
                "min_latency": 1.0,
                "last_stable_moving_average_latency": 2.0,
                "last_stable_max_latency": 3.5,
                "last_stable_p50": 1.8,
                "last_stable_p75": 2.2,
                "last_stable_p90": 2.8,
                "last_stable_p95": 3.1,
                "last_stable_p99": 3.4,
                "chunk_server_e2e_latency_seconds_p99": 2.9,
                "decode_latency_seconds_avg": 0.5,
                "total_dropped_chunks": 0,
            }
        )
    )

    defaults = {
        "release": "3.2",
        "model": "CR2-8B",
        "precision": "FP8",
        "engine": "vLLM",
        "isl_text": 100,
    }

    rows = extract_max_streams_data(tmp_path, "H100", defaults)

    assert len(rows) == 1
    row = rows[0]
    assert row["Max Concurrent Streams"] == 144
    assert row["Chunk E2E Latency Min (s)"] == 1.0
    assert row["Chunk E2E Latency Avg (s)"] == 2.0
    assert row["Chunk E2E Latency Max (s)"] == 3.5
    assert row["Chunk E2E Latency p50 (s)"] == 1.8
    assert row["Chunk E2E Latency p75 (s)"] == 2.2
    assert row["Chunk E2E Latency p90 (s)"] == 2.8
    assert row["Chunk E2E Latency p95 (s)"] == 3.1
    assert row["Chunk E2E Latency p99 (s)"] == 3.4
    assert row["Server E2E Stage Latency p99 (s)"] == 2.9
    assert row["Decode latency Avg (s)"] == 0.5
    assert row["Dropped Chunks"] == 0
