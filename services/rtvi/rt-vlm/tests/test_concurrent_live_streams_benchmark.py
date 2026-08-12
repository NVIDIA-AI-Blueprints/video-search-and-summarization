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
import types
from datetime import datetime, timezone
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parents[1] / "perf" / "benchmark"
sys.path.insert(0, str(BENCHMARK_DIR))

try:
    import pandas  # noqa: F401
except ImportError:
    sys.modules["pandas"] = types.SimpleNamespace(DataFrame=object)

import concurrent_live_streams_benchmark as concurrent_live_streams_benchmark_module  # noqa: E402
from concurrent_live_streams_benchmark import ConcurrentLiveStreamsBenchmark  # noqa: E402
from base import BenchmarkCleanupError  # noqa: E402
from latency_tracker import LatencyTracker  # noqa: E402


def _benchmark():
    return ConcurrentLiveStreamsBenchmark("http://localhost:0", output_base_dir="/tmp")


class _SkipBadRtspSourceBenchmark(ConcurrentLiveStreamsBenchmark):
    def __init__(self):
        super().__init__("http://localhost:0", output_base_dir="/tmp")
        self.attempts = []

    def _add_live_stream(self, video_config, stream_num):
        self.attempts.append(stream_num)
        if stream_num == 1:
            raise RuntimeError("transient source failure")
        return f"stream-{stream_num}"


def test_stream_add_retries_then_skips_bad_rtsp_source():
    benchmark = _SkipBadRtspSourceBenchmark()
    skipped_sources = []

    stream_id, next_source_num = benchmark._add_live_stream_with_retries(
        {
            "rtsp_urls": ["rtsp://source-1", "rtsp://source-2"],
            "stream_add_retry_attempts": 2,
            "stream_add_retry_delay_seconds": 0,
            "stream_add_max_rtsp_source_skips": 1,
        },
        stream_num=1,
        rtsp_source_num=1,
        skipped_rtsp_sources=skipped_sources,
    )

    assert stream_id == "stream-2"
    assert next_source_num == 3
    assert benchmark.attempts == [1, 1, 2]
    assert skipped_sources == [
        {
            "logical_stream_num": 1,
            "rtsp_source_num": 1,
            "error": "transient source failure",
            "attempts": 2,
        }
    ]


def test_concurrent_live_intentional_rtsp_reuse_is_not_limited_to_one_logical_stream():
    benchmark = _SkipBadRtspSourceBenchmark()

    stream_id, next_source_num = benchmark._add_live_stream_with_retries(
        {
            "rtsp_url": "rtsp://source-shared",
            "unique_rtsp_url_per_stream": False,
        },
        stream_num=2,
        rtsp_source_num=2,
        skipped_rtsp_sources=[],
    )

    assert stream_id == "stream-2"
    assert next_source_num == 3


def test_concurrent_live_intentional_reuse_does_not_skip_the_same_failed_url():
    benchmark = _SkipBadRtspSourceBenchmark()

    try:
        benchmark._add_live_stream_with_retries(
            {
                "rtsp_url": "rtsp://source-shared",
                "unique_rtsp_url_per_stream": False,
                "stream_add_retry_attempts": 2,
                "stream_add_max_rtsp_source_skips": 5,
                "stream_add_retry_delay_seconds": 0,
            },
            stream_num=1,
            rtsp_source_num=1,
            skipped_rtsp_sources=[],
        )
    except RuntimeError as exc:
        assert "after skipping 0 RTSP source(s)" in str(exc)
    else:
        raise AssertionError("Intentional source reuse skipped to the same failed URL")

    assert benchmark.attempts == [1, 1]


def test_exact_stream_cardinality_is_required():
    ConcurrentLiveStreamsBenchmark._require_exact_stream_count(128, 128)

    try:
        ConcurrentLiveStreamsBenchmark._require_exact_stream_count(127, 128)
    except RuntimeError as exc:
        assert "Started 127/128 streams" in str(exc)
    else:
        raise AssertionError("partial concurrent live-stream run was accepted")


def test_unrecoverable_stream_add_failure_aborts_ramp_immediately(tmp_path):
    benchmark = _benchmark()
    attempted_streams = []

    def fail_stream_add(_video_config, stream_num, _source_num, _skipped_sources):
        attempted_streams.append(stream_num)
        raise RuntimeError("source pool unavailable")

    benchmark._configure_http_session = lambda _pool_size: None
    benchmark._add_live_stream_with_retries = fail_stream_add
    benchmark.scrape_metrics = lambda: {}
    benchmark._stop_live_generation_requests = lambda *_args, **_kwargs: None
    benchmark._batch_delete_streams = lambda *_args, **_kwargs: None

    try:
        benchmark._execute_concurrent_iteration(
            iteration=1,
            video_config={"duration_seconds": 1},
            chunk_size=10,
            stream_count=128,
            benchmark_config={"backend_type": "rtvi_vlm"},
            model_name="test-model",
            iteration_dir=str(tmp_path),
        )
    except RuntimeError as exc:
        assert "Aborting concurrent stream ramp at 1/128" in str(exc)
    else:
        raise AssertionError("unrecoverable RTSP add failure did not abort the ramp")

    assert attempted_streams == [1]


def test_cleanup_failure_aborts_remaining_iterations(tmp_path):
    benchmark = _benchmark()
    attempts = []

    def fail_cleanup(iteration, *_args, **_kwargs):
        attempts.append(iteration)
        raise BenchmarkCleanupError("stale live streams remain")

    benchmark._execute_concurrent_iteration = fail_cleanup

    try:
        benchmark._execute_concurrent_live_streams_test_case(
            test_case_id="cleanup-failure",
            video_config={},
            chunk_size=10,
            stream_count=8,
            benchmark_config={"iterations": 3},
            model_name="test-model",
            scenario_dir=str(tmp_path),
        )
    except BenchmarkCleanupError as exc:
        assert "stale live streams remain" in str(exc)
    else:
        raise AssertionError("cleanup failure did not abort remaining iterations")

    assert attempts == [1]


def test_latency_tracker_preserves_record_timestamps():
    tracker = LatencyTracker()

    tracker.record_latency(1.25, "stream-a", recorded_at=123.0)

    assert tracker.get_all_latency_records() == {
        "stream-a": [{"latency": 1.25, "recorded_at": 123.0}]
    }


def test_timestamp_latency_clamps_future_media_timestamp(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 5, 2, 15, 13, 24, tzinfo=timezone.utc)

    benchmark = _benchmark()
    monkeypatch.setattr(concurrent_live_streams_benchmark_module, "datetime", FixedDatetime)

    benchmark._record_timestamp_latency(
        {"media_info": {"type": "timestamp", "end_timestamp": "2026-05-02T15:13:31.000Z"}},
        stream_num=1,
        stream_id="stream-a",
    )

    assert benchmark.latency_tracker.get_all_latency_records() == {
        "stream-a": [{"latency": 0.0, "recorded_at": FixedDatetime.now().timestamp()}]
    }


def test_startup_burst_filter_discards_initial_queued_chunk_flush():
    benchmark = _benchmark()
    records = {
        "stream-a": [
            {"latency": 76.4, "recorded_at": 100.00},
            {"latency": 67.2, "recorded_at": 100.01},
            {"latency": 56.3, "recorded_at": 100.03},
            {"latency": 46.6, "recorded_at": 100.04},
            {"latency": 36.4, "recorded_at": 100.05},
            {"latency": 27.5, "recorded_at": 100.08},
            {"latency": 17.5, "recorded_at": 100.10},
            {"latency": 7.5, "recorded_at": 100.13},
            {"latency": 2.18, "recorded_at": 104.60},
            {"latency": 2.15, "recorded_at": 114.60},
        ]
    }

    filtered, summary = benchmark._filter_startup_burst_latency_records(
        records,
        {"discard_startup_burst_samples": True},
        chunk_size=10,
    )

    assert filtered == {"stream-a": [2.18, 2.15]}
    assert summary["raw_total_measurements"] == 10
    assert summary["filtered_total_measurements"] == 2
    assert summary["discarded_measurements"] == 8
    assert summary["streams_with_discarded_startup_burst"] == 1
    assert summary["per_stream_discarded_measurements"] == {"stream-a": 8}


def test_startup_burst_filter_keeps_spaced_saturated_samples():
    benchmark = _benchmark()
    records = {
        "stream-a": [
            {"latency": 50.0, "recorded_at": 100.0},
            {"latency": 90.0, "recorded_at": 112.0},
            {"latency": 130.0, "recorded_at": 124.0},
        ]
    }

    filtered, summary = benchmark._filter_startup_burst_latency_records(
        records,
        {"discard_startup_burst_samples": True},
        chunk_size=10,
    )

    assert filtered == {"stream-a": [50.0, 90.0, 130.0]}
    assert summary["discarded_measurements"] == 0
