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
VLM Captions / Video Embeddings Benchmark Implementation

Tests concurrent requests at different concurrency levels.
Supports both RTVI VLM (VLM captions) and RTVI Embed (video embeddings) backends.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import requests
from base import BenchmarkBase
from latency_tracker import LatencyTracker
from vlm_api import build_vlm_generation_request, merge_vlm_params


class ConcurrencyBenchmark(BenchmarkBase):
    """Concurrency benchmark - test concurrent VLM caption generation or video embeddings"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.latency_tracker = LatencyTracker()
        self.backend_type = None  # Will be set from global config

    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Dict[str, Any]:
        """Parse concurrency benchmark configuration"""
        if scenario_config.get("benchmark_mode") != "concurrency":
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

        # Determine backend type from global config
        self.backend_type = global_config.get("backend_type", "rtvi_vlm")

        # Merge scenario-level API params with global based on backend type
        if self.backend_type == "rtvi_embed":
            api_params = self._merge_with_defaults(
                scenario_config.get("generate_video_embeddings_params", {}),
                global_config.get("generate_video_embeddings_params", {}),
            )
            params_key = "generate_video_embeddings_params"
        else:
            api_params = self._merge_with_defaults(
                scenario_config.get("generate_captions_params", {}),
                global_config.get("generate_captions_params", {}),
            )
            params_key = "generate_captions_params"

        return {
            "videos": scenario_config["videos"],
            params_key: api_params,
            "api_params": api_params,
            "vlm_prompts": scenario_config.get("vlm_prompts", global_config.get("vlm_prompts", {})),
            "backend_type": self.backend_type,
            "prompt": scenario_config.get("prompt", global_config.get("prompt", "")),
            "system_prompt": scenario_config.get(
                "system_prompt",
                global_config.get("system_prompt", ""),
            ),
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
            "iterations": scenario_config.get("iterations", 3),
        }

    def execute(self, config: Dict, scenario_name: str) -> Dict[str, Any]:
        """Execute concurrency benchmark"""

        global_config = self.parse_global_config(config)
        benchmark_config = self.parse_benchmark_config(
            config["test_scenarios"][scenario_name], global_config
        )

        backend_label = "video embeddings" if self.backend_type == "rtvi_embed" else "VLM captions"
        self.logger.info(f"Starting {backend_label} concurrency benchmark: {scenario_name}")

        scenario_dir = self.setup_scenario_directory(scenario_name)
        model_name = self.get_available_models()

        execution_results = {
            "scenario_name": scenario_name,
            "benchmark_mode": "concurrency",
            "backend_type": self.backend_type,
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
                    if self.backend_type == "rtvi_embed":
                        test_result = self._execute_embeddings_test_case(
                            test_case_id,
                            video_config,
                            chunk_size,
                            benchmark_config,
                            model_name,
                            scenario_dir,
                        )
                    else:
                        test_result = self._execute_vlm_captions_test_case(
                            test_case_id,
                            video_config,
                            chunk_size,
                            benchmark_config,
                            model_name,
                            scenario_dir,
                        )
                    execution_results["test_cases"].append(test_result)
                    execution_results["successful_test_cases"] += 1
                    self.logger.info(f"Test case {test_case_id} completed successfully")

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
            f"Concurrency benchmark completed: {execution_results['successful_test_cases']}/"
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
        return f"concurrency_{name}_{chunk_size}sec"

    def _execute_vlm_captions_test_case(
        self,
        test_case_id: str,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        scenario_dir: str,
    ) -> Dict[str, Any]:
        """Run VLM captions test case with multiple iterations and aggregate results."""
        return self._execute_concurrency_test_case_with_iterations(
            test_case_id,
            video_config,
            chunk_size,
            benchmark_config,
            model_name,
            scenario_dir,
            backend="vlm_captions",
        )

    def _execute_embeddings_test_case(
        self,
        test_case_id: str,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        scenario_dir: str,
    ) -> Dict[str, Any]:
        """Run embeddings test case with multiple iterations and aggregate results."""
        return self._execute_concurrency_test_case_with_iterations(
            test_case_id,
            video_config,
            chunk_size,
            benchmark_config,
            model_name,
            scenario_dir,
            backend="embeddings",
        )

    def _execute_concurrency_test_case_with_iterations(
        self,
        test_case_id: str,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        scenario_dir: str,
        backend: str,
    ) -> Dict[str, Any]:
        """Shared iterations wrapper for vlm_captions and embeddings test cases."""
        test_case_dir = os.path.join(scenario_dir, test_case_id)
        os.makedirs(test_case_dir, exist_ok=True)

        iterations = benchmark_config.get("iterations", 3)
        iteration_results = []

        for iteration in range(1, iterations + 1):
            iteration_dir = os.path.join(test_case_dir, f"iteration_{iteration}")
            os.makedirs(iteration_dir, exist_ok=True)
            try:
                result = self._execute_concurrency_iteration(
                    iteration,
                    video_config,
                    chunk_size,
                    benchmark_config,
                    model_name,
                    iteration_dir,
                    backend,
                )
                iteration_results.append(result)
                self.logger.info(f"Iteration {iteration}/{iterations} completed for {test_case_id}")
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
                    "mean_total_processing_time_seconds": _mean("total_processing_time_seconds"),
                    "std_total_processing_time_seconds": _std("total_processing_time_seconds"),
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

        results = {
            "test_case_id": test_case_id,
            "benchmark_mode": "concurrency",
            "backend_type": backend,
            "video_file": video_config["filepath"],
            "chunk_size": chunk_size,
            "concurrency_levels": video_config.get("concurrency_levels", []),
            "iterations": iterations,
            "successful_iterations": len(successful),
            "success": len(successful) > 0,
            "iteration_results": iteration_results,
            "concurrency_summary": concurrency_summary,
        }

        summary_file = os.path.join(test_case_dir, "test_case_summary.json")
        self.save_json_data(self.round_floats(results), summary_file)

        self.logger.info(
            f"Test case {test_case_id}: {len(successful)}/{iterations} iterations successful"
        )
        return results

    def _execute_concurrency_iteration(
        self,
        iteration: int,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        iteration_dir: str,
        backend: str,
    ) -> Dict[str, Any]:
        """Execute one iteration across all concurrency levels."""
        self.logger.info(
            f"Starting iteration {iteration}: "
            f"levels {video_config['concurrency_levels']}, chunk {chunk_size}s, backend={backend}"
        )

        concurrency_results = []
        single_level_fn = (
            self._test_single_embeddings_concurrency_level
            if backend == "embeddings"
            else self._test_single_concurrency_level
        )
        optimal_fn = (
            self._find_optimal_embeddings_concurrency_for_target_latency
            if backend == "embeddings"
            else self._find_optimal_concurrency_for_target_latency
        )
        results_filename = (
            "embeddings_results.json" if backend == "embeddings" else "vlm_captions_results.json"
        )

        for concurrency_level in video_config["concurrency_levels"]:
            level_result = single_level_fn(
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

        target_latency = video_config.get("target_latency_seconds", 0.0)
        optimal_result = None
        if target_latency > 0.0:
            optimal_result = optimal_fn(
                video_config,
                concurrency_results,
                iteration_dir,
                chunk_size,
                benchmark_config,
                model_name,
                target_latency,
            )
            if optimal_result and "all_test_results" in optimal_result:
                for test_result in optimal_result["all_test_results"]:
                    concurrency_level = test_result.get("concurrency_level")
                    already_tested = any(
                        r["concurrency_level"] == concurrency_level for r in concurrency_results
                    )
                    if not already_tested:
                        concurrency_results.append(test_result)

        concurrency_results.sort(key=lambda x: x.get("concurrency_level", 0))
        all_tested_levels = sorted(
            list(set(r.get("concurrency_level", 0) for r in concurrency_results))
        )

        results = {
            "iteration": iteration,
            "benchmark_mode": "concurrency",
            "backend_type": backend,
            "video_file": video_config["filepath"],
            "chunk_size": chunk_size,
            "concurrency_levels_tested": all_tested_levels,
            "concurrency_results": concurrency_results,
            "optimal_target_concurrency": optimal_result,
            "target_latency_seconds": target_latency,
            "success": len(concurrency_results) > 0,
        }

        results_file = os.path.join(iteration_dir, results_filename)
        self.save_json_data(self.round_floats(results), results_file)

        return results

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

        # Start GPU monitoring
        self.start_gpu_monitoring()

        # Clear latency tracker
        self.latency_tracker.clear()

        try:
            # Launch concurrent requests
            e2e_start_time = time.time()
            executor = ThreadPoolExecutor(max_workers=concurrency_level)
            futures = []
            total_requests = concurrency_level * 10

            for i in range(total_requests):
                future = executor.submit(
                    self._process_single_vlm_caption,
                    video_config,
                    chunk_size,
                    benchmark_config,
                    model_name,
                    i,
                    test_case_dir,
                )
                futures.append(future)

            # Wait for completion and collect results
            completed_files = 0
            failed_files = 0
            processing_times = []

            for future in futures:
                try:
                    result = future.result()
                    if result.get("success", False):
                        completed_files += 1
                        processing_times.append(result.get("processing_time", 0))
                    else:
                        failed_files += 1
                except Exception as e:
                    self.logger.error(f"VLM caption generation failed: {e}")
                    failed_files += 1

            e2e_end_time = time.time()
            total_processing_time_seconds = e2e_end_time - e2e_start_time
            executor.shutdown(wait=True)
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
                "total_processing_time_seconds": total_processing_time_seconds,
                "completed_files": completed_files,
                "failed_files": failed_files,
                "throughput_files_per_second": (
                    completed_files / total_processing_time_seconds
                    if total_processing_time_seconds > 0
                    else 0
                ),
                "p50_latency": p50_latency,
                "p75_latency": p75_latency,
                "p90_latency": p90_latency,
                "p95_latency": p95_latency,
                "p99_latency": p99_latency,
                "avg_latency": latency_stats.get("avg_latency", 0),
                "latency_history": latency_history,
            }

        finally:
            # Stop GPU monitoring and export data
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

        self.logger.info(
            f"Concurrency {concurrency_level} ({total_requests} requests): "
            f"{completed_files}/{total_requests} completed, "
            f"Total processing time: {total_processing_time_seconds:.3f}s, "
            f"avg: {result.get('avg_latency', 0):.2f}s, "
            f"p50: {result.get('p50_latency', 0):.2f}s, p75: {result.get('p75_latency', 0):.2f}s, "
            f"p90: {result.get('p90_latency', 0):.2f}s, p95: {result.get('p95_latency', 0):.2f}s, "
            f"p99: {result.get('p99_latency', 0):.2f}s"
        )
        time.sleep(2)
        return self.round_floats(result)

    def _test_single_embeddings_concurrency_level(
        self,
        test_case_id: str,
        video_config: Dict,
        chunk_size: int,
        concurrency_level: int,
        benchmark_config: Dict,
        model_name: str,
        test_case_dir: str,
    ) -> Dict[str, Any]:
        """Test a single concurrency level for embeddings"""
        self.logger.debug(f"Testing embeddings concurrency level: {concurrency_level}")

        # Configure connection pool
        self._configure_http_session(concurrency_level + 50)

        # Start GPU monitoring
        self.start_gpu_monitoring()

        # Clear latency tracker
        self.latency_tracker.clear()

        try:
            # Launch concurrent requests
            e2e_start_time = time.time()
            executor = ThreadPoolExecutor(max_workers=concurrency_level)
            futures = []
            total_requests = concurrency_level * 10

            # Upload file
            files = {
                "filename": (None, os.path.abspath(video_config["filepath"])),
                "purpose": (None, "vision"),
                "media_type": (None, "video"),
            }

            file_ids = []
            for i in range(total_requests):
                file_response = self.make_api_call("/files", method="POST", files=files)
                file_id = file_response.json().get("id")
                self.active_resources.append(f"file_{file_id}")
                file_ids.append(file_id)

            for i in range(total_requests):
                future = executor.submit(
                    self._process_single_embedding,
                    video_config,
                    chunk_size,
                    benchmark_config,
                    model_name,
                    i,
                    test_case_dir,
                    file_ids[i],
                )
                futures.append(future)

            # Wait for completion and collect results
            completed_files = 0
            failed_files = 0
            processing_times = []

            for future in futures:
                try:
                    result = future.result()
                    if result.get("success", False):
                        completed_files += 1
                        processing_times.append(result.get("processing_time", 0))
                    else:
                        failed_files += 1
                except Exception:
                    # Keep the benchmark running, but log full traceback for diagnosis.
                    self.logger.exception("Embedding generation failed")
                    failed_files += 1

            e2e_end_time = time.time()
            total_processing_time_seconds = e2e_end_time - e2e_start_time
            executor.shutdown(wait=True)
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
                "total_processing_time_seconds": total_processing_time_seconds,
                "completed_files": completed_files,
                "failed_files": failed_files,
                "throughput_files_per_second": (
                    completed_files / total_processing_time_seconds
                    if total_processing_time_seconds > 0
                    else 0
                ),
                "p50_latency": p50_latency,
                "p75_latency": p75_latency,
                "p90_latency": p90_latency,
                "p95_latency": p95_latency,
                "p99_latency": p99_latency,
                "avg_latency": latency_stats.get("avg_latency", 0),
                "latency_history": latency_history,
            }

            # Cleanup files — one batch GET to check existence, then DELETE only active ones
            active_file_ids = self._fetch_active_file_ids()
            for file_id in file_ids:
                try:
                    if file_id in active_file_ids:
                        self.make_api_call(f"/files/{file_id}", method="DELETE")
                    else:
                        self.logger.debug(f"File {file_id} already gone, skipping DELETE")
                    if f"file_{file_id}" in self.active_resources:
                        self.active_resources.remove(f"file_{file_id}")
                except Exception as e:
                    self.logger.error(f"Failed to cleanup file {file_id}: {e}")

        finally:
            # Stop GPU monitoring and export data
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

        self.logger.info(
            f"Embeddings concurrency {concurrency_level} ({total_requests} requests): "
            f"{completed_files}/{total_requests} completed, "
            f"Total processing time: {total_processing_time_seconds:.3f}s, "
            f"avg: {result.get('avg_latency', 0):.2f}s, "
            f"p50: {result.get('p50_latency', 0):.2f}s, p75: {result.get('p75_latency', 0):.2f}s, "
            f"p90: {result.get('p90_latency', 0):.2f}s, p95: {result.get('p95_latency', 0):.2f}s, "
            f"p99: {result.get('p99_latency', 0):.2f}s, "
            f"throughput: {result.get('throughput_files_per_second', 0):.3f} files/sec"
        )
        time.sleep(2)
        return self.round_floats(result)

    def _process_single_vlm_caption(
        self,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        file_index: int,
        test_case_dir: str,
    ) -> Dict[str, Any]:
        """Process a single file for VLM caption generation"""
        file_id = None
        try:
            start_time = time.time()

            # Upload file
            files = {
                "filename": (None, os.path.abspath(video_config["filepath"])),
                "purpose": (None, "vision"),
                "media_type": (None, "video"),
            }

            file_response = self.make_api_call("/files", method="POST", files=files)
            file_id = file_response.json().get("id")
            self.active_resources.append(f"file_{file_id}")

            params = merge_vlm_params(self._merge_with_defaults, video_config, benchmark_config)
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
                f"VLM {api_mode} request payload:\n{json.dumps(request_data, indent=2)}"
            )

            vlm_response = self.make_api_call(endpoint, method="POST", data=request_data)
            processing_time = time.time() - start_time
            self.latency_tracker.record_latency(processing_time, f"file_{file_index}")

            self.save_response(
                vlm_response,
                test_case_dir,
                f"vlm_{response_label}_response_file_{file_index}.json",
            )

            return {"success": True, "processing_time": processing_time, "file_id": file_id}

        except Exception as e:
            self.logger.error(f"Error processing VLM captions for file {file_index}: {e}")
            return {"success": False, "error": str(e)}
        finally:
            # Cleanup: DELETE directly — no preflight GET /v1/files to avoid the thundering-herd
            # where all N workers fire _fetch_active_file_ids() simultaneously at concurrency drain.
            # 404 = already gone; 409 = server still processing (it will self-clean).
            if file_id and f"file_{file_id}" in self.active_resources:
                try:
                    self.make_api_call(f"/files/{file_id}", method="DELETE")
                except requests.exceptions.HTTPError as e:
                    self.logger.debug(f"File {file_id} cleanup: {e}")
                except Exception as e:
                    self.logger.warning(f"Failed to cleanup file {file_id}: {e}")
                if f"file_{file_id}" in self.active_resources:
                    self.active_resources.remove(f"file_{file_id}")

    def _process_single_embedding(
        self,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        file_index: int,
        test_case_dir: str,
        file_id: str,
    ) -> Dict[str, Any]:
        """Process a single file for video embedding generation"""
        try:
            # Get embeddings params
            params = self._merge_with_defaults(
                video_config.get("generate_video_embeddings_params", {}),
                benchmark_config.get("generate_video_embeddings_params", {}),
            )

            # Generate video embeddings
            request_data = {
                "id": [file_id],
                "model": params.get("model", model_name),
                "chunk_duration": chunk_size,
                "stream": False,
            }

            # Add optional parameters if user configured them
            if "chunk_overlap_duration" in params:
                request_data["chunk_overlap_duration"] = params["chunk_overlap_duration"]
            if "enable_metadata" in params:
                request_data["enable_metadata"] = params["enable_metadata"]

            self.logger.debug(
                f"Video embeddings request payload:\n{json.dumps(request_data, indent=2)}"
            )

            start_time = time.time()

            embeddings_response = self.make_api_call(
                "/generate_video_embeddings", method="POST", data=request_data
            )
            processing_time = time.time() - start_time
            self.logger.debug(f"Processing time: {processing_time} seconds")
            self.latency_tracker.record_latency(processing_time, f"file_{file_index}")

            self.save_response(
                embeddings_response, test_case_dir, f"embeddings_response_file_{file_index}.json"
            )

            return {"success": True, "processing_time": processing_time, "file_id": file_id}

        except Exception as e:
            # Keep the benchmark running, but log full traceback for diagnosis.
            self.logger.exception("Embedding generation failed")
            return {"success": False, "error": str(e)}
        finally:
            pass

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
        Find the optimal concurrency level that achieves the target P99 latency
        using interpolation/extrapolation from existing results.
        """
        if len(concurrency_results) < 2:
            self.logger.warning("Not enough data points to extrapolate optimal concurrency")
            return {"error": "Insufficient data points"}

        self.logger.info(f"Finding optimal concurrency for {target_latency}-second P99 latency...")

        # Extract data points (concurrency, p99_latency)
        data_points = [
            (result["concurrency_level"], result["p99_latency"])
            for result in concurrency_results
            if result.get("p99_latency", 0) > 0
        ]

        if len(data_points) < 2:
            return {"error": "No valid P99 latency data points"}

        # Sort by concurrency level
        data_points.sort(key=lambda x: x[0])

        # Use binary search to find optimal concurrency for target P99 latency
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
            f"vlm_captions_{os.path.splitext(os.path.basename(video_config['filepath']))[0]}_{chunk_size}sec",
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
                f"vlm_captions_{os.path.splitext(os.path.basename(video_config['filepath']))[0]}"
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
                    f"vlm_captions_{os.path.splitext(os.path.basename(video_config['filepath']))[0]}"
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
            f"Best latency: {best_result.get('p99_latency', 0):.2f}s"
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

    def _find_optimal_embeddings_concurrency_for_target_latency(
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
        Find the optimal concurrency level for embeddings that achieves the target P99 latency
        using interpolation/extrapolation from existing results.
        """
        if len(concurrency_results) < 2:
            self.logger.warning("Not enough data points to extrapolate optimal concurrency")
            return {"error": "Insufficient data points"}

        self.logger.info(
            f"Finding optimal embeddings concurrency for {target_latency}-second P99 latency..."
        )

        # Extract data points (concurrency, p99_latency)
        data_points = [
            (result["concurrency_level"], result["p99_latency"])
            for result in concurrency_results
            if result.get("p99_latency", 0) > 0
        ]

        if len(data_points) < 2:
            return {"error": "No valid P99 latency data points"}

        # Sort by concurrency level
        data_points.sort(key=lambda x: x[0])

        # Use binary search to find optimal concurrency for target P99 latency
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
        current_result = self._test_single_embeddings_concurrency_level(
            f"embeddings_{os.path.splitext(os.path.basename(video_config['filepath']))[0]}_{chunk_size}sec",
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
                f"embeddings_{os.path.splitext(os.path.basename(video_config['filepath']))[0]}"
                f"_{chunk_size}sec"
            )
            current_result = self._test_single_embeddings_concurrency_level(
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
                    f"embeddings_{os.path.splitext(os.path.basename(video_config['filepath']))[0]}"
                    f"_{chunk_size}sec"
                )
                current_result = self._test_single_embeddings_concurrency_level(
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
            f"Best latency: {best_result.get('p99_latency', 0):.2f}s"
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
        """Generate Excel report from concurrency benchmark results"""
        self.logger.debug(f"Analyzing concurrency results from: {results_dir}")

        # Load execution summary
        summary_file = os.path.join(results_dir, "execution_summary.json")
        if not os.path.exists(summary_file):
            raise FileNotFoundError(f"Execution summary not found: {summary_file}")

        with open(summary_file, "r") as f:
            execution_summary = json.load(f)

        backend_type = execution_summary.get("backend_type", "rtvi_vlm")

        # Parse all test case results
        all_concurrency_results = []

        for test_case in execution_summary["test_cases"]:
            if not test_case.get("success", False):
                continue

            test_case_id = test_case["test_case_id"]
            test_case_dir = os.path.join(results_dir, test_case_id)

            # Load aggregated results (test_case_summary.json written by _execute_concurrency_test_case)
            results_file = os.path.join(test_case_dir, "test_case_summary.json")
            if not os.path.exists(results_file):
                self.logger.warning(f"No test_case_summary.json found for {test_case_id}")
                continue

            with open(results_file, "r") as f:
                tc_results = json.load(f)

            # concurrency_summary is a dict keyed by str(level) with mean_* aggregated fields.
            # Filter to configured levels only (excludes binary-search probe levels).
            configured_levels = set(tc_results.get("concurrency_levels", []))
            if not configured_levels:
                # Fallback for old JSON without concurrency_levels: levels present in ALL
                # successful iterations are configured; probe levels appear in only 1-2.
                iter_results = [
                    it for it in tc_results.get("iteration_results", []) if it.get("success")
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
            summary_items = tc_results.get("concurrency_summary", {}).values()
            if configured_levels:
                summary_items = [
                    r for r in summary_items if r.get("concurrency_level") in configured_levels
                ]
            for concurrency_result in summary_items:
                concurrency_level = concurrency_result.get("concurrency_level", 0)
                test_case_id_with_concurrency = f"{test_case_id}_c{concurrency_level}"

                result_backend_type = tc_results.get("backend_type", backend_type)
                gpu_usage_mean_key = (
                    "inference_gpu_usage_mean"
                    if result_backend_type == "embeddings"
                    else "vlm_gpu_usage_mean"
                )
                gpu_memory_mean_key = (
                    "inference_gpu_memory_mean"
                    if result_backend_type == "embeddings"
                    else "vlm_gpu_memory_mean"
                )
                gpu_usage_p90_key = (
                    "inference_gpu_usage_p90"
                    if result_backend_type == "embeddings"
                    else "vlm_gpu_usage_p90"
                )
                nvdec_usage_mean_key = (
                    "inference_nvdec_usage_mean"
                    if result_backend_type == "embeddings"
                    else "vlm_nvdec_usage_mean"
                )
                nvdec_usage_p90_key = (
                    "inference_nvdec_usage_p90"
                    if result_backend_type == "embeddings"
                    else "vlm_nvdec_usage_p90"
                )

                level_result = {
                    "test_case_id": test_case_id_with_concurrency,
                    "benchmark_mode": "concurrency",
                    "backend_type": result_backend_type,
                    "video_file": os.path.basename(tc_results.get("video_file", "")),
                    "chunk_size": tc_results.get("chunk_size", 0),
                    "concurrency_level": concurrency_level,
                    "iterations": tc_results.get("successful_iterations", 0),
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
                    gpu_usage_mean_key: concurrency_result.get("mean_vlm_gpu_usage_mean", 0),
                    gpu_memory_mean_key: concurrency_result.get("mean_vlm_gpu_memory_mean", 0),
                    gpu_usage_p90_key: concurrency_result.get("mean_vlm_gpu_usage_p90", 0),
                    nvdec_usage_mean_key: concurrency_result.get("mean_vlm_nvdec_usage_mean", 0),
                    nvdec_usage_p90_key: concurrency_result.get("mean_vlm_nvdec_usage_p90", 0),
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

            # Create separate sheets for each test case
            test_case_groups = {}
            for result in all_concurrency_results:
                base_test_case = result["test_case_id"].rsplit("_c", 1)[0]
                if base_test_case not in test_case_groups:
                    test_case_groups[base_test_case] = []
                test_case_groups[base_test_case].append(result)

            for tc_id, test_case_data in test_case_groups.items():
                test_case_df = pd.DataFrame(test_case_data)
                sheet_name = tc_id[:31]
                test_case_df.to_excel(writer, sheet_name=sheet_name, index=False)

        # Add plots to Excel file
        try:
            backend_label = "embeddings" if backend_type == "rtvi_embed" else "VLM captions"
            self.logger.debug(f"Adding plots to {backend_label} concurrency Excel report...")
            if all_concurrency_results:
                detail_df = pd.DataFrame(all_concurrency_results)
                x_column = "test_case_id"
                latency_columns = [
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

        backend_label = "embeddings" if backend_type == "rtvi_embed" else "VLM captions"
        self.logger.debug(f"{backend_label} concurrency results analysis completed: {output_file}")
