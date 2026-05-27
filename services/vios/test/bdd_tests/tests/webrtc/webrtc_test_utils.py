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
Shared utilities for WebRTC BDD tests.
"""
import logging
import time
import uuid
from typing import List, Dict, Any, Optional

import requests
import websockets
from aiortc import RTCIceCandidate, RTCPeerConnection

logger = logging.getLogger(__name__)

# API Endpoints
ENDPOINTS_LIVE = {
    'streams': '/vst/api/v1/live/streams',
    'websocket': '/vst/api/v1/live/ws'
}

ENDPOINTS_REPLAY = {
    'streams': '/vst/api/v1/replay/streams',
    'websocket': '/vst/api/v1/replay/ws'
}


class WebRTCStreamContext:
    """Context for a single WebRTC stream connection."""
    
    def __init__(self, stream_id: str, index: int = 0):
        self.index: int = index
        self.connection_id: str = str(uuid.uuid4())
        self.peer_id: str = str(uuid.uuid4())
        self.stream_id: str = stream_id
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.peer_connection: Optional[RTCPeerConnection] = None
        self.configuration: Optional[Dict[str, Any]] = None
        self.ice_servers: Optional[Dict[str, Any]] = None
        self.media_session_id: Optional[str] = None
        self.video_tracker: 'VideoFrameTracker' = None
        self.ice_connection_state: Optional[str] = None
        self.success: bool = False
        self.error: Optional[str] = None
    
    def initialize_tracker(self):
        """Initialize video frame tracker."""
        self.video_tracker = VideoFrameTracker()


class WebRTCContext:
    """Base context to store test data between WebRTC test steps."""
    def __init__(self):
        self.streams: List[Dict[str, Any]] = []
        self.stream_results: List[WebRTCStreamContext] = []
        self.test_data: List[Dict[str, Any]] = []


class VideoFrameTracker:
    """Track received video frames to measure framerate."""
    
    def __init__(self):
        self.frames: List[float] = []
        self.start_time: Optional[float] = None
        self.frame_count: int = 0
        
    def add_frame(self, timestamp: Optional[float] = None):
        """Record a received frame."""
        if timestamp is None:
            timestamp = time.time()
        
        if self.start_time is None:
            self.start_time = timestamp
            
        self.frames.append(timestamp)
        self.frame_count += 1
        
    def get_duration(self) -> float:
        """Get the duration of frame collection in seconds."""
        if self.start_time is None or len(self.frames) == 0:
            return 0.0
        return self.frames[-1] - self.start_time
        
    def get_framerate(self) -> float:
        """Calculate average framerate."""
        if len(self.frames) < 2 or self.start_time is None:
            return 0.0
            
        duration = self.get_duration()
        if duration <= 0:
            return 0.0
            
        return self.frame_count / duration
        
    def is_healthy(self, min_fps: float = 10.0, min_frames: int = 30, min_duration: float = 0.0) -> bool:
        """Check if framerate is healthy."""
        if self.frame_count < min_frames:
            return False
        
        duration = self.get_duration()
        if duration < min_duration:
            return False
            
        fps = self.get_framerate()
        return fps >= min_fps
    
    def get_summary(self) -> str:
        """Get a summary string of frame statistics."""
        fps = self.get_framerate()
        duration = self.get_duration()
        return f"{self.frame_count} frames, {fps:.2f} FPS, {duration:.2f}s duration"


def parse_ice_candidate(candidate_str: str, sdp_mid: str, sdp_mline_index: int) -> RTCIceCandidate:
    """Parse ICE candidate string into RTCIceCandidate object."""
    if candidate_str.startswith("candidate:"):
        candidate_str = candidate_str[len("candidate:"):]
    
    parts = candidate_str.split()
    if len(parts) < 8:
        raise ValueError(f"Invalid ICE candidate: {candidate_str}")
    
    return RTCIceCandidate(
        foundation=parts[0],
        component=int(parts[1]),
        protocol=parts[2],
        priority=int(parts[3]),
        ip=parts[4],
        port=int(parts[5]),
        type=parts[7],
        sdpMid=sdp_mid,
        sdpMLineIndex=sdp_mline_index
    )


def fetch_streams(api_base_url: str, endpoint: str, timeout: int, verify_ssl: bool) -> List[Dict[str, Any]]:
    """Fetch available streams from the API."""
    url = f"{api_base_url}{endpoint}"
    
    response = requests.get(
        url,
        timeout=timeout,
        verify=verify_ssl
    )
    response.raise_for_status()
    
    streams = response.json()
    logger.info("Fetched %d stream(s)", len(streams))
    return streams


def extract_stream_names(streams: List[Dict[str, Any]]) -> List[str]:
    """Extract stream names/IDs from streams response."""
    stream_names = []
    for stream_obj in streams:
        if isinstance(stream_obj, dict):
            for stream_name in stream_obj.keys():
                stream_names.append(stream_name)
    
    logger.info("Extracted %d stream name(s)", len(stream_names))
    return stream_names
