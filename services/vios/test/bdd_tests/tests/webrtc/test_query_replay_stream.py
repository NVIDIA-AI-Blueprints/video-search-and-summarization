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
    RTCConfiguration,
    RTCIceServer,
)
from pytest_bdd import scenarios, given, when, then

from ..test_utils import assert_with_detailed_failure
from .webrtc_test_utils import VideoFrameTracker, parse_ice_candidate

logger = logging.getLogger(__name__)

# Load scenarios from the feature file
scenarios("../../features/webrtc/query_replay_stream.feature")

# API Endpoints
ENDPOINTS = {
    "streams": "/vst/api/v1/replay/streams",
    "storage_size": "/vst/api/v1/storage/size",
    "websocket": "/vst/api/v1/replay/ws",
    "query": "/vst/api/v1/replay/stream/query",
    "pause": "/vst/api/v1/replay/stream/pause",
}

# Minimum frames to confirm stream is playing before querying
MIN_FRAMES_FOR_QUERY = 5


class WebRTCQueryStreamContext:
    """Context for a single WebRTC replay stream with query support."""

    def __init__(self, stream_id: str, start_time: str, end_time: str):
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
        self.query_response: Optional[Dict[str, Any]] = None
        self.query_status_code: Optional[int] = None


class ScenarioContext:
    """Context to store test data between BDD steps."""

    def __init__(self):
        self.streams: List[Dict[str, Any]] = []
        self.timelines: Dict[str, Any] = {}
        self.test_data: List[Dict[str, Any]] = []
        self.stream_contexts: List[WebRTCQueryStreamContext] = []
        self.query_all_response: Optional[List[Dict[str, Any]]] = None
        self.query_all_status_code: Optional[int] = None
        self.pause_query_results: List[Dict[str, Any]] = []


@pytest.fixture
def context():
    """Create a test context."""
    return ScenarioContext()


@pytest.fixture
def test_endpoints():
    """Get test endpoints configuration."""
    return ENDPOINTS


@given("the VST API and WebSocket are configured for query test")
def vst_configured(api_config, test_endpoints):
    """Verify VST API and WebSocket configuration is available."""
    assert api_config["base_url"], "Base URL must be configured"
    assert test_endpoints["streams"]
    assert test_endpoints["websocket"]
    assert test_endpoints["query"]


@when("all replay streams with valid timelines are selected for query test")
def select_replay_streams(context, api_config, test_endpoints, test_params):
    """Fetch streams and select all with valid timelines for query testing."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = test_params["timeout"]

    # Fetch available replay streams
    url = f"{base_url}{test_endpoints['streams']}"
    response = requests.get(url, timeout=timeout, verify=verify_ssl)
    response.raise_for_status()
    context.streams = response.json()
    assert len(context.streams) > 0, "No replay streams available"

    # Fetch timelines
    url = f"{base_url}{test_endpoints['storage_size']}"
    response = requests.get(url, params={"timelines": "true"}, timeout=timeout, verify=verify_ssl)
    response.raise_for_status()
    context.timelines = response.json()
    assert context.timelines, "No timeline data found"

    # Extract stream names
    all_stream_names = []
    for stream_obj in context.streams:
        if isinstance(stream_obj, dict):
            all_stream_names.extend(list(stream_obj.keys()))
    assert len(all_stream_names) > 0, "No stream names found"

    # Filter to valid streams with timelines (exclude test uploads)
    valid_stream_names = []
    for stream_name in all_stream_names:
        if stream_name.startswith("test_upload_"):
            continue
        stream_timeline_data = context.timelines.get(stream_name)
        if not stream_timeline_data or "timelines" not in stream_timeline_data:
            continue
        timelines = stream_timeline_data["timelines"]
        if not isinstance(timelines, list) or len(timelines) == 0:
            continue
        valid_stream_names.append(stream_name)

    assert len(valid_stream_names) > 0, "No valid streams with timelines found"

    replay_duration = test_params.get("replay_duration_seconds", 60)

    # Build test data for all valid streams
    context.test_data = []
    for stream_name in valid_stream_names:
        stream_timeline_data = context.timelines[stream_name]
        timeline = stream_timeline_data["timelines"][0]
        start_time_str = timeline.get("startTime")
        end_time_str = timeline.get("endTime")
        assert start_time_str and end_time_str, f"Invalid timeline for {stream_name}"

        start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))

        middle_time = start_time + (end_time - start_time) / 2
        replay_end_time = middle_time + timedelta(seconds=replay_duration)
        if replay_end_time > end_time:
            replay_end_time = end_time

        context.test_data.append(
            {
                "stream_id": stream_name,
                "start_time": middle_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "end_time": replay_end_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            }
        )

    logger.info(
        "Selected %d streams for query test: %s",
        len(context.test_data),
        [item["stream_id"] for item in context.test_data],
    )


async def establish_stream_and_query(
    stream_ctx: WebRTCQueryStreamContext,
    api_config: Dict[str, Any],
    test_endpoints: Dict[str, str],
    test_params: Dict[str, Any],
) -> None:
    """Establish a WebRTC replay stream, wait for playback, then call the query API."""
    ping_task = None

    try:
        base_url = api_config["base_url"]
        parsed = urlparse(base_url)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        ws_url = f"{ws_scheme}://{parsed.netloc}{test_endpoints['websocket']}"
        ws_url += f"?connectionId={stream_ctx.connection_id}&streamId={stream_ctx.stream_id}"

        logger.info(
            "Connecting WebRTC replay for query test: %s (%s to %s)",
            stream_ctx.stream_id,
            stream_ctx.start_time,
            stream_ctx.end_time,
        )

        # Connect WebSocket
        stream_ctx.websocket = await websockets.connect(
            ws_url, ping_interval=None, ping_timeout=None
        )

        # Keepalive pings
        async def send_keepalive_pings():
            try:
                while not stream_ctx.websocket.closed:
                    await asyncio.sleep(10)
                    if not stream_ctx.websocket.closed:
                        await stream_ctx.websocket.send(
                            json.dumps({"apiKey": "api/v1/replay/ping"})
                        )
            except websockets.exceptions.ConnectionClosed:
                pass
            except Exception as e:
                logger.debug("Keepalive ping error: %s", e)

        ping_task = asyncio.create_task(send_keepalive_pings())

        # Request configuration
        await stream_ctx.websocket.send(
            json.dumps(
                {
                    "apiKey": "api/v1/replay/configuration",
                    "data": None,
                    "peerId": stream_ctx.peer_id,
                }
            )
        )

        # Request ICE servers
        await stream_ctx.websocket.send(
            json.dumps(
                {
                    "apiKey": "api/v1/replay/iceServers",
                    "peerId": stream_ctx.peer_id,
                    "data": {"peerId": stream_ctx.peer_id},
                }
            )
        )

        # Wait for configuration and ICE servers
        received_config = False
        received_ice = False
        ice_configuration = None

        while not (received_config and received_ice):
            message_str = await asyncio.wait_for(
                stream_ctx.websocket.recv(), timeout=10.0
            )
            message = json.loads(message_str)
            api_key = message.get("apiKey", "")

            if api_key == "api/v1/replay/configuration":
                stream_ctx.configuration = message.get("data", {})
                received_config = True
            elif api_key == "api/v1/replay/iceServers":
                stream_ctx.ice_servers = message.get("data", {})
                ice_servers_list = stream_ctx.ice_servers.get("iceServers", [])
                received_ice = True

                if ice_servers_list:
                    ice_configuration = RTCConfiguration(
                        iceServers=[
                            RTCIceServer(urls=srv["urls"]) for srv in ice_servers_list
                        ]
                    )

        # Create peer connection
        stream_ctx.peer_connection = (
            RTCPeerConnection(configuration=ice_configuration)
            if ice_configuration
            else RTCPeerConnection()
        )

        # ICE connection state handler
        @stream_ctx.peer_connection.on("iceconnectionstatechange")
        async def on_ice_connection_state_change():
            state = stream_ctx.peer_connection.iceConnectionState
            logger.info("ICE connection state: %s", state)
            if state in ("connected", "completed"):
                stream_ctx.ice_connection_state = state

        # ICE candidate handler
        @stream_ctx.peer_connection.on("icecandidate")
        async def on_ice_candidate(candidate):
            if candidate:
                await stream_ctx.websocket.send(
                    json.dumps(
                        {
                            "apiKey": "api/v1/replay/iceCandidate",
                            "data": [
                                {
                                    "candidate": candidate.candidate,
                                    "sdpMid": candidate.sdpMid,
                                    "sdpMLineIndex": candidate.sdpMLineIndex,
                                }
                            ],
                            "peerId": stream_ctx.peer_id,
                        }
                    )
                )

        # Video track handler
        @stream_ctx.peer_connection.on("track")
        async def on_track(track):
            if track.kind == "video":
                try:
                    while True:
                        await track.recv()
                        stream_ctx.video_tracker.add_frame()
                        if stream_ctx.video_tracker.frame_count >= MIN_FRAMES_FOR_QUERY:
                            break
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug("Video track error: %s", e)

        # Add transceivers
        stream_ctx.peer_connection.addTransceiver("audio", direction="recvonly")
        stream_ctx.peer_connection.addTransceiver("video", direction="recvonly")

        # Create and send offer
        offer = await stream_ctx.peer_connection.createOffer()
        await stream_ctx.peer_connection.setLocalDescription(offer)

        await stream_ctx.websocket.send(
            json.dumps(
                {
                    "apiKey": "api/v1/replay/stream/start",
                    "peerId": stream_ctx.peer_id,
                    "data": {
                        "clientIpAddr": None,
                        "peerId": stream_ctx.peer_id,
                        "sessionDescription": {
                            "sdp": stream_ctx.peer_connection.localDescription.sdp,
                            "type": stream_ctx.peer_connection.localDescription.type,
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
                                "needHalo": False,
                            },
                        },
                        "streamId": stream_ctx.stream_id,
                        "startTime": stream_ctx.start_time,
                        "endTime": stream_ctx.end_time,
                    },
                }
            )
        )

        # Process messages until stream is playing
        timeout = test_params["signaling_timeout"]
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                message_str = await asyncio.wait_for(
                    stream_ctx.websocket.recv(), timeout=5.0
                )
                message = json.loads(message_str)
                api_key = message.get("apiKey", "")

                if api_key == "api/v1/replay/ping":
                    continue
                elif api_key == "api/v1/replay/setAnswer":
                    answer_data = message.get("data", {})
                    stream_ctx.media_session_id = answer_data.get("mediaSessionId")
                    logger.info(
                        "Received SDP answer, media session: %s",
                        stream_ctx.media_session_id,
                    )

                    if answer_data.get("sdp") and answer_data.get("type"):
                        answer = RTCSessionDescription(
                            sdp=answer_data["sdp"], type=answer_data["type"]
                        )
                        await stream_ctx.peer_connection.setRemoteDescription(answer)
                        await asyncio.sleep(1.0)
                elif api_key == "api/v1/replay/iceCandidate":
                    candidate_data = message.get("data", [])
                    if isinstance(candidate_data, list):
                        for cand_info in candidate_data:
                            try:
                                candidate = parse_ice_candidate(
                                    cand_info["candidate"],
                                    cand_info["sdpMid"],
                                    cand_info["sdpMLineIndex"],
                                )
                                await stream_ctx.peer_connection.addIceCandidate(
                                    candidate
                                )
                            except Exception as e:
                                logger.warning("Failed to add ICE candidate: %s", e)

                # Check if stream is playing
                if (
                    stream_ctx.ice_connection_state in ("connected", "completed")
                    and stream_ctx.video_tracker.frame_count >= MIN_FRAMES_FOR_QUERY
                ):
                    break

            except asyncio.TimeoutError:
                if (
                    stream_ctx.ice_connection_state in ("connected", "completed")
                    and stream_ctx.video_tracker.frame_count >= MIN_FRAMES_FOR_QUERY
                ):
                    break
                elif stream_ctx.video_tracker.frame_count > 0:
                    continue

        # Verify stream is playing before querying
        if stream_ctx.ice_connection_state is None and stream_ctx.peer_connection:
            stream_ctx.ice_connection_state = (
                stream_ctx.peer_connection.iceConnectionState
            )

        assert stream_ctx.ice_connection_state in ("connected", "completed"), (
            f"Stream not playing: ICE state = "
            f"{stream_ctx.ice_connection_state or 'unknown'}"
        )

        logger.info(
            "Stream is playing (frames: %d, ICE: %s). Querying with peerid=%s",
            stream_ctx.video_tracker.frame_count,
            stream_ctx.ice_connection_state,
            stream_ctx.peer_id,
        )

        # Call query API while stream is active
        query_url = f"{base_url}{test_endpoints['query']}"
        query_response = requests.get(
            query_url,
            params={"peerid": stream_ctx.peer_id, "metadata": "false"},
            timeout=test_params["timeout"],
            verify=api_config.get("verify_ssl", False),
        )

        stream_ctx.query_status_code = query_response.status_code
        stream_ctx.query_response = query_response.json()

        logger.info(
            "Query API response (status=%d): %s",
            stream_ctx.query_status_code,
            json.dumps(stream_ctx.query_response),
        )

        # Cleanup: stop stream and close connections
        ping_task.cancel()
        try:
            await ping_task
        except asyncio.CancelledError:
            pass

        if stream_ctx.websocket and stream_ctx.media_session_id:
            try:
                if not stream_ctx.websocket.closed:
                    await stream_ctx.websocket.send(
                        json.dumps(
                            {
                                "apiKey": "api/v1/replay/stream/stop",
                                "peerId": stream_ctx.peer_id,
                                "data": {
                                    "peerId": stream_ctx.peer_id,
                                    "mediaSessionId": stream_ctx.media_session_id,
                                },
                            }
                        )
                    )
                    await asyncio.sleep(0.5)
            except websockets.exceptions.ConnectionClosed:
                pass

        if stream_ctx.peer_connection:
            await stream_ctx.peer_connection.close()

        if stream_ctx.websocket and not stream_ctx.websocket.closed:
            await stream_ctx.websocket.close()

        stream_ctx.success = True

    except Exception as e:
        stream_ctx.error = str(e)
        stream_ctx.success = False
        logger.exception("Query test failed for stream %s", stream_ctx.stream_id)

        # Best-effort cleanup on error
        if ping_task:
            ping_task.cancel()
        try:
            if stream_ctx.peer_connection:
                await stream_ctx.peer_connection.close()
            if stream_ctx.websocket and not stream_ctx.websocket.closed:
                await stream_ctx.websocket.close()
        except Exception as cleanup_exc:
            logger.error(
                "Cleanup failed for stream %s (peer_id=%s): %s",
                stream_ctx.stream_id,
                stream_ctx.peer_id,
                cleanup_exc,
            )

        raise


@when("each WebRTC replay session is started and queried")
def start_replay_and_query(context, api_config, test_endpoints, test_params):
    """Establish a WebRTC replay session for each stream and call the query API."""
    assert len(context.test_data) > 0, "No test data available"

    for i, test_item in enumerate(context.test_data):
        stream_ctx = WebRTCQueryStreamContext(
            stream_id=test_item["stream_id"],
            start_time=test_item["start_time"],
            end_time=test_item["end_time"],
        )

        logger.info(
            "Starting query test for stream %d/%d: %s",
            i + 1,
            len(context.test_data),
            test_item["stream_id"],
        )

        asyncio.run(
            establish_stream_and_query(stream_ctx, api_config, test_endpoints, test_params)
        )

        context.stream_contexts.append(stream_ctx)

        logger.info(
            "WebRTC query test completed for stream '%s': success=%s, query_status=%s",
            stream_ctx.stream_id,
            stream_ctx.success,
            stream_ctx.query_status_code,
        )


@then("each query response contains a valid timestamp")
def verify_query_responses(context):
    """Verify that the query API response contains a valid timestamp for each stream."""
    assert len(context.stream_contexts) > 0, "No stream contexts available"

    for stream_ctx in context.stream_contexts:
        stream_label = f"[{stream_ctx.stream_id}]"
        assert stream_ctx.success, (
            f"{stream_label} Stream establishment failed: {stream_ctx.error}"
        )

        # Verify HTTP status
        assert_with_detailed_failure(
            stream_ctx.query_status_code == 200,
            f"{stream_label} Query API HTTP Status",
            "HTTP 200",
            f"HTTP {stream_ctx.query_status_code}",
            additional_info=f"Response: {json.dumps(stream_ctx.query_response)}",
        )

        # Verify response has 'ts' field
        assert_with_detailed_failure(
            "ts" in stream_ctx.query_response,
            f"{stream_label} Query API Response Format",
            "Response contains 'ts' field",
            f"Response keys: {list(stream_ctx.query_response.keys())}",
            additional_info=f"Full response: {json.dumps(stream_ctx.query_response)}",
        )

        ts = stream_ctx.query_response["ts"]

        # Verify 'ts' is a positive number (epoch milliseconds)
        assert_with_detailed_failure(
            isinstance(ts, (int, float)) and ts > 0,
            f"{stream_label} Query API Timestamp Value",
            "Positive numeric timestamp (epoch milliseconds)",
            f"ts = {ts} (type: {type(ts).__name__})",
        )

        # Verify 'ts' is in milliseconds format (13 digits, not seconds which is 10 digits)
        assert_with_detailed_failure(
            len(str(int(ts))) == 13,
            f"{stream_label} Query API Timestamp Format",
            "13-digit epoch milliseconds",
            f"ts = {ts} ({len(str(int(ts)))} digits)",
            additional_info=(
                "Timestamp should be in milliseconds (13 digits), not seconds (10 digits)"
            ),
        )

        # Verify timestamp falls within the replay time range
        replay_start_ms = int(
            datetime.fromisoformat(
                stream_ctx.start_time.replace("Z", "+00:00")
            ).timestamp()
            * 1000
        )
        replay_end_ms = int(
            datetime.fromisoformat(
                stream_ctx.end_time.replace("Z", "+00:00")
            ).timestamp()
            * 1000
        )

        assert_with_detailed_failure(
            replay_start_ms <= ts <= replay_end_ms,
            f"{stream_label} Query API Timestamp Range",
            f"Timestamp between {replay_start_ms} and {replay_end_ms}",
            f"ts = {ts}",
            additional_info=(
                f"Replay window: {stream_ctx.start_time} to {stream_ctx.end_time}\n"
                f"  Timestamp: {ts} ({datetime.fromtimestamp(ts / 1000).isoformat()}Z)"
            ),
        )

        logger.info(
            "Query API validation passed: ts=%d (peer_id=%s, stream=%s)",
            ts,
            stream_ctx.peer_id,
            stream_ctx.stream_id,
        )

    logger.info(
        "All %d streams passed query validation", len(context.stream_contexts)
    )


async def establish_stream(
    stream_ctx: WebRTCQueryStreamContext,
    api_config: Dict[str, Any],
    test_endpoints: Dict[str, str],
    test_params: Dict[str, Any],
) -> None:
    """Establish a WebRTC replay stream and wait until it is playing. Does not query or clean up."""
    base_url = api_config["base_url"]
    parsed = urlparse(base_url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_url = f"{ws_scheme}://{parsed.netloc}{test_endpoints['websocket']}"
    ws_url += f"?connectionId={stream_ctx.connection_id}&streamId={stream_ctx.stream_id}"

    logger.info(
        "Establishing WebRTC replay: %s (%s to %s)",
        stream_ctx.stream_id,
        stream_ctx.start_time,
        stream_ctx.end_time,
    )

    stream_ctx.websocket = await websockets.connect(
        ws_url, ping_interval=None, ping_timeout=None
    )

    # Request configuration
    await stream_ctx.websocket.send(
        json.dumps(
            {
                "apiKey": "api/v1/replay/configuration",
                "data": None,
                "peerId": stream_ctx.peer_id,
            }
        )
    )

    # Request ICE servers
    await stream_ctx.websocket.send(
        json.dumps(
            {
                "apiKey": "api/v1/replay/iceServers",
                "peerId": stream_ctx.peer_id,
                "data": {"peerId": stream_ctx.peer_id},
            }
        )
    )

    # Wait for configuration and ICE servers
    received_config = False
    received_ice = False
    ice_configuration = None

    while not (received_config and received_ice):
        message_str = await asyncio.wait_for(
            stream_ctx.websocket.recv(), timeout=10.0
        )
        message = json.loads(message_str)
        api_key = message.get("apiKey", "")

        if api_key == "api/v1/replay/configuration":
            stream_ctx.configuration = message.get("data", {})
            received_config = True
        elif api_key == "api/v1/replay/iceServers":
            stream_ctx.ice_servers = message.get("data", {})
            ice_servers_list = stream_ctx.ice_servers.get("iceServers", [])
            received_ice = True

            if ice_servers_list:
                ice_configuration = RTCConfiguration(
                    iceServers=[
                        RTCIceServer(urls=srv["urls"]) for srv in ice_servers_list
                    ]
                )

    # Create peer connection
    stream_ctx.peer_connection = (
        RTCPeerConnection(configuration=ice_configuration)
        if ice_configuration
        else RTCPeerConnection()
    )

    @stream_ctx.peer_connection.on("iceconnectionstatechange")
    async def on_ice_connection_state_change():
        state = stream_ctx.peer_connection.iceConnectionState
        logger.info("ICE connection state for %s: %s", stream_ctx.stream_id, state)
        if state in ("connected", "completed"):
            stream_ctx.ice_connection_state = state

    @stream_ctx.peer_connection.on("icecandidate")
    async def on_ice_candidate(candidate):
        if candidate:
            await stream_ctx.websocket.send(
                json.dumps(
                    {
                        "apiKey": "api/v1/replay/iceCandidate",
                        "data": [
                            {
                                "candidate": candidate.candidate,
                                "sdpMid": candidate.sdpMid,
                                "sdpMLineIndex": candidate.sdpMLineIndex,
                            }
                        ],
                        "peerId": stream_ctx.peer_id,
                    }
                )
            )

    @stream_ctx.peer_connection.on("track")
    async def on_track(track):
        if track.kind == "video":
            try:
                while True:
                    await track.recv()
                    stream_ctx.video_tracker.add_frame()
                    if stream_ctx.video_tracker.frame_count >= MIN_FRAMES_FOR_QUERY:
                        break
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("Video track error for %s: %s", stream_ctx.stream_id, e)

    stream_ctx.peer_connection.addTransceiver("audio", direction="recvonly")
    stream_ctx.peer_connection.addTransceiver("video", direction="recvonly")

    offer = await stream_ctx.peer_connection.createOffer()
    await stream_ctx.peer_connection.setLocalDescription(offer)

    await stream_ctx.websocket.send(
        json.dumps(
            {
                "apiKey": "api/v1/replay/stream/start",
                "peerId": stream_ctx.peer_id,
                "data": {
                    "clientIpAddr": None,
                    "peerId": stream_ctx.peer_id,
                    "sessionDescription": {
                        "sdp": stream_ctx.peer_connection.localDescription.sdp,
                        "type": stream_ctx.peer_connection.localDescription.type,
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
                            "needHalo": False,
                        },
                    },
                    "streamId": stream_ctx.stream_id,
                    "startTime": stream_ctx.start_time,
                    "endTime": stream_ctx.end_time,
                },
            }
        )
    )

    # Process messages until stream is playing
    timeout = test_params["signaling_timeout"]
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            message_str = await asyncio.wait_for(
                stream_ctx.websocket.recv(), timeout=5.0
            )
            message = json.loads(message_str)
            api_key = message.get("apiKey", "")

            if api_key == "api/v1/replay/ping":
                continue
            elif api_key == "api/v1/replay/setAnswer":
                answer_data = message.get("data", {})
                stream_ctx.media_session_id = answer_data.get("mediaSessionId")
                logger.info(
                    "Received SDP answer for %s, media session: %s",
                    stream_ctx.stream_id,
                    stream_ctx.media_session_id,
                )

                if answer_data.get("sdp") and answer_data.get("type"):
                    answer = RTCSessionDescription(
                        sdp=answer_data["sdp"], type=answer_data["type"]
                    )
                    await stream_ctx.peer_connection.setRemoteDescription(answer)
                    await asyncio.sleep(1.0)
            elif api_key == "api/v1/replay/iceCandidate":
                candidate_data = message.get("data", [])
                if isinstance(candidate_data, list):
                    for cand_info in candidate_data:
                        try:
                            candidate = parse_ice_candidate(
                                cand_info["candidate"],
                                cand_info["sdpMid"],
                                cand_info["sdpMLineIndex"],
                            )
                            await stream_ctx.peer_connection.addIceCandidate(candidate)
                        except Exception as e:
                            logger.warning("Failed to add ICE candidate: %s", e)

            if (
                stream_ctx.ice_connection_state in ("connected", "completed")
                and stream_ctx.video_tracker.frame_count >= MIN_FRAMES_FOR_QUERY
            ):
                break

        except asyncio.TimeoutError:
            if (
                stream_ctx.ice_connection_state in ("connected", "completed")
                and stream_ctx.video_tracker.frame_count >= MIN_FRAMES_FOR_QUERY
            ):
                break
            elif stream_ctx.video_tracker.frame_count > 0:
                continue

    if stream_ctx.ice_connection_state is None and stream_ctx.peer_connection:
        stream_ctx.ice_connection_state = stream_ctx.peer_connection.iceConnectionState

    if stream_ctx.ice_connection_state in ("connected", "completed"):
        stream_ctx.success = True
        logger.info(
            "Stream %s is playing (frames: %d, ICE: %s, peer_id: %s)",
            stream_ctx.stream_id,
            stream_ctx.video_tracker.frame_count,
            stream_ctx.ice_connection_state,
            stream_ctx.peer_id,
        )
    else:
        stream_ctx.error = (
            f"Stream not playing: ICE state = "
            f"{stream_ctx.ice_connection_state or 'unknown'}"
        )
        logger.error("Failed to establish stream %s: %s", stream_ctx.stream_id, stream_ctx.error)


async def cleanup_stream(stream_ctx: WebRTCQueryStreamContext) -> None:
    """Stop and clean up a WebRTC replay stream."""
    if stream_ctx.websocket and stream_ctx.media_session_id:
        try:
            if not stream_ctx.websocket.closed:
                await stream_ctx.websocket.send(
                    json.dumps(
                        {
                            "apiKey": "api/v1/replay/stream/stop",
                            "peerId": stream_ctx.peer_id,
                            "data": {
                                "peerId": stream_ctx.peer_id,
                                "mediaSessionId": stream_ctx.media_session_id,
                            },
                        }
                    )
                )
                await asyncio.sleep(0.5)
        except websockets.exceptions.ConnectionClosed:
            pass

    if stream_ctx.peer_connection:
        await stream_ctx.peer_connection.close()

    if stream_ctx.websocket and not stream_ctx.websocket.closed:
        await stream_ctx.websocket.close()


async def establish_all_streams_and_query(
    stream_contexts: List[WebRTCQueryStreamContext],
    api_config: Dict[str, Any],
    test_endpoints: Dict[str, str],
    test_params: Dict[str, Any],
) -> Dict[str, Any]:
    """Establish all streams concurrently, call query API without peerid, then clean up."""
    # Start all streams concurrently
    await asyncio.gather(
        *[
            establish_stream(ctx, api_config, test_endpoints, test_params)
            for ctx in stream_contexts
        ]
    )

    active_count = sum(1 for ctx in stream_contexts if ctx.success)
    logger.info(
        "Established %d/%d streams, calling query API without peerid",
        active_count,
        len(stream_contexts),
    )

    # Call query API without peerid
    base_url = api_config["base_url"]
    query_url = f"{base_url}{test_endpoints['query']}"
    query_response = requests.get(
        query_url,
        timeout=test_params["timeout"],
        verify=api_config.get("verify_ssl", False),
    )

    result = {
        "status_code": query_response.status_code,
        "response": query_response.json(),
    }

    logger.info(
        "Query API (no peerid) response (status=%d): %s",
        result["status_code"],
        json.dumps(result["response"]),
    )

    # Clean up all streams
    await asyncio.gather(*[cleanup_stream(ctx) for ctx in stream_contexts])

    return result


@when("all WebRTC replay sessions are started and the query API is called without peerid")
def start_all_and_query_without_peerid(context, api_config, test_endpoints, test_params):
    """Start replay streams concurrently (up to parallelism limit), then call query API without peerid."""
    assert len(context.test_data) > 0, "No test data available"

    parallelism = test_params.get("parallelism", 1)
    num_streams = min(parallelism, len(context.test_data))
    selected_items = random.sample(context.test_data, num_streams)

    logger.info(
        "Selected %d/%d streams (parallelism=%d): %s",
        num_streams,
        len(context.test_data),
        parallelism,
        [item["stream_id"] for item in selected_items],
    )

    for test_item in selected_items:
        context.stream_contexts.append(
            WebRTCQueryStreamContext(
                stream_id=test_item["stream_id"],
                start_time=test_item["start_time"],
                end_time=test_item["end_time"],
            )
        )

    result = asyncio.run(
        establish_all_streams_and_query(
            context.stream_contexts, api_config, test_endpoints, test_params
        )
    )

    context.query_all_status_code = result["status_code"]
    context.query_all_response = result["response"]


@then("the query response is a valid array containing all active peer connections")
def verify_query_all_response(context):
    """Verify that the query API response without peerid returns a valid array."""
    assert_with_detailed_failure(
        context.query_all_status_code == 200,
        "Query API (no peerid) HTTP Status",
        "HTTP 200",
        f"HTTP {context.query_all_status_code}",
        additional_info=f"Response: {json.dumps(context.query_all_response)}",
    )

    assert_with_detailed_failure(
        isinstance(context.query_all_response, list),
        "Query API (no peerid) Response Type",
        "JSON array",
        f"{type(context.query_all_response).__name__}",
        additional_info=f"Response: {json.dumps(context.query_all_response)}",
    )

    # Build set of peer IDs for streams we successfully established
    active_peer_ids = {
        ctx.peer_id for ctx in context.stream_contexts if ctx.success
    }
    assert len(active_peer_ids) > 0, "No streams were successfully established"

    required_fields = ["duration", "frameTime", "peerId", "sensorId", "startTime", "streamId"]

    # Validate required fields for all entries first
    for entry in context.query_all_response:
        entry_label = f"[peerId={entry.get('peerId', 'missing')}]"
        missing_fields = [f for f in required_fields if f not in entry]
        assert_with_detailed_failure(
            len(missing_fields) == 0,
            f"{entry_label} Required Fields",
            f"All fields present: {required_fields}",
            f"Missing: {missing_fields}",
            additional_info=f"Entry: {json.dumps(entry)}",
        )

    # Build set of peer IDs returned by the query API
    response_peer_ids = {entry["peerId"] for entry in context.query_all_response}

    # Verify all our active peer IDs are present in the response
    missing_peers = active_peer_ids - response_peer_ids
    assert_with_detailed_failure(
        len(missing_peers) == 0,
        "Query API (no peerid) Contains All Active Peers",
        f"All {len(active_peer_ids)} active peer IDs present",
        f"Missing {len(missing_peers)} peer IDs: {missing_peers}",
        additional_info=(
            f"Active peers: {active_peer_ids}\n"
            f"Response peers: {response_peer_ids}"
        ),
    )

    for entry in context.query_all_response:
        peer_id = entry["peerId"]
        entry_label = f"[peerId={peer_id}]"

        # Only validate entries that belong to our test streams
        if peer_id not in active_peer_ids:
            logger.info(
                "Skipping validation for peer %s (not from this test)", peer_id
            )
            continue

        frame_time = entry["frameTime"]

        # Verify frameTime is a positive number
        assert_with_detailed_failure(
            isinstance(frame_time, (int, float)) and frame_time > 0,
            f"{entry_label} frameTime Value",
            "Positive numeric timestamp (epoch milliseconds)",
            f"frameTime = {frame_time} (type: {type(frame_time).__name__})",
        )

        # Verify frameTime is in milliseconds format (13 digits)
        assert_with_detailed_failure(
            len(str(int(frame_time))) == 13,
            f"{entry_label} frameTime Format",
            "13-digit epoch milliseconds",
            f"frameTime = {frame_time} ({len(str(int(frame_time)))} digits)",
            additional_info=(
                "Timestamp should be in milliseconds (13 digits), not seconds (10 digits)"
            ),
        )

        # Find the matching stream context to validate the time range
        matching_ctx = next(
            (ctx for ctx in context.stream_contexts if ctx.peer_id == peer_id), None
        )
        if matching_ctx:
            replay_start_ms = int(
                datetime.fromisoformat(
                    matching_ctx.start_time.replace("Z", "+00:00")
                ).timestamp()
                * 1000
            )
            replay_end_ms = int(
                datetime.fromisoformat(
                    matching_ctx.end_time.replace("Z", "+00:00")
                ).timestamp()
                * 1000
            )

            assert_with_detailed_failure(
                replay_start_ms <= frame_time <= replay_end_ms,
                f"{entry_label} frameTime Range",
                f"Timestamp between {replay_start_ms} and {replay_end_ms}",
                f"frameTime = {frame_time}",
                additional_info=(
                    f"Replay window: {matching_ctx.start_time} to {matching_ctx.end_time}\n"
                    f"  frameTime: {frame_time} "
                    f"({datetime.fromtimestamp(frame_time / 1000).isoformat()}Z)"
                ),
            )

        # Verify frameTime >= startTime
        start_time_val = entry["startTime"]
        assert_with_detailed_failure(
            frame_time >= start_time_val,
            f"{entry_label} frameTime >= startTime",
            f"frameTime ({frame_time}) >= startTime ({start_time_val})",
            f"frameTime ({frame_time}) < startTime ({start_time_val})",
        )

        # Verify streamId matches
        assert_with_detailed_failure(
            entry["streamId"] == matching_ctx.stream_id if matching_ctx else True,
            f"{entry_label} streamId",
            f"streamId = {matching_ctx.stream_id if matching_ctx else 'N/A'}",
            f"streamId = {entry['streamId']}",
        )

        logger.info(
            "Query entry validated: peerId=%s, streamId=%s, frameTime=%d",
            peer_id,
            entry["streamId"],
            frame_time,
        )

    logger.info(
        "Query API (no peerid) validation passed: %d entries, %d active peers verified",
        len(context.query_all_response),
        len(active_peer_ids),
    )


# Delay after pause to allow the gstreamer pipeline to fully pause
PAUSE_SETTLE_DELAY_SECONDS = 1


async def establish_all_pause_query_and_cleanup(
    stream_contexts: List[WebRTCQueryStreamContext],
    api_config: Dict[str, Any],
    test_endpoints: Dict[str, str],
    test_params: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Establish all streams, pause them, query with both variants, then clean up.

    All operations run in a single async context to keep peer connections alive.
    """
    # Establish all streams concurrently
    await asyncio.gather(
        *[
            establish_stream(ctx, api_config, test_endpoints, test_params)
            for ctx in stream_contexts
        ]
    )

    active_contexts = [ctx for ctx in stream_contexts if ctx.success]
    assert len(active_contexts) > 0, "No streams were successfully established"

    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = test_params["timeout"]
    pause_url = f"{base_url}{test_endpoints['pause']}"
    query_url = f"{base_url}{test_endpoints['query']}"

    # Pause all active streams
    for ctx in active_contexts:
        assert ctx.media_session_id, (
            f"No media session ID for stream {ctx.stream_id}"
        )
        pause_response = requests.post(
            pause_url,
            json={
                "peerId": ctx.peer_id,
                "mediaSessionId": ctx.media_session_id,
            },
            timeout=timeout,
            verify=verify_ssl,
        )

        assert_with_detailed_failure(
            pause_response.status_code == 200,
            f"[{ctx.stream_id}] Pause API HTTP Status",
            "HTTP 200",
            f"HTTP {pause_response.status_code}",
            additional_info=f"Response: {pause_response.text}",
        )

        assert_with_detailed_failure(
            pause_response.json() is True,
            f"[{ctx.stream_id}] Pause API Response",
            "true",
            f"{pause_response.json()}",
        )

        logger.info(
            "Stream %s paused (peer_id=%s, media_session=%s)",
            ctx.stream_id,
            ctx.peer_id,
            ctx.media_session_id,
        )

    logger.info(
        "All %d streams paused. Waiting %ds for pipelines to settle.",
        len(active_contexts),
        PAUSE_SETTLE_DELAY_SECONDS,
    )

    # Wait for all gstreamer pipelines to fully pause
    await asyncio.sleep(PAUSE_SETTLE_DELAY_SECONDS)

    # Query each stream with peerid
    peerid_ts_map: Dict[str, int] = {}
    for ctx in active_contexts:
        peerid_response = requests.get(
            query_url,
            params={"peerid": ctx.peer_id, "metadata": "false"},
            timeout=timeout,
            verify=verify_ssl,
        )

        assert_with_detailed_failure(
            peerid_response.status_code == 200,
            f"[{ctx.stream_id}] Query with peerid HTTP Status",
            "HTTP 200",
            f"HTTP {peerid_response.status_code}",
            additional_info=f"Response: {peerid_response.text}",
        )

        peerid_data = peerid_response.json()
        assert "ts" in peerid_data, (
            f"[{ctx.stream_id}] Response missing 'ts' field: {peerid_data}"
        )
        peerid_ts_map[ctx.peer_id] = peerid_data["ts"]

        logger.info(
            "Query with peerid for %s: ts=%d (peer_id=%s)",
            ctx.stream_id,
            peerid_data["ts"],
            ctx.peer_id,
        )

    # Query without peerid (single call returns all)
    all_response = requests.get(
        query_url,
        timeout=timeout,
        verify=verify_ssl,
    )

    assert_with_detailed_failure(
        all_response.status_code == 200,
        "Query without peerid HTTP Status",
        "HTTP 200",
        f"HTTP {all_response.status_code}",
        additional_info=f"Response: {all_response.text}",
    )

    all_data = all_response.json()
    assert isinstance(all_data, list), (
        f"Expected JSON array, got {type(all_data).__name__}"
    )

    # Build frameTime map from array response
    frame_time_map: Dict[str, int] = {}
    for entry in all_data:
        entry_peer_id = entry.get("peerId")
        if entry_peer_id in peerid_ts_map:
            frame_time_map[entry_peer_id] = entry["frameTime"]

    # Build results
    results: List[Dict[str, Any]] = []
    for ctx in active_contexts:
        assert ctx.peer_id in frame_time_map, (
            f"Peer {ctx.peer_id} ({ctx.stream_id}) not found in array query response"
        )
        results.append(
            {
                "stream_id": ctx.stream_id,
                "peer_id": ctx.peer_id,
                "ts": peerid_ts_map[ctx.peer_id],
                "frame_time": frame_time_map[ctx.peer_id],
            }
        )

        logger.info(
            "Pause query results for %s: ts=%d, frameTime=%d",
            ctx.stream_id,
            peerid_ts_map[ctx.peer_id],
            frame_time_map[ctx.peer_id],
        )

    # Clean up all streams
    await asyncio.gather(*[cleanup_stream(ctx) for ctx in stream_contexts])

    return results


@when("the replay streams are started paused and queried with both query variants")
def start_pause_and_query(context, api_config, test_endpoints, test_params):
    """Start streams concurrently, pause all, query with both variants in one async context."""
    assert len(context.test_data) > 0, "No test data available"

    parallelism = test_params.get("parallelism", 1)
    num_streams = min(parallelism, len(context.test_data))
    selected_items = random.sample(context.test_data, num_streams)

    logger.info(
        "Selected %d/%d streams for pause query test (parallelism=%d): %s",
        num_streams,
        len(context.test_data),
        parallelism,
        [item["stream_id"] for item in selected_items],
    )

    for test_item in selected_items:
        context.stream_contexts.append(
            WebRTCQueryStreamContext(
                stream_id=test_item["stream_id"],
                start_time=test_item["start_time"],
                end_time=test_item["end_time"],
            )
        )

    context.pause_query_results = asyncio.run(
        establish_all_pause_query_and_cleanup(
            context.stream_contexts, api_config, test_endpoints, test_params
        )
    )


@then("the ts from peerid query matches the frameTime from the array query for each stream")
def verify_paused_query_consistency(context):
    """Verify that ts and frameTime are equal for each paused stream."""
    assert len(context.pause_query_results) > 0, "No pause query results available"

    for result in context.pause_query_results:
        stream_id = result["stream_id"]
        ts = result["ts"]
        frame_time = result["frame_time"]

        assert_with_detailed_failure(
            ts == frame_time,
            f"[{stream_id}] Paused Stream Query Consistency (ts == frameTime)",
            f"ts ({ts}) == frameTime ({frame_time})",
            f"ts ({ts}) != frameTime ({frame_time})",
            additional_info=(
                f"Difference: {abs(ts - frame_time)} ms\n"
                f"  ts (peerid query): {ts} "
                f"({datetime.fromtimestamp(ts / 1000).isoformat()}Z)\n"
                f"  frameTime (array query): {frame_time} "
                f"({datetime.fromtimestamp(frame_time / 1000).isoformat()}Z)"
            ),
        )

        logger.info(
            "[%s] Paused query consistency verified: ts=%d == frameTime=%d",
            stream_id,
            ts,
            frame_time,
        )

    logger.info(
        "All %d streams passed pause query consistency check",
        len(context.pause_query_results),
    )
