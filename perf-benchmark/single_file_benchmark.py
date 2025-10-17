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
Single File Benchmark Implementation

Handles traditional video upload + summarization + chat completions workflow.
"""

import json
import os
import time
from typing import Any, Dict

import pandas as pd
from base import BenchmarkBase


class SingleFileBenchmark(BenchmarkBase):
    """Single file benchmark - upload video, summarize, process chat questions"""

    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Dict[str, Any]:
        """Parse single file benchmark configuration"""
        if scenario_config.get("benchmark_mode") != "single_file":
            raise ValueError(f"Invalid benchmark mode: {scenario_config.get('benchmark_mode')}")

        if "videos" not in scenario_config:
            raise ValueError("Missing 'videos' field in scenario config")

        # Validate video configurations
        for i, video in enumerate(scenario_config["videos"]):
            if "filepath" not in video:
                raise ValueError(f"Missing 'filepath' in video {i}")
            if "chunk_sizes" not in video:
                raise ValueError(f"Missing 'chunk_sizes' in video {i}")

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
            "chat_questions": global_config.get("chat_questions", []),
        }

    def execute(self, config: Dict, scenario_name: str) -> Dict[str, Any]:
        """Execute single file benchmark"""
        self.logger.info(f"Starting single file benchmark: {scenario_name}")

        global_config = self.parse_global_config(config)
        benchmark_config = self.parse_benchmark_config(
            config["test_scenarios"][scenario_name], global_config
        )

        scenario_dir = self.setup_scenario_directory(scenario_name)
        model_name = self.get_available_models()

        execution_results = {
            "scenario_name": scenario_name,
            "benchmark_mode": "single_file",
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
                    test_result = self._execute_single_test_case(
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
            f"Single file benchmark completed: {execution_results['successful_test_cases']}/"
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
        return f"single_file_{name}_{chunk_size}sec"

    def _execute_single_test_case(
        self,
        test_case_id: str,
        video_config: Dict,
        chunk_size: int,
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
                    iteration, video_config, chunk_size, benchmark_config, model_name, iteration_dir
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
        benchmark_config: Dict,
        model_name: str,
        iteration_dir: str,
    ) -> Dict[str, Any]:
        """Execute a single iteration of the test"""
        file_id = None

        # Start GPU monitoring
        self.start_gpu_monitoring()

        try:
            # Upload video file
            file_response = self._upload_video_file(video_config["filepath"], iteration_dir)
            file_id = file_response.json()["id"]
            self.active_resources.append(f"file_{file_id}")

            # Run summarization
            self._run_summarization(
                file_id, chunk_size, benchmark_config, model_name, iteration_dir, video_config
            )

            # Process chat questions if enabled
            chat_results = self._process_chat_questions(
                file_id, benchmark_config, model_name, iteration_dir, chunk_size, video_config
            )

            # Scrape metrics
            metrics = self.scrape_metrics()
            metrics_file = os.path.join(iteration_dir, "metrics.json")
            self.save_json_data(metrics, metrics_file)

            # Save test case data for this iteration
            self._save_test_case_data(
                video_config, chunk_size, iteration, iteration_dir, benchmark_config, model_name
            )

            return {
                "iteration": iteration,
                "success": True,
                "file_id": file_id,
                "summarization_completed": True,
                "chat_questions_completed": len(chat_results.get("successful_questions", [])),
                "api_metrics": metrics,
            }

        finally:
            # Stop GPU monitoring and export data
            self.stop_gpu_monitoring(
                export_dir=iteration_dir, filename_prefix=f"gpu_metrics_iter_{iteration}"
            )

            # Cleanup uploaded file
            if file_id and f"file_{file_id}" in self.active_resources:
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

    def _run_summarization(
        self,
        file_id: str,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        iteration_dir: str,
        video_config: Dict,
    ):
        """Run video summarization"""
        # Get summarize params
        params = self._merge_with_defaults(
            video_config.get("summarize_api_params", {}), benchmark_config["summarize_api_params"]
        )

        # Build summarization request
        request_data = {
            "id": [file_id],
            "model": model_name,
            "response_format": {"type": "text"},
            "chunk_duration": chunk_size,
            "temperature": params["temperature"],
            "max_tokens": params["max_tokens"],
            "enable_chat": params["enable_chat"],
            "enable_chat_history": params["enable_chat_history"],
            "summarize": params["summarize"],
        }

        # Add optional parameters if user configured them
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
            f"Sending /summarize request with payload: {json.dumps(request_data, indent=2)}"
        )

        response = self.make_api_call("/summarize", method="POST", data=request_data)

        # Save response
        self.save_response(response, iteration_dir, "summarize_response.json")

        return response

    def _process_chat_questions(
        self,
        file_id: str,
        benchmark_config: Dict,
        model_name: str,
        iteration_dir: str,
        chunk_size: int,
        video_config: Dict,
    ) -> Dict[str, Any]:
        """Process chat completion questions"""
        # Get chat questions from video config first, then fall back to global
        video_chat_questions = video_config.get("chat_questions", [])
        global_chat_questions = benchmark_config.get("chat_questions", [])
        chat_questions = video_chat_questions if video_chat_questions else global_chat_questions

        if not chat_questions:
            return {"successful_questions": [], "failed_questions": []}

        successful_questions = []
        failed_questions = []
        chat_latencies = []

        # Get chat params
        chat_params = self._merge_with_defaults(
            video_config.get("chat_api_params", {}), benchmark_config["chat_api_params"]
        )

        for i, question in enumerate(chat_questions):
            try:
                start_time = time.time()

                request_data = {
                    "id": file_id,
                    "model": model_name,
                    "chunk_duration": chunk_size,
                    "temperature": chat_params["temperature"],
                    "max_tokens": chat_params["max_tokens"],
                    "messages": [{"content": question, "role": "user"}],
                }

                # Add optional parameters if user configured them
                if "top_p" in chat_params:
                    request_data["top_p"] = chat_params["top_p"]
                if "top_k" in chat_params:
                    request_data["top_k"] = chat_params["top_k"]
                if "seed" in chat_params:
                    request_data["seed"] = chat_params["seed"]
                if "stream" in chat_params:
                    request_data["stream"] = chat_params["stream"]
                    request_data["stream_options"] = {"include_usage": False}

                self.logger.debug(
                    f"Sending /chat/completions request with payload: {json.dumps(request_data, indent=2)}"
                )

                response = self.make_api_call("/chat/completions", method="POST", data=request_data)

                latency = time.time() - start_time
                chat_latencies.append(latency)
                successful_questions.append({"question": question, "latency": latency})

                # Save individual response
                self.save_response(response, iteration_dir, f"chat_response_{i+1}.json")

            except Exception as e:
                failed_questions.append({"question": question, "error": str(e)})

        # Save chat summary
        chat_summary = {
            "questions": chat_questions,
            "latencies": chat_latencies,
            "successful_questions": successful_questions,
            "failed_questions": failed_questions,
            "avg_latency": sum(chat_latencies) / len(chat_latencies) if chat_latencies else 0,
        }

        chat_summary_file = os.path.join(iteration_dir, "chat_latencies.json")
        self.save_json_data(self.round_floats(chat_summary), chat_summary_file)

        return chat_summary

    def _scrape_api_metrics(self) -> Dict[str, Any]:
        """Scrape metrics from /metrics endpoint"""
        try:
            response = self.make_api_call("/metrics")
            metrics = {}

            for line in response.text.split("\n"):
                if line and not line.startswith("#"):
                    try:
                        name, value = line.split(" ", 1)
                        if any(name.endswith(suffix) for suffix in ["_latest", "_sum", "_count"]):
                            metrics[name] = float(value)
                    except ValueError:
                        continue

            return metrics
        except Exception as e:
            self.logger.error(f"Failed to scrape metrics: {e}")
            return {}

    def _save_test_case_data(
        self,
        video_config: Dict,
        chunk_size: int,
        iteration: int,
        iteration_dir: str,
        benchmark_config: Dict,
        model_name: str,
    ):
        """Save test case metadata"""
        # Get merged params
        summarize_params = self._merge_with_defaults(
            video_config.get("summarize_api_params", {}), benchmark_config["summarize_api_params"]
        )
        chat_params = self._merge_with_defaults(
            video_config.get("chat_api_params", {}), benchmark_config["chat_api_params"]
        )

        test_case_data = {
            "id": self._generate_test_case_id(video_config, chunk_size),
            "input_data": {
                "filepath": video_config["filepath"],
                "model": model_name,
                "chunk-duration": chunk_size,
                "summarize_temperature": summarize_params["temperature"],
                "summarize_max_tokens": summarize_params["max_tokens"],
                "summarize_enable_audio": summarize_params.get("enable_audio", False),
                "summarize_enable_cv_metadata": summarize_params.get("enable_cv_metadata", False),
                "summarize_cv_pipeline_prompt": summarize_params.get("cv_pipeline_prompt", ""),
                "summarize_vlm_input_width": summarize_params.get("vlm_input_width", 0),
                "summarize_vlm_input_height": summarize_params.get("vlm_input_height", 0),
                "enable-chat": summarize_params["enable_chat"],
                "enable-chat-history": summarize_params["enable_chat_history"],
                "summarize": summarize_params["summarize"],
                "chat_temperature": chat_params["temperature"],
                "chat_max_tokens": chat_params["max_tokens"],
                "chat_top_p": chat_params.get("top_p", 0),
                "chat_top_k": chat_params.get("top_k", 0),
                "chat_seed": chat_params.get("seed", 0),
                "caption": benchmark_config.get("prompts", {}).get("caption", ""),
                "caption_summarization": benchmark_config.get("prompts", {}).get(
                    "caption_summarization", ""
                ),
                "summary_aggregation": benchmark_config.get("prompts", {}).get(
                    "summary_aggregation", ""
                ),
                "chat_questions": benchmark_config.get("chat_questions", []),
            },
            "expected_result": {"status": "success"},
            "iteration": iteration,
        }

        test_case_file = os.path.join(iteration_dir, "test_case_data.json")
        self.save_json_data(test_case_data, test_case_file)

    def analyze_results(self, results_dir: str, output_file: str) -> None:
        """Generate Excel report from single file benchmark results"""
        self.logger.debug(f"Analyzing single file results from: {results_dir}")

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
            self.logger.debug("Adding plots to single file Excel report...")
            if detail_data:
                detail_df = pd.DataFrame(detail_data)
                x_column = "test_case_id"
                latency_columns = [
                    "e2e_latency",
                    "vlm_pipeline_latency",
                    "ca_rag_latency",
                    "chat_avg_latency",
                ]

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

        self.logger.debug(f"Single file results analysis completed: {output_file}")

    def _parse_iteration_data(
        self, iteration_dir: str, test_case: Dict, iteration: int
    ) -> Dict[str, Any]:
        """Parse data from a single iteration"""
        try:
            # Load API metrics
            metrics_file = os.path.join(iteration_dir, "metrics.json")
            api_metrics = {}
            if os.path.exists(metrics_file):
                with open(metrics_file, "r") as f:
                    api_metrics = json.load(f)

            # Load chat summary
            chat_file = os.path.join(iteration_dir, "chat_latencies.json")
            chat_summary = {}
            if os.path.exists(chat_file):
                with open(chat_file, "r") as f:
                    chat_summary = json.load(f)

            # Load summarize response for chunks processed
            summarize_file = os.path.join(iteration_dir, "summarize_response.json")
            summarize_data = {}
            if os.path.exists(summarize_file):
                with open(summarize_file, "r") as f:
                    summarize_data = json.load(f)

            # Process GPU stats
            gpu_stats_file = os.path.join(iteration_dir, f"gpu_metrics_iter_{iteration}_stats.json")
            gpu_metrics = self.process_gpu_stats(gpu_stats_file)

            # Load test case data for configuration fields
            test_case_file = os.path.join(iteration_dir, "test_case_data.json")
            test_config = {}
            if os.path.exists(test_case_file):
                with open(test_case_file, "r") as f:
                    test_case_data = json.load(f)
                    input_data = test_case_data.get("input_data", {})
                    # Add test_ prefix to all input_data fields
                    for key, value in input_data.items():
                        test_config[f"test_{key}"] = value

            # Combine all data - only the essential columns
            iteration_data = {
                "test_case_id": test_case["test_case_id"],
                "filename": os.path.basename(test_case["video_file"]),
                "total_chunks_processed": summarize_data.get("usage", {}).get(
                    "total_chunks_processed", 0
                ),
                "vlm_pipeline_latency": api_metrics.get("vlm_pipeline_latency_seconds_latest", 0),
                "vlm_latency": api_metrics.get("vlm_latency_seconds_latest", 0),
                "decode_latency": api_metrics.get("decode_latency_seconds_latest", 0),
                "ca_rag_latency": api_metrics.get("ca_rag_latency_seconds_latest", 0),
                "e2e_latency": api_metrics.get("e2e_latency_seconds_latest", 0),
                "chat_avg_latency": chat_summary.get("avg_latency", 0),
                "chat_total_questions": len(chat_summary.get("questions", [])),
                "chat_successful_questions": len(chat_summary.get("successful_questions", [])),
                "vlm_gpu_usage_mean": gpu_metrics.get("vlm_gpu_usage_mean", 0),
                "vlm_gpu_usage_p90": gpu_metrics.get("vlm_gpu_usage_p90", 0),
                "llm_gpu_usage_mean": gpu_metrics.get("llm_gpu_usage_mean", 0),
                "llm_gpu_usage_p90": gpu_metrics.get("llm_gpu_usage_p90", 0),
                "vlm_nvdec_usage_mean": gpu_metrics.get("vlm_nvdec_usage_mean", 0),
                "benchmark_mode": "single_file",
                "chunk_size": test_case["chunk_size"],
                "iteration": iteration,
                "source_folder": f"iteration_{iteration}",
            }

            return self.round_floats(iteration_data)

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
                "total_chunks_processed",
                "vlm_pipeline_latency",
                "vlm_latency",
                "decode_latency",
                "ca_rag_latency",
                "e2e_latency",
                "chat_avg_latency",
                "vlm_gpu_usage_mean",
                "vlm_gpu_usage_p90",
                "llm_gpu_usage_mean",
                "llm_gpu_usage_p90",
                "vlm_nvdec_usage_mean",
            ]

            summary = {
                "test_case_id": test_case["test_case_id"],
                "filename": os.path.basename(test_case["video_file"]),
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

            summary["chat_total_questions"] = (
                iteration_data[0].get("chat_total_questions", 0) if iteration_data else 0
            )
            summary["chat_successful_questions"] = (
                iteration_data[0].get("chat_successful_questions", 0) if iteration_data else 0
            )
            summary["benchmark_mode"] = "single_file"

            return summary

        except Exception as e:
            self.logger.error(f"Error calculating test case summary: {e}")
            return None
