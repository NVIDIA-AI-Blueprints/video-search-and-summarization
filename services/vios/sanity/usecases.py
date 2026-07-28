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
Sanity use-cases for VIOS+NVStreamer. Each ``run(ctx)`` drives one use-case and
returns a UseCaseResult (status + a snapshot + http links). All heavy lifting is
reused from the bdd_tests overlay lib.
"""
from __future__ import annotations

import functools
import json
import logging
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from sanity_common import SanityContext, UseCaseResult
# verbs from the bdd_tests overlay lib (path wired in sanity_common)
from scripts.overlay.metadata_generator import epoch_ms_to_iso_z
# NOTE: no FakeESServer / OverlaySpec / *BoxPublisher imports here on purpose -- use-cases
# never seed their own metadata; the single continuous metadata_service is the only
# generator + fake-ES + broker publisher for the whole run.
from tests.overlay.overlay_test_utils import (
    download_overlay_clip, assert_overlay_box, assert_overlay_box_sustained,
    assert_live_box_border, extract_frame_rgb, count_border_colorful, overlay_configuration)

logger = logging.getLogger("sanity.usecases")
# Live/replay picture overlay param: showAll + debug + a bolder (thicker) box.
# thickness drives BOTH the box line width and the objId label size (font = thickness*2
# in overlay_internal), so bump it to 11 for a clearly-visible objId (frame number).
OVERLAY = json.dumps({"bbox": {"showAll": "true", "showObjId": "true", "objIdPosition": 0,
                               "objIdTextColor": "white", "objIdTextBGColor": "black"},
                      "debug": "true", "thickness": "11"})
V = "/vst/api/v1"


def _record_http_request(ctx, result, method, path, params=None, headers=None, body=None, **context):
    """Retain the exact prepared HTTP request before it is issued.

    This also stores the request on the context so run_usecase can preserve it if an assertion
    or decoder raises before the UseCaseResult is returned.
    """
    url = f"{ctx.base_url.rstrip('/')}{path}"
    prepared = requests.Request(method, url, params=params, json=body).prepare()
    details = {
        "type": "http",
        "method": method.upper(),
        "url": prepared.url,
        "path": path,
        "query_params": dict(params or {}),
        "headers": dict(headers or {}),
        "body": body,
        **context,
    }
    result.request = details
    ctx.active_request = dict(details)
    return details


def _res(ctx: SanityContext, sensor: str):
    """The source resolution of a sensor's stream (WxH), so overlay metadata pixel
    coords match the actual frame. Falls back to ctx.width/height."""
    return ctx.stream_res.get(sensor, (ctx.width, ctx.height))


def _center_box(w: int, h: int, frac: float = 0.20):
    """Centered box (leftX, topY, rightX, bottomY) covering `frac` of a WxH frame."""
    bw, bh = w * frac, h * frac
    cx, cy = w / 2.0, h / 2.0
    return (int(cx - bw / 2), int(cy - bh / 2), int(cx + bw / 2), int(cy + bh / 2))


def _img_dims(path: Path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
                         capture_output=True, text=True).stdout.strip()
    w, h = out.split("x")
    return int(w), int(h)


def _assert_panel_box(panel_png: Path):
    """Assert the red bbox is drawn in the VST-UI video panel screenshot (the box
    sits at the panel centre, scaled)."""
    w, h = _img_dims(panel_png)
    rgb = _decode_image_rgb(panel_png, w, h)
    el, et, er, eb = int(.40 * w), int(.40 * h), int(.60 * w), int(.60 * h)
    border, floor = count_border_colorful(rgb, w, h, (el, et, er, eb))
    logger.info("UI panel %dx%d box border=%d floor=%d", w, h, border, floor)
    assert border > floor * 5 + 80, f"no bbox in UI video panel (border={border} floor={floor})"


def _frame_from_mp4(mp4: Path, at: float, out_png: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(mp4),
                    "-frames:v", "1", str(out_png), "-y"], check=True)
    return out_png


def _count_red(rgb: bytes, w: int, h: int, step: int = 2) -> int:
    """Count red (bbox-colour) pixels in a frame -- used for the video wall where
    boxes appear at several tile centres, not one fixed spot."""
    n = 0
    for y in range(0, h, step):
        base = y * w * 3
        for x in range(0, w, step):
            o = base + x * 3
            if rgb[o] - rgb[o + 1] > 25 and rgb[o] - rgb[o + 2] > 25 and rgb[o] > 90:
                n += 1
    return n


def _ms(iso: str) -> int:
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000 + 0.5)


def _slug(label: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in label).strip("_")


_VARIANT_RES = {"1080p": (1920, 1080), "720p": (1280, 720),
                "480p": (640, 480), "4k": (3840, 2160)}


def _resolve_sensor(ctx: SanityContext, target: str, variant: str = None, sensor: str = None):
    """A direct `sensor` id wins (used by the internal evidence matrix). Else file ->
    the VIOS file sensor; rtsp -> the `variant`-resolution stream or the first one."""
    if sensor:
        return sensor
    if target == "file":
        return ctx.file_sensor
    if variant and variant in _VARIANT_RES:
        want = _VARIANT_RES[variant]
        for s in ctx.provisioned_streams:
            if ctx.stream_res.get(s) == want:
                return s
        logger.warning("no provisioned %s stream at %s; using first", variant, want)
    return ctx.provisioned_streams[0] if ctx.provisioned_streams else ctx.stream_id


def _res_tag(ctx: SanityContext, sensor: str) -> str:
    """Descriptive label for a sensor (codec_res_fps from its name), else WxH."""
    name = ctx.stream_names.get(sensor, "") if hasattr(ctx, "stream_names") else ""
    for codec in ("h264", "h265"):
        if codec in name:
            return name[name.index(codec):]      # e.g. h264_1080p_30fps
    w, h = _res(ctx, sensor)
    return f"{w}x{h}"


def _recent_window(ctx: SanityContext, length=10):
    """A window ending at wall-clock now-200ms (the live tail). rtsp/continuous only."""
    end = int(time.time() * 1000) - 200
    return end - length * 1000, end


def _first_url(obj) -> str:
    """Find the first http(s) URL value in a JSON response (the /url APIs vary the
    key: video_url / image_url / url / ...)."""
    if isinstance(obj, str):
        return obj if obj.startswith("http") else ""
    if isinstance(obj, dict):
        for v in obj.values():
            u = _first_url(v)
            if u:
                return u
    return ""


def _panel_has_video(panel_png: Path) -> bool:
    """True if the UI video panel shows moving/real content (luma variance), used
    for the no-overlay live/replay assertion."""
    import statistics
    w, h = _img_dims(panel_png)
    rgb = _decode_image_rgb(panel_png, w, h)
    lum = [rgb[i] for i in range(0, len(rgb), 331)]   # sparse sample of the R channel
    return len(lum) > 50 and statistics.pstdev(lum) > 10


def _window(ctx: SanityContext, sensor: str = None, lead=20, length=10, guard=3):
    """A historical sub-window inside the sensor's longest recorded segment. lead/
    length adapt down to whatever is recorded, so a short (e.g. 30s) clip still yields
    a valid window instead of 'recording too short'.

    lead defaults to 20s (not the segment start): the overlay metadata plugin attaches
    just before VIOS and its RTSP reader needs ~10-15s to connect through VIOS's proxy, so
    the first ~15s of every recording predates real-time (pts-aligned) metadata. Starting the
    window at t0+20 keeps it in the real-time-covered region, where the box is drawn across
    the whole clip -- instead of only appearing at the tail once real-time caught up. The
    >=60s recording gate guarantees room for this lead + length."""
    sensor = sensor or ctx.stream_id
    tl = requests.get(f"{ctx.base_url}{V}/storage/timelines", timeout=30, verify=ctx.verify_ssl).json()
    ranges = (tl or {}).get(sensor) or []
    if not ranges:
        raise RuntimeError(f"no recording timeline for {sensor}")
    r = max(ranges, key=lambda x: _ms(x["endTime"]) - _ms(x["startTime"]))
    t0, t1 = _ms(r["startTime"]), _ms(r["endTime"])
    usable = (t1 - t0) / 1000.0 - guard            # seconds available from t0 before the tail guard
    if usable <= 1.5:
        raise RuntimeError(f"recording too short for {sensor} ({(t1 - t0) / 1000.0:.1f}s)")
    length = min(length, max(1.0, usable - 0.5))    # shrink the clip to fit
    lead = min(lead, max(0.0, usable - length))     # pull the start in to fit
    start = t0 + int(lead * 1000)
    end = min(start + int(length * 1000), t1 - guard * 1000)
    if end <= start:
        raise RuntimeError(f"recording too short for {sensor}")
    return start, end


def _snap_jpg_from_bytes(ctx: SanityContext, content: bytes, name: str) -> Path:
    p = ctx.out_dir / name
    p.write_bytes(content)
    return p


def _decode_image_rgb(path: Path, w: int, h: int) -> bytes:
    """Decode a still image (JPG) to raw rgb24 -- no -ss seeking (that returns 0
    frames on a single-image input)."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-"],
        capture_output=True, check=True).stdout
    if len(out) < w * h * 3:
        raise RuntimeError(f"short image decode: {len(out)} < {w*h*3}")
    return out[:w * h * 3]


# NOTE: the overlay use-cases are the PARAMETRIC VERBS below (download/picture/webrtc_live/
# webrtc_replay with overlay=True). There is ONE metadata source for the whole run -- the
# continuous metadata_service (single generator + single FakeESServer on :19200 + broker
# publisher); every use-case CONSUMES it. No use-case starts its own ES/publisher.


def video_wall(ctx: SanityContext, overlay=True) -> UseCaseResult:
    """WebRTC video wall of the uniform provisioned copies WITH overlay: every tile's
    bbox comes from the ONE continuous metadata_service (which publishes for all copies to
    the broker) -- no per-tile publishers here."""
    from sanity_common import BDD_ROOT
    r = UseCaseResult(name="video_wall", group="webrtc")
    streams = ctx.provisioned_streams
    if not streams:
        return UseCaseResult(name="video_wall", status="SKIP",
                             detail="needs uniform NVStreamer copies -- run with --input-mp4")
    png = ctx.out_dir / "video_wall.png"
    mp4 = ctx.out_dir / "video_wall.mp4"
    # Boxes on every tile come from the plan's CONTINUOUS metadata_service (publishing for
    # all copies to the broker) -- no per-tile publishers here.
    try:
        script = BDD_ROOT / "scripts/overlay/ui_video_wall.py"
        proc = subprocess.run(["python3", str(script), "--base-url", ctx.base_url,
                               "--streams", ",".join(streams), "--out", str(png),
                               "--out-mp4", str(mp4)],
                              capture_output=True, text=True, timeout=180)
        line = (proc.stdout.strip().splitlines() or [""])[-1]
        playing = "playing=True" in line
        tm = re.search(r"tiles=(\d+)", line)
        tiles = tm.group(1) if tm else str(len(streams))
        red = 0
        if png.exists():
            w, h = _img_dims(png)
            red = _count_red(_decode_image_rgb(png, w, h), w, h)
        boxes_ok = red >= 800   # several tile boxes' worth of red pixels
        logger.info("video wall: %s red_px=%d", line, red)
        r.status = "PASS" if (playing and boxes_ok) else "FAIL"
        r.detail = (f"WebRTC video wall of {len(streams)} uniform copies, overlay on all tiles; "
                    f"playing={playing}, tiles={tiles}, box red_px={red}.")
        r.image = png if png.exists() else None
        r.links = []
        if mp4.exists():
            r.links.append(ctx.publish(mp4, "sanity_video_wall.mp4"))
        if png.exists():
            r.links.append(ctx.publish(png, "sanity_video_wall.png"))
    except subprocess.TimeoutExpired:
        r.status = "FAIL"; r.detail = "video wall UI capture timed out"
    return r


def nvstreamer_file_upload(ctx: SanityContext) -> UseCaseResult:
    """Verify NVStreamer is serving the uploaded clip copies (provisioning PUT
    them to NVStreamer, which then serves RTSP)."""
    r = UseCaseResult(name="nvstreamer_file_upload", group="setup")
    if not ctx.provisioned_streams:
        r.status = "SKIP"; r.detail = "no provisioned copies -- run with --input-mp4"; return r
    resp = requests.get(f"{ctx.nvstreamer_url}{V}/sensor/list", timeout=25)
    names = {(s.get("name") or s.get("sensorId")) for s in (resp.json() or [])}
    served = [s for s in ctx.provisioned_streams if s in names]
    r.status = "PASS" if len(served) >= max(1, len(ctx.provisioned_streams) // 2) else "FAIL"
    r.detail = f"NVStreamer serving copies as RTSP: {served}"
    return r


def rtsp_add_recording_check(ctx: SanityContext) -> UseCaseResult:
    """Verify an RTSP source added to VIOS is being recorded (timeline present)."""
    r = UseCaseResult(name="rtsp_add_recording_check", group="setup")
    check = ctx.provisioned_streams[0] if ctx.provisioned_streams else ctx.stream_id
    tl = requests.get(f"{ctx.base_url}{V}/storage/timelines", timeout=30, verify=ctx.verify_ssl).json()
    ranges = (tl or {}).get(check) or []
    r.status = "PASS" if ranges else "FAIL"
    r.detail = (f"RTSP stream '{check}' recorded ({len(ranges)} timeline range(s))"
                if ranges else f"no recording timeline for '{check}'")
    return r


def vios_file_upload(ctx: SanityContext) -> UseCaseResult:
    """Verify the clip uploaded to VIOS registered as a file-backed sensor."""
    r = UseCaseResult(name="vios_file_upload", group="setup")
    fs = ctx.file_sensor
    if not fs:
        r.status = "SKIP"; r.detail = "no file sensor -- run with --input-mp4"; return r
    resp = requests.get(f"{ctx.base_url}{V}/sensor/list", timeout=30, verify=ctx.verify_ssl)
    ids = {(s.get("sensorId") or s.get("name")) for s in (resp.json() or [])}
    r.status = "PASS" if fs in ids else "FAIL"
    r.detail = f"VIOS file sensor {'present' if fs in ids else 'MISSING'}: {fs}"
    return r


def milestone_adaptor_test(ctx: SanityContext) -> UseCaseResult:
    return UseCaseResult(name="milestone_adaptor_test", status="SKIP",
                         detail="not yet implemented (Milestone VMS adaptor: add sensor, verify stream/recording)")


def onvif_adaptor_test(ctx: SanityContext) -> UseCaseResult:
    return UseCaseResult(name="onvif_adaptor_test", status="SKIP",
                         detail="not yet implemented (ONVIF adaptor: discover/add sensor, verify stream)")


# --------------------------------------------------------------------------- #
# Parametric verbs (driven by the structured `usecases:` items in the plans).  #
# --------------------------------------------------------------------------- #

def download(ctx: SanityContext, target="rtsp", recent=False, overlay=False,
             variant=None, sensor=None) -> UseCaseResult:
    """Download a recorded clip. target=file|rtsp; recent=live tail (rtsp only);
    overlay=burn bboxes from (fake) ES and assert them against a control clip.
    variant=1080p|480p|4k|... picks that-resolution stream; sensor= a direct id."""
    label = (f"download[{target}{',' + variant if variant else ''}"
             f"{',recent' if recent else ''}{',overlay' if overlay else ''}]")
    r = UseCaseResult(name=label, group="download")
    sensor = _resolve_sensor(ctx, target, variant, sensor)
    if not sensor:
        r.status = "SKIP"; r.detail = f"no {target} sensor (run with --input-mp4)"; return r
    if recent and target == "file":
        r.status = "SKIP"; r.detail = "recent is rtsp-only (continuous live tail)"; return r
    start, end = _recent_window(ctx, 10) if recent else _window(ctx, sensor, length=10)
    slug = _slug(f"{label}_{sensor}")   # sensor in the slug -> unique evidence file per
                                        # stream/plan (else per-resolution + per-plan images
                                        # collide on one filename and the last write wins)
    # File sensors must NOT carry the `streamid` header -- it triggers SDR routing they
    # aren't registered for ("upstream-cluster required" 503). RTSP keeps it.
    rk = "" if target == "file" else sensor
    src_w, src_h = _res(ctx, sensor)     # metadata coords must match the source frame
    # Record the exact request (for the failures manifest / PDF) BEFORE issuing it.
    r.request = {"api": f"GET {V}/storage/file/{sensor}", "sensor": sensor, "target": target,
                 "startTime": epoch_ms_to_iso_z(start), "endTime": epoch_ms_to_iso_z(end),
                 "streamid": rk or None, "overlay": bool(overlay), "container": "mp4",
                 "transcode": "full" if overlay else "(remux)"}
    ov = ctx.out_dir / f"{slug}.mp4"
    if overlay:
        # Metadata comes from the plan's CONTINUOUS metadata_service (its fake-ES already
        # holds per-frame bboxes with incrementing objectId). Just download the window with
        # overlay + a no-overlay control, and assert the box.
        ctl = ctx.out_dir / f"{slug}_control.mp4"
        download_overlay_clip(ctx.base_url, sensor, rk, start, end, ov, True, ctx.verify_ssl)
        download_overlay_clip(ctx.base_url, sensor, rk, start, end, ctl, False, ctx.verify_ssl)
        ow, oh = _img_dims(ov)       # assert in the OUTPUT resolution (centered box)
        # debug=true draws frame-wide info, so only require the box region to differ.
        assert_overlay_box(ov, ctl, [1.0, 5.0, 9.0], ow, oh, _center_box(ow, oh),
                           require_localized=False)
        r.detail = f"{target} clip with overlay ({src_w}x{src_h}); box from continuous metadata."
    else:
        res = download_overlay_clip(ctx.base_url, sensor, rk, start, end, ov, False, ctx.verify_ssl)
        if not res["success"]:
            r.status = "FAIL"; r.detail = f"download failed: {res}"; return r
        r.detail = f"{target} clip downloaded ({res['bytes']} bytes) and decodes."
    frame = ctx.out_dir / f"{slug}.png"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "3", "-i", str(ov), "-frames:v", "1",
                    str(frame), "-y"], check=False)
    r.status = "PASS"
    r.image = frame if frame.exists() else None
    r.links = [ctx.publish(ov, f"sanity_{slug}.mp4")]
    if frame.exists():
        r.links.append(ctx.publish(frame, f"sanity_{slug}.png"))
    return r


def download_overlay_long(ctx: SanityContext, sensor=None, variant=None, dur=60) -> UseCaseResult:
    """LONG (default 60s) overlay download that asserts the bbox is SUSTAINED across the WHOLE
    clip, not just the first frames. Catches metadata dropout over time -- the box proper for
    the first ~10-15s then flickering / absent for the rest, seen on stock 3.2.1 when the server
    stops delivering ES metadata past the first batch (video_metadata_query_batch_size_num_frames).
    Samples every ~5s and fails listing the timestamps where the box is missing. Plan-1 only."""
    label = f"download_overlay_long[{dur}s]"
    r = UseCaseResult(name=label, group="download")
    sensor = _resolve_sensor(ctx, "rtsp", variant, sensor)
    if not sensor:
        r.status = "SKIP"; r.detail = "no rtsp sensor (run with --input-mp4)"; return r
    # Window past the RTSP-warmup zone (lead=20), dur long. By the time the overlay matrix runs
    # (minutes after deploy) the recording is long enough; _window adapts down if it isn't.
    start, end = _window(ctx, sensor, lead=20, length=dur, guard=3)
    got = (end - start) / 1000.0
    slug = _slug(f"{label}_{sensor}")
    src_w, src_h = _res(ctx, sensor)
    r.request = {"api": f"GET {V}/storage/file/{sensor}", "sensor": sensor, "target": "rtsp",
                 "startTime": epoch_ms_to_iso_z(start), "endTime": epoch_ms_to_iso_z(end),
                 "streamid": sensor, "overlay": True, "container": "mp4", "transcode": "full",
                 "duration_s": round(got, 1)}
    ov = ctx.out_dir / f"{slug}.mp4"
    ctl = ctx.out_dir / f"{slug}_control.mp4"
    download_overlay_clip(ctx.base_url, sensor, sensor, start, end, ov, True, ctx.verify_ssl)
    download_overlay_clip(ctx.base_url, sensor, sensor, start, end, ctl, False, ctx.verify_ssl)
    ow, oh = _img_dims(ov)
    # sample every ~5s across the whole clip (inside a 1s edge guard)
    step = 5.0
    n = max(2, int((got - 2.0) // step) + 1)
    samples = [round(1.0 + i * step, 1) for i in range(n) if 1.0 + i * step <= got - 1.0]
    summary = assert_overlay_box_sustained(ov, ctl, samples, ow, oh, _center_box(ow, oh),
                                           min_present_frac=0.9)
    r.detail = (f"rtsp {got:.0f}s clip overlay ({src_w}x{src_h}); box sustained in "
                f"{summary['present_frac']:.0%} of {len(samples)} samples across the clip"
                + (f"; absent at {summary['absent_seconds']}s" if summary['absent_seconds'] else "."))
    frame = ctx.out_dir / f"{slug}.png"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "3", "-i", str(ov), "-frames:v", "1",
                    str(frame), "-y"], check=False)
    r.status = "PASS"
    r.image = frame if frame.exists() else None
    r.links = [ctx.publish(ov, f"sanity_{slug}.mp4")]
    if frame.exists():
        r.links.append(ctx.publish(frame, f"sanity_{slug}.png"))
    return r


def picture(ctx: SanityContext, target="rtsp", recent=False, overlay=False,
            variant=None, sensor=None) -> UseCaseResult:
    """Snapshot JPEG. recent=live picture (broker overlay); else storage picture at
    a recorded time (ES overlay). target=file|rtsp; variant picks a resolution."""
    label = (f"picture[{target}{',' + variant if variant else ''}"
             f"{',recent' if recent else ''}{',overlay' if overlay else ''}]")
    r = UseCaseResult(name=label, group="picture")
    sensor = _resolve_sensor(ctx, target, variant, sensor)
    if not sensor:
        r.status = "SKIP"; r.detail = f"no {target} sensor (run with --input-mp4)"; return r
    if recent and target == "file":
        r.status = "SKIP"; r.detail = "recent is rtsp-only (continuous live tail)"; return r
    slug = _slug(f"{label}_{sensor}")   # sensor in the slug -> unique evidence file per
                                        # stream/plan (else per-resolution + per-plan images
                                        # collide on one filename and the last write wins)
    src_w, src_h = _res(ctx, sensor)     # metadata coords must match the source frame
    pub = t = es = None
    try:
        # Overlay metadata (live broker + replay ES) is provided by the plan's CONTINUOUS
        # metadata_service; this use-case just requests the picture with overlay + asserts.
        if recent:                       # live picture -> broker metadata (continuous)
            params = {"overlay": OVERLAY} if overlay else {}
            path = f"{V}/live/stream/{sensor}/picture"
            headers = {"streamid": sensor}
            _record_http_request(ctx, r, "GET", path, params=params, headers=headers,
                                 sensor=sensor, target=target, overlay=bool(overlay))
            resp = requests.get(f"{ctx.base_url}{path}", params=params, headers=headers,
                                timeout=25, verify=ctx.verify_ssl)
        else:                            # storage picture at a recorded time -> ES metadata (continuous)
            # A single-frame overlay picture needs a metadata doc matching the EXACT instant.
            # Real-time metadata (recent tail, keyed by the frame's own SEI ts) matches; an
            # early/backfilled instant returns "UNMATCH" and draws no box (the download, which
            # matches over a 10s window, is unaffected). So overlay -> a recent instant.
            if overlay and target != "file":
                start = _recent_window(ctx, 4)[0]
            else:
                start, _ = _window(ctx, sensor, lead=20, length=1, guard=3)
            params = {"startTime": epoch_ms_to_iso_z(start)}
            if overlay:
                params["overlay"] = OVERLAY
            # File sensors: snapshot via /replay/stream and NO streamid header (SDR
            # routing they aren't registered for). RTSP: /storage/stream + streamid.
            _seg = "replay" if target == "file" else "storage"
            _hdr = None if target == "file" else {"streamid": sensor}
            path = f"{V}/{_seg}/stream/{sensor}/picture"
            _record_http_request(ctx, r, "GET", path, params=params, headers=_hdr,
                                 sensor=sensor, target=target, overlay=bool(overlay))
            # NOTE: no retry here on purpose. If a storage snapshot returns 500
            # ("no valid stream found for given timestamps"), that is a REAL failure to
            # report -- e.g. VIOS not serving an h265 snapshot of an early/young-recording
            # region -- not something to paper over.
            resp = requests.get(f"{ctx.base_url}{path}", params=params, headers=_hdr,
                                timeout=30, verify=ctx.verify_ssl)
        if resp.status_code != 200 or len(resp.content) < 1000:
            r.status = "FAIL"; r.detail = f"picture HTTP {resp.status_code}, {len(resp.content)} bytes"; return r
        jpg = _snap_jpg_from_bytes(ctx, resp.content, f"{slug}.jpg")
        ow, oh = _img_dims(jpg)          # assert in the OUTPUT resolution (centered box)
        rgb = _decode_image_rgb(jpg, ow, oh)
        if overlay:
            assert_live_box_border(rgb, ow, oh, _center_box(ow, oh))
            r.detail = f"{target} {'live' if recent else 'storage'} picture drew the box ({src_w}x{src_h})."
        else:
            r.detail = f"{target} {'live' if recent else 'storage'} picture returned and decodes."
        r.status = "PASS"
        r.image = jpg
        r.links = [ctx.publish(jpg, f"sanity_{slug}.jpg")]
    finally:
        if pub is not None:
            pub.stop(); t.join(timeout=5)
        if es is not None:
            es.stop()
    return r


def download_url(ctx: SanityContext, target="rtsp") -> UseCaseResult:
    """Non-blocking download: request a video URL, fetch it, and publish a durable copy."""
    label = f"download_url[{target}]"
    r = UseCaseResult(name=label, group="download")
    sensor = _resolve_sensor(ctx, target)
    if not sensor:
        r.status = "SKIP"; r.detail = f"no {target} sensor (run with --input-mp4)"; return r
    start, end = _window(ctx, sensor, length=10)
    # File sensors must NOT carry the streamid header -- it triggers SDR routing they aren't
    # registered for ("upstream-cluster required" 503). RTSP keeps it.
    _hdr = None if target == "file" else {"streamid": sensor}
    r.request = {"api": f"GET {V}/storage/file/{sensor}/url", "sensor": sensor, "target": target,
                 "startTime": epoch_ms_to_iso_z(start), "endTime": epoch_ms_to_iso_z(end),
                 "streamid": (sensor if _hdr else None), "container": "mp4"}
    u = requests.get(f"{ctx.base_url}{V}/storage/file/{sensor}/url",
                     params={"startTime": epoch_ms_to_iso_z(start), "endTime": epoch_ms_to_iso_z(end),
                             "container": "mp4"}, headers=_hdr, timeout=30, verify=ctx.verify_ssl)
    if u.status_code != 200:
        r.status = "FAIL"; r.detail = f"URL request HTTP {u.status_code}"; return r
    video_url = _first_url(u.json())
    if not video_url:
        r.status = "FAIL"; r.detail = f"no video_url in response: {u.text[:120]}"; return r
    r.request["returned_url"] = video_url
    g = requests.get(video_url, timeout=120, verify=ctx.verify_ssl, stream=True)
    out = ctx.out_dir / f"{_slug(label)}.mp4"
    size = 0
    if g.status_code == 200:
        with out.open("wb") as stream:
            for chunk in g.iter_content(65536):
                if chunk:
                    stream.write(chunk)
                    size += len(chunk)
    r.status = "PASS" if size > 0 else "FAIL"
    r.detail = f"{target} non-blocking URL issued and fetched ({size} bytes)."
    if size > 0:
        r.links = [ctx.publish(out, f"sanity_{_slug(label)}.mp4")]
    return r


def picture_url(ctx: SanityContext, target="rtsp") -> UseCaseResult:
    """Non-blocking picture: request a picture URL, then fetch the JPEG."""
    label = f"picture_url[{target}]"
    r = UseCaseResult(name=label, group="picture")
    sensor = _resolve_sensor(ctx, target)
    if not sensor:
        r.status = "SKIP"; r.detail = f"no {target} sensor (run with --input-mp4)"; return r
    start, _ = _window(ctx, sensor, lead=10, length=1)
    # File sensors: /replay/stream + NO streamid header (SDR route -> 503). RTSP: /storage + streamid.
    _seg = "replay" if target == "file" else "storage"
    _hdr = None if target == "file" else {"streamid": sensor}
    r.request = {"api": f"GET {V}/{_seg}/stream/{sensor}/picture/url", "sensor": sensor,
                 "target": target, "startTime": epoch_ms_to_iso_z(start),
                 "streamid": (sensor if _hdr else None)}
    u = requests.get(f"{ctx.base_url}{V}/{_seg}/stream/{sensor}/picture/url",
                     params={"startTime": epoch_ms_to_iso_z(start)}, headers=_hdr,
                     timeout=30, verify=ctx.verify_ssl)
    if u.status_code != 200:
        r.status = "FAIL"; r.detail = f"URL request HTTP {u.status_code}"; return r
    img_url = _first_url(u.json())
    if not img_url:
        r.status = "FAIL"; r.detail = f"no picture url in response: {u.text[:120]}"; return r
    r.request["returned_url"] = img_url
    g = requests.get(img_url, timeout=30, verify=ctx.verify_ssl)
    ok = g.status_code == 200 and len(g.content) > 1000
    if ok:
        jpg = _snap_jpg_from_bytes(ctx, g.content, f"{_slug(label)}.jpg")
        r.image = jpg
        r.links = [ctx.publish(jpg, f"sanity_{_slug(label)}.jpg")]
    r.status = "PASS" if ok else "FAIL"
    r.detail = f"{target} picture URL issued and fetched ({len(g.content)} bytes)."
    return r


def webrtc_live(ctx: SanityContext, source="vios", overlay=True, sensor=None) -> UseCaseResult:
    """VST-UI live WebRTC. source=nvstreamer uses a provisioned RTSP copy. overlay
    asserts the bbox in the panel; otherwise just asserts the panel is playing."""
    from sanity_common import BDD_ROOT
    label = f"webrtc_live[{source}{',overlay' if overlay else ''}]"
    r = UseCaseResult(name=label, group="webrtc")
    # Live-view a real provisioned sensor (ctx.stream_id is the video stem, not a sensor).
    sensor = sensor or (ctx.provisioned_streams[0] if ctx.provisioned_streams else ctx.stream_id)
    slug = _slug(f"{label}_{sensor}")   # sensor in the slug -> unique evidence file per
                                        # stream/plan (else per-resolution + per-plan images
                                        # collide on one filename and the last write wins)
    out = ctx.out_dir / f"{slug}.mp4"
    panel = ctx.out_dir / f"{slug}_panel.png"
    cap = BDD_ROOT / "scripts/overlay/ui_capture.py"
    if source == "nvstreamer":
        # Play on the NVStreamer's own UI (:31000 Media Streams), not the VST UI.
        cmd = ["python3", str(cap), "--ui", "nvstreamer", "--base-url", ctx.nvstreamer_url,
               "--stream-id", sensor, "--seconds", "10", "--out", str(out), "--panel-out", str(panel)]
    else:
        rw, rh = _res(ctx, sensor)      # (dims only matter if self-publishing)
        # live overlay metadata comes from the plan's CONTINUOUS metadata_service -> --no-publish
        cmd = ["python3", str(cap), "--mode", "live", "--base-url", ctx.base_url,
               "--stream-id", sensor, "--seconds", "10", "--broker", ctx.broker,
               "--width", str(rw), "--height", str(rh), "--no-publish",
               "--out", str(out), "--panel-out", str(panel)]
        if not overlay:
            cmd.append("--no-overlay")
    ui_url = (ctx.nvstreamer_url.rstrip("/") + "/" if source == "nvstreamer"
              else ctx.base_url.rstrip("/") + "/vst/")
    r.request = {
        "type": "browser_ui",
        "method": "PLAYWRIGHT",
        "url": ui_url,
        "params": {"sensor": sensor, "source": source, "overlay": bool(overlay),
                   "mode": "live", "seconds": 10, "broker": ctx.broker},
        "command": cmd,
    }
    ctx.active_request = dict(r.request)
    subprocess.run(cmd, check=True, timeout=180)
    # Attach the captured UI frame + video as evidence FIRST -- so it shows in the PDF even
    # if the box/panel check then fails (the headless panel-screenshot detection is flaky; a
    # visible frame lets a reviewer confirm the overlay themselves).
    frame = _frame_from_mp4(out, 6.0, ctx.out_dir / f"{slug}.png")
    r.image = frame if frame and frame.exists() else None
    r.links = [ctx.publish(out, f"sanity_{slug}.mp4")]
    if r.image:
        r.links.append(ctx.publish(frame, f"sanity_{slug}.png"))
    try:
        if source != "nvstreamer" and overlay:
            _assert_panel_box(panel)
            r.status = "PASS"; r.detail = f"VST-UI live WebRTC ({source}) with bbox+debug; box detected in the panel."
        else:
            assert _panel_has_video(panel), "no live video in the UI panel"
            ui = "NVStreamer-UI" if source == "nvstreamer" else "VST-UI"
            r.status = "PASS"; r.detail = f"{ui} live WebRTC ({source}); panel is playing video."
    except AssertionError as e:
        r.status = "FAIL"
        r.detail = f"VST-UI live WebRTC ({source}) captured (see evidence), but panel check failed: {e}"
    return r


def webrtc_replay(ctx: SanityContext, target="rtsp", overlay=True, seek=0,
                  variant=None, sensor=None) -> UseCaseResult:
    """VST-UI replay WebRTC with optional overlay and real backend seek verification."""
    from sanity_common import BDD_ROOT
    label = (f"webrtc_replay[{target}{',' + variant if variant else ''}"
             f"{',overlay' if overlay else ''}{',seek' if seek else ''}]")
    r = UseCaseResult(name=label, group="webrtc")
    sensor = _resolve_sensor(ctx, target, variant, sensor)
    if not sensor:
        r.status = "SKIP"; r.detail = f"no {target} sensor (run with --input-mp4)"; return r
    file_timeline_count = None
    file_timeline_request = None
    if target == "file":
        # Validate the exact keyed request used by clients. File sensors publish
        # camera_proxy events and SDR must map the file stream ID to its owning
        # stream-processing workload before timeline and replay controls run.
        timeline_path = f"{V}/storage/size"
        timeline_params = {"timelines": "true"}
        timeline_headers = {"streamId": sensor}
        timeline_response = requests.get(
            f"{ctx.base_url}{timeline_path}",
            params=timeline_params,
            headers=timeline_headers,
            timeout=30,
            verify=ctx.verify_ssl,
        )
        file_timeline_request = {
            "type": "http",
            "method": "GET",
            "url": timeline_response.request.url,
            "path": timeline_path,
            "query_params": timeline_params,
            "headers": timeline_headers,
            "status": timeline_response.status_code,
        }
        if timeline_response.status_code != 200:
            r.status = "FAIL"
            r.detail = (
                f"file-sensor timeline query failed for {sensor}: "
                f"HTTP {timeline_response.status_code}: {timeline_response.text[:200]}"
            )
            r.request = file_timeline_request
            return r
        storage = timeline_response.json() or {}
        file_entry = storage.get(sensor) or {}
        file_timelines = file_entry.get("timelines") or []
        file_timeline_count = len(file_timelines)
        if not file_timelines:
            r.status = "FAIL"
            r.detail = f"no file-sensor timeline in storage/size response for {sensor}"
            r.request = file_timeline_request
            return r
    slug = _slug(f"{label}_{sensor}")   # sensor in the slug -> unique evidence file per
                                        # stream/plan (else per-resolution + per-plan images
                                        # collide on one filename and the last write wins)
    if overlay:      # replay reads ES metadata -- provided by the continuous metadata_service
        tl = requests.get(f"{ctx.base_url}{V}/storage/timelines", timeout=30, verify=ctx.verify_ssl).json()
        if not sorted((tl or {}).get(sensor) or [], key=lambda x: _ms(x["startTime"])):
            r.status = "SKIP"; r.detail = f"no recording timeline for {sensor}"; return r
    out = ctx.out_dir / f"{slug}.mp4"
    panel = ctx.out_dir / f"{slug}_panel.png"
    cap = BDD_ROOT / "scripts/overlay/ui_capture.py"
    cmd = ["python3", str(cap), "--mode", "replay", "--base-url", ctx.base_url,
           "--stream-id", sensor, "--seconds", "12", "--seek", str(seek),
           "--out", str(out), "--panel-out", str(panel)]
    if not overlay:
        cmd.append("--no-overlay")
    r.request = {
        "type": "browser_ui",
        "method": "PLAYWRIGHT",
        "url": ctx.base_url.rstrip("/") + "/vst/",
        "params": {"sensor": sensor, "target": target, "overlay": bool(overlay),
                   "mode": "replay", "seconds": 12, "seek_count": seek},
        "command": cmd,
    }
    if file_timeline_request:
        r.request["preconditions"] = [file_timeline_request]
    ctx.active_request = dict(r.request)
    subprocess.run(cmd, check=True, timeout=200)
    # Attach evidence FIRST so the captured UI frame/video shows in the PDF even if the
    # panel-screenshot check fails (same rationale as webrtc_live).
    frame = _frame_from_mp4(out, 6.0, ctx.out_dir / f"{slug}.png")
    r.image = frame if frame and frame.exists() else None
    r.links = [ctx.publish(out, f"sanity_{slug}.mp4")]
    if r.image:
        r.links.append(ctx.publish(frame, f"sanity_{slug}.png"))
    try:
        if overlay:
            _assert_panel_box(panel)
            r.status = "PASS"; r.detail = f"VST-UI replay ({target}) overlay from continuous ES; box detected; {seek} seek(s) ok."
        else:
            assert _panel_has_video(panel), "no replay video in the UI panel"
            timeline_detail = (
                f"{file_timeline_count} timeline range(s); " if file_timeline_count is not None else ""
            )
            r.status = "PASS"
            r.detail = (
                f"VST-UI replay ({target}) no overlay; {timeline_detail}"
                f"panel playing; {seek} backend seek API call(s) succeeded."
            )
    except AssertionError as e:
        r.status = "FAIL"
        r.detail = f"VST-UI replay ({target}) captured (see evidence), but panel check failed: {e}"
    return r


def _time_call(fn, repeat: int):
    """Run fn() `repeat` times, return (avg_ms, min_ms, max_ms, ok_count)."""
    samples, ok = [], 0
    for _ in range(repeat):
        t0 = time.time()
        try:
            if fn():
                ok += 1
                samples.append((time.time() - t0) * 1000.0)
        except Exception as e:  # noqa: BLE001
            logger.warning("perf sample failed: %s", e)
    if not samples:
        return None
    return (sum(samples) / len(samples), min(samples), max(samples), ok)


def perf(ctx: SanityContext, group="download", repeat=10, dur=6) -> UseCaseResult:
    """Latency stats (avg of `repeat`) for the download or picture variants.

    Non-overlay downloads take the fast remux path; overlay downloads force a full
    transcode (bboxes re-drawn) so the two are meant to differ. `dur` is the clip
    length in seconds for the download variants."""
    label = f"perf[{group}]"
    r = UseCaseResult(name=label, group="perf")
    sensor = _resolve_sensor(ctx, "rtsp")
    if not sensor:
        r.status = "SKIP"; r.detail = "no rtsp sensor (run with --input-mp4)"; return r

    def _dl(recent, overlay):
        start, end = _recent_window(ctx, dur) if recent else _window(ctx, sensor, length=dur)
        params = {"startTime": epoch_ms_to_iso_z(start), "endTime": epoch_ms_to_iso_z(end),
                  "container": "mp4", "disableAudio": "true"}
        if overlay:                      # overlay -> full transcode + bbox draw (slow path)
            params["transcode"] = "full"
            params["configuration"] = overlay_configuration()
        # else: no transcode -> server remuxes the stored stream (fast path)
        resp = requests.get(f"{ctx.base_url}{V}/storage/file/{sensor}", params=params,
                            headers={"streamid": sensor}, timeout=120, verify=ctx.verify_ssl, stream=True)
        n = sum(len(c) for c in resp.iter_content(65536)) if resp.status_code == 200 else 0
        return n > 0

    def _pic(recent, overlay):
        if recent:                       # live picture (broker overlay)
            params = {"overlay": OVERLAY} if overlay else {}
            resp = requests.get(f"{ctx.base_url}{V}/live/stream/{sensor}/picture",
                                params=params, headers={"streamid": sensor}, timeout=25, verify=ctx.verify_ssl)
        else:                            # storage picture (ES overlay)
            start, _ = _window(ctx, sensor, lead=10, length=1)
            params = {"startTime": epoch_ms_to_iso_z(start)}
            if overlay:
                params["overlay"] = OVERLAY
            resp = requests.get(f"{ctx.base_url}{V}/storage/stream/{sensor}/picture",
                                params=params, headers={"streamid": sensor}, timeout=30, verify=ctx.verify_ssl)
        return resp.status_code == 200 and len(resp.content) > 1000

    # Overlay metadata (recorded ES + live broker) is provided by the plan's CONTINUOUS
    # metadata_service -- it owns the fake-ES on :19200 and publishes to the broker for the
    # whole plan. The perf variants just TIME the requests against it; they must NOT seed
    # their own ES/publisher (that collides on the ES port -> 'Address already in use').
    if group == "download":
        variants = [("download", _dl, False, False), ("download_recent", _dl, True, False),
                    ("download_overlay", _dl, False, True), ("download_recent_overlay", _dl, True, True)]
    else:
        variants = [("picture", _pic, False, False), ("picture_recent", _pic, True, False),
                    ("picture_overlay", _pic, False, True), ("picture_recent_overlay", _pic, True, True)]

    all_ok = True
    for name, fn, recent, overlay in variants:
        stat = _time_call(lambda rc=recent, ov=overlay: fn(rc, ov), repeat)
        if stat is None:
            r.metrics[name] = {"avg_ms": None, "ok": 0, "n": repeat}
            all_ok = False
        else:
            avg, lo, hi, ok = stat
            r.metrics[name] = {"avg_ms": round(avg, 1), "min_ms": round(lo, 1),
                               "max_ms": round(hi, 1), "ok": ok, "n": repeat}
            if ok < repeat:
                all_ok = False

    clip = f"{dur}s clips" if group == "download" else "single frame"
    r.status = "PASS" if all_ok else "FAIL"
    r.detail = (f"latency avg of {repeat} per variant ({group}, {clip}); "
                f"overlay variants force full transcode + bbox draw.")
    return r


def evidence_usecases(ctx: SanityContext, nvstreamer: bool = True, wall: bool = False):
    """Curated overlay-evidence matrix, derived from the ACTUAL provisioned streams
    (no resolutions named in the plan). Works for a generated variant set OR a user
    video-set, and degrades gracefully -- if only one resolution is present it emits
    what's available (never errors). Returns [(label, callable, {'evidence': True})]:
      download/picture overlay on up to 3 distinct-resolution streams + recent;
      webrtc live overlay + replay overlay on up to 2 streams (each seek + overlay);
      + nvstreamer live (Plan-1) OR video-wall with overlay (Plan-2)."""
    import functools
    out = []
    streams = list(ctx.provisioned_streams)
    if not streams:
        return out

    def ev(label, fn):
        return (label, fn, {"evidence": True})

    # distinct-resolution streams (up to 3); robust to fewer -- whatever is available.
    sel, seen = [], set()
    for s in streams:
        r = _res(ctx, s)
        if r not in seen:
            seen.add(r); sel.append(s)
        if len(sel) >= 3:
            break

    for s in sel:
        out.append(ev(f"download_overlay[{_res_tag(ctx, s)}]",
                      functools.partial(download, overlay=True, sensor=s)))
    out.append(ev("download_recent_overlay",
                  functools.partial(download, recent=True, overlay=True, sensor=sel[0])))
    for s in sel:
        out.append(ev(f"picture_overlay[{_res_tag(ctx, s)}]",
                      functools.partial(picture, overlay=True, sensor=s)))
    out.append(ev("picture_recent_overlay",
                  functools.partial(picture, recent=True, overlay=True, sensor=sel[0])))
    for s in sel[:2]:
        out.append(ev(f"webrtc_live_overlay[{_res_tag(ctx, s)}]",
                      functools.partial(webrtc_live, overlay=True, sensor=s)))
    for s in sel[:2]:                    # each replay is a single case: seek + overlay
        out.append(ev(f"webrtc_replay_overlay[{_res_tag(ctx, s)}]",
                      functools.partial(webrtc_replay, overlay=True, seek=3, sensor=s)))
    if wall:                             # Plan-2: synchronized video-wall with overlay
        out.append(ev("video_wall_overlay", functools.partial(video_wall, overlay=True)))
    if nvstreamer:                       # Plan-1 only
        # long (60s) overlay download -- asserts the box is SUSTAINED across the whole clip
        # (catches metadata dropout over time, not just first-frames presence).
        out.append(ev("download_overlay_long",
                      functools.partial(download_overlay_long, sensor=sel[0], dur=60)))
        out.append(ev("webrtc_live_nvstreamer",       # NVStreamer-UI live
                      functools.partial(webrtc_live, source="nvstreamer", overlay=False, sensor=sel[0])))
    return out


def default_suite(ctx: SanityContext, sync_wall: bool = False, nvstreamer: bool = True):
    """The canonical sanity suite, built internally (no use-cases listed in the plan):
    setup checks -> functional download/picture/URL -> latency perf -> the multi-
    resolution overlay-evidence matrix (+ nvstreamer live for Plan-1, or the
    synchronized video-wall for Plan-2). `nvstreamer=False` (the ONVIF adaptor plan, which
    has no NVStreamer or file sensor) drops the NVStreamer/file-sensor checks + the
    NVStreamer-UI live case, keeping the RTSP + full overlay coverage. Returns
    [(label, callable, meta)]."""
    import functools
    items = []

    def add(label, fn):
        items.append((label, fn, {}))

    # setup / provisioning checks (NVStreamer + file sensor only exist for NVStreamer plans)
    if nvstreamer:
        add("nvstreamer_file_upload", nvstreamer_file_upload)
    add("rtsp_add_recording_check", rtsp_add_recording_check)
    if nvstreamer:
        add("vios_file_upload", vios_file_upload)
        add("download[file]", functools.partial(download, target="file"))
        add("picture[file]", functools.partial(picture, target="file"))
        add("webrtc_replay[file,seek]",
            functools.partial(webrtc_replay, target="file", overlay=False, seek=3))
    # per-RTSP-stream functional coverage -- every stream has different codec/res/fps, so
    # download + picture + webrtc-just-play are exercised on EACH provisioned stream.
    for s in ctx.provisioned_streams:
        tag = _res_tag(ctx, s)
        add(f"download[{tag}]", functools.partial(download, target="rtsp", sensor=s))
        add(f"picture[{tag}]", functools.partial(picture, target="rtsp", sensor=s))
        add(f"webrtc_play[{tag}]", functools.partial(webrtc_live, overlay=False, sensor=s))
    # live-tail on the primary stream
    add("download[rtsp,recent]", functools.partial(download, target="rtsp", recent=True))
    add("picture[rtsp,recent]", functools.partial(picture, target="rtsp", recent=True))
    # non-blocking URL flows
    add("download_url[rtsp]", functools.partial(download_url, target="rtsp"))
    add("picture_url[rtsp]", functools.partial(picture_url, target="rtsp"))
    if nvstreamer:
        add("download_url[file]", functools.partial(download_url, target="file"))
        add("picture_url[file]", functools.partial(picture_url, target="file"))
    # latency perf
    add("perf[download]", functools.partial(perf, group="download"))
    add("perf[picture]", functools.partial(perf, group="picture"))
    # overlay-evidence matrix (multi-resolution) + nvstreamer live / video-wall
    items += evidence_usecases(ctx, nvstreamer=nvstreamer and not sync_wall, wall=sync_wall)
    return items


def milestone_suite(ctx: SanityContext):
    """Plan-4 Milestone suite: NO overlay (Milestone cameras carry no VST metadata). For each
    ONLINE discovered camera, exercise the read paths -- download (recorded clip), live +
    replay picture, and webrtc live + replay -- all overlay=False. Returns [(label, fn, meta)]."""
    import functools
    out = []
    if not ctx.provisioned_streams:
        out.append(("milestone_discover", lambda c: UseCaseResult(
            name="milestone_discover", status="FAIL", detail="no online Milestone cameras discovered"), {}))
        return out
    for s in ctx.provisioned_streams:
        out.append((f"download[{s}]", functools.partial(download, target="rtsp", overlay=False, sensor=s), {"evidence": True}))
        out.append((f"picture_live[{s}]", functools.partial(picture, target="rtsp", recent=True, overlay=False, sensor=s), {"evidence": True}))
        out.append((f"picture_replay[{s}]", functools.partial(picture, target="rtsp", overlay=False, sensor=s), {"evidence": True}))
        out.append((f"webrtc_live[{s}]", functools.partial(webrtc_live, overlay=False, sensor=s), {"evidence": True}))
        out.append((f"webrtc_replay[{s}]", functools.partial(webrtc_replay, target="rtsp", overlay=False, seek=3, sensor=s), {"evidence": True}))
    return out


# All individual use-cases, resolvable by name (plans reference these via groups).
# The overlay names map to the parametric verbs with overlay=True -- they CONSUME the one
# continuous metadata_service (no per-use-case ES/publisher).
USECASE_FUNCS = {
    "nvstreamer_file_upload": nvstreamer_file_upload,
    "rtsp_add_recording_check": rtsp_add_recording_check,
    "vios_file_upload": vios_file_upload,
    "download_overlay": functools.partial(download, overlay=True),
    "live_picture_overlay": functools.partial(picture, recent=True, overlay=True),
    "replay_picture_overlay": functools.partial(picture, overlay=True),
    "webrtc_live_overlay": functools.partial(webrtc_live, overlay=True),
    "webrtc_replay_overlay": functools.partial(webrtc_replay, overlay=True, seek=3),
    "video_wall": video_wall,
    "milestone_adaptor_test": milestone_adaptor_test,
    "onvif_adaptor_test": onvif_adaptor_test,
}

# Ordered registry for the non-plan (single-run) mode (all consume the continuous service).
USECASES = [
    ("download_overlay", functools.partial(download, overlay=True)),
    ("live_picture_overlay", functools.partial(picture, recent=True, overlay=True)),
    ("replay_picture_overlay", functools.partial(picture, overlay=True)),
    ("webrtc_live_overlay", functools.partial(webrtc_live, overlay=True)),
    ("webrtc_replay_overlay", functools.partial(webrtc_replay, overlay=True, seek=3)),
    ("video_wall", video_wall),
]

# Parametric verbs referenced by the structured `usecases:` items ({test: <verb>, ...}).
VERB_FUNCS = {
    "download": download,
    "picture": picture,
    "download_url": download_url,
    "picture_url": picture_url,
    "webrtc_live": webrtc_live,
    "webrtc_replay": webrtc_replay,
    "video_wall": video_wall,
    "perf": perf,
}
