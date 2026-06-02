# SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import concurrent.futures
import copy
import glob
import json
import os
import re
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

import json_repair
import prometheus_client as prom
import requests.exceptions
from pyaml_env import parse_config

from chunk_info import ChunkInfo, RequestSource, get_timestamp_str
from lvs_errors import classify_es_error
from otel_helper import (
    create_historical_span,
    get_tracer,
    is_tracing_enabled,
    trace_operation,
)
from rag_adapter import RagAdapter
from rtvi_vlm_client import RtviVlmClient
from via_exception import ViaException
from via_logger import TimeMeasure, logger, safe_log
from vlm_pipeline.vlm_types import VlmChunkResponse, VlmRequestParams
from vss_api_models import (
    ChatCompletionQuery,
    GenerateCaptionsRequest,
    StreamSummarizeRequest,
    SummarizationQuery,
)

try:
    from rtvi_vlm_client import RtviError
except ImportError:
    # Stub so isinstance(ex, RtviError) works even when rtvi_vlm_client is absent
    class RtviError(Exception):
        pass


def _safe_collection_name(stream_id) -> str:
    """Return a stream_id sanitized for use as an Elasticsearch index name.

    ES indices may not contain '-', '/', '\\', '*', '?', '"', '<', '>', '|',
    ',', or '#'; replace separators commonly found in UUIDs/paths with '_'.
    """
    sid = str(stream_id or "unknown")
    for ch in ("-", "/", "\\", " "):
        sid = sid.replace(ch, "_")
    return f"default_{sid}"


# NOTE: _build_rtvi_static_info was removed. It built the static_info dict
# that LVS used to stamp into RTVI's VlmQuery so downstream Logstash had
# uuid / camera_id / file / asset_dir / cv_meta / etc. on every chunk. With
# the new flow:
#   - File path: doesn't go to Kafka at all; in-process add_doc handles
#     the metadata.
#   - Live-stream path: captioning starts via RTVI's /v1/stream/add (no LVS
#     involvement); Logstash derives the index name from baseline
#     vision_llm.info[streamId] (= chunk.streamId = asset UUID when
#     camera_id="" is sent on stream/add).


MAX_MILVUS_STRING_LEN = 65535


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

    class Usage:
        def __init__(
            self,
            summary_tokens: int = 0,
            aggregation_tokens: int = 0,
            summary_requests: int = 0,
            summary_latency: float = 0.0,
            aggregation_latency: float = 0.0,
            *args,
            **kwargs,
        ) -> None:
            self.summary_tokens = summary_tokens
            self.aggregation_tokens = aggregation_tokens
            self.summary_requests = summary_requests
            self.summary_latency = summary_latency
            self.aggregation_latency = aggregation_latency

    def __init__(self) -> None:
        self.request_id = str(uuid.uuid4())
        self.source_id = ""
        self.chunk_count = 0
        self.chunk_size = 0
        self.video_fps = None
        self.chunk_overlap_duration = 0
        self.file = ""
        self.source_url = ""
        self.processed_chunk_list: list[VlmChunkResponse] = []
        self.is_summarization = False
        self.vlm_request_params = VlmRequestParams()
        self.progress = 0
        self.response: list[RequestInfo.Response] = []
        self.usage: RequestInfo.Usage = RequestInfo.Usage()
        self.is_live = False
        self.start_timestamp = None
        self.end_timestamp = None
        self.queue_time = None
        self.start_time = None
        self.end_time = None
        self.file_duration = 0
        self.status = RequestInfo.Status.QUEUED
        self.status_event = Event()
        self.enable_vlm_structured_output = True
        self.objects_of_interest = None
        self._ca_rag_latency = 0
        self._ctx_mgr = None
        self._output_process_thread_pool: concurrent.futures.ThreadPoolExecutor = None
        self.summarize = None
        self.pending_add_doc_start_time = 0
        self.pending_add_doc_end_time = 0
        self.summarize_batch_size = None
        self.vlm_input_width = None
        self.vlm_input_height = None
        self.enable_audio = False
        self.last_chunk: ChunkInfo | None = None
        self.summarize_top_p = None
        self.summarize_temperature = None
        self.summarize_max_tokens = None
        self.camera_id = ""
        # OTEL spans
        self._e2e_span = None
        self.vlm_pipeline_span = None
        self._e2e_span_context = None
        self._vlm_pipeline_span_context = None
        # fps metrics
        self._fps_start_time = None
        self._fps_frame_count = 0
        self._fps_last_update_time = None
        self._fps_is_active = False
        self.user_specified_collection_name = None
        self.custom_metadata = None
        self.delete_external_collection = False
        self.error_message = ""
        self.schema = None
        self.batch_response_method = None
        self.scenario = None
        self.events = None
        self.auto_generate_prompt = None
        self.time_metadata_keys = None
        self.rtvi_status_code = None
        self.rtvi_error_code = None
        self.rtvi_error_message = None
        self.enable_qa = False
        self._qa_ctx_mgr = None


class DCSerializer:
    @staticmethod
    def to_json(request_info: RequestInfo, file_path):
        try:
            with open(file_path, "w") as f:
                for vlm_response in request_info.processed_chunk_list:
                    json.dump(
                        {
                            "vlm_response": vlm_response.vlm_response,
                            "rtvi_frame_count": vlm_response.rtvi_frame_count,
                            "chunk": {
                                "sourceId": vlm_response.chunk.sourceId,
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
                            },
                        },
                        f,
                    )
                    f.write("\n")
        except Exception as e:
            logger.warning("write to_json Exception: %s", str(e))

    @staticmethod
    def from_json(file_path):
        request_info = RequestInfo()
        try:
            with open(file_path, "r") as f:
                for line in f:
                    data = json.loads(line)
                    chunk_info = ChunkInfo()
                    chunk_data = data.get("chunk", {})
                    chunk_info.sourceId = chunk_data.get("sourceId", "")
                    chunk_info.chunkIdx = chunk_data.get("chunkIdx", 0)
                    chunk_info.file = chunk_data.get("file", "")
                    chunk_info.pts_offset_ns = chunk_data.get("pts_offset_ns", 0)
                    chunk_info.start_pts = chunk_data.get("start_pts", 0)
                    chunk_info.end_pts = chunk_data.get("end_pts", 0)
                    chunk_info.start_ntp = chunk_data.get("start_ntp", "")
                    chunk_info.end_ntp = chunk_data.get("end_ntp", "")
                    chunk_info.start_ntp_float = chunk_data.get("start_ntp_float", 0.0)
                    chunk_info.end_ntp_float = chunk_data.get("end_ntp_float", 0.0)
                    chunk_info.is_first = chunk_data.get("is_first", False)
                    chunk_info.is_last = chunk_data.get("is_last", False)
                    vlm_response = VlmChunkResponse()
                    vlm_response.vlm_response = data.get("vlm_response", "")
                    # Accept either new (rtvi_frame_count) or old (frame_times) format.
                    vlm_response.rtvi_frame_count = data.get(
                        "rtvi_frame_count", len(data.get("frame_times", []) or [])
                    )
                    vlm_response.chunk = chunk_info

                    request_info.processed_chunk_list.append(vlm_response)
                # Sort the processed_chunk_list by chunkIdx
                if request_info.processed_chunk_list:
                    request_info.processed_chunk_list.sort(key=lambda x: x.chunk.chunkIdx)
        except Exception as e:
            logger.warning("read from json exception: %s", str(e))
        return request_info


class LiveStreamInfo:
    """Store information for a live stream"""

    def __init__(self) -> None:
        self.chunk_size = 0
        self.req_info: list[RequestInfo] = []
        self.source_id: str = ""
        self.stop = False
        self.live_stream_ended = False
        self.pending_futures = []


def ntp_to_unix_timestamp(ntp_ts):
    """Convert an RFC3339 timestamp string to a UNIX timestamp(float)"""
    return (
        datetime.strptime(ntp_ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
    )


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

            self.vlm_queue_time = prom.Histogram(
                "vlm_queue_time_seconds",
                "Time a chunk waited in RTVI's VLM queue before processing",
                buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 3.0, 10.0, 30.0],
            )

            self.vlm_chunk_total_latency = prom.Histogram(
                "vlm_chunk_total_latency_seconds",
                "Per-chunk wall-clock window from decode-start to VLM-end",
                buckets=[0.1, 0.3, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0, 60.0],
            )

            self.vlm_frames_per_chunk = prom.Histogram(
                "vlm_frames_per_chunk",
                "Frames decoded and fed to the VLM per chunk",
                buckets=[1, 4, 8, 16, 24, 32, 48, 64, 96, 128, 256],
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
            prom.REGISTRY.unregister(self.vlm_queue_time)
            prom.REGISTRY.unregister(self.vlm_chunk_total_latency)
            prom.REGISTRY.unregister(self.vlm_frames_per_chunk)
            prom.REGISTRY.unregister(self.decode_latency_latest)
            prom.REGISTRY.unregister(self.vlm_latency_latest)
            prom.REGISTRY.unregister(self.ca_rag_latency_latest)
            prom.REGISTRY.unregister(self.e2e_latency_latest)
            prom.REGISTRY.unregister(self.vlm_pipeline_latency_latest)

    def __init__(self, args) -> None:
        """Initialize the VIA Stream Handler"""
        logger.info("Initializing VIA Stream Handler")

        self._lock = RLock()
        self._running = True
        self._request_info_map: dict[str, RequestInfo] = {}
        self._notification_llm_api_key = None
        self._notification_llm_params = None

        self._start_time = time.time()
        self._metrics = ViaStreamHandler.Metrics()

        # Start a background thread to update the system uptime metric every 10 seconds
        def update_metrics():
            while self._running:
                uptime = time.time() - self._start_time
                self._metrics.system_uptime.set(uptime)
                time.sleep(10)

        uptime_thread = Thread(target=update_metrics, daemon=True, name="via-uptime-metrics-thread")
        uptime_thread.start()

        self._live_stream_info_map: dict[str, LiveStreamInfo] = {}
        self._args = args
        if os.environ.get("VSS_LOG_LEVEL"):
            self._args.log_level = os.environ.get("VSS_LOG_LEVEL").upper()
        self.first_init = True

        self.default_caption_prompt = self._args.summarization_query
        self._ctx_mgr_pool = []
        self._qa_ctx_mgr_pool = []
        self.NUM_CA_RAG_PROCESSES_LAUNCH = 10
        self.num_ctx_mgr = 0
        self.num_qa_ctx_mgr = 0
        self.MAX_STREAMS = self._args.max_live_streams

        self._vlm_pipeline = RtviVlmClient(args)
        logger.info(
            "Using RTVI-VLM backend at %s",
            os.environ.get("RTVI_VLM_URL", "http://localhost:8000"),
        )

        # Kafka producer (streaming-only architecture). Active only when
        # KAFKA_ENABLED=true and the request is dispatched on a streaming
        # RTVI path (file SSE or live-stream). Non-streaming requests retain
        # the in-process add_doc flow even with KAFKA_ENABLED=true.
        self._kafka_enabled = os.environ.get("KAFKA_ENABLED", "false").lower() in (
            "true",
            "1",
        )
        self._kafka_producer = None
        if self._kafka_enabled:
            try:
                from kafka_producer import ViaKafkaProducer

                self._kafka_producer = ViaKafkaProducer()
                logger.info(
                    "Kafka producer initialized for via-engine: enabled=%s",
                    self._kafka_producer.enabled,
                )
            except Exception as ex:
                logger.error("Failed to initialize via-engine Kafka producer: %s", ex)
                self._kafka_producer = None

        self._caption_source = os.environ.get("LVS_CAPTION_SOURCE", "db").lower()
        if self._caption_source not in ("sse", "db"):
            logger.warning(
                "Invalid LVS_CAPTION_SOURCE=%r, falling back to 'db'",
                self._caption_source,
            )
            self._caption_source = "db"
        if self._kafka_enabled:
            logger.info(
                "Kafka file-path caption source: LVS_CAPTION_SOURCE=%s "
                "(sse=use SSE captions for aggregation, db=retrieve from Elastic DB)",
                self._caption_source,
            )

        if not args.disable_ca_rag:
            try:
                try:
                    config = parse_config(args.ca_rag_config)
                except Exception as e:
                    self.stop(True)
                    raise ValueError(f"{args.ca_rag_config} is not a valid YAML file") from e

                # Ensure vector_db host/port come from environment when set (e.g. in Docker).
                # Skip when using elasticsearch_db only - otherwise nvidia_rag
                # still connects to Milvus at startup.
                _db_backend = os.environ.get("LVS_DATABASE_BACKEND", "vector_db")
                if _db_backend != "elasticsearch_db" and (
                    os.environ.get("MILVUS_DB_HOST") or os.environ.get("MILVUS_DB_GRPC_PORT")
                ):
                    if "tools" not in config:
                        config["tools"] = {}

                    if "vector_db" not in config["tools"]:
                        config["tools"]["vector_db"] = {"params": {}}

                    if "params" not in config["tools"]["vector_db"]:
                        config["tools"]["vector_db"]["params"] = {}

                    if os.environ.get("MILVUS_DB_HOST"):
                        _host = os.environ["MILVUS_DB_HOST"]
                        config["tools"]["vector_db"]["params"]["host"] = _host

                    if os.environ.get("MILVUS_DB_GRPC_PORT"):
                        # CA-RAG (ContextManagerConfig/MilvusDBConfig) expects port as string
                        config["tools"]["vector_db"]["params"]["port"] = str(
                            os.environ["MILVUS_DB_GRPC_PORT"]
                        )

                self._ca_rag_config = config
                self._ctx_mgr = True
                os.environ["CA_RAG_ENABLE_WARMUP"] = "true"
                # Use a copy for the pool so we can strip vector_db when using Elasticsearch
                pool_config = deepcopy(config)
                self._create_ctx_mgr_pool(pool_config)

            except Exception as e:
                self.stop(True)
                logger.error(traceback.format_exc())
                raise (ValueError("CA-RAG setup failed.")) from e
        else:
            self._ctx_mgr = None

        logger.info("Initialized VIA Stream Handler")

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
                    "ServerBusy",
                    503,
                )
            logger.info(  # noqa: BLK100
                f"Context Manager Process Pool is empty,"
                f" adding new processes from index {self.num_ctx_mgr}"
            )
            for i in range(self.NUM_CA_RAG_PROCESSES_LAUNCH):
                self._ctx_mgr_pool.append(
                    RagAdapter(ContextManager(config=config, process_index=self.num_ctx_mgr))
                )
                os.environ["CA_RAG_ENABLE_WARMUP"] = "false"
                self.num_ctx_mgr = self.num_ctx_mgr + 1
                if self.num_ctx_mgr >= self.MAX_STREAMS:
                    return

    def _create_qa_ctx_mgr_pool(self, config):
        """Create a pool of ContextManagers configured only for QA (ingestion + retriever)."""
        from vss_ctx_rag.context_manager import ContextManager

        with self._lock:
            if len(self._qa_ctx_mgr_pool) > 0:
                return

            qa_config = deepcopy(config)
            qa_config["context_manager"]["functions"] = []
            for fn in ("ingestion_function", "retriever_function"):
                if fn in qa_config.get("functions", {}):
                    qa_config["context_manager"]["functions"].append(fn)

            logger.info(
                "QA Context Manager Pool is empty, adding new processes from index %d",
                self.num_qa_ctx_mgr,
            )
            for i in range(self.NUM_CA_RAG_PROCESSES_LAUNCH):
                self._qa_ctx_mgr_pool.append(
                    RagAdapter(ContextManager(config=qa_config, process_index=self.num_qa_ctx_mgr))
                )
                os.environ["CA_RAG_ENABLE_WARMUP"] = "false"
                self.num_qa_ctx_mgr += 1
                if self.num_qa_ctx_mgr >= self.MAX_STREAMS:
                    return

    @staticmethod
    def _remove_think_tags(text: str) -> tuple[str, str]:
        """Strip ``<think>…</think>`` reasoning blocks that some models emit.

        Returns ``(cleaned_text, reasoning)`` where *reasoning* is the
        concatenated content of all ``<think>`` blocks (empty string when
        none are present).
        """
        think_blocks = re.findall(r"<think>(.*?)</think>", text, flags=re.DOTALL)
        out = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        if "<think>" not in out and "</think>" in out:
            leading = re.search(r"^(.*?)</think>", out, flags=re.DOTALL)
            if leading:
                think_blocks.append(leading.group(1))
            out = re.sub(r".*?</think>", "", out, flags=re.DOTALL)
        reasoning = "\n".join(b.strip() for b in think_blocks if b.strip())
        return out, reasoning

    def _parse_vlm_json_to_plaintext(self, vlm_response: str) -> str:
        """Parse VLM JSON response into plain-text ``<start> <end> <description>`` lines."""
        try:
            cleaned, _ = self._remove_think_tags(vlm_response)
            content = self.extract_json_from_vlm_response(cleaned)
            events = json_repair.loads(content)
            if isinstance(events, dict):
                events = events.get("events", [events])
            if not isinstance(events, list):
                events = [events]
            lines = []
            for event in events:
                if isinstance(event, dict):
                    start = event.get("start_time", "")
                    end = event.get("end_time", "")
                    desc = event.get("description", "")
                    lines.append(f"{start} {end} {desc}")
            return "\n".join(lines) if lines else vlm_response
        except Exception:
            return vlm_response

    def _process_output(
        self,
        req_info: RequestInfo,
        is_live_stream_ended: bool,
        chunk_responses: list[VlmChunkResponse],
    ):
        new_response = []
        if not is_live_stream_ended and req_info.status != RequestInfo.Status.FAILED:
            try:
                new_response = self._get_aggregated_summary(req_info, chunk_responses)
                if (
                    new_response
                    and len(new_response) > 0
                    and "Summarization failed" in new_response[0].response
                ):
                    raise Exception("Summarization failed" + new_response[0].response)
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
            live_stream_id = req_info.source_id
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
                self._update_completion_metrics(req_info, chunk_responses)
        else:
            if req_info.status == RequestInfo.Status.FAILED:
                logger.info(
                    "Summary generation failed for video file request %s", req_info.request_id
                )
                self._update_completion_metrics(req_info, chunk_responses)
            else:
                req_info.progress = 100
                req_info.end_time = time.time()
                self._update_completion_metrics(req_info, chunk_responses)
                req_info.status = RequestInfo.Status.SUCCESSFUL
                logger.info(
                    "Summary generated for video file request %s,"
                    " total processing time - %.2f seconds, summary %s",
                    req_info.request_id,
                    req_info.end_time - req_info.start_time,
                    "",
                )

            self._metrics.queries_processed.inc()
            self._metrics.queries_pending.dec()
        req_info.status_event.set()

    def _get_cv_metadata_for_chunk(self, json_file, frame_times):
        cv_meta = []
        if json_file:
            with open(json_file, "r") as f:
                data = json.load(f)

            # Sort data by timestamp once
            sorted_data = sorted(data, key=lambda x: x.get("timestamp", 0))
            current_idx = 0

            for frame_time in frame_times:
                frame_time_ns = frame_time * 1e9  # Convert to nanoseconds
                # Continue from last found position instead of searching from start
                while (
                    current_idx < len(sorted_data)
                    and sorted_data[current_idx].get("timestamp", 0) < 0.99 * frame_time_ns
                ):
                    current_idx += 1

                if (
                    current_idx < len(sorted_data)
                    and sorted_data[current_idx].get("timestamp", 0) <= 1.01 * frame_time_ns
                ):
                    cv_meta.append(sorted_data[current_idx])

        return cv_meta

    @staticmethod
    def _convert_event_timestamps_to_iso(raw_result: str) -> str:
        """Convert numeric epoch timestamps in aggregated events to ISO 8601 strings.

        Parses the JSON result from CA-RAG aggregation and converts any numeric
        start_time / end_time values in the events list to ISO 8601 format
        (e.g. "2026-04-30T10:39:20.934Z"). Non-numeric values are left as-is.
        Returns the re-serialized JSON string, or the original string on error.
        """
        if not raw_result:
            return raw_result
        try:
            parsed = json.loads(raw_result)
        except (json.JSONDecodeError, TypeError, ValueError):
            return raw_result
        if not isinstance(parsed, dict):
            return raw_result

        def _epoch_to_iso(val):
            if isinstance(val, (int, float)):
                dt = datetime.fromtimestamp(val, tz=timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
            return val

        for event in parsed.get("events", []) or []:
            if isinstance(event, dict):
                if "start_time" in event:
                    event["start_time"] = _epoch_to_iso(event["start_time"])
                if "end_time" in event:
                    event["end_time"] = _epoch_to_iso(event["end_time"])
        return json.dumps(parsed)

    @staticmethod
    def _remove_segmasks_from_cv_meta(cv_meta_):
        cv_meta = deepcopy(cv_meta_)
        for data in cv_meta:
            for obj in data.get("objects", []):
                if "misc" not in obj:
                    continue
                for misc in obj.get("misc", []):
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
            if not req_info.file_duration:
                logger.warning(
                    "file_duration is zero for request %s, cannot compute frame_rate",
                    req_info.request_id,
                )
                return None
            frame_rate = frame_count / (req_info.file_duration / 1e9)
            logger.info("Creating cached frames video with frame rate %s", frame_rate)
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
                logger.info("Cached Frames Video created at %s", video_path)
                # Now delete all jpg files
                [shutil.os.remove(f) for f in glob.glob(images_path)]
                return video_path
            except subprocess.CalledProcessError as e:
                logger.error("FFmpeg command failed: %s", e)
                return None
        else:
            return None

    @staticmethod
    def _sanitize_vlm_response(text: str) -> str:
        """
        Strip special characters from VLM response.
        Removes literal \\uXXXX sequences and other non-ASCII characters.
        """
        if not text:
            return text
        text = re.sub(r"\\u[0-9a-fA-F]{4}", "", text)
        return text.encode("ascii", "ignore").decode("ascii")

    @staticmethod
    def _parse_rtvi_time(val):
        """Parse a time value that may be numeric seconds or an ISO 8601 timestamp string."""
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
        try:
            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            return 0.0

    def _on_vlm_chunk_response(self, response: VlmChunkResponse, req_info: RequestInfo):
        """Gather chunks processed by the pipeline and run any further post-processing"""
        if not self._running:
            return
        # Create per-chunk parent span that covers all operations for this chunk
        vlm_pipeline_ctx = getattr(req_info, "_vlm_pipeline_span_context", None)
        chunk_parent_ctx = None

        # Calculate chunk processing span (from decode start to VLM end)
        # Use explicit None checks to avoid issues with 0 being falsy
        decode_start = getattr(response, "decode_start_time", None)
        vlm_start = getattr(response, "vlm_start_time", None)
        vlm_end = getattr(response, "vlm_end_time", None)
        decode_end = getattr(response, "decode_end_time", None)

        chunk_start = decode_start if decode_start else vlm_start
        chunk_end = vlm_end if vlm_end else decode_end

        if chunk_start and chunk_end and chunk_end > chunk_start:
            if vlm_pipeline_ctx is None:
                logger.warning(
                    f"Chunk {response.chunk.chunkIdx}: vlm_pipeline_ctx is None - chunk span will be root"
                )
            chunk_parent_ctx = create_historical_span(
                f"Chunk {response.chunk.chunkIdx}",
                chunk_start,
                chunk_end,
                {
                    "chunk_idx": response.chunk.chunkIdx,
                    "source_id": response.chunk.sourceId,
                    "total_latency_ms": (chunk_end - chunk_start) * 1000,
                    "operation": "chunk_processing",
                },
                parent_context=vlm_pipeline_ctx,
            )

        # Per-chunk decode latency and OTEL tracing
        if hasattr(response, "decode_start_time") and hasattr(response, "decode_end_time"):
            if (
                response.decode_start_time
                and response.decode_end_time
                and response.decode_end_time > response.decode_start_time
            ):
                decode_latency = response.decode_end_time - response.decode_start_time
                self._metrics.decode_latency.observe(decode_latency)

                # Create OTEL span for decode operation (child of chunk span)
                create_historical_span(
                    f"Decode - Chunk {response.chunk.chunkIdx}",
                    response.decode_start_time,
                    response.decode_end_time,
                    {
                        "chunk_idx": response.chunk.chunkIdx,
                        "decode_latency_ms": decode_latency * 1000,
                        "source_id": response.chunk.sourceId,
                        "operation": "decode",
                    },
                    parent_context=chunk_parent_ctx or vlm_pipeline_ctx,
                )

        if hasattr(response, "vlm_start_time") and hasattr(response, "vlm_end_time"):
            if (
                response.vlm_start_time
                and response.vlm_end_time
                and response.vlm_end_time > response.vlm_start_time
            ):
                vlm_latency = response.vlm_end_time - response.vlm_start_time
                self._metrics.vlm_latency.observe(vlm_latency)
                create_historical_span(
                    f"RTVI-VLM Inference - Chunk {response.chunk.chunkIdx}",
                    response.vlm_start_time,
                    response.vlm_end_time,
                    {
                        "chunk_idx": response.chunk.chunkIdx,
                        "vlm_latency_ms": vlm_latency * 1000,
                        "source_id": response.chunk.sourceId,
                        "vlm_response_length": (
                            len(response.vlm_response) if response.vlm_response else 0
                        ),
                        "operation": "vlm_inference",
                    },
                    parent_context=chunk_parent_ctx or vlm_pipeline_ctx,
                )

        # Log and observe token usage per chunk if available
        if hasattr(response, "vlm_stats") and response.vlm_stats:
            input_tokens = response.vlm_stats.get("input_tokens", 0)
            output_tokens = response.vlm_stats.get("output_tokens", 0)
            self._metrics.vlm_input_tokens.observe(input_tokens)
            self._metrics.vlm_output_tokens.observe(output_tokens)

        # processing_latency_s == chunk_latency_ms in RTVI, so only one is observed.
        queue_time = getattr(response, "queue_time", 0) or 0
        if queue_time > 0:
            self._metrics.vlm_queue_time.observe(queue_time)
        chunk_total_latency = getattr(response, "rtvi_chunk_latency_s", 0) or 0
        if chunk_total_latency > 0:
            self._metrics.vlm_chunk_total_latency.observe(chunk_total_latency)
        frame_count = getattr(response, "rtvi_frame_count", 0) or 0
        if frame_count > 0:
            self._metrics.vlm_frames_per_chunk.observe(frame_count)

        self._update_stream_fps(response, req_info)
        chunk = response.chunk
        vlm_response = response.vlm_response
        if vlm_response is not None:
            vlm_response = self._sanitize_vlm_response(vlm_response)
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
                self._vlm_pipeline.abort_chunks(req_info.source_id)
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
                logger.debug(
                    "chunkIdx = %s  chunk.start_pts = %s  chunk.end_pts = %s"
                    "  CV metadata length = %d",
                    chunk.chunkIdx,
                    chunk.start_pts,
                    chunk.end_pts,
                    len(cv_meta),
                )
                # Since cv metadata is getting  attached seperately to the context manager,
                # set cached_frames_cv_meta to empty string in chunk
                chunk.cached_frames_cv_meta = ""
                with TimeMeasure("Context Manager - Add Doc"):
                    add_doc_start_time = time.time()
                    _rag_parent_ctx = chunk_parent_ctx or vlm_pipeline_ctx
                    with trace_operation(
                        f"CTX-RAG Add Doc - Chunk {chunk.chunkIdx}",
                        parent_context=_rag_parent_ctx,
                        operation="ctx_rag_add_doc",
                        chunk_idx=chunk.chunkIdx,
                        source_id=req_info.source_id,
                    ):
                        try:
                            req_info._ctx_mgr.add_doc(
                                vlm_response,
                                doc_i=(
                                    chunk.chunkIdx * 2 if req_info.enable_audio else chunk.chunkIdx
                                ),
                                doc_meta=(
                                    vars(chunk)
                                    | {
                                        "uuid": req_info.source_id,
                                        "cv_meta": cv_meta_str,
                                        "camera_id": req_info.camera_id,
                                    }
                                ),
                                callback=lambda output: logger.debug(
                                    f"Summary till now: {output.result()}"
                                ),
                            )
                        except Exception as add_doc_ex:
                            # ES rejected the chunk write
                            # (typically the shard cap). Mirror the
                            # response.error chunk-failure pattern at the
                            # top of this method: mark FAILED, classify
                            # the error for the HTTP layer, abort
                            # remaining chunks. Do NOT return early --
                            # the chunk must still land in
                            # processed_chunk_list so the natural
                            # completion path (line ~1257) eventually
                            # fires _process_output, which is the only
                            # safe place to mark progress=100 and let
                            # check_status_remove_req_id recycle the
                            # ctx_mgr. Returning here would leave
                            # in-flight VLM chunks stranded on a
                            # never-cleaned-up request.
                            self._handle_es_dependency_error(req_info, add_doc_ex)

                    # Gate audio add_doc on req_info.status so a failure
                    # on the video add above doesn't trigger a second
                    # round-trip to a known-broken ES on the same chunk.
                    if (
                        transcript is not None and req_info.status != RequestInfo.Status.FAILED
                    ):  # enable audio

                        if response.audio_transcript:
                            logger.info("Adding audio transcript for chunk %r", chunk)

                        with trace_operation(
                            f"CTX-RAG Add Doc Audio - Chunk {chunk.chunkIdx}",
                            parent_context=_rag_parent_ctx,
                            operation="ctx_rag_add_doc_audio",
                            chunk_idx=chunk.chunkIdx,
                            source_id=req_info.source_id,
                        ):
                            try:
                                req_info._ctx_mgr.add_doc(
                                    transcript,
                                    doc_i=chunk.chunkIdx * 2 + 1,
                                    doc_meta=(
                                        vars(chunk)
                                        | {
                                            "uuid": req_info.source_id,
                                            "cv_meta": cv_meta_str,
                                            "camera_id": req_info.camera_id,
                                        }
                                    ),
                                    callback=lambda output: logger.debug(
                                        f"Summary till now: {output.result()}"
                                    ),
                                )
                            except Exception as add_doc_audio_ex:
                                # See note above: mark FAILED but do not
                                # return; let the chunk reach the natural
                                # completion path so cleanup runs at the
                                # right time.
                                self._handle_es_dependency_error(req_info, add_doc_audio_ex)

                    if req_info._qa_ctx_mgr and req_info.status != RequestInfo.Status.FAILED:
                        plaintext = self._parse_vlm_json_to_plaintext(vlm_response)
                        doc_i = chunk.chunkIdx * 2 if req_info.enable_audio else chunk.chunkIdx
                        try:
                            req_info._qa_ctx_mgr.add_doc(
                                plaintext,
                                doc_i=doc_i,
                                doc_meta=vars(chunk)
                                | {
                                    "uuid": req_info.source_id,
                                    "cv_meta": cv_meta_str,
                                    "camera_id": req_info.camera_id,
                                },
                            )
                        except Exception as qa_ex:
                            logger.warning(
                                "QA add_doc failed for chunk %d: %s",
                                chunk.chunkIdx,
                                qa_ex,
                            )

                    if os.environ.get("VSS_POST_PROCESS_ON_EACH_DOC_ADD", "false").lower() in (
                        "true",
                        "1",
                    ):
                        with trace_operation(
                            f"CTX-RAG Call - Ingestion - Chunk {chunk.chunkIdx}",
                            parent_context=_rag_parent_ctx,
                            operation="ctx_rag_call_ingestion",
                            chunk_idx=chunk.chunkIdx,
                            source_id=req_info.source_id,
                        ):
                            req_info._ctx_mgr.call(
                                {
                                    "ingestion_function": {
                                        "uuid": req_info.source_id,
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
            live_stream_id = req_info.source_id
            with self._lock:
                lsinfo = self._live_stream_info_map.get(live_stream_id)
            if lsinfo is None:
                logger.debug(
                    "Live stream %s already removed, skipping post-chunk processing"
                    " for request %s",
                    live_stream_id,
                    req_info.request_id,
                )
                return

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

            if req_info.processed_chunk_list:
                curIdx = req_info.processed_chunk_list[0].chunk.chunkIdx
                gathered_chunks = 1

                for processed_chunk in req_info.processed_chunk_list[1:]:
                    if processed_chunk.chunk.chunkIdx != curIdx + 1:
                        break
                    curIdx += 1
                    gathered_chunks += 1

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
                "Gathered %d chunks, total chunk duration is %.2f sec for query %s",
                gathered_chunks,
                gathered_chunks_total_duration,
                req_info.request_id,
            )

            with self._lock:
                lsinfo_stopped = lsinfo.stop
            if gathered_chunks > 0 and not lsinfo_stopped:
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
                    last_meta["camera_id"] = req_info.camera_id
                    last_meta["uuid"] = req_info.source_id
                    with trace_operation(
                        "CTX-RAG Add Doc - Live Stream End",
                        parent_context=getattr(req_info, "_e2e_span_context", None),
                        operation="ctx_rag_add_doc_live_end",
                        source_id=req_info.source_id,
                    ):
                        try:
                            req_info._ctx_mgr.add_doc(
                                ".",
                                doc_i=(
                                    last_chunk.chunkIdx * 2
                                    if req_info.enable_audio
                                    else last_chunk.chunkIdx
                                ),
                                doc_meta=last_meta,
                            )
                        except Exception as live_end_add_doc_ex:
                            # ES rejected the end-of-stream
                            # marker. The stream is wrapping up anyway, so
                            # mark the request FAILED so downstream
                            # aggregation does not silently produce an
                            # empty summary.
                            self._handle_es_dependency_error(req_info, live_end_add_doc_ex)
                            return
                # Summary Duration not specified or total duration is greater than summary duration.
                logger.info(
                    "Generating summary for live stream %s request %s with asset id %s",
                    live_stream_id,
                    req_info.request_id,
                    req_info.source_id,
                )

                with self._lock:
                    pending_count = len(lsinfo.pending_futures)
                if pending_count > 1:
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
                with self._lock:
                    lsinfo.pending_futures.append(fut)

                def handle_future_done(fut: concurrent.futures.Future):
                    if fut.cancelled():
                        return
                    if fut.exception():
                        logger.error("".join(traceback.format_exception(fut.exception())))

                def remove_fut_from_pending(done_fut: concurrent.futures.Future):
                    with self._lock:
                        if done_fut in lsinfo.pending_futures:
                            lsinfo.pending_futures.remove(done_fut)

                fut.add_done_callback(handle_future_done)
                fut.add_done_callback(remove_fut_from_pending)
                req_info.processed_chunk_list = req_info.processed_chunk_list[gathered_chunks:]

            if response.is_live_stream_ended:
                with self._lock:
                    lsinfo_stopped = lsinfo.stop
                    pending_snapshot = list(lsinfo.pending_futures)
                if lsinfo_stopped:
                    req_info.status = RequestInfo.Status.STOPPING
                    for fut in pending_snapshot:
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
        req_info.progress = (
            90 * len(req_info.processed_chunk_list) / req_info.chunk_count
            if req_info.chunk_count > 0
            else 0
        )
        logger.info(
            "Processed chunk for query %s, total chunks %d, processed chunks %d, chunk %r,",
            req_info.request_id,
            req_info.chunk_count,
            len(req_info.processed_chunk_list),
            chunk,
        )

        if len(req_info.processed_chunk_list) == req_info.chunk_count:
            # All chunks of file processed
            cur_time = time.time()

            self._finalize_stream_fps_tracking(req_info)

            if req_info.status == RequestInfo.Status.FAILED:
                self._vlm_pipeline.abort_chunks_done(req_info.source_id)
            else:
                logger.info(
                    "Processed all chunks for query %s, VLM pipeline time %.2f sec",
                    req_info.request_id,
                    cur_time - req_info.start_time,
                )
                logger.info("Generating summary for request %s", req_info.request_id)

                # Always update vlm_pipeline_latency metric (decoupled from health eval)
                latency = cur_time - req_info.start_time
                if latency is not None and latency > 0:
                    self._metrics.vlm_pipeline_latency_latest.set(latency)

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

    def _end_vlm_pipeline_span(self, req_info: RequestInfo) -> None:
        """End the 'VLM Pipeline Latency' span if open. Idempotent."""
        span = getattr(req_info, "vlm_pipeline_span", None)
        if span is None:
            return
        try:
            span.end()
        except Exception as e:
            logger.error("Failed to end vlm_pipeline_latency span: %s", e)
        finally:
            req_info.vlm_pipeline_span = None

    def _trigger_query(self, req_info: RequestInfo):
        """Trigger a file-based query via RTVI-VLM SSE streaming."""

        logger.info("Triggering RTVI query %s", req_info.request_id)
        req_info.status = RequestInfo.Status.PROCESSING
        req_info.start_time = time.time()

        # Dense caption cache: load from .dc.json if available
        enable_dense_caption = bool(os.environ.get("ENABLE_DENSE_CAPTION", False))
        if enable_dense_caption:
            dc_file = os.path.join(
                os.environ.get("VIA_LOG_DIR", "/tmp/via-logs"),
                f"dc_{req_info.source_id}.json",
            )
            if os.access(dc_file, os.R_OK):
                logger.info("Dense caption cache hit: %s", dc_file)
                req_info_deserialized = DCSerializer.from_json(dc_file)
                req_info.chunk_count = len(req_info_deserialized.processed_chunk_list)
                for vlm_response in req_info_deserialized.processed_chunk_list:
                    self._on_vlm_chunk_response(vlm_response, req_info)
                return

        # Create OTEL spans for end-to-end and VLM pipeline tracing
        if is_tracing_enabled():
            from opentelemetry import trace

            tracer = get_tracer()
            if tracer:
                req_info._e2e_span = tracer.start_span("Summarization E2E Latency")
                req_info._e2e_span.set_attribute("request_id", req_info.request_id)
                req_info._e2e_span.set_attribute("source_id", req_info.source_id)
                req_info._e2e_span_context = trace.set_span_in_context(req_info._e2e_span)

                req_info.vlm_pipeline_span = tracer.start_span(
                    "VLM Pipeline Latency", context=req_info._e2e_span_context
                )
                req_info.vlm_pipeline_span.set_attribute("request_id", req_info.request_id)
                req_info.vlm_pipeline_span.set_attribute("source_id", req_info.source_id)
                req_info._vlm_pipeline_span_context = trace.set_span_in_context(
                    req_info.vlm_pipeline_span, context=req_info._e2e_span_context
                )

        if req_info._ctx_mgr:
            ca_rag_config = self.update_ca_rag_config(req_info)
            logger.debug("Updating Context Manager with config for RTVI query")
            req_info._ctx_mgr.configure(config=ca_rag_config)

        # Determine whether to use url or id for RTVI
        source_url = req_info.source_url if hasattr(req_info, "source_url") else None
        file_id = req_info.source_id

        # Build VLM generation params from req_info
        gen_config = req_info.vlm_request_params.vlm_generation_config or {}

        try:
            chunk_idx = 0
            model_info = self._vlm_pipeline.get_models_info()
            rtvi_sse_start = time.time()

            # Build media_info dict for RTVI if start/end offsets are set
            rtvi_media_info = None
            if req_info.start_timestamp is not None or req_info.end_timestamp is not None:
                _start = req_info.start_timestamp
                _end = req_info.end_timestamp
                rtvi_media_info = {
                    "type": "offset",
                    "start_offset": int(_start) if _start is not None else None,
                    "end_offset": int(_end) if _end is not None else None,
                }

            # Convert response_format to dict for RTVI
            resp_fmt = getattr(req_info, "response_format", None)
            if resp_fmt and hasattr(resp_fmt, "type"):
                _type = resp_fmt.type
                resp_fmt = {"type": _type.value if hasattr(_type, "value") else _type}

            # Convert stream_options to dict for RTVI
            strm_opts = getattr(req_info, "stream_options", None)
            if strm_opts and hasattr(strm_opts, "model_dump"):
                strm_opts = strm_opts.model_dump()

            # OTEL span: track RTVI SSE streaming duration
            if is_tracing_enabled():
                create_historical_span(
                    "RTVI SSE Connection",
                    rtvi_sse_start,
                    rtvi_sse_start,  # end updated after loop
                    {
                        "request_id": req_info.request_id,
                        "source_id": req_info.source_id,
                        "rtvi_url": self._vlm_pipeline._base_url,
                        "operation": "generate_captions_stream",
                    },
                    parent_context=getattr(req_info, "_vlm_pipeline_span_context", None),
                )

            for sse_chunk in self._vlm_pipeline.generate_captions_stream(
                url=source_url or None,
                file_id=file_id,
                prompt=req_info.vlm_request_params.vlm_prompt or "",
                model=model_info.id,
                chunk_duration=req_info.chunk_size,
                chunk_overlap_duration=req_info.chunk_overlap_duration,
                max_tokens=gen_config.get("max_new_tokens"),
                min_tokens=gen_config.get("min_tokens"),
                temperature=gen_config.get("temperature"),
                top_p=gen_config.get("top_p"),
                top_k=gen_config.get("top_k"),
                seed=gen_config.get("seed"),
                ignore_eos=gen_config.get("ignore_eos"),
                enable_reasoning=gen_config.get("enable_reasoning", False),
                system_prompt=gen_config.get("system_prompt"),
                enable_audio=req_info.enable_audio,
                response_format=resp_fmt,
                stream_options=strm_opts,
                media_info=rtvi_media_info,
                vlm_input_width=req_info.vlm_input_width,
                vlm_input_height=req_info.vlm_input_height,
                num_frames_per_second_or_fixed_frames_chunk=getattr(
                    req_info, "num_frames_per_second_or_fixed_frames_chunk", None
                ),
                use_fps_for_chunking=getattr(req_info, "use_fps_for_chunking", None),
                creation_time=getattr(req_info, "creation_time", None),
                alert_category=getattr(req_info, "alert_category", None),
                media_type="video",
                api_type=getattr(req_info, "api_type", None),
                mm_processor_kwargs=getattr(req_info, "mm_processor_kwargs", None),
            ):
                chunk_responses = sse_chunk.get("chunk_responses", [])
                if not chunk_responses:
                    continue

                for cr in chunk_responses:
                    start_sec = self._parse_rtvi_time(cr.get("start_time", 0))
                    end_sec = self._parse_rtvi_time(cr.get("end_time", 0))

                    chunk_info = ChunkInfo(
                        sourceId=req_info.source_id,
                        chunkIdx=cr.get("chunk_id", chunk_idx),
                        file=req_info.file,
                        start_pts=int(start_sec * 1e9),
                        end_pts=int(end_sec * 1e9),
                        start_ntp=get_timestamp_str(start_sec),
                        end_ntp=get_timestamp_str(end_sec),
                        start_ntp_float=start_sec,
                        end_ntp_float=end_sec,
                        is_first=(chunk_idx == 0),
                    )

                    response = VlmChunkResponse()
                    response.chunk = chunk_info
                    response.vlm_response = cr.get("content", "")
                    response.audio_transcript = cr.get("audio_transcript") or None
                    response.model_info = model_info

                    # Prefer nested `metrics` block (absolute timestamps); fall
                    # back to flat `*_latency_ms` durations anchored at SSE arrival.
                    metrics = cr.get("metrics") or {}
                    decode_start = float(metrics.get("decode_start_time", 0) or 0)
                    decode_end = float(metrics.get("decode_end_time", 0) or 0)
                    vlm_start = float(metrics.get("vlm_start_time", 0) or 0)
                    vlm_end = float(metrics.get("vlm_end_time", 0) or 0)
                    input_tokens = int(metrics.get("input_tokens", 0) or 0)
                    output_tokens = int(metrics.get("output_tokens", 0) or 0)
                    sse_arrival_time = time.time()

                    if not (decode_start and decode_end and vlm_start and vlm_end):
                        decode_latency_ms = cr.get("decode_latency_ms")
                        vlm_latency_ms = cr.get("vlm_latency_ms")
                        if vlm_latency_ms is not None:
                            vlm_end = vlm_end or sse_arrival_time
                            vlm_start = vlm_start or (vlm_end - float(vlm_latency_ms) / 1000.0)
                        if decode_latency_ms is not None:
                            decode_end = decode_end or (vlm_start or sse_arrival_time)
                            decode_start = decode_start or (
                                decode_end - float(decode_latency_ms) / 1000.0
                            )

                    if not input_tokens:
                        input_tokens = int(cr.get("input_tokens", 0) or 0)
                    if not output_tokens:
                        output_tokens = int(cr.get("output_tokens", 0) or 0)

                    if decode_start > 0:
                        response.decode_start_time = decode_start
                    if decode_end > 0:
                        response.decode_end_time = decode_end
                    response.vlm_start_time = vlm_start if vlm_start > 0 else req_info.start_time
                    response.vlm_end_time = vlm_end if vlm_end > 0 else sse_arrival_time

                    if input_tokens or output_tokens:
                        response.vlm_stats = {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        }

                    response.queue_time = float(cr.get("queue_time_s", 0) or 0)
                    response.processing_latency = float(cr.get("processing_latency_s", 0) or 0)
                    response.rtvi_chunk_latency_s = (
                        float(cr.get("chunk_latency_ms", 0) or 0) / 1000.0
                    )
                    response.rtvi_frame_count = int(cr.get("frame_count", 0) or 0)
                    response.reasoning_description = cr.get("reasoning_description") or None

                    rtvi_span_start = vlm_start if vlm_start > 0 else req_info.start_time
                    rtvi_span_end = vlm_end if vlm_end > 0 else sse_arrival_time
                    rtvi_span_attrs = {
                        "chunk_idx": chunk_idx,
                        "source_id": req_info.source_id,
                        "start_time": start_sec,
                        "end_time": end_sec,
                        "content_length": len(response.vlm_response),
                        "operation": "rtvi_chunk_response",
                        "queue_time_s": response.queue_time,
                        "chunk_latency_s": response.rtvi_chunk_latency_s,
                        "frame_count": response.rtvi_frame_count,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    }
                    if response.reasoning_description:
                        rtvi_span_attrs["has_reasoning"] = True
                    create_historical_span(
                        f"RTVI Chunk {chunk_idx}",
                        rtvi_span_start,
                        rtvi_span_end,
                        rtvi_span_attrs,
                        parent_context=getattr(req_info, "_vlm_pipeline_span_context", None),
                    )

                    self._on_vlm_chunk_response(response, req_info)
                    chunk_idx += 1

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as ex:
            logger.error(
                "RTVI dependency is down (url=%s, request_id=%s): %s",
                self._vlm_pipeline._base_url,
                req_info.request_id,
                ex,
            )
            req_info.status = RequestInfo.Status.FAILED
            req_info.error_message = f"RTVI dependency is down at {self._vlm_pipeline._base_url}"
            req_info.rtvi_status_code = 503
            req_info.rtvi_error_code = "DependencyUnavailable"
            req_info.end_time = time.time()
            req_info.progress = 100
            self._metrics.queries_processed.inc()
            self._metrics.queries_pending.dec()
            self._end_vlm_pipeline_span(req_info)
            req_info.status_event.set()
            return
        except Exception as ex:
            logger.error("RTVI query %s failed: %s", req_info.request_id, ex)
            req_info.status = RequestInfo.Status.FAILED
            if isinstance(ex, RtviError):
                req_info.error_message = ex.message
                req_info.rtvi_status_code = ex.status_code
                req_info.rtvi_error_code = ex.code
            else:
                req_info.error_message = str(ex)
            req_info.end_time = time.time()
            req_info.progress = 100
            self._metrics.queries_processed.inc()
            self._metrics.queries_pending.dec()
            self._end_vlm_pipeline_span(req_info)
            req_info.status_event.set()
            return

        req_info.chunk_count = chunk_idx

        logger.info(
            "RTVI query %s: received %d chunks, VLM time %.2fs",
            req_info.request_id,
            chunk_idx,
            time.time() - req_info.start_time,
        )

        # RTVI offline path: chunk_count is otherwise only incremented for live
        # streams (see _on_vlm_chunk_response), so reflect the total here for
        # offline file requests so usage metrics + progress aren't stuck at 0.
        if not req_info.is_live:
            req_info.chunk_count = chunk_idx

        pipeline_latency = time.time() - req_info.start_time
        if pipeline_latency > 0:
            self._metrics.vlm_pipeline_latency_latest.set(pipeline_latency)
        self._end_vlm_pipeline_span(req_info)

        # Dense caption cache: save chunks to .dc.json for future reuse
        if enable_dense_caption and req_info.processed_chunk_list:
            dc_file = os.path.join(
                os.environ.get("VIA_LOG_DIR", "/tmp/via-logs"),
                f"dc_{req_info.source_id}.json",
            )
            DCSerializer.to_json(req_info, dc_file)
            logger.info("Dense caption cache saved: %s (%d chunks)", dc_file, chunk_idx)

        # All chunks received — trigger aggregated summary
        if req_info._output_process_thread_pool:
            req_info._output_process_thread_pool.submit(
                self._process_output, req_info, False, req_info.processed_chunk_list
            )
            req_info._output_process_thread_pool.shutdown(wait=False)

    def start_stream_captions(self, request: GenerateCaptionsRequest):
        """Fire-and-forget captioning kickoff for a stream via RTVI.

        Builds the VLM prompt (auto-generated or raw), calls
        RtviVlmClient.start_captions, and returns immediately. The caller
        (via_server) returns a lightweight acknowledgment to the agent.
        Raises RtviError or ConnectionError on failure.
        """
        stream_id = str(request.id)
        model_info = self._vlm_pipeline.get_models_info()

        if request.override_vlm_prompt:
            caption_prompt = request.prompt
        else:
            caption_prompt = self._create_vlm_prompt(
                prompt=request.prompt,
                enable_vlm_structured_output=request.enable_vlm_structured_output,
                objects_of_interest=request.objects_of_interest or [],
                events=request.events or [],
                scenario=request.scenario or "",
                is_livestream=True,
            )

        gen_config = {}
        if request.max_tokens is not None:
            gen_config["max_new_tokens"] = request.max_tokens
        if request.temperature is not None:
            gen_config["temperature"] = request.temperature
        if request.top_p is not None:
            gen_config["top_p"] = request.top_p
        if request.top_k is not None:
            gen_config["top_k"] = request.top_k
        if request.seed is not None:
            gen_config["seed"] = request.seed

        logger.info(
            "start_stream_captions: stream_id=%s, model=%s, chunk_duration=%d",
            stream_id,
            model_info.id,
            request.chunk_duration,
        )

        start_captions_start = time.time()

        if is_tracing_enabled():
            tracer = get_tracer()
            if tracer:
                span = tracer.start_span("start_stream_captions")
                span.set_attribute("source_id", stream_id)
                span.set_attribute("model", model_info.id)

        try:
            self._vlm_pipeline.start_captions(
                file_id=stream_id,
                prompt=caption_prompt,
                model=model_info.id,
                chunk_duration=request.chunk_duration,
                chunk_overlap_duration=request.chunk_overlap_duration,
                max_tokens=gen_config.get("max_new_tokens"),
                temperature=gen_config.get("temperature"),
                top_p=gen_config.get("top_p"),
                top_k=gen_config.get("top_k"),
                seed=gen_config.get("seed"),
                enable_reasoning=request.enable_reasoning,
                system_prompt=request.system_prompt or None,
                enable_audio=request.enable_audio,
                vlm_input_width=request.vlm_input_width,
                vlm_input_height=request.vlm_input_height,
                num_frames_per_second_or_fixed_frames_chunk=(
                    request.num_frames_per_second_or_fixed_frames_chunk
                ),
                use_fps_for_chunking=request.use_fps_for_chunking or None,
                creation_time=request.creation_time,
                alert_category=request.alert_category,
                media_type="video",
                mm_processor_kwargs=request.mm_processor_kwargs,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as ex:
            logger.error(
                "RTVI dependency is down (url=%s, stream_id=%s): %s",
                self._vlm_pipeline._base_url,
                stream_id,
                ex,
            )
            raise ViaException(
                f"RTVI dependency is down at {self._vlm_pipeline._base_url}",
                "DependencyUnavailable",
                503,
            ) from ex

        create_historical_span(
            "RTVI start_captions (generate_captions API)",
            start_captions_start,
            time.time(),
            {
                "source_id": stream_id,
                "rtvi_url": self._vlm_pipeline._base_url,
                "operation": "start_captions",
            },
        )

        logger.info(
            "start_stream_captions: captioning started for stream_id=%s",
            stream_id,
        )

        self._store_event_prompt_in_db(
            stream_id,
            events_list=request.events or [],
            objects_of_interest=request.objects_of_interest or [],
            scenario=request.scenario or "",
        )

    def _store_event_prompt_in_db(
        self,
        asset_id: str,
        events_list: list,
        objects_of_interest: list | None = None,
        scenario: str = "",
    ) -> None:
        """Write event/scenario/objects metadata as a document to the DB.

        Only writes when ``LVS_DATABASE_BACKEND=elasticsearch_db`` **and**
        ``LVS_DROP_EMPTY_EVENT_FIELDS`` is false (default is true).
        Borrows a CA-RAG context manager from the pool, configures it
        with the stream's uuid, writes a single metadata document, then
        returns the ctx_mgr. Best-effort: failures are logged and
        swallowed so the caller is unaffected.
        """
        db_backend = os.environ.get("LVS_DATABASE_BACKEND", "vector_db")
        drop_empty = os.environ.get("LVS_DROP_EMPTY_EVENT_FIELDS", "true").lower() == "false"
        if db_backend != "elasticsearch_db" or not drop_empty:
            logger.debug(
                "_store_event_prompt_in_db: skipped for %s "
                "(LVS_DATABASE_BACKEND=%s, LVS_DROP_EMPTY_EVENT_FIELDS=%s)",
                asset_id,
                db_backend,
                drop_empty,
            )
            return

        ctx_mgr = None
        try:
            with self._lock:
                self._create_ctx_mgr_pool(self._ca_rag_config)
                if not self._ctx_mgr_pool:
                    logger.warning(
                        "_store_event_prompt_in_db: no ctx_mgr available for %s",
                        asset_id,
                    )
                    return
                ctx_mgr = self._ctx_mgr_pool.pop()

            config = deepcopy(self._ca_rag_config)
            config["context_manager"]["uuid"] = asset_id
            ctx_mgr.configure(config=config)

            events_text = json.dumps({"events": events_list}, ensure_ascii=False)
            ctx_mgr.add_doc(
                events_text,
                doc_i=-1,
                doc_meta={
                    "uuid": asset_id,
                    "doc_type": "event_list",
                    "events_list": events_list,
                    "batch_i": -1,
                    "chunk_idx": -1,
                },
            )
            logger.info(
                "_store_event_prompt_in_db: wrote event_list to DB for %s: %s",
                asset_id,
                events_text,
            )

            if objects_of_interest:
                objects_text = json.dumps(
                    {"objects_of_interest": objects_of_interest}, ensure_ascii=False
                )
                ctx_mgr.add_doc(
                    objects_text,
                    doc_i=-1,
                    doc_meta={
                        "uuid": asset_id,
                        "doc_type": "object_list",
                        "objects_of_interest": objects_of_interest,
                        "batch_i": -1,
                        "chunk_idx": -1,
                    },
                )
                logger.info(
                    "_store_event_prompt_in_db: wrote object_list to DB for %s: %s",
                    asset_id,
                    objects_text,
                )

            scenario_text = json.dumps({"scenario": scenario}, ensure_ascii=False)
            ctx_mgr.add_doc(
                scenario_text,
                doc_i=-1,
                doc_meta={
                    "uuid": asset_id,
                    "doc_type": "scenario",
                    "scenario": scenario,
                    "batch_i": -1,
                    "chunk_idx": -1,
                },
            )
            logger.info(
                "_store_event_prompt_in_db: wrote scenario to DB for %s: %s",
                asset_id,
                scenario_text,
            )
        except Exception as ex:
            logger.warning(
                "_store_event_prompt_in_db failed for %s: %s",
                asset_id,
                ex,
            )
        finally:
            if ctx_mgr is not None:
                with self._lock:
                    self._ctx_mgr_pool.append(ctx_mgr)

    def summarize_stream(self, request: StreamSummarizeRequest):
        """Summarize a live stream by aggregating captions from Elasticsearch via CA-RAG.

        Requires ``KAFKA_ENABLED=true``. Creates a RequestInfo, borrows a
        CA-RAG context manager from the pool, queries the ``summarization``
        function with the stream UUID and optional time window, converts
        event timestamps to ISO 8601, publishes structured_events +
        aggregated_summary back to Kafka, and sets the result on req_info.
        Returns request_id for the caller to wait on.
        """
        if not self._kafka_enabled:
            raise ViaException(
                "Please enable Kafka for live stream summarization",
                "BadRequest",
                400,
            )

        stream_id = str(request.id)
        camera_id = getattr(request, "camera_id", "default") or "default"

        req_info = RequestInfo()
        req_info.source_id = stream_id
        req_info.camera_id = camera_id
        req_info.is_summarization = True
        req_info.summarize = True
        req_info.enable_vlm_structured_output = request.enable_vlm_structured_output
        req_info.summarize_batch_size = request.summarize_batch_size
        req_info.summarize_max_tokens = request.summarize_max_tokens
        req_info.summarize_temperature = request.summarize_temperature
        req_info.summarize_top_p = request.summarize_top_p
        req_info.schema = getattr(request, "schema_field", None)
        req_info.batch_response_method = request.batch_response_method
        req_info.auto_generate_prompt = request.auto_generate_prompt
        req_info.time_metadata_keys = request.time_metadata_keys
        req_info.user_specified_collection_name = request.collection_name
        req_info.custom_metadata = request.custom_metadata
        req_info.delete_external_collection = request.delete_external_collection
        req_info.enable_qa = getattr(request, "enable_qa", False)
        req_info.vlm_request_params = VlmRequestParams()
        req_info.vlm_request_params.vlm_prompt = ""

        start_time = request.start_time
        end_time = request.end_time
        req_info.start_timestamp = (
            start_time if start_time and start_time != 0 and start_time != "" else None
        )
        req_info.end_timestamp = end_time if end_time and end_time != 0 and end_time != "" else None

        req_info.queue_time = time.time()
        with self._lock:
            self._request_info_map[req_info.request_id] = req_info
        self._metrics.queries_pending.inc()

        req_info.status = RequestInfo.Status.PROCESSING
        req_info.start_time = time.time()

        if is_tracing_enabled():
            from opentelemetry import trace

            tracer = get_tracer()
            if tracer:
                req_info._e2e_span = tracer.start_span("Stream Summarization E2E Latency")
                req_info._e2e_span.set_attribute("request_id", req_info.request_id)
                req_info._e2e_span.set_attribute("source_id", stream_id)
                req_info._e2e_span.set_attribute("is_live", True)
                req_info._e2e_span_context = trace.set_span_in_context(req_info._e2e_span)

        ctx_mgr = None
        try:
            with self._lock:
                self._create_ctx_mgr_pool(self._ca_rag_config)
                if not self._ctx_mgr_pool:
                    raise ViaException(
                        "No context manager available in pool",
                        "InternalServerError",
                        500,
                    )
                ctx_mgr = self._ctx_mgr_pool.pop()

            config = deepcopy(self._ca_rag_config)
            config["context_manager"]["uuid"] = req_info.source_id
            ctx_mgr.configure(config=config)

            sub_state: dict = {"uuids": [req_info.source_id]}
            if req_info.start_timestamp is not None:
                sub_state["start_time"] = req_info.start_timestamp
            if req_info.end_timestamp is not None:
                sub_state["end_time"] = req_info.end_timestamp

            logger.info(
                "summarize_stream: stream_id=%s sub_state=%s",
                req_info.source_id,
                sub_state,
            )

            agg_response = ctx_mgr.call({"summarization_online": sub_state})

            if agg_response.get("error"):
                logger.error(
                    "summarize_stream error for %s: %s",
                    req_info.source_id,
                    agg_response.get("error"),
                )
                req_info.status = RequestInfo.Status.FAILED
                req_info.error_message = str(agg_response.get("error"))
            else:
                sub = agg_response.get("summarization_online", {}) or {}
                raw_result = sub.get("result", "")
                metadata = sub.get("metadata", {}) or {}
                req_info.usage = RequestInfo.Usage(**metadata)

                raw_result = self._convert_event_timestamps_to_iso(raw_result)

                events: list = []
                video_summary: str = ""
                if isinstance(raw_result, str) and raw_result:
                    try:
                        parsed = json.loads(raw_result)
                        if isinstance(parsed, dict):
                            events = parsed.get("events", []) or []
                            video_summary = parsed.get("video_summary", "") or ""
                    except (json.JSONDecodeError, TypeError, ValueError) as parse_ex:
                        logger.warning(
                            "summarize_stream: non-JSON result for %s: %s",
                            req_info.source_id,
                            parse_ex,
                        )
                        video_summary = raw_result

                self._publish_aggregate_to_kafka(
                    stream_id=req_info.source_id,
                    camera_id=getattr(req_info, "camera_id", "") or "",
                    events=events,
                    video_summary=video_summary,
                )

                if req_info.enable_qa:
                    qa_ctx = None
                    try:
                        self._create_qa_ctx_mgr_pool(self._ca_rag_config)
                        with self._lock:
                            if self._qa_ctx_mgr_pool:
                                qa_ctx = self._qa_ctx_mgr_pool.pop()
                        if qa_ctx:
                            qa_cfg = deepcopy(self._ca_rag_config)
                            qa_cfg["context_manager"]["uuid"] = req_info.source_id
                            qa_cfg["context_manager"]["functions"] = []
                            fn = "ingestion_function"
                            if fn in qa_cfg.get("functions", {}):
                                qa_cfg["context_manager"]["functions"].append(fn)
                                if "params" not in qa_cfg["functions"][fn]:
                                    qa_cfg["functions"][fn]["params"] = {}
                                qa_cfg["functions"][fn]["params"]["uuid"] = req_info.source_id
                            qa_cfg["functions"].pop("retriever_function", None)
                            qa_ctx.configure(config=qa_cfg)
                            logger.info(
                                "summarize_stream: running ingestion on QA ctx_mgr: %s",
                                req_info.source_id,
                            )
                            qa_ctx.call({"ingestion_function": {"uuid": req_info.source_id}})
                    except Exception as qa_ex:
                        logger.warning(
                            "summarize_stream: QA ingestion failed for %s: %s",
                            req_info.source_id,
                            qa_ex,
                        )
                    finally:
                        if qa_ctx is not None:
                            with self._lock:
                                self._qa_ctx_mgr_pool.append(qa_ctx)

                req_info.response = [
                    RequestInfo.Response(
                        req_info.start_timestamp or "",
                        req_info.end_timestamp or "",
                        raw_result,
                    )
                ]
                req_info.status = RequestInfo.Status.SUCCESSFUL

        except Exception as ex:
            logger.error(
                "summarize_stream failed for %s: %s",
                req_info.source_id,
                ex,
            )
            req_info.status = RequestInfo.Status.FAILED
            req_info.error_message = str(ex)
        finally:
            if ctx_mgr is not None:
                with self._lock:
                    if os.environ.get(
                        "LVS_DISABLE_DB_RESET_ON_REQUEST_DONE", "false"
                    ).lower() not in ("true", "1"):
                        try:
                            ctx_mgr.reset({"summarization": {"uuid": req_info.source_id}})
                        except Exception as reset_ex:
                            logger.warning(
                                "summarize_stream: ctx_mgr.reset failed for %s: %s",
                                req_info.source_id,
                                reset_ex,
                            )
                    self._ctx_mgr_pool.append(ctx_mgr)

        req_info.end_time = time.time()
        req_info.progress = 100
        self._metrics.queries_processed.inc()
        self._metrics.queries_pending.dec()
        req_info.status_event.set()

        return req_info.request_id

    def chat_completion(self, query: ChatCompletionQuery) -> dict:
        """Answer a question over a previously-summarized video/stream using graph-based QA.

        Borrows a QA ctx_mgr from the dedicated QA pool (ingestion + retriever
        only), configures it with the asset UUID, calls retriever_function, and
        returns the response dict.

        The asset must have been previously processed with ``enable_qa=true``
        so that the knowledge graph was built by ``ingestion_function``.
        """
        if self._args.disable_ca_rag:
            raise ViaException(
                "CA-RAG is required for chat completions (QA)",
                "BadConfiguration",
                400,
            )

        asset_id = str(query.id)

        question = ""
        for msg in reversed(query.messages):
            if msg.role == "user":
                question = msg.content
                break
        if not question:
            raise ViaException(
                "No user message found in messages",
                "InvalidParameters",
                422,
            )

        logger.info(
            "chat_completion: asset_id=%s, question=%.200s",
            asset_id,
            question,
        )

        qa_ctx = None
        try:
            self._create_qa_ctx_mgr_pool(self._ca_rag_config)
            with self._lock:
                if not self._qa_ctx_mgr_pool:
                    raise ViaException(
                        "No QA context manager available in pool",
                        "InternalServerError",
                        500,
                    )
                qa_ctx = self._qa_ctx_mgr_pool.pop()

            qa_config = deepcopy(self._ca_rag_config)
            qa_config["context_manager"]["uuid"] = asset_id
            qa_config["context_manager"]["functions"] = []
            fn = "retriever_function"
            if fn in qa_config.get("functions", {}):
                qa_config["context_manager"]["functions"].append(fn)
                qa_config["functions"][fn]["params"]["uuid"] = asset_id
            qa_config["functions"].pop("ingestion_function", None)
            qa_ctx.configure(config=qa_config)

            with TimeMeasure("Chat Completion - Retriever Function"):
                result = qa_ctx.call(
                    {
                        "retriever_function": {
                            "question": question,
                            "is_live": query.is_live,
                            "is_last": False,
                        }
                    }
                )

            if result.get("error"):
                raise ViaException(
                    f"Retriever function error: {result.get('error')}",
                    "InternalServerError",
                    500,
                )

            retriever_result = result.get("retriever_function", {})
            answer, reasoning = self._remove_think_tags(retriever_result.get("response", ""))

            logger.info(
                "chat_completion: asset_id=%s answer_length=%d",
                asset_id,
                len(answer),
            )

            resp = {"answer": answer, "metadata": retriever_result.get("metadata", {})}
            if reasoning:
                resp["reasoning"] = reasoning
            return resp

        except ViaException:
            raise
        except Exception as ex:
            logger.error("chat_completion failed for %s: %s", asset_id, ex)
            raise ViaException(
                f"Chat completion failed: {ex}",
                "InternalServerError",
                500,
            ) from ex
        finally:
            if qa_ctx is not None:
                with self._lock:
                    self._qa_ctx_mgr_pool.append(qa_ctx)

    def _publish_aggregate_to_kafka(
        self,
        stream_id: str,
        camera_id: str,
        events: list,
        video_summary: str,
    ) -> None:
        """Publish the summarize_live_stream result to Kafka.

        Splits ``events`` into ``max_events_per_batch`` batches and sends one
        ``doc_type=structured_events`` Kafka message per batch on the
        ``KAFKA_STRUCTURED_SUMMARY_TOPIC`` (default
        ``mdx-structured-events-summary``), then sends a single
        ``doc_type=aggregated_summary`` carrying the narrative video_summary.

        Best-effort: failures are logged and swallowed so the synchronous
        HTTP response is never blocked by Kafka producer errors. No-op when
        KAFKA_ENABLED is false or the producer was not constructed.
        """
        if not (
            self._kafka_enabled
            and self._kafka_producer is not None
            and self._kafka_producer.enabled
        ):
            return

        collection = _safe_collection_name(stream_id)
        try:
            batch_size = int(
                self._ca_rag_config.get("functions", {})
                .get("summarization", {})
                .get("params", {})
                .get("max_events_per_batch", 50)
            )
        except (AttributeError, ValueError, TypeError):
            batch_size = 50
        batch_size = max(batch_size, 1)

        events = events or []
        try:
            for batch_start in range(0, max(len(events), 1), batch_size):
                batch = events[batch_start : batch_start + batch_size]
                batch_text = json.dumps({"events": batch}, indent=2, ensure_ascii=False)
                batch_meta = {
                    "chunkIdx": -1,
                    "batch_i": batch_start // batch_size,
                    "uuid": stream_id,
                    "camera_id": camera_id or "",
                    "event_count": len(batch),
                }
                self._kafka_producer.publish_structured_doc(
                    text=batch_text,
                    metadata=batch_meta,
                    collection_name=collection,
                    doc_type="structured_events",
                    batch_i=batch_start // batch_size,
                )

            summary_meta = {
                "uuid": stream_id,
                "camera_id": camera_id or "",
                "total_events": len(events),
            }
            self._kafka_producer.publish_structured_doc(
                text=video_summary or "",
                metadata=summary_meta,
                collection_name=collection,
                doc_type="aggregated_summary",
            )
            self._kafka_producer.flush()
            logger.info(
                "summarize_live_stream: published %d structured_events "
                "batch(es) and 1 aggregated_summary to Kafka for stream %s",
                max(1, (len(events) + batch_size - 1) // batch_size),
                stream_id,
            )
        except Exception as ex:
            # A Kafka publish failure here used to be
            # silently swallowed. Logstash-side ES rejections (e.g. the
            # shard cap) only surface back to LVS through this path for
            # the structured/aggregated docs, so swallowing them hid the
            # very failures that took down the 12-hour stress run.
            # Classify and re-raise so the caller (summarize_stream's
            # outer try/except, or the file-path Kafka-mode aggregator)
            # marks the request as FAILED instead of returning empty.
            http_status, user_message = classify_es_error(ex)
            logger.error(
                "summarize_live_stream: publish to Kafka failed for stream %s: %s",
                stream_id,
                ex,
            )
            raise ViaException(user_message, "DependencyError", http_status) from ex

    def _handle_es_dependency_error(
        self, req_info: "RequestInfo", exc: BaseException
    ) -> tuple[int, str]:
        """Classify an ES dependency error and mark the request as FAILED.

        When a downstream call to Elasticsearch (in-process
        ``add_doc -> ElasticsearchDBTool.add_summary``, the
        ``vlm_structured_summarization_online`` aggregator's
        ``retrieve_docs``, or the Kafka publish path) raises, this helper:

        - Classifies the underlying exception via :func:`classify_es_error`
          (which logs the full status / detail at ERROR for triage).
        - Sets ``req_info.status = FAILED`` and ``error_message`` so the
          HTTP layer can surface the classified message.
        - Stashes ``dependency_http_status`` / ``dependency_error_code``
          on req_info so :mod:`via_server` can return 503 instead of the
          generic 500.
        - Calls ``vlm_pipeline.abort_chunks(source_id)`` for the file path
          to stop subsequent VLM chunks landing on a doomed request.
          Live-stream requests don't abort because new chunks may still
          arrive after a transient ES failure.

        Intentionally does NOT touch ``req_info.progress`` or
        ``req_info.status_event`` here. Doing so from the per-chunk
        callback path would trigger the HTTP handler's
        ``check_status_remove_req_id`` cleanup before in-flight VLM
        chunks have actually stopped, leaving them holding a
        ``req_info._ctx_mgr`` reference that has been reset and
        returned to the shared pool. The natural ``_process_output``
        completion path sets ``progress=100`` and
        ``wait_for_request_done`` polls ``status`` on a 5s tick, so
        FAILED requests still surface the 503 to the user without the
        chunk-callback race.

        Returns the ``(http_status, user_message)`` tuple from
        :func:`classify_es_error` so the caller can re-raise a
        :class:`ViaException` with the right shape.
        """
        http_status, user_message = classify_es_error(exc)
        req_info.status = RequestInfo.Status.FAILED
        req_info.error_message = user_message
        req_info.dependency_http_status = http_status
        req_info.dependency_error_code = "DependencyError"
        if not req_info.is_live:
            try:
                self._vlm_pipeline.abort_chunks(req_info.source_id)
            except Exception as abort_ex:
                logger.warning(
                    "abort_chunks failed for %s after ES error: %s",
                    req_info.source_id,
                    abort_ex,
                )
        return http_status, user_message

    def drop_collection_for_asset(self, asset_id: str, *, force_legacy: bool = False) -> dict:
        """Drop the Elasticsearch index/collection associated with `asset_id`.

        Used by /files DELETE (and stream removal) when KAFKA_ENABLED=true so
        that documents Logstash wrote into ``default_<asset_id>`` are removed
        alongside the asset. By default this is a no-op when KAFKA_ENABLED
        is false because the legacy in-process path historically relied on
        ``reset({uuid:...})`` per request to clean up.

        Set ``force_legacy=True`` to bypass the KAFKA_ENABLED guard. This
        is intended ONLY for the file-path completion handler, which
        needs to drop the per-file ``default_<file_id>`` index after each
        summarize so the cluster shard pool drains as fast as it fills.
        Other callers (DELETE /files, live-stream teardown) keep their
        existing semantics by leaving the flag at its default.

        Returns the context-manager subprocess response dict; idempotent on
        a missing index.
        """
        if not self._kafka_enabled and not force_legacy:
            return {"skipped": True, "reason": "KAFKA_ENABLED=false"}

        if self._args.disable_ca_rag:
            return {"skipped": True, "reason": "ca-rag disabled"}

        ctx_mgr = None
        try:
            with self._lock:
                self._create_ctx_mgr_pool(self._ca_rag_config)
                if not self._ctx_mgr_pool:
                    return {"error": "no context manager available in pool"}
                ctx_mgr = self._ctx_mgr_pool.pop()

            config = deepcopy(self._ca_rag_config)
            config["context_manager"]["uuid"] = asset_id
            ctx_mgr.configure(config=config)
            result = ctx_mgr.drop_collection()
            logger.info("drop_collection for asset_id=%s -> %s", asset_id, result)
            return result
        except Exception as ex:
            logger.warning("drop_collection_for_asset failed for %s: %s", asset_id, ex)
            return {"error": str(ex)}
        finally:
            if ctx_mgr is not None:
                with self._lock:
                    self._ctx_mgr_pool.append(ctx_mgr)

    def get_ctx_mgr(self, source_id: str) -> None:
        """
        Return a ContextManager associated with the given source_id.
        """
        with self._lock:
            for _, request_info in self._request_info_map.items():
                if request_info.source_id == source_id:
                    # Remove old data for the same source
                    if request_info.summarize:
                        request_info._ctx_mgr.reset(
                            {
                                "summarization": {"uuid": request_info.source_id},
                            }
                        )
                    return request_info._ctx_mgr
            # If ctx mgr not found in request info map
            logger.info(f"Getting new Context Manager for {source_id}")
            return self._ctx_mgr_pool.pop()

    def remove_request_id(self, request_id: str) -> None:
        """Remove request info for a single request ID"""
        with self._lock:
            if request_id in self._request_info_map:
                del self._request_info_map[request_id]

    def summarize(
        self,
        source: RequestSource,
        query: SummarizationQuery,
    ):
        """Run a summarization query on a file"""
        # Enable summarization if summarization config is enabled  OR API passes enable flag
        # Enable summarization if none provided
        if self._ctx_mgr:
            summarize_enable = "summarization" in self._ca_rag_config.get(
                "context_manager", {}
            ).get("functions", [])
            if query.summarize is None:
                query.summarize = summarize_enable
        if not query.prompt:
            query.prompt = self.default_caption_prompt

        return self.query(
            source=source,
            query=query,
            is_summarization=True,
        )

    def query(
        self,
        source: RequestSource,
        query: SummarizationQuery,
        is_summarization=False,
        skip_ca_rag=False,
    ):
        """Run a query on a file"""

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

        if (
            query.chunk_duration > 0
            and query.chunk_overlap_duration > 0
            and query.chunk_overlap_duration >= query.chunk_duration
        ):
            raise ViaException(
                "chunkOverlapDuration must be less than chunkDuration", "BadParameter", 400
            )

        vlm_generation_config = {}
        # Extract user specified llm output parameters
        if query.max_tokens is not None:
            vlm_generation_config["max_new_tokens"] = query.max_tokens
        if query.min_tokens is not None:
            vlm_generation_config["min_tokens"] = query.min_tokens
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
        if query.ignore_eos is not None:
            vlm_generation_config["ignore_eos"] = query.ignore_eos

        # Create a RequestInfo object and populate it
        req_info = RequestInfo()
        req_info.file = source.url or ""
        req_info.chunk_size = query.chunk_duration
        req_info.is_summarization = is_summarization
        req_info.override_vlm_prompt = query.override_vlm_prompt
        if req_info.override_vlm_prompt:
            req_info.vlm_request_params.vlm_prompt = query.prompt
        else:
            req_info.vlm_request_params.vlm_prompt = self._create_vlm_prompt(
                prompt=query.prompt,
                enable_vlm_structured_output=query.enable_vlm_structured_output,
                objects_of_interest=query.objects_of_interest,
                events=query.events,
                scenario=query.scenario,
                enable_audio=query.enable_audio,
            )
        req_info.vlm_request_params.vlm_generation_config = vlm_generation_config
        req_info.source_id = source.source_id
        req_info.camera_id = source.camera_id
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
        req_info._output_process_thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        req_info.summarize = query.summarize
        req_info.summarize_batch_size = query.summarize_batch_size
        req_info.vlm_input_width = query.vlm_input_width
        req_info.vlm_input_height = query.vlm_input_height
        req_info.enable_audio = query.enable_audio
        req_info.response_format = getattr(query, "response_format", None)
        req_info.stream_options = getattr(query, "stream_options", None)
        req_info.api_type = getattr(query, "api_type", "")
        req_info.num_frames_per_second_or_fixed_frames_chunk = getattr(
            query, "num_frames_per_second_or_fixed_frames_chunk", None
        )
        req_info.use_fps_for_chunking = getattr(query, "use_fps_for_chunking", False)
        req_info.creation_time = getattr(query, "creation_time", None)
        req_info.alert_category = getattr(query, "alert_category", None)
        req_info.mm_processor_kwargs = getattr(query, "mm_processor_kwargs", None)
        req_info.user_specified_collection_name = query.collection_name
        req_info.custom_metadata = query.custom_metadata
        req_info.delete_external_collection = query.delete_external_collection
        req_info.schema = query.schema
        req_info.batch_response_method = query.batch_response_method
        req_info.scenario = query.scenario
        req_info.events = query.events
        req_info.auto_generate_prompt = query.auto_generate_prompt
        req_info.time_metadata_keys = query.time_metadata_keys
        req_info.enable_vlm_structured_output = query.enable_vlm_structured_output
        req_info.objects_of_interest = query.objects_of_interest
        req_info.enable_qa = getattr(query, "enable_qa", False)
        if not self._args.disable_ca_rag and not skip_ca_rag:
            with self._lock:
                self._create_ctx_mgr_pool(self._ca_rag_config)
                req_info._ctx_mgr = self.get_ctx_mgr(req_info.source_id)
            try:
                config = deepcopy(self._ca_rag_config)
                config["context_manager"]["uuid"] = req_info.source_id
                req_info._ctx_mgr.configure(config=config)
            except Exception as ex:
                logger.error(traceback.format_exc())
                logger.error("Query failed for %s - %s", req_info.request_id, str(ex))
                if req_info._ctx_mgr is not None:
                    with self._lock:
                        self._ctx_mgr_pool.append(req_info._ctx_mgr)
                        req_info._ctx_mgr = None
                return req_info.request_id
            # Reset the context manager for the first time
            if self.first_init and os.environ.get(
                "VSS_DISABLE_DB_RESET_ON_INIT", "false"
            ).lower() not in ["true", "1"]:
                self.first_init = False
                req_info._ctx_mgr.reset(
                    {
                        "summarization": {"erase_db": True},
                    }
                )

            if req_info.enable_qa:
                try:
                    self._create_qa_ctx_mgr_pool(self._ca_rag_config)
                    with self._lock:
                        if not self._qa_ctx_mgr_pool:
                            raise ViaException(
                                "No QA context manager available", "InternalServerError", 500
                            )
                        req_info._qa_ctx_mgr = self._qa_ctx_mgr_pool.pop()
                    qa_config = deepcopy(self._ca_rag_config)
                    qa_config["context_manager"]["uuid"] = req_info.source_id
                    qa_config["context_manager"]["functions"] = []
                    fn = "ingestion_function"
                    if fn in qa_config.get("functions", {}):
                        qa_config["context_manager"]["functions"].append(fn)
                        if "params" not in qa_config["functions"][fn]:
                            qa_config["functions"][fn]["params"] = {}
                        qa_config["functions"][fn]["params"]["uuid"] = req_info.source_id
                    qa_config["functions"].pop("retriever_function", None)
                    req_info._qa_ctx_mgr.configure(config=qa_config)
                    logger.info("Borrowed QA ctx_mgr for source_id=%s", req_info.source_id)
                except ViaException:
                    raise
                except Exception as ex:
                    logger.error("Failed to configure QA ctx_mgr: %s", ex)

        req_info.summarize_top_p = query.summarize_top_p
        req_info.summarize_temperature = query.summarize_temperature
        req_info.summarize_max_tokens = query.summarize_max_tokens

        req_info.chunk_overlap_duration = query.chunk_overlap_duration

        req_info.queue_time = time.time()
        # Adding the request info to the request info map
        with self._lock:
            self._request_info_map[req_info.request_id] = req_info

        # Add the request to the pending queue
        self._metrics.queries_pending.inc()

        req_info.source_url = source.url or ""

        self._store_event_prompt_in_db(
            req_info.source_id,
            req_info.events or [],
            req_info.objects_of_interest or [],
            req_info.scenario or "",
        )

        # url may be None for id-only requests (asset pre-uploaded via /files)
        self._trigger_query(req_info)

        return req_info.request_id

    def generate_vlm_captions(
        self, source: RequestSource, query: SummarizationQuery, is_rtsp=False
    ):
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
            raise ViaException(
                "generate_vlm_captions is not supported for live streams. "
                "Use POST /v1/generate_captions instead.",
                "NotImplemented",
                501,
            )

        return self.query(
            source=source,
            query=query,
            is_summarization=False,
            skip_ca_rag=True,
        )

    def _create_vlm_prompt(
        self,
        prompt: str,
        enable_vlm_structured_output: bool,
        objects_of_interest: list[str],
        events: list[str],
        scenario: str,
        is_livestream: bool = False,
        enable_audio: bool = False,
    ):
        """Create a VLM prompt based on the user supplied prompt
        and enable_vlm_structured_output and objects_of_interest.

        Supports environment variable overrides:
        - LVS_PROMPT_VLM_ROLE: Override the AI role/persona description
        - LVS_PROMPT_VLM_INSTRUCTION: Override the instruction section
        - LVS_PROMPT_VLM_CONSTRAINTS: Add additional constraints (empty by default)
        - LVS_PROMPT_VLM_STRUCTURED_OUTPUT: Override output format and JSON template

        Template variables available for substitution in custom prompts:
        - {scenario}: The scenario parameter
        - {events}: Comma-separated list of events
        - {objects_of_interest}: Comma-separated list of objects of interest
        """
        if not enable_vlm_structured_output:
            return prompt

        # Build comma-separated lists for template variable substitution
        # Coerce to strings to handle None or non-string values in lists
        events_csv = ", ".join(str(e) for e in events) if events else ""
        objects_csv = ", ".join(str(o) for o in objects_of_interest) if objects_of_interest else ""

        # Default prompt sections
        default_role = (
            "You are an advanced intelligent video analysis system specialized in "
            "analyzing {scenario} and generating captions."
        )

        default_instruction = (
            "Focus on detecting these events: {events}. "
            "Also watch for these objects of interest: {objects_of_interest}. "
            "Describe all events and objects of "
            "interest throughout the video."
        )

        if enable_audio:
            default_instruction += (
                " Additionally, include relevant audio transcripts from the video chunk. "
                "If speech or meaningful audio is present, transcribe it and incorporate "
                "the transcript into your event descriptions."
            )

        # Constraints is empty by default - users can add additional constraints via env var
        default_constraints = ""

        # Timestamp format varies: seconds for video files, ISO 8601 for livestreams
        if is_livestream:
            time_format_instruction = (
                "Provide the result in json format with timestamp in ISO 8601 format "
                '(e.g. "2026-04-30T10:39:20.934Z") for demarcation of each event.'
            )
            time_example_start = '"ISO 8601 timestamp"'
            time_example_end = '"ISO 8601 timestamp"'
        else:
            time_format_instruction = (
                "Provide the result in json format with 'seconds' for time depiction "
                "for each event."
            )
            time_example_start = "t_start"
            time_example_end = "t_end"

        default_structured_output = f"""\
{time_format_instruction} \
Use keywords 'start_time', 'end_time', 'description', "type" \
in the json output. "type" should be the event type and chosen from \
[{{events}}] only. You MUST include 'type', 'start_time', 'end_time', 'description' in json output. \
This is very important and you must follow this strictly.

[
{{
 "start_time": {time_example_start}, #(MANDATORY)
 "end_time": {time_example_end}, #(MANDATORY)
 "type": Choose from [{{events}}] #(MANDATORY)
 "description": EVENT1, #(MANDATORY)
}},
]"""

        # Read env vars - use default if not set or empty/whitespace
        env_role = os.environ.get("LVS_PROMPT_VLM_ROLE", "").strip()
        env_instruction = os.environ.get("LVS_PROMPT_VLM_INSTRUCTION", "").strip()
        env_constraints = os.environ.get("LVS_PROMPT_VLM_CONSTRAINTS", "").strip()
        env_structured_output = os.environ.get("LVS_PROMPT_VLM_STRUCTURED_OUTPUT", "").strip()

        # Use env value if set (non-empty after strip), otherwise use default
        role = env_role if env_role else default_role
        instruction = env_instruction if env_instruction else default_instruction
        constraints = env_constraints if env_constraints else default_constraints
        structured_output = (
            env_structured_output if env_structured_output else default_structured_output
        )

        # Helper function for safe template variable substitution
        # Uses str.replace() instead of .format() to handle literal braces in JSON
        # Coerce values to strings to prevent TypeError when inputs are None or non-string
        def substitute_vars(text: str) -> str:
            result = text
            result = result.replace("{scenario}", str(scenario) if scenario else "")
            result = result.replace("{events}", str(events_csv) if events_csv else "")
            result = result.replace(
                "{objects_of_interest}", str(objects_csv) if objects_csv else ""
            )
            return result

        # Apply template variable substitution to all sections
        formatted_role = substitute_vars(role)
        formatted_instruction = substitute_vars(instruction)
        formatted_constraints = substitute_vars(constraints)
        formatted_structured_output = substitute_vars(structured_output)

        # Build the final prompt with sections separated by blank lines
        logger.info(
            "Final prompt: %s\n\n%s\n\n%s\n\n%s\n",
            formatted_role,
            formatted_instruction,
            formatted_constraints,
            formatted_structured_output,
        )
        return f"{formatted_role}\n\n\
            {formatted_instruction}\n\n\
            {formatted_constraints}\n\n\
            {formatted_structured_output}\n"

    def _update_completion_metrics(self, req_info, chunk_responses: list[VlmChunkResponse]):
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

        e2e_latency = time.time() - req_info.start_time if req_info.start_time else 0
        self._metrics.e2e_latency_latest.set(e2e_latency)
        self._metrics.ca_rag_latency_latest.set(req_info._ca_rag_latency)

        if chunk_responses:
            max_decode_end_time = find_extreme(chunk_responses, max, "decode_end_time")
            min_decode_start_time = find_extreme(chunk_responses, min, "decode_start_time")
            decode_latency = max_decode_end_time - min_decode_start_time
            self._metrics.decode_latency_latest.set(decode_latency)

            max_vlm_end_time = find_extreme(chunk_responses, max, "vlm_end_time")
            min_vlm_start_time = find_extreme(chunk_responses, min, "vlm_start_time")
            vlm_latency = max_vlm_end_time - min_vlm_start_time
            self._metrics.vlm_latency_latest.set(vlm_latency)

    def add_rtsp_stream(self, source_id: str, chunk_size: int):
        """Register a live stream so generate_vlm_captions can attach a query.

        Creates a LiveStreamInfo entry in _live_stream_info_map which is
        required by generate_vlm_captions and remove_rtsp_stream.
        """
        with self._lock:
            if source_id in self._live_stream_info_map:
                existing = self._live_stream_info_map[source_id]
                if existing.req_info:
                    raise ViaException(
                        "Live stream already has query "
                        f"'{existing.req_info[0].request_id}' running."
                        " Update or stop the same query.",
                        "BadParameters",
                        400,
                    )
                # Previous entry exists but has no active request — clean it up
                self._live_stream_info_map.pop(source_id)
                logger.info("Cleaned up stale live stream entry for %s", source_id)

            if len(self._live_stream_info_map) >= self._args.max_live_streams:
                raise ViaException(
                    "Server is already processing maximum number of live streams"
                    f" ({self._args.max_live_streams})",
                    "ServerBusy",
                    503,
                )

            if chunk_size is None or chunk_size == 0:
                raise ViaException(
                    "Non-zero chunk duration required for live-stream", "InvalidParameter", 400
                )

            live_stream_info = LiveStreamInfo()
            live_stream_info.chunk_size = chunk_size
            live_stream_info.source_id = source_id

            self._live_stream_info_map[source_id] = live_stream_info

    def remove_rtsp_stream(self, source_id: str):
        """Remove a live stream from the server"""
        with self._lock:
            if source_id not in self._live_stream_info_map:
                logger.debug(f"Live stream {source_id} not active")
                return
            logger.info("Removing live stream %s from pipeline", source_id)
            live_stream_info = self._live_stream_info_map[source_id]
        live_stream_info.stop = True

        self._vlm_pipeline.remove_live_stream(source_id)

        with self._lock:
            self._live_stream_info_map.pop(source_id)

        logger.info("Removed live stream %s from pipeline", source_id)

        ctx_mgrs_to_be_removed = []
        with self._lock:
            for req_info in self._request_info_map.values():
                if req_info.source_id == source_id and req_info._ctx_mgr:
                    ctx_mgrs_to_be_removed.append((req_info._ctx_mgr, req_info))
                    req_info._ctx_mgr = None
            self._request_info_map = {
                req_id: req_info
                for req_id, req_info in self._request_info_map.items()
                if req_info.source_id != source_id
            }
        for ctx_mgr, req_info in ctx_mgrs_to_be_removed:
            try:
                if req_info.summarize:
                    ctx_mgr.reset(
                        {
                            "summarization": {"uuid": req_info.source_id},
                            "delete_external_collection": req_info.delete_external_collection,
                        }
                    )
            except Exception as ex:
                logger.error(
                    "ctx_mgr.reset failed during remove_rtsp_stream for source_id=%s: %s",
                    req_info.source_id,
                    ex,
                )
            finally:
                with self._lock:
                    logger.info(
                        f"Adding Context Manager no.: {ctx_mgr._process_index} back to process pool."
                    )
                    self._ctx_mgr_pool.append(ctx_mgr)
        try:
            shutil.rmtree(f"/tmp/via/cached_frames/{source_id}")
        except FileNotFoundError:
            pass

    def stop(self, force=False):
        """Stop the VIA Stream Handler"""
        self._running = False
        safe_log(logger, "info", "Stopping VIA Stream Handler")

        lsinfo_to_be_removed = list(self._live_stream_info_map.values())
        for lsinfo in lsinfo_to_be_removed:
            self.remove_rtsp_stream(lsinfo.source_id)

        if hasattr(self, "_vlm_pipeline") and self._vlm_pipeline is not None:
            self._vlm_pipeline.stop(force=True)

        if hasattr(self, "_kafka_producer") and self._kafka_producer is not None:
            try:
                self._kafka_producer.close()
            except Exception as ex:
                logger.warning("Error closing Kafka producer: %s", ex)
            self._kafka_producer = None

        self._metrics.unregister()

        self._ctx_mgr = None

        for ctx_mgr in self._ctx_mgr_pool:
            try:
                ctx_mgr.process.kill()
                ctx_mgr.process.join(timeout=2)
            except Exception as e:
                safe_log(
                    logger,
                    "error",
                    "Error shutting down context manager for request %s: %s",
                    ctx_mgr._process_index,
                    e,
                )

        for qa_ctx in self._qa_ctx_mgr_pool:
            try:
                qa_ctx.process.kill()
                qa_ctx.process.join(timeout=2)
            except Exception as e:
                safe_log(
                    logger,
                    "error",
                    "Error shutting down QA context manager %s: %s",
                    qa_ctx._process_index,
                    e,
                )

        for req_info in self._request_info_map.values():
            if req_info._ctx_mgr:
                try:
                    req_info._ctx_mgr.process.kill()
                    req_info._ctx_mgr.process.join(timeout=2)
                except Exception as e:
                    safe_log(
                        logger,
                        "error",
                        "Error shutting down context manager for request %s: %s",
                        req_info._ctx_mgr._process_index,
                        e,
                    )

        safe_log(logger, "info", "Stopped VIA Stream Handler")

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
            # If request for file summarization has completed
            lsinfo = self._live_stream_info_map.get(req_info.source_id)
            if (
                (not req_info.is_live and req_info.progress == 100)
                or (
                    req_info.is_live
                    and lsinfo is not None
                    and lsinfo.live_stream_ended
                    and len(req_info.response) == 0
                )
                or (req_info.is_live and lsinfo is None)
            ):
                # Remove only this specific request, not all requests for the same asset
                # This allows concurrent processing of the same asset by multiple requests
                self.remove_request_id(request_id)
                if req_info._ctx_mgr:
                    if not os.environ.get(
                        "LVS_DISABLE_DB_RESET_ON_REQUEST_DONE", "false"
                    ).lower() in [
                        "true",
                        "1",
                    ]:  # noqa: E501
                        req_info._ctx_mgr.reset(
                            {
                                "summarization": {"uuid": req_info.source_id},
                                "delete_external_collection": req_info.delete_external_collection,
                            }
                        )
                        # Drop the per-file Elasticsearch
                        # index after the summarize completes so the
                        # cluster shard pool drains as fast as it fills.
                        # Strictly file-path only; live-stream summarize
                        # completion never triggers this drop because
                        # streams reuse the same source_id across multiple
                        # /v1/stream_summarize calls. force_legacy=True
                        # bypasses drop_collection_for_asset's KAFKA_ENABLED
                        # guard so the legacy in-process file path also
                        # benefits — both paths create per-file indices.
                        if not req_info.is_live:
                            try:
                                self.drop_collection_for_asset(
                                    req_info.source_id, force_legacy=True
                                )
                            except Exception as drop_ex:
                                logger.warning(
                                    "post-summarize drop_collection_for_asset" " failed for %s: %s",
                                    req_info.source_id,
                                    drop_ex,
                                )
                    self._ctx_mgr_pool.append(req_info._ctx_mgr)
                    logger.info(
                        f"Returning Context Manager Process"
                        f"{req_info._ctx_mgr._process_index} to process pool"
                    )
                if req_info._qa_ctx_mgr:
                    self._qa_ctx_mgr_pool.append(req_info._qa_ctx_mgr)
                    logger.info(
                        "Returning QA Context Manager Process%s to QA pool",
                        req_info._qa_ctx_mgr._process_index,
                    )
                    req_info._qa_ctx_mgr = None

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

    def extract_json_from_vlm_response(self, vlm_response: str) -> str:
        """Extract and clean JSON from vlm_response"""
        # Remove markdown code blocks if present
        content = re.sub(r"```(?:json)?\s*\n(.*?)\n```", r"\1", vlm_response, flags=re.DOTALL)

        # Find JSON content by braces if not already clean
        if not content.strip().startswith(("{", "[")):
            json_start = content.find("{")
            json_end = content.rfind("}")
            if json_start != -1 and json_end != -1 and json_end > json_start:
                content = content[json_start : json_end + 1]

        # Clean escaped characters
        return content.strip().replace("\\n", "\n").replace('\\"', '"')

    def format_response(self, response: str, max_chars: int = 1000000):
        """Format the response to fit within max_chars limit.

        If the response exceeds max_chars:
        1. Remove the 'events' list (as it consumes many characters)
        2. Truncate 'video_summary' to fit within the limit

        Args:
            response: JSON string containing events, total_events, video_summary, uuid
            max_chars: Maximum character limit for the output (default: 1000000)

        Returns:
            Formatted JSON string within the character limit
        """
        if len(response) <= max_chars:
            return response

        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            # If not valid JSON, truncate the raw string
            return response[:max_chars]

        # First, try removing events list
        if "events" in data:
            data_without_events = {
                "events": [],
                "total_events": data.get("total_events", 0),
                "video_summary": data.get("video_summary", ""),
                "uuid": data.get("uuid", ""),
            }

            result = json.dumps(data_without_events)

            if len(result) <= max_chars:
                return result

            # Still too large, need to truncate video_summary
            # Calculate overhead (JSON structure without video_summary content)
            data_without_events["video_summary"] = ""
            overhead = len(json.dumps(data_without_events))

            # Available chars for video_summary (accounting for potential escaping)
            available_chars = max_chars - overhead - 10  # 10 chars buffer for safety

            if available_chars > 0:
                video_summary = data.get("video_summary", "")
                # Truncate and add ellipsis
                if len(video_summary) > available_chars:
                    truncated_summary = video_summary[: available_chars - 3] + "..."
                else:
                    truncated_summary = video_summary
                data_without_events["video_summary"] = truncated_summary
            else:
                data_without_events["video_summary"] = ""

            return json.dumps(data_without_events)

        # No events key, just return truncated response
        return response[:max_chars]

    # NOTE: _publish_kafka_structured_summary was removed. The live-stream
    # summarize flow publishes via _publish_aggregate_to_kafka inside
    # summarize_stream(). The file path never published from
    # _get_aggregated_summary regardless of KAFKA_ENABLED.

    def _get_aggregated_summary(
        self, req_info: RequestInfo, chunk_responses: list[VlmChunkResponse]
    ):
        """Aggregated summary for the request"""

        saved_dc_file = req_info.file + ".dc.json"
        if not os.access(saved_dc_file, os.R_OK) and self._args.enable_dev_dc_gen:
            # Serialize the object to a JSON file
            req_info_to_write = req_info
            DCSerializer.to_json(req_info_to_write, saved_dc_file)

        if chunk_responses:
            with TimeMeasure("Chunk Processing - Filter and Sort"):
                # Filter out chunks that do not have an associated vlm response
                chunk_responses = list(
                    filter(lambda item: item.vlm_response is not None, chunk_responses)
                )
                # Sort chunks based on their start times
                chunk_responses.sort(key=lambda item: ntp_to_unix_timestamp(item.chunk.start_ntp))

        if len(chunk_responses) == 0:
            # Return empty response if there are no chunks / chunks with vlm responses
            logger.info(f"No chunks with vlm responses for request {req_info.request_id}")
            return []

        if req_info._ctx_mgr:
            # Debug mode: skip summarization and return concatenated chunk responses
            if os.environ.get("LVS_CHUNK_DEBUG", "").lower() == "true":
                logger.info(
                    f"LVS_CHUNK_DEBUG enabled: skipping summarization for request {req_info.request_id}"
                )
                parsed_responses = []
                for idx, proc_chunk in enumerate(chunk_responses):
                    json_str = self.extract_json_from_vlm_response(proc_chunk.vlm_response)
                    logger.debug(f"JSON string: {json_str}")
                    parsed = json_repair.loads(json_str)
                    parsed = parsed[0] if isinstance(parsed, list) else parsed
                    parsed["id"] = idx
                    parsed["start_time"] = idx * 10
                    parsed["end_time"] = (idx * 10) + 9
                    parsed_responses.append(parsed)
                agg_response = json.dumps(parsed_responses)
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
                        "Chunk debug mode enabled",
                    )
                ]

            with TimeMeasure("Context Aware RAG Latency") as cms_t:
                try:
                    # Summarize individual chunk VLM responses using CA-RAG
                    # TODO: Handle the last chunk id, should be -1
                    if not req_info.is_live:
                        last_meta = vars(chunk_responses[-1].chunk)
                        last_meta["is_last"] = True
                        last_meta["uuid"] = req_info.source_id
                        last_meta["cv_meta"] = ""
                        last_meta["camera_id"] = req_info.camera_id
                        _agg_parent_ctx = getattr(req_info, "_e2e_span_context", None)
                        with TimeMeasure("Context Manager Summarize/add_doc - last chunk"):
                            with trace_operation(
                                "CTX-RAG Add Doc - Final",
                                parent_context=_agg_parent_ctx,
                                operation="ctx_rag_add_doc_final",
                                source_id=req_info.source_id,
                            ):
                                req_info._ctx_mgr.add_doc(
                                    ".",
                                    doc_i=(
                                        2 * chunk_responses[-1].chunk.chunkIdx + 2
                                        if req_info.enable_audio
                                        else chunk_responses[-1].chunk.chunkIdx + 1
                                    ),
                                    doc_meta=last_meta,
                                )

                    if req_info.enable_qa and req_info._qa_ctx_mgr:
                        last_chunk = chunk_responses[-1].chunk
                        last_doc_i = (
                            2 * last_chunk.chunkIdx + 2
                            if req_info.enable_audio
                            else last_chunk.chunkIdx + 1
                        )
                        try:
                            req_info._qa_ctx_mgr.add_doc(
                                ".",
                                doc_i=last_doc_i,
                                doc_meta=vars(chunk_responses[-1].chunk)
                                | {
                                    "uuid": req_info.source_id,
                                    "camera_id": req_info.camera_id,
                                    "is_last": True,
                                    "cv_meta": "",
                                },
                            )
                        except Exception as qa_last_ex:
                            logger.warning("QA add_doc (final) failed: %s", qa_last_ex)
                        with TimeMeasure("Context Manager - Ingestion Function (QA)"):
                            with trace_operation(
                                "CTX-RAG Call - Ingestion (QA)",
                                parent_context=_agg_parent_ctx,
                                operation="ctx_rag_call_ingestion_qa",
                                source_id=req_info.source_id,
                            ):
                                logger.info(
                                    "Running ingestion_function on QA ctx_mgr: source_id=%s",
                                    req_info.source_id,
                                )
                                req_info._qa_ctx_mgr.call(
                                    {
                                        "ingestion_function": {
                                            "uuid": req_info.source_id,
                                        }
                                    }
                                )

                    _agg_parent_ctx = getattr(req_info, "_e2e_span_context", None)
                    if req_info.summarize:
                        # Decide whether to aggregate from in-process SSE
                        # captions (start_index/end_index) or from the
                        # Elastic DB populated by Kafka->Logstash->ES
                        # (uuids). Controlled by LVS_CAPTION_SOURCE env
                        # var: "sse" (default) uses the SSE captions
                        # already accumulated via add_doc; "db" retrieves
                        # from the database.
                        _use_db = self._is_file_path_kafka_mode() and self._caption_source == "db"
                        if _use_db:
                            logger.info(
                                "Aggregation for %s: reading captions from "
                                "Elastic DB (LVS_CAPTION_SOURCE=db)",
                                req_info.source_id,
                            )
                            settle_secs = self._kafka_settle_secs()
                            if settle_secs > 0:
                                logger.info(
                                    "Waiting %.3fs for Kafka -> Logstash -> ES "
                                    "raw_events flush before aggregating %s",
                                    settle_secs,
                                    req_info.source_id,
                                )
                                time.sleep(settle_secs)
                            sum_state: dict = {"uuids": [str(req_info.source_id)]}
                            _start_ts = getattr(req_info, "start_timestamp", None)
                            _end_ts = getattr(req_info, "end_timestamp", None)
                            if _start_ts:
                                try:
                                    sum_state["start_time"] = float(_start_ts)
                                except (TypeError, ValueError):
                                    pass
                            if _end_ts:
                                try:
                                    sum_state["end_time"] = float(_end_ts)
                                except (TypeError, ValueError):
                                    pass
                        else:
                            if self._is_file_path_kafka_mode():
                                logger.info(
                                    "Aggregation for %s: using SSE captions "
                                    "(LVS_CAPTION_SOURCE=sse)",
                                    req_info.source_id,
                                )
                            sum_state = {
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
                        with TimeMeasure("Context Manager Summarize/call - summarize"):
                            with trace_operation(
                                "CTX-RAG Call - Summarize",
                                parent_context=_agg_parent_ctx,
                                operation="ctx_rag_call_summarize",
                                source_id=req_info.source_id,
                            ):
                                agg_response = req_info._ctx_mgr.call({"summarization": sum_state})
                        if agg_response.get("error"):
                            logger.error(
                                f"Error for Request ID: {req_info.request_id} "
                                f"Source ID: {req_info.source_id}"
                            )
                            logger.error(f"An internal error occurred: {agg_response.get('error')}")
                            logger.error(traceback.format_exc())
                            # ctx-rag returned an error
                            # string from its subprocess. Wrap the string
                            # in an exception so classify_es_error can
                            # fingerprint a shard-cap failure (the message
                            # carries through the queue), mark the request
                            # FAILED, and raise to the HTTP layer instead
                            # of returning an empty "Summarization failed"
                            # CompletionResponse with HTTP 200.
                            ctx_err = Exception(str(agg_response.get("error")))
                            http_status, user_message = self._handle_es_dependency_error(
                                req_info, ctx_err
                            )
                            raise ViaException(
                                user_message, "DependencyError", http_status
                            ) from ctx_err

                        else:
                            result_metadata = agg_response.get("summarization", {}).get(
                                "metadata", {}
                            )
                            agg_response = agg_response.get("summarization", {}).get("result", "")
                            req_info.usage = RequestInfo.Usage(**result_metadata)
                            # File-path Kafka mode: mirror the live-stream
                            # summarize_stream extraction and publish
                            # structured_events + aggregated_summary back
                            # to Kafka via _publish_aggregate_to_kafka so
                            # Logstash can index them in default_<file_id>.
                            # _publish_aggregate_to_kafka early-returns
                            # when KAFKA_ENABLED is false / producer is
                            # unavailable, so the gate here only guards
                            # against parsing the agg_response JSON when
                            # we know we're not going to publish anyway.
                            if self._is_file_path_kafka_mode():
                                _events: list = []
                                _video_summary: str = ""
                                if isinstance(agg_response, str) and agg_response:
                                    try:
                                        _parsed = json.loads(agg_response)
                                        if isinstance(_parsed, dict):
                                            _events = _parsed.get("events", []) or []
                                            _video_summary = _parsed.get("video_summary", "") or ""
                                    except (json.JSONDecodeError, TypeError, ValueError) as ex:
                                        logger.warning(
                                            "_get_aggregated_summary (file-path Kafka mode):"
                                            " non-JSON aggregator result for %s: %s",
                                            req_info.source_id,
                                            ex,
                                        )
                                        _video_summary = agg_response
                                self._publish_aggregate_to_kafka(
                                    stream_id=str(req_info.source_id),
                                    camera_id=getattr(req_info, "camera_id", None) or "default",
                                    events=_events,
                                    video_summary=_video_summary,
                                )
                    else:
                        agg_response = "Media processed"
                except ViaException:
                    # ViaException is already classified (e.g. raised by
                    # the agg_response.get("error") branch above, or by
                    # _handle_es_dependency_error inside
                    # _publish_aggregate_to_kafka). Let it propagate so
                    # the HTTP layer surfaces the right status.
                    raise
                except Exception as ex:
                    logger.error(traceback.format_exc())
                    logger.error(
                        "Summary aggregation failed for query %s - %s",
                        req_info.request_id,
                        str(ex),
                    )
                    # An unhandled exception inside the
                    # ctx-rag call path (ES read failure, transport error,
                    # etc.) used to be replaced with a string and returned
                    # as a HTTP 200 "Summarization failed" CompletionResponse.
                    # Classify it, mark FAILED, and raise so the user gets
                    # a 503 instead.
                    http_status, user_message = self._handle_es_dependency_error(req_info, ex)
                    raise ViaException(user_message, "DependencyError", http_status) from ex
            req_info._ca_rag_latency = cms_t.execution_time

            agg_response = self.format_response(agg_response)
            agg_response = self._sanitize_vlm_response(agg_response)

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

    @staticmethod
    def populate_argument_parser(parser: ArgumentParser):
        """Add VIA Stream Handler arguments to the argument parser"""

        parser.add_argument("--max-live-streams", type=int, default=256)
        parser.add_argument("--enable-audio", action="store_true", default=False)

        parser.add_argument(
            "--enable-dev-dc-gen",
            action="store_true",
            default=False,
            help="Enable dense caption generation (dev mode)",
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

    def _create_named_thread_pool(self, max_workers=1, prefix="via"):
        """Create a ThreadPoolExecutor with named threads"""
        return concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=f"{prefix}-{str(uuid.uuid4())[:8]}",
        )

    def _start_stream_fps_tracking(self, req_info: RequestInfo):
        """Start FPS tracking for a new stream."""
        req_info._fps_start_time = time.time()
        req_info._fps_frame_count = 0
        req_info._fps_last_update_time = req_info._fps_start_time
        req_info._fps_is_active = True
        logger.debug(f"Started FPS tracking for stream: {req_info.request_id}")

    def _update_stream_fps(self, response: VlmChunkResponse, req_info: RequestInfo):
        """Update FPS tracking for a stream.

        Prefers RTVI's `frame_count` (lands on response.rtvi_frame_count
        via _trigger_query) over the derived chunk_size * video_fps, which
        is only a guess in the absence of explicit FPS info.
        """
        if not req_info._fps_is_active:
            return

        frame_count = getattr(response, "rtvi_frame_count", 0) or 0
        if not frame_count and req_info.video_fps:
            frame_count = int(req_info.chunk_size * req_info.video_fps)

        req_info._fps_frame_count += frame_count
        req_info._fps_last_update_time = time.time()

        # current_fps = self._get_request_fps(req_info)
        # self._metrics.stream_fps_histogram.observe(current_fps)

    def _finalize_stream_fps_tracking(self, req_info: RequestInfo):
        """Finalize FPS tracking for a completed stream."""
        if not req_info._fps_is_active:
            return

        final_fps = self._get_request_fps(req_info)
        # self._metrics.stream_fps_histogram.observe(final_fps)
        req_info._fps_is_active = False
        logger.debug(
            f"Finalized FPS tracking for stream {req_info.request_id}, final FPS: {final_fps:.2f}"
        )

    def _get_db_tool_name(self, ca_rag_config):
        """Return DB tool name from config (config uses !ENV ${LVS_DATABASE_BACKEND:elasticsearch_db})."""
        summ = ca_rag_config.get("functions", {}).get("summarization", {})
        return summ.get("tools", {}).get("db")

    def _update_db_tool_param(self, ca_rag_config, db_tool_name, param_name, param_value):
        """Update DB tool parameter for a given function."""

        # Get the tool that this function references for DB operations
        tool = ca_rag_config.get("tools", {}).get(db_tool_name)
        if tool is not None:
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
        if function_name not in functions:
            logger.debug(f"Function {function_name} not found in ca_rag_config")
            return
        llm_tool_name = functions.get(function_name, {}).get("tools", {}).get("llm", "")
        if not llm_tool_name:
            logger.debug(f"LLM tool name not found for function {function_name}")
            return

        # Ensure tools and params sections exist
        if "tools" not in ca_rag_config:
            ca_rag_config["tools"] = {}
        if llm_tool_name not in ca_rag_config.get("tools", {}):
            ca_rag_config["tools"][llm_tool_name] = {"params": {}}
        elif "params" not in ca_rag_config.get("tools", {}).get(llm_tool_name, {}):
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

        if "context_manager" not in ca_rag_config:
            ca_rag_config["context_manager"] = {}
        ca_rag_config["context_manager"]["uuid"] = req_info.source_id

        # Set configurations (only if summarization function exists)
        if "summarization" in ca_rag_config.get("functions", {}):
            # Set enable_vlm_structured_output and objects_of_interest if provided
            if "params" not in ca_rag_config["functions"]["summarization"]:
                ca_rag_config["functions"]["summarization"]["params"] = {}

            # Set explicit summarization batch size
            if req_info.summarize_batch_size:
                summ_batch_size = req_info.summarize_batch_size
                if req_info.enable_audio:
                    # Make batch size even so that audio, video docs
                    # for a chunk are processed together.
                    summ_batch_size += 1 if (summ_batch_size % 2) != 0 else 0
                if "params" not in ca_rag_config["functions"]["summarization"]:
                    ca_rag_config["functions"]["summarization"]["params"] = {}
                ca_rag_config["functions"]["summarization"]["params"][
                    "batch_size"
                ] = summ_batch_size

            # Set additional summarization parameters
            if "params" not in ca_rag_config["functions"]["summarization"]:
                ca_rag_config["functions"]["summarization"]["params"] = {}

            if not req_info.enable_vlm_structured_output:
                if req_info.schema:
                    ca_rag_config["functions"]["summarization"]["params"][
                        "schema"
                    ] = req_info.schema
                if req_info.batch_response_method:
                    ca_rag_config["functions"]["summarization"]["params"][
                        "batch_response_method"
                    ] = req_info.batch_response_method
                if req_info.scenario:
                    ca_rag_config["functions"]["summarization"]["params"][
                        "scenario"
                    ] = req_info.scenario
                if req_info.events:
                    ca_rag_config["functions"]["summarization"]["params"][
                        "events"
                    ] = req_info.events
                if req_info.auto_generate_prompt is not None:
                    ca_rag_config["functions"]["summarization"]["params"][
                        "auto_generate_prompt"
                    ] = req_info.auto_generate_prompt
                if req_info.time_metadata_keys:
                    ca_rag_config["functions"]["summarization"]["params"][
                        "time_metadata_keys"
                    ] = req_info.time_metadata_keys

        if "summarization" in ca_rag_config.get("functions", {}):
            if "params" not in ca_rag_config["functions"]["summarization"]:
                ca_rag_config["functions"]["summarization"]["params"] = {}
            if req_info.source_id:
                ca_rag_config["functions"]["summarization"]["params"]["uuid"] = req_info.source_id

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
            if "summarization" in ca_rag_config.get("context_manager", {}).get("functions", []):
                ca_rag_config["context_manager"]["functions"].remove("summarization")

        db_tool = self._get_db_tool_name(ca_rag_config)
        self._update_db_tool_param(
            ca_rag_config,
            db_tool,
            "user_specified_collection_name",
            req_info.user_specified_collection_name,
        )
        self._update_db_tool_param(
            ca_rag_config, db_tool, "custom_metadata", req_info.custom_metadata
        )
        self._update_db_tool_param(
            ca_rag_config,
            db_tool,
            "delete_external_collection",
            req_info.delete_external_collection,
        )

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
            dict: Dictionary with source_id -> fps mapping for active streams
        """
        with self._lock:
            active_streams_info = {}
            for req_info in self._request_info_map.values():
                if req_info._fps_is_active and req_info.source_id:
                    source_id = req_info.source_id
                    active_streams_info[source_id] = self._get_request_fps(req_info)
            return active_streams_info

    def update_live_stream_summary_latency(self, _latency: float):
        """Update live stream summary latency metric (disabled)"""
        pass

    def update_live_stream_captions_latency(self, _latency: float):
        """Update live stream captions latency metric (disabled)"""
        pass

    def _is_file_path_kafka_mode(self) -> bool:
        """True iff the file-summarize path should run in Kafka mode.

        In Kafka mode the file path:
          * publishes ``structured_events`` + ``aggregated_summary`` back
            to Kafka via :py:meth:`_publish_aggregate_to_kafka` after
            aggregation completes.

        The caption source for aggregation is controlled by
        ``LVS_CAPTION_SOURCE`` (default ``db``):
          * ``sse`` — aggregation uses the in-process captions received
            via SSE (``start_index / end_index``).
          * ``db`` — aggregation retrieves captions from Elastic DB
            populated by the Kafka -> Logstash -> ES pipeline
            (``uuids``).  Requires a settle delay so Logstash can flush.

        Requires both server-level ``KAFKA_ENABLED`` (env) and config-level
        ``functions.summarization.params.kafka_enabled`` (CA-RAG YAML).
        Returns ``False`` if either flag is off — the legacy in-process
        flow then runs unchanged.
        """
        if not self._kafka_enabled:
            return False
        summ = (self._ca_rag_config or {}).get("functions", {}).get("summarization", {})
        return bool(summ.get("params", {}).get("kafka_enabled", False))

    def _kafka_settle_secs(self) -> float:
        """Seconds to sleep after RTVI SSE ``[DONE]`` in file-path Kafka mode.

        Reads ``tools.<db>.params.kafka_consumer_settle_secs`` from the
        parsed CA-RAG config; falls back to env override
        ``LVS_KAFKA_CONSUMER_SETTLE_SECS`` when the YAML key is absent;
        defaults to ``5.0``. Used so the Kafka -> Logstash -> ES pipeline
        has time to flush raw_events into the DB before the aggregator
        (running with ``kafka_enabled=true``) reads them at acall time.
        """
        db_name = self._get_db_tool_name(self._ca_rag_config) or "elasticsearch_db"
        tools = (self._ca_rag_config or {}).get("tools", {})
        val = tools.get(db_name, {}).get("params", {}).get("kafka_consumer_settle_secs")
        if val is None:
            val = os.environ.get("LVS_KAFKA_CONSUMER_SETTLE_SECS", "5.0")
        try:
            return float(val)
        except (TypeError, ValueError):
            return 5.0
