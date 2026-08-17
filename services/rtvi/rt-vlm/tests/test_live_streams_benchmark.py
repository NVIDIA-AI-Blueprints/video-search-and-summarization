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

# isort: skip_file

import sys
import threading
import time
import types
from contextlib import contextmanager
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path

import requests

BENCHMARK_DIR = Path(__file__).resolve().parents[1] / "perf" / "benchmark"
sys.path.insert(0, str(BENCHMARK_DIR))

import live_streams_benchmark as live_streams_benchmark_module  # noqa: E402
from concurrent_live_streams_benchmark import (  # noqa: E402
    _rtsp_reuse_summary as concurrent_rtsp_reuse_summary,
    _rtsp_source_config_for_results as concurrent_rtsp_source_config_for_results,
)
from base import BenchmarkBase, BenchmarkCleanupError  # noqa: E402
from latency_tracker import LatencyTracker  # noqa: E402
from live_streams_benchmark import (  # noqa: E402
    LiveStreamsBenchmark,
    _rtsp_reuse_summary as max_rtsp_reuse_summary,
    _rtsp_source_config_for_results as max_rtsp_source_config_for_results,
)


@contextmanager
def _fast_probe_sleep():
    original_time = live_streams_benchmark_module.time
    live_streams_benchmark_module.time = types.SimpleNamespace(
        time=time.time,
        sleep=lambda _seconds: None,
    )
    try:
        yield
    finally:
        live_streams_benchmark_module.time = original_time


class _AdvancingTime:
    def __init__(self):
        self.current_time = 0.0

    def time(self):
        return self.current_time

    def sleep(self, seconds):
        self.current_time += max(float(seconds), 0.001)


class _ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        future = Future()
        future.set_result(None)
        return future


class _FakeLatencyTracker:
    def __init__(self):
        self.clear_calls = 0

    def clear(self, reset_ignored=False):
        self.clear_calls += 1


class _PhaseOneLatencyTracker:
    def __init__(self, stability_sequence):
        self.stability_sequence = list(stability_sequence)
        self.is_stable_calls = 0
        self.clear_calls = 0

    def clear(self, reset_ignored=False):
        self.clear_calls += 1

    def get_stats(self):
        return {
            "avg_latency": 20.0,
            "moving_average_latency": 20.0,
            "max_latency": 20.0,
            "min_latency": 20.0,
            "total_measurements": max(self.is_stable_calls, 1),
        }

    def get_recent_p95(self):
        return 20.0

    def get_recent_percentiles(self):
        return {"p50": 20.0, "p75": 20.0, "p90": 20.0, "p95": 20.0, "p99": 20.0}

    def is_stable(self, latency_threshold):
        idx = min(self.is_stable_calls, len(self.stability_sequence) - 1)
        self.is_stable_calls += 1
        return self.stability_sequence[idx]

    def get_per_stream_stats_str(self):
        return ""

    def get_all_latencies(self):
        return {}

    def get_stream_measurement_counts(self):
        return {f"stream-{idx}": self.is_stable_calls for idx in range(1, 6)}

    def get_fresh_stream_coverage(self, baseline_counts, active_stream_ids, min_new_measurements=1):
        current_counts = self.get_stream_measurement_counts()
        fresh_streams = sum(
            current_counts.get(stream_id, 0) - baseline_counts.get(stream_id, 0)
            >= min_new_measurements
            for stream_id in active_stream_ids
        )
        return {
            "active_streams": len(active_stream_ids),
            "fresh_streams": fresh_streams,
            "coverage": fresh_streams / len(active_stream_ids) if active_stream_ids else 0.0,
            "stale_streams": [],
        }


class _FakeGpuMonitor:
    supported_gpu_ids = [0]

    def get_gpu_usage(self):
        return 0.0

    def get_nvdec_usage(self):
        return 0.0


class _FakePrometheusGpuCollector:
    def get_latest_stats(self):
        return {"gpu_usage": 96.0, "nvdec_usage": 73.0}


class _CleanupBenchmark(BenchmarkBase):
    def __init__(self):
        super().__init__("http://localhost:0", output_base_dir="/tmp")
        self.active_stream_ids = set()
        self.api_calls = []

    def parse_benchmark_config(self, scenario_config, global_config):
        return {}

    def execute(self, config, scenario_name):
        return {}

    def analyze_results(self, results, config):
        return {}

    def _fetch_active_stream_ids(self, strict=False):
        return set(self.active_stream_ids)

    def make_api_call(
        self,
        endpoint,
        method="GET",
        data=None,
        params=None,
        headers=None,
        files=None,
        timeout=None,
    ):
        self.api_calls.append((method, endpoint, data))

        class _Response:
            def json(self_inner):
                if endpoint == "/streams/delete-batch":
                    self.active_stream_ids.difference_update(data["stream_ids"])
                    return {"deleted": list(data["stream_ids"]), "errors": []}
                return {}

        return _Response()


class _StreamInfoFailureCleanupBenchmark(_CleanupBenchmark):
    def _fetch_active_stream_ids(self, strict=False):
        if strict:
            raise RuntimeError("Could not fetch active stream IDs: stream-info unavailable")
        return set()


class _PartialDeleteFailureCleanupBenchmark(_CleanupBenchmark):
    def __init__(self):
        super().__init__()
        self.waited_for = []

    def make_api_call(self, endpoint, method="GET", data=None, **kwargs):
        if endpoint == "/streams/delete-batch":
            self.active_stream_ids.discard("stream-a")

            class _Response:
                @staticmethod
                def json():
                    return {
                        "deleted": ["stream-a"],
                        "errors": [
                            {
                                "stream_id": "stream-b",
                                "status_code": 500,
                                "error": "drain failed",
                            }
                        ],
                    }

            return _Response()
        return super().make_api_call(endpoint, method=method, data=data, **kwargs)

    def _wait_for_streams_deleted(self, stream_ids, *args, **kwargs):
        self.waited_for = list(stream_ids)


class _TransientDeleteConflictCleanupBenchmark(_CleanupBenchmark):
    def __init__(self):
        super().__init__()
        self.delete_attempts = 0

    def make_api_call(self, endpoint, method="GET", data=None, **kwargs):
        if endpoint == "/streams/delete-batch":
            self.delete_attempts += 1

            class _Response:
                def json(response_self):
                    if self.delete_attempts == 1:
                        return {
                            "deleted": [],
                            "errors": [
                                {
                                    "stream_id": "stream-a",
                                    "status_code": 409,
                                    "error_code": "ResourceInUse",
                                    "error": "Live stream stream-a is already being stopped",
                                }
                            ],
                        }
                    self.active_stream_ids.discard("stream-a")
                    return {"deleted": ["stream-a"], "errors": []}

            return _Response()
        return super().make_api_call(endpoint, method=method, data=data, **kwargs)


class _FileDeleteFailureCleanupBenchmark(_CleanupBenchmark):
    def make_api_call(self, endpoint, method="GET", data=None, **kwargs):
        if endpoint.startswith("/files/") and method == "DELETE":
            response = requests.Response()
            response.status_code = 500
            raise requests.exceptions.HTTPError("file delete failed", response=response)
        return super().make_api_call(endpoint, method=method, data=data, **kwargs)


class _ProbeFreshnessLatencyTracker:
    def __init__(self, coverage_sequence):
        self.coverage_sequence = list(coverage_sequence)
        self.coverage_calls = 0

    def get_stats(self):
        return {
            "avg_latency": 1.0,
            "moving_average_latency": 1.0,
            "max_latency": 1.0,
            "min_latency": 1.0,
            "total_measurements": 1,
        }

    def get_recent_p95(self):
        return 1.0

    def is_stable(self, latency_threshold):
        return True

    def get_stream_measurement_counts(self):
        return {}

    def get_fresh_stream_coverage(self, baseline_counts, active_stream_ids, min_new_measurements=1):
        idx = min(self.coverage_calls, len(self.coverage_sequence) - 1)
        self.coverage_calls += 1
        coverage = self.coverage_sequence[idx]
        fresh_streams = int(len(active_stream_ids) * coverage)
        return {
            "active_streams": len(active_stream_ids),
            "fresh_streams": fresh_streams,
            "coverage": coverage,
            "stale_streams": active_stream_ids[fresh_streams:],
        }

    def get_per_stream_stats_str(self):
        return ""


class _ProbeOnlyLiveStreamsBenchmark(LiveStreamsBenchmark):
    def __init__(self, stable_through: int):
        super().__init__("http://localhost:0", output_base_dir="/tmp")
        self.stable_through = stable_through
        self.latency_tracker = _FakeLatencyTracker()
        self.probed_counts = []
        self.added_stream_numbers = []
        self.deleted_batches = []
        self.captured_snapshots = []

    def _add_live_stream(self, video_config, stream_num):
        self.added_stream_numbers.append(stream_num)
        return f"stream-{len(self.added_stream_numbers)}"

    def _batch_delete_streams(self, stream_ids, inter_delete_delay=0.0):
        self.deleted_batches.append(list(stream_ids))

    def _monitor_stream_latency(self, *args, **kwargs):
        return None

    def _run_probe_stability_check(
        self,
        probe_label,
        stream_count,
        stability_check_interval,
        required_stable_windows,
        required_unstable_windows,
        latency_threshold,
        active_stream_ids=None,
        video_config=None,
    ):
        self.probed_counts.append(stream_count)
        return stream_count <= self.stable_through

    def _capture_stable_probe_latency_stats(self, stream_count, snapshot_source):
        self.captured_snapshots.append((stream_count, snapshot_source))
        return {
            "moving_average_latency": float(stream_count),
            "max_latency": float(stream_count),
            "p50": float(stream_count),
            "p75": float(stream_count),
            "p90": float(stream_count),
            "p95": float(stream_count),
            "p99": float(stream_count),
        }

    def reset_prometheus_collectors(self):
        return None


class _PhaseOneOnlyLiveStreamsBenchmark(LiveStreamsBenchmark):
    def __init__(self, latency_tracker, output_base_dir):
        super().__init__("http://localhost:0", output_base_dir=str(output_base_dir))
        self.latency_tracker = latency_tracker
        self.gpu_monitor = _FakeGpuMonitor()

    def _add_live_stream(self, video_config, stream_num):
        return f"stream-{stream_num}"

    def _batch_delete_streams(self, stream_ids, inter_delete_delay=0.0):
        return None

    def _monitor_stream_latency(self, *args, **kwargs):
        return None

    def start_gpu_monitoring(self):
        return None

    def reset_gpu_monitoring(self):
        return None

    def stop_recording_gpu_usage(self):
        return None

    def stop_gpu_monitoring(self, export_dir=None, filename_prefix="gpu_metrics"):
        return None

    def start_cpu_monitoring(self):
        return None

    def stop_cpu_monitoring(self):
        return {}

    def scrape_metrics(self):
        return {}

    def process_gpu_stats(self, gpu_stats_file):
        return {}

    def get_cpu_stats(self):
        return {}


class _AddFailureAfterStableBenchmark(_PhaseOneOnlyLiveStreamsBenchmark):
    def _add_live_stream(self, video_config, stream_num):
        if stream_num > video_config["fail_add_after_stream"]:
            raise RuntimeError("RTSP add failure: test source refused next stream")
        return f"stream-{stream_num}"


class _SkipBadRtspSourceBenchmark(_PhaseOneOnlyLiveStreamsBenchmark):
    def __init__(self, latency_tracker, output_base_dir):
        super().__init__(latency_tracker, output_base_dir)
        self.add_attempts = []

    def _add_live_stream(self, video_config, stream_num):
        self.add_attempts.append(stream_num)
        if stream_num in video_config["bad_rtsp_source_nums"]:
            raise RuntimeError(f"RTSP add failure for source {stream_num}")
        return f"stream-source-{stream_num}"


def test_cleanup_resources_batches_live_stream_deletes():
    benchmark = _CleanupBenchmark()
    benchmark.active_stream_ids = {"stream-a", "stream-b"}
    benchmark.active_resources = ["stream_stream-a", "file_file-a", "stream_stream-b"]

    benchmark.cleanup_resources()

    assert benchmark.api_calls == [
        ("DELETE", "/streams/delete-batch", {"stream_ids": ["stream-a", "stream-b"]}),
        ("DELETE", "/files/file-a", None),
    ]
    assert benchmark.active_resources == []


def test_stop_live_generation_requests_uses_backend_specific_endpoint():
    benchmark = _CleanupBenchmark()

    benchmark._stop_live_generation_requests(["stream-a"], backend_type="rtvi_vlm")
    benchmark._stop_live_generation_requests(["stream-b"], backend_type="rtvi_embed")

    assert ("DELETE", "/generate_captions/stream-a", None) in benchmark.api_calls
    assert ("DELETE", "/generate_video_embeddings/stream-b", None) in benchmark.api_calls


def test_stop_live_generation_requests_uses_bounded_concurrency(monkeypatch):
    class _ConcurrentStopBenchmark(_CleanupBenchmark):
        def __init__(self):
            super().__init__()
            self.current_calls = 0
            self.peak_calls = 0
            self.lock = threading.Lock()

        def make_api_call(self, endpoint, method="GET", data=None, **kwargs):
            with self.lock:
                self.current_calls += 1
                self.peak_calls = max(self.peak_calls, self.current_calls)
            time.sleep(0.02)
            with self.lock:
                self.current_calls -= 1
            return super().make_api_call(endpoint, method=method, data=data, **kwargs)

    monkeypatch.setenv("RTVI_BENCHMARK_STREAM_STOP_MAX_WORKERS", "2")
    benchmark = _ConcurrentStopBenchmark()

    benchmark._stop_live_generation_requests([f"stream-{i}" for i in range(4)])

    assert benchmark.peak_calls == 2
    assert len(benchmark.api_calls) == 4


def test_live_stream_results_preserve_all_supported_rtsp_source_modes():
    source_configs = [
        ({"rtsp_url": "rtsp://example.test/live"}, {"rtsp_url": "rtsp://example.test/live"}),
        (
            {"rtsp_urls": ["rtsp://example.test/one", "rtsp://example.test/two"]},
            {
                "rtsp_url": "",
                "rtsp_urls": ["rtsp://example.test/one", "rtsp://example.test/two"],
            },
        ),
        (
            {"rtsp_urls_file": "/tmp/rtsp-urls.txt"},
            {"rtsp_url": "", "rtsp_urls_file": "/tmp/rtsp-urls.txt"},
        ),
        (
            {"rtsp_url_template": "rtsp://example.test/stream-{stream_num}"},
            {
                "rtsp_url": "",
                "rtsp_url_template": "rtsp://example.test/stream-{stream_num}",
            },
        ),
    ]

    for source_config, expected in source_configs:
        assert max_rtsp_source_config_for_results(source_config) == expected
        assert concurrent_rtsp_source_config_for_results(source_config) == expected


def test_cleanup_resources_can_use_blocking_live_stream_delete(monkeypatch):
    monkeypatch.setenv("RTVI_BENCHMARK_BLOCKING_STREAM_DELETE", "true")

    benchmark = _CleanupBenchmark()
    benchmark.active_stream_ids = {"stream-a", "stream-b"}
    benchmark.active_resources = ["stream_stream-a", "stream_stream-b"]

    benchmark.cleanup_resources()

    assert benchmark.api_calls == [
        (
            "DELETE",
            "/streams/delete-batch",
            {
                "stream_ids": ["stream-a", "stream-b"],
                "blocking": True,
                "drain_timeout_seconds": 300.0,
            },
        ),
    ]
    assert benchmark.active_resources == []


def test_live_stream_cleanup_verification_fails_closed_when_stream_info_unavailable():
    benchmark = _StreamInfoFailureCleanupBenchmark()
    benchmark.active_resources = ["stream_stream-a"]

    try:
        benchmark.assert_no_active_streams("before scenario")
    except RuntimeError as exc:
        assert "stream-info unavailable" in str(exc)
    else:
        raise AssertionError("Expected stream-info fetch failure to abort cleanup verification")


def test_batch_live_stream_delete_fails_closed_when_stream_info_unavailable():
    benchmark = _StreamInfoFailureCleanupBenchmark()
    benchmark.active_resources = ["stream_stream-a"]

    try:
        benchmark._batch_delete_streams(["stream-a"])
    except RuntimeError as exc:
        assert "stream-info unavailable" in str(exc)
    else:
        raise AssertionError("Expected stream-info fetch failure to abort batch cleanup")

    assert benchmark.active_resources == ["stream_stream-a"]


def test_batch_delete_waits_only_for_successful_or_already_gone_streams():
    benchmark = _PartialDeleteFailureCleanupBenchmark()
    benchmark.active_stream_ids = {"stream-a", "stream-b"}
    benchmark.active_resources = ["stream_stream-a", "stream_stream-b"]

    try:
        benchmark._batch_delete_streams(["stream-a", "stream-b"])
    except RuntimeError as exc:
        assert "drain failed" in str(exc)
    else:
        raise AssertionError("Expected the failed stream delete to fail closed")

    assert benchmark.waited_for == ["stream-a"]
    assert benchmark.active_resources == ["stream_stream-b"]


def test_batch_delete_retries_transient_resource_in_use(monkeypatch):
    monkeypatch.setenv("RTVI_BENCHMARK_STREAM_DELETE_RETRY_DELAY_SEC", "0")
    benchmark = _TransientDeleteConflictCleanupBenchmark()
    benchmark.active_stream_ids = {"stream-a"}
    benchmark.active_resources = ["stream_stream-a"]

    benchmark._batch_delete_streams(["stream-a"])

    assert benchmark.delete_attempts == 2
    assert benchmark.active_resources == []


def test_ntp_latency_ignores_null_terminal_media_info():
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")

    assert benchmark._extract_ntp_timestamp_latency_seconds({"media_info": None}) == (None, "")


def test_cleanup_resources_keeps_tracking_after_file_delete_http_error():
    benchmark = _FileDeleteFailureCleanupBenchmark()
    benchmark.active_resources = ["file_file-a"]

    try:
        benchmark.cleanup_resources()
    except RuntimeError as exc:
        assert "file_file-a" in str(exc)
    else:
        raise AssertionError("Expected a non-404 file delete error to fail cleanup")

    assert benchmark.active_resources == ["file_file-a"]


def test_max_live_stream_cleanup_failure_aborts_remaining_test_cases(tmp_path):
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir=str(tmp_path))
    attempts = []
    benchmark.parse_global_config = lambda _config: {}
    benchmark.parse_benchmark_config = lambda _scenario, _global: {
        "videos": [{"name": "test", "chunk_sizes": [10, 20]}]
    }
    benchmark.setup_scenario_directory = lambda _scenario: str(tmp_path)
    benchmark.get_available_models = lambda: "test-model"

    def fail_cleanup(test_case_id, *_args, **_kwargs):
        attempts.append(test_case_id)
        raise BenchmarkCleanupError("stale live streams remain")

    benchmark._execute_live_streams_test_case = fail_cleanup

    try:
        benchmark.execute({"test_scenarios": {"scenario": {}}}, "scenario")
    except BenchmarkCleanupError as exc:
        assert "stale live streams remain" in str(exc)
    else:
        raise AssertionError("cleanup failure did not abort remaining test cases")

    assert attempts == ["max_live_streams_test_10sec"]


def test_rtsp_reuse_summary_marks_pool_exhaustion_without_reuse(tmp_path):
    rtsp_urls_file = tmp_path / "rtsp-urls.txt"
    rtsp_urls_file.write_text(
        "rtsp://example.test/one\nrtsp://example.test/two\n",
        encoding="utf-8",
    )
    video_config = {
        "rtsp_url": "rtsp://example.test/fallback",
        "rtsp_urls_file": str(rtsp_urls_file),
        "allow_rtsp_url_reuse_after_pool_exhausted": True,
    }

    expected = {
        "unique_rtsp_url_per_stream": True,
        "rtsp_url_source_count": 2,
        "rtsp_url_pool_exhausted": True,
        "rtsp_url_pool_exhausted_at_stream": 3,
        "rtsp_url_reuse_count": 0,
        "rtsp_url_reuse_caveat": (
            "RTSP source pool exhausted at stream 3; "
            "independent stream benchmarks require a distinct RTSP URL for each "
            "stream and must stop instead of reusing a fallback rtsp_url."
        ),
    }

    assert max_rtsp_reuse_summary(video_config, 5) == expected
    assert concurrent_rtsp_reuse_summary(video_config, 5) == expected


def test_independent_rtsp_pool_exhaustion_does_not_reuse_fallback(tmp_path):
    rtsp_urls_file = tmp_path / "rtsp-urls.txt"
    rtsp_urls_file.write_text("rtsp://example.test/one\n", encoding="utf-8")
    video_config = {
        "rtsp_url": "rtsp://example.test/fallback",
        "rtsp_urls_file": str(rtsp_urls_file),
        "allow_rtsp_url_reuse_after_pool_exhausted": True,
    }

    try:
        live_streams_benchmark_module._rtsp_url_for_stream(video_config, 2)
    except ValueError as exc:
        assert "requires a distinct RTSP URL" in str(exc)
    else:
        raise AssertionError("Expected independent RTSP pool exhaustion to fail closed")

    try:
        __import__("concurrent_live_streams_benchmark")._rtsp_url_for_stream(video_config, 2)
    except ValueError as exc:
        assert "requires a distinct RTSP URL" in str(exc)
    else:
        raise AssertionError("Expected concurrent RTSP pool exhaustion to fail closed")


def test_intentional_rtsp_reuse_reports_distinct_logical_assets():
    video_config = {
        "rtsp_url": "rtsp://example.test/shared",
        "unique_rtsp_url_per_stream": False,
        "force_unique_benchmark_stream_ids": True,
    }
    expected = {
        "unique_rtsp_url_per_stream": False,
        "rtsp_url_source_count": 1,
        "rtsp_url_pool_exhausted": False,
        "rtsp_url_pool_exhausted_at_stream": None,
        "rtsp_url_reuse_count": 2,
        "rtsp_url_reuse_caveat": (
            "3 logical stream assets intentionally reuse one RTSP URL; "
            "asset and caption request IDs remain distinct, but source content is shared."
        ),
    }

    assert live_streams_benchmark_module._rtsp_url_for_stream(video_config, 2) == (
        "rtsp://example.test/shared"
    )
    assert max_rtsp_reuse_summary(video_config, 3) == expected
    assert concurrent_rtsp_reuse_summary(video_config, 3) == expected


def test_add_failure_after_stable_window_reports_capacity_boundary(tmp_path):
    tracker = _PhaseOneLatencyTracker([True, True])
    with _fast_probe_sleep():
        benchmark = _AddFailureAfterStableBenchmark(tracker, tmp_path)
        result = benchmark._execute_live_streams_test_case(
            test_case_id="add_failure_boundary",
            video_config={
                "name": "add_failure_boundary",
                "rtsp_url": "rtsp://example.test/live",
                "chunk_sizes": [10],
                "latency_threshold_seconds": 10,
                "initial_stream_count": 5,
                "fail_add_after_stream": 5,
                "stability_check_interval": 0,
                "required_stable_windows": 1,
                "required_unstable_windows": 2,
                "binary_search_refinement": False,
            },
            chunk_size=10,
            benchmark_config={"backend_type": "rtvi_vlm", "api_params": {}},
            model_name="test-model",
            scenario_dir=str(tmp_path),
        )

    assert result["success"] is True
    assert result["max_sustainable_streams"] == 5
    assert result["last_stable_stream_count"] == 5
    assert result["first_unstable_stream_count"] == 6
    assert result["stream_add_failure_stream_count"] == 6
    assert "RTSP add failure" in result["stream_add_failure"]


def test_initial_seed_add_failure_still_validates_created_streams(tmp_path):
    tracker = _PhaseOneLatencyTracker([True, True])
    with _fast_probe_sleep():
        benchmark = _AddFailureAfterStableBenchmark(tracker, tmp_path)
        result = benchmark._execute_live_streams_test_case(
            test_case_id="initial_add_failure_boundary",
            video_config={
                "name": "initial_add_failure_boundary",
                "rtsp_url": "rtsp://example.test/live",
                "chunk_sizes": [10],
                "latency_threshold_seconds": 10,
                "initial_stream_count": 10,
                "fail_add_after_stream": 5,
                "stability_check_interval": 0,
                "required_stable_windows": 1,
                "required_unstable_windows": 2,
                "binary_search_refinement": False,
            },
            chunk_size=10,
            benchmark_config={"backend_type": "rtvi_vlm", "api_params": {}},
            model_name="test-model",
            scenario_dir=str(tmp_path),
        )

    assert result["success"] is True
    assert result["max_sustainable_streams"] == 5
    assert result["last_stable_stream_count"] == 5
    assert result["first_unstable_stream_count"] == 6
    assert result["stream_add_failure_stream_count"] == 6
    assert "RTSP add failure" in result["stream_add_failure"]


def test_stream_add_retries_then_skips_bad_rtsp_source(tmp_path):
    benchmark = _SkipBadRtspSourceBenchmark(_PhaseOneLatencyTracker([True]), tmp_path)
    skipped_sources = []

    stream_id, next_source_num = benchmark._add_live_stream_with_retries(
        {
            "rtsp_urls": [
                "rtsp://example.test/one",
                "rtsp://example.test/two",
                "rtsp://example.test/three",
            ],
            "bad_rtsp_source_nums": {2},
            "stream_add_retry_attempts": 2,
            "stream_add_max_rtsp_source_skips": 1,
            "stream_add_retry_delay_seconds": 0,
        },
        stream_num=2,
        rtsp_source_num=2,
        skipped_rtsp_sources=skipped_sources,
    )

    assert stream_id == "stream-source-3"
    assert next_source_num == 4
    assert benchmark.add_attempts == [2, 2, 3]
    assert skipped_sources == [
        {
            "logical_stream_num": 2,
            "rtsp_source_num": 2,
            "error": "RTSP add failure for source 2",
            "attempts": 2,
        }
    ]


def test_max_live_intentional_rtsp_reuse_is_not_limited_to_one_logical_stream(tmp_path):
    benchmark = _SkipBadRtspSourceBenchmark(_PhaseOneLatencyTracker([True]), tmp_path)

    stream_id, next_source_num = benchmark._add_live_stream_with_retries(
        {
            "rtsp_url": "rtsp://example.test/shared",
            "unique_rtsp_url_per_stream": False,
            "bad_rtsp_source_nums": set(),
        },
        stream_num=2,
        rtsp_source_num=2,
        skipped_rtsp_sources=[],
    )

    assert stream_id == "stream-source-2"
    assert next_source_num == 3


def test_max_live_intentional_reuse_does_not_skip_the_same_failed_url(tmp_path):
    benchmark = _SkipBadRtspSourceBenchmark(_PhaseOneLatencyTracker([True]), tmp_path)

    try:
        benchmark._add_live_stream_with_retries(
            {
                "rtsp_url": "rtsp://example.test/shared",
                "unique_rtsp_url_per_stream": False,
                "bad_rtsp_source_nums": {2},
                "stream_add_retry_attempts": 2,
                "stream_add_max_rtsp_source_skips": 5,
                "stream_add_retry_delay_seconds": 0,
            },
            stream_num=2,
            rtsp_source_num=2,
            skipped_rtsp_sources=[],
        )
    except Exception as exc:
        assert "after skipping 0 RTSP source(s)" in str(exc)
    else:
        raise AssertionError("Intentional source reuse skipped to the same failed URL")

    assert benchmark.add_attempts == [2, 2]


def test_binary_search_extends_one_stream_at_a_time_after_stable_ceiling():
    with _fast_probe_sleep():
        benchmark = _ProbeOnlyLiveStreamsBenchmark(stable_through=42)
        active_stream_ids = [f"existing-{idx}" for idx in range(38)]

        max_streams, latency_stats, unstable_ceiling = benchmark._binary_search_max_streams(
            low=38,
            high=40,
            active_stream_ids=active_stream_ids,
            active_futures=[],
            executor=_ImmediateExecutor(),
            video_config={"rtsp_url": "rtsp://example.test/live"},
            chunk_size=30,
            benchmark_config={"backend_type": "rtvi_vlm", "api_params": {}},
            model_name="test-model",
            stability_check_interval=0,
            required_stable_windows=2,
            required_unstable_windows=3,
            latency_threshold=10.0,
            probe_cooldown=0.0,
        )

    assert max_streams == 42
    assert unstable_ceiling == 43
    assert latency_stats["p95"] == 42.0
    assert benchmark.probed_counts == [39, 40, 41, 42, 43]
    assert len(active_stream_ids) == 43


def test_probe_waits_for_fresh_latency_coverage_before_declaring_unstable(monkeypatch):
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")
    benchmark.gpu_monitor = _FakeGpuMonitor()
    tracker = _ProbeFreshnessLatencyTracker([0.0, 0.1, 1.0, 1.0])
    benchmark.latency_tracker = tracker
    fake_time = _AdvancingTime()
    monkeypatch.setattr(live_streams_benchmark_module, "time", fake_time)

    result = benchmark._run_probe_stability_check(
        probe_label="BS probe",
        stream_count=107,
        stability_check_interval=10,
        required_stable_windows=2,
        required_unstable_windows=1,
        latency_threshold=10.0,
        active_stream_ids=[f"stream-{idx}" for idx in range(100)],
        video_config={
            "fresh_latency_timeout_seconds": 100,
            "min_stable_stream_coverage": 0.9,
        },
    )

    assert result is True
    assert tracker.coverage_calls == 4


def test_probe_times_out_zero_measurement_streams_through_freshness_path(monkeypatch):
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")
    benchmark.gpu_monitor = _FakeGpuMonitor()
    fake_time = _AdvancingTime()
    monkeypatch.setattr(live_streams_benchmark_module, "time", fake_time)

    result = benchmark._run_probe_stability_check(
        probe_label="BS probe",
        stream_count=50,
        stability_check_interval=10,
        required_stable_windows=2,
        required_unstable_windows=1,
        latency_threshold=10.0,
        active_stream_ids=[f"stream-{idx}" for idx in range(50)],
        video_config={
            "fresh_latency_timeout_seconds": 30,
            "min_stable_stream_coverage": 0.9,
        },
    )

    assert result is False
    assert fake_time.current_time < 90


def test_binary_search_linear_extension_can_be_disabled():
    with _fast_probe_sleep():
        benchmark = _ProbeOnlyLiveStreamsBenchmark(stable_through=42)
        active_stream_ids = [f"existing-{idx}" for idx in range(38)]

        max_streams, latency_stats, unstable_ceiling = benchmark._binary_search_max_streams(
            low=38,
            high=40,
            active_stream_ids=active_stream_ids,
            active_futures=[],
            executor=_ImmediateExecutor(),
            video_config={
                "rtsp_url": "rtsp://example.test/live",
                "binary_search_linear_extension": False,
            },
            chunk_size=30,
            benchmark_config={"backend_type": "rtvi_vlm", "api_params": {}},
            model_name="test-model",
            stability_check_interval=0,
            required_stable_windows=2,
            required_unstable_windows=3,
            latency_threshold=10.0,
            probe_cooldown=0.0,
        )

    assert max_streams == 39
    assert unstable_ceiling == 40
    assert latency_stats["p95"] == 39.0
    assert benchmark.probed_counts == [39]


def test_binary_search_linear_extension_reports_no_ceiling_at_cap():
    with _fast_probe_sleep():
        benchmark = _ProbeOnlyLiveStreamsBenchmark(stable_through=100)
        active_stream_ids = [f"existing-{idx}" for idx in range(38)]

        max_streams, latency_stats, unstable_ceiling = benchmark._binary_search_max_streams(
            low=38,
            high=40,
            active_stream_ids=active_stream_ids,
            active_futures=[],
            executor=_ImmediateExecutor(),
            video_config={
                "rtsp_url": "rtsp://example.test/live",
                "binary_search_linear_extension_cap": 42,
            },
            chunk_size=30,
            benchmark_config={"backend_type": "rtvi_vlm", "api_params": {}},
            model_name="test-model",
            stability_check_interval=0,
            required_stable_windows=2,
            required_unstable_windows=3,
            latency_threshold=10.0,
            probe_cooldown=0.0,
        )

    assert max_streams == 42
    assert unstable_ceiling is None
    assert latency_stats["p95"] == 42.0
    assert benchmark.probed_counts == [39, 40, 41, 42]
    assert len(active_stream_ids) == 42


def test_latency_growth_instability_check_is_opt_in():
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")

    enabled, ratio_threshold = benchmark._get_latency_growth_instability_config({})
    assert enabled is False
    assert ratio_threshold == 1.25

    enabled, ratio_threshold = benchmark._get_latency_growth_instability_config(
        {
            "enable_latency_growth_instability_check": "true",
            "latency_growth_ratio_threshold": 1.5,
        }
    )
    assert enabled is True
    assert ratio_threshold == 1.5


def test_consecutive_latency_growth_uses_configured_ratio_threshold():
    assert LiveStreamsBenchmark._has_consecutive_latency_growth([2.0, 3.1, 4.8], 1.5)
    assert not LiveStreamsBenchmark._has_consecutive_latency_growth([2.0, 2.4, 3.1], 1.5)
    assert not LiveStreamsBenchmark._has_consecutive_latency_growth([0.0, 2.0, 4.0], 1.25)


def test_phase1_requires_configured_unstable_windows_before_degradation(tmp_path):
    tracker = _PhaseOneLatencyTracker([False, False, False])
    with _fast_probe_sleep():
        benchmark = _PhaseOneOnlyLiveStreamsBenchmark(tracker, tmp_path)
        result = benchmark._execute_live_streams_test_case(
            test_case_id="phase1_unstable_threshold",
            video_config={
                "name": "threshold",
                "rtsp_url": "rtsp://example.test/live",
                "chunk_sizes": [10],
                "latency_threshold_seconds": 10,
                "initial_stream_count": 5,
                "stability_check_interval": 0,
                "required_stable_windows": 2,
                "required_unstable_windows": 3,
                "binary_search_refinement": False,
            },
            chunk_size=10,
            benchmark_config={"backend_type": "rtvi_vlm", "api_params": {}},
            model_name="test-model",
            scenario_dir=str(tmp_path),
        )

    assert tracker.is_stable_calls == 3
    assert result["degradation_detected"] is True
    assert result["phase2_binary_search_applied"] is False


def test_latency_tracker_reports_fresh_stream_coverage():
    tracker = LatencyTracker()
    tracker.record_latency(1.0, "stream-1")
    tracker.record_latency(1.0, "stream-2")
    baseline = tracker.get_stream_measurement_counts()

    tracker.record_latency(1.2, "stream-1")
    tracker.record_latency(1.3, "stream-3")

    coverage = tracker.get_fresh_stream_coverage(
        baseline,
        ["stream-1", "stream-2", "stream-3"],
        min_new_measurements=1,
    )

    assert coverage["active_streams"] == 3
    assert coverage["fresh_streams"] == 2
    assert coverage["coverage"] == 2 / 3
    assert coverage["stale_streams"] == ["stream-2"]


def test_live_stream_latency_defaults_to_ntp_timestamp(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 5, 2, 15, 13, 28, tzinfo=timezone.utc)

    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")
    result = {
        "media_info": {"type": "timestamp", "end_timestamp": "2026-05-02T15:13:24.000Z"},
        "chunk_responses": [
            {
                "chunk_id": 7,
                "processing_latency_s": 1.25,
                "chunk_latency_ms": 1400.0,
            }
        ],
    }
    monkeypatch.setattr(live_streams_benchmark_module, "datetime", FixedDatetime)

    latency, source = benchmark._extract_live_stream_latency_seconds(result)

    assert latency == 4.0
    assert source == "media_info.end_timestamp"


def test_live_stream_latency_clamps_future_ntp_timestamp(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 5, 2, 15, 13, 24, tzinfo=timezone.utc)

    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")
    result = {
        "media_info": {"type": "timestamp", "end_timestamp": "2026-05-02T15:13:31.000Z"},
    }
    monkeypatch.setattr(live_streams_benchmark_module, "datetime", FixedDatetime)

    latency, source = benchmark._extract_live_stream_latency_seconds(result)

    assert latency == 0.0
    assert source == "media_info.end_timestamp"


def test_live_stream_latency_can_use_processing_latency_for_diagnostics():
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")
    result = {
        "media_info": {"type": "timestamp", "end_timestamp": "2026-05-02T15:13:24.000Z"},
        "chunk_responses": [
            {
                "chunk_id": 7,
                "processing_latency_s": 1.25,
                "chunk_latency_ms": 1400.0,
            }
        ],
    }

    latency, source = benchmark._extract_live_stream_latency_seconds(
        result, latency_measurement_source="processing_latency"
    )

    assert latency == 1.25
    assert source == "processing_latency_s"


def test_live_stream_latency_uses_chunk_latency_when_processing_latency_missing():
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")
    result = {
        "media_info": {"type": "timestamp", "end_timestamp": "2026-05-02T15:13:24.000Z"},
        "chunk_responses": [
            {
                "chunk_id": 7,
                "chunk_latency_ms": 1400.0,
            }
        ],
    }

    latency, source = benchmark._extract_live_stream_latency_seconds(
        result, latency_measurement_source="processing_latency"
    )

    assert latency == 1.4
    assert source == "chunk_latency_ms"


def test_live_stream_chunk_integrity_detects_chunk_id_gap():
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")

    benchmark._record_stream_chunk("stream-1", 0)
    baseline = benchmark._get_stream_drop_counts()
    benchmark._record_stream_chunk("stream-1", 3)

    drops = benchmark._get_new_stream_drops(baseline, ["stream-1"])

    assert drops["total_dropped_chunks"] == 2
    assert drops["streams_with_drops"] == 1
    assert drops["per_stream_dropped_chunks"] == {"stream-1": 2}


def test_live_stream_chunk_integrity_records_chat_completion_chunk_id():
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")

    benchmark._record_response_chunks(
        "stream-1",
        {
            "chunk_id": 0,
            "choices": [{"index": 0, "delta": {"content": "yes"}, "finish_reason": None}],
        },
    )
    baseline = benchmark._get_stream_drop_counts()
    benchmark._record_response_chunks(
        "stream-1",
        {
            "chunk_id": 2,
            "choices": [{"index": 0, "delta": {"content": "no"}, "finish_reason": None}],
        },
    )

    drops = benchmark._get_new_stream_drops(baseline, ["stream-1"])

    assert drops["total_dropped_chunks"] == 1
    assert drops["streams_with_drops"] == 1
    assert drops["per_stream_dropped_chunks"] == {"stream-1": 1}


def test_live_stream_drop_baseline_latches_drops_across_stability_windows():
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")

    benchmark._record_stream_chunk("stream-1", 0)
    validation_baseline = benchmark._get_stream_drop_counts()
    benchmark._record_stream_chunk("stream-1", 3)

    first_window_drops = benchmark._get_new_stream_drops(validation_baseline, ["stream-1"])
    next_window_drops = benchmark._get_new_stream_drops(validation_baseline, ["stream-1"])

    assert first_window_drops["total_dropped_chunks"] == 2
    assert next_window_drops["total_dropped_chunks"] == 2


def test_gpu_status_prefers_prometheus_collector_over_legacy_monitor():
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")
    benchmark.gpu_monitor = _FakeGpuMonitor()
    benchmark.prometheus_gpu_collector = _FakePrometheusGpuCollector()
    benchmark.prometheus_collector_active = True

    gpu_usage, nvdec_usage, source = benchmark._get_current_gpu_monitor_usage()

    assert gpu_usage == 96.0
    assert nvdec_usage == 73.0
    assert source == "DCGM"


def test_phase2_stable_probe_snapshot_uses_prometheus_gpu_status():
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")
    benchmark.latency_tracker = _PhaseOneLatencyTracker([True])
    benchmark.gpu_monitor = _FakeGpuMonitor()
    benchmark.prometheus_gpu_collector = _FakePrometheusGpuCollector()
    benchmark.prometheus_collector_active = True
    benchmark.get_cpu_stats = lambda: {}

    benchmark._capture_stable_probe_latency_stats(133, "unit")

    snapshot = benchmark.latency_snapshots[-1]
    assert snapshot["gpu_usage_pct"] == 96.0
    assert snapshot["nvdec_usage_pct"] == 73.0
    assert snapshot["gpu_metric_source"] == "DCGM"


def test_generate_captions_endpoint_defaults_to_current_api(monkeypatch):
    monkeypatch.delenv("RTVI_VLM_GENERATE_CAPTIONS_ENDPOINT", raising=False)
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")

    assert benchmark._resolve_generate_captions_endpoint({}, {}) == "/generate_captions"


def test_generate_captions_endpoint_can_use_legacy_env_override(monkeypatch):
    monkeypatch.setenv("RTVI_VLM_GENERATE_CAPTIONS_ENDPOINT", "generate_captions_alerts")
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")

    assert benchmark._resolve_generate_captions_endpoint({}, {}) == "/generate_captions_alerts"


def test_generate_captions_endpoint_prefers_video_override(monkeypatch):
    monkeypatch.setenv("RTVI_VLM_GENERATE_CAPTIONS_ENDPOINT", "generate_captions_alerts")
    benchmark = LiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")

    assert (
        benchmark._resolve_generate_captions_endpoint(
            {"generate_captions_endpoint": "custom_video_endpoint"},
            {"generate_captions_endpoint": "custom_scenario_endpoint"},
        )
        == "/custom_video_endpoint"
    )
