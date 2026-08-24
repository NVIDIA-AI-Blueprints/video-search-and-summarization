# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_media_file_info_module(monkeypatch, tracks):
    class MediaInfoStub:
        @staticmethod
        def parse(_file):
            return SimpleNamespace(tracks=tracks)

    gst_stub = SimpleNamespace(init=lambda _args: None)
    gst_pbutils_stub = SimpleNamespace(
        Discoverer=lambda: None,
        DiscovererVideoInfo=type("DiscovererVideoInfo", (), {}),
        pb_utils_get_codec_description=lambda _caps: "",
    )
    gi_repository_stub = SimpleNamespace(Gst=gst_stub, GstPbutils=gst_pbutils_stub)
    gi_stub = SimpleNamespace(
        require_version=lambda _name, _version: None,
        repository=gi_repository_stub,
    )

    monkeypatch.setitem(sys.modules, "gi", gi_stub)
    monkeypatch.setitem(sys.modules, "gi.repository", gi_repository_stub)
    monkeypatch.setitem(sys.modules, "gi.repository.Gst", gst_stub)
    monkeypatch.setitem(sys.modules, "gi.repository.GstPbutils", gst_pbutils_stub)
    monkeypatch.setitem(
        sys.modules, "pymediainfo", SimpleNamespace(MediaInfo=MediaInfoStub)
    )

    module_path = Path(__file__).parents[2] / "src" / "utils" / "media_file_info.py"
    module_name = "_test_media_file_info"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def test_mediainfo_uses_container_duration_when_video_track_duration_missing(monkeypatch):
    tracks = [
        SimpleNamespace(track_type="General", duration="40971822"),
        SimpleNamespace(
            track_type="Video",
            format="AVC",
            duration=None,
            frame_rate=None,
            original_frame_rate="7.000",
            width=3840,
            height=2160,
        ),
    ]
    media_file_info = _load_media_file_info_module(monkeypatch, tracks).MediaFileInfo

    info = media_file_info._get_info_mediainfo("/tmp/input.mkv")

    assert info.video_duration_nsec == 40971822000000
    assert info.video_fps == 7.0
    assert info.video_resolution == (3840, 2160)


def test_mediainfo_defaults_missing_video_fps_to_zero(monkeypatch):
    tracks = [
        SimpleNamespace(track_type="General", duration="1000"),
        SimpleNamespace(
            track_type="Video",
            format="AVC",
            duration=None,
            frame_rate=None,
            original_frame_rate=None,
            width=1920,
            height=1080,
        ),
    ]
    media_file_info = _load_media_file_info_module(monkeypatch, tracks).MediaFileInfo

    info = media_file_info._get_info_mediainfo("/tmp/input.mkv")

    assert info.video_duration_nsec == 1000000000
    assert info.video_fps == 0.0
