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

"""Capture RGB frames from a live WebRTC session with overlay enabled."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import websockets
from aiortc import (
    RTCConfiguration,
    RTCIceCandidate,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)

logger = logging.getLogger(__name__)

WS_PATH = "/vst/api/v1/live/ws"


def _parse_ice_candidate(candidate_str: str, sdp_mid: str, sdp_mline_index: int) -> RTCIceCandidate:
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
        sdpMLineIndex=sdp_mline_index,
    )


def _frame_to_rgb_bytes(frame) -> Tuple[bytes, int, int]:
    """Convert an aiortc/av VideoFrame to packed rgb24 bytes (no numpy/Pillow)."""
    rgb = frame.reformat(format="rgb24")
    w, h = rgb.width, rgb.height
    plane = rgb.planes[0]
    row_bytes = w * 3
    line_size = int(plane.line_size)
    if line_size == row_bytes:
        data = bytes(plane)
        expected = row_bytes * h
        if len(data) < expected:
            raise RuntimeError(f"short RGB plane: got {len(data)}, expected {expected}")
        return data[:expected], w, h
    # Strip per-row stride padding so border scans hit the correct pixels.
    raw = memoryview(plane)
    out = bytearray(row_bytes * h)
    for y in range(h):
        start = y * line_size
        out[y * row_bytes:(y + 1) * row_bytes] = raw[start:start + row_bytes]
    return bytes(out), w, h


def _overlay_options(color: str = "red", thickness: int = 11) -> Dict[str, Any]:
    """Legacy overlay schema required by ``/api/v1/live/stream/start`` validation.

    Picture APIs accept the newer ``bbox.showAll`` object; WebRTC start still
    validates against ``needBbox`` / ``needTripwire`` / ``needRoi`` booleans.
    """
    return {
        "needBbox": True,
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
        "color": color,
        "thickness": int(thickness),
    }


async def capture_live_webrtc_overlay_frames(
    base_url: str,
    stream_id: str,
    *,
    collect_frames: int = 30,
    warmup_frames: int = 10,
    signaling_timeout: float = 60.0,
    overlay_color: str = "red",
    overlay_thickness: int = 11,
) -> List[Tuple[bytes, int, int]]:
    """Start a live WebRTC session with overlay and return RGB frame samples.

    Returns a list of ``(rgb24_bytes, width, height)`` after discarding
    ``warmup_frames``, up to ``collect_frames`` samples.
    """
    parsed = urlparse(base_url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    connection_id = str(uuid.uuid4())
    peer_id = str(uuid.uuid4())
    ws_url = (
        f"{ws_scheme}://{parsed.netloc}{WS_PATH}"
        f"?connectionId={connection_id}&streamId={stream_id}"
    )

    collected: List[Tuple[bytes, int, int]] = []
    media_session_id: Optional[str] = None
    peer_connection: Optional[RTCPeerConnection] = None
    websocket = None
    ping_task = None
    track_task = None
    ice_connected = asyncio.Event()
    frames_done = asyncio.Event()

    try:
        websocket = await websockets.connect(ws_url, ping_interval=None, ping_timeout=None)

        async def keepalive():
            try:
                while not websocket.closed:
                    await asyncio.sleep(10)
                    if not websocket.closed:
                        await websocket.send(json.dumps({"apiKey": "api/v1/live/ping"}))
            except Exception:  # noqa: BLE001
                pass

        ping_task = asyncio.create_task(keepalive())

        await websocket.send(json.dumps({
            "apiKey": "api/v1/live/configuration", "data": None, "peerId": peer_id,
        }))
        await websocket.send(json.dumps({
            "apiKey": "api/v1/live/iceServers",
            "peerId": peer_id,
            "data": {"peerId": peer_id},
        }))

        received_config = False
        received_ice = False
        ice_configuration = None
        while not (received_config and received_ice):
            message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10.0))
            api_key = message.get("apiKey", "")
            if api_key == "api/v1/live/configuration":
                received_config = True
            elif api_key == "api/v1/live/iceServers":
                received_ice = True
                ice_servers = message.get("data", {}).get("iceServers", [])
                if ice_servers:
                    ice_configuration = RTCConfiguration(
                        iceServers=[RTCIceServer(urls=srv["urls"]) for srv in ice_servers]
                    )

        peer_connection = (
            RTCPeerConnection(configuration=ice_configuration)
            if ice_configuration else RTCPeerConnection()
        )

        @peer_connection.on("iceconnectionstatechange")
        async def on_ice_state():
            state = peer_connection.iceConnectionState
            logger.info("overlay-webrtc ICE state: %s", state)
            if state in ("connected", "completed"):
                ice_connected.set()

        @peer_connection.on("icecandidate")
        async def on_ice_candidate(candidate):
            if candidate:
                await websocket.send(json.dumps({
                    "apiKey": "api/v1/live/iceCandidate",
                    "peerId": peer_id,
                    "data": [{
                        "candidate": candidate.candidate,
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex,
                    }],
                }))

        @peer_connection.on("track")
        async def on_track(track):
            if track.kind != "video":
                return
            try:
                seen = 0
                need = warmup_frames + collect_frames
                while seen < need:
                    frame = await track.recv()
                    seen += 1
                    if seen <= warmup_frames:
                        continue
                    collected.append(_frame_to_rgb_bytes(frame))
                frames_done.set()
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("overlay-webrtc track error: %s", exc)
                frames_done.set()

        peer_connection.addTransceiver("audio", direction="recvonly")
        peer_connection.addTransceiver("video", direction="recvonly")

        offer = await peer_connection.createOffer()
        await peer_connection.setLocalDescription(offer)
        await websocket.send(json.dumps({
            "apiKey": "api/v1/live/stream/start",
            "peerId": peer_id,
            "data": {
                "clientIpAddr": None,
                "peerId": peer_id,
                "sessionDescription": {
                    "sdp": peer_connection.localDescription.sdp,
                    "type": peer_connection.localDescription.type,
                },
                "options": {
                    "rtptransport": "udp",
                    "timeout": 60,
                    "quality": "auto",
                    "overlay": _overlay_options(overlay_color, overlay_thickness),
                },
                "streamId": stream_id,
            },
        }))

        deadline = asyncio.get_event_loop().time() + signaling_timeout
        got_answer = False
        while asyncio.get_event_loop().time() < deadline:
            if frames_done.is_set() and len(collected) >= max(1, collect_frames // 3):
                break
            try:
                message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2.0))
            except asyncio.TimeoutError:
                continue
            api_key = message.get("apiKey", "")
            if api_key == "api/v1/live/ping":
                continue
            if api_key == "api/v1/live/setAnswer":
                data = message.get("data", {})
                media_session_id = data.get("mediaSessionId")
                got_answer = True
                logger.info(
                    "overlay-webrtc setAnswer session=%s has_sdp=%s",
                    media_session_id, bool(data.get("sdp")),
                )
                if data.get("sdp") and data.get("type"):
                    answer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
                    await peer_connection.setRemoteDescription(answer)
                    await asyncio.sleep(1.0)
            elif api_key == "api/v1/live/iceCandidate":
                for cand_info in message.get("data", []) or []:
                    try:
                        cand = _parse_ice_candidate(
                            cand_info["candidate"],
                            cand_info["sdpMid"],
                            cand_info["sdpMLineIndex"],
                        )
                        await peer_connection.addIceCandidate(cand)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("ice candidate skip: %s", exc)
            elif api_key in (
                "api/v1/live/peerConnectionError",
                "api/v1/live/error",
                "error",
            ):
                logger.error("overlay-webrtc signaling error: %s", message)

            if frames_done.is_set():
                break

        # Allow track task a moment after ICE if signaling loop exited early.
        try:
            await asyncio.wait_for(frames_done.wait(), timeout=20.0)
        except asyncio.TimeoutError:
            pass

        if not collected:
            raise AssertionError(
                f"No WebRTC video frames captured for stream {stream_id} "
                f"(ICE connected={ice_connected.is_set()}, got_answer={got_answer}, "
                f"pc={getattr(peer_connection, 'connectionState', '?')}/"
                f"{getattr(peer_connection, 'iceConnectionState', '?')})"
            )
        logger.info(
            "overlay-webrtc captured %d RGB frames for %s",
            len(collected), stream_id,
        )
        return collected
    finally:
        if track_task is not None:
            track_task.cancel()
        if ping_task is not None:
            ping_task.cancel()
        if peer_connection is not None:
            try:
                await peer_connection.close()
            except Exception:  # noqa: BLE001
                pass
        if websocket is not None and media_session_id:
            try:
                if not websocket.closed:
                    await websocket.send(json.dumps({
                        "apiKey": "api/v1/live/stream/stop",
                        "peerId": peer_id,
                        "data": {"peerId": peer_id, "mediaSessionId": media_session_id},
                    }))
                    await asyncio.sleep(0.3)
            except Exception:  # noqa: BLE001
                pass
        if websocket is not None and not websocket.closed:
            try:
                await websocket.close()
            except Exception:  # noqa: BLE001
                pass


def sample_first_middle_last(frames: List[Any]) -> List[Tuple[int, Any]]:
    """Return (index, frame) for first, middle, and last entries."""
    if not frames:
        return []
    if len(frames) == 1:
        return [(0, frames[0])]
    if len(frames) == 2:
        return [(0, frames[0]), (1, frames[1])]
    mid = len(frames) // 2
    return [(0, frames[0]), (mid, frames[mid]), (len(frames) - 1, frames[-1])]
