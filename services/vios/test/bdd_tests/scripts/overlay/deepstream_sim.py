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
DeepStream simulator for the VIOS overlay path.

Mirrors what DeepStream does end-to-end:
  1. Listen on the broker (redis/kafka) `vst_events` topic for VIOS
     `camera_status_change` events with change == "camera_streaming".
  2. Pull the `camera_url` (VIOS RTSP proxy) + codec from the event.
  3. Read the RTSP and parse each frame's SEI (VST_CUSTOM_META) to get the
     server-authored (frameId, timestamp) -- see live555 testRTSPClient.cpp
     `afterGettingFrame()` / `parseSeiFrameId()` and VIOS NvMediaSource SEI insert.
  4. Publish overlay metadata (a centered bbox) back to `vst-overlay-test` with the
     objectId set to the SEI frameId and the metadata timestamp set to the SEI PTS,
     so VIOS draws the box on exactly the frame it belongs to.

SEI layout (VST_CUSTOM_META), from MultiFramedRTPSource.hh:
  typedef struct { int64_t frameId; int64_t timestamp; } SeiFramePayload;  // LE, 16B
  NAL: [startcode][NALhdr][payloadType 0x05(h264)/0x01(h265)][payloadSize][16B UUID][16B payload]
  pts_ms = timestamp / 1000
"""
from __future__ import annotations

import argparse
import json
import logging
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # bdd_tests dir for scripts.*

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("deepstream_sim")

VST_UUID = b"VST_CUSTOM_META"       # 15 chars, padded to a 16-byte UUID
MEGA_UUID = b"NVDS_CUSTOMMETA"


# --------------------------------------------------------------------------- #
# SEI parsing                                                                  #
# --------------------------------------------------------------------------- #
def _split_nals(buf: bytes):
    """Yield NAL units (without start code) from an Annex-B / AVCC buffer."""
    if buf[:3] == b"\x00\x00\x01" or buf[:4] == b"\x00\x00\x00\x01":
        i, n = 0, len(buf)
        starts = []
        while i < n - 3:
            if buf[i] == 0 and buf[i + 1] == 0 and buf[i + 2] == 1:
                starts.append(i + 3)
            i += 1
        for k, s in enumerate(starts):
            e = starts[k + 1] - 3 if k + 1 < len(starts) else len(buf)
            while e > s and buf[e - 1] == 0:      # trim trailing zeros before next start
                e -= 1
            yield buf[s:e]
    else:                                          # AVCC: 4-byte length prefixes
        i, n = 0, len(buf)
        while i + 4 <= n:
            ln = int.from_bytes(buf[i:i + 4], "big")
            i += 4
            if ln <= 0 or i + ln > n:
                break
            yield buf[i:i + ln]
            i += ln


def parse_vst_sei(nal: bytes, codec: str):
    """Return (frameId, pts_ms) from a VIOS user-data SEI NAL, else None. Handles both
    UUIDs: VST_CUSTOM_META (binary {int64 frameId, int64 timestamp}) and
    NVDS_CUSTOMMETA (JSON {frame_id|frame_num, timestamp}). pts_ms = timestamp/1000."""
    if not nal:
        return None
    h265 = codec.upper() in ("H265", "HEVC")
    if h265:
        if len(nal) < 3 or ((nal[0] >> 1) & 0x3F) not in (39, 40):   # prefix/suffix SEI
            return None
        hdr = 2                                                      # 2-byte NAL header
    else:
        if (nal[0] & 0x1F) != 6:                                     # SEI
            return None
        hdr = 1                                                      # 1-byte NAL header
    if len(nal) < hdr + 2 + 16 or nal[hdr] != 0x05:                  # user_data_unregistered
        return None
    size = nal[hdr + 1]                                              # SEI payloadSize (<255 here)
    uuid = nal[hdr + 2: hdr + 2 + 16]
    payload = nal[hdr + 2 + 16: hdr + 2 + max(size, 16)]             # bytes after the 16B UUID
    if VST_UUID in uuid:
        if len(payload) < 16:
            return None
        frame_id, ts = struct.unpack_from("<qq", payload, 0)
        if frame_id == -1 or ts < 0:
            return None
        return frame_id, ts // 1000
    if MEGA_UUID in uuid:
        try:
            txt = payload.split(b"\x00", 1)[0].decode("utf-8", "ignore").strip()
            j = json.loads(txt)
            fid = j.get("frame_id", j.get("frame_num", -1))
            ts = j.get("timestamp", -1)                              # epoch NANOSECONDS
            if int(fid) < 0 or int(ts) < 0:
                return None
            return int(fid), int(ts) // 1_000_000                   # ns -> epoch ms
        except Exception:  # noqa: BLE001
            return None
    return None


def iter_rtsp_sei(rtsp_url: str, codec: str, seconds: float):
    """Open the RTSP and yield (frameId, pts_ms) parsed from each frame's SEI."""
    import av                                                        # PyAV
    opts = {"rtsp_transport": "tcp", "stimeout": "5000000"}
    container = av.open(rtsp_url, options=opts, timeout=10)
    try:
        vstream = next(s for s in container.streams if s.type == "video")
        deadline = time.time() + seconds
        for packet in container.demux(vstream):
            if time.time() > deadline:
                break
            data = bytes(packet)
            if not data:
                continue
            for nal in _split_nals(data):
                r = parse_vst_sei(nal, codec)
                if r:
                    yield r
    finally:
        container.close()


# --------------------------------------------------------------------------- #
# Overlay metadata publish (reuses the overlay lib serialization)             #
# --------------------------------------------------------------------------- #
def make_publisher(broker, sensor_id, topic, width, height, redis_host, redis_port, kafka):
    from scripts.overlay.live_publisher import LiveBoxSpec, RedisBoxPublisher, KafkaBoxPublisher
    spec = LiveBoxSpec(sensor_id=sensor_id, width=width, height=height)
    if broker == "kafka":
        return KafkaBoxPublisher(kafka, topic, spec, fps=30), spec
    return RedisBoxPublisher(redis_host, redis_port, topic, spec, fps=30), spec


def stream_camera(camera_url, sensor_id, codec, broker, topic, width, height,
                  redis_host, redis_port, kafka, seconds):
    """Read SEI from one camera's RTSP and publish a bbox per frame, stamped with
    the SEI frameId (objectId) + SEI pts (metadata timestamp)."""
    pub, spec = make_publisher(broker, sensor_id, topic, width, height, redis_host, redis_port, kafka)
    n = 0
    try:
        for frame_id, pts_ms in iter_rtsp_sei(camera_url, codec, seconds):
            # one metadata doc at this frame's exact PTS, objectId = SEI frameId
            spec.obj_id = str(frame_id)
            pub.publish_once(epoch_ms=pts_ms)
            n += 1
        log.info("camera %s: published %d SEI-aligned metadata docs", sensor_id, n)
    except Exception as e:  # noqa: BLE001
        log.warning("camera %s stream error: %s", sensor_id, e)
    finally:
        try:
            pub.stop()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# camera_streaming event listener                                             #
# --------------------------------------------------------------------------- #
def listen_camera_streaming(broker, events_topic, redis_host, redis_port, kafka, on_camera,
                            seconds, stop_event=None):
    """Consume `camera_status_change` events; call on_camera(url, sensor_id, codec) for each
    change == 'camera_streaming'. Runs until `stop_event` is set when given (a continuous
    subscriber), otherwise for `seconds` (one-shot). The redis path keeps a single `last`
    cursor across the whole run so no event is missed."""
    deadline = time.time() + seconds
    _go = (lambda: not stop_event.is_set()) if stop_event is not None else (lambda: time.time() < deadline)
    if broker == "kafka":
        from confluent_kafka import Consumer
        c = Consumer({"bootstrap.servers": kafka, "group.id": "deepstream-sim",
                      "auto.offset.reset": "latest"})
        c.subscribe([events_topic])
        while _go():
            msg = c.poll(1.0)
            if msg is None or msg.error():
                continue
            _handle_event(msg.value(), on_camera)
        c.close()
    else:
        import redis
        r = redis.Redis(host=redis_host, port=redis_port)
        last = "$"
        while _go():
            resp = r.xread({events_topic: last}, count=10, block=1000)
            for _stream, entries in resp or []:
                for eid, fields in entries:
                    last = eid
                    payload = fields.get(b"sensor.id") or next(iter(fields.values()), b"")
                    _handle_event(payload, on_camera)


def _handle_event(raw, on_camera):
    try:
        d = json.loads(raw)
        ev = d.get("event", {})
        url = ev.get("camera_url", "")
        # Only real RTSP sensors -- camera_streaming also fires for file sensors; ignore those.
        if ev.get("change") == "camera_streaming" and url.startswith("rtsp://"):
            codec = (ev.get("metadata", {}) or {}).get("codec", "H264")
            on_camera(url, ev.get("camera_name") or ev.get("camera_id"), codec)
    except Exception:  # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default="redis", choices=["redis", "kafka"])
    ap.add_argument("--events-topic", default="vst_events")
    ap.add_argument("--meta-topic", default="vst-overlay-test")
    ap.add_argument("--redis-host", default="localhost")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--kafka", default="172.17.0.1:9092")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--seconds", type=float, default=15.0)
    # Direct mode: skip the event listener, stream a known camera immediately.
    ap.add_argument("--camera-url")
    ap.add_argument("--sensor-id")
    ap.add_argument("--codec", default="H264")
    a = ap.parse_args()

    if a.camera_url and a.sensor_id:
        stream_camera(a.camera_url, a.sensor_id, a.codec, a.broker, a.meta_topic,
                      a.width, a.height, a.redis_host, a.redis_port, a.kafka, a.seconds)
        return

    threads = []

    def on_camera(url, sensor_id, codec):
        log.info("camera_streaming: %s (%s) -> %s", sensor_id, codec, url)
        t = threading.Thread(target=stream_camera, daemon=True, args=(
            url, sensor_id, codec, a.broker, a.meta_topic, a.width, a.height,
            a.redis_host, a.redis_port, a.kafka, a.seconds))
        t.start(); threads.append(t)

    listen_camera_streaming(a.broker, a.events_topic, a.redis_host, a.redis_port,
                            a.kafka, on_camera, a.seconds)
    for t in threads:
        t.join(timeout=5)


if __name__ == "__main__":
    main()
