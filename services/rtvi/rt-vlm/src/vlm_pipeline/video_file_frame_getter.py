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
"""Video File Frame Getter

This module supports getting frames from a video file either as raw frame tensors or
JPEG encoded images. Supports decoding of a part of file using start/end timestamps,
picking N frames from the segment as well as pre-processing the decoded frames
as required by the VLM model.
"""

import ctypes
import json
import os
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Condition, Lock
from typing import Callable, Optional

import cupy as cp
import gi
import grpc

try:
    import gst_video_sei_meta

    print("gst_video_sei_meta library found")
    HAVE_SEI_META_LIB = True
except ImportError:
    gst_video_sei_meta = None
    HAVE_SEI_META_LIB = False
    print("gst_video_sei_meta library not found")
import gc
import multiprocessing as mp

import numpy as np
import pyds
import torch
import torch.nn.functional as F
import yaml
from torchvision.transforms import v2

from common.chunk_info import ChunkInfo
from common.logger import TimeMeasure, logger
from utils.media_file_info import MediaFileInfo

gi.require_version("Gst", "1.0")

from gi.repository import GLib, Gst  # noqa: E402

Gst.init(None)

# Long safety-net timeout for the cached H264/H265 decoder NULL transition
# in `_set_pipeline_null_and_clear_refs`. Empirical decoder teardown under
# 8x concurrent file_burst at B200 stress is ~10 s/decoder; this 120 s
# bound is ~12x headroom (effectively unbounded for healthy paths) while
# still letting a worker thread recover after 2 minutes if the decoder
# wedges. The previous 5 s bound was too tight: under load the Python
# ref dropped while the underlying CUDA decoder context was still alive,
# leaking VRAM permanently. Tune via env if needed in future.
DECODER_TEARDOWN_TIMEOUT_NS = 120 * Gst.SECOND

# Handle FORCE_SW_AV1_DECODER environment variable
# When set to true/True/TRUE/1, forces software AV1 decoder instead of hardware decoder
# This is useful for platforms where hardware AV1 decoding is not supported
force_sw_av1 = os.environ.get("FORCE_SW_AV1_DECODER", "false").lower() in ("true", "1", "yes")
if force_sw_av1:
    av1dec = Gst.ElementFactory.find("av1dec")
    if av1dec:
        current_rank = av1dec.get_rank()
        # Update av1dec rank above nvv4l2decoder to prefer software decoder
        new_rank = 276
        av1dec.set_rank(new_rank)
        logger.info(
            "FORCE_SW_AV1_DECODER enabled: Updated rank of %s from %d to %d",
            av1dec.get_name(),
            current_rank,
            new_rank,
        )
    else:
        logger.warning("FORCE_SW_AV1_DECODER is set but av1dec element not found")

UNTRACKED_OBJECT_ID = 0xFFFFFFFFFFFFFFFF


def _env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value == "":
        return default
    return raw_value.strip().lower() in ("1", "true", "yes", "on")


def get_timestamp_str(ts):
    """Get RFC3339 string timestamp"""
    return (
        datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        + f".{(int(ts * 1000) % 1000):03d}Z"
    )


@dataclass
class FrameSelectorData:
    """Data structure for storing frame selector cached data.

    Attributes:
        cached_pts: List of presentation timestamps in seconds for selected frames
        cached_frames: List of pre-processed frame tensors corresponding to cached_pts
    """

    cached_pts: list[float] = field(default_factory=list)
    cached_frames: list = field(default_factory=list)
    decode_start_time: float = field(default_factory=time.time)


class ToCHW:
    """
    Converts tensor from HWC (interleaved) to CHW (planar)
    """

    def __init__(self):
        pass

    def __call__(self, clip):
        return clip.permute(2, 0, 1)

    def __repr__(self) -> str:
        return self.__class__.__name__


class Rescale:
    """
    Convert tensor data type from uint8 to float, divide value by 255.0
    """

    def __init__(self, factor):
        self._factor = factor
        pass

    def __call__(self, clip):
        return clip.float().mul(self._factor)

    def __repr__(self) -> str:
        return self.__class__.__name__


class BaseFrameSelector:
    """Base Frame Selector

    Base class for implementing a frame selector."""

    def __init__(self):
        self._chunk = None

    def set_chunk(self, chunk: ChunkInfo):
        """Set Chunk to select frames from"""
        self._chunk = chunk

    def choose_frame(self, buffer, pts: int):
        """Choose a frame for processing.

        Implementations should return a boolean indicating if the frame should
        be chosen for processing.

        Args:
            buffer: GstBuffer
            pts: Frame timestamp in nanoseconds.

        Returns:
            bool: Boolean indicating if the frame should be chosen for processing.
        """
        return False


class DefaultFrameSelector:
    """Default Frame Selector.

    Selects N equally spaced frames from a chunk.
    """

    ALL_FRAMES = -1

    def __init__(self, num_frames_per_second_or_fixed_frames=8, use_fps_for_chunking=False):
        """Default initializer.

        Args:
            num_frames (int, optional): Number of frames to select from a chunk. Defaults to 8.
        """
        self._num_frames_per_second_or_fixed_frames = num_frames_per_second_or_fixed_frames
        self._selected_pts_array = deque()
        self._use_fps_for_chunking = use_fps_for_chunking
        self._select_all_frames = False
        self._selection_start_pts = 0
        self._selection_end_pts = 0

    @property
    def selects_all_frames(self):
        return self._select_all_frames

    def set_chunk(self, chunk: ChunkInfo):
        self._chunk = chunk
        self._selected_pts_array = deque()
        self._select_all_frames = False
        start_pts = chunk.start_pts
        end_pts = chunk.end_pts

        if start_pts == -1 or end_pts == -1:
            # If start or end PTS is not set (=-1), set it to 0 and file duration
            # to decode the entire file
            start_pts = 0
            end_pts = MediaFileInfo.get_info(chunk.file).video_duration_nsec

        # Adjust for the PTS offset (in case of split files)
        start_pts -= chunk.pts_offset_ns
        end_pts -= chunk.pts_offset_ns

        if self._chunk.end_pts < 0:
            self._chunk.end_pts = end_pts

        self._selection_start_pts = start_pts
        self._selection_end_pts = end_pts

        if (
            not self._use_fps_for_chunking
            and int(self._num_frames_per_second_or_fixed_frames) == self.ALL_FRAMES
        ):
            self._select_all_frames = True
            self._num_frames = self.ALL_FRAMES
            logger.debug(
                "Selecting all frames for %s, start_pts=%d, end_pts=%d",
                chunk,
                start_pts,
                end_pts,
            )
            return

        # Calculate PTS for N equally spaced frames
        if self._use_fps_for_chunking:
            self._num_frames = int(
                self._num_frames_per_second_or_fixed_frames
                * (end_pts - start_pts)
                / 1_000_000_000.0
            )
        else:
            self._num_frames = int(self._num_frames_per_second_or_fixed_frames)

        if self._num_frames < 1:
            logger.warning(f"num_frames={self._num_frames} is less than 1, setting to 1")
            self._num_frames = 1

        logger.debug(
            f"num_frames={self._num_frames}, "
            f"num_frames_per_second_or_fixed_frames={self._num_frames_per_second_or_fixed_frames}, "
            f"use_fps_for_chunking={self._use_fps_for_chunking}, "
            f"end_pts={end_pts}, start_pts={start_pts}"
        )
        pts_diff = (end_pts - start_pts) / self._num_frames
        for i in range(self._num_frames):
            self._selected_pts_array.append(start_pts + i * pts_diff)
        logger.debug("Selected PTS = %s for %s", self._selected_pts_array, chunk)
        logger.debug(
            "chunk.end_pts=%d, len(self._selected_pts_array)=%d",
            end_pts,
            len(self._selected_pts_array),
        )

    def choose_frame(self, buffer, pts):
        if self._select_all_frames:
            return self._selection_start_pts <= pts <= self._selection_end_pts

        # Choose the frame if it's PTS is more than the next sampled PTS in the
        # list.
        if (
            len(self._selected_pts_array)
            and pts >= self._selected_pts_array[0]
            and pts <= self._chunk.end_pts
        ):
            while len(self._selected_pts_array) and pts >= self._selected_pts_array[0]:
                self._selected_pts_array.popleft()  # O(1) instead of O(n) with pop(0)
            return True
        if pts >= self._chunk.end_pts:
            self._selected_pts_array.clear()
        return False


class AudioChunkIterator:
    """Iterator that yields audio chunks from queue.

    Provides iteration over audio frames with thread-safe access to the underlying cache.
    Implements context manager protocol for proper resource cleanup.
    """

    def __init__(
        self,
        audio_frames_queue: mp.Queue,
        audio_stop: mp.Event,
    ) -> None:
        """Initialize the iterator.

        Args:
            audio_frames_queue: Queue of audio frame dictionaries
            audio_stop: Event to signal when to stop iteration
        """
        self._audio_frames_queue = audio_frames_queue
        self._audio_stop = audio_stop

    def close(self) -> None:
        """Clean up resources."""
        pass

    def __enter__(self):
        return self

    def __exit__(self, type_, value, traceback) -> None:
        self.close()

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        """Get next audio chunk as bytes.

        Returns:
            Audio data as bytes

        Raises:
            StopIteration: When audio_stop is set and no more frames
        """
        if not self._audio_frames_queue.empty():
            audio_frame = self._audio_frames_queue.get()
            if audio_frame is not None and audio_frame["audio"] is not None:
                return audio_frame["audio"].tobytes()

        if self._audio_stop.is_set():
            logger.debug("Stopping audio chunk iterator")
            raise StopIteration

        # No frames available, wait briefly and retry
        time.sleep(0.03)
        return self.__next__()


def streaming_audio_asr(
    asr_input_queue,
    asr_output_queue,
    asr_config_file,
    audio_stop,
    audio_error,
    asr_process_finished,
):
    """Send audio frames and receive text from ASR"""
    import riva.client  # noqa: PLC0415

    logger.info("Starting audio streaming process")

    # Load ASR configuration from file and create ASR service
    try:
        with open(asr_config_file, mode="r", encoding="utf8") as c:
            config_docs = yaml.safe_load_all(c)
            for doc in config_docs:
                if doc["name"] == "riva_server":
                    server_config = doc["detail"]
                    server_uri = server_config["server_uri"]
                if doc["name"] == "riva_model":
                    model_name = doc["detail"]["model_name"]
                if doc["name"] == "riva_asr_stream":
                    asr_config = doc["detail"]
    except Exception as e:
        raise ValueError(f"{asr_config_file} is not a valid YAML file") from e

    if asr_config is None or server_uri is None:
        raise Exception("RIVA ASR configuration is not valid.")

    ssl_cert = server_config.get("ssl_cert", None)
    use_ssl = server_config.get("use_ssl", False)
    riva_nim_server = server_config.get("is_nim", False)
    metadata_args = []
    if use_ssl:
        metadata = server_config.get("metadata", None)
        if metadata is not None:
            for k, v in metadata.items():
                metadata_args.append([k, v])

    # Create ASR service channel
    auth = riva.client.Auth(
        use_ssl=use_ssl, ssl_cert=ssl_cert, uri=server_uri, metadata_args=metadata_args
    )
    asr_service = riva.client.ASRService(auth)

    language_code = asr_config.get("language_code", "en-US")
    enable_automatic_punctuation = asr_config.get("enable_automatic_punctuation", True)
    profanity_filter = asr_config.get("profanity_filter", True)

    if riva_nim_server:
        # Do not pass model name for NIM
        riva_asr_config = riva.client.RecognitionConfig(
            encoding=riva.client.AudioEncoding.LINEAR_PCM,
            sample_rate_hertz=16000,
            language_code=language_code,
            max_alternatives=1,
            enable_automatic_punctuation=enable_automatic_punctuation,
            profanity_filter=profanity_filter,
            verbatim_transcripts=False,
        )
    else:
        riva_asr_config = riva.client.RecognitionConfig(
            encoding=riva.client.AudioEncoding.LINEAR_PCM,
            sample_rate_hertz=16000,
            language_code=language_code,
            max_alternatives=1,
            enable_automatic_punctuation=enable_automatic_punctuation,
            model=model_name,
            profanity_filter=profanity_filter,
            verbatim_transcripts=False,
        )

    streaming_config = riva.client.StreamingRecognitionConfig(
        config=riva_asr_config, interim_results=False
    )

    audio_chunk_iterator = AudioChunkIterator(asr_input_queue, audio_stop)

    try:
        response_generator = asr_service.streaming_response_generator(
            audio_chunk_iterator, streaming_config
        )

        for response in response_generator:
            try:
                start_time = None
                end_time = None
                transcript = ""
                for result in response.results:
                    transcript += result.alternatives[0].transcript
                    for word in result.alternatives[0].words:
                        if start_time is None or start_time > word.start_time:
                            start_time = word.start_time
                        if end_time is None or end_time < word.end_time:
                            end_time = word.end_time

                asr_output_queue.put(
                    {"transcript": transcript, "start": start_time, "end": end_time}
                )

            except AttributeError as e:
                logger.error(f"Invalid response format from ASR service: {e}")
                audio_error.set()
            except Exception as e:
                logger.error(f"Error processing ASR response: {e}")
                audio_error.set()

    except grpc.RpcError as e:
        logger.error(f"gRPC error during ASR streaming: {e}")
        audio_error.set()
    except Exception as e:
        logger.error(f"Unexpected error during ASR streaming: {e}")
        audio_error.set()
    finally:
        audio_chunk_iterator.close()

    logger.info("Exiting ASR streaming process")


def _should_issue_initial_seek(
    *, is_image: bool, start_pts: int, pipeline_has_streamed: bool
) -> bool:
    """Decide whether ``get_frames`` should issue the upfront ``seek_simple``.

    - Images never need a seek.
    - For non-zero start_pts we always seek to position the pipeline.
    - For start_pts == 0 we can skip only when the pipeline has not yet
      run its main loop. A pipeline that has streamed before may be parked
      at EOS or a later segment from the prior decode and must be rewound,
      even when the chunk starts at 0. Skipping the no-op seek-to-0 on a
      fresh pipeline avoids the PAUSED-state race that triggers the
      deferred-seek fallback path.
    """
    if is_image:
        return False
    if start_pts > 0:
        return True
    return pipeline_has_streamed


def _can_play_through_after_seek_failure(
    *, seek_position: int, pipeline_has_streamed: bool, current_position: Optional[int]
) -> bool:
    if seek_position == 0 and pipeline_has_streamed:
        return False
    if current_position is not None and current_position > seek_position:
        return False
    return True


class VideoFileFrameGetter:
    """Get frames from a video file as a list of tensors."""

    def __init__(
        self,
        frame_selector: BaseFrameSelector,
        frame_width=0,
        frame_height=0,
        gpu_id=0,
        do_preprocess=False,
        image_mean=[],
        rescale_factor=0,
        image_std=0,
        crop_height=0,
        crop_width=0,
        shortest_edge: int | None = None,
        enable_jpeg_output=False,
        image_aspect_ratio="",
        data_type_int8=False,
        audio_support=False,
        cv_pipeline_configs={},
    ) -> None:
        self._selected_pts_array = deque()
        self._last_gst_buffer = None
        self._loop = None
        self._bus = None
        self._frame_selector = frame_selector
        self._chunk = None
        self._gpu_id = gpu_id
        self._sei_base_time = None
        self._frame_width = self._frame_width_orig = frame_width
        self._frame_height = self._frame_height_orig = frame_height
        self._uridecodebin = None
        self._image_mean = image_mean
        self._rescale_factor = rescale_factor
        self._image_std = image_std
        self._crop_height = crop_height
        self._crop_width = crop_width
        self._shortest_edge = shortest_edge
        self._do_preprocess = do_preprocess
        self._image_aspect_ratio = image_aspect_ratio
        self._enable_jpeg_output = enable_jpeg_output
        self._data_type_int8 = data_type_int8
        self._audio_support = audio_support
        self._enable_audio = False
        self._pipeline = None
        self._last_stream_id = ""
        self._is_live = False
        self._live_stream_frame_selectors: dict[BaseFrameSelector, FrameSelectorData] = {}
        self._live_stream_frame_selectors_lock = Lock()
        self._file_frame_cache_lock = Lock()
        self._cached_frames = None
        self._cached_frames_pts = None
        self._audio_start_cv = Condition()
        self._audio_end_cv = Condition()
        self._audio_present_cv = Condition()
        self._live_stream_audio_transcripts_lock = Lock()
        self._live_stream_next_chunk_start_pts = 0
        self._audio_current_pts = 0
        self._live_stream_next_chunk_idx = 0
        self._live_stream_chunk_duration = 0
        self._live_stream_chunk_overlap_duration = 0
        self._live_stream_ntp_epoch = 0
        self._live_stream_ntp_pts = 0
        self._live_stream_request_id = 0
        self._last_video_codec = None
        self._live_stream_chunk_decoded_callback: Callable[
            [
                ChunkInfo,
                torch.Tensor | list[np.ndarray],  # frames
                list[float],  # frame_times
                list[dict],  # transcripts
                Optional[str],  # error_msg
                float,  # decode_start_time
                float,  # decode_end_time
                list[dict],  # audio_frames (for VLM-native audio; empty list when using ASR)
            ],
            None,
        ] = None
        self._on_stream_error_callback = None
        self._gst_signal_handler_ids: list[tuple[object, int]] = []
        self._gst_pad_probe_ids: list[tuple[object, int]] = []
        self._bus_signal_watch_added = False
        self._first_frame_width = 0
        self._first_frame_height = 0
        self._err_msg = None
        self._err_msg_lock = threading.Lock()
        # Dedicated CUDA stream for frame tensor copies — avoids blocking
        # the default stream which vLLM inference uses
        self._copy_stream = None  # lazily created on first use
        self._previous_frame_width = 0
        self._previous_frame_height = 0
        self._last_frame_pts = 0
        self._uridecodebin = None
        self._adecodebin = None
        self._idecodebin = None
        self._vdecodebin = None
        self._vdecodebin_h264 = None
        self._vdecodebin_h265 = None
        self._rtspsrc = None
        self._udpsrc = None
        self._audio_eos = False
        self._audio_stop = mp.Event()
        self._audio_error = mp.Event()
        self._asr_process_finished = mp.Event()
        self._audio_start_pts = None
        self._audio_frames_lock = threading.Lock()
        self._audio_present = False
        self._eos_sent = False
        self._end_pts = None
        self._start_pts = None
        self._chunk_duration = None
        self._seek_done = False
        # True once a deferred do_seek has been scheduled for the current
        # chunk; gates seek_probe_callback from scheduling overlapping
        # GLib idle/timeout sources while a retry chain is in flight.
        self._seek_triggered = False
        self._seek_position = 0
        self._seek_probe_id = None
        # True once this fgetter's pipeline has run its main loop at least
        # once (data has flowed). While False, the pipeline is at byte 0
        # by definition, so the upfront seek to 0 is a no-op that only
        # triggers the PAUSED-state race — skip it.
        self._pipeline_has_streamed = False
        self._pipeline_has_file_buffer_probe = False
        self._file_pipeline_reusable = True
        self._audio_convert = None
        self._audio_resampler = None
        self._audio_capsfilter1 = None
        self._audio_capsfilter2 = None
        self._audio_appsink = None
        self._audio_q1 = None
        self._model_name = None
        self._server_uri = None
        self._riva_nim_server = True
        self._asr_config_file = "/tmp/rtvi/riva_asr_grpc_conf.yaml"
        self._server_config = None
        self._asr_config = None
        self._auth = None
        # GOP-aware decode optimization (file-based decoding only)
        self._parser_last_i_frame_pts: Optional[int] = None
        self._parser_estimated_gop_duration_ns: Optional[int] = None
        self._parser_current_gop_has_target: bool = True
        self._tee = None
        self._nvtracker = None
        self._cached_transcripts = []
        self._cached_audio_frames = []
        self._use_vlm_audio = False  # True when VLM handles audio natively (no ASR)
        self._asr_input_queue = None
        self._asr_output_queue = None
        self._asr_process = None
        self._cv_pipeline_configs = cv_pipeline_configs
        self._gdino = None
        self._gdino_engine = None
        self._splitmuxsink = None
        self._reconnection_attempt_count = 0
        self._error_first_detected_time = None
        self._reconnection_timeout = int(os.environ.get("RTVI_RTSP_RECONNECTION_WINDOW", "60"))
        self._reconnection_interval = int(os.environ.get("RTVI_RTSP_RECONNECTION_INTERVAL", "5"))
        self._reconnection_max_attempts = int(
            os.environ.get("RTVI_RTSP_RECONNECTION_MAX_ATTEMPTS", "10")
        )
        self._gop_decode_opt_enabled = os.environ.get(
            "RTVI_ENABLE_GOP_DECODE_OPT", "true"
        ).lower() not in ("false", "0", "no", "off")

        if "gdino_engine" in self._cv_pipeline_configs:
            self._gdino_engine = self._cv_pipeline_configs["gdino_engine"]
            if os.path.isfile(self._gdino_engine):
                from cv_pipeline.gsam_pipeline_trt_ds import cudaSetDevice

                cudaSetDevice(self._gpu_id)
                self._gdino = None
                logger.debug(
                    "Live stream : Created gdino handle %s " "for gdino engine %s",
                    self._gdino,
                    self._gdino_engine,
                )

        self._tracker_config = "/opt/nvidia/deepstream/deepstream/samples\
                    /configs/deepstream-app/config_tracker_NvDCF_perf.yml"
        if "tracker_config" in self._cv_pipeline_configs:
            if os.path.isfile(self._cv_pipeline_configs["tracker_config"]):
                self._tracker_config = self._cv_pipeline_configs["tracker_config"]
        self._inference_interval = 1
        if "inference_interval" in self._cv_pipeline_configs:
            self._inference_interval = self._cv_pipeline_configs["inference_interval"]

    def _connect_gst_signal(self, obj, *args):
        handler_id = obj.connect(*args)
        self._gst_signal_handler_ids.append((obj, handler_id))
        return handler_id

    def _add_gst_pad_probe(self, pad, probe_type, callback, *args):
        probe_id = pad.add_probe(probe_type, callback, *args)
        self._gst_pad_probe_ids.append((pad, probe_id))
        return probe_id

    def _disconnect_gst_callbacks(self):
        for pad, probe_id in reversed(self._gst_pad_probe_ids):
            try:
                pad.remove_probe(probe_id)
            except Exception as ex:
                logger.debug("Failed to remove Gst pad probe %s: %s", probe_id, ex)
        self._gst_pad_probe_ids.clear()

        for obj, handler_id in reversed(self._gst_signal_handler_ids):
            try:
                obj.disconnect(handler_id)
            except Exception as ex:
                logger.debug("Failed to disconnect Gst signal handler %s: %s", handler_id, ex)
        self._gst_signal_handler_ids.clear()

        if self._bus is not None and self._bus_signal_watch_added:
            try:
                self._bus.remove_signal_watch()
            except Exception as ex:
                logger.debug("Failed to remove Gst bus signal watch: %s", ex)
        self._bus_signal_watch_added = False

    def _append_file_frame_to_cache(self, image_tensor, pts_seconds: float) -> bool:
        with self._file_frame_cache_lock:
            if self._cached_frames is None or self._cached_frames_pts is None:
                logger.debug("Dropping decoded file frame after cache handoff/teardown")
                return False
            self._cached_frames.append(image_tensor)
            self._cached_frames_pts.append(pts_seconds)
            return True

    def _preprocess(self, frames):
        if frames and not self._enable_jpeg_output:
            # Handle multi-image scenario where frames may have different dimensions
            if len(frames) > 1:
                # Get the first frame's dimensions as target size
                first_frame = frames[0]
                target_height, target_width = first_frame.shape[:2]

                # Resize all frames to the same dimensions
                # Determine if we need to resize and get target dimensions
                need_resize = False
                for frame in frames:
                    frame_height, frame_width = frame.shape[:2]
                    if (frame_height, frame_width) != (target_height, target_width):
                        need_resize = True
                        break

                if need_resize:
                    # Use torch.nn.functional.interpolate for GPU-accelerated resizing
                    # Prepare frames for stacking - ensure they're all the same size
                    processed_frames = []
                    for frame in frames:
                        frame_height, frame_width = frame.shape[:2]

                        if (frame_height, frame_width) != (target_height, target_width):
                            # Determine if frame is HWC or CHW based on shape
                            if frame.shape[-1] == 3:  # HWC format
                                # Convert to CHW for interpolation
                                frame_chw = frame.permute(2, 0, 1).contiguous()
                                # Convert to float for interpolation
                                # (interpolate doesn't support Byte tensors)
                                frame_chw = frame_chw.float()
                                # Resize using GPU-accelerated interpolation
                                resized_chw = F.interpolate(
                                    frame_chw.unsqueeze(0),  # Add batch dimension
                                    size=(target_height, target_width),
                                    mode="bilinear",
                                    align_corners=False,
                                ).squeeze(
                                    0
                                )  # Remove batch dimension
                                # Convert back to HWC and uint8
                                resized_frame = resized_chw.permute(1, 2, 0).clamp(0, 255).byte()
                            else:  # Already CHW format
                                frame_chw = frame.contiguous()
                                # Convert to float for interpolation
                                # (interpolate doesn't support Byte tensors)
                                frame_chw = frame_chw.float()
                                resized_frame = (
                                    F.interpolate(
                                        frame_chw.unsqueeze(0),
                                        size=(target_height, target_width),
                                        mode="bilinear",
                                        align_corners=False,
                                    )
                                    .squeeze(0)
                                    .clamp(0, 255)
                                    .byte()
                                )
                            processed_frames.append(resized_frame)
                        else:
                            processed_frames.append(frame)
                    frames = processed_frames

            frames = torch.stack(frames)
            if not self._data_type_int8:
                frames = frames.half()
            if self._do_preprocess:
                if self._crop_height and self._crop_width:
                    frames = v2.functional.center_crop(
                        frames, [self._crop_height, self._crop_width]
                    )
                frames = v2.functional.normalize(
                    frames,
                    [x / (self._rescale_factor) for x in self._image_mean],
                    [x / (self._rescale_factor) for x in self._image_std],
                ).half()
        return frames

    def _update_live_stream_timestamps(self):
        """Update timestampfilter with combined timestamps from all active frame selectors.

        Must be called within self._live_stream_frame_selectors_lock.
        Combines timestamps from all overlapping chunks and updates the C++ plugin.
        """
        if not self._timestamp_filter:
            return

        # Collect all timestamps from active selectors
        all_timestamps = []
        for fs in self._live_stream_frame_selectors.keys():
            # Get remaining timestamps from this selector
            all_timestamps.extend(list(fs._selected_pts_array))

        # Sort and convert to comma-separated string
        all_timestamps.sort()
        timestamps_str = ",".join(str(int(pts)) for pts in all_timestamps) if all_timestamps else ""

        # Update the filter (thread-safe via plugin's internal mutex)
        self._timestamp_filter.set_property("timestamps", timestamps_str)

        logger.debug(
            "Updated timestampfilter with %d timestamps from %d active selectors",
            len(all_timestamps),
            len(self._live_stream_frame_selectors),
        )

    def _process_finished_chunks(self, current_pts=None, flush=False):
        chunks_processed_fs = []

        for fs, fs_data in self._live_stream_frame_selectors.items():
            if (
                (current_pts is not None and current_pts >= fs._chunk.end_pts)
                or (len(fs._selected_pts_array) == 0 and not fs.selects_all_frames)
                or flush
            ):
                if len(fs_data.cached_pts) == len(fs_data.cached_frames) or flush:
                    try:
                        cached_frames = self._preprocess(fs_data.cached_frames)
                    except torch.OutOfMemoryError as exc:
                        self._handle_cuda_oom(
                            exc,
                            "preprocessing live stream chunk frames",
                            clear_live_selectors=False,
                        )
                        fs_data.cached_frames = []
                        cached_frames = []
                    base_time = (
                        self._live_stream_ntp_epoch - self._live_stream_ntp_pts
                    ) / 1000000000
                    if self._sei_base_time:
                        base_time = self._sei_base_time / 1000000000
                    if base_time == 0:
                        base_time = time.time() - (fs._chunk.end_pts / 1e9)

                    if self._last_frame_pts >= fs._chunk.start_pts:
                        fs._chunk.end_pts = self._last_frame_pts

                    fs._chunk.start_ntp = get_timestamp_str(base_time + fs._chunk.start_pts / 1e9)
                    fs._chunk.end_ntp = get_timestamp_str(base_time + fs._chunk.end_pts / 1e9)
                    fs._chunk.start_ntp_float = base_time + (fs._chunk.start_pts / 1e9)
                    fs._chunk.end_ntp_float = base_time + (fs._chunk.end_pts / 1e9)

                    if self._enable_audio:
                        with self._live_stream_audio_transcripts_lock:
                            cached_transcripts = [
                                transcript
                                for transcript in self._cached_transcripts
                                if transcript["start"] < fs._chunk.end_pts
                            ]

                            next_chunk_start = (
                                fs._chunk.end_pts - self._live_stream_chunk_overlap_duration * 1e9
                            )
                            self._cached_transcripts = [
                                transcript
                                for transcript in self._cached_transcripts
                                if transcript["start"] >= next_chunk_start
                            ]
                    else:
                        cached_transcripts = []

                    # Collect audio frames for VLM-native audio (skips ASR path)
                    vlm_audio_frames = []
                    if self._enable_audio and self._use_vlm_audio:
                        chunk_end_secs = fs._chunk.end_pts / 1e9
                        with self._audio_frames_lock:
                            vlm_audio_frames = [
                                f for f in self._cached_audio_frames if f["start"] < chunk_end_secs
                            ]
                            self._cached_audio_frames = [
                                f for f in self._cached_audio_frames if f["start"] >= chunk_end_secs
                            ]

                    with self._err_msg_lock:
                        err_msg = self._err_msg
                    self._live_stream_chunk_decoded_callback(
                        fs._chunk,
                        cached_frames,
                        fs_data.cached_pts,
                        cached_transcripts,
                        err_msg,
                        fs_data.decode_start_time,
                        time.time(),
                        vlm_audio_frames,
                    )
                    chunks_processed_fs.append(fs)

        for fs in chunks_processed_fs:
            self._live_stream_frame_selectors.pop(fs)

        # Update timestampfilter if any chunks were removed
        if chunks_processed_fs and self._timestamp_filter:
            self._update_live_stream_timestamps()

    def _create_live_stream_video_preview_branch(
        self, pipeline, link_src_elem, link_sink_elem=None
    ):
        x264enc = Gst.ElementFactory.make("x264enc")
        if x264enc is None:
            return False

        tee = Gst.ElementFactory.make("tee")
        pipeline.add(tee)

        link_src_elem.link(tee)
        if link_sink_elem is not None:
            tee.link(link_sink_elem)
        # Create preview pipeline branch
        preview_queue = Gst.ElementFactory.make("queue")
        pipeline.add(preview_queue)
        tee.link(preview_queue)

        self._preview_valve = Gst.ElementFactory.make("valve")
        self._preview_valve.set_property("drop-mode", 2)
        preview_convert = Gst.ElementFactory.make("nvvideoconvert")
        preview_convert.set_property("compute-hw", 1)
        preview_convert.set_property("interpolation-method", 1)  # bilinear
        pipeline.add(self._preview_valve)
        pipeline.add(preview_convert)
        preview_queue.link(self._preview_valve)
        self._preview_valve.link(preview_convert)

        x264enc.set_property("bframes", 0)  # Disable B-frames
        x264enc.set_property("speed-preset", "fast")  # Fastest encoding preset
        x264enc.set_property("tune", "zerolatency")  # Optimize for low latency
        x264enc.set_property("key-int-max", 30)
        pipeline.add(x264enc)
        preview_convert.link(x264enc)

        h264parse = Gst.ElementFactory.make("h264parse")
        pipeline.add(h264parse)
        x264enc.link(h264parse)

        splitmuxsink = Gst.ElementFactory.make("splitmuxsink")
        splitmuxsink.set_property("muxer-factory", "mpegtsmux")
        splitmuxsink.set_property("max-size-time", 10 * 1000000000)
        # os.makedirs(f"/tmp/assets/{self._live_stream_request_id}", exist_ok=True)
        splitmuxsink.set_property(
            "location",
            f"/tmp/assets/{self._live_stream_request_id}/{self._live_stream_request_id}_%d.ts",
        )
        splitmuxsink.set_property("max-files", 2)
        pipeline.add(splitmuxsink)
        h264parse.link(splitmuxsink)

        def valve_control_thread(self):
            preview_file = f"/tmp/assets/{self._live_stream_request_id}/.ui_preview"
            time.sleep(60)
            while self._pipeline is not None:
                try:
                    if os.path.exists(preview_file):
                        mtime = os.path.getmtime(preview_file)
                        if time.time() - mtime <= 30:
                            self._preview_valve.set_property("drop", False)
                            time.sleep(1)
                            continue
                    self._preview_valve.set_property("drop", True)
                except Exception as e:
                    logger.error(f"Error in valve control thread: {e}")
                    self._preview_valve.set_property("drop", True)
                time.sleep(1)

        threading.Thread(target=valve_control_thread, daemon=True, args=(self,)).start()

        h264parse_src_pad = preview_queue.get_static_pad("sink")

        def on_h264parse_buffer(pad, info):
            buffer = info.get_buffer()
            buffer.dts = Gst.CLOCK_TIME_NONE
            if not hasattr(self, "_prev_pts"):
                self._prev_pts = -1

            if self._prev_pts >= buffer.pts:
                return Gst.PadProbeReturn.DROP

            self._prev_pts = buffer.pts
            return Gst.PadProbeReturn.OK

        self._add_gst_pad_probe(h264parse_src_pad, Gst.PadProbeType.BUFFER, on_h264parse_buffer)
        self._splitmuxsink = splitmuxsink
        return True

    def _asr_input_thread(self):
        """Thread that reads audio frames from the cached frames and sends them to the ASR service"""
        while not self._audio_stop.is_set() or len(self._cached_audio_frames) > 0:
            with self._audio_frames_lock:
                while len(self._cached_audio_frames) > 0:
                    audio_frame = self._cached_audio_frames.pop(0)
                    self._asr_input_queue.put(audio_frame)
            time.sleep(0.03)

    def _asr_output_thread(self):
        """Thread that reads ASR output from the queue and sends it to the cached frames"""
        while not self._asr_process_finished.is_set() or not self._asr_output_queue.empty():
            if not self._asr_output_queue.empty():
                asr_output = self._asr_output_queue.get()
                if len(asr_output["transcript"]) > 0:
                    start_time = asr_output["start"]
                    end_time = asr_output["end"]
                    transcript = asr_output["transcript"]
                    start_time *= 1e6
                    end_time *= 1e6
                    start_time += self._audio_start_pts
                    end_time += self._audio_start_pts

                    with self._audio_end_cv:
                        self._audio_current_pts = start_time
                        self._audio_end_cv.notify()

                    with self._audio_start_cv:
                        with self._err_msg_lock:
                            has_error = self._err_msg is not None
                        if (
                            (start_time) > self._live_stream_next_chunk_start_pts
                            and not has_error
                            and not self._stop_stream
                        ):
                            logger.debug("Waiting for next audio chunk start.")
                            self._audio_start_cv.wait(1)

                    with self._live_stream_audio_transcripts_lock:
                        self._cached_transcripts.append(
                            {
                                "transcript": transcript,
                                "start": start_time,
                                "end": end_time,
                            }
                        )
                    logger.debug(
                        "Audio transcript: %s, buffer.pts: %d, duration: %d",
                        transcript,
                        start_time,
                        end_time - start_time,
                    )

                    with self._audio_end_cv:
                        self._audio_current_pts = end_time
                        self._audio_end_cv.notify()

            with self._err_msg_lock:
                if self._audio_error.is_set() and self._err_msg is None:
                    self._err_msg = "Error in ASR transcript generation."
                    self._audio_stop.set()
                    logger.error(self._err_msg)
                    break
            time.sleep(0.03)

        with self._audio_end_cv:
            self._audio_end_cv.notify()

    def _on_parser_src_buffer(self, pad, info):
        """Drop delta frames when the next target PTS falls in a future GOP.

        Prevents nvv4l2decoder from processing P/B frames that will never
        contribute to a selected output frame (file-based decoding only).
        """
        buffer = info.get_buffer()
        if buffer is None or buffer.pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK

        if self._is_live:
            return Gst.PadProbeReturn.OK

        if self._frame_selector.selects_all_frames:
            return Gst.PadProbeReturn.OK

        is_delta = buffer.has_flags(Gst.BufferFlags.DELTA_UNIT)

        if not is_delta:
            # I-frame: update running average of GOP duration.
            prev_i_pts = self._parser_last_i_frame_pts
            if prev_i_pts is not None and buffer.pts > prev_i_pts:
                gop_dur = buffer.pts - prev_i_pts
                est = self._parser_estimated_gop_duration_ns
                self._parser_estimated_gop_duration_ns = (
                    gop_dur if est is None else max(gop_dur, est)
                )
            self._parser_last_i_frame_pts = buffer.pts

            # Check whether any selected target falls within this GOP.
            est_gop = self._parser_estimated_gop_duration_ns
            if est_gop and est_gop > 0:
                next_gop_start = buffer.pts + est_gop
                targets = self._frame_selector._selected_pts_array
                self._parser_current_gop_has_target = any(
                    buffer.pts <= (t + self._frame_duration_ns) < next_gop_start for t in targets
                )
            else:
                # GOP size unknown yet — keep all frames conservatively.
                self._parser_current_gop_has_target = True

            self._frame_duration_ns = 0

            logger.debug(
                "I-frame pts=%d ns, est_gop=%s ns, gop_has_target=%s",
                buffer.pts,
                self._parser_estimated_gop_duration_ns,
                self._parser_current_gop_has_target,
            )
            return Gst.PadProbeReturn.OK

        if self._frame_duration_ns == 0 and self._parser_last_i_frame_pts is not None:
            self._frame_duration_ns = buffer.pts - self._parser_last_i_frame_pts

        # Delta frame: drop if no target lives in the current GOP.
        if not self._parser_current_gop_has_target:
            return Gst.PadProbeReturn.DROP
        else:
            # Check if remaining targets are in the current GOP.
            est_gop = self._parser_estimated_gop_duration_ns
            if est_gop and est_gop > 0:
                next_gop_start = self._parser_last_i_frame_pts + est_gop
                targets = self._frame_selector._selected_pts_array
                self._parser_current_gop_has_target = any(
                    buffer.pts <= t + self._frame_duration_ns < next_gop_start for t in targets
                )
                if not self._parser_current_gop_has_target:
                    logger.debug(
                        "Dropping next delta frames %s: no target lives in the current GOP.",
                        buffer.pts,
                    )
            else:
                # GOP size unknown yet — keep all frames conservatively.
                self._parser_current_gop_has_target = True

        return Gst.PadProbeReturn.OK

    def _create_pipeline(
        self, file_or_rtsp: str, username="", password="", create_source_elems_only=False
    ):
        # Construct DeepStream pipeline for decoding
        # For raw frames as tensor:
        # uridecodebin -> probe (frame selector) -> nvvideconvert -> appsink
        #     -> frame pre-processing -> add to cache
        # For jpeg images:
        # uridecodebin -> probe (frame selector) -> nvjpegenc -> appsink -> add to cache
        # For audio: uridecodebin -> probe -> audioconvert ->
        # resample -> asr -> appsink -> add text_to cache
        self._is_live = file_or_rtsp.startswith("rtsp://")
        pipeline = self._pipeline if create_source_elems_only else Gst.Pipeline()

        # Reset per-file GOP tracking state.
        self._parser_last_i_frame_pts = None
        self._parser_current_gop_has_target = True
        self._parser_estimated_gop_duration_ns = None
        self._frame_duration_ns = 0

        def cb_elem_added(elem, username, password, selff):
            if "nvv4l2decoder" in elem.get_factory().get_name():
                elem.set_property("gpu-id", self._gpu_id)
                elem.set_property("extract-sei-type5-data", True)
                elem.set_property("sei-uuid", "NVDS_CUSTOMMETA")
            if "mpeg4videoparse" in elem.get_factory().get_name():
                elem.set_property("config-interval", -1)
            parser_name = elem.get_factory().get_name()
            if parser_name in ("h264parse", "h265parse", "mpeg4videoparse"):
                if self._gop_decode_opt_enabled:
                    src_pad = elem.get_static_pad("src")
                    if src_pad is not None:
                        self._add_gst_pad_probe(
                            src_pad,
                            Gst.PadProbeType.BUFFER,
                            lambda pad, info: self._on_parser_src_buffer(pad, info),
                        )
                        logger.debug("GOP decode-opt probe attached to %s", parser_name)
                else:
                    logger.debug(
                        "GOP decode-opt disabled via RTVI_ENABLE_GOP_DECODE_OPT; "
                        "probe not attached to %s",
                        parser_name,
                    )
                    logger.debug("GOP decode-opt probe attached to %s", parser_name)
            if parser_name == "rtpjitterbuffer":
                drop_on_latency = _env_bool("RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY", False)
                if elem.find_property("drop-on-latency"):
                    elem.set_property("drop-on-latency", drop_on_latency)
                    logger.info(
                        "Configured rtpjitterbuffer drop-on-latency=%s",
                        drop_on_latency,
                    )
                else:
                    logger.warning("rtpjitterbuffer has no drop-on-latency property; ignoring")
                try:
                    faststart_min_packets = int(
                        os.environ.get("RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS", "") or "0"
                    )
                except ValueError:
                    faststart_min_packets = 0
                    logger.warning(
                        "Ignoring invalid RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS=%r",
                        os.environ.get("RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS"),
                    )
                if faststart_min_packets > 0:
                    if elem.find_property("faststart-min-packets"):
                        elem.set_property("faststart-min-packets", faststart_min_packets)
                        logger.info(
                            "Configured rtpjitterbuffer faststart-min-packets=%d",
                            faststart_min_packets,
                        )
                    else:
                        logger.warning(
                            "rtpjitterbuffer has no faststart-min-packets property; ignoring"
                        )
            if "rtspsrc" == elem.get_factory().get_name():
                selff._rtspsrc = elem
                pyds.configure_source_for_ntp_sync(hash(elem))
                timeout = int(os.environ.get("RTVI_RTSP_TIMEOUT", "") or "2000") * 1000
                latency = int(os.environ.get("RTVI_RTSP_LATENCY", "") or "2000")
                elem.set_property("timeout", timeout)
                elem.set_property("latency", latency)
                if elem.find_property("drop-on-latency"):
                    drop_on_latency = _env_bool("RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY", False)
                    elem.set_property("drop-on-latency", drop_on_latency)
                    logger.info("Configured rtspsrc drop-on-latency=%s", drop_on_latency)
                # Below code need additional review and tests.
                # Also is a feature - to let users change protocol.
                # Protocols: Allowed lower transport protocols
                # Default: 0x00000007, "tcp+udp-mcast+udp"
                # protocols = int(os.environ.get("RTVI_RTSP_PROTOCOLS", "") or "7")
                # elem.set_property("protocols", protocols)

                if username and password:
                    elem.set_property("user-id", username)
                    elem.set_property("user-pw", password)

                if not self._audio_support or not self._enable_audio:
                    # Ignore audio
                    self._connect_gst_signal(elem, "select-stream", cb_select_stream)

                # Connect before-send to handle TEARDOWN per:
                # Unfortunately, going to the NULL state involves going through PAUSED,
                # so rtspsrc does not know the difference and will send a PAUSE
                # when you wanted a TEARDOWN. The workaround is to
                # hook into the before-send signal and return FALSE in this case.
                # Source: https://gstreamer.freedesktop.org/documentation/rtsp/rtspsrc.html
                self._connect_gst_signal(elem, "before-send", cb_before_send, selff)
            if "udpsrc" == elem.get_factory().get_name():
                logger.debug("udpsrc created")
                selff._udpsrc = elem

        def cb_newpad_decodebin(uridecodebin, uridecodebin_pad, self):
            caps = uridecodebin_pad.get_current_caps()
            gststruct = caps.get_structure(0)
            gstname = gststruct.get_name()
            if gstname.find("video") != -1:
                uridecodebin_pad.link(self._q1.get_static_pad("sink"))
                logger.info("Video stream found.")
                with self._err_msg_lock:
                    if self._error_first_detected_time is not None:
                        logger.info("Reconnection successful. Resetting reconnection tracking.")
                        error_message = (
                            f"Live stream reconnection successful. Error message: {self._err_msg}"
                        )
                        if self._on_stream_error_callback:
                            self._on_stream_error_callback(
                                error_message, self._live_stream_request_id, 0
                            )
                        self._err_msg = None
                        self._error_first_detected_time = None
                        self._reconnection_attempt_count = 0

            if gstname.find("audio") != -1 and self._enable_audio and self._audio_q1:
                self._audio_present = True
                with self._audio_present_cv:
                    self._audio_present_cv.notify()
                self._audio_eos = False
                uridecodebin_pad.link(self._audio_q1.get_static_pad("sink"))
                logger.info("Audio stream found.")

        uridecodebin = None
        if self._is_live:
            uridecodebin = Gst.ElementFactory.make("uridecodebin")
            uridecodebin.set_property("uri", file_or_rtsp)
            pipeline.add(uridecodebin)
            self._uridecodebin = uridecodebin
        else:
            filesrc = Gst.ElementFactory.make("filesrc")
            filesrc.set_property("location", file_or_rtsp)
            pipeline.add(filesrc)
            self._filesrc = filesrc

            self._parsebin = Gst.ElementFactory.make("parsebin")
            pipeline.add(self._parsebin)

            filesrc.link(self._parsebin)

            def cb_newpad_parsebin(parsebin, parsebin_pad, self):
                caps = parsebin_pad.query_caps(None)
                if not caps:
                    return
                gststruct = caps.get_structure(0)
                gstname = gststruct.get_name()

                if gstname.find("video") != -1:
                    if (
                        gstname.find("h264") != -1
                        and os.environ.get("DISABLE_DECODER_REUSE", "true") == "false"
                    ):
                        if not self._vdecodebin_h264:
                            self._vdecodebin_h264 = Gst.ElementFactory.make("decodebin")
                            pipeline.add(self._vdecodebin_h264)
                            self._vdecodebin_h264.set_state(Gst.State.PLAYING)
                            self._connect_gst_signal(
                                self._vdecodebin_h264, "pad-added", cb_newpad_decodebin, self
                            )
                            self._connect_gst_signal(
                                self._vdecodebin_h264,
                                "deep-element-added",
                                lambda bin, subbin, elem, username=username, password=password, selff=self: cb_elem_added(  # noqa: E501
                                    elem, username, password, selff
                                ),
                            )
                        else:
                            pipeline.add(self._vdecodebin_h264)
                            self._vdecodebin_h264.link(self._q1)
                        self._vdecodebin = self._vdecodebin_h264
                    elif (
                        gstname.find("h265") != -1
                        and os.environ.get("DISABLE_DECODER_REUSE", "true") == "false"
                    ):
                        if not self._vdecodebin_h265:
                            self._vdecodebin_h265 = Gst.ElementFactory.make("decodebin")
                            pipeline.add(self._vdecodebin_h265)
                            self._vdecodebin_h265.set_state(Gst.State.PLAYING)
                            self._connect_gst_signal(
                                self._vdecodebin_h265, "pad-added", cb_newpad_decodebin, self
                            )
                            self._connect_gst_signal(
                                self._vdecodebin_h265,
                                "deep-element-added",
                                lambda bin, subbin, elem, username=username, password=password, selff=self: cb_elem_added(  # noqa: E501
                                    elem, username, password, selff
                                ),
                            )
                        else:
                            pipeline.add(self._vdecodebin_h265)
                            self._vdecodebin_h265.link(self._q1)
                        self._vdecodebin = self._vdecodebin_h265
                    elif not self._vdecodebin:
                        self._vdecodebin = Gst.ElementFactory.make("decodebin")
                        pipeline.add(self._vdecodebin)
                        self._vdecodebin.set_state(Gst.State.PLAYING)
                        self._connect_gst_signal(
                            self._vdecodebin, "pad-added", cb_newpad_decodebin, self
                        )
                        self._connect_gst_signal(
                            self._vdecodebin,
                            "deep-element-added",
                            lambda bin, subbin, elem, username=username, password=password, selff=self: cb_elem_added(  # noqa: E501
                                elem, username, password, selff
                            ),
                        )
                    parsebin_pad.link(self._vdecodebin.get_static_pad("sink"))

                if gstname.find("image") != -1:
                    self._idecodebin = Gst.ElementFactory.make("decodebin")
                    pipeline.add(self._idecodebin)
                    self._idecodebin.set_state(Gst.State.PLAYING)
                    parsebin_pad.link(self._idecodebin.get_static_pad("sink"))
                    self._connect_gst_signal(
                        self._idecodebin, "pad-added", cb_newpad_decodebin, self
                    )

                if gstname.find("audio") != -1 and self._audio_support and self._enable_audio:
                    if self._adecodebin is not None:
                        logger.warning(
                            "Multiple audio tracks detected; only the first track will be used."
                        )
                        return
                    self._adecodebin = Gst.ElementFactory.make("decodebin")
                    pipeline.add(self._adecodebin)
                    self._adecodebin.set_state(Gst.State.PLAYING)
                    parsebin_pad.link(self._adecodebin.get_static_pad("sink"))
                    self._connect_gst_signal(
                        self._adecodebin, "pad-added", cb_newpad_decodebin, self
                    )

            self._connect_gst_signal(self._parsebin, "pad-added", cb_newpad_parsebin, self)

        if create_source_elems_only:
            return

        self._q1 = Gst.ElementFactory.make("queue")
        pipeline.add(self._q1)

        qvideoconvert = Gst.ElementFactory.make("queue")
        pipeline.add(qvideoconvert)

        # Create timestampfilter element for C++ based frame filtering (faster than Python buffer_probe).
        # Keep live streams on the Python selector path by default. The live chunk manager needs to
        # observe the continuous stream to create/close chunks; putting timestampfilter upstream can
        # starve that manager once the current target list is consumed.
        self._timestamp_filter = None
        enable_live_timestamp_filter = os.environ.get(
            "RTVI_ENABLE_LIVE_TIMESTAMP_FILTER", "false"
        ).lower() in ("true", "1", "yes")
        enable_file_timestamp_filter = os.environ.get(
            "RTVI_ENABLE_FILE_TIMESTAMP_FILTER", "true"
        ).lower() in ("true", "1", "yes")
        selects_all_file_frames = not self._is_live and self._frame_selector.selects_all_frames
        use_timestamp_filter = (
            enable_live_timestamp_filter if self._is_live else enable_file_timestamp_filter
        )
        if selects_all_file_frames:
            # All-frame file chunks have no discrete timestamp target list.
            # Keep them on the Python selector path instead of asking
            # timestampfilter to interpret an empty list.
            use_timestamp_filter = False
        if use_timestamp_filter:
            self._timestamp_filter = Gst.ElementFactory.make("timestampfilter", "pts_filter")
        elif self._is_live:
            logger.info(
                "Live stream timestampfilter disabled for %s; using Python frame selector",
                self._live_stream_request_id,
            )
        elif selects_all_file_frames:
            logger.info("File timestampfilter disabled for all-frame chunk")
        else:
            logger.info("File timestampfilter disabled; using Python frame selector")
        if self._timestamp_filter:
            # For file-based, set initial timestamps from frame selector
            if not self._is_live:
                # Convert timestamps to comma-separated string (nanoseconds)
                timestamps_str = ",".join(
                    str(int(pts)) for pts in self._frame_selector._selected_pts_array
                )
                self._timestamp_filter.set_property("timestamps", timestamps_str)
                self._timestamp_filter.set_property("send-eos-when-done", not self._audio_present)
            else:
                # For live streams, initialize empty (will be updated dynamically)
                self._timestamp_filter.set_property("timestamps", "")
                self._timestamp_filter.set_property(
                    "send-eos-when-done", False
                )  # Never send EOS for live
            pipeline.add(self._timestamp_filter)
        elif use_timestamp_filter:
            logger.warning("timestampfilter plugin not found, falling back to Python buffer_probe")

        if self._is_live and not os.environ.get("RTVI_DISABLE_LIVESTREAM_PREVIEW", ""):
            logger.info(
                "Creating live stream video preview branch for %s", self._live_stream_request_id
            )
            if not self._create_live_stream_video_preview_branch(pipeline, self._q1, qvideoconvert):
                logger.warning(
                    "Failed to create live stream video preview branch. Additional codecs not installed."  # noqa: E501
                )
                self._q1.link(qvideoconvert)
        else:
            self._q1.link(qvideoconvert)

        q2 = Gst.ElementFactory.make("queue")
        pipeline.add(q2)

        videoconvert = Gst.ElementFactory.make("nvvideoconvert")
        self._videoconvert = videoconvert
        videoconvert.set_property("nvbuf-memory-type", 2)
        videoconvert.set_property("compute-hw", 1)
        videoconvert.set_property("interpolation-method", 1)  # bilinear for better scaling quality

        videoconvert.set_property("gpu-id", self._gpu_id)
        pipeline.add(videoconvert)

        if self._enable_jpeg_output:
            jpegenc = Gst.ElementFactory.make("nvjpegenc")
            format = "I420"  # only RGB/I420 supported by nvjpegenc
            if jpegenc is None:
                jpegenc = Gst.ElementFactory.make("nvimageenc")
                format = "RGB"  # only RGB/I420 supported by nvjpegenc
            pipeline.add(jpegenc)
        else:
            format = "GBR" if self._do_preprocess else "RGB"
            pass

        # Add parallel encoding pipeline for saving images to disk
        self._enable_image_save = os.getenv("SAVE_CHUNK_FRAMES_MINIO", "false").lower() == "true"
        if self._enable_image_save and self._enable_jpeg_output is False:
            # Create a tee to split the video stream
            tee = Gst.ElementFactory.make("tee")
            tee.set_property("name", "video_tee")
            pipeline.add(tee)

            # Create encoding branch
            encode_queue = Gst.ElementFactory.make("queue")
            pipeline.add(encode_queue)

            # Create video converter for encoding branch
            encode_videoconvert = Gst.ElementFactory.make("nvvideoconvert")
            encode_videoconvert.set_property("gpu-id", self._gpu_id)
            pipeline.add(encode_videoconvert)

            # Create caps filter for encoding format
            encode_capsfilter = Gst.ElementFactory.make("capsfilter")
            encode_format = "I420"  # I420 works well with both nvjpegenc and nvimageenc
            encode_capsfilter.set_property(
                "caps", Gst.Caps.from_string(f"video/x-raw(memory:NVMM), format={encode_format}")
            )
            pipeline.add(encode_capsfilter)

            image_encoder = Gst.ElementFactory.make("nvjpegenc")
            if image_encoder is None:
                image_encoder = Gst.ElementFactory.make("nvimageenc")

            if image_encoder is None:
                logger.warning("NVIDIA encoders not available. Falling back to software encoding.")
                image_encoder = Gst.ElementFactory.make("jpegenc")
                encode_capsfilter.set_property(
                    "caps", Gst.Caps.from_string("video/x-raw, format=I420")
                )

            pipeline.add(image_encoder)
            fakesink = Gst.ElementFactory.make("fakesink")
            fakesink.set_property("async", False)
            pipeline.add(fakesink)

            # Store elements for later linking and cleanup
            self._encoding_elements = {
                "tee": tee,
                "encode_queue": encode_queue,
                "encode_videoconvert": encode_videoconvert,
                "encode_capsfilter": encode_capsfilter,
                "image_encoder": image_encoder,
                "fakesink": fakesink,
            }

            # Link encoding pipeline elements
            tee.link(encode_queue)
            encode_queue.link(encode_videoconvert)
            encode_videoconvert.link(encode_capsfilter)
            encode_capsfilter.link(image_encoder)
            image_encoder.link(fakesink)

        # format = "NV12"
        capsfilter = Gst.ElementFactory.make("capsfilter")
        self._out_caps_filter = capsfilter
        capsfilter.set_property(
            "caps",
            Gst.Caps.from_string(
                (
                    f"video/x-raw(memory:NVMM), format={format},"
                    f" width={self._frame_width}, height={self._frame_height}"
                )
                if self._frame_width and self._frame_height
                else f"video/x-raw(memory:NVMM), format={format}"
            ),
        )
        pipeline.add(capsfilter)

        self._audio_q1 = None
        if self._audio_support:
            self._audio_eos = False
            self._audio_present = False
            self._audio_q1 = Gst.ElementFactory.make("queue")
            pipeline.add(self._audio_q1)

            # Audio converter for non-interleaved audio to interleaved conversion
            self._audio_convert = Gst.ElementFactory.make("audioconvert")
            pipeline.add(self._audio_convert)

            self._audio_resampler = Gst.ElementFactory.make("audioresample")
            pipeline.add(self._audio_resampler)

            self._audio_capsfilter1 = Gst.ElementFactory.make("capsfilter")
            audio_format = "S16LE"
            self._audio_capsfilter1.set_property(
                "caps",
                Gst.Caps.from_string(
                    f"audio/x-raw, format={audio_format}" f"channels=1, channel-mask=(bitmask)1"
                ),
            )
            pipeline.add(self._audio_capsfilter1)

            self._audio_capsfilter2 = Gst.ElementFactory.make("capsfilter")

            audio_format = "S16LE"
            self._audio_capsfilter2.set_property(
                "caps",
                Gst.Caps.from_string(
                    f"audio/x-raw, format={audio_format},"
                    f"rate=16000, channels=1, channel-mask=(bitmask)1"
                ),
            )
            pipeline.add(self._audio_capsfilter2)

        def buffer_probe(pad, info, data):
            # Probe callback function to pass chosen frames and drop other frames
            buffer = info.get_buffer()
            if buffer.pts == Gst.CLOCK_TIME_NONE:
                return Gst.PadProbeReturn.DROP

            self._last_frame_pts = buffer.pts

            if self._is_live:
                buffer_address = hash(buffer)
                if HAVE_SEI_META_LIB:
                    video_sei_meta = gst_video_sei_meta.gst_buffer_get_video_sei_meta(
                        buffer_address
                    )
                else:
                    video_sei_meta = None

                if video_sei_meta:
                    try:
                        sei_data = json.loads(video_sei_meta.sei_metadata_ptr)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse SEI metadata JSON: {e}")
                        sei_data = None

                    if sei_data and "sim_time" in sei_data:
                        sim_time = sei_data["sim_time"]
                        if isinstance(sim_time, (int, float)):
                            original_pts = buffer.pts
                            new_pts = sim_time * 1e9
                            logger.debug(
                                f"SEI timestamp override: original_pts={original_pts} ns, "
                                f"sim_time={sim_time} s, new_pts={new_pts} ns"
                            )
                            buffer.pts = new_pts
                        else:
                            logger.warning(
                                f"SEI sim_time is not numeric (type={type(sim_time).__name__}, "
                                f"value={sim_time}), skipping timestamp override"
                            )

                new_chunk = False
                if buffer.pts >= self._live_stream_next_chunk_start_pts:
                    with self._audio_end_cv:
                        with self._err_msg_lock:
                            has_error = self._err_msg is not None
                        if (
                            self._audio_present
                            and self._audio_current_pts < self._live_stream_next_chunk_start_pts
                            and not has_error
                            and not self._stop_stream
                        ):
                            logger.debug(
                                "In buffer probe waiting for audio processing,"
                                "current audio pts: %d",
                                self._audio_current_pts,
                            )
                            self._audio_end_cv.wait(1)

                with self._live_stream_frame_selectors_lock:
                    if video_sei_meta:
                        try:
                            self._sei_data = json.loads(video_sei_meta.sei_metadata_ptr)
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse SEI metadata JSON: {e}")
                            self._sei_data = None

                        if self._sei_data and self._sei_base_time is None:
                            if "timestamp" in self._sei_data:
                                timestamp = self._sei_data["timestamp"]
                                if isinstance(timestamp, (int, float)):
                                    self._sei_base_time = timestamp - buffer.pts
                                    logger.debug(
                                        f"SEI base_time initialized: timestamp={timestamp} ns, "
                                        f"buffer.pts={buffer.pts} ns, base_time={self._sei_base_time} ns"
                                    )
                                else:
                                    logger.warning(
                                        f"SEI timestamp is not numeric (type={type(timestamp).__name__}, "
                                        f"value={timestamp}), skipping base_time calculation"
                                    )
                            else:
                                logger.warning(
                                    "SEI data missing 'timestamp' key, skipping base_time calculation"
                                )

                    if buffer.pts >= self._live_stream_next_chunk_start_pts:
                        fs = DefaultFrameSelector(
                            num_frames_per_second_or_fixed_frames=(
                                self._frame_selector._num_frames_per_second_or_fixed_frames
                            ),
                            use_fps_for_chunking=self._frame_selector._use_fps_for_chunking,
                        )
                        chunk = ChunkInfo()
                        chunk.file = self._live_stream_url
                        chunk.chunkIdx = self._live_stream_next_chunk_idx
                        chunk.is_first = chunk.chunkIdx == 0
                        if chunk.is_first:
                            self._live_stream_next_chunk_start_pts = buffer.pts
                        chunk.start_pts = int(self._live_stream_next_chunk_start_pts)
                        chunk.end_pts = int(
                            chunk.start_pts + self._live_stream_chunk_duration * 1e9
                        )

                        fs.set_chunk(chunk)
                        self._live_stream_frame_selectors[fs] = FrameSelectorData()
                        self._live_stream_next_chunk_start_pts = (
                            chunk.end_pts - self._live_stream_chunk_overlap_duration * 1e9
                        )
                        self._live_stream_next_chunk_idx += 1
                        new_chunk = True

                        # Update timestampfilter with combined timestamps from all active selectors
                        self._update_live_stream_timestamps()

                    # For live streams with timestampfilter, the filtering is done by the C++ plugin
                    # We only need to track which frames are selected for cached_pts
                    if self._timestamp_filter:
                        # Timestampfilter will handle filtering, we just track selected frames
                        for fs, fs_data in self._live_stream_frame_selectors.items():
                            if fs.choose_frame(buffer, buffer.pts):
                                fs_data.cached_pts.append(buffer.pts / 1e9)
                        choose_frame = (
                            True  # Let timestampfilter make the actual keep/drop decision
                        )
                    else:
                        # Fallback: Python-based filtering when timestampfilter not available
                        choose_frame = False
                        for fs, fs_data in self._live_stream_frame_selectors.items():
                            if fs.choose_frame(buffer, buffer.pts):
                                choose_frame = True
                                fs_data.cached_pts.append(buffer.pts / 1e9)

                    self._process_finished_chunks(buffer.pts)

                if new_chunk:
                    with self._audio_start_cv:
                        self._audio_start_cv.notify()

                if choose_frame:
                    return Gst.PadProbeReturn.OK

            else:
                if self._frame_selector.choose_frame(buffer, buffer.pts):
                    return Gst.PadProbeReturn.OK
                selector_done = len(self._frame_selector._selected_pts_array) == 0
                if self._frame_selector.selects_all_frames:
                    selector_done = buffer.pts >= self._frame_selector._selection_end_pts
                if selector_done and not self._eos_sent:
                    if self._audio_present:
                        if self._audio_eos:
                            self._pipeline.send_event(Gst.Event.new_eos())
                            self._eos_sent = True
                            logger.debug("sent eos")
                    else:
                        self._pipeline.send_event(Gst.Event.new_eos())
                        if self._audio_convert:
                            self._audio_convert.send_event(Gst.Event.new_eos())
                        self._eos_sent = True
                        logger.debug("sent eos")

            return Gst.PadProbeReturn.DROP

        def add_to_cache(buffer, width, height):
            # Probe callback to add raw frame / jpeg image to cache
            success, mapinfo = buffer.map(Gst.MapFlags.READ)
            if not success:
                logger.warning("Failed to map decoded frame buffer")
                return False
            try:
                if self._enable_jpeg_output:
                    # Buffer contains JPEG image, add to cache as is.
                    image_tensor = np.frombuffer(mapinfo.data, dtype=np.uint8).copy()
                else:
                    # Extract GPU memory pointer and create tensor from it using
                    # DeepStream Python Bindings and cupy.
                    _, shape, strides, dataptr, size = pyds.get_nvds_buf_surface_gpu(
                        hash(buffer), 0
                    )
                    ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
                    ctypes.pythonapi.PyCapsule_GetPointer.argtypes = [
                        ctypes.py_object,
                        ctypes.c_char_p,
                    ]
                    owner = None
                    c_data_ptr = ctypes.pythonapi.PyCapsule_GetPointer(dataptr, None)
                    unownedmem = cp.cuda.UnownedMemory(c_data_ptr, size, owner)
                    memptr = cp.cuda.MemoryPointer(unownedmem, 0)
                    n_frame_gpu = cp.ndarray(
                        shape=shape, dtype=np.uint8, memptr=memptr, strides=strides, order="C"
                    )
                    # Clone on a dedicated stream to avoid blocking the default
                    # CUDA stream (used by vLLM inference). Sync before buffer
                    # unmap so the DeepStream buffer can be safely reused.
                    if self._copy_stream is None:
                        self._copy_stream = torch.cuda.Stream()
                    with torch.cuda.stream(self._copy_stream):
                        image_tensor = torch.as_tensor(n_frame_gpu, device="cuda").clone()
                    self._copy_stream.synchronize()

                # Cache the pre-processed frame / jpeg and its timestamp. Convert
                # the timestamps from nanoseconds to seconds.
                if self._is_live:
                    with self._live_stream_frame_selectors_lock:
                        for _, fs_data in self._live_stream_frame_selectors.items():
                            if buffer.pts / 1e9 in fs_data.cached_pts:
                                fs_data.cached_frames.append(image_tensor)
                        self._process_finished_chunks(buffer.pts)
                else:
                    return self._append_file_frame_to_cache(image_tensor, buffer.pts / 1000000000.0)
            finally:
                buffer.unmap(mapinfo)
            return True

        def add_text_to_cache(buffer):
            # Probe callback to add audio transcription to cache
            _, mapinfo = buffer.map(Gst.MapFlags.READ)
            transcription = mapinfo.data.decode("utf-8")
            logger.debug(
                "Audio transcript: %s, buffer.pts: %d, duration: %d",
                transcription,
                buffer.pts,
                buffer.duration,
            )

            # Cache the audio transcripts and its timestamp. Convert
            # the timestamps from nanoseconds to seconds.
            with self._audio_end_cv:
                self._audio_current_pts = buffer.pts
                self._audio_end_cv.notify()

            with self._audio_start_cv:
                with self._err_msg_lock:
                    has_error = self._err_msg is not None
                if (
                    buffer.pts > self._live_stream_next_chunk_start_pts
                    and not has_error
                    and not self._stop_stream
                ):
                    logger.debug("Wating for next audio chunk start.")
                    self._audio_start_cv.wait(1)

            with self._live_stream_frame_selectors_lock:
                self._cached_transcripts.append(
                    {
                        "transcript": transcription,
                        "start": (buffer.pts) / 1000000000.0,
                        "end": (buffer.pts + buffer.duration) / 1000000000.0,
                    }
                )

            with self._audio_end_cv:
                self._audio_current_pts = buffer.pts + buffer.duration
                self._audio_end_cv.notify()

            buffer.unmap(mapinfo)
            logger.debug("Picked audio transcription buffer %d", buffer.pts)

        def add_audio_to_cache(buffer):
            # Probe callback to add audio samples to cache
            _, mapinfo = buffer.map(Gst.MapFlags.READ)
            audio_tensor = np.frombuffer(mapinfo.data, dtype=np.int16)
            # logger.debug(
            #     "New audio buffer, buffer.pts: %d, duration: %d", buffer.pts, buffer.duration
            # )

            with self._audio_frames_lock:
                if self._audio_start_pts is None:
                    if buffer.pts != Gst.CLOCK_TIME_NONE:
                        self._audio_start_pts = buffer.pts
                    else:
                        self._audio_start_pts = 0

            # Cache the audio samples and their timestamp. Convert
            # the timestamps from nanoseconds to seconds.
            with self._audio_frames_lock:
                self._cached_audio_frames.append(
                    {
                        "audio": audio_tensor,
                        "start": (buffer.pts) / 1000000000.0,
                        "end": (buffer.pts + buffer.duration) / 1000000000.0,
                    }
                )

            buffer.unmap(mapinfo)
            # logger.debug("Picked audio buffer %d", buffer.pts)

        def on_new_sample(appsink):
            # Appsink callback to pull frame from the pipeline
            sample = appsink.emit("pull-sample")
            caps = sample.get_caps()
            height = caps.get_structure(0).get_value("height")
            width = caps.get_structure(0).get_value("width")
            if self._first_frame_width == 0:
                logger.debug("first width,height in chunk=%d, %d", width, height)
                self._first_frame_width = width
                self._first_frame_height = height
            if sample:
                buffer = sample.get_buffer()
                if not add_to_cache(buffer, width, height):
                    return Gst.FlowReturn.ERROR
            return Gst.FlowReturn.OK

        def on_new_sample_audio(audio_appsink):
            # Appsink callback to pull audio samples from the pipeline
            sample = audio_appsink.emit("pull-sample")
            if sample:
                buffer = sample.get_buffer()
                # logger.debug("New audio buffer with pts: %d", buffer.pts)
                if buffer:
                    if self._is_live:
                        if buffer.get_size():
                            add_audio_to_cache(buffer)
                    else:
                        if buffer.pts >= self._end_pts and not self._audio_eos:
                            self._audio_eos = True
                            logger.info("Audio pipeline finished for chunk: %d", self._chunkIdx)
                        if buffer.get_size() and not self._audio_eos:
                            # Audio buffer for file input
                            add_audio_to_cache(buffer)
            return Gst.FlowReturn.OK

        def cb_ntpquery(pad, info, data):
            # Probe callback to handle NTP information from RTSP stream
            # This requires RTSP Sender Report support in the source.
            query = info.get_query()
            if query.type == Gst.QueryType.CUSTOM:
                struct = query.get_structure()
                if "nvds-ntp-sync" == struct.get_name():
                    _, data._live_stream_ntp_epoch = struct.get_uint64("ntp-time-epoch-ns")
                    _, data._live_stream_ntp_pts = struct.get_uint64("frame-timestamp")
            return Gst.PadProbeReturn.OK

        appsink = Gst.ElementFactory.make("appsink")
        appsink.set_property("async", False)
        appsink.set_property("sync", False)
        appsink.set_property("enable-last-sample", False)
        appsink.set_property("emit-signals", True)
        self._connect_gst_signal(appsink, "new-sample", on_new_sample)
        pipeline.add(appsink)

        if self._audio_support:
            self._audio_appsink = Gst.ElementFactory.make("appsink")
            self._audio_appsink.set_property("async", False)
            self._audio_appsink.set_property("sync", False)
            self._audio_appsink.set_property("enable-last-sample", False)
            self._audio_appsink.set_property("emit-signals", True)
            self._connect_gst_signal(self._audio_appsink, "new-sample", on_new_sample_audio)
            pipeline.add(self._audio_appsink)

        if uridecodebin:
            self._connect_gst_signal(uridecodebin, "pad-added", cb_newpad_decodebin, self)

        def cb_autoplug_continue(bin, pad, caps, udata):
            # Ignore audio
            return not caps.to_string().startswith("audio/")

        if not self._audio_support or not self._enable_audio:
            if uridecodebin:
                self._connect_gst_signal(
                    uridecodebin, "autoplug-continue", cb_autoplug_continue, None
                )

        def cb_select_stream(source, idx, caps):
            if "audio" in caps.to_string():
                return False
            return True

        def cb_before_send(rtspsrc, message, selff):
            """
            Callback function for the 'before-send' signal.

            This function is called before each RTSP request is sent. It checks if the
            message is a PAUSE command. If it is, the function returns False to skip
            sending the message. Otherwise, it returns True to allow the message to be sent.
            Skipping all msgs including: GstRtsp.RTSPMessage.PAUSE
            """
            logger.debug("selff._stop_stream = %s", selff._stop_stream)
            if selff._stop_stream:
                logger.debug(
                    "Intercepting stream:%s " "as we are trying to move pipeline to NULL", message
                )
                return False  # Skip sending the PAUSE message
            return True  # Allow sending the message

        if uridecodebin:
            self._connect_gst_signal(
                uridecodebin,
                "deep-element-added",
                lambda bin, subbin, elem, username=username, password=password, selff=self: cb_elem_added(  # noqa: E501
                    elem, username, password, selff
                ),
            )

        pad = videoconvert.get_static_pad("sink")

        def buffer_probe_event_eos(pad, info, data):
            # Probe callback function to send explicit EOS on audio path
            # Send EOS for image input (not self._audio_present) or
            # for RTSP input (wowza stream input needs this).
            event = info.get_event()

            if event.type == Gst.EventType.EOS:
                if self._audio_convert:
                    if not self._audio_present or self._is_live:
                        self._audio_convert.send_event(Gst.Event.new_eos())
            return Gst.PadProbeReturn.OK

        def buffer_probe_event(pad, info, data):
            # Probe callback function to pass chosen frames and drop other frames
            event = info.get_event()
            if event.type != Gst.EventType.CAPS:
                return Gst.PadProbeReturn.OK

            caps = event.parse_caps()
            struct = caps.get_structure(0)
            _, width = struct.get_int("width")
            _, height = struct.get_int("height")

            out_pad_width = 0
            out_pad_height = 0

            if self._image_aspect_ratio == "pad":
                pad_size = abs(width - height) // 2
                out_pad_width = pad_size if width < height else 0
                out_pad_height = pad_size if width > height else 0

            out_width = width + 2 * out_pad_width
            out_height = height + 2 * out_pad_height

            if self._shortest_edge is not None:
                shortest_edge = (
                    self._shortest_edge
                    if isinstance(self._shortest_edge, list)
                    else [self._shortest_edge, self._shortest_edge]
                )
                out_pad_width *= shortest_edge[0] / out_width
                out_pad_height *= shortest_edge[1] / out_height
                out_width, out_height = shortest_edge

            self._out_caps_filter.set_property(
                "caps",
                Gst.Caps.from_string(
                    f"video/x-raw(memory:NVMM), format=GBR, width={out_width}, height={out_height}"
                ),
            )

            if out_pad_width or out_pad_height:
                self._videoconvert.set_property(
                    "dest-crop",
                    (
                        f"{int(out_pad_width)}:{int(out_pad_height)}:"
                        f"{int(out_width-2*out_pad_width)}:{int(out_height-2*out_pad_height)}"
                    ),
                )
                self._videoconvert.set_property("interpolation-method", 1)

            return Gst.PadProbeReturn.OK

        if self._do_preprocess:
            # Event probe to calculate and set pre-processing params based on file resolution
            self._add_gst_pad_probe(
                pad, Gst.PadProbeType.EVENT_DOWNSTREAM, buffer_probe_event, self
            )

        # For live streams, all-frame file chunks, or if timestampfilter is unavailable,
        # use Python buffer_probe. For all-frame chunks this enforces the chunk end PTS
        # while timestampfilter's empty target list passes all frames through.
        use_python_buffer_probe = (
            self._is_live or self._frame_selector.selects_all_frames or not self._timestamp_filter
        )
        self._pipeline_has_file_buffer_probe = bool(not self._is_live and use_python_buffer_probe)
        if use_python_buffer_probe:
            self._add_gst_pad_probe(pad, Gst.PadProbeType.BUFFER, buffer_probe, self)
        if self._audio_convert:
            self._add_gst_pad_probe(
                pad, Gst.PadProbeType.EVENT_DOWNSTREAM, buffer_probe_event_eos, self
            )
        if self._is_live:
            self._add_gst_pad_probe(pad, Gst.PadProbeType.QUERY_DOWNSTREAM, cb_ntpquery, self)

        # Link timestampfilter between qvideoconvert and videoconvert for non-live streams
        if self._timestamp_filter:
            qvideoconvert.link(self._timestamp_filter)
            self._timestamp_filter.link(videoconvert)
        else:
            qvideoconvert.link(videoconvert)

        # Connect main pipeline elements, inserting tee if image saving is enabled
        if self._enable_image_save and hasattr(self, "_encoding_elements"):
            # Pipeline with tee for parallel encoding: videoconvert -> capsfilter -> tee -> main branch
            videoconvert.link(capsfilter)
            capsfilter.link(self._encoding_elements["tee"])

            self._encoding_elements["tee"].link(q2)
        else:
            # Original pipeline without tee
            videoconvert.link(capsfilter)
            if self._enable_jpeg_output:
                capsfilter.link(jpegenc)
                jpegenc.link(q2)
            else:
                capsfilter.link(q2)

        q2.link(appsink)

        def audio_buffer_probe(pad, info, data):
            # Probe callback function to pass chosen frames and drop other frames
            if not self._enable_audio:
                return Gst.PadProbeReturn.DROP

            if self._is_live:
                return Gst.PadProbeReturn.OK

            buffer = info.get_buffer()

            # Small overlap in audio chunks so that words are not missed
            audio_overlap = min(self._chunk_duration // 10, 5e9)

            if buffer.pts > self._end_pts + audio_overlap or buffer.pts < self._start_pts:
                return Gst.PadProbeReturn.DROP
            else:
                return Gst.PadProbeReturn.OK

        if self._audio_support:
            self._audio_q1.link(self._audio_convert)
            self._audio_convert.link(self._audio_capsfilter1)
            self._audio_capsfilter1.link(self._audio_resampler)
            self._audio_resampler.link(self._audio_capsfilter2)
            self._audio_capsfilter2.link(self._audio_appsink)

            audio_pad = self._audio_convert.get_static_pad("sink")
            self._add_gst_pad_probe(audio_pad, Gst.PadProbeType.BUFFER, audio_buffer_probe, self)

        self._loop = GLib.MainLoop()
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        self._bus = bus
        self._bus_signal_watch_added = True

        def bus_call(bus, message, selff):
            t = message.type
            if t == Gst.MessageType.EOS:
                # sys.stdout.write("End-of-stream\n")
                logger.debug("EOS received on bus")
                selff._audio_stop.set()
                selff._loop.quit()
            elif t == Gst.MessageType.WARNING:
                err, debug = message.parse_warning()

                # Ignore known harmless warnings
                if "Retrying using a tcp connection" in debug:
                    return True

                sys.stderr.write("Warning: %s: %s\n" % (err, debug))
            elif t == Gst.MessageType.ERROR:
                err, debug = message.parse_error()
                sys.stderr.write("Error: %s: %s\n" % (err, debug))
                with self._err_msg_lock:
                    self._err_msg = f"{err}:{debug}"
                selff._audio_stop.set()
                selff._loop.quit()
            return True

        self._connect_gst_signal(bus, "message", bus_call, self)
        return pipeline

    def flush_pipeline(self):
        if self._pipeline:
            time.sleep(0.01)
            send_event_result = self._pipeline.send_event(Gst.Event.new_flush_start())
            if not send_event_result:
                logger.warning("Failed to send flush start event")
            time.sleep(0.01)
            send_event_result = self._pipeline.send_event(Gst.Event.new_flush_stop(True))
            if not send_event_result:
                logger.warning("Failed to send flush stop event")
            time.sleep(0.01)

    def _set_element_null(self, element, description: str, timeout_ns: int = 5 * Gst.SECOND):
        if element is None:
            return
        try:
            element.set_state(Gst.State.NULL)
            ret, cur_state, _ = element.get_state(timeout_ns)
            if ret == Gst.StateChangeReturn.ASYNC:
                logger.warning(
                    "%s NULL teardown still ASYNC after %ds (cur=%s); "
                    "dropping reference and continuing",
                    description,
                    timeout_ns // Gst.SECOND,
                    cur_state,
                )
            elif ret == Gst.StateChangeReturn.FAILURE:
                logger.warning("%s NULL teardown failed (cur=%s)", description, cur_state)
        except Exception as ex:
            logger.warning("%s NULL teardown failed: %s", description, ex)

    def _set_pipeline_null_and_clear_refs(self):
        # Wait for the whole bin to reach NULL before clearing Python refs.
        # Otherwise GStreamer can dispose internal children like typefind or
        # decodebin while they are still PAUSED during error-path teardown.
        self._set_element_null(self._pipeline, "Pipeline")
        self._disconnect_gst_callbacks()
        for dec_name, dec in (
            ("H264 decoder", self._vdecodebin_h264),
            ("H265 decoder", self._vdecodebin_h265),
        ):
            # Cached decoders own a CUDA decoder context. Use a long
            # finite timeout (DECODER_TEARDOWN_TIMEOUT_NS, default 120 s):
            # short bounds let the Python ref drop while the context is
            # still alive under perf load, leaking the CUDA decoder
            # permanently — destroy_pipeline() runs on every retry, so
            # the leak compounds and OOMs the GPU as the workload
            # progresses. The 120 s bound is ~12x measured worst-case
            # teardown under 8x concurrent stress, so it is effectively
            # unbounded for healthy paths but still lets a worker
            # recover after 2 minutes if the decoder genuinely wedges.
            # The pipeline-level call above keeps the shorter 5 s bound
            # where hangs are more likely and don't leak CUDA.
            self._set_element_null(dec, dec_name, timeout_ns=DECODER_TEARDOWN_TIMEOUT_NS)
        self._pipeline = None
        self._clear_pipeline_elements()

    def destroy_pipeline(self):
        # Release cached CUDA frame tensors before pipeline teardown
        with self._file_frame_cache_lock:
            self._cached_frames = None
            self._cached_frames_pts = None
        self._cached_audio_frames = []
        self._cached_transcripts = []
        # Clear live stream frame selectors that may hold cached CUDA tensors
        with self._live_stream_frame_selectors_lock:
            self._live_stream_frame_selectors.clear()
        # Clean up dedicated CUDA copy stream
        if self._copy_stream is not None:
            self._copy_stream.synchronize()
            self._copy_stream = None
        self._live_stream_chunk_decoded_callback = None
        self._on_stream_error_callback = None
        self._set_pipeline_null_and_clear_refs()
        if self._gdino:
            self._gdino = None
        # Force PyTorch to return freed CUDA memory to the device
        gc.collect()
        torch.cuda.empty_cache()

    def _destroy_pipeline_before_current_decode(self):
        """Destroy a reused file pipeline without losing this chunk's cache.

        get_frames() creates a fresh cache for the current chunk before it
        knows whether a reusable pipeline must be rebuilt for a new file. The
        normal destroy path intentionally sets the cache to None so late
        callbacks from the old pipeline drop frames. For an in-flight rebuild,
        restore the fresh current-chunk cache after the old pipeline is gone so
        the new pipeline can append decoded frames instead of triggering a
        decode retry.
        """
        with self._file_frame_cache_lock:
            current_cached_frames = self._cached_frames
            current_cached_frames_pts = self._cached_frames_pts
            self._cached_frames = None
            self._cached_frames_pts = None

        try:
            self.destroy_pipeline()
        finally:
            with self._file_frame_cache_lock:
                if current_cached_frames is not None and current_cached_frames_pts is not None:
                    self._cached_frames = current_cached_frames
                    self._cached_frames_pts = current_cached_frames_pts

    # Debug functionality
    # Dump cached frames
    def _clear_pipeline_elements(self):
        self._bus = None
        self._loop = None
        self._vdecodebin_h264 = None
        self._vdecodebin_h265 = None
        self._vdecodebin = None
        self._adecodebin = None
        self._idecodebin = None
        self._uridecodebin = None
        self._filesrc = None
        self._parsebin = None
        self._rtspsrc = None
        self._udpsrc = None
        self._nvtracker = None
        self._nvstreammux = None
        self._q1 = None
        self._q2 = None
        self._q3 = None
        self._q4 = None
        self._q5 = None
        self._q6 = None
        self._tee = None
        self._splitmuxsink = None
        self._preview_valve = None
        self._videoconvert = None
        self._out_caps_filter = None
        self._audio_convert = None
        self._audio_resampler = None
        self._audio_capsfilter1 = None
        self._audio_capsfilter2 = None
        self._audio_appsink = None
        self._audio_q1 = None
        self._timestamp_filter = None
        if hasattr(self, "_encoding_elements"):
            self._encoding_elements = {}
        self._pipeline_has_file_buffer_probe = False

    def _wait_for_paused(
        self,
        pipeline,
        max_attempts: int = 20,
        timeout_per_attempt_ns: int = 10 * Gst.MSECOND,
    ) -> bool:
        """Block until the pipeline is in PAUSED (or fails), handling ASYNC returns.

        Under concurrent pipeline starts on high-end GPUs (B200, H100) the
        PAUSED transition can stay ASYNC for several ms after ``set_state``
        returns. Polling ``get_state`` with a finite per-attempt timeout
        gives the pipeline time to settle without blocking us forever if it
        wedges. Without this, a subsequent ``seek_simple`` runs against a
        not-yet-settled pipeline and returns False, falling into the
        deferred-seek path and forcing playthrough decode of frames before
        ``start_pts``.

        Default is 20 × 10 ms = up to 200 ms total wait — empirically more
        than enough for the ASYNC window to close in the healthy case;
        bounded so a wedged element can't hang a worker.

        Returns True if the pipeline reached PAUSED (SUCCESS or NO_PREROLL),
        False if it failed or was still ASYNC after ``max_attempts``.
        """
        for _ in range(max_attempts):
            state_ret, cur_state, pending = pipeline.get_state(timeout_per_attempt_ns)
            if state_ret in (
                Gst.StateChangeReturn.SUCCESS,
                Gst.StateChangeReturn.NO_PREROLL,
            ):
                return True
            if state_ret == Gst.StateChangeReturn.FAILURE:
                logger.warning(
                    "Pipeline failed to reach PAUSED (ret=%s cur=%s pending=%s)",
                    state_ret,
                    cur_state,
                    pending,
                )
                return False
            # ASYNC: get_state already waited for timeout_per_attempt_ns,
            # so loop straight back into the next attempt without another
            # sleep — that would only delay the seek further.
        logger.warning(
            "Pipeline still ASYNC after %d × %dns; seek may be unreliable",
            max_attempts,
            timeout_per_attempt_ns,
        )
        return False

    def get_frames(
        self,
        chunk: ChunkInfo,
        frame_selector=None,
        enable_audio=False,
        request_id="",
        frame_width=None,
        frame_height=None,
        video_codec=None,
    ):
        """Get frames from a chunk

        Args:
            chunk (ChunkInfo): Chunk to get frames from

        Returns:
            (list[tensor], list[float]): List of tensors containing raw frames or jpeg images
                                         and a list of corresponding timestamps in seconds
        """
        with self._file_frame_cache_lock:
            self._cached_frames = []
            self._cached_frames_pts = []
        self._cached_audio_frames = []
        self._audio_eos = False
        self._audio_present = False
        self._enable_audio = enable_audio
        self._eos_sent = False
        self._end_pts = chunk.end_pts
        self._start_pts = chunk.start_pts
        self._chunk_duration = chunk.end_pts - chunk.start_pts
        self._chunkIdx = chunk.chunkIdx
        self._current_stream_id = getattr(chunk, "streamId", None)

        # Reset per-file GOP tracking state.
        self._parser_last_i_frame_pts = None
        self._parser_current_gop_has_target = True
        self._parser_estimated_gop_duration_ns = None
        self._frame_duration_ns = 0

        self._is_warmup = False if request_id else True
        with self._err_msg_lock:
            self._err_msg = None
        self._file_pipeline_reusable = True

        logger.debug("Audio ASR enabled: %d", enable_audio)

        if not frame_width:
            frame_width = self._frame_width_orig
        if not frame_height:
            frame_height = self._frame_height_orig

        frame_selector_backup = self._frame_selector
        if frame_selector:
            self._frame_selector = frame_selector
        self._frame_selector.set_chunk(chunk)

        if (
            self._pipeline
            and not self._is_live
            and self._frame_selector.selects_all_frames
            and not self._pipeline_has_file_buffer_probe
        ):
            logger.info(
                "Rebuilding file pipeline for all-frame chunk %s so Python buffer probe "
                "can enforce chunk end PTS",
                chunk,
            )
            self._destroy_pipeline_before_current_decode()

        old_pipeline = None
        # ";" in chunk.file denotes a list of files
        for file in chunk.file.split(";"):
            if video_codec:
                file_video_codec = video_codec
            else:
                file_video_codec = MediaFileInfo.get_info(file).video_codec

            is_image = file_video_codec in ["JPEG", "PNG"]

            is_codec_changed = self._last_video_codec != file_video_codec
            is_resolution_changed = (
                frame_width != self._previous_frame_width
                or frame_height != self._previous_frame_height
            )
            # Non-live file requests get per-request stream ids, so including
            # streamId here defeats decoder reuse and repeatedly hot-swaps
            # filesrc/parsebin for the same file under file-burst load.
            is_file_changed = self._last_stream_id != file

            def backup_decodebin():
                # If codec or resolution has changed, remove the decodebin from the pipeline
                # and keep the decodebin backed up if old codec is h264 or h265
                if self._pipeline:

                    if self._vdecodebin:
                        self._pipeline.remove(self._vdecodebin)
                        if self._vdecodebin not in [self._vdecodebin_h264, self._vdecodebin_h265]:
                            self._vdecodebin.set_state(Gst.State.NULL)
                self._vdecodebin = None

            if (is_codec_changed or is_resolution_changed) and self._pipeline:
                backup_decodebin()
                old_pipeline = self._pipeline
                self._pipeline = None
                self._vdecodebin = None
                if not (self._frame_width and self._frame_height):
                    # Next pipeline should use same resolution as first
                    # to allow all frames in the chunk have same resolution
                    self._frame_width = self._first_frame_width
                    self._frame_height = self._first_frame_height

            # If the actual file changed, rebuild the pipeline. Hot-swapping
            # filesrc/parsebin in a paused pipeline is prone to qtdemux
            # not-linked errors under file-burst concurrency.
            if self._pipeline and (
                is_file_changed or (self._enable_audio and self._adecodebin is None)
            ):
                self._destroy_pipeline_before_current_decode()
            else:
                if self._adecodebin and self._enable_audio:
                    self._audio_present = True

            self._last_stream_id = file
            self._frame_width = frame_width
            self._frame_height = frame_height
            self._previous_frame_width = frame_width
            self._previous_frame_height = frame_height
            self._last_video_codec = file_video_codec

            if not self._pipeline:
                self._pipeline = self._create_pipeline(file)
                # New pipeline starts at byte 0; loop hasn't run yet.
                self._pipeline_has_streamed = False

            pipeline = self._pipeline

            # Set start/end time in the file based on chunk info.
            start_pts = chunk.start_pts - chunk.pts_offset_ns

            timestamps_str = ""
            if frame_selector:
                timestamps_str = ",".join(
                    str(int(pts)) for pts in frame_selector._selected_pts_array
                )
            else:
                timestamps_str = ",".join(
                    str(int(pts)) for pts in self._frame_selector._selected_pts_array
                )
            if self._timestamp_filter:
                self._timestamp_filter.set_property("timestamps", timestamps_str)

            # Reset seek tracking for this chunk
            self._seek_done = False
            self._seek_triggered = False
            self._seek_position = int(start_pts)
            if self._seek_probe_id is not None and self._timestamp_filter:
                # Remove old probe if exists
                sink_pad = self._timestamp_filter.get_static_pad("sink")
                sink_pad.remove_probe(self._seek_probe_id)
                self._seek_probe_id = None

            # Try to seek now (works if pipeline already linked).
            # Wait until the pipeline is truly in PAUSED before seeking —
            # under load the bindings can return SUCCESS while still ASYNC
            # and the seek would race with the state transition.
            pipeline.set_state(Gst.State.PAUSED)
            # Return value is intentionally ignored: a False (still ASYNC
            # or FAILURE after the bounded wait) just means the upcoming
            # seek_simple will likely fail and we'll fall into the
            # deferred-seek + retry path, which is the correct fallback.
            self._wait_for_paused(pipeline)

            seek_result = True

            # Skip the upfront seek only when start_pts == 0 AND the
            # pipeline has not yet streamed (loop.run hasn't executed).
            # A streamed-then-reused pipeline may be parked at EOS or a
            # later segment from the prior decode and must be rewound,
            # even when the chunk starts at 0. Skipping the no-op
            # seek-to-0 on a fresh pipeline avoids the PAUSED-state
            # race that triggers the deferred-seek fallback path.
            if _should_issue_initial_seek(
                is_image=is_image,
                start_pts=start_pts,
                pipeline_has_streamed=self._pipeline_has_streamed,
            ):
                seek_result = pipeline.seek_simple(
                    Gst.Format.TIME,
                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT | Gst.SeekFlags.SNAP_BEFORE,
                    start_pts,
                )

            if seek_result:
                logger.debug("Seek successful to %.2fs in get_frames()", start_pts / 1e9)
                self._seek_done = True
            else:
                logger.debug(
                    "Seek failed in get_frames(), installing buffer probe on timestampfilter"
                )

                # Add buffer probe on timestampfilter sink pad - will seek when first buffer arrives
                _seek_attempts = [0]
                _seek_max_attempts = 5
                _seek_async_waits = [0]
                _seek_max_async_waits = 100
                _seek_flags = (
                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT | Gst.SeekFlags.SNAP_BEFORE
                )

                def seek_probe_callback(
                    pad,
                    _info,
                    user_data,
                    _seek_attempts=_seek_attempts,
                    _seek_max_attempts=_seek_max_attempts,
                    _seek_async_waits=_seek_async_waits,
                    _seek_max_async_waits=_seek_max_async_waits,
                    _seek_flags=_seek_flags,
                ):
                    selff = user_data
                    if not selff._seek_done:
                        logger.debug(
                            "First buffer reached timestampfilter, seeking to %.2f",
                            selff._seek_position / 1e9,
                        )

                        # Defer seek to idle/timeout callback to avoid deadlock.
                        # Seeking with FLUSH inside a probe callback can cause
                        # pipeline to hang.
                        # IMPORTANT: do_seek runs on the GLib main loop thread
                        # (same thread as self._loop.run()). Never call
                        # time.sleep() here — blocking the main loop prevents
                        # EOS/state-change bus messages from being dispatched,
                        # which causes the seek itself to keep failing and the
                        # loop to never quit (request hangs). Retry by
                        # rescheduling via GLib.timeout_add so the main loop
                        # can breathe between attempts.
                        def do_seek(pipeline=pipeline, pad=pad):
                            # Do not burn retry attempts while the state transition
                            # is still ASYNC. This callback runs on the GLib main
                            # loop, so reschedule instead of blocking it.
                            state_ret, _, _ = pipeline.get_state(0)
                            if (
                                state_ret == Gst.StateChangeReturn.ASYNC
                                and _seek_async_waits[0] < _seek_max_async_waits
                            ):
                                _seek_async_waits[0] += 1
                                GLib.timeout_add(10, do_seek)
                                return False
                            if (
                                state_ret == Gst.StateChangeReturn.ASYNC
                                and _seek_async_waits[0] == _seek_max_async_waits
                            ):
                                logger.warning(
                                    "Pipeline still ASYNC after %d deferred-seek waits; "
                                    "attempting seek anyway",
                                    _seek_max_async_waits,
                                )
                                _seek_async_waits[0] += 1
                            seek_result = pipeline.seek_simple(
                                Gst.Format.TIME, _seek_flags, selff._seek_position
                            )
                            if seek_result:
                                logger.debug("Seek successful in deferred callback")
                                selff._seek_done = True
                                # Remove probe now that we've actually seeked —
                                # subsequent post-seek buffers must reach
                                # timestampfilter normally.
                                if selff._seek_probe_id is not None:
                                    pad.remove_probe(selff._seek_probe_id)
                                    selff._seek_probe_id = None
                            else:
                                _seek_attempts[0] += 1
                                if _seek_attempts[0] < _seek_max_attempts:
                                    # Reschedule after 10 ms — non-blocking so
                                    # the main loop can process pipeline
                                    # events between attempts. Most failed
                                    # seeks recover within 1-2 retries.
                                    GLib.timeout_add(10, do_seek)
                                else:
                                    # Out of retries: remove the probe so buffers can
                                    # drain. If the pipeline is still before the target,
                                    # timestampfilter can recover by playthrough. If it
                                    # is already at or past the target, playthrough cannot
                                    # produce the requested frames, so fail the attempt and
                                    # let the caller rebuild the pipeline.
                                    selff._seek_done = True
                                    if selff._seek_probe_id is not None:
                                        pad.remove_probe(selff._seek_probe_id)
                                        selff._seek_probe_id = None
                                    try:
                                        position_ok, current_position = pipeline.query_position(
                                            Gst.Format.TIME
                                        )
                                    except Exception:
                                        position_ok = False
                                        current_position = None
                                    if not position_ok:
                                        current_position = None
                                    if not _can_play_through_after_seek_failure(
                                        seek_position=selff._seek_position,
                                        pipeline_has_streamed=selff._pipeline_has_streamed,
                                        current_position=current_position,
                                    ):
                                        err_msg = (
                                            "Deferred seek to "
                                            f"{selff._seek_position / 1e9:.2f}s failed"
                                        )
                                        logger.error(
                                            "%s; current position is %.2fs, retrying with "
                                            "a rebuilt pipeline",
                                            err_msg,
                                            (
                                                current_position / 1e9
                                                if current_position is not None
                                                else -1
                                            ),
                                        )
                                        with selff._err_msg_lock:
                                            selff._err_msg = err_msg
                                        selff._audio_stop.set()
                                        selff._loop.quit()
                                    else:
                                        logger.warning(
                                            "Deferred seek to %.2fs failed after %d retries; "
                                            "abandoning seek and relying on timestamp filtering",
                                            selff._seek_position / 1e9,
                                            _seek_max_attempts,
                                        )
                            return False  # Remove this idle/timeout source

                        if not selff._seek_triggered:
                            GLib.idle_add(do_seek)
                            selff._seek_triggered = True

                    # Drop this buffer — it has old segment info from before
                    # the seek. Fresh post-seek buffers with correct segment
                    # will follow once do_seek succeeds. The probe stays
                    # installed until do_seek removes it on success.
                    return Gst.PadProbeReturn.DROP

                if self._timestamp_filter:
                    sink_pad = self._timestamp_filter.get_static_pad("sink")
                    self._seek_probe_id = self._add_gst_pad_probe(
                        sink_pad, Gst.PadProbeType.BUFFER, seek_probe_callback, self
                    )
                    logger.debug(
                        "Installed buffer probe (id=%d) on timestampfilter sink pad",
                        self._seek_probe_id,
                    )

            # Set the pipeline to PLAYING and wait for end-of-stream or error
            pipeline.set_state(Gst.State.PLAYING)
            with TimeMeasure("Decode "):
                self._loop.run()
            # Pipeline has now advanced past position 0 — any subsequent
            # reuse of this fgetter needs a real seek to rewind, even for
            # a chunk-0 request.
            self._pipeline_has_streamed = True
            pipeline.set_state(Gst.State.PAUSED)
            self._file_pipeline_reusable = self._wait_for_paused(pipeline)
            if old_pipeline:
                self._set_element_null(old_pipeline, "Replaced pipeline")

        with self._err_msg_lock:
            has_error = self._err_msg is not None
        if has_error:
            self._set_pipeline_null_and_clear_refs()

        # Return the cached raw preprocessed frames / jpegs and the corresponding timestamps.
        # Freeze the lists before preprocessing. GStreamer can still deliver
        # late appsink callbacks after EOS/error teardown; those callbacks now
        # drop frames instead of mutating this chunk's completed cache.
        with self._file_frame_cache_lock:
            cached_frames = self._cached_frames or []
            cached_frames_pts = self._cached_frames_pts or []
            self._cached_frames = None
            self._cached_frames_pts = None

        # Adjust for the PTS offset if any.
        cached_frames_pts = [t + chunk.pts_offset_ns / 1e9 for t in cached_frames_pts]

        for audio_frame in self._cached_audio_frames:
            audio_frame["start"] += chunk.pts_offset_ns / 1e9
            audio_frame["end"] += chunk.pts_offset_ns / 1e9

        # reset frame resoulution config after processing multiple files
        self._frame_width = self._frame_width_orig
        self._frame_height = self._frame_height_orig
        self._first_frame_width = 0
        self._first_frame_height = 0
        logger.debug(
            "sampled frame num: %d, chunk: %s, gpu_id: %d",
            len(cached_frames),
            chunk,
            self._gpu_id,
        )
        if len(cached_frames) == 0:
            logger.warning("No frames found for chunk %s", chunk)
        preprocessed_frames = self._preprocess(cached_frames)
        self._frame_selector = frame_selector_backup

        with self._err_msg_lock:
            err_msg = self._err_msg
        return (
            preprocessed_frames,
            cached_frames_pts,
            self._cached_audio_frames,
            err_msg,
        )

    def dispose_pipeline(self):
        if self._pipeline.set_state(Gst.State.NULL) != Gst.StateChangeReturn.SUCCESS:
            logger.error("Couldn't set state to NULL for pipeline")
        logger.info("Pipeline moved to NULL")

    def dispose_pipeline_from_separate_thread(self):
        """Safely move pipeline to NULL state and clean up resources."""

        # Create a flag to track completion
        self._disposal_complete = False

        def disposal_thread():
            """Thread function to handle pipeline disposal"""
            try:
                logger.debug("Starting pipeline disposal in separate thread")
                self.dispose_pipeline()
                self._disposal_complete = True
                logger.debug("Pipeline disposal completed")
            except Exception as e:
                logger.debug("Error during pipeline disposal: %s", e)
                self._disposal_complete = True  # Mark as complete even on error

        # Start disposal thread
        disposal_thread = threading.Thread(target=disposal_thread, daemon=True)
        disposal_thread.start()

        # Wait for disposal to complete with timeout
        timeout = 120  # Total timeout in seconds
        start_time = time.time()
        while not self._disposal_complete:
            if time.time() - start_time > timeout:
                logger.error("ERROR: Pipeline disposal timed out after %d seconds", timeout)
                break
            time.sleep(2)
            logger.debug("Waiting for pipeline disposal to complete...")

    def dispose_source(self, src):
        if src.set_state(Gst.State.NULL) != Gst.StateChangeReturn.SUCCESS:
            logger.error("Couldn't set state to NULL for %s", self._uridecodebin.get_name())
        logger.info("Source removed")

    def stream(
        self,
        live_stream_url: str,
        chunk_duration: int,
        on_chunk_decoded: Callable[
            [
                ChunkInfo,
                torch.Tensor | list[np.ndarray],  # frames
                list[float],  # frame_times
                list[dict],  # transcripts
                Optional[str],  # error_msg
                float,  # decode_start_time
                float,  # decode_end_time
                dict,  # kwargs
            ],
            None,
        ],
        chunk_overlap_duration=0,
        username="",
        password="",
        enable_audio=False,
        use_vlm_audio=False,
        live_stream_id="",
        on_stream_error_callback: Optional[Callable[[str, str, int], None]] = None,
    ):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        self._last_stream_id = ""

        self._live_stream_frame_selectors.clear()
        self._live_stream_url = live_stream_url
        self._live_stream_next_chunk_idx = 0
        self._live_stream_chunk_duration = chunk_duration
        self._live_stream_chunk_overlap_duration = chunk_overlap_duration
        self._live_stream_chunk_decoded_callback = on_chunk_decoded
        self._on_stream_error_callback = on_stream_error_callback
        self._last_frame_pts = 0
        self._stop_stream = False
        self._enable_audio = enable_audio
        self._use_vlm_audio = use_vlm_audio and enable_audio
        self._is_warmup = False
        self._current_stream_id = live_stream_id

        if live_stream_id:
            self._live_stream_request_id = live_stream_id
        else:
            self._live_stream_request_id = str(uuid.uuid4())
        # Rerun the pipeline if it runs into errors like disconnection
        # Stop if pipeline stops with EOS
        while not self._stop_stream:
            current_time = time.time()
            with self._err_msg_lock:
                has_error = self._err_msg is not None
            if not self._pipeline or has_error:
                with self._err_msg_lock:
                    if self._err_msg is not None:
                        error_message = (
                            f"Live stream received error. Retrying after "
                            f"{self._reconnection_interval} seconds, "
                            f"attempt {self._reconnection_attempt_count}: {self._err_msg}"
                        )
                        logger.error(error_message)

                        # Send error to Kafka via callback
                        if self._on_stream_error_callback:
                            self._on_stream_error_callback(
                                error_message,
                                live_stream_id,
                                self._reconnection_attempt_count,
                            )

                        if self._error_first_detected_time is None:
                            self._error_first_detected_time = time.time()
                            self._reconnection_attempt_count = 0
                        else:
                            self._reconnection_attempt_count += 1
                        if self._reconnection_attempt_count >= self._reconnection_max_attempts or (
                            current_time - self._error_first_detected_time
                            > self._reconnection_timeout
                        ):
                            final_error_message = (
                                f"Live stream received error. Max reconnection attempts "
                                f"{self._reconnection_attempt_count}, "
                                f"timeout {self._reconnection_timeout} seconds: {self._err_msg}"
                            )
                            logger.error(final_error_message)

                            # Send final error to Kafka via callback
                            if self._on_stream_error_callback:
                                self._on_stream_error_callback(
                                    final_error_message,
                                    live_stream_id,
                                    self._reconnection_attempt_count,
                                )

                            self._stop_stream = True
                            break

                        time.sleep(self._reconnection_interval)
                        self._err_msg = None
            else:
                break
            self._live_stream_next_chunk_start_pts = 0
            self._audio_current_pts = 0
            self._audio_present = False
            self._audio_eos = False
            self._enable_audio = enable_audio
            self._audio_start_pts = None
            self._audio_stop.clear()
            self._audio_error.clear()
            self._asr_process_finished.clear()
            self._live_stream_ntp_epoch = 0
            self._live_stream_ntp_pts = 0
            self._cached_transcripts = []

            self._pipeline = self._create_pipeline(live_stream_url, username, password)

            # Start input, output audio ASR in a separate process if audio is enabled,
            # audio stream is present, and VLM does not handle audio natively.
            if enable_audio and not use_vlm_audio:

                def start_asr_threads():
                    self._asr_input_queue = mp.Queue()
                    self._asr_output_queue = mp.Queue()
                    self._asr_process = mp.Process(
                        target=streaming_audio_asr,
                        args=(
                            self._asr_input_queue,
                            self._asr_output_queue,
                            self._asr_config_file,
                            self._audio_stop,
                            self._audio_error,
                            self._asr_process_finished,
                        ),
                    )

                    self._asr_input_thread = threading.Thread(
                        target=self._asr_input_thread, daemon=True
                    )
                    self._asr_output_thread = threading.Thread(
                        target=self._asr_output_thread, daemon=True
                    )
                    self._asr_input_thread.start()
                    self._asr_process.start()
                    self._asr_output_thread.start()

                def wait_and_start_asr():
                    while not self._audio_present and not self._audio_stop.is_set():
                        with self._audio_present_cv:
                            self._audio_present_cv.wait()

                    if self._audio_present:
                        start_asr_threads()

                # Wait for audio stream to be found and then start ASR threads
                threading.Thread(target=wait_and_start_asr, daemon=True).start()

            logger.debug("Pipeline for live stream to PLAYING")
            self._pipeline.set_state(Gst.State.PLAYING)
            logger.debug("Pipeline for live stream to loop.run")
            self._loop.run()

            # Wait for audio streaming thread to complete
            if enable_audio and self._audio_present and not use_vlm_audio:
                logger.debug("Waiting for audio streaming threads to complete")
                self._audio_stop.set()
                self._asr_input_thread.join()
                self._asr_process.join()
                self._asr_process_finished.set()
                self._asr_output_thread.join()

                self._asr_input_queue.close()
                self._asr_output_queue.close()
            else:
                # exit the audio streaming check thread (or VLM-audio mode with no ASR threads)
                self._audio_stop.set()
                with self._audio_present_cv:
                    self._audio_present_cv.notify()

            if self._rtspsrc:
                logger.debug("forcing EOS; %s", self._last_stream_id)
                # Send EOS event to the source
                handled = self._rtspsrc.send_event(Gst.Event.new_eos())
                if self._nvtracker:
                    self._nvtracker.send_event(Gst.Event.new_eos())
                # time.sleep(1)
                logger.debug("EOS forced; %s : %s", handled, self._last_stream_id)
                self._rtspsrc.set_property("timeout", 0)
                # BCD max-stream runs use a large RTSP jitter buffer to avoid packet
                # drops. Do not let that buffer length extend teardown.
                try:
                    self._rtspsrc.set_property("latency", 0)
                except TypeError:
                    logger.debug("rtspsrc latency property could not be reset during teardown")
                if self._udpsrc:
                    logger.debug(
                        "forcing udpsrc timeout to 0 before teardown; %s", self._last_stream_id
                    )
                    self._udpsrc.set_property("timeout", 0)

            if self._pipeline:
                try:
                    self._pipeline.send_event(Gst.Event.new_flush_start())
                    self._pipeline.send_event(Gst.Event.new_flush_stop(False))
                except Exception as ex:
                    logger.debug("Failed to flush live pipeline before teardown: %s", ex)

            # Need to remove source bin and then move pipeline to NULL
            # to avoid Gst bug:
            # https://discourse.gstreamer.org/t/gstreamer-1-16-3-setting-rtsp-pipeline-to-null/538/11
            # TODO: Try latest GStreamer version for any fixes
            logger.debug("pipe teardown: unlink_source : %s", self._last_stream_id)
            if self._tee is not None:
                self._uridecodebin.unlink(self._tee)
            else:
                self._uridecodebin.unlink(self._q1)

            if self._audio_q1 is not None:
                self._uridecodebin.unlink(self._audio_q1)
            self._pipeline.remove(self._uridecodebin)

            # logger.debug(f"pipe teardown: to READY : {self._last_stream_id}")
            # self._pipeline.set_state(Gst.State.READY)
            # time.sleep(1)
            logger.debug("pipe teardown: to NULL : %s", self._last_stream_id)
            self.dispose_pipeline_from_separate_thread()
            logger.debug("pipe teardown: dispose_source : %s", self._last_stream_id)
            # The main loop has already quit here; GLib.idle_add would not run
            # reliably and leaves RTSP source elements alive across Phase 2 probes.
            if self._rtspsrc:
                self._set_element_null(self._rtspsrc, "RTSP source")
            if self._uridecodebin:
                self._set_element_null(self._uridecodebin, "URI decodebin")
            logger.debug("pipe teardown: done : %s", self._last_stream_id)
            self._process_finished_chunks(flush=True)

        self._disconnect_gst_callbacks()
        self._pipeline = None
        self._clear_pipeline_elements()
        self._live_stream_frame_selectors.clear()
        self._live_stream_chunk_decoded_callback = None
        self._on_stream_error_callback = None
        if self._copy_stream is not None:
            self._copy_stream.synchronize()
            self._copy_stream = None
        # Release fragmented CUDA memory after stream teardown
        gc.collect()
        torch.cuda.empty_cache()

    def stop_stream(self):
        self._stop_stream = True
        logger.debug("Force quit loop")
        self._audio_stop.set()
        if self._loop is not None:
            self._loop.quit()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Video File Frame Getter")
    parser.add_argument("file_or_rtsp", type=str, help="File / RTSP streams to frames from")

    parser.add_argument(
        "--chunk-duration",
        type=int,
        default=10,
        help="Chunk duration in seconds to use for live streams",
    )
    parser.add_argument(
        "--chunk-overlap-duration",
        type=int,
        default=0,
        help="Chunk overlap duration in seconds to use for live streams",
    )
    parser.add_argument(
        "--username", type=str, default=None, help="Username to access the live stream"
    )
    parser.add_argument(
        "--password", type=str, default=None, help="Password to access the live stream"
    )

    parser.add_argument(
        "--start-time", type=float, default=0, help="Start time in sec to get frames from"
    )

    parser.add_argument(
        "--end-time", type=float, default=-1, help="End time in sec to get frames from"
    )

    parser.add_argument("--num-frames", type=int, default=8, help="Number of frames to get")
    parser.add_argument(
        "--use-fps-for-chunking",
        action="store_true",
        default=False,
        help=(
            "Use FPS for chunking. If True, num-frames is interpreted as FPS for sampling frames, "
            "else as fixed number of frames per chunk"
        ),
    )
    parser.add_argument("--gpu-id", type=int, default=0, help="gpu id")

    parser.add_argument(
        "--enable-jpeg-output",
        type=bool,
        default=False,
        help="enable JPEG output instead of NVMM:x-raw",
    )

    parser.add_argument(
        "--enable-audio",
        type=bool,
        default=False,
        help="enable audio transcription using RIVA ASR",
    )

    args = parser.parse_args()

    frame_getter = VideoFileFrameGetter(
        frame_selector=DefaultFrameSelector(
            num_frames_per_second_or_fixed_frames=args.num_frames,
            use_fps_for_chunking=args.use_fps_for_chunking,
        ),
        gpu_id=args.gpu_id,
        enable_jpeg_output=args.enable_jpeg_output,
        audio_support=args.enable_audio,
    )

    if args.file_or_rtsp.startswith("rtsp://"):
        frame_getter.stream(
            args.file_or_rtsp,
            chunk_duration=args.chunk_duration,
            chunk_overlap_duration=args.chunk_overlap_duration,
            username=args.username,
            password=args.password,
            on_chunk_decoded=lambda chunk, frames, frame_times, transcripts, error_msg, kwargs: print(
                f"Picked {len(frames)} frames with times: {frame_times} \
                for chunk {chunk}\n audio transcripts\n: {transcripts}\n\n\n"
            ),
            enable_audio=args.enable_audio,
        )
    else:
        chunk = ChunkInfo()
        chunk.file = args.file_or_rtsp
        chunk.start_pts = args.start_time * 1000000000
        chunk.end_pts = args.end_time * 1000000000 if args.end_time >= 0 else -1
        frames, frames_pts, audio_frames, error = frame_getter.get_frames(
            chunk, enable_audio=args.enable_audio
        )
        print(f"Picked {len(frames)} frames with times: {frames_pts}")
