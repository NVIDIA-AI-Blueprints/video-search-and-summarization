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
"""Video File Frame Getter

This module supports getting frames from a video file either as raw frame tensors or
JPEG encoded images. Supports decoding of a part of file using start/end timestamps,
picking N frames from the segment as well as pre-processing the decoded frames
as required by the VLM model.
"""

import ctypes
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from threading import Condition, Event, Lock
from typing import Callable

import cupy as cp
import cv2
import gi
import grpc
import gst_video_sei_meta
import numpy as np
import pyds
import riva.client
import torch
import yaml
from torchvision.transforms import v2

from chunk_info import ChunkInfo
from utils import JsonCVMetadata, MediaFileInfo, get_json_file_name
from via_logger import TimeMeasure, logger

gi.require_version("Gst", "1.0")

from gi.repository import GLib, Gst  # noqa: E402

Gst.init(None)

if os.environ.get("FORCE_SW_AV1_DECODER", "false") == "true":
    av1dec = Gst.ElementFactory.find("av1dec")
    if av1dec:
        current_rank = av1dec.get_rank()
        # Update av1dec rank above nvv4l2decoder
        new_rank = current_rank + 20
        av1dec.set_rank(new_rank)
        logger.info("Updated rank of %s from %d to %d", av1dec.get_name(), current_rank, new_rank)

UNTRACKED_OBJECT_ID = 0xFFFFFFFFFFFFFFFF
np.random.seed(1000)
rgb_array = np.random.random((1000, 3))


def get_timestamp_str(ts):
    """Get RFC3339 string timestamp"""
    return (
        datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        + f".{(int(ts * 1000) % 1000):03d}Z"
    )


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

    def __init__(self, num_frames=8):
        """Default initializer.

        Args:
            num_frames (int, optional): Number of frames to select from a chunk. Defaults to 8.
        """
        self._num_frames = num_frames
        self._selected_pts_array = []

    def set_chunk(self, chunk: ChunkInfo):
        self._chunk = chunk
        self._selected_pts_array = []
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

        # Calculate PTS for N equally spaced frames
        pts_diff = (end_pts - start_pts) / self._num_frames
        for i in range(self._num_frames):
            self._selected_pts_array.append(start_pts + i * pts_diff)
        logger.debug(f"Selected PTS = {self._selected_pts_array} for {chunk}")

    def choose_frame(self, buffer, pts):
        # Choose the frame if it's PTS is more than the next sampled PTS in the
        # list.
        if (
            len(self._selected_pts_array)
            and pts >= self._selected_pts_array[0]
            and pts <= self._chunk.end_pts
        ):
            while len(self._selected_pts_array) and pts >= self._selected_pts_array[0]:
                self._selected_pts_array.pop(0)
            return True
        if pts >= self._chunk.end_pts:
            self._selected_pts_array.clear()
        return False


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
        self._selected_pts_array = []
        self._last_gst_buffer = None
        self._loop = None
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
        self._live_stream_frame_selectors: dict[BaseFrameSelector, any] = {}
        self._live_stream_frame_selectors_lock = Lock()
        self._audio_start_cv = Condition()
        self._audio_end_cv = Condition()
        self._live_stream_audio_transcripts_lock = Lock()
        self._live_stream_next_chunk_start_pts = 0
        self._audio_current_pts = 0
        self._live_stream_next_chunk_idx = 0
        self._live_stream_chunk_duration = 0
        self._live_stream_chunk_overlap_duration = 0
        self._live_stream_ntp_epoch = 0
        self._live_stream_ntp_pts = 0
        self._live_stream_request_id = 0
        self._output_cv_metadata = None
        self._dump_cached_frames = False
        self._live_stream_chunk_decoded_callback: Callable[
            [ChunkInfo, torch.Tensor | list[np.ndarray], list[float], list[dict]], None
        ] = None
        self._first_frame_width = 0
        self._first_frame_height = 0
        self._got_error = False
        self._previous_frame_width = 0
        self._previous_frame_height = 0
        self._destroy_pipeline = False
        self._last_frame_pts = 0
        self._uridecodebin = None
        self._rtspsrc = None
        self._udpsrc = None
        self._audio_eos = False
        self._audio_stop = Event()
        self._audio_start_pts = None
        self._audio_frames_lock = Lock()
        self._audio_present = False
        self._eos_sent = False
        self._end_pts = None
        self._chunk_duration = None
        self._audio_convert = None
        self._audio_resampler = None
        self._audio_capsfilter1 = None
        self._audio_capsfilter2 = None
        self._audio_appsink = None
        self._audio_q1 = None
        self._model_name = None
        self._server_uri = None
        self._riva_nim_server = True
        self._asr_config_file = "/tmp/via/riva_asr_grpc_conf.yaml"
        self._server_config = None
        self._asr_config = None
        self._auth = None
        self._tee = None
        self._nvtracker = None
        self._cached_transcripts = []
        self._cached_audio_frames = []
        self._cv_pipeline_configs = cv_pipeline_configs
        self._gdino = None
        self._gdino_engine = None
        # Mask formatting related configs
        self._mask_border_width = 5
        self._center_text_on_object = True
        self._draw_bbox = False
        self._fill_mask = True
        self._pipeline_width = 0
        self._pipeline_height = 0
        self._splitmuxsink = None
        self._cached_frames_cv_meta = []  # List of cached frames cv meta for each chunk
        if "gdino_engine" in self._cv_pipeline_configs:
            self._gdino_engine = self._cv_pipeline_configs["gdino_engine"]
            if os.path.isfile(self._gdino_engine):
                from cv_pipeline.gsam_pipeline_trt_ds import cudaSetDevice

                cudaSetDevice(self._gpu_id)
                self._gdino = None
                # self._gdino = GroundingDino(
                #     trt_engine=self._gdino_engine, max_text_len=256, batch_size=1
                # )
                # memory_pool = cp.cuda.MemoryPool(cp.cuda.malloc_managed)
                # # Set the memory pool as the default allocator
                # cp.cuda.set_allocator(memory_pool.malloc)
                print(
                    f"Live Stream : Created gdino handle {self._gdino} \
                        for gdino engine {self._gdino_engine}"
                )

        self._tracker_config = "/opt/nvidia/deepstream/deepstream/samples\
                    /configs/deepstream-app/config_tracker_NvDCF_perf.yml"
        if "tracker_config" in self._cv_pipeline_configs:
            if os.path.isfile(self._cv_pipeline_configs["tracker_config"]):
                self._tracker_config = self._cv_pipeline_configs["tracker_config"]
        self._inference_interval = 1
        if "inference_interval" in self._cv_pipeline_configs:
            self._inference_interval = self._cv_pipeline_configs["inference_interval"]

        if self._audio_support:
            self._create_asr_service_auth()

    def _set_frame_resolution(self, frame_width, frame_height):
        if frame_width and (self._previous_frame_width != frame_width):
            self._previous_frame_width = self._frame_width
            self._frame_width = frame_width
            self._destroy_pipeline = True
        if frame_height and (self._previous_frame_height != frame_height):
            self._previous_frame_height = self._frame_height
            self._frame_height = frame_height
            self._destroy_pipeline = True

    def _preprocess(self, frames):
        if frames and not self._enable_jpeg_output:
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

    def _create_video_from_cached_frames(self, chunk_idx):
        def check_ffmpeg():
            """Check if FFmpeg is installed."""
            ffmpeg_path = shutil.which("ffmpeg_for_overlay_video")
            return ffmpeg_path is not None

        if self._is_live:
            video_path = f"{self._cached_frames_dir}/../{self._request_id}_{chunk_idx}.ts"
        else:
            video_path = f"{self._cached_frames_dir}/{self._request_id}_{chunk_idx}.ts"
        images_path = f"{self._cached_frames_dir}/frame_*.jpg"
        if os.path.exists(self._cached_frames_dir) and check_ffmpeg():
            # BN TBD : Need better way to handle this
            # calculate frame rate from number of frames and duration
            frame_count = len(
                [f for f in os.listdir(self._cached_frames_dir) if f.endswith(".jpg")]
            )
            frame_rate = frame_count / self._live_stream_chunk_duration
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
                *(["libx264", "-preset", "ultrafast"] if self._is_live else ["copy"]),
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

    def _process_finished_chunks(self, current_pts=None, flush=False):
        chunks_processed_fs = []

        for fs, (cached_pts, cached_frames) in self._live_stream_frame_selectors.items():
            if (
                (current_pts is not None and current_pts >= fs._chunk.end_pts)
                or len(fs._selected_pts_array) == 0
                or flush
            ):
                if len(cached_pts) == len(cached_frames) or flush:
                    self.dump_cached_frame(cached_frames, cached_pts, self._enable_jpeg_output)
                    cached_frames = self._preprocess(cached_frames)
                    base_time = (
                        self._live_stream_ntp_epoch - self._live_stream_ntp_pts
                    ) / 1000000000
                    if self._sei_base_time:
                        base_time = self._sei_base_time / 1000000000
                    if base_time == 0:
                        base_time = time.time() - (fs._chunk.end_pts / 1e9)
                    if flush and self._last_frame_pts >= fs._chunk.start_pts:
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

                    # write json metadata for current chunk
                    if self._output_cv_metadata:
                        json_file_name = get_json_file_name(
                            self._live_stream_request_id, fs._chunk.chunkIdx
                        )
                        self._output_cv_metadata.write_json_file(json_file_name)
                        fs._chunk.cv_metadata_json_file = json_file_name
                        # Return the cv meta to be passed to Graph RAG pipeline
                        fs._chunk.cached_frames_cv_meta = self._output_cv_metadata.get_cv_meta()
                        self._output_cv_metadata = None

                    # create video from cached frames
                    osd_output_video_file = None
                    if self._dump_cached_frames:
                        osd_output_video_file = self._create_video_from_cached_frames(
                            fs._chunk.chunkIdx
                        )
                        fs._chunk.osd_output_video_file = osd_output_video_file
                        print(f"OSD output video file: {osd_output_video_file}")

                    self._live_stream_chunk_decoded_callback(
                        fs._chunk, cached_frames, cached_pts, cached_transcripts
                    )
                    chunks_processed_fs.append(fs)

        for fs in chunks_processed_fs:
            self._live_stream_frame_selectors.pop(fs)

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

        h264parse_src_pad.add_probe(Gst.PadProbeType.BUFFER, on_h264parse_buffer)
        self._splitmuxsink = splitmuxsink
        return True

    class AudioChunkIterator:
        """Iterator that yields audio chunks from cached frames.

        Provides iteration over audio frames with thread-safe access to the underlying cache.
        Implements context manager protocol for proper resource cleanup.
        """

        def __init__(
            self,
            cached_audio_frames: list,
            audio_frames_lock: Lock,
            audio_stop: Event,
        ) -> None:
            """Initialize the iterator.

            Args:
                cached_audio_frames: List of cached audio frame dictionaries
                audio_frames_lock: Lock for thread-safe access to cached frames
                audio_stop: Event to signal when to stop iteration
            """
            self._cached_audio_frames = cached_audio_frames
            self._audio_frames_lock = audio_frames_lock
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
            with self._audio_frames_lock:
                if len(self._cached_audio_frames) > 0:
                    audio_frame = self._cached_audio_frames.pop(0)
                    if audio_frame is not None and audio_frame["audio"] is not None:
                        return audio_frame["audio"].tobytes()
                    return b""

            if self._audio_stop.is_set():
                logger.debug("Stopping audio chunk iterator")
                raise StopIteration

            # No frames available, wait briefly and retry
            time.sleep(0.03)
            return self.__next__()

    def _create_asr_service_auth(self):
        """Create ASR service channel"""
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

    def _streaming_audio_asr(self):
        """Send audio frames and receive text from ASR"""
        logger.info("Starting audio streaming thread")

        asr_service = riva.client.ASRService(self._auth)

        language_code = self._asr_config.get("language_code", "en-US")
        enable_automatic_punctuation = self._asr_config.get("enable_automatic_punctuation", True)
        profanity_filter = self._asr_config.get("profanity_filter", True)

        if self._riva_nim_server:
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
                model=self._model_name,
                profanity_filter=profanity_filter,
                verbatim_transcripts=False,
            )

        streaming_config = riva.client.StreamingRecognitionConfig(
            config=riva_asr_config, interim_results=False
        )

        audio_chunk_iterator = self.AudioChunkIterator(
            self._cached_audio_frames, self._audio_frames_lock, self._audio_stop
        )

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

                    if len(transcript) > 0:
                        start_time *= 1e6
                        end_time *= 1e6
                        start_time += self._audio_start_pts
                        end_time += self._audio_start_pts

                        with self._audio_end_cv:
                            self._audio_current_pts = start_time
                            self._audio_end_cv.notify()

                        with self._audio_start_cv:
                            if (
                                (start_time) > self._live_stream_next_chunk_start_pts
                                and not self._got_error
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

                except AttributeError as e:
                    logger.error(f"Invalid response format from ASR service: {e}")
                    self._got_error = True
                except Exception as e:
                    logger.error(f"Error processing ASR response: {e}")
                    self._got_error = True

        except grpc.RpcError as e:
            logger.error(f"gRPC error during ASR streaming: {e}")
            self._got_error = True
        except Exception as e:
            logger.error(f"Unexpected error during ASR streaming: {e}")
            self._got_error = True
        finally:
            audio_chunk_iterator.close()

        logger.debug("Exiting ASR streaming thread")
        with self._audio_end_cv:
            self._audio_end_cv.notify()

    def _create_pipeline(self, file_or_rtsp: str, username="", password=""):
        # Construct DeepStream pipeline for decoding
        # For raw frames as tensor:
        # uridecodebin -> probe (frame selector) -> nvvideconvert -> appsink
        #     -> frame pre-processing -> add to cache
        # For jpeg images:
        # uridecodebin -> probe (frame selector) -> nvjpegenc -> appsink -> add to cache
        # For audio: uridecodebin -> probe -> audioconvert ->
        # resample -> asr -> appsink -> add text_to cache
        self._is_live = file_or_rtsp.startswith("rtsp://")
        pipeline = Gst.Pipeline()

        uridecodebin = Gst.ElementFactory.make("uridecodebin")
        if self._is_live:
            uridecodebin.set_property("uri", file_or_rtsp)
        else:
            uridecodebin.set_property("uri", f"file://{os.path.abspath(file_or_rtsp)}")
        pipeline.add(uridecodebin)
        self._uridecodebin = uridecodebin
        # self._is_live = True

        self._q1 = Gst.ElementFactory.make("queue")
        pipeline.add(self._q1)

        qvideoconvert = Gst.ElementFactory.make("queue")
        pipeline.add(qvideoconvert)

        if self._is_live and not os.environ.get("VSS_DISABLE_LIVESTREAM_PREVIEW", ""):
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

        videoconvert.set_property("gpu-id", self._gpu_id)
        pipeline.add(videoconvert)

        if self._enable_jpeg_output:
            jpegenc = Gst.ElementFactory.make("nvjpegenc")
            pipeline.add(jpegenc)
            format = "RGB"  # only RGB/I420 supported by nvjpegenc
        else:
            format = "GBR" if self._do_preprocess else "RGB"
            pass
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
                video_sei_meta = gst_video_sei_meta.gst_buffer_get_video_sei_meta(buffer_address)

                if video_sei_meta:
                    sei_data = json.loads(video_sei_meta.sei_metadata_ptr)
                    buffer.pts = sei_data["sim_time"] * 1e9

                new_chunk = False
                if buffer.pts >= self._live_stream_next_chunk_start_pts:
                    with self._audio_end_cv:
                        if (
                            self._audio_present
                            and self._audio_current_pts < self._live_stream_next_chunk_start_pts
                            and not self._got_error
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
                        self._sei_data = json.loads(video_sei_meta.sei_metadata_ptr)
                        if self._sei_base_time is None:
                            self._sei_base_time = self._sei_data["timestamp"] - buffer.pts

                    if buffer.pts >= self._live_stream_next_chunk_start_pts:
                        fs = DefaultFrameSelector(self._frame_selector._num_frames)
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
                        self._live_stream_frame_selectors[fs] = ([], [])
                        self._live_stream_next_chunk_start_pts = (
                            chunk.end_pts - self._live_stream_chunk_overlap_duration * 1e9
                        )
                        self._live_stream_next_chunk_idx += 1
                        new_chunk = True

                    choose_frame = False
                    for fs, (
                        cached_pts,
                        cached_frames,
                    ) in self._live_stream_frame_selectors.items():
                        if fs.choose_frame(buffer, buffer.pts):
                            choose_frame = True
                            cached_pts.append(buffer.pts / 1e9)

                    self._process_finished_chunks(buffer.pts)

                if new_chunk:
                    with self._audio_start_cv:
                        self._audio_start_cv.notify()

                if choose_frame:
                    return Gst.PadProbeReturn.OK

            else:
                if self._frame_selector.choose_frame(buffer, buffer.pts):
                    return Gst.PadProbeReturn.OK
                if len(self._frame_selector._selected_pts_array) == 0 and not self._eos_sent:
                    if self._audio_present:
                        if self._audio_eos:
                            self._pipeline.send_event(Gst.Event.new_eos())
                            self._eos_sent = True
                    else:
                        self._pipeline.send_event(Gst.Event.new_eos())
                        if self._audio_convert:
                            self._audio_convert.send_event(Gst.Event.new_eos())
                        self._eos_sent = True

            return Gst.PadProbeReturn.DROP

        def add_to_cache(buffer, width, height):
            # Probe callback to add raw frame / jpeg image to cache
            _, mapinfo = buffer.map(Gst.MapFlags.READ)
            if self._enable_jpeg_output:
                # Buffer contains JPEG image, add to cache as is
                image_tensor = np.frombuffer(mapinfo.data, dtype=np.uint8)
            else:
                # Buffer contains raw frame

                # Extract GPU memory pointer and create tensor from it using
                # DeepStream Python Bindings and cupy
                _, shape, strides, dataptr, size = pyds.get_nvds_buf_surface_gpu(hash(buffer), 0)
                ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
                ctypes.pythonapi.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
                owner = None
                c_data_ptr = ctypes.pythonapi.PyCapsule_GetPointer(dataptr, None)
                unownedmem = cp.cuda.UnownedMemory(c_data_ptr, size, owner)
                memptr = cp.cuda.MemoryPointer(unownedmem, 0)
                n_frame_gpu = cp.ndarray(
                    shape=shape, dtype=np.uint8, memptr=memptr, strides=strides, order="C"
                )
                image_tensor = torch.tensor(
                    n_frame_gpu, dtype=torch.uint8, requires_grad=False, device="cuda"
                )

            # Cache the pre-processed frame / jpeg and its timestamp. Convert
            # the timestamps from nanoseconds to seconds.
            if self._is_live:
                with self._live_stream_frame_selectors_lock:
                    for _, (cached_pts, cached_frames) in self._live_stream_frame_selectors.items():
                        if buffer.pts / 1e9 in cached_pts:
                            cached_frames.append(image_tensor)
                    self._process_finished_chunks(buffer.pts)
            else:
                self._cached_frames.append(image_tensor)
                self._cached_frames_pts.append((buffer.pts) / 1000000000.0)
            buffer.unmap(mapinfo)
            logger.debug(f"Picked buffer {buffer.pts}")

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
                if (
                    buffer.pts > self._live_stream_next_chunk_start_pts
                    and not self._got_error
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
            logger.debug(
                "New audio buffer, buffer.pts: %d, duration: %d", buffer.pts, buffer.duration
            )

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
            logger.debug("Picked audio buffer %d", buffer.pts)

        def on_new_sample(appsink):
            # Appsink callback to pull frame from the pipeline
            sample = appsink.emit("pull-sample")
            caps = sample.get_caps()
            height = caps.get_structure(0).get_value("height")
            width = caps.get_structure(0).get_value("width")
            if self._first_frame_width == 0:
                logger.debug(f"first width,height in chunk={width}, {height}")
                self._first_frame_width = width
                self._first_frame_height = height
            if sample:
                buffer = sample.get_buffer()
                add_to_cache(buffer, width, height)
            return Gst.FlowReturn.OK

        def on_new_sample_audio(audio_appsink):
            # Appsink callback to pull audio samples from the pipeline
            sample = audio_appsink.emit("pull-sample")
            if sample:
                buffer = sample.get_buffer()
                logger.debug("New audio buffer with pts: %d", buffer.pts)
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
        appsink.connect("new-sample", on_new_sample)
        pipeline.add(appsink)

        if self._audio_support:
            self._audio_appsink = Gst.ElementFactory.make("appsink")
            self._audio_appsink.set_property("async", False)
            self._audio_appsink.set_property("sync", False)
            self._audio_appsink.set_property("enable-last-sample", False)
            self._audio_appsink.set_property("emit-signals", True)
            self._audio_appsink.connect("new-sample", on_new_sample_audio)
            pipeline.add(self._audio_appsink)

        def cb_newpad(uridecodebin, uridecodebin_pad, self):
            caps = uridecodebin_pad.get_current_caps()
            gststruct = caps.get_structure(0)
            gstname = gststruct.get_name()
            if gstname.find("video") != -1:
                uridecodebin_pad.link(self._q1.get_static_pad("sink"))
                logger.info("Video stream found.")
            if gstname.find("audio") != -1 and self._enable_audio and self._audio_q1:
                self._audio_present = True
                self._audio_eos = False
                uridecodebin_pad.link(self._audio_q1.get_static_pad("sink"))
                logger.info("Audio stream found.")

        uridecodebin.connect("pad-added", cb_newpad, self)

        def cb_autoplug_continue(bin, pad, caps, udata):
            # Ignore audio
            return not caps.to_string().startswith("audio/")

        if not self._audio_support:
            uridecodebin.connect("autoplug-continue", cb_autoplug_continue, None)

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
            logger.debug(f"selff._stop_stream = {selff._stop_stream}")
            if selff._stop_stream:
                logger.debug(
                    f"Intercepting stream:{message} as we are trying to move pipeline to NULL"
                )
                return False  # Skip sending the PAUSE message
            return True  # Allow sending the message

        def cb_elem_added(elem, username, password, selff):
            if "nvv4l2decoder" in elem.get_factory().get_name():
                elem.set_property("gpu-id", self._gpu_id)
                elem.set_property("extract-sei-type5-data", True)
                elem.set_property("sei-uuid", "NVDS_CUSTOMMETA")
            if "rtspsrc" == elem.get_factory().get_name():
                selff._rtspsrc = elem
                pyds.configure_source_for_ntp_sync(hash(elem))
                timeout = int(os.environ.get("VSS_RTSP_TIMEOUT", "") or "2000") * 1000
                latency = int(os.environ.get("VSS_RTSP_LATENCY", "") or "2000")
                elem.set_property("timeout", timeout)
                elem.set_property("latency", latency)
                # Below code need additional review and tests.
                # Also is a feature - to let users change protocol.
                # Protocols: Allowed lower transport protocols
                # Default: 0x00000007, "tcp+udp-mcast+udp"
                # protocols = int(os.environ.get("VSS_RTSP_PROTOCOLS", "") or "7")
                # elem.set_property("protocols", protocols)

                if username and password:
                    elem.set_property("user-id", username)
                    elem.set_property("user-pw", password)

                if not self._audio_support:
                    # Ignore audio
                    elem.connect("select-stream", cb_select_stream)

                # Connect before-send to handle TEARDOWN per:
                # Unfortunately, going to the NULL state involves going through PAUSED,
                # so rtspsrc does not know the difference and will send a PAUSE
                # when you wanted a TEARDOWN. The workaround is to
                # hook into the before-send signal and return FALSE in this case.
                # Source: https://gstreamer.freedesktop.org/documentation/rtsp/rtspsrc.html
                elem.connect("before-send", cb_before_send, selff)
            if "udpsrc" == elem.get_factory().get_name():
                logger.debug("udpsrc created")
                selff._udpsrc = elem

        uridecodebin.connect(
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
            pad.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, buffer_probe_event, self)

        pad.add_probe(Gst.PadProbeType.BUFFER, buffer_probe, self)
        pad.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, buffer_probe_event_eos, self)
        pad.add_probe(Gst.PadProbeType.QUERY_DOWNSTREAM, cb_ntpquery, self)

        qvideoconvert.link(videoconvert)

        videoconvert.link(capsfilter)
        if self._enable_jpeg_output:
            capsfilter.link(jpegenc)
            jpegenc.link(q2)
        else:
            capsfilter.link(q2)

        q2.link(appsink)

        def audio_buffer_probe(pad, info, data):
            # Probe callback function to pass chosen frames and drop other frames
            if self._is_live:
                return Gst.PadProbeReturn.OK

            buffer = info.get_buffer()

            # Small overlap in audio chunks so that words are not missed
            audio_overlap = min(self._chunk_duration // 10, 5e9)

            if buffer.pts > self._end_pts + audio_overlap:
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
            audio_pad.add_probe(Gst.PadProbeType.BUFFER, audio_buffer_probe, self)

        self._loop = GLib.MainLoop()
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        self._bus = bus

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
                self._got_error = True
                selff._audio_stop.set()
                selff._loop.quit()
            return True

        bus.connect("message", bus_call, self)
        return pipeline

    def find_center(self, mask, rect_params):
        center_x = rect_params.width / 2
        center_y = rect_params.height / 2
        if mask is not None:
            # Calculate the moments of the mask
            moments = cv2.moments(mask)
            # Calculate the centroid
            if moments["m00"] != 0:
                center_x = int(moments["m10"] / moments["m00"])
                center_y = int(moments["m01"] / moments["m00"])
        return center_x, center_y

    def get_border(self, mask, border_width=3):
        # Convert mask to uint8 format (0 or 255)
        mask_uint8 = (mask * 255).astype(np.uint8)

        # Perform dilation and erosion
        kernel = np.ones((border_width, border_width), np.uint8)
        dilation = cv2.dilate(mask_uint8, kernel, iterations=1)
        erosion = cv2.erode(mask_uint8, kernel, iterations=1)

        # Subtract the original mask from the eroded mask to get the border
        border = cv2.subtract(dilation, erosion)

        # Convert border to binary format
        border = (border > 0).astype(np.float32)

        h, w = border.shape
        return border.reshape(h, w, 1)

    def add_mask_meta_to_obj_meta(self, obj_meta, mask_file):
        with open(mask_file, "rb") as f:
            # Read the first 8 bytes for dimensions
            dims = np.frombuffer(f.read(8), dtype=np.int32)
            height, width = dims
            # Read the remaining bytes for the array data
            # mask = np.frombuffer(f.read(), dtype=np.uint8).reshape((height, width))
            mask = np.frombuffer(f.read(), dtype=np.uint8)
            if mask is not None:
                mask_params = pyds.NvOSD_MaskParams.cast(obj_meta.mask_params)
                mask_params.threshold = 0.5
                mask_params.width = width
                mask_params.height = height
                if mask_params.data is None:
                    buffer = mask_params.alloc_mask_array()
                    buffer[:] = mask.reshape(width * height)

    def add_obj_meta_to_frame(self, batch_meta, frame_meta, object_json_meta, obj_labels_list):
        """Inserts an object into the metadata"""
        # this is a good place to insert objects into the metadata.
        # Here's an example of inserting a single object.
        obj_meta = pyds.NvDsObjectMeta.cast(pyds.nvds_acquire_obj_meta_from_pool(batch_meta))
        # Set bbox properties. These are in input resolution.
        bbox_json = object_json_meta["bbox"]
        rect_params = obj_meta.rect_params
        rect_params.left = int(bbox_json["lX"])
        rect_params.top = int(bbox_json["tY"])
        rect_params.width = int(bbox_json["rX"] - bbox_json["lX"])
        rect_params.height = int(bbox_json["bY"] - bbox_json["tY"])

        # Set the object classification label.
        obj_meta.obj_label = object_json_meta["type"]

        # set classId based on index of the label in obj_labels_list
        classId = obj_labels_list.index(obj_meta.obj_label)

        # Semi-transparent yellow backgroud
        rect_params.has_bg_color = 0
        rect_params.bg_color.set(1, 1, 0, 0.4)

        # Red border of width 3
        rect_params.border_width = 2
        if classId == 0:
            rect_params.border_color.set(0, 1, 0, 0.5)
        elif classId == 1:
            rect_params.border_color.set(0, 0, 1, 0.5)
        elif classId == 2:
            rect_params.border_color.set(1, 0, 0, 0.5)
        elif classId == 3:
            rect_params.border_color.set(1, 0, 1, 0.5)
        else:
            rect_params.border_color.set(1, 1, 1, 0.5)

        # Set object info including class, detection confidence, etc.
        obj_meta.confidence = object_json_meta["conf"]
        obj_meta.class_id = classId
        obj_meta.object_id = int(object_json_meta["id"])

        if "misc" in object_json_meta:
            for misc_object in object_json_meta["misc"]:
                if "seg" in misc_object and "mask" in misc_object["seg"]:
                    # print(f"Adding masks for {frame_meta.buf_pts}")
                    self.add_mask_meta_to_obj_meta(obj_meta, misc_object["seg"]["mask"])
                    break

        # Set display text for the object.
        txt_params = obj_meta.text_params
        if txt_params.display_text:
            pyds.free_buffer(txt_params.display_text)

        txt_params.x_offset = max(0, int(rect_params.left))
        txt_params.y_offset = max(0, int(rect_params.top) - 10)
        txt_params.display_text = obj_meta.obj_label + "[" + str(obj_meta.object_id) + "]"
        # Font , font-color and font-size
        txt_params.font_params.font_name = "Serif"
        txt_params.font_params.font_size = 15
        # set(red, green, blue, alpha); set to White
        txt_params.font_params.font_color.set(1.0, 1.0, 1.0, 1.0)

        # Text background color
        txt_params.set_bg_clr = 1
        # set(red, green, blue, alpha); set to Black
        txt_params.text_bg_clr.set(0.0, 0.0, 0.0, 1.0)

        # Inser the object into current frame meta
        # This object has no parent
        pyds.nvds_add_obj_meta_to_frame(frame_meta, obj_meta, None)

    def add_cv_meta_to_frame(self, batch_meta, frame_meta, frame_json_meta, obj_labels_list):
        for obj_json_meta in frame_json_meta["objects"]:
            self.add_obj_meta_to_frame(batch_meta, frame_meta, obj_json_meta, obj_labels_list)
        # Also save the cv meta to be passed to Graph RAG pipeline
        self._cached_frames_cv_meta.append(frame_json_meta)

    def modify_osd_meta(self, batch_meta, frame_meta):
        l_obj = frame_meta.obj_meta_list
        obj_list = []
        while l_obj is not None:
            try:
                # Casting l_obj.data to pyds.NvDsObjectMeta
                obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
                obj_meta.text_params.display_text = str(obj_meta.object_id)
                if not self._draw_bbox:
                    obj_meta.rect_params.border_width = 0

                rgb_value = rgb_array[obj_meta.object_id % 1000]
                # obj_meta.rect_params.border_color.set(*u_data._mask_color)

                obj_meta.rect_params.border_color.set(rgb_value[0], rgb_value[1], rgb_value[2], 1.0)
                obj_meta.text_params.font_params.font_size = int(self._pipeline_height / 40)

                rgb_value1 = rgb_array[999 - obj_meta.object_id % 1000]
                rgb_value1[1] = max(0.0, 1.0 - ((rgb_value1[0] + rgb_value1[2]) * 0.5))
                obj_meta.text_params.font_params.font_color.set(
                    rgb_value1[0], rgb_value1[1], rgb_value1[2], 1.0
                )
                # obj_meta.text_params.font_params.font_color.set(1.0, 1.0, 1.0, 1.0)

                if self._fill_mask:
                    new_obj_meta = pyds.NvDsObjectMeta.cast(
                        pyds.nvds_acquire_obj_meta_from_pool(batch_meta)
                    )

                    new_obj_meta.rect_params = obj_meta.rect_params
                    mask_params = pyds.NvOSD_MaskParams.cast(new_obj_meta.mask_params)
                    mask_params.threshold = 0.01
                    mask_params.width = obj_meta.mask_params.width
                    mask_params.height = obj_meta.mask_params.height

                    if mask_params.data is None:
                        buffer = mask_params.alloc_mask_array()
                        buffer[:] = obj_meta.mask_params.get_mask_array().reshape(
                            obj_meta.mask_params.height * obj_meta.mask_params.width
                        )
                        # buffer[buffer > 0.1] = 0.45

                    # new_obj_meta.rect_params.border_color.set(1.0, 0.0, 0.0, 0.2)
                    new_obj_meta.rect_params.border_color.set(
                        rgb_value[0], rgb_value[1], rgb_value[2], 0.3
                    )
                    obj_list.append(new_obj_meta)

                mask_crop = None

                if self._center_text_on_object or self._mask_border_width:
                    if obj_meta.mask_params.data is not None:
                        mask_crop = obj_meta.mask_params.get_mask_array().reshape(
                            obj_meta.mask_params.height, obj_meta.mask_params.width
                        )

                if self._center_text_on_object:
                    cx, cy = self.find_center(mask_crop, obj_meta.rect_params)
                    obj_meta.text_params.x_offset = max(
                        int(cx - 0.015 * self._pipeline_width + obj_meta.rect_params.left), 0
                    )
                    obj_meta.text_params.y_offset = max(
                        int(cy - 0.015 * self._pipeline_height + obj_meta.rect_params.top), 0
                    )

                if self._mask_border_width:
                    if mask_crop is not None:
                        mask_crop = self.get_border(mask_crop, self._mask_border_width)
                        obj_meta.mask_params.get_mask_array()[:] = mask_crop.ravel()

            except StopIteration:
                break

            try:
                l_obj = l_obj.next
            except StopIteration:
                break

        try:
            for obj_meta in obj_list:
                pyds.nvds_add_obj_meta_to_frame(frame_meta, obj_meta, None)
        except StopIteration:
            pass

    def _create_osd_pipeline(
        self,
        file_or_rtsp: str,
        cv_metadata_json_file="",
        username="",
        password="",
        cv_pipeline_text_prompt="",
        request_id="dumps",
    ):
        # Construct DeepStream pipeline for decoding
        # For raw frames as tensor:
        # uridecodebin -> probe (frame selector) -> nvvideconvert -> appsink
        #     -> frame pre-processing -> add to cache
        # For jpeg images:
        # uridecodebin -> probe (frame selector) -> nvjpegenc -> appsink -> add to cache
        print(f"******cv_metadata_json_file = {cv_metadata_json_file}****")
        # Initialization of JSON CV metadata
        if cv_metadata_json_file:
            self.input_cv_metadata = JsonCVMetadata()
            self.input_cv_metadata.read_json_file(cv_metadata_json_file)
        else:
            self.input_cv_metadata = None
        self._output_cv_metadata = None
        # dump cached frames when OSD pipeline is enabled
        self._dump_cached_frames = True
        self._request_id = request_id
        self._cached_frames_dir = f"/tmp/via/cached_frames/{request_id}"
        # Check if the cached frames dump folder exists
        try:
            os.makedirs(self._cached_frames_dir, exist_ok=True)
            print(f"Request ID: {request_id} - Cached frames saved at {self._cached_frames_dir}")
        except Exception as e:
            logger.error(f"Error creating cached frames directory: {e}")
            self._dump_cached_frames = False
        # format text prompt
        caption = cv_pipeline_text_prompt
        caption = caption.lower()
        caption = caption.strip()
        if not caption.endswith("."):
            caption = caption + "."
        cat_list = caption.split(" . ")
        cat_list[-1] = cat_list[-1].replace(" .", "")
        self._text_prompts = cat_list

        self._is_live = file_or_rtsp.startswith("rtsp://")
        pipeline = Gst.Pipeline()

        uridecodebin = Gst.ElementFactory.make("uridecodebin")
        if self._is_live:
            uridecodebin.set_property("uri", file_or_rtsp)
        else:
            uridecodebin.set_property("uri", f"file://{os.path.abspath(file_or_rtsp)}")
        pipeline.add(uridecodebin)
        self._uridecodebin = uridecodebin
        # self._is_live = True

        # Add a tee, queue and fakesink reuired for seeking
        # decoder -> tee -> queue -> fakesink
        #               |-> queue -> nvstreammux
        self._tee = Gst.ElementFactory.make("tee")
        pipeline.add(self._tee)
        tee_pad = self._tee.get_static_pad("sink")
        queue_tee_fakesink = Gst.ElementFactory.make("queue")
        pipeline.add(queue_tee_fakesink)
        seek_fakesink = Gst.ElementFactory.make("fakesink")
        seek_fakesink.set_property("async", False)
        seek_fakesink.set_property("sync", False)
        pipeline.add(seek_fakesink)

        def add_audio_to_cache(buffer):
            # Probe callback to add audio samples to cache
            _, mapinfo = buffer.map(Gst.MapFlags.READ)
            audio_tensor = np.frombuffer(mapinfo.data, dtype=np.int16)
            logger.debug(
                "New audio buffer, buffer.pts: %d, duration: %d", buffer.pts, buffer.duration
            )

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
            logger.debug("Picked audio buffer %d", buffer.pts)

        def on_new_sample_audio(audio_appsink):
            # Appsink callback to pull audio samples from the pipeline
            sample = audio_appsink.emit("pull-sample")
            if sample:
                buffer = sample.get_buffer()
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

        self._audio_q1 = None
        if self._audio_support:
            self._audio_eos = False
            self._audio_present = False

            self._audio_q1 = Gst.ElementFactory.make("queue")
            pipeline.add(self._audio_q1)
            self._audio_q1.set_property("max-size-buffers", 30)

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

            self._audio_appsink = Gst.ElementFactory.make("appsink")
            self._audio_appsink.set_property("async", False)
            self._audio_appsink.set_property("sync", False)
            self._audio_appsink.set_property("enable-last-sample", False)
            self._audio_appsink.set_property("emit-signals", True)
            self._audio_appsink.connect("new-sample", on_new_sample_audio)
            pipeline.add(self._audio_appsink)

        q1 = Gst.ElementFactory.make("queue")
        pipeline.add(q1)

        nvstreammux = Gst.ElementFactory.make("nvstreammux")
        pipeline.add(nvstreammux)
        stream_width, stream_height = MediaFileInfo.get_info(file_or_rtsp).video_resolution
        self._pipeline_width = stream_width
        self._pipeline_height = stream_height
        nvstreammux.set_property("width", stream_width)
        nvstreammux.set_property("height", stream_height)
        nvstreammux.set_property("batch-size", 1)
        nvstreammux.set_property("batched-push-timeout", -1)
        nvstreammux.set_property("gpu-id", self._gpu_id)

        videoconvert_to_osd = Gst.ElementFactory.make("nvvideoconvert")
        pipeline.add(videoconvert_to_osd)

        nvdsosd = Gst.ElementFactory.make("nvdsosd")
        pipeline.add(nvdsosd)
        nvdsosd.set_property("display-mask", True)
        nvdsosd.set_property("gpu-id", self._gpu_id)
        # BN : TBD : the alpha value (transparency) doesn't work in process mode 1 (i.e. GPU)
        # Hence using process mode 0 (CPU)
        # This also requires another nvvideoconvert i.e. videoconvert_to_osd
        # since "process-mode" 0 supports only RGBA output
        nvdsosd.set_property("process-mode", 0)

        q2 = Gst.ElementFactory.make("queue")
        pipeline.add(q2)

        if self._is_live and not os.environ.get("VSS_DISABLE_LIVESTREAM_PREVIEW", ""):
            logger.info(
                "Creating live stream video preview branch for %s", self._live_stream_request_id
            )
            if not self._create_live_stream_video_preview_branch(pipeline, self._tee, None):
                logger.warning(
                    "Failed to create live stream video preview branch. Additional codecs not installed."  # noqa: E501
                )

        videoconvert = Gst.ElementFactory.make("nvvideoconvert")
        self._videoconvert = videoconvert
        videoconvert.set_property("nvbuf-memory-type", 2)

        videoconvert.set_property("gpu-id", self._gpu_id)
        pipeline.add(videoconvert)

        if self._enable_jpeg_output:
            jpegenc = Gst.ElementFactory.make("nvjpegenc")
            pipeline.add(jpegenc)
            format = "RGB"  # only RGB/I420 supported by nvjpegenc
        else:
            format = "GBR" if self._do_preprocess else "RGB"
            pass
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

        def get_buffer_pts(buffer):
            ###########################
            # Get the correct PTS for the biffer from frame meta
            # Retrieve batch metadata from the gst_buffer
            # Note that pyds.gst_buffer_get_nvds_batch_meta() expects the
            # C address of gst_buffer as input, which is obtained with hash(gst_buffer)
            batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(buffer))
            l_frame = batch_meta.frame_meta_list
            buffer_pts = 0
            while l_frame is not None:
                try:
                    # Note that l_frame.data needs a cast to pyds.NvDsFrameMeta
                    # The casting also keeps ownership of the underlying memory
                    # in the C code, so the Python garbage collector will leave
                    # it alone.
                    frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
                    buffer_pts = frame_meta.buf_pts
                except StopIteration:
                    break
                l_frame = l_frame.next
            #############################
            return buffer_pts

        def update_ntp_pts(buffer, ntp_pts):
            ###########################
            # Get the correct PTS for the biffer from frame meta
            # Retrieve batch metadata from the gst_buffer
            # Note that pyds.gst_buffer_get_nvds_batch_meta() expects the
            # C address of gst_buffer as input, which is obtained with hash(gst_buffer)
            batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(buffer))
            l_frame = batch_meta.frame_meta_list
            while l_frame is not None:
                try:
                    # Note that l_frame.data needs a cast to pyds.NvDsFrameMeta
                    # The casting also keeps ownership of the underlying memory
                    # in the C code, so the Python garbage collector will leave
                    # it alone.
                    frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
                    frame_meta.ntp_timestamp = ntp_pts
                except StopIteration:
                    break
                l_frame = l_frame.next
            #############################

        def write_cv_metadata(buffer, udata):
            # Retrieve batch metadata from the gst_buffer
            # Note that pyds.gst_buffer_get_nvds_batch_meta() expects the
            # C address of gst_buffer as input, which is obtained with hash(gst_buffer)
            batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(buffer))
            l_frame = batch_meta.frame_meta_list
            while l_frame is not None:
                try:
                    # Note that l_frame.data needs a cast to pyds.NvDsFrameMeta
                    # The casting also keeps ownership of the underlying memory
                    # in the C code, so the Python garbage collector will leave
                    # it alone.
                    frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
                    udata._output_cv_metadata.write_frame(frame_meta)
                except StopIteration:
                    break
                try:
                    l_frame = l_frame.next
                except StopIteration:
                    break
            # udata._output_cv_metadata.write_past_frame_meta(batch_meta)

        def buffer_probe(pad, info, data):
            # Probe callback function to pass chosen frames and drop other frames
            buffer = info.get_buffer()
            buffer_pts = get_buffer_pts(buffer)
            # buffer_pts = buffer.pts
            # print (f"Got frame with buffer_pts = :{buffer_pts}")
            if buffer_pts == Gst.CLOCK_TIME_NONE:
                return Gst.PadProbeReturn.DROP
            self._last_frame_pts = buffer_pts
            if self._is_live:
                new_chunk = False
                if buffer_pts >= self._live_stream_next_chunk_start_pts:
                    with self._audio_end_cv:
                        if (
                            self._audio_present
                            and (self._audio_current_pts) < self._live_stream_next_chunk_start_pts
                            and not self._got_error
                            and not self._stop_stream
                        ):
                            logger.debug(
                                "In buffer probe waiting for audio processing,"
                                "current audio pts: %d",
                                self._audio_current_pts,
                            )
                            self._audio_end_cv.wait(1)

                with self._live_stream_frame_selectors_lock:
                    buffer_address = hash(buffer)
                    video_sei_meta = gst_video_sei_meta.gst_buffer_get_video_sei_meta(
                        buffer_address
                    )

                    if video_sei_meta:
                        self._sei_data = json.loads(video_sei_meta.sei_metadata_ptr)
                        ntp_pts = self._sei_data["sim_time"] * 1e9
                        if self._sei_base_time is None:
                            self._sei_base_time = self._sei_data["timestamp"] - ntp_pts
                        update_ntp_pts(buffer, ntp_pts)

                    if buffer_pts >= self._live_stream_next_chunk_start_pts:
                        fs = DefaultFrameSelector(self._frame_selector._num_frames)
                        chunk = ChunkInfo()
                        chunk.file = self._live_stream_url
                        chunk.chunkIdx = self._live_stream_next_chunk_idx
                        chunk.is_first = chunk.chunkIdx == 0
                        if chunk.is_first:
                            self._live_stream_next_chunk_start_pts = buffer_pts
                        chunk.start_pts = int(self._live_stream_next_chunk_start_pts)
                        chunk.end_pts = int(
                            chunk.start_pts + self._live_stream_chunk_duration * 1e9
                        )
                        # Create json cv metadata for new chunk
                        self._output_cv_metadata = JsonCVMetadata(
                            request_id=self._live_stream_request_id, chunkIdx=chunk.chunkIdx
                        )
                        # Create a new directory for saving cached frames
                        self._cached_frames_dir = (
                            f"/tmp/via/cached_frames/{self._request_id}/{chunk.chunkIdx}"
                        )
                        try:
                            os.makedirs(self._cached_frames_dir, exist_ok=True)
                            print(
                                f"Live stream Request ID: \
                                    {self._request_id} - chunk {chunk.chunkIdx} - \
                                  Cached frames saved at {self._cached_frames_dir}"
                            )
                        except Exception as e:
                            logger.error(f"Error creating cached frames directory: {e}")
                            self._dump_cached_frames = False
                        # print(chunk)
                        fs.set_chunk(chunk)
                        self._live_stream_frame_selectors[fs] = ([], [])
                        self._live_stream_next_chunk_start_pts = (
                            chunk.end_pts - self._live_stream_chunk_overlap_duration * 1e9
                        )
                        self._live_stream_next_chunk_idx += 1
                        new_chunk = True

                    choose_frame = False
                    for fs, (
                        cached_pts,
                        cached_frames,
                    ) in self._live_stream_frame_selectors.items():
                        if fs.choose_frame(buffer, buffer_pts):
                            choose_frame = True
                            cached_pts.append(buffer_pts / 1e9)

                    # If frame is chosen, then we need to output cv metadata of the frame
                    if choose_frame:
                        write_cv_metadata(buffer, data)

                    self._process_finished_chunks(buffer_pts)

                if new_chunk:
                    with self._audio_start_cv:
                        self._audio_start_cv.notify()

                if choose_frame:
                    return Gst.PadProbeReturn.OK

            else:
                if self._frame_selector.choose_frame(buffer, buffer_pts):
                    # print(f"Chosen frame buffer.pts = {buffer.pts}")
                    return Gst.PadProbeReturn.OK
                if len(self._frame_selector._selected_pts_array) == 0:
                    if self._audio_present:
                        if self._audio_eos:
                            self._pipeline.send_event(Gst.Event.new_eos())
                            self._eos_sent = True
                    else:
                        self._pipeline.send_event(Gst.Event.new_eos())
                        if self._audio_convert:
                            self._audio_convert.send_event(Gst.Event.new_eos())
                        self._eos_sent = True

            return Gst.PadProbeReturn.DROP

        def add_to_cache(buffer, width, height):
            # Probe callback to add raw frame / jpeg image to cache
            _, mapinfo = buffer.map(Gst.MapFlags.READ)
            if self._enable_jpeg_output:
                # Buffer contains JPEG image, add to cache as is
                image_tensor = np.frombuffer(mapinfo.data, dtype=np.uint8)
            else:
                # Buffer contains raw frame

                # Extract GPU memory pointer and create tensor from it using
                # DeepStream Python Bindings and cupy
                _, shape, strides, dataptr, size = pyds.get_nvds_buf_surface_gpu(hash(buffer), 0)
                ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
                ctypes.pythonapi.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
                owner = None
                c_data_ptr = ctypes.pythonapi.PyCapsule_GetPointer(dataptr, None)
                unownedmem = cp.cuda.UnownedMemory(c_data_ptr, size, owner)
                memptr = cp.cuda.MemoryPointer(unownedmem, 0)
                n_frame_gpu = cp.ndarray(
                    shape=shape, dtype=np.uint8, memptr=memptr, strides=strides, order="C"
                )
                image_tensor = torch.tensor(
                    n_frame_gpu, dtype=torch.uint8, requires_grad=False, device="cuda"
                )

            # Cache the pre-processed frame / jpeg and its timestamp. Convert
            # the timestamps from nanoseconds to seconds.
            buffer_pts = get_buffer_pts(buffer)
            # buffer_pts = buffer.pts
            # print (f"Caching frame with buffer_pts = :{buffer_pts}")
            if self._is_live:
                with self._live_stream_frame_selectors_lock:
                    for _, (cached_pts, cached_frames) in self._live_stream_frame_selectors.items():
                        if buffer_pts / 1e9 in cached_pts:
                            cached_frames.append(image_tensor)
                    self._process_finished_chunks(buffer_pts)
            else:
                self._cached_frames.append(image_tensor)
                self._cached_frames_pts.append((buffer_pts) / 1000000000.0)
            buffer.unmap(mapinfo)
            logger.debug(f"Picked buffer {buffer_pts}")

        def on_new_sample(appsink):
            # Appsink callback to pull frame from the pipeline
            sample = appsink.emit("pull-sample")
            caps = sample.get_caps()
            height = caps.get_structure(0).get_value("height")
            width = caps.get_structure(0).get_value("width")
            if self._first_frame_width == 0:
                logger.debug(f"first width,height in chunk={width}, {height}")
                self._first_frame_width = width
                self._first_frame_height = height
            if sample:
                buffer = sample.get_buffer()
                add_to_cache(buffer, width, height)
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
        appsink.connect("new-sample", on_new_sample)
        pipeline.add(appsink)

        def cb_newpad(uridecodebin, uridecodebin_pad, self):
            caps = uridecodebin_pad.get_current_caps()
            gststruct = caps.get_structure(0)
            gstname = gststruct.get_name()
            if gstname.find("video") != -1:
                uridecodebin_pad.link(self._tee.get_static_pad("sink"))
                logger.info("Video stream found.")
            if gstname.find("audio") != -1 and self._enable_audio and self._audio_q1:
                self._audio_present = True
                uridecodebin_pad.link(self._audio_q1.get_static_pad("sink"))
                logger.info("Audio stream found.")

        uridecodebin.connect("pad-added", cb_newpad, self)

        def cb_autoplug_continue(bin, pad, caps, udata):
            # Ignore audio
            return not caps.to_string().startswith("audio/")

        if not self._audio_support:
            uridecodebin.connect("autoplug-continue", cb_autoplug_continue, None)

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
            logger.debug(f"selff._stop_stream = {selff._stop_stream}")
            if selff._stop_stream:
                logger.debug(
                    f"Intercepting stream:{message} as we are trying to move pipeline to NULL"
                )
                return False  # Skip sending the PAUSE message
            return True  # Allow sending the message

        def cb_elem_added(elem, username, password, selff):
            if "nvv4l2decoder" in elem.get_factory().get_name():
                elem.set_property("gpu-id", self._gpu_id)
                elem.set_property("extract-sei-type5-data", True)
                elem.set_property("sei-uuid", "NVDS_CUSTOMMETA")
            if "rtspsrc" == elem.get_factory().get_name():
                selff._rtspsrc = elem
                pyds.configure_source_for_ntp_sync(hash(elem))
                timeout = int(os.environ.get("VSS_RTSP_TIMEOUT", "") or "2000") * 1000
                latency = int(os.environ.get("VSS_RTSP_LATENCY", "") or "2000")
                elem.set_property("timeout", timeout)
                elem.set_property("latency", latency)
                # Below code need additional review and tests.
                # Also is a feature - to let users change protocol.
                # Protocols: Allowed lower transport protocols
                # Default: 0x00000007, "tcp+udp-mcast+udp"
                # protocols = int(os.environ.get("VSS_RTSP_PROTOCOLS", "") or "7")
                # elem.set_property("protocols", protocols)

                if username and password:
                    elem.set_property("user-id", username)
                    elem.set_property("user-pw", password)

                if not self._audio_support:
                    # Ignore audio
                    elem.connect("select-stream", cb_select_stream)

                # Connect before-send to handle TEARDOWN per:
                # Unfortunately, going to the NULL state involves going through PAUSED,
                # so rtspsrc does not know the difference and will send a PAUSE
                # when you wanted a TEARDOWN. The workaround is to
                # hook into the before-send signal and return FALSE in this case.
                # Source: https://gstreamer.freedesktop.org/documentation/rtsp/rtspsrc.html
                elem.connect("before-send", cb_before_send, selff)
            if "udpsrc" == elem.get_factory().get_name():
                logger.debug("udpsrc created")
                selff._udpsrc = elem

        uridecodebin.connect(
            "deep-element-added",
            lambda bin, subbin, elem, username=username, password=password, selff=self: cb_elem_added(  # noqa: E501
                elem, username, password, selff
            ),
        )

        pad = videoconvert_to_osd.get_static_pad("sink")

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
            pad.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, buffer_probe_event, self)

        pad.add_probe(Gst.PadProbeType.BUFFER, buffer_probe, self)
        pad.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, buffer_probe_event_eos, self)
        tee_pad.add_probe(Gst.PadProbeType.QUERY_DOWNSTREAM, cb_ntpquery, self)

        def osd_sink_pad_buffer_probe(pad, info, u_data):
            gst_buffer = info.get_buffer()
            if not gst_buffer:
                print("Unable to get GstBuffer ")
                return

            # Retrieve batch metadata from the gst_buffer
            # # Note that pyds.gst_buffer_get_nvds_batch_meta() expects the
            # C address of gst_buffer as input, which is obtained with hash(gst_buffer)
            batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
            l_frame = batch_meta.frame_meta_list

            while l_frame is not None:
                try:
                    # Note that l_frame.data needs a cast to pyds.NvDsFrameMeta
                    # The casting also keeps ownership of the underlying memory
                    # in the C code, so the Python garbage collector will leave
                    # it alone.
                    frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
                except StopIteration:
                    break

                if u_data.input_cv_metadata:
                    frame_json_meta = u_data.input_cv_metadata.get_frame_cv_meta(frame_meta.buf_pts)
                    obj_labels_list = u_data.input_cv_metadata.get_obj_labels_list()
                    if frame_json_meta:
                        self.add_cv_meta_to_frame(
                            batch_meta, frame_meta, frame_json_meta, obj_labels_list
                        )

                self.modify_osd_meta(batch_meta, frame_meta)

                try:
                    l_frame = l_frame.next
                except StopIteration:
                    break
            return Gst.PadProbeReturn.OK

        osdsinkpad = nvdsosd.get_static_pad("sink")
        if not osdsinkpad:
            sys.stderr.write(" Unable to get sink pad of osd \n")
        osdsinkpad.add_probe(Gst.PadProbeType.BUFFER, osd_sink_pad_buffer_probe, self)

        self._tee.link(q1)
        self._tee.link(queue_tee_fakesink)
        queue_tee_fakesink.link(seek_fakesink)

        def audio_buffer_probe(pad, info, data):
            # Probe callback function to pass chosen frames and drop other frames
            if self._is_live:
                return Gst.PadProbeReturn.OK

            buffer = info.get_buffer()

            # Small overlap in audio chunks so that words are not missed
            audio_overlap = min(self._chunk_duration // 10, 5e9)

            if buffer.pts > self._end_pts + audio_overlap:
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
            audio_pad.add_probe(Gst.PadProbeType.BUFFER, audio_buffer_probe, self)

        q1_src_pad = q1.get_static_pad("src")
        mux_sinkpad = nvstreammux.request_pad_simple("sink_0")
        q1_src_pad.link(mux_sinkpad)
        if self._is_live and self._gdino_engine:
            # Create gdino - tracker pipeline (Similar to CV pipeline)
            # streammux -> queue3 -> videoconvert2 -> capsfilter1 (RGBA) -> queue4 -> videoconvert3
            # -> queue5 -> tracker -> queue6  ->  videoconvert_to_osd -> osd
            # create elements
            print("Creating more elements")
            q3 = Gst.ElementFactory.make("queue")
            pipeline.add(q3)
            videoconvert2 = Gst.ElementFactory.make("nvvideoconvert")
            videoconvert2.set_property("nvbuf-memory-type", 2)
            videoconvert2.set_property("interpolation-method", 1)
            pipeline.add(videoconvert2)
            capsfilter1 = Gst.ElementFactory.make("capsfilter")
            capsfilter1.set_property(
                "caps",
                Gst.Caps.from_string("video/x-raw(memory:NVMM), format=NV12"),
            )
            pipeline.add(capsfilter1)
            q4 = Gst.ElementFactory.make("queue")
            pipeline.add(q4)
            videoconvert3 = Gst.ElementFactory.make("nvvideoconvert")
            pipeline.add(videoconvert3)
            q5 = Gst.ElementFactory.make("queue")
            pipeline.add(q5)
            nvtracker = Gst.ElementFactory.make("nvtracker")
            nvtracker.set_property("user-meta-pool-size", 256)
            nvtracker.set_property(
                "ll-lib-file",
                "/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so",
            )
            nvtracker.set_property("ll-config-file", self._tracker_config)
            pipeline.add(nvtracker)
            self._nvtracker = nvtracker
            q6 = Gst.ElementFactory.make("queue")
            pipeline.add(q6)
            # BN : TBD : add nvdslogger when the issue in nvdslogger is fixed
            # when nvdslogger is enabled, stale states are maintained in the second run
            # nvdslogger = Gst.ElementFactory.make("nvdslogger")
            # pipeline.add(nvdslogger)

            # Add buffer probes
            unique_filename = f"/tmp/config_nvinferserver_{uuid.uuid4()}.txt"
            self._unique_filename = unique_filename

            # Copy the file to /tmp with the unique filename
            shutil.copy(
                "/opt/nvidia/TritonGdino/config_triton_nvinferserver_gdino.txt", unique_filename
            )

            if not os.path.exists("/tmp/nvdsinferserver_custom_impl_gdino/"):
                shutil.copytree(
                    "/opt/nvidia/TritonGdino/nvdsinferserver_custom_impl_gdino/",
                    "/tmp/nvdsinferserver_custom_impl_gdino/",
                )
            else:
                print("nvdsinferserver_custom_impl_gdino already exists in /tmp")

            if not os.path.exists(f"/tmp/TritonGdino_{self._gpu_id}/"):
                shutil.copytree(
                    "/opt/nvidia/TritonGdino/",
                    f"/tmp/TritonGdino_{self._gpu_id}/",
                )

                with open(
                    f"/tmp/TritonGdino_{self._gpu_id}/"
                    "triton_model_repo/gdino_preprocess/config.pbtxt",
                    "r",
                ) as file:
                    content = file.read()

                # Use a regex pattern that allows for optional spaces around the colon and brackets
                modified_content = re.sub(
                    r"gpu_ids:\s*\[\s*0\s*\]", f"gpu_ids: [{self._gpu_id}]", content
                )

                # Write the modified content back to the file
                with open(
                    f"/tmp/TritonGdino_{self._gpu_id}/"
                    "triton_model_repo/gdino_preprocess/config.pbtxt",
                    "w",
                ) as file:
                    file.write(modified_content)
                # print (modified_content)

                with open(
                    f"/tmp/TritonGdino_{self._gpu_id}/" "triton_model_repo/gdino_trt/config.pbtxt",
                    "r",
                ) as file:
                    content = file.read()

                # Use a regex pattern that allows for optional spaces around the colon and brackets
                modified_content = re.sub(
                    r"gpu_ids:\s*\[\s*0\s*\]", f"gpu_ids: [{self._gpu_id}]", content
                )

                # Write the modified content back to the file
                with open(
                    f"/tmp/TritonGdino_{self._gpu_id}/" "triton_model_repo/gdino_trt/config.pbtxt",
                    "w",
                ) as file:
                    file.write(modified_content)
                # print (modified_content)

                with open(
                    f"/tmp/TritonGdino_{self._gpu_id}/"
                    "triton_model_repo/gdino_postprocess/config.pbtxt",
                    "r",
                ) as file:
                    content = file.read()

                # Use a regex pattern that allows for optional spaces around the colon and brackets
                modified_content = re.sub(
                    r"gpu_ids:\s*\[\s*0\s*\]", f"gpu_ids: [{self._gpu_id}]", content
                )

                # Write the modified content back to the file
                with open(
                    f"/tmp/TritonGdino_{self._gpu_id}/"
                    "triton_model_repo/gdino_postprocess/config.pbtxt",
                    "w",
                ) as file:
                    file.write(modified_content)
                # print (modified_content)

            else:
                print(f"TritonGdino_{self._gpu_id} already exists in /tmp")

            threshold = None
            # check if last element has confidence threshold
            if self._text_prompts and ";" in self._text_prompts[-1]:
                # split the last element into text and threshold
                text, threshold = self._text_prompts[-1].split(";")
                # strip punctuation from text
                text = text.strip()
                # replace last element with just the text
                self._text_prompts[-1] = text.rstrip(".")
                # remove any trailing periods and whitespace from threshold
                threshold = threshold.strip().rstrip(".")
                try:
                    threshold = float(threshold)
                except ValueError:
                    print(f"warning: invalid threshold format in prompt: {threshold}")

            # prompt_text = " . ".join(self._text_prompts) + " . "
            # pattern = r"person . face . car . bus . backpack . "
            # print(prompt_text)
            # print(pattern)

            # Read the file, modify it, and write it back
            with open(unique_filename, "r") as file:
                content = file.read()

            prompt_text = " . ".join(self._text_prompts) + " . "
            print(self._text_prompts)

            # Try to find the type_name pattern in the config file content
            # Pattern to match the entire type_name including optional threshold
            existing_pattern = r'type_name:\s*"([^"]+?)(?:;[0-9]*\.?[0-9]+)?"'
            match = re.search(existing_pattern, content)

            if match:
                # Extract the full matched type_name string
                full_match = match.group(0)

                # Check if the existing type_name has a threshold value (format: "text;threshold")
                if ";" in full_match:
                    # Extract existing threshold, removing trailing quote
                    existing_threshold = full_match.split(";")[1].rstrip('"')

                    # If a new threshold was provided in text_prompts, use it
                    if threshold is not None:
                        new_type_name = f'type_name: "{prompt_text.strip()};{threshold}"'
                    # Otherwise keep the existing threshold from config
                    else:
                        new_type_name = f'type_name: "{prompt_text.strip()};{existing_threshold}"'

                # No threshold in existing type_name
                else:
                    # If a new threshold was provided in text_prompts, use it
                    if threshold is not None:
                        new_type_name = f'type_name: "{prompt_text.strip()};{threshold}"'
                    # No threshold anywhere, use default 0.3
                    else:
                        new_type_name = f'type_name: "{prompt_text.strip()};0.3"'

                # Replace the old type_name with the new one, preserving rest of content
                modified_content = re.sub(full_match, new_type_name, content)

            # Could not find type_name pattern in config
            else:
                print("Warning: Could not find type_name pattern in config file")
                modified_content = content  # Keep content unchanged

            # Use a regex pattern that allows for optional spaces around the colon and brackets
            modified_content = re.sub(
                r"gpu_ids:\s*\[\s*0\s*\]", f"gpu_ids: [{self._gpu_id}]", modified_content
            )
            modified_content = re.sub(r"device:\s*0", f"device: {self._gpu_id}", modified_content)

            modified_content = re.sub(
                r"root:\s*\"./triton_model_repo/\"",
                f'root: "/tmp/TritonGdino_{self._gpu_id}/' 'triton_model_repo/"',
                modified_content,
            )
            print(f"Setting GDINO Inference interval to : {self._inference_interval}")
            modified_content = re.sub(
                r"interval:\s*0", f"interval: {self._inference_interval}", modified_content
            )

            # Write the modified content back to the file
            # print(modified_content)
            # print(unique_filename)
            with open(unique_filename, "w") as file:
                file.write(modified_content)

            # Set the property to use the modified file
            nvdsinferserver = Gst.ElementFactory.make("nvinferserver")
            pipeline.add(nvdsinferserver)
            nvdsinferserver.set_property("config-file-path", unique_filename)
            # nvdsinferserver.set_property("interval", self._inference_interval)

            # pgiesrcpad = q4.get_static_pad("sink")
            # if not pgiesrcpad:
            #     sys.stderr.write(" Unable to get src pad of primary infer \n")
            self._frame_no = 0
            # self._inference_interval = 1
            # pgiesrcpad.add_probe(Gst.PadProbeType.BUFFER, gdino_pgie_src_pad_buffer_probe, self)
            # nvtrackersrcpad = nvtracker.get_static_pad("src")
            # nvtrackersrcpad.add_probe(Gst.PadProbeType.BUFFER, tracker_src_pad_buffer_probe, self)

            # link elements
            nvstreammux.link(q3)
            q3.link(videoconvert2)
            videoconvert2.link(capsfilter1)
            capsfilter1.link(nvdsinferserver)
            nvdsinferserver.link(q4)
            q4.link(videoconvert3)
            videoconvert3.link(q5)
            q5.link(nvtracker)
            nvtracker.link(q6)
            # q6.link(nvdslogger)
            # nvdslogger.link(videoconvert_to_osd)
            q6.link(videoconvert_to_osd)
        else:
            nvstreammux.link(videoconvert_to_osd)
        videoconvert_to_osd.link(nvdsosd)
        nvdsosd.link(videoconvert)
        videoconvert.link(capsfilter)
        if self._enable_jpeg_output:
            capsfilter.link(jpegenc)
            jpegenc.link(q2)
        else:
            capsfilter.link(q2)

        q2.link(appsink)

        self._loop = GLib.MainLoop()
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        self._bus = bus

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
                self._got_error = True
                selff._audio_stop.set()
                selff._loop.quit()
            return True

        bus.connect("message", bus_call, self)
        return pipeline

    def destroy_pipeline(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        if self._gdino:
            self._gdino = None

    # Debug functionality
    # Dump cached frames
    def dump_cached_frame(self, cached_frames, cached_frames_pts, enable_jpeg_output):
        from PIL import Image

        if self._dump_cached_frames:
            for frame, frame_pts in zip(cached_frames, cached_frames_pts):
                if enable_jpeg_output:
                    output_path = os.path.join(
                        self._cached_frames_dir, f"frame_{frame_pts:08.3f}.jpg"
                    )
                    with open(output_path, "wb") as f:
                        f.write(frame.tobytes())
                else:
                    # Move tensor to CPU (if it's on GPU) and detach (if part of a graph)
                    frame_cpu = frame.cpu().detach()
                    # Convert to NumPy array
                    numpy_image = frame_cpu.numpy()
                    # Ensure array is in correct shape (H,W,C) and uint8 format
                    if len(numpy_image.shape) == 3:
                        if numpy_image.shape[0] == 3:  # If channels are first (C,H,W)
                            numpy_image = numpy_image.transpose(1, 2, 0)
                    numpy_image = numpy_image.astype(np.uint8)
                    # print(numpy_image.shape)
                    pil_image = Image.fromarray(numpy_image)
                    pil_image.save(
                        os.path.join(self._cached_frames_dir, f"frame_{frame_pts:08.3f}.jpg")
                    )

    def get_frames(
        self,
        chunk: ChunkInfo,
        retain_pipeline=False,
        frame_selector=None,
        enable_audio=False,
        request_id="",
    ):
        """Get frames from a chunk

        Args:
            chunk (ChunkInfo): Chunk to get frames from

        Returns:
            (list[tensor], list[float]): List of tensors containing raw frames or jpeg images
                                         and a list of corresponding timestamps in seconds
        """
        self._cached_frames = []
        self._cached_frames_pts = []
        self._cached_audio_frames = []
        self._audio_eos = False
        self._audio_present = False
        self._enable_audio = enable_audio
        self._eos_sent = False
        self._end_pts = chunk.end_pts
        self._chunk_duration = chunk.end_pts - chunk.start_pts
        self._chunkIdx = chunk.chunkIdx

        logger.info("Audio ASR enabled: %d", enable_audio)

        old_pipeline = None
        # ";" in chunk.file denotes a list of files
        for file in chunk.file.split(";"):
            if (
                self._last_stream_id != (chunk.streamId + file)
                or self._destroy_pipeline
                or chunk.cv_metadata_json_file
            ) and self._pipeline:
                old_pipeline = self._pipeline
                self._pipeline = None
                self._destroy_pipeline = False
                if not (self._frame_width and self._frame_height):
                    # Next pipeline should use same resoulution as first
                    # to allow all frames in the chunk have same resoulution
                    self._frame_width = self._first_frame_width
                    self._frame_height = self._first_frame_height

            self._last_stream_id = chunk.streamId + file

            if not self._pipeline:
                if chunk.cv_metadata_json_file:
                    self._pipeline = self._create_osd_pipeline(
                        file,
                        cv_metadata_json_file=chunk.cv_metadata_json_file,
                        request_id=request_id,
                    )
                    self._destroy_pipeline = True
                else:
                    self._pipeline = self._create_pipeline(file)
            pipeline = self._pipeline

            # Set start/end time in the file based on chunk info.
            frame_selector_backup = self._frame_selector
            if frame_selector:
                self._frame_selector = frame_selector
            self._frame_selector.set_chunk(chunk)
            start_pts = chunk.start_pts - chunk.pts_offset_ns

            pipeline.set_state(Gst.State.PAUSED)
            pipeline.get_state(Gst.CLOCK_TIME_NONE)

            pipeline.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT | Gst.SeekFlags.SNAP_BEFORE,
                start_pts,
            )

            # Set the pipeline to PLAYING and wait for end-of-stream or error
            pipeline.set_state(Gst.State.PLAYING)
            with TimeMeasure("Decode "):
                self._loop.run()
            pipeline.set_state(Gst.State.PAUSED)
            if old_pipeline:
                old_pipeline.set_state(Gst.State.NULL)

        if not retain_pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

        # Return the cached raw preprocessed frames / jpegs and the corresponding timestamps.
        # Adjust for the PTS offset if any.
        self._cached_frames_pts = [t + chunk.pts_offset_ns / 1e9 for t in self._cached_frames_pts]

        for audio_frame in self._cached_audio_frames:
            audio_frame["start"] += chunk.pts_offset_ns / 1e9
            audio_frame["end"] += chunk.pts_offset_ns / 1e9

        # reset frame resoulution config after processing multiple files
        self._frame_width = self._frame_width_orig
        self._frame_height = self._frame_height_orig
        self._first_frame_width = 0
        self._first_frame_height = 0
        self.dump_cached_frame(
            self._cached_frames, self._cached_frames_pts, self._enable_jpeg_output
        )

        preprocessed_frames = self._preprocess(self._cached_frames)
        self._cached_frames = None
        self._frame_selector = frame_selector_backup

        # Return the cv meta to be passed to Graph RAG pipeline
        if chunk.cv_metadata_json_file and self._cached_frames_cv_meta:
            chunk.cached_frames_cv_meta = self._cached_frames_cv_meta
            self._cached_frames_cv_meta = []

        return preprocessed_frames, self._cached_frames_pts, self._cached_audio_frames

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
                logger.debug(f"Error during pipeline disposal: {e}")
                self._disposal_complete = True  # Mark as complete even on error

        # Start disposal thread
        disposal_thread = threading.Thread(target=disposal_thread)
        disposal_thread.start()

        # Wait for disposal to complete with timeout
        timeout = 120  # Total timeout in seconds
        start_time = time.time()
        while not self._disposal_complete:
            if time.time() - start_time > timeout:
                logger.error(f"ERROR: Pipeline disposal timed out after {timeout} seconds")
                break
            time.sleep(2)
            logger.debug("Waiting for pipeline disposal to complete...")

    def dispose_source(self, src):
        if src.set_state(Gst.State.NULL) != Gst.StateChangeReturn.SUCCESS:
            logger.error(f"Couldn't set state to NULL for {self._uridecodebin.get_name()}")
        logger.info("Source removed")

    def stream(
        self,
        live_stream_url: str,
        chunk_duration: int,
        on_chunk_decoded: Callable[
            [ChunkInfo, torch.Tensor | list[np.ndarray], list[float], list[dict]], None
        ],
        chunk_overlap_duration=0,
        username="",
        password="",
        enable_audio=False,
        enable_cv_pipeline=False,
        cv_pipeline_text_prompt="",
        live_stream_id="",
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
        self._last_frame_pts = 0
        self._stop_stream = False
        self._enable_audio = enable_audio

        if live_stream_id:
            self._live_stream_request_id = live_stream_id
        else:
            self._live_stream_request_id = str(uuid.uuid4())
        # Rerun the pipeline if it runs into errors like disconnection
        # Stop if pipeline stops with EOS
        while ((not self._pipeline) or self._got_error) and (not self._stop_stream):
            if self._got_error:
                logger.error("Live stream received error. Retrying after 5 seconds")
                time.sleep(5)
            self._got_error = False
            self._live_stream_next_chunk_start_pts = 0
            self._audio_current_pts = 0
            self._audio_present = False
            self._audio_eos = False
            self._enable_audio = enable_audio
            self._audio_start_pts = None
            self._audio_stop.clear()
            self._live_stream_ntp_epoch = 0
            self._live_stream_ntp_pts = 0
            self._cached_transcripts = []

            if enable_cv_pipeline:
                self._pipeline = self._create_osd_pipeline(
                    live_stream_url,
                    username=username,
                    password=password,
                    cv_pipeline_text_prompt=cv_pipeline_text_prompt,
                    request_id=self._live_stream_request_id,
                )
            else:
                self._pipeline = self._create_pipeline(live_stream_url, username, password)

            # Start streaming audio ASR in a separate thread if audio is enabled
            if enable_audio:

                def audio_streaming_thread():
                    try:
                        logger.debug("Starting audio streaming thread")
                        self._streaming_audio_asr()
                    except Exception as e:
                        logger.error(f"Error in audio streaming thread: {e}")
                        self._got_error = True

                self._audio_streaming_thread = threading.Thread(
                    target=audio_streaming_thread, daemon=True
                )
                self._audio_streaming_thread.start()

            logger.debug("Pipeline for live stream to PLAYING")
            self._pipeline.set_state(Gst.State.PLAYING)
            logger.debug("Pipeline for live stream to loop.run")
            self._loop.run()

            # Wait for audio streaming thread to complete
            if enable_audio:
                logger.debug("Waiting for audio streaming thread to complete")
                self._audio_stop.set()
                self._audio_streaming_thread.join()

            if self._rtspsrc:
                logger.debug(f"forcing EOS; {self._last_stream_id}")
                # Send EOS event to the source
                handled = self._rtspsrc.send_event(Gst.Event.new_eos())
                if self._nvtracker:
                    self._nvtracker.send_event(Gst.Event.new_eos())
                # time.sleep(1)
                logger.debug(f"EOS forced; {handled} : {self._last_stream_id}")
                self._rtspsrc.set_property("timeout", 0)
                if self._udpsrc:
                    logger.debug(
                        f"forcing udpsrc timeout to 0 before teardown; {self._last_stream_id}"
                    )
                    self._udpsrc.set_property("timeout", 0)

            # Need to remove source bin and then move pipeline to NULL
            # to avoid Gst bug:
            # https://discourse.gstreamer.org/t/gstreamer-1-16-3-setting-rtsp-pipeline-to-null/538/11
            # TODO: Try latest GStreamer version for any fixes
            logger.debug(f"pipe teardown: unlink_source : {self._last_stream_id}")
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
            logger.debug(f"pipe teardown: to NULL : {self._last_stream_id}")
            self.dispose_pipeline_from_separate_thread()
            logger.debug(f"pipe teardown: dispose_source : {self._last_stream_id}")
            GLib.idle_add(self.dispose_source, self._rtspsrc)
            GLib.idle_add(self.dispose_source, self._uridecodebin)
            logger.debug(f"pipe teardown: done : {self._last_stream_id}")
            self._process_finished_chunks(flush=True)

        self._pipeline = None
        self._live_stream_frame_selectors.clear()

    def stop_stream(self):
        self._stop_stream = True
        logger.debug("Force quit loop")
        self._audio_stop.set()
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
        "--start-time", type=int, default=0, help="Start time in sec to get frames from"
    )

    parser.add_argument(
        "--end-time", type=int, default=-1, help="End time in sec to get frames from"
    )

    parser.add_argument("--num-frames", type=int, default=8, help="Number of frames to get")

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

    parser.add_argument(
        "--enable-cv-pipeline",
        type=bool,
        default=False,
        help="enable CV pipeline",
    )

    args = parser.parse_args()

    frame_getter = VideoFileFrameGetter(
        frame_selector=DefaultFrameSelector(args.num_frames),
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
            on_chunk_decoded=lambda chunk, frames, frame_times, transcripts: print(
                f"Picked {len(frames)} frames with times: {frame_times} \
                for chunk {chunk}\n audio transcripts\n: {transcripts}\n\n\n"
            ),
            enable_audio=args.enable_audio,
            enable_cv_pipeline=args.enable_cv_pipeline,
        )
    else:
        chunk = ChunkInfo()
        chunk.file = args.file_or_rtsp
        chunk.start_pts = args.start_time * 1000000000
        chunk.end_pts = args.end_time * 1000000000 if args.end_time >= 0 else -1
        frames, frames_pts, audio_frames = frame_getter.get_frames(
            chunk, enable_audio=args.enable_audio
        )
        print(f"Picked {len(frames)} frames with times: {frames_pts}")
