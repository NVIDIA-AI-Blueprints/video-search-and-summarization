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
"""VIA File Splitter"""

import os
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

import gi

from chunk_info import ChunkInfo
from utils import MediaFileInfo
from via_logger import TimeMeasure

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

Gst.init(None)


def get_timestamp_str(ts):
    """Get RFC3339 string timestamp"""
    return (
        datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        + f".{(int(ts * 1000) % 1000):03d}Z"
    )


def ntp_to_unix_timestamp(ntp_ts):
    """Convert an RFC3339 timestamp string to a UNIX timestamp(float)"""
    return (
        datetime.strptime(ntp_ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
    )


class FileSplitter:
    """File Splitter

    Splits files / streams into chunks based on configuration. Supports two modes:
    * "split" - Files are actually split into smaller chunk files.
    * "seek" - Files are not actually split, but the chunks contain start & end
               timestamps in the original file.
    """

    class SplitMode(Enum):
        SPLIT = "split"
        SEEK = "seek"

        def __str__(self):
            return self.value

    def __init__(
        self,
        stream: str,
        mode: SplitMode,
        chunk_duration_sec: int,
        on_new_chunk: Callable[[ChunkInfo], None],
        sliding_window_overlap_sec=0,
        start_pts: int | None = None,
        end_pts: int | None = None,
        output_file_prefix="",
        username="",
        password="",
        media_file_info: MediaFileInfo = None,
    ) -> None:
        """FileSplitter constructor.

        Args:
            stream: RTSP URL or file path
            mode: Split mode
            chunk_duration_sec: Chunk duration in seconds
            on_new_chunk: Callback when new chunks are generated
            sliding_window_overlap_sec: Chunk overlap duration in seconds. Defaults to 0.
            start_pts: Time in file to start chunking from, specified as nanoseconds.
                       Defaults to None.
            end_pts: Time in file to stop chunking at, specified as nanoseconds. Defaults to None.
            output_file_prefix: For "split" chunking mode, prefix of the output chunk files.
                                Defaults to "".
        """
        self._stream = stream
        self._split_mode = mode
        self._chunk_duration_sec = chunk_duration_sec
        self._sliding_window_overlap_sec = sliding_window_overlap_sec
        self._on_new_chunk = on_new_chunk
        self._output_file_prefix = output_file_prefix
        self._last_chunk_file = ""
        self._last_pts_offset = 0
        self._last_chunkidx = 0
        self._loop = None
        self._ntp_epoch = 0
        self._ntp_pts = 0
        self._start_pts = start_pts
        self._end_pts = end_pts
        self._base_ntp_time = 0
        self._got_error = False
        self._username = username
        self._password = password
        self._media_file_info = media_file_info

    def split(self):
        """Split the stream

        Returns:
            Boolean indicating if the split was successful or an error was encountered.
        """
        self._got_error = False
        self._last_chunk_file = ""
        self._last_pts_offset = 0
        self._last_chunkidx = 0
        with TimeMeasure("File Split"):
            if self._media_file_info is None:
                media_file_info = (
                    MediaFileInfo.get_info(self._stream) if ";" not in self._stream else None
                )
            else:
                media_file_info = self._media_file_info
            if self._split_mode == self.SplitMode.SEEK:
                # "seek" mode. File is not actually split.
                # Chunks are generated with start/end time in the original file.

                if self._stream.find(";") != -1:
                    # This is a list of image files
                    info = ChunkInfo()
                    info.chunkIdx = 0
                    info.file = self._stream
                    info.pts_offset_ns = 0
                    info.start_pts = 0
                    info.end_pts = 0
                    info.start_ntp = get_timestamp_str(self._base_ntp_time + info.start_pts / 1e9)
                    info.end_ntp = get_timestamp_str(self._base_ntp_time + info.end_pts / 1e9)
                    info.is_first = True
                    self._on_new_chunk(info)
                    return
                elif media_file_info.is_image:
                    info = ChunkInfo()
                    info.chunkIdx = 0
                    info.file = self._stream
                    info.pts_offset_ns = 0
                    info.start_pts = 0
                    info.end_pts = 0
                    info.start_ntp = get_timestamp_str(self._base_ntp_time + info.start_pts / 1e9)
                    info.end_ntp = get_timestamp_str(self._base_ntp_time + info.end_pts / 1e9)
                    info.is_first = True
                    self._on_new_chunk(info)
                    return

                # Calculate the start / end times for chunking
                duration = media_file_info.video_duration_nsec
                cur_pts = 0 if self._start_pts is None or self._start_pts < 0 else self._start_pts
                end_pts = (
                    duration if self._end_pts is None or self._end_pts > duration else self._end_pts
                )

                # If chunk duration is 0 (no chunking), generate a single chunk
                # by setting chunk duration to file duration.
                if self._chunk_duration_sec == 0:
                    self._chunk_duration_sec = duration

                chunkIdx = 0
                # Iterate while end of file (based on duration) is not reached
                while cur_pts < end_pts:
                    # Create and populate a new chunk from cur_pts to cur_pts + chunk duration
                    info = ChunkInfo()
                    info.chunkIdx = chunkIdx
                    info.is_first = True if chunkIdx == 0 else False
                    info.file = self._stream
                    info.pts_offset_ns = 0
                    info.start_pts = cur_pts
                    # Handle edge case of end_pts going over the file duration.
                    info.end_pts = cur_pts + min(
                        self._chunk_duration_sec * 1000000000, end_pts - cur_pts
                    )
                    info.start_ntp = get_timestamp_str(self._base_ntp_time + info.start_pts / 1e9)
                    info.end_ntp = get_timestamp_str(self._base_ntp_time + info.end_pts / 1e9)
                    info.start_ntp_float = ntp_to_unix_timestamp(info.start_ntp)
                    info.end_ntp_float = ntp_to_unix_timestamp(info.end_ntp)
                    self._on_new_chunk(info)
                    chunkIdx += 1
                    # Get the next chunk start time
                    cur_pts += (
                        self._chunk_duration_sec - self._sliding_window_overlap_sec
                    ) * 1000000000
                    # if the end time of the chunking is already reached stop iterating
                    if info.end_pts >= end_pts:
                        break

            if self._split_mode == self.SplitMode.SPLIT:
                # "split" mode. Acutally split into vide chunk files.

                # Create the Gstreamer pipeline
                # urisourcebin -> parsebin -> splitmuxsink
                pipeline = Gst.Pipeline()

                srcbin = Gst.ElementFactory.make("urisourcebin")
                srcbin.set_property(
                    "uri",
                    (
                        self._stream
                        if self._stream.startswith("rtsp://")
                        else "file://" + os.path.abspath(self._stream)
                    ),
                )
                pipeline.add(srcbin)

                def cb_ntpquery(pad, info, data):
                    # Probe callback to handle NTP information from RTSP stream
                    # This requires RTSP Sender Report support in the source.
                    query = info.get_query()
                    if query.type == Gst.QueryType.CUSTOM:
                        struct = query.get_structure()
                        if "nvds-ntp-sync" == struct.get_name():
                            _, data._ntp_epoch = struct.get_uint64("ntp-time-epoch-ns")
                            _, data._ntp_pts = struct.get_uint64("frame-timestamp")
                    return Gst.PadProbeReturn.OK

                def cb_newpad_srcbin(srcbin, srcbin_src_pad, parsebin):
                    # Callback for handling a new elementary stream in source
                    caps = srcbin_src_pad.query_caps()
                    if not caps:
                        return

                    # For video stream, link to the parsebin and add a probe to
                    # handle NTP information.
                    if "video" in caps.to_string():
                        srcbin.link(parsebin)
                        srcbin_src_pad.add_probe(
                            Gst.PadProbeType.QUERY_DOWNSTREAM, cb_ntpquery, self
                        )

                    # Ignore audio stream, link it to a fakesink
                    if "audio" in caps.to_string():
                        fsink = Gst.ElementFactory.make("fakesink")
                        pipeline.add(fsink)
                        fsink.set_property("async", False)
                        srcbin.link(fsink)
                        fsink.set_state(Gst.State.PLAYING)

                def cb_element_added_srcbin(srcbin, bin, elem, spliiter):
                    # Callback when the source bin gets created

                    # If the stream is RTSP, configure the source element to retrieve
                    # NTP information as well as set UDP connection timeout to 2 sec.
                    if "rtspsrc" in elem.get_name():
                        import pyds

                        pyds.configure_source_for_ntp_sync(hash(elem))
                        elem.set_property("timeout", 2000000)

                        if self._username and self._password:
                            elem.set_property("user-id", self._username)
                            elem.set_property("user-pw", self._password)

                def cb_newpad(parsebin, parser_src_pad, splitmuxsink):
                    # Callback when a new elementary stream is created by parsebin
                    caps = parser_src_pad.query_caps()
                    if not caps:
                        return
                    gststruct = caps.get_structure(0)
                    gstname = gststruct.get_name()

                    # For h264 / h265 streams, add a parser and link it between
                    # parsebin and splitmuxsink
                    if "video" in gstname:
                        if "h264" in gstname:
                            parse = Gst.ElementFactory.make("h264parse")
                            pipeline.add(parse)
                            parsebin.link(parse)
                            parse.link(splitmuxsink)
                            parse.set_state(Gst.State.PLAYING)
                        if "h265" in gstname:
                            parse = Gst.ElementFactory.make("h265parse")
                            pipeline.add(parse)
                            parsebin.link(parse)
                            parse.link(splitmuxsink)
                            parse.set_state(Gst.State.PLAYING)

                def cb_format_location(splitmuxsink, fragmentid, sample, data):
                    # Callback when splitmuxsink is about write a new chunk

                    if self._last_chunk_file:
                        # New chunk is about to be started, create chunk object
                        # for old chunk and call the callback function
                        info = ChunkInfo()
                        info.chunkIdx = self._last_chunkidx
                        info.file = self._last_chunk_file
                        info.pts_offset_ns = self._last_pts_offset

                        info.start_pts = info.pts_offset_ns
                        info.end_pts = sample.get_buffer().pts

                        if self._ntp_epoch:
                            base_time = (self._ntp_epoch - self._ntp_pts) / 1000000000
                        else:
                            base_time = self._base_ntp_time
                        info.start_ntp = get_timestamp_str(base_time + info.start_pts / 1e9)
                        info.end_ntp = get_timestamp_str(base_time + info.end_pts / 1e9)

                        data._on_new_chunk(info)

                    # Save the info about new chunk to be written so that it can be used
                    # in the next callback.
                    self._last_chunk_file = data._output_file_prefix + "_%05d.mp4" % fragmentid
                    self._last_pts_offset = sample.get_buffer().pts
                    self._last_chunkidx = fragmentid
                    return None

                def bus_call(bus, message, loop):
                    t = message.type
                    if t == Gst.MessageType.EOS:
                        sys.stdout.write("End-of-stream\n")
                        loop.quit()
                    elif t == Gst.MessageType.WARNING:
                        err, debug = message.parse_warning()
                        sys.stderr.write("Warning: %s: %s\n" % (err, debug))
                    elif t == Gst.MessageType.ERROR:
                        err, debug = message.parse_error()
                        sys.stderr.write("Error: %s: %s\n" % (err, debug))
                        self._got_error = True
                        loop.quit()
                    return True

                parsebin = Gst.ElementFactory.make("parsebin")
                pipeline.add(parsebin)

                splitmuxsink = Gst.ElementFactory.make("splitmuxsink")
                splitmuxsink.set_property("max-size-time", self._chunk_duration_sec * 1000000000)
                splitmuxsink.set_property("location", self._output_file_prefix + "_%05d.mp4")
                pipeline.add(splitmuxsink)

                srcbin.connect("pad-added", cb_newpad_srcbin, parsebin)
                srcbin.connect("deep-element-added", cb_element_added_srcbin, self)
                parsebin.connect("pad-added", cb_newpad, splitmuxsink)
                splitmuxsink.connect("format-location-full", cb_format_location, self)

                self._loop = GLib.MainLoop()
                bus = pipeline.get_bus()
                bus.add_signal_watch()
                bus.connect("message", bus_call, self._loop)

                pipeline.set_state(Gst.State.PLAYING)
                self._loop.run()
                pipeline.set_state(Gst.State.NULL)
                self._loop = None

            # Call the callback one extra time with None, to indicate no more
            # chunks will be created
            self._on_new_chunk(None)
        return not self._got_error

    def stop_split(self):
        if self._loop:
            self._loop.quit()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="File Splitter test")
    parser.add_argument(
        "--split-mode",
        type=FileSplitter.SplitMode,
        choices=list(FileSplitter.SplitMode),
        default=FileSplitter.SplitMode.SEEK,
        help="File split mode",
    )
    parser.add_argument(
        "--sliding-window-overlap-sec",
        type=int,
        default=0,
        help="Sliding window overlap between chunks in seconds",
    )
    parser.add_argument("--output-file-prefix", type=str, help="Output file prefix for split mode")
    parser.add_argument("file", type=str, help="File to run the splitter on")
    parser.add_argument("chunk_size", type=int, help="Chunk Size in seconds")

    args = parser.parse_args()

    def test_on_new_chunk(info: ChunkInfo):
        if info:
            print(
                f"Got Chunk: idx={info.chunkIdx} file={info.file} "
                f"pts_offset_ns={info.pts_offset_ns} start_pts={info.start_pts} "
                f"end_pts={info.end_pts}"
            )

    splitter = FileSplitter(
        args.file,
        args.split_mode,
        args.chunk_size,
        test_on_new_chunk,
        args.sliding_window_overlap_sec,
        output_file_prefix=args.output_file_prefix,
    )
    splitter.split()
