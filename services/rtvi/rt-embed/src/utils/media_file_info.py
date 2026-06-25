# SPDX-FileCopyrightText: Copyright (c) 2023-2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Media File Info."""

import asyncio
import concurrent.futures
import os
from dataclasses import dataclass

import gi
from pymediainfo import MediaInfo

gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")

from gi.repository import Gst  # noqa: E402
from gi.repository import GstPbutils  # noqa: E402

Gst.init(None)

# Dedicated thread pool for media info extraction
# Allows concurrent ffprobe operations without blocking the default thread pool
_media_info_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=10, thread_name_prefix="mediainfo_"
)


@dataclass
class MediaFileInfo:
    is_image: bool = False
    video_codec: str = ""
    video_duration_nsec: int = 0
    video_fps: float = 0.0
    video_resolution: tuple[int, int] = (0, 0)

    @staticmethod
    def _get_info_gst(uri_or_file: str, username="", password=""):
        uri_or_file = str(uri_or_file)
        media_file_info = MediaFileInfo()

        if uri_or_file.startswith("rtsp://") or uri_or_file.startswith("file://"):
            uri = uri_or_file
        else:
            uri = "file://" + os.path.abspath(str(uri_or_file))

        def select_stream(source, idx, caps):
            if "audio" in caps.to_string():
                return False
            return True

        def source_setup(discoverer, source):
            if uri.startswith("rtsp://"):
                source.connect("select-stream", select_stream)
                source.set_property("timeout", 1000000)
                if username and password:
                    source.set_property("user-id", username)
                    source.set_property("user-pw", password)

        discoverer = GstPbutils.Discoverer()
        discoverer.connect("source-setup", source_setup)

        try:
            file_info = discoverer.discover_uri(uri)
        except gi.repository.GLib.GError as e:
            raise Exception("Unsupported file type - " + uri + " Error:" + str(e))
        for stream_info in file_info.get_stream_list():
            if isinstance(stream_info, GstPbutils.DiscovererVideoInfo):
                media_file_info.video_duration_nsec = int(file_info.get_duration())
                media_file_info.video_codec = str(
                    GstPbutils.pb_utils_get_codec_description(stream_info.get_caps())
                )
                media_file_info.video_resolution = (
                    int(stream_info.get_width()),
                    int(stream_info.get_height()),
                )
                media_file_info.video_fps = float(
                    stream_info.get_framerate_num() / stream_info.get_framerate_denom()
                )
                media_file_info.is_image = bool(stream_info.is_image())
                break
        return media_file_info

    @staticmethod
    def _get_info_mediainfo(uri_or_file: str):
        if uri_or_file.startswith("file://"):
            file = uri_or_file[7:]
        else:
            file = uri_or_file

        media_file_info = MediaFileInfo()
        media_info = MediaInfo.parse(file)
        have_image_or_video = False
        for track in media_info.tracks:
            if track.track_type == "Video":
                media_file_info.is_image = False
                media_file_info.video_codec = track.format
                media_file_info.video_duration_nsec = float(track.duration) * 1000000
                media_file_info.video_fps = track.frame_rate
                media_file_info.video_resolution = (track.width, track.height)
                have_image_or_video = True
                return media_file_info
            if track.track_type == "Image":
                media_file_info.is_image = True
                media_file_info.video_codec = track.format
                media_file_info.video_duration_nsec = 0
                media_file_info.video_fps = 0
                media_file_info.video_resolution = (track.width, track.height)
                have_image_or_video = True

        if not have_image_or_video:
            raise Exception("Unsupported file type - " + file)
        return media_file_info

    @staticmethod
    def get_info(uri_or_file: str, username="", password=""):
        if str(uri_or_file).startswith("rtsp://"):
            return MediaFileInfo._get_info_gst(uri_or_file, username, password)
        else:
            return MediaFileInfo._get_info_mediainfo(str(uri_or_file))

    @staticmethod
    async def get_info_async(uri_or_file: str, username="", password=""):
        return await asyncio.get_event_loop().run_in_executor(
            _media_info_executor, MediaFileInfo.get_info, uri_or_file, username, password
        )
