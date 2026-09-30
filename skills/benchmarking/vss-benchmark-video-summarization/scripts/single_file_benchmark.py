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
Single File Benchmark Implementation

Handles traditional video upload + summarization workflow.
"""

import json
import os
import time
from typing import Any, Dict

import pandas as pd
from base import BenchmarkBase


class SingleFileBenchmark(BenchmarkBase):
    """Single file benchmark - upload video, summarize"""

    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Dict[str, Any]:
        """Parse single file benchmark configuration"""
        if scenario_config.get("benchmark_mode") != "single_file":
            raise ValueError(f"Invalid benchmark mode: {scenario_config.get('benchmark_mode')}")

        if "videos" not in scenario_config:
            raise ValueError("Missing 'videos' field in scenario config")

        # Validate video configurations
        for i, video in enumerate(scenario_config["videos"]):
            if "url" not in video and "filepath" not in video:
                raise ValueError(f"Missing 'url' or 'filepath' in video {i}")
            if "chunk_sizes" not in video:
                raise ValueError(f"Missing 'chunk_sizes' in video {i}")

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
            "iterations": scenario_config.get("iterations", 3),
            "videos": scenario_config["videos"],
            "summarize_api_params": summarize_params,
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

        # Discarded warmup run: absorbs one-time costs (VLM/LLM model load, CUDA
        # context init, connection pools) so every counted iteration is a true
        # steady-state cold run. Never recorded as an iteration in the summary.
        self._warmup_run(benchmark_config, model_name, scenario_dir)

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
                    if test_result.get("success", False):
                        execution_results["successful_test_cases"] += 1
                        self.logger.info(
                            f"Test case {test_case_id} completed successfully "
                            f"({test_result.get('successful_iterations', 0)}/"
                            f"{test_result.get('iterations', 0)} iterations successful)"
                        )
                    else:
                        execution_results["failed_test_cases"] += 1
                        self.logger.error(
                            f"Test case {test_case_id} failed "
                            f"(0/{test_result.get('iterations', 0)} iterations successful)"
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
            f"Single file benchmark completed: {execution_results['successful_test_cases']}/"
            f"{execution_results['total_test_cases']} test cases successful "
            f"({execution_results['failed_test_cases']} failed)"
        )

        return execution_results

    def _warmup_run(self, benchmark_config: Dict, model_name: str, scenario_dir: str) -> None:
        """Run one full-pipeline summarization to warm the system, then discard it.

        The very first request after deploy pays one-time costs (model load into
        GPU, CUDA kernel compilation, ES/connection setup) that are not
        representative of steady-state latency. Running — and discarding — one
        warmup pass keeps those costs out of the measured iterations. Uses the
        first configured video + chunk size; results are never counted.
        """
        videos = benchmark_config.get("videos") or []
        if not videos or not videos[0].get("chunk_sizes"):
            self.logger.info("Warmup skipped: no videos/chunk sizes configured.")
            return
        video_config = videos[0]
        chunk_size = video_config["chunk_sizes"][0]
        warmup_dir = os.path.join(scenario_dir, "_warmup")
        os.makedirs(warmup_dir, exist_ok=True)

        file_id = None
        try:
            self.logger.info(
                "Warmup run (discarded): warming the VLM/LLM pipeline before measured iterations ..."
            )
            file_id = self._upload_video(video_config)
            video_config_with_id = video_config.copy()
            video_config_with_id["video_id"] = file_id
            self._run_summarization(
                file_id, chunk_size, benchmark_config, model_name, warmup_dir, video_config_with_id
            )
            self.logger.info("Warmup run complete (results discarded).")
        except Exception as e:
            self.logger.warning(
                f"Warmup run failed (continuing; measured iterations are unaffected): {e}"
            )
        finally:
            if file_id:
                self._delete_video(file_id)

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
        return f"single_file_{name}_{chunk_size}sec"

    def _upload_video(self, video_config: Dict) -> str:
        """
        Upload a video to the server via POST /files and return the persistent file_id.

        If 'filepath' is present in video_config, the local file is uploaded as
        multipart form data. If 'url' is present, the URL is passed as the
        'filename' form field so the server downloads it.

        Returns:
            file_id (str): The id from the /files response
        """
        video_source = video_config.get("url") or video_config.get("filepath", "")
        self.logger.info(f"Uploading video via /files: {video_source}")

        url = f"{self.base_url}/files"

        try:
            if "filepath" in video_config:
                # Local file upload — multipart form with file field
                filepath = video_config["filepath"]
                with open(filepath, "rb") as f:
                    files = {"file": (os.path.basename(filepath), f, "video/mp4")}
                    data = {"purpose": "vision", "media_type": "video"}
                    response = self.session.post(url, files=files, data=data)
            else:
                # URL-based — pass URL as filename so the server downloads it
                data = {
                    "filename": video_config["url"],
                    "purpose": "vision",
                    "media_type": "video",
                }
                response = self.session.post(url, data=data)

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

            file_id = None
            try:
                file_id = self._upload_video(video_config)
                self.logger.info(f"Iteration {iteration}: uploaded fresh file_id {file_id}")
                video_config_with_id = video_config.copy()
                video_config_with_id["video_id"] = file_id

                result = self._execute_single_iteration(
                    iteration,
                    video_config_with_id,
                    chunk_size,
                    benchmark_config,
                    model_name,
                    iteration_dir,
                )
                # Add GPU metrics to this iteration's result for execution_summary.json
                gpu_stats_file = os.path.join(
                    iteration_dir, f"gpu_metrics_iter_{iteration}_stats.json"
                )
                if os.path.exists(gpu_stats_file):
                    gpu_metrics = self.process_gpu_stats(gpu_stats_file)
                    result["gpu_metrics"] = self.round_floats(
                        {
                            "vlm_gpu_usage_mean": gpu_metrics.get("vlm_gpu_usage_mean", 0),
                            "vlm_gpu_usage_p90": gpu_metrics.get("vlm_gpu_usage_p90", 0),
                            "llm_gpu_usage_mean": gpu_metrics.get("llm_gpu_usage_mean", 0),
                            "llm_gpu_usage_p90": gpu_metrics.get("llm_gpu_usage_p90", 0),
                            "vlm_nvdec_usage_mean": gpu_metrics.get("vlm_nvdec_usage_mean", 0),
                        }
                    )
                # Add CPU/RAM metrics to this iteration's result for execution_summary.json
                cpu_stats_file = os.path.join(
                    iteration_dir, f"cpu_metrics_iter_{iteration}_stats.json"
                )
                if os.path.exists(cpu_stats_file):
                    cpu_metrics = self.process_cpu_stats(cpu_stats_file)
                    result["cpu_metrics"] = self.round_floats(
                        {
                            "cpu_usage_mean": cpu_metrics.get("cpu_usage_mean", 0),
                            "cpu_usage_p90": cpu_metrics.get("cpu_usage_p90", 0),
                            "ram_percent_mean": cpu_metrics.get("ram_percent_mean", 0),
                            "ram_percent_p90": cpu_metrics.get("ram_percent_p90", 0),
                            "ram_used_gb_mean": cpu_metrics.get("ram_used_gb_mean", 0),
                            "ram_used_gb_max": cpu_metrics.get("ram_used_gb_max", 0),
                        }
                    )
                iteration_results.append(result)
                self.logger.debug(f"Iteration {iteration} completed for {test_case_id}")

            except Exception as e:
                self.logger.error(f"Iteration {iteration} failed for {test_case_id}: {e}")
                iteration_results.append(
                    {"iteration": iteration, "success": False, "error": str(e)}
                )
            finally:
                # Delete this iteration's asset so it can't be reused as a warm cache
                # hit and to avoid leaking assets on the backend.
                if file_id:
                    self._delete_video(file_id)

            # Wait between iterations
            if iteration < iterations:
                time.sleep(5)

        # Calculate aggregated results
        successful_iterations = [r for r in iteration_results if r.get("success", False)]

        test_result = {
            "test_case_id": test_case_id,
            "video_url": video_config.get("url") or video_config.get("filepath", ""),
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
        # Start GPU and CPU monitoring
        self.start_gpu_monitoring()
        self.start_cpu_monitoring()

        try:
            start_time = time.perf_counter()
            # Run summarization with video_id (asset already downloaded)
            _, event_stats = self._run_summarization(
                video_config["video_id"],
                chunk_size,
                benchmark_config,
                model_name,
                iteration_dir,
                video_config,
            )
            wall_clock_seconds = time.perf_counter() - start_time

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
                "summarization_completed": True,
                "wall_clock_seconds": wall_clock_seconds,
                "api_metrics": metrics,
                **event_stats,
            }

        finally:
            self.stop_gpu_monitoring(
                export_dir=iteration_dir, filename_prefix=f"gpu_metrics_iter_{iteration}"
            )
            self.stop_cpu_monitoring(
                export_dir=iteration_dir, filename_prefix=f"cpu_metrics_iter_{iteration}"
            )

    def _run_summarization(
        self,
        video_id: str,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        iteration_dir: str,
        video_config: Dict,
    ):
        """Run video summarization using video_id (asset already downloaded)"""
        # Get summarize params
        params = self._merge_with_defaults(
            video_config.get("summarize_api_params", {}), benchmark_config["summarize_api_params"]
        )

        # Build summarization request with id only (asset pre-uploaded via /files)
        request_data = {
            "id": video_id,
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
            f"Sending /summarize request with payload: {json.dumps(request_data, indent=2)}"
        )

        response = self.make_api_call("/summarize", method="POST", data=request_data)

        # Save response
        self.save_response(response, iteration_dir, "summarize_response.json")

        # Parse and save formatted content + extract event stats
        event_stats = self.extract_and_save_formatted_content(
            iteration_dir, "summarize_response.json"
        )

        return response, event_stats

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

        # These are the VLM captioning request params (temperature, max_tokens, ...).
        # The CA-RAG summarization sampling is set by the deployment config, not the
        # request, so it is not recorded here.
        test_case_data = {
            "id": self._generate_test_case_id(video_config, chunk_size),
            "input_data": {
                "url": video_config.get("url") or video_config.get("filepath", ""),
                "model": model_name,
                "chunk-duration": chunk_size,
                "temperature": summarize_params.get("temperature", 0),
                "max_tokens": summarize_params["max_tokens"],
                "min_tokens": summarize_params.get("min_tokens"),
                "ignore_eos": summarize_params.get("ignore_eos"),
                "vlm_input_width": summarize_params.get("vlm_input_width", 0),
                "vlm_input_height": summarize_params.get("vlm_input_height", 0),
                "num_frames_per_chunk": summarize_params.get("num_frames_per_chunk", 0),
                "events": summarize_params["events"],
                "scenario": summarize_params["scenario"],
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

            # Load summarize response for chunks processed
            summarize_file = os.path.join(iteration_dir, "summarize_response.json")
            summarize_data = {}
            if os.path.exists(summarize_file):
                with open(summarize_file, "r") as f:
                    summarize_data = json.load(f)

            # Load formatted content for event stats
            formatted_file = os.path.join(iteration_dir, "summarize_response_formatted.json")
            events_detected = 0
            event_type_counts = {}
            if os.path.exists(formatted_file):
                with open(formatted_file, "r") as f:
                    content_data = json.load(f)
                events_detected = len(content_data.get("events", []))
                for ev in content_data.get("events", []):
                    etype = ev.get("type", "unknown")
                    event_type_counts[etype] = event_type_counts.get(etype, 0) + 1

            # Process GPU stats
            gpu_stats_file = os.path.join(iteration_dir, f"gpu_metrics_iter_{iteration}_stats.json")
            gpu_metrics = self.process_gpu_stats(gpu_stats_file)

            # Process CPU/RAM stats
            cpu_stats_file = os.path.join(iteration_dir, f"cpu_metrics_iter_{iteration}_stats.json")
            cpu_metrics = self.process_cpu_stats(cpu_stats_file)

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
                "filename": os.path.basename(
                    (test_case.get("video_url") or "").split("?")[0]
                ),
                "total_chunks_processed": summarize_data.get("usage", {}).get(
                    "total_chunks_processed", 0
                ),
                "vlm_pipeline_latency": api_metrics.get("vlm_pipeline_latency_seconds_latest", 0),
                "vlm_latency": api_metrics.get("vlm_latency_seconds_latest", 0),
                "decode_latency": api_metrics.get("decode_latency_seconds_latest", 0),
                "ca_rag_latency": api_metrics.get("ca_rag_latency_seconds_latest", 0),
                "e2e_latency": api_metrics.get("e2e_latency_seconds_latest", 0),
                "vlm_gpu_usage_mean": gpu_metrics.get("vlm_gpu_usage_mean", 0),
                "vlm_gpu_usage_p90": gpu_metrics.get("vlm_gpu_usage_p90", 0),
                "llm_gpu_usage_mean": gpu_metrics.get("llm_gpu_usage_mean", 0),
                "llm_gpu_usage_p90": gpu_metrics.get("llm_gpu_usage_p90", 0),
                "vlm_nvdec_usage_mean": gpu_metrics.get("vlm_nvdec_usage_mean", 0),
                "cpu_usage_mean": cpu_metrics.get("cpu_usage_mean", 0),
                "cpu_usage_p90": cpu_metrics.get("cpu_usage_p90", 0),
                "ram_percent_mean": cpu_metrics.get("ram_percent_mean", 0),
                "ram_percent_p90": cpu_metrics.get("ram_percent_p90", 0),
                "ram_used_gb_mean": cpu_metrics.get("ram_used_gb_mean", 0),
                "ram_used_gb_max": cpu_metrics.get("ram_used_gb_max", 0),
                "events_detected": events_detected,
                "event_type_counts": json.dumps(event_type_counts) if event_type_counts else "{}",
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
                "events_detected",
                "vlm_pipeline_latency",
                "vlm_latency",
                "decode_latency",
                "ca_rag_latency",
                "e2e_latency",
                "vlm_gpu_usage_mean",
                "vlm_gpu_usage_p90",
                "llm_gpu_usage_mean",
                "llm_gpu_usage_p90",
                "vlm_nvdec_usage_mean",
                "cpu_usage_mean",
                "cpu_usage_p90",
                "ram_percent_mean",
                "ram_percent_p90",
                "ram_used_gb_mean",
                "ram_used_gb_max",
            ]

            summary = {
                "test_case_id": test_case["test_case_id"],
                "filename": os.path.basename(
                    (test_case.get("video_url") or "").split("?")[0]
                ),
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

            summary["benchmark_mode"] = "single_file"

            return summary

        except Exception as e:
            self.logger.error(f"Error calculating test case summary: {e}")
            return None
