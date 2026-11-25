# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from pathlib import Path
import struct
from urllib.parse import unquote, urlparse


def _cv2():
    import cv2

    return cv2


def _container_duration_seconds(path):
    """Read duration from an ISO-BMFF mvhd box without codec libraries."""
    try:
        file_size = path.stat().st_size
        with path.open("rb") as stream:
            offset = 0
            while offset + 8 <= file_size:
                stream.seek(offset)
                size, box_type = struct.unpack(">I4s", stream.read(8))
                header_size = 8
                if size == 1:
                    size = struct.unpack(">Q", stream.read(8))[0]
                    header_size = 16
                elif size == 0:
                    size = file_size - offset
                if size < header_size or offset + size > file_size:
                    break
                if box_type == b"moov":
                    child_offset = offset + header_size
                    box_end = offset + size
                    while child_offset + 8 <= box_end:
                        stream.seek(child_offset)
                        child_size, child_type = struct.unpack(">I4s", stream.read(8))
                        child_header_size = 8
                        if child_size == 1:
                            child_size = struct.unpack(">Q", stream.read(8))[0]
                            child_header_size = 16
                        elif child_size == 0:
                            child_size = box_end - child_offset
                        if child_size < child_header_size or child_offset + child_size > box_end:
                            break
                        if child_type == b"mvhd":
                            stream.seek(child_offset + child_header_size)
                            payload = stream.read(32)
                            if payload[0] == 1:
                                timescale = struct.unpack(">I", payload[20:24])[0]
                                duration = struct.unpack(">Q", payload[24:32])[0]
                            else:
                                timescale = struct.unpack(">I", payload[12:16])[0]
                                duration = struct.unpack(">I", payload[16:20])[0]
                            return duration / timescale if timescale else None
                        child_offset += child_size
                offset += size
    except (OSError, IndexError, struct.error):
        pass
    return None


class _NvidiaVideoCapture:
    def __init__(self, pipeline, path):
        self._pipeline = pipeline
        self._path = path
        self._cv2 = _cv2()
        self._capture = None
        self._position = 0
        self._frame_count = 0.0
        self._pending_frame = None
        self._fast_seek = True
        self._open()

    def _open(self):
        if self._capture is not None:
            self._capture.release()
        self._capture = self._cv2.VideoCapture(self._pipeline, self._cv2.CAP_GSTREAMER)
        self._position = 0
        self._pending_frame = None
        self._frame_count = self._capture.get(self._cv2.CAP_PROP_FRAME_COUNT)
        if self._frame_count <= 0:
            fps = self._capture.get(self._cv2.CAP_PROP_FPS)
            duration = _container_duration_seconds(self._path)
            if fps > 0 and duration is not None:
                self._frame_count = float(round(duration * fps))

    def isOpened(self):
        return self._capture.isOpened()

    def read(self):
        if self._pending_frame is not None:
            frame = self._pending_frame
            self._pending_frame = None
            self._position += 1
            return True, frame

        ok, frame = self._capture.read()
        if ok:
            self._position += 1
        return ok, frame

    def get(self, prop_id):
        if prop_id == self._cv2.CAP_PROP_POS_FRAMES:
            return float(self._position)
        if prop_id == self._cv2.CAP_PROP_FRAME_COUNT:
            return self._frame_count
        return self._capture.get(prop_id)

    def set(self, prop_id, value):
        if prop_id != self._cv2.CAP_PROP_POS_FRAMES:
            return self._capture.set(prop_id, value)

        target = max(0, int(value))
        if target == self._position:
            return True

        fps = self._capture.get(self._cv2.CAP_PROP_FPS)
        if self._fast_seek and fps > 0:
            # Some hardware pipelines accept a seek but fail on the next read.
            # Validate it now and cache the frame expected by the caller.
            if self._capture.set(self._cv2.CAP_PROP_POS_MSEC, target * 1000.0 / fps):
                ok, frame = self._capture.read()
                if ok:
                    self._pending_frame = frame
                    self._position = target
                    return True
            self._fast_seek = False
            self._open()

        if target < self._position:
            self._open()
        while self._position < target:
            ok, _ = self.read()
            if not ok:
                return False
        return True

    def release(self):
        self._capture.release()

    def __getattr__(self, name):
        return getattr(self._capture, name)


def open_video_capture(source):
    """Open local videos through NVIDIA GStreamer, preserving other sources."""
    cv2 = _cv2()
    if not isinstance(source, (str, Path)):
        return cv2.VideoCapture(source)

    source_text = str(source)
    parsed = urlparse(source_text)
    if parsed.scheme and parsed.scheme != "file":
        return cv2.VideoCapture(source_text)
    if " ! " in source_text:
        return cv2.VideoCapture(source_text, cv2.CAP_GSTREAMER)

    path = Path(unquote(parsed.path) if parsed.scheme == "file" else source_text).resolve()
    pipeline = (
        f"uridecodebin uri={path.as_uri()} ! "
        "nvvideoconvert ! video/x-raw,format=BGRx ! "
        "videoconvert ! video/x-raw,format=BGR ! "
        "appsink sync=false drop=false"
    )
    capture = _NvidiaVideoCapture(pipeline, path)
    if capture.isOpened():
        return capture

    capture.release()
    return cv2.VideoCapture(source_text)
