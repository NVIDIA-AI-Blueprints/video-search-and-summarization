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

import asyncio
import json
import logging
import random
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

import pytest
import requests
import websockets
from aiortc import (
    RTCPeerConnection, 
    RTCSessionDescription, 
    RTCIceCandidate, 
    RTCConfiguration,
    RTCIceServer,
)
from pytest_bdd import scenarios, given, when, then

from ..test_utils import assert_with_detailed_failure

logger = logging.getLogger(__name__)


# Load scenarios from the feature file
scenarios('../../features/webrtc/replay_webrtc_stream.feature')


# API Endpoints for this test
ENDPOINTS = {
    'streams': '/vst/api/v1/replay/streams',
    'storage_size': '/vst/api/v1/storage/size',
    'websocket': '/vst/api/v1/replay/ws'
}


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


class WebRTCStreamContext:
    """Context for a single WebRTC replay stream connection."""
    
    def __init__(self, stream_id: str, start_time: str, end_time: str, index: int = 0):
        self.index: int = index
        self.connection_id: str = str(uuid.uuid4())
        self.peer_id: str = str(uuid.uuid4())
        self.stream_id: str = stream_id
        self.start_time: str = start_time
        self.end_time: str = end_time
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.peer_connection: Optional[RTCPeerConnection] = None
        self.configuration: Optional[Dict[str, Any]] = None
        self.ice_servers: Optional[Dict[str, Any]] = None
        self.media_session_id: Optional[str] = None
        self.video_tracker: VideoFrameTracker = VideoFrameTracker()
        self.ice_connection_state: Optional[str] = None
        self.success: bool = False
        self.error: Optional[str] = None


class ScenarioContext:
    """Context to store test data between steps."""
    
    def __init__(self):
        self.streams: List[Dict[str, Any]] = []
        self.timelines: Dict[str, Any] = {}
        self.test_data: List[Dict[str, Any]] = []
        self.stream_results: List[WebRTCStreamContext] = []


@pytest.fixture
def context():
    """Create a test context."""
    return ScenarioContext()


@pytest.fixture
def test_endpoints():
    """Get test endpoints configuration."""
    return ENDPOINTS


@given('the VST API and WebSocket are configured')
def vst_configured(api_config, test_endpoints):
    """Verify VST API and WebSocket configuration is available."""
    assert api_config['base_url'], "Base URL must be configured"
    assert test_endpoints['streams'], "Streams endpoint must be configured"
    assert test_endpoints['storage_size'], "Storage size endpoint must be configured"
    assert test_endpoints['websocket'], "WebSocket endpoint must be configured"


@when('the list of available replay streams is fetched')
def fetch_streams(context, api_config, test_endpoints, test_params):
    """Fetch the list of available replay streams from the API."""
    url = f"{api_config['base_url']}{test_endpoints['streams']}"
    
    response = requests.get(
        url,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False)
    )
    response.raise_for_status()
    
    context.streams = response.json()
    assert len(context.streams) > 0, "No streams found"
    
    logger.info("Fetched %d stream(s) from API", len(context.streams))


@when('valid time ranges from the timelines are selected')
def select_time_ranges(context, api_config, test_endpoints, test_params):
    """Fetch timelines and select valid time ranges for replay."""
    # Fetch timelines
    url = f"{api_config['base_url']}{test_endpoints['storage_size']}"
    params = {'timelines': 'true'}
    
    response = requests.get(
        url,
        params=params,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False)
    )
    response.raise_for_status()
    
    context.timelines = response.json()
    assert context.timelines, "No timeline data found"
    
    # Extract stream names from streams response
    all_stream_names = []
    for stream_obj in context.streams:
        if isinstance(stream_obj, dict):
            all_stream_names.extend(list(stream_obj.keys()))
    
    assert len(all_stream_names) > 0, "No stream names found"
    
    # Filter to only include streams with valid non-empty timelines
    # Also exclude test_upload_ sensors
    valid_stream_names = []
    excluded_test_uploads = 0
    no_timeline_count = 0
    empty_timeline_count = 0
    
    for stream_name in all_stream_names:
        # Skip test upload sensors
        if stream_name.startswith("test_upload_"):
            excluded_test_uploads += 1
            logger.debug("Excluding test upload sensor: %s", stream_name)
            continue
        
        # Check if stream has timeline data
        stream_timeline_data = context.timelines.get(stream_name)
        
        if not stream_timeline_data or 'timelines' not in stream_timeline_data:
            no_timeline_count += 1
            logger.debug("No timeline data for %s", stream_name)
            continue
        
        timelines = stream_timeline_data['timelines']
        
        if not isinstance(timelines, list) or len(timelines) == 0:
            empty_timeline_count += 1
            logger.debug("Empty timeline for %s", stream_name)
            continue
        
        # Stream has valid timeline
        valid_stream_names.append(stream_name)
    
    # Log filtering results
    if excluded_test_uploads > 0:
        logger.info("Excluded %d test upload sensor(s)", excluded_test_uploads)
    if no_timeline_count > 0:
        logger.info("Excluded %d sensor(s) with no timeline data", no_timeline_count)
    if empty_timeline_count > 0:
        logger.info("Excluded %d sensor(s) with empty timelines", empty_timeline_count)
    
    # Fail if no valid streams found
    assert len(valid_stream_names) > 0, \
        f"No valid streams with timelines found. " \
        f"Total streams: {len(all_stream_names)}, " \
        f"Test uploads excluded: {excluded_test_uploads}, " \
        f"No timeline: {no_timeline_count}, " \
        f"Empty timeline: {empty_timeline_count}"
    
    logger.info("Found %d valid stream(s) with timelines", len(valid_stream_names))
    
    # Randomly select streams for testing
    parallelism = test_params.get('parallelism', 1)
    num_streams_to_test = min(parallelism, len(valid_stream_names))
    selected_stream_names = random.sample(valid_stream_names, num_streams_to_test)
    
    logger.info("Selected %d stream(s) for testing: %s", len(selected_stream_names), ', '.join(selected_stream_names))
    
    # Generate test data from timelines
    test_data = []
    replay_duration = test_params.get('replay_duration_seconds', 60)
    
    for stream_name in selected_stream_names:
        stream_timeline_data = context.timelines.get(stream_name)
        timelines = stream_timeline_data['timelines']
        
        # Use the first timeline
        timeline = timelines[0]
        start_time_str = timeline.get('startTime')
        end_time_str = timeline.get('endTime')
        
        if not start_time_str or not end_time_str:
            continue
        
        try:
            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
            
            # Calculate middle time and add replay duration
            middle_time = start_time + (end_time - start_time) / 2
            replay_end_time = middle_time + timedelta(seconds=replay_duration)
            
            # Ensure end time doesn't exceed timeline end
            if replay_end_time > end_time:
                replay_end_time = end_time
            
            test_data.append({
                'stream_id': stream_name,
                'start_time': middle_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
                'end_time': replay_end_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
            })
        except (ValueError, AttributeError):
            continue
    
    context.test_data = test_data
    assert len(context.test_data) > 0, "No valid test data found from timelines"
    
    logger.info("Generated %d test time range(s)", len(context.test_data))


async def establish_single_webrtc_stream(
    stream_ctx: WebRTCStreamContext,
    api_config: Dict[str, Any],
    test_endpoints: Dict[str, str],
    test_params: Dict[str, Any]
) -> Dict[str, Any]:
    """Establish a single WebRTC replay connection for one stream."""
    
    try:
        # Build WebSocket URL
        base_url = api_config['base_url']
        parsed = urlparse(base_url)
        ws_scheme = 'wss' if parsed.scheme == 'https' else 'ws'
        ws_url = f"{ws_scheme}://{parsed.netloc}{test_endpoints['websocket']}"
        ws_url += f"?connectionId={stream_ctx.connection_id}&streamId={stream_ctx.stream_id}"
        
        logger.info("  [%d] Connecting to replay: %s (%s to %s)", stream_ctx.index, stream_ctx.stream_id, stream_ctx.start_time, stream_ctx.end_time)
        
        # Connect to WebSocket
        stream_ctx.websocket = await websockets.connect(
            ws_url,
            ping_interval=None,
            ping_timeout=None
        )
        
        # Background task for keepalive pings
        async def send_keepalive_pings():
            try:
                while not stream_ctx.websocket.closed:
                    await asyncio.sleep(10)
                    if not stream_ctx.websocket.closed:
                        await stream_ctx.websocket.send(json.dumps({"apiKey": "api/v1/replay/ping"}))
            except websockets.exceptions.ConnectionClosed:
                pass
            except Exception as e:
                logger.debug("Keepalive ping error: %s", e)
        
        ping_task = asyncio.create_task(send_keepalive_pings())
        
        # Request configuration
        await stream_ctx.websocket.send(json.dumps({
            "apiKey": "api/v1/replay/configuration",
            "data": None,
            "peerId": stream_ctx.peer_id
        }))
        
        # Request ICE servers
        await stream_ctx.websocket.send(json.dumps({
            "apiKey": "api/v1/replay/iceServers",
            "peerId": stream_ctx.peer_id,
            "data": {"peerId": stream_ctx.peer_id}
        }))
        
        # Wait for configuration and ICE servers
        received_config = False
        received_ice = False
        ice_configuration = None
        
        while not (received_config and received_ice):
            message_str = await asyncio.wait_for(stream_ctx.websocket.recv(), timeout=10.0)
            message = json.loads(message_str)
            api_key = message.get('apiKey', '')
            
            if api_key == 'api/v1/replay/configuration':
                stream_ctx.configuration = message.get('data', {})
                received_config = True
            elif api_key == 'api/v1/replay/iceServers':
                stream_ctx.ice_servers = message.get('data', {})
                ice_servers_list = stream_ctx.ice_servers.get('iceServers', [])
                received_ice = True
                
                if ice_servers_list:
                    ice_configuration = RTCConfiguration(
                        iceServers=[RTCIceServer(urls=srv['urls']) for srv in ice_servers_list]
                    )
        
        # Create RTCPeerConnection
        stream_ctx.peer_connection = RTCPeerConnection(configuration=ice_configuration) if ice_configuration else RTCPeerConnection()
        
        # Setup event handlers
        @stream_ctx.peer_connection.on("iceconnectionstatechange")
        async def on_ice_connection_state_change():
            state = stream_ctx.peer_connection.iceConnectionState
            logger.info("  [%d] ICE connection state: %s", stream_ctx.index, state)
            if state in ('connected', 'completed'):
                stream_ctx.ice_connection_state = state
        
        @stream_ctx.peer_connection.on("icegatheringstatechange")
        async def on_ice_gathering_state_change():
            state = stream_ctx.peer_connection.iceGatheringState
            logger.info("  [%d] ICE gathering state: %s", stream_ctx.index, state)
        
        @stream_ctx.peer_connection.on("icecandidate")
        async def on_ice_candidate(candidate):
            if candidate:
                logger.info("  [%d] Sending local ICE candidate", stream_ctx.index)
                await stream_ctx.websocket.send(json.dumps({
                    "apiKey": "api/v1/replay/iceCandidate",
                    "data": [{
                        "candidate": candidate.candidate,
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex
                    }],
                    "peerId": stream_ctx.peer_id
                }))
        
        @stream_ctx.peer_connection.on("track")
        async def on_track(track):
            if track.kind == "video":
                try:
                    min_frames = test_params['min_frames_for_validation']
                    min_duration = test_params.get('min_duration_for_validation', 0.0)
                    
                    while True:
                        await track.recv()
                        stream_ctx.video_tracker.add_frame()
                        
                        # Check if we've collected enough frames over sufficient duration
                        if (stream_ctx.video_tracker.frame_count >= min_frames and
                            stream_ctx.video_tracker.get_duration() >= min_duration):
                            break
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug("Video track error for stream %s: %s", stream_ctx.stream_id, e)
        
        # Add transceivers
        stream_ctx.peer_connection.addTransceiver("audio", direction="recvonly")
        stream_ctx.peer_connection.addTransceiver("video", direction="recvonly")
        
        # Create and send offer
        offer = await stream_ctx.peer_connection.createOffer()
        await stream_ctx.peer_connection.setLocalDescription(offer)
        
        await stream_ctx.websocket.send(json.dumps({
            "apiKey": "api/v1/replay/stream/start",
            "peerId": stream_ctx.peer_id,
            "data": {
                "clientIpAddr": None,
                "peerId": stream_ctx.peer_id,
                "sessionDescription": {
                    "sdp": stream_ctx.peer_connection.localDescription.sdp,
                    "type": stream_ctx.peer_connection.localDescription.type
                },
                "options": {
                    "rtptransport": "udp",
                    "timeout": 60,
                    "quality": "auto",
                    "overlay": {
                        "needBbox": False,
                        "needTripwire": False,
                        "needRoi": False,
                        "debug": False,
                        "opacity": 255,
                        "framerate": 15,
                        "objectId": [],
                        "proximityClass": [],
                        "entrantClass": [],
                        "proximityAreaFactor": 1.3,
                        "proximityAnimation": "",
                        "overlayColorCode": [],
                        "needHalo": False
                    }
                },
                "streamId": stream_ctx.stream_id,
                "startTime": stream_ctx.start_time,
                "endTime": stream_ctx.end_time
            }
        }))
        
        # Process messages
        timeout = test_params['signaling_timeout']
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                message_str = await asyncio.wait_for(stream_ctx.websocket.recv(), timeout=5.0)
                message = json.loads(message_str)
                api_key = message.get('apiKey', '')
                
                if api_key == 'api/v1/replay/ping':
                    continue
                elif api_key == 'api/v1/replay/setAnswer':
                    answer_data = message.get('data', {})
                    stream_ctx.media_session_id = answer_data.get('mediaSessionId')
                    logger.info("  [%d] Received SDP answer, media session: %s", stream_ctx.index, stream_ctx.media_session_id)
                    
                    if answer_data.get('sdp') and answer_data.get('type'):
                        answer = RTCSessionDescription(sdp=answer_data['sdp'], type=answer_data['type'])
                        await stream_ctx.peer_connection.setRemoteDescription(answer)
                        logger.info("  [%d] Set remote description, waiting for ICE connection...", stream_ctx.index)
                        await asyncio.sleep(1.0)
                elif api_key == 'api/v1/replay/iceCandidate':
                    candidate_data = message.get('data', [])
                    if isinstance(candidate_data, list):
                        for cand_info in candidate_data:
                            try:
                                candidate = parse_ice_candidate(
                                    cand_info['candidate'],
                                    cand_info['sdpMid'],
                                    cand_info['sdpMLineIndex']
                                )
                                await stream_ctx.peer_connection.addIceCandidate(candidate)
                                logger.info("  [%d] Added remote ICE candidate", stream_ctx.index)
                            except Exception as e:
                                logger.warning("  [%d] Failed to add ICE candidate: %s", stream_ctx.index, e)
                
                # Check if we're done (ICE connected + enough frames over sufficient duration)
                min_frames = test_params['min_frames_for_validation']
                min_duration = test_params.get('min_duration_for_validation', 0.0)
                
                if (stream_ctx.ice_connection_state in ('connected', 'completed') and 
                    stream_ctx.video_tracker.frame_count >= min_frames and
                    stream_ctx.video_tracker.get_duration() >= min_duration):
                    break
                        
            except asyncio.TimeoutError:
                # Check if we're done even without new messages
                min_frames = test_params['min_frames_for_validation']
                min_duration = test_params.get('min_duration_for_validation', 0.0)
                
                if (stream_ctx.ice_connection_state in ('connected', 'completed') and 
                    stream_ctx.video_tracker.frame_count >= min_frames and
                    stream_ctx.video_tracker.get_duration() >= min_duration):
                    break
                elif stream_ctx.video_tracker.frame_count > 0:
                    continue
        
        # If ICE hasn't connected yet, give it more time -- the async callback
        # may not have fired during the message loop's recv() blocks.
        if stream_ctx.ice_connection_state not in ('connected', 'completed'):
            logger.info("  [%d] ICE not yet connected after message loop, waiting up to 30s...", stream_ctx.index)
            for _ in range(30):
                if stream_ctx.ice_connection_state in ('connected', 'completed'):
                    break
                await asyncio.sleep(1.0)
        
        # Capture final ICE connection state before cleanup
        if stream_ctx.peer_connection and stream_ctx.ice_connection_state is None:
            stream_ctx.ice_connection_state = stream_ctx.peer_connection.iceConnectionState
        
        # Cleanup
        ping_task.cancel()
        try:
            await ping_task
        except asyncio.CancelledError:
            pass
        
        if stream_ctx.websocket and stream_ctx.media_session_id:
            try:
                if not stream_ctx.websocket.closed:
                    await stream_ctx.websocket.send(json.dumps({
                        "apiKey": "api/v1/replay/stream/stop",
                        "peerId": stream_ctx.peer_id,
                        "data": {
                            "peerId": stream_ctx.peer_id,
                            "mediaSessionId": stream_ctx.media_session_id
                        }
                    }))
                    await asyncio.sleep(0.5)
            except websockets.exceptions.ConnectionClosed:
                pass
        
        if stream_ctx.peer_connection:
            await stream_ctx.peer_connection.close()
        
        if stream_ctx.websocket and not stream_ctx.websocket.closed:
            await stream_ctx.websocket.close()
        
        stream_ctx.success = True
        logger.info("  [%d] Completed - Frames: %d, FPS: %.2f", stream_ctx.index, stream_ctx.video_tracker.frame_count, stream_ctx.video_tracker.get_framerate())
        
    except Exception as e:
        stream_ctx.error = str(e)
        stream_ctx.success = False
        logger.error("  [%d] Failed: %s", stream_ctx.index, e)
    
    return {
        'index': stream_ctx.index,
        'stream_id': stream_ctx.stream_id,
        'success': stream_ctx.success,
        'error': stream_ctx.error
    }


@then('a WebRTC connection is established for each replay stream')
def establish_webrtc_connections(context, api_config, test_endpoints, test_params):
    """Establish WebRTC replay connections."""
    
    logger.info("Testing %d replay stream(s) in parallel", len(context.test_data))
    
    async def run_batch(test_data: List[Dict[str, Any]]) -> List[WebRTCStreamContext]:
        """Run WebRTC connections in parallel."""
        contexts = [
            WebRTCStreamContext(
                item['stream_id'],
                item['start_time'],
                item['end_time'],
                idx
            )
            for idx, item in enumerate(test_data)
        ]
        
        tasks = [
            establish_single_webrtc_stream(ctx, api_config, test_endpoints, test_params)
            for ctx in contexts
        ]
        
        await asyncio.gather(*tasks)
        return contexts
    
    # Establish all WebRTC connections in parallel
    all_results = asyncio.run(run_batch(context.test_data))
    context.stream_results = all_results
    
    # Count successes
    successful = sum(1 for r in all_results if r.success)
    logger.info("WebRTC replay: %d/%d streams established successfully", successful, len(all_results))
    
    assert len(all_results) > 0, "No streams were tested"


@then('the stream reaches PLAYING state')
def verify_playing_state(context):
    """Verify that streams have established ICE connection."""
    failed = []
    for r in context.stream_results:
        if r.ice_connection_state not in ('connected', 'completed'):
            failed.append((r, r.ice_connection_state or 'unknown'))
    
    if failed:
        logger.warning("Failed streams (ICE connection not established):")
        for r, state in failed:
            logger.warning("  - Index %d (%s): ICE state = %s", r.index, r.stream_id, state)
    
    # Assert with detailed error reporting
    if len(failed) > 0:
        failed_items = [
            {'description': f"Stream {r.stream_id[:12]}... (index {r.index}): ICE state={state}"}
            for r, state in failed
        ]
        assert_with_detailed_failure(
            False,
            "WebRTC Replay ICE Connection",
            f"All {len(context.stream_results)} stream(s) must establish ICE connection (state: connected/completed)",
            f"{len(context.stream_results) - len(failed)}/{len(context.stream_results)} stream(s) established ICE connection",
            failed_items,
            f"Check WebSocket signaling and ICE server configuration.\n"
            f"  Total replay streams tested: {len(context.stream_results)}\n"
            f"  ICE connected: {len(context.stream_results) - len(failed)}\n"
            f"  Failed to connect: {len(failed)}\n"
            f"  Verify timeline data exists for selected streams"
        )
    
    logger.info("All %d stream(s) have established ICE connection", len(context.stream_results))


@then('the video framerate is healthy')
def verify_framerate(context, test_params):
    """Verify that video framerate is healthy for all streams."""
    min_fps = test_params['min_fps']
    min_frames = test_params['min_frames_for_validation']
    min_duration = test_params.get('min_duration_for_validation', 0.0)
    
    unhealthy = []
    for result in context.stream_results:
        if not result.video_tracker.is_healthy(min_fps, min_frames, min_duration):
            unhealthy.append(result)
    
    if unhealthy:
        logger.warning("Unhealthy streams:")
        for r in unhealthy:
            fps = r.video_tracker.get_framerate()
            duration = r.video_tracker.get_duration()
            logger.warning("  - Index %d (%s): %d frames, %.2f FPS over %.2fs", r.index, r.stream_id, r.video_tracker.frame_count, fps, duration)
    
    # Assert with detailed error reporting
    if len(unhealthy) > 0:
        failed_items = []
        for r in unhealthy:
            fps = r.video_tracker.get_framerate()
            duration = r.video_tracker.get_duration()
            failed_items.append({
                'description': f"Stream {r.stream_id[:12]}... (index {r.index}): {r.video_tracker.frame_count} frames, {fps:.2f} FPS over {duration:.2f}s"
            })
        
        assert_with_detailed_failure(
            False,
            "WebRTC Replay Framerate Validation",
            f"All streams must have >= {min_fps} FPS over >= {min_duration}s with >= {min_frames} frames",
            f"{len(context.stream_results) - len(unhealthy)}/{len(context.stream_results)} stream(s) have healthy framerate",
            failed_items,
            f"Check replay video quality and network bandwidth.\n"
            f"  Required: >= {min_fps} FPS, >= {min_frames} frames, >= {min_duration}s duration\n"
            f"  Healthy streams: {len(context.stream_results) - len(unhealthy)}\n"
            f"  Unhealthy streams: {len(unhealthy)}\n"
            f"  Possible causes: Network congestion, server load, timeline data quality"
        )
    
    # Log summary
    logger.info("Framerate Validation Summary:")
    logger.info("  Required: >= %.1f FPS over >= %.1fs duration", min_fps, min_duration)
    logger.info("  All %d stream(s) passed:", len(context.stream_results))
    for r in context.stream_results:
        fps = r.video_tracker.get_framerate()
        duration = r.video_tracker.get_duration()
        logger.info("    - Index %d: %.2f FPS over %.2fs (%d frames)", r.index, fps, duration, r.video_tracker.frame_count)

