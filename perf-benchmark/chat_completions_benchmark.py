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
Chat Completions Benchmark Implementation

Tests /chat/completions API performance with parallel queries at various concurrency levels.
Measures non-streaming query performance metrics (latency, throughput, etc.).
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

import pandas as pd
from base import BenchmarkBase


class ChatCompletionsBenchmark(BenchmarkBase):
    """Chat completions benchmark - test query performance at various concurrency levels"""

    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Dict[str, Any]:
        """Parse chat completions benchmark configuration"""
        if scenario_config.get("benchmark_mode") != "chat_completions_burst":
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

        # Merge scenario-level API params with global
        summarize_params = self._merge_with_defaults(
            scenario_config.get("summarize_api_params", {}),
            global_config.get("summarize_api_params", {}),
        )
        chat_params = self._merge_with_defaults(
            scenario_config.get("chat_api_params", {}), global_config.get("chat_api_params", {})
        )

        return {
            "iterations": scenario_config.get("iterations", 3),
            "videos": scenario_config["videos"],
            "summarize_api_params": summarize_params,
            "chat_api_params": chat_params,
            "prompts": global_config.get("prompts", {}),
            "chat_questions": scenario_config.get(
                "chat_questions", global_config.get("chat_questions", [])
            ),
            "ingestion_wait_seconds": scenario_config.get("ingestion_wait_seconds", 3),
        }

    def execute(self, config: Dict, scenario_name: str) -> Dict[str, Any]:
        """Execute chat completions benchmark"""
        self.logger.info(f"Starting chat completions benchmark: {scenario_name}")

        global_config = self.parse_global_config(config)
        benchmark_config = self.parse_benchmark_config(
            config["test_scenarios"][scenario_name], global_config
        )

        scenario_dir = self.setup_scenario_directory(scenario_name)
        model_name = self.get_available_models()

        execution_results = {
            "scenario_name": scenario_name,
            "benchmark_mode": "chat_completions_burst",
            "scenario_dir": scenario_dir,
            "test_cases": [],
            "total_test_cases": 0,
            "successful_test_cases": 0,
            "failed_test_cases": 0,
        }

        # Execute test cases: ingest once per (video, chunk_size), then test all concurrency levels
        for video_config in benchmark_config["videos"]:
            for chunk_size in video_config["chunk_sizes"]:
                # Count test cases for this (video, chunk_size) combination
                num_concurrency_levels = len(video_config["concurrency_levels"])
                execution_results["total_test_cases"] += num_concurrency_levels

                try:
                    # Execute all concurrency levels for this (video, chunk_size) with shared ingestion
                    test_results = self._execute_video_chunk_group(
                        video_config,
                        chunk_size,
                        benchmark_config,
                        model_name,
                        scenario_dir,
                    )

                    for test_result in test_results:
                        execution_results["test_cases"].append(test_result)
                        if test_result.get("success", False):
                            execution_results["successful_test_cases"] += 1
                            self.logger.info(
                                f"Test case {test_result['test_case_id']} completed successfully"
                            )
                        else:
                            execution_results["failed_test_cases"] += 1
                            self.logger.error(f"Test case {test_result['test_case_id']} failed")

                except Exception as e:
                    filepath = video_config["filepath"]
                    self.logger.error(
                        f"Video chunk group failed (video={filepath}, "
                        f"chunk_size={chunk_size}): {e}"
                    )
                    # Mark all concurrency levels as failed for this group
                    for concurrency_level in video_config["concurrency_levels"]:
                        test_case_id = self._generate_test_case_id(
                            video_config, chunk_size, concurrency_level
                        )
                        execution_results["failed_test_cases"] += 1
                        execution_results["test_cases"].append(
                            {"test_case_id": test_case_id, "success": False, "error": str(e)}
                        )

        # Save execution summary
        summary_file = os.path.join(scenario_dir, "execution_summary.json")
        self.save_json_data(self.round_floats(execution_results), summary_file)

        self.logger.info(
            f"Chat completions benchmark completed: {execution_results['successful_test_cases']}/"
            f"{execution_results['total_test_cases']} test cases successful"
        )

        return execution_results

    def _generate_test_case_id(
        self, video_config: Dict, chunk_size: int, concurrency_level: int
    ) -> str:
        """Generate unique test case ID"""
        filename = os.path.basename(video_config["filepath"])
        name = video_config.get("name", os.path.splitext(filename)[0])
        return f"chat_burst_{name}_{chunk_size}sec_c{concurrency_level}"

    def _execute_video_chunk_group(
        self,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        scenario_dir: str,
    ) -> List[Dict[str, Any]]:
        """Execute all concurrency levels for a (video, chunk_size) combination with shared ingestion"""
        self.logger.info(
            f"Starting video chunk group: {video_config['filepath']}, chunk_size={chunk_size}"
        )

        filename = os.path.basename(video_config["filepath"])
        name = video_config.get("name", os.path.splitext(filename)[0])
        group_dir = os.path.join(scenario_dir, f"ingestion_{name}_{chunk_size}sec")
        os.makedirs(group_dir, exist_ok=True)

        file_id = None
        test_results = []

        try:
            self.logger.info(f"Ingesting video: {video_config['filepath']}")
            file_response = self._upload_video_file(video_config["filepath"], group_dir)
            file_id = file_response.json()["id"]
            self.active_resources.append(f"file_{file_id}")

            self._run_ingestion_only(
                file_id, chunk_size, benchmark_config, model_name, group_dir, video_config
            )

            wait_seconds = benchmark_config.get("ingestion_wait_seconds", 3)
            self.logger.info(f"Waiting {wait_seconds} seconds after ingestion...")
            time.sleep(wait_seconds)

            for concurrency_level in video_config["concurrency_levels"]:
                test_case_id = self._generate_test_case_id(
                    video_config, chunk_size, concurrency_level
                )

                try:
                    test_result = self._execute_chat_completions_test_case(
                        test_case_id,
                        video_config,
                        chunk_size,
                        concurrency_level,
                        benchmark_config,
                        model_name,
                        scenario_dir,
                        file_id,
                    )
                    test_results.append(test_result)

                except Exception as e:
                    self.logger.error(f"Test case {test_case_id} failed: {e}")
                    test_results.append(
                        {"test_case_id": test_case_id, "success": False, "error": str(e)}
                    )

        finally:
            if file_id and f"file_{file_id}" in self.active_resources:
                try:
                    self.make_api_call(f"/files/{file_id}", method="DELETE")
                    self.active_resources.remove(f"file_{file_id}")
                    self.logger.info(f"Cleaned up file {file_id}")
                except Exception as e:
                    self.logger.error(f"Failed to cleanup file {file_id}: {e}")

        return test_results

    def _execute_chat_completions_test_case(
        self,
        test_case_id: str,
        video_config: Dict,
        chunk_size: int,
        concurrency_level: int,
        benchmark_config: Dict,
        model_name: str,
        scenario_dir: str,
        shared_file_id: str = None,
    ) -> Dict[str, Any]:
        """Execute chat completions test case with multiple iterations"""
        test_case_dir = os.path.join(scenario_dir, test_case_id)
        os.makedirs(test_case_dir, exist_ok=True)

        iterations = benchmark_config["iterations"]
        iteration_results = []

        for iteration in range(1, iterations + 1):
            iteration_dir = os.path.join(test_case_dir, f"iteration_{iteration}")
            os.makedirs(iteration_dir, exist_ok=True)

            try:
                result = self._execute_single_iteration(
                    iteration,
                    video_config,
                    chunk_size,
                    concurrency_level,
                    benchmark_config,
                    model_name,
                    iteration_dir,
                    shared_file_id,
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
                time.sleep(5)

        # Calculate aggregated results
        successful_iterations = [r for r in iteration_results if r.get("success", False)]

        test_result = {
            "test_case_id": test_case_id,
            "video_file": video_config["filepath"],
            "chunk_size": chunk_size,
            "concurrency_level": concurrency_level,
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
        video_config: Dict,
        chunk_size: int,
        concurrency_level: int,
        benchmark_config: Dict,
        model_name: str,
        iteration_dir: str,
        shared_file_id: str = None,
    ) -> Dict[str, Any]:
        """Execute a single iteration of the test"""
        file_id = shared_file_id
        should_cleanup = shared_file_id is None

        self.start_gpu_monitoring()

        try:
            if shared_file_id is None:
                self.logger.info(f"Ingesting video: {video_config['filepath']}")
                file_response = self._upload_video_file(video_config["filepath"], iteration_dir)
                file_id = file_response.json()["id"]
                self.active_resources.append(f"file_{file_id}")

                self._run_ingestion_only(
                    file_id, chunk_size, benchmark_config, model_name, iteration_dir, video_config
                )

                wait_seconds = benchmark_config.get("ingestion_wait_seconds", 3)
                self.logger.info(f"Waiting {wait_seconds} seconds after ingestion...")
                time.sleep(wait_seconds)
            else:
                self.logger.debug(f"Using shared file_id: {shared_file_id}")
            self.logger.info(f"Running queries at concurrency level: {concurrency_level}")
            query_results = self._run_concurrent_queries(
                file_id,
                chunk_size,
                concurrency_level,
                benchmark_config,
                model_name,
                iteration_dir,
                video_config,
                stream=False,
            )

            # Scrape metrics
            metrics = self.scrape_metrics()
            metrics_file = os.path.join(iteration_dir, "metrics.json")
            self.save_json_data(metrics, metrics_file)

            # Save test case data
            self._save_test_case_data(
                video_config,
                chunk_size,
                concurrency_level,
                iteration,
                iteration_dir,
                benchmark_config,
                model_name,
            )

            return {
                "iteration": iteration,
                "success": True,
                "file_id": file_id,
                "concurrency_level": concurrency_level,
                "query_results": query_results,
                "api_metrics": metrics,
            }

        finally:
            self.stop_gpu_monitoring(
                export_dir=iteration_dir, filename_prefix=f"gpu_metrics_iter_{iteration}"
            )

            if should_cleanup and file_id and f"file_{file_id}" in self.active_resources:
                try:
                    self.make_api_call(f"/files/{file_id}", method="DELETE")
                    self.active_resources.remove(f"file_{file_id}")
                except Exception as e:
                    self.logger.error(f"Failed to cleanup file {file_id}: {e}")

    def _upload_video_file(self, filepath: str, iteration_dir: str):
        """Upload video file to API"""
        files = {
            "filename": (None, os.path.abspath(filepath)),
            "purpose": (None, "vision"),
            "media_type": (None, "video"),
        }

        response = self.make_api_call("/files", method="POST", files=files)

        # Save response
        self.save_response(response, iteration_dir, "file_add_response.json")

        return response

    def _run_ingestion_only(
        self,
        file_id: str,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        iteration_dir: str,
        video_config: Dict,
    ):
        """Run ingestion only (summarize API with summarize disabled)"""
        # Get summarize params
        params = self._merge_with_defaults(
            video_config.get("summarize_api_params", {}), benchmark_config["summarize_api_params"]
        )

        # Build summarization request with summarize disabled
        request_data = {
            "id": [file_id],
            "model": model_name,
            "response_format": {"type": "text"},
            "chunk_duration": chunk_size,
            "temperature": params["temperature"],
            "max_tokens": params["max_tokens"],
            "enable_chat": params["enable_chat"],
            "enable_chat_history": params["enable_chat_history"],
            "summarize": False,  # Disable summarization for ingestion only
        }

        # Add optional parameters if configured
        if "enable_audio" in params:
            request_data["enable_audio"] = params["enable_audio"]
        if "enable_cv_metadata" in params:
            request_data["enable_cv_metadata"] = params["enable_cv_metadata"]
        if "cv_pipeline_prompt" in params:
            request_data["cv_pipeline_prompt"] = params["cv_pipeline_prompt"]
        if "vlm_input_width" in params:
            request_data["vlm_input_width"] = params["vlm_input_width"]
        if "vlm_input_height" in params:
            request_data["vlm_input_height"] = params["vlm_input_height"]
        if "chunk_overlap_duration" in params:
            request_data["chunk_overlap_duration"] = params["chunk_overlap_duration"]
        if "num_frames_per_chunk" in params:
            request_data["num_frames_per_chunk"] = params["num_frames_per_chunk"]

        # Add prompts
        video_prompts = video_config.get("prompts", {})
        global_prompts = benchmark_config.get("prompts", {})

        if video_prompts.get("caption") or global_prompts.get("caption"):
            request_data["prompt"] = video_prompts.get("caption", global_prompts.get("caption"))
        if video_prompts.get("caption_summarization") or global_prompts.get(
            "caption_summarization"
        ):
            request_data["caption_summarization_prompt"] = video_prompts.get(
                "caption_summarization", global_prompts.get("caption_summarization")
            )
        if video_prompts.get("summary_aggregation") or global_prompts.get("summary_aggregation"):
            request_data["summary_aggregation_prompt"] = video_prompts.get(
                "summary_aggregation", global_prompts.get("summary_aggregation")
            )

        self.logger.debug(
            f"Sending /summarize request (ingestion only) with payload: {json.dumps(request_data, indent=2)}"
        )

        response = self.make_api_call("/summarize", method="POST", data=request_data)

        # Save response
        self.save_response(response, iteration_dir, "ingestion_response.json")

        return response

    def _run_concurrent_queries(
        self,
        file_id: str,
        chunk_size: int,
        concurrency_level: int,
        benchmark_config: Dict,
        model_name: str,
        iteration_dir: str,
        video_config: Dict,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Run concurrent chat completion queries as a burst test"""
        chat_questions = benchmark_config.get("chat_questions", [])
        if not chat_questions:
            self.logger.warning("No chat questions configured, skipping queries")
            return {}

        chat_params = self._merge_with_defaults(
            video_config.get("chat_api_params", {}), benchmark_config["chat_api_params"]
        )

        query_results = []
        num_queries = concurrency_level

        queries = []
        for i in range(num_queries):
            question_idx = i % len(chat_questions)
            question = chat_questions[question_idx]
            queries.append((i, question))

        start_time = time.time()
        with ThreadPoolExecutor(max_workers=num_queries) as executor:
            futures = {
                executor.submit(
                    self._execute_single_query,
                    file_id,
                    chunk_size,
                    question,
                    chat_params,
                    model_name,
                    query_idx,
                    stream,
                ): (query_idx, question)
                for query_idx, question in queries
            }

            for future in as_completed(futures):
                query_idx, question = futures[future]
                try:
                    result = future.result()
                    query_results.append(result)
                except Exception as e:
                    self.logger.error(f"Query {query_idx} failed: {e}")
                    query_results.append(
                        {
                            "query_idx": query_idx,
                            "question": question,
                            "success": False,
                            "error": str(e),
                        }
                    )

        total_time = time.time() - start_time

        # Calculate statistics
        successful_queries = [r for r in query_results if r.get("success", False)]
        latencies = [r["latency"] for r in successful_queries if "latency" in r]

        results = {
            "concurrency_level": concurrency_level,
            "total_queries": num_queries,
            "successful_queries": len(successful_queries),
            "failed_queries": num_queries - len(successful_queries),
            "total_time": total_time,
            "query_results": query_results,
        }

        if latencies:
            results.update(
                {
                    "avg_latency": sum(latencies) / len(latencies),
                    "min_latency": min(latencies),
                    "max_latency": max(latencies),
                    "p50_latency": self._calculate_percentile(latencies, 50),
                    "p90_latency": self._calculate_percentile(latencies, 90),
                    "p95_latency": self._calculate_percentile(latencies, 95),
                    "p99_latency": self._calculate_percentile(latencies, 99),
                    "throughput_qps": len(successful_queries) / total_time if total_time > 0 else 0,
                }
            )

        results_file = os.path.join(iteration_dir, "query_results.json")
        self.save_json_data(self.round_floats(results), results_file)

        return results

    def _execute_single_query(
        self,
        file_id: str,
        chunk_size: int,
        question: str,
        chat_params: Dict,
        model_name: str,
        query_idx: int,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Execute a single chat completion query"""
        request_data = {
            "id": file_id,
            "model": model_name,
            "chunk_duration": chunk_size,
            "temperature": chat_params["temperature"],
            "max_tokens": chat_params["max_tokens"],
            "messages": [{"content": question, "role": "user"}],
            "stream": False,
        }

        # Add optional parameters
        if "top_p" in chat_params:
            request_data["top_p"] = chat_params["top_p"]
        if "top_k" in chat_params:
            request_data["top_k"] = chat_params["top_k"]
        if "seed" in chat_params:
            request_data["seed"] = chat_params["seed"]

        payload_json = json.dumps(request_data, indent=2)
        self.logger.debug(
            f"Sending /chat/completions request (query_idx={query_idx}) "
            f"with payload: {payload_json}"
        )

        start_time = time.time()

        try:
            response = self.make_api_call("/chat/completions", method="POST", data=request_data)
            latency = time.time() - start_time

            response_data = response.json()
            response_content = ""
            total_tokens = 0

            if response_data.get("choices"):
                response_content = response_data["choices"][0]["message"]["content"]
            if response_data.get("usage"):
                total_tokens = response_data["usage"].get("completion_tokens", 0)

            result = {
                "query_idx": query_idx,
                "question": question,
                "success": True,
                "latency": latency,
                "total_tokens": total_tokens,
                "response_content": response_content,
            }

            return result

        except Exception as e:
            latency = time.time() - start_time
            self.logger.error(f"Query {query_idx} failed: {e}")
            return {
                "query_idx": query_idx,
                "question": question,
                "success": False,
                "latency": latency,
                "error": str(e),
            }

    def _calculate_percentile(self, values: List[float], percentile: float) -> float:
        """Calculate percentile of a list of values"""
        if not values:
            return 0.0
        sorted_values = sorted(values)
        index = (percentile / 100.0) * (len(sorted_values) - 1)
        lower = int(index)
        upper = lower + 1
        if upper >= len(sorted_values):
            return sorted_values[-1]
        weight = index - lower
        return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight

    def _save_test_case_data(
        self,
        video_config: Dict,
        chunk_size: int,
        concurrency_level: int,
        iteration: int,
        iteration_dir: str,
        benchmark_config: Dict,
        model_name: str,
    ):
        """Save test case metadata"""
        test_case_data = {
            "id": self._generate_test_case_id(video_config, chunk_size, concurrency_level),
            "input_data": {
                "filepath": video_config["filepath"],
                "model": model_name,
                "chunk_duration": chunk_size,
                "concurrency_level": concurrency_level,
                "chat_questions": benchmark_config.get("chat_questions", []),
                "ingestion_wait_seconds": benchmark_config.get("ingestion_wait_seconds", 3),
            },
            "expected_result": {"status": "success"},
            "iteration": iteration,
        }

        test_case_file = os.path.join(iteration_dir, "test_case_data.json")
        self.save_json_data(test_case_data, test_case_file)

    def analyze_results(self, results_dir: str, output_file: str) -> None:
        """Generate Excel report from chat completions benchmark results"""
        self.logger.debug(f"Analyzing chat completions results from: {results_dir}")

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
            if summary_data:
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name="Summary", index=False)

            try:
                gpu_info_df = self.get_gpu_info_dataframe()
                gpu_info_df.to_excel(writer, sheet_name="GPU_Info", index=False)
            except Exception as e:
                self.logger.warning(f"Failed to add GPU info sheet: {e}")

            if detail_data:
                detail_df = pd.DataFrame(detail_data)
                detail_df.to_excel(writer, sheet_name="All Iterations", index=False)

        # Add plots to Excel file
        try:
            self.logger.debug("Adding plots to chat completions Excel report...")
            if detail_data:
                detail_df = pd.DataFrame(detail_data)

                # Plot latencies vs concurrency level
                x_column = "concurrency_level"
                latency_columns = [
                    "avg_latency",
                    "p50_latency",
                    "p90_latency",
                    "p95_latency",
                    "p99_latency",
                    "throughput_qps",
                ]

                latency_columns = [col for col in latency_columns if col in detail_df.columns]

                if latency_columns:
                    self.add_plots_to_excel(output_file, detail_df, x_column, latency_columns)
                else:
                    self.logger.warning("No latency columns found for plotting")
        except Exception as e:
            self.logger.warning(f"Failed to add plots to Excel: {e}")
            self.logger.debug("Excel file created without plots")

        self.logger.debug(f"Chat completions results analysis completed: {output_file}")

    def _parse_iteration_data(
        self, iteration_dir: str, test_case: Dict, iteration: int
    ) -> Dict[str, Any]:
        """Parse data from a single iteration"""
        try:
            query_results_file = os.path.join(iteration_dir, "query_results.json")
            query_data = {}
            if os.path.exists(query_results_file):
                with open(query_results_file, "r") as f:
                    query_data = json.load(f)

            gpu_stats_file = os.path.join(iteration_dir, f"gpu_metrics_iter_{iteration}_stats.json")
            gpu_metrics = self.process_gpu_stats(gpu_stats_file)

            iteration_data = {
                "test_case_id": test_case["test_case_id"],
                "filename": os.path.basename(test_case["video_file"]),
                "chunk_size": test_case["chunk_size"],
                "concurrency_level": test_case["concurrency_level"],
                "iteration": iteration,
                "total_queries": query_data.get("total_queries", 0),
                "successful_queries": query_data.get("successful_queries", 0),
                "avg_latency": query_data.get("avg_latency", 0),
                "p50_latency": query_data.get("p50_latency", 0),
                "p90_latency": query_data.get("p90_latency", 0),
                "p95_latency": query_data.get("p95_latency", 0),
                "p99_latency": query_data.get("p99_latency", 0),
                "throughput_qps": query_data.get("throughput_qps", 0),
                "vlm_gpu_usage_mean": gpu_metrics.get("vlm_gpu_usage_mean", 0),
                "vlm_gpu_usage_p90": gpu_metrics.get("vlm_gpu_usage_p90", 0),
                "llm_gpu_usage_mean": gpu_metrics.get("llm_gpu_usage_mean", 0),
                "llm_gpu_usage_p90": gpu_metrics.get("llm_gpu_usage_p90", 0),
                "benchmark_mode": "chat_completions_burst",
                "source_folder": f"iteration_{iteration}",
            }

            return self.round_floats(iteration_data)

        except Exception as e:
            self.logger.error(f"Error parsing iteration data from {iteration_dir}: {e}")
            return None

    def _calculate_test_case_summary(self, test_case_dir: str, test_case: Dict) -> Dict[str, Any]:
        """Calculate summary statistics for a test case across all iterations"""
        try:
            iteration_data = []
            for iteration in range(1, test_case["iterations"] + 1):
                iteration_dir = os.path.join(test_case_dir, f"iteration_{iteration}")
                data = self._parse_iteration_data(iteration_dir, test_case, iteration)
                if data:
                    iteration_data.append(data)

            if not iteration_data:
                return None

            numeric_fields = [
                "avg_latency",
                "p50_latency",
                "p90_latency",
                "p95_latency",
                "p99_latency",
                "throughput_qps",
                "vlm_gpu_usage_mean",
                "vlm_gpu_usage_p90",
                "llm_gpu_usage_mean",
                "llm_gpu_usage_p90",
            ]

            summary = {
                "test_case_id": test_case["test_case_id"],
                "filename": os.path.basename(test_case["video_file"]),
                "chunk_size": test_case["chunk_size"],
                "concurrency_level": test_case["concurrency_level"],
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
                        summary[field] = f"{mean_val:.2f} ± {std_pct:.1f}%"
                else:
                    summary[field] = "0.00 ± 0.0%"

            summary["benchmark_mode"] = "chat_completions_burst"

            return summary

        except Exception as e:
            self.logger.error(f"Error calculating test case summary: {e}")
            return None
