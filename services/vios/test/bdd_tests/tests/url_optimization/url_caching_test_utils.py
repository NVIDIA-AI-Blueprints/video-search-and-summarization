# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Shared utilities for URL caching optimization BDD tests.
"""
import logging
import re
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

ENDPOINTS = {
    'streams': '/vst/api/v1/replay/streams',
    'storage_size': '/vst/api/v1/storage/size',
    'picture_url': '/vst/api/v1/replay/stream/{stream_id}/picture/url',
    'video_url': '/vst/api/v1/storage/file/{stream_id}/url',
}

_ENVOY_SENSOR_PREFIX_RE = re.compile(
    r"^(test_upload_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def envoy_streamid_route_key(stream_id: Optional[str]) -> str:
    """Value for the streamid header used by Envoy routing."""
    if not stream_id:
        return ""
    m = _ENVOY_SENSOR_PREFIX_RE.match(stream_id)
    return m.group(1) if m else stream_id


class CachingTestContext:
    """Context to store test data between URL caching test steps."""
    def __init__(self):
        self.streams: List[Dict[str, Any]] = []
        self.timelines: Dict[str, Any] = {}
        self.selected_stream_id: str = ""
        self.selected_timestamp: str = ""
        self.selected_start_time: str = ""
        self.selected_end_time: str = ""
        self.first_response: Dict[str, Any] = {}
        self.second_response: Dict[str, Any] = {}
        self.first_request_duration: float = 0.0
        self.second_request_duration: float = 0.0


def extract_filename_from_url(url: str) -> str:
    """Extract the filename component from a temp file URL path."""
    return PurePosixPath(url).name


def select_replay_timestamp(streams: List[Dict[str, Any]],
                            timelines: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Pick a single stream+timestamp pair from available timelines.

    Returns dict with 'stream_id' and 'timestamp', or None if no suitable data.
    """
    for stream_obj in streams:
        if not isinstance(stream_obj, dict):
            continue
        for stream_name in stream_obj.keys():
            stream_data = timelines.get(stream_name)
            if not isinstance(stream_data, dict):
                continue
            timeline_list = stream_data.get('timelines', [])
            if not timeline_list:
                continue

            for timeline in timeline_list:
                start_str = timeline.get('startTime')
                end_str = timeline.get('endTime')
                if not start_str or not end_str:
                    continue
                try:
                    start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                    end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                    if (end - start).total_seconds() < 5:
                        continue
                    mid = start + (end - start) / 2
                    return {
                        'stream_id': stream_name,
                        'timestamp': mid.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
                    }
                except (ValueError, AttributeError):
                    continue
    return None


def select_video_time_range(streams: List[Dict[str, Any]],
                            timelines: Dict[str, Any],
                            duration_sec: int = 5) -> Optional[Dict[str, str]]:
    """
    Pick a single stream+time-range for video URL testing.

    Returns dict with 'stream_id', 'start_time', 'end_time', or None.
    """
    for stream_obj in streams:
        if not isinstance(stream_obj, dict):
            continue
        for stream_name in stream_obj.keys():
            stream_data = timelines.get(stream_name)
            if not isinstance(stream_data, dict):
                continue
            timeline_list = stream_data.get('timelines', [])
            if not timeline_list:
                continue

            for timeline in timeline_list:
                start_str = timeline.get('startTime')
                end_str = timeline.get('endTime')
                if not start_str or not end_str:
                    continue
                try:
                    start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                    end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                    if (end - start).total_seconds() < duration_sec * 2:
                        continue
                    mid = start + (end - start) / 2
                    clip_end = mid + timedelta(seconds=duration_sec)
                    if clip_end > end:
                        clip_end = end
                    fmt = lambda dt: dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                    return {
                        'stream_id': stream_name,
                        'start_time': fmt(mid),
                        'end_time': fmt(clip_end),
                    }
                except (ValueError, AttributeError):
                    continue
    return None
