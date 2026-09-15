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
Text Embedding Benchmark Implementation

Handles text embedding generation workflow and measures inference latency.
"""

import json
import os
import time
from typing import Any, Dict

import pandas as pd
from base import BenchmarkBase


class TextEmbeddingBenchmark(BenchmarkBase):
    """Text embedding benchmark - generate embeddings for text inputs"""

    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Dict[str, Any]:
        """Parse text embedding benchmark configuration"""
        if scenario_config.get("benchmark_mode") != "text_embedding":
            raise ValueError(f"Invalid benchmark mode: {scenario_config.get('benchmark_mode')}")

        if "text_inputs" not in scenario_config:
            raise ValueError("Missing 'text_inputs' field in scenario config")

        # Validate text input configurations
        for i, text_input_config in enumerate(scenario_config["text_inputs"]):
            if "text" not in text_input_config:
                raise ValueError(f"Missing 'text' in text_input {i}")

        return {
            "iterations": scenario_config.get("iterations", 3),
            "text_inputs": scenario_config["text_inputs"],
        }

    def execute(self, config: Dict, scenario_name: str) -> Dict[str, Any]:
        """Execute text embedding benchmark"""
        self.logger.info(f"Starting text embedding benchmark: {scenario_name}")

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
            "benchmark_mode": "text_embedding",
            "scenario_dir": scenario_dir,
            "test_cases": [],
            "total_test_cases": 0,
            "successful_test_cases": 0,
            "failed_test_cases": 0,
        }

        # Execute test cases for each text input
        for text_input_config in benchmark_config["text_inputs"]:
            test_case_id = self._generate_test_case_id(text_input_config)
            execution_results["total_test_cases"] += 1

            try:
                test_result = self._execute_single_test_case(
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
        self.save_json_data(self.round_floats(execution_results), summary_file)

        self.logger.info(
            f"Text embedding benchmark completed: {execution_results['successful_test_cases']}/"
            f"{execution_results['total_test_cases']} test cases successful"
        )

        return execution_results

    def _generate_test_case_id(self, text_input_config: Dict) -> str:
        """Generate unique test case ID"""
        # Use optional 'name' field if provided
        if "name" in text_input_config:
            return f"text_embedding_{text_input_config['name']}"
        else:
            # Generate name based on text length and first few words
            text = text_input_config["text"]
            # Take first 3 words or up to 30 characters
            words = text.split()[:3]
            short_text = "_".join(words).replace("/", "_").replace("\\", "_")
            if len(short_text) > 30:
                short_text = short_text[:30]
            return f"text_embedding_{short_text}"

    def _run_warmup(self, model_name: str, benchmark_config: Dict) -> None:
        """Run a single warmup text embedding request before the benchmark."""
        text_inputs = benchmark_config.get("text_inputs", [])
        warmup_text = text_inputs[0].get("text", "warmup") if text_inputs else "warmup"
        request_data = {"text_input": warmup_text, "model": model_name}
        self.logger.info("Running warmup text embedding request...")
        try:
            self.make_api_call("/generate_text_embeddings", method="POST", data=request_data)
            self.logger.info("Warmup text embedding request completed")
        except Exception as e:
            self.logger.warning(f"Warmup request failed (benchmark will continue): {e}")

    def _execute_single_test_case(
        self,
        test_case_id: str,
        text_input_config: Dict,
        benchmark_config: Dict,
        model_name: str,
        scenario_dir: str,
    ) -> Dict[str, Any]:
        """Execute a single test case with multiple iterations"""
        test_case_dir = os.path.join(scenario_dir, test_case_id)
        os.makedirs(test_case_dir, exist_ok=True)

        iterations = benchmark_config["iterations"]
        iteration_results = []

        for iteration in range(1, iterations + 1):
            iteration_dir = os.path.join(test_case_dir, f"iteration_{iteration}")
            os.makedirs(iteration_dir, exist_ok=True)

            try:
                result = self._execute_single_iteration(
                    iteration, text_input_config, model_name, iteration_dir
                )
                iteration_results.append(result)
                self.logger.debug(f"Iteration {iteration} completed for {test_case_id}")

            except Exception as e:
                self.logger.error(f"Iteration {iteration} failed for {test_case_id}: {e}")
                iteration_results.append(
                    {"iteration": iteration, "success": False, "error": str(e)}
                )

            # Wait between iterations
            if iteration < iterations:
                time.sleep(1)

        # Calculate aggregated results
        successful_iterations = [r for r in iteration_results if r.get("success", False)]

        test_result = {
            "test_case_id": test_case_id,
            "text_input": text_input_config["text"],
            "iterations": iterations,
            "successful_iterations": len(successful_iterations),
            "success": len(successful_iterations) > 0,
            "iteration_results": iteration_results,
        }

        # Save test case summary
        test_summary_file = os.path.join(test_case_dir, "test_case_summary.json")
        self.save_json_data(self.round_floats(test_result), test_summary_file)

        return test_result

    def _execute_single_iteration(
        self,
        iteration: int,
        text_input_config: Dict,
        model_name: str,
        iteration_dir: str,
    ) -> Dict[str, Any]:
        """Execute a single iteration of the test"""

        # Start GPU monitoring
        self.start_gpu_monitoring()

        try:
            # Run text embeddings generation and get start/end metrics
            request_latency, start_metrics, end_metrics = self._run_text_embeddings_generation(
                text_input_config, model_name, iteration_dir
            )

            # Save start and end metrics to JSON files for debugging
            start_metrics_file = os.path.join(iteration_dir, "metrics_start.json")
            end_metrics_file = os.path.join(iteration_dir, "metrics_end.json")
            self.save_json_data(start_metrics, start_metrics_file)
            self.save_json_data(end_metrics, end_metrics_file)

            # Calculate metrics delta (only for this request)
            # Extract e2e latency - use latest value as it represents this request
            e2e_latency = end_metrics.get("e2e_latency_seconds_latest_seconds", 0)

            # Calculate average chunk latency using delta of sum and count
            start_chunk_sum = start_metrics.get("chunk_latency_seconds_sum", 0)
            start_chunk_count = start_metrics.get("chunk_latency_seconds_count", 0)
            end_chunk_sum = end_metrics.get("chunk_latency_seconds_sum", 0)
            end_chunk_count = end_metrics.get("chunk_latency_seconds_count", 0)

            delta_chunk_sum = end_chunk_sum - start_chunk_sum
            delta_chunk_count = end_chunk_count - start_chunk_count
            chunk_latency = delta_chunk_sum / delta_chunk_count if delta_chunk_count > 0 else 0

            # if delta_chunk_count > 0:
            #     inference_latency = chunk_latency
            # else:
            #     inference_latency = request_latency
            #     e2e_latency = request_latency

            inference_latency = request_latency
            e2e_latency = request_latency

            # Save computed metrics delta
            metrics_delta = {
                "e2e_latency": e2e_latency,
                "chunk_latency": chunk_latency,
                "chunk_latency_sum_delta": delta_chunk_sum,
                "chunk_latency_count_delta": delta_chunk_count,
            }
            delta_metrics_file = os.path.join(iteration_dir, "metrics_delta.json")
            self.save_json_data(metrics_delta, delta_metrics_file)

            # Save test case data for this iteration
            self._save_test_case_data(
                text_input_config,
                iteration,
                iteration_dir,
                model_name,
                e2e_latency,
                inference_latency,
            )

            return {
                "iteration": iteration,
                "success": True,
                "text_embeddings_generation_completed": True,
                "inference_latency": inference_latency,
                "e2e_latency": e2e_latency,
            }

        finally:
            # Stop GPU monitoring and export data
            self.stop_gpu_monitoring(
                export_dir=iteration_dir, filename_prefix=f"gpu_metrics_iter_{iteration}"
            )

    def _run_text_embeddings_generation(
        self,
        text_input_config: Dict,
        model_name: str,
        iteration_dir: str,
    ):
        """Run text embeddings generation and measure inference latency"""

        text_input = text_input_config["text"]

        # Build generate_text_embeddings request
        request_data = {
            "text_input": text_input,
            "model": model_name,
        }

        self.logger.debug(
            "Sending /generate_text_embeddings request "
            f"with payload: {json.dumps(request_data, indent=2)}"
        )

        # Record metrics before the API call
        start_metrics = self.scrape_metrics()

        # Measure inference latency around the API call
        start_time = time.time()
        response = self.make_api_call("/generate_text_embeddings", method="POST", data=request_data)
        end_time = time.time()

        request_latency = end_time - start_time

        # Record metrics after the API call
        end_metrics = self.scrape_metrics()

        # Save response
        self.save_response(response, iteration_dir, "generate_text_embeddings_response.json")

        return request_latency, start_metrics, end_metrics

    def _save_test_case_data(
        self,
        text_input_config: Dict,
        iteration: int,
        iteration_dir: str,
        model_name: str,
        e2e_latency: float,
        inference_latency: float,
    ):
        """Save test case metadata"""
        test_case_data = {
            "id": self._generate_test_case_id(text_input_config),
            "input_data": {
                "text": text_input_config["text"],
                "model": model_name,
                "backend_type": "rtvi_embed",
            },
            "expected_result": {"status": "success"},
            "iteration": iteration,
            "inference_latency": inference_latency,
            "e2e_latency": e2e_latency,
        }

        test_case_file = os.path.join(iteration_dir, "test_case_data.json")
        self.save_json_data(test_case_data, test_case_file)

    def analyze_results(self, results_dir: str, output_file: str) -> None:
        """Generate Excel report from text embedding benchmark results"""
        self.logger.debug(f"Analyzing text embedding results from: {results_dir}")

        # Load execution summary
        summary_file = os.path.join(results_dir, "execution_summary.json")
        if not os.path.exists(summary_file):
            raise FileNotFoundError(f"Execution summary not found: {summary_file}")

        with open(summary_file, "r") as f:
            execution_summary = json.load(f)

        # Parse all test case results
        summary_data = []
        detail_data = []

        for test_case in execution_summary["test_cases"]:
            if not test_case.get("success", False):
                continue

            test_case_id = test_case["test_case_id"]
            test_case_dir = os.path.join(results_dir, test_case_id)

            # Process each iteration
            for iteration_result in test_case["iteration_results"]:
                if not iteration_result.get("success", False):
                    continue

                iteration = iteration_result["iteration"]
                iteration_dir = os.path.join(test_case_dir, f"iteration_{iteration}")

                # Parse iteration data
                iteration_data = self._parse_iteration_data(iteration_dir, test_case, iteration)
                if iteration_data:
                    detail_data.append(iteration_data)

            # Calculate test case summary
            if test_case["successful_iterations"] > 0:
                test_case_summary = self._calculate_test_case_summary(test_case_dir, test_case)
                if test_case_summary:
                    summary_data.append(test_case_summary)

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

            # Details sheet
            if detail_data:
                detail_df = pd.DataFrame(detail_data)
                detail_df.to_excel(writer, sheet_name="All Iterations", index=False)

            # Individual test case sheets
            for test_case in execution_summary["test_cases"]:
                if test_case.get("success", False):
                    test_case_id = test_case["test_case_id"]
                    test_case_data = [
                        d for d in detail_data if d.get("test_case_id") == test_case_id
                    ]
                    if test_case_data:
                        test_case_df = pd.DataFrame(test_case_data)
                        sheet_name = test_case_id[:31]
                        test_case_df.to_excel(writer, sheet_name=sheet_name, index=False)

        # Add plots to Excel file
        try:
            self.logger.debug("Adding plots to text embedding Excel report...")
            if detail_data:
                detail_df = pd.DataFrame(detail_data)
                x_column = "test_case_id"
                latency_columns = ["e2e_latency", "inference_latency"]

                latency_columns = [col for col in latency_columns if col in detail_df.columns]

                if latency_columns:
                    # Preserve the order of test cases as they appear in the data
                    detail_df[x_column] = pd.Categorical(
                        detail_df[x_column], categories=detail_df[x_column].unique(), ordered=True
                    )
                    self.add_plots_to_excel(output_file, detail_df, x_column, latency_columns)
                else:
                    self.logger.warning("No latency columns found for plotting")
        except Exception as e:
            self.logger.warning(f"Failed to add plots to Excel: {e}")
            self.logger.debug("Excel file created without plots")

        self.logger.debug(f"Text embedding results analysis completed: {output_file}")

    def _parse_iteration_data(
        self, iteration_dir: str, test_case: Dict, iteration: int
    ) -> Dict[str, Any]:
        """Parse data from a single iteration"""
        try:
            # Load test case data
            test_case_file = os.path.join(iteration_dir, "test_case_data.json")
            if not os.path.exists(test_case_file):
                self.logger.error(f"Test case data file not found: {test_case_file}")
                return None

            with open(test_case_file, "r") as f:
                test_case_data = json.load(f)

            inference_latency = test_case_data.get("inference_latency", 0)
            e2e_latency = test_case_data.get("e2e_latency", 0)

            # Load response data
            response_file = os.path.join(iteration_dir, "generate_text_embeddings_response.json")
            response_data = {}
            num_embeddings = 0
            if os.path.exists(response_file):
                with open(response_file, "r") as f:
                    response_data = json.load(f)
                    # Count the number of embeddings returned
                    if "data" in response_data:
                        num_embeddings = len(response_data["data"])

            # Process GPU stats
            gpu_stats_file = os.path.join(iteration_dir, f"gpu_metrics_iter_{iteration}_stats.json")
            gpu_metrics = self.process_gpu_stats(gpu_stats_file)

            # Get text input length
            text_input = test_case.get("text_input", "")
            text_length = len(text_input)

            # Combine all data
            iteration_data = {
                "test_case_id": test_case["test_case_id"],
                "text_length": text_length,
                "num_embeddings": num_embeddings,
                "inference_latency": inference_latency,
                "e2e_latency": e2e_latency,
                "inference_gpu_usage_mean": gpu_metrics.get("vlm_gpu_usage_mean", 0),
                "inference_gpu_usage_p90": gpu_metrics.get("vlm_gpu_usage_p90", 0),
                "benchmark_mode": "text_embedding",
                "backend_type": "rtvi_embed",
                "iteration": iteration,
                "source_folder": f"iteration_{iteration}",
            }
            # Add Prometheus/DCGM metrics if present (from DCGM exporter)
            for k, v in gpu_metrics.items():
                if k.startswith("prometheus_") or k.startswith("nodeexporter_"):
                    iteration_data[k] = v

            return self.round_floats(iteration_data, 6)

        except Exception as e:
            self.logger.error(f"Error parsing iteration data from {iteration_dir}: {e}")
            return None

    def _calculate_test_case_summary(self, test_case_dir: str, test_case: Dict) -> Dict[str, Any]:
        """Calculate summary statistics for a test case across all iterations"""
        try:
            # Load all iteration data for this test case
            iteration_data = []
            for iteration in range(1, test_case["iterations"] + 1):
                iteration_dir = os.path.join(test_case_dir, f"iteration_{iteration}")
                data = self._parse_iteration_data(iteration_dir, test_case, iteration)
                if data:
                    iteration_data.append(data)

            if not iteration_data:
                return None

            # Calculate statistics - mean ± std%
            numeric_fields = [
                "inference_latency",
                "e2e_latency",
                "inference_gpu_usage_mean",
                "inference_gpu_usage_p90",
            ]

            summary = {
                "test_case_id": test_case["test_case_id"],
                "text_length": iteration_data[0].get("text_length", 0),
                "num_embeddings": iteration_data[0].get("num_embeddings", 0),
            }

            for field in numeric_fields:
                values = [d.get(field, 0) for d in iteration_data if d.get(field) is not None]
                if values:
                    mean_val = sum(values) / len(values)
                    if len(values) > 1:
                        std_val = (
                            sum((x - mean_val) ** 2 for x in values) / (len(values) - 1)
                        ) ** 0.5
                        std_pct = (std_val / mean_val * 100) if mean_val > 0 else 0
                    else:
                        std_pct = 0

                    if mean_val >= 100:
                        summary[field] = f"{mean_val:.0f} ± {std_pct:.1f}%"
                    elif mean_val >= 10:
                        summary[field] = f"{mean_val:.1f} ± {std_pct:.1f}%"
                    else:
                        summary[field] = f"{mean_val:.4f} ± {std_pct:.1f}%"
                else:
                    summary[field] = "0.00 ± 0.0%"

            summary["benchmark_mode"] = "text_embedding"

            return summary

        except Exception as e:
            self.logger.error(f"Error calculating test case summary: {e}")
            return None
