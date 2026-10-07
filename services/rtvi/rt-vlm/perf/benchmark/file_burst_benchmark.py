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
File Burst Benchmark Implementation

Tests concurrent file processing at different concurrency levels.
"""

import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests
from base import BenchmarkBase, BenchmarkCleanupError
from latency_tracker import LatencyTracker
from vlm_api import build_vlm_generation_request, merge_vlm_params


def file_burst_results_success(concurrency_results: List[Dict[str, Any]]) -> bool:
    """Return true only when every measured concurrency level completed without failures."""
    return bool(concurrency_results) and all(
        result.get("failed_files", 0) == 0 for result in concurrency_results
    )


def count_failed_file_burst_requests(concurrency_results: List[Dict[str, Any]]) -> int:
    return int(sum(result.get("failed_files", 0) for result in concurrency_results))


def bool_config_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


class FileBurstBenchmark(BenchmarkBase):
    """File burst benchmark - test concurrent file processing"""

    FILE_UPLOAD_TIMEOUT_ENV = "RTVI_BENCHMARK_FILE_UPLOAD_TIMEOUT_SEC"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.latency_tracker = LatencyTracker()

    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Dict[str, Any]:
        """Parse file burst benchmark configuration"""
        if scenario_config.get("benchmark_mode") != "file_burst":
            raise ValueError(f"Invalid benchmark mode: {scenario_config.get('benchmark_mode')}")

        if "videos" not in scenario_config:
            raise ValueError("Missing 'videos' field in scenario config")

        # Validate video configurations
        for i, video in enumerate(scenario_config["videos"]):
            if "filepath" not in video:
                raise ValueError(f"Missing 'filepath' in video {i}")
            if "chunk_sizes" not in video:
                raise ValueError(f"Missing 'chunk_sizes' in video {i}")
            if "concurrency_levels" not in video:
                raise ValueError(f"Missing 'concurrency_levels' in video {i}")

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
            "reuse_uploaded_files": scenario_config.get(
                "reuse_uploaded_files",
                global_config.get("reuse_uploaded_files", True),
            ),
            "preupload_file_pool_size": scenario_config.get(
                "preupload_file_pool_size",
                global_config.get("preupload_file_pool_size"),
            ),
            "prompt": global_config.get("prompt", ""),
            "system_prompt": global_config.get("system_prompt", ""),
            "iterations": scenario_config.get("iterations", 3),
        }

    def execute(self, config: Dict, scenario_name: str) -> Dict[str, Any]:
        """Execute file burst benchmark"""
        self.logger.info(f"Starting file burst benchmark: {scenario_name}")

        global_config = self.parse_global_config(config)
        benchmark_config = self.parse_benchmark_config(
            config["test_scenarios"][scenario_name], global_config
        )

        scenario_dir = self.setup_scenario_directory(scenario_name)
        model_name = self.get_available_models()

        execution_results = {
            "scenario_name": scenario_name,
            "benchmark_mode": "file_burst",
            "scenario_dir": scenario_dir,
            "test_cases": [],
            "total_test_cases": 0,
            "successful_test_cases": 0,
            "failed_test_cases": 0,
        }

        # Execute test cases for each video, chunk size, and concurrency level
        for video_config in benchmark_config["videos"]:
            for chunk_size in video_config["chunk_sizes"]:
                test_case_id = self._generate_test_case_id(video_config, chunk_size)
                execution_results["total_test_cases"] += 1

                try:
                    test_result = self._execute_file_burst_test_case(
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
                            f"Test case {test_case_id} completed with request failures"
                        )

                except BenchmarkCleanupError:
                    raise
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
            f"File burst benchmark completed: {execution_results['successful_test_cases']}/"
            f"{execution_results['total_test_cases']} test cases successful"
        )

        return execution_results

    def _generate_test_case_id(self, video_config: Dict, chunk_size: int) -> str:
        """Generate unique test case ID"""
        # Use optional 'name' field if provided, otherwise use filename
        id = ""
        if "name" in video_config:
            id = video_config["name"]
        filename = os.path.basename(video_config["filepath"])
        name = f"{id}_{os.path.splitext(filename)[0]}" if id else os.path.splitext(filename)[0]
        return f"file_burst_{name}_{chunk_size}sec"

    def _execute_file_burst_test_case(
        self,
        test_case_id: str,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        scenario_dir: str,
    ) -> Dict[str, Any]:
        """Run file burst test case with multiple iterations and aggregate results."""
        test_case_dir = os.path.join(scenario_dir, test_case_id)
        os.makedirs(test_case_dir, exist_ok=True)

        iterations = benchmark_config.get("iterations", 3)
        iteration_results = []

        for iteration in range(1, iterations + 1):
            iteration_dir = os.path.join(test_case_dir, f"iteration_{iteration}")
            os.makedirs(iteration_dir, exist_ok=True)
            try:
                result = self._execute_file_burst_iteration(
                    iteration,
                    video_config,
                    chunk_size,
                    benchmark_config,
                    model_name,
                    iteration_dir,
                )
                iteration_results.append(result)
                self.logger.info(f"Iteration {iteration}/{iterations} completed for {test_case_id}")
            except BenchmarkCleanupError:
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

        # Aggregate per-concurrency-level stats across successful iterations.
        # Only include the configured concurrency_levels — binary-search probe levels
        # (added by _find_optimal_concurrency_for_target_latency) appear in only 1-2
        # iterations each and would produce unreliable cross-iteration averages.
        concurrency_summary = {}
        if successful:
            configured_levels = set(video_config.get("concurrency_levels", []))
            all_levels = sorted(
                set(
                    r["concurrency_level"]
                    for it in successful
                    for r in it.get("concurrency_results", [])
                    if r.get("concurrency_level") in configured_levels
                )
            )
            for level in all_levels:
                level_runs = [
                    r
                    for it in successful
                    for r in it.get("concurrency_results", [])
                    if r.get("concurrency_level") == level
                ]

                def _mean(field):
                    vals = [r[field] for r in level_runs if isinstance(r.get(field), (int, float))]
                    return round(float(np.mean(vals)), 3) if vals else None

                def _std(field):
                    vals = [r[field] for r in level_runs if isinstance(r.get(field), (int, float))]
                    return round(float(np.std(vals)), 3) if len(vals) > 1 else 0.0

                concurrency_summary[str(level)] = {
                    "concurrency_level": level,
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
                    "mean_throughput": _mean("throughput_files_per_second"),
                    "std_throughput": _std("throughput_files_per_second"),
                    "mean_failed_files": _mean("failed_files"),
                    "mean_vlm_gpu_usage_mean": _mean("vlm_gpu_usage_mean"),
                    "std_vlm_gpu_usage_mean": _std("vlm_gpu_usage_mean"),
                    "mean_vlm_gpu_memory_mean": _mean("vlm_gpu_memory_mean"),
                    "std_vlm_gpu_memory_mean": _std("vlm_gpu_memory_mean"),
                    "mean_vlm_gpu_usage_p90": _mean("vlm_gpu_usage_p90"),
                    "std_vlm_gpu_usage_p90": _std("vlm_gpu_usage_p90"),
                    "mean_vlm_nvdec_usage_mean": _mean("vlm_nvdec_usage_mean"),
                    "std_vlm_nvdec_usage_mean": _std("vlm_nvdec_usage_mean"),
                    "mean_vlm_nvdec_usage_p90": _mean("vlm_nvdec_usage_p90"),
                    "std_vlm_nvdec_usage_p90": _std("vlm_nvdec_usage_p90"),
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
                    concurrency_summary[str(level)][f"mean_{field}"] = _mean(field)

        results = {
            "test_case_id": test_case_id,
            "benchmark_mode": "file_burst",
            "video_file": video_config["filepath"],
            "chunk_size": chunk_size,
            "concurrency_levels": video_config.get("concurrency_levels", []),
            "iterations": iterations,
            "successful_iterations": len(successful),
            "failed_iterations": iterations - len(successful),
            "success": len(successful) == iterations,
            "iteration_results": iteration_results,
            "concurrency_summary": concurrency_summary,
        }

        summary_file = os.path.join(test_case_dir, "test_case_summary.json")
        self.save_json_data(self.round_floats(results), summary_file)

        self.logger.info(
            f"Test case {test_case_id}: {len(successful)}/{iterations} iterations successful"
        )
        return results

    def _execute_file_burst_iteration(
        self,
        iteration: int,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        iteration_dir: str,
    ) -> Dict[str, Any]:
        """Execute one iteration of a file burst test across all concurrency levels."""
        self.logger.info(
            f"Starting iteration {iteration}: "
            f"levels {video_config['concurrency_levels']}, chunk {chunk_size}s"
        )

        concurrency_results = []

        # Test each concurrency level
        for concurrency_level in video_config["concurrency_levels"]:
            level_result = self._test_single_concurrency_level(
                f"iter{iteration}",
                video_config,
                chunk_size,
                concurrency_level,
                benchmark_config,
                model_name,
                iteration_dir,
            )
            if level_result:
                concurrency_results.append(level_result)
            else:
                self.logger.error(f"Failed to test concurrency level {concurrency_level}")
            time.sleep(2)

        # Find optimal concurrency for target latency.
        # Set target_latency_seconds: 0 in the video config to skip this step.
        target_latency = video_config.get("target_latency_seconds", 60.0)
        optimal_result = None
        if target_latency > 0.0:
            optimal_result = self._find_optimal_concurrency_for_target_latency(
                video_config,
                concurrency_results,
                iteration_dir,
                chunk_size,
                benchmark_config,
                model_name,
                target_latency,
            )

        # Add binary search results to concurrency_results
        if optimal_result and "all_test_results" in optimal_result:
            for test_result in optimal_result["all_test_results"]:
                concurrency_level = test_result.get("concurrency_level")
                already_tested = any(
                    r["concurrency_level"] == concurrency_level for r in concurrency_results
                )
                if not already_tested:
                    concurrency_results.append(test_result)

        # Sort all results by concurrency level
        concurrency_results.sort(key=lambda x: x.get("concurrency_level", 0))

        all_tested_levels = sorted(
            list(set(r.get("concurrency_level", 0) for r in concurrency_results))
        )
        total_failed_files = count_failed_file_burst_requests(concurrency_results)

        results = {
            "iteration": iteration,
            "benchmark_mode": "file_burst",
            "video_file": video_config["filepath"],
            "chunk_size": chunk_size,
            "concurrency_levels_tested": all_tested_levels,
            "concurrency_results": concurrency_results,
            "optimal_target_concurrency": optimal_result,
            "target_latency_seconds": target_latency,
            "failed_files": total_failed_files,
            "success": file_burst_results_success(concurrency_results),
            "backend_type": benchmark_config.get("backend_type", "rtvi_vlm"),
        }

        results_file = os.path.join(iteration_dir, "file_burst_results.json")
        self.save_json_data(self.round_floats(results), results_file)

        return results

    def _reuse_uploaded_files(self, video_config: Dict, benchmark_config: Dict) -> bool:
        value = video_config.get(
            "reuse_uploaded_files",
            benchmark_config.get("reuse_uploaded_files"),
        )
        return bool_config_value(value, default=True)

    def _preupload_pool_size(
        self, video_config: Dict, benchmark_config: Dict, concurrency: int
    ) -> int:
        configured = video_config.get(
            "preupload_file_pool_size",
            benchmark_config.get("preupload_file_pool_size"),
        )
        if configured in (None, ""):
            return max(1, concurrency)
        return max(1, int(configured))

    def _file_upload_timeout_sec(self) -> float:
        raw_timeout = os.environ.get(self.FILE_UPLOAD_TIMEOUT_ENV, "")
        if not raw_timeout:
            return self.DEFAULT_READ_TIMEOUT

        try:
            timeout_sec = float(raw_timeout)
        except ValueError:
            timeout_sec = 0

        if not math.isfinite(timeout_sec) or timeout_sec <= 0:
            self.logger.warning(
                "Invalid %s=%r; using default %ss",
                self.FILE_UPLOAD_TIMEOUT_ENV,
                raw_timeout,
                self.DEFAULT_READ_TIMEOUT,
            )
            return self.DEFAULT_READ_TIMEOUT
        return timeout_sec

    def _upload_file_burst_asset(self, video_config: Dict) -> str:
        filepath = os.path.abspath(video_config["filepath"])
        timeout_sec = self._file_upload_timeout_sec()
        files = {
            "filename": (None, filepath),
            "purpose": (None, "vision"),
            "media_type": (None, "video"),
        }

        start_time = time.monotonic()
        self.logger.info(
            "Uploading reusable file asset from %s with %ss read timeout",
            filepath,
            f"{timeout_sec:g}",
        )
        try:
            file_response = self.make_api_call(
                "/files",
                method="POST",
                files=files,
                timeout=(self.DEFAULT_CONNECT_TIMEOUT, timeout_sec),
            )
        except Exception as e:
            self.logger.error(
                "Reusable file asset upload failed for %s after %.2fs: %s",
                os.path.basename(filepath),
                time.monotonic() - start_time,
                e,
            )
            raise

        file_id = file_response.json().get("id")
        self.active_resources.append(f"file_{file_id}")
        self.logger.info(
            "Uploaded reusable file asset %s for %s in %.2fs",
            file_id,
            os.path.basename(filepath),
            time.monotonic() - start_time,
        )
        return file_id

    def _delete_file_burst_asset(self, file_id: str) -> None:
        if not file_id or f"file_{file_id}" not in self.active_resources:
            return
        try:
            if file_id in self._fetch_active_file_ids():
                self.make_api_call(f"/files/{file_id}", method="DELETE")
            else:
                self.logger.debug(f"File {file_id} already gone, skipping DELETE")
            self.active_resources.remove(f"file_{file_id}")
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                # 409 Conflict: file still in use by an in-flight VLM request
                # (typically after a concurrency-level timeout). Keep it tracked
                # so cleanup failure stops later work from using polluted state.
                self.logger.warning(f"File {file_id} still in use (409), cleanup incomplete")
            else:
                self.logger.error(f"Failed to cleanup file {file_id}: {e}")
        except Exception as e:
            self.logger.error(f"Failed to cleanup file {file_id}: {e}")

    def _prepare_uploaded_file_pool(self, video_config: Dict, pool_size: int) -> List[str]:
        file_ids = []
        self.logger.info(
            "Pre-uploading %d reusable file asset(s) for %s",
            pool_size,
            os.path.basename(video_config["filepath"]),
        )
        try:
            for _ in range(pool_size):
                file_ids.append(self._upload_file_burst_asset(video_config))
        except Exception:
            self._cleanup_uploaded_file_pool(file_ids)
            raise
        return file_ids

    def _cleanup_uploaded_file_pool(self, file_ids: List[str]) -> None:
        for file_id in file_ids:
            self._delete_file_burst_asset(file_id)
        undeleted = [file_id for file_id in file_ids if f"file_{file_id}" in self.active_resources]
        if undeleted:
            raise BenchmarkCleanupError(
                f"{len(undeleted)}/{len(file_ids)} file-burst assets remain in use after cleanup"
            )

    def _test_single_concurrency_level(
        self,
        test_case_id: str,
        video_config: Dict,
        chunk_size: int,
        concurrency_level: int,
        benchmark_config: Dict,
        model_name: str,
        test_case_dir: str,
    ) -> Dict[str, Any]:
        """Test a single concurrency level"""
        self.logger.debug(f"Testing concurrency level: {concurrency_level}")

        # Configure connection pool
        self._configure_http_session(concurrency_level + 50)

        # Clear latency tracker
        self.latency_tracker.clear()

        preuploaded_file_ids = []
        reuse_uploaded_files = self._reuse_uploaded_files(video_config, benchmark_config)
        preuploaded_file_pool_size = 0
        monitoring_started = False

        try:
            if reuse_uploaded_files:
                preuploaded_file_pool_size = self._preupload_pool_size(
                    video_config,
                    benchmark_config,
                    concurrency_level,
                )
                preuploaded_file_ids = self._prepare_uploaded_file_pool(
                    video_config,
                    preuploaded_file_pool_size,
                )

            # Start GPU monitoring after setup so upload/delete overhead is outside
            # the measured throughput window.
            self.start_gpu_monitoring()
            monitoring_started = True

            e2e_start_time = time.time()
            completed_files = 0
            failed_files = 0
            processing_times = []
            pipeline_stage_samples: Dict[str, List[float]] = {}
            wave_count = 0
            next_file_index = 0
            steady_state_duration = float(video_config.get("steady_state_duration_seconds") or 0)

            while True:
                wave_count += 1
                # A large wave can outlive the server keep-alive timeout. Start each
                # wave with a fresh pool so POSTs do not reuse stale closed sockets.
                self._reset_http_session(concurrency_level + 50)
                executor = ThreadPoolExecutor(max_workers=concurrency_level)
                futures = []

                for i in range(concurrency_level):
                    future = executor.submit(
                        self._process_single_file_burst,
                        video_config,
                        chunk_size,
                        benchmark_config,
                        model_name,
                        next_file_index + i,
                        test_case_dir,
                        (
                            preuploaded_file_ids[(next_file_index + i) % len(preuploaded_file_ids)]
                            if preuploaded_file_ids
                            else None
                        ),
                    )
                    futures.append(future)
                next_file_index += concurrency_level

                for future in futures:
                    try:
                        file_result = future.result()
                        for metric_key, values in file_result.pop(
                            "pipeline_stage_samples", {}
                        ).items():
                            pipeline_stage_samples.setdefault(metric_key, []).extend(values)
                        if file_result.get("success", False):
                            completed_files += 1
                            processing_times.append(file_result.get("processing_time", 0))
                        else:
                            failed_files += 1
                    except Exception as e:
                        self.logger.error(f"File processing failed: {e}")
                        failed_files += 1
                executor.shutdown(wait=True)

                elapsed = time.time() - e2e_start_time
                if steady_state_duration <= 0 or elapsed >= steady_state_duration:
                    break
                self.logger.info(
                    f"Concurrency {concurrency_level}: steady-state wave {wave_count} "
                    f"completed, elapsed={elapsed:.1f}s/{steady_state_duration:.1f}s"
                )

            e2e_end_time = time.time()
            e2e_latency_seconds = e2e_end_time - e2e_start_time
            latency_stats = self.latency_tracker.get_stats()

            # Calculate P50, P75, P90, P95, P99 latency percentiles
            if processing_times:
                p50_latency = float(np.percentile(processing_times, 50))
                p75_latency = float(np.percentile(processing_times, 75))
                p90_latency = float(np.percentile(processing_times, 90))
                p95_latency = float(np.percentile(processing_times, 95))
                p99_latency = float(np.percentile(processing_times, 99))
            else:
                p50_latency = p75_latency = p90_latency = p95_latency = p99_latency = 0

            latency_history = self.latency_tracker.get_all_latencies()

            result = {
                "concurrency_level": concurrency_level,
                "e2e_latency_seconds": e2e_latency_seconds,
                "completed_files": completed_files,
                "failed_files": failed_files,
                "throughput_files_per_second": (
                    completed_files / e2e_latency_seconds if e2e_latency_seconds > 0 else 0
                ),
                "steady_state_duration_seconds": steady_state_duration,
                "wave_count": wave_count,
                "reuse_uploaded_files": reuse_uploaded_files,
                "preuploaded_file_pool_size": preuploaded_file_pool_size,
                "p50_latency": p50_latency,
                "p75_latency": p75_latency,
                "p90_latency": p90_latency,
                "p95_latency": p95_latency,
                "p99_latency": p99_latency,
                "avg_latency": latency_stats.get("avg_latency", 0),
                "latency_history": latency_history,
                **self.summarize_pipeline_stage_samples(pipeline_stage_samples),
            }

        finally:
            # Stop GPU monitoring and export data
            if monitoring_started:
                self.stop_gpu_monitoring(
                    export_dir=test_case_dir,
                    filename_prefix=f"gpu_metrics_concurrency_{concurrency_level}",
                )

                # Process GPU stats
                gpu_stats_file = os.path.join(
                    test_case_dir, f"gpu_metrics_concurrency_{concurrency_level}_stats.json"
                )
                gpu_metrics = self.process_gpu_stats(gpu_stats_file)
                if gpu_metrics and "result" in locals():
                    result.update(gpu_metrics)
            self._cleanup_uploaded_file_pool(preuploaded_file_ids)

        self.logger.info(
            f"Concurrency {concurrency_level}: "
            f"{completed_files}/{concurrency_level} completed, "
            f"E2E latency: {e2e_latency_seconds:.2f}s, "
            f"avg: {result.get('avg_latency', 0):.2f}s, "
            f"p50: {result.get('p50_latency', 0):.2f}s, p75: {result.get('p75_latency', 0):.2f}s, "
            f"p90: {result.get('p90_latency', 0):.2f}s, p95: {result.get('p95_latency', 0):.2f}s, "
            f"p99: {result.get('p99_latency', 0):.2f}s"
        )
        time.sleep(2)
        return self.round_floats(result)

    def _process_single_file_burst(
        self,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        file_index: int,
        test_case_dir: str,
        preuploaded_file_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process a single file for burst mode"""
        file_id = None
        file_uploaded_for_request = False
        backend_type = benchmark_config.get("backend_type", "rtvi_vlm")
        pipeline_stage_samples: Dict[str, List[float]] = {}
        pipeline_stage_metrics: Dict[str, Any] = {}

        try:
            start_time = time.time()

            if preuploaded_file_id:
                file_id = preuploaded_file_id
            else:
                file_id = self._upload_file_burst_asset(video_config)
                file_uploaded_for_request = True

            # Get API params (works for both backends)
            if backend_type == "rtvi_embed":
                params = self._merge_with_defaults(
                    video_config.get("generate_video_embeddings_params", {}),
                    benchmark_config["api_params"],
                )
            else:
                params = merge_vlm_params(self._merge_with_defaults, video_config, benchmark_config)

            if backend_type == "rtvi_embed":
                # Run generate_video_embeddings for rtvi_embed backend
                request_data = {
                    "id": file_id,
                    "model": model_name,
                    "chunk_duration": chunk_size,
                    "stream": False,
                }

                # Add optional parameters for embeddings
                if "chunk_overlap_duration" in params:
                    request_data["chunk_overlap_duration"] = params["chunk_overlap_duration"]

                self.logger.debug(
                    "Sending generate_video_embeddings request with payload: "
                    f"{json.dumps(request_data, indent=2)}"
                )

                response = self.make_api_call(
                    "/generate_video_embeddings", method="POST", data=request_data
                )
                response_payload = response.json()
                pipeline_stage_samples = self.extract_pipeline_stage_samples(response_payload)
                pipeline_stage_metrics = self.summarize_pipeline_stage_samples(
                    pipeline_stage_samples
                )
                processing_time = time.time() - start_time
                self.latency_tracker.record_latency(processing_time, f"file_{file_index}")

                self.save_response(
                    response,
                    test_case_dir,
                    f"generate_video_embeddings_response_file_{file_index}.json",
                )

            else:
                api_mode, endpoint, request_data, response_label = build_vlm_generation_request(
                    video_config=video_config,
                    benchmark_config=benchmark_config,
                    params=params,
                    model_name=model_name,
                    asset_id=file_id,
                    chunk_size=chunk_size,
                    stream=False,
                )

                self.logger.debug(
                    f"Sending {endpoint} ({api_mode}) request with payload: "
                    f"{json.dumps(request_data, indent=2)}"
                )

                # Allow long videos to override the default 60 s read timeout.
                # For a 10-min video (60 chunks) the server can take 10-15 min.
                req_timeout_seconds = video_config.get(
                    "request_timeout_seconds", self.DEFAULT_READ_TIMEOUT
                )
                response = self.make_api_call(
                    endpoint,
                    method="POST",
                    data=request_data,
                    timeout=(self.DEFAULT_CONNECT_TIMEOUT, req_timeout_seconds),
                )
                response_payload = response.json()
                pipeline_stage_samples = self.extract_pipeline_stage_samples(response_payload)
                pipeline_stage_metrics = self.summarize_pipeline_stage_samples(
                    pipeline_stage_samples
                )
                processing_time = time.time() - start_time
                self.latency_tracker.record_latency(processing_time, f"file_{file_index}")

                self.save_response(
                    response,
                    test_case_dir,
                    f"{response_label}_response_file_{file_index}.json",
                )

            return {
                "success": True,
                "processing_time": processing_time,
                "file_id": file_id,
                "reused_uploaded_file": bool(preuploaded_file_id),
                "pipeline_stage_samples": pipeline_stage_samples,
                **pipeline_stage_metrics,
            }

        except Exception as e:
            self.logger.error(f"Error processing file {file_index}: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if file_uploaded_for_request:
                self._delete_file_burst_asset(file_id)

    def _find_optimal_concurrency_for_target_latency(
        self,
        video_config: Dict,
        concurrency_results: List[Dict],
        test_case_dir: str,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        target_latency: float,
    ) -> Dict[str, Any]:
        """
        Find the optimal concurrency level that achieves the target p99 latency
        using interpolation/extrapolation from existing results.
        """
        if len(concurrency_results) < 2:
            self.logger.warning("Not enough data points to extrapolate optimal concurrency")
            return {"error": "Insufficient data points"}

        self.logger.info(f"Finding optimal concurrency for {target_latency}-second p99 latency...")

        # Extract data points (concurrency, p99_latency)
        data_points = [
            (result["concurrency_level"], result["p99_latency"])
            for result in concurrency_results
            if result.get("p99_latency", 0) > 0
        ]

        if len(data_points) < 2:
            return {"error": "No valid p99 latency data points"}

        # Sort by concurrency level
        data_points.sort(key=lambda x: x[0])

        # Use binary search to find optimal concurrency for target average latency
        tolerance = video_config.get("target_latency_tolerance", 5.0)
        max_concurrency = max(video_config.get("concurrency_levels", [500]))

        # Calculate initial estimate using linear interpolation
        throughput_factors = []
        for concurrency, latency in data_points:
            if latency > 0:
                throughput_factors.append(concurrency / latency)

        if not throughput_factors:
            return {"error": "Cannot calculate throughput factors"}

        # Use average throughput factor for initial estimate
        avg_throughput_factor = sum(throughput_factors) / len(throughput_factors)
        initial_estimate = int(round(target_latency * avg_throughput_factor))
        initial_estimate = max(1, min(initial_estimate, max_concurrency))

        self.logger.info(
            f"Starting binary search with initial estimate: {initial_estimate} "
            f"(based on avg throughput factor: {avg_throughput_factor:.3f})"
        )

        # Binary search bounds
        low = 1
        high = max_concurrency
        best_result = None
        best_concurrency = initial_estimate
        search_results = []
        all_test_results = []

        # Phase 1: Linear estimation to cross target latency
        current_concurrency = initial_estimate
        current_result = self._test_single_concurrency_level(
            f"file_burst_{os.path.splitext(os.path.basename(video_config['filepath']))[0]}_{chunk_size}sec",
            video_config,
            chunk_size,
            current_concurrency,
            benchmark_config,
            model_name,
            test_case_dir,
        )
        current_latency = current_result.get("p99_latency", 0)

        search_results.append(
            {"concurrency": current_concurrency, "latency": current_latency, "iteration": "initial"}
        )
        all_test_results.append(current_result)

        best_result = current_result
        best_concurrency = current_concurrency
        iteration = 1

        # Phase 1: Use linear estimation until we cross target_latency or reach max concurrency
        while current_latency < target_latency and current_concurrency < max_concurrency:
            # Calculate next estimate using linear scaling
            if current_latency > 0:
                scale_factor = target_latency / current_latency
                scale_factor = min(scale_factor, 2.0)  # Cap at 2x increase per step
                next_concurrency = int(round(current_concurrency * scale_factor))
                next_concurrency = min(next_concurrency, max_concurrency)
            else:
                next_concurrency = min(current_concurrency * 2, max_concurrency)

            # Ensure we're making progress
            if next_concurrency <= current_concurrency:
                next_concurrency = current_concurrency + 1

            if next_concurrency > max_concurrency:
                break

            current_concurrency = next_concurrency
            test_case_prefix = (
                f"file_burst_{os.path.splitext(os.path.basename(video_config['filepath']))[0]}"
                f"_{chunk_size}sec"
            )
            current_result = self._test_single_concurrency_level(
                test_case_prefix,
                video_config,
                chunk_size,
                current_concurrency,
                benchmark_config,
                model_name,
                test_case_dir,
            )
            current_latency = current_result.get("p99_latency", 0)

            search_results.append(
                {
                    "concurrency": current_concurrency,
                    "latency": current_latency,
                    "iteration": f"linear_{iteration}",
                }
            )
            all_test_results.append(current_result)

            # Update best result if this is closer to target
            if abs(current_latency - target_latency) < abs(
                best_result.get("p99_latency", 0) - target_latency
            ):
                best_result = current_result
                best_concurrency = current_concurrency

            iteration += 1

        if abs(current_latency - target_latency) <= tolerance:
            self.logger.info(f"Linear estimation achieved target within tolerance ({tolerance}s)")
        else:
            # Phase 2: Binary search to optimize around target
            self.logger.debug("Starting binary search optimization around target latency")

            # Set binary search bounds based on our linear estimation results
            if current_latency < target_latency:
                low = current_concurrency
                high = min(current_concurrency * 2, max_concurrency)
            else:
                # We overshot, so set bounds around the last two tests
                if len(all_test_results) >= 2:
                    prev_result = all_test_results[-2]
                    low = prev_result.get("concurrency_level", current_concurrency // 2)
                else:
                    low = current_concurrency // 2
                high = current_concurrency

            binary_iteration = 1

            while abs(current_latency - target_latency) > tolerance:
                next_concurrency = (low + high) // 2

                # Ensure we don't test the same concurrency twice
                if next_concurrency == current_concurrency or any(
                    result.get("concurrency_level") == next_concurrency
                    for result in all_test_results
                ):
                    self.logger.info(
                        "Binary search converged or would test duplicate concurrency, stopping"
                    )
                    break

                current_concurrency = next_concurrency
                test_case_prefix = (
                    f"file_burst_{os.path.splitext(os.path.basename(video_config['filepath']))[0]}"
                    f"_{chunk_size}sec"
                )
                current_result = self._test_single_concurrency_level(
                    test_case_prefix,
                    video_config,
                    chunk_size,
                    current_concurrency,
                    benchmark_config,
                    model_name,
                    test_case_dir,
                )
                current_latency = current_result.get("p99_latency", 0)

                search_results.append(
                    {
                        "concurrency": current_concurrency,
                        "latency": current_latency,
                        "iteration": f"binary_{binary_iteration}",
                    }
                )
                all_test_results.append(current_result)

                # Update search bounds
                if current_latency < target_latency:
                    low = current_concurrency
                else:
                    high = current_concurrency

                # Update best result if this is closer to target
                if abs(current_latency - target_latency) < abs(
                    best_result.get("p99_latency", 0) - target_latency
                ):
                    best_result = current_result
                    best_concurrency = current_concurrency

                binary_iteration += 1

        self.logger.info(
            f"Binary search completed. Best concurrency: {best_concurrency}, "
            f"Best p99 latency: {best_result.get('p99_latency', 0):.2f}s"
        )

        return {
            "target_latency_seconds": target_latency,
            "estimated_concurrency": best_concurrency,
            "actual_result": best_result,
            "initial_estimate": initial_estimate,
            "throughput_factor_used": avg_throughput_factor,
            "search_results": search_results,
            "all_test_results": all_test_results,
            "data_points_used": data_points,
        }

    def analyze_results(self, results_dir: str, output_file: str) -> None:
        """Generate Excel report from file burst benchmark results"""
        self.logger.debug(f"Analyzing file burst results from: {results_dir}")

        # Load execution summary
        summary_file = os.path.join(results_dir, "execution_summary.json")
        if not os.path.exists(summary_file):
            raise FileNotFoundError(f"Execution summary not found: {summary_file}")

        with open(summary_file, "r") as f:
            execution_summary = json.load(f)

        # Parse all test case results
        all_concurrency_results = []

        for test_case in execution_summary["test_cases"]:
            if not test_case.get("success", False):
                continue

            test_case_id = test_case["test_case_id"]
            test_case_dir = os.path.join(results_dir, test_case_id)

            # Load aggregated results (test_case_summary.json written by _execute_file_burst_test_case)
            results_file = os.path.join(test_case_dir, "test_case_summary.json")
            if os.path.exists(results_file):
                with open(results_file, "r") as f:
                    burst_results = json.load(f)

                # concurrency_summary is a dict keyed by str(level) with mean_* aggregated fields.
                # Filter to configured levels only (excludes binary-search probe levels).
                configured_levels = set(burst_results.get("concurrency_levels", []))
                if not configured_levels:
                    # Fallback for old JSON without concurrency_levels: levels present in ALL
                    # successful iterations are configured; probe levels appear in only 1-2.
                    iter_results = [
                        it for it in burst_results.get("iteration_results", []) if it.get("success")
                    ]
                    if iter_results:
                        from collections import Counter

                        level_counts = Counter(
                            r["concurrency_level"]
                            for it in iter_results
                            for r in it.get("concurrency_results", [])
                        )
                        configured_levels = {
                            lvl for lvl, cnt in level_counts.items() if cnt == len(iter_results)
                        }
                summary_items = burst_results.get("concurrency_summary", {}).values()
                if configured_levels:
                    summary_items = [
                        r for r in summary_items if r.get("concurrency_level") in configured_levels
                    ]
                for concurrency_result in summary_items:
                    concurrency_level = concurrency_result.get("concurrency_level", 0)
                    test_case_id_with_concurrency = f"{test_case_id}_c{concurrency_level}"

                    level_result = {
                        "test_case_id": test_case_id_with_concurrency,
                        "benchmark_mode": "file_burst",
                        "video_file": os.path.basename(burst_results.get("video_file", "")),
                        "chunk_size": burst_results.get("chunk_size", 0),
                        "concurrency_level": concurrency_level,
                        "iterations": burst_results.get("successful_iterations", 0),
                        "avg_latency": concurrency_result.get("mean_avg_latency", 0),
                        "std_avg_latency": concurrency_result.get("std_avg_latency", 0),
                        "p50_latency": concurrency_result.get("mean_p50_latency", 0),
                        "std_p50_latency": concurrency_result.get("std_p50_latency", 0),
                        "p75_latency": concurrency_result.get("mean_p75_latency", 0),
                        "std_p75_latency": concurrency_result.get("std_p75_latency", 0),
                        "p90_latency": concurrency_result.get("mean_p90_latency", 0),
                        "std_p90_latency": concurrency_result.get("std_p90_latency", 0),
                        "p95_latency": concurrency_result.get("mean_p95_latency", 0),
                        "std_p95_latency": concurrency_result.get("std_p95_latency", 0),
                        "p99_latency": concurrency_result.get("mean_p99_latency", 0),
                        "std_p99_latency": concurrency_result.get("std_p99_latency", 0),
                        "throughput_files_per_second": concurrency_result.get("mean_throughput", 0),
                        "std_throughput": concurrency_result.get("std_throughput", 0),
                        "vlm_gpu_usage_mean": concurrency_result.get("mean_vlm_gpu_usage_mean", 0),
                        "std_vlm_gpu_usage_mean": concurrency_result.get(
                            "std_vlm_gpu_usage_mean", 0
                        ),
                        "vlm_gpu_memory_mean": concurrency_result.get(
                            "mean_vlm_gpu_memory_mean", 0
                        ),
                        "std_vlm_gpu_memory_mean": concurrency_result.get(
                            "std_vlm_gpu_memory_mean", 0
                        ),
                        "vlm_gpu_usage_p90": concurrency_result.get("mean_vlm_gpu_usage_p90", 0),
                        "std_vlm_gpu_usage_p90": concurrency_result.get("std_vlm_gpu_usage_p90", 0),
                        "vlm_nvdec_usage_mean": concurrency_result.get(
                            "mean_vlm_nvdec_usage_mean", 0
                        ),
                        "std_vlm_nvdec_usage_mean": concurrency_result.get(
                            "std_vlm_nvdec_usage_mean", 0
                        ),
                        "vlm_nvdec_usage_p90": concurrency_result.get(
                            "mean_vlm_nvdec_usage_p90", 0
                        ),
                        "std_vlm_nvdec_usage_p90": concurrency_result.get(
                            "std_vlm_nvdec_usage_p90", 0
                        ),
                    }

                    all_concurrency_results.append(self.round_floats(level_result))

        # Create Excel file
        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
            # Summary sheet with all concurrency results
            if all_concurrency_results:
                summary_df = pd.DataFrame(all_concurrency_results)
                summary_df.to_excel(writer, sheet_name="Summary", index=False)

            # GPU Info sheet
            try:
                gpu_info_df = self.get_gpu_info_dataframe()
                gpu_info_df.to_excel(writer, sheet_name="GPU_Info", index=False)
            except Exception as e:
                self.logger.warning(f"Failed to add GPU info sheet: {e}")

            # Create separate sheets per test case (outside except — always runs)
            test_case_groups = {}
            for result in all_concurrency_results:
                base_test_case = "_".join(result["test_case_id"].split("_")[:-1])
                if base_test_case not in test_case_groups:
                    test_case_groups[base_test_case] = []
                test_case_groups[base_test_case].append(result)

            for tc_id, test_case_data in test_case_groups.items():
                test_case_df = pd.DataFrame(test_case_data)
                sheet_name = tc_id[:31]
                test_case_df.to_excel(writer, sheet_name=sheet_name, index=False)

        # Add plots to Excel file
        try:
            self.logger.debug("Adding plots to file burst Excel report...")
            if all_concurrency_results:
                detail_df = pd.DataFrame(all_concurrency_results)
                x_column = "test_case_id"
                latency_columns = [
                    "e2e_latency_seconds",
                    "avg_latency",
                    "p50_latency",
                    "p75_latency",
                    "p90_latency",
                    "p95_latency",
                    "p99_latency",
                ]

                latency_columns = [col for col in latency_columns if col in detail_df.columns]

                if latency_columns:
                    self.add_plots_to_excel(output_file, detail_df, x_column, latency_columns)
                else:
                    self.logger.warning("No latency columns found for plotting")
        except Exception as e:
            self.logger.warning(f"Failed to add plots to Excel: {e}")
            self.logger.debug("Excel file created without plots")

        self.logger.debug(f"File burst results analysis completed: {output_file}")
