#!/usr/bin/env python3
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
import asyncio
import concurrent.futures
import multiprocessing
import os
import queue
import subprocess
import sys
import time
from argparse import ArgumentParser
from dataclasses import dataclass, field
from enum import Enum
from threading import Event, Lock, Thread
from typing import Callable, Optional

import nvtx
import torch
import yaml
from grpc._channel import _MultiThreadedRendezvous

from api_models.captions import VlmQuery
from api_models.embeddings import TextEmbeddingsQuery
from common.chunk_info import ChunkInfo
from common.health_status import HealthStatus
from common.logger import LOG_STATUS_LEVEL, logger
from models.base_vlm_model import VlmGenerationConfig, VlmModelOutput
from models.dynamic_model_loader import DynamicModelLoader, load_model
from utils.asset_manager import Asset

from .errors import CUDA_OOM_STATUS_CODE, format_cuda_oom_error, is_cuda_oom_error
from .ngc_model_downloader import download_model, download_model_git
from .process_base import ProcessBase

# Built-in model class paths
BUILTIN_MODEL_CLASSES = {
    "cosmos-reason1": "models.vllm_compatible.vllm_compatible_model.VllmCompatible",
    "cosmos-reason2": "models.vllm_compatible.vllm_compatible_model.VllmCompatible",
    "cosmos-reason3": "models.vllm_compatible.vllm_compatible_model.VllmCompatible",
    "vllm-compatible": "models.vllm_compatible.vllm_compatible_model.VllmCompatible",
    "openai-compat": "models.openai_compat.openai_compat_model.CompOpenAIModel",
}


# Location to download and cache NGC models
NGC_MODEL_CACHE = os.environ.get("NGC_MODEL_CACHE") or "/opt/nvidia/rtvi/.rtvi/ngc_model_cache/"
DEFAULT_DECODE_MAX_ATTEMPTS = 2


def _decode_max_attempts() -> int:
    raw_value = os.environ.get("RTVI_DECODE_MAX_ATTEMPTS", str(DEFAULT_DECODE_MAX_ATTEMPTS))
    try:
        return max(1, int(raw_value))
    except ValueError:
        logger.warning(
            "Invalid RTVI_DECODE_MAX_ATTEMPTS=%r; using %d",
            raw_value,
            DEFAULT_DECODE_MAX_ATTEMPTS,
        )
        return DEFAULT_DECODE_MAX_ATTEMPTS


def _reuse_file_decoder_pipeline() -> bool:
    return os.environ.get("RTVI_REUSE_FILE_DECODER_PIPELINE", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


class VlmModelType(Enum):
    OPENAI_COMPATIBLE = "openai-compat"  # Any OpenAI API compatible on NIM/OpenAI/Azure-OpenAI
    VLLM_COMPATIBLE = "vllm-compatible"
    COSMOS_REASON1 = "cosmos-reason1"
    COSMOS_REASON2 = "cosmos-reason2"
    COSMOS_REASON3 = "cosmos-reason3"
    CUSTOM = "custom"

    def __str__(self):
        return self.value


def get_model_class_path(vlm_model_type, model_path, model_implementation_path=None):
    """
    Determine the class path to use for model loading.

    Args:
        vlm_model_type: VlmModelType enum (use VlmModelType.CUSTOM for custom models)
        model_path: Path to the model weights/files
        model_implementation_path: Path to model implementation (for custom models)

    Returns:
        str: The class path to use for dynamic model loading (never None)
    """
    # Normalize to string identifier to support both Enum and plain string inputs
    model_type_value = getattr(vlm_model_type, "value", vlm_model_type)

    # Check if custom model by comparing the value
    if model_type_value == "custom":

        # Custom model
        return model_implementation_path or model_path
    else:
        # Built-in model
        model_type_key = model_type_value
        return BUILTIN_MODEL_CLASSES[model_type_key]


@dataclass
class VlmModelInfo:
    """Model inforamtion"""

    id: str = ""
    created: int = 0
    owned_by: str = ""
    api_type: str = ""


@dataclass
class VlmRequestParams:
    vlm_generation_config: Optional[VlmGenerationConfig] = None
    vlm_prompt: Optional[str] = None
    chat_messages: Optional[list[dict[str, str]]] = None  # Structured OpenAI-format messages
    stream: bool = False  # Enable token-level streaming for text-only requests
    # True for POST /generate_text_embeddings — embedding models use generate() with text chunks
    is_text_embeddings_query: bool = False

    @staticmethod
    def from_vlm_query(vlm_query: VlmQuery) -> "VlmRequestParams":
        """Build VlmRequestParams from VlmQuery object.

        Args:
            vlm_query: VlmQuery object containing query parameters

        Returns:
            VlmRequestParams object
        """
        params = VlmRequestParams()
        params.vlm_prompt = vlm_query.prompt

        # Build generation config from query fields
        config_kwargs = {}
        if vlm_query.max_tokens is not None:
            config_kwargs["max_new_tokens"] = vlm_query.max_tokens
        if vlm_query.min_tokens is not None:
            config_kwargs["min_tokens"] = vlm_query.min_tokens
        if vlm_query.top_p is not None:
            config_kwargs["top_p"] = vlm_query.top_p
        if vlm_query.top_k is not None:
            config_kwargs["top_k"] = vlm_query.top_k
        if vlm_query.temperature is not None:
            config_kwargs["temperature"] = vlm_query.temperature
        if vlm_query.seed is not None:
            config_kwargs["seed"] = vlm_query.seed
        if vlm_query.enable_reasoning:
            config_kwargs["enable_reasoning"] = vlm_query.enable_reasoning
        if getattr(vlm_query, "preserve_reasoning_tags", False):
            config_kwargs["preserve_reasoning_tags"] = True
        if vlm_query.system_prompt:
            config_kwargs["system_prompt"] = vlm_query.system_prompt
        if vlm_query.ignore_eos is not None:
            config_kwargs["ignore_eos"] = vlm_query.ignore_eos
        if hasattr(vlm_query, "mm_processor_kwargs") and vlm_query.mm_processor_kwargs:
            config_kwargs["mm_processor_kwargs"] = vlm_query.mm_processor_kwargs
        if hasattr(vlm_query, "media_io_kwargs") and vlm_query.media_io_kwargs:
            config_kwargs["media_io_kwargs"] = vlm_query.media_io_kwargs

        # Create VlmGenerationConfig instance with provided values
        params.vlm_generation_config = VlmGenerationConfig(**config_kwargs)
        params.stream = vlm_query.stream if hasattr(vlm_query, "stream") else False
        return params

    @staticmethod
    def from_text_embeddings_query(
        text_embeddings_query: TextEmbeddingsQuery,
    ) -> "VlmRequestParams":
        """Build VlmRequestParams from TextEmbeddingsQuery object.

        Args:
            text_embeddings_query: TextEmbeddingsQuery object containing query parameters

        Returns:
            VlmRequestParams object
        """
        return VlmRequestParams(
            vlm_prompt="dummy prompt",
            is_text_embeddings_query=True,
        )


class DecoderProcess(ProcessBase):
    """Chunk decoder process"""

    def __init__(
        self, args, gpu_id=0, disabled=False, input_queue=None, input_queue_lock=None
    ) -> None:
        super().__init__(
            gpu_id=gpu_id,
            disabled=disabled,
            input_queue=input_queue,
            input_queue_lock=input_queue_lock,
            description="DecoderProcess",
        )
        self._vlm_model_type = args.vlm_model_type
        self._num_decoders_per_gpu = args.num_decoders_per_gpu
        self._num_frames_per_second_or_fixed_frames_chunk = (
            args.num_frames_per_second_or_fixed_frames_chunk
        )
        self._model_path = args.model_path
        self._model_implementation_path = args.model_implementation_path or args.model_path
        self._module_loader = None
        self._max_live_streams = max(1, -(-args.max_live_streams // args.num_gpus))
        self._enable_audio = args.enable_audio
        self._use_fps_for_chunking = args.use_fps_for_chunking or False

    def _initialize(self):
        from .video_file_frame_getter import DefaultFrameSelector, VideoFileFrameGetter

        self._live_stream_handle_info: dict[str, dict] = {}

        self._nfrms = self._num_frames_per_second_or_fixed_frames_chunk
        self._image_mean = None
        self._rescale_factor = None
        self._image_std = None
        self._crop_height = None
        self._crop_width = None
        self._shortest_edge = None
        self._do_preprocess = False
        self._image_aspect_ratio = ""
        self._enable_jpeg_tensors = False
        self._width = 0
        self._height = 0
        self._data_type_int8 = False

        # Populate model-specific frame pre-processing parameters
        # Use model-specific decoder configuration for all other models
        from models.dynamic_model_loader import DynamicModelLoader

        class_path = get_model_class_path(
            self._vlm_model_type, self._model_path, self._model_implementation_path
        )

        if self._vlm_model_type == VlmModelType.CUSTOM:
            # Custom model
            logger.info(f"Model implementation path: {self._model_implementation_path}")

        loader = DynamicModelLoader(class_path)

        # Get input configuration from model class
        input_config = loader.get_input_config(self._model_path, self._vlm_model_type.value)

        logger.info(f"Model input config: {input_config}")

        # Apply input configuration
        if not self._nfrms:
            self._nfrms = input_config.num_frames
        self._minframes = 1
        self._data_type_int8 = True  # Assume true for dynamic model loader
        self._enable_jpeg_tensors = input_config.use_jpeg_encoding
        self._width = input_config.width
        self._height = input_config.height

        if (
            "VLM_INPUT_WIDTH" in os.environ
            and os.environ["VLM_INPUT_WIDTH"]
            and "VLM_INPUT_HEIGHT" in os.environ
            and os.environ["VLM_INPUT_HEIGHT"]
        ):
            self._width = int(os.environ["VLM_INPUT_WIDTH"])
            self._height = int(os.environ["VLM_INPUT_HEIGHT"])
            logger.info(f"Forcing input to VLM {self._width}X{self._height}")

        # Initialize multiple frame getters (decoders)
        self._fgetters = [
            VideoFileFrameGetter(
                frame_selector=DefaultFrameSelector(self._nfrms),
                frame_width=self._width,
                frame_height=self._height,
                gpu_id=0,
                do_preprocess=self._do_preprocess,
                image_mean=self._image_mean,
                rescale_factor=self._rescale_factor,
                image_std=self._image_std,
                crop_height=self._crop_height,
                crop_width=self._crop_width,
                shortest_edge=self._shortest_edge,
                image_aspect_ratio=self._image_aspect_ratio,
                enable_jpeg_output=self._enable_jpeg_tensors,
                data_type_int8=self._data_type_int8,
                audio_support=self._enable_audio,
            )
            for _ in range(self._num_decoders_per_gpu)
        ]
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=int(self._max_live_streams + 1)
        )
        self._file_thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=int(self._num_decoders_per_gpu + 1)
        )
        self._fgetter_handoff_lock = Lock()
        return True

    def _warmup(self):
        chunk = ChunkInfo()
        chunk.file = "/opt/nvidia/rtvi/warmup_streams/its_264.mp4"
        chunk.end_pts = 5000000000
        if os.path.exists(chunk.file):
            for fgetter in self._fgetters:
                fgetter.get_frames(chunk)

        chunk.file = "/opt/nvidia/rtvi/warmup_streams/its_265.mp4"
        if os.path.exists(chunk.file):
            for fgetter in self._fgetters:
                fgetter.get_frames(chunk)

        # Decoder warmup is local to this process. Do not forward the decoded
        # warmup frames to VLM: they have no prompt/request_params and may still
        # be CUDA tensors, which are unsafe to send through the process queue.

    def _decode_chunk(
        self,
        fgetter,
        chunk: ChunkInfo,
        vlm_query: VlmQuery,
        video_codec,
        **kwargs,
    ):

        if chunk.chunk_type == "text":
            return {
                "chunk": chunk,
                **kwargs,
            }

        from .video_file_frame_getter import DefaultFrameSelector

        decode_start_time = time.time()
        logger.log(LOG_STATUS_LEVEL, "Chunk (%s) decode starting", chunk)
        nvtx_decode_start = nvtx.start_range(message="Decode Process-" + str(chunk), color="blue")

        # Extract parameters from VlmQuery
        num_frames_per_second_or_fixed_frames_chunk = (
            vlm_query.num_frames_per_second_or_fixed_frames_chunk
        )
        use_fps_for_chunking = vlm_query.use_fps_for_chunking or False
        if num_frames_per_second_or_fixed_frames_chunk:
            frame_selector = DefaultFrameSelector(
                num_frames_per_second_or_fixed_frames_chunk,
                use_fps_for_chunking=use_fps_for_chunking,
            )
        else:
            frame_selector = DefaultFrameSelector(
                self._nfrms,
                use_fps_for_chunking=self._use_fps_for_chunking,
            )

        enable_audio = vlm_query.enable_audio
        vlm_input_width = vlm_query.vlm_input_width
        vlm_input_height = vlm_query.vlm_input_height

        frames = []
        frame_times = []
        audio_frames = []
        error = None
        error_status_code = 500
        decode_retry_count = 0
        decode_max_attempts = _decode_max_attempts()
        for attempt in range(decode_max_attempts):
            try:
                frames, frame_times, audio_frames, error = fgetter.get_frames(
                    chunk,
                    frame_selector,
                    enable_audio,
                    request_id=kwargs["request_id"],
                    frame_width=vlm_input_width,
                    frame_height=vlm_input_height,
                    video_codec=video_codec,
                )
            except torch.OutOfMemoryError as exc:
                error = format_cuda_oom_error(exc, "extracting decoded chunk frames")
                error_status_code = CUDA_OOM_STATUS_CODE
                logger.error(error, exc_info=True)
                frames = []
                frame_times = []
                audio_frames = []
                break
            decoded_frame_count = len(frames)
            if not error and decoded_frame_count >= self._minframes:
                break
            if is_cuda_oom_error(error):
                error_status_code = CUDA_OOM_STATUS_CODE
                break
            if not error:
                error = (
                    f"decoded {decoded_frame_count} frame(s), "
                    f"required at least {self._minframes}"
                )
            if attempt + 1 >= decode_max_attempts:
                break
            decode_retry_count += 1
            logger.warning(
                "Retrying decode for chunk %s after frame extraction error " "(attempt %d/%d): %s",
                chunk,
                attempt + 1,
                decode_max_attempts,
                error,
            )
            # Tear down the broken pipeline AND its cached decoders.
            # The next get_frames() call will rebuild a fresh pipeline +
            # fresh CUDA decoder. If the retry succeeds, the freshly-built
            # decoder becomes the new cached one and is preserved by
            # flush_pipeline() in the delayed append below — we only pay
            # CUDA decoder creation once per failure, not per chunk.
            try:
                fgetter.destroy_pipeline()
            except Exception as ex:
                logger.warning(
                    "Failed to reset decoder pipeline before retry for chunk %s: %s",
                    chunk,
                    ex,
                )
        frame_times = [float("%.2f" % frame_ele) for frame_ele in frame_times]

        nvtx.end_range(nvtx_decode_start)

        def append_fgetter_delayed():
            # If the retry happened and succeeded, fgetter now holds a freshly
            # built pipeline + cached decoder; we want flush_pipeline() so the
            # new decoder is reused on the next chunk. CUDA decoder context
            # creation is expensive — that's the whole point of reuse.
            # Destroy only on terminal failure or when reuse is globally off.
            try:
                if (
                    error
                    or not _reuse_file_decoder_pipeline()
                    or not getattr(fgetter, "_file_pipeline_reusable", True)
                ):
                    with self._fgetter_handoff_lock:
                        fgetter.destroy_pipeline()
            except Exception as ex:
                logger.warning("Failed to reset decoder pipeline for chunk %s: %s", chunk, ex)
                try:
                    fgetter.destroy_pipeline()
                except Exception as destroy_ex:
                    logger.warning(
                        "Failed to destroy decoder pipeline after reset failure "
                        "for chunk %s: %s",
                        chunk,
                        destroy_ex,
                    )
            finally:
                self._fgetters.append(fgetter)

        self._file_thread_pool.submit(append_fgetter_delayed)
        logger.log(LOG_STATUS_LEVEL, "Chunk (%s) decoded, frames=%d", chunk, len(frames))
        decode_end_time = time.time()

        error_msg = f"Decode error: {error}" if error else None

        if len(frames) >= self._minframes:
            return {
                "chunk": chunk,
                "frames": frames,
                "error": error_msg,
                "error_status_code": error_status_code,
                "frame_times": frame_times,
                "audio_frames": audio_frames,
                "audio_transcript": [],
                "decode_start_time": decode_start_time,
                "decode_end_time": decode_end_time,
                "decode_retry_count": decode_retry_count,
                "is_live_stream": False,
                **kwargs,
            }
        elif error_msg:
            return {
                "chunk": chunk,
                "error": error_msg,
                "error_status_code": error_status_code,
                "decode_retry_count": decode_retry_count,
                **kwargs,
            }
        else:
            return {}

    def _handle_command(self, command, **kwargs):
        logger.debug(f"command is {command}")
        if command == "start-live-stream":
            logger.debug("start-live-stream")
            self._thread_pool.submit(self._live_stream, **kwargs)
            logger.debug("start-live-stream")
        if command == "stop-live-stream":
            live_stream_id = kwargs["live_stream_id"]
            logger.debug(f"Stop live stream - {live_stream_id} checking")
            if live_stream_id in self._live_stream_handle_info:
                logger.debug(f"Stop live stream - {live_stream_id} found")
                fgetter = self._live_stream_handle_info[live_stream_id]["frame_getter"]
                self._thread_pool.submit(fgetter.stop_stream)
            else:
                logger.error(f"Stop live stream - {live_stream_id} not found")

    def _live_stream(
        self,
        asset: Asset,
        vlm_query: VlmQuery,
        **kwargs,
    ):
        """Process a live stream.

        Args:
            asset: Asset object containing stream URL and credentials
            vlm_query: VlmQuery object containing all query parameters
            **kwargs: Additional parameters passed through
        """
        from .video_file_frame_getter import DefaultFrameSelector, VideoFileFrameGetter

        logger.info(f"Starting live stream {asset.asset_id}")
        use_fps_for_chunking = vlm_query.use_fps_for_chunking or False
        if vlm_query.num_frames_per_second_or_fixed_frames_chunk:
            frame_selector = DefaultFrameSelector(
                vlm_query.num_frames_per_second_or_fixed_frames_chunk,
                use_fps_for_chunking=use_fps_for_chunking,
            )
        else:
            frame_selector = DefaultFrameSelector(
                self._nfrms,
                use_fps_for_chunking=self._use_fps_for_chunking,
            )

        fgetter = VideoFileFrameGetter(
            frame_selector=frame_selector,
            frame_width=vlm_query.vlm_input_width or self._width,
            frame_height=vlm_query.vlm_input_height or self._height,
            gpu_id=0,
            do_preprocess=self._do_preprocess,
            image_mean=self._image_mean,
            rescale_factor=self._rescale_factor,
            image_std=self._image_std,
            crop_height=self._crop_height,
            crop_width=self._crop_width,
            shortest_edge=self._shortest_edge,
            image_aspect_ratio=self._image_aspect_ratio,
            enable_jpeg_output=self._enable_jpeg_tensors,
            data_type_int8=self._data_type_int8,
            audio_support=self._enable_audio,
        )

        self._live_stream_handle_info[asset.asset_id] = {"frame_getter": fgetter, "num_chunks": 0}

        def on_chunk_decoded(
            chunk: ChunkInfo,
            frames,
            frame_times,
            transcripts,
            error,
            live_stream_id,
            decode_start_time,
            decode_end_time,
            audio_frames=None,
            **kwargs,
        ):
            frame_times = [float("%.2f" % frame_ele) for frame_ele in frame_times]
            chunk.streamId = live_stream_id

            asr_output = ""
            for asr_transcript in transcripts:
                asr_output += asr_transcript["transcript"] + " "
            transcript = asr_output if len(asr_output) != 0 else None

            if error is not None:
                error_msg = "Decode error: " + error
                logger.error(f"Error decoding chunk {chunk}: {error}")
            else:
                error_msg = None
                logger.log(
                    LOG_STATUS_LEVEL, "Decoded new chunk (%s), frames=%d", chunk, len(frames)
                )

            if len(frames) >= self._minframes:
                self._handle_result(
                    {
                        "chunk": chunk,
                        "frames": frames,
                        "frame_times": frame_times,
                        "request_id": kwargs.get("request_id", ""),
                        "chunk_id": chunk.chunkIdx,
                        "enqueue_time": decode_start_time,
                        "audio_frames": audio_frames or [],
                        "audio_transcript": transcript,
                        "error": error_msg,
                        "is_live_stream": True,
                        "decode_start_time": decode_start_time,
                        "decode_end_time": decode_end_time,
                        **kwargs,
                    },
                    chunk=chunk,
                    **kwargs,
                )
                self._live_stream_handle_info[live_stream_id]["num_chunks"] += 1
            elif error_msg:
                self._final_output_queue.put(
                    {
                        "chunk": chunk,
                        "request_id": kwargs.get("request_id", ""),
                        "chunk_id": chunk.chunkIdx,
                        "error": error_msg,
                        "error_status_code": (
                            CUDA_OOM_STATUS_CODE if is_cuda_oom_error(error_msg) else 500
                        ),
                        "is_live_stream": True,
                        "decode_start_time": decode_start_time,
                        "decode_end_time": decode_end_time,
                        **kwargs,
                    }
                )
                self._live_stream_handle_info[live_stream_id]["num_chunks"] += 1

        def on_stream_error(error_message: str, stream_id: str, attempt_count: int):
            """Callback to send stream errors to output queue for Kafka logging."""
            self._final_output_queue.put(
                {
                    "stream_error": True,
                    "stream_id": stream_id,
                    "error_message": error_message,
                    "attempt_count": attempt_count,
                }
            )

        vlm_supports_audio = os.environ.get("VLM_MODEL_SUPPORTS_AUDIO", "false").lower() == "true"
        use_vlm_audio = vlm_query.enable_audio and vlm_supports_audio

        logger.debug(f"Pipeline for live stream starting up: {asset.asset_id}")
        fgetter.stream(
            live_stream_url=asset.path,
            chunk_duration=vlm_query.chunk_duration,
            chunk_overlap_duration=0,
            username=asset.username,
            password=asset.password,
            live_stream_id=asset.asset_id,
            on_chunk_decoded=(
                lambda chunk, frames, frame_times, transcripts, error, decode_start_time, decode_end_time, audio_frames, live_stream_id=asset.asset_id, kwargs=kwargs: on_chunk_decoded(  # noqa: E501
                    chunk,
                    frames,
                    frame_times,
                    transcripts,
                    error,
                    live_stream_id,
                    decode_start_time,
                    decode_end_time,
                    audio_frames=audio_frames,
                    **kwargs,
                )
            ),
            enable_audio=vlm_query.enable_audio,
            use_vlm_audio=use_vlm_audio,
            on_stream_error_callback=on_stream_error,
        )

        logger.debug(f"Pipeline for live stream tearing down: {asset.asset_id}")
        fgetter.destroy_pipeline()
        logger.debug(f"Pipeline for live stream torn down: {asset.asset_id}")

        self._final_output_queue.put(
            {
                "live_stream_ended": True,
                "live_stream_id": asset.asset_id,
                "total_chunks": self._live_stream_handle_info[asset.asset_id]["num_chunks"],
            }
        )
        self._live_stream_handle_info.pop(asset.asset_id)

    def _deinitialize(self):
        for fgetter in self._fgetters:
            fgetter.destroy_pipeline()

        # Shutdown thread pools
        if hasattr(self, "_thread_pool") and self._thread_pool is not None:
            try:
                self._thread_pool.shutdown(wait=True, cancel_futures=True)
            except TypeError:
                # For older Python versions without cancel_futures
                self._thread_pool.shutdown(wait=True)
        if hasattr(self, "_file_thread_pool") and self._file_thread_pool is not None:
            try:
                self._file_thread_pool.shutdown(wait=True, cancel_futures=True)
            except TypeError:
                self._file_thread_pool.shutdown(wait=True)

    def _is_busy(self):
        return len(self._fgetters) == 0

    def _process(self, **kwargs):
        """Decode a chunk and return selected frames as raw frames / JPEG images"""
        return self._file_thread_pool.submit(self._decode_chunk, self._fgetters.pop(), **kwargs)


class VlmProcess(ProcessBase):
    """VLM Process"""

    def __init__(
        self,
        args,
        asset_dir,
        gpu_id: int | str = 0,
        disabled=False,
        input_queue=None,
        input_queue_lock=None,
    ) -> None:
        super().__init__(
            batch_size=args.vlm_batch_size,
            gpu_id=gpu_id,
            disabled=disabled,
            input_queue=input_queue,
            input_queue_lock=input_queue_lock,
            description="VlmProcess",
        )
        self._vlm_model_type = args.vlm_model_type
        self._model_path = args.model_path
        self._model_implementation_path = args.model_implementation_path or args.model_path
        self._args = args
        self._asset_dir = asset_dir
        self._num_gpus = args.num_gpus
        # Handle None vlm_batch_size - default to 1 if None
        vlm_batch_size = args.vlm_batch_size if args.vlm_batch_size is not None else 1
        self._num_futures_threads = max(1, vlm_batch_size)

    def _initialize(self):
        # Determine the class path to use
        class_path = get_model_class_path(
            self._vlm_model_type, self._model_path, self._model_implementation_path
        )

        # Load model using dynamic loader
        # Prepare model loading parameters
        model_kwargs = {
            "max_batch_size": self._batch_size,
            "async_output": True,
            "vlm_model_type": self._vlm_model_type.value,
        }

        self._model = load_model(class_path, self._model_path, **model_kwargs)

        return True

    def _deinitialize(self):
        self._model = None

    def _supports_batching(self):
        return True

    def _can_batch(self, item1, item2):
        if hasattr(self._model, "can_batch"):
            return self._model.can_batch(item1, item2)
        else:
            return False

    def _is_busy(self):
        if hasattr(self._model, "can_enqueue_requests"):
            return not self._model.can_enqueue_requests()
        else:
            return False

    def _warmup(self):
        if hasattr(self._model, "warmup"):
            self._model.warmup()

    def _process(
        self, chunk: list[ChunkInfo], request_params: list[VlmRequestParams | None], **kwargs
    ):
        """Generate VLM output for a batch of chunks"""

        if not request_params[0] or not request_params[0].vlm_prompt:
            for chunk_ in chunk:
                logger.log(LOG_STATUS_LEVEL, "Skipping VLM output generation for (%s)", chunk_)
            return

        vlm_start_time = time.time()
        nvtx_vlm_process_start = nvtx.start_range(message="VLM Process-" + str(chunk), color="blue")

        for chunk_ in chunk:
            logger.log(LOG_STATUS_LEVEL, "Generating VLM output for (%s)", chunk_)

        # Use model classes directly instead of context classes
        ctx = self._model

        frames = kwargs.pop("frames", None)
        frame_times = kwargs.pop("frame_times", [[]] * len(chunk))

        # Extract audio_frames before calling VLM to pass to generate()
        audio_frame_dummy = [{"audio": None, "start": None, "end": None}]
        audio_frames = kwargs.pop("audio_frames", [audio_frame_dummy] * len(chunk))
        audio_transcript = kwargs.pop("audio_transcript", [[]] * len(chunk))
        error_msg = kwargs.pop("error", [None] * len(chunk))

        decode_only = kwargs.pop("decode_only", [False])[0]
        # frames is [[]] when batched from empty frames=[] — detect text-only
        is_text_only = not frames or all(
            (f is None or (isinstance(f, list) and len(f) == 0)) for f in frames
        )

        if decode_only:

            vlm_output_batch = [
                VlmModelOutput(
                    output="Skipping since dense captioning is enabled",
                    input_tokens=0,
                    output_tokens=0,
                )
                for _ in chunk
            ]
        elif is_text_only and chunk and chunk[0].chunk_type == "text":
            # Text-only VLM request — bypass frame processing
            # Use structured chat_messages if available (multi-turn), else build from prompt
            if request_params[0].chat_messages:
                messages = request_params[0].chat_messages
            else:
                messages = []
                if (
                    request_params[0].vlm_generation_config
                    and request_params[0].vlm_generation_config.system_prompt
                ):
                    messages.append(
                        {
                            "role": "system",
                            "content": request_params[0].vlm_generation_config.system_prompt,
                        }
                    )
                messages.append({"role": "user", "content": request_params[0].vlm_prompt})

            if (
                request_params[0].stream
                and hasattr(self, "_token_stream_queue")
                and hasattr(ctx, "_event_loop")
            ):
                # Token-level streaming: consume async generator, push deltas to queue
                chunk_id = kwargs.get("chunk_id", [0])
                chunk_id_val = chunk_id[0] if isinstance(chunk_id, list) else chunk_id

                async def _consume_stream():
                    token_count = 0
                    try:
                        async for delta in ctx.generate_text_only_stream(
                            messages=messages,
                            generation_config=request_params[0].vlm_generation_config,
                        ):
                            token_count += 1
                            self._token_stream_queue.put(
                                {"chunk_id": chunk_id_val, "type": "token", "delta": delta}
                            )
                    except (RuntimeError, ConnectionError, asyncio.CancelledError) as e:
                        logger.error("Token streaming error: %s", e, exc_info=True)
                        self._token_stream_queue.put(
                            {"chunk_id": chunk_id_val, "type": "error", "message": str(e)}
                        )
                    except Exception as e:
                        # Catch-all for unexpected errors during streaming
                        logger.error("Unexpected token streaming error: %s", e, exc_info=True)
                        self._token_stream_queue.put(
                            {"chunk_id": chunk_id_val, "type": "error", "message": str(e)}
                        )
                    finally:
                        logger.debug("Token streaming complete: %d deltas sent", token_count)
                        self._token_stream_queue.put({"chunk_id": chunk_id_val, "type": "done"})

                # Run the stream consumer on the model's event loop
                future = asyncio.run_coroutine_threadsafe(_consume_stream(), ctx._event_loop)
                future.result()  # Wait for streaming to complete
                vlm_output_batch = [
                    VlmModelOutput(output="[streamed]", input_tokens=0, output_tokens=0)
                ]
            elif request_params[0].is_text_embeddings_query:
                vlm_output_batch = ctx.generate(
                    query=request_params[0].vlm_prompt,
                    chunks=chunk,
                    video_frames=frames,
                    video_frames_times=frame_times,
                    generation_config=request_params[0].vlm_generation_config,
                )
            else:
                try:
                    vlm_output_batch = ctx.generate_text_only(
                        messages=messages,
                        generation_config=request_params[0].vlm_generation_config,
                    )
                except NotImplementedError:
                    vlm_output_batch = ctx.generate(
                        query=request_params[0].vlm_prompt,
                        chunks=chunk,
                        video_frames=frames,
                        video_frames_times=frame_times,
                        generation_config=request_params[0].vlm_generation_config,
                    )
        else:
            vlm_output_batch = ctx.generate(
                query=request_params[0].vlm_prompt,
                chunks=chunk,
                video_frames=frames,
                video_frames_times=frame_times,
                generation_config=request_params[0].vlm_generation_config,
                audio_frames=audio_frames,
            )
        if "is_live_stream" in kwargs and self._num_gpus > 1:
            time.sleep(0.1)

        def process_vlm_output(
            chunk,
            request_params,
            vlm_output_list,
            frame_times,
            audio_frames,
            audio_transcript,
            error_msg,
            vlm_start_time,
            nvtx_vlm_process_start,
        ):
            if not isinstance(vlm_output_list, list):
                raise TypeError(f"Expected List[VlmModelOutput], got {type(vlm_output_list)}")

            if len(vlm_output_list) > 0 and not isinstance(vlm_output_list[0], VlmModelOutput):
                raise TypeError(f"Expected VlmModelOutput objects, got {type(vlm_output_list[0])}")

            if len(vlm_output_list) != len(chunk):
                logger.warning(
                    "Mismatch between VLM outputs (%d) and chunks (%d); normalizing lengths",
                    len(vlm_output_list),
                    len(chunk),
                )
                if len(vlm_output_list) > len(chunk):
                    vlm_output_list = vlm_output_list[: len(chunk)]
                else:
                    padding = len(chunk) - len(vlm_output_list)
                    vlm_output_list.extend(
                        VlmModelOutput(
                            output="No response generated for this chunk",
                            input_tokens=0,
                            output_tokens=0,
                        )
                        for _ in range(padding)
                    )

            nvtx.end_range(nvtx_vlm_process_start)
            for idx, chunk_ in enumerate(chunk):
                logger.log(
                    LOG_STATUS_LEVEL,
                    "VLM output generated for (%s), %s",
                    chunk_,
                    vlm_output_list[idx].output,
                )
            return {
                "chunk": chunk,
                "request_params": request_params,
                "vlm_output": vlm_output_list,  # Pass VlmModelOutput objects directly
                "frame_times": frame_times,
                "audio_frames": audio_frames,
                "audio_transcript": audio_transcript,
                "error": error_msg,
                "vlm_start_time": [vlm_start_time] * len(chunk),
                "vlm_end_time": [time.time()] * len(chunk),
                **kwargs,
            }

        process_output_args = (
            chunk,
            request_params,
            vlm_output_batch,
            frame_times,
            audio_frames,
            audio_transcript,
            error_msg,
            vlm_start_time,
            nvtx_vlm_process_start,
        )

        if isinstance(vlm_output_batch, concurrent.futures.Future):
            return self._handle_future_result(
                process_vlm_output,
                *process_output_args,
            )
        else:
            return process_vlm_output(*process_output_args)


class AsrProcess(ProcessBase):
    """ASR Process"""

    def __init__(
        self,
        args,
        asset_dir,
        gpu_id=0,
        disabled=False,
        input_queue=None,
        input_queue_lock=None,
    ) -> None:
        super().__init__(
            batch_size=1,
            gpu_id=gpu_id,
            disabled=disabled,
            input_queue=input_queue,
            input_queue_lock=input_queue_lock,
        )
        self._args = args
        self._model_name = None
        self._server_uri = None
        self._riva_nim_server = True
        self._asr_config_file = "/tmp/rtvi/riva_asr_grpc_conf.yaml"
        self._server_config = None
        self._asr_config = None
        try:
            with open(self._asr_config_file, mode="r", encoding="utf8") as c:
                config_docs = yaml.safe_load_all(c)
                for doc in config_docs:
                    if doc["name"] == "riva_server":
                        self._server_config = doc["detail"]
                        self._server_uri = self._server_config["server_uri"]
                    if doc["name"] == "riva_model":
                        self._model_name = doc["detail"]["model_name"]
                    if doc["name"] == "riva_asr_stream":
                        self._asr_config = doc["detail"]
        except Exception as e:
            raise ValueError(f"{self._asr_config_file} is not a valid YAML file") from e

        if self._asr_config is None or self._server_uri is None:
            raise Exception("RIVA ASR configuration is not valid.")

        self._auth = None
        self._asr_service = None

    def _initialize(self):
        import riva.client  # noqa: PLC0415

        # Create GRPC channel
        ssl_cert = self._server_config.get("ssl_cert", None)
        use_ssl = self._server_config.get("use_ssl", False)
        metadata_args = []
        if use_ssl:
            metadata = self._server_config.get("metadata", None)
            if metadata is not None:
                for k, v in metadata.items():
                    metadata_args.append([k, v])

        self._auth = riva.client.Auth(
            use_ssl=use_ssl, ssl_cert=ssl_cert, uri=self._server_uri, metadata_args=metadata_args
        )
        self._asr_service = riva.client.ASRService(self._auth)

        language_code = self._asr_config.get("language_code", "en-US")
        enable_automatic_punctuation = self._asr_config.get("enable_automatic_punctuation", True)
        profanity_filter = self._asr_config.get("profanity_filter", True)

        if self._riva_nim_server:
            # Do not pass model name for NIM
            self._riva_asr_config = riva.client.RecognitionConfig(
                encoding=riva.client.AudioEncoding.LINEAR_PCM,
                sample_rate_hertz=16000,
                language_code=language_code,
                max_alternatives=1,
                enable_automatic_punctuation=enable_automatic_punctuation,
                profanity_filter=profanity_filter,
                verbatim_transcripts=False,
            )
        else:
            self._riva_asr_config = riva.client.RecognitionConfig(
                encoding=riva.client.AudioEncoding.LINEAR_PCM,
                sample_rate_hertz=16000,
                language_code=language_code,
                max_alternatives=1,
                enable_automatic_punctuation=enable_automatic_punctuation,
                model=self._model_name,
                profanity_filter=profanity_filter,
                verbatim_transcripts=False,
            )
        return True

    def _deinitialize(self):
        self._model_name = None

    def _supports_batching(self):
        return False

    def _can_batch(self, item1, item2):
        return False

    def _is_busy(self):
        return False

    def _warmup(self):
        pass

    def _process(self, chunk: ChunkInfo, request_params: VlmRequestParams | None, **kwargs):
        """Generate ASR response for a chunk (non batching)"""

        if not request_params:
            logger.log(LOG_STATUS_LEVEL, "Skipping ASR response generation for (%s)", chunk)
            return

        asr_start_time = time.time()
        nvtx_asr_process_start = nvtx.start_range(message="ASR Process-" + str(chunk), color="blue")

        logger.log(LOG_STATUS_LEVEL, "Generating ASR response for (%s)", chunk)

        audio_frames = kwargs.pop("audio_frames", None)
        audio_transcript = kwargs.pop("audio_transcript", None)
        error_msg = kwargs.pop("error", [None] * len(chunk))

        if audio_transcript is not None and len(audio_transcript) > 0:
            return {
                "chunk": chunk,
                "request_params": request_params,
                "audio_transcript": audio_transcript,
                "error": error_msg,
                "asr_start_time": asr_start_time,
                "asr_end_time": time.time(),
                **kwargs,
            }

        audio_data = bytes()
        bytes_list = []
        if audio_frames is not None:
            for frame in audio_frames:
                if frame["audio"] is not None:
                    bytes_list.append(frame["audio"].tobytes())

        audio_data = b"".join(bytes_list)
        if len(audio_data) == 0:
            asr_response = None
        else:
            asr_response = self._asr_service.offline_recognize(
                audio_data, self._riva_asr_config, future=True
            )

        def process_asr_response(
            chunk,
            request_params,
            asr_response,
            error_msg,
            asr_start_time,
            nvtx_asr_process_start,
        ):
            nvtx.end_range(nvtx_asr_process_start)
            if asr_response is not None and len(asr_response.results) >= 1:
                transcript = ""
                for result in asr_response.results:
                    transcript += result.alternatives[0].transcript
            else:
                transcript = None

            logger.log(LOG_STATUS_LEVEL, "ASR response generated for (%s), %s", chunk, transcript)

            return {
                "chunk": chunk,
                "request_params": request_params,
                "audio_transcript": transcript,
                "error": error_msg,
                "asr_start_time": asr_start_time,
                "asr_end_time": time.time(),
                **kwargs,
            }

        process_output_args = (
            chunk,
            request_params,
            asr_response,
            error_msg,
            asr_start_time,
            nvtx_asr_process_start,
        )

        if isinstance(asr_response, _MultiThreadedRendezvous):
            return self._handle_future_result(
                process_asr_response,
                *process_output_args,
            )
        else:
            return process_asr_response(*process_output_args)


@dataclass
class PipelineChunkResult:
    chunk: ChunkInfo = None
    vlm_model_output: VlmModelOutput | None = None  # VlmModelOutput object directly
    audio_transcript: str = ""
    error: str | None = None
    error_status_code: int = 500
    queue_time: float = 0
    processing_latency: float = 0
    is_live_stream_ended: bool = False
    is_stream_error: bool = False  # Stream reconnection error
    stream_error_message: str | None = None  # Error message for Kafka logging
    stream_error_attempt_count: int = 0  # Reconnection attempt count
    decode_start_time: float = 0
    decode_end_time: float = 0
    decode_retry_count: int = 0
    vlm_start_time: float = 0
    vlm_end_time: float = 0
    asr_start_time: float = 0
    asr_end_time: float = 0
    frame_times: list[float] = field(default_factory=list)

    def __str__(self) -> str:
        """String representation of the chunk result for debugging"""
        timings = {
            "decode": (
                f"{self.decode_start_time:.3f}-{self.decode_end_time:.3f}"
                if self.decode_start_time and self.decode_end_time
                else "N/A"
            ),
            "vlm": (
                f"{self.vlm_start_time:.3f}-{self.vlm_end_time:.3f}"
                if self.vlm_start_time and self.vlm_end_time
                else "N/A"
            ),
            "asr": (
                f"{self.asr_start_time:.3f}-{self.asr_end_time:.3f}"
                if self.asr_start_time and self.asr_end_time
                else "N/A"
            ),
        }

        chunk_info = f"chunk[{self.chunk.chunkIdx}]" if self.chunk else "No chunk"
        tokens = (
            f"in:{self.vlm_model_output.input_tokens}/out:{self.vlm_model_output.output_tokens}"
            if self.vlm_model_output
            else "N/A"
        )

        return (
            f"PipelineChunkResult({chunk_info}, error={bool(self.error)}, "
            f"timings={timings}, tokens={tokens}, "
            f"transcript={bool(self.audio_transcript)}, frames={len(self.frame_times)})"
        )


def check_peer_access():
    """
    Checks if all GPU pairs have peer access enabled.
    Returns False if any pair cannot access each other, True if all pairs have access.
    """
    try:
        # Get number of available GPUs
        num_gpus = torch.cuda.device_count()

        # If less than 2 GPUs, peer access is not relevant
        if num_gpus < 2:
            return True

        # Check all pairs of GPUs
        for i in range(num_gpus):
            for j in range(i + 1, num_gpus):
                # Check peer access in both directions
                if not torch.cuda.can_device_access_peer(
                    i, j
                ) or not torch.cuda.can_device_access_peer(j, i):
                    return False

        return True

    except Exception as e:
        print(f"Error checking peer access: {e}")
        return False


class VlmPipeline:
    """VLM Pipeline"""

    class _LiveStreamInfo:
        num_chunks_processed = 0
        on_chunk_result: Callable[[PipelineChunkResult], None] = None
        end_of_stream = False
        total_chunks_at_eos = 0
        all_chunks_processed = False
        gpu_id = -1

    def __init__(self, asset_dir, args) -> None:
        """Initialize the VLM pipeline"""
        logger.info("Initializing VLM pipeline")

        self._start_time = time.time()
        self._args = args

        mp_ctx = multiprocessing.get_context("spawn")

        self._dec_q = mp_ctx.Queue()
        self._dec_q_lock = mp_ctx.Lock()
        have_peer_access = check_peer_access()
        logger.info(f"Have peer access: {have_peer_access}")

        if have_peer_access:
            self._vlm_q = mp_ctx.Queue(maxsize=(128 * self._args.num_gpus))
            self._vlm_q_lock = mp_ctx.Lock()
        else:
            self._vlm_q = None
            self._vlm_q_lock = None

        self._asr_q = mp_ctx.Queue()
        self._asr_q_lock = mp_ctx.Lock()
        self._asr_procs = []

        self._chunk_counter = 0
        self._chunk_callback_map: dict[int, Callable[[PipelineChunkResult], None]] = {}
        self._live_stream_id_map: dict[str, VlmPipeline._LiveStreamInfo] = {}

        self._enqueue_lock = Lock()

        # Model path is required for locally executed models like CR1
        if (
            args.vlm_model_type
            in [
                VlmModelType.COSMOS_REASON1,
                VlmModelType.VLLM_COMPATIBLE,
            ]
            and not args.model_path
        ):
            raise Exception("model-path not provided")

        if args.vlm_model_type == VlmModelType.CUSTOM and not args.model_implementation_path:
            raise Exception("model-implementation-path not provided")

        if args.model_path and args.model_path.startswith("ngc:"):
            # NGC model path provided, download the model if not found in cache

            # Workaround for some asyncio issue
            def download_thread_func(ngc_model_path, download_prefix, model_path_):
                try:
                    model_path = download_model(
                        ngc_model_path, download_prefix, args.vlm_model_type.value
                    )
                except Exception as ex:
                    model_path_[1] = ex
                    return
                model_path_[0] = model_path

            model_path_ = ["", ""]
            download_thread = Thread(
                target=download_thread_func,
                args=(args.model_path[4:], NGC_MODEL_CACHE, model_path_),
            )
            download_thread.start()
            download_thread.join()
            if model_path_[1]:
                raise model_path_[1] from None
            args.model_path = model_path_[0]
        if args.model_path and args.model_path.startswith("git:"):
            args.model_path = download_model_git(args.model_path[4:], NGC_MODEL_CACHE)
        if args.model_path and args.model_repository_script_path:
            logger.info(f"Running model repository script: {args.model_repository_script_path}")
            subprocess.run(
                [
                    "python3",
                    args.model_repository_script_path,
                    "--model_path",
                    args.model_path,
                    "--triton_repo_path",
                    "/tmp/triton_model_repo",
                    "--max_batch_size",
                    str(args.vlm_batch_size),
                ],
                check=True,
            )
            logger.info("Model repository script completed")

        self._processed_chunk_queue = mp_ctx.Queue()
        self._token_stream_queue = mp_ctx.Queue()  # For text-only token-level streaming
        self._processed_chunk_queue_watcher_stop_event = Event()
        self._processed_chunk_queue_watcher_thread = None

        # Create the ASR processes
        # Only create ASR process if audio enabled AND VLM doesn't handle audio natively
        self._num_asr_procs = 1
        vlm_supports_audio = os.environ.get("VLM_MODEL_SUPPORTS_AUDIO", "false").lower() == "true"

        if self._args.enable_audio and not vlm_supports_audio:
            logger.info(
                "Creating ASR process for audio transcription (VLM_MODEL_SUPPORTS_AUDIO=false)"
            )
            self._asr_procs = [
                AsrProcess(
                    args,
                    asset_dir,
                    i,
                    not self._args.enable_audio,
                    self._asr_q,
                    self._asr_q_lock,
                )
                for i in range(self._num_asr_procs)
            ]
            for idx, asr_proc in enumerate(self._asr_procs):
                asr_proc.set_output_queue(self._processed_chunk_queue)
                asr_proc.set_final_output_queue(self._processed_chunk_queue)
                asr_proc.start()
        else:
            if self._args.enable_audio:
                logger.info(
                    "Skipping ASR process - VLM handles audio natively "
                    "(VLM_MODEL_SUPPORTS_AUDIO=true)"
                )
            self._asr_procs = []  # No ASR processes needed

        self._num_gpus_per_vlm_proc = max(int(os.environ.get("NUM_GPUS_PER_VLM_PROC", "") or 1), 1)

        self._num_vlm_procs = args.num_gpus
        if args.vlm_model_type == VlmModelType.OPENAI_COMPATIBLE:
            self._num_vlm_procs = 1
        elif not args.disable_vlm:
            self._num_vlm_procs = args.num_gpus // self._num_gpus_per_vlm_proc
            if self._num_vlm_procs == 0:
                raise Exception(
                    f"Not enough GPUs to run VLM pipeline. Available GPUs: {args.num_gpus}."
                    f" GPUs per VLM instance: {self._num_gpus_per_vlm_proc}"
                )
            logger.info(f"GPUs per VLM instance: {self._num_gpus_per_vlm_proc}")

        logger.info(f"num_vlm_procs set to {self._num_vlm_procs}")

        # Create the VLM processes, one on each GPU
        self._vlm_procs = [
            VlmProcess(
                args,
                asset_dir,
                ",".join(
                    map(
                        str,
                        range(
                            i * self._num_gpus_per_vlm_proc, (i + 1) * self._num_gpus_per_vlm_proc
                        ),
                    )
                ),
                args.disable_vlm,
                self._vlm_q,
                self._vlm_q_lock,
            )
            for i in range(self._num_vlm_procs)
        ]
        self._decoder_procs = []
        for idx, vlm_proc in enumerate(self._vlm_procs):
            # Only route to ASR if audio is enabled AND VLM doesn't handle audio natively
            if self._args.enable_audio and not vlm_supports_audio:
                # Audio enabled but VLM doesn't support it → use ASR for transcription
                vlm_proc.set_output_queue(self._asr_procs[idx % self._num_asr_procs].input_queue)
            else:
                # Either audio disabled OR VLM handles audio natively → skip ASR
                vlm_proc.set_output_queue(self._processed_chunk_queue)
            vlm_proc.set_final_output_queue(self._processed_chunk_queue)
            vlm_proc.set_token_stream_queue(self._token_stream_queue)
            vlm_proc.start()

        # vLLM profiles available GPU memory during startup.  Initializing the
        # decoder at the same time can transiently release GPU memory and trip
        # vLLM's memory-profiling guard, so let VLM settle first.
        for idx, proc in enumerate(self._vlm_procs):
            if not proc.wait_for_initialization():
                self.stop()
                raise Exception(f"Failed to load VLM on GPU {idx}")

        # Create the chunk decoding processes, one on each GPU
        self._decoder_procs = [
            DecoderProcess(args, i, args.disable_decoding, self._dec_q, self._dec_q_lock)
            for i in range(args.num_gpus)
        ]
        for idx, dec_proc in enumerate(self._decoder_procs):
            dec_proc.set_output_queue(self._vlm_procs[idx % self._num_vlm_procs].input_queue)
            dec_proc.set_final_output_queue(self._processed_chunk_queue)
            dec_proc.start()

        # Wait for all processes to complete initialization
        for idx, proc in enumerate(self._decoder_procs):
            if not proc.wait_for_initialization():
                self.stop()
                raise Exception(f"Failed to load Decoder on GPU {idx}")
        for idx, proc in enumerate(self._asr_procs):
            if not proc.wait_for_initialization():
                self.stop()
                raise Exception(f"Failed to load ASR process {idx}")

        # Create a thread to gather chunks processed by the VLM pipeline
        self._processed_chunk_queue_watcher_stop_event = Event()
        self._processed_chunk_queue_watcher_thread = Thread(
            target=self._watch_processed_chunk_queue
        )
        self._processed_chunk_queue_watcher_thread.start()

        logger.info("Initialized VLM pipeline")

    def _watch_processed_chunk_queue(self):
        """Gather chunks processed by the pipeline and return via callback"""

        while not self._processed_chunk_queue_watcher_stop_event.is_set():
            try:
                item: dict = self._processed_chunk_queue.get(timeout=1)
            except queue.Empty:
                continue

            if item.get("stream_error", False):
                # Handle stream reconnection errors - send to callback for Kafka logging
                stream_id = item.get("stream_id", "")
                if stream_id in self._live_stream_id_map:
                    lsinfo = self._live_stream_id_map[stream_id]
                    chunk_result = PipelineChunkResult()
                    chunk_result.is_stream_error = True
                    chunk_result.stream_error_message = item.get("error_message", "")
                    chunk_result.stream_error_attempt_count = item.get("attempt_count", 0)
                    lsinfo.on_chunk_result(chunk_result)
                continue

            if item.get("live_stream_ended", False):
                live_stream_id = item["live_stream_id"]
                # Membership guard: remove_live_stream may have popped the entry
                # after a drain timeout, in which case a late EOS from decoder
                # must be ignored rather than crash the watcher with KeyError.
                # Mirrors the guards used on stream-error (above) and chunk
                # results (below).
                if live_stream_id not in self._live_stream_id_map:
                    continue
                lsinfo = self._live_stream_id_map[live_stream_id]
                lsinfo.end_of_stream = True
                lsinfo.total_chunks_at_eos = item["total_chunks"]

                if lsinfo.num_chunks_processed >= lsinfo.total_chunks_at_eos:
                    chunk_result = PipelineChunkResult()
                    chunk_result.is_live_stream_ended = True
                    lsinfo.on_chunk_result(chunk_result)
                    lsinfo.all_chunks_processed = True
                continue

            chunk_result = PipelineChunkResult()
            chunk_result.error = item.get("error", None)
            chunk_result.error_status_code = item.get("error_status_code", 500)
            chunk_result.chunk = item["chunk"]
            chunk_result.decode_retry_count = item.get("decode_retry_count", 0)
            if not chunk_result.error:
                # vlm_output is a single VlmModelOutput object (already unbatched)
                chunk_result.vlm_model_output = item.get("vlm_output", None)

                audio_transcript_raw = item.get("audio_transcript", None)
                if isinstance(audio_transcript_raw, list):
                    chunk_result.audio_transcript = (
                        " ".join(audio_transcript_raw) if audio_transcript_raw else ""
                    )
                else:
                    chunk_result.audio_transcript = audio_transcript_raw
                chunk_result.queue_time = max(
                    item.get("decode_start_time", item.get("vlm_start_time", 0))
                    - item.get("enqueue_time", 0),
                    0,
                )
                chunk_result.processing_latency = max(
                    item.get("vlm_end_time", 0)
                    - item.get("decode_start_time", item.get("vlm_start_time", 0)),
                    0,
                )
                chunk_result.decode_start_time = item.get("decode_start_time", 0)
                chunk_result.decode_end_time = item.get("decode_end_time", 0)
                chunk_result.vlm_start_time = item.get("vlm_start_time", 0)
                chunk_result.vlm_end_time = item.get("vlm_end_time", 0)
                chunk_result.asr_start_time = item.get("asr_start_time", 0)
                chunk_result.asr_end_time = item.get("asr_end_time", 0)
                chunk_result.frame_times = item.get("frame_times", [])

            if item.get("is_live_stream", False):
                if chunk_result.vlm_end_time and chunk_result.vlm_end_time - (
                    chunk_result.vlm_start_time
                ) > (chunk_result.chunk.end_pts - chunk_result.chunk.start_pts):
                    logger.warning(
                        "Detected high load on the system. This may result in higher response"
                        " times. Try reducing number of streams or increasing the chunk size"
                    )
                if chunk_result.chunk.streamId in self._live_stream_id_map:
                    lsinfo = self._live_stream_id_map[chunk_result.chunk.streamId]
                    lsinfo.on_chunk_result(chunk_result)
                    lsinfo.num_chunks_processed += 1
                    if (
                        lsinfo.end_of_stream
                        and lsinfo.num_chunks_processed >= lsinfo.total_chunks_at_eos
                    ):
                        chunk_result = PipelineChunkResult()
                        chunk_result.is_live_stream_ended = True
                        lsinfo.on_chunk_result(chunk_result)
                        lsinfo.all_chunks_processed = True
                continue
            callback = self._chunk_callback_map.pop(item["chunk_id"], None)
            if callback:
                callback(chunk_result)

    def abort_chunks(self, stream_id: str):
        for proc in self._decoder_procs + self._vlm_procs + self._asr_procs:
            proc.send_command("drop-chunks", stream_id=stream_id)

    def abort_chunks_done(self, stream_id: str):
        for proc in self._decoder_procs + self._vlm_procs + self._asr_procs:
            proc.send_command("stop-drop-chunks", stream_id=stream_id)

    def stop(self, force=False):
        """Stop the VLM Pipeline"""
        logger.info("Stopping VLM pipeline")
        if force:
            # Force terminate the processes started by the pipeline
            for proc in self._decoder_procs:
                proc.terminate()
            for proc in self._vlm_procs:
                proc.terminate()
            for proc in self._asr_procs:
                proc.terminate()
        else:
            # Wait for the processes started by VLM pipeline to stop gracefully
            for proc in self._decoder_procs:
                proc.stop()
            for proc in self._vlm_procs:
                proc.stop()
            for proc in self._asr_procs:
                proc.stop()

        # Stop the processed chunk result watcher thread
        if self._processed_chunk_queue_watcher_thread:
            self._processed_chunk_queue_watcher_stop_event.set()
            self._processed_chunk_queue_watcher_thread.join()

        logger.info("Stopped VLM pipeline")

    def get_token_stream_queue(self):
        """Get the token stream queue for text-only SSE streaming."""
        return self._token_stream_queue

    def get_models_info(self):
        """Get loaded model information using dynamic model loader"""
        # Determine the class path to use
        class_path = get_model_class_path(
            self._args.vlm_model_type, self._args.model_path, self._args.model_implementation_path
        )

        # Load model class and get info
        try:
            loader = DynamicModelLoader(class_path)
            model_class = loader.get_model_class()
            id, api_type, owned_by = model_class.get_model_info(
                self._args.model_path, self._args.vlm_model_type
            )
        except Exception as e:
            logger.warning(f"Failed to get model info via dynamic loader: {e}")
            # Fallback for models that don't implement get_model_info properly
            id = (
                os.path.basename(os.path.abspath(self._args.model_path))
                if self._args.model_path
                else "unknown"
            )
            api_type = "internal"
            owned_by = "custom"

        info = VlmModelInfo()
        info.api_type = api_type
        info.id = id
        info.created = self._start_time
        info.owned_by = owned_by
        return info

    def enqueue_chunk(
        self,
        chunk: ChunkInfo,
        on_chunk_result: Callable[[PipelineChunkResult], None],
        vlm_query: VlmQuery,
        request_id="",
        video_codec=None,
        decode_only=False,
    ):
        # Build request params from vlm_query
        request_params = VlmRequestParams.from_vlm_query(vlm_query)

        with self._enqueue_lock:
            curr_chunk_counter = self._chunk_counter
            self._chunk_counter += 1
            self._chunk_callback_map[curr_chunk_counter] = on_chunk_result
        self._decoder_procs[curr_chunk_counter % self._args.num_gpus].enqueue_chunk(
            chunk,
            vlm_query=vlm_query,
            request_params=request_params,
            chunk_id=curr_chunk_counter,
            enqueue_time=time.time(),
            request_id=request_id,
            video_codec=video_codec,
            decode_only=decode_only,
        )

    def enqueue_vlm_text_chunk(
        self,
        chunk: ChunkInfo,
        on_chunk_result: Callable[[PipelineChunkResult], None],
        vlm_query: VlmQuery,
        request_id="",
        chat_messages: list | None = None,
    ):
        """Enqueue a text-only chunk for VLM processing (no frames).

        Unlike enqueue_text_chunk (for embeddings), this uses VlmQuery params
        and routes through the VLM model's generate_text_only path.

        Args:
            chat_messages: Optional structured messages (system/user/assistant dicts)
                           for multi-turn conversation. If provided, used instead of
                           flattened vlm_query.prompt.
        """
        request_params = VlmRequestParams.from_vlm_query(vlm_query)
        if chat_messages:
            request_params.chat_messages = chat_messages
        with self._enqueue_lock:
            curr_chunk_counter = self._chunk_counter
            self._chunk_counter += 1
            self._chunk_callback_map[curr_chunk_counter] = on_chunk_result
            decode_start_time = decode_end_time = time.time()
            self._vlm_procs[curr_chunk_counter % self._args.num_vlm_procs].enqueue_chunk(
                chunk,
                request_params=request_params,
                chunk_id=curr_chunk_counter,
                enqueue_time=time.time(),
                request_id=request_id,
                decode_start_time=decode_start_time,
                decode_end_time=decode_end_time,
                frames=[],
                frame_times=[],
                audio_frames=[],
                audio_transcript=[],
                error=None,
                is_live_stream=False,
            )

    def enqueue_text_chunk(
        self,
        chunk: ChunkInfo,
        on_chunk_result: Callable[[PipelineChunkResult], None],
        text_embeddings_query: TextEmbeddingsQuery,
        request_id="",
    ):
        request_params = VlmRequestParams.from_text_embeddings_query(text_embeddings_query)
        with self._enqueue_lock:
            curr_chunk_counter = self._chunk_counter
            self._chunk_counter += 1
            self._chunk_callback_map[curr_chunk_counter] = on_chunk_result
            decode_start_time = decode_end_time = time.time()
            self._vlm_procs[curr_chunk_counter % self._args.num_vlm_procs].enqueue_chunk(
                chunk,
                request_params=request_params,
                chunk_id=curr_chunk_counter,
                enqueue_time=time.time(),
                request_id=request_id,
                decode_start_time=decode_start_time,
                decode_end_time=decode_end_time,
                frames=[],
                frame_times=[],
                audio_frames=[],
                audio_transcript=[],
                error=None,
                is_live_stream=False,
            )

    def add_live_stream(
        self,
        asset: Asset,
        vlm_query: VlmQuery,
        on_chunk_result: Callable[[PipelineChunkResult], None],
    ):
        """Add a live stream for processing.

        Args:
            asset: Asset object containing stream URL and credentials
            vlm_query: VlmQuery object containing all query parameters
                (including chunk_duration settings)
            on_chunk_result: Callback function for chunk results
        """
        gpu_dec_use_cnt = {i: 0 for i in range(self._args.num_gpus)}
        for info in self._live_stream_id_map.values():
            gpu_dec_use_cnt[info.gpu_id] += 1
        least_used_gpu = min(gpu_dec_use_cnt, key=gpu_dec_use_cnt.get)

        self._live_stream_id_map[asset.asset_id] = self._LiveStreamInfo()
        self._live_stream_id_map[asset.asset_id].gpu_id = least_used_gpu
        self._live_stream_id_map[asset.asset_id].on_chunk_result = on_chunk_result

        # Build request params from vlm_query
        request_params = VlmRequestParams.from_vlm_query(vlm_query)

        self._decoder_procs[least_used_gpu].send_command(
            "start-live-stream",
            asset=asset,
            vlm_query=vlm_query,
            request_params=request_params,
        )

    def remove_live_stream(
        self, live_stream_id: str, timeout_sec: Optional[float] = None
    ) -> Optional[float]:
        """Drain and remove a live stream.

        Returns the drain wall-clock in seconds, or ``None`` if the stream
        wasn't registered (nothing to drain). The return value lets callers
        record per-stream drain latency without racing on shared state —
        critical under parallel batch delete.
        """
        if live_stream_id not in self._live_stream_id_map:
            return None
        lsinfo = self._live_stream_id_map[live_stream_id]

        if timeout_sec is None:
            timeout_sec = float(os.environ.get("RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC", "30"))

        self._decoder_procs[lsinfo.gpu_id].send_command(
            "stop-live-stream", live_stream_id=live_stream_id
        )

        for proc in self._vlm_procs:
            proc.send_command("drop-chunks", stream_id=live_stream_id)
        for proc in self._asr_procs:
            proc.send_command("drop-chunks", stream_id=live_stream_id)

        drain_start = time.monotonic()
        deadline = drain_start + timeout_sec
        timed_out = False
        while not lsinfo.all_chunks_processed:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.1)
        drain_elapsed = time.monotonic() - drain_start

        if timed_out:
            logger.warning(
                "Drain timed out after %.1fs for live-stream %s; "
                "forcing completion and aborting in-flight chunks.",
                drain_elapsed,
                live_stream_id,
            )
            lsinfo.all_chunks_processed = True

        try:
            self._live_stream_id_map.pop(live_stream_id)
        except KeyError as e:
            # can happen if multiple stream delete requests are happening in parallel
            logger.info(f"{e}: live stream already removed from map;")

        for proc in self._vlm_procs:
            proc.send_command("stop-drop-chunks", stream_id=live_stream_id)
        for proc in self._asr_procs:
            proc.send_command("stop-drop-chunks", stream_id=live_stream_id)

        return drain_elapsed

    def get_health_status(self):
        checks = []
        for i, decoder in enumerate(self._decoder_procs):
            if decoder and not decoder._disabled:
                is_alive = decoder.is_alive()
                checks.append(
                    HealthStatus(
                        healthy=is_alive,
                        message=f"Decoder process {i} GPU {decoder._gpu_id} is {'running' if is_alive else 'not running'}",  # noqa: E501
                        component=f"decoder_process_{i}",
                        timestamp=time.time(),
                    )
                )
        for i, vlm in enumerate(self._vlm_procs):
            if vlm and not vlm._disabled:
                is_alive = vlm.is_alive()
                checks.append(
                    HealthStatus(
                        healthy=is_alive,
                        message=f"VLM process {i} is {'running' if is_alive else 'not running'}",
                        component=f"vlm_process_{i}",
                        timestamp=time.time(),
                    )
                )
        for i, asr in enumerate(self._asr_procs):
            if asr and not asr._disabled:
                is_alive = asr.is_alive()
                checks.append(
                    HealthStatus(
                        healthy=is_alive,
                        message=f"ASR process {i} is {'running' if is_alive else 'not running'}",
                        component=f"asr_process_{i}",
                        timestamp=time.time(),
                    )
                )
        return checks

    @staticmethod
    def populate_argument_parser(parser: ArgumentParser):
        """Add VLM Pipeline arguments to the argument parser"""
        parser.add_argument(
            "--num-gpus",
            default=1,
            type=int,
            help="Number of GPUs to run the pipeline on",
        )
        parser.add_argument(
            "--num-vlm-procs",
            default=1,
            type=int,
            help="Number of VLM processes to use in parallel;"
            " applicable only for openai-compat; others == num-gpus",
        )
        parser.add_argument(
            "--num-decoders-per-gpu",
            default=5,
            type=int,
            help="Number of Decoder pipelines to run on each GPU in parallel",
        )

        parser.add_argument(
            "--vlm-model-type",
            type=VlmModelType,
            choices=list(VlmModelType),
            required=True,
            help="Vision Language Model to use. Use 'custom' for custom model implementations.",
        )
        parser.add_argument(
            "--vlm-batch-size",
            type=int,
            default=1,
            help="Batch size to use for the VLM model",
        )

        parser.add_argument(
            "--disable-vlm",
            action="store_true",
            default=False,
            help="Disable the VLM",
        )
        parser.add_argument(
            "--disable-decoding",
            action="store_true",
            default=False,
            help="Disable Decoding",
        )
        parser.add_argument(
            "--model-path",
            type=str,
            required=False,
            help="Location of the model weights/files",
        )
        parser.add_argument(
            "--model-implementation-path",
            type=str,
            required=False,
            help="Location of the model implementation (inference.py). "
            "If not specified, uses --model-path for both implementation and weights.",
        )
        parser.add_argument(
            "--model-repository-script-path",
            type=str,
            required=False,
            help="Location of the model repository script. "
            "If provided, the script is used to export the model to ONNX and "
            "create the TRITON model repository. "
            "The script should be a python script that takes the following arguments: "
            "--model_path, --triton_repo_path, --max_batch_size",
        )
        parser.add_argument(
            "--num-frames-per-second-or-fixed-frames-chunk",
            type=int,
            help="Number of frames to pick from each chunk",
        )
        parser.add_argument(
            "--max-live-streams",
            type=int,
            default=256,
            help="Number of maximum live streams to support at a time",
        )
        parser.add_argument(
            "--enable-audio",
            action="store_true",
            default=False,
            help="Enable audio transcription using ASR",
        )
        parser.add_argument(
            "--use-fps-for-chunking",
            action="store_true",
            default=False,
            help="Use FPS for chunking",
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="VLM Pipeline", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    VlmPipeline.populate_argument_parser(parser)
    args = parser.parse_args()

    try:
        pipeline = VlmPipeline("/tmp/rtvi/assets", args)
    except Exception as ex:
        logger.error("Could not load VLM Pipeline - " + str(ex))
        sys.exit(-1)

    pipeline.stop()
