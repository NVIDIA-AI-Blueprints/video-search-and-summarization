######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
######################################################################################################

"""
Live Streams Benchmark Implementation

Tests maximum concurrent live streams without performance degradation.
"""

import json
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from concurrent.futures import wait
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import sseclient
from base import (
    BenchmarkBase,
    BenchmarkCleanupError,
    BenchmarkResourceUnavailableError,
    build_stream_start_error,
)
from latency_tracker import LatencyTracker
from vlm_api import (
    GENERATE_CAPTIONS_API,
    build_vlm_generation_request,
    merge_vlm_params,
    resolve_vlm_endpoint,
)


def _rtsp_url_for_stream(video_config: Dict, stream_num: int) -> str:
    """Return the RTSP URL to use for a benchmark stream.

    Max-live-stream tests should measure independent stream capacity.
    Configure rtsp_urls_file, rtsp_url_template, or rtsp_urls to use real
    independent RTSP paths. Set unique_rtsp_url_per_stream=false only for
    intentional source reuse tests.
    """
    if not video_config.get("unique_rtsp_url_per_stream", True):
        if "rtsp_url" not in video_config:
            raise ValueError("rtsp_url is required when unique_rtsp_url_per_stream=false")
        return video_config["rtsp_url"]

    rtsp_urls = video_config.get("rtsp_urls")
    if rtsp_urls:
        if not isinstance(rtsp_urls, list) or not rtsp_urls:
            raise ValueError("rtsp_urls must be a non-empty list when provided")
        if stream_num > len(rtsp_urls):
            if video_config.get("allow_rtsp_url_reuse_after_pool_exhausted", False):
                return _reused_rtsp_url(video_config, stream_num, len(rtsp_urls), "rtsp_urls")
            raise ValueError(
                f"Not enough rtsp_urls entries for independent stream {stream_num}; "
                f"only {len(rtsp_urls)} URLs were provided"
            )
        return rtsp_urls[stream_num - 1]

    rtsp_urls_file = video_config.get("rtsp_urls_file")
    if rtsp_urls_file:
        with open(rtsp_urls_file, "r", encoding="utf-8") as url_file:
            rtsp_urls = [line.strip() for line in url_file if line.strip()]
        if not rtsp_urls:
            raise ValueError(f"rtsp_urls_file is empty: {rtsp_urls_file}")
        if stream_num > len(rtsp_urls):
            if video_config.get("allow_rtsp_url_reuse_after_pool_exhausted", False):
                return _reused_rtsp_url(video_config, stream_num, len(rtsp_urls), "rtsp_urls_file")
            raise ValueError(
                f"Not enough rtsp_urls_file entries for independent stream {stream_num}; "
                f"only {len(rtsp_urls)} URLs were provided in {rtsp_urls_file}"
            )
        return rtsp_urls[stream_num - 1]

    rtsp_url_template = video_config.get("rtsp_url_template")
    if rtsp_url_template:
        return rtsp_url_template.format(stream_num=stream_num, stream_index=stream_num - 1)

    raise ValueError(
        "unique_rtsp_url_per_stream is enabled, but no rtsp_urls_file, "
        "rtsp_url_template, or rtsp_urls was provided. Set "
        "unique_rtsp_url_per_stream=false only for intentional source reuse tests."
    )


def _reused_rtsp_url(
    video_config: Dict, stream_num: int, source_count: int, source_name: str
) -> str:
    raise ValueError(
        f"{source_name} only provides {source_count} URL(s), but independent "
        f"stream {stream_num} requires a distinct RTSP URL. Add more aliases "
        "to the configured source pool or set unique_rtsp_url_per_stream=false "
        "for an explicit source-reuse/subscriber test."
    )


def _rtsp_url_source_count(video_config: Dict) -> Optional[int]:
    if not video_config.get("unique_rtsp_url_per_stream", True):
        # Intentional source reuse is not bounded by the number of physical
        # URLs. Each logical stream still receives a unique asset ID.
        return None

    rtsp_urls = video_config.get("rtsp_urls")
    if rtsp_urls:
        return len(rtsp_urls)

    rtsp_urls_file = video_config.get("rtsp_urls_file")
    if rtsp_urls_file:
        with open(rtsp_urls_file, "r", encoding="utf-8") as url_file:
            return len([line for line in url_file if line.strip()])

    if video_config.get("rtsp_url_template"):
        return None

    return None


def _rtsp_reuse_summary(video_config: Dict, stream_count: int) -> Dict[str, Any]:
    unique_mode = bool(video_config.get("unique_rtsp_url_per_stream", True))
    source_count = (
        _rtsp_url_source_count(video_config)
        if unique_mode
        else (1 if video_config.get("rtsp_url") else None)
    )
    reuse_count = 0 if unique_mode else max(0, stream_count - 1)
    exhausted_at_stream = None
    caveat = ""

    if unique_mode and source_count is not None and stream_count > source_count:
        exhausted_at_stream = source_count + 1
        caveat = (
            f"RTSP source pool exhausted at stream {exhausted_at_stream}; "
            "independent stream benchmarks require a distinct RTSP URL for each "
            "stream and must stop instead of reusing a fallback rtsp_url."
        )
    elif not unique_mode and reuse_count:
        caveat = (
            f"{stream_count} logical stream assets intentionally reuse one RTSP URL; "
            "asset and caption request IDs remain distinct, but source content is shared."
        )

    return {
        "unique_rtsp_url_per_stream": unique_mode,
        "rtsp_url_source_count": source_count,
        "rtsp_url_pool_exhausted": exhausted_at_stream is not None,
        "rtsp_url_pool_exhausted_at_stream": exhausted_at_stream,
        "rtsp_url_reuse_count": reuse_count,
        "rtsp_url_reuse_caveat": caveat,
    }


def _stream_payload_for_stream(video_config: Dict, stream_num: int, rtsp_url: str) -> Dict:
    stream = {
        "liveStreamUrl": rtsp_url,
        "description": f"Test stream {stream_num}",
    }
    if video_config.get("force_unique_benchmark_stream_ids", True):
        stream_id = str(uuid.uuid4())
        prefix = video_config.get("benchmark_sensor_name_prefix", "rtvi-perf")
        stream["id"] = stream_id
        stream["sensor_name"] = f"{prefix}-{stream_num}-{stream_id[:8]}"
    return stream


def _rtsp_source_config_for_results(video_config: Dict) -> Dict[str, Any]:
    """Return the configured RTSP source while preserving the legacy result field."""
    source_config = {"rtsp_url": video_config.get("rtsp_url", "")}
    for key in (
        "rtsp_urls",
        "rtsp_urls_file",
        "rtsp_url_template",
        "allow_rtsp_url_reuse_after_pool_exhausted",
        "force_unique_benchmark_stream_ids",
    ):
        if key in video_config:
            source_config[key] = video_config[key]
    return source_config


class LiveStreamsBenchmark(BenchmarkBase):
    """Live streams benchmark - test maximum sustainable concurrent streams"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.latency_tracker = LatencyTracker()
        # Per-stream-add API call latency (seconds), populated by _add_live_stream()
        self.stream_add_latencies: List[float] = []
        # Latency snapshots taken every N streams; list of dicts {stream_count, stats}
        self.latency_snapshots: List[Dict[str, Any]] = []
        self._stream_integrity_lock = threading.Lock()
        self._stream_last_chunk_ids: Dict[str, int] = {}
        self._stream_dropped_chunks: Dict[str, int] = {}
        self._added_stream_ids: set[str] = set()

    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Dict[str, Any]:
        """Parse live streams benchmark configuration"""
        if scenario_config.get("benchmark_mode") != "max_live_streams":
            raise ValueError(f"Invalid benchmark mode: {scenario_config.get('benchmark_mode')}")

        if "videos" not in scenario_config:
            raise ValueError("Missing 'videos' field in scenario config")

        # Validate video configurations
        for i, video in enumerate(scenario_config["videos"]):
            if not any(
                key in video
                for key in ("rtsp_url", "rtsp_urls", "rtsp_urls_file", "rtsp_url_template")
            ):
                raise ValueError(
                    f"Missing RTSP source in video {i}; provide one of rtsp_url, "
                    "rtsp_urls, rtsp_urls_file, or rtsp_url_template"
                )
            if "chunk_sizes" not in video:
                raise ValueError(f"Missing 'chunk_sizes' in video {i}")
            if "latency_threshold_seconds" not in video:
                raise ValueError(f"Missing 'latency_threshold_seconds' in video {i}")

        # Check backend type
        backend_type = global_config.get("backend_type", "rtvi_vlm")

        # Merge scenario-level API params with global based on backend type
        if backend_type == "rtvi_embed":
            api_params = self._merge_with_defaults(
                scenario_config.get("generate_video_embeddings_params", {}),
                global_config.get("generate_video_embeddings_params", {}),
            )
        else:
            api_params = self._merge_with_defaults(
                scenario_config.get("generate_captions_params", {}),
                global_config.get("generate_captions_params", {}),
            )

        return {
            "videos": scenario_config["videos"],
            "api_params": api_params,
            "backend_type": backend_type,
            "vlm_api_mode": scenario_config.get(
                "vlm_api_mode",
                global_config.get("vlm_api_mode"),
            ),
            "chat_completions_params": self._merge_with_defaults(
                scenario_config.get("chat_completions_params", {}),
                global_config.get("chat_completions_params", {}),
            ),
            "chat_completions_params_inherited": "chat_completions_params" not in scenario_config,
            "chat_completions_endpoint": scenario_config.get(
                "chat_completions_endpoint",
                global_config.get("chat_completions_endpoint"),
            ),
            "generate_captions_endpoint": scenario_config.get(
                "generate_captions_endpoint",
                global_config.get("generate_captions_endpoint"),
            ),
            "latency_measurement_source": scenario_config.get(
                "latency_measurement_source",
                global_config.get("latency_measurement_source", "ntp_timestamp"),
            ),
            "prompt": global_config.get("prompt", ""),
            "system_prompt": global_config.get("system_prompt", ""),
        }

    def _resolve_generate_captions_endpoint(
        self, video_config: Dict, benchmark_config: Dict
    ) -> str:
        return resolve_vlm_endpoint(video_config, benchmark_config, GENERATE_CAPTIONS_API)

    def _reset_stream_integrity_tracking(self) -> None:
        """Clear per-stream chunk continuity state for a fresh measurement window."""
        with self._stream_integrity_lock:
            self._stream_last_chunk_ids.clear()
            self._stream_dropped_chunks.clear()

    def _record_stream_chunk(self, stream_id: str, chunk_id: Any) -> None:
        """Record chunk IDs and count gaps as dropped chunks for BCD max-stream checks."""
        try:
            current_chunk_id = int(chunk_id)
        except (TypeError, ValueError):
            return

        with self._stream_integrity_lock:
            previous_chunk_id = self._stream_last_chunk_ids.get(stream_id)
            if previous_chunk_id is not None and current_chunk_id > previous_chunk_id + 1:
                dropped_chunks = current_chunk_id - previous_chunk_id - 1
                self._stream_dropped_chunks[stream_id] = (
                    self._stream_dropped_chunks.get(stream_id, 0) + dropped_chunks
                )
            if previous_chunk_id is None or current_chunk_id > previous_chunk_id:
                self._stream_last_chunk_ids[stream_id] = current_chunk_id

    def _record_response_chunks(self, stream_id: str, result: Dict[str, Any]) -> None:
        """Record chunk IDs from generate_captions and chat-completions stream events."""
        for chunk_response in result.get("chunk_responses") or []:
            if isinstance(chunk_response, dict):
                self._record_stream_chunk(stream_id, chunk_response.get("chunk_id"))

        if "chunk_id" in result:
            self._record_stream_chunk(stream_id, result.get("chunk_id"))

    def _get_stream_drop_counts(self) -> Dict[str, int]:
        """Return cumulative dropped chunk counts per stream."""
        with self._stream_integrity_lock:
            return dict(self._stream_dropped_chunks)

    def _get_new_stream_drops(
        self, baseline_counts: Dict[str, int], active_stream_ids: List[str]
    ) -> Dict[str, Any]:
        """Return dropped chunk deltas since a window baseline."""
        current_counts = self._get_stream_drop_counts()
        per_stream_dropped = {
            stream_id: current_counts.get(stream_id, 0) - baseline_counts.get(stream_id, 0)
            for stream_id in active_stream_ids
            if current_counts.get(stream_id, 0) - baseline_counts.get(stream_id, 0) > 0
        }
        return {
            "total_dropped_chunks": sum(per_stream_dropped.values()),
            "streams_with_drops": len(per_stream_dropped),
            "per_stream_dropped_chunks": per_stream_dropped,
        }

    def _extract_live_stream_latency_seconds(
        self, result: Dict[str, Any], latency_measurement_source: str = "ntp_timestamp"
    ) -> Tuple[Optional[float], str]:
        """Extract the latency value used for max-stream stability.

        BCD live-stream latency is measured from the last frame NTP timestamp until captions are
        emitted, so timestamp latency is the default. Server-side chunk timing is kept as an
        opt-in diagnostic source for experiments.
        """
        source = str(latency_measurement_source or "ntp_timestamp").strip().lower()
        if source in {"ntp", "ntp_timestamp", "timestamp", "media_info.end_timestamp"}:
            timestamp_latency = self._extract_ntp_timestamp_latency_seconds(result)
            if timestamp_latency[0] is not None:
                return timestamp_latency
            return self._extract_server_processing_latency_seconds(result)

        if source in {"processing", "processing_latency", "processing_latency_s"}:
            processing_latency = self._extract_server_processing_latency_seconds(result)
            if processing_latency[0] is not None:
                return processing_latency
            return self._extract_ntp_timestamp_latency_seconds(result)

        self.logger.warning(
            f"Unknown latency_measurement_source={latency_measurement_source!r}; "
            "falling back to ntp_timestamp"
        )
        timestamp_latency = self._extract_ntp_timestamp_latency_seconds(result)
        if timestamp_latency[0] is not None:
            return timestamp_latency
        return self._extract_server_processing_latency_seconds(result)

    def _extract_server_processing_latency_seconds(
        self, result: Dict[str, Any]
    ) -> Tuple[Optional[float], str]:
        chunk_responses = result.get("chunk_responses") or []
        if chunk_responses and isinstance(chunk_responses[0], dict):
            first_chunk = chunk_responses[0]
            latency = self._coerce_positive_float(first_chunk.get("processing_latency_s"))
            if latency is not None:
                return latency, "processing_latency_s"
            chunk_latency_ms = self._coerce_positive_float(first_chunk.get("chunk_latency_ms"))
            if chunk_latency_ms is not None:
                return chunk_latency_ms / 1000.0, "chunk_latency_ms"

        return None, ""

    def _extract_ntp_timestamp_latency_seconds(
        self, result: Dict[str, Any]
    ) -> Tuple[Optional[float], str]:
        media_info = result.get("media_info")
        if not isinstance(media_info, dict):
            return None, ""
        if media_info.get("type") == "timestamp":
            end_timestamp = media_info["end_timestamp"]
            dt = datetime.strptime(end_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=timezone.utc
            )
            current_time = datetime.now(timezone.utc)
            latency = (current_time - dt).total_seconds()
            if latency < 0:
                self.logger.warning(
                    "Negative timestamp latency %.2fs from media_info.end_timestamp; "
                    "clamping to 0.00s for reporting. Check RTSP/NTP clock alignment.",
                    latency,
                )
                latency = 0.0
            return latency, "media_info.end_timestamp"

        return None, ""

    @staticmethod
    def _coerce_positive_float(value: Any) -> Optional[float]:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return None
        if numeric_value < 0:
            return None
        return numeric_value

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off", ""}
        return bool(value)

    def _get_latency_growth_instability_config(self, video_config: Dict) -> Tuple[bool, float]:
        enabled = self._as_bool(video_config.get("enable_latency_growth_instability_check", False))
        ratio_threshold = self._coerce_positive_float(
            video_config.get("latency_growth_ratio_threshold", 1.25)
        )
        if ratio_threshold is None or ratio_threshold <= 1.0:
            ratio_threshold = 1.25
        return enabled, ratio_threshold

    @staticmethod
    def _has_consecutive_latency_growth(
        recent_moving_avg_history: List[float], ratio_threshold: float
    ) -> bool:
        if len(recent_moving_avg_history) < 3:
            return False

        previous, current, latest = recent_moving_avg_history[-3:]
        if previous <= 0 or current <= 0:
            return False

        return current / previous > ratio_threshold and latest / current > ratio_threshold

    def _get_current_gpu_monitor_usage(self) -> Tuple[Optional[float], Optional[float], str]:
        """Return current GPU/NVDEC usage, preferring DCGM over legacy in-process samples."""
        if self.prometheus_gpu_collector and self.prometheus_collector_active:
            latest_stats = self.prometheus_gpu_collector.get_latest_stats()
            gpu_usage = latest_stats.get("gpu_usage")
            nvdec_usage = latest_stats.get("nvdec_usage")
            if gpu_usage is not None or nvdec_usage is not None:
                return gpu_usage, nvdec_usage, "DCGM"

        if self.gpu_monitor and self.gpu_monitor.supported_gpu_ids:
            return (
                self.gpu_monitor.get_gpu_usage(),
                self.gpu_monitor.get_nvdec_usage(),
                "NVML",
            )

        return None, None, "N/A"

    def execute(self, config: Dict, scenario_name: str) -> Dict[str, Any]:
        """Execute live streams benchmark"""
        self.logger.info(f"Starting live streams benchmark: {scenario_name}")

        global_config = self.parse_global_config(config)
        benchmark_config = self.parse_benchmark_config(
            config["test_scenarios"][scenario_name], global_config
        )

        scenario_dir = self.setup_scenario_directory(scenario_name)
        model_name = self.get_available_models()

        execution_results = {
            "scenario_name": scenario_name,
            "benchmark_mode": "max_live_streams",
            "scenario_dir": scenario_dir,
            "test_cases": [],
            "total_test_cases": 0,
            "successful_test_cases": 0,
            "failed_test_cases": 0,
        }

        # Execute test cases for each video and chunk size
        for video_config in benchmark_config["videos"]:
            for chunk_size in video_config["chunk_sizes"]:
                test_case_id = self._generate_test_case_id(video_config, chunk_size)
                execution_results["total_test_cases"] += 1

                try:
                    test_result = self._execute_live_streams_test_case(
                        test_case_id,
                        video_config,
                        chunk_size,
                        benchmark_config,
                        model_name,
                        scenario_dir,
                    )
                    execution_results["test_cases"].append(test_result)
                    if test_result.get("success", False):
                        execution_results["successful_test_cases"] += 1
                        self.logger.info(f"Test case {test_case_id} completed successfully")
                    else:
                        execution_results["failed_test_cases"] += 1
                        self.logger.error(
                            f"Test case {test_case_id} completed without sustainable streams"
                        )

                except BenchmarkCleanupError:
                    raise
                except BenchmarkResourceUnavailableError as e:
                    self.logger.warning(f"Test case {test_case_id} is resource-bound: {e}")
                    execution_results["failed_test_cases"] += 1
                    execution_results["test_cases"].append(
                        {
                            "test_case_id": test_case_id,
                            "success": False,
                            "resource_bound": True,
                            "status_code": e.status_code,
                            "code": e.code,
                            "error": str(e),
                        }
                    )
                except Exception as e:
                    self.logger.error(f"Test case {test_case_id} failed: {e}")
                    execution_results["failed_test_cases"] += 1
                    execution_results["test_cases"].append(
                        {"test_case_id": test_case_id, "success": False, "error": str(e)}
                    )

        # Save execution summary
        summary_file = os.path.join(scenario_dir, "execution_summary.json")
        self.save_json_data(self.round_floats(execution_results), summary_file)

        self.logger.info(
            f"Live streams benchmark completed: {execution_results['successful_test_cases']}/"
            f"{execution_results['total_test_cases']} test cases successful"
        )

        return execution_results

    def _generate_test_case_id(self, video_config: Dict, chunk_size: int) -> str:
        """Generate unique test case ID"""
        stream_name = video_config.get("name", "live_stream")
        return f"max_live_streams_{stream_name}_{chunk_size}sec"

    def _execute_live_streams_test_case(
        self,
        test_case_id: str,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        scenario_dir: str,
    ) -> Dict[str, Any]:
        """Execute live streams test case - gradually increase streams until degradation"""
        self.logger.info(f"Starting max live streams test: {test_case_id}")

        test_case_dir = os.path.join(scenario_dir, test_case_id)
        os.makedirs(test_case_dir, exist_ok=True)

        # Configure connection pool for max live streams
        self._configure_http_session(300)

        # Start GPU monitoring
        self.start_gpu_monitoring()

        # Start CPU/memory monitoring (no-op if not configured)
        self.start_cpu_monitoring()

        # Clear latency tracker and per-test accumulators
        self.latency_tracker.clear(reset_ignored=True)
        self.stream_add_latencies.clear()
        self.latency_snapshots.clear()
        self._reset_stream_integrity_tracking()

        # Captured after the active stream count is reached so decode deltas
        # exclude stream-add ramp time where possible.
        start_metrics: Dict[str, Any] = {}

        active_futures = []
        active_stream_ids = []
        executor = None
        degradation_detected = False
        binary_search_applied = False
        phase2_stable_start = None
        phase2_unstable_ceiling = None
        start_time = time.time()
        interrupted = False
        cpu_stats: Dict[str, Any] = {}
        stream_add_failure = ""
        stream_add_failure_stream_count = 0
        skipped_rtsp_sources: List[Dict[str, Any]] = []
        # Snapshot latency stats every this many streams
        latency_snapshot_interval = video_config.get("latency_snapshot_interval", 20)

        initial_stream_count = video_config.get("initial_stream_count", 5)
        if initial_stream_count <= 0:
            raise ValueError(f"initial_stream_count must be > 0, got {initial_stream_count!r}")
        latency_threshold = video_config["latency_threshold_seconds"]

        try:
            self.logger.info(f"Starting with {initial_stream_count} initial streams...")

            executor = ThreadPoolExecutor(max_workers=300)

            # Start initial streams, spread over one chunk window at steady state rate so VLM requests
            # arrive uniformly at (chunk_size / initial_stream_count) req/sec.
            initial_inter_stream_delay = 1 + (chunk_size / initial_stream_count)
            self.logger.info(
                f"Adding {initial_stream_count} initial streams with "
                f"{initial_inter_stream_delay:.2f}s spacing "
                f"(target rate: {initial_stream_count / chunk_size:.2f} req/s)"
            )
            next_rtsp_source_num = 1
            for stream_num in range(1, initial_stream_count + 1):
                stream_id = None
                try:
                    stream_id, next_rtsp_source_num = self._add_live_stream_with_retries(
                        video_config,
                        stream_num,
                        next_rtsp_source_num,
                        skipped_rtsp_sources,
                    )
                except Exception as exc:
                    stream_add_failure = str(exc)
                    stream_add_failure_stream_count = stream_num
                    self.logger.warning(
                        f"Initial stream add failed at {stream_num}; "
                        f"continuing with {len(active_stream_ids)} stream(s) "
                        f"for stability validation: {exc}"
                    )
                if stream_id:
                    active_stream_ids.append(stream_id)
                    self.active_resources.append(f"stream_{stream_id}")
                    future = self._start_stream_monitoring(
                        executor,
                        video_config,
                        chunk_size,
                        benchmark_config,
                        model_name,
                        stream_id,
                        stream_num,
                    )
                    active_futures.append(future)
                    self.logger.info(f"Launched stream {stream_num}")
                    time.sleep(initial_inter_stream_delay)
                else:
                    self.logger.error(f"Failed to create initial stream {stream_num}")
                    break

            # Monitor and gradually add more streams
            last_check_time = time.time()
            stability_check_interval = video_config.get("stability_check_interval", 60)
            current_stream_count = len(active_stream_ids)
            self.logger.debug(f"Using stability check interval: {stability_check_interval} seconds")

            # BCD steady-state validation starts after all initial streams are active.
            self.latency_tracker.clear(reset_ignored=True)
            self._reset_stream_integrity_tracking()
            self.reset_gpu_monitoring()
            self.reset_prometheus_collectors()
            self.reset_cpu_monitoring()
            self.reset_pipeline_stage_samples()
            start_metrics = self.scrape_metrics()

            # Consecutive window counters for robust stability detection
            consecutive_stable_windows = 0
            consecutive_unstable_windows = 0
            required_stable_windows = video_config.get("required_stable_windows", 3)
            required_unstable_windows = video_config.get("required_unstable_windows", 2)
            min_stable_stream_coverage = float(video_config.get("min_stable_stream_coverage", 0.9))
            min_fresh_measurements = int(video_config.get("min_fresh_measurements_per_stream", 1))
            require_no_dropped_chunks = self._as_bool(
                video_config.get("require_no_dropped_chunks", True)
            )
            (
                latency_growth_instability_enabled,
                latency_growth_ratio_threshold,
            ) = self._get_latency_growth_instability_config(video_config)
            fresh_latency_timeout = float(
                video_config.get(
                    "fresh_latency_timeout_seconds",
                    stability_check_interval
                    * max(required_stable_windows + required_unstable_windows, 1),
                )
            )
            window_measurement_baseline = self.latency_tracker.get_stream_measurement_counts()
            # Keep this baseline fixed for the current stream-count validation period.
            # BCD max-streams requires no dropped chunks, so a drop in any stability
            # window must remain visible until we either add streams or end the probe.
            window_drop_baseline = self._get_stream_drop_counts()
            freshness_wait_started_at = None

            # Track recent moving average history for ratio-based instability detection
            recent_moving_avg_history = []
            max_history_length = 3

            # State tracking
            last_stable_stream_count = 0
            last_stable_moving_average_latency = 0
            last_stable_p50 = 0.0
            last_stable_p75 = 0.0
            last_stable_p90 = 0.0
            last_stable_p95 = 0
            last_stable_p99 = 0.0
            last_stable_max_latency = 0
            last_stable_resource_metrics: Dict[str, Any] = {}
            first_unstable_stream_count = 0

            self.logger.info(
                f"Stability criteria: {required_stable_windows} consecutive stable windows "
                f"required to add streams, {required_unstable_windows} consecutive unstable "
                f"windows required to declare degradation; fresh stream coverage >= "
                f"{min_stable_stream_coverage:.0%} with >= {min_fresh_measurements} "
                f"new measurement(s)/stream; wait up to {fresh_latency_timeout:.0f}s "
                f"for fresh responses before counting a low-coverage window as unstable; "
                f"require no dropped chunks: {require_no_dropped_chunks}; "
                f"latency growth instability check: "
                f"{'enabled' if latency_growth_instability_enabled else 'disabled'}"
            )
            while not degradation_detected and not interrupted and current_stream_count < 300:
                try:
                    current_time = time.time()
                    if current_time - last_check_time >= stability_check_interval:
                        recent_stats = self.latency_tracker.get_stats()
                        avg_latency = recent_stats.get("avg_latency", 0)
                        moving_average_latency = recent_stats.get("moving_average_latency", 0)
                        recent_max_latency = recent_stats.get("max_latency", 0)

                        # Check stability using p95 of recent readings
                        recent_p95 = self.latency_tracker.get_recent_p95()
                        is_stable = self.latency_tracker.is_stable(latency_threshold)
                        current_measurement_counts = (
                            self.latency_tracker.get_stream_measurement_counts()
                        )
                        freshness = self.latency_tracker.get_fresh_stream_coverage(
                            window_measurement_baseline,
                            active_stream_ids,
                            min_fresh_measurements,
                        )
                        active_streams = freshness["active_streams"]
                        fresh_streams = freshness["fresh_streams"]
                        fresh_coverage = freshness["coverage"]
                        freshness_pending = False
                        freshness_wait_elapsed = 0.0
                        if active_streams and fresh_coverage < min_stable_stream_coverage:
                            is_stable = False
                            if freshness_wait_started_at is None:
                                freshness_wait_started_at = current_time
                            freshness_wait_elapsed = current_time - freshness_wait_started_at
                            freshness_pending = freshness_wait_elapsed < fresh_latency_timeout
                            freshness_wait_status = (
                                "waiting for queued responses"
                                if freshness_pending
                                else "freshness wait timed out"
                            )
                            self.logger.warning(
                                f"Fresh latency coverage below threshold at "
                                f"{current_stream_count} streams: "
                                f"{fresh_streams}/{active_streams} streams "
                                f"({fresh_coverage:.1%} < {min_stable_stream_coverage:.1%}) "
                                f"produced >= {min_fresh_measurements} new measurement(s); "
                                f"{freshness_wait_status} "
                                f"({freshness_wait_elapsed:.0f}s/{fresh_latency_timeout:.0f}s)"
                            )
                        else:
                            freshness_wait_started_at = None

                        stream_drops = self._get_new_stream_drops(
                            window_drop_baseline, active_stream_ids
                        )
                        if require_no_dropped_chunks and stream_drops["total_dropped_chunks"] > 0:
                            is_stable = False
                            self.logger.warning(
                                f"Dropped chunk continuity detected at {current_stream_count} "
                                f"streams: {stream_drops['total_dropped_chunks']} chunk(s) across "
                                f"{stream_drops['streams_with_drops']} stream(s)"
                            )

                        # Track moving average history for ratio-based instability detection
                        if latency_growth_instability_enabled:
                            recent_moving_avg_history.append(moving_average_latency)
                            if len(recent_moving_avg_history) > max_history_length:
                                recent_moving_avg_history.pop(0)

                            if self._has_consecutive_latency_growth(
                                recent_moving_avg_history, latency_growth_ratio_threshold
                            ):
                                ratio_1_to_0 = (
                                    recent_moving_avg_history[1] / recent_moving_avg_history[0]
                                )
                                ratio_2_to_1 = (
                                    recent_moving_avg_history[2] / recent_moving_avg_history[1]
                                )
                                is_stable = False
                                self.logger.warning(
                                    f"Detected consecutive latency increases above "
                                    f"{latency_growth_ratio_threshold:.2f}x: "
                                    f"{recent_moving_avg_history[0]:.2f}s -> "
                                    f"{recent_moving_avg_history[1]:.2f}s -> "
                                    f"{recent_moving_avg_history[2]:.2f}s "
                                    f"(ratios: {ratio_1_to_0:.2f}x, {ratio_2_to_1:.2f}x)"
                                )

                        # Update consecutive window counters based on stability
                        if freshness_pending:
                            consecutive_stable_windows = 0
                            consecutive_unstable_windows = 0
                            stability_status = (
                                f"Waiting for fresh responses "
                                f"({freshness_wait_elapsed:.0f}/{fresh_latency_timeout:.0f}s)"
                            )
                        elif is_stable:
                            consecutive_stable_windows += 1
                            consecutive_unstable_windows = 0  # Reset unstable counter
                            stability_status = (
                                f"Stable ({consecutive_stable_windows}/{required_stable_windows})"
                            )
                        else:
                            consecutive_unstable_windows += 1
                            consecutive_stable_windows = 0  # Reset stable counter
                            stability_status = (
                                f"Unstable ({consecutive_unstable_windows}/"
                                f"{required_unstable_windows})"
                            )

                        gpu_usage, nvdec_usage, gpu_metric_source = (
                            self._get_current_gpu_monitor_usage()
                        )
                        gpu_usage_str = "N/A" if gpu_usage is None else f"{gpu_usage:.2f}"
                        nvdec_usage_str = "N/A" if nvdec_usage is None else f"{nvdec_usage:.2f}"

                        self.logger.info(
                            f"Stability check - Current streams: {current_stream_count}, "
                            f"Avg latency: {avg_latency:.2f}s, "
                            f"Moving average latency: {moving_average_latency:.2f}s, "
                            f"Recent P95: {recent_p95:.2f}s, "
                            f"Fresh streams: {fresh_streams}/{active_streams} "
                            f"({fresh_coverage:.1%}), "
                            f"Dropped chunks: {stream_drops['total_dropped_chunks']}, "
                            f"GPU latest % ({gpu_metric_source}): {gpu_usage_str}, "
                            f"NVDEC latest % ({gpu_metric_source}): {nvdec_usage_str}, "
                            f"Status: {stability_status}"
                        )
                        self.logger.debug(f"Latency tracker stats: {recent_stats}")

                        # Print per-stream statistics
                        per_stream_stats_str = self.latency_tracker.get_per_stream_stats_str()
                        if per_stream_stats_str:
                            self.logger.info("Per-stream statistics:")
                            for line in per_stream_stats_str.split("\n"):
                                self.logger.info(line)

                        # Snapshot per-stream video-processing latency every N streams
                        last_snapshot_count = (
                            self.latency_snapshots[-1]["stream_count"]
                            if self.latency_snapshots
                            else 0
                        )
                        if current_stream_count - last_snapshot_count >= latency_snapshot_interval:
                            snapshot = {
                                "stream_count": current_stream_count,
                                "avg_latency": avg_latency,
                                "moving_average_latency": moving_average_latency,
                                "p95_latency": recent_p95,
                                "max_latency": recent_max_latency,
                                "dropped_chunks": stream_drops["total_dropped_chunks"],
                                "gpu_usage_pct": gpu_usage,
                                "nvdec_usage_pct": nvdec_usage,
                                "gpu_metric_source": gpu_metric_source,
                                **self.get_cpu_stats(),
                            }
                            self.latency_snapshots.append(snapshot)
                            self.logger.info(
                                f"Latency snapshot @ {current_stream_count} streams: "
                                f"avg={avg_latency:.2f}s, p95={recent_p95:.2f}s, "
                                f"moving_avg={moving_average_latency:.2f}s"
                            )

                        if (
                            not is_stable
                            and consecutive_unstable_windows >= required_unstable_windows
                        ):
                            threshold_msg = (
                                f"exceeds threshold: {latency_threshold:.2f}s"
                                if recent_p95 > latency_threshold
                                else ""
                            )
                            self.logger.info(
                                f"System degradation confirmed after {consecutive_unstable_windows} "
                                f"consecutive unstable windows with {current_stream_count} streams. "
                                f"Recent P95 latency: {recent_p95:.2f}s {threshold_msg}"
                            )
                            first_unstable_stream_count = len(active_stream_ids)
                            degradation_detected = True
                            current_stream_count = last_stable_stream_count
                            self.stop_recording_gpu_usage()
                            break

                        # Only add more streams if we have confirmed stability
                        # (require consecutive stable windows)
                        if consecutive_stable_windows >= required_stable_windows:
                            # Reset counter for next stability check cycle
                            consecutive_stable_windows = 0
                            recent_moving_avg_history = []  # Reset history after adding streams

                            # Save current state as last known stable configuration
                            last_stable_stream_count = current_stream_count
                            last_stable_moving_average_latency = moving_average_latency
                            last_stable_max_latency = recent_max_latency
                            _recent_pcts = self.latency_tracker.get_recent_percentiles()
                            last_stable_p50 = _recent_pcts["p50"]
                            last_stable_p75 = _recent_pcts["p75"]
                            last_stable_p90 = _recent_pcts["p90"]
                            last_stable_p95 = _recent_pcts["p95"]
                            last_stable_p99 = _recent_pcts["p99"]
                            last_stable_resource_metrics = {
                                **self.get_current_prometheus_collector_stats(),
                                **self.get_pipeline_stage_stats(),
                            }

                            # Reset profiling collectors so final GPU/CPU stats reflect
                            # only this stable window, not the full ramp-up history
                            self.reset_prometheus_collectors()
                            self.logger.info(
                                f"Profiling window reset at {current_stream_count} streams "
                                f"(GPU/CPU stats will cover last stable window only)"
                            )

                            # Add more streams, spread over one chunk window so the
                            # new streams join uniformly at the target request rate.
                            add_stream_count = video_config.get("add_stream_count", 1)
                            target_count = current_stream_count + add_stream_count
                            inter_stream_delay = 1 + (chunk_size / target_count)
                            self.logger.info(
                                f"Adding {add_stream_count} stream(s) → target {target_count}, "
                                f"spacing {inter_stream_delay:.2f}s "
                                f"(target rate: {target_count / chunk_size:.2f} req/s)"
                            )
                            for _ in range(add_stream_count):
                                current_stream_count += 1
                                stream_id = None
                                have_error = False
                                try:
                                    stream_id, next_rtsp_source_num = (
                                        self._add_live_stream_with_retries(
                                            video_config,
                                            current_stream_count,
                                            next_rtsp_source_num,
                                            skipped_rtsp_sources,
                                        )
                                    )
                                except Exception as exc:
                                    stream_add_failure = str(exc)
                                    stream_add_failure_stream_count = current_stream_count
                                    self.logger.warning(
                                        f"Stream add failed at {current_stream_count}; "
                                        f"treating {last_stable_stream_count} streams as "
                                        f"the observed capacity boundary: {exc}"
                                    )
                                if stream_id:
                                    active_stream_ids.append(stream_id)
                                    self.active_resources.append(f"stream_{stream_id}")
                                    future = self._start_stream_monitoring(
                                        executor,
                                        video_config,
                                        chunk_size,
                                        benchmark_config,
                                        model_name,
                                        stream_id,
                                        current_stream_count,
                                    )
                                    active_futures.append(future)
                                    time.sleep(inter_stream_delay)
                                    self.logger.info(
                                        f"Adding stream {current_stream_count} (confirmed stable "
                                        f"for {required_stable_windows} consecutive windows)"
                                    )
                                else:
                                    self.logger.error(
                                        f"Failed to create stream {current_stream_count}"
                                    )
                                    first_unstable_stream_count = current_stream_count
                                    current_stream_count -= 1
                                    self.logger.warning("Cannot add more streams, stopping test")
                                    have_error = True
                                    self.stop_recording_gpu_usage()
                                if have_error:
                                    degradation_detected = True
                                    current_stream_count = last_stable_stream_count
                                    break
                            if degradation_detected:
                                break

                            self.logger.info(
                                "Resetting latency and profiling windows after adding stream(s)"
                            )
                            self.latency_tracker.clear(reset_ignored=True)
                            self._reset_stream_integrity_tracking()
                            self.reset_gpu_monitoring()
                            self.reset_prometheus_collectors()
                            self.reset_cpu_monitoring()
                            self.reset_pipeline_stage_samples()
                            window_measurement_baseline = (
                                self.latency_tracker.get_stream_measurement_counts()
                            )
                            window_drop_baseline = self._get_stream_drop_counts()
                        elif freshness_pending:
                            # Keep the baseline while waiting so late responses can accumulate
                            # toward the fresh-coverage threshold instead of being discarded.
                            pass
                        else:
                            window_measurement_baseline = current_measurement_counts

                        last_check_time = current_time
                    else:
                        time.sleep(1)

                except KeyboardInterrupt:
                    interrupted = True
                    break

            phase2_enabled = video_config.get("binary_search_refinement", True)
            # Phase 2 runs in two cases:
            #   (a) Normal: ramp-up found a stable band — search between last_stable and first_unstable.
            #   (b) Initial-too-high: initial_stream_count itself caused degradation
            #       (last_stable_stream_count == 0) — binary search between 0 and first_unstable
            #       to find the true maximum without restarting the test.
            if degradation_detected and phase2_enabled and first_unstable_stream_count > 1:
                if last_stable_stream_count == 0:
                    self.logger.info(
                        f"Initial stream count ({first_unstable_stream_count}) caused immediate "
                        f"degradation — binary searching between 0 and {first_unstable_stream_count} "
                        f"to find the true max stable stream count."
                    )
                binary_search_applied = True
                phase2_stable_start = last_stable_stream_count
                phase2_unstable_ceiling = first_unstable_stream_count
                probe_cooldown = video_config.get("binary_search_probe_cooldown_seconds", 15.0)
                if self.gpu_monitor and not self.gpu_monitoring_active:
                    self.logger.info("Restarting GPU monitoring for Phase 2 probes")
                    self.start_gpu_monitoring()
                self.reset_gpu_monitoring()
                (
                    current_stream_count,
                    phase2_latency_stats,
                    phase2_unstable_ceiling,
                ) = self._binary_search_max_streams(
                    low=last_stable_stream_count,
                    high=first_unstable_stream_count,
                    active_stream_ids=active_stream_ids,
                    active_futures=active_futures,
                    executor=executor,
                    video_config=video_config,
                    chunk_size=chunk_size,
                    benchmark_config=benchmark_config,
                    model_name=model_name,
                    stability_check_interval=stability_check_interval,
                    required_stable_windows=required_stable_windows,
                    required_unstable_windows=required_unstable_windows,
                    latency_threshold=latency_threshold,
                    probe_cooldown=probe_cooldown,
                )
                last_stable_stream_count = current_stream_count
                # Update last_stable_* with Phase 2's final stable measurements.
                # Critical when Phase 1 degraded immediately (last_stable_* = 0) — Phase 2
                # found a valid stable count but the latency stats were never recorded.
                if phase2_latency_stats:
                    last_stable_moving_average_latency = phase2_latency_stats.get(
                        "moving_average_latency", last_stable_moving_average_latency
                    )
                    last_stable_max_latency = phase2_latency_stats.get(
                        "max_latency", last_stable_max_latency
                    )
                    last_stable_p50 = phase2_latency_stats.get("p50", last_stable_p50)
                    last_stable_p75 = phase2_latency_stats.get("p75", last_stable_p75)
                    last_stable_p90 = phase2_latency_stats.get("p90", last_stable_p90)
                    last_stable_p95 = phase2_latency_stats.get("p95", last_stable_p95)
                    last_stable_p99 = phase2_latency_stats.get("p99", last_stable_p99)
                    last_stable_resource_metrics = phase2_latency_stats.get(
                        "resource_metrics", last_stable_resource_metrics
                    )

        except KeyboardInterrupt:
            interrupted = True
        finally:
            # Capture metrics after test to compute average decode latency
            end_metrics = self.scrape_metrics()

            # Stop profiling before stream cleanup so GPU/NVDEC/CPU stats do not
            # include delete/drain and monitor-thread teardown.
            self.stop_gpu_monitoring(
                export_dir=test_case_dir, filename_prefix="gpu_metrics_max_live_streams"
            )

            # Stop CPU monitoring and capture stats before cleanup for the same reason.
            cpu_stats = self.stop_cpu_monitoring()

            # Stop generation first so the server releases active request ownership
            # before asset deletion.
            cleanup_error = None
            self.logger.info(
                f"Stopping live generation for {len(active_stream_ids)} active streams..."
            )
            try:
                self._stop_live_generation_requests(
                    list(active_stream_ids),
                    backend_type=benchmark_config.get("backend_type", "rtvi_vlm"),
                    captions_endpoint=self._resolve_generate_captions_endpoint(
                        video_config, benchmark_config
                    ),
                )
            except Exception as e:
                cleanup_error = e
                self.logger.error(f"Live generation stop failed: {e}")

            blocking_delete = self._blocking_stream_delete_enabled()
            if blocking_delete:
                # Blocking batch delete owns the generation stop. Issue it before
                # waiting for SSE clients, otherwise overloaded streams cannot
                # finish and the wait grows by one timeout per stream.
                self.logger.info(f"Cleaning up {len(active_stream_ids)} active streams...")
                try:
                    self._batch_delete_streams(list(active_stream_ids))
                except Exception as e:
                    cleanup_error = cleanup_error or e
                    self.logger.error(f"Live stream cleanup failed: {e}")

            # Give all monitoring futures one shared completion window.
            self.logger.info(f"Waiting for {len(active_futures)} monitoring threads to complete...")
            completed_futures, unfinished_futures = wait(
                active_futures,
                timeout=self.DEFAULT_THREAD_WAIT_TIMEOUT,
            )
            for future in completed_futures:
                try:
                    future.result()
                except Exception as e:
                    cleanup_error = cleanup_error or e
                    self.logger.error(f"Monitoring thread completion error: {e}")
            if unfinished_futures:
                monitor_error = RuntimeError(
                    f"{len(unfinished_futures)}/{len(active_futures)} monitoring threads "
                    f"did not stop within {self.DEFAULT_THREAD_WAIT_TIMEOUT}s"
                )
                cleanup_error = cleanup_error or monitor_error
                self.logger.error(str(monitor_error))

            if not blocking_delete:
                # Cleanup all streams via batch endpoint. Sequential deletes can
                # spend one full drain timeout per overloaded stream.
                self.logger.info(f"Cleaning up {len(active_stream_ids)} active streams...")
                try:
                    self._batch_delete_streams(list(active_stream_ids))
                except Exception as e:
                    cleanup_error = cleanup_error or e
                    self.logger.error(f"Live stream cleanup failed: {e}")

            if executor is not None:
                executor.shutdown(wait=False)
            if cleanup_error is not None:
                raise BenchmarkCleanupError(
                    f"Live-stream cleanup failed after test case {test_case_id}: {cleanup_error}"
                ) from cleanup_error

        actual_duration = time.time() - start_time

        # Get combined stats
        latency_stats = self.latency_tracker.get_stats()

        # Process GPU stats
        gpu_metrics = {}
        gpu_stats_file = os.path.join(test_case_dir, "gpu_metrics_max_live_streams_stats.json")
        gpu_metrics = self.process_gpu_stats(gpu_stats_file)

        # Calculate max sustainable streams
        max_sustainable = current_stream_count

        latency_history = self.latency_tracker.get_all_latencies()
        stream_drop_counts = self._get_stream_drop_counts()
        total_dropped_chunks = sum(stream_drop_counts.values())

        # Compute average decode latency from /metrics deltas
        start_count = start_metrics.get("decode_latency_seconds_count") if start_metrics else None
        end_count = end_metrics.get("decode_latency_seconds_count") if end_metrics else None
        start_sum = start_metrics.get("decode_latency_seconds_sum") if start_metrics else None
        end_sum = end_metrics.get("decode_latency_seconds_sum") if end_metrics else None
        avg_decode_latency = 0.0
        if end_count is not None and end_sum is not None:
            count_delta = end_count - (start_count or 0)
            sum_delta = end_sum - (start_sum or 0)
            if count_delta > 0 and sum_delta >= 0:
                avg_decode_latency = sum_delta / count_delta
        stable_decode_latency = last_stable_resource_metrics.get("chunk_decode_latency_seconds_avg")
        decode_latency_for_report = (
            stable_decode_latency
            if isinstance(stable_decode_latency, (int, float))
            else avg_decode_latency
        )

        # Compute stream-add API call latency stats
        stream_add_stats: Dict[str, Any] = {}
        if self.stream_add_latencies:
            arr = self.stream_add_latencies
            stream_add_stats = {
                "stream_add_latency_mean": round(float(np.mean(arr)), 3),
                "stream_add_latency_max": round(float(np.max(arr)), 3),
                "stream_add_latency_min": round(float(np.min(arr)), 3),
                "stream_add_latency_p90": round(float(np.percentile(arr, 90)), 3),
                "stream_add_latency_count": len(arr),
                "stream_add_latencies": arr,
            }

        results = {
            "test_case_id": test_case_id,
            "benchmark_mode": "max_live_streams",
            **_rtsp_source_config_for_results(video_config),
            **_rtsp_reuse_summary(video_config, len(active_stream_ids)),
            "chunk_size": chunk_size,
            "initial_stream_count": initial_stream_count,
            "max_sustainable_streams": max_sustainable,
            "degradation_detected": degradation_detected,
            "stream_add_failure": stream_add_failure,
            "stream_add_failure_stream_count": stream_add_failure_stream_count,
            "skipped_rtsp_sources": skipped_rtsp_sources,
            "skipped_rtsp_source_count": len(skipped_rtsp_sources),
            "latency_threshold_seconds": latency_threshold,
            "total_test_duration_seconds": actual_duration,
            "total_streams_tested": current_stream_count,
            "success": max_sustainable > 0,
            "last_stable_stream_count": last_stable_stream_count,
            "first_unstable_stream_count": first_unstable_stream_count,
            "last_stable_moving_average_latency": last_stable_moving_average_latency,
            "last_stable_p50": last_stable_p50,
            "last_stable_p75": last_stable_p75,
            "last_stable_p90": last_stable_p90,
            "last_stable_p95": last_stable_p95,
            "last_stable_p99": last_stable_p99,
            "last_stable_max_latency": last_stable_max_latency,
            **latency_stats,
            **gpu_metrics,
            **stream_add_stats,
            **cpu_stats,
            **last_stable_resource_metrics,
            "decode_latency_seconds_count_start": start_count or 0,
            "decode_latency_seconds_count_end": end_count or 0,
            "decode_latency_seconds_sum_start": start_sum or 0,
            "decode_latency_seconds_sum_end": end_sum or 0,
            "decode_latency_seconds_avg": decode_latency_for_report,
            "latency_history": latency_history,
            "latency_snapshots": self.latency_snapshots,
            "backend_type": benchmark_config.get("backend_type", "rtvi_vlm"),
            "phase2_binary_search_applied": binary_search_applied,
            "phase2_stable_start": phase2_stable_start,
            "phase2_final_stable": current_stream_count if binary_search_applied else None,
            "phase2_unstable_ceiling": phase2_unstable_ceiling,
            "total_dropped_chunks": total_dropped_chunks,
            "stream_dropped_chunks": stream_drop_counts,
        }

        # Save results
        results_file = os.path.join(test_case_dir, "max_live_streams_results.json")
        self.save_json_data(self.round_floats(results), results_file)

        self.logger.info(f"Max live streams test completed: {max_sustainable} sustainable streams ")

        return results

    def _add_live_stream_with_retries(
        self,
        video_config: Dict,
        stream_num: int,
        rtsp_source_num: int,
        skipped_rtsp_sources: List[Dict[str, Any]],
    ) -> Tuple[str, int]:
        """Add a live stream, retrying transient failures and skipping bad RTSP sources."""
        max_attempts = max(1, int(video_config.get("stream_add_retry_attempts", 3)))
        max_skips = max(0, int(video_config.get("stream_add_max_rtsp_source_skips", 5)))
        if not video_config.get("unique_rtsp_url_per_stream", True):
            # Advancing a logical source number still resolves to the same URL
            # in reuse mode, so source skipping cannot recover an add failure.
            max_skips = 0
        retry_delay = max(0.0, float(video_config.get("stream_add_retry_delay_seconds", 2.0)))
        source_count = _rtsp_url_source_count(video_config)
        skipped_this_call = 0
        last_error = None

        while source_count is None or rtsp_source_num <= source_count:
            for attempt in range(1, max_attempts + 1):
                try:
                    stream_id = self._add_live_stream(video_config, rtsp_source_num)
                    if rtsp_source_num != stream_num:
                        self.logger.info(
                            f"Logical stream {stream_num} using RTSP source "
                            f"{rtsp_source_num} after skipped source(s)"
                        )
                    return stream_id, rtsp_source_num + 1
                except BenchmarkResourceUnavailableError:
                    raise
                except Exception as exc:
                    last_error = exc
                    self.logger.warning(
                        f"Failed to add logical stream {stream_num} from RTSP source "
                        f"{rtsp_source_num} (attempt {attempt}/{max_attempts}): {exc}"
                    )
                    if attempt < max_attempts and retry_delay > 0:
                        time.sleep(retry_delay)

            skipped_rtsp_sources.append(
                {
                    "logical_stream_num": stream_num,
                    "rtsp_source_num": rtsp_source_num,
                    "error": str(last_error),
                    "attempts": max_attempts,
                }
            )
            skipped_this_call += 1
            if skipped_this_call > max_skips:
                raise Exception(
                    f"Failed to add live stream {stream_num} after skipping "
                    f"{max_skips} RTSP source(s); last error: {last_error}"
                )
            self.logger.warning(
                f"Skipping RTSP source {rtsp_source_num} for logical stream "
                f"{stream_num}; trying next RTSP source"
            )
            rtsp_source_num += 1

        raise ValueError(
            f"Not enough valid RTSP sources to create logical stream {stream_num}; "
            f"source_count={source_count}, skipped={skipped_this_call}, "
            f"last_error={last_error}"
        )

    def _add_live_stream(self, video_config: Dict, stream_num: int) -> str:
        """Add a live stream for testing"""
        rtsp_url = _rtsp_url_for_stream(video_config, stream_num)
        if not rtsp_url.startswith("rtsp://"):
            raise ValueError(
                f"rtsp_url does not start with 'rtsp://': {rtsp_url!r}. "
                "Run setup_perf_env.sh to replace the RTSP_STREAM_URL placeholder."
            )
        request_data = {"streams": [_stream_payload_for_stream(video_config, stream_num, rtsp_url)]}

        self.logger.debug(f"Adding stream {stream_num}: liveStreamUrl={rtsp_url!r}")

        t0 = time.time()
        try:
            response = self.make_api_call("/streams/add", method="POST", data=request_data)
        except requests.exceptions.HTTPError as exc:
            rejected_response = getattr(exc, "response", None)
            if rejected_response is not None:
                error = build_stream_start_error(rejected_response, "/streams/add", stream_num)
                if isinstance(error, BenchmarkResourceUnavailableError):
                    raise error from exc
            raise
        add_latency = time.time() - t0
        self.stream_add_latencies.append(add_latency)
        self.logger.debug(f"Stream {stream_num} add API latency: {add_latency:.3f}s")

        result_json = response.json()
        if result_json.get("errors") and len(result_json["errors"]) > 0:
            error = result_json["errors"][0]
            raise Exception(f"Failed to add live stream: {error.get('error', 'Unknown error')}")
        if result_json.get("results") and len(result_json["results"]) > 0:
            stream_id = result_json["results"][0].get("id")
            if not isinstance(stream_id, str) or not stream_id:
                raise RuntimeError("Failed to add live stream: response missing results[0].id")
            if stream_id in self._added_stream_ids:
                raise Exception(
                    f"Server returned duplicate live stream id {stream_id} for stream "
                    f"{stream_num}; benchmark requires distinct stream assets"
                )
            self._added_stream_ids.add(stream_id)
            self.logger.debug(f"Successfully created live stream {stream_num}: {stream_id}")
            return stream_id
        else:
            raise Exception("No results returned from API")

    def _batch_delete_streams(self, stream_ids: List[str], inter_delete_delay: float = 0.0) -> None:
        super()._batch_delete_streams(stream_ids, inter_delete_delay=inter_delete_delay)
        for stream_id in stream_ids:
            self.latency_tracker.remove_stream(str(stream_id))
            self._added_stream_ids.discard(str(stream_id))

    def _prepare_stream_count_probe(
        self,
        target_count: int,
        active_stream_ids: List[str],
        active_futures: List,
        executor,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        probe_cooldown: float,
        probe_label: str,
    ) -> bool:
        """Prepare exactly target_count live streams for a Phase 2 probe."""
        current_count = len(active_stream_ids)
        inter_stream_delay = 1 + (chunk_size / target_count)

        if target_count > current_count:
            delta = target_count - current_count
            self.logger.info(
                f"  {probe_label}: keeping {current_count} existing streams, "
                f"adding {delta} more to reach {target_count}"
            )
            self.latency_tracker.clear(reset_ignored=True)
            self._reset_stream_integrity_tracking()
            self.reset_gpu_monitoring()
            self.reset_prometheus_collectors()
            self.reset_cpu_monitoring()
            self.reset_pipeline_stage_samples()
            start_idx = current_count + 1
        else:
            if active_stream_ids:
                self.logger.info(
                    f"  {probe_label}: deleting all {current_count} active streams "
                    f"before adding {target_count} fresh..."
                )
                self._stop_live_generation_requests(
                    list(active_stream_ids),
                    backend_type=benchmark_config.get("backend_type", "rtvi_vlm"),
                    captions_endpoint=self._resolve_generate_captions_endpoint(
                        video_config, benchmark_config
                    ),
                )
                self._batch_delete_streams(list(active_stream_ids))
                active_stream_ids.clear()
            self.logger.info(
                f"  Waiting {probe_cooldown:.0f}s cooldown before starting "
                f"probe at {target_count} streams..."
            )
            time.sleep(probe_cooldown)
            self.latency_tracker.clear(reset_ignored=True)
            self._reset_stream_integrity_tracking()
            self.reset_gpu_monitoring()
            self.reset_prometheus_collectors()
            self.reset_cpu_monitoring()
            self.reset_pipeline_stage_samples()
            self.logger.info(f"  Latency history cleared; adding {target_count} streams fresh...")
            start_idx = 1

        for i in range(start_idx, target_count + 1):
            stream_id = None
            try:
                stream_id = self._add_live_stream(video_config, i)
            except Exception as exc:
                self.logger.warning(
                    f"  Failed to add stream {i}; treating {target_count} as " f"unstable: {exc}"
                )
            if stream_id:
                active_stream_ids.append(stream_id)
                self.active_resources.append(f"stream_{stream_id}")
                future = self._start_stream_monitoring(
                    executor,
                    video_config,
                    chunk_size,
                    benchmark_config,
                    model_name,
                    stream_id,
                    i,
                )
                active_futures.append(future)
                time.sleep(inter_stream_delay)
            else:
                self.logger.warning(
                    f"  Failed to add stream {i}; treating {target_count} as unstable"
                )
                if active_stream_ids:
                    self._stop_live_generation_requests(
                        list(active_stream_ids),
                        backend_type=benchmark_config.get("backend_type", "rtvi_vlm"),
                        captions_endpoint=self._resolve_generate_captions_endpoint(
                            video_config, benchmark_config
                        ),
                    )
                    self._batch_delete_streams(list(active_stream_ids))
                    active_stream_ids.clear()
                    self.logger.info(
                        f"  Waiting {probe_cooldown:.0f}s cooldown after "
                        f"failure-cleanup before next probe..."
                    )
                    time.sleep(probe_cooldown)
                    self.latency_tracker.clear(reset_ignored=True)
                    self._reset_stream_integrity_tracking()
                    self.reset_gpu_monitoring()
                    self.reset_prometheus_collectors()
                    self.reset_cpu_monitoring()
                    self.reset_pipeline_stage_samples()
                return False

        self.latency_tracker.clear(reset_ignored=True)
        self._reset_stream_integrity_tracking()
        self.reset_gpu_monitoring()
        self.reset_prometheus_collectors()
        self.reset_cpu_monitoring()
        self.reset_pipeline_stage_samples()
        self.logger.info(
            f"  Added {len(active_stream_ids)} streams; steady-state windows reset; "
            "starting stability check..."
        )
        return True

    def _run_probe_stability_check(
        self,
        probe_label: str,
        stream_count: int,
        stability_check_interval: float,
        required_stable_windows: int,
        required_unstable_windows: int,
        latency_threshold: float,
        active_stream_ids: List[str],
        video_config: Dict,
    ) -> bool:
        """Run the Phase 2 stability loop for one stream-count probe."""
        bs_required_unstable = max(required_unstable_windows, 3)
        consecutive_stable = 0
        consecutive_unstable = 0
        last_check = time.time()
        min_stable_stream_coverage = float(video_config.get("min_stable_stream_coverage", 0.9))
        min_fresh_measurements = int(video_config.get("min_fresh_measurements_per_stream", 1))
        require_no_dropped_chunks = self._as_bool(
            video_config.get("require_no_dropped_chunks", True)
        )
        fresh_latency_timeout = float(
            video_config.get(
                "fresh_latency_timeout_seconds",
                stability_check_interval
                * max(required_stable_windows + required_unstable_windows, 1),
            )
        )
        window_measurement_baseline = self.latency_tracker.get_stream_measurement_counts()
        # Latch dropped chunks for the whole Phase 2 probe. A probe that drops any
        # chunk during validation is not BCD-stable even if later windows recover.
        window_drop_baseline = self._get_stream_drop_counts()
        freshness_wait_started_at = None
        timeout = fresh_latency_timeout + (
            stability_check_interval * (required_stable_windows + bs_required_unstable + 1)
        )
        deadline = time.time() + timeout

        while time.time() < deadline:
            if time.time() - last_check < stability_check_interval:
                time.sleep(1)
                continue

            recent_stats = self.latency_tracker.get_stats()
            avg_latency = recent_stats.get("avg_latency", 0)
            moving_average_latency = recent_stats.get("moving_average_latency", 0)
            recent_p95 = self.latency_tracker.get_recent_p95()
            is_stable = self.latency_tracker.is_stable(latency_threshold)
            current_measurement_counts = self.latency_tracker.get_stream_measurement_counts()
            freshness = self.latency_tracker.get_fresh_stream_coverage(
                window_measurement_baseline,
                active_stream_ids,
                min_fresh_measurements,
            )
            active_streams = freshness["active_streams"]
            fresh_streams = freshness["fresh_streams"]
            fresh_coverage = freshness["coverage"]
            freshness_pending = False
            freshness_wait_elapsed = 0.0
            if active_streams and fresh_coverage < min_stable_stream_coverage:
                is_stable = False
                current_time = time.time()
                if freshness_wait_started_at is None:
                    freshness_wait_started_at = current_time
                freshness_wait_elapsed = current_time - freshness_wait_started_at
                freshness_pending = freshness_wait_elapsed < fresh_latency_timeout
                self.logger.warning(
                    f"[{probe_label} {stream_count}] Fresh latency coverage below threshold: "
                    f"{fresh_streams}/{active_streams} streams "
                    f"({fresh_coverage:.1%} < {min_stable_stream_coverage:.1%}) "
                    f"produced >= {min_fresh_measurements} new measurement(s); "
                    f"{'waiting for queued responses' if freshness_pending else 'freshness wait timed out'} "
                    f"({freshness_wait_elapsed:.0f}s/{fresh_latency_timeout:.0f}s)"
                )
            else:
                freshness_wait_started_at = None

            stream_drops = self._get_new_stream_drops(window_drop_baseline, active_stream_ids)
            if require_no_dropped_chunks and stream_drops["total_dropped_chunks"] > 0:
                is_stable = False
                self.logger.warning(
                    f"[{probe_label} {stream_count}] Dropped chunk continuity detected: "
                    f"{stream_drops['total_dropped_chunks']} chunk(s) across "
                    f"{stream_drops['streams_with_drops']} stream(s)"
                )

            if freshness_pending:
                consecutive_stable = 0
                consecutive_unstable = 0
                stability_status = (
                    f"Waiting for fresh responses "
                    f"({freshness_wait_elapsed:.0f}/{fresh_latency_timeout:.0f}s)"
                )
            elif is_stable:
                consecutive_stable += 1
                consecutive_unstable = 0
                stability_status = f"Stable ({consecutive_stable}/{required_stable_windows})"
            else:
                consecutive_unstable += 1
                consecutive_stable = 0
                stability_status = f"Unstable ({consecutive_unstable}/{bs_required_unstable})"

            gpu_usage, nvdec_usage, gpu_metric_source = self._get_current_gpu_monitor_usage()
            gpu_usage_str = "N/A" if gpu_usage is None else f"{gpu_usage:.2f}"
            nvdec_usage_str = "N/A" if nvdec_usage is None else f"{nvdec_usage:.2f}"

            self.logger.info(
                f"[{probe_label} {stream_count}] Stability check - streams: {stream_count}, "
                f"avg: {avg_latency:.2f}s, "
                f"moving_avg: {moving_average_latency:.2f}s, "
                f"p95: {recent_p95:.2f}s, "
                f"fresh: {fresh_streams}/{active_streams} ({fresh_coverage:.1%}), "
                f"dropped_chunks: {stream_drops['total_dropped_chunks']}, "
                f"GPU latest ({gpu_metric_source}): {gpu_usage_str}%, "
                f"NVDEC latest ({gpu_metric_source}): {nvdec_usage_str}%, "
                f"status: {stability_status}"
            )
            self.logger.debug(
                f"[{probe_label} {stream_count}] Latency tracker stats: {recent_stats}"
            )

            per_stream_stats_str = self.latency_tracker.get_per_stream_stats_str()
            if per_stream_stats_str:
                self.logger.info(f"[{probe_label} {stream_count}] Per-stream statistics:")
                for line in per_stream_stats_str.split("\n"):
                    self.logger.info(line)

            if consecutive_stable >= required_stable_windows:
                return True
            if consecutive_unstable >= bs_required_unstable:
                return False
            if not freshness_pending:
                window_measurement_baseline = current_measurement_counts
            last_check = time.time()

        self.logger.warning(
            f"  Timeout at {stream_count} streams after {timeout:.0f}s; treating as unstable"
        )
        return False

    def _capture_stable_probe_latency_stats(
        self, stream_count: int, snapshot_source: str
    ) -> Dict[str, Any]:
        """Capture latency stats and a snapshot for a confirmed stable Phase 2 probe."""
        stats = self.latency_tracker.get_stats()
        percentiles = self.latency_tracker.get_recent_percentiles()
        gpu_usage, nvdec_usage, gpu_metric_source = self._get_current_gpu_monitor_usage()
        final_stable_latency_stats = {
            "moving_average_latency": stats.get("moving_average_latency", 0),
            "max_latency": stats.get("max_latency", 0),
            **percentiles,
            "resource_metrics": {
                **self.get_current_prometheus_collector_stats(),
                **self.get_pipeline_stage_stats(),
            },
        }
        snapshot = {
            "stream_count": stream_count,
            "avg_latency": stats.get("avg_latency", 0),
            "moving_average_latency": stats.get("moving_average_latency", 0),
            "p95_latency": percentiles.get("p95", 0),
            "max_latency": stats.get("max_latency", 0),
            "gpu_usage_pct": gpu_usage,
            "nvdec_usage_pct": nvdec_usage,
            "gpu_metric_source": gpu_metric_source,
            **self.get_cpu_stats(),
        }
        self.latency_snapshots.append(snapshot)
        self.logger.info(
            f"  Latency snapshot @ {stream_count} streams ({snapshot_source}): "
            f"avg={snapshot['avg_latency']:.2f}s, "
            f"p95={snapshot['p95_latency']:.2f}s, "
            f"moving_avg={snapshot['moving_average_latency']:.2f}s"
        )
        return final_stable_latency_stats

    def _binary_search_max_streams(
        self,
        low: int,
        high: int,
        active_stream_ids: List[str],
        active_futures: List,
        executor,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        stability_check_interval: float,
        required_stable_windows: int,
        required_unstable_windows: int,
        latency_threshold: float,
        probe_cooldown: float = 15.0,
    ) -> Tuple[int, Dict, Optional[int]]:
        """Phase 2: binary search and optional one-stream linear extension.

        Upward probes keep the currently stable streams and add only the delta.
        Downward probes tear down active streams, wait `probe_cooldown` seconds,
        then add the lower count fresh so degraded queues do not contaminate the
        next measurement.

        Stability check mirrors Phase 1: requires `required_stable_windows`
        consecutive stable windows to confirm stable, and
        `max(required_unstable_windows, 3)` consecutive unstable windows to
        confirm unstable.  Full Phase 1 logging (avg, moving_avg, p95, GPU,
        per-stream stats) is emitted at each check interval.  A latency snapshot
        is appended to `self.latency_snapshots` for every stable mid.

        Args:
            low: Last confirmed stable stream count (Phase 1 result).
            high: First confirmed unstable stream count (Phase 1 result).
            active_stream_ids: Mutable list of currently active stream IDs (modified in place).
            active_futures: Mutable list of monitoring thread futures (appended to).
            executor: ThreadPoolExecutor for monitoring threads.
            video_config: Per-video config dict.
            chunk_size: Chunk duration in seconds.
            benchmark_config: Global benchmark config dict.
            model_name: Model name string.
            stability_check_interval: Seconds between stability checks.
            required_stable_windows: Consecutive stable windows to confirm stable.
            required_unstable_windows: Consecutive unstable windows threshold (floored at 3).
            latency_threshold: P95 latency threshold in seconds.
            probe_cooldown: Seconds to wait after full stream deletion before starting probe.

        Returns:
            Tuple of (max_stable_stream_count, final_stable_latency_stats, unstable_ceiling).
            final_stable_latency_stats is empty dict if no stable mid was found.
            unstable_ceiling is None when linear extension reaches its cap without instability.
        """
        self.logger.info(
            f"Phase 2: binary search between {low} (stable) and {high} (unstable) — "
            f"currently {len(active_stream_ids)} active streams"
        )
        # Latency stats captured when we confirm a stable mid — returned to caller so it
        # can update last_stable_* (avoids 0s in summary when Phase 1 never stabilised).
        final_stable_latency_stats: Dict = {}
        unstable_ceiling: Optional[int] = high

        while high - low > 1:
            mid = (low + high) // 2
            self.logger.info(f"Binary search: probing mid={mid} (low={low}, high={high})")

            probe_ready = self._prepare_stream_count_probe(
                target_count=mid,
                active_stream_ids=active_stream_ids,
                active_futures=active_futures,
                executor=executor,
                video_config=video_config,
                chunk_size=chunk_size,
                benchmark_config=benchmark_config,
                model_name=model_name,
                probe_cooldown=probe_cooldown,
                probe_label="Binary search probe",
            )
            if not probe_ready:
                high = mid
                unstable_ceiling = mid
                continue

            is_stable_at_mid = self._run_probe_stability_check(
                probe_label="BS probe",
                stream_count=mid,
                stability_check_interval=stability_check_interval,
                required_stable_windows=required_stable_windows,
                required_unstable_windows=required_unstable_windows,
                latency_threshold=latency_threshold,
                active_stream_ids=active_stream_ids,
                video_config=video_config,
            )

            if is_stable_at_mid:
                self.logger.info(f"  {mid} streams: STABLE → low={mid}")
                low = mid
                final_stable_latency_stats = self._capture_stable_probe_latency_stats(
                    mid, "binary search"
                )
            else:
                self.logger.info(f"  {mid} streams: UNSTABLE → high={mid}")
                high = mid
                unstable_ceiling = mid

        if video_config.get("binary_search_linear_extension", True):
            linear_cap = int(
                video_config.get(
                    "binary_search_linear_extension_cap",
                    video_config.get("max_stream_count", 300),
                )
            )
            probe_count = max(high, low + 1)
            reached_cap_without_instability = False
            while probe_count <= linear_cap:
                self.logger.info(
                    f"Linear extension: probing {probe_count} streams "
                    f"(current stable={low}, cap={linear_cap})"
                )
                probe_ready = self._prepare_stream_count_probe(
                    target_count=probe_count,
                    active_stream_ids=active_stream_ids,
                    active_futures=active_futures,
                    executor=executor,
                    video_config=video_config,
                    chunk_size=chunk_size,
                    benchmark_config=benchmark_config,
                    model_name=model_name,
                    probe_cooldown=probe_cooldown,
                    probe_label="Linear extension probe",
                )
                if not probe_ready:
                    high = probe_count
                    unstable_ceiling = probe_count
                    reached_cap_without_instability = False
                    break

                is_stable_at_probe = self._run_probe_stability_check(
                    probe_label="Linear probe",
                    stream_count=probe_count,
                    stability_check_interval=stability_check_interval,
                    required_stable_windows=required_stable_windows,
                    required_unstable_windows=required_unstable_windows,
                    latency_threshold=latency_threshold,
                    active_stream_ids=active_stream_ids,
                    video_config=video_config,
                )
                if is_stable_at_probe:
                    self.logger.info(f"  {probe_count} streams: STABLE → low={probe_count}")
                    low = probe_count
                    unstable_ceiling = None
                    final_stable_latency_stats = self._capture_stable_probe_latency_stats(
                        probe_count, "linear extension"
                    )
                    reached_cap_without_instability = probe_count == linear_cap
                    probe_count += 1
                    continue

                self.logger.info(f"  {probe_count} streams: UNSTABLE")
                high = probe_count
                unstable_ceiling = probe_count
                reached_cap_without_instability = False
                break

            if reached_cap_without_instability:
                self.logger.info(
                    f"Linear extension reached cap {linear_cap} without instability; "
                    "phase2_unstable_ceiling will be null"
                )

        self.logger.info(f"Phase 2 complete: max stable streams = {low}")
        return low, final_stable_latency_stats, unstable_ceiling

    def _start_stream_monitoring(
        self,
        executor,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        stream_id: str,
        stream_num: int,
    ):
        """Start SSE monitoring and wait until the server accepts or rejects generation."""
        startup_future = Future()
        startup_stop_event = threading.Event()
        monitor_future = executor.submit(
            self._monitor_stream_latency,
            video_config,
            chunk_size,
            benchmark_config,
            model_name,
            stream_id,
            stream_num,
            startup_stop_event=startup_stop_event,
            startup_future=startup_future,
        )
        timeout_seconds = max(
            0.1,
            float(video_config.get("stream_startup_timeout_seconds", 30.0)),
        )
        try:
            startup_future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as exc:
            startup_stop_event.set()
            monitor_future.cancel()
            raise RuntimeError(
                f"Timed out after {timeout_seconds:.1f}s waiting for live stream "
                f"{stream_num} generation admission"
            ) from exc
        return monitor_future

    def _monitor_stream_latency(
        self,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        stream_id: str,
        stream_num: int,
        startup_stop_event: Optional[threading.Event] = None,
        startup_future: Optional[Future] = None,
    ):
        """Monitor latency for a specific stream using SSE"""
        try:
            backend_type = benchmark_config.get("backend_type", "rtvi_vlm")
            latency_measurement_source = video_config.get(
                "latency_measurement_source",
                benchmark_config.get("latency_measurement_source", "ntp_timestamp"),
            )

            # Get API params (works for both backends)
            if backend_type == "rtvi_embed":
                params = self._merge_with_defaults(
                    video_config.get("generate_video_embeddings_params", {}),
                    benchmark_config["api_params"],
                )
            else:
                params = merge_vlm_params(self._merge_with_defaults, video_config, benchmark_config)

            if backend_type == "rtvi_embed":
                # Start generate_video_embeddings with streaming for rtvi_embed backend
                request_data = {
                    "id": stream_id,
                    "model": model_name,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "chunk_duration": chunk_size,
                }

                # Add optional parameters for embeddings
                if "chunk_overlap_duration" in params:
                    request_data["chunk_overlap_duration"] = params["chunk_overlap_duration"]

                # Add optional live stream parameters from video config
                if video_config.get("chunk_overlap_duration") is not None:
                    request_data["chunk_overlap_duration"] = video_config["chunk_overlap_duration"]

                endpoint = "/generate_video_embeddings"
                self.logger.debug(
                    f"Sending {endpoint} request with payload: {json.dumps(request_data, indent=2)}"
                )
            else:
                # Start VLM generation with streaming for rtvi_vlm backend
                api_mode, endpoint, request_data, _response_label = build_vlm_generation_request(
                    video_config=video_config,
                    benchmark_config=benchmark_config,
                    params=params,
                    model_name=model_name,
                    asset_id=stream_id,
                    chunk_size=chunk_size,
                    stream=True,
                )
                self.logger.debug(
                    f"Sending {endpoint} ({api_mode}) request with payload: "
                    f"{json.dumps(request_data, indent=2)}"
                )

            try:
                url = f"{self.base_url}{endpoint}"
                startup_timeout = max(
                    0.1,
                    float(video_config.get("stream_startup_timeout_seconds", 30.0)),
                )
                response = self.session.post(
                    url,
                    json=request_data,
                    stream=True,
                    timeout=(self.DEFAULT_CONNECT_TIMEOUT, startup_timeout),
                )
                self.logger.debug(
                    f"{endpoint} request successful for stream {stream_num}, "
                    f"status: {response.status_code}"
                )
            except requests.exceptions.RequestException as e:
                message = f"{endpoint} request failed for stream {stream_num}: {e}"
                self.logger.error(message)
                if hasattr(e, "response") and e.response is not None:
                    try:
                        error_details = e.response.json()
                        self.logger.error(f"Error details: {error_details}")
                    except (json.JSONDecodeError, AttributeError):
                        self.logger.error(f"Response text: {e.response.text}")
                raise RuntimeError(message) from e

            if response.status_code >= 400:
                self.logger.error(
                    f"{endpoint} failed for stream {stream_num}, status: {response.status_code}"
                )
                try:
                    error_details = response.json()
                    self.logger.error(f"Error details: {error_details}")
                except (json.JSONDecodeError, AttributeError):
                    self.logger.error(f"Response text: {response.text}")
                raise build_stream_start_error(response, endpoint, stream_num)

            if startup_stop_event is not None and startup_stop_event.is_set():
                response.close()
                return

            if startup_future is not None and not startup_future.done():
                startup_future.set_result(None)

            # Process SSE events
            client = sseclient.SSEClient(response)
            self.logger.debug(f"Processing SSE events for stream {stream_num}")
            for event in client.events():
                if startup_stop_event is not None and startup_stop_event.is_set():
                    break
                data = event.data.strip()
                self.logger.debug(f"Stream {stream_num} received SSE event: {data[:200]}...")

                if data == "[DONE]":
                    self.logger.debug(f"Stream {stream_num} received [DONE] event")
                    break

                try:
                    result = json.loads(data)

                    if not isinstance(result, dict):
                        self.logger.error(f"Stream {stream_num} received non-dict result: {data}")
                        continue

                    self.logger.debug(f"Stream {stream_num} parsed JSON event successfully")

                    self._record_response_chunks(stream_id, result)
                    self.record_pipeline_stage_samples(result)

                    if backend_type == "rtvi_embed":
                        # Process embeddings response
                        if result.get("embeddings"):
                            self.logger.debug("")
                            self.logger.debug(f"=== Stream {stream_num} Embeddings ===")
                            self.logger.debug(f"Embeddings count: {len(result['embeddings'])}")

                            # Print additional info if available
                            if result.get("usage"):
                                usage = result["usage"]
                                if usage.get("total_chunks_processed"):
                                    self.logger.debug(
                                        f"Chunks processed: {usage['total_chunks_processed']}"
                                    )
                                if usage.get("query_processing_time"):
                                    self.logger.debug(
                                        f"Processing time: {usage['query_processing_time']:.2f}s"
                                    )
                                    self.logger.debug("=" * 40)

                        latency, latency_source = self._extract_live_stream_latency_seconds(
                            result, latency_measurement_source
                        )
                        if latency is not None:
                            self.latency_tracker.record_latency(latency, stream_id)
                            self.logger.debug(
                                f"Stream {stream_num} recorded {latency_source} latency: "
                                f"{latency:.2f}s"
                            )
                    else:
                        # Process captions content when available
                        choices = result.get("choices")
                        choice = choices[0] if choices and isinstance(choices[0], dict) else None
                        if choice and choice.get("finish_reason") == "stop":
                            message = choice.get("message")
                            delta = choice.get("delta")
                            message = message if isinstance(message, dict) else {}
                            delta = delta if isinstance(delta, dict) else {}
                            captions_content = message.get("content") or delta.get("content") or ""
                            self.logger.debug("")
                            self.logger.debug(f"=== Stream {stream_num} Captions ===")
                            self.logger.debug(f"Captions: {captions_content}")

                            # Print additional info if available
                            if result.get("usage"):
                                usage = result["usage"]
                                if usage.get("total_chunks_processed"):
                                    self.logger.debug(
                                        f"Chunks processed: {usage['total_chunks_processed']}"
                                    )
                                if usage.get("query_processing_time"):
                                    self.logger.debug(
                                        f"Processing time: {usage['query_processing_time']:.2f}s"
                                    )
                            self.logger.debug("=" * 40)

                        latency, latency_source = self._extract_live_stream_latency_seconds(
                            result, latency_measurement_source
                        )
                        if latency is not None:
                            self.latency_tracker.record_latency(latency, stream_id)
                            self.logger.debug(
                                f"Stream {stream_num} recorded {latency_source} latency: "
                                f"{latency:.2f}s"
                            )

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    self.logger.error(f"Error processing stream event for stream {stream_num}: {e}")
                    continue

        except Exception as e:
            if startup_future is not None and not startup_future.done():
                startup_future.set_exception(e)
            self.logger.error(f"Error monitoring stream {stream_num}: {e}")

    def analyze_results(self, results_dir: str, output_file: str) -> None:
        """Generate Excel report from live streams benchmark results"""
        self.logger.debug(f"Analyzing live streams results from: {results_dir}")

        # Load execution summary
        summary_file = os.path.join(results_dir, "execution_summary.json")
        if not os.path.exists(summary_file):
            raise FileNotFoundError(f"Execution summary not found: {summary_file}")

        with open(summary_file, "r") as f:
            execution_summary = json.load(f)

        # Parse all test case results
        summary_data = []
        snapshots_data = []  # per-N-stream latency snapshots across all test cases
        per_core_data = []  # per-core CPU usage rows

        for test_case in execution_summary["test_cases"]:
            test_case_id = test_case["test_case_id"]
            test_case_dir = os.path.join(results_dir, test_case_id)

            # Load live streams results
            results_file = os.path.join(test_case_dir, "max_live_streams_results.json")
            if os.path.exists(results_file):
                with open(results_file, "r") as f:
                    stream_results = json.load(f)

                # Determine backend type from test case config
                backend_type = stream_results.get("backend_type", "rtvi_vlm")

                # Use appropriate metric names based on backend type
                if backend_type == "rtvi_embed":
                    gpu_usage_mean_key = "inference_gpu_usage_mean"
                    gpu_usage_p90_key = "inference_gpu_usage_p90"
                    nvdec_usage_mean_key = "inference_nvdec_usage_mean"
                else:
                    gpu_usage_mean_key = "vlm_gpu_usage_mean"
                    gpu_usage_p90_key = "vlm_gpu_usage_p90"
                    nvdec_usage_mean_key = "vlm_nvdec_usage_mean"

                result_summary = {
                    "test_case_id": test_case_id,
                    "benchmark_mode": "max_live_streams",
                    "rtsp_url": stream_results.get("rtsp_url", ""),
                    "chunk_size": stream_results.get("chunk_size", 0),
                    "max_sustainable_streams": stream_results.get("max_sustainable_streams", 0),
                    "latency_threshold_seconds": stream_results.get("latency_threshold_seconds", 0),
                    "total_streams_tested": stream_results.get("total_streams_tested", 0),
                    "decode_latency": stream_results.get("decode_latency_seconds_avg", 0),
                    "avg_latency": stream_results.get("last_stable_moving_average_latency", 0),
                    "p50_latency": stream_results.get("last_stable_p50", 0),
                    "p75_latency": stream_results.get("last_stable_p75", 0),
                    "p90_latency": stream_results.get("last_stable_p90", 0),
                    "p95_latency": stream_results.get("last_stable_p95", 0),
                    "p99_latency": stream_results.get("last_stable_p99", 0),
                    "max_latency": stream_results.get("last_stable_max_latency", 0),
                    # GPU utilisation — process_gpu_stats always writes "vlm_gpu_*" keys
                    # regardless of backend type, so source keys are hardcoded here.
                    # The dict key is the dynamic variable so the summary column name
                    # matches the backend (e.g. "inference_gpu_usage_mean" for rtvi_embed).
                    gpu_usage_mean_key: stream_results.get("vlm_gpu_usage_mean", 0),
                    gpu_usage_p90_key: stream_results.get("vlm_gpu_usage_p90", 0),
                    nvdec_usage_mean_key: stream_results.get("vlm_nvdec_usage_mean", 0),
                    # GPU memory (always stored as vlm_gpu_memory_mean by process_gpu_stats)
                    "vlm_gpu_memory_mean": stream_results.get("vlm_gpu_memory_mean", 0),
                    # Stream-add API latency
                    "stream_add_latency_mean": stream_results.get("stream_add_latency_mean", 0),
                    "stream_add_latency_p90": stream_results.get("stream_add_latency_p90", 0),
                    "stream_add_latency_max": stream_results.get("stream_add_latency_max", 0),
                    "stream_add_latency_count": stream_results.get("stream_add_latency_count", 0),
                    # CPU utilisation (populated when cpu_monitoring is enabled)
                    "cpu_usage_mean": stream_results.get("cpu_usage_mean", 0),
                    "cpu_usage_p90": stream_results.get("cpu_usage_p90", 0),
                    # CPU saturation: non-zero means at least one core was fully loaded
                    "max_any_core_pct": stream_results.get("max_any_core_pct", 0),
                    "cores_over_90pct_count": stream_results.get("cores_over_90pct_count", 0),
                    "cores_over_100pct_count": stream_results.get("cores_over_100pct_count", 0),
                    # System memory utilisation
                    "mem_usage_mean": stream_results.get("mem_usage_mean", 0),
                    "mem_usage_p90": stream_results.get("mem_usage_p90", 0),
                    "backend_type": backend_type,
                }
                # Add Prometheus/DCGM metrics if present (from DCGM exporter)
                for k, v in stream_results.items():
                    if k.startswith("prometheus_") or k.startswith("nodeexporter_"):
                        result_summary[k] = v

                summary_data.append(self.round_floats(result_summary))

                # Collect latency snapshots for per-N-stream sheet
                for snap in stream_results.get("latency_snapshots", []):
                    snapshots_data.append({"test_case_id": test_case_id, **snap})

                # Collect per-core CPU data for the CPU_Per_Core sheet
                per_core = stream_results.get("per_core", {})
                if per_core:
                    per_core_row = {"test_case_id": test_case_id, **per_core}
                    per_core_data.append(self.round_floats(per_core_row))

        # Create Excel file
        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
            # Summary sheet
            if summary_data:
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name="Summary", index=False)

            # Per-N-stream latency snapshots sheet
            if snapshots_data:
                snapshots_df = pd.DataFrame(snapshots_data)
                snapshots_df.to_excel(writer, sheet_name="Latency_Snapshots", index=False)

            # Per-core CPU usage sheet (populated when cpu_monitoring is enabled)
            if per_core_data:
                per_core_df = pd.DataFrame(per_core_data)
                per_core_df.to_excel(writer, sheet_name="CPU_Per_Core", index=False)

            # GPU Info sheet
            try:
                gpu_info_df = self.get_gpu_info_dataframe()
                gpu_info_df.to_excel(writer, sheet_name="GPU_Info", index=False)
            except Exception as e:
                self.logger.warning(f"Failed to add GPU info sheet: {e}")

                # Individual test case sheets
                for i, result in enumerate(summary_data):
                    test_case_df = pd.DataFrame([result])
                    sheet_name = result["test_case_id"][:31]
                    test_case_df.to_excel(writer, sheet_name=sheet_name, index=False)

        self.logger.debug(f"Live streams results analysis completed: {output_file}")
