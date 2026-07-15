# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
File Burst Benchmark Implementation

Tests concurrent file processing at different concurrency levels.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from base import BenchmarkBase
from latency_tracker import LatencyTracker


class FileBurstBenchmark(BenchmarkBase):
    """File burst benchmark - test concurrent file processing"""

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
            if "url" not in video and "filepath" not in video:
                raise ValueError(f"Missing 'url' or 'filepath' in video {i}")
            if "chunk_sizes" not in video:
                raise ValueError(f"Missing 'chunk_sizes' in video {i}")
            if "concurrency_levels" not in video:
                raise ValueError(f"Missing 'concurrency_levels' in video {i}")

        # Merge scenario-level API params with global
        summarize_params = self._merge_with_defaults(
            scenario_config.get("summarize_api_params", {}),
            global_config.get("summarize_api_params", {}),
        )

        # Validate mandatory fields
        if "events" not in summarize_params:
            raise ValueError("Missing 'events' field in summarize_api_params")
        if "scenario" not in summarize_params:
            raise ValueError("Missing 'scenario' field in summarize_api_params")

        return {
            "videos": scenario_config["videos"],
            "summarize_api_params": summarize_params,
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
                            f"Test case {test_case_id} failed (no successful concurrency level)"
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
            f"File burst benchmark completed: {execution_results['successful_test_cases']}/"
            f"{execution_results['total_test_cases']} test cases successful "
            f"({execution_results['failed_test_cases']} failed)"
        )

        return execution_results

    def _upload_video(self, video_config: Dict) -> str:
        """Upload a video via POST /files. Returns the persistent file_id.

        Requires VIA_DEV_API=true on the server.
        """
        video_source = video_config.get("url") or video_config.get("filepath", "")
        self.logger.info(f"Uploading video via /files: {video_source}")
        url = f"{self.base_url}/files"
        try:
            if "filepath" in video_config:
                filepath = video_config["filepath"]
                with open(filepath, "rb") as f:
                    files = {"file": (os.path.basename(filepath), f, "video/mp4")}
                    data = {"purpose": "vision", "media_type": "video"}
                    response = self.session.post(url, files=files, data=data, timeout=300)
            else:
                data = {"filename": video_config["url"], "purpose": "vision", "media_type": "video"}
                response = self.session.post(url, data=data, timeout=300)
            response.raise_for_status()
            file_id = response.json().get("id")
            if not file_id:
                raise ValueError("No 'id' in /files response")
            self.logger.info(f"Upload completed. File ID: {file_id}")
            return file_id
        except Exception as e:
            self.logger.error(f"Upload via /files failed: {e}")
            raise

    def _delete_video(self, file_id: str) -> None:
        """Delete a previously uploaded video via DELETE /files/{file_id}."""
        try:
            self.logger.info(f"Cleaning up file: {file_id}")
            self.make_api_call(f"/files/{file_id}", method="DELETE")
            self.logger.info(f"Deleted file: {file_id}")
        except Exception as e:
            self.logger.warning(f"Failed to delete file {file_id}: {e}")

    def _generate_test_case_id(self, video_config: Dict, chunk_size: int) -> str:
        """Generate unique test case ID"""
        # Use optional 'name' field if provided, otherwise extract from URL or filepath
        id = ""
        if "name" in video_config:
            id = video_config["name"]
        # Extract filename from URL or filepath
        video_source = video_config.get("url") or video_config.get("filepath", "")
        filename = os.path.basename(video_source.split("?")[0])
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
        """Execute file burst test case for all concurrency levels"""
        self.logger.info(
            f"Starting file burst test: {test_case_id} with levels {video_config['concurrency_levels']}"
        )

        test_case_dir = os.path.join(scenario_dir, test_case_id)
        os.makedirs(test_case_dir, exist_ok=True)

        # Each burst request uses url (new asset) so each gets a different context manager
        # and LLM calls run in parallel across processes.
        video_config_with_id = video_config.copy()

        concurrency_results = []

        # Test each concurrency level
        for concurrency_level in video_config_with_id["concurrency_levels"]:
            level_result = self._test_single_concurrency_level(
                test_case_id,
                video_config_with_id,
                chunk_size,
                concurrency_level,
                benchmark_config,
                model_name,
                test_case_dir,
            )
            if level_result:
                concurrency_results.append(level_result)
            else:
                self.logger.error(f"Failed to test concurrency level {concurrency_level}")
            time.sleep(2)

        # Find optimal concurrency for target latency
        target_latency = video_config_with_id.get("target_latency_seconds", 60.0)
        optimal_result, search_results_list = self._find_optimal_concurrency_for_target_latency(
            video_config_with_id,
            concurrency_results,
            test_case_dir,
            chunk_size,
            benchmark_config,
            model_name,
            target_latency,
        )

        # Add binary search results to concurrency_results
        if search_results_list:
            for test_result in search_results_list:
                concurrency_level = test_result.get("concurrency_level")
                already_tested = any(
                    result["concurrency_level"] == concurrency_level
                    for result in concurrency_results
                )
                if not already_tested:
                    concurrency_results.append(test_result)

        # Sort all results by concurrency level
        concurrency_results.sort(key=lambda x: x.get("concurrency_level", 0))

        # Extract all tested concurrency levels
        all_tested_levels = sorted(
            list(set(result.get("concurrency_level", 0) for result in concurrency_results))
        )

        search_path = []
        if optimal_result and "error" not in optimal_result:
            search_path = optimal_result.get("search_results", [])

        # Save results
        results = {
            "test_case_id": test_case_id,
            "benchmark_mode": "file_burst",
            "video_url": video_config.get("url") or video_config.get("filepath", ""),
            "chunk_size": chunk_size,
            "concurrency_levels_tested": all_tested_levels,
            "concurrency_results": concurrency_results,
            "optimal_target_concurrency": optimal_result,
            "target_latency_seconds": target_latency,
            "search_path": search_path,
            "success": len(concurrency_results) > 0,
        }

        results_file = os.path.join(test_case_dir, "file_burst_results.json")
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
        """Test a single concurrency level.

        Pre-uploads N copies of the video via /files so each concurrent worker
        gets its own persistent asset. This eliminates download latency from
        the burst measurement — only pure inference throughput is timed.
        """
        self.logger.info(f"Testing concurrency level: {concurrency_level}")

        # Configure connection pool
        self._configure_http_session(concurrency_level + 50)

        # Pre-upload N copies of the video (one per concurrent worker)
        file_ids = []
        self.logger.info(
            f"Pre-uploading {concurrency_level} copies via /files..."
        )
        for i in range(concurrency_level):
            file_id = self._upload_video(video_config)
            file_ids.append(file_id)
        self.logger.info(f"Pre-upload complete: {len(file_ids)} assets ready")

        # Start GPU and CPU monitoring
        self.start_gpu_monitoring()
        self.start_cpu_monitoring()

        # Clear latency tracker
        self.latency_tracker.clear()

        try:
            # Launch concurrent requests — each worker gets a pre-uploaded file_id
            e2e_start_time = time.time()
            with ThreadPoolExecutor(max_workers=concurrency_level) as executor:
                futures = []

                for i in range(concurrency_level):
                    future = executor.submit(
                        self._process_single_file_burst,
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
                total_events_detected = 0

                for future in futures:
                    try:
                        result = future.result()
                        if result.get("success", False):
                            completed_files += 1
                            processing_times.append(result.get("processing_time", 0))
                            total_events_detected += result.get("events_detected", 0)
                        else:
                            failed_files += 1
                    except Exception as e:
                        self.logger.error(f"File processing failed: {e}")
                        failed_files += 1

                e2e_end_time = time.time()
                e2e_latency_seconds = e2e_end_time - e2e_start_time
            latency_stats = self.latency_tracker.get_stats()

            # Calculate P90 latency
            if processing_times:
                p90_latency = float(np.percentile(processing_times, 90))
            else:
                p90_latency = 0

            latency_history = self.latency_tracker.get_all_latencies()

            result = {
                "concurrency_level": concurrency_level,
                "e2e_latency_seconds": e2e_latency_seconds,
                "completed_files": completed_files,
                "failed_files": failed_files,
                "total_events_detected": total_events_detected,
                "avg_events_per_file": (
                    total_events_detected / completed_files if completed_files > 0 else 0
                ),
                "throughput_files_per_second": (
                    completed_files / e2e_latency_seconds if e2e_latency_seconds > 0 else 0
                ),
                "p90_latency": p90_latency,
                "avg_latency": latency_stats.get("avg_latency", 0),
                "latency_history": latency_history,
            }

        finally:
            self.stop_gpu_monitoring(
                export_dir=test_case_dir,
                filename_prefix=f"gpu_metrics_concurrency_{concurrency_level}",
            )
            self.stop_cpu_monitoring(
                export_dir=test_case_dir,
                filename_prefix=f"cpu_metrics_concurrency_{concurrency_level}",
            )

            gpu_stats_file = os.path.join(
                test_case_dir, f"gpu_metrics_concurrency_{concurrency_level}_stats.json"
            )
            gpu_metrics = self.process_gpu_stats(gpu_stats_file)
            if gpu_metrics and "result" in locals():
                result.update(gpu_metrics)

            cpu_stats_file = os.path.join(
                test_case_dir, f"cpu_metrics_concurrency_{concurrency_level}_stats.json"
            )
            cpu_metrics = self.process_cpu_stats(cpu_stats_file)
            if cpu_metrics and "result" in locals():
                result.update(cpu_metrics)

        # Clean up all pre-uploaded assets
        for fid in file_ids:
            self._delete_video(fid)

        self.logger.info(
            f"Concurrency {concurrency_level}: "
            f"{completed_files}/{concurrency_level} completed, "
            f"E2E latency: {e2e_latency_seconds:.2f}s, "
            f"avg individual latency: {result.get('avg_latency', 0):.2f}s, "
            f"p90: {result.get('p90_latency', 0):.2f}s"
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
        file_id: str = None,
    ) -> Dict[str, Any]:
        """Process a single file for burst mode.

        Uses pre-uploaded file_id (no download during timed burst).
        """
        try:
            start_time = time.time()

            # Get summarize params
            params = self._merge_with_defaults(
                video_config.get("summarize_api_params", {}),
                benchmark_config["summarize_api_params"],
            )

            # Summarize by id — asset was pre-uploaded via /files
            request_data = {
                "id": file_id,
                "model": model_name,
                "chunk_duration": chunk_size,
                "max_tokens": params["max_tokens"],
                "events": params["events"],
                "scenario": params["scenario"],
            }

            # Add optional parameters if user configured them
            if "vlm_input_width" in params:
                request_data["vlm_input_width"] = params["vlm_input_width"]
            if "vlm_input_height" in params:
                request_data["vlm_input_height"] = params["vlm_input_height"]
            if "chunk_overlap_duration" in params:
                request_data["chunk_overlap_duration"] = params["chunk_overlap_duration"]
            if "num_frames_per_chunk" in params:
                request_data["num_frames_per_chunk"] = params["num_frames_per_chunk"]
            if "temperature" in params:
                request_data["temperature"] = params["temperature"]
            if "min_tokens" in params:
                request_data["min_tokens"] = params["min_tokens"]
            if "ignore_eos" in params:
                request_data["ignore_eos"] = params["ignore_eos"]

            self.logger.debug(
                f"Sending summarize request with payload: {json.dumps(request_data, indent=2)}"
            )

            summarize_response = self.make_api_call("/summarize", method="POST", data=request_data)
            processing_time = time.time() - start_time
            self.latency_tracker.record_latency(processing_time, f"file_{file_index}")

            response_filename = f"summarize_response_file_{file_index}.json"
            self.save_response(summarize_response, test_case_dir, response_filename)

            event_stats = self.extract_and_save_formatted_content(test_case_dir, response_filename)

            return {"success": True, "processing_time": processing_time, **event_stats}

        except Exception as e:
            self.logger.error(f"Error processing file {file_index}: {e}")
            return {"success": False, "error": str(e)}

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
        Find the optimal concurrency level that achieves the target average latency
        using interpolation/extrapolation from existing results.
        """
        if len(concurrency_results) < 2:
            self.logger.warning("Not enough data points to extrapolate optimal concurrency")
            return ({"error": "Insufficient data points"}, [])

        self.logger.info(
            f"Finding optimal concurrency for {target_latency}-second average latency..."
        )

        # Extract data points (concurrency, avg_latency)
        data_points = [
            (result["concurrency_level"], result["avg_latency"])
            for result in concurrency_results
            if result.get("avg_latency", 0) > 0
        ]

        if len(data_points) < 2:
            return ({"error": "No valid average latency data points"}, [])

        # Sort by concurrency level
        data_points.sort(key=lambda x: x[0])

        # Use binary search to find optimal concurrency for target average latency
        tolerance = video_config.get("target_latency_tolerance", 5.0)
        max_concurrency = 500

        # Calculate initial estimate using linear interpolation
        throughput_factors = []
        for concurrency, latency in data_points:
            if latency > 0:
                throughput_factors.append(concurrency / latency)

        if not throughput_factors:
            return ({"error": "Cannot calculate throughput factors"}, [])

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
        video_source = video_config.get("url") or video_config.get("filepath", "")
        video_basename = os.path.splitext(os.path.basename(video_source.split("?")[0]))[0]
        test_name = f"file_burst_{video_basename}_{chunk_size}sec"
        current_result = self._test_single_concurrency_level(
            test_name,
            video_config,
            chunk_size,
            current_concurrency,
            benchmark_config,
            model_name,
            test_case_dir,
        )
        current_latency = current_result.get("avg_latency", 0)

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
                f"file_burst_{os.path.splitext(os.path.basename(video_source.split('?')[0]))[0]}"
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
            current_latency = current_result.get("avg_latency", 0)

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
                best_result.get("avg_latency", 0) - target_latency
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
                    f"file_burst_{os.path.splitext(os.path.basename(video_source.split('?')[0]))[0]}"
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
                current_latency = current_result.get("avg_latency", 0)

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
                    best_result.get("avg_latency", 0) - target_latency
                ):
                    best_result = current_result
                    best_concurrency = current_concurrency

                binary_iteration += 1

        self.logger.info(
            f"Binary search completed. Best concurrency: {best_concurrency}, "
            f"Best latency: {best_result.get('avg_latency', 0):.2f}s"
        )

        return (
            {
                "target_latency_seconds": target_latency,
                "estimated_concurrency": best_concurrency,
                "actual_result": best_result,
                "initial_estimate": initial_estimate,
                "throughput_factor_used": avg_throughput_factor,
                "search_results": search_results,
                "data_points_used": data_points,
            },
            all_test_results,
        )

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

            # Load file burst results
            results_file = os.path.join(test_case_dir, "file_burst_results.json")
            if os.path.exists(results_file):
                with open(results_file, "r") as f:
                    burst_results = json.load(f)

                optimal = burst_results.get("optimal_target_concurrency") or {}
                best_concurrency = optimal.get("estimated_concurrency")

                # Process each concurrency result
                for concurrency_result in burst_results.get("concurrency_results", []):
                    concurrency_level = concurrency_result.get("concurrency_level", 0)
                    test_case_id_with_concurrency = f"{test_case_id}_c{concurrency_level}"

                    level_result = {
                        "test_case_id": test_case_id_with_concurrency,
                        "benchmark_mode": "file_burst",
                        "video_url": burst_results.get("video_url", ""),
                        "chunk_size": burst_results.get("chunk_size", 0),
                        "concurrency_level": concurrency_level,
                        "is_optimal": concurrency_level == best_concurrency,
                        "e2e_latency_seconds": concurrency_result.get("e2e_latency_seconds", 0),
                        "completed_files": concurrency_result.get("completed_files", 0),
                        "failed_files": concurrency_result.get("failed_files", 0),
                        "total_events_detected": concurrency_result.get("total_events_detected", 0),
                        "avg_events_per_file": concurrency_result.get("avg_events_per_file", 0),
                        "throughput_files_per_second": concurrency_result.get(
                            "throughput_files_per_second", 0
                        ),
                        "p90_latency": concurrency_result.get("p90_latency", 0),
                        "avg_latency": concurrency_result.get("avg_latency", 0),
                        "vlm_gpu_usage_mean": concurrency_result.get("vlm_gpu_usage_mean", 0),
                        "vlm_gpu_usage_p90": concurrency_result.get("vlm_gpu_usage_p90", 0),
                        "llm_gpu_usage_mean": concurrency_result.get("llm_gpu_usage_mean", 0),
                        "llm_gpu_usage_p90": concurrency_result.get("llm_gpu_usage_p90", 0),
                        "vlm_nvdec_usage_mean": concurrency_result.get("vlm_nvdec_usage_mean", 0),
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
                    base_test_case = "_".join(result["test_case_id"].split("_")[:-1])
                    if base_test_case not in test_case_groups:
                        test_case_groups[base_test_case] = []
                    test_case_groups[base_test_case].append(result)

                for test_case_id, test_case_data in test_case_groups.items():
                    test_case_df = pd.DataFrame(test_case_data)
                    sheet_name = test_case_id[:31]
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
                    "p90_latency",
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
