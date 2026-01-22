######################################################################################################
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
######################################################################################################
"""
Alert Review Benchmark Implementation

Tests concurrent alert review API calls at different concurrency levels.
"""

import json
import os
import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from base import BenchmarkBase
from latency_tracker import LatencyTracker


class AlertReviewBenchmark(BenchmarkBase):
    """Alert review benchmark - test concurrent /reviewAlert API calls"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.latency_tracker = LatencyTracker()

    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Dict[str, Any]:
        """Parse alert review benchmark configuration"""
        if scenario_config.get("benchmark_mode") != "alert_review_burst":
            raise ValueError(f"Invalid benchmark mode: {scenario_config.get('benchmark_mode')}")

        if "videos" not in scenario_config:
            raise ValueError("Missing 'videos' field in scenario config")

        # Validate video configurations
        for i, video in enumerate(scenario_config["videos"]):
            if "filepath" not in video:
                raise ValueError(f"Missing 'filepath' in video {i}")
            if "concurrency_levels" not in video:
                raise ValueError(f"Missing 'concurrency_levels' in video {i}")
            if "alert_prompts" not in video:
                raise ValueError(f"Missing 'alert_prompts' in video {i}")

        # Merge scenario-level API params with global
        alert_review_params = self._merge_with_defaults(
            scenario_config.get("alert_review_params", {}),
            global_config.get("alert_review_params", {}),
        )

        return {
            "videos": scenario_config["videos"],
            "alert_review_params": alert_review_params,
        }

    def execute(self, config: Dict, scenario_name: str) -> Dict[str, Any]:
        """Execute alert review benchmark"""
        self.logger.info(f"Starting alert review benchmark: {scenario_name}")

        global_config = self.parse_global_config(config)
        benchmark_config = self.parse_benchmark_config(
            config["test_scenarios"][scenario_name], global_config
        )

        scenario_dir = self.setup_scenario_directory(scenario_name)

        execution_results = {
            "scenario_name": scenario_name,
            "benchmark_mode": "alert_review_burst",
            "scenario_dir": scenario_dir,
            "test_cases": [],
            "total_test_cases": 0,
            "successful_test_cases": 0,
            "failed_test_cases": 0,
        }

        # Execute test cases for each video and concurrency level
        for video_config in benchmark_config["videos"]:
            test_case_id = self._generate_test_case_id(video_config)
            execution_results["total_test_cases"] += 1

            try:
                test_result = self._execute_alert_review_test_case(
                    test_case_id, video_config, benchmark_config, scenario_dir
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
            f"Alert review benchmark completed: {execution_results['successful_test_cases']}/"
            f"{execution_results['total_test_cases']} test cases successful"
        )

        return execution_results

    def _generate_test_case_id(self, video_config: Dict) -> str:
        """Generate unique test case ID"""
        # Use optional 'name' field if provided, otherwise use filename
        id = ""
        if "name" in video_config:
            id = video_config["name"]
        filename = os.path.basename(video_config["filepath"])
        name = f"{id}_{os.path.splitext(filename)[0]}" if id else os.path.splitext(filename)[0]
        return f"alert_review_{name}"

    def _execute_alert_review_test_case(
        self,
        test_case_id: str,
        video_config: Dict,
        benchmark_config: Dict,
        scenario_dir: str,
    ) -> Dict[str, Any]:
        """Execute alert review test case for all concurrency levels"""
        self.logger.info(
            f"Starting alert review test: {test_case_id} with levels {video_config['concurrency_levels']}"
        )

        test_case_dir = os.path.join(scenario_dir, test_case_id)
        os.makedirs(test_case_dir, exist_ok=True)

        concurrency_results = []

        # Test each concurrency level
        for concurrency_level in video_config["concurrency_levels"]:
            level_result = self._test_single_concurrency_level(
                test_case_id,
                video_config,
                concurrency_level,
                benchmark_config,
                test_case_dir,
            )
            if level_result:
                concurrency_results.append(level_result)
            else:
                self.logger.error(f"Failed to test concurrency level {concurrency_level}")
            time.sleep(2)

        # Find optimal concurrency for target latency
        target_latency = video_config.get("target_latency_seconds", 60.0)
        optimal_result = self._find_optimal_concurrency_for_target_latency(
            video_config,
            concurrency_results,
            test_case_dir,
            benchmark_config,
            target_latency,
        )

        # Add binary search results to concurrency_results
        if optimal_result and "all_test_results" in optimal_result:
            for test_result in optimal_result["all_test_results"]:
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

        # Save results
        results = {
            "test_case_id": test_case_id,
            "benchmark_mode": "alert_review_burst",
            "video_file": video_config["filepath"],
            "alert_prompts": video_config["alert_prompts"],
            "concurrency_levels_tested": all_tested_levels,
            "concurrency_results": concurrency_results,
            "optimal_target_concurrency": optimal_result,
            "target_latency_seconds": target_latency,
            "success": len(concurrency_results) > 0,
        }

        results_file = os.path.join(test_case_dir, "alert_review_burst_results.json")
        self.save_json_data(self.round_floats(results), results_file)

        return results

    def _test_single_concurrency_level(
        self,
        test_case_id: str,
        video_config: Dict,
        concurrency_level: int,
        benchmark_config: Dict,
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

            for i in range(concurrency_level):
                future = executor.submit(
                    self._process_single_alert_review,
                    video_config,
                    benchmark_config,
                    i,
                    test_case_dir,
                    concurrency_level,
                )
                futures.append(future)

            # Wait for completion and collect results
            completed_alerts = 0
            failed_alerts = 0
            processing_times = []
            review_statuses = []
            verification_results = []

            for future in futures:
                try:
                    result = future.result()
                    if result.get("success", False):
                        completed_alerts += 1
                        processing_times.append(result.get("processing_time", 0))
                        review_statuses.append(result.get("review_status", "UNKNOWN"))
                        verification_results.append(result.get("verification_result", False))
                    else:
                        failed_alerts += 1
                except Exception as e:
                    self.logger.error(f"Alert review processing failed: {e}")
                    failed_alerts += 1

            e2e_end_time = time.time()
            e2e_latency_seconds = e2e_end_time - e2e_start_time
            executor.shutdown(wait=True)
            latency_stats = self.latency_tracker.get_stats()

            # Calculate P90 latency
            if processing_times:
                p90_latency = float(np.percentile(processing_times, 90))
            else:
                p90_latency = 0

            # Calculate success metrics
            successful_reviews = sum(1 for status in review_statuses if status == "SUCCESS")
            failed_reviews = len(review_statuses) - successful_reviews
            total_true_positives = sum(1 for result in verification_results if result)
            total_false_positives = len(verification_results) - total_true_positives

            result = {
                "concurrency_level": concurrency_level,
                "e2e_latency_seconds": e2e_latency_seconds,
                "completed_alerts": completed_alerts,
                "failed_alerts": failed_alerts,
                "throughput_alerts_per_second": (
                    completed_alerts / e2e_latency_seconds if e2e_latency_seconds > 0 else 0
                ),
                "p90_latency": p90_latency,
                "avg_latency": latency_stats.get("avg_latency", 0),
                "successful_reviews": successful_reviews,
                "failed_reviews": failed_reviews,
                "true_positives": total_true_positives,
                "false_positives": total_false_positives,
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
            f"Concurrency {concurrency_level}: "
            f"{result.get('completed_alerts', 0)}/{concurrency_level} completed, "
            f"E2E latency: {result.get('e2e_latency_seconds', 0):.2f}s, "
            f"avg individual latency: {result.get('avg_latency', 0):.2f}s, "
            f"p90: {result.get('p90_latency', 0):.2f}s, "
            f"successful reviews: {result.get('successful_reviews', 0)}, "
            f"true positives: {result.get('true_positives', 0)}"
        )
        time.sleep(2)
        return self.round_floats(result)

    def _process_single_alert_review(
        self,
        video_config: Dict,
        benchmark_config: Dict,
        alert_index: int,
        test_case_dir: str,
        concurrency_level: int,
    ) -> Dict[str, Any]:
        """Process a single alert review"""
        try:
            start_time = time.time()

            # Generate unique ID and timestamp for alert
            alert_id = str(uuid.uuid4())
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Get a random prompt from the alert_prompts list
            prompt = random.choice(video_config["alert_prompts"])

            # Get alert review params
            params = self._merge_with_defaults(
                video_config.get("alert_review_params", {}), benchmark_config["alert_review_params"]
            )

            # Get system prompt from config or use default
            system_prompt = video_config.get(
                "system_prompt", "Answer the user's question with yes or no only"
            )

            # Build VLM parameters
            vlm_params = {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "max_tokens": params["max_tokens"],
                "temperature": params["temperature"],
            }

            # Add optional VLM parameters if user configured them
            if "top_p" in params:
                vlm_params["top_p"] = params["top_p"]
            if "top_k" in params:
                vlm_params["top_k"] = params["top_k"]
            if "seed" in params:
                vlm_params["seed"] = params["seed"]

            # Build VSS parameters
            vss_params = {
                "vlm_params": vlm_params,
                "do_verification": params["do_verification"],
            }

            # Add optional VSS parameters if user configured them
            if "num_frames_per_chunk" in params:
                vss_params["num_frames_per_chunk"] = params["num_frames_per_chunk"]
            if "cv_metadata_overlay" in params:
                vss_params["cv_metadata_overlay"] = params["cv_metadata_overlay"]
            if "enable_reasoning" in params:
                vss_params["enable_reasoning"] = params["enable_reasoning"]
            if "debug" in params:
                vss_params["debug"] = params["debug"]
            if "vlm_input_width" in params:
                vss_params["vlm_input_width"] = params["vlm_input_width"]
            if "vlm_input_height" in params:
                vss_params["vlm_input_height"] = params["vlm_input_height"]

            # Build request payload following alert_inspector format
            request_data = {
                "version": "1.0",
                "id": alert_id,
                "@timestamp": timestamp,
                "sensor_id": video_config.get("sensor_id", "benchmark_sensor"),
                "video_path": video_config["filepath"],
                "cv_metadata_path": video_config.get("cv_metadata_path", ""),
                "confidence": video_config.get("confidence", 1.0),
                "alert": {
                    "severity": video_config.get("alert_severity", "MEDIUM"),
                    "status": "REVIEW_PENDING",
                    "type": video_config.get("alert_type", "object_detection"),
                    "description": f"Alert review for prompt: {prompt}",
                },
                "event": {
                    "type": video_config.get("event_type", "video_analysis"),
                    "description": f"Video analysis for prompt: {prompt}",
                },
                "vss_params": vss_params,
                "meta_labels": [
                    {"key": "alert_index", "value": str(alert_index)},
                    {"key": "prompt_text", "value": prompt},
                    {
                        "key": "enable_reasoning",
                        "value": str(vss_params.get("enable_reasoning", False)),
                    },
                ],
            }

            self.logger.debug(
                f"Sending /reviewAlert request with payload: {json.dumps(request_data, indent=2)}"
            )

            # Make the /reviewAlert API call
            response = self.make_api_call("/reviewAlert", method="POST", data=request_data)
            result_data = response.json()

            # Save the response
            self.save_response(
                response,
                test_case_dir,
                f"alert_review_response_c{concurrency_level}_alert_{alert_index}.json",
            )

            review_result = result_data.get("result", {})
            review_status = review_result.get("status", "UNKNOWN")
            verification_result = review_result.get("verification_result", None)

            processing_time = time.time() - start_time
            self.latency_tracker.record_latency(processing_time, f"alert_{alert_index}")

            return {
                "success": True,
                "processing_time": processing_time,
                "alert_id": alert_id,
                "prompt": prompt,
                "review_status": review_status,
                "verification_result": verification_result,
            }

        except Exception as e:
            self.logger.error(f"Error processing alert review {alert_index}: {e}")
            return {"success": False, "error": str(e)}

    def _find_optimal_concurrency_for_target_latency(
        self,
        video_config: Dict,
        concurrency_results: List[Dict],
        test_case_dir: str,
        benchmark_config: Dict,
        target_latency: float,
    ) -> Dict[str, Any]:
        """
        Find the optimal concurrency level that achieves the target average latency
        using interpolation/extrapolation from existing results.
        """
        if len(concurrency_results) < 2:
            self.logger.warning("Not enough data points to extrapolate optimal concurrency")
            return {"error": "Insufficient data points"}

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
            return {"error": "No valid average latency data points"}

        # Sort by concurrency level
        data_points.sort(key=lambda x: x[0])

        # Use binary search to find optimal concurrency for target average latency
        tolerance = video_config.get("target_latency_tolerance", 5.0)
        max_concurrency = 2048

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
        all_test_results = []  # Store complete test results for final report

        # Phase 1: Linear estimation to cross target latency
        current_concurrency = initial_estimate
        test_case_id = (
            f"alert_review_{os.path.splitext(os.path.basename(video_config['filepath']))[0]}"
        )
        current_result = self._test_single_concurrency_level(
            test_case_id,
            video_config,
            current_concurrency,
            benchmark_config,
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
                # Be conservative with scaling to avoid overshooting too much
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
            current_result = self._test_single_concurrency_level(
                test_case_id,
                video_config,
                current_concurrency,
                benchmark_config,
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
        elif current_concurrency == 1 and current_latency > target_latency:
            # Already at minimum concurrency but still exceeding target - can't improve
            self.logger.warning(
                f"Cannot achieve target latency {target_latency}s: minimum concurrency (1) "
                f"already produces {current_latency:.2f}s latency"
            )
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
                    low = max(1, prev_result.get("concurrency_level", current_concurrency // 2))
                else:
                    low = max(1, current_concurrency // 2)
                high = current_concurrency

            binary_iteration = 1

            while abs(current_latency - target_latency) > tolerance:
                # Calculate midpoint (ensure at least 1)
                next_concurrency = max(1, (low + high) // 2)

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
                current_result = self._test_single_concurrency_level(
                    test_case_id,
                    video_config,
                    current_concurrency,
                    benchmark_config,
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
        """Generate Excel report from alert review benchmark results"""
        self.logger.debug(f"Analyzing alert review results from: {results_dir}")

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

            # Load alert review results
            results_file = os.path.join(test_case_dir, "alert_review_burst_results.json")
            if os.path.exists(results_file):
                with open(results_file, "r") as f:
                    burst_results = json.load(f)

                # Process each concurrency result
                for concurrency_result in burst_results.get("concurrency_results", []):
                    concurrency_level = concurrency_result.get("concurrency_level", 0)
                    test_case_id_with_concurrency = f"{test_case_id}_c{concurrency_level}"

                    level_result = {
                        "test_case_id": test_case_id_with_concurrency,
                        "benchmark_mode": "alert_review_burst",
                        "video_file": os.path.basename(burst_results.get("video_file", "")),
                        "concurrency_level": concurrency_level,
                        "e2e_latency_seconds": concurrency_result.get("e2e_latency_seconds", 0),
                        "completed_alerts": concurrency_result.get("completed_alerts", 0),
                        "failed_alerts": concurrency_result.get("failed_alerts", 0),
                        "throughput_alerts_per_second": concurrency_result.get(
                            "throughput_alerts_per_second", 0
                        ),
                        "p90_latency": concurrency_result.get("p90_latency", 0),
                        "avg_latency": concurrency_result.get("avg_latency", 0),
                        "successful_reviews": concurrency_result.get("successful_reviews", 0),
                        "failed_reviews": concurrency_result.get("failed_reviews", 0),
                        "true_positives": concurrency_result.get("true_positives", 0),
                        "false_positives": concurrency_result.get("false_positives", 0),
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
            self.logger.debug("Adding plots to alert review Excel report...")
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

        self.logger.debug(f"Alert review results analysis completed: {output_file}")
