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
Text Embedding Concurrency Benchmark Implementation

Tests concurrent batched text embedding requests at multiple parallelism levels.
Measures latency and throughput for the /generate_text_embeddings endpoint.

For a given concurrency_level (total texts):
  1. Replicate the texts pool by cycling to produce exactly concurrency_level texts.
  2. Divide into batches of ≤100: ceil(concurrency_level / 100) API calls.
  3. Fire all batches concurrently via ThreadPoolExecutor.

Example: concurrency_level=256 → 3 parallel API calls: [100, 100, 56 texts].
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


class TextEmbeddingConcurrencyBenchmark(BenchmarkBase):
    """Text embedding concurrency benchmark - concurrent batched text embedding requests"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.latency_tracker = LatencyTracker()

    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Dict[str, Any]:
        """Parse text embedding concurrency benchmark configuration"""
        if scenario_config.get("benchmark_mode") != "text_embedding_concurrency":
            raise ValueError(f"Invalid benchmark mode: {scenario_config.get('benchmark_mode')}")

        if "text_inputs" not in scenario_config:
            raise ValueError("Missing 'text_inputs' field in scenario config")

        for i, text_input_config in enumerate(scenario_config["text_inputs"]):
            if "texts" not in text_input_config:
                raise ValueError(f"Missing 'texts' in text_input {i}")
            if (
                not isinstance(text_input_config["texts"], list)
                or len(text_input_config["texts"]) < 1
            ):
                raise ValueError(f"'texts' in text_input {i} must be a non-empty list")
            if "concurrency_levels" not in text_input_config:
                raise ValueError(f"Missing 'concurrency_levels' in text_input {i}")
            if not all(
                isinstance(c, int) and c > 0 for c in text_input_config["concurrency_levels"]
            ):
                raise ValueError(
                    f"'concurrency_levels' in text_input {i} must be a list of positive integers"
                )

        # Merge scenario-level API params with global
        api_params = self._merge_with_defaults(
            scenario_config.get("generate_text_embeddings_params", {}),
            global_config.get("generate_text_embeddings_params", {}),
        )

        return {
            "text_inputs": scenario_config["text_inputs"],
            "generate_text_embeddings_params": api_params,
        }

    def execute(self, config: Dict, scenario_name: str) -> Dict[str, Any]:
        """Execute text embedding concurrency benchmark"""
        self.logger.info(f"Starting text embedding concurrency benchmark: {scenario_name}")

        global_config = self.parse_global_config(config)
        benchmark_config = self.parse_benchmark_config(
            config["test_scenarios"][scenario_name], global_config
        )

        scenario_dir = self.setup_scenario_directory(scenario_name)
        model_name = self.get_available_models()

        # Warmup: one text embedding request before the benchmark
        self._run_warmup(model_name, benchmark_config)

        execution_results = {
            "scenario_name": scenario_name,
            "benchmark_mode": "text_embedding_concurrency",
            "scenario_dir": scenario_dir,
            "test_cases": [],
            "total_test_cases": 0,
            "successful_test_cases": 0,
            "failed_test_cases": 0,
        }

        for text_input_config in benchmark_config["text_inputs"]:
            name = text_input_config.get("name", f"input_{execution_results['total_test_cases']}")
            test_case_id = f"text_embed_conc_{name}"
            execution_results["total_test_cases"] += 1

            try:
                test_result = self._execute_text_embedding_concurrency_test_case(
                    test_case_id,
                    text_input_config,
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
        self.save_json_data(self.round_floats(execution_results, 3), summary_file)

        self.logger.info(
            f"Text embedding concurrency benchmark completed: "
            f"{execution_results['successful_test_cases']}/"
            f"{execution_results['total_test_cases']} test cases successful"
        )

        return execution_results

    def _run_warmup(self, model_name: str, benchmark_config: Dict) -> None:
        """Run a single warmup text embedding request before the benchmark."""
        text_inputs = benchmark_config.get("text_inputs", [])
        if text_inputs and text_inputs[0].get("texts"):
            warmup_text = text_inputs[0]["texts"][0]
        else:
            warmup_text = "warmup"
        params = benchmark_config.get("generate_text_embeddings_params", {})
        effective_model = params.get("model", model_name)
        request_data = {"text_input": warmup_text, "model": effective_model}
        self.logger.info("Running warmup text embedding request...")
        try:
            self.make_api_call("/generate_text_embeddings", method="POST", data=request_data)
            self.logger.info("Warmup text embedding request completed")
        except Exception as e:
            self.logger.warning(f"Warmup request failed (benchmark will continue): {e}")

    def _execute_text_embedding_concurrency_test_case(
        self,
        test_case_id: str,
        text_input_config: Dict,
        benchmark_config: Dict,
        model_name: str,
        scenario_dir: str,
    ) -> Dict[str, Any]:
        """Execute text embedding concurrency test case for all concurrency levels"""
        self.logger.info(
            f"Starting text embedding concurrency test: {test_case_id} "
            f"with levels {text_input_config['concurrency_levels']}"
        )

        test_case_dir = os.path.join(scenario_dir, test_case_id)
        os.makedirs(test_case_dir, exist_ok=True)

        concurrency_results = []

        # Test each concurrency level
        for concurrency_level in text_input_config["concurrency_levels"]:
            level_result = self._test_single_text_embedding_concurrency_level(
                test_case_id,
                text_input_config,
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

        # Optional: binary search for optimal concurrency (if target_latency_seconds set)
        target_latency = text_input_config.get("target_latency_seconds", 0.0)
        optimal_result = None
        if target_latency > 0.0:
            optimal_result = self._find_optimal_text_embedding_concurrency_for_target_latency(
                text_input_config,
                concurrency_results,
                test_case_dir,
                benchmark_config,
                model_name,
                target_latency,
            )

            # Merge binary-search results into concurrency_results (dedup by level)
            if optimal_result and "all_test_results" in optimal_result:
                for test_result in optimal_result["all_test_results"]:
                    concurrency_level = test_result.get("concurrency_level")
                    already_tested = any(
                        result["concurrency_level"] == concurrency_level
                        for result in concurrency_results
                    )
                    if not already_tested:
                        concurrency_results.append(test_result)

        # Sort by concurrency_level
        concurrency_results.sort(key=lambda x: x.get("concurrency_level", 0))

        # Extract all tested concurrency levels
        all_tested_levels = sorted(
            list(set(result.get("concurrency_level", 0) for result in concurrency_results))
        )

        results = {
            "test_case_id": test_case_id,
            "benchmark_mode": "text_embedding_concurrency",
            "concurrency_levels_tested": all_tested_levels,
            "concurrency_results": concurrency_results,
            "optimal_target_concurrency": optimal_result,
            "target_latency_seconds": target_latency,
            "success": len(concurrency_results) > 0,
        }

        results_file = os.path.join(test_case_dir, "text_embedding_concurrency_results.json")
        self.save_json_data(self.round_floats(results, 3), results_file)

        return results

    def _test_single_text_embedding_concurrency_level(
        self,
        test_case_id: str,
        text_input_config: Dict,
        concurrency_level: int,
        benchmark_config: Dict,
        model_name: str,
        test_case_dir: str,
    ) -> Dict[str, Any]:
        """Test a single concurrency level - auto-batches concurrency_level texts into ≤100 per call"""
        self.logger.debug(f"Testing text embedding concurrency level: {concurrency_level}")

        MAX_BATCH_SIZE = 100

        # Configure connection pool
        self._configure_http_session(concurrency_level + 50)

        # Start GPU monitoring
        self.start_gpu_monitoring()

        # Clear latency tracker
        self.latency_tracker.clear()

        result = {}

        try:
            # 1. Replicate texts pool to exactly concurrency_level texts
            texts_pool = text_input_config["texts"]
            all_texts = [texts_pool[i % len(texts_pool)] for i in range(concurrency_level)]

            # 2. Split into batches of ≤100
            batches = [
                all_texts[i : i + MAX_BATCH_SIZE] for i in range(0, len(all_texts), MAX_BATCH_SIZE)
            ]
            num_batches = len(batches)

            # Resolve effective model from benchmark config params
            params = benchmark_config.get("generate_text_embeddings_params", {})
            effective_model = params.get("model", model_name)

            # 3. Run the test 10 times and aggregate results
            NUM_RUNS = 10
            all_processing_times = []
            total_completed_texts = 0
            total_failed_batches = 0
            e2e_start = time.time()

            for run in range(NUM_RUNS):
                with ThreadPoolExecutor(max_workers=num_batches) as executor:
                    futures = [
                        executor.submit(
                            self._process_single_text_batch,
                            batch,
                            effective_model,
                            run * num_batches + i,
                        )
                        for i, batch in enumerate(batches)
                    ]
                    for future in futures:
                        try:
                            batch_result = future.result()
                            if batch_result["success"]:
                                all_processing_times.append(batch_result["processing_time"])
                                total_completed_texts += batch_result["num_embeddings"]
                            else:
                                total_failed_batches += 1
                        except Exception as e:
                            self.logger.error(f"Text batch processing failed: {e}")
                            total_failed_batches += 1

            total_processing_time_seconds = time.time() - e2e_start
            processing_times = all_processing_times
            completed_texts = total_completed_texts
            failed_batches = total_failed_batches
            latency_stats = self.latency_tracker.get_stats()

            p90 = float(np.percentile(processing_times, 90)) if processing_times else 0.0
            p95 = float(np.percentile(processing_times, 95)) if processing_times else 0.0
            p99 = float(np.percentile(processing_times, 99)) if processing_times else 0.0
            throughput = (
                completed_texts / total_processing_time_seconds
                if total_processing_time_seconds > 0
                else 0.0
            )

            result = {
                "concurrency_level": concurrency_level,
                "num_batches": num_batches,
                "batch_sizes": [len(b) for b in batches],
                "completed_texts": completed_texts,
                "failed_batches": failed_batches,
                "throughput_texts_per_second": throughput,
                "avg_latency": latency_stats.get("avg_latency", 0),
                "p90_latency": p90,
                "p95_latency": p95,
                "p99_latency": p99,
                "latency_history": self.latency_tracker.get_all_latencies(),
            }

            self.logger.info(
                f"Text embedding concurrency {concurrency_level}: "
                f"{completed_texts} texts in {num_batches} batches over {NUM_RUNS} runs, "
                f"Total processing time: {total_processing_time_seconds:.3f}s, "
                f"avg batch latency: {result.get('avg_latency', 0):.3f}s, "
                f"p90: {p90:.3f}s, p95: {p95:.3f}s, p99: {p99:.3f}s, "
                f"throughput: {throughput:.3f} texts/sec"
            )

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
            if gpu_metrics and result:
                result.update(gpu_metrics)

        time.sleep(2)
        return self.round_floats(result, 3)

    def _process_single_text_batch(
        self,
        texts_batch: List[str],
        model_name: str,
        request_index: int,
    ) -> Dict[str, Any]:
        """Process a single batch of texts for text embedding generation"""
        try:
            request_data = {"text_input": texts_batch, "model": model_name}
            start_time = time.time()
            response = self.make_api_call(
                "/generate_text_embeddings", method="POST", data=request_data
            )
            processing_time = time.time() - start_time
            self.latency_tracker.record_latency(processing_time, f"req_{request_index}")
            num_embeddings = len(response.json().get("data", []))
            return {
                "success": True,
                "processing_time": processing_time,
                "num_texts": len(texts_batch),
                "num_embeddings": num_embeddings,
            }
        except Exception as e:
            self.logger.error(f"Error processing text batch {request_index}: {e}")
            return {"success": False, "error": str(e)}

    def _find_optimal_text_embedding_concurrency_for_target_latency(
        self,
        text_input_config: Dict,
        concurrency_results: List[Dict],
        test_case_dir: str,
        benchmark_config: Dict,
        model_name: str,
        target_latency: float,
    ) -> Dict[str, Any]:
        """
        Find the optimal total text count that achieves the target P99 batch latency
        using linear estimation followed by binary search.
        Only runs if target_latency is set; returns an error dict otherwise.
        """
        if target_latency is None:
            return {"error": "No target_latency_seconds configured"}

        if len(concurrency_results) < 2:
            self.logger.warning("Not enough data points to extrapolate optimal concurrency")
            return {"error": "Insufficient data points"}

        self.logger.info(
            f"Finding optimal text embedding concurrency for {target_latency}-second P99 latency..."
        )

        # Extract data points (concurrency_level, p99_latency)
        data_points = [
            (result["concurrency_level"], result["p99_latency"])
            for result in concurrency_results
            if result.get("p99_latency", 0) > 0
        ]

        if len(data_points) < 2:
            return {"error": "No valid P99 latency data points"}

        # Sort by concurrency level
        data_points.sort(key=lambda x: x[0])

        tolerance = text_input_config.get("target_latency_tolerance", 5.0)
        max_concurrency = 10000  # max texts

        # Calculate initial estimate using linear interpolation
        throughput_factors = []
        for concurrency, latency in data_points:
            if latency > 0:
                throughput_factors.append(concurrency / latency)

        if not throughput_factors:
            return {"error": "Cannot calculate throughput factors"}

        avg_throughput_factor = sum(throughput_factors) / len(throughput_factors)
        initial_estimate = int(round(target_latency * avg_throughput_factor))
        initial_estimate = max(1, min(initial_estimate, max_concurrency))

        self.logger.info(
            f"Starting binary search with initial estimate: {initial_estimate} "
            f"(based on avg throughput factor: {avg_throughput_factor:.3f})"
        )

        low = 1
        high = max_concurrency
        best_result = None
        best_concurrency = initial_estimate
        search_results = []
        all_test_results = []

        # Phase 1: Linear estimation to cross target latency
        current_concurrency = initial_estimate
        current_result = self._test_single_text_embedding_concurrency_level(
            "text_embed_conc_search",
            text_input_config,
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

        # Phase 1: Use linear estimation until we cross target_latency or reach max_concurrency
        while current_latency < target_latency and current_concurrency < max_concurrency:
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
            current_result = self._test_single_text_embedding_concurrency_level(
                "text_embed_conc_search",
                text_input_config,
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

            # Set binary search bounds based on linear estimation results
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
                current_result = self._test_single_text_embedding_concurrency_level(
                    "text_embed_conc_search",
                    text_input_config,
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
            f"Best P99 latency: {best_result.get('p99_latency', 0):.2f}s"
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
        """Generate Excel report from text embedding concurrency benchmark results"""
        self.logger.debug(f"Analyzing text embedding concurrency results from: {results_dir}")

        # Load execution summary
        summary_file = os.path.join(results_dir, "execution_summary.json")
        if not os.path.exists(summary_file):
            raise FileNotFoundError(f"Execution summary not found: {summary_file}")

        with open(summary_file, "r") as f:
            execution_summary = json.load(f)

        all_concurrency_results = []

        for test_case in execution_summary["test_cases"]:
            if not test_case.get("success", False):
                continue

            test_case_id = test_case["test_case_id"]
            test_case_dir = os.path.join(results_dir, test_case_id)

            results_file = os.path.join(test_case_dir, "text_embedding_concurrency_results.json")
            if os.path.exists(results_file):
                with open(results_file, "r") as f:
                    results = json.load(f)

                for concurrency_result in results.get("concurrency_results", []):
                    concurrency_level = concurrency_result.get("concurrency_level", 0)
                    test_case_id_with_concurrency = f"{test_case_id}_c{concurrency_level}"
                    level_result = {
                        "test_case_id": test_case_id_with_concurrency,
                        "benchmark_mode": "text_embedding_concurrency",
                        "concurrency_level": concurrency_level,
                        "num_batches": concurrency_result.get("num_batches", 0),
                        "avg_latency": concurrency_result.get("avg_latency", 0),
                        "p90_latency": concurrency_result.get("p90_latency", 0),
                        "p95_latency": concurrency_result.get("p95_latency", 0),
                        "p99_latency": concurrency_result.get("p99_latency", 0),
                        "throughput_texts_per_second": concurrency_result.get(
                            "throughput_texts_per_second", 0
                        ),
                        "completed_texts": concurrency_result.get("completed_texts", 0),
                        "failed_batches": concurrency_result.get("failed_batches", 0),
                        "inference_gpu_usage_mean": concurrency_result.get("vlm_gpu_usage_mean", 0),
                        "inference_gpu_memory_mean": concurrency_result.get(
                            "vlm_gpu_memory_mean", 0
                        ),
                        "inference_gpu_usage_p90": concurrency_result.get("vlm_gpu_usage_p90", 0),
                    }
                    all_concurrency_results.append(self.round_floats(level_result, 3))

        # Create Excel file
        output_dir = os.path.dirname(output_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
            if all_concurrency_results:
                summary_df = pd.DataFrame(all_concurrency_results)
                summary_df.to_excel(writer, sheet_name="Summary", index=False)

                # Apply 3-decimal-place format to all float columns
                ws = writer.sheets["Summary"]
                float_cols = [
                    col_idx + 1  # openpyxl is 1-indexed
                    for col_idx, col in enumerate(summary_df.columns)
                    if pd.api.types.is_float_dtype(summary_df[col])
                ]
                for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
                    for cell in row:
                        if cell.column in float_cols:
                            cell.number_format = "0.000"

            try:
                gpu_info_df = self.get_gpu_info_dataframe()
                gpu_info_df.to_excel(writer, sheet_name="GPU_Info", index=False)
            except Exception as e:
                self.logger.warning(f"Failed to add GPU info sheet: {e}")

        # Add plots to Excel file
        try:
            self.logger.debug("Adding plots to text embedding concurrency Excel report...")
            if all_concurrency_results:
                df = pd.DataFrame(all_concurrency_results)
                x_column = "test_case_id"
                latency_columns = [
                    "avg_latency",
                    "p90_latency",
                    "p95_latency",
                    "p99_latency",
                ]
                latency_columns = [col for col in latency_columns if col in df.columns]
                if latency_columns:
                    self.add_plots_to_excel(output_file, df, x_column, latency_columns)
                else:
                    self.logger.warning("No latency columns found for plotting")
        except Exception as e:
            self.logger.warning(f"Failed to add plots to Excel: {e}")
            self.logger.debug("Excel file created without plots")

        self.logger.debug(f"Text embedding concurrency results analysis completed: {output_file}")
