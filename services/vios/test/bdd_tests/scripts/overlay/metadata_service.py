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
Continuous overlay-metadata service -- a standing DeepStream stand-in.

Unlike the one-shot publisher, this runs CONTINUOUSLY for every RTSP sensor so a
user can play webrtc-live or replay at any time and always find metadata:
  * parses each frame's SEI (frameId + PTS) off the VIOS proxy RTSP,
  * publishes a centered bbox to the LIVE broker (redis/kafka) -> live overlay,
  * appends the same bbox as an ES document to an in-process fake-ES on
    :19200 (video_metadata_server) -> download / replay overlay.
On stop (SIGINT/SIGTERM) it clears the broker stream + ES so the box is left clean.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.overlay.fake_es_server import FakeESServer          # noqa: E402
from scripts.overlay.deepstream_sim import parse_vst_sei          # noqa: E402
from scripts.overlay.live_publisher import (                      # noqa: E402
    LiveBoxSpec, RedisBoxPublisher, KafkaBoxPublisher)
from scripts.overlay.metadata_generator import epoch_ms_to_iso_z  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("metadata_service")

_STOP = threading.Event()

_STREAMING_PATH = "/bdd/webhooks/camera/streaming"
_REMOVE_PATH = "/bdd/webhooks/camera/remove"
_MAX_WEBHOOK_BODY = 1024 * 1024


class _OverlayWebhookHandler(BaseHTTPRequestHandler):
    """Receive VIOS camera lifecycle callbacks for direct-mode sanity."""

    protocol_version = "HTTP/1.1"

    def do_PUT(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch(_STREAMING_PATH, "camera_streaming")

    def do_DELETE(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch(_REMOVE_PATH, "camera_remove")

    def _dispatch(self, expected_path, expected_change):
        if urlsplit(self.path).path != expected_path:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "invalid Content-Length")
            return
        if length <= 0 or length > _MAX_WEBHOOK_BODY:
            self.send_error(400 if length <= 0 else 413)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error(400, "invalid JSON")
            return
        event = payload.get("event", {}) if isinstance(payload, dict) else {}
        if event.get("change") != expected_change:
            self.send_error(400, f"expected event.change={expected_change}")
            return
        server = self.server
        try:
            if expected_change == "camera_streaming":
                url = str(event.get("camera_url") or "")
                sensor_id = event.get("camera_name") or event.get("camera_id")
                is_file = event.get("camera_type") == "file"
                if not sensor_id or (not is_file and not url.startswith("rtsp://")):
                    raise ValueError(
                        "camera_streaming requires a camera name and an RTSP URL unless file-backed"
                    )
                codec = (event.get("metadata") or {}).get("codec", "H264")
                if not is_file:
                    server.on_streaming(str(sensor_id), url, str(codec))
            else:
                sensor_id = event.get("camera_name") or event.get("camera_id")
                if not sensor_id:
                    raise ValueError("camera_remove requires camera_id")
                server.on_remove(str(sensor_id))
        except ValueError as exc:
            self.send_error(400, str(exc))
            return
        response = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)
        self.close_connection = True

    def log_message(self, _format, *_args):
        return


class OverlayWebhookServer:
    """Threaded HTTP receiver for camera_streaming and camera_remove."""

    def __init__(self, host, port, on_streaming, on_remove):
        self._server = ThreadingHTTPServer((host, port), _OverlayWebhookHandler)
        self._server.on_streaming = on_streaming
        self._server.on_remove = on_remove
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="overlay-webhooks", daemon=True)

    @property
    def port(self):
        return self._server.server_address[1]

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _centered_box(w, h, frac=0.20):
    bw, bh = w * frac, h * frac
    cx, cy = w / 2.0, h / 2.0
    return int(cx - bw / 2), int(cy - bh / 2), int(cx + bw / 2), int(cy + bh / 2)


_BACKFILLED = set()          # sensors whose prior-recording ES window is already seeded


def _iso_to_ms(s):
    from datetime import datetime
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def _backfill_es(sensor_id, es, base_url, fps, loop_frames, source_frames, w, h, until_ms=None):
    """Seed ES with per-frame bbox docs from the recording's earliest segment start up to
    `until_ms` (the first live SEI frame; default = now), so a replay/download of time
    recorded BEFORE the live reader attached still carries overlay metadata.

    Anchoring the end to the FIRST live frame (not wall-clock now) is what makes coverage
    CONTINUOUS: recording begins when VIOS deploys and the RTSP reader needs a few seconds to
    connect + decode the first SEI, so a backfill snapshotted at worker start (recording ~0s
    old) fills nothing and leaves that ramp-up window boxless -- a download of the recording's
    first ~15s then only draws the box at the very end (where real-time took over). Ending the
    backfill exactly where real-time begins closes that gap. objectId follows the same
    burned-frame mapping as the live path. Runs once per sensor."""
    if sensor_id in _BACKFILLED or fps <= 0:
        return 0
    try:
        tl = requests.get(f"{base_url}/vst/api/v1/storage/timelines", timeout=15).json() or {}
        ranges = tl.get(sensor_id) or []
        if not ranges:
            return 0                       # no recording yet; try again on the next worker cycle
        t0 = min(_iso_to_ms(r["startTime"]) for r in ranges)
        now = int(until_ms) if until_ms else int(time.time() * 1000)
        if now - t0 < 1500:
            _BACKFILLED.add(sensor_id); return 0
        step = 1000.0 / fps
        docs, i, t = [], 0, float(t0)
        while t < now - 500:
            oid = _burned_object_id(i, loop_frames, source_frames)
            docs.append(_es_doc(sensor_id, oid, int(t), w, h))
            t += step; i += 1
            if len(docs) >= 2000:
                es.store.append(docs); docs = []
        if docs:
            es.store.append(docs)
        _BACKFILLED.add(sensor_id)
        log.info("backfilled %d ES doc(s) for %s (%.1fs of prior recording)",
                 i, sensor_id, (now - t0) / 1000.0)
        return i
    except Exception as e:  # noqa: BLE001
        log.warning("ES backfill for %s failed: %s", sensor_id, e)
        return 0


def _es_doc(sensor_id, frame_id, epoch_ms, w, h):
    lx, ty, rx, by = _centered_box(w, h)
    return {
        "version": "4.0",
        "timestamp": epoch_ms_to_iso_z(epoch_ms),
        "sensorId": sensor_id,
        "id": str(frame_id),
        "objects": [{
            "id": str(frame_id), "type": "Person", "confidence": 0.99,
            "bbox": {"leftX": float(lx), "topY": float(ty),
                     "rightX": float(rx), "bottomY": float(by)},
            "bbox3d": {"coordinates": [0.0] * 12, "confidence": 0.0},
        }],
        "_epoch_ms": epoch_ms,
    }


def _publisher(broker, sensor_id, topic, w, h, redis_host, redis_port, kafka, field):
    spec = LiveBoxSpec(sensor_id=sensor_id, width=w, height=h)
    if broker == "kafka":
        return KafkaBoxPublisher(kafka, topic, spec, fps=30), spec
    return RedisBoxPublisher(redis_host, redis_port, topic, spec, fps=30, field=field), spec


def _discover_sensors(base_url):
    """RTSP sensors + their proxy camera_url + codec + resolution, from VIOS."""
    sensors = requests.get(f"{base_url}/vst/api/v1/sensor/list", timeout=20).json() or []
    # camera_url per sensor from the emitted camera_streaming events (proxy rtsp://.../live/<name>)
    urls = {}
    try:
        import subprocess
        raw = subprocess.run(["docker", "exec", "redis-server", "redis-cli",
                              "XRANGE", "vst_events", "-", "+"], capture_output=True, text=True).stdout
        import re
        for m in re.finditer(r'rtsp://[^"\s]+/live/([^"\s]+)', raw):
            urls[m.group(1)] = m.group(0)
    except Exception as e:  # noqa: BLE001
        log.warning("event scan for camera_url failed: %s", e)
    out = []
    for s in sensors:
        nm = s.get("name", "")
        if not nm or "_file" in nm:                # skip file sensors
            continue
        url = urls.get(nm)
        if not url:
            continue
        res = (s.get("resolution") or "").lower()
        w, h = (1920, 1080)
        if "x" in res:
            try:
                w, h = int(res.split("x")[0]), int(res.split("x")[1])
            except Exception:  # noqa: BLE001
                pass
        codec = "H265" if "h265" in nm.lower() or "hevc" in res else "H264"
        out.append((s.get("sensorId") or nm, url, codec, w, h))
    return out


_FPS_RE = re.compile(r"(\d+)fps")


def _probe_nb_frames(path):
    """Exact frame count of a variant file via ffprobe (0 on failure)."""
    try:
        import subprocess
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-count_frames", "-show_entries", "stream=nb_read_frames",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60).stdout.strip()
        return int(out) if out.isdigit() else 0
    except Exception:  # noqa: BLE001
        return 0


def _media_info(nvs_url):
    """Per-video {name: {frames, fps, w, h, dur}} from the NVStreamer mediainfo API --
    authoritative FrameCount/Framerate/Width/Height/Duration with no file access, and it
    covers a mixed-duration/fps video-set. Returns {} if NVStreamer is unreachable."""
    out = {}
    try:
        lst = requests.get(f"{nvs_url}/api/v1/sensor/list", timeout=15).json() or []
        lst = lst if isinstance(lst, list) else lst.get("sensors", [])
        for s in lst:
            sid, nm = s.get("sensorId"), s.get("name")
            if not sid or not nm:
                continue
            try:
                mi = requests.get(f"{nvs_url}/api/v1/storage/file/mediainfo",
                                  params={"sensorId": sid}, timeout=15).json() or {}
                out[nm] = {"frames": int(mi.get("FrameCount") or 0),
                           "fps": float(mi.get("Framerate") or 0),
                           "w": int(mi.get("Width") or 0),
                           "h": int(mi.get("Height") or 0),
                           "dur": float(mi.get("Duration") or 0)}
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        log.warning("NVStreamer mediainfo unavailable (%s); using ffprobe/stream fallback", e)
    return out


def _metadata_fps(media_info, loop_frames, clip_seconds):
    """Use authoritative media FPS, or derive it from the actual media duration."""
    media_duration = float(media_info.get("dur") or clip_seconds or 0.0)
    return float(
        media_info.get("fps")
        or (loop_frames / media_duration if media_duration else 0.0)
    )


def _loop_frames(sensor_id, videos_dir, clip_seconds):
    """Frames per playback loop for a sensor's variant. Prefer the file's *real* frame
    count (ffprobe) -- ffmpeg's -r resample yields e.g. 302 (not 300) for a 30s 10fps
    clip, and using the wrong loop length makes the per-loop index drift a couple frames
    every loop. Fall back to round(variant_fps * clip_seconds) from the name if the file
    isn't reachable."""
    if videos_dir:
        p = Path(videos_dir) / f"{sensor_id}.mp4"
        if p.exists():
            n = _probe_nb_frames(p)
            if n:
                return n
    m = _FPS_RE.search(sensor_id)
    return int(round(int(m.group(1)) * clip_seconds)) if m else 0


def _burned_object_id(frame_id, loop_frames, source_frames):
    """Map VIOS's *continuous* SEI frameId to the source video's *per-loop* burned-in
    frame number, so the overlay objectId matches the ffmpeg 'Frame N' burn-in.

    The burn resets to 0 each loop (baked into the source pixels); the SEI frameId keeps
    climbing across loops. A variant is `source_frames` source frames (the -t clip)
    resampled to `loop_frames` output frames, so per loop:
        idx    = frameId mod loop_frames         (this loop's output-frame index)
        burned = round(idx * source_frames / loop_frames)
    30fps off a 30fps source -> loop_frames == source_frames == 900 -> burned = frameId
    mod 900 exactly (verified 53031 mod 900 == 831). Downsampled variants use the real
    ffprobe'd loop_frames so the index doesn't drift; residual +-1 comes from ffmpeg's
    non-uniform frame picking, which no closed form can remove."""
    if not loop_frames or not source_frames:
        return frame_id
    return int(round((frame_id % loop_frames) * source_frames / loop_frames))


def run_sensor(sensor_id, url, codec, w, h, broker, topic, es, redis_host, redis_port, kafka,
               field, loop_frames, source_frames, base_url="", fps=0.0, stop_event=None):
    """Continuously read SEI off one RTSP and publish live + append to ES, reconnecting.

    First backfills ES for the already-recorded window (so replay of time recorded before
    this worker attached still has overlay metadata), then streams real-time forward."""
    import av
    backfilled = False
    should_stop = lambda: _STOP.is_set() or (stop_event is not None and stop_event.is_set())
    while not should_stop():
        pub = None
        try:
            container = av.open(url, options={"rtsp_transport": "tcp", "stimeout": "5000000"}, timeout=10)
            vstream = next(s for s in container.streams if s.type == "video")
            # Authoritative source resolution comes from the RTSP stream itself, not VIOS's
            # (often-empty) resolution field. The overlay draws the bbox in source-pixel
            # space, so a centered box must be computed against the ACTUAL WxH -- else it
            # lands off-centre (4K) or off-frame (480p) when everything defaults to 1080p.
            rw = getattr(vstream.codec_context, "width", 0) or w
            rh = getattr(vstream.codec_context, "height", 0) or h
            pub, spec = _publisher(broker, sensor_id, topic, rw, rh, redis_host, redis_port, kafka, field)
            batch, last_flush = [], time.time()
            for packet in container.demux(vstream):
                if should_stop():
                    break
                for nal in re.split(b"\x00\x00\x01", bytes(packet))[1:]:
                    r = parse_vst_sei(nal, codec)
                    if not r:
                        continue
                    frame_id, pts_ms = r
                    # First live frame: backfill [earliest segment .. this frame] so ES
                    # coverage is continuous from recording start up to where real-time takes
                    # over (closes the RTSP-connect ramp-up gap). rw/rh are the authoritative
                    # stream dims, so backfilled boxes are centered correctly too.
                    if base_url and not backfilled:
                        _backfill_es(sensor_id, es, base_url, fps, loop_frames,
                                     source_frames, rw, rh, until_ms=pts_ms)
                        backfilled = True
                    oid = _burned_object_id(frame_id, loop_frames, source_frames)
                    spec.obj_id = str(oid)
                    pub.publish_once(epoch_ms=pts_ms)                 # live broker
                    batch.append(_es_doc(sensor_id, oid, pts_ms, rw, rh))
                if time.time() - last_flush > 1.0 and batch:         # ES in ~1s batches
                    es.store.append(batch)
                    batch, last_flush = [], time.time()
            container.close()
        except Exception as e:  # noqa: BLE001
            log.warning("sensor %s stream error (reconnecting): %s", sensor_id, e)
        finally:
            try:
                pub and pub.stop()
            except Exception:  # noqa: BLE001
                pass
        if not should_stop():
            time.sleep(2)


def _ensure_group(host, port, topic, group="redis-consumer-group"):
    """Idempotently ensure VIOS's consumer group exists on the metadata stream.

    VIOS's nvds redis adaptor consumes via ``XREADGROUP GROUP redis-consumer-group ...``
    over a *shared/persistent* connection and creates the group only once; if the group
    is ever missing (e.g. redis flushed, or a stale run DEL'd the stream) every read
    returns NOGROUP and the live overlay silently gets nothing. Creating it here (MKSTREAM,
    at the tip) self-heals that. Safe to call repeatedly -- BUSYGROUP means it already
    exists and is left untouched (so we never disturb an in-flight read position)."""
    try:
        import redis
        r = redis.Redis(host=host, port=port)
        try:
            r.xgroup_create(topic, group, id="$", mkstream=True)
            log.info("ensured consumer group %s on %s", group, topic)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
    except Exception as e:  # noqa: BLE001
        log.warning("ensure consumer group failed (will retry): %s", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:30888")
    ap.add_argument("--nvstreamer-url", default="http://localhost:31000",
                    help="NVStreamer base URL for mediainfo (per-video FrameCount/WxH/Duration/Framerate)")
    ap.add_argument("--broker", default="redis", choices=["redis", "kafka"])
    ap.add_argument("--meta-topic", default="vst-overlay-test")
    ap.add_argument("--events-topic", default="vst_events",
                    help="VIOS camera_status_change event topic to subscribe for camera_streaming")
    ap.add_argument("--event-transport", default="redis", choices=["redis", "webhook"],
                    help="how camera_streaming/camera_remove reach this plugin")
    ap.add_argument("--webhook-host", default="0.0.0.0")
    ap.add_argument("--webhook-port", type=int, default=18088)
    ap.add_argument("--field", default="value",
                    help="redis stream payload field key (DeepStream schema: 'value'; legacy: 'sensor.id')")
    ap.add_argument("--source-fps", type=float, default=0.0,
                    help="0 = video played as-is: objectId = frameId mod frame_count (EXACT for any "
                         "duration/fps video-set). Set >0 only for OUR synthetic variants that resample a "
                         "burned source (e.g. 30) -- then objectId is scaled to the source frame index (approx).")
    ap.add_argument("--clip-seconds", type=float, default=30.0,
                    help="variant clip length (ffmpeg -t / variant_dur); only used when --source-fps > 0")
    ap.add_argument("--videos-dir",
                    default=str(Path(__file__).resolve().parents[4]
                                / "deployment/stream-processing/docker-compose/nvstreamer/videos/nvstreamer-1"),
                    help="dir holding <sensor>.mp4 variants; ffprobe'd for exact per-loop frame counts")
    ap.add_argument("--redis-host", default="localhost")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--kafka", default="172.17.0.1:9092")
    ap.add_argument("--es-host", default="0.0.0.0")
    ap.add_argument("--es-port", type=int, default=19200)
    ap.add_argument("--es-index", default="mdx-bev-test")
    ap.add_argument("--es-retention-hours", type=float, default=3.0,
                    help="rolling fake-ES event-time history retained per sensor")
    a = ap.parse_args()
    if a.es_retention_hours <= 0:
        ap.error("--es-retention-hours must be greater than zero")

    es = FakeESServer(
        host=a.es_host,
        port=a.es_port,
        index_name=a.es_index,
        retention_hours=a.es_retention_hours,
    ).start()
    log.info(
        "fake-ES (video_metadata_server) up on %s; retaining %.3g hours per sensor",
        es.base_url,
        a.es_retention_hours,
    )

    running = {}       # sensorId -> (worker thread, per-sensor stop event)
    starting = set()   # webhook retry dedupe while a worker is being constructed
    running_lock = threading.Lock()
    minfo_cache = {}   # sensorId -> {frames,w,h} from NVStreamer mediainfo (fetched lazily)

    def _start_worker(sid, url, codec, w=0, h=0):
        """Start one idempotent RTSP metadata worker from a lifecycle event."""
        if not sid or "_file" in sid:                 # overlay is RTSP-only; never file sensors
            return False
        with running_lock:
            current = running.get(sid)
            if sid in starting or (current is not None and current[0].is_alive()):
                return False
            starting.add(sid)
        mi = minfo_cache.get(sid)
        if mi is None:
            try:
                minfo_cache.update(_media_info(a.nvstreamer_url))
            except Exception as exc:  # noqa: BLE001
                log.warning("mediainfo fetch failed: %s", exc)
            mi = minfo_cache.get(sid, {})
        loop_frames = mi.get("frames") or _loop_frames(sid, a.videos_dir, a.clip_seconds)
        mw, mh = (mi.get("w") or w or 1920), (mi.get("h") or h or 1080)
        source_frames = (int(round(a.source_fps * a.clip_seconds))
                         if a.source_fps > 0 else loop_frames)
        fps = _metadata_fps(mi, loop_frames, a.clip_seconds)
        worker_stop = threading.Event()
        th = threading.Thread(target=run_sensor, daemon=True, args=(
            sid, url, codec, mw, mh, a.broker, a.meta_topic, es,
            a.redis_host, a.redis_port, a.kafka, a.field, loop_frames, source_frames,
            a.base_url, fps, worker_stop))
        with running_lock:
            starting.discard(sid)
            running[sid] = (th, worker_stop)
        th.start()
        log.info("started metadata worker: %s (%s) loop_frames=%d", sid, url, loop_frames)
        return True

    def _stop_worker(sid):
        """Idempotently stop the worker associated with camera_remove."""
        with running_lock:
            current = running.get(sid)
        if current is None:
            log.info("camera_remove for inactive worker: %s", sid)
            return False
        current[1].set()
        log.info("stopping metadata worker after camera_remove: %s", sid)
        return True

    def _alive_workers():
        with running_lock:
            return [sid for sid, (thread, _stop) in running.items() if thread.is_alive()]

    def _shutdown(*_):
        log.info("stopping: clearing metadata (broker + ES)")
        _STOP.set()
        with running_lock:
            for _thread, worker_stop in running.values():
                worker_stop.set()
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    webhook_server = None
    if a.event_transport == "webhook":
        webhook_server = OverlayWebhookServer(
            a.webhook_host, a.webhook_port, _start_worker, _stop_worker).start()
        log.info("event-driven overlay plugin: webhook receivers on %s:%d",
                 a.webhook_host, webhook_server.port)
    else:
        from scripts.overlay.deepstream_sim import listen_camera_streaming

        def _event_listener():
            while not _STOP.is_set():
                try:
                    listen_camera_streaming(
                        "redis", a.events_topic, a.redis_host, a.redis_port, a.kafka,
                        lambda url, sid, codec: _start_worker(sid, url, codec),
                        seconds=0, stop_event=_STOP)
                except Exception as exc:  # noqa: BLE001
                    log.warning("camera_streaming listener error (retrying): %s", exc)
                    time.sleep(2)
        threading.Thread(target=_event_listener, daemon=True).start()
        log.info("event-driven overlay plugin: subscribed to Redis '%s'", a.events_topic)

    def reconcile():
        """Redis-mode backstop; webhook mode deliberately has no Redis dependency."""
        if a.event_transport != "redis":
            return
        if a.broker == "redis":
            _ensure_group(a.redis_host, a.redis_port, a.meta_topic)
        try:
            for sid, url, codec, w, h in _discover_sensors(a.base_url):
                _start_worker(sid, url, codec, w, h)
        except Exception as exc:  # noqa: BLE001
            log.warning("reconcile discovery failed (will retry): %s", exc)

    if a.broker == "redis":
        _ensure_group(a.redis_host, a.redis_port, a.meta_topic)
    while not _STOP.is_set():
        for _ in range(15):
            if _STOP.is_set():
                break
            time.sleep(1)
        if not _STOP.is_set():
            reconcile()
            alive = _alive_workers()
            log.info("continuous metadata: %d workers alive %s", len(alive), alive)
    if webhook_server is not None:
        webhook_server.stop()
    # clear on stop -- XTRIM to 0 empties the metadata but keeps the stream key and
    # VIOS's consumer group intact. DEL would destroy the group, and VIOS (shared
    # persistent connection) never recreates it -> XREADGROUP NOGROUP -> live overlay
    # silently stops until streamprocessing restarts. Never DEL this stream.
    if a.broker == "redis":
        try:
            import redis
            redis.Redis(host=a.redis_host, port=a.redis_port).xtrim(
                a.meta_topic, maxlen=0, approximate=False)
        except Exception:  # noqa: BLE001
            pass
    try:
        es.store.clear(); es.stop()
    except Exception:  # noqa: BLE001
        pass
    log.info("metadata service stopped + cleared")


if __name__ == "__main__":
    main()
