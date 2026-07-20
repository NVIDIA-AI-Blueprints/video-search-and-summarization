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
Capture a VIOS WebRTC stream (live or replay) with the bbox+debug overlay to MP4.

Mirrors the web UI's WebRTC signaling (same as tests/webrtc/*). ``--mode live``
plays the live stream (metadata from the broker; publish concurrently). ``--mode
replay`` plays a recorded window ``[start,end]`` (metadata from Elasticsearch; the
caller seeds it). Proof of the live/replay WebRTC overlay path end to end.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import av
import websockets
from aiortc import (RTCConfiguration, RTCIceServer, RTCPeerConnection,
                    RTCSessionDescription)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.webrtc.webrtc_test_utils import parse_ice_candidate  # noqa: E402
from scripts.overlay.live_publisher import LiveBoxSpec, RedisBoxPublisher, KafkaBoxPublisher  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("webrtc_capture")

_OVERLAY = {"needBbox": True, "needTripwire": False, "needRoi": False, "debug": True,
            "opacity": 255, "framerate": 30, "objectId": [], "proximityClass": [],
            "entrantClass": [], "proximityAreaFactor": 1.3, "proximityAnimation": "",
            "overlayColorCode": [], "needHalo": False}


async def capture(base_url, stream_id, seconds, out_path, mode="live",
                  start_iso=None, end_iso=None, overlay=None) -> int:
    _overlay_opt = overlay or _OVERLAY
    px = mode  # "live" or "replay" -> apiKey / ws path prefix
    parsed = urlparse(base_url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    peer_id, conn_id = str(uuid.uuid4()), str(uuid.uuid4())
    ws_url = f"{ws_scheme}://{parsed.netloc}/vst/api/v1/{px}/ws?connectionId={conn_id}&streamId={stream_id}"

    frames = []
    ws = await websockets.connect(ws_url, ping_interval=None, ping_timeout=None)
    await ws.send(json.dumps({"apiKey": f"api/v1/{px}/configuration", "data": None, "peerId": peer_id}))
    await ws.send(json.dumps({"apiKey": f"api/v1/{px}/iceServers", "peerId": peer_id, "data": {"peerId": peer_id}}))
    ice_cfg = None
    got_cfg = got_ice = False
    while not (got_cfg and got_ice):
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        k = m.get("apiKey", "")
        if k == f"api/v1/{px}/configuration":
            got_cfg = True
        elif k == f"api/v1/{px}/iceServers":
            srvs = (m.get("data") or {}).get("iceServers", [])
            got_ice = True
            if srvs:
                ice_cfg = RTCConfiguration(iceServers=[RTCIceServer(urls=s["urls"]) for s in srvs])

    pc = RTCPeerConnection(configuration=ice_cfg) if ice_cfg else RTCPeerConnection()
    deadline = {"t": None}

    @pc.on("iceconnectionstatechange")
    async def _ice():
        log.info("ICE state: %s", pc.iceConnectionState)

    @pc.on("icecandidate")
    async def _cand(c):
        if c:
            await ws.send(json.dumps({"apiKey": f"api/v1/{px}/iceCandidate",
                                      "data": [{"candidate": c.candidate, "sdpMid": c.sdpMid,
                                                "sdpMLineIndex": c.sdpMLineIndex}], "peerId": peer_id}))

    @pc.on("track")
    async def _track(track):
        if track.kind != "video":
            return
        while True:
            try:
                frames.append(await track.recv())
            except Exception:
                break
            if deadline["t"] and time.monotonic() >= deadline["t"]:
                break

    pc.addTransceiver("audio", direction="recvonly")
    pc.addTransceiver("video", direction="recvonly")
    await pc.setLocalDescription(await pc.createOffer())
    data = {"clientIpAddr": None, "peerId": peer_id,
            "sessionDescription": {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
            "options": {"rtptransport": "udp", "timeout": 60, "quality": "auto", "overlay": _overlay_opt},
            "streamId": stream_id}
    if mode == "replay":
        data["startTime"] = start_iso
        data["endTime"] = end_iso
    await ws.send(json.dumps({"apiKey": f"api/v1/{px}/stream/start", "peerId": peer_id, "data": data}))

    start = time.time()
    while time.time() - start < seconds + 8:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        except asyncio.TimeoutError:
            if deadline["t"] and time.monotonic() >= deadline["t"]:
                break
            continue
        k = m.get("apiKey", "")
        if k == f"api/v1/{px}/setAnswer":
            d = m.get("data", {})
            if d.get("sdp") and d.get("type"):
                await pc.setRemoteDescription(RTCSessionDescription(sdp=d["sdp"], type=d["type"]))
                deadline["t"] = time.monotonic() + seconds
        elif k == f"api/v1/{px}/iceCandidate":
            for ci in (m.get("data") or []):
                try:
                    await pc.addIceCandidate(parse_ice_candidate(ci["candidate"], ci["sdpMid"], ci["sdpMLineIndex"]))
                except Exception:
                    pass
        if deadline["t"] and time.monotonic() >= deadline["t"] and len(frames) > 5:
            break

    await pc.close()
    await ws.close()
    log.info("captured %d frames; encoding %s", len(frames), out_path)
    if not frames:
        return 0
    _encode(frames, out_path, fps=30)
    return len(frames)


def _encode(frames, out_path, fps=30):
    from fractions import Fraction
    first = frames[0]
    out = av.open(out_path, "w")
    st = out.add_stream("libx264", rate=fps)
    st.width, st.height, st.pix_fmt = first.width, first.height, "yuv420p"
    st.time_base = Fraction(1, fps)
    for i, fr in enumerate(frames):
        nf = fr.reformat(format="yuv420p")
        nf.pts = i
        nf.time_base = Fraction(1, fps)
        for pkt in st.encode(nf):
            out.mux(pkt)
    for pkt in st.encode():
        out.mux(pkt)
    out.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:30888")
    p.add_argument("--stream-id", default="warehouse_sample")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--out", default="/tmp/vios_live/webrtc_overlay.mp4")
    p.add_argument("--mode", default="live", choices=["live", "replay"])
    p.add_argument("--start-time", help="replay window start (ISO8601)")
    p.add_argument("--end-time", help="replay window end (ISO8601)")
    p.add_argument("--broker", default="redis", choices=["redis", "kafka"])
    p.add_argument("--topic", default="vst-overlay-test")
    p.add_argument("--no-publish", action="store_true",
                   help="don't publish metadata; consume only (e.g. an external metadata_service feeds the broker)")
    p.add_argument("--show-obj-id", action="store_true",
                   help="send the new-schema overlay with bbox.showObjId=true (draws the objectId on the box)")
    args = p.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    overlay = None
    if args.show_obj_id:  # exact browser stream/start overlay schema, with objId shown
        overlay = {
            "bbox": {"showAll": True, "objectId": [], "classType": [], "showObjId": True,
                     "objIdPosition": 0, "objIdTextColor": "white", "objIdTextBGColor": "black"},
            "tripwire": {"showAll": False, "id": []},
            "roi": {"showAll": False, "id": []},
            "debug": True, "entrantClass": [], "needHalo": False, "opacity": 255,
            "overlayColorCode": [], "proximityAnimation": "", "proximityAreaFactor": 1.3,
            "proximityClass": [],
        }

    pub = t = None
    if args.mode == "live" and not args.no_publish:  # replay reads metadata from ES (seeded by caller)
        spec = LiveBoxSpec(sensor_id=args.stream_id)
        pub = (RedisBoxPublisher("localhost", 6379, args.topic, spec, fps=30)
               if args.broker == "redis" else
               KafkaBoxPublisher("172.17.0.1:9092", args.topic, spec, fps=30))
        t = threading.Thread(target=pub.run, args=(args.seconds + 12,), daemon=True)
        t.start()

    n = asyncio.get_event_loop().run_until_complete(
        capture(args.base_url, args.stream_id, args.seconds, args.out,
                mode=args.mode, start_iso=args.start_time, end_iso=args.end_time, overlay=overlay))
    if pub:
        pub.stop(); t.join(timeout=5)
    print(f"RESULT: captured {n} frames -> {args.out}")
    return 0 if n > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
