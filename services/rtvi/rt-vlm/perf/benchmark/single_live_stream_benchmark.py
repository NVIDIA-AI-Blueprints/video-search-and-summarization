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
Single Live Stream Benchmark Implementation

Handles single live stream testing with multiple iterations to measure
latency and performance for individual RTSP streams.
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict

import pandas as pd
import requests
import sseclient
from base import BenchmarkBase
from vlm_api import build_vlm_generation_request, merge_vlm_params


class SingleLiveStreamBenchmark(BenchmarkBase):
    """Single live stream benchmark - test individual live stream performance with iterations"""

    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Dict[str, Any]:
        """Parse single live stream benchmark configuration"""
        if scenario_config.get("benchmark_mode") != "single_live_stream":
            raise ValueError(f"Invalid benchmark mode: {scenario_config.get('benchmark_mode')}")

        if "videos" not in scenario_config:
            raise ValueError("Missing 'videos' field in scenario config")

        # Validate video configurations
        for i, video in enumerate(scenario_config["videos"]):
            if "rtsp_url" not in video:
                raise ValueError(f"Missing 'rtsp_url' in video {i}")
            if "chunk_sizes" not in video:
                raise ValueError(f"Missing 'chunk_sizes' in video {i}")

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
            "iterations": scenario_config.get("iterations", 3),
            "duration_seconds": scenario_config.get("duration_seconds", 60),
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
            "prompt": global_config.get("prompt", ""),
            "system_prompt": global_config.get("system_prompt", ""),
        }

    def execute(self, config: Dict, scenario_name: str) -> Dict[str, Any]:
        """Execute single live stream benchmark"""
        self.logger.info(f"Starting single live stream benchmark: {scenario_name}")

        global_config = self.parse_global_config(config)
        benchmark_config = self.parse_benchmark_config(
            config["test_scenarios"][scenario_name], global_config
        )

        scenario_dir = self.setup_scenario_directory(scenario_name)
        model_name = self.get_available_models()

        execution_results = {
            "scenario_name": scenario_name,
            "benchmark_mode": "single_live_stream",
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
            f"Single live stream benchmark completed: {execution_results['successful_test_cases']}/"
            f"{execution_results['total_test_cases']} test cases successful"
        )

        return execution_results

    def _generate_test_case_id(self, video_config: Dict, chunk_size: int) -> str:
        """Generate unique test case ID"""
        stream_name = video_config.get("name", "live_stream")
        return f"single_live_stream_{stream_name}_{chunk_size}sec"

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
            "rtsp_url": video_config["rtsp_url"],
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
        stream_id = None

        # Start GPU monitoring
        self.start_gpu_monitoring()

        try:
            # Add live stream
            stream_id = self._add_live_stream(video_config, iteration, iteration_dir)
            self.active_resources.append(f"stream_{stream_id}")

            # Capture metrics before stream monitoring to compute average decode latency
            start_metrics = self.scrape_metrics()

            # Run live stream monitoring
            latency_data = self._monitor_stream(
                stream_id, chunk_size, benchmark_config, model_name, iteration_dir, video_config
            )

            # Scrape metrics after stream monitoring
            end_metrics = self.scrape_metrics()
            metrics = dict(end_metrics)
            start_count = start_metrics.get("decode_latency_seconds_count")
            end_count = end_metrics.get("decode_latency_seconds_count")
            start_sum = start_metrics.get("decode_latency_seconds_sum")
            end_sum = end_metrics.get("decode_latency_seconds_sum")
            avg_decode_latency = 0.0
            if end_count is not None and end_sum is not None:
                count_delta = end_count - (start_count or 0)
                sum_delta = end_sum - (start_sum or 0)
                if count_delta > 0 and sum_delta >= 0:
                    avg_decode_latency = sum_delta / count_delta

            metrics.update(
                {
                    "decode_latency_seconds_count_start": start_count or 0,
                    "decode_latency_seconds_count_end": end_count or 0,
                    "decode_latency_seconds_sum_start": start_sum or 0,
                    "decode_latency_seconds_sum_end": end_sum or 0,
                    "decode_latency_seconds_avg": avg_decode_latency,
                }
            )
            metrics_file = os.path.join(iteration_dir, "metrics.json")
            self.save_json_data(metrics, metrics_file)

            # Save test case data for this iteration
            self._save_test_case_data(
                video_config, chunk_size, iteration, iteration_dir, benchmark_config, model_name
            )

            backend_type = benchmark_config.get("backend_type", "rtvi_vlm")
            status_message = (
                "embeddings_generation_completed"
                if backend_type == "rtvi_embed"
                else "captions_generation_completed"
            )

            return {
                "iteration": iteration,
                "success": True,
                "stream_id": stream_id,
                status_message: True,
                "api_metrics": metrics,
                "latency_data": latency_data,
            }

        finally:
            # Stop GPU monitoring and export data
            self.stop_gpu_monitoring(
                export_dir=iteration_dir, filename_prefix=f"gpu_metrics_iter_{iteration}"
            )

            # Cleanup stream
            if stream_id and f"stream_{stream_id}" in self.active_resources:
                try:
                    self.make_api_call(f"/streams/delete/{stream_id}", method="DELETE")
                    self.active_resources.remove(f"stream_{stream_id}")
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code in [400, 404]:
                        self.logger.warning(f"Stream {stream_id} already deleted or not found")
                        if f"stream_{stream_id}" in self.active_resources:
                            self.active_resources.remove(f"stream_{stream_id}")
                    else:
                        self.logger.error(f"Failed to cleanup stream {stream_id}: {e}")
                except Exception as e:
                    self.logger.error(f"Failed to cleanup stream {stream_id}: {e}")

    def _add_live_stream(self, video_config: Dict, iteration: int, iteration_dir: str) -> str:
        """Add a live stream for testing"""
        request_data = {
            "streams": [
                {
                    "liveStreamUrl": video_config["rtsp_url"],
                    "description": f"Single live stream test iteration {iteration}",
                }
            ]
        }

        self.logger.debug(
            f"Sending /streams/add request with payload: {json.dumps(request_data, indent=2)}"
        )

        response = self.make_api_call("/streams/add", method="POST", data=request_data)

        # Save response
        self.save_response(response, iteration_dir, "stream_add_response.json")

        result_json = response.json()
        if result_json.get("errors") and len(result_json["errors"]) > 0:
            error = result_json["errors"][0]
            raise Exception(f"Failed to add live stream: {error.get('error', 'Unknown error')}")
        if result_json.get("results") and len(result_json["results"]) > 0:
            stream_id = result_json["results"][0].get("id")
            self.logger.debug(f"Successfully created live stream: {stream_id}")
            return stream_id
        else:
            raise Exception("No results returned from API")

    def _monitor_stream(
        self,
        stream_id: str,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        iteration_dir: str,
        video_config: Dict,
    ) -> Dict[str, Any]:
        """Monitor stream for specified duration and collect latency data"""
        backend_type = benchmark_config.get("backend_type", "rtvi_vlm")
        duration_seconds = benchmark_config.get("duration_seconds", 60)

        # Get API params (works for both backends)
        if backend_type == "rtvi_embed":
            params = self._merge_with_defaults(
                video_config.get("generate_video_embeddings_params", {}),
                benchmark_config["api_params"],
            )
        else:
            params = merge_vlm_params(self._merge_with_defaults, video_config, benchmark_config)

        if backend_type == "rtvi_embed":
            # Build generate_video_embeddings request
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

        # Start streaming request
        try:
            url = f"{self.base_url}{endpoint}"
            response = self.session.post(url, json=request_data, stream=True)
            self.logger.debug(
                f"{endpoint} request successful for stream {stream_id}, "
                f"status: {response.status_code}"
            )
        except requests.exceptions.RequestException:
            self.logger.exception("%s request failed for stream %s", endpoint, stream_id)
            # Propagate to mark the iteration as failed
            raise

        if response.status_code >= 400:
            self.logger.error(f"{endpoint} failed, status: {response.status_code}")
            try:
                error_details = response.json()
                self.logger.error(f"Error details: {error_details}")
            except (json.JSONDecodeError, AttributeError):
                self.logger.error(f"Response text: {response.text}")
            raise Exception(f"Stream monitoring request failed with status {response.status_code}")

        # Save initial response
        response_filename = (
            "generate_video_embeddings_response.json"
            if backend_type == "rtvi_embed"
            else "generate_captions_response.json"
        )

        # Process SSE events and collect latency data
        latencies = []
        chunks_processed = 0
        start_time = time.time()
        event_count = 0
        elapsed_time = 0.0
        stream_deleted = False

        try:
            client = sseclient.SSEClient(response)
            self.logger.info(f"Monitoring stream for {duration_seconds} seconds...")

            for event in client.events():
                current_time = time.time()
                elapsed_time = current_time - start_time

                # Check if duration exceeded
                if elapsed_time >= duration_seconds and not stream_deleted:
                    self.logger.info(f"Duration reached ({duration_seconds}s), stopping monitoring")
                    # Attempt to delete the live stream after the monitoring duration is reached
                    try:
                        # Reuse common API call for consistency and timeouts
                        self.make_api_call(f"/streams/delete/{stream_id}", method="DELETE")
                        self.logger.info(f"Deleted live stream for URL: {video_config['rtsp_url']}")
                    except requests.exceptions.HTTPError as e:
                        self.logger.warning(
                            "Failed to delete live stream: "
                            f"status={e.response.status_code if e.response else 'unknown'}"
                        )
                    stream_deleted = True
                    self.active_resources.remove(f"stream_{stream_id}")

                data = event.data.strip()
                event_count += 1

                if data == "[DONE]":
                    self.logger.debug("Received [DONE] event")
                    break

                try:
                    result = json.loads(data)

                    if backend_type == "rtvi_embed":
                        # Process embeddings response
                        if result.get("embeddings"):
                            self.logger.debug(
                                f"Received embeddings: {len(result['embeddings'])} chunks"
                            )
                        if result.get("usage"):
                            chunks_processed = result["usage"].get("total_chunks_processed", 0)

                        # Track latency from timestamp events
                        if result.get("media_info", {}).get("type") == "timestamp":
                            end_timestamp = result["media_info"]["end_timestamp"]
                            dt = datetime.strptime(end_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                                tzinfo=timezone.utc
                            )
                            current_time_utc = datetime.now(timezone.utc)
                            latency = (current_time_utc - dt).total_seconds()
                            latencies.append(latency)
                            self.logger.debug(f"Recorded latency: {latency:.2f}s")
                    else:
                        # Process captions
                        choices = result.get("choices")
                        choice = choices[0] if choices and isinstance(choices[0], dict) else None
                        if choice and choice.get("finish_reason") == "stop":
                            message = choice.get("message")
                            delta = choice.get("delta")
                            message = message if isinstance(message, dict) else {}
                            delta = delta if isinstance(delta, dict) else {}
                            captions_content = message.get("content") or delta.get("content") or ""
                            self.logger.debug(f"Received captions: {captions_content[:100]}...")
                        if result.get("usage"):
                            chunks_processed = result["usage"].get("total_chunks_processed", 0)

                        # Track latency from timestamp events
                        if result.get("media_info", {}).get("type") == "timestamp":
                            end_timestamp = result["media_info"]["end_timestamp"]
                            dt = datetime.strptime(end_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                                tzinfo=timezone.utc
                            )
                            current_time_utc = datetime.now(timezone.utc)
                            latency = (current_time_utc - dt).total_seconds()
                            latencies.append(latency)
                            self.logger.debug(f"Recorded latency: {latency:.2f}s")

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    self.logger.error(f"Error processing stream event: {e}")
                    continue

        except Exception as e:
            self.logger.error(f"Error during stream monitoring: {e}")
            raise

        # Calculate latency statistics
        latency_data = {
            "latencies": latencies,
            "event_count": event_count,
            "chunks_processed": chunks_processed,
            "duration_seconds": elapsed_time,
        }

        if latencies:
            latency_data["avg_latency"] = sum(latencies) / len(latencies)
            latency_data["min_latency"] = min(latencies)
            latency_data["max_latency"] = max(latencies)

        # Save latency data
        latency_file = os.path.join(iteration_dir, response_filename)
        self.save_json_data(latency_data, latency_file)

        self.logger.info(
            f"Stream monitoring completed: {len(latencies)} latency "
            f"measurements, {chunks_processed} chunks processed"
        )

        return latency_data

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
        backend_type = benchmark_config.get("backend_type", "rtvi_vlm")

        # Get merged params based on backend type
        if backend_type == "rtvi_embed":
            api_params = self._merge_with_defaults(
                video_config.get("generate_video_embeddings_params", {}),
                benchmark_config["api_params"],
            )

            test_case_data = {
                "id": self._generate_test_case_id(video_config, chunk_size),
                "input_data": {
                    "rtsp_url": video_config["rtsp_url"],
                    "model": model_name,
                    "chunk_duration": chunk_size,
                    "chunk_overlap_duration": api_params.get("chunk_overlap_duration", 0),
                    "duration_seconds": benchmark_config.get("duration_seconds", 60),
                    "backend_type": backend_type,
                },
                "expected_result": {"status": "success"},
                "iteration": iteration,
            }
        else:
            api_params = self._merge_with_defaults(
                video_config.get("generate_captions_params", {}),
                benchmark_config["api_params"],
            )

            test_case_data = {
                "id": self._generate_test_case_id(video_config, chunk_size),
                "input_data": {
                    "rtsp_url": video_config["rtsp_url"],
                    "model": model_name,
                    "chunk_duration": chunk_size,
                    "temperature": api_params["temperature"],
                    "max_tokens": api_params["max_tokens"],
                    "enable_audio": api_params.get("enable_audio", False),
                    "vlm_input_width": api_params.get("vlm_input_width", 0),
                    "vlm_input_height": api_params.get("vlm_input_height", 0),
                    "duration_seconds": benchmark_config.get("duration_seconds", 60),
                    "prompt": benchmark_config.get("prompt", ""),
                    "backend_type": backend_type,
                },
                "expected_result": {"status": "success"},
                "iteration": iteration,
            }

        test_case_file = os.path.join(iteration_dir, "test_case_data.json")
        self.save_json_data(test_case_data, test_case_file)

    def analyze_results(self, results_dir: str, output_file: str) -> None:
        """Generate Excel report from single live stream benchmark results"""
        self.logger.debug(f"Analyzing single live stream results from: {results_dir}")

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
            self.logger.debug("Adding plots to single live stream Excel report...")
            if detail_data:
                detail_df = pd.DataFrame(detail_data)
                x_column = "test_case_id"

                # Determine backend type from the data
                backend_type = (
                    detail_df["backend_type"].iloc[0]
                    if "backend_type" in detail_df.columns
                    else "rtvi_vlm"
                )

                # Select appropriate latency columns based on backend type
                if backend_type == "rtvi_embed":
                    latency_columns = [
                        "e2e_latency",
                        "inference_pipeline_latency",
                        "avg_stream_latency",
                        "p95_stream_latency",
                    ]
                else:
                    latency_columns = [
                        "e2e_latency",
                        "vlm_pipeline_latency",
                        "avg_stream_latency",
                        "p95_stream_latency",
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

        self.logger.debug(f"Single live stream results analysis completed: {output_file}")

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

            # Load test case data to determine backend type
            test_case_file = os.path.join(iteration_dir, "test_case_data.json")
            backend_type = "rtvi_vlm"  # default
            if os.path.exists(test_case_file):
                with open(test_case_file, "r") as f:
                    test_case_data = json.load(f)
                    backend_type = test_case_data.get("input_data", {}).get(
                        "backend_type", "rtvi_vlm"
                    )

            # Load the appropriate response file based on backend type
            if backend_type == "rtvi_embed":
                response_file = os.path.join(
                    iteration_dir, "generate_video_embeddings_response.json"
                )
            else:
                response_file = os.path.join(iteration_dir, "generate_captions_response.json")

            latency_data = {}
            if os.path.exists(response_file):
                with open(response_file, "r") as f:
                    latency_data = json.load(f)

            # Process GPU stats
            gpu_stats_file = os.path.join(iteration_dir, f"gpu_metrics_iter_{iteration}_stats.json")
            gpu_metrics = self.process_gpu_stats(gpu_stats_file)

            # Combine all data - use inference_ prefix for rtvi_embed, vlm_ for rtvi_vlm
            pipeline_latency_key = (
                "inference_pipeline_latency"
                if backend_type == "rtvi_embed"
                else "vlm_pipeline_latency"
            )
            latency_key = "inference_latency" if backend_type == "rtvi_embed" else "vlm_latency"
            gpu_usage_key = (
                "inference_gpu_usage_mean" if backend_type == "rtvi_embed" else "vlm_gpu_usage_mean"
            )
            nvdec_usage_key = (
                "inference_nvdec_usage_mean"
                if backend_type == "rtvi_embed"
                else "vlm_nvdec_usage_mean"
            )

            iteration_data = {
                "test_case_id": test_case["test_case_id"],
                "rtsp_url": test_case["rtsp_url"],
                "chunks_processed": latency_data.get("chunks_processed", 0),
                "duration_seconds": latency_data.get("duration_seconds", 0),
                "avg_stream_latency": latency_data.get("avg_latency", 0),
                "min_stream_latency": latency_data.get("min_latency", 0),
                "max_stream_latency": latency_data.get("max_latency", 0),
                pipeline_latency_key: api_metrics.get(
                    "vlm_pipeline_latency_seconds_latest_seconds", 0
                ),
                latency_key: api_metrics.get("vlm_latency_seconds_latest_seconds", 0),
                "decode_latency": (
                    api_metrics.get("decode_latency_seconds_avg")
                    if "decode_latency_seconds_avg" in api_metrics
                    else api_metrics.get("decode_latency_seconds_latest_seconds", 0)
                ),
                "e2e_latency": api_metrics.get("e2e_latency_seconds_latest_seconds", 0),
                gpu_usage_key: gpu_metrics.get("vlm_gpu_usage_mean", 0),
                nvdec_usage_key: gpu_metrics.get("vlm_nvdec_usage_mean", 0),
                "benchmark_mode": "single_live_stream",
                "backend_type": backend_type,
                "chunk_size": test_case["chunk_size"],
                "iteration": iteration,
                "source_folder": f"iteration_{iteration}",
            }
            # Add Prometheus/DCGM metrics if present (from DCGM exporter)
            for k, v in gpu_metrics.items():
                if k.startswith("prometheus_") or k.startswith("nodeexporter_"):
                    iteration_data[k] = v

            return self.round_floats(iteration_data, 3)

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

            # Determine backend type from first iteration
            backend_type = iteration_data[0].get("backend_type", "rtvi_vlm")

            # Calculate statistics - mean ± std%
            # Use appropriate field names based on backend type
            if backend_type == "rtvi_embed":
                numeric_fields = [
                    "chunks_processed",
                    "duration_seconds",
                    "avg_stream_latency",
                    "inference_pipeline_latency",
                    "inference_latency",
                    "decode_latency",
                    "e2e_latency",
                    "inference_gpu_usage_mean",
                    "inference_nvdec_usage_mean",
                ]
            else:
                numeric_fields = [
                    "chunks_processed",
                    "duration_seconds",
                    "avg_stream_latency",
                    "vlm_pipeline_latency",
                    "vlm_latency",
                    "decode_latency",
                    "e2e_latency",
                    "vlm_gpu_usage_mean",
                    "vlm_nvdec_usage_mean",
                ]

            summary = {
                "test_case_id": test_case["test_case_id"],
                "rtsp_url": test_case["rtsp_url"],
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
                        summary[field] = f"{mean_val:.3f} ± {std_pct:.1f}%"
                else:
                    summary[field] = "0.00 ± 0.0%"

            summary["benchmark_mode"] = "single_live_stream"

            return summary

        except Exception as e:
            self.logger.error(f"Error calculating test case summary: {e}")
            return None
