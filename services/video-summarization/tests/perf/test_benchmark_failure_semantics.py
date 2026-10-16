# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression coverage for benchmark failure and cleanup semantics."""

import json
import logging
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore", FutureWarning)
    from base import BenchmarkBase
    from file_burst_benchmark import FileBurstBenchmark
    from single_file_benchmark import SingleFileBenchmark
    import file_burst_benchmark as file_burst_module
    import single_file_benchmark as single_file_module
    import vss_perf_benchmark as benchmark_main

from vss_perf_lvs_adapter import execution_results_to_test_cases


def test_single_file_requires_every_iteration_to_pass(monkeypatch, tmp_path):
    benchmark = object.__new__(SingleFileBenchmark)
    benchmark.logger = logging.getLogger("single-file-failure-test")
    benchmark._upload_video = lambda config: "file-1"
    deleted = []
    benchmark._delete_video = deleted.append
    results = iter(
        [
            {"iteration": 1, "success": True},
            {"iteration": 2, "success": False, "error": "request failed"},
        ]
    )
    benchmark._execute_single_iteration = lambda *args: next(results)
    benchmark.save_json_data = lambda *args: None
    monkeypatch.setattr(single_file_module.time, "sleep", lambda *_: None)

    result = benchmark._execute_single_test_case(
        "case", {"url": "http://media/video.mp4"}, 10, {"iterations": 2}, "model", str(tmp_path)
    )

    assert result["successful_iterations"] == 1
    assert result["success"] is False
    assert deleted == ["file-1"]


@pytest.mark.parametrize("iterations", [0, -1, True, 1.5])
def test_single_file_rejects_nonpositive_or_noninteger_iterations(iterations):
    benchmark = object.__new__(SingleFileBenchmark)

    with pytest.raises(ValueError, match="positive integer"):
        benchmark.parse_benchmark_config(
            {
                "benchmark_mode": "single_file",
                "iterations": iterations,
                "videos": [{"url": "http://media/video.mp4", "chunk_sizes": [10]}],
                "summarize_api_params": {"events": [], "scenario": "test"},
            },
            {},
        )


def test_file_burst_requires_all_requested_work_to_pass(monkeypatch, tmp_path):
    benchmark = object.__new__(FileBurstBenchmark)
    benchmark.logger = logging.getLogger("file-burst-failure-test")
    benchmark._test_single_concurrency_level = lambda *args: {
        "concurrency_level": args[3],
        "completed_files": 0 if args[3] == 2 else args[3],
        "failed_files": 2 if args[3] == 2 else 0,
        "success": args[3] != 2,
        "avg_latency": 1.0,
    }
    benchmark._find_optimal_concurrency_for_target_latency = lambda *args: (
        {"error": "not needed"},
        [],
    )
    benchmark.save_json_data = lambda *args: None
    monkeypatch.setattr(file_burst_module.time, "sleep", lambda *_: None)

    result = benchmark._execute_file_burst_test_case(
        "case",
        {"url": "http://media/video.mp4", "concurrency_levels": [1, 2]},
        10,
        {},
        "model",
        str(tmp_path),
    )

    assert result["success"] is False


def test_file_burst_cleans_partial_uploads(monkeypatch, tmp_path):
    benchmark = object.__new__(FileBurstBenchmark)
    benchmark.logger = logging.getLogger("file-burst-cleanup-test")
    benchmark._configure_http_session = lambda *_: None
    uploads = iter(["file-1", RuntimeError("second upload failed")])

    def upload(_config):
        value = next(uploads)
        if isinstance(value, Exception):
            raise value
        return value

    benchmark._upload_video = upload
    deleted = []
    benchmark._delete_video = deleted.append

    with pytest.raises(RuntimeError, match="second upload failed"):
        benchmark._test_single_concurrency_level(
            "case",
            {"url": "http://media/video.mp4"},
            10,
            2,
            {},
            "model",
            str(tmp_path),
        )

    assert deleted == ["file-1"]


def test_file_burst_stops_monitors_before_cleanup_even_when_export_fails(
    monkeypatch, tmp_path
):
    benchmark = object.__new__(FileBurstBenchmark)
    benchmark.logger = logging.getLogger("file-burst-monitor-cleanup-test")
    benchmark.latency_tracker = SimpleNamespace(
        clear=lambda: None,
        get_stats=lambda: {"avg_latency": 1.0},
        get_all_latencies=lambda: [1.0],
    )
    benchmark._configure_http_session = lambda *_: None
    benchmark._upload_video = lambda _config: "file-1"
    benchmark._process_single_file_burst = lambda *args: {
        "success": True,
        "processing_time": 1.0,
        "events_detected": 1,
    }
    benchmark.start_gpu_monitoring = lambda: None
    benchmark.start_cpu_monitoring = lambda: None
    events = []

    def fail_gpu_export(**_kwargs):
        events.append("stop-gpu")
        raise RuntimeError("GPU export failed")

    benchmark.stop_gpu_monitoring = fail_gpu_export
    benchmark.stop_cpu_monitoring = lambda **kwargs: events.append("stop-cpu")
    benchmark._delete_video = lambda file_id: events.append(f"delete-{file_id}")
    monkeypatch.setattr(file_burst_module.time, "sleep", lambda *_: None)

    with pytest.raises(RuntimeError, match="GPU export failed"):
        benchmark._test_single_concurrency_level(
            "case",
            {"url": "http://media/video.mp4"},
            10,
            1,
            {},
            "model",
            str(tmp_path),
        )

    assert events == ["stop-gpu", "stop-cpu", "delete-file-1"]


def test_adapter_retains_failed_test_case():
    execution_results = {
        "benchmark_mode": "single_file",
        "scenario_dir": "",
        "test_cases": [
            {
                "test_case_id": "failed-case",
                "video_url": "video.mp4",
                "chunk_size": 10,
                "iterations": 2,
                "successful_iterations": 0,
                "success": False,
                "error": "all requests failed",
                "iteration_results": [],
            }
        ],
    }

    cases = execution_results_to_test_cases(None, execution_results)

    assert len(cases) == 1
    assert cases[0]["status"] == "FAIL"
    assert cases[0]["config"]["error"] == "all requests failed"


def test_process_gpu_stats_uses_only_vlm_nvdec_samples(tmp_path):
    stats_path = tmp_path / "gpu_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "gpu_stats": {},
                "nvdec_stats": {"mean": 45.0, "std": 0.0, "p90": 90.0},
                "nvdec_data": {
                    "GPU0_AvgNVDEC": [80.0, 100.0],
                    "GPU1_AvgNVDEC": [0.0, 0.0],
                },
            }
        ),
        encoding="utf-8",
    )
    benchmark = SimpleNamespace(
        vlm_gpus=[0], llm_gpus=[1], logger=logging.getLogger("nvdec-test")
    )

    metrics = BenchmarkBase.process_gpu_stats(benchmark, str(stats_path))

    assert metrics["vlm_nvdec_usage_mean"] == 90.0


def test_requested_upload_failure_propagates(monkeypatch, tmp_path):
    json_path = tmp_path / "result.json"
    monkeypatch.setattr(benchmark_main, "build_and_save", lambda *args, **kwargs: json_path)
    monkeypatch.setattr(
        benchmark_main,
        "discover_platform",
        lambda **kwargs: {"gpu": {"model": "H100", "count": 2}},
    )
    monkeypatch.setattr(benchmark_main, "upload_result_file", lambda *args, **kwargs: False)
    args = SimpleNamespace(
        output_json="result.json",
        upload=True,
        triggered_by="ci_pipeline",
        pipeline_url="https://ci.example/job?x=1&y=2",
        release="test",
    )

    result = benchmark_main.write_vss_result_json(
        args,
        {"vlm_gpus": [0], "llm_gpus": [1]},
        {
            "gpu_model": "H100",
            "gpu_topology": "1x1",
            "vlm_model": "cosmos-reason3-nano",
            "llm_model": "nemotron-3-nano",
            "vision_input_tokens": "9k",
        },
        "h100-2-1x1-cr3-nano-rtvi-nemo3nano-9k",
        [{"test_case_id": "case", "status": "PASS", "metrics": {}}],
        benchmark_names=["quick_test"],
        benchmark_modes=["single_file"],
        output_base_dir=str(tmp_path),
        duration_seconds=1.0,
        passed=1,
        failed=0,
    )

    assert result is None
