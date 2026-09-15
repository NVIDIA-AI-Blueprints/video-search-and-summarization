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
Concurrent Live Streams Benchmark Implementation

Tests end-to-end latencies for a fixed number of concurrent live streams.
Unlike max_live_streams (which discovers the capacity ceiling), this benchmark
starts all N streams simultaneously, runs them for a configured duration, and
reports latency statistics (avg, p90, p95, p99, max, min, per-stream breakdown).
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


def concurrent_live_stream_iteration_success(
    actual_streams_started: int,
    requested_stream_count: int,
    streams_with_errors: int,
    total_measurements: int,
) -> bool:
    return (
        actual_streams_started == requested_stream_count
        and streams_with_errors == 0
        and total_measurements > 0
    )


def _rtsp_url_for_stream(video_config: Dict, stream_num: int) -> str:
    """Return the RTSP URL to use for a benchmark stream.

    Concurrent live-stream tests should measure independent stream capacity.
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


class ConcurrentLiveStreamsBenchmark(BenchmarkBase):
    """Concurrent live streams benchmark - measure latency for a fixed stream count"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.latency_tracker = LatencyTracker()
        self._added_stream_ids: set[str] = set()

    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Dict[str, Any]:
        """Parse concurrent live streams benchmark configuration"""
        if scenario_config.get("benchmark_mode") != "concurrent_live_streams":
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
            if "stream_count" not in video:
                raise ValueError(f"Missing 'stream_count' in video {i}")
            stream_count = video["stream_count"]
            if isinstance(stream_count, int):
                if stream_count <= 0:
                    raise ValueError(
                        f"'stream_count' in video {i} must be a positive integer, got: {stream_count}"
                    )
                video["stream_count"] = [stream_count]
            elif isinstance(stream_count, list):
                if not stream_count:
                    raise ValueError(f"'stream_count' list in video {i} must not be empty")
                for sc in stream_count:
                    if not isinstance(sc, int) or sc <= 0:
                        raise ValueError(
                            f"All values in 'stream_count' list in video {i}"
                            f"must be positive integers, got: {sc}"
                        )
            else:
                raise ValueError(
                    f"'stream_count' in video {i} must be a positive integer"
                    f"or list of positive integers, got: {stream_count}"
                )

        # Check backend type
        backend_type = scenario_config.get(
            "backend_type", global_config.get("backend_type", "rtvi_vlm")
        )

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
            "iterations": scenario_config.get("iterations", 3),
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
            "discard_startup_burst_samples": scenario_config.get(
                "discard_startup_burst_samples",
                global_config.get("discard_startup_burst_samples", True),
            ),
            "startup_burst_window_seconds": scenario_config.get(
                "startup_burst_window_seconds",
                global_config.get("startup_burst_window_seconds"),
            ),
            "startup_burst_min_samples": scenario_config.get(
                "startup_burst_min_samples",
                global_config.get("startup_burst_min_samples", 2),
            ),
            "startup_burst_max_discard_per_stream": scenario_config.get(
                "startup_burst_max_discard_per_stream",
                global_config.get("startup_burst_max_discard_per_stream", 20),
            ),
            "prompt": global_config.get("prompt", ""),
            "system_prompt": global_config.get("system_prompt", ""),
        }

    def execute(self, config: Dict, scenario_name: str) -> Dict[str, Any]:
        """Execute concurrent live streams benchmark"""
        self.logger.info(f"Starting concurrent live streams benchmark: {scenario_name}")

        global_config = self.parse_global_config(config)
        benchmark_config = self.parse_benchmark_config(
            config["test_scenarios"][scenario_name], global_config
        )

        scenario_dir = self.setup_scenario_directory(scenario_name)
        model_name = self.get_available_models()

        execution_results = {
            "scenario_name": scenario_name,
            "benchmark_mode": "concurrent_live_streams",
            "scenario_dir": scenario_dir,
            "test_cases": [],
            "total_test_cases": 0,
            "successful_test_cases": 0,
            "failed_test_cases": 0,
        }

        # Execute test cases for each video, chunk size, and stream count
        for video_config in benchmark_config["videos"]:
            for chunk_size in video_config["chunk_sizes"]:
                for stream_count in video_config["stream_count"]:
                    stream_name = video_config.get("name", "live_stream")
                    test_case_id = f"concurrent_live_streams_{stream_name}_{chunk_size}sec_{stream_count}streams"  # noqa: E501
                    execution_results["total_test_cases"] += 1

                    try:
                        test_result = self._execute_concurrent_live_streams_test_case(
                            test_case_id,
                            video_config,
                            chunk_size,
                            stream_count,
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
                                f"Test case {test_case_id} completed without valid measurements"
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
            f"Concurrent live streams benchmark completed: "
            f"{execution_results['successful_test_cases']}/"
            f"{execution_results['total_test_cases']} test cases successful"
        )

        return execution_results

    def _record_timestamp_latency(
        self, result: Dict[str, Any], stream_num: int, stream_id: str
    ) -> None:
        """Record live timestamp latency, clamping impossible negative clock-skew samples."""
        media_info = result.get("media_info") or {}
        if media_info.get("type") != "timestamp":
            return

        end_timestamp = media_info["end_timestamp"]
        self.logger.debug("Stream %s processing timestamp event: %s", stream_num, end_timestamp)
        dt = datetime.strptime(end_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        current_time = datetime.now(timezone.utc)
        latency = (current_time - dt).total_seconds()
        if latency < 0:
            self.logger.warning(
                "Stream %s produced negative timestamp latency %.2fs; "
                "clamping to 0.00s for reporting. Check RTSP/NTP clock alignment.",
                stream_num,
                latency,
            )
            latency = 0.0

        self.logger.info("Stream %s latency: %s seconds", stream_num, latency)
        self.latency_tracker.record_latency(
            latency,
            stream_id,
            recorded_at=current_time.timestamp(),
        )
        self.logger.debug("Stream %s recorded latency: %s seconds", stream_num, latency)

    def _execute_concurrent_live_streams_test_case(
        self,
        test_case_id: str,
        video_config: Dict,
        chunk_size: int,
        stream_count: int,
        benchmark_config: Dict,
        model_name: str,
        scenario_dir: str,
    ) -> Dict[str, Any]:
        """Run concurrent live streams test case with multiple iterations."""
        test_case_dir = os.path.join(scenario_dir, test_case_id)
        os.makedirs(test_case_dir, exist_ok=True)

        iterations = benchmark_config.get("iterations", 3)
        iteration_results = []

        for iteration in range(1, iterations + 1):
            iteration_dir = os.path.join(test_case_dir, f"iteration_{iteration}")
            os.makedirs(iteration_dir, exist_ok=True)
            try:
                result = self._execute_concurrent_iteration(
                    iteration,
                    video_config,
                    chunk_size,
                    stream_count,
                    benchmark_config,
                    model_name,
                    iteration_dir,
                )
                iteration_results.append(result)
                self.logger.info(f"Iteration {iteration}/{iterations} completed for {test_case_id}")
            except BenchmarkCleanupError:
                raise
            except BenchmarkResourceUnavailableError:
                raise
            except Exception as e:
                self.logger.error(
                    f"Iteration {iteration}/{iterations} failed for {test_case_id}: {e}"
                )
                iteration_results.append(
                    {"iteration": iteration, "success": False, "error": str(e)}
                )

            if iteration < iterations:
                self.logger.info("Waiting 10s between iterations...")
                time.sleep(10)

        successful = [r for r in iteration_results if r.get("success", False)]

        def _mean(field):
            vals = [r[field] for r in successful if isinstance(r.get(field), (int, float))]
            return round(float(np.mean(vals)), 3) if vals else None

        def _std(field):
            vals = [r[field] for r in successful if isinstance(r.get(field), (int, float))]
            return round(float(np.std(vals)), 3) if len(vals) > 1 else 0.0

        test_result = {
            "test_case_id": test_case_id,
            "benchmark_mode": "concurrent_live_streams",
            "stream_count": stream_count,
            "chunk_size": chunk_size,
            "iterations": iterations,
            "successful_iterations": len(successful),
            "success": len(successful) > 0,
            "iteration_results": iteration_results,
            "mean_avg_latency": _mean("avg_latency"),
            "std_avg_latency": _std("avg_latency"),
            "mean_p50_latency": _mean("p50_latency"),
            "std_p50_latency": _std("p50_latency"),
            "mean_p75_latency": _mean("p75_latency"),
            "std_p75_latency": _std("p75_latency"),
            "mean_p90_latency": _mean("p90_latency"),
            "std_p90_latency": _std("p90_latency"),
            "mean_p95_latency": _mean("p95_latency"),
            "std_p95_latency": _std("p95_latency"),
            "mean_p99_latency": _mean("p99_latency"),
            "std_p99_latency": _std("p99_latency"),
            "mean_max_latency": _mean("max_latency"),
            "std_max_latency": _std("max_latency"),
            "mean_min_latency": _mean("min_latency"),
            "std_min_latency": _std("min_latency"),
            "mean_total_measurements": _mean("total_measurements"),
            "mean_raw_total_measurements": _mean("raw_total_measurements"),
            "mean_discarded_startup_burst_measurements": _mean(
                "discarded_startup_burst_measurements"
            ),
            "mean_decode_latency_seconds_avg": _mean("decode_latency_seconds_avg"),
        }
        for field in (
            "chunk_decode_latency_seconds_avg",
            "chunk_decode_latency_seconds_min",
            "chunk_decode_latency_seconds_max",
            "chunk_decode_latency_seconds_p50",
            "chunk_decode_latency_seconds_p75",
            "chunk_decode_latency_seconds_p90",
            "chunk_decode_latency_seconds_p95",
            "chunk_decode_latency_seconds_p99",
            "chunk_vlm_latency_seconds_avg",
            "chunk_vlm_latency_seconds_min",
            "chunk_vlm_latency_seconds_max",
            "chunk_vlm_latency_seconds_p50",
            "chunk_vlm_latency_seconds_p75",
            "chunk_vlm_latency_seconds_p90",
            "chunk_vlm_latency_seconds_p95",
            "chunk_vlm_latency_seconds_p99",
            "chunk_queue_latency_seconds_avg",
            "chunk_queue_latency_seconds_min",
            "chunk_queue_latency_seconds_max",
            "chunk_queue_latency_seconds_p50",
            "chunk_queue_latency_seconds_p75",
            "chunk_queue_latency_seconds_p90",
            "chunk_queue_latency_seconds_p95",
            "chunk_queue_latency_seconds_p99",
            "chunk_server_processing_latency_seconds_avg",
            "chunk_server_processing_latency_seconds_min",
            "chunk_server_processing_latency_seconds_max",
            "chunk_server_processing_latency_seconds_p50",
            "chunk_server_processing_latency_seconds_p75",
            "chunk_server_processing_latency_seconds_p90",
            "chunk_server_processing_latency_seconds_p95",
            "chunk_server_processing_latency_seconds_p99",
            "chunk_server_e2e_latency_seconds_avg",
            "chunk_server_e2e_latency_seconds_min",
            "chunk_server_e2e_latency_seconds_max",
            "chunk_server_e2e_latency_seconds_p50",
            "chunk_server_e2e_latency_seconds_p75",
            "chunk_server_e2e_latency_seconds_p90",
            "chunk_server_e2e_latency_seconds_p95",
            "chunk_server_e2e_latency_seconds_p99",
        ):
            test_result[f"mean_{field}"] = _mean(field)

        summary_file = os.path.join(test_case_dir, "test_case_summary.json")
        self.save_json_data(self.round_floats(test_result), summary_file)

        self.logger.info(
            f"Test case {test_case_id} completed: "
            f"{len(successful)}/{iterations} iterations successful, "
            f"mean_p95={test_result['mean_p95_latency']}s"
        )
        return test_result

    def _execute_concurrent_iteration(
        self,
        iteration: int,
        video_config: Dict,
        chunk_size: int,
        stream_count: int,
        benchmark_config: Dict,
        model_name: str,
        iteration_dir: str,
    ) -> Dict[str, Any]:
        """
        Execute one iteration of a concurrent live streams test —
        start all streams simultaneously and measure latencies.
        """
        duration_seconds = video_config.get("duration_seconds", 120)

        self.logger.info(
            f"Starting iteration {iteration}: "
            f"{stream_count} streams, {duration_seconds}s duration"
        )

        # Configure connection pool sized for stream_count + 50 headroom
        self._configure_http_session(stream_count + 50)

        # Clear latency trackers for this test. They are reset again after the
        # stream ramp so reported stats cover only the steady-state window.
        self.latency_tracker.clear()
        self.reset_pipeline_stage_samples()

        active_stream_ids = []
        active_futures = []
        streams_with_errors = 0
        skipped_rtsp_sources: List[Dict[str, Any]] = []
        start_time = time.time()
        steady_state_start_time = None
        steady_state_end_time = None
        start_metrics: Dict[str, Any] = {}
        end_metrics: Dict[str, Any] = {}
        monitoring_started = False
        stop_event = threading.Event()
        executor = ThreadPoolExecutor(max_workers=stream_count + 10)

        try:
            # Add all stream_count streams upfront
            self.logger.info(f"Adding {stream_count} concurrent streams...")

            inter_stream_delay = 1 + (chunk_size / stream_count)
            next_rtsp_source_num = 1
            for stream_num in range(1, stream_count + 1):
                try:
                    stream_id, next_rtsp_source_num = self._add_live_stream_with_retries(
                        video_config,
                        stream_num,
                        next_rtsp_source_num,
                        skipped_rtsp_sources,
                    )
                    if stream_id:
                        active_stream_ids.append(stream_id)
                        self.active_resources.append(f"stream_{stream_id}")
                        self.logger.info(f"Added stream {stream_num}/{stream_count}: {stream_id}")
                    else:
                        raise RuntimeError(
                            f"Failed to add stream {stream_num}: no stream_id returned"
                        )

                    if stream_id:
                        future = self._start_stream_monitoring(
                            executor,
                            video_config,
                            chunk_size,
                            benchmark_config,
                            model_name,
                            stream_id,
                            stream_num,
                            stop_event,
                        )
                        active_futures.append(future)

                    time.sleep(inter_stream_delay)
                except BenchmarkResourceUnavailableError:
                    raise
                except Exception as e:
                    self.logger.error(f"Failed to add stream {stream_num}: {e}")
                    streams_with_errors += 1
                    raise RuntimeError(
                        f"Aborting concurrent stream ramp at {stream_num}/{stream_count}: {e}"
                    ) from e

            actual_streams_started = len(active_stream_ids)
            self.logger.info(
                f"Started {actual_streams_started}/{stream_count} streams "
                f"({streams_with_errors} errors)"
            )

            self._require_exact_stream_count(actual_streams_started, stream_count)

            # BCD steady-state collection begins only after all requested streams
            # are active. This excludes stream add/ramp-up from latency, GPU/DCGM,
            # NVDEC, CPU, and stage-latency aggregates.
            self.latency_tracker.clear()
            self.reset_pipeline_stage_samples()
            start_metrics = self.scrape_metrics()
            self.start_gpu_monitoring()
            monitoring_started = True
            steady_state_start_time = time.time()

            self.logger.info(
                f"Launched {len(active_futures)} monitoring threads. "
                f"Running for {duration_seconds} seconds..."
            )

            # Sleep for the configured duration
            try:
                time.sleep(duration_seconds)
            except KeyboardInterrupt:
                self.logger.info("Test interrupted by user")

            # Signal all monitoring threads to stop
            self.logger.info("Signaling monitoring threads to stop...")
            stop_event.set()
            steady_state_end_time = time.time()
            end_metrics = self.scrape_metrics()

            if monitoring_started:
                self.stop_gpu_monitoring(
                    export_dir=iteration_dir,
                    filename_prefix="gpu_metrics_concurrent_live_streams",
                )
                monitoring_started = False

        finally:
            if not stop_event.is_set():
                stop_event.set()
            if not end_metrics:
                end_metrics = self.scrape_metrics()
            if monitoring_started:
                self.stop_gpu_monitoring(
                    export_dir=iteration_dir,
                    filename_prefix="gpu_metrics_concurrent_live_streams",
                )
                monitoring_started = False

            # Stop generation first so monitor threads exit and the server releases
            # active request ownership before asset deletion.
            cleanup_error = None
            self.logger.info(
                f"Stopping live generation for {len(active_stream_ids)} active streams..."
            )
            try:
                self._stop_live_generation_requests(
                    list(active_stream_ids),
                    backend_type=benchmark_config.get("backend_type", "rtvi_vlm"),
                    captions_endpoint=resolve_vlm_endpoint(
                        video_config,
                        benchmark_config,
                        GENERATE_CAPTIONS_API,
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
                # Delete all streams through the batch endpoint. Sequential deletes
                # can spend one full drain timeout per overloaded stream.
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
                    f"Live-stream cleanup failed after iteration {iteration}: {cleanup_error}"
                ) from cleanup_error

        actual_duration = time.time() - start_time
        steady_state_duration_seconds = (
            steady_state_end_time - steady_state_start_time
            if steady_state_start_time is not None and steady_state_end_time is not None
            else 0.0
        )

        # Compute final latency stats
        raw_latency_history = self.latency_tracker.get_all_latencies()
        latency_records = self.latency_tracker.get_all_latency_records()
        latency_history, latency_filter = self._filter_startup_burst_latency_records(
            latency_records,
            benchmark_config,
            chunk_size,
        )
        latency_stats, per_stream_stats, latency_percentiles = self._compute_latency_summary(
            latency_history
        )
        p90_latency = latency_percentiles["p90_latency"]
        p95_latency = latency_percentiles["p95_latency"]
        p99_latency = latency_percentiles["p99_latency"]
        p50_latency = latency_percentiles["p50_latency"]
        p75_latency = latency_percentiles["p75_latency"]

        if latency_filter["discarded_measurements"]:
            self.logger.info(
                "Discarded %s startup burst latency samples across %s stream(s) "
                "before report stats (raw=%s, filtered=%s)",
                latency_filter["discarded_measurements"],
                latency_filter["streams_with_discarded_startup_burst"],
                latency_filter["raw_total_measurements"],
                latency_filter["filtered_total_measurements"],
            )

        # Process GPU stats
        gpu_stats_file = os.path.join(
            iteration_dir, "gpu_metrics_concurrent_live_streams_stats.json"
        )
        gpu_metrics = self.process_gpu_stats(gpu_stats_file)

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

        results = {
            "iteration": iteration,
            "benchmark_mode": "concurrent_live_streams",
            **_rtsp_source_config_for_results(video_config),
            **_rtsp_reuse_summary(video_config, actual_streams_started),
            "chunk_size": chunk_size,
            "stream_count": stream_count,
            "actual_streams_started": actual_streams_started,
            "streams_with_errors": streams_with_errors,
            "skipped_rtsp_sources": skipped_rtsp_sources,
            "skipped_rtsp_source_count": len(skipped_rtsp_sources),
            "duration_seconds": duration_seconds,
            "actual_duration_seconds": actual_duration,
            "steady_state_duration_seconds": steady_state_duration_seconds,
            "success": concurrent_live_stream_iteration_success(
                actual_streams_started,
                stream_count,
                streams_with_errors,
                latency_stats.get("total_measurements", 0),
            ),
            **latency_stats,
            "p50_latency": p50_latency,
            "p75_latency": p75_latency,
            "p90_latency": p90_latency,
            "p95_latency": p95_latency,
            "p99_latency": p99_latency,
            "per_stream_stats": per_stream_stats,
            **gpu_metrics,
            **self.get_pipeline_stage_stats(),
            "raw_total_measurements": latency_filter["raw_total_measurements"],
            "discarded_startup_burst_measurements": latency_filter["discarded_measurements"],
            "latency_filter": latency_filter,
            "decode_latency_seconds_count_start": start_count or 0,
            "decode_latency_seconds_count_end": end_count or 0,
            "decode_latency_seconds_sum_start": start_sum or 0,
            "decode_latency_seconds_sum_end": end_sum or 0,
            "decode_latency_seconds_avg": avg_decode_latency,
            "latency_history": latency_history,
            "backend_type": benchmark_config.get("backend_type", "rtvi_vlm"),
        }
        if latency_filter["discarded_measurements"]:
            results["raw_latency_history"] = raw_latency_history

        # Save results
        results_file = os.path.join(iteration_dir, "concurrent_live_streams_results.json")
        self.save_json_data(self.round_floats(results), results_file)

        self.logger.info(
            f"Iteration {iteration} completed: "
            f"avg={latency_stats.get('avg_latency', 0):.2f}s, "
            f"p90={p90_latency:.2f}s, p95={p95_latency:.2f}s, p99={p99_latency:.2f}s, "
            f"total_measurements={latency_stats.get('total_measurements', 0)}"
        )

        return results

    @staticmethod
    def _require_exact_stream_count(
        actual_streams_started: int, requested_stream_count: int
    ) -> None:
        if actual_streams_started != requested_stream_count:
            raise RuntimeError(
                f"Started {actual_streams_started}/{requested_stream_count} streams; "
                "concurrent live-stream results require exact stream cardinality"
            )

    def _add_live_stream_with_retries(
        self,
        video_config: Dict,
        stream_num: int,
        rtsp_source_num: int,
        skipped_rtsp_sources: List[Dict[str, Any]],
    ) -> Tuple[str, int]:
        """Add one logical stream, retrying and skipping transiently bad RTSP sources."""
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
                raise RuntimeError(
                    f"Failed to add logical stream {stream_num} after skipping "
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

    def _filter_startup_burst_latency_records(
        self,
        latency_records: Dict[str, List[Dict[str, float]]],
        benchmark_config: Dict[str, Any],
        chunk_size: int,
    ) -> Tuple[Dict[str, List[float]], Dict[str, Any]]:
        """
        Drop only the leading same-stream burst caused by queued chunks at stream startup.

        RTSP chunk latency is measured from the chunk's media timestamp to the SSE event receive
        time. If the service is still draining work or the stream start is delayed, one stream can
        emit several old chunk responses within milliseconds. Those values are not steady-state
        latency for this scenario and should not pollute the final report. Sustained overload
        remains visible because spaced high-latency samples are kept.
        """
        enabled = bool(benchmark_config.get("discard_startup_burst_samples", True))
        configured_window = benchmark_config.get("startup_burst_window_seconds")
        if configured_window is None:
            window_seconds = min(2.0, max(float(chunk_size) * 0.25, 0.5))
        else:
            window_seconds = max(float(configured_window), 0.0)

        min_burst_samples = max(int(benchmark_config.get("startup_burst_min_samples", 2)), 2)
        max_discard = max(
            int(benchmark_config.get("startup_burst_max_discard_per_stream", 20)),
            0,
        )

        filtered_history: Dict[str, List[float]] = {}
        per_stream_discarded: Dict[str, int] = {}
        raw_total_measurements = 0
        filtered_total_measurements = 0

        for stream_id, records in latency_records.items():
            latencies = [float(record.get("latency", 0.0)) for record in records]
            raw_total_measurements += len(latencies)
            discard_count = 0

            if enabled and max_discard > 0 and len(records) > min_burst_samples:
                burst_count = self._leading_burst_record_count(
                    records,
                    window_seconds,
                    max_discard,
                )
                if min_burst_samples <= burst_count < len(records):
                    discard_count = min(burst_count, max_discard)

            if discard_count:
                per_stream_discarded[stream_id] = discard_count

            filtered_latencies = latencies[discard_count:]
            filtered_history[stream_id] = filtered_latencies
            filtered_total_measurements += len(filtered_latencies)

        discarded_measurements = sum(per_stream_discarded.values())
        filter_summary = {
            "enabled": enabled,
            "startup_burst_window_seconds": window_seconds,
            "startup_burst_min_samples": min_burst_samples,
            "startup_burst_max_discard_per_stream": max_discard,
            "raw_total_measurements": raw_total_measurements,
            "filtered_total_measurements": filtered_total_measurements,
            "discarded_measurements": discarded_measurements,
            "streams_with_discarded_startup_burst": len(per_stream_discarded),
            "per_stream_discarded_measurements": per_stream_discarded,
        }
        return filtered_history, filter_summary

    @staticmethod
    def _leading_burst_record_count(
        records: List[Dict[str, float]],
        window_seconds: float,
        max_discard: int,
    ) -> int:
        """Count leading records that arrived too close together for normal RTSP chunks."""
        if len(records) < 2:
            return len(records)

        first_recorded_at = records[0].get("recorded_at")
        if first_recorded_at is None:
            return 1

        previous_recorded_at = float(first_recorded_at)
        burst_count = 1
        search_limit = min(len(records), max_discard + 1)
        for record in records[1:search_limit]:
            recorded_at = record.get("recorded_at")
            if recorded_at is None:
                break

            recorded_at = float(recorded_at)
            if recorded_at - previous_recorded_at > window_seconds:
                break

            burst_count += 1
            previous_recorded_at = recorded_at

        return burst_count

    @staticmethod
    def _compute_latency_summary(
        latency_history: Dict[str, List[float]],
    ) -> Tuple[Dict[str, Any], Dict[str, Dict[str, float]], Dict[str, float]]:
        """Compute aggregate and per-stream latency stats from a selected history."""
        all_latencies = []
        moving_average_latencies = []
        per_stream_stats = {}

        for stream_id, latencies in latency_history.items():
            if latencies:
                all_latencies.extend(latencies)
                moving_average_latencies.extend(latencies[-3:])
                per_stream_stats[stream_id] = {
                    "avg_latency": sum(latencies) / len(latencies),
                    "max_latency": max(latencies),
                    "min_latency": min(latencies),
                    "total_measurements": len(latencies),
                }
            else:
                per_stream_stats[stream_id] = {
                    "avg_latency": 0.0,
                    "max_latency": 0.0,
                    "min_latency": 0.0,
                    "total_measurements": 0,
                }

        if not all_latencies:
            latency_stats = {
                "avg_latency": 0,
                "max_latency": 0,
                "min_latency": 0,
                "total_measurements": 0,
                "moving_average_latency": 0,
            }
            latency_percentiles = {
                "p50_latency": 0.0,
                "p75_latency": 0.0,
                "p90_latency": 0.0,
                "p95_latency": 0.0,
                "p99_latency": 0.0,
            }
            return latency_stats, per_stream_stats, latency_percentiles

        latency_stats = {
            "avg_latency": sum(all_latencies) / len(all_latencies),
            "max_latency": max(all_latencies),
            "min_latency": min(all_latencies),
            "total_measurements": len(all_latencies),
            "moving_average_latency": sum(moving_average_latencies) / len(moving_average_latencies),
        }
        latency_percentiles = {
            "p50_latency": float(np.percentile(all_latencies, 50)),
            "p75_latency": float(np.percentile(all_latencies, 75)),
            "p90_latency": float(np.percentile(all_latencies, 90)),
            "p95_latency": float(np.percentile(all_latencies, 95)),
            "p99_latency": float(np.percentile(all_latencies, 99)),
        }
        return latency_stats, per_stream_stats, latency_percentiles

    def _add_live_stream(self, video_config: Dict, stream_num: int) -> str:
        """Add a live stream for testing"""
        rtsp_url = _rtsp_url_for_stream(video_config, stream_num)
        request_data = {"streams": [_stream_payload_for_stream(video_config, stream_num, rtsp_url)]}

        self.logger.debug(
            f"Sending /streams/add request with payload: {json.dumps(request_data, indent=2)}"
        )

        try:
            response = self.make_api_call("/streams/add", method="POST", data=request_data)
        except requests.exceptions.HTTPError as exc:
            rejected_response = getattr(exc, "response", None)
            if rejected_response is not None:
                error = build_stream_start_error(rejected_response, "/streams/add", stream_num)
                if isinstance(error, BenchmarkResourceUnavailableError):
                    raise error from exc
            raise
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
            self._added_stream_ids.discard(str(stream_id))

    def _start_stream_monitoring(
        self,
        executor,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        stream_id: str,
        stream_num: int,
        stop_event: threading.Event,
    ):
        """Start SSE monitoring and wait until the server accepts or rejects generation."""
        startup_future = Future()
        monitor_future = executor.submit(
            self._monitor_stream_latency_until_stopped,
            video_config,
            chunk_size,
            benchmark_config,
            model_name,
            stream_id,
            stream_num,
            stop_event,
            startup_future=startup_future,
        )
        timeout_seconds = max(
            0.1,
            float(video_config.get("stream_startup_timeout_seconds", 30.0)),
        )
        try:
            startup_future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as exc:
            stop_event.set()
            monitor_future.cancel()
            raise RuntimeError(
                f"Timed out after {timeout_seconds:.1f}s waiting for live stream "
                f"{stream_num} generation admission"
            ) from exc
        return monitor_future

    def _monitor_stream_latency_until_stopped(
        self,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        stream_id: str,
        stream_num: int,
        stop_event: threading.Event,
        startup_future: Optional[Future] = None,
    ):
        """Monitor latency for a specific stream using SSE until stop_event is set"""
        try:
            backend_type = benchmark_config.get("backend_type", "rtvi_vlm")

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

            if startup_future is not None and not startup_future.done():
                startup_future.set_result(None)

            # Process SSE events until stop_event is set
            client = sseclient.SSEClient(response)
            self.logger.debug(f"Processing SSE events for stream {stream_num}")
            for event in client.events():
                # Check stop signal after each event
                if stop_event.is_set():
                    self.logger.debug(f"Stream {stream_num} stop signal received, exiting SSE loop")
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
                    self.record_pipeline_stage_samples(result)

                    if backend_type == "rtvi_embed":
                        # Process embeddings response
                        if result.get("embeddings"):
                            self.logger.debug("")
                            self.logger.debug(f"=== Stream {stream_num} Embeddings ===")
                            self.logger.debug(f"Embeddings count: {len(result['embeddings'])}")

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

                        self._record_timestamp_latency(result, stream_num, stream_id)
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

                        self._record_timestamp_latency(result, stream_num, stream_id)

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
        """Generate Excel report from concurrent live streams benchmark results"""
        self.logger.debug(f"Analyzing concurrent live streams results from: {results_dir}")

        # Load execution summary
        summary_file = os.path.join(results_dir, "execution_summary.json")
        if not os.path.exists(summary_file):
            raise FileNotFoundError(f"Execution summary not found: {summary_file}")

        with open(summary_file, "r") as f:
            execution_summary = json.load(f)

        summary_data = []

        for test_case in execution_summary["test_cases"]:
            test_case_id = test_case["test_case_id"]
            test_case_dir = os.path.join(results_dir, test_case_id)

            # Find all iteration directories and load results from each
            iteration_dirs = sorted(
                d
                for d in os.listdir(test_case_dir)
                if os.path.isdir(os.path.join(test_case_dir, d)) and d.startswith("iteration_")
            )

            iteration_data_list = []
            for iter_dir_name in iteration_dirs:
                iter_results_file = os.path.join(
                    test_case_dir, iter_dir_name, "concurrent_live_streams_results.json"
                )
                if os.path.exists(iter_results_file):
                    with open(iter_results_file, "r") as f:
                        iteration_data_list.append(json.load(f))

            if not iteration_data_list:
                self.logger.warning(f"No iteration results found for {test_case_id}")
                continue

            # Use metadata from first iteration
            first_iter = iteration_data_list[0]
            backend_type = first_iter.get("backend_type", "rtvi_vlm")

            # Use appropriate metric names based on backend type
            if backend_type == "rtvi_embed":
                gpu_usage_mean_key = "inference_gpu_usage_mean"
                gpu_usage_p90_key = "inference_gpu_usage_p90"
                nvdec_usage_mean_key = "inference_nvdec_usage_mean"
            else:
                gpu_usage_mean_key = "vlm_gpu_usage_mean"
                gpu_usage_p90_key = "vlm_gpu_usage_p90"
                nvdec_usage_mean_key = "vlm_nvdec_usage_mean"

            def _mean_field(field, _iters=iteration_data_list):
                vals = [r[field] for r in _iters if isinstance(r.get(field), (int, float))]
                return float(np.mean(vals)) if vals else 0

            result_summary = {
                "test_case_id": test_case_id,
                "benchmark_mode": "concurrent_live_streams",
                "rtsp_url": first_iter.get("rtsp_url", ""),
                "chunk_size": first_iter.get("chunk_size", 0),
                "stream_count": first_iter.get("stream_count", 0),
                "actual_streams_started": _mean_field("actual_streams_started"),
                "duration_seconds": _mean_field("duration_seconds"),
                "total_measurements": _mean_field("total_measurements"),
                "raw_total_measurements": _mean_field("raw_total_measurements"),
                "discarded_startup_burst_measurements": _mean_field(
                    "discarded_startup_burst_measurements"
                ),
                "avg_latency": _mean_field("avg_latency"),
                "p50_latency": _mean_field("p50_latency"),
                "p75_latency": _mean_field("p75_latency"),
                "p90_latency": _mean_field("p90_latency"),
                "p95_latency": _mean_field("p95_latency"),
                "p99_latency": _mean_field("p99_latency"),
                "max_latency": _mean_field("max_latency"),
                "min_latency": _mean_field("min_latency"),
                "decode_latency_seconds_avg": _mean_field("decode_latency_seconds_avg"),
                gpu_usage_mean_key: _mean_field("vlm_gpu_usage_mean"),
                gpu_usage_p90_key: _mean_field("vlm_gpu_usage_p90"),
                nvdec_usage_mean_key: _mean_field("vlm_nvdec_usage_mean"),
                "backend_type": backend_type,
            }

            summary_data.append(self.round_floats(result_summary))

        # Create Excel file
        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
            # Summary sheet
            if summary_data:
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name="Summary", index=False)

            # GPU Info sheet
            try:
                gpu_info_df = self.get_gpu_info_dataframe()
                gpu_info_df.to_excel(writer, sheet_name="GPU_Info", index=False)
            except Exception as e:
                self.logger.warning(f"Failed to add GPU info sheet: {e}")

        self.logger.debug(f"Concurrent live streams results analysis completed: {output_file}")
