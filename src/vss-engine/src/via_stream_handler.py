######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

from vlm_pipeline import VlmPipeline, VlmRequestParams, VlmChunkResponse, VlmModelType  # isort:skip
import argparse
import concurrent.futures
import copy
import glob
import json
import os
import shutil
import subprocess
import time
import traceback
import uuid
from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from threading import Event, RLock, Thread
from urllib.parse import urlparse

import aiohttp
import cuda
import cuda.bindings.runtime
import gi
import jinja2
import nvtx
import prometheus_client as prom
import uvicorn
from fastapi import FastAPI
from minio import Minio
from pyaml_env import parse_config

from asset_manager import Asset
from chunk_info import ChunkInfo
from cv_pipeline import CVPipeline
from otel_helper import create_historical_span, get_tracer, is_tracing_enabled
from utils import MediaFileInfo, process_highlight_request
from via_exception import ViaException
from via_health_eval import GPUMonitor, RequestHealthMetrics
from via_logger import TimeMeasure, logger
from vss_api_models import (
    DEFAULT_CALLBACK_JSON_TEMPLATE,
    ReviewAlertRequest,
    SummarizationQuery,
)

ALERT_CALLBACK_PORT = 60000
MAX_MILVUS_STRING_LEN = 65535

ALERT_REVIEW_SKIP_GUARDRAILS = os.environ.get("ALERT_REVIEW_SKIP_GUARDRAILS", "true") == "true"


class AlertInfo:
    """Store information for an alert"""

    def __init__(self):
        self.alert_id = str(uuid.uuid4())
        self.events: list[str] = []
        self.callbackUrl = None
        self.callbackJsonTemplate = DEFAULT_CALLBACK_JSON_TEMPLATE
        self.callbackToken = None
        self.liveStreamId = ""
        self.requestId = ""
        self.alert_tool: AlertSseTool | AlertCallbackTool = None
        self.name = ""


class RequestInfo:
    """Store information for a request"""

    class Status(Enum):
        """Video Query Request Status."""

        QUEUED = "queued"
        PROCESSING = "processing"
        SUCCESSFUL = "successful"
        FAILED = "failed"
        STOPPING = "stopping"

    class Response:
        def __init__(
            self,
            start_timestamp: str,
            end_timestamp: str,
            response: str,
            reasoning_description: str = "",
        ) -> None:
            self.start_timestamp = start_timestamp
            self.end_timestamp = end_timestamp
            self.response = response
            self.reasoning_description = reasoning_description

    class Alert:
        offset = 0
        ntpTimestamp = ""
        detectedEvents: list[str] = []
        streamId = ""
        name = ""
        alertId = ""
        details = ""
        alert_time = 0

    def __init__(self) -> None:
        self.request_id = str(uuid.uuid4())
        self.stream_id = ""
        self.chunk_count = 0
        self.chunk_size = 0
        self.video_fps = None
        self.chunk_overlap_duration = 0
        self.file = ""
        self.processed_chunk_list: list[VlmChunkResponse] = []
        self.is_summarization = False
        self.vlm_request_params = VlmRequestParams()
        self.progress = 0
        self.response: list[RequestInfo.Response] = []
        self.is_live = False
        self.start_timestamp = None
        self.end_timestamp = None
        self.queue_time = None
        self.start_time = None
        self.end_time = None
        self.file_duration = 0
        self.assets: list[Asset] = None
        self.status = RequestInfo.Status.QUEUED
        self.status_event = Event()
        self.summary_duration = 0
        self.caption_summarization_prompt = ""
        self.summary_aggregation_prompt = ""
        self.graph_rag_prompt_yaml = ""
        self._health_summary = None
        self._monitor = None
        self._ca_rag_latency = 0
        self._ctx_mgr = None
        self._output_process_thread_pool: concurrent.futures.ThreadPoolExecutor = None
        self.alerts: list[RequestInfo.Alert] = []
        self.nvtx_vlm_start = None
        self.nvtx_summarization_start = None
        self.summarize = None
        self.enable_chat = True
        self.enable_chat_history = True
        self.enable_cv_pipeline = False
        self.cv_metadata_json_file = ""
        self.pending_add_doc_start_time = 0
        self.pending_add_doc_end_time = 0
        self.num_frames_per_chunk = None
        self.summarize_batch_size = None
        self.rag_batch_size = None
        self.rag_top_k = None
        self.vlm_input_width = None
        self.vlm_input_height = None
        self.enable_audio = False
        self.last_chunk: ChunkInfo | None = None
        self.summarize_top_p = None
        self.summarize_temperature = None
        self.summarize_max_tokens = None
        self.chat_top_p = None
        self.chat_temperature = None
        self.chat_max_tokens = None
        self.notification_top_p = None
        self.notification_temperature = None
        self.notification_max_tokens = None
        self.highlight = False
        self.graph_db = None
        self.enable_cot = False
        self.enable_image = False
        self.alert_review = False
        self.camera_id = ""
        # OTEL spans
        self._e2e_span = None
        self.vlm_pipeline_span = None
        # fps metrics
        self._fps_start_time = None
        self._fps_frame_count = 0
        self._fps_last_update_time = None
        self._fps_is_active = False
        self.user_specified_collection_name = None
        self.custom_metadata = None
        self.delete_external_collection = False
        self.error_message = ""
        self.media_file_info: MediaFileInfo = None


class DCSerializer:
    @staticmethod
    def to_json(request_info: RequestInfo, file_path):
        try:
            with open(file_path, "w") as f:
                for vlm_response in request_info.processed_chunk_list:
                    json.dump(
                        {
                            "vlm_response": vlm_response.vlm_response,
                            "frame_times": vlm_response.frame_times,
                            "chunk": {
                                "streamId": vlm_response.chunk.streamId,
                                "chunkIdx": vlm_response.chunk.chunkIdx,
                                "file": vlm_response.chunk.file,
                                "pts_offset_ns": vlm_response.chunk.pts_offset_ns,
                                "start_pts": vlm_response.chunk.start_pts,
                                "end_pts": vlm_response.chunk.end_pts,
                                "start_ntp": vlm_response.chunk.start_ntp,
                                "end_ntp": vlm_response.chunk.end_ntp,
                                "start_ntp_float": vlm_response.chunk.start_ntp_float,
                                "end_ntp_float": vlm_response.chunk.end_ntp_float,
                                "is_first": vlm_response.chunk.is_first,
                                "is_last": vlm_response.chunk.is_last,
                                "asset_dir": vlm_response.chunk.asset_dir,
                            },
                        },
                        f,
                    )
                    f.write("\n")
        except Exception as e:
            logger.warning("write to_json Exception:", str(e))

    @staticmethod
    def from_json(file_path):
        request_info = RequestInfo()
        try:
            with open(file_path, "r") as f:
                for line in f:
                    data = json.loads(line)
                    chunk_info = ChunkInfo()
                    chunk_info.streamId = data["chunk"]["streamId"]
                    chunk_info.chunkIdx = data["chunk"]["chunkIdx"]
                    chunk_info.file = data["chunk"]["file"]
                    chunk_info.pts_offset_ns = data["chunk"]["pts_offset_ns"]
                    chunk_info.start_pts = data["chunk"]["start_pts"]
                    chunk_info.end_pts = data["chunk"]["end_pts"]
                    chunk_info.start_ntp = data["chunk"]["start_ntp"]
                    chunk_info.end_ntp = data["chunk"]["end_ntp"]
                    chunk_info.start_ntp_float = data["chunk"]["start_ntp_float"]
                    chunk_info.end_ntp_float = data["chunk"]["end_ntp_float"]
                    chunk_info.is_first = data["chunk"]["is_first"]
                    chunk_info.is_last = data["chunk"]["is_last"]
                    vlm_response = VlmChunkResponse()
                    vlm_response.vlm_response = data["vlm_response"]
                    vlm_response.frame_times = data["frame_times"]
                    vlm_response.chunk = chunk_info

                    request_info.processed_chunk_list.append(vlm_response)
                # Sort the processed_chunk_list by chunkIdx
                if request_info.processed_chunk_list:
                    request_info.processed_chunk_list.sort(key=lambda x: x.chunk.chunkIdx)
        except Exception as e:
            logger.warning("read from json exception", str(e))
        return request_info


class LiveStreamInfo:
    """Store information for a live stream"""

    def __init__(self) -> None:
        self.chunk_size = 0
        self.req_info: list[RequestInfo] = []
        self.asset: Asset = None
        self.stop = False
        self.live_stream_ended = False
        self.pending_futures = []


def ntp_to_unix_timestamp(ntp_ts):
    """Convert an RFC3339 timestamp string to a UNIX timestamp(float)"""
    return (
        datetime.strptime(ntp_ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
    )


class AlertCallbackTool:
    def __init__(
        self,
        name,
        alert_info: AlertInfo,
        stream_handler,
        req_info: RequestInfo = None,
        sse_tool_name: str = "",
    ):
        self.name = name
        self._alert_info = alert_info
        self._stream_handler = stream_handler
        self._req_info = req_info
        self._sse_tool_name = sse_tool_name

    async def notify(self, title: str, message: str, metadata: dict):
        with self._stream_handler._lock:
            alert = RequestInfo.Alert()
            alert.details = metadata["doc"]
            alert.detectedEvents = metadata["events_detected"]
            alert.name = self._sse_tool_name
            alert.alertId = self._alert_info.alert_id
            alert.ntpTimestamp = metadata["start_ntp"]
            alert.streamId = metadata["streamId"]
            alert.alert_time = time.time()
            self._stream_handler._recent_alerts_list.append(alert)
            if self._req_info:
                self._req_info.alerts.append(alert)
        try:
            doc = metadata["doc"]
            events_detected = metadata["events_detected"]
            callback_json = jinja2.Template(self._alert_info.callbackJsonTemplate).render(
                streamId=self._alert_info.liveStreamId,
                alertId=self._alert_info.alert_id,
                ntpTimestamp=metadata["start_ntp"],
                alertText=json.dumps(doc)[1:-1],
                detectedEvents=json.dumps(events_detected),
            )
            headers = (
                {"Authorization": f"Bearer {self._alert_info.callbackToken}"}
                if self._alert_info.callbackToken
                else {}
            )
            if self._alert_info.callbackUrl is not None:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url=self._alert_info.callbackUrl,
                        json=json.loads(callback_json),
                        headers=headers,
                    ) as r:
                        r.raise_for_status()
        except Exception as ex:
            logger.error(
                "Alert callback failed for event(s) '%s' - %s", ", ".join(events_detected), str(ex)
            )


class AlertSseTool:
    def __init__(
        self,
        name,
        sse_tool_name,
        req_info: RequestInfo,
        stream_handler,
        alert_info: AlertInfo,
    ):
        self.name = name
        self._req_info = req_info
        self._sse_tool_name = sse_tool_name
        self._stream_handler = stream_handler
        self._alert_info = alert_info

    async def notify(self, title: str, message: str, metadata: dict):
        alert = RequestInfo.Alert()
        alert.details = metadata["doc"]
        alert.detectedEvents = metadata["events_detected"]
        alert.name = self._sse_tool_name
        alert.alertId = self._alert_info.alert_id
        if self._req_info.is_live:
            alert.ntpTimestamp = metadata["start_ntp"]
        else:
            alert.offset = int(metadata["start_pts"] / 1e9)
        alert.streamId = metadata["streamId"]
        alert.alert_time = time.time()
        self._req_info.alerts.append(alert)
        if self._req_info.is_live:
            with self._stream_handler._lock:
                self._stream_handler._recent_alerts_list.append(alert)


class ViaStreamHandler:
    """VIA Stream Handler"""

    class Metrics:
        def __init__(self) -> None:
            """Initialize the VIA Stream Handler metrics.
            Metrics are based on the prometheus client."""
            self.queries_processed = prom.Gauge(
                "video_file_queries_processed",
                "Number of video file queries whose processing is complete",
            )
            self.queries_pending = prom.Gauge(
                "video_file_queries_pending",
                "Number of video file queries which are queued and yet to be processed",
            )

            self.active_live_streams = prom.Gauge(
                "active_live_streams",
                "Number of live streams whose summaries are being actively generated",
            )

            self.system_uptime = prom.Gauge(
                "system_uptime_seconds", "Number of seconds the via-server system has been running"
            )

            self.stream_fps_histogram = prom.Histogram(
                "stream_fps",
                "FPS measurements per stream",
                buckets=[
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
                ],
            )

            self.decode_latency = prom.Histogram(
                "decode_latency_seconds",
                "Video decode processing latency in seconds",
                buckets=[0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0],
            )

            self.vlm_latency = prom.Histogram(
                "vlm_latency_seconds",
                "VLM processing latency in seconds",
                buckets=[1.0, 3.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0],
            )

            self.add_doc_latency = prom.Histogram(
                "add_doc_latency_seconds",
                "Context manager add_doc processing latency in seconds",
                buckets=[
                    0.00005,
                    0.0001,
                    0.0005,
                    0.001,
                    0.003,
                    0.01,
                    0.03,
                    0.1,
                    0.3,
                    1.0,
                ],
            )

            self.vlm_input_tokens = prom.Histogram(
                "vlm_input_tokens_per_chunk",
                "Number of tokens input to the VLM model per chunk",
                buckets=[10, 20, 50, 100, 200, 500, 1000, 2000],
            )

            self.vlm_output_tokens = prom.Histogram(
                "vlm_output_tokens_per_chunk",
                "Number of tokens output from the VLM model per chunk",
                buckets=[10, 20, 50, 100, 200, 500, 1000, 2000],
            )

            self.e2e_latency_latest = prom.Gauge(
                "e2e_latency_seconds_latest", "Latest end-to-end latency in seconds"
            )

            self.vlm_pipeline_latency_latest = prom.Gauge(
                "vlm_pipeline_latency_seconds_latest",
                "Latest latency of the VLM pipeline processing in seconds",
            )

            self.ca_rag_latency_latest = prom.Gauge(
                "ca_rag_latency_seconds_latest", "Latest CA-RAG processing latency in seconds"
            )

            self.decode_latency_latest = prom.Gauge(
                "decode_latency_seconds_latest", "Latest video decode processing latency in seconds"
            )

            self.vlm_latency_latest = prom.Gauge(
                "vlm_latency_seconds_latest", "Latest VLM processing latency in seconds"
            )

            self.chat_completions_latency = prom.Histogram(
                "chat_completions_latency_seconds",
                "Chat completions API processing latency in seconds",
                buckets=[0.1, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0],
            )

            self.chat_completions_latency_latest = prom.Gauge(
                "chat_completions_latency_seconds_latest",
                "Latest chat completions API processing latency in seconds",
            )

            self.cv_pipeline_latency_latest = prom.Gauge(
                "cv_pipeline_latency_seconds_latest",
                "Latest CV pipeline processing latency in seconds",
            )

            self.asr_pipeline_latency = prom.Histogram(
                "asr_pipeline_latency_seconds",
                "ASR pipeline processing latency in seconds",
                buckets=[0.1, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0],
            )

            self.asr_pipeline_latency_latest = prom.Gauge(
                "asr_pipeline_latency_seconds_latest",
                "Latest ASR pipeline processing latency in seconds",
            )

            self.live_stream_summary_latency = prom.Histogram(
                "live_stream_summary_latency_seconds",
                "Live stream summary processing latency in seconds",
                buckets=[10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 70.0, 100.0, 200, 300, 500, 1000],
            )

            self.live_stream_captions_latency = prom.Histogram(
                "live_stream_captions_latency_seconds",
                "Live stream captions processing latency in seconds",
                buckets=[10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 70.0, 100.0, 200, 300, 500, 1000],
            )

        def unregister(self):
            prom.REGISTRY.unregister(self.queries_processed)
            prom.REGISTRY.unregister(self.queries_pending)
            prom.REGISTRY.unregister(self.active_live_streams)
            prom.REGISTRY.unregister(self.system_uptime)
            prom.REGISTRY.unregister(self.decode_latency)
            prom.REGISTRY.unregister(self.vlm_latency)
            prom.REGISTRY.unregister(self.add_doc_latency)
            prom.REGISTRY.unregister(self.vlm_input_tokens)
            prom.REGISTRY.unregister(self.vlm_output_tokens)
            prom.REGISTRY.unregister(self.decode_latency_latest)
            prom.REGISTRY.unregister(self.vlm_latency_latest)
            prom.REGISTRY.unregister(self.ca_rag_latency_latest)
            prom.REGISTRY.unregister(self.e2e_latency_latest)
            prom.REGISTRY.unregister(self.vlm_pipeline_latency_latest)
            prom.REGISTRY.unregister(self.chat_completions_latency)
            prom.REGISTRY.unregister(self.chat_completions_latency_latest)
            prom.REGISTRY.unregister(self.cv_pipeline_latency_latest)
            prom.REGISTRY.unregister(self.asr_pipeline_latency)
            prom.REGISTRY.unregister(self.asr_pipeline_latency_latest)
            prom.REGISTRY.unregister(self.stream_fps_histogram)
            prom.REGISTRY.unregister(self.live_stream_summary_latency)
            prom.REGISTRY.unregister(self.live_stream_captions_latency)

    def __init__(self, args) -> None:
        """Initialize the VIA Stream Handler"""
        logger.info("Initializing VIA Stream Handler")

        self._lock = RLock()
        self._request_info_map: dict[str, RequestInfo] = {}
        self._notification_llm_api_key = None
        self._notification_llm_params = None

        self._start_time = time.time()
        self._metrics = ViaStreamHandler.Metrics()

        # Start a background thread to update the system uptime metric every 10 seconds
        def update_metrics():
            while True:
                uptime = time.time() - self._start_time
                self._metrics.system_uptime.set(uptime)
                time.sleep(10)

        uptime_thread = Thread(target=update_metrics, daemon=True, name="via-uptime-metrics-thread")
        uptime_thread.start()

        self._live_stream_info_map: dict[str, LiveStreamInfo] = {}
        self._alert_info_map: dict[str, AlertInfo] = {}
        self._recent_alerts_list: list[RequestInfo.Alert] = []
        self._args = args
        if os.environ.get("VSS_LOG_LEVEL"):
            self._args.log_level = os.environ.get("VSS_LOG_LEVEL").upper()
        self._args.cv_pipeline_configs = {"gdino_engine": "", "tracker_config": ""}

        self._via_health_eval = False
        self.first_init = True
        self._start_ca_rag_alert_handler()

        self.default_caption_prompt = self._args.summarization_query
        self._ctx_mgr_pool = []
        self.NUM_CA_RAG_PROCESSES_LAUNCH = 10
        self.num_ctx_mgr = 0
        self.MAX_STREAMS = self._args.max_live_streams

        self._LLMRailsPool = []
        self._rails_config = None
        if not self._args.disable_guardrails:
            # Load guardrails config from file
            from nemoguardrails import RailsConfig

            self._rails_config = RailsConfig.from_path(self._args.guardrails_config)
            # Create LLM Rails pool
            self._create_llm_rails_pool()

        self._args.cv_pipeline_configs["gdino_engine"] = CVPipeline.get_gdino_engine()
        self._args.cv_pipeline_configs["tracker_config"] = CVPipeline.get_tracker_config()
        self._args.cv_pipeline_configs["inference_interval"] = CVPipeline.get_inference_interval()
        logger.info(self._args.cv_pipeline_configs)

        self._vlm_pipeline = VlmPipeline(args.asset_dir, args)

        if not self._args.disable_cv_pipeline:
            try:
                self._cv_pipeline_args = argparse.Namespace()
                if (
                    os.environ.get("NUM_CV_CHUNKS_PER_GPU")
                    and int(os.environ.get("NUM_CV_CHUNKS_PER_GPU")) > 0
                ):
                    setattr(
                        self._cv_pipeline_args,
                        "num_chunks",
                        self._args.num_gpus * int(os.environ.get("NUM_CV_CHUNKS_PER_GPU")),
                    )
                else:
                    setattr(self._cv_pipeline_args, "num_chunks", self._args.num_gpus * 2)
                # setattr(
                #     self._cv_pipeline_args,
                #     "gdino_engine",
                #     "/tmp/via/data/models/gdino-sam/swinb.fp16.engine",
                # )
                setattr(
                    self._cv_pipeline_args,
                    "tracker_config",
                    os.environ.get(
                        "CV_PIPELINE_TRACKER_CONFIG",
                        "/opt/nvidia/via/config/default_tracker_config.yml",
                    ),
                )
                setattr(
                    self._cv_pipeline_args, "fusion_config", "config/MOT_EVAL_config_fusion.yml"
                )
                setattr(self._cv_pipeline_args, "inference_interval", 0)
                self._cv_pipeline = CVPipeline(self._cv_pipeline_args)
            except Exception as e:
                raise (ValueError(f"CV pipeline setup failed. {str(e)}")) from e
        else:
            self._cv_pipeline = None

        if not args.disable_ca_rag:
            try:
                try:
                    config = parse_config(args.ca_rag_config)
                except Exception as e:
                    self.stop(True)
                    raise ValueError(f"{args.ca_rag_config} is not a valid YAML file") from e

                self._ca_rag_config = config
                self._ctx_mgr = True
                os.environ["CA_RAG_ENABLE_WARMUP"] = "true"
                self._create_ctx_mgr_pool(config)

            except Exception as e:
                self.stop(True)
                logger.error(traceback.format_exc())
                raise (ValueError("CA-RAG setup failed.")) from e
        else:
            self._ctx_mgr = None

        # Fix for proper boolean environment variable handling
        health_eval_value = os.environ.get("ENABLE_VIA_HEALTH_EVAL", "").lower()
        self._via_health_eval = health_eval_value in ("true", "1")

        logger.info("Initialized VIA Stream Handler")

    def _create_llm_rails_pool(self):
        from nemoguardrails import LLMRails

        with self._lock:
            # Create LLM Rails pool only if the pool is empty
            if len(self._LLMRailsPool) > 0:
                return
            # Create LLM Rails pool of size MAX_RAILS_INSTANCES with default as 64
            max_rails_instances = int(os.environ.get("MAX_RAILS_INSTANCES", "") or 64)
            max_rails_instances = min(max(max_rails_instances, 1), 256)
            for i in range(max_rails_instances):
                self._LLMRailsPool.append(LLMRails(self._rails_config))

                if i == 0:
                    try:
                        response = self._LLMRailsPool[0].generate(
                            messages=[{"role": "user", "content": "Hi"}]
                        )
                    except Exception as e:
                        logger.error("Error in guardrails: %s", str(e))
                        self.stop(True)
                        raise Exception("Guardrails failed")
                    if "an internal error has occurred" in response["content"]:
                        self.stop(True)
                        raise Exception("Guardrails failed")
        logger.info("Loaded Guardrails")

    def _check_rails(self, prompt: str):
        if self._rails_config:
            with TimeMeasure("Guardrails process"):
                nvtx_guardrails_start = nvtx.start_range(message="Guardrails-", color="blue")
                logger.info("Guardrails in progress")
                rails = None
                while rails is None:
                    with self._lock:
                        if len(self._LLMRailsPool) > 0:
                            rails = self._LLMRailsPool.pop()
                            break
                    time.sleep(0.1)  # Unlock and sleep for 100ms before trying again
                try:
                    response = rails.generate(
                        messages=[{"role": "user", "content": prompt.strip()}]
                    )
                except Exception as e:
                    logger.error("Error in guardrails: %s", str(e))
                    with self._lock:
                        self._LLMRailsPool.append(rails)
                    raise Exception("Guardrails failed")
                # Return the rails to the pool
                with self._lock:
                    self._LLMRailsPool.append(rails)

                nvtx.end_range(nvtx_guardrails_start)

                if response["content"] != "lmm":
                    if "an internal error has occurred" in response["content"]:
                        logger.error("Guardrails failed")
                        raise ViaException("An internal error has occurred")
                    logger.info("Guardrails engaged")
                    raise ViaException(response["content"], "", 400)

                logger.info("Guardrails pass")

    def _create_ctx_mgr_pool(self, config):
        from vss_ctx_rag.context_manager import ContextManager

        with self._lock:
            # Create ctx mgr pool only if the pool is empty
            if len(self._ctx_mgr_pool) > 0:
                return
            if self.num_ctx_mgr >= self.MAX_STREAMS:
                raise ViaException(
                    "Server is already processing maximum number of live streams"
                    f" ({self._args.max_live_streams})",
                    503,
                )
            logger.info(
                f"Context Manager Process Pool is empty, adding new processes from index \
                      {self.num_ctx_mgr}"
            )
            for i in range(self.NUM_CA_RAG_PROCESSES_LAUNCH):
                self._ctx_mgr_pool.append(
                    ContextManager(config=config, process_index=self.num_ctx_mgr)
                )
                os.environ["CA_RAG_ENABLE_WARMUP"] = "false"
                self.num_ctx_mgr = self.num_ctx_mgr + 1
                if self.num_ctx_mgr >= self.MAX_STREAMS:
                    return

    def _start_ca_rag_alert_handler(self):
        app = FastAPI()

        @app.post("/via-alert-callback")
        async def handle_alert(data: dict):
            print(json.dumps(data, indent=2))
            title = data["title"]
            message = data["message"]
            doc_meta = data["metadata"]
            with self._lock:
                alert = self._alert_info_map.get(doc_meta["event_id"], None)
            if alert:
                await alert.alert_tool.notify(title, message, doc_meta)

        config = uvicorn.Config(app, host="127.0.0.1", port=ALERT_CALLBACK_PORT)
        self._ca_rag_alert_handler_server = uvicorn.Server(config)

        self._ca_rag_alert_handler_thread = Thread(
            target=self._ca_rag_alert_handler_server.run,
            daemon=True,
            name="via-ca-rag-alert-handler",
        )
        self._ca_rag_alert_handler_thread.start()

    def _process_output(
        self,
        req_info: RequestInfo,
        is_live_stream_ended: bool,
        chunk_responses: list[VlmChunkResponse],
    ):
        new_response = []
        if (
            not is_live_stream_ended
            and req_info.status != RequestInfo.Status.FAILED
            and not req_info.alert_review
        ):
            try:
                new_response = self._get_aggregated_summary(req_info, chunk_responses)
            except Exception as ex:
                logger.error("".join(traceback.format_exception(ex)))
                if not req_info.is_live:
                    req_info.status = RequestInfo.Status.FAILED
                else:
                    req_info.response += [
                        RequestInfo.Response(
                            chunk_responses[0].chunk.start_ntp,
                            chunk_responses[-1].chunk.end_ntp,
                            "Summarization failed",
                        )
                    ]
            req_info.response += new_response

        if req_info.is_live:
            live_stream_id = req_info.assets[0].asset_id
            if new_response:
                logger.info(
                    "Generated new summary for live stream %s request %s,"
                    " start-time %s end-time %s",
                    live_stream_id,
                    req_info.request_id,
                    new_response[0].start_timestamp,
                    new_response[-1].end_timestamp,
                )
            elif chunk_responses:
                logger.error(
                    "Failed to generate summary for live stream %s request %s,"
                    " start-time %s end-time %s",
                    live_stream_id,
                    req_info.request_id,
                    chunk_responses[0].chunk.start_ntp,
                    chunk_responses[-1].chunk.end_ntp,
                )

            if is_live_stream_ended:
                if live_stream_id in self._live_stream_info_map:
                    lsinfo = self._live_stream_info_map[live_stream_id]
                    lsinfo.live_stream_ended = True
                    if not lsinfo.stop:
                        concurrent.futures.wait(lsinfo.pending_futures)
                req_info.end_time = time.time()
                req_info.progress = 100
                req_info.status = RequestInfo.Status.SUCCESSFUL
                self._metrics.active_live_streams.dec()
                self.stop_via_gpu_monitor(req_info, chunk_responses)
        else:
            if req_info.status == RequestInfo.Status.FAILED:
                logger.info(
                    "Summary generation failed for video file request %s", req_info.request_id
                )
                self.stop_via_gpu_monitor(req_info, chunk_responses)
            else:
                req_info.progress = 100
                req_info.end_time = time.time()
                self.stop_via_gpu_monitor(req_info, chunk_responses)
                req_info.status = RequestInfo.Status.SUCCESSFUL
                cuda.bindings.runtime.cudaProfilerStop()
                nvtx.end_range(req_info.nvtx_summarization_start)
                logger.info(
                    "Summary generated for video file request %s,"
                    " total processing time - %.2f seconds, summary %s",
                    req_info.request_id,
                    req_info.end_time - req_info.start_time,
                    "",
                )

            # Unlock the asset and update metrics
            for asset in req_info.assets:
                asset.unlock()
            # Remove cached embeddings.
            for asset in req_info.assets:
                try:
                    if os.environ.get("VSS_CACHE_VIDEO_EMBEDS", "false").lower() not in [
                        "true",
                        "1",
                    ]:
                        shutil.rmtree(f"{self._args.asset_dir}/{asset.asset_id}/embeddings")
                except Exception:
                    pass
            self._metrics.queries_processed.inc()
            self._metrics.queries_pending.dec()
        req_info.status_event.set()

    def _get_cv_metadata_for_chunk(self, json_file, frame_times):
        cv_meta = []
        if json_file:
            with open(json_file, "r") as f:
                data = json.load(f)

            # Sort data by timestamp once
            sorted_data = sorted(data, key=lambda x: x["timestamp"])
            current_idx = 0

            for frame_time in frame_times:
                frame_time_ns = frame_time * 1e9  # Convert to nanoseconds
                # Continue from last found position instead of searching from start
                while (
                    current_idx < len(sorted_data)
                    and sorted_data[current_idx]["timestamp"] < 0.99 * frame_time_ns
                ):
                    current_idx += 1

                if (
                    current_idx < len(sorted_data)
                    and sorted_data[current_idx]["timestamp"] <= 1.01 * frame_time_ns
                ):
                    cv_meta.append(sorted_data[current_idx])

        return cv_meta

    @staticmethod
    def _remove_segmasks_from_cv_meta(cv_meta_):
        cv_meta = deepcopy(cv_meta_)
        for data in cv_meta:
            for obj in data["objects"]:
                if "misc" not in obj:
                    continue
                for misc in obj["misc"]:
                    misc["seg"] = {}
        return cv_meta

    def _create_video_from_cached_frames(self, req_info):
        def check_ffmpeg():
            """Check if FFmpeg is installed."""
            ffmpeg_path = shutil.which("ffmpeg_for_overlay_video")
            return ffmpeg_path is not None

        cached_frames_dir = f"/tmp/via/cached_frames/{req_info.request_id}"
        video_path = f"{cached_frames_dir}/{req_info.request_id}.mp4"
        images_path = f"{cached_frames_dir}/frame_*.jpg"
        if os.path.exists(cached_frames_dir) and check_ffmpeg():
            # BN TBD : Need better way to handle this
            # calculate frame rate from number of frames and duration
            frame_count = len([f for f in os.listdir(cached_frames_dir) if f.endswith(".jpg")])
            frame_rate = frame_count / (req_info.file_duration / 1e9)
            print(f"Creating cached frames video with frame rate {frame_rate}")
            command = [
                "ffmpeg_for_overlay_video",
                "-hide_banner",
                "-loglevel",
                "error",
                "-framerate",
                str(frame_rate),
                "-pattern_type",
                "glob",
                "-i",
                images_path,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                video_path,
            ]
            try:
                # Execute the command
                subprocess.run(command, check=True)
                print(f"Cached Frames Video created at {video_path}")
                # Now delete all jpg files
                [shutil.os.remove(f) for f in glob.glob(images_path)]
                return video_path
            except subprocess.CalledProcessError as e:
                print(f"FFmpeg command failed: {e}")
                return None
        else:
            return None

    def _on_vlm_chunk_response(self, response: VlmChunkResponse, req_info: RequestInfo):
        """Gather chunks processed by the pipeline and run any further post-processing"""
        # Per-chunk decode latency and OTEL tracing
        if hasattr(response, "decode_start_time") and hasattr(response, "decode_end_time"):
            if (
                response.decode_start_time
                and response.decode_end_time
                and response.decode_end_time > response.decode_start_time
            ):
                decode_latency = response.decode_end_time - response.decode_start_time
                self._metrics.decode_latency.observe(decode_latency)

                # Create OTEL span for decode operation with historical timing
                create_historical_span(
                    f"Decode - Chunk {response.chunk.chunkIdx}",
                    response.decode_start_time,
                    response.decode_end_time,
                    {
                        "chunk_idx": response.chunk.chunkIdx,
                        "decode_latency_ms": decode_latency * 1000,
                        "stream_id": response.chunk.streamId,
                        "operation": "decode",
                    },
                )

        # Per-chunk VLM latency and OTEL tracing
        if hasattr(response, "vlm_start_time") and hasattr(response, "vlm_end_time"):
            if (
                response.vlm_start_time
                and response.vlm_end_time
                and response.vlm_end_time > response.vlm_start_time
            ):
                vlm_latency = response.vlm_end_time - response.vlm_start_time
                self._metrics.vlm_latency.observe(vlm_latency)

                # Create OTEL span for VLM operation with historical timing
                create_historical_span(
                    f"VLM NIM Inference - Chunk {response.chunk.chunkIdx}",
                    response.vlm_start_time,
                    response.vlm_end_time,
                    {
                        "chunk_idx": response.chunk.chunkIdx,
                        "vlm_latency_ms": vlm_latency * 1000,
                        "stream_id": response.chunk.streamId,
                        "vlm_response_length": (
                            len(response.vlm_response) if response.vlm_response else 0
                        ),
                        "operation": "vlm_inference",
                        "model_id": str(response.model_info.id),
                        "model_api_type": response.model_info.api_type,
                    },
                )

        # Per-chunk embedding latency and OTEL tracing
        if hasattr(response, "embed_start_time") and hasattr(response, "embed_end_time"):
            if (
                response.embed_start_time
                and response.embed_end_time
                and response.embed_end_time > response.embed_start_time
            ):
                embed_latency = response.embed_end_time - response.embed_start_time

                # Create OTEL span for embedding operation with historical timing
                create_historical_span(
                    f"Embeddings - Chunk {response.chunk.chunkIdx}",
                    response.embed_start_time,
                    response.embed_end_time,
                    {
                        "chunk_idx": response.chunk.chunkIdx,
                        "embed_latency_ms": embed_latency * 1000,
                        "stream_id": response.chunk.streamId,
                        "operation": "embeddings",
                    },
                )

        # Log and observe token usage per chunk if available
        if hasattr(response, "vlm_stats") and response.vlm_stats:
            input_tokens = response.vlm_stats.get("input_tokens", 0)
            output_tokens = response.vlm_stats.get("output_tokens", 0)
            self._metrics.vlm_input_tokens.observe(input_tokens)
            self._metrics.vlm_output_tokens.observe(output_tokens)

        # Per-chunk ASR latency
        if hasattr(response, "asr_start_time") and hasattr(response, "asr_end_time"):
            if (
                response.asr_start_time
                and response.asr_end_time
                and response.asr_end_time > response.asr_start_time
                and req_info.enable_audio
            ):
                asr_latency = response.asr_end_time - response.asr_start_time
                self._metrics.asr_pipeline_latency.observe(asr_latency)
                self._metrics.asr_pipeline_latency_latest.set(asr_latency)
                # Create OTEL span for ASR operation with historical timing
                create_historical_span(
                    f"ASR NIM Inference - Chunk {response.chunk.chunkIdx}",
                    response.asr_start_time,
                    response.asr_end_time,
                    {
                        "chunk_idx": response.chunk.chunkIdx,
                        "asr_latency_ms": asr_latency * 1000,
                        "stream_id": response.chunk.streamId,
                        "operation": "asr",
                    },
                )

        self._update_stream_fps(response, req_info)
        chunk = response.chunk
        vlm_response = response.vlm_response
        # frame_times = response.frame_times
        if req_info.enable_audio:
            if response.audio_transcript:
                transcript = "Audio transcript: " + response.audio_transcript
            else:
                if chunk is not None:
                    logger.info(
                        "No audio transcription available for chunk at %.2f.", chunk.start_pts / 1e9
                    )
                transcript = "Audio transcript not available."
        else:
            transcript = None

        if response.error:
            if not req_info.is_live:
                # Error was encountered while processing a chunk,
                # mark the request as failed for files
                # For live streams, continue processing new chunks
                req_info.status = RequestInfo.Status.FAILED
                req_info.error_message = response.error
                self._vlm_pipeline.abort_chunks(req_info.assets[0].asset_id)
            logger.error(
                "Encountered error while processing chunk %r of query %s - %s",
                chunk,
                req_info.request_id,
                response.error,
            )
        elif vlm_response is not None:
            if req_info.enable_audio:
                vlm_response = "Video description: " + vlm_response

            logger.debug("%s\n %s", vlm_response, transcript)

            response.vlm_response = vlm_response
            # Add the chunk VLM response to the milvus DB
            if req_info._ctx_mgr:
                # Along with chunk, add cv metadata for the chunk
                # get cv metadata present in file chunk.cv_metadata_json_file
                # for duration chunk.start_pts to chunk.end_pts
                cv_meta = chunk.cached_frames_cv_meta
                cv_meta_str = json.dumps(self._remove_segmasks_from_cv_meta(cv_meta))
                if len(cv_meta_str) > MAX_MILVUS_STRING_LEN:
                    cv_meta_str = cv_meta_str[:MAX_MILVUS_STRING_LEN]
                    logger.warning(
                        "CV metadata length exceeds max milvus string length, " "truncating to %d",
                        MAX_MILVUS_STRING_LEN,
                    )
                print(
                    f"chunkIdx = {chunk.chunkIdx}  chunk.start_pts = {chunk.start_pts} \
                      chunk.end_pts = {chunk.end_pts} CV metadata length = {len(cv_meta)}"
                )
                # Since cv metadata is getting  attached seperately to the context manager,
                # set cached_frames_cv_meta to empty string in chunk
                chunk.cached_frames_cv_meta = ""
                with TimeMeasure("Context Manager - Add Doc"):
                    add_doc_start_time = time.time()
                    req_info._ctx_mgr.add_doc(
                        vlm_response,
                        doc_i=chunk.chunkIdx * 2 if req_info.enable_audio else chunk.chunkIdx,
                        doc_meta=(
                            vars(chunk)
                            | {
                                "uuid": req_info.stream_id,
                                "cv_meta": cv_meta_str,
                                "camera_id": req_info.camera_id,
                            }
                        ),
                        callback=lambda output: logger.debug(
                            f"Summary till now: {output.result()}"
                        ),
                    )

                    if transcript is not None:  # enable audio

                        if response.audio_transcript:
                            logger.info("Adding audio transcript for chunk %r", chunk)

                        req_info._ctx_mgr.add_doc(
                            transcript,
                            doc_i=chunk.chunkIdx * 2 + 1,
                            doc_meta=(
                                vars(chunk)
                                | {
                                    "uuid": req_info.stream_id,
                                    "cv_meta": cv_meta_str,
                                    "camera_id": req_info.camera_id,
                                }
                            ),
                            callback=lambda output: logger.debug(
                                f"Summary till now: {output.result()}"
                            ),
                        )
                    if os.environ.get("VSS_POST_PROCESS_ON_EACH_DOC_ADD", "false").lower() in (
                        "true",
                        "1",
                    ):
                        req_info._ctx_mgr.call(
                            {
                                "ingestion_function": {
                                    "uuid": req_info.stream_id,
                                    "camera_id": req_info.camera_id,
                                },
                            }
                        )

                    if req_info.last_chunk is None or req_info.last_chunk.chunkIdx < chunk.chunkIdx:
                        req_info.last_chunk = chunk
                    add_doc_end_time = time.time()
                    response.add_doc_start_time = add_doc_start_time
                    response.add_doc_end_time = add_doc_end_time
                    # Observe add_doc latency metrics
                    if (
                        add_doc_end_time
                        and add_doc_start_time
                        and add_doc_end_time > add_doc_start_time
                    ):
                        add_doc_latency = add_doc_end_time - add_doc_start_time
                        self._metrics.add_doc_latency.observe(add_doc_latency)

        if req_info.is_live:
            live_stream_id = req_info.assets[0].asset_id
            lsinfo = self._live_stream_info_map[live_stream_id]

            if not response.is_live_stream_ended:
                logger.info(
                    "Generated new response for live-stream %s, query %s, chunk %r, summary %s",
                    live_stream_id,
                    req_info.request_id,
                    chunk,
                    vlm_response,
                )
                req_info.processed_chunk_list.append(response)
                req_info.chunk_count += 1

            req_info.processed_chunk_list.sort(key=lambda x: x.chunk.chunkIdx)

            gathered_chunks = 0
            gathered_chunks_total_duration = 0

            if req_info.summary_duration > 0:
                summ_batch_size = req_info.summary_duration // req_info.chunk_size

            if req_info.processed_chunk_list:
                curIdx = req_info.processed_chunk_list[0].chunk.chunkIdx
                gathered_chunks = 1

                for processed_chunk in req_info.processed_chunk_list[1:]:
                    if processed_chunk.chunk.chunkIdx != curIdx + 1:
                        break
                    curIdx += 1
                    gathered_chunks += 1
                    if (req_info.summary_duration > 0) and (gathered_chunks == summ_batch_size):
                        break

            # Calculate the total duration of gathered chunks
            gathered_chunks_total_duration = (
                ntp_to_unix_timestamp(
                    req_info.processed_chunk_list[gathered_chunks - 1].chunk.end_ntp
                )
                - ntp_to_unix_timestamp(req_info.processed_chunk_list[0].chunk.start_ntp)
                if req_info.processed_chunk_list
                else 0
            )

            logger.info(
                "Gathered %d chunks, total chunk duration \
                    is %.2f sec for query %s, summary duration %d sec",
                gathered_chunks,
                gathered_chunks_total_duration,
                req_info.request_id,
                req_info.summary_duration,
            )

            if (
                (
                    req_info.summary_duration == 0
                    or req_info._ctx_mgr is None
                    or (
                        (req_info.summary_duration > 0)
                        and (gathered_chunks == req_info.summary_duration // req_info.chunk_size)
                    )
                    or response.is_live_stream_ended
                )
                and gathered_chunks > 0
                and not lsinfo.stop
            ):
                if response.is_live_stream_ended and req_info.last_chunk is not None:
                    last_chunk = req_info.last_chunk.model_copy(deep=True)
                    last_chunk.start_ntp = last_chunk.end_ntp
                    last_chunk.start_ntp_float = last_chunk.end_ntp_float
                    last_chunk.start_pts = last_chunk.end_pts
                    last_chunk.chunkIdx = last_chunk.chunkIdx + 1
                    last_chunk.is_last = True
                    last_meta = vars(last_chunk)
                    last_meta["cv_meta"] = ""
                    last_meta["request_id"] = req_info.request_id
                    last_meta["asset_dir"] = self._args.asset_dir
                    last_meta["camera_id"] = req_info.camera_id
                    last_meta["uuid"] = req_info.stream_id
                    req_info._ctx_mgr.add_doc(
                        ".",
                        doc_i=(
                            last_chunk.chunkIdx * 2
                            if req_info.enable_audio
                            else last_chunk.chunkIdx
                        ),
                        doc_meta=last_meta,
                    )
                # Summary Duration not specified or total duration is greater than summary duration.
                logger.info(
                    "Generating summary for live stream %s request %s with asset id %s",
                    live_stream_id,
                    req_info.request_id,
                    req_info.stream_id,
                )

                if len(lsinfo.pending_futures) > 1:
                    logger.warning(
                        "Possible high load on the system detected. This may result in higher"
                        " response times. Try reducing number of streams or increasing the chunk"
                        " size or tuning the CA-RAG config for reduced latency."
                    )

                fut = req_info._output_process_thread_pool.submit(
                    self._process_output,
                    req_info,
                    False,
                    req_info.processed_chunk_list[:gathered_chunks],
                )
                lsinfo.pending_futures.append(fut)

                def handle_future_done(fut: concurrent.futures.Future):
                    if fut.cancelled():
                        return
                    if fut.exception():
                        logger.error("".join(traceback.format_exception(fut.exception())))

                fut.add_done_callback(handle_future_done)
                fut.add_done_callback(lsinfo.pending_futures.remove)
                req_info.processed_chunk_list = req_info.processed_chunk_list[gathered_chunks:]

            if response.is_live_stream_ended:
                if lsinfo.stop:
                    req_info.status = RequestInfo.Status.STOPPING
                    for fut in lsinfo.pending_futures:
                        fut.cancel()

                # Queue that the request be marked completed
                # once all pending aggregation requests are completed.
                fut = req_info._output_process_thread_pool.submit(
                    self._process_output, req_info, True, []
                )
                fut.add_done_callback(
                    lambda fut, tpool=req_info._output_process_thread_pool: tpool.shutdown(
                        wait=False
                    )
                )
            return

        # Cache the processed chunk of a file
        req_info.processed_chunk_list.append(response)
        req_info.progress = 90 * len(req_info.processed_chunk_list) / req_info.chunk_count
        logger.info(
            "Processed chunk for query %s, total chunks %d, processed chunks %d, chunk %r,",
            req_info.request_id,
            req_info.chunk_count,
            len(req_info.processed_chunk_list),
            chunk,
        )

        if len(req_info.processed_chunk_list) == req_info.chunk_count:
            # All chunks of file processed
            nvtx.end_range(req_info.nvtx_vlm_start)
            cur_time = time.time()

            self._finalize_stream_fps_tracking(req_info)

            # if OSD pipeline was executed, create a video from all the cached frames
            if req_info.enable_cv_pipeline:
                self.osd_output_video_file = self._create_video_from_cached_frames(req_info)

            if req_info.status == RequestInfo.Status.FAILED:
                self._vlm_pipeline.abort_chunks_done(req_info.assets[0].asset_id)
            else:
                logger.info(
                    "Processed all chunks for query %s, VLM pipeline time %.2f sec",
                    req_info.request_id,
                    cur_time - req_info.start_time,
                )
                if not req_info.alert_review:
                    logger.info("Generating summary for request %s", req_info.request_id)

                # Always update vlm_pipeline_latency metric (decoupled from health eval)
                latency = cur_time - req_info.start_time
                if latency is not None and latency > 0:
                    self._metrics.vlm_pipeline_latency_latest.set(latency)

                if req_info._health_summary:
                    req_info._health_summary.vlm_pipeline_latency = latency

                if req_info.vlm_pipeline_span:
                    try:
                        req_info.vlm_pipeline_span.end()
                    except Exception as e:
                        logger.error(f"Failed to end vlm_pipeline_latency span: {e}")

            # Queue for getting the aggregated summary
            if req_info._output_process_thread_pool:
                req_info._output_process_thread_pool.submit(
                    self._process_output, req_info, False, req_info.processed_chunk_list
                )
                req_info._output_process_thread_pool.shutdown(wait=False)

    def _trigger_query(self, req_info: RequestInfo, start_time: float = None):
        """Trigger a query on a file"""
        from file_splitter import FileSplitter

        logger.info("Triggering oldest queued query %s", req_info.request_id)
        req_info.status = RequestInfo.Status.PROCESSING
        req_info.start_time = start_time if start_time else time.time()

        if is_tracing_enabled():
            tracer = get_tracer()
            self._via_health_eval = True
            if tracer:
                req_info._e2e_span = tracer.start_span("VIA Pipeline End-to-End")
                req_info._e2e_span.set_attribute("request_id", req_info.request_id)
                req_info._e2e_span.set_attribute("stream_id", req_info.stream_id)
                req_info._e2e_span.set_attribute("is_live", req_info.is_live)

                req_info.vlm_pipeline_span = tracer.start_span("VLM Pipeline Latency")
                req_info.vlm_pipeline_span.set_attribute("request_id", req_info.request_id)
                req_info.vlm_pipeline_span.set_attribute("stream_id", req_info.stream_id)
                req_info.vlm_pipeline_span.set_attribute("is_live", req_info.is_live)

        # Start FPS tracking for this stream
        self._start_stream_fps_tracking(req_info)

        # Trigger collecting VIA GPU health metrics
        self.start_via_gpu_monitor(req_info)

        if req_info._ctx_mgr:
            ca_rag_config = self.update_ca_rag_config(req_info)
            logger.debug(f"Updating Context Manager with config {ca_rag_config}")
            req_info._ctx_mgr.configure(config=ca_rag_config)
        else:
            logger.debug("Request does not contain Context Manager")

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
        enable_dense_caption_frames = bool(os.environ.get("ENABLE_DENSE_CAPTION_FRAMES", False))
        saved_responses = {}

        if enable_dense_caption:
            # Get dense caption from file if present
            saved_dc_file = req_info.file + ".dc.json"
            if os.access(saved_dc_file, os.R_OK):
                logger.info(f"Saved DC available {saved_dc_file}")
                req_info_deserialized = DCSerializer.from_json(saved_dc_file)
                req_info.chunk_count = len(req_info_deserialized.processed_chunk_list)
                for vlm_response in req_info_deserialized.processed_chunk_list:
                    self._on_vlm_chunk_response(vlm_response, req_info)
                return

        if enable_dense_caption_frames:
            # Get dense caption from file if present
            saved_dc_file = req_info.file + ".dc.json"
            if os.access(saved_dc_file, os.R_OK):
                logger.info(
                    f"Saved DC available {saved_dc_file}, regenerating dense caption frames."
                )
                req_info_deserialized = DCSerializer.from_json(saved_dc_file)
                # Create a lookup dictionary for saved responses by chunk index
                for vlm_response in req_info_deserialized.processed_chunk_list:
                    vlm_response.chunk.streamId = req_info.stream_id
                    saved_responses[vlm_response.chunk.chunkIdx] = vlm_response

        def _on_new_chunk(chunk: ChunkInfo, saved_responses=None):
            """Callback for when a new chunk is created"""
            if chunk is None:
                return
            chunk.streamId = req_info.stream_id
            chunk.cv_metadata_json_file = req_info.cv_metadata_json_file
            req_info.chunk_count += 1

            saved_response = (
                saved_responses.get(chunk.chunkIdx) if enable_dense_caption_frames else None
            )

            # If we have a saved dense caption response, use it directly
            if saved_response is not None and enable_dense_caption_frames:
                logger.info(f"Using saved dense caption for chunk {chunk.chunkIdx}")
                self._vlm_pipeline.enqueue_chunk(
                    chunk,
                    lambda _, req_info=req_info: self._on_vlm_chunk_response(
                        saved_response, req_info
                    ),
                    req_info.vlm_request_params,
                    req_info.num_frames_per_chunk,
                    req_info.vlm_input_width,
                    req_info.vlm_input_height,
                    req_info.enable_audio,
                    req_info.request_id,
                    video_codec,
                    decode_only=True,
                )
            else:
                # No saved response, enqueue the chunk for normal VLM processing
                self._vlm_pipeline.enqueue_chunk(
                    chunk,
                    lambda response, req_info=req_info: self._on_vlm_chunk_response(
                        response, req_info
                    ),
                    req_info.vlm_request_params,
                    req_info.num_frames_per_chunk,
                    req_info.vlm_input_width,
                    req_info.vlm_input_height,
                    req_info.enable_audio,
                    req_info.request_id,
                    video_codec,
                )

        nvtx_file_split_start = nvtx.start_range(
            message="File Splitting-" + str(req_info.request_id), color="blue"
        )
        # Create virtual file chunks
        FileSplitter(
            paths_string,
            FileSplitter.SplitMode.SEEK,
            req_info.chunk_size,
            start_pts=int(req_info.start_timestamp * 1e9),
            end_pts=int(req_info.end_timestamp * 1e9),
            sliding_window_overlap_sec=req_info.chunk_overlap_duration,
            media_file_info=req_info.media_file_info,
            on_new_chunk=lambda chunk: _on_new_chunk(chunk, saved_responses),
        ).split()
        nvtx.end_range(nvtx_file_split_start)

        # No chunks were created. Mark the request completed and trigger next query if queued
        if req_info.chunk_count == 0:
            req_info.status = RequestInfo.Status.SUCCESSFUL
            req_info.progress = 100
            req_info.end_time = time.time()
            req_info.response = []
            self._finalize_stream_fps_tracking(req_info)
            req_info.status_event.set()
        req_info.nvtx_vlm_start = nvtx.start_range(
            message="VLM Pipeline-" + str(req_info.request_id), color="green"
        )

    def get_ctx_mgr(self, assets: list[Asset]) -> None:
        """
        Return a ContextManager associated with the given assets.
        """
        with self._lock:
            for _, request_info in self._request_info_map.items():
                req_matches = True
                for asset in assets:
                    if asset not in request_info.assets:
                        req_matches = False
                        break
                if req_matches:
                    # Remove old data for the same asset
                    if request_info.enable_chat:
                        request_info._ctx_mgr.reset(
                            {
                                "summarization": {"uuid": request_info.stream_id},
                                "retriever_function": {"uuid": request_info.stream_id},
                                "ingestion_function": {"uuid": request_info.stream_id},
                            }
                        )
                    elif request_info.summarize:
                        request_info._ctx_mgr.reset(
                            {
                                "summarization": {"uuid": request_info.stream_id},
                            }
                        )
                    return request_info._ctx_mgr
            # If ctx mgr not found in request info map
            logger.info(f"Getting new Context Manager for {assets[0].asset_id}")
            return self._ctx_mgr_pool.pop()

    def remove_request_ids(self, assets: list[Asset]) -> None:
        """
        Remove request infos matching the asset list
        """
        request_id_list = []
        with self._lock:
            for req_id, request_info in self._request_info_map.items():
                req_matches = True
                for asset in assets:
                    if asset not in request_info.assets:
                        req_matches = False
                        break
                if req_matches:
                    request_id_list.append(req_id)
            for req_id in request_id_list:
                del self._request_info_map[req_id]

    def get_request_infos(self, assets: list[Asset]) -> list[RequestInfo]:
        """
        Returns a list of request_infos associated with the given assets.

        Args:
            assets (list[Asset]): A list of Asset objects to find the request_infos for.

        Returns:
            list[RequestInfo]: A list of request_infos associated with the assets
        """
        with self._lock:
            request_infos = []
            for asset in assets:
                for request_id, request_info in self._request_info_map.items():
                    if asset in request_info.assets:
                        request_infos.append(request_info)
        return request_infos

    def qa(
        self,
        assets: list[Asset],
        messages: str = None,
        generation_config=None,
        start_timestamp=None,
        end_timestamp=None,
        highlight=False,
    ):
        try:
            request_infos = self.get_request_infos(assets)
            if len(request_infos) > 1:
                logger.info(
                    f"Multiple video processing requests identified for same assets;"
                    f" using request to identify the Graph database: {str(request_infos[-1])}"
                )
            if len(request_infos) >= 1:
                if request_infos[-1].enable_chat is False:
                    return (
                        "Chat functionality disabled for request id: "
                        + request_infos[-1].request_id
                    )

                # Run guardrails on the user supplied prompt
                try:
                    self._check_rails(messages)
                except ViaException as ex:
                    return ex.message
                except Exception:
                    return "Guardrails failed."

                if highlight:
                    highlight_query = process_highlight_request(messages)
                    result = request_infos[-1]._ctx_mgr.call(
                        {
                            "retriever_function": {
                                "question": highlight_query,
                                "is_live": request_infos[-1].is_live,
                                "is_last": False,
                            }
                        }
                    )
                    logger.debug(f"Q&A: result object is {result}")

                    # Handle the response
                    retriever_result = result["retriever_function"]

                    # Check if there's an error in the result
                    if "error" in retriever_result:
                        logger.error(f"Error in retriever function: {retriever_result['error']}")
                        return retriever_result["error"]

                    # Get the response if no error
                    if "response" not in retriever_result:
                        logger.error("No response found in retriever result")
                        return "Couldn't Produce Highlights. Please try again."

                    response = retriever_result["response"]
                    if response == "No matching scenarios found":
                        return response
                    try:
                        # Validate that the response is valid JSON
                        json.loads(response)
                        return response
                    except json.JSONDecodeError as e:
                        logger.error(f"Error decoding JSON: {str(e)}")
                        return "Couldn't Produce Highlights. Please try again."

                else:
                    result = request_infos[-1]._ctx_mgr.call(
                        {
                            "retriever_function": {
                                "question": messages,
                                "is_live": request_infos[-1].is_live,
                                "is_last": False,
                            }
                        }
                    )
                    logger.debug(f"Q&A: result object is {result}")

                    retriever_result = result["retriever_function"]

                    if "error" in result and result["error"]:
                        return result["error"]

                    if "response" not in retriever_result:
                        logger.error("No response found in retriever result")
                        return "An internal error occurred"

                    return retriever_result["response"]
            else:
                return (
                    "Chat functionality disabled; "
                    "please call /summarize API with enable_chat: True;"
                )
        except Exception as e:
            error_message = f"An error occurred: {str(e)} - {e.__class__.__name__}"
            logger.error(error_message)
            raise ViaException(error_message)

    def summarize(
        self,
        assets: list[Asset],
        query: SummarizationQuery,
    ):
        """Run a summarization query on a file"""
        # Enable summarization if summarization config is enabled  OR API passes enable flag
        # Enable summarization if none provided
        if self._ctx_mgr:
            summarize_enable = (
                "summarization" in self._ca_rag_config["context_manager"]["functions"]
            )
            if query.summarize is None:
                query.summarize = summarize_enable
        cuda.bindings.runtime.cudaProfilerStart()
        if not query.prompt:
            query.prompt = self.default_caption_prompt

        if self._args.vlm_model_type == VlmModelType.COSMOS_REASON2:
            query.prompt = query.prompt + ". Make sure the answer contain correct timestamps."
        if query.enable_cv_metadata and self._args.vlm_model_type == VlmModelType.COSMOS_REASON1:
            # Enable reasoning for Cosmos Reason1 to extract SoM metadata
            if os.environ.get("VSS_FORCE_CR1_REASONING_FOR_CV_METADATA", "true").lower() in [
                "true",
                "1",
            ]:
                query.enable_reasoning = True
                query.max_tokens = max(query.max_tokens, 1024)

        return self.query(
            assets=assets,
            query=query,
            is_summarization=True,
        )

    def query(
        self,
        assets: list[Asset],
        query: SummarizationQuery,
        is_summarization=False,
        pregenerated_cv_metadata_json_file="",
        skip_guardrails=False,
        skip_ca_rag=False,
    ):
        """Run a query on a file"""

        if self._args.disable_ca_rag is True and (query.enable_chat is True):
            raise ViaException("CA-RAG must be enabled to use chat feature", "BadParameter", 400)

        if self._args.enable_audio is False and (query.enable_audio is True):
            raise ViaException(
                "Audio ASR is not supported by this server instance", "BadParameter", 400
            )
        if (query.vlm_input_width > 0 and query.vlm_input_width < 16) or (
            query.vlm_input_height > 0 and query.vlm_input_height < 16
        ):
            raise ViaException(
                "vlm_input_width and vlm_input_height must be greater than or equal to 16",
                "BadParameter",
                400,
            )

        try:
            media_file_info = MediaFileInfo.get_info(assets[0].path)
            # Get file duration
            file_duration = media_file_info.video_duration_nsec
        except gi.repository.GLib.GError as ex:
            raise ViaException(ex.message, "FailedRequest", 400)

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
            raise ViaException(
                "chunkOverlapDuration must be less than chunkDuration", "BadParameter", 400
            )

        # Run guardrails on the user supplied prompt
        if not skip_guardrails:
            self._check_rails(query.prompt)

        vlm_generation_config = {}
        # Extract user specified llm output parameters
        if query.max_tokens is not None:
            vlm_generation_config["max_new_tokens"] = query.max_tokens
        if query.top_p is not None:
            vlm_generation_config["top_p"] = query.top_p
        if query.top_k is not None:
            vlm_generation_config["top_k"] = query.top_k
        if query.temperature is not None:
            vlm_generation_config["temperature"] = query.temperature
        if query.seed is not None:
            vlm_generation_config["seed"] = query.seed
        if query.enable_reasoning:
            vlm_generation_config["enable_reasoning"] = query.enable_reasoning
        if query.system_prompt:
            vlm_generation_config["system_prompt"] = query.system_prompt

        # Create a RequestInfo object and populate it
        req_info = RequestInfo()
        req_info.file = assets[0].path
        req_info.media_file_info = media_file_info
        req_info.chunk_size = query.chunk_duration
        req_info.is_summarization = is_summarization
        req_info.vlm_request_params.vlm_prompt = query.prompt
        req_info.vlm_request_params.vlm_generation_config = vlm_generation_config
        req_info.assets = assets
        req_info.stream_id = req_info.assets[0].asset_id
        req_info.camera_id = req_info.assets[0].camera_id
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
        req_info.summary_aggregation_prompt = query.summary_aggregation_prompt
        req_info.caption_summarization_prompt = query.caption_summarization_prompt
        req_info._output_process_thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        req_info.graph_db = query.graph_db
        req_info.enable_cot = query.enable_cot
        req_info.enable_image = query.enable_image
        req_info.summarize = query.summarize
        req_info.enable_chat = query.enable_chat
        req_info.enable_chat_history = query.enable_chat_history
        req_info.num_frames_per_chunk = query.num_frames_per_chunk
        req_info.summarize_batch_size = query.summarize_batch_size
        req_info.rag_top_k = query.rag_top_k
        req_info.rag_batch_size = query.rag_batch_size
        req_info.vlm_input_width = query.vlm_input_width
        req_info.vlm_input_height = query.vlm_input_height
        req_info.enable_audio = query.enable_audio
        req_info.user_specified_collection_name = query.collection_name
        req_info.custom_metadata = query.custom_metadata
        req_info.delete_external_collection = query.delete_external_collection
        # FIXME(shaunakg/slakhotia): How do we handle this in the new design?
        req_info.nvtx_summarization_start = nvtx.start_range(
            message="Summarization-" + str(req_info.request_id), color="blue"
        )
        if not self._args.disable_ca_rag and not skip_ca_rag:
            with self._lock:
                self._create_ctx_mgr_pool(self._ca_rag_config)
                req_info._ctx_mgr = self.get_ctx_mgr(req_info.assets)
            try:
                config = deepcopy(self._ca_rag_config)
                config["context_manager"]["uuid"] = req_info.stream_id
                req_info._ctx_mgr.configure(config=config)
            except Exception as ex:
                logger.error(traceback.format_exc())
                logger.error("Query failed for %s - %s", req_info.request_id, str(ex))
                return req_info.request_id
            # Reset the context manager for the first time
            if (
                self.first_init
                and req_info.enable_chat
                and os.environ.get("VSS_DISABLE_DB_RESET_ON_INIT", "false").lower()
                not in ["true", "1"]
            ):
                self.first_init = False
                req_info._ctx_mgr.reset(
                    {
                        "summarization": {"erase_db": True},
                        "retriever_function": {},
                        "ingestion_function": {"erase_db": True},
                    }
                )

        # Lock the asset(s) so that it cannot be deleted while it is being used.
        for asset in req_info.assets:
            asset.lock()
        req_info.summarize_top_p = query.summarize_top_p
        req_info.summarize_temperature = query.summarize_temperature
        req_info.summarize_max_tokens = query.summarize_max_tokens
        req_info.chat_top_p = query.chat_top_p
        req_info.chat_temperature = query.chat_temperature
        req_info.chat_max_tokens = query.chat_max_tokens
        req_info.notification_top_p = query.notification_top_p
        req_info.notification_temperature = query.notification_temperature
        req_info.notification_max_tokens = query.notification_max_tokens

        req_info.chunk_overlap_duration = query.chunk_overlap_duration

        req_info.queue_time = time.time()
        # Adding the request info to the request info map
        with self._lock:
            self._request_info_map[req_info.request_id] = req_info

        # Add the request to the pending queue
        self._metrics.queries_pending.inc()

        req_info.enable_cv_pipeline = query.enable_cv_metadata
        req_info.cv_metadata_json_file = pregenerated_cv_metadata_json_file

        if self._cv_pipeline and req_info.enable_cv_pipeline:
            print("Executing CV pipeline")
            cv_pipeline_start_time = time.time()

            def _on_cv_pipeline_done(json_fused_file, req_info):
                cv_pipeline_end_time = time.time()
                cv_pipeline_latency = cv_pipeline_end_time - cv_pipeline_start_time

                # Record CV pipeline latency metrics
                self._metrics.cv_pipeline_latency_latest.set(cv_pipeline_latency)

                print(
                    f"Finished processing CV pipeline for {req_info.file} \
                        and output is in {json_fused_file}"
                )
                print(f"Time taken by cv pipeline in sec = {cv_pipeline_latency}")

                # OTEL trace for cv pipeline
                create_historical_span(
                    "CV Pipeline",
                    cv_pipeline_start_time,
                    cv_pipeline_end_time,
                    {"operation": "cv_pipeline"},
                )

                # Add the output json file to req_info
                req_info.cv_metadata_json_file = json_fused_file
                self._trigger_query(req_info, cv_pipeline_start_time)

            self._cv_pipeline.process_cv_pipeline(
                req_info.file,
                lambda json_fused_file, req_info=req_info: _on_cv_pipeline_done(
                    json_fused_file, req_info
                ),
                text_prompt=query.cv_pipeline_prompt,
                output_file="",
            )
        else:
            self._trigger_query(req_info, None)

        return req_info.request_id

    def generate_vlm_captions(self, assets: list[Asset], query: SummarizationQuery, is_rtsp=False):
        """Run VLM captions generation on a file or RTSP stream.
        This reuses the query function since they have identical logic.
        """
        # For VLM captions, we skip CA-RAG to get individual chunk responses
        # and set summarize=False to avoid summarization
        query.summarize = False

        # Set default prompt if not provided
        if not query.prompt:
            query.prompt = self.default_caption_prompt

        # Modify prompt based on enable_reasoning parameter
        if query.enable_reasoning:
            logger.debug("Reasoning is enabled in generate_vlm_captions API")

        if is_rtsp:
            # Handle RTSP stream VLM captions by reusing add_rtsp_stream_query
            if len(assets) != 1:
                raise ViaException(
                    "RTSP VLM captions require exactly one asset", "BadParameter", 400
                )

            asset = assets[0]

            # Validate input dimensions
            if (query.vlm_input_width > 0 and query.vlm_input_width < 16) or (
                query.vlm_input_height > 0 and query.vlm_input_height < 16
            ):
                raise ViaException(
                    "vlm_input_width and vlm_input_height must be greater than or equal to 16",
                    "BadParameter",
                    400,
                )

            live_stream_info = self._live_stream_info_map[asset.asset_id]
            if len(live_stream_info.req_info) > 0:
                raise ViaException(
                    "Live stream already has query "
                    f"'{live_stream_info.req_info[0].request_id}' running."
                    " Update or stop the same query.",
                    "BadParameters",
                    400,
                )

            # Run guardrails on the user supplied prompt
            self._check_rails(query.prompt)

            # Create VLM captions request directly without using add_rtsp_stream_query
            # to avoid summary_duration validation issues
            req_id = self._create_rtsp_vlm_captions_request(asset, query)
            return req_id
        else:
            # Handle file-based VLM captions
            req_id = self.query(
                assets=assets,
                query=query,
                is_summarization=False,
                skip_ca_rag=True,
            )

            return req_id

    def _create_rtsp_vlm_captions_request(self, asset: Asset, query: SummarizationQuery):
        """Create a VLM captions request for RTSP streams without requiring summary_duration."""

        # Create a RequestInfo object and populate it for VLM captions
        req_info = RequestInfo()
        req_info.file = asset.path
        req_info.stream_id = asset.asset_id
        req_info.chunk_size = query.chunk_duration
        req_info.is_summarization = False  # VLM captions are not summarization
        req_info.vlm_request_params.vlm_prompt = query.prompt
        req_info.is_live = True
        req_info.status = RequestInfo.Status.PROCESSING
        req_info.summary_duration = 0  # VLM captions don't use summary_duration
        req_info.start_time = time.time()
        req_info.queue_time = time.time()
        req_info.assets = [asset]
        req_info.summary_aggregation_prompt = query.summary_aggregation_prompt
        req_info.caption_summarization_prompt = query.caption_summarization_prompt
        req_info._output_process_thread_pool = self._create_named_thread_pool(
            max_workers=1, prefix=f"vss-processor-{req_info.request_id[:8]}"
        )

        # VLM captions specific settings
        req_info.summarize = False  # Always False for VLM captions
        req_info.enable_chat = query.enable_chat
        req_info.enable_chat_history = query.enable_chat_history
        req_info.num_frames_per_chunk = query.num_frames_per_chunk
        req_info.vlm_input_width = query.vlm_input_width
        req_info.vlm_input_height = query.vlm_input_height

        # VLM generation config
        vlm_generation_config = {}
        if query.max_tokens is not None:
            vlm_generation_config["max_new_tokens"] = query.max_tokens
        if query.top_p is not None:
            vlm_generation_config["top_p"] = query.top_p
        if query.top_k is not None:
            vlm_generation_config["top_k"] = query.top_k
        if query.temperature is not None:
            vlm_generation_config["temperature"] = query.temperature
        if query.seed is not None:
            vlm_generation_config["seed"] = query.seed
        if query.enable_reasoning:
            vlm_generation_config["enable_reasoning"] = query.enable_reasoning
        if query.system_prompt:
            vlm_generation_config["system_prompt"] = query.system_prompt
        req_info.vlm_request_params.vlm_generation_config = vlm_generation_config

        # Add the request to the request info map
        with self._lock:
            self._request_info_map[req_info.request_id] = req_info

        # Add to live stream info
        live_stream_info = self._live_stream_info_map[asset.asset_id]
        live_stream_info.req_info.append(req_info)
        self._metrics.active_live_streams.inc()

        # Trigger collecting VIA GPU health metrics
        self.start_via_gpu_monitor(req_info)

        req_info.enable_cv_pipeline = query.enable_cv_metadata

        # Add to VLM pipeline for processing
        self._vlm_pipeline.add_live_stream(
            asset.asset_id,
            asset.path,
            live_stream_info.chunk_size,
            lambda response, req_info=req_info: self._on_vlm_chunk_response(response, req_info),
            req_info.vlm_request_params,
            username=asset.username,
            password=asset.password,
            num_frames_per_chunk=query.num_frames_per_chunk,
            vlm_input_width=query.vlm_input_width,
            vlm_input_height=query.vlm_input_height,
            enable_cv_pipeline=(self._cv_pipeline and req_info.enable_cv_pipeline),
            cv_pipeline_text_prompt=query.cv_pipeline_prompt,
        )

        return req_info.request_id

    def get_recent_alert(self, live_stream_id: str):
        with self._lock:
            # Remove alerts older than 1 hour
            current_time = time.time()
            one_hour_ago = current_time - 3600
            self._recent_alerts_list = [
                alert for alert in self._recent_alerts_list if alert.alert_time > one_hour_ago
            ]

            # Filter alerts by live_stream_id if specified
            if live_stream_id:
                return [
                    alert for alert in self._recent_alerts_list if alert.streamId == live_stream_id
                ]
            return self._recent_alerts_list

    def start_via_gpu_monitor(self, req_info):
        # Start collecting VIA GPU health metrics if enabled
        if self._via_health_eval and req_info._monitor is None:
            logger.info(f"Starting GPUMonitor for request {req_info.request_id}")
            req_info._monitor = GPUMonitor()
            req_info._monitor.start_recording_nvdec(
                interval_in_seconds=0.2,
                nvdec_plot_file_name="/tmp/via-logs/via_nvdec_usage_"
                + str(req_info.request_id)
                + ".csv",
            )
            req_info._monitor.start_recording_gpu_usage(
                interval_in_seconds=0.2,
                gpu_plot_file_name="/tmp/via-logs/via_gpu_usage_"
                + str(req_info.request_id)
                + ".csv",
            )
            req_info._health_summary = RequestHealthMetrics()
            req_info._health_summary.health_graph_paths = [
                req_info._monitor.nvdec_plot_file_name,
                req_info._monitor.gpu_plot_file_name,
            ]
            req_info._health_summary.set_gpu_names(req_info._monitor.get_gpu_names())
            req_info._health_summary.chunk_size = req_info.chunk_size
            req_info._health_summary.chunk_overlap_duration = req_info.chunk_overlap_duration
            req_info._health_summary.input_video_duration = req_info.file_duration / (
                1000 * 1000 * 1000
            )  # ns to s
            if req_info._health_summary.chunk_size <= 0:
                req_info._health_summary.num_chunks = 1
            else:
                req_info._health_summary.num_chunks = (
                    req_info._health_summary.input_video_duration
                    / req_info._health_summary.chunk_size
                )
            req_info._health_summary.num_gpus = self._args.num_gpus
            info = self.get_models_info()
            req_info._health_summary.vlm_model_name = str(info.id)
            req_info._health_summary.vlm_batch_size = self._args.vlm_batch_size

    def stop_via_gpu_monitor(self, req_info, chunk_responses: list[VlmChunkResponse]):
        def find_extreme(responses, func, value):
            values = []
            for response in responses:
                if hasattr(response, value):
                    attr_value = getattr(response, value)
                    if attr_value is not None:
                        values.append(attr_value)
            if not values:
                return 0
            return func(values)

        # Always update Prometheus metrics (decoupled from health eval)
        e2e_latency = time.time() - req_info.start_time if req_info.start_time else 0
        self._metrics.e2e_latency_latest.set(e2e_latency)
        self._metrics.ca_rag_latency_latest.set(req_info._ca_rag_latency)

        if chunk_responses:
            max_decode_end_time = find_extreme(chunk_responses, max, "decode_end_time")
            min_decode_start_time = find_extreme(chunk_responses, min, "decode_start_time")
            decode_latency = max_decode_end_time - min_decode_start_time
            self._metrics.decode_latency_latest.set(decode_latency)

            max_vlm_end_time = find_extreme(chunk_responses, max, "vlm_end_time")
            min_vlm_embed_start_time = find_extreme(chunk_responses, min, "embed_start_time")
            if min_vlm_embed_start_time == 0:
                # embed_start_time unavailable, use vlm_start_time instead
                min_vlm_embed_start_time = find_extreme(chunk_responses, min, "vlm_start_time")
            vlm_latency = max_vlm_end_time - min_vlm_embed_start_time
            self._metrics.vlm_latency_latest.set(vlm_latency)

        # GPU monitoring and health summary only when health eval is enabled
        if self._via_health_eval and req_info._monitor is not None:
            logger.info(f"Stopping GPUMonitor for request {req_info.request_id}")
            plot_graph_file = "/tmp/via-logs/via_plot_nvdec_" + str(req_info.request_id) + ".png"
            plot_graph_files = {
                "gpu": "/tmp/via-logs/via_plot_gpu_" + str(req_info.request_id) + ".png",
                "gpu_mem": "/tmp/via-logs/via_plot_gpu_mem_" + str(req_info.request_id) + ".png",
            }
            req_info._monitor.stop_recording_nvdec(plot_graph_file=plot_graph_file)
            req_info._monitor.stop_recording_gpu(plot_graph_files=plot_graph_files)
            req_info._health_summary.health_graph_plot_paths = [
                plot_graph_file,
                plot_graph_files["gpu"],
                plot_graph_files["gpu_mem"],
            ]
            req_info._health_summary.e2e_latency = e2e_latency

            if chunk_responses:
                req_info._health_summary.decode_latency = decode_latency

                create_historical_span(
                    "Total Decode Latency",
                    min_decode_start_time,
                    max_decode_end_time,
                    {"operation": "decode"},
                )

                req_info._health_summary.vlm_latency = vlm_latency

                create_historical_span(
                    "Total VLM Latency",
                    min_vlm_embed_start_time,
                    max_vlm_end_time,
                    {"operation": "vlm"},
                )

                req_info._health_summary.pending_doc_start_time = (
                    req_info.pending_add_doc_start_time
                )
                req_info._health_summary.pending_doc_end_time = req_info.pending_add_doc_end_time
                req_info._health_summary.pending_add_doc_latency = (
                    req_info.pending_add_doc_end_time - req_info.pending_add_doc_start_time
                )
                req_info._health_summary.req_start_time = req_info.start_time
                req_info._health_summary.total_vlm_input_tokens = sum(
                    [resp.vlm_stats.get("input_tokens", 0) for resp in chunk_responses]
                )
                req_info._health_summary.total_vlm_output_tokens = sum(
                    [resp.vlm_stats.get("output_tokens", 0) for resp in chunk_responses]
                )
                try:
                    for response in chunk_responses:
                        req_info._health_summary.all_times.append(
                            {
                                "chunk_id": response.chunk.chunkIdx,
                                "decode_start": response.decode_start_time,
                                "decode_end": response.decode_end_time,
                                "embed_start": response.embed_start_time,
                                "embed_end": response.embed_end_time,
                                "vlm_start": response.vlm_start_time,
                                "vlm_end": response.vlm_end_time,
                                "add_doc_start": response.add_doc_start_time,
                                "add_doc_end": response.add_doc_end_time,
                                "vlm_stats": response.vlm_stats,
                            }
                        )
                except Exception as e:
                    print("Error:", e)

            # End OTEL end-to-end pipeline span
            if req_info._e2e_span:
                try:
                    req_info._e2e_span.set_attribute(
                        "e2e_latency_ms", req_info._health_summary.e2e_latency * 1000
                    )
                    req_info._e2e_span.set_attribute("chunk_count", req_info.chunk_count)
                    req_info._e2e_span.set_attribute("total_chunks_processed", len(chunk_responses))
                    req_info._e2e_span.end()
                    logger.info("Ended e2e OTEL span")
                except Exception as e:
                    logger.info(f"Failed to end e2e OTEL span: {e}")

            req_info._health_summary.ca_rag_latency = req_info._ca_rag_latency
            logger.debug(f"_health_summary json: {str(vars(req_info._health_summary))}")
            health_summary_file_name = (
                "/tmp/via-logs/via_health_summary_" + str(req_info.request_id) + ".json"
            )
            req_info._health_summary.dump_json(file_name=health_summary_file_name)

            from otel_helper import dump_traces_to_file

            trace_files = dump_traces_to_file(str(req_info.request_id))
            if trace_files["json_file"]:
                logger.info(
                    f"OTEL traces dumped to {trace_files['json_file']} and {trace_files['text_file']}"
                )

            logger.info(f"VIA Health Summary written to {health_summary_file_name}")
            req_info._monitor = None

    def add_rtsp_stream(self, asset: Asset, chunk_size=None):
        """Add an RTSP stream to the server and start streaming

        Args:
            asset: Live stream asset to add
            chunk_size: Chunk size to use, in seconds
        """

        # A live stream can be added only once
        with self._lock:
            if asset.asset_id in self._live_stream_info_map:
                raise ViaException(
                    "Live stream already has query "
                    f"'{self._live_stream_info_map[asset.asset_id].req_info[0].request_id}' running."  # noqa: E501
                    " Update or stop the same query.",
                    "BadParameters",
                    400,
                )

            if len(self._live_stream_info_map) >= self._args.max_live_streams:
                raise ViaException(
                    "Server is already processing maximum number of live streams"
                    f" ({self._args.max_live_streams})",
                    503,
                )

            if chunk_size is None or chunk_size == 0:
                raise ViaException(
                    "Non-zero chunk duration required for live-stream", "InvalidParameter", 400
                )

            # Create a live stream info object and populate it
            live_stream_info = LiveStreamInfo()
            live_stream_info.chunk_size = chunk_size
            live_stream_info.asset = asset

            # Lock the asset so that it cannot be deleted while it is being used.
            asset.lock()

            self._live_stream_info_map[asset.asset_id] = live_stream_info

    def add_rtsp_stream_query(self, asset: Asset, query: SummarizationQuery):
        """Add a query on the RTSP stream

        Args:
            asset: Asset to add the query on
            query: Summarization query

        Returns:
            A unique ID for the request
        """
        if (query.vlm_input_width > 0 and query.vlm_input_width < 16) or (
            query.vlm_input_height > 0 and query.vlm_input_height < 16
        ):
            raise ViaException(
                "vlm_input_width and vlm_input_height must be greater than or equal to 16",
                "BadParameter",
                400,
            )

        live_stream_info = self._live_stream_info_map[asset.asset_id]
        if len(live_stream_info.req_info) > 0:
            raise ViaException(
                "Live stream already has query "
                f"'{live_stream_info.req_info[0].request_id}' running."
                " Update or stop the same query.",
                "BadParameters",
                400,
            )

        # For VLM captions (when summarize=False), summary_duration is not used
        # For regular summarization, summary_duration can be 0 (no periodic summarization)
        if query.summarize is False:
            # VLM captions don't use summary_duration, so skip validation
            pass
        else:
            # For regular summarization, allow summary_duration to be 0
            if query.summary_duration > 0 and (query.summary_duration % query.chunk_duration != 0):
                raise ViaException(
                    "summary_duration must be an exact multiple of chunk_duration",
                    "BadParameters",
                    400,
                )

        if self._args.enable_audio is False and (query.enable_audio is True):
            raise ViaException(
                "Audio ASR is not supported by this server instance", "BadParameter", 400
            )

        # Highest preference is to the user specified VLM prompt in the API call,
        # next to the VLM prompt (caption) in the CA RAG config. Lastly to the
        # prompt specified as argument to the app
        if not query.prompt:
            query.prompt = self.default_caption_prompt
        if self._args.vlm_model_type == VlmModelType.COSMOS_REASON2:
            query.prompt = query.prompt + ". Make sure the answer contain correct timestamps."

        # Run guardrails on the user supplied prompt
        self._check_rails(query.prompt)

        vlm_generation_config = {}
        # Extract user specified llm output parameters
        if query.max_tokens is not None:
            vlm_generation_config["max_new_tokens"] = query.max_tokens
        if query.top_p is not None:
            vlm_generation_config["top_p"] = query.top_p
        if query.top_k is not None:
            vlm_generation_config["top_k"] = query.top_k
        if query.temperature is not None:
            vlm_generation_config["temperature"] = query.temperature
        if query.seed is not None:
            vlm_generation_config["seed"] = query.seed

        # Create a RequestInfo object and populate it
        req_info = RequestInfo()
        req_info.file = asset.path
        req_info.stream_id = asset.asset_id
        req_info.camera_id = asset.camera_id
        req_info.chunk_size = query.chunk_duration
        req_info.is_summarization = True
        req_info.vlm_request_params.vlm_prompt = query.prompt
        req_info.vlm_request_params.vlm_generation_config = vlm_generation_config
        req_info.is_live = True
        req_info.status = RequestInfo.Status.PROCESSING
        req_info.summary_duration = query.summary_duration
        req_info.start_time = time.time()  # capture start of pipeline
        req_info.queue_time = time.time()
        req_info.assets = [asset]
        req_info.summary_aggregation_prompt = query.summary_aggregation_prompt
        req_info.caption_summarization_prompt = query.caption_summarization_prompt
        req_info._output_process_thread_pool = self._create_named_thread_pool(
            max_workers=1, prefix=f"vss-processor-{req_info.request_id[:8]}"
        )
        if self._ctx_mgr:
            summarize_enable = self._ca_rag_config.get("summarization", {})
            summarize_enable = summarize_enable.get("enable", True)
            if query.summarize is None:
                query.summarize = summarize_enable
        req_info.summarize = query.summarize
        req_info.enable_chat = query.enable_chat
        req_info.enable_chat_history = query.enable_chat_history
        req_info.num_frames_per_chunk = query.num_frames_per_chunk
        req_info.rag_top_k = query.rag_top_k
        req_info.rag_batch_size = query.rag_batch_size
        req_info.enable_audio = query.enable_audio

        # Try to use cached video FPS from asset first
        if asset.video_fps is not None:
            req_info.video_fps = float(asset.video_fps)
            logger.debug(f"Using cached video_fps {req_info.video_fps} for asset {asset.asset_id}")
        else:
            logger.warning(
                f"Could not get video_fps for live stream {asset.asset_id}, using default 30.0"
            )
            req_info.video_fps = 30.0

        if not self._args.disable_ca_rag:
            with self._lock:
                self._create_ctx_mgr_pool(self._ca_rag_config)
                req_info._ctx_mgr = self.get_ctx_mgr(req_info.assets)
            try:
                config = deepcopy(self._ca_rag_config)
                config["context_manager"]["uuid"] = req_info.stream_id
                req_info._ctx_mgr.configure(config=config)
            except Exception as ex:
                logger.error(traceback.format_exc())
                logger.error("Query failed for %s - %s", req_info.request_id, str(ex))
                return req_info.request_id
            # Reset the context manager for the first time
            if (
                self.first_init
                and req_info.enable_chat
                and os.environ.get("VSS_DISABLE_DB_RESET_ON_INIT", "false").lower()
                not in ["true", "1"]
            ):
                self.first_init = False
                req_info._ctx_mgr.reset(
                    {
                        "summarization": {"erase_db": True},
                        "retriever_function": {},
                        "ingestion_function": {"erase_db": True},
                    }
                )
            req_info.graph_db = query.graph_db
            req_info.enable_cot = query.enable_cot
            req_info.enable_image = query.enable_image
            req_info.summarize_top_p = query.summarize_top_p
            req_info.summarize_temperature = query.summarize_temperature
            req_info.summarize_max_tokens = query.summarize_max_tokens
            req_info.chat_top_p = query.chat_top_p
            req_info.chat_temperature = query.chat_temperature
            req_info.chat_max_tokens = query.chat_max_tokens
            req_info.notification_top_p = query.notification_top_p
            req_info.notification_temperature = query.notification_temperature
            req_info.notification_max_tokens = query.notification_max_tokens
            req_info.user_specified_collection_name = query.collection_name
            req_info.custom_metadata = query.custom_metadata
            req_info.delete_external_collection = query.delete_external_collection
            ca_rag_config = self.update_ca_rag_config(req_info)
            req_info._ctx_mgr.configure(ca_rag_config)

        # Add the request to the request info map
        with self._lock:
            self._request_info_map[req_info.request_id] = req_info

        live_stream_info.req_info.append(req_info)
        self._metrics.active_live_streams.inc()

        self._start_stream_fps_tracking(req_info)

        # Trigger collecting VIA GPU health metrics
        self.start_via_gpu_monitor(req_info)

        req_info.enable_cv_pipeline = query.enable_cv_metadata

        self._vlm_pipeline.add_live_stream(
            asset.asset_id,
            asset.path,
            live_stream_info.chunk_size,
            lambda response, req_info=req_info: self._on_vlm_chunk_response(response, req_info),
            req_info.vlm_request_params,
            username=asset.username,
            password=asset.password,
            num_frames_per_chunk=query.num_frames_per_chunk,
            vlm_input_width=query.vlm_input_width,
            vlm_input_height=query.vlm_input_height,
            enable_audio=req_info.enable_audio,
            enable_cv_pipeline=(self._cv_pipeline and req_info.enable_cv_pipeline),
            cv_pipeline_text_prompt=query.cv_pipeline_prompt,
        )

        return req_info.request_id

    def remove_video_file(self, asset: Asset):
        logger.info("Removing video %s from pipeline", asset.asset_id)
        ctx_mgrs_to_be_removed = []
        with self._lock:
            for req_info in self._request_info_map.values():
                if asset in req_info.assets and req_info._ctx_mgr:
                    ctx_mgrs_to_be_removed.append((req_info._ctx_mgr, req_info))
                    req_info._ctx_mgr = None

            self._request_info_map = {
                req_id: req_info
                for req_id, req_info in self._request_info_map.items()
                if asset not in req_info.assets
            }

        for ctx_mgr, req_info in ctx_mgrs_to_be_removed:
            if req_info.enable_chat:
                logger.info(
                    f"Resetting context manager {ctx_mgr._process_index}"
                    " for ingestion, retrieval and summarization"
                )
                ctx_mgr.reset(
                    {
                        "summarization": {"uuid": req_info.stream_id},
                        "retriever_function": {"uuid": req_info.stream_id},
                        "ingestion_function": {
                            "uuid": req_info.stream_id,
                            "delete_external_collection": req_info.delete_external_collection,
                        },
                    }
                )
            elif req_info.summarize:
                logger.info(
                    f"Resetting context manager {ctx_mgr._process_index}" " for summarization"
                )
                ctx_mgr.reset(
                    {
                        "summarization": {
                            "uuid": req_info.stream_id,
                            "delete_external_collection": req_info.delete_external_collection,
                        },
                    }
                )
            with self._lock:
                logger.info(f"Adding context manager : {ctx_mgr._process_index} to process pool.")
                self._ctx_mgr_pool.append(ctx_mgr)

        # TODO: This needs to be cleaned up. We shouldn't create minio client here.
        # It should be created once during the initialization of the stream handler.
        if os.getenv("SAVE_CHUNK_FRAMES_MINIO", "false").lower() in ["true", "1"]:
            logger.info(f"Removing asset: {asset.asset_dir}")

            minio_host = os.environ.get("MINIO_HOST")
            minio_port = os.environ.get("MINIO_PORT")
            minio_username = os.environ.get("MINIO_USERNAME")
            minio_password = os.environ.get("MINIO_PASSWORD")

            if not (minio_host and minio_port and minio_username and minio_password):
                logger.error("Minio environment variables not set, cannot retrieve images.")
                return

            minio_uri = f"http://{minio_host}:{minio_port}"

            if not (minio_uri and minio_username and minio_password):
                return

            # Parse the URI to determine if connection is secure
            parsed_uri = urlparse(minio_uri)
            secure = parsed_uri.scheme == "https"
            # Use netloc if available, otherwise path
            endpoint = parsed_uri.netloc or parsed_uri.path

            # Initialize the Minio client
            client = Minio(
                endpoint, access_key=minio_username, secret_key=minio_password, secure=secure
            )

            # The root bucket name is the stream id
            root_bucket = asset.asset_id
            logger.info(f"Clearing the minio bucket: {root_bucket}")
            if client.bucket_exists(root_bucket):
                objects = client.list_objects(root_bucket, recursive=True)
                # Delete all objects
                for obj in objects:
                    client.remove_object(root_bucket, obj.object_name)
                # Delete the bucket
                client.remove_bucket(root_bucket)
                logger.info(f"Removed bucket: {root_bucket}")

    def remove_rtsp_stream(self, asset: Asset):
        """Remove an RTSP stream from the server"""
        with self._lock:
            if asset.asset_id not in self._live_stream_info_map:
                logger.debug(f"RTSP stream for video {asset.asset_id} not active")
                return
            logger.info("Removing live stream %s from pipeline", asset.asset_id)
            live_stream_info = self._live_stream_info_map[asset.asset_id]
        live_stream_info.stop = True

        self._vlm_pipeline.remove_live_stream(asset.asset_id)

        # Unlock the asset so that it may be deleted and remove the stream
        # from live stream info map
        live_stream_info.asset.unlock()

        with self._lock:
            for alert_id in list(self._alert_info_map.keys()):
                if self._alert_info_map[alert_id].liveStreamId == asset.asset_id:
                    self.remove_live_stream_alert(alert_id)

            self._live_stream_info_map.pop(asset.asset_id)

        logger.info("Removed live stream %s from pipeline", asset.asset_id)

        ctx_mgrs_to_be_removed = []
        with self._lock:
            for req_info in self._request_info_map.values():
                if asset in req_info.assets and req_info._ctx_mgr:
                    ctx_mgrs_to_be_removed.append((req_info._ctx_mgr, req_info))
                    req_info._ctx_mgr = None
            self._request_info_map = {
                req_id: req_info
                for req_id, req_info in self._request_info_map.items()
                if asset not in req_info.assets
            }
        for ctx_mgr, req_info in ctx_mgrs_to_be_removed:
            if req_info.enable_chat:
                ctx_mgr.reset(
                    {
                        "summarization": {"uuid": req_info.stream_id},
                        "retriever_function": {"uuid": req_info.stream_id},
                        "ingestion_function": {
                            "uuid": req_info.stream_id,
                            "delete_external_collection": req_info.delete_external_collection,
                        },
                    }
                )
            elif req_info.summarize:
                ctx_mgr.reset(
                    {
                        "summarization": {"uuid": req_info.stream_id},
                        "delete_external_collection": req_info.delete_external_collection,
                    }
                )
            with self._lock:
                logger.info(
                    f"Adding Context Manager no.: {ctx_mgr._process_index} back to process pool."
                )
                self._ctx_mgr_pool.append(ctx_mgr)
        try:
            shutil.rmtree(f"/tmp/via/cached_frames/{asset.asset_id}")
        except FileNotFoundError:
            pass

    def get_event_list(self, liveStreamId: str):
        events_list = []
        with self._lock:
            for alert_id, ainfo in self._alert_info_map.items():
                if ainfo.liveStreamId == liveStreamId:
                    events_list.append({"event_id": alert_id, "event_list": ainfo.events})
        return events_list

    def add_live_stream_alert(
        self,
        liveStreamId: str,
        events: list[str],
        isCallback=False,
        callbackUrl=None,
        callbackJsonTemplate: str = "",
        callbackToken=None,
        alertName="",
    ):
        if not self._ctx_mgr:
            raise ViaException("Alerts functionality is disabled", "MethodNotAllowed", 405)

        with self._lock:
            if liveStreamId not in self._live_stream_info_map:
                raise ViaException(
                    f"No such live-stream {liveStreamId} or live-stream not active",
                    "BadParameters",
                    400,
                )
            req_info = self._live_stream_info_map[liveStreamId].req_info[0]

        ainfo = AlertInfo()
        ainfo.name = alertName
        ainfo.liveStreamId = liveStreamId
        ainfo.events = events
        ainfo.callbackUrl = callbackUrl
        if callbackJsonTemplate:
            ainfo.callbackJsonTemplate = callbackJsonTemplate
        ainfo.callbackToken = callbackToken

        try:
            test_json = jinja2.Template(ainfo.callbackJsonTemplate).render(
                streamId=ainfo.liveStreamId,
                alertId=ainfo.alert_id,
                ntpTimestamp="1970-01-01T00:00:00.000Z",
                alertText="Some text",
                detectedEvents=json.dumps(["some event1", "some event2"]),
            )

            json.loads(test_json)
        except json.decoder.JSONDecodeError:
            raise ViaException(
                f"Json template results into invalid json '{test_json}'",
                "BadParameters",
                400,
            )

        ainfo.alert_tool = (
            AlertCallbackTool(
                name="alert-" + ainfo.alert_id,
                alert_info=ainfo,
                stream_handler=self,
                sse_tool_name=alertName,
                req_info=req_info,
            )
            if isCallback
            else AlertSseTool(
                name="alert-" + ainfo.alert_id,
                alert_info=ainfo,
                req_info=req_info,
                sse_tool_name=alertName,
                stream_handler=self,
            )
        )
        with self._lock:
            self._alert_info_map[ainfo.alert_id] = ainfo

        if req_info._ctx_mgr:
            ca_rag_config = self.update_ca_rag_config(req_info)
            req_info._ctx_mgr.configure(ca_rag_config)

        return ainfo

    def remove_live_stream_alert(self, alert_id: str):
        with self._lock:
            if alert_id not in self._alert_info_map:
                raise ViaException(f"No such alert {alert_id}", "BadParameters", 400)
            ainfo = self._alert_info_map.pop(alert_id)

            liveStreamId = ainfo.liveStreamId
            if liveStreamId not in self._live_stream_info_map:
                return

            lsinfo = self._live_stream_info_map[liveStreamId]

        if lsinfo.req_info:
            if lsinfo.req_info[0]._ctx_mgr:
                req_info = lsinfo.req_info[0]
                ca_rag_config = self.update_ca_rag_config(req_info)
                req_info._ctx_mgr.configure(ca_rag_config)
        logger.info("Removed alert %s for live stream %s", alert_id, lsinfo.asset.asset_id)

    def live_stream_alerts(self):
        with self._lock:
            return list(self._alert_info_map.values())

    def add_alert(
        self,
        requestId: str,
        assetId: str,
        events: list[str],
        isCallback=False,
        callbackUrl: str = "",
        callbackJsonTemplate: str = "",
        callbackToken=None,
        alertName="",
    ):
        if not self._ctx_mgr:
            raise ViaException("Alerts functionality is disabled", "MethodNotAllowed", 405)

        with self._lock:
            if requestId not in self._request_info_map:
                raise ViaException(
                    f"No such request {requestId} or request not active",
                    "BadParameters",
                    400,
                )
            req_info = self._request_info_map[requestId]

        ainfo = AlertInfo()
        ainfo.name = alertName
        ainfo.requestId = requestId
        ainfo.liveStreamId = assetId
        ainfo.events = events
        ainfo.callbackUrl = callbackUrl
        if callbackJsonTemplate:
            ainfo.callbackJsonTemplate = callbackJsonTemplate
        ainfo.callbackToken = callbackToken

        try:
            test_json = jinja2.Template(ainfo.callbackJsonTemplate).render(
                streamId=ainfo.liveStreamId,
                alertId=ainfo.alert_id,
                ntpTimestamp="1970-01-01T00:00:00.000Z",
                alertText="Some text",
                detectedEvents=json.dumps(["some event1", "some event2"]),
            )

            json.loads(test_json)
        except json.decoder.JSONDecodeError:
            raise ViaException(
                f"Json template results into invalid json '{test_json}'",
                "BadParameters",
                400,
            )

        ainfo.alert_tool = (
            AlertCallbackTool(
                name="alert-" + ainfo.alert_id,
                alert_info=ainfo,
                stream_handler=self,
                req_info=req_info,
            )
            if isCallback
            else AlertSseTool(
                name="alert-" + ainfo.alert_id,
                req_info=req_info,
                sse_tool_name=alertName,
                alert_info=ainfo,
                stream_handler=self,
            )
        )

        with self._lock:
            self._alert_info_map[ainfo.alert_id] = ainfo

        if req_info._ctx_mgr:
            ca_rag_config = self.update_ca_rag_config(req_info)
            req_info._ctx_mgr.configure(ca_rag_config)

        return ainfo

    def remove_alert(self, alert_id: str):
        with self._lock:
            if alert_id not in self._alert_info_map:
                raise ViaException(f"No such alert {alert_id}", "BadParameters", 400)
            ainfo = self._alert_info_map.pop(alert_id)

            requestId = ainfo.requestId
            if requestId not in self._request_info_map:
                return

            req_info = self._request_info_map[requestId]

        if req_info._ctx_mgr:
            ca_rag_config = self.update_ca_rag_config(req_info)
            req_info._ctx_mgr.configure(ca_rag_config)
        logger.info("Removed alert %s for live stream %s", alert_id, req_info.assets[0].asset_id)

    def stop(self, force=False):
        """Stop the VIA Stream Handler"""
        logger.info("Stopping VIA Stream Handler")

        lsinfo_to_be_removed = list(self._live_stream_info_map.values())
        for lsinfo in lsinfo_to_be_removed:
            self.remove_rtsp_stream(lsinfo.asset)

        if hasattr(self, "_vlm_pipeline") and self._vlm_pipeline is not None:
            self._vlm_pipeline.stop(force)

        self._metrics.unregister()

        self._ctx_mgr = None

        self._ca_rag_alert_handler_server.should_exit = True
        self._ca_rag_alert_handler_server = None

        if self._cv_pipeline:
            self._cv_pipeline.stop(force)

        for ctx_mgr in self._ctx_mgr_pool:
            try:
                ctx_mgr.process.terminate()
            except Exception as e:
                logger.error(
                    f"Error shutting down context manager for request {ctx_mgr._process_index}: {e}"
                )

        for req_info in self._request_info_map.values():
            if req_info._ctx_mgr:
                try:
                    req_info._ctx_mgr.process.terminate()
                except Exception as e:
                    logger.error(
                        f"Error shutting down context manager for request {req_info._ctx_mgr._process_index}: {e}"  # noqa: E501
                    )

        logger.info("Stopped VIA Stream Handler")

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
                raise ViaException(f"No such request-id {request_id}", "InvalidParameterValue", 400)

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

    def check_status_remove_req_id(self, request_id):
        with self._lock:
            req_info = self._request_info_map.get(request_id, None)
            if not req_info:
                return
            if not req_info.enable_chat:
                # If request for file summarization has completed
                if (not req_info.is_live and req_info.progress == 100) or (
                    req_info.is_live
                    and self._live_stream_info_map[req_info.assets[0].asset_id].live_stream_ended
                    and len(req_info.alerts) == 0
                    and len(req_info.response) == 0
                ):
                    # If live stream ended
                    self.remove_request_ids(req_info.assets)
                    if req_info._ctx_mgr:
                        req_info._ctx_mgr.reset(
                            {
                                "summarization": {"uuid": req_info.stream_id},
                                "delete_external_collection": req_info.delete_external_collection,
                            }
                        )
                        self._ctx_mgr_pool.append(req_info._ctx_mgr)
                        logger.info(
                            f"Returning Context Manager Process"
                            f"{req_info._ctx_mgr._process_index} to process pool"
                        )

    def wait_for_request_done(self, request_id):
        """Wait for request to either complete or fail."""

        with self._lock:
            if request_id not in self._request_info_map:
                raise ViaException(f"No such request-id {request_id}", "InvalidParameterValue", 400)
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

    def _get_aggregated_summary(
        self, req_info: RequestInfo, chunk_responses: list[VlmChunkResponse]
    ):
        """Aggregated summary for the request"""

        with nvtx.annotate(message="StreamHandler/SaveDCFile", color="yellow"):
            saved_dc_file = req_info.file + ".dc.json"
            if not os.access(saved_dc_file, os.R_OK) and self._args.enable_dev_dc_gen:
                logger.info(f"Generating DC file at {saved_dc_file}")
                # Serialize the object to a JSON file
                req_info_to_write = req_info
                DCSerializer.to_json(req_info_to_write, saved_dc_file)

        if chunk_responses:
            with TimeMeasure("Chunk Processing - Filter and Sort"):
                with nvtx.annotate(message="StreamHandler/FilterNSort", color="yellow"):
                    # Filter out chunks that do not have an associated vlm response
                    chunk_responses = list(
                        filter(lambda item: item.vlm_response is not None, chunk_responses)
                    )
                    # Sort chunks based on their start times
                    chunk_responses.sort(
                        key=lambda item: ntp_to_unix_timestamp(item.chunk.start_ntp)
                    )

        if len(chunk_responses) == 0:
            # Return empty response if there are no chunks / chunks with vlm responses
            logger.info(f"No chunks with vlm responses for request {req_info.request_id}")
            return []

        if self._via_health_eval is True:
            with TimeMeasure("VLM Test Data - Write Chunk Responses"):
                with open(
                    "/tmp/via-logs/vlm_testdata_" + str(req_info.request_id) + ".txt", "w"
                ) as f:
                    with nvtx.annotate(message="StreamHandler/WriteChnkIDAns", color="green"):
                        f.write("Chunk_ID,Answer\n")
                        for proc_chunk in chunk_responses:
                            idx = proc_chunk.chunk.chunkIdx
                            summ = proc_chunk.vlm_response.replace("\n", "  ")
                            f.write(f'{idx},"{summ}"\n')

        if req_info._ctx_mgr:
            with TimeMeasure("Context Aware RAG Latency") as cms_t:
                try:
                    with nvtx.annotate(
                        message="CA RAG-" + str(req_info.request_id), color="yellow"
                    ):
                        # Summarize indivudual chunk VLM responses using CA-RAG
                        # TODO: Handle the last chunk id, should be -1
                        if not req_info.is_live:
                            last_meta = vars(chunk_responses[-1].chunk)
                            last_meta["is_last"] = True
                            last_meta["uuid"] = req_info.stream_id
                            last_meta["cv_meta"] = ""
                            last_meta["asset_dir"] = self._args.asset_dir
                            last_meta["camera_id"] = req_info.camera_id
                            with TimeMeasure("Context Manager Summarize/add_doc - last chunk"):
                                req_info._ctx_mgr.add_doc(
                                    ".",
                                    doc_i=(
                                        2 * chunk_responses[-1].chunk.chunkIdx + 2
                                        if req_info.enable_audio
                                        else chunk_responses[-1].chunk.chunkIdx + 1
                                    ),
                                    doc_meta=last_meta,
                                )

                        if req_info.summarize:
                            if req_info.enable_chat:
                                with TimeMeasure(
                                    "Context Manager Summarize/call - summarize_and_ingest"
                                ):
                                    logger.debug(
                                        f"Summarizing and ingesting chunk"
                                        f" {chunk_responses[0].chunk.chunkIdx}"
                                        f" to {chunk_responses[-1].chunk.chunkIdx}"
                                    )
                                    agg_response = req_info._ctx_mgr.call(
                                        {
                                            "summarization": {
                                                "start_index": (
                                                    2 * chunk_responses[0].chunk.chunkIdx
                                                    if req_info.enable_audio
                                                    else chunk_responses[0].chunk.chunkIdx
                                                ),
                                                "end_index": (
                                                    2 * chunk_responses[-1].chunk.chunkIdx + 1
                                                    if req_info.enable_audio
                                                    else chunk_responses[-1].chunk.chunkIdx
                                                ),
                                            },
                                            "ingestion_function": {
                                                "uuid": req_info.stream_id,
                                                "camera_id": req_info.camera_id,
                                            },
                                        }
                                    )
                            else:
                                with TimeMeasure("Context Manager Summarize/call - summarize"):
                                    agg_response = req_info._ctx_mgr.call(
                                        {
                                            "summarization": {
                                                "start_index": (
                                                    2 * chunk_responses[0].chunk.chunkIdx
                                                    if req_info.enable_audio
                                                    else chunk_responses[0].chunk.chunkIdx
                                                ),
                                                "end_index": (
                                                    2 * chunk_responses[-1].chunk.chunkIdx + 1
                                                    if req_info.enable_audio
                                                    else chunk_responses[-1].chunk.chunkIdx
                                                ),
                                            }
                                        }
                                    )

                            if "error" in agg_response and agg_response["error"]:
                                logger.error(
                                    f"Error for Request ID: {req_info.request_id}"
                                    f"Stream ID: {req_info.stream_id}"
                                )
                                logger.error(f"An internal error occurred: {agg_response['error']}")
                                logger.error(traceback.format_exc())
                                agg_response = "Summarization failed. Please check server \
                                    logs for more details.\n"

                            agg_response = agg_response["summarization"]["result"]
                            if self._via_health_eval is True:
                                with open(
                                    "/tmp/via-logs/summ_testdata_"
                                    + str(req_info.request_id)
                                    + ".txt",
                                    "w",
                                ) as f:
                                    f.write("Chunk_ID,Answer\n")
                                    summ = str(agg_response).replace("\n", "  ")
                                    f.write(f'{0},"{summ}"\n')
                        elif req_info.enable_chat:
                            req_info._ctx_mgr.call(
                                {
                                    "ingestion_function": {
                                        "uuid": req_info.stream_id,
                                        "camera_id": req_info.camera_id,
                                    },
                                }
                            )
                            agg_response = "Media processed"
                        else:
                            agg_response = "Media processed"
                except Exception as ex:
                    logger.error(traceback.format_exc())
                    logger.error(
                        "Summary aggregation failed for query %s - %s", req_info.request_id, str(ex)
                    )
                    agg_response = (
                        "Summarization failed. Please check server logs for more details.\n"
                    )
            req_info._ca_rag_latency = cms_t.execution_time

            # Return summarized response
            # For aggregated responses, combine reasoning descriptions from all chunks
            combined_reasoning = ""
            for chunk in chunk_responses:
                if hasattr(chunk, "vlm_stats") and chunk.vlm_stats:
                    chunk_reasoning = chunk.vlm_stats.get("reasoning_description", "")
                    if chunk_reasoning:
                        if combined_reasoning:
                            combined_reasoning += "\n\n"
                        combined_reasoning += f"Chunk {chunk.chunk.chunkIdx}: {chunk_reasoning}"

            return [
                RequestInfo.Response(
                    (
                        chunk_responses[0].chunk.start_ntp
                        if req_info.is_live
                        else chunk_responses[0].chunk.start_pts / 1e9
                    ),
                    (
                        chunk_responses[-1].chunk.end_ntp
                        if req_info.is_live
                        else chunk_responses[-1].chunk.end_pts / 1e9
                    ),
                    agg_response,
                    combined_reasoning,
                )
            ]

        # CA-RAG is disabled. Return a list of individual chunk VLM responses
        responses = []
        for processed_chunk in chunk_responses:
            # Extract reasoning description from VLM stats if available
            reasoning_description = ""
            if hasattr(processed_chunk, "vlm_stats") and processed_chunk.vlm_stats:
                reasoning_description = processed_chunk.vlm_stats.get("reasoning_description", "")

            responses.append(
                RequestInfo.Response(
                    (
                        processed_chunk.chunk.start_ntp
                        if req_info.is_live
                        else processed_chunk.chunk.start_pts / 1e9
                    ),
                    (
                        processed_chunk.chunk.end_ntp
                        if req_info.is_live
                        else processed_chunk.chunk.end_pts / 1e9
                    ),
                    processed_chunk.vlm_response,
                    reasoning_description,
                )
            )
        return responses

    def review_alert(self, review_alert_request: ReviewAlertRequest, asset: Asset):

        vlm_system_prompt = review_alert_request.vss_params.vlm_params.system_prompt

        query = SummarizationQuery(
            id=review_alert_request.id,
            model=self.get_models_info().id,
            chunk_duration=review_alert_request.vss_params.chunk_duration,
            chunk_overlap_duration=review_alert_request.vss_params.chunk_overlap_duration,
            prompt=review_alert_request.vss_params.vlm_params.prompt,
            summarize=False,
            enable_reasoning=review_alert_request.vss_params.enable_reasoning,
            system_prompt=vlm_system_prompt,
        )
        if review_alert_request.vss_params.vlm_params.max_tokens is not None:
            query.max_tokens = review_alert_request.vss_params.vlm_params.max_tokens
        if review_alert_request.vss_params.vlm_params.top_p is not None:
            query.top_p = review_alert_request.vss_params.vlm_params.top_p
        if review_alert_request.vss_params.vlm_params.top_k is not None:
            query.top_k = review_alert_request.vss_params.vlm_params.top_k
        if review_alert_request.vss_params.vlm_params.temperature is not None:
            query.temperature = review_alert_request.vss_params.vlm_params.temperature
        if review_alert_request.vss_params.vlm_params.seed is not None:
            query.seed = review_alert_request.vss_params.vlm_params.seed
        if review_alert_request.vss_params.num_frames_per_chunk:
            query.num_frames_per_chunk = review_alert_request.vss_params.num_frames_per_chunk
        if review_alert_request.vss_params.vlm_input_width:
            query.vlm_input_width = review_alert_request.vss_params.vlm_input_width
        if review_alert_request.vss_params.vlm_input_height:
            query.vlm_input_height = review_alert_request.vss_params.vlm_input_height

        if (
            review_alert_request.cv_metadata_path
            and review_alert_request.vss_params.cv_metadata_overlay
            and not os.path.exists(review_alert_request.cv_metadata_path)
        ):
            raise ViaException(
                f"CV metadata file {review_alert_request.cv_metadata_path} does not exist",
                "InvalidParameterValue",
                400,
            )

        req_id = self.query(
            assets=[asset],
            query=query,
            is_summarization=False,
            pregenerated_cv_metadata_json_file=(
                review_alert_request.cv_metadata_path
                if review_alert_request.vss_params.cv_metadata_overlay
                else ""
            ),
            skip_guardrails=ALERT_REVIEW_SKIP_GUARDRAILS,
            skip_ca_rag=True,
        )

        self.wait_for_request_done(req_id)
        req_info = self._request_info_map[req_id]
        result = False
        parsed_chunk_responses = []
        selected_frames_ts = []

        reasoning_description = (
            "" if len(req_info.processed_chunk_list) == 1 else "Detailed reasoning per chunk:"
        )

        req_info.processed_chunk_list.sort(key=lambda x: x.chunk.chunkIdx)

        for chunk in req_info.processed_chunk_list:
            parsed_chunk_responses.append((chunk.chunk, chunk.vlm_response))
            # get words from chunk.vlm_response and check if any of them are "yes" or "true"
            import string

            if not chunk.vlm_response:
                chunk.vlm_response = ""

            words = [word.strip(string.punctuation) for word in chunk.vlm_response.split()]
            if any(word.lower() in ["yes", "true"] for word in words):
                result = True

        for chunk in req_info.processed_chunk_list:
            # Extract reasoning description from VLM stats if available
            if hasattr(chunk, "vlm_stats") and chunk.vlm_stats:
                chunk_reasoning = chunk.vlm_stats.get("reasoning_description", "")
                if chunk_reasoning:
                    if len(req_info.processed_chunk_list) > 1:
                        reasoning_description += (
                            "\n---------------------------\n"
                            + f"{int(chunk.chunk.start_pts/1e9)}-{int(chunk.chunk.end_pts/1e9)} sec: \n{chunk_reasoning}"  # noqa: E501
                        )
                    else:
                        reasoning_description = chunk_reasoning

        for chunk in req_info.processed_chunk_list:
            selected_frames_ts.extend(chunk.frame_times)

        selected_frames_ts.sort()

        with self._lock:
            self._request_info_map.pop(req_info.request_id, None)

        response = ""
        if len(parsed_chunk_responses) == 1:
            response = parsed_chunk_responses[0][1]
        elif len(parsed_chunk_responses) > 1:
            response = "Detailed description per chunk:\n" + "\n".join(
                [
                    f"{int(chunk.start_pts/1e9)}-{int(chunk.end_pts/1e9)} sec: {details}"
                    for chunk, details in parsed_chunk_responses
                ]
            )

        return result, response, selected_frames_ts, reasoning_description

    @staticmethod
    def populate_argument_parser(parser: ArgumentParser):
        """Add VIA Stream Handler arguments to the argument parser"""

        VlmPipeline.populate_argument_parser(parser)

        parser.add_argument(
            "--disable-guardrails",
            action="store_true",
            default=False,
            help="Disable NEMO Guardrails",
        )
        parser.add_argument(
            "--enable-dev-dc-gen",
            action="store_true",
            default=False,
            help="Disable NEMO Guardrails",
        )
        parser.add_argument(
            "--disable-cv-pipeline",
            action="store_true",
            default=False,
            help="Disable CV Pipeline",
        )
        parser.add_argument(
            "--guardrails-config",
            type=str,
            default="/opt/nvidia/via/guardrails_config",
            help="NEMO Guardrails configuration",
        )
        parser.add_argument(
            "--max-file-duration",
            type=int,
            default=0,
            help="Maximum file duration to allow (0 = no restriction)",
        )

        parser.add_argument(
            "--disable-ca-rag",
            action="store_true",
            default=False,
            help="Enable/Disable CA-RAG",
        )
        parser.add_argument(
            "--ca-rag-config",
            type=str,
            default="/opt/nvidia/via/default_config.yaml",
            help="CA RAG config path",
        )
        parser.add_argument(
            "--summarization-query",
            type=str,
            default="Summarize the video",
            help="LLM query to use for summarization",
        )
        parser.add_argument(
            "--asset-dir", type=str, help="Directory to store the assets in", default="assets"
        )

    def _create_named_thread_pool(self, max_workers=1, prefix="via"):
        """Create a ThreadPoolExecutor with named threads"""
        return concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=f"{prefix}-{str(uuid.uuid4())[:8]}"
        )

    def _start_stream_fps_tracking(self, req_info: RequestInfo):
        """Start FPS tracking for a new stream."""
        req_info._fps_start_time = time.time()
        req_info._fps_frame_count = 0
        req_info._fps_last_update_time = req_info._fps_start_time
        req_info._fps_is_active = True
        logger.debug(f"Started FPS tracking for stream: {req_info.request_id}")

    def _update_stream_fps(self, response: VlmChunkResponse, req_info: RequestInfo):
        """Update FPS tracking for a stream."""
        if not req_info._fps_is_active:
            return

        if req_info.video_fps:
            frame_count = int(req_info.chunk_size * req_info.video_fps)
        else:
            frame_count = (
                len(response.frame_times)
                if hasattr(response, "frame_times") and response.frame_times
                else 0
            )

        req_info._fps_frame_count += frame_count
        req_info._fps_last_update_time = time.time()

        current_fps = self._get_request_fps(req_info)
        self._metrics.stream_fps_histogram.observe(current_fps)

    def _finalize_stream_fps_tracking(self, req_info: RequestInfo):
        """Finalize FPS tracking for a completed stream."""
        if not req_info._fps_is_active:
            return

        final_fps = self._get_request_fps(req_info)
        self._metrics.stream_fps_histogram.observe(final_fps)
        req_info._fps_is_active = False
        logger.debug(
            f"Finalized FPS tracking for stream {req_info.request_id}, final FPS: {final_fps:.2f}"
        )

    def _update_db_tool_param(self, ca_rag_config, db_tool_name, param_name, param_value):
        """Update DB tool parameter for a given function."""

        # Get the tool that this function references for DB operations
        tool = ca_rag_config.get("tools", {}).get(db_tool_name, {})
        if tool:
            # Ensure params section exists
            if "params" not in tool:
                tool["params"] = {}

            # Set the parameter
            if param_value:
                tool["params"][param_name] = param_value
            else:
                tool["params"].pop(param_name, None)

    def _update_llm_tool_param(self, ca_rag_config, function_name, param_name, param_value):
        """Update LLM tool parameter for a given function."""
        if param_value is None:
            return

        # Get the tool that this function references for LLM operations
        functions = ca_rag_config.get("functions", {})
        llm_tool_name = functions.get(function_name, {}).get("tools", {}).get("llm", "nvidia_llm")

        # Ensure tools and params sections exist
        if "tools" not in ca_rag_config:
            ca_rag_config["tools"] = {}
        if llm_tool_name not in ca_rag_config["tools"]:
            ca_rag_config["tools"][llm_tool_name] = {"params": {}}
        elif "params" not in ca_rag_config["tools"][llm_tool_name]:
            ca_rag_config["tools"][llm_tool_name]["params"] = {}

        # Set the parameter
        ca_rag_config["tools"][llm_tool_name]["params"][param_name] = param_value

    def update_ca_rag_config(self, req_info: RequestInfo):
        """
        Update and configure the ca_rag_config for the given request.

        Args:
            req_info: RequestInfo object containing configuration parameters

        Returns:
            dict: Configured ca_rag_config dictionary
        """
        ca_rag_config = copy.deepcopy(self._ca_rag_config)

        ca_rag_config["context_manager"]["uuid"] = req_info.stream_id

        # Set prompt configurations
        if req_info.caption_summarization_prompt:
            ca_rag_config["functions"]["summarization"]["params"]["prompts"][
                "caption"
            ] = req_info.vlm_request_params.vlm_prompt
            ca_rag_config["functions"]["summarization"]["params"]["prompts"][
                "caption_summarization"
            ] = req_info.caption_summarization_prompt
        if req_info.summary_aggregation_prompt:
            ca_rag_config["functions"]["summarization"]["params"]["prompts"][
                "summary_aggregation"
            ] = req_info.summary_aggregation_prompt

        # Set batch size for live streams based on summary duration
        if req_info.is_live:
            if req_info.summary_duration > 0:
                summ_batch_size = int(req_info.summary_duration / req_info.chunk_size)
                if req_info.enable_audio:
                    summ_batch_size *= 2
                ca_rag_config["functions"]["summarization"]["params"][
                    "batch_size"
                ] = summ_batch_size

        # Set explicit summarization batch size
        if req_info.summarize_batch_size:
            summ_batch_size = req_info.summarize_batch_size
            if req_info.enable_audio:
                # Make batch size even so that audio, video docs
                # for a chunk are processed together.
                summ_batch_size += 1 if (summ_batch_size % 2) != 0 else 0
            ca_rag_config["functions"]["summarization"]["params"]["batch_size"] = summ_batch_size

        # Ensure params dictionaries exist for retriever and ingestion functions
        if "params" not in ca_rag_config["functions"]["retriever_function"]:
            ca_rag_config["functions"]["retriever_function"]["params"] = {}
        if "params" not in ca_rag_config["functions"]["ingestion_function"]:
            ca_rag_config["functions"]["ingestion_function"]["params"] = {}

        # Set RAG parameters
        if req_info.rag_batch_size:
            ca_rag_config["functions"]["retriever_function"]["params"][
                "batch_size"
            ] = req_info.rag_batch_size
            ca_rag_config["functions"]["ingestion_function"]["params"][
                "batch_size"
            ] = req_info.rag_batch_size
        if req_info.rag_top_k:
            ca_rag_config["functions"]["retriever_function"]["params"]["top_k"] = req_info.rag_top_k
            ca_rag_config["functions"]["ingestion_function"]["params"]["top_k"] = req_info.rag_top_k
        if req_info.stream_id:
            ca_rag_config["functions"]["retriever_function"]["params"]["uuid"] = req_info.stream_id
            ca_rag_config["functions"]["ingestion_function"]["params"]["uuid"] = req_info.stream_id
            ca_rag_config["functions"]["summarization"]["params"]["uuid"] = req_info.stream_id

        # Update LLM tool parameters for summarization
        if req_info.summarize:
            self._update_llm_tool_param(
                ca_rag_config, "summarization", "top_p", req_info.summarize_top_p
            )
            self._update_llm_tool_param(
                ca_rag_config, "summarization", "temperature", req_info.summarize_temperature
            )
            self._update_llm_tool_param(
                ca_rag_config, "summarization", "max_tokens", req_info.summarize_max_tokens
            )
        else:
            if "summarization" in ca_rag_config["context_manager"]["functions"]:
                ca_rag_config["context_manager"]["functions"].remove("summarization")

        # Configure chat functionality
        if req_info.enable_chat:
            # Update LLM tool parameters for chat functions
            self._update_llm_tool_param(
                ca_rag_config, "retriever_function", "top_p", req_info.chat_top_p
            )
            self._update_llm_tool_param(
                ca_rag_config, "retriever_function", "temperature", req_info.chat_temperature
            )
            self._update_llm_tool_param(
                ca_rag_config, "retriever_function", "max_tokens", req_info.chat_max_tokens
            )
            self._update_llm_tool_param(
                ca_rag_config, "ingestion_function", "top_p", req_info.chat_top_p
            )
            self._update_llm_tool_param(
                ca_rag_config, "ingestion_function", "temperature", req_info.chat_temperature
            )
            self._update_llm_tool_param(
                ca_rag_config, "ingestion_function", "max_tokens", req_info.chat_max_tokens
            )

            # Configure chat history
            logger.info(f"enable_chat_history | STREAM_HANDLER: {req_info.enable_chat_history}")
            ca_rag_config["functions"]["retriever_function"]["params"][
                "chat_history"
            ] = req_info.enable_chat_history
        else:
            if "retriever_function" in ca_rag_config["context_manager"]["functions"]:
                ca_rag_config["context_manager"]["functions"].remove("retriever_function")
            if "ingestion_function" in ca_rag_config["context_manager"]["functions"]:
                ca_rag_config["context_manager"]["functions"].remove("ingestion_function")

        # Update notification LLM tool parameters
        self._update_llm_tool_param(
            ca_rag_config, "notification", "top_p", req_info.notification_top_p
        )
        self._update_llm_tool_param(
            ca_rag_config, "notification", "temperature", req_info.notification_temperature
        )
        self._update_llm_tool_param(
            ca_rag_config, "notification", "max_tokens", req_info.notification_max_tokens
        )

        self._update_db_tool_param(
            ca_rag_config,
            "vector_db",
            "user_specified_collection_name",
            req_info.user_specified_collection_name,
        )
        self._update_db_tool_param(
            ca_rag_config, "vector_db", "custom_metadata", req_info.custom_metadata
        )
        self._update_db_tool_param(
            ca_rag_config,
            "vector_db",
            "delete_external_collection",
            req_info.delete_external_collection,
        )

        event_list = self.get_event_list(req_info.stream_id)
        if event_list:
            ca_rag_config["functions"]["notification"]["params"]["events"] = event_list
        return ca_rag_config

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
                    stream_id = req_info.assets[0].asset_id
                    active_streams_info[stream_id] = self._get_request_fps(req_info)
            return active_streams_info

    def update_live_stream_summary_latency(self, latency: float):
        """Update live stream summary latency metric"""
        if hasattr(self._metrics, "live_stream_summary_latency"):
            self._metrics.live_stream_summary_latency.observe(latency)

    def update_live_stream_captions_latency(self, latency: float):
        """Update live stream captions latency metric"""
        if hasattr(self._metrics, "live_stream_captions_latency"):
            self._metrics.live_stream_captions_latency.observe(latency)
