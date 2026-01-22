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
Live Streams Benchmark Implementation

Tests maximum concurrent live streams without performance degradation.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict

import pandas as pd
import requests
import sseclient
from base import BenchmarkBase
from latency_tracker import LatencyTracker


class LiveStreamsBenchmark(BenchmarkBase):
    """Live streams benchmark - test maximum sustainable concurrent streams"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.latency_tracker = LatencyTracker()

    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Dict[str, Any]:
        """Parse live streams benchmark configuration"""
        if scenario_config.get("benchmark_mode") != "max_live_streams":
            raise ValueError(f"Invalid benchmark mode: {scenario_config.get('benchmark_mode')}")

        if "videos" not in scenario_config:
            raise ValueError("Missing 'videos' field in scenario config")

        # Validate video configurations
        for i, video in enumerate(scenario_config["videos"]):
            if "rtsp_url" not in video:
                raise ValueError(f"Missing 'rtsp_url' in video {i}")
            if "chunk_sizes" not in video:
                raise ValueError(f"Missing 'chunk_sizes' in video {i}")
            if "latency_threshold_seconds" not in video:
                raise ValueError(f"Missing 'latency_threshold_seconds' in video {i}")
            if "summary_duration" not in video:
                raise ValueError(f"Missing 'summary_duration' in video {i}")

        # Merge scenario-level API params with global
        summarize_params = self._merge_with_defaults(
            scenario_config.get("summarize_api_params", {}),
            global_config.get("summarize_api_params", {}),
        )

        return {
            "videos": scenario_config["videos"],
            "summarize_api_params": summarize_params,
            "prompts": global_config.get("prompts", {}),
        }

    def execute(self, config: Dict, scenario_name: str) -> Dict[str, Any]:
        """Execute live streams benchmark"""
        self.logger.info(f"Starting live streams benchmark: {scenario_name}")

        global_config = self.parse_global_config(config)
        benchmark_config = self.parse_benchmark_config(
            config["test_scenarios"][scenario_name], global_config
        )

        scenario_dir = self.setup_scenario_directory(scenario_name)
        model_name = self.get_available_models()

        execution_results = {
            "scenario_name": scenario_name,
            "benchmark_mode": "max_live_streams",
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
                    test_result = self._execute_live_streams_test_case(
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
            f"Live streams benchmark completed: {execution_results['successful_test_cases']}/"
            f"{execution_results['total_test_cases']} test cases successful"
        )

        return execution_results

    def _generate_test_case_id(self, video_config: Dict, chunk_size: int) -> str:
        """Generate unique test case ID"""
        stream_name = video_config.get("name", "live_stream")
        return f"max_live_streams_{stream_name}_{chunk_size}sec"

    def _execute_live_streams_test_case(
        self,
        test_case_id: str,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        scenario_dir: str,
    ) -> Dict[str, Any]:
        """Execute live streams test case - gradually increase streams until degradation"""
        self.logger.info(f"Starting max live streams test: {test_case_id}")

        test_case_dir = os.path.join(scenario_dir, test_case_id)
        os.makedirs(test_case_dir, exist_ok=True)

        # Configure connection pool for max live streams
        self._configure_http_session(300)

        # Start GPU monitoring
        self.start_gpu_monitoring()

        # Clear latency tracker
        self.latency_tracker.clear()

        active_futures = []
        active_stream_ids = []
        degradation_detected = False
        start_time = time.time()
        interrupted = False

        initial_stream_count = video_config.get("initial_stream_count", 5)
        latency_threshold = video_config["latency_threshold_seconds"]

        try:
            self.logger.info(f"Starting with {initial_stream_count} initial streams...")

            executor = ThreadPoolExecutor(max_workers=250)

            # Start initial streams
            for stream_num in range(1, initial_stream_count + 1):
                stream_id = self._add_live_stream(video_config, stream_num)
                if stream_id:
                    active_stream_ids.append(stream_id)
                    self.active_resources.append(f"stream_{stream_id}")
                    future = executor.submit(
                        self._monitor_stream_latency,
                        video_config,
                        chunk_size,
                        benchmark_config,
                        model_name,
                        stream_id,
                        stream_num,
                        test_case_dir,
                    )
                    active_futures.append(future)
                    self.logger.info(f"Launched stream {stream_num}")
                else:
                    self.logger.error(f"Failed to create initial stream {stream_num}")
                    break

            # Monitor and gradually add more streams
            last_check_time = time.time()
            stability_check_interval = video_config.get("stability_check_interval", 60)
            current_stream_count = len(active_stream_ids)
            self.logger.debug(f"Using stability check interval: {stability_check_interval} seconds")

            while not degradation_detected and not interrupted and current_stream_count < 200:
                try:
                    current_time = time.time()
                    if current_time - last_check_time >= stability_check_interval:
                        recent_stats = self.latency_tracker.get_stats()
                        avg_latency = recent_stats.get("avg_latency", 0)

                        # Check stability using p95 of recent readings
                        recent_p95 = self.latency_tracker.get_recent_p95()
                        is_stable = self.latency_tracker.is_stable(latency_threshold)

                        self.logger.info(
                            f"Stability check - Current streams: {current_stream_count}, "
                            f"Avg latency: {avg_latency:.2f}s, Recent P95: {recent_p95:.2f}s, "
                            f"Stable: {is_stable}"
                        )
                        self.logger.debug(f"Latency tracker stats: {recent_stats}")

                        # Print per-stream statistics
                        per_stream_stats_str = self.latency_tracker.get_per_stream_stats_str()
                        if per_stream_stats_str:
                            self.logger.info("Per-stream statistics:")
                            for line in per_stream_stats_str.split("\n"):
                                self.logger.info(line)

                        if not is_stable:
                            self.logger.info(
                                f"System unstable with {current_stream_count} streams. "
                                f"Recent P95 latency: {recent_p95:.2f}s "
                                f"exceeds threshold: {latency_threshold:.2f}s"
                            )
                            degradation_detected = self._verify_stability_and_adjust(
                                latency_threshold, active_stream_ids
                            )
                            # Update current_stream_count to reflect streams after verification
                            current_stream_count = len(active_stream_ids)
                            break

                        # If stable, add one more stream
                        current_stream_count += 1
                        stream_id = self._add_live_stream(video_config, current_stream_count)
                        if stream_id:
                            active_stream_ids.append(stream_id)
                            self.active_resources.append(f"stream_{stream_id}")
                            future = executor.submit(
                                self._monitor_stream_latency,
                                video_config,
                                chunk_size,
                                benchmark_config,
                                model_name,
                                stream_id,
                                current_stream_count,
                                test_case_dir,
                            )
                            active_futures.append(future)
                            self.logger.info(
                                f"Adding stream {current_stream_count} (latencies stable)"
                            )
                        else:
                            self.logger.error(f"Failed to create stream {current_stream_count}")
                            current_stream_count -= 1
                            # Stop testing if we can't add more streams
                            self.logger.warning("Cannot add more streams, stopping test")
                            break

                        last_check_time = current_time
                    else:
                        time.sleep(1)

                except KeyboardInterrupt:
                    interrupted = True
                    break

        except KeyboardInterrupt:
            interrupted = True
        finally:
            # Cleanup all streams
            self.logger.info(f"Cleaning up {len(active_stream_ids)} active streams...")
            for i, stream_id in enumerate(active_stream_ids, 1):
                try:
                    self.make_api_call(f"/live-stream/{stream_id}", method="DELETE")
                    self.logger.info(f"Deleted stream {i}/{len(active_stream_ids)}: {stream_id}")
                    if f"stream_{stream_id}" in self.active_resources:
                        self.active_resources.remove(f"stream_{stream_id}")
                except requests.exceptions.HTTPError as e:
                    # Handle 400/404 errors gracefully - stream may already be deleted
                    if e.response is not None and e.response.status_code in [400, 404]:
                        self.logger.warning(f"Stream {stream_id} already deleted or not found")
                        if f"stream_{stream_id}" in self.active_resources:
                            self.active_resources.remove(f"stream_{stream_id}")
                    else:
                        self.logger.error(f"Error deleting stream {stream_id}: {e}")
                except Exception as e:
                    self.logger.error(f"Error deleting stream {stream_id}: {e}")

            # Wait for monitoring threads to complete
            self.logger.info(f"Waiting for {len(active_futures)} monitoring threads to complete...")
            for i, future in enumerate(active_futures):
                try:
                    future.result(timeout=self.DEFAULT_THREAD_WAIT_TIMEOUT)
                except Exception as e:
                    self.logger.error(f"Thread {i+1} completion error: {e}")

            executor.shutdown(wait=True)

            # Stop GPU monitoring and export data
            self.stop_gpu_monitoring(
                export_dir=test_case_dir, filename_prefix="gpu_metrics_max_live_streams"
            )

        actual_duration = time.time() - start_time

        # Get combined stats
        latency_stats = self.latency_tracker.get_stats()

        # Process GPU stats
        gpu_metrics = {}
        gpu_stats_file = os.path.join(test_case_dir, "gpu_metrics_max_live_streams_stats.json")
        gpu_metrics = self.process_gpu_stats(gpu_stats_file)

        # Calculate max sustainable streams
        max_sustainable = current_stream_count

        latency_history = self.latency_tracker.get_all_latencies()

        results = {
            "test_case_id": test_case_id,
            "benchmark_mode": "max_live_streams",
            "rtsp_url": video_config["rtsp_url"],
            "chunk_size": chunk_size,
            "initial_stream_count": initial_stream_count,
            "max_sustainable_streams": max_sustainable,
            "degradation_detected": degradation_detected,
            "latency_threshold_seconds": latency_threshold,
            "total_test_duration_seconds": actual_duration,
            "total_streams_tested": current_stream_count,
            "success": max_sustainable > 0,
            **latency_stats,
            **gpu_metrics,
            "latency_history": latency_history,
        }

        # Save results
        results_file = os.path.join(test_case_dir, "max_live_streams_results.json")
        self.save_json_data(self.round_floats(results), results_file)

        self.logger.info(
            f"Max live streams test completed: {max_sustainable} sustainable streams "
            f"(threshold: {latency_threshold}s)"
        )

        return results

    def _add_live_stream(self, video_config: Dict, stream_num: int) -> str:
        """Add a live stream for testing"""
        request_data = {
            "liveStreamUrl": video_config["rtsp_url"],
            "description": f"Test stream {stream_num}",
            "camera_id": f"camera_{stream_num}",
        }

        self.logger.debug(
            f"Sending /live-stream request with payload: {json.dumps(request_data, indent=2)}"
        )

        response = self.make_api_call("/live-stream", method="POST", data=request_data)
        stream_id = response.json().get("id")
        self.logger.debug(f"Successfully created live stream {stream_num}: {stream_id}")
        return stream_id

    def _monitor_stream_latency(
        self,
        video_config: Dict,
        chunk_size: int,
        benchmark_config: Dict,
        model_name: str,
        stream_id: str,
        stream_num: int,
        test_case_dir: str,
    ):
        """Monitor latency for a specific stream using SSE"""
        # Create stream-specific directory for dumping responses
        stream_dir = os.path.join(test_case_dir, f"stream_{stream_num}")
        os.makedirs(stream_dir, exist_ok=True)

        try:
            # Get summarize params
            params = self._merge_with_defaults(
                video_config.get("summarize_api_params", {}),
                benchmark_config["summarize_api_params"],
            )

            # Start summarization with streaming
            request_data = {
                "id": [stream_id],
                "model": model_name,
                "response_format": {"type": "text"},
                "enable_chat": params["enable_chat"],
                "enable_chat_history": params["enable_chat_history"],
                "stream": True,
                "stream_options": {"include_usage": True},
                "chunk_duration": chunk_size,
                "temperature": params["temperature"],
                "max_tokens": params["max_tokens"],
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
            if video_prompts.get("summary_aggregation") or global_prompts.get(
                "summary_aggregation"
            ):
                request_data["summary_aggregation_prompt"] = video_prompts.get(
                    "summary_aggregation", global_prompts.get("summary_aggregation")
                )

            # Add optional live stream parameters
            if video_config.get("chunk_overlap_duration") is not None:
                request_data["chunk_overlap_duration"] = video_config["chunk_overlap_duration"]
            request_data["summary_duration"] = video_config["summary_duration"]

            self.logger.debug(
                f"Sending /summarize request with payload: {json.dumps(request_data, indent=2)}"
            )

            try:
                url = f"{self.base_url}/summarize"
                response = self.session.post(url, json=request_data, stream=True)
                self.logger.debug(
                    f"Summarization request successful for stream {stream_num}, "
                    f"status: {response.status_code}"
                )
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Summarization request failed for stream {stream_num}: {str(e)}")
                if hasattr(e, "response") and e.response is not None:
                    try:
                        error_details = e.response.json()
                        self.logger.error(f"Error details: {error_details}")
                    except (json.JSONDecodeError, AttributeError):
                        self.logger.error(f"Response text: {e.response.text}")
                return

            if response.status_code >= 400:
                self.logger.error(
                    f"Summarization failed for stream {stream_num}, status: {response.status_code}"
                )
                try:
                    error_details = response.json()
                    self.logger.error(f"Error details: {error_details}")
                except (json.JSONDecodeError, AttributeError):
                    self.logger.error(f"Response text: {response.text}")
                return

            # Process SSE events
            client = sseclient.SSEClient(response)
            self.logger.debug(f"Processing SSE events for stream {stream_num}")

            # Open file for dumping all events
            events_file = os.path.join(stream_dir, "sse_events.jsonl")
            event_count = 0

            with open(events_file, "w") as f:
                for event in client.events():
                    data = event.data.strip()
                    event_count += 1

                    # Write event to file
                    f.write(data + "\n")
                    f.flush()

                    self.logger.debug(
                        f"Stream {stream_num} received SSE event #{event_count}: {data[:200]}..."
                    )

                    if data == "[DONE]":
                        self.logger.debug(f"Stream {stream_num} received [DONE] event")
                        break

                    try:
                        result = json.loads(data)
                        self.logger.debug(f"Stream {stream_num} parsed JSON event successfully")

                        # Print summary content when available
                        if result.get("choices"):
                            choice = result["choices"][0]
                            if choice.get("finish_reason") == "stop":
                                summary_content = choice["message"]["content"]
                                self.logger.debug("")
                                self.logger.debug(f"=== Stream {stream_num} Summary ===")
                                self.logger.debug(f"Summary: {summary_content}")

                                # Print additional info if available
                                if result.get("usage"):
                                    usage = result["usage"]
                                    if usage.get("total_chunks_processed"):
                                        self.logger.debug(
                                            f"Chunks processed: {usage['total_chunks_processed']}"
                                        )
                                    if usage.get("query_processing_time"):
                                        self.logger.debug(
                                            f"Processing time: {usage['query_processing_time']:.2f}s"
                                        )
                                self.logger.debug("=" * 40)

                            elif choice.get("finish_reason") == "tool_calls":
                                self.logger.info(
                                    f"Stream {stream_num} Alert: "
                                    f"{choice['message']['tool_calls'][0]['alert']['name']}"
                                )
                                alert = choice["message"]["tool_calls"][0]["alert"]
                                self.logger.debug(
                                    f"Alert Details: {alert['detectedEvents']} - {alert['details']}"
                                )

                        # Track latency from timestamp events
                        if result.get("media_info", {}).get("type") == "timestamp":
                            end_timestamp = result["media_info"]["end_timestamp"]
                            self.logger.debug(
                                f"Stream {stream_num} processing timestamp event: {end_timestamp}"
                            )
                            dt = datetime.strptime(end_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                                tzinfo=timezone.utc
                            )
                            current_time = datetime.now(timezone.utc)
                            latency = (current_time - dt).total_seconds()

                            self.latency_tracker.record_latency(latency, stream_id)
                            self.logger.debug(
                                f"Stream {stream_num} recorded latency: {latency:.2f}s"
                            )

                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        self.logger.error(
                            f"Error processing stream event for stream {stream_num}: {e}"
                        )
                        continue

            # Log summary of events received
            self.logger.info(
                f"Stream {stream_num} received {event_count} total SSE events, saved to {events_file}"
            )

        except Exception as e:
            self.logger.error(f"Error monitoring stream {stream_num}: {e}")

    def _verify_stability_and_adjust(
        self, latency_threshold: float, active_stream_ids: list
    ) -> bool:
        """Verify system stability after detecting degradation"""
        verification_wait_time = 180  # 3 minutes
        reduction_attempts = 0

        self.logger.info("Starting stability verification phase...")

        while True:
            current_streams = len(active_stream_ids)

            if current_streams == 0:
                self.logger.error("No streams remaining - system could not stabilize")
                return True

            self.logger.info(
                f"Verification attempt {reduction_attempts + 1}: {current_streams} streams, "
                f"waiting {verification_wait_time}s to verify stability..."
            )

            # Wait for verification period
            time.sleep(verification_wait_time)

            # Check if system is now stable
            recent_p95 = self.latency_tracker.get_recent_p95()
            is_stable = self.latency_tracker.is_stable(latency_threshold)

            self.logger.info(
                f"Verification result: Recent P95: {recent_p95:.2f}s, Stable: {is_stable}"
            )

            if is_stable:
                self.logger.info(f"System stabilized with {current_streams} streams")
                return False  # No degradation - system is stable

            # Remove one stream
            if active_stream_ids:
                stream_to_remove = active_stream_ids.pop()
                try:
                    self.make_api_call(f"/live-stream/{stream_to_remove}", method="DELETE")
                    if f"stream_{stream_to_remove}" in self.active_resources:
                        self.active_resources.remove(f"stream_{stream_to_remove}")
                    # Remove stream data from latency tracker
                    self.latency_tracker.remove_stream(stream_to_remove)
                    self.logger.info(f"Removed stream {stream_to_remove}")
                except requests.exceptions.HTTPError as e:
                    # Handle 400/404 errors gracefully - stream may already be deleted
                    if e.response is not None and e.response.status_code in [400, 404]:
                        self.logger.warning(
                            f"Stream {stream_to_remove} already deleted or not found"
                        )
                        if f"stream_{stream_to_remove}" in self.active_resources:
                            self.active_resources.remove(f"stream_{stream_to_remove}")
                        # Still remove from latency tracker
                        self.latency_tracker.remove_stream(stream_to_remove)
                    else:
                        self.logger.error(f"Error removing stream {stream_to_remove}: {e}")
                except Exception as e:
                    self.logger.error(f"Error removing stream {stream_to_remove}: {e}")

                # Print per-stream statistics after removing stream for stability
                per_stream_stats_str = self.latency_tracker.get_per_stream_stats_str()
                if per_stream_stats_str:
                    self.logger.info("Per-stream statistics after removal:")
                    for line in per_stream_stats_str.split("\n"):
                        self.logger.info(line)

            reduction_attempts += 1
            if reduction_attempts >= 5:  # Limit reduction attempts
                break

        # Max reduction attempts reached without stabilizing
        return True

    def analyze_results(self, results_dir: str, output_file: str) -> None:
        """Generate Excel report from live streams benchmark results"""
        self.logger.debug(f"Analyzing live streams results from: {results_dir}")

        # Load execution summary
        summary_file = os.path.join(results_dir, "execution_summary.json")
        if not os.path.exists(summary_file):
            raise FileNotFoundError(f"Execution summary not found: {summary_file}")

        with open(summary_file, "r") as f:
            execution_summary = json.load(f)

        # Parse all test case results
        summary_data = []

        for test_case in execution_summary["test_cases"]:
            if not test_case.get("success", False):
                continue

            test_case_id = test_case["test_case_id"]
            test_case_dir = os.path.join(results_dir, test_case_id)

            # Load live streams results
            results_file = os.path.join(test_case_dir, "max_live_streams_results.json")
            if os.path.exists(results_file):
                with open(results_file, "r") as f:
                    stream_results = json.load(f)

                result_summary = {
                    "test_case_id": test_case_id,
                    "benchmark_mode": "max_live_streams",
                    "rtsp_url": stream_results.get("rtsp_url", ""),
                    "chunk_size": stream_results.get("chunk_size", 0),
                    "max_sustainable_streams": stream_results.get("max_sustainable_streams", 0),
                    "latency_threshold_seconds": stream_results.get("latency_threshold_seconds", 0),
                    "total_streams_tested": stream_results.get("total_streams_tested", 0),
                    "avg_latency": stream_results.get("avg_latency", 0),
                    "max_latency": stream_results.get("max_latency", 0),
                    "vlm_gpu_usage_mean": stream_results.get("vlm_gpu_usage_mean", 0),
                    "vlm_gpu_usage_p90": stream_results.get("vlm_gpu_usage_p90", 0),
                    "llm_gpu_usage_mean": stream_results.get("llm_gpu_usage_mean", 0),
                    "llm_gpu_usage_p90": stream_results.get("llm_gpu_usage_p90", 0),
                    "vlm_nvdec_usage_mean": stream_results.get("vlm_nvdec_usage_mean", 0),
                }

                summary_data.append(self.round_floats(result_summary))

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

                # Individual test case sheets
                for i, result in enumerate(summary_data):
                    test_case_df = pd.DataFrame([result])
                    sheet_name = result["test_case_id"][:31]
                    test_case_df.to_excel(writer, sheet_name=sheet_name, index=False)

        self.logger.debug(f"Live streams results analysis completed: {output_file}")
