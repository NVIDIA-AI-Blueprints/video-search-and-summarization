# SPDX-FileCopyrightText: Copyright (c) 2023-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import json
import math
import os
import queue
import shutil
import time
import uuid
from argparse import ArgumentParser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum, auto
from threading import Event, RLock, Thread
from typing import Callable, Optional

import cuda.bindings.runtime
import gi
import nvtx
from google.protobuf.timestamp_pb2 import Timestamp
from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter

import redis
from api_models.captions import VlmQuery
from api_models.embeddings import TextEmbeddingsQuery
from common.chunk_info import ChunkInfo
from common.logger import logger
from common.service_exception import ServiceException
from common.version import VERSION
from kafka import KafkaProducer
from kafka.errors import KafkaError
from server.protos import ext_pb2, nv_pb2
from utils.asset_manager import Asset
from utils.dense_caption_serializer import DenseCaptionSerializer
from utils.file_splitter import ntp_to_unix_timestamp
from utils.media_file_info import MediaFileInfo
from utils.otel_helper import create_historical_span, get_tracer
from utils.request_profiler import GPUMonitor, RequestMetrics

from vlm_pipeline import VlmPipeline, PipelineChunkResult  # isort:skip


# Convert PTS offset to absolute ISO8601 timestamp.
def convert_pts_to_absolute_timestamp(creation_time: str, pts: float) -> str:
    """Convert PTS offset to absolute ISO8601 timestamp."""
    try:
        creation_dt = datetime.strptime(creation_time, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        creation_dt = datetime.strptime(creation_time, "%Y-%m-%dT%H:%M:%SZ")

    creation_timestamp = creation_dt.replace(tzinfo=timezone.utc).timestamp()
    absolute_timestamp = creation_timestamp + pts / 1e9
    timestamp = datetime.fromtimestamp(absolute_timestamp, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    return timestamp[:-4] + "Z"


# Build media_info dictionary for non-streaming response.
def build_media_info_dict_non_streaming(
    creation_time: str, start_timestamp: float, end_timestamp: float
):
    """Build media_info dictionary based on non-streaming response."""
    if creation_time:
        start_offset = convert_pts_to_absolute_timestamp(creation_time, start_timestamp * 1e9)
        end_offset = convert_pts_to_absolute_timestamp(creation_time, end_timestamp * 1e9)
        return {
            "type": "timestamp",
            "start_timestamp": start_offset,
            "end_timestamp": end_offset,
        }
    else:
        start_offset = int(start_timestamp)
        end_offset = int(end_timestamp)
        return {"type": "offset", "start_offset": start_offset, "end_offset": end_offset}


# Build media_info dictionary for live/file response.
def build_media_info_dict(is_live: bool, first_resp, creation_time: str):
    """Build media_info dictionary based on live/file response."""
    if is_live:
        return {
            "type": "timestamp",
            "start_timestamp": first_resp.chunk.start_ntp,
            "end_timestamp": first_resp.chunk.end_ntp,
        }
    else:
        if creation_time:
            start_offset = convert_pts_to_absolute_timestamp(
                creation_time, first_resp.chunk.start_pts
            )
            end_offset = convert_pts_to_absolute_timestamp(creation_time, first_resp.chunk.end_pts)
            return {
                "type": "timestamp",
                "start_timestamp": start_offset,
                "end_timestamp": end_offset,
            }
        else:
            start_offset = int(first_resp.chunk.start_pts / 1e9)
            end_offset = int(first_resp.chunk.end_pts / 1e9)
            return {"type": "offset", "start_offset": start_offset, "end_offset": end_offset}


@dataclass
class RequestInfo:
    """Store information for a request"""

    class Status(StrEnum):
        """Video Query Request Status."""

        QUEUED = auto()
        PROCESSING = auto()
        SUCCESSFUL = auto()
        FAILED = auto()

    # Request identification
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Query information
    query: VlmQuery | None = None  # VlmQuery object - contains all query parameters
    chunk_count: int = 0
    video_fps: float | None = None

    # Text embeddings query
    text_query: TextEmbeddingsQuery | None = None

    # Results
    processed_chunk_list: list[PipelineChunkResult] = field(default_factory=list)
    response: list[PipelineChunkResult] = field(default_factory=list)

    # Stream information
    is_live: bool = False
    start_timestamp: float | None = None
    end_timestamp: float | None = None

    # Timing
    queue_time: float | None = None
    start_time: float | None = None
    end_time: float | None = None
    file_duration: int = 0

    # Assets and status
    assets: list[Asset] | None = None
    status: "RequestInfo.Status" = field(default_factory=lambda: RequestInfo.Status.QUEUED)
    status_event: Event = field(default_factory=Event)

    # Metrics and monitoring
    _request_metrics: object | None = None
    _monitor: object | None = None

    # NVTX profiling
    nvtx_vlm_start: object | None = None
    nvtx_summarization_start: object | None = None

    # OTEL spans
    _e2e_span: object | None = None
    vlm_pipeline_span: object | None = None

    # FPS metrics
    _fps_start_time: float | None = None
    _fps_frame_count: int = 0
    _fps_last_update_time: float | None = None
    _fps_is_active: bool = False

    # Error tracking
    error_message: str = ""
    error_status_code: int = 500

    # File handles for test data
    vlm_testdata_file_handle: object | None = None

    media_file_info: MediaFileInfo | None = None

    @property
    def stream_id(self) -> str:
        """Return the stream/asset ID from the first asset."""
        return self.assets[0].asset_id if self.assets else ""

    @property
    def progress(self) -> float:
        """Calculate progress as percentage of processed chunks."""
        # If request is completed or failed, return 100%
        if self.status in (RequestInfo.Status.SUCCESSFUL, RequestInfo.Status.FAILED):
            return 100.0
        if self.is_live:
            return 0.0
        # If no chunks to process, return 100%
        if self.chunk_count == 0:
            return 100.0
        # Return progress based on processed chunks (capped at 90% until complete)
        return (len(self.processed_chunk_list) / self.chunk_count) * 100.0


def get_timestamp_str(seconds: float) -> str:
    """Get RFC3339 string timestamp"""
    return (
        datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        + f".{(int(seconds * 1000) % 1000):03d}Z"
    )


class RTVIStreamHandler:
    """Stream Handler"""

    @staticmethod
    def get_histogram_views():
        """Get OpenTelemetry Views for histogram bucket configuration.

        Define histogram bucket boundaries that match the original Prometheus configuration.
        These Views must be passed to the MeterProvider during initialization.

        Returns:
            List of View objects for histogram configuration
        """
        try:
            from opentelemetry.sdk.metrics._internal.aggregation import (
                ExplicitBucketHistogramAggregation,
            )
            from opentelemetry.sdk.metrics.view import View

            return [
                # Stream FPS histogram - measures frames per second
                View(
                    instrument_name="stream_fps",
                    aggregation=ExplicitBucketHistogramAggregation(
                        boundaries=[
                            1.0,
                            5.0,
                            10.0,
                            20.0,
                            30,
                            50.0,
                            100.0,
                            200,
                            300,
                            400,
                            500,
                            750,
                            1000,
                            5000,
                        ]
                    ),
                ),
                # Decode latency histogram - video decode processing time
                View(
                    instrument_name="decode_latency_seconds",
                    aggregation=ExplicitBucketHistogramAggregation(
                        boundaries=[0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0]
                    ),
                ),
                # VLM latency histogram - VLM model inference time
                View(
                    instrument_name="vlm_latency_seconds",
                    aggregation=ExplicitBucketHistogramAggregation(
                        boundaries=[
                            0.1,
                            0.3,
                            1.0,
                            3.0,
                            5.0,
                            10.0,
                            15.0,
                            20.0,
                            25.0,
                            30.0,
                            35.0,
                            40.0,
                            45.0,
                            50.0,
                        ]
                    ),
                ),
                # VLM input tokens histogram - tokens sent to VLM
                View(
                    instrument_name="vlm_input_tokens_per_chunk",
                    aggregation=ExplicitBucketHistogramAggregation(
                        boundaries=[10, 20, 50, 100, 200, 500, 1000, 2000]
                    ),
                ),
                # VLM output tokens histogram - tokens generated by VLM
                View(
                    instrument_name="vlm_output_tokens_per_chunk",
                    aggregation=ExplicitBucketHistogramAggregation(
                        boundaries=[10, 20, 50, 100, 200, 500, 1000, 2000]
                    ),
                ),
                # ASR pipeline latency histogram - audio transcription time
                View(
                    instrument_name="asr_pipeline_latency_seconds",
                    aggregation=ExplicitBucketHistogramAggregation(
                        boundaries=[0.1, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0]
                    ),
                ),
                # Live stream summary latency histogram
                View(
                    instrument_name="live_stream_chunk_latency_seconds",
                    aggregation=ExplicitBucketHistogramAggregation(
                        boundaries=[
                            0.1,
                            0.3,
                            0.5,
                            1.0,
                            2.0,
                            3.0,
                            5.0,
                            10.0,
                            15.0,
                            20.0,
                            30.0,
                            40.0,
                            50.0,
                            70.0,
                            100.0,
                            200,
                            300,
                            500,
                            1000,
                        ]
                    ),
                ),
                # Live stream captions latency histogram
                View(
                    instrument_name="live_stream_captions_latency_seconds",
                    aggregation=ExplicitBucketHistogramAggregation(
                        boundaries=[
                            0.1,
                            0.3,
                            0.5,
                            1.0,
                            2.0,
                            3.0,
                            5.0,
                            10.0,
                            15.0,
                            20.0,
                            30.0,
                            40.0,
                            50.0,
                            70.0,
                            100.0,
                            200,
                            300,
                            500,
                            1000,
                        ]
                    ),
                ),
                # Stream delete latency histogram - end-to-end remove_rtsp_stream wall clock
                View(
                    instrument_name="rtvi_stream_delete_latency_seconds",
                    aggregation=ExplicitBucketHistogramAggregation(
                        boundaries=[
                            0.01,
                            0.025,
                            0.05,
                            0.1,
                            0.25,
                            0.5,
                            1.0,
                            2.5,
                            5.0,
                            10.0,
                            25.0,
                            60.0,
                        ]
                    ),
                ),
                # Stream delete drain latency histogram - VlmPipeline drain wait inside delete
                View(
                    instrument_name="rtvi_stream_delete_drain_latency_seconds",
                    aggregation=ExplicitBucketHistogramAggregation(
                        boundaries=[
                            0.01,
                            0.025,
                            0.05,
                            0.1,
                            0.25,
                            0.5,
                            1.0,
                            2.5,
                            5.0,
                            10.0,
                            25.0,
                            60.0,
                        ]
                    ),
                ),
            ]
        except ImportError as e:
            logger.warning(
                f"Could not import View or ExplicitBucketHistogramAggregation: {e}. "
                "Histograms will use default bucket boundaries."
            )
            return []

    class Metrics:
        def __init__(self, start_time: float, service_name: str = "rtvi") -> None:
            """Initialize the Stream Handler metrics using OpenTelemetry.

            Args:
                start_time: Timestamp when the service started
                service_name: Service name for meter identification
            """
            # Get the meter from the global MeterProvider
            meter_provider = metrics.get_meter_provider()
            self._meter: Meter = meter_provider.get_meter(
                service_name,
                version=VERSION,
            )

            # Store start time for uptime calculation
            self._start_time = start_time

            # Storage for latest values (used by observable gauges)
            # Note: Counter values are no longer stored since UpDownCounters handle this directly
            self._e2e_latency_latest_value = 0.0
            self._vlm_pipeline_latency_latest_value = 0.0
            self._decode_latency_latest_value = 0.0
            self._vlm_latency_latest_value = 0.0
            self._chunk_latency_latest_value = 0.0
            self._asr_pipeline_latency_latest_value = 0.0
            self._live_stream_captions_latency_latest_value = 0.0
            self._live_stream_chunk_latency_latest_value = 0.0

            # UpDownCounters for counts that increment/decrement
            self._queries_processed_counter = self._meter.create_up_down_counter(
                name="video_file_queries_processed",
                description="Number of video file queries whose processing is complete",
                unit="1",
            )

            self._queries_pending_counter = self._meter.create_up_down_counter(
                name="video_file_queries_pending",
                description="Number of video file queries which are queued and yet to be processed",
                unit="1",
            )

            self._active_live_streams_counter = self._meter.create_up_down_counter(
                name="active_live_streams",
                description="Number of live streams whose summaries are being actively generated",
                unit="1",
            )

            self._decode_retry_counter = self._meter.create_counter(
                name="rtvi_decode_retry_total",
                description="Number of video file decode retries after frame extraction errors",
                unit="1",
            )

            # Initialize counters to 0 so they appear in metrics immediately
            # OpenTelemetry instruments only export after first recording
            self._queries_processed_counter.add(0)
            self._queries_pending_counter.add(0)
            self._active_live_streams_counter.add(0)
            self._decode_retry_counter.add(0, {"reason": "first_attempt_error"})

            # Observable gauge for system uptime
            self._meter.create_observable_gauge(
                name="system_uptime_seconds",
                callbacks=[self._observe_system_uptime],
                description="Number of seconds the system has been running",
                unit="s",
            )

            # Histograms for distributions
            self._stream_fps_histogram = self._meter.create_histogram(
                name="stream_fps",
                description="FPS measurements per stream",
                unit="fps",
            )

            self._decode_latency = self._meter.create_histogram(
                name="decode_latency_seconds",
                description="Video decode processing latency in seconds",
                unit="s",
            )

            self._vlm_latency = self._meter.create_histogram(
                name="vlm_latency_seconds",
                description="VLM processing latency in seconds",
                unit="s",
            )

            self._chunk_latency = self._meter.create_histogram(
                name="chunk_latency_seconds",
                description="Chunk processing latency in seconds (decode start to VLM end)",
                unit="s",
            )

            self._delete_latency = self._meter.create_histogram(
                name="rtvi_stream_delete_latency_seconds",
                description="End-to-end wall-clock latency of a single live-stream delete",
                unit="s",
            )

            self._delete_drain_latency = self._meter.create_histogram(
                name="rtvi_stream_delete_drain_latency_seconds",
                description="Time spent waiting for VlmPipeline drain inside remove_live_stream",
                unit="s",
            )

            self._vlm_input_tokens = self._meter.create_histogram(
                name="vlm_input_tokens_per_chunk",
                description="Number of tokens input to the VLM model per chunk",
                unit="tokens",
            )

            self._vlm_output_tokens = self._meter.create_histogram(
                name="vlm_output_tokens_per_chunk",
                description="Number of tokens output from the VLM model per chunk",
                unit="tokens",
            )

            self._asr_pipeline_latency = self._meter.create_histogram(
                name="asr_pipeline_latency_seconds",
                description="ASR pipeline processing latency in seconds",
                unit="s",
            )

            self._live_stream_chunk_latency = self._meter.create_histogram(
                name="live_stream_chunk_latency_seconds",
                description="Live stream chunk processing latency in seconds",
                unit="s",
            )

            self._live_stream_captions_latency = self._meter.create_histogram(
                name="live_stream_captions_latency_seconds",
                description="Live stream captions processing latency in seconds",
                unit="s",
            )

            # Observable gauges for latest values
            self._meter.create_observable_gauge(
                name="e2e_latency_seconds_latest",
                callbacks=[lambda options: [metrics.Observation(self._e2e_latency_latest_value)]],
                description="Latest end-to-end latency in seconds",
                unit="s",
            )

            self._meter.create_observable_gauge(
                name="vlm_pipeline_latency_seconds_latest",
                callbacks=[
                    lambda options: [metrics.Observation(self._vlm_pipeline_latency_latest_value)]
                ],
                description="Latest latency of the VLM pipeline processing in seconds",
                unit="s",
            )

            self._meter.create_observable_gauge(
                name="decode_latency_seconds_latest",
                callbacks=[
                    lambda options: [metrics.Observation(self._decode_latency_latest_value)]
                ],
                description="Latest video decode processing latency in seconds",
                unit="s",
            )

            self._meter.create_observable_gauge(
                name="vlm_latency_seconds_latest",
                callbacks=[lambda options: [metrics.Observation(self._vlm_latency_latest_value)]],
                description="Latest VLM processing latency in seconds",
                unit="s",
            )

            self._meter.create_observable_gauge(
                name="chunk_latency_seconds_latest",
                callbacks=[lambda options: [metrics.Observation(self._chunk_latency_latest_value)]],
                description="Latest chunk processing latency in seconds",
                unit="s",
            )

            self._meter.create_observable_gauge(
                name="asr_pipeline_latency_seconds_latest",
                callbacks=[
                    lambda options: [metrics.Observation(self._asr_pipeline_latency_latest_value)]
                ],
                description="Latest ASR pipeline processing latency in seconds",
                unit="s",
            )

            self._meter.create_observable_gauge(
                name="live_stream_chunk_latency_seconds_latest",
                callbacks=[
                    lambda options: [
                        metrics.Observation(self._live_stream_chunk_latency_latest_value)
                    ]
                ],
                description="Latest live stream chunk processing latency in seconds",
                unit="s",
            )

            self._meter.create_observable_gauge(
                name="live_stream_captions_latency_seconds_latest",
                callbacks=[
                    lambda options: [
                        metrics.Observation(self._live_stream_captions_latency_latest_value)
                    ]
                ],
                description="Latest live stream captions processing latency in seconds",
                unit="s",
            )

        def _observe_system_uptime(self, options) -> list:
            """Callback for system uptime observable gauge."""
            uptime = time.time() - self._start_time
            return [metrics.Observation(uptime)]

    def __init__(self, args, service_name: str = "rtvi") -> None:
        """Initialize the Stream Handler

        Args:
            args: Command line arguments
            service_name: Service name for metrics identification (e.g., 'rtvi-vlm', 'rtvi-embed')
        """
        logger.info("Initializing Stream Handler for service: %s", service_name)

        self._lock = RLock()
        self._stopping = False
        self._request_info_map: dict[str, RequestInfo] = {}

        self._start_time = time.time()
        self._metrics = RTVIStreamHandler.Metrics(self._start_time, service_name)

        self._args = args

        # Optional dedicated executor for background filesystem cleanup
        # (rmtree of per-stream cached-frames dirs). Wired in by the owning
        # server via set_cleanup_executor; None means inline rmtree.
        self._cleanup_executor = None

        request_profiling_value = os.environ.get("ENABLE_REQUEST_PROFILING", "").lower()
        self._profile_requests = request_profiling_value in ("true", "1")

        # Initialize Kafka producer if enabled
        # argparse converts --kafka-enabled to kafka_enabled attribute
        # Priority: command-line args > environment variables > defaults
        kafka_enabled_arg = getattr(args, "kafka_enabled", "false")
        kafka_topic_arg = getattr(args, "kafka_topic", "mdx-vlm-captions")
        kafka_bootstrap_servers_arg = getattr(args, "kafka_bootstrap_servers", "")

        # Parse kafka_enabled: command-line arg takes precedence
        # If command-line arg is the default "false", check environment variable
        if kafka_enabled_arg == "false":
            kafka_enabled_env = os.environ.get("KAFKA_ENABLED", "false")
            kafka_enabled = str(kafka_enabled_env).lower() == "true"
        else:
            kafka_enabled = str(kafka_enabled_arg).lower() == "true"

        self._kafka_enabled = kafka_enabled

        # Parse kafka_topic: command-line arg takes precedence
        # If command-line arg is the default, check environment variable
        if kafka_topic_arg == "mdx-vlm-captions":
            self._kafka_topic = os.environ.get("KAFKA_TOPIC", "mdx-vlm-captions")
        else:
            self._kafka_topic = str(kafka_topic_arg)

        # Parse kafka_bootstrap_servers: command-line arg takes precedence
        # If command-line arg is empty (default), check environment variable
        if kafka_bootstrap_servers_arg == "":
            kafka_bootstrap_servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "")
        else:
            kafka_bootstrap_servers = str(kafka_bootstrap_servers_arg)

        self._kafka_producer = None
        self._kafka_send_queue = None
        self._kafka_send_thread = None
        self._kafka_send_stop_event = Event()
        try:
            self._kafka_send_queue_maxsize = int(
                os.environ.get("KAFKA_ASYNC_SEND_QUEUE_MAXSIZE", "1024")
            )
        except ValueError:
            logger.warning(
                "Invalid KAFKA_ASYNC_SEND_QUEUE_MAXSIZE=%r; using 1024",
                os.environ.get("KAFKA_ASYNC_SEND_QUEUE_MAXSIZE"),
            )
            self._kafka_send_queue_maxsize = 1024
        if self._kafka_send_queue_maxsize <= 0:
            logger.warning(
                "KAFKA_ASYNC_SEND_QUEUE_MAXSIZE must be positive; using 1024 instead of %s",
                self._kafka_send_queue_maxsize,
            )
            self._kafka_send_queue_maxsize = 1024
        self._kafka_incident_topic = os.environ.get("KAFKA_INCIDENT_TOPIC", "mdx-vlm-incidents")

        if service_name == "rtvi-vlm":
            self._kafka_error_topic = os.environ.get("ERROR_MESSAGE_TOPIC", "mdx-vlm-errors")
        else:
            self._kafka_error_topic = os.environ.get("ERROR_MESSAGE_TOPIC", "mdx-embed-errors")

        # Initialize Redis client for error messages
        self._redis_client = None
        self._redis_send_queue = None
        self._redis_send_thread = None
        self._redis_send_stop_event = Event()
        try:
            self._redis_send_queue_maxsize = int(
                os.environ.get("REDIS_ASYNC_PUBLISH_QUEUE_MAXSIZE", "1024")
            )
        except ValueError:
            logger.warning(
                "Invalid REDIS_ASYNC_PUBLISH_QUEUE_MAXSIZE=%r; using 1024",
                os.environ.get("REDIS_ASYNC_PUBLISH_QUEUE_MAXSIZE"),
            )
            self._redis_send_queue_maxsize = 1024
        if self._redis_send_queue_maxsize <= 0:
            logger.warning(
                "REDIS_ASYNC_PUBLISH_QUEUE_MAXSIZE must be positive; using 1024 instead of %s",
                self._redis_send_queue_maxsize,
            )
            self._redis_send_queue_maxsize = 1024
        self._use_redis_error_bus = (
            os.environ.get("ENABLE_REDIS_ERROR_MESSAGES", "false").lower() == "true"
        )

        if service_name == "rtvi-vlm":
            self._redis_error_channel = os.environ.get("ERROR_MESSAGE_TOPIC", "mdx-vlm-errors")
        else:
            self._redis_error_channel = os.environ.get("ERROR_MESSAGE_TOPIC", "mdx-embed-errors")

        try:
            import socket  # isort: skip

            podname = socket.gethostname()
        except Exception as e:
            logger.error("Failed to get hostname: %s", e)
            podname = service_name
        self._podname = podname

        # Initialize place configuration from environment variables with defaults
        self._place_config = {
            "name": os.environ.get("PLACE_NAME", "Dock Entrance-East"),
            "type": os.environ.get("PLACE_TYPE", "warehouse-bay"),
            "lat": float(os.environ.get("PLACE_LAT", "37.3706")),
            "lon": float(os.environ.get("PLACE_LON", "-121.9672")),
            "alt": float(os.environ.get("PLACE_ALT", "0.0")),
            "coordinate_x": float(os.environ.get("PLACE_COORDINATE_X", "12.5")),
            "coordinate_y": float(os.environ.get("PLACE_COORDINATE_Y", "4.2")),
        }

        # Initialize analytics module configuration from environment variables with defaults
        self._analytics_module_config = {
            "id": os.environ.get("ANALYTICS_MODULE_ID", "vlm-activity-detector"),
            "description": os.environ.get(
                "ANALYTICS_MODULE_DESCRIPTION", "RTVI Safety Compliance Detector"
            ),
            "source": os.environ.get("ANALYTICS_MODULE_SOURCE", "rtvi-vlm"),
        }

        if kafka_enabled:
            if not kafka_bootstrap_servers:
                logger.warning(
                    "KAFKA_ENABLED is true but KAFKA_BOOTSTRAP_SERVERS not set. Kafka disabled."
                )
            else:
                try:
                    bootstrap_servers_list = [s.strip() for s in kafka_bootstrap_servers.split(",")]
                    logger.info(
                        "Initializing Kafka producer. Topic: %s, Bootstrap servers: %s",
                        self._kafka_topic,
                        bootstrap_servers_list,
                    )
                    # Try to create producer with idempotence first (requires Kafka >= 0.11)
                    # If that fails, fall back to non-idempotent producer
                    # Let Kafka auto-detect API version for best compatibility
                    producer_config = {
                        "bootstrap_servers": bootstrap_servers_list,
                        "value_serializer": lambda v: v,  # Already serialized protobuf bytes
                        "acks": "all",  # Wait for all replicas
                        "retries": 3,
                        "max_in_flight_requests_per_connection": 1,
                        "request_timeout_ms": 60000,  # 60 second timeout for metadata
                        "metadata_max_age_ms": 300000,  # Refresh metadata every 5 minutes
                        "connections_max_idle_ms": 540000,  # Close idle connections after 9 minutes
                    }

                    try:
                        # Try with idempotence first
                        producer_config["enable_idempotence"] = True
                        self._kafka_producer = KafkaProducer(**producer_config)
                    except Exception as idempotence_error:
                        if "Idempotent" in str(idempotence_error) or "0.11" in str(
                            idempotence_error
                        ):
                            logger.warning(
                                "Kafka broker version < 0.11 detected or idempotence not supported. "
                                "Creating producer without idempotence. Error: %s",
                                idempotence_error,
                            )
                            # Fall back to non-idempotent producer for older Kafka versions
                            producer_config.pop("enable_idempotence", None)
                            producer_config["max_in_flight_requests_per_connection"] = (
                                5  # Can be higher without idempotence
                            )
                            self._kafka_producer = KafkaProducer(**producer_config)
                        else:
                            # Re-raise if it's a different error
                            raise
                    # KafkaProducer will connect lazily on first send
                    # Log success - actual connection will be tested on first message send
                    logger.info(
                        "Kafka producer initialized successfully. Topic: %s, Bootstrap servers: %s. "
                        "Producer is ready and will connect automatically when sending messages.",
                        self._kafka_topic,
                        bootstrap_servers_list,
                    )
                    self._start_kafka_sender()
                except KafkaError as e:
                    logger.error(
                        "Kafka error initializing producer: %s. "
                        "Check that Kafka is running and KAFKA_BOOTSTRAP_SERVERS is correct. "
                        "Bootstrap servers: %s. "
                        "Troubleshooting: 1) Verify Kafka is running: docker ps | grep kafka "
                        "2) Check connectivity: telnet <host> <port> "
                        "3) Verify KAFKA_BOOTSTRAP_SERVERS format: host:port",
                        e,
                        kafka_bootstrap_servers,
                        exc_info=True,
                    )
                    self._kafka_producer = None
                except Exception as e:
                    logger.error(
                        "Failed to initialize Kafka producer: %s. Bootstrap servers: %s",
                        e,
                        kafka_bootstrap_servers,
                        exc_info=True,
                    )
                    self._kafka_producer = None

        # Initialize Redis client for error messages if ENABLE_REDIS_ERROR_MESSAGES is enabled
        if self._use_redis_error_bus:
            redis_host = os.environ.get("REDIS_HOST", "localhost")
            redis_port = int(os.environ.get("REDIS_PORT", "6379"))
            redis_db = int(os.environ.get("REDIS_DB", "0"))
            redis_password = os.environ.get("REDIS_PASSWORD", None)

            try:
                logger.info(
                    "Initializing Redis client for error messages. Channel: %s, Host: %s:%d",
                    self._redis_error_channel,
                    redis_host,
                    redis_port,
                )
                self._redis_client = redis.Redis(
                    host=redis_host,
                    port=redis_port,
                    db=redis_db,
                    password=redis_password,
                    decode_responses=False,  # We'll handle encoding ourselves
                    socket_connect_timeout=5,
                    socket_timeout=5,
                )
                # Test the connection
                self._redis_client.ping()
                logger.info(
                    "Redis client initialized successfully for error messages. "
                    "Channel: %s, Host: %s:%d",
                    self._redis_error_channel,
                    redis_host,
                    redis_port,
                )
                self._start_redis_sender()
            except Exception as e:
                logger.error(
                    "Failed to initialize Redis client: %s. Host: %s:%d. "
                    "Error messages will not be sent to Redis.",
                    e,
                    redis_host,
                    redis_port,
                    exc_info=True,
                )
                self._redis_client = None

        try:
            self._vlm_pipeline = VlmPipeline(args.asset_dir, args)
            self._loaded_models_info = self.get_models_info()
        except Exception as e:
            self._send_error_message_to_kafka(
                f"Failed to initialize Inference pipeline: {e}",
                "",
                "critical",
            )
            logger.error("Failed to initialize Inference pipeline: %s", e)
            raise

        logger.info("Initialized Stream Handler")

    def update_metrics(self):
        # System uptime is now automatically observed via OpenTelemetry callback
        pass

    def _get_live_stream_request(self, asset_id: str) -> RequestInfo | None:
        """Get active live stream request for an asset_id.

        Args:
            asset_id: The asset ID to look up

        Returns:
            RequestInfo if found, None otherwise
        """
        for req_info in self._request_info_map.values():
            if (
                req_info.is_live
                and req_info.status == RequestInfo.Status.PROCESSING
                and req_info.assets
                and req_info.assets[0].asset_id == asset_id
            ):
                return req_info
        return None

    def _count_active_live_streams(self) -> int:
        """Count currently active live streams.

        Returns:
            Number of active live streams
        """
        return sum(
            1
            for req_info in self._request_info_map.values()
            if req_info.is_live and req_info.status == RequestInfo.Status.PROCESSING
        )

    def _process_output(
        self,
        req_info: RequestInfo,
        is_live_stream_ended: bool,
        chunk_responses: list[PipelineChunkResult],
    ):

        if req_info.assets and len(req_info.assets) > 0:
            saved_dc_file = req_info.assets[0].path + ".dc.json"
            should_write_dc = (
                not req_info.is_live
                and not os.access(saved_dc_file, os.R_OK)
                and self._args.enable_dev_dc_gen
            )
            if should_write_dc:
                logger.info("Generating DC file at %s", saved_dc_file)
                # Serialize the object to a JSON file
                DenseCaptionSerializer.to_json(req_info.processed_chunk_list, saved_dc_file)

        chunk_responses = [chunk for chunk in chunk_responses if chunk.chunk]

        if chunk_responses and len(chunk_responses) > 0:
            if chunk_responses[0].chunk.chunk_type == "text":
                chunk_responses.sort(key=lambda item: item.chunk.chunkIdx)
            else:
                # Sort chunks based on their start times
                chunk_responses.sort(key=lambda item: ntp_to_unix_timestamp(item.chunk.start_ntp))

        if req_info.vlm_testdata_file_handle:
            for proc_chunk in chunk_responses:
                if proc_chunk.vlm_model_output:
                    idx = proc_chunk.chunk.chunkIdx
                    summ = proc_chunk.vlm_model_output.output.replace("\n", "  ")
                    req_info.vlm_testdata_file_handle.write(f'{idx},"{summ}"\n')

        req_info.response += chunk_responses

        if req_info.is_live:
            if is_live_stream_ended:
                req_info.end_time = time.time()
                self._metrics._active_live_streams_counter.add(-1)
                self.stop_request_profiling(req_info, chunk_responses)
                self._cleanup_request_files(req_info)
                # Unlock the asset and update metrics
                if req_info.assets:
                    for asset in req_info.assets:
                        asset.unlock()
                # End OTEL end-to-end pipeline span
                if req_info._e2e_span:
                    try:
                        req_info._e2e_span.set_attribute(
                            "e2e_latency_ms", (time.time() - req_info.start_time) * 1000
                        )
                        req_info._e2e_span.set_attribute("chunk_count", req_info.chunk_count)
                        req_info._e2e_span.set_attribute(
                            "total_chunks_processed", len(chunk_responses)
                        )
                        req_info._e2e_span.end()
                        logger.info("Ended e2e OTEL span")
                    except Exception as e:
                        logger.warning("Failed to end e2e OTEL span: %s", e)

                req_info.status_event.set()
                req_info.status = RequestInfo.Status.SUCCESSFUL
        else:
            request_files_cleaned_up = False
            if req_info.status == RequestInfo.Status.FAILED:
                logger.info("Processing failed for video file request %s", req_info.request_id)
                self.stop_request_profiling(req_info, chunk_responses)
                self._cleanup_request_files(req_info)
                request_files_cleaned_up = True
            else:
                req_info.end_time = time.time()
                self.stop_request_profiling(req_info, chunk_responses)
                cuda.bindings.runtime.cudaProfilerStop()
                if not req_info.text_query:
                    nvtx.end_range(req_info.nvtx_summarization_start)
                    logger.info(
                        "Processing completed for video file request %s,"
                        " total processing time - %.2f seconds",
                        req_info.request_id,
                        req_info.end_time - req_info.start_time,
                    )

            # End OTEL end-to-end pipeline span
            if req_info._e2e_span:
                try:
                    req_info._e2e_span.set_attribute(
                        "e2e_latency_ms", (time.time() - req_info.start_time) * 1000
                    )
                    req_info._e2e_span.set_attribute("chunk_count", req_info.chunk_count)
                    req_info._e2e_span.set_attribute("total_chunks_processed", len(chunk_responses))
                    req_info._e2e_span.end()
                    logger.info("Ended e2e OTEL span")
                except Exception as e:
                    logger.warning("Failed to end e2e OTEL span: %s", e)

            # Unlock the asset and update metrics
            if req_info.assets:
                for asset in req_info.assets:
                    asset.unlock()

            if not req_info.text_query:
                self._metrics._queries_processed_counter.add(1)
                self._metrics._queries_pending_counter.add(-1)

            if not request_files_cleaned_up:
                self._cleanup_request_files(req_info)
            if req_info.status != RequestInfo.Status.FAILED:
                req_info.status = RequestInfo.Status.SUCCESSFUL
            req_info.status_event.set()

    def _cleanup_request_files(self, req_info: RequestInfo):
        """Close file handles that were opened for the request"""
        # Close vlm_testdata_file if it was opened
        if req_info.vlm_testdata_file_handle:
            try:
                req_info.vlm_testdata_file_handle.close()
                logger.debug("Closed vlm_testdata_file for request %s", req_info.request_id)
            except Exception as e:
                logger.warning("Failed to close vlm_testdata_file: %s", e)
            finally:
                req_info.vlm_testdata_file_handle = None

    def _seconds_to_timestamp(self, seconds: Optional[float]) -> Optional[Timestamp]:
        """Convert floating point seconds to protobuf Timestamp."""
        if seconds is None:
            return None
        try:
            seconds_float = float(seconds)
        except (TypeError, ValueError):
            return None

        if math.isnan(seconds_float) or math.isinf(seconds_float):
            return None

        seconds_floor = math.floor(seconds_float)
        nanos = int(round((seconds_float - seconds_floor) * 1_000_000_000))
        if nanos >= 1_000_000_000:
            seconds_floor += 1
            nanos -= 1_000_000_000
        timestamp = Timestamp()
        timestamp.seconds = int(seconds_floor)
        timestamp.nanos = nanos
        return timestamp

    def _coerce_relative_seconds(self, value: object) -> Optional[float]:
        """Coerce timestamp-like values (possibly nanoseconds) to seconds."""
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None

        if math.isnan(numeric) or math.isinf(numeric):
            return None

        # Heuristic: values larger than this are likely nanoseconds
        if abs(numeric) > 1_000_000.0:
            return numeric / 1_000_000_000.0
        return numeric

    def _resolve_chunk_time_bounds(
        self, chunk: ChunkInfo
    ) -> tuple[Optional[float], Optional[float]]:
        """Resolve chunk start/end timestamps in seconds."""

        def _ntp_to_seconds(ntp_value: str) -> Optional[float]:
            if not ntp_value:
                return None
            try:
                return ntp_to_unix_timestamp(ntp_value)
            except Exception:
                return None

        start_seconds = _ntp_to_seconds(getattr(chunk, "start_ntp", ""))
        end_seconds = _ntp_to_seconds(getattr(chunk, "end_ntp", ""))

        start_ntp_float = getattr(chunk, "start_ntp_float", None)
        if start_seconds is None and start_ntp_float not in (None, 0.0):
            start_seconds = float(start_ntp_float)

        end_ntp_float = getattr(chunk, "end_ntp_float", None)
        if end_seconds is None and end_ntp_float not in (None, 0.0):
            end_seconds = float(end_ntp_float)

        start_pts = getattr(chunk, "start_pts", None)
        if start_seconds is None and start_pts is not None:
            start_seconds = start_pts / 1_000_000_000.0

        end_pts = getattr(chunk, "end_pts", None)
        if end_seconds is None and end_pts is not None and end_pts >= 0:
            end_seconds = end_pts / 1_000_000_000.0

        if (
            end_seconds is None
            and start_seconds is not None
            and end_pts is not None
            and end_pts >= 0
            and start_pts is not None
        ):
            duration = (end_pts - start_pts) / 1_000_000_000.0
            if duration >= 0:
                end_seconds = start_seconds + duration

        return start_seconds, end_seconds

    def _build_frame_messages(
        self,
        chunk_result: PipelineChunkResult,
        sensor_id: str,
        version: str,
        chunk_start_seconds: Optional[float | None],
    ) -> list[nv_pb2.Frame]:
        """Convert frame times into protobuf frames."""
        frames_proto: list[nv_pb2.Frame] = []
        chunk = chunk_result.chunk
        frame_times = chunk_result.frame_times or []

        def _to_absolute_seconds(relative_seconds: Optional[float]) -> Optional[float]:
            if relative_seconds is None:
                return None
            if chunk_start_seconds is not None:
                return chunk_start_seconds + relative_seconds
            return relative_seconds

        if not frames_proto and frame_times:
            for idx, frame_time in enumerate(frame_times):
                relative_seconds = self._coerce_relative_seconds(frame_time)
                absolute_seconds = _to_absolute_seconds(relative_seconds)

                frame_msg = nv_pb2.Frame()
                if version:
                    frame_msg.version = version
                frame_msg.id = f"{chunk.chunkIdx}:{idx}"
                timestamp = self._seconds_to_timestamp(absolute_seconds)
                if timestamp:
                    frame_msg.timestamp.CopyFrom(timestamp)
                frame_msg.sensorId = sensor_id
                frame_msg.info["frameNo"] = str(idx)
                frame_msg.info["chunkIdx"] = str(chunk.chunkIdx)
                frames_proto.append(frame_msg)

        return frames_proto

    def _chunk_result_to_vision_llm_text(
        self, chunk_result: PipelineChunkResult, req_info: RequestInfo
    ) -> Optional[nv_pb2.VisionLLM]:
        """Populate VisionLLM message from a processed chunk result."""
        chunk = chunk_result.chunk
        if not chunk:
            return None
        vision_llm = nv_pb2.VisionLLM()
        model_version = ""
        if getattr(self._loaded_models_info, "id", None):
            model_version = str(self._loaded_models_info.id)
        elif req_info.query and getattr(req_info.query, "model", None):
            vision_llm.version = model_version
        vision_llm.info["requestId"] = req_info.request_id
        vision_llm.info["chunkIdx"] = str(chunk.chunkIdx)

        llm_msg = nv_pb2.LLM()
        llm_msg.info["modelId"] = str(self._loaded_models_info.id)
        llm_msg.info["modelApiType"] = str(self._loaded_models_info.api_type)
        llm_msg.info["modelOwnedBy"] = str(self._loaded_models_info.owned_by)
        query_msg = llm_msg.queries.add()
        query_msg.id = f"{req_info.request_id}:{chunk.chunkIdx}"
        query_msg.params["chunkIdx"] = str(chunk.chunkIdx)
        query_msg.params["requestId"] = req_info.request_id
        if req_info.text_query:
            query_msg.prompts["text"] = req_info.text_query.text_input_list[chunk.chunkIdx]
        if chunk_result.vlm_model_output and chunk_result.vlm_model_output.embeddings:
            vision_embeddings = nv_pb2.Embedding()
            vision_embeddings.vector.extend(chunk_result.vlm_model_output.embeddings)

            llm_msg.visionEmbeddings.append(vision_embeddings)

        vision_llm.llm.CopyFrom(llm_msg)

        return vision_llm

    def _build_incident_message(
        self,
        *,
        chunk_result: PipelineChunkResult,
        req_info: RequestInfo,
        chunk: ChunkInfo,
        sensor_id: str,
        sensor_msg: nv_pb2.Sensor,
        llm_msg: nv_pb2.LLM,
        start_timestamp: Optional[Timestamp],
        end_timestamp: Optional[Timestamp],
        frames_proto: list[nv_pb2.Frame],
        model_version: str,
    ) -> ext_pb2.Incident:
        """Assemble an Incident proto populated with available metadata."""
        incident = ext_pb2.Incident()
        incident.sensorId = sensor_id or req_info.stream_id

        if start_timestamp:
            incident.timestamp.CopyFrom(start_timestamp)
        if end_timestamp:
            incident.end.CopyFrom(end_timestamp)

        if frames_proto:
            frame_ids = [frames_proto[0].id]
            if frames_proto[-1].id != frames_proto[0].id:
                frame_ids.append(frames_proto[-1].id)
            incident.frameIds.extend(frame_ids)
        else:
            incident.frameIds.extend(
                [
                    f"{chunk.chunkIdx}:start",
                    f"{chunk.chunkIdx}:end",
                ]
            )

        # Populate object identifiers from query context when available.
        object_ids: list[str] = []
        if req_info.query:
            try:
                object_ids = [str(obj_id) for obj_id in req_info.query.id_list]
            except Exception:
                if getattr(req_info.query, "id", None):
                    object_ids = [str(req_info.query.id)]
        if not object_ids and req_info.stream_id:
            object_ids = [req_info.stream_id]
        if object_ids:
            incident.objectIds.extend(object_ids)

        # Map stream/asset information into the Place message.
        # Use stream-specific place info if available, otherwise fall back to global config
        place_msg = nv_pb2.Place()
        place_msg.id = sensor_id or req_info.stream_id

        # Get place info from asset if available, otherwise use global config
        if req_info.assets and req_info.assets[0]:
            asset = req_info.assets[0]
            place_msg.name = asset.place_name or self._place_config["name"]
            place_msg.type = asset.place_type or self._place_config["type"]
            place_msg.location.lat = (
                asset.place_lat if asset.place_lat is not None else self._place_config["lat"]
            )
            place_msg.location.lon = (
                asset.place_lon if asset.place_lon is not None else self._place_config["lon"]
            )
            place_msg.location.alt = (
                asset.place_alt if asset.place_alt is not None else self._place_config["alt"]
            )
            place_msg.coordinate.x = (
                asset.place_coordinate_x
                if asset.place_coordinate_x is not None
                else self._place_config["coordinate_x"]
            )
            place_msg.coordinate.y = (
                asset.place_coordinate_y
                if asset.place_coordinate_y is not None
                else self._place_config["coordinate_y"]
            )
        else:
            # Fall back to global config
            place_msg.name = self._place_config["name"]
            place_msg.type = self._place_config["type"]
            place_msg.location.lat = self._place_config["lat"]
            place_msg.location.lon = self._place_config["lon"]
            place_msg.location.alt = self._place_config["alt"]
            place_msg.coordinate.x = self._place_config["coordinate_x"]
            place_msg.coordinate.y = self._place_config["coordinate_y"]
        incident.place.CopyFrom(place_msg)

        # Use analytics module configuration from environment variables
        analytics_module = nv_pb2.AnalyticsModule()
        analytics_module.id = self._analytics_module_config["id"]
        analytics_module.description = self._analytics_module_config["description"]
        analytics_module.source = self._analytics_module_config["source"]
        if model_version:
            analytics_module.version = model_version
        analytics_module.info["requestId"] = req_info.request_id
        analytics_module.info["chunkIdx"] = str(chunk.chunkIdx)
        analytics_module.info["streamType"] = "live" if req_info.is_live else "file"
        incident.analyticsModule.CopyFrom(analytics_module)
        # Use alert_category for incident.category (shown in UI "Alert Type" column)
        alert_category = None
        if req_info.query:
            raw = getattr(req_info.query, "alert_category", None)
            alert_category = str(raw).strip() if raw else None
        incident.category = alert_category or "vlm-alert"
        if alert_category:
            incident.info["alertCategory"] = alert_category

        incident.llm.CopyFrom(llm_msg)

        stream_id = req_info.stream_id or chunk.streamId or ""
        incident.info["requestId"] = req_info.request_id
        incident.info["chunkIdx"] = str(chunk.chunkIdx)
        incident.info["streamId"] = stream_id
        if sensor_id:
            incident.info["sensorId"] = sensor_id
        if req_info.query:
            if req_info.query.prompt:
                incident.info["prompt"] = req_info.query.prompt
            if req_info.query.system_prompt:
                incident.info["systemPrompt"] = req_info.query.system_prompt
            if req_info.query.model:
                incident.info["requestedModel"] = req_info.query.model

        return incident

    def _chunk_result_to_vision_llm(
        self, chunk_result: PipelineChunkResult, req_info: RequestInfo
    ) -> tuple[Optional[nv_pb2.VisionLLM], Optional[ext_pb2.Incident]]:
        """Populate VisionLLM message from a processed chunk result."""
        chunk = chunk_result.chunk
        if not chunk:
            logger.debug("No chunk info available for request %s", req_info.request_id)
            return None, None

        vision_llm = nv_pb2.VisionLLM()
        incident: Optional[ext_pb2.Incident] = None

        model_version = ""
        if getattr(self._loaded_models_info, "id", None):
            model_version = str(self._loaded_models_info.id)
        elif req_info.query and getattr(req_info.query, "model", None):
            model_version = req_info.query.model
        if model_version:
            vision_llm.version = model_version
        else:
            # string version = 1;
            vision_llm.version = "1.0"

        creation_time_str = (
            req_info.assets[0].creation_time
            if req_info.assets and len(req_info.assets) > 0
            else None
        )
        creation_time = ntp_to_unix_timestamp(creation_time_str) if creation_time_str else None
        start_seconds, end_seconds = self._resolve_chunk_time_bounds(chunk)

        if creation_time is not None:
            if start_seconds is not None:
                start_seconds = creation_time + start_seconds
            if end_seconds is not None:
                end_seconds = creation_time + end_seconds

        start_timestamp = self._seconds_to_timestamp(start_seconds)
        if start_timestamp:
            #     google.protobuf.Timestamp timestamp = 2;
            vision_llm.timestamp.CopyFrom(start_timestamp)

        end_timestamp = self._seconds_to_timestamp(end_seconds)
        if end_timestamp:
            #     google.protobuf.Timestamp end = 3;
            vision_llm.end.CopyFrom(end_timestamp)

        asset = req_info.assets[0] if req_info.assets and len(req_info.assets) > 0 else None
        stream_id = req_info.stream_id or chunk.streamId or ""
        sensor_name = asset.sensor_name.strip() if asset and asset.sensor_name else ""
        camera_id = asset.camera_id.strip() if asset and asset.camera_id else ""
        # nv-schema sensor fields identify the source sensor/camera. RTVI stream
        # correlation fields below use asset_id/stream_id instead.
        sensor_id = camera_id or sensor_name or stream_id

        if req_info.is_live:
            start_pts = getattr(chunk, "start_pts", None)
            if start_seconds is not None and start_pts is not None:
                chunk_start_seconds = start_seconds - start_pts / 1_000_000_000.0
            else:
                chunk_start_seconds = None
        else:
            chunk_start_seconds = creation_time

        frames_proto = self._build_frame_messages(
            chunk_result, sensor_id, model_version, chunk_start_seconds
        )
        if frames_proto:
            vision_llm.frames.extend(frames_proto)
            # string startFrameId = 4;
            vision_llm.startFrameId = frames_proto[0].id
            # string endFrameId = 5;
            vision_llm.endFrameId = frames_proto[-1].id
            if not vision_llm.HasField("timestamp") and frames_proto[0].HasField("timestamp"):
                #     google.protobuf.Timestamp timestamp = 2;
                vision_llm.timestamp.CopyFrom(frames_proto[0].timestamp)
            if not vision_llm.HasField("end") and frames_proto[-1].HasField("timestamp"):
                #     google.protobuf.Timestamp end = 3;
                vision_llm.end.CopyFrom(frames_proto[-1].timestamp)
        else:
            # string startFrameId = 4;
            vision_llm.startFrameId = f"{chunk.chunkIdx}:start"
            # string endFrameId = 5;
            vision_llm.endFrameId = f"{chunk.chunkIdx}:end"

        sensor_msg = nv_pb2.Sensor()
        if asset:
            # string id = 1;
            sensor_msg.id = sensor_id
            # string type = 2;
            sensor_msg.type = "Camera" if asset.is_live else "Video"
            # string description = 3;
            if asset.description:
                sensor_msg.description = asset.description
            # elif asset.path:
            #    sensor_msg.description = asset.path
            if asset.path:
                sensor_msg.info["path"] = asset.path
            if asset.asset_dir:
                sensor_msg.info["assetDir"] = asset.asset_dir
            if asset.url:
                sensor_msg.info["url"] = asset.url
            if camera_id:
                sensor_msg.info["cameraId"] = camera_id
            if sensor_name and sensor_name != sensor_id:
                sensor_msg.info["sensorName"] = sensor_name
        else:
            # string id = 1;
            sensor_msg.id = sensor_id
        # map <string, string> info = 6;
        if chunk.asset_dir:
            sensor_msg.info["chunkAssetDir"] = chunk.asset_dir
        if req_info.video_fps:
            sensor_msg.info["videoFps"] = f"{req_info.video_fps:.2f}"
        if sensor_msg.id or sensor_msg.type or sensor_msg.description or sensor_msg.info:
            vision_llm.sensor.CopyFrom(sensor_msg)

        llm_msg = nv_pb2.LLM()
        # map <string, string> info = 1;
        if getattr(self._loaded_models_info, "id", None):
            llm_msg.info["modelId"] = str(self._loaded_models_info.id)
        if getattr(self._loaded_models_info, "api_type", None):
            llm_msg.info["modelApiType"] = str(self._loaded_models_info.api_type)
        if getattr(self._loaded_models_info, "owned_by", None):
            llm_msg.info["modelOwnedBy"] = str(self._loaded_models_info.owned_by)
        if req_info.query:
            if req_info.query.model:
                llm_msg.info["requestedModel"] = req_info.query.model
            llm_msg.info["enableReasoning"] = str(req_info.query.enable_reasoning)
        # repeated Query queries = 2;
        query_msg = llm_msg.queries.add()
        # string id = 1;
        query_msg.id = f"{req_info.request_id}:{chunk.chunkIdx}"
        # map <string, string> params = 2;
        query_msg.params["chunkIdx"] = str(chunk.chunkIdx)
        query_msg.params["streamId"] = stream_id
        if camera_id:
            query_msg.params["cameraId"] = camera_id
        if sensor_id:
            query_msg.params["sensorId"] = sensor_id
        if sensor_name and sensor_name != sensor_id:
            query_msg.params["sensorName"] = sensor_name
        query_msg.params["requestId"] = req_info.request_id

        if creation_time is not None and start_seconds is not None and end_seconds is not None:
            start_ntp_val = get_timestamp_str(start_seconds)
            end_ntp_val = get_timestamp_str(end_seconds)
        else:
            start_ntp_val = getattr(chunk, "start_ntp", "") or ""
            end_ntp_val = getattr(chunk, "end_ntp", "") or ""
        if start_ntp_val:
            query_msg.params["startNtp"] = start_ntp_val
        if end_ntp_val:
            query_msg.params["endNtp"] = end_ntp_val

        if req_info.query:
            # map <string, string> prompts = 3;
            if req_info.query.prompt:
                query_msg.prompts["user"] = req_info.query.prompt
            if req_info.query.system_prompt:
                query_msg.prompts["system"] = req_info.query.system_prompt
        if chunk_result.vlm_model_output and chunk_result.vlm_model_output.output:
            # string response = 4;
            query_msg.response = chunk_result.vlm_model_output.output
            lower_response = chunk_result.vlm_model_output.output.lower()
            trigger_tokens = [token for token in ("yes", "true") if token in lower_response]
            triggered = bool(trigger_tokens)
            if triggered:
                incident = self._build_incident_message(
                    chunk_result=chunk_result,
                    req_info=req_info,
                    chunk=chunk,
                    sensor_id=sensor_id,
                    sensor_msg=sensor_msg,
                    llm_msg=llm_msg,
                    start_timestamp=start_timestamp,
                    end_timestamp=end_timestamp,
                    frames_proto=frames_proto,
                    model_version=model_version,
                )
                incident.isAnomaly = True
                incident.info["triggerPhrase"] = ",".join(trigger_tokens)
                vision_llm.info["incidentDetected"] = "true"
                incident.info["verdict"] = "confirmed"
            else:
                vision_llm.info["incidentDetected"] = "false"

            # map <string, string> info = 1;
            vision_llm.info["inputTokens"] = str(chunk_result.vlm_model_output.input_tokens)
            vision_llm.info["outputTokens"] = str(chunk_result.vlm_model_output.output_tokens)
            if chunk_result.vlm_model_output.reasoning_description:
                # map <string, string> info = 1;
                vision_llm.info["reasoningDescription"] = (
                    chunk_result.vlm_model_output.reasoning_description
                )
        elif chunk_result.error:
            query_msg.response = ""
            # map <string, string> info = 1;
            vision_llm.info["error"] = chunk_result.error
        else:
            query_msg.response = ""

        if chunk_result.vlm_model_output and chunk_result.vlm_model_output.embeddings:
            vision_embeddings = nv_pb2.Embedding()
            vision_embeddings.vector.extend(chunk_result.vlm_model_output.embeddings)

            llm_msg.visionEmbeddings.append(vision_embeddings)

        vision_llm.llm.CopyFrom(llm_msg)

        if chunk_result.audio_transcript:
            vision_llm.info["audioTranscript"] = chunk_result.audio_transcript
        if chunk_result.decode_end_time and chunk_result.decode_start_time:
            decode_latency = chunk_result.decode_end_time - chunk_result.decode_start_time
            vision_llm.info["decodeLatencyMs"] = f"{decode_latency * 1000:.3f}"
        if chunk_result.vlm_end_time and chunk_result.vlm_start_time:
            vlm_latency = chunk_result.vlm_end_time - chunk_result.vlm_start_time
            vision_llm.info["vlmLatencyMs"] = f"{vlm_latency * 1000:.3f}"
        if chunk_result.vlm_end_time and chunk_result.decode_start_time:
            chunk_latency = chunk_result.vlm_end_time - chunk_result.decode_start_time
            vision_llm.info["chunkLatencyMs"] = f"{chunk_latency * 1000:.3f}"
        if chunk_result.asr_end_time and chunk_result.asr_start_time:
            asr_latency = chunk_result.asr_end_time - chunk_result.asr_start_time
            vision_llm.info["asrLatencyMs"] = f"{asr_latency * 1000:.3f}"
        if chunk_result.queue_time:
            vision_llm.info["queueTimeS"] = f"{chunk_result.queue_time:.3f}"
        if chunk_result.processing_latency:
            vision_llm.info["processingLatencyS"] = f"{chunk_result.processing_latency:.3f}"

        vision_llm.info["chunkIdx"] = str(chunk.chunkIdx)
        vision_llm.info["streamId"] = stream_id
        if camera_id:
            vision_llm.info["cameraId"] = camera_id
        if sensor_id:
            vision_llm.info["sensorId"] = sensor_id
        vision_llm.info["requestId"] = req_info.request_id
        vision_llm.info["frameCount"] = str(len(frames_proto)) if frames_proto else "0"

        return vision_llm, incident

    def _start_kafka_sender(self) -> bool:
        """Start a bounded background sender for Kafka producer.send calls."""
        with self._lock:
            if self._stopping:
                return False

            if self._kafka_producer is None:
                return False

            if self._kafka_send_thread is not None and self._kafka_send_thread.is_alive():
                return True

            self._kafka_send_stop_event.clear()
            self._kafka_send_queue = queue.Queue(maxsize=self._kafka_send_queue_maxsize)
            self._kafka_send_thread = Thread(
                target=self._kafka_sender_loop,
                name="rtvi-kafka-sender",
                daemon=True,
            )
            self._kafka_send_thread.start()
            logger.info(
                "Started Kafka async sender thread with queue maxsize %d",
                self._kafka_send_queue_maxsize,
            )
            return True

    def _kafka_sender_loop(self) -> None:
        """Run queued Kafka send callables outside request/pipeline callbacks."""
        while True:
            kafka_send_queue = self._kafka_send_queue
            if kafka_send_queue is None:
                return

            if self._kafka_send_stop_event.is_set() and kafka_send_queue.empty():
                return

            try:
                description, send_callable = kafka_send_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                send_callable()
            except Exception as exc:
                logger.error(
                    "Unexpected error in Kafka async sender for %s: %s",
                    description,
                    exc,
                    exc_info=True,
                )
            finally:
                kafka_send_queue.task_done()

    def _submit_kafka_send(self, description: str, send_callable: Callable[[], None]) -> bool:
        """Queue a Kafka send without blocking the caller on broker metadata timeouts."""
        with self._lock:
            if self._stopping:
                logger.debug(
                    "Stream handler is stopping; dropping Kafka message for %s", description
                )
                return False

            if self._kafka_producer is None:
                logger.error("Kafka producer not available")
                return False

            if self._kafka_send_thread is None or not self._kafka_send_thread.is_alive():
                self._start_kafka_sender()

            if self._kafka_send_queue is None:
                logger.error(
                    "Kafka async sender not available; dropping Kafka message for %s", description
                )
                return False

            try:
                self._kafka_send_queue.put_nowait((description, send_callable))
                return True
            except queue.Full:
                logger.error(
                    "Kafka async send queue is full (maxsize=%d); dropping Kafka message for %s",
                    self._kafka_send_queue_maxsize,
                    description,
                )
                return False

    def _send_protobuf_to_kafka(
        self,
        serialized_proto: bytes,
        chunk_result: PipelineChunkResult,
        req_info: RequestInfo,
        message_type: str = "vision_llm",
        kafka_topic: Optional[str] = None,
    ) -> None:
        """Send serialized protobuf message to Kafka topic.

        Args:
            serialized_proto: Serialized protobuf message bytes
            chunk_result: PipelineChunkResult containing chunk information
            req_info: RequestInfo containing request metadata
            message_type: Logical type of the protobuf payload (vision_llm, incident, etc.)
            kafka_topic: Optional override topic; defaults to configured topic
        """
        with self._lock:
            kafka_producer = self._kafka_producer
            if self._stopping:
                logger.debug(
                    "Stream handler is stopping; dropping Kafka message for request %s, chunk %s",
                    req_info.request_id,
                    chunk_result.chunk.chunkIdx,
                )
                return

            if kafka_producer is None:
                logger.error("Kafka producer not available")
                return

        topic = kafka_topic or self._kafka_topic
        request_id = req_info.request_id
        chunk_idx = chunk_result.chunk.chunkIdx
        key = f"{request_id}:{chunk_idx}".encode("utf-8")
        headers = [("message_type", message_type.encode("utf-8"))]
        bootstrap_servers = kafka_producer.config.get("bootstrap_servers", ["unknown"])
        if isinstance(bootstrap_servers, str):
            bootstrap_servers_str = bootstrap_servers
        else:
            bootstrap_servers_str = ", ".join(bootstrap_servers)

        def send_to_kafka() -> None:
            try:
                future = kafka_producer.send(
                    topic,
                    key=key,
                    value=serialized_proto,
                    headers=headers,
                )

                def on_send_success(record_metadata):
                    logger.debug(
                        "Kafka message sent successfully. Topic: %s, Partition: %s, Offset: %s, "
                        "Request: %s, Chunk: %s",
                        record_metadata.topic,
                        record_metadata.partition,
                        record_metadata.offset,
                        request_id,
                        chunk_idx,
                    )

                def on_send_error(excp):
                    error_msg = str(excp)
                    if "KafkaTimeoutError" in error_msg or "Failed to update metadata" in error_msg:
                        logger.error(
                            "Kafka timeout error sending message for request %s, chunk %s: %s. "
                            "This usually means Kafka brokers are not reachable. "
                            "Verify KAFKA_BOOTSTRAP_SERVERS=%s is correct and Kafka is running. "
                            "TROUBLESHOOTING: "
                            "1) If RTVI is running in Docker, use container name 'kafka:9092' (not 'localhost:9092') "  # noqa: E501
                            "2) Verify Kafka container is on same network: "
                            "docker network inspect kafka_network "
                            "3) Test connectivity: docker exec ${RTVI_CONTAINER} python3 -c \"import socket; s=socket.socket(); s.connect(('kafka', 9092)); print('OK')\" "  # noqa: E501
                            "4) Check Kafka advertised listeners: docker exec kafka env | grep KAFKA_ADVERTISED_LISTENERS "  # noqa: E501
                            "   (should include 'kafka:9092' for Docker network access)",
                            request_id,
                            chunk_idx,
                            excp,
                            bootstrap_servers_str,
                            exc_info=True,
                        )
                    else:
                        logger.error(
                            "Failed to send Kafka message for request %s, chunk %s: %s",
                            request_id,
                            chunk_idx,
                            excp,
                            exc_info=True,
                        )

                future.add_callback(on_send_success)
                future.add_errback(on_send_error)
            except KafkaError as e:
                logger.error(
                    "Kafka error sending message for request %s, chunk %s: %s",
                    request_id,
                    chunk_idx,
                    e,
                    exc_info=True,
                )
            except Exception as e:
                logger.error(
                    "Unexpected error sending Kafka message for request %s, chunk %s: %s",
                    request_id,
                    chunk_idx,
                    e,
                    exc_info=True,
                )

        self._submit_kafka_send(
            f"request {request_id}, chunk {chunk_idx}, type {message_type}",
            send_to_kafka,
        )

    def _send_error_message_to_kafka(
        self, error_message: str, uuid_or_stream_id: str = "", type: str = "functional"
    ):
        """Send error message to Kafka topic or Redis channel based on ENABLE_REDIS_ERROR_MESSAGES."""

        # Switch between Redis and Kafka based on environment variable
        if self._use_redis_error_bus:
            self._send_error_message_to_redis(error_message, uuid_or_stream_id, type)
            return

        if not self._kafka_enabled:
            return

        with self._lock:
            kafka_producer = self._kafka_producer
            if self._stopping:
                logger.debug(
                    "Stream handler is stopping; dropping Kafka error message for stream %s",
                    uuid_or_stream_id,
                )
                return

            if kafka_producer is None:
                logger.error("Kafka producer not available")
                return

        stream_id = str(uuid_or_stream_id) if uuid_or_stream_id else ""

        kafka_topic = self._kafka_error_topic

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z"
        kafka_error_message = {
            "streamId": stream_id,
            "timestamp": timestamp,
            "type": type,
            "source": self._podname,
            "event": error_message,
        }
        serialized_error_message = json.dumps(kafka_error_message).encode("utf-8")
        headers = [("message_type", "error".encode("utf-8"))]

        def send_to_kafka() -> None:
            try:
                future = kafka_producer.send(
                    kafka_topic,
                    value=serialized_error_message,
                    headers=headers,
                )

                def on_send_success(_):
                    logger.info(
                        "Kafka error message sent successfully for stream %s",
                        stream_id,
                    )

                def on_send_error(_):
                    logger.error(
                        "Kafka error sending error message for stream %s",
                        stream_id,
                        exc_info=True,
                    )

                future.add_callback(on_send_success)
                future.add_errback(on_send_error)
            except Exception as e:
                logger.error(
                    "Error sending Kafka error message for stream %s: %s",
                    stream_id,
                    e,
                    exc_info=True,
                )

        self._submit_kafka_send(f"error message for stream {stream_id}", send_to_kafka)

    def _start_redis_sender(self) -> bool:
        """Start a bounded background sender for Redis publish calls."""
        with self._lock:
            if self._stopping:
                return False

            if self._redis_client is None:
                return False

            if self._redis_send_thread is not None and self._redis_send_thread.is_alive():
                return True

            self._redis_send_stop_event.clear()
            self._redis_send_queue = queue.Queue(maxsize=self._redis_send_queue_maxsize)
            self._redis_send_thread = Thread(
                target=self._redis_sender_loop,
                name="rtvi-redis-sender",
                daemon=True,
            )
            self._redis_send_thread.start()
            logger.info(
                "Started Redis async sender thread with queue maxsize %d",
                self._redis_send_queue_maxsize,
            )
            return True

    def _redis_sender_loop(self) -> None:
        """Run queued Redis publish callables outside request/pipeline callbacks."""
        while True:
            redis_send_queue = self._redis_send_queue
            if redis_send_queue is None:
                return

            if self._redis_send_stop_event.is_set() and redis_send_queue.empty():
                return

            try:
                description, publish_callable = redis_send_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                publish_callable()
            except Exception as exc:
                logger.error(
                    "Unexpected error in Redis async sender for %s: %s",
                    description,
                    exc,
                    exc_info=True,
                )
            finally:
                redis_send_queue.task_done()

    def _submit_redis_publish(self, description: str, publish_callable: Callable[[], None]) -> bool:
        """Queue a Redis publish without blocking the caller on Redis socket timeouts."""
        with self._lock:
            if self._stopping:
                logger.debug(
                    "Stream handler is stopping; dropping Redis message for %s", description
                )
                return False

            if self._redis_client is None:
                logger.error("Redis client not available")
                return False

            if self._redis_send_thread is None or not self._redis_send_thread.is_alive():
                self._start_redis_sender()

            if self._redis_send_queue is None:
                logger.error(
                    "Redis async sender not available; dropping Redis message for %s", description
                )
                return False

            try:
                self._redis_send_queue.put_nowait((description, publish_callable))
                return True
            except queue.Full:
                logger.error(
                    "Redis async publish queue is full (maxsize=%d); dropping Redis message for %s",
                    self._redis_send_queue_maxsize,
                    description,
                )
                return False

    def _send_error_message_to_redis(
        self, error_message: str, uuid_or_stream_id: str = "", type: str = "functional"
    ):
        """Send error message to Redis channel."""

        with self._lock:
            redis_client = self._redis_client
            if self._stopping:
                logger.debug(
                    "Stream handler is stopping; dropping Redis error message for stream %s",
                    uuid_or_stream_id,
                )
                return

            if redis_client is None:
                logger.error("Redis client not available")
                return

        stream_id = str(uuid_or_stream_id) if uuid_or_stream_id else ""
        redis_channel = self._redis_error_channel

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z"
        redis_error_message = {
            "streamId": stream_id,
            "timestamp": timestamp,
            "type": type,
            "source": self._podname,
            "event": error_message,
        }
        serialized_error_message = json.dumps(redis_error_message).encode("utf-8")

        def publish_to_redis() -> None:
            try:
                redis_client.publish(redis_channel, serialized_error_message)

                logger.info(
                    "Redis error message sent successfully for stream %s on channel %s",
                    stream_id,
                    redis_channel,
                )
            except Exception as e:
                logger.error(
                    "Error sending Redis error message for stream %s: %s",
                    stream_id,
                    e,
                    exc_info=True,
                )

        self._submit_redis_publish(f"error message for stream {stream_id}", publish_to_redis)

    def _on_vlm_chunk_response(self, chunk_result: PipelineChunkResult, req_info: RequestInfo):
        """Gather chunks processed by the pipeline and run any further post-processing"""
        try:
            if self._kafka_enabled:
                vision_llm_message, incident_message = self._chunk_result_to_vision_llm(
                    chunk_result, req_info
                )
            else:
                vision_llm_message = None
                incident_message = None
        except Exception as exc:
            vision_llm_message = None
            incident_message = None
            error_message = "Failed to build VisionLLM protobuf for chunk %s: %s" % (
                getattr(chunk_result.chunk, "chunkIdx", "unknown"),
                exc,
            )
            self._send_error_message_to_kafka(error_message, req_info.stream_id)
            logger.error(error_message, exc_info=True)

        if vision_llm_message:
            chunk_result.vision_llm_proto = vision_llm_message
            try:
                chunk_result.vision_llm_proto_serialized = vision_llm_message.SerializeToString()
                # Send to Kafka if producer is available
                if chunk_result.vision_llm_proto_serialized:
                    self._send_protobuf_to_kafka(
                        chunk_result.vision_llm_proto_serialized,
                        chunk_result,
                        req_info,
                    )
            except Exception as exc:
                error_message = "Failed to serialize VisionLLM protobuf for chunk %s: %s" % (
                    getattr(chunk_result.chunk, "chunkIdx", "unknown"),
                    exc,
                )
                self._send_error_message_to_kafka(error_message, req_info.stream_id)
                logger.error(error_message)

        if incident_message:
            chunk_result.incident_proto = incident_message
            try:
                chunk_result.incident_proto_serialized = incident_message.SerializeToString()
                if chunk_result.incident_proto_serialized:
                    self._send_protobuf_to_kafka(
                        chunk_result.incident_proto_serialized,
                        chunk_result,
                        req_info,
                        message_type="incident",
                        kafka_topic=self._kafka_incident_topic or self._kafka_topic,
                    )
            except Exception as exc:
                error_message = "Failed to serialize Incident protobuf for chunk %s: %s" % (
                    getattr(chunk_result.chunk, "chunkIdx", "unknown"),
                    exc,
                )
                self._send_error_message_to_kafka(error_message, req_info.stream_id)
                logger.error(error_message)

        if chunk_result.decode_retry_count:
            self._metrics._decode_retry_counter.add(
                chunk_result.decode_retry_count,
                {"reason": "first_attempt_error"},
            )

        # Per-chunk decode latency and OTEL tracing
        if chunk_result.decode_end_time > chunk_result.decode_start_time:
            decode_latency = chunk_result.decode_end_time - chunk_result.decode_start_time
            self._metrics._decode_latency.record(decode_latency)
            self._metrics._decode_latency_latest_value = decode_latency

            # Create OTEL span for decode operation with historical timing
            create_historical_span(
                f"Decode - Chunk {chunk_result.chunk.chunkIdx}",
                chunk_result.decode_start_time,
                chunk_result.decode_end_time,
                {
                    "chunk_idx": chunk_result.chunk.chunkIdx,
                    "decode_latency_ms": decode_latency * 1000,
                    "stream_id": chunk_result.chunk.streamId,
                    "operation": "decode",
                },
                parent_span=req_info.vlm_pipeline_span,
            )

        # Per-chunk VLM latency and OTEL tracing
        if chunk_result.vlm_end_time > chunk_result.vlm_start_time:
            vlm_latency = chunk_result.vlm_end_time - chunk_result.vlm_start_time
            self._metrics._vlm_latency.record(vlm_latency)
            self._metrics._vlm_latency_latest_value = vlm_latency

            # Create OTEL span for VLM operation with historical timing
            create_historical_span(
                f"VLM Inference - Chunk {chunk_result.chunk.chunkIdx}",
                chunk_result.vlm_start_time,
                chunk_result.vlm_end_time,
                {
                    "chunk_idx": chunk_result.chunk.chunkIdx,
                    "vlm_latency_ms": vlm_latency * 1000,
                    "stream_id": chunk_result.chunk.streamId,
                    "vlm_response_length": (
                        len(chunk_result.vlm_model_output.output)
                        if chunk_result.vlm_model_output
                        else 0
                    ),
                    "operation": "vlm_inference",
                    "model_id": str(self._loaded_models_info.id),
                    "model_api_type": self._loaded_models_info.api_type,
                },
                parent_span=req_info.vlm_pipeline_span,
            )

        # Per-chunk VLM latency and OTEL tracing
        if chunk_result.vlm_end_time > chunk_result.decode_start_time:
            chunk_latency = chunk_result.vlm_end_time - chunk_result.decode_start_time
            self._metrics._chunk_latency.record(chunk_latency)
            self._metrics._chunk_latency_latest_value = chunk_latency

            # Create OTEL span for VLM operation with historical timing
            create_historical_span(
                f"VLM Chunk Inference - Chunk {chunk_result.chunk.chunkIdx}",
                chunk_result.decode_start_time,
                chunk_result.vlm_end_time,
                {
                    "chunk_idx": chunk_result.chunk.chunkIdx,
                    "chunk_latency_ms": chunk_latency * 1000,
                    "stream_id": chunk_result.chunk.streamId,
                    "operation": "chunk",
                },
                parent_span=req_info.vlm_pipeline_span,
            )

        if req_info.vlm_pipeline_span and chunk_result.chunk:
            req_info.vlm_pipeline_span.add_event(
                f"chunk {chunk_result.chunk.chunkIdx} processed",
                {
                    "chunk_idx": chunk_result.chunk.chunkIdx,
                },
            )

        # Log and observe token usage per chunk if available
        if chunk_result.vlm_model_output:
            self._metrics._vlm_input_tokens.record(chunk_result.vlm_model_output.input_tokens)
            self._metrics._vlm_output_tokens.record(chunk_result.vlm_model_output.output_tokens)

        # Per-chunk ASR latency
        if chunk_result.asr_end_time > chunk_result.asr_start_time:
            asr_latency = chunk_result.asr_end_time - chunk_result.asr_start_time
            self._metrics._asr_pipeline_latency.record(asr_latency)
            self._metrics._asr_pipeline_latency_latest_value = asr_latency
            # Create OTEL span for ASR operation with historical timing
            create_historical_span(
                f"ASR Inference - Chunk {chunk_result.chunk.chunkIdx}",
                chunk_result.asr_start_time,
                chunk_result.asr_end_time,
                {
                    "chunk_idx": chunk_result.chunk.chunkIdx,
                    "asr_latency_ms": asr_latency * 1000,
                    "stream_id": chunk_result.chunk.streamId,
                    "operation": "asr",
                },
                parent_span=req_info.vlm_pipeline_span,
            )

        if req_info._request_metrics and chunk_result.chunk:
            try:
                all_times = getattr(req_info._request_metrics, "all_times", None)
                if all_times is not None:
                    all_times.append(
                        {
                            "chunk_id": chunk_result.chunk.chunkIdx,
                            "decode_start_time": chunk_result.decode_start_time,
                            "decode_end_time": chunk_result.decode_end_time,
                            "vlm_start_time": chunk_result.vlm_start_time,
                            "vlm_end_time": chunk_result.vlm_end_time,
                            "chunk_start_time": chunk_result.decode_start_time,
                            "chunk_end_time": chunk_result.vlm_end_time,
                            "vlm_input_tokens": (
                                chunk_result.vlm_model_output.input_tokens
                                if chunk_result.vlm_model_output
                                else 0
                            ),
                            "vlm_output_tokens": (
                                chunk_result.vlm_model_output.output_tokens
                                if chunk_result.vlm_model_output
                                else 0
                            ),
                        }
                    )
            except Exception as e:
                logger.warning("Error collecting request metrics: %s", e)

        self._update_stream_fps(chunk_result, req_info)

        if chunk_result.error:
            if not req_info.is_live:
                # Error was encountered while processing a chunk,
                # mark the request as failed for files
                # For live streams, continue processing new chunks
                req_info.status = RequestInfo.Status.FAILED
                req_info.error_message = chunk_result.error
                req_info.error_status_code = chunk_result.error_status_code
                self._vlm_pipeline.abort_chunks(req_info.assets[0].asset_id)
                req_info.status_event.set()

            self._send_error_message_to_kafka(chunk_result.error, req_info.stream_id)
            logger.error(
                "Encountered error while processing chunk %r of query %s - %s",
                chunk_result.chunk,
                req_info.request_id,
                chunk_result.error,
            )

        if req_info.is_live:
            live_stream_id = req_info.assets[0].asset_id

            # Handle stream reconnection errors - send to Kafka error topic
            if chunk_result.is_stream_error:
                self._send_error_message_to_kafka(
                    chunk_result.stream_error_message,
                    live_stream_id,
                    type="stream_reconnection",
                )
                logger.warning(
                    "Stream reconnection error for live-stream %s (attempt %d): %s",
                    live_stream_id,
                    chunk_result.stream_error_attempt_count,
                    chunk_result.stream_error_message,
                )
                return

            if not chunk_result.is_live_stream_ended:
                logger.info(
                    "Generated new response for live-stream %s, query %s, chunk %r, summary %s",
                    live_stream_id,
                    req_info.request_id,
                    chunk_result.chunk,
                    chunk_result.vlm_model_output.output if chunk_result.vlm_model_output else "",
                )
                req_info.chunk_count += 1

            self._process_output(req_info, False, [chunk_result])

            if chunk_result.is_live_stream_ended:

                cur_time = time.time()
                latency = cur_time - req_info.start_time
                if latency is not None and latency > 0:
                    vlm_latency_value = latency
                else:
                    vlm_latency_value = 0
                self._metrics._vlm_pipeline_latency_latest_value = vlm_latency_value
                if req_info._request_metrics:
                    req_info._request_metrics.vlm_pipeline_latency = vlm_latency_value

                # Queue that the request be marked completed
                # once all pending aggregation requests are completed.
                self._process_output(req_info, True, [])

                if req_info.vlm_pipeline_span:
                    try:
                        req_info.vlm_pipeline_span.end()
                    except Exception as e:
                        logger.warning("Failed to end vlm_pipeline_latency span: %s", e)
            return

        # Cache the processed chunk of a file
        req_info.processed_chunk_list.append(chunk_result)
        logger.info(
            "Processed chunk for query %s, total chunks %d, processed chunks %d, chunk %r,",
            req_info.request_id,
            req_info.chunk_count,
            len(req_info.processed_chunk_list),
            chunk_result.chunk,
        )

        # For streaming file requests, deliver each chunk immediately via SSE
        # by appending directly to the response queue (bypasses _process_output
        # finalization logic which should only run when all chunks are done).
        if req_info.query and req_info.query.stream and chunk_result.chunk:
            req_info.response.append(chunk_result)

        if len(req_info.processed_chunk_list) == req_info.chunk_count:
            # All chunks of file processed
            if not req_info.text_query:
                nvtx.end_range(req_info.nvtx_vlm_start)
            else:
                nvtx.end_range(req_info.nvtx_text_embeddings_start)
            cur_time = time.time()

            self._finalize_stream_fps_tracking(req_info)

            if req_info.status == RequestInfo.Status.FAILED:
                self._vlm_pipeline.abort_chunks_done(req_info.assets[0].asset_id)
            else:
                latency = cur_time - req_info.start_time
                logger.info(
                    "Processed all chunks for query %s, VLM pipeline time %.2f sec",
                    req_info.request_id,
                    latency,
                )

                if latency is not None and latency > 0:
                    if req_info._request_metrics:
                        req_info._request_metrics.vlm_pipeline_latency = latency
                    self._metrics._vlm_pipeline_latency_latest_value = latency

            if req_info.vlm_pipeline_span:
                try:
                    req_info.vlm_pipeline_span.end()
                except Exception as e:
                    logger.warning("Failed to end vlm_pipeline_latency span: %s", e)

            if req_info.query and req_info.query.stream:
                # Streaming: chunks already delivered individually; call with
                # empty list so finalization (profiling, OTEL, status) still runs.
                self._process_output(req_info, False, [])
            else:
                # Non-streaming: deliver all chunks at once
                self._process_output(req_info, False, req_info.processed_chunk_list)

    def _trigger_query(self, req_info: RequestInfo, start_time: float = None):
        """Trigger a query on a file"""
        from utils.file_splitter import FileSplitter

        logger.info("Triggering oldest queued query %s", req_info.request_id)
        req_info.status = RequestInfo.Status.PROCESSING
        req_info.start_time = start_time if start_time else time.time()

        # Open vlm_testdata_file once for writing if profiling is enabled
        if self._profile_requests:
            vlm_testdata_file_path = f"/tmp/rtvi-logs/vlm_testdata_{req_info.request_id}.txt"
            try:
                req_info.vlm_testdata_file_handle = open(vlm_testdata_file_path, "w")
                req_info.vlm_testdata_file_handle.write("Chunk_ID,Answer\n")
                logger.debug("Opened vlm_testdata_file at %s", vlm_testdata_file_path)
            except Exception as e:
                error_message = "Failed to open vlm_testdata_file: %s" % e
                self._send_error_message_to_kafka(error_message, req_info.stream_id)
                logger.warning(error_message)
                req_info.vlm_testdata_file_handle = None

        tracer = get_tracer()
        if tracer:
            req_info.vlm_pipeline_span = tracer.start_span(
                "VLM Pipeline Latency", context=trace.set_span_in_context(req_info._e2e_span)
            )
            req_info.vlm_pipeline_span.set_attribute("request_id", req_info.request_id)
            req_info.vlm_pipeline_span.set_attribute("stream_id", req_info.stream_id)
            req_info.vlm_pipeline_span.set_attribute("is_live", req_info.is_live)

        # Start FPS tracking for this stream
        self._start_stream_fps_tracking(req_info)

        # Trigger collecting GPU metrics
        self.start_request_profiling(req_info)

        paths_string = ";".join([asset.path for asset in req_info.assets])
        video_codec = None
        if len(req_info.assets) == 1:
            video_codec = req_info.media_file_info.video_codec
            req_info.video_fps = float(req_info.media_file_info.video_fps)

        # Set start/end times if not specified by user
        if not req_info.start_timestamp:
            req_info.start_timestamp = 0
        if req_info.end_timestamp is None:
            req_info.end_timestamp = req_info.file_duration / 1e9

        enable_dense_caption = bool(os.environ.get("ENABLE_DENSE_CAPTION", False))
        saved_responses = {}

        if enable_dense_caption:
            # Get dense caption from file if present
            saved_dc_file = req_info.assets[0].path + ".dc.json"
            if os.access(saved_dc_file, os.R_OK):
                logger.info(
                    "Saved DC available %s, regenerating dense caption frames.", saved_dc_file
                )
                processed_chunk_list = DenseCaptionSerializer.from_json(saved_dc_file)
                # Create a lookup dictionary for saved responses by chunk index
                for vlm_output in processed_chunk_list:
                    vlm_output.chunk.streamId = req_info.stream_id
                    saved_responses[vlm_output.chunk.chunkIdx] = vlm_output

        def _on_new_chunk(chunk: ChunkInfo, saved_responses=None):
            """Callback for when a new chunk is created"""
            if chunk is None:
                return
            chunk.streamId = req_info.stream_id
            req_info.chunk_count += 1

            saved_response = saved_responses.get(chunk.chunkIdx)

            # If we have a saved dense caption response, use it directly
            if saved_response:
                logger.info("Using saved dense caption for chunk %s", chunk.chunkIdx)
                self._on_vlm_chunk_response(saved_response, req_info)
            else:
                # Decode-only mode: we have saved response but need to decode for frame images
                decode_only = bool(saved_response)

                # No saved response, enqueue the chunk for normal VLM processing
                self._vlm_pipeline.enqueue_chunk(
                    chunk,
                    lambda response, saved_response=saved_response, req_info=req_info: (
                        self._on_vlm_chunk_response(saved_response or response, req_info)
                    ),
                    req_info.query,
                    req_info.request_id,
                    video_codec,
                    decode_only,
                )

        nvtx_file_split_start = nvtx.start_range(
            message="File Splitting-" + str(req_info.request_id), color="blue"
        )
        # Create virtual file chunks
        FileSplitter(
            paths_string,
            FileSplitter.SplitMode.SEEK,
            req_info.query.chunk_duration,
            start_pts=int(req_info.start_timestamp * 1e9),
            end_pts=int(req_info.end_timestamp * 1e9),
            sliding_window_overlap_sec=req_info.query.chunk_overlap_duration,
            media_file_info=req_info.media_file_info,
            on_new_chunk=lambda chunk: _on_new_chunk(chunk, saved_responses),
        ).split()
        nvtx.end_range(nvtx_file_split_start)

        # No chunks were created. Mark the request completed and trigger next query if queued
        if req_info.chunk_count == 0:
            req_info.status = RequestInfo.Status.SUCCESSFUL
            req_info.end_time = time.time()
            req_info.response = []
            self._finalize_stream_fps_tracking(req_info)
        req_info.nvtx_vlm_start = nvtx.start_range(
            message="VLM Pipeline-" + str(req_info.request_id), color="green"
        )

    def query(
        self,
        assets: list[Asset],
        query: VlmQuery,
    ):
        """Run a query on a file

        Args:
            assets: List of assets to query
            query: VlmQuery object with query parameters
        """

        try:
            # Get file duration
            media_file_info = MediaFileInfo.get_info(assets[0].path)
            file_duration = media_file_info.video_duration_nsec
        except gi.repository.GLib.GError as ex:
            raise ServiceException(ex.message, "FailedRequest", 400)

        if (
            self._args.max_file_duration != 0
            and file_duration > self._args.max_file_duration * 60000000000
        ):
            return (
                False,
                f"File duration {round(file_duration/60000000000, 2)} is greater"
                f" than max allowed {self._args.max_file_duration} minutes",
                None,
            )

        if (
            query.chunk_duration > 0
            and query.chunk_overlap_duration > 0
            and query.chunk_overlap_duration >= query.chunk_duration
        ):
            raise ServiceException(
                "chunkOverlapDuration must be less than chunkDuration", "BadParameter", 400
            )

        # Create a RequestInfo object and populate it
        req_info = RequestInfo()
        req_info.media_file_info = media_file_info
        req_info.query = query  # Store the entire query object
        req_info.assets = assets
        req_info.start_timestamp = (
            query.media_info.start_offset
            if query.media_info and query.media_info.type == "offset"
            else None
        )
        req_info.end_timestamp = (
            query.media_info.end_offset
            if query.media_info and query.media_info.type == "offset"
            else None
        )
        req_info.file_duration = file_duration

        req_info.nvtx_summarization_start = nvtx.start_range(
            message="Summarization-" + str(req_info.request_id), color="blue"
        )

        # Lock the asset(s) so that it cannot be deleted while it is being used.
        for asset in req_info.assets:
            asset.lock()

        req_info.queue_time = time.time()
        # Adding the request info to the request info map
        with self._lock:
            self._request_info_map[req_info.request_id] = req_info

        # Add the request to the pending queue
        self._metrics._queries_pending_counter.add(1)

        tracer = get_tracer()
        if tracer:
            req_info._e2e_span = tracer.start_span("Pipeline End-to-End")
            req_info._e2e_span.set_attribute("request_id", req_info.request_id)
            req_info._e2e_span.set_attribute("stream_id", req_info.stream_id)
            req_info._e2e_span.set_attribute("is_live", req_info.is_live)

        self._trigger_query(req_info, None)
        return req_info.request_id

    def generate_vlm_captions(self, assets: list[Asset], query: VlmQuery, is_rtsp=False):
        """Run VLM captions generation on a file or RTSP stream.
        This reuses the query function since they have identical logic.
        """

        # Modify prompt based on enable_reasoning parameter
        if query.enable_reasoning:
            logger.debug("Reasoning is enabled in generate_vlm_captions API")

        # Validate input dimensions
        if (query.vlm_input_width > 0 and query.vlm_input_width < 16) or (
            query.vlm_input_height > 0 and query.vlm_input_height < 16
        ):
            raise ServiceException(
                "vlm_input_width and vlm_input_height must be greater than or equal to 16",
                "BadParameter",
                400,
            )

        if not self._args.enable_audio and query.enable_audio:
            raise ServiceException(
                "Audio processing is not enabled. "
                "Set VLM_MODEL_SUPPORTS_AUDIO=true in your .env to enable native audio processing "
                "for Omni models (e.g. Nemotron Nano Omni).",
                "BadParameter",
                400,
            )

        if is_rtsp:
            # Handle RTSP stream VLM captions by reusing add_rtsp_stream_query
            if len(assets) != 1:
                raise ServiceException(
                    "RTSP VLM captions require exactly one asset", "BadParameter", 400
                )

            asset = assets[0]

            # Create VLM captions request (includes stream setup and validation)
            req_id = self._create_rtsp_vlm_captions_request(asset, query)
            return req_id
        else:
            # Handle file-based VLM captions
            req_id = self.query(
                assets=assets,
                query=query,
            )
            return req_id

    def _create_rtsp_vlm_captions_request(self, asset: Asset, query: VlmQuery):
        """Create a VLM captions request for RTSP streams without requiring summary_duration."""

        # Validate chunk_duration parameter
        if query.chunk_duration <= 0:
            raise ServiceException("chunk_duration must be greater than 0", "BadParameter", 400)

        # A live stream can be added only once
        with self._lock:
            existing_request = self._get_live_stream_request(asset.asset_id)
            if existing_request:
                raise ServiceException(
                    f"Live stream already has query '{existing_request.request_id}' running."
                    " Update or stop the same query.",
                    "BadParameters",
                    400,
                )

            if self._count_active_live_streams() >= self._args.max_live_streams:
                raise ServiceException(
                    "Server is already processing maximum number of live streams"
                    f" ({self._args.max_live_streams})",
                    503,
                )

            # Lock the asset so that it cannot be deleted while it is being used.
            asset.lock()

        # Create a RequestInfo object and populate it for VLM captions
        req_info = RequestInfo()
        req_info.query = query  # Store the entire query object
        req_info.assets = [asset]
        req_info.is_live = True
        req_info.status = RequestInfo.Status.PROCESSING
        req_info.start_time = time.time()
        req_info.queue_time = time.time()

        # Add the request to the request info map
        with self._lock:
            self._request_info_map[req_info.request_id] = req_info

        self._metrics._active_live_streams_counter.add(1)

        # Open vlm_testdata_file once for writing if profiling is enabled
        if self._profile_requests:
            vlm_testdata_file_path = f"/tmp/rtvi-logs/vlm_testdata_{req_info.request_id}.txt"
            try:
                req_info.vlm_testdata_file_handle = open(vlm_testdata_file_path, "w")
                req_info.vlm_testdata_file_handle.write("Chunk_ID,Answer\n")
                logger.debug("Opened vlm_testdata_file at %s", vlm_testdata_file_path)
            except Exception as e:
                logger.warning("Failed to open vlm_testdata_file: %s", e)
                req_info.vlm_testdata_file_handle = None

        # Trigger collecting GPU metrics
        self.start_request_profiling(req_info)

        tracer = get_tracer()
        if tracer:
            req_info._e2e_span = tracer.start_span("Pipeline End-to-End")
            req_info._e2e_span.set_attribute("request_id", req_info.request_id)
            req_info._e2e_span.set_attribute("stream_id", req_info.stream_id)
            req_info._e2e_span.set_attribute("is_live", req_info.is_live)

            req_info.vlm_pipeline_span = tracer.start_span(
                "VLM Pipeline Latency", context=trace.set_span_in_context(req_info._e2e_span)
            )
            req_info.vlm_pipeline_span.set_attribute("request_id", req_info.request_id)
            req_info.vlm_pipeline_span.set_attribute("stream_id", req_info.stream_id)
            req_info.vlm_pipeline_span.set_attribute("is_live", req_info.is_live)

        # Add to VLM pipeline for processing
        self._vlm_pipeline.add_live_stream(
            asset=asset,
            vlm_query=req_info.query,
            on_chunk_result=lambda response, req_info=req_info: self._on_vlm_chunk_response(
                response, req_info
            ),
        )

        return req_info.request_id

    def start_request_profiling(self, req_info):
        # Start collecting GPU metrics if enabled
        if not self._profile_requests:
            return

        logger.info("Starting GPUMonitor for request %s", req_info.request_id)
        req_info._monitor = GPUMonitor()
        req_info._monitor.start_recording_nvdec(
            interval_in_seconds=0.2,
            nvdec_plot_file_name="/tmp/rtvi-logs/nvdec_usage_" + str(req_info.request_id) + ".csv",
        )
        req_info._monitor.start_recording_gpu_usage(
            interval_in_seconds=0.2,
            gpu_plot_file_name="/tmp/rtvi-logs/gpu_usage_" + str(req_info.request_id) + ".csv",
        )
        req_info._request_metrics = RequestMetrics()
        req_info._request_metrics.resource_usage_graph_paths = [
            req_info._monitor.nvdec_plot_file_name,
            req_info._monitor.gpu_plot_file_name,
        ]
        req_info._request_metrics.set_gpu_names(req_info._monitor.get_gpu_names())
        req_info._request_metrics.chunk_size = req_info.query.chunk_duration
        req_info._request_metrics.chunk_overlap_duration = req_info.query.chunk_overlap_duration
        req_info._request_metrics.input_video_duration = req_info.file_duration / (
            1000 * 1000 * 1000
        )  # ns to s
        if req_info._request_metrics.chunk_size <= 0:
            req_info._request_metrics.num_chunks = 1
        else:
            req_info._request_metrics.num_chunks = (
                req_info._request_metrics.input_video_duration
                / req_info._request_metrics.chunk_size
            )
        req_info._request_metrics.num_gpus = self._args.num_gpus
        info = self._loaded_models_info
        req_info._request_metrics.vlm_model_name = str(info.id)
        req_info._request_metrics.vlm_batch_size = self._args.vlm_batch_size

    def stop_request_profiling(self, req_info, chunk_responses: list[PipelineChunkResult]):
        def find_extreme(responses, func, value):
            values = []
            for response in responses:
                # Handle both dictionary items (from all_times) and objects (from chunk_responses)
                if isinstance(response, dict):
                    attr_value = response.get(value)
                elif hasattr(response, value):
                    attr_value = getattr(response, value)
                else:
                    attr_value = None
                if attr_value is not None:
                    values.append(attr_value)
            if not values:
                return 0
            return func(values)

        cur_time = time.time()
        e2e_latency = cur_time - req_info.start_time
        self._metrics._e2e_latency_latest_value = e2e_latency

        if req_info._monitor:
            logger.info("Stopping GPUMonitor for request %s", req_info.request_id)
            plot_graph_file = "/tmp/rtvi-logs/plot_nvdec_" + str(req_info.request_id) + ".png"
            plot_graph_files = {
                "gpu": "/tmp/rtvi-logs/plot_gpu_" + str(req_info.request_id) + ".png",
                "gpu_mem": "/tmp/rtvi-logs/plot_gpu_mem_" + str(req_info.request_id) + ".png",
            }
            req_info._monitor.stop_recording_nvdec(plot_graph_file=plot_graph_file)
            req_info._monitor.stop_recording_gpu(plot_graph_files=plot_graph_files)
            req_info._request_metrics.resource_usage_graph_plot_paths = [
                plot_graph_file,
                plot_graph_files["gpu"],
                plot_graph_files["gpu_mem"],
            ]

        if req_info._request_metrics:
            all_times = getattr(req_info._request_metrics, "all_times", None)
            if all_times and len(all_times) > 0:
                max_decode_end_time = find_extreme(all_times, max, "decode_end_time")
                min_decode_start_time = find_extreme(all_times, min, "decode_start_time")
                max_vlm_end_time = find_extreme(all_times, max, "vlm_end_time")
                min_vlm_start_time = find_extreme(all_times, min, "vlm_start_time")
            elif chunk_responses and len(chunk_responses) > 0:
                max_decode_end_time = find_extreme(chunk_responses, max, "decode_end_time")
                min_decode_start_time = find_extreme(chunk_responses, min, "decode_start_time")
                max_vlm_end_time = find_extreme(chunk_responses, max, "vlm_end_time")
                min_vlm_start_time = find_extreme(chunk_responses, min, "vlm_start_time")
            else:
                max_decode_end_time = 0
                min_decode_start_time = 0
                max_vlm_end_time = 0
                min_vlm_start_time = 0
        elif chunk_responses and len(chunk_responses) > 0:
            max_decode_end_time = find_extreme(chunk_responses, max, "decode_end_time")
            min_decode_start_time = find_extreme(chunk_responses, min, "decode_start_time")
            max_vlm_end_time = find_extreme(chunk_responses, max, "vlm_end_time")
            min_vlm_start_time = find_extreme(chunk_responses, min, "vlm_start_time")
        else:
            max_decode_end_time = 0
            min_decode_start_time = 0
            max_vlm_end_time = 0
            min_vlm_start_time = 0

        create_historical_span(
            "Total Decode Latency",
            min_decode_start_time,
            max_decode_end_time,
            {"operation": "decode"},
            parent_span=req_info.vlm_pipeline_span,
        )

        create_historical_span(
            "Total VLM Latency",
            min_vlm_start_time,
            max_vlm_end_time,
            {"operation": "vlm"},
            parent_span=req_info.vlm_pipeline_span,
        )

        create_historical_span(
            "Total Chunk Latency",
            min_decode_start_time,
            max_vlm_end_time,
            {"operation": "chunk"},
            parent_span=req_info.vlm_pipeline_span,
        )

        if req_info._request_metrics:
            req_info._request_metrics.e2e_latency = e2e_latency
            req_info._request_metrics.decode_latency = max_decode_end_time - min_decode_start_time
            req_info._request_metrics.vlm_latency = max_vlm_end_time - min_vlm_start_time
            req_info._request_metrics.req_start_time = req_info.start_time
            req_info._request_metrics.total_vlm_input_tokens = sum(
                [
                    resp.vlm_model_output.input_tokens if resp.vlm_model_output else 0
                    for resp in chunk_responses
                ]
            )
            req_info._request_metrics.total_vlm_output_tokens = sum(
                [
                    resp.vlm_model_output.output_tokens if resp.vlm_model_output else 0
                    for resp in chunk_responses
                ]
            )

            logger.debug("_request_metrics json: %s", str(vars(req_info._request_metrics)))
            metrics_summary_file_name = (
                "/tmp/rtvi-logs/request_metrics_" + str(req_info.request_id) + ".json"
            )
            req_info._request_metrics.dump_json(file_name=metrics_summary_file_name)
            logger.info("Request Metrics Summary written to %s", metrics_summary_file_name)

        # if self._profile_requests:  # disabled for now, to be re-enabled later

        #     from utils.otel_helper import dump_traces_to_file

        #     trace_files = dump_traces_to_file(str(req_info.request_id))
        #     if trace_files["json_file"]:
        #         logger.info(
        #             "OTEL traces dumped to %s and %s",
        #             trace_files["json_file"],
        #             trace_files["text_file"],
        #         )

        req_info._monitor = None

    def remove_video_file(self, asset: Asset):
        logger.info("Removing video %s from pipeline", asset.asset_id)
        # Phase A (under lock): pop request_info_map entries referencing this asset.
        with self._lock:
            if asset.use_count > 0:
                logger.debug("Asset %s still in use, skipping removal", asset.asset_id)
                return
            self._request_info_map = {
                req_id: req_info
                for req_id, req_info in self._request_info_map.items()
                if req_info.assets is None or asset not in req_info.assets
            }
        # Phase B (lock released): no pipeline drain for files today, but keeping
        # the pop-then-release structure mirrors remove_rtsp_stream so any future
        # lock-escaping cleanup can be added below without reshuffling.

    def remove_rtsp_stream(self, asset: Asset, drain_timeout_sec: Optional[float] = None):
        """Remove an RTSP stream from the server."""
        _start = time.monotonic()
        try:
            # Phase A (under lock): pop request_info_map entry, read what we need.
            with self._lock:
                existing_request = self._get_live_stream_request(asset.asset_id)
                if not existing_request:
                    logger.debug("RTSP stream for video %s not active", asset.asset_id)
                    has_active_request = False
                else:
                    has_active_request = True
                    self._request_info_map = {
                        req_id: req_info
                        for req_id, req_info in self._request_info_map.items()
                        if req_info.assets is None or asset not in req_info.assets
                    }

            # Phase B (lock released): drain the pipeline and remove cached frames.
            if has_active_request:
                logger.info("Removing live stream %s from pipeline", asset.asset_id)
                # Take drain latency from the return value to avoid racing on a
                # shared attribute under parallel batch delete.
                drain_latency = self._vlm_pipeline.remove_live_stream(
                    asset.asset_id, timeout_sec=drain_timeout_sec
                )
                logger.info("Removed live stream %s from pipeline", asset.asset_id)

                if drain_latency is not None:
                    self._metrics._delete_drain_latency.record(drain_latency)

                # A forced/timed-out pipeline drain may bypass the live EOS
                # callback that normally unlocks the request asset. Release the
                # request-held lock here so AssetManager cleanup does not return
                # ResourceInUse after this delete path has already stopped the
                # stream and removed its request from the active map.
                for request_asset in existing_request.assets or []:
                    if request_asset.use_count > 0:
                        request_asset.unlock()

                self._safe_rmtree(f"/tmp/rtvi/cached_frames/{asset.asset_id}")
        finally:
            _elapsed = time.monotonic() - _start
            self._metrics._delete_latency.record(_elapsed)
            logger.info(
                "Delete live-stream %s total=%.3fs",
                asset.asset_id,
                _elapsed,
            )

    def set_cleanup_executor(self, executor) -> None:
        """Wire in a ThreadPoolExecutor for fire-and-forget rmtree.

        When set, ``_safe_rmtree`` submits the removal to this executor so
        callers never block on disk I/O during a stream delete.
        """
        self._cleanup_executor = executor

    def _safe_rmtree(self, path: str) -> None:
        """Remove a directory tree, ignoring FileNotFoundError.

        Submits to ``_cleanup_executor`` when configured (fire-and-forget),
        else runs inline. Any unexpected error is logged but never raised
        so it does not break the delete path.
        """

        def _do_rmtree() -> None:
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning("rmtree(%s) failed: %s", path, e)

        if self._cleanup_executor is not None:
            self._cleanup_executor.submit(_do_rmtree)
        else:
            _do_rmtree()

    def stop(self, force=False):
        """Stop the Stream Handler"""
        logger.info("Stopping Stream Handler")
        with self._lock:
            self._stopping = True
            kafka_stop_event = getattr(self, "_kafka_send_stop_event", None)
            if kafka_stop_event is not None:
                kafka_stop_event.set()
            redis_stop_event = getattr(self, "_redis_send_stop_event", None)
            if redis_stop_event is not None:
                redis_stop_event.set()

        if hasattr(self, "_vlm_pipeline") and self._vlm_pipeline is not None:
            self._vlm_pipeline.stop(force)

        # Close Kafka producer if initialized
        kafka_producer = getattr(self, "_kafka_producer", None)
        kafka_send_thread = getattr(self, "_kafka_send_thread", None)
        if kafka_producer is not None:
            try:
                if kafka_send_thread is not None:
                    kafka_send_thread.join(timeout=5)
                    if kafka_send_thread.is_alive():
                        logger.warning("Kafka async sender thread did not stop within timeout")
                kafka_producer.flush(timeout=10)
                kafka_producer.close(timeout=10)
                logger.info("Kafka producer closed")
            except Exception as e:
                logger.warning("Error closing Kafka producer: %s", e)
            finally:
                with self._lock:
                    self._kafka_send_thread = None
                    self._kafka_send_queue = None
                    self._kafka_producer = None

        # Close Redis client if initialized
        redis_client = getattr(self, "_redis_client", None)
        redis_send_thread = getattr(self, "_redis_send_thread", None)
        if redis_client is not None:
            try:
                if redis_send_thread is not None:
                    redis_send_thread.join(timeout=5)
                    if redis_send_thread.is_alive():
                        logger.warning("Redis async sender thread did not stop within timeout")
                redis_client.close()
                logger.info("Redis client closed")
            except Exception as e:
                logger.warning("Error closing Redis client: %s", e)
            finally:
                with self._lock:
                    self._redis_send_thread = None
                    self._redis_send_queue = None
                    self._redis_client = None

        logger.info("Stopped Stream Handler")

    def get_response(self, request_id, chunk_response_size=None):
        """Get currently available response for the request

        Args:
            request_id: ID of the request
            chunk_response_size: Number of chunked responses to include.
                                 Defaults to None (all available).

        Returns:
            A tuple of the request details and currently available response
        """
        with self._lock:
            if request_id not in self._request_info_map:
                raise ServiceException(
                    f"No such request-id {request_id}", "InvalidParameterValue", 400
                )

            req_info = self._request_info_map[request_id]
        if chunk_response_size is None:
            # Return all available response
            response = req_info.response
            # Reset response to empty
            req_info.response = []
        else:
            # Get user specified number of chunked responses
            response = req_info.response[:chunk_response_size]
            # Remove the responses that will be returned
            req_info.response = req_info.response[chunk_response_size:]
        return req_info, response

    def wait_for_request_done(self, request_id):
        """Wait for request to either complete or fail."""

        with self._lock:
            if request_id not in self._request_info_map:
                raise ServiceException(
                    f"No such request-id {request_id}", "InvalidParameterValue", 400
                )
            req_info = self._request_info_map[request_id]

        while req_info.status not in [RequestInfo.Status.FAILED, RequestInfo.Status.SUCCESSFUL]:
            logger.info(
                "Status for query %s is %s, percent complete is %.2f, size of response list is %d",
                req_info.request_id,
                req_info.status.value,
                req_info.progress,
                len(req_info.response),
            )
            req_info.status_event.wait(timeout=5)

    def get_models_info(self):
        return self._vlm_pipeline.get_models_info()

    def get_health_status(self, readiness=False):
        checks = self._vlm_pipeline.get_health_status()
        is_healthy = all(check.healthy for check in checks)
        current_time = time.time()
        status = {
            "healthy": is_healthy,
            "timestamp": current_time,
            "uptime_seconds": current_time - self._start_time,
            "checks": checks,
        }
        return status

    def generate_text_embeddings(self, query: TextEmbeddingsQuery):
        """Generate embeddings for a given text input."""

        req_info = RequestInfo()
        req_info.text_query = query
        req_info.chunk_count = len(query.text_input_list)
        req_info.status = RequestInfo.Status.PROCESSING
        req_info.start_time = time.time()
        req_info.queue_time = time.time()
        req_info.status_event = Event()
        req_info.nvtx_text_embeddings_start = nvtx.start_range(
            message="Text Embeddings-" + str(req_info.request_id), color="blue"
        )

        logger.debug("New text embeddings request: %s", req_info.request_id)
        # req_info.status_event.set()

        with self._lock:
            self._request_info_map[req_info.request_id] = req_info

        logger.debug("Enqueuing text chunks: %s", len(query.text_input_list))
        for chunk_idx, text_input in enumerate(query.text_input_list):
            self._vlm_pipeline.enqueue_text_chunk(
                chunk=ChunkInfo(
                    text_input=text_input,
                    chunkIdx=chunk_idx,
                    chunk_type="text",
                    frame_times=[0.0 + chunk_idx * 0.01],  # dummy frame time
                ),
                on_chunk_result=lambda response, req_info=req_info: self._on_text_chunk_response(
                    response, req_info
                ),
                text_embeddings_query=query,
                request_id=req_info.request_id,
            )
        return req_info.request_id

    def _on_text_chunk_response(self, chunk_result: PipelineChunkResult, req_info: RequestInfo):
        """Callback for when a new text chunk is created"""
        # Check if the response contains an error
        if chunk_result.error:
            req_info.status = RequestInfo.Status.FAILED
            req_info.error_message = chunk_result.error
            req_info.error_status_code = chunk_result.error_status_code
            self._send_error_message_to_kafka(chunk_result.error, req_info.stream_id)
            req_info.status_event.set()
            return

        # Track inference latency for text embeddings
        if chunk_result.vlm_end_time > chunk_result.vlm_start_time:
            vlm_latency = chunk_result.vlm_end_time - chunk_result.vlm_start_time
            processing_latency = chunk_result.vlm_end_time - chunk_result.decode_start_time
            # queue_time = chunk_result.vlm_start_time - chunk_result.decode_start_time
            self._metrics._vlm_latency.record(vlm_latency)
            self._metrics._vlm_latency_latest_value = vlm_latency
            self._metrics._chunk_latency.record(processing_latency)
            self._metrics._chunk_latency_latest_value = processing_latency
            logger.debug(
                "Text embedding VLM latency for chunk %d: %.3f ms",
                chunk_result.chunk.chunkIdx,
                vlm_latency * 1000,
            )

        req_info.processed_chunk_list.append(chunk_result)

        kafka_enabled_for_text_embeddings = (
            os.getenv("ENABLE_KAFKA_MESSAGES_FOR_TEXT_INPUT", "false").lower() == "true"
        )

        if self._kafka_enabled and kafka_enabled_for_text_embeddings:
            try:
                vision_llm_message = self._chunk_result_to_vision_llm_text(chunk_result, req_info)
            except Exception as exc:
                vision_llm_message = None
                error_message = "Failed to build VisionLLM protobuf for chunk %s: %s" % (
                    getattr(chunk_result.chunk, "chunkIdx", "unknown"),
                    exc,
                )
                self._send_error_message_to_kafka(error_message, req_info.stream_id)
                logger.debug(
                    error_message,
                    exc_info=True,
                )

            if vision_llm_message:
                chunk_result.vision_llm_proto = vision_llm_message
                try:
                    chunk_result.vision_llm_proto_serialized = (
                        vision_llm_message.SerializeToString()
                    )
                    # Send to Kafka if producer is available
                    if chunk_result.vision_llm_proto_serialized:
                        self._send_protobuf_to_kafka(
                            chunk_result.vision_llm_proto_serialized, chunk_result, req_info
                        )
                except Exception as exc:
                    error_message = "Failed to serialize VisionLLM protobuf for chunk %s: %s" % (
                        getattr(chunk_result.chunk, "chunkIdx", "unknown"),
                        exc,
                    )
                    self._send_error_message_to_kafka(error_message, req_info.stream_id)
                    logger.debug(error_message)

        if len(req_info.processed_chunk_list) == len(req_info.text_query.text_input_list):
            req_info.status = RequestInfo.Status.SUCCESSFUL
            req_info.status_event.set()

            # Calculate and log e2e latency for text embeddings
            req_info.end_time = time.time()
            e2e_latency = req_info.end_time - req_info.start_time
            self._metrics._e2e_latency_latest_value = e2e_latency

            self._process_output(req_info, False, req_info.processed_chunk_list)

    @staticmethod
    def populate_argument_parser(parser: ArgumentParser):
        """Add Stream Handler arguments to the argument parser"""

        VlmPipeline.populate_argument_parser(parser)

        parser.add_argument(
            "--enable-dev-dc-gen",
            action="store_true",
            default=False,
            help="Generate Dense Captions file for debugging",
        )
        parser.add_argument(
            "--max-file-duration",
            type=int,
            default=0,
            help="Maximum file duration to allow (0 = no restriction)",
        )

        parser.add_argument(
            "--asset-dir", type=str, help="Directory to store the assets in", default="assets"
        )

        parser.add_argument(
            "--kafka-enabled",
            action="store_true",
            default=False,
            help="Enable Kafka integration (true/false)."
            " Can also be set via KAFKA_ENABLED environment variable.",
        )
        parser.add_argument(
            "--kafka-topic",
            type=str,
            default="mdx-vlm-captions",
            help="Kafka topic name for VisionLLM messages."
            " Defaults to 'mdx-vlm-captions'. Can also be set via KAFKA_TOPIC environment variable.",
        )
        parser.add_argument(
            "--kafka-bootstrap-servers",
            type=str,
            default="",
            help="Kafka bootstrap servers (comma-separated list)."
            " Can also be set via KAFKA_BOOTSTRAP_SERVERS environment variable.",
        )

    def _start_stream_fps_tracking(self, req_info: RequestInfo):
        """Start FPS tracking for a new stream."""
        req_info._fps_start_time = time.time()
        req_info._fps_frame_count = 0
        req_info._fps_last_update_time = req_info._fps_start_time
        req_info._fps_is_active = True
        logger.debug("Started FPS tracking for stream: %s", req_info.request_id)

    def _update_stream_fps(self, chunk_result: PipelineChunkResult, req_info: RequestInfo):
        """Update FPS tracking for a stream."""
        if not req_info._fps_is_active:
            return

        if req_info.video_fps:
            frame_count = int(req_info.query.chunk_duration * req_info.video_fps)
        else:
            frame_count = (
                len(chunk_result.frame_times)
                if hasattr(chunk_result, "frame_times") and chunk_result.frame_times
                else 0
            )

        req_info._fps_frame_count += frame_count
        req_info._fps_last_update_time = time.time()

        current_fps = self._get_request_fps(req_info)
        self._metrics._stream_fps_histogram.record(current_fps)

    def _finalize_stream_fps_tracking(self, req_info: RequestInfo):
        """Finalize FPS tracking for a completed stream."""
        if not req_info._fps_is_active:
            return

        final_fps = self._get_request_fps(req_info)
        self._metrics._stream_fps_histogram.record(final_fps)
        req_info._fps_is_active = False
        logger.debug(
            "Finalized FPS tracking for stream %s, final FPS: %.2f",
            req_info.request_id,
            final_fps,
        )

    def _get_request_fps(self, req_info: RequestInfo) -> float:
        """Get current FPS for a request."""
        if not req_info._fps_is_active or req_info._fps_start_time is None:
            return 0.0

        elapsed_time = req_info._fps_last_update_time - req_info._fps_start_time
        if elapsed_time > 0 and req_info._fps_frame_count > 0:
            return req_info._fps_frame_count / elapsed_time
        return 0.0

    def get_active_streams_info(self) -> dict:
        """Get information about all active streams and their FPS.

        Returns:
            dict: Dictionary with stream_id -> fps mapping for active streams
        """
        with self._lock:
            active_streams_info = {}
            for req_info in self._request_info_map.values():
                if req_info._fps_is_active and req_info.assets and len(req_info.assets) > 0:
                    active_streams_info[req_info.stream_id] = self._get_request_fps(req_info)
            return active_streams_info

    def update_live_stream_chunk_latency(self, latency: float):
        """Update live stream chunk latency metric"""
        if hasattr(self._metrics, "_live_stream_chunk_latency"):
            # Record distribution
            self._metrics._live_stream_chunk_latency.record(latency)
        # Maintain latest value for dashboards (parity with captions)
        if hasattr(self._metrics, "_live_stream_chunk_latency_latest_value"):
            self._metrics._live_stream_chunk_latency_latest_value = latency

    def update_live_stream_captions_latency(self, latency: float):
        """Update live stream captions latency metric"""
        if hasattr(self._metrics, "_live_stream_captions_latency"):
            self._metrics._live_stream_captions_latency.record(latency)
            # Update latest value for observable gauge
            self._metrics._live_stream_captions_latency_latest_value = latency
