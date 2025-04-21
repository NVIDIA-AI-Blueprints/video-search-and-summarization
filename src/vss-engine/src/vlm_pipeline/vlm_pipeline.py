#!/usr/bin/env python3
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
import concurrent.futures
import json
import multiprocessing
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time
from argparse import ArgumentParser
from enum import Enum
from threading import Event, Lock, Thread
from typing import Callable, Optional

import nvtx
import riva.client
import torch
import yaml
from grpc._channel import _MultiThreadedRendezvous

from chunk_info import ChunkInfo
from models.custom.custom_model import CustomModelContext, CustomModuleLoader
from via_logger import LOG_STATUS_LEVEL, TimeMeasure, logger

from .embedding_helper import EmbeddingHelper
from .ngc_model_downloader import download_model, download_model_git
from .process_base import ViaProcessBase

# Location to download and cache NGC models
NGC_MODEL_CACHE = os.environ.get("NGC_MODEL_CACHE", "") or os.path.expanduser(
    "~/.via/ngc_model_cache/"
)

FORCE_TRT = True


class VlmModelType(Enum):
    VILA_15 = "vila-1.5"
    NVILA = "nvila"
    OPENAI_COMPATIBLE = "openai-compat"  # Any OpenAI API compatible on NIM/OpenAI/Azure-OpenAI

    def __str__(self):
        return self.value


class TrtLlmMode(Enum):
    FP16 = "fp16"
    FP8 = "fp8"
    INT8 = "int8"
    INT4 = "int4"
    INT4_AWQ = "int4_awq"

    def __str__(self):
        return self.value


class VlmModelInfo:
    """Model inforamtion"""

    def __init__(self) -> None:
        self.id = ""
        self.created = 0
        self.owned_by = ""
        self.api_type = ""


class VlmRequestParams:
    vlm_generation_config: Optional[dict] = None
    vlm_prompt: Optional[str] = None

    def __eq__(self, other) -> bool:
        if isinstance(other, VlmRequestParams):
            return (
                self.vlm_prompt == other.vlm_prompt
                and self.vlm_generation_config == other.vlm_generation_config
            )
        return False


class DecoderProcess(ViaProcessBase):
    """Chunk decoder process"""

    def __init__(
        self, args, gpu_id=0, disabled=False, input_queue=None, input_queue_lock=None
    ) -> None:
        super().__init__(
            gpu_id=gpu_id,
            disabled=disabled,
            input_queue=input_queue,
            input_queue_lock=input_queue_lock,
        )
        self._vlm_model_type = args.vlm_model_type
        self._num_decoders_per_gpu = args.num_decoders_per_gpu
        self._num_frames_per_chunk = args.num_frames_per_chunk
        self._model_path = args.model_path
        self._module_loader = None
        self._max_live_streams = max(1, -(-args.max_live_streams // args.num_gpus))
        self._enable_audio = args.enable_audio
        self._cv_pipeline_configs = args.cv_pipeline_configs

    def _initialize(self):
        from .video_file_frame_getter import DefaultFrameSelector, VideoFileFrameGetter

        self._live_stream_handle_info: dict[str, dict] = {}

        self._nfrms = self._num_frames_per_chunk
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
        if self._vlm_model_type is None:
            # use custom module load if model type is not specified
            module_loader = CustomModuleLoader(self._model_path)
            manifest = module_loader.manifest()
            input_spec = manifest.pop("input", None)
            if input_spec:
                self._nfrms = input_spec.pop("number_of_frames", 1)
                crop_size = input_spec.pop("crop_size", None)
                if crop_size:
                    self._crop_width = crop_size[0]
                    self._crop_height = crop_size[1]
                self._enable_jpeg_tensors = input_spec.pop("jpeg_encoded", False)
            self._minframes = 1
        elif self._vlm_model_type in [VlmModelType.VILA_15]:
            if not self._nfrms:
                self._nfrms = 8
            self._minframes = 1

            sys.path.append(os.path.dirname(os.path.dirname(__file__)) + "/models/vila15/VILA")
            import llava.model.language_model.llava_llama  # noqa: F401
            from llava.model.multimodal_encoder.intern_encoder import (
                InternVisionPreprocessor,
            )
            from transformers import AutoModel
            from transformers.models.siglip.image_processing_siglip import (
                SiglipImageProcessor,
            )

            # Load the model to pseudo memory (meta). This is required to get
            # the image preprocessor without acutally loading the model
            with TimeMeasure("VILA decoder Model load"):
                device_map = {
                    "model.vision_tower": "meta",
                    "model.embed_tokens": "meta",
                    "model.layers": "meta",
                    "model.norm": "meta",
                    "lm_head": "meta",
                    "model.mm_projector": "meta",
                }
                model = AutoModel.from_pretrained(
                    self._model_path,
                    low_cpu_mem_usage=True,
                    device_map=device_map,
                )

                # Load the image preprocessor
                image_processor = model.get_vision_tower().image_processor

                # Populate the image preprocessing parameters for VILA 1.5
                if isinstance(image_processor, InternVisionPreprocessor):
                    self._shortest_edge = [
                        image_processor.size["height"],
                        image_processor.size["width"],
                    ]
                    self._rescale_factor = 1 / 255.0
                    self._image_mean = (0.485, 0.456, 0.406)
                    self._image_std = (0.229, 0.224, 0.225)
                    self._do_preprocess = True
                    # self._run_image_processor = True
                    # self._image_processor = image_processor
                elif isinstance(image_processor, SiglipImageProcessor):
                    self._image_mean = image_processor.image_mean
                    self._rescale_factor = image_processor.rescale_factor
                    self._image_std = image_processor.image_std
                    if hasattr(image_processor, "crop_size"):
                        self._crop_height = image_processor.crop_size["height"]
                        self._crop_width = image_processor.crop_size["width"]
                    if "shortest_edge" in image_processor.size:
                        self._shortest_edge = image_processor.size["shortest_edge"]
                    elif "width" in image_processor.size and "height" in image_processor.size:
                        self._shortest_edge = [
                            image_processor.size["height"],
                            image_processor.size["width"],
                        ]
                    self._do_preprocess = True
                    self._image_aspect_ratio = model.config.image_aspect_ratio

                else:
                    raise Exception("Unsupported image preprocessor")

            del model
            torch.cuda.empty_cache()
        elif self._vlm_model_type in [VlmModelType.NVILA]:
            with open(self._model_path + "/config.json") as f:
                config = json.load(f)
            if not self._nfrms or self._nfrms > config.get("num_video_frames", 8):
                self._nfrms = config.get("num_video_frames", 8)
            self._minframes = 1
            self._data_type_int8 = True

        elif self._vlm_model_type in [VlmModelType.OPENAI_COMPATIBLE]:
            if not self._nfrms or self._nfrms > 10:
                self._nfrms = 10
            self._minframes = 1
            # For OpenAI compatible models, JPEG images are used
            self._enable_jpeg_tensors = True

        else:
            self._width = 224
            self._height = 224
            if not self._nfrms:
                self._nfrms = 8
            self._minframes = 8

        if (
            "VLM_INPUT_WIDTH" in os.environ
            and os.environ["VLM_INPUT_WIDTH"]
            and "VLM_INPUT_HEIGHT" in os.environ
            and os.environ["VLM_INPUT_HEIGHT"]
        ):
            self._width = int(os.environ["VLM_INPUT_WIDTH"])
            self._height = int(os.environ["VLM_INPUT_HEIGHT"])
            logger.info(f"Forcing input to Embedding Gen {self._width}X{self._height}")

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
            max_workers=int(self._num_decoders_per_gpu)
        )
        return True

    def _warmup(self):
        chunk = ChunkInfo()
        chunk.file = "/opt/nvidia/deepstream/deepstream/samples/streams/sample_office.mp4"
        chunk.end_pts = 5000000000
        for fgetter in self._fgetters:
            frames, frame_times, audio_frames = fgetter.get_frames(chunk, True)
        self._output_queue.put(
            {
                "chunk": chunk,
                "chunk_id": -1,
                "frames": frames,
                "frame_times": frame_times,
                "audio_frames": audio_frames,
                "request_params": None,
                "is_warmup": True,
            }
        )

    def _decode_chunk(
        self,
        fgetter,
        chunk: ChunkInfo,
        num_frames_per_chunk,
        vlm_input_width,
        vlm_input_height,
        **kwargs,
    ):
        from .video_file_frame_getter import DefaultFrameSelector

        decode_start_time = time.time()
        logger.log(LOG_STATUS_LEVEL, f"Chunk ({chunk}) decode starting")
        nvtx_decode_start = nvtx.start_range(message="Decode Process-" + str(chunk), color="blue")
        if num_frames_per_chunk:
            frame_selector = DefaultFrameSelector(num_frames_per_chunk)
        else:
            frame_selector = DefaultFrameSelector(self._nfrms)

        enable_audio = kwargs["enable_audio"] if "enable_audio" in kwargs else False

        if vlm_input_width or vlm_input_height:
            fgetter._set_frame_resolution(vlm_input_width, vlm_input_height)
        frames, frame_times, audio_frames = fgetter.get_frames(
            chunk, True, frame_selector, enable_audio, request_id=kwargs["request_id"]
        )
        frame_times = [float("%.2f" % frame_ele) for frame_ele in frame_times]
        nvtx.end_range(nvtx_decode_start)
        self._fgetters.append(fgetter)
        logger.log(LOG_STATUS_LEVEL, f"Chunk ({chunk}) decoded, frames={len(frames)}")
        logger.debug(f"Chunk ({chunk}) decoded, frames={len(frames)}")
        logger.debug(f"decoded{self._minframes} {len(frames)}")
        decode_end_time = time.time()
        if len(frames) >= self._minframes:
            return {
                "chunk": chunk,
                "frames": frames,
                "frame_times": frame_times,
                "audio_frames": audio_frames,
                "decode_start_time": decode_start_time,
                "decode_end_time": decode_end_time,
                **kwargs,
            }
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
        live_stream_id: str,
        live_stream_url: str,
        username: str,
        password: str,
        chunk_size: int,
        num_frames_per_chunk: int,
        vlm_input_width: int,
        vlm_input_height: int,
        enable_audio: bool,
        enable_cv_pipeline: bool,
        cv_pipeline_text_prompt: str,
        **kwargs,
    ):
        from .video_file_frame_getter import DefaultFrameSelector, VideoFileFrameGetter

        logger.info(f"Starting live stream {live_stream_id}")
        if num_frames_per_chunk:
            frame_selector = DefaultFrameSelector(num_frames_per_chunk)
        else:
            frame_selector = DefaultFrameSelector(self._nfrms)

        fgetter = VideoFileFrameGetter(
            frame_selector=frame_selector,
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
            cv_pipeline_configs=self._cv_pipeline_configs,
        )
        if vlm_input_width or vlm_input_height:
            fgetter._set_frame_resolution(vlm_input_width, vlm_input_height)

        self._live_stream_handle_info[live_stream_id] = {"frame_getter": fgetter, "num_chunks": 0}

        def on_chunk_decoded(
            chunk: ChunkInfo, frames, frame_times, transcripts, live_stream_id, **kwargs
        ):
            frame_times = [float("%.2f" % frame_ele) for frame_ele in frame_times]
            chunk.streamId = live_stream_id

            asr_output = ""
            for asr_transcript in transcripts:
                asr_output += asr_transcript["transcript"] + " "
            transcript = asr_output if len(asr_output) != 0 else None

            logger.log(LOG_STATUS_LEVEL, f"Decoded new chunk ({chunk}), frames={len(frames)}")
            if len(frames) >= self._minframes:
                self._handle_result(
                    {
                        "chunk": chunk,
                        "frames": frames,
                        "frame_times": frame_times,
                        "audio_transcript": transcript,
                        "is_live_stream": True,
                        **kwargs,
                    },
                    chunk=chunk,
                    **kwargs,
                )
                self._live_stream_handle_info[live_stream_id]["num_chunks"] += 1

        logger.debug(f"Pipeline for live stream starting up: {live_stream_id}")
        fgetter.stream(
            live_stream_url=live_stream_url,
            chunk_duration=chunk_size,
            chunk_overlap_duration=0,
            username=username,
            password=password,
            enable_cv_pipeline=enable_cv_pipeline,
            cv_pipeline_text_prompt=cv_pipeline_text_prompt,
            live_stream_id=live_stream_id,
            on_chunk_decoded=(
                lambda chunk, frames, frame_times, transcripts, live_stream_id=live_stream_id, kwargs=kwargs: on_chunk_decoded(  # noqa: E501
                    chunk, frames, frame_times, transcripts, live_stream_id, **kwargs
                )
            ),
            enable_audio=enable_audio,
        )

        logger.debug(f"Pipeline for live stream tearing down: {live_stream_id}")
        fgetter.destroy_pipeline()
        logger.debug(f"Pipeline for live stream torn down: {live_stream_id}")

        self._final_output_queue.put(
            {
                "live_stream_ended": True,
                "live_stream_id": live_stream_id,
                "total_chunks": self._live_stream_handle_info[live_stream_id]["num_chunks"],
            }
        )
        self._live_stream_handle_info.pop(live_stream_id)

    def _deinitialize(self):
        for fgetter in self._fgetters:
            fgetter.destroy_pipeline()

    def _is_busy(self):
        return len(self._fgetters) == 0

    def _process(self, **kwargs):
        """Decode a chunk and return selected frames as raw frames / JPEG images"""
        if self._vlm_model_type == VlmModelType.NVILA:
            return self._file_thread_pool.submit(self._decode_chunk, self._fgetters.pop(), **kwargs)
        else:
            return self._thread_pool.submit(self._decode_chunk, self._fgetters.pop(), **kwargs)


class EmbeddingProcess(ViaProcessBase):
    """Embedding Generation Process"""

    def __init__(
        self, args, asset_dir, gpu_id=0, disabled=False, input_queue=None, input_queue_lock=None
    ) -> None:
        super().__init__(
            gpu_id=gpu_id,
            disabled=disabled,
            input_queue=input_queue,
            input_queue_lock=input_queue_lock,
            qsize=3,
        )
        self._vlm_model_type = args.vlm_model_type
        self._model_path = args.model_path
        self._use_trt = args.use_trt
        self._trt_engine_dir = args.trt_engine_dir
        self._asset_dir = asset_dir

    def _initialize(self):
        # Create an instance of embedding helper to save embeddings
        self._emb_helper = EmbeddingHelper(self._asset_dir)

        # Model specific embedding generator
        if self._vlm_model_type == VlmModelType.VILA_15:
            from models.vila15.vila15_embedding_generator import (
                Vila15EmbeddingGenerator,
            )

            self._emb_generator = Vila15EmbeddingGenerator(
                self._model_path,
                use_trt=self._use_trt,
                trt_engine_dir=self._trt_engine_dir,
                async_output=True,
            )
        elif self._vlm_model_type == VlmModelType.OPENAI_COMPATIBLE:
            from models.common.frame_jpeg_tensor_generator import (
                FrameJPEGTensorGenerator,
            )

            self._emb_generator = FrameJPEGTensorGenerator()
        elif self._vlm_model_type is None:
            model = CustomModuleLoader(self._model_path).load_model()
            self._emb_generator = model.get_embedding_generator()

        return True

    def _deinitialize(self):
        self._emb_generator = None

    def _warmup(self):
        if self._emb_generator is None:
            return

        if hasattr(self._emb_generator, "warmup"):
            self._emb_generator.warmup()

    def _supports_batching(self):
        return True

    def _process(self, chunk: list[ChunkInfo], frames: list, frame_times: list, **kwargs):
        """Generate embeddings for a chunk and save them"""

        if kwargs.get("is_warmup", [False])[0]:
            return

        audio_frames = [[{"audio": None, "start": None, "end": None}]]
        audio_frames = kwargs.pop("audio_frames", audio_frames)
        audio_transcript = [[]]
        audio_transcript = kwargs.pop("audio_transcript", audio_transcript)

        if self._emb_generator is None:
            # Model does not support embeddings, send the frames to the VLM process
            return {
                "chunk": chunk,
                "frames": [frames[0].clone()] if isinstance(frames[0], torch.Tensor) else frames,
                "frame_times": frame_times,
                "audio_frames": audio_frames,
                "audio_transcript": audio_transcript,
                **kwargs,
            }

        embed_start_time = time.time()
        # Model supports explicit embeddings, generate and cache the embeddings.
        nvtx_embedding_start = nvtx.start_range(
            message="Embedding Process-" + str(chunk[0]), color="blue"
        )
        embeddings = self._emb_generator.get_embeddings(frames)

        def on_embeddings_done(
            chunk,
            embeddings,
            frame_times,
            audio_frames,
            audio_transcript,
            embed_start_time,
            nvtx_embedding_start,
        ):
            logger.log(LOG_STATUS_LEVEL, f"Embeddings generated for {chunk[0]}")
            nvtx.end_range(nvtx_embedding_start)
            self._emb_helper.save_embeddings(chunk[0], embeddings[0], frame_times[0])
            return {
                "chunk": chunk,
                "embed_start_time": [embed_start_time] * len(chunk),
                "embed_end_time": [time.time()] * len(chunk),
                "audio_frames": audio_frames,
                "audio_transcript": audio_transcript,
                **kwargs,
            }

        if isinstance(embeddings, concurrent.futures.Future):
            return self._handle_future_result(
                on_embeddings_done,
                chunk,
                embeddings,
                frame_times,
                audio_frames,
                audio_transcript,
                embed_start_time,
                nvtx_embedding_start,
            )
        else:
            return on_embeddings_done(
                chunk,
                embeddings,
                frame_times,
                audio_frames,
                audio_transcript,
                embed_start_time,
                nvtx_embedding_start,
            )


class VlmProcess(ViaProcessBase):
    """VLM Process"""

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
            batch_size=args.vlm_batch_size,
            gpu_id=gpu_id,
            disabled=disabled,
            input_queue=input_queue,
            input_queue_lock=input_queue_lock,
        )
        self._vlm_model_type = args.vlm_model_type
        self._model_path = args.model_path
        self._use_trt = args.use_trt
        self._trt_engine_dir = args.trt_engine_dir
        self._args = args
        self._asset_dir = asset_dir
        self._num_gpus = args.num_gpus
        self._num_futures_threads = max(1, args.vlm_batch_size // 2)

    def _initialize(self):
        # Create an instance of EmbeddingHelper to retrieve chunk embeddings
        use_gpu_mem_for_embedding_load = True
        # Embedding sent to network; can avoid GPU mem
        # Also: torch.cuda_init() from parallel threads without as may GPUs
        # give error:
        # RuntimeError: No CUDA GPUs are available
        if self._vlm_model_type == VlmModelType.OPENAI_COMPATIBLE:
            use_gpu_mem_for_embedding_load = False
        self._emb_helper = EmbeddingHelper(
            self._asset_dir, use_gpu_mem=use_gpu_mem_for_embedding_load
        )

        # Model specific initialization
        if self._vlm_model_type == VlmModelType.VILA_15:
            from models.vila15.vila15_model import Vila15

            self._model = Vila15(
                self._model_path,
                use_trt=self._use_trt,
                trt_engine_dir=self._trt_engine_dir,
                max_batch_size=self._batch_size,
                async_output=True,
            )
            if self._model.TRTLLM_EXECUTOR_INFLIGHT_BATCHING:
                self._batch_size = 1
        elif self._vlm_model_type == VlmModelType.NVILA:
            from models.nvila.nvila_model import NVila

            self._model = NVila(
                self._model_path,
                use_trt=self._use_trt,
                trt_engine_dir=self._trt_engine_dir,
                max_batch_size=self._batch_size,
                async_output=True,
            )
            self._batch_size = 1

        elif self._vlm_model_type == VlmModelType.OPENAI_COMPATIBLE:
            from models.openai_compat.openai_compat_model import CompOpenAIModel

            self._model = CompOpenAIModel(True)
            self._batch_size = 1
        elif self._vlm_model_type is None:
            loader = CustomModuleLoader(self._model_path)
            self._model = loader.load_model()
            self._batch_size = 1
        return True

    def _deinitialize(self):
        self._model = None

    def _supports_batching(self):
        return True

    def _can_batch(self, item1, item2):
        # For VLM, batching can be performed only if number of frames used
        # for embedding generation is equal.
        return (
            (self._vlm_model_type == VlmModelType.VILA_15)
            and self._emb_helper.get_num_frames_embedding(item1["chunk"])
            == self._emb_helper.get_num_frames_embedding(item2["chunk"])
            and item1["request_params"] == item2["request_params"]
        )

    def _is_busy(self):
        return (
            self._vlm_model_type == VlmModelType.VILA_15
            or self._vlm_model_type == VlmModelType.NVILA
        ) and not self._model.can_enqueue_requests()

    def _warmup(self):
        if hasattr(self._model, "warmup"):
            self._model.warmup()

    def _process(
        self, chunk: list[ChunkInfo], request_params: list[VlmRequestParams | None], **kwargs
    ):
        """Generate VLM responses for a batch of chunks"""

        if not request_params[0] or not request_params[0].vlm_prompt:
            for chunk_ in chunk:
                logger.log(LOG_STATUS_LEVEL, f"Skipping VLM response generation for ({chunk_})")
            return

        vlm_start_time = time.time()
        nvtx_vlm_process_start = nvtx.start_range(message="VLM Process-" + str(chunk), color="blue")

        for chunk_ in chunk:
            logger.log(LOG_STATUS_LEVEL, f"Generating VLM response for ({chunk_})")

        # Model specific context handlers
        if self._vlm_model_type == VlmModelType.VILA_15:
            from models.vila15.vila15_context import Vila15Context

            ctx = Vila15Context(self._model)
        elif self._vlm_model_type == VlmModelType.NVILA:
            from models.nvila.nvila_context import NVilaContext

            ctx = NVilaContext(self._model)
        elif self._vlm_model_type == VlmModelType.OPENAI_COMPATIBLE:
            from models.common.model_context_frame_input import ModelContextFrameInput

            ctx = ModelContextFrameInput(self._model)
        elif self._vlm_model_type is None:
            ctx = CustomModelContext(self._model)

        embeds = []
        frame_times = []
        if (
            self._vlm_model_type is not None or self._model.get_embedding_generator() is not None
        ) and self._vlm_model_type != VlmModelType.NVILA:
            # Model supports explicit embeddings, fetch the embedding for each chunk
            # in the input batch
            for chunk_ in chunk:
                embed, ftime = self._emb_helper.get_embedding(chunk_)
                embeds.append(embed)
                frame_times.append(ftime)

        frames = kwargs.pop("frames", None)
        frame_times = kwargs.pop("frame_times", frame_times)
        # Set the video embeddings on the context class along with time information
        ctx.set_video_embeds(chunk, embeds, frames, frame_times)

        vlm_response_stats = ctx.ask(
            request_params[0].vlm_prompt,
            generation_config=request_params[0].vlm_generation_config,
            chunk=chunk,
        )
        if "is_live_stream" in kwargs and self._num_gpus > 1:
            time.sleep(0.1)

        def process_vlm_response(
            chunk,
            request_params,
            vlm_response_stats,
            frame_times,
            audio_frames,
            audio_transcript,
            vlm_start_time,
            nvtx_vlm_process_start,
        ):
            vlm_response, stats = vlm_response_stats
            nvtx.end_range(nvtx_vlm_process_start)
            for idx, chunk_ in enumerate(chunk):
                logger.log(
                    LOG_STATUS_LEVEL, f"VLM response generated for ({chunk_}), {vlm_response[idx]}"
                )
            return {
                "chunk": chunk,
                "request_params": request_params,
                "vlm_response": vlm_response,
                "vlm_stats": stats,
                "frame_times": frame_times,
                "audio_frames": audio_frames,
                "audio_transcript": audio_transcript,
                "vlm_start_time": [vlm_start_time] * len(chunk),
                "vlm_end_time": [time.time()] * len(chunk),
                **kwargs,
            }

        audio_frames = [[{"audio": None, "start": None, "end": None}]]
        audio_frames = kwargs.pop("audio_frames", audio_frames)
        audio_transcript = [[]]
        audio_transcript = kwargs.pop("audio_transcript", audio_transcript)

        if isinstance(vlm_response_stats, concurrent.futures.Future):
            return self._handle_future_result(
                process_vlm_response,
                chunk,
                request_params,
                vlm_response_stats,
                frame_times,
                audio_frames,
                audio_transcript,
                vlm_start_time,
                nvtx_vlm_process_start,
            )
        else:
            return process_vlm_response(
                chunk,
                request_params,
                vlm_response_stats,
                frame_times,
                audio_frames,
                audio_transcript,
                vlm_start_time,
                nvtx_vlm_process_start,
            )


class AsrProcess(ViaProcessBase):
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
        self._asr_config_file = "/tmp/via/riva_asr_grpc_conf.yaml"
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
            logger.log(LOG_STATUS_LEVEL, f"Skipping ASR response generation for ({chunk})")
            return

        asr_start_time = time.time()
        nvtx_asr_process_start = nvtx.start_range(message="ASR Process-" + str(chunk), color="blue")

        logger.log(LOG_STATUS_LEVEL, f"Generating ASR response for ({chunk})")

        audio_frames = kwargs.pop("audio_frames", None)
        audio_transcript = kwargs.pop("audio_transcript", None)

        if audio_transcript is not None and len(audio_transcript) > 0:
            return {
                "chunk": chunk,
                "request_params": request_params,
                "audio_transcript": audio_transcript,
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

            logger.log(LOG_STATUS_LEVEL, f"ASR response generated for ({chunk}), {transcript}")

            return {
                "chunk": chunk,
                "request_params": request_params,
                "audio_transcript": transcript,
                "asr_start_time": asr_start_time,
                "asr_end_time": time.time(),
                **kwargs,
            }

        if isinstance(asr_response, _MultiThreadedRendezvous):
            return self._handle_future_result(
                process_asr_response,
                chunk,
                request_params,
                asr_response,
                asr_start_time,
                nvtx_asr_process_start,
            )
        else:
            return process_asr_response(
                chunk,
                request_params,
                asr_response,
                asr_start_time,
                nvtx_asr_process_start,
            )


class VlmChunkResponse:
    chunk: ChunkInfo = None
    vlm_response: str | None = None
    audio_transcript: str | None = None
    error: str | None = None
    queue_time = 0
    processing_latency = 0
    is_live_stream_ended = False
    decode_start_time = 0
    decode_end_time = 0
    embed_start_time = 0
    embed_end_time = 0
    vlm_start_time = 0
    vlm_end_time = 0
    vlm_stats = {}
    add_doc_start_time = 0
    add_doc_end_time = 0
    frame_times: list[float] = []


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
        on_chunk_reponse: Callable[[VlmChunkResponse], None] = None
        end_of_stream = False
        total_chunks_at_eos = 0
        all_chunks_processed = False
        gpu_id = -1

    def __init__(self, asset_dir, args) -> None:
        """Initialize the VLM pipeline"""
        logger.info("Initializing VLM pipeline")

        self._start_time = time.time()
        use_gpu_mem_for_embedding_load = True
        if args.vlm_model_type == VlmModelType.OPENAI_COMPATIBLE:
            # Embedding sent to network; can avoid GPU mem
            # Also: torch.cuda_init() from parallel threads without as may GPUs
            # give error:
            # RuntimeError: No CUDA GPUs are available
            use_gpu_mem_for_embedding_load = False
        self._emb_helper = EmbeddingHelper(asset_dir, use_gpu_mem=use_gpu_mem_for_embedding_load)
        self._args = args

        mp_ctx = multiprocessing.get_context("spawn")

        self._have_emb_gen = False
        if args.vlm_model_type != VlmModelType.NVILA:
            self._have_emb_gen = True

        self._dec_q = mp_ctx.Queue()
        self._dec_q_lock = mp_ctx.Lock()
        have_peer_access = check_peer_access()
        logger.info(f"Have peer access: {have_peer_access}")
        if args.vlm_model_type == VlmModelType.NVILA and not have_peer_access:
            self._vlm_q = None
            self._vlm_q_lock = None
        else:
            self._vlm_q = mp_ctx.Queue(
                maxsize=(0 if self._have_emb_gen else 3 * self._args.num_gpus)
            )
            self._vlm_q_lock = mp_ctx.Lock()

        self._asr_q = mp_ctx.Queue()
        self._asr_q_lock = mp_ctx.Lock()
        self._asr_procs = []

        self._chunk_counter = 0
        self._chunk_callback_map: dict[int, Callable[[VlmChunkResponse], None]] = {}
        self._live_stream_id_map: dict[str, VlmPipeline._LiveStreamInfo] = {}

        self._enqueue_lock = Lock()

        if args.vlm_model_type == VlmModelType.OPENAI_COMPATIBLE:
            from models.openai_compat.openai_compat_model import CompOpenAIModel

            CompOpenAIModel()

        # Model path is required for locally executed models like VILA
        if args.vlm_model_type != VlmModelType.OPENAI_COMPATIBLE and not args.model_path:
            raise Exception("model-path not provided")

        if args.model_path and args.model_path.startswith("ngc:"):
            # NGC model path provided, download the model if not found in cache

            # Workaround for some asyncio issue
            def download_thread_func(ngc_model_path, download_prefix, model_path_):
                try:
                    model_path = download_model(ngc_model_path, download_prefix)
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

        if FORCE_TRT and (args.vlm_model_type == VlmModelType.VILA_15):
            # TRT inference forced for locally executed models

            # Infer the TRT engine directory if not specified
            trt_engine_dir = args.trt_engine_dir
            if not trt_engine_dir:
                trt_engine_dir = os.path.join(
                    args.model_path, f"trt-engines/{args.trt_llm_mode}/0-gpu"
                )
            config_file = os.path.join(trt_engine_dir, "config.json")
            rank0_file = os.path.join(trt_engine_dir, "rank0.engine")
            visual_engines_dir = os.path.join(trt_engine_dir, "visual_engines")

            # Check if engine already exists
            build_engine = False
            if os.environ.get("VILA_FORCE_ENGINE_BUILD", "false") == "true" or (
                not os.path.isfile(config_file)
                or not os.path.isfile(rank0_file)
                or not os.path.isfile(os.path.join(visual_engines_dir, "visual_encoder.engine"))
            ):
                logger.info("TRT-LLM Engine not found. Generating engines ...")
                build_engine = True

            if build_engine:
                vila_ngc_engine = os.environ.get("VILA_ENGINE_NGC_RESOURCE", "")
                if vila_ngc_engine:
                    with tempfile.TemporaryDirectory() as temp_dir:
                        # Download the engine from NGC if user has provided NGC path
                        def download_thread_func(ngc_model_path, download_prefix, model_path_):
                            try:
                                model_path = download_model(ngc_model_path, download_prefix)
                            except Exception as ex:
                                model_path_[1] = ex
                                return
                            model_path_[0] = model_path

                        model_path_ = ["", ""]
                        download_thread = Thread(
                            target=download_thread_func,
                            args=(vila_ngc_engine, temp_dir, model_path_),
                        )
                        download_thread.start()
                        download_thread.join()
                        if model_path_[1]:
                            raise model_path_[1] from None

                        # Move engine files from downloaded path to trt_engine_dir
                        downloaded_path = model_path_[0]

                        # Create trt_engine_dir if it doesn't exist
                        os.makedirs(trt_engine_dir, exist_ok=True)

                        # Move config.json
                        src_config = os.path.join(downloaded_path, "config.json")
                        if os.path.exists(src_config):
                            shutil.move(src_config, config_file)
                            logger.debug(f"Moved config.json from {src_config} to {config_file}")

                        # Move rank0.engine
                        src_rank0 = os.path.join(downloaded_path, "rank0.engine")
                        if os.path.exists(src_rank0):
                            shutil.move(src_rank0, rank0_file)
                            logger.debug(f"Copied rank0.engine from {src_rank0} to {rank0_file}")

                        # Move visual_engines directory
                        src_visual = os.path.join(downloaded_path, "visual_engines")
                        if os.path.exists(src_visual):
                            if os.path.exists(visual_engines_dir):
                                shutil.rmtree(visual_engines_dir)
                            shutil.move(src_visual, visual_engines_dir)
                            logger.debug(
                                f"Copied visual_engines from {src_visual} to {visual_engines_dir}"
                            )

                        logger.info(f"Using prebuilt TRT-LLM engine from NGC: {vila_ngc_engine}")

                        build_engine = False

            if not build_engine:
                # Check if the engine can support user configured batch size
                with open(os.path.join(trt_engine_dir, "config.json")) as f:
                    config = json.load(f)
                    if config["build_config"]["max_batch_size"] < args.vlm_batch_size:
                        logger.info(
                            f"Existing TRT-LLM engine at {trt_engine_dir} has lower"
                            f" max-batch-size({config['build_config']['max_batch_size']}) than"
                            f" requested ({args.vlm_batch_size}). Re-generating engines ..."
                        )
                        build_engine = True
                    if (
                        os.environ.get("VILA_LORA_PATH", "")
                        and not config["build_config"]["plugin_config"]["lora_plugin"]
                    ):
                        logger.info(
                            f"Existing TRT-LLM engine at {trt_engine_dir} built"
                            f" without lora support however lora has been configured."
                            f" Re-generating engines ..."
                        )
                        build_engine = True

            if build_engine:
                # Still need to build engine. Run the build_engine script automatically
                base_path = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
                model_dir = "vila15"
                result = subprocess.run(
                    [
                        "bash",
                        os.path.join(base_path, f"models/{model_dir}/trt_helper/build_engine.sh"),
                        args.model_path,
                        str(args.vlm_batch_size),
                        args.trt_llm_mode.value,
                        trt_engine_dir,
                    ]
                )
                if result.returncode:
                    raise Exception("Failed to generate TRT-LLM engine")

                logger.info("Generated TRT-LLM engines")

            args.use_trt = True
            args.trt_engine_dir = trt_engine_dir

        if args.use_trt and not args.trt_engine_dir:
            raise Exception("TRT mode selected but TRT engine directory not set")

        if FORCE_TRT and (args.vlm_model_type == VlmModelType.NVILA):
            args.use_trt = True

        self._processed_chunk_queue = mp_ctx.Queue()
        self._processed_chunk_queue_watcher_stop_event = Event()
        self._processed_chunk_queue_watcher_thread = None

        # Create the ASR processes
        self._num_asr_procs = 1
        if self._args.enable_audio:
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

        self._num_vlm_procs = args.num_gpus
        if args.vlm_model_type == VlmModelType.OPENAI_COMPATIBLE:
            self._num_vlm_procs = args.num_vlm_procs
        logger.info(f"num_vlm_procs set to {self._num_vlm_procs}")

        # Create the VLM processes, one on each GPU
        self._vlm_procs = [
            VlmProcess(
                args,
                asset_dir,
                i,
                args.disable_vlm,
                self._vlm_q,
                self._vlm_q_lock,
            )
            for i in range(self._num_vlm_procs)
        ]
        for idx, vlm_proc in enumerate(self._vlm_procs):
            if self._args.enable_audio:
                vlm_proc.set_output_queue(self._asr_procs[idx % self._num_asr_procs].input_queue)
            else:
                vlm_proc.set_output_queue(self._processed_chunk_queue)
            vlm_proc.set_final_output_queue(self._processed_chunk_queue)
            vlm_proc.start()

        self._emb_gen_procs = []
        # Create the embedding generation processes, one on each GPU
        if self._have_emb_gen:
            self._emb_gen_procs = [
                EmbeddingProcess(args, asset_dir, i, args.disable_embeddings)
                for i in range(self._num_vlm_procs)
            ]
            for idx, emb_gen_proc in enumerate(self._emb_gen_procs):
                emb_gen_proc.set_output_queue(
                    self._vlm_procs[idx % self._num_vlm_procs].input_queue
                )
                emb_gen_proc.set_final_output_queue(self._processed_chunk_queue)
                emb_gen_proc.start()

        # Create the chunk decoding processes, one on each GPU
        self._decoder_procs = [
            DecoderProcess(args, i, args.disable_decoding, self._dec_q, self._dec_q_lock)
            for i in range(args.num_gpus)
        ]
        for idx, dec_proc in enumerate(self._decoder_procs):
            if self._have_emb_gen:
                dec_proc.set_output_queue(
                    self._emb_gen_procs[idx % self._num_vlm_procs].input_queue
                )
            else:
                dec_proc.set_output_queue(self._vlm_procs[idx % self._num_vlm_procs].input_queue)
            dec_proc.set_final_output_queue(self._processed_chunk_queue)
            dec_proc.start()

        # Wait for all processes to complete initialization
        for idx, proc in enumerate(self._decoder_procs):
            if not proc.wait_for_initialization():
                self.stop()
                raise Exception(f"Failed to load Decoder on GPU {idx}")
        for idx, proc in enumerate(self._emb_gen_procs):
            if not proc.wait_for_initialization():
                self.stop()
                raise Exception(f"Failed to load Embedding Generator on GPU {idx}")
        for idx, proc in enumerate(self._vlm_procs):
            if not proc.wait_for_initialization():
                self.stop()
                raise Exception(f"Failed to load VLM on GPU {idx}")
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

            if item.get("live_stream_ended", False):
                lsinfo = self._live_stream_id_map[item["live_stream_id"]]
                lsinfo.end_of_stream = True
                lsinfo.total_chunks_at_eos = item["total_chunks"]

                if lsinfo.num_chunks_processed >= lsinfo.total_chunks_at_eos:
                    response = VlmChunkResponse()
                    response.is_live_stream_ended = True
                    lsinfo.on_chunk_reponse(response)
                    lsinfo.all_chunks_processed = True
                continue

            response = VlmChunkResponse()
            response.error = item.get("error", None)
            response.chunk = item["chunk"]
            if not response.error:
                response.vlm_response = item.get("vlm_response", None)
                response.audio_transcript = item.get("audio_transcript", None)
                response.queue_time = max(
                    item.get("decode_start_time", item.get("vlm_start_time", 0))
                    - item.get("enqueue_time", 0),
                    0,
                )
                response.processing_latency = max(
                    item.get("vlm_end_time", 0)
                    - item.get("decode_start_time", item.get("vlm_start_time", 0)),
                    0,
                )
                response.decode_start_time = item.get("decode_start_time")
                response.decode_end_time = item.get("decode_end_time")
                response.embed_start_time = item.get("embed_start_time")
                response.embed_end_time = item.get("embed_end_time")
                response.vlm_start_time = item.get("vlm_start_time")
                response.vlm_end_time = item.get("vlm_end_time")
                response.vlm_stats = item.get("vlm_stats", {"input_tokens": 0, "output_tokens": 0})
                response.frame_times = item.get("frame_times", [])

            if item.get("is_live_stream", False):
                if response.vlm_end_time and response.vlm_end_time - (
                    response.embed_end_time or response.vlm_start_time
                ) > (response.chunk.end_pts - response.chunk.start_pts):
                    logger.warning(
                        "Detected high load on the system. This may result in higher response"
                        " times. Try reducing number of streams or increasing the chunk size"
                    )
                if response.chunk.streamId in self._live_stream_id_map:
                    lsinfo = self._live_stream_id_map[response.chunk.streamId]
                    lsinfo.on_chunk_reponse(response)
                    lsinfo.num_chunks_processed += 1
                    if (
                        lsinfo.end_of_stream
                        and lsinfo.num_chunks_processed >= lsinfo.total_chunks_at_eos
                    ):
                        response = VlmChunkResponse()
                        response.is_live_stream_ended = True
                        lsinfo.on_chunk_reponse(response)
                        lsinfo.all_chunks_processed = True
                try:
                    # Remove embedding and video chunks for live streams.
                    # Currently, they are not being retained
                    self._emb_helper.remove_chunk_data(response.chunk)
                except Exception:
                    pass
                continue
            callback = self._chunk_callback_map.pop(item["chunk_id"], None)
            if callback:
                callback(response)

    def abort_chunks(self, stream_id: str):
        for proc in self._decoder_procs + self._emb_gen_procs + self._vlm_procs + self._asr_procs:
            proc.send_command("drop-chunks", stream_id=stream_id)

    def abort_chunks_done(self, stream_id: str):
        for proc in self._decoder_procs + self._emb_gen_procs + self._vlm_procs + self._asr_procs:
            proc.send_command("stop-drop-chunks", stream_id=stream_id)

    def stop(self, force=False):
        """Stop the VLM Pipeline"""
        logger.info("Stopping VLM pipeline")
        if force:
            # Force terminate the processes started by the pipeline
            for proc in self._decoder_procs:
                proc.terminate()
            for proc in self._emb_gen_procs:
                proc.terminate()
            for proc in self._vlm_procs:
                proc.terminate()
            for proc in self._asr_procs:
                proc.terminate()
        else:
            # Wait for the processes started by VLM pipeline to stop gracefully
            for proc in self._decoder_procs:
                proc.stop()
            for proc in self._emb_gen_procs:
                proc.stop()
            for proc in self._vlm_procs:
                proc.stop()
            for proc in self._asr_procs:
                proc.stop()

        # Stop the processed chunk response watcher thread
        if self._processed_chunk_queue_watcher_thread:
            self._processed_chunk_queue_watcher_stop_event.set()
            self._processed_chunk_queue_watcher_thread.join()

        logger.info("Stopped VLM pipeline")

    def get_models_info(self):
        """Get loaded model information"""
        api_type = ""
        id = ""
        owned_by = ""
        if self._args.vlm_model_type == VlmModelType.VILA_15:
            from models.vila15.vila15_model import Vila15

            id, api_type, owned_by = Vila15.get_model_info()
        elif self._args.vlm_model_type == VlmModelType.OPENAI_COMPATIBLE:
            from models.openai_compat.openai_compat_model import CompOpenAIModel

            id, api_type, owned_by = CompOpenAIModel.get_model_info()
        elif self._args.vlm_model_type == VlmModelType.NVILA:
            from models.nvila.nvila_model import NVila

            id, api_type, owned_by = NVila.get_model_info()
        else:
            id = os.path.basename(os.path.abspath(self._args.model_path))
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
        on_chunk_reponse: Callable[[VlmChunkResponse], None],
        request_params: Optional[VlmRequestParams] = None,
        num_frames_per_chunk=0,
        vlm_input_width=0,
        vlm_input_height=0,
        enable_audio=False,
        request_id="",
    ):
        with self._enqueue_lock:
            curr_chunk_counter = self._chunk_counter
            self._chunk_counter += 1
            self._chunk_callback_map[curr_chunk_counter] = on_chunk_reponse
        if (
            self._emb_helper.have_embedding(chunk)
            and (not vlm_input_width)
            and (not vlm_input_height)
            and (not enable_audio)
        ):
            self._vlm_procs[curr_chunk_counter % self._num_vlm_procs].enqueue_chunk(
                chunk,
                request_params=request_params,
                chunk_id=curr_chunk_counter,
                enqueue_time=time.time(),
            )
        else:
            self._decoder_procs[curr_chunk_counter % self._args.num_gpus].enqueue_chunk(
                chunk,
                request_params=request_params,
                chunk_id=curr_chunk_counter,
                enqueue_time=time.time(),
                num_frames_per_chunk=num_frames_per_chunk,
                vlm_input_width=vlm_input_width,
                vlm_input_height=vlm_input_height,
                enable_audio=enable_audio,
                request_id=request_id,
            )

    def add_live_stream(
        self,
        live_stream_id: str,
        live_stream_url: str,
        chunk_size: int,
        on_chunk_reponse: Callable[[VlmChunkResponse], None],
        request_params: Optional[VlmRequestParams] = None,
        username: str = None,
        password: str = None,
        num_frames_per_chunk=0,
        vlm_input_width=0,
        vlm_input_height=0,
        enable_audio=False,
        enable_cv_pipeline=False,
        cv_pipeline_text_prompt="",
    ):
        gpu_dec_use_cnt = {i: 0 for i in range(self._args.num_gpus)}
        for info in self._live_stream_id_map.values():
            gpu_dec_use_cnt[info.gpu_id] += 1
        least_used_gpu = min(gpu_dec_use_cnt, key=gpu_dec_use_cnt.get)

        self._live_stream_id_map[live_stream_id] = self._LiveStreamInfo()
        self._live_stream_id_map[live_stream_id].gpu_id = least_used_gpu
        self._live_stream_id_map[live_stream_id].on_chunk_reponse = on_chunk_reponse
        self._decoder_procs[least_used_gpu].send_command(
            "start-live-stream",
            live_stream_id=live_stream_id,
            live_stream_url=live_stream_url,
            username=username,
            password=password,
            request_params=request_params,
            chunk_size=chunk_size,
            num_frames_per_chunk=num_frames_per_chunk,
            vlm_input_width=vlm_input_width,
            vlm_input_height=vlm_input_height,
            enable_audio=enable_audio,
            enable_cv_pipeline=enable_cv_pipeline,
            cv_pipeline_text_prompt=cv_pipeline_text_prompt,
        )

    def remove_live_stream(self, live_stream_id: str):
        if live_stream_id not in self._live_stream_id_map:
            return
        lsinfo = self._live_stream_id_map[live_stream_id]
        self._decoder_procs[lsinfo.gpu_id].send_command(
            "stop-live-stream", live_stream_id=live_stream_id
        )

        for proc in self._emb_gen_procs:
            proc.send_command("drop-chunks", stream_id=live_stream_id)
        for proc in self._vlm_procs:
            proc.send_command("drop-chunks", stream_id=live_stream_id)
        for proc in self._asr_procs:
            proc.send_command("drop-chunks", stream_id=live_stream_id)

        while not lsinfo.all_chunks_processed:
            time.sleep(0.1)

        try:
            self._live_stream_id_map.pop(live_stream_id)
        except KeyError as e:
            # can happen if multiple stream delete requests are happening in parallel
            logger.info(f"{e}: live stream already removed from map;")

        for proc in self._emb_gen_procs:
            proc.send_command("stop-drop-chunks", stream_id=live_stream_id)
        for proc in self._vlm_procs:
            proc.send_command("stop-drop-chunks", stream_id=live_stream_id)
        for proc in self._asr_procs:
            proc.send_command("stop-drop-chunks", stream_id=live_stream_id)

    @staticmethod
    def populate_argument_parser(parser: ArgumentParser):
        """Add VIA Pipeline arguments to the argument parser"""
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
            default=None,
            help="Vision Language Model to use",
        )
        parser.add_argument(
            "--trt-llm-mode",
            type=TrtLlmMode,
            choices=list(TrtLlmMode),
            default=TrtLlmMode.INT4_AWQ,
            help="Vision Language Model to use",
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
            "--disable-embeddings",
            action="store_true",
            default=False,
            help="Disable Video Embeddings Generation",
        )
        parser.add_argument(
            "--model-path",
            type=str,
            required=False,
            help="Location of the model",
        )
        parser.add_argument(
            "--trt-build-int8",
            action="store_true",
            help="Build TRTLLM engine in int8 mode",
        )
        parser.add_argument(
            "--use-trt",
            action="store_true",
            help="Use TensorRT",
        )
        parser.add_argument(
            "--trt-engine-dir",
            type=str,
            help="Path to TRT engine directory",
        )
        parser.add_argument(
            "--num-frames-per-chunk",
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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="VLM Pipeline", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    VlmPipeline.populate_argument_parser(parser)
    args = parser.parse_args()

    try:
        pipeline = VlmPipeline("/tmp/via/assets", args)
    except Exception as ex:
        logger.error("Could not load VLM Pipeline - " + str(ex))
        sys.exit(-1)

    pipeline.stop()
