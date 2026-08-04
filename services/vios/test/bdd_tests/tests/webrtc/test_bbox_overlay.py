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
BDD tests for VST bbox overlay rendering.

Covers BDD-GAP-050, BDD-GAP-051 (live picture + live WebRTC), BDD-GAP-052.

Live scenarios publish ``nv.Frame`` protobuf to a Redis stream so the VIOS
notification consumer (DsProtoParser → LiveMetadataStore) can feed the overlay
draw path. Requires:

  * VIOS with enable_notification_consumer=true and
    use_message_broker_consumer=redis (topic must match config)
  * An active live RTSP stream (sensorId == published sensorId)
  * Redis reachable from the BDD host
  * ``poetry`` deps: redis, protobuf, aiortc; host tools: ffmpeg/ffprobe

GAP-050 / GAP-052 remain stubs (stored / replay metadata paths).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path

import pytest
import requests
from pytest_bdd import scenarios, given, when, then, parsers

from scripts.overlay.live_publisher import LiveBoxSpec, RedisBoxPublisher
from tests.picture.picture_test_utils import ENDPOINTS_LIVE, fetch_streams
from tests.webrtc.bbox_overlay_assert import (
    assert_live_box_border,
    jpeg_to_rgb,
    scale_box,
)
from tests.webrtc.webrtc_overlay_capture import (
    capture_live_webrtc_overlay_frames,
    sample_first_middle_last,
)

logger = logging.getLogger(__name__)

scenarios('../../features/webrtc/bbox_overlay.feature')

_DEFAULTS = {
    "stream_id": "",
    "redis_host": "localhost",
    "redis_port": 6379,
    "topic": "mdx-raw",
    "width": 1920,
    "height": 1080,
    "fps": 30.0,
    "warmup_s": 3.0,
    "publish_duration_s": 45.0,
    "picture_attempts": 4,
    "min_border": 200,
    # Forced red via unknown class type (avoids vst_config overlay_color_code).
    "obj_type": "BddOverlayTest",
    "overlay_color": "red",
    "overlay_thickness": 11,
    "expected_rgb": [255, 0, 0],
    "rgb_tol": 55,
    "timeout": 30,
    # WebRTC live overlay sampling (first / middle / last of collected frames).
    "webrtc_warmup_frames": 10,
    "webrtc_collect_frames": 30,
    "webrtc_min_passing_samples": 2,
    "webrtc_min_border": 80,
    "webrtc_signaling_timeout": 60,
}


class BBoxContext:
    def __init__(self):
        self.stream_id = None
        self.sensor_name = None
        self.filter_value = None
        self.last_picture = None
        self.params = dict(_DEFAULTS)
        self.spec = None
        self.publisher = None
        self.pub_thread = None
        self.rgb = None
        self.jpeg_w = None
        self.jpeg_h = None
        self.base_url = None
        self.verify_ssl = False
        self.webrtc_frames = None  # list[(rgb, w, h)]


@pytest.fixture
def context():
    ctx = BBoxContext()
    yield ctx
    if ctx.publisher is not None:
        ctx.publisher.stop()
    if ctx.pub_thread is not None:
        ctx.pub_thread.join(timeout=5)


def _load_params(config) -> dict:
    params = dict(_DEFAULTS)
    try:
        params.update(
            config.get("tests", {})
            .get("bbox_overlay_tests", {})
            .get("test_parameters", {})
        )
    except (TypeError, AttributeError):
        pass
    return params


def _resolve_live_stream(ctx: BBoxContext, api_config: dict) -> tuple[str, str]:
    """Return ``(stream_id, sensor_name)`` for API vs Redis metadata matching.

    LiveMetadataStore filters on the camera *name* (``sensorID`` / overlay
    match log), which can differ from the ``/live/streams`` map key / UUID.
    Prefer an H264 stream when auto-picking so WebRTC decode is reliable.
    """
    streams = fetch_streams(
        api_config["base_url"],
        ENDPOINTS_LIVE["streams"],
        ctx.params.get("timeout", 30),
        api_config.get("verify_ssl", False),
    )
    entries = []
    for stream_obj in streams:
        if not isinstance(stream_obj, dict) or not stream_obj:
            continue
        for sid, arr in stream_obj.items():
            meta = {}
            name = sid
            if isinstance(arr, list) and arr and isinstance(arr[0], dict):
                name = arr[0].get("name") or arr[0].get("streamId") or sid
                meta = arr[0].get("metadata") or {}
            codec = str(meta.get("codec") or "").upper()
            entries.append((sid, name, codec))

    configured = (ctx.stream_id or ctx.params.get("stream_id")
                  or ctx.params.get("bbox_stream_id") or "").strip()
    if configured:
        for sid, name, _codec in entries:
            if configured in (sid, name):
                return sid, name
        # Configured id not listed yet — treat as both API id and sensor name.
        return configured, configured

    # Prefer known-good H264 warehouse sample, then any H264, else first stream.
    for sid, name, codec in entries:
        if name == "warehouse_sample" or sid == "warehouse_sample":
            return sid, name
    for sid, name, codec in entries:
        if codec == "H264":
            return sid, name
    if entries:
        sid, name, _codec = entries[0]
        return sid, name
    pytest.skip("No live streams available for bbox overlay live test")


def _resolve_live_stream_id(ctx: BBoxContext, api_config: dict) -> str:
    sid, _name = _resolve_live_stream(ctx, api_config)
    return sid


@given('the VST API is configured for bbox overlay tests')
def configure_bbox(context, api_config, config):
    """Load overlay test parameters; live path can auto-pick a stream."""
    context.params = _load_params(config)
    context.base_url = api_config["base_url"]
    context.verify_ssl = api_config.get("verify_ssl", False)
    context.stream_id = (
        context.params.get("stream_id")
        or context.params.get("bbox_stream_id")
        or None
    )
    if context.stream_id == "":
        context.stream_id = None


@given(parsers.parse('a stream has stored bbox metadata with classType "{class_type}"'))
def need_classtype_metadata(context, class_type):
    pytest.skip(
        f"Requires stored bbox metadata for classType '{class_type}'. "
        f"Seed metadata via the perception pipeline."
    )


@given('an active stream has live bbox metadata')
def need_live_bbox_metadata(context, api_config):
    """Start Redis nv.Frame publisher keyed to an active live stream."""
    stream_id, sensor_name = _resolve_live_stream(context, api_config)
    context.stream_id = stream_id
    context.sensor_name = sensor_name
    params = context.params
    spec = LiveBoxSpec(
        # Overlay LiveMetadataStore matches protobuf sensorId to camera *name*.
        sensor_id=sensor_name,
        width=int(params["width"]),
        height=int(params["height"]),
        obj_type=params.get("obj_type", "BddOverlayTest"),
    )
    context.spec = spec
    try:
        publisher = RedisBoxPublisher(
            params["redis_host"],
            int(params["redis_port"]),
            params["topic"],
            spec,
            fps=float(params["fps"]),
        )
        # Touch Redis early so we skip cleanly if unreachable / redis missing.
        publisher._r.ping()
    except Exception as exc:  # noqa: BLE001 — surface as skip for CI without Redis
        pytest.skip(
            "Redis publisher unavailable (need redis+protobuf packages and a "
            f"reachable Redis with VIOS use_message_broker_consumer=redis, "
            f"topic={params.get('topic')!r}): {exc}"
        )
    context.publisher = publisher
    context.pub_thread = threading.Thread(
        target=publisher.run,
        args=(float(params["publish_duration_s"]),),
        daemon=True,
    )
    context.pub_thread.start()
    time.sleep(float(params["warmup_s"]))
    logger.info(
        "Redis bbox publisher started api_stream=%s sensor=%s topic=%s host=%s:%s",
        context.stream_id, sensor_name, params["topic"],
        params["redis_host"], params["redis_port"],
    )


@given('a recorded stream has stored bbox metadata')
def need_recorded_bbox_metadata(context):
    pytest.skip(
        "Requires a recorded stream with stored bbox metadata."
    )


@when(parsers.parse('overlay is requested filtered by classType "{filter_value}"'))
def overlay_classtype_filter(context, filter_value):
    context.filter_value = filter_value


@when('the live picture is requested with overlay=true')
def live_picture_overlay(context, tmp_path):
    params = context.params
    assert context.stream_id, "stream_id not set"
    url = (
        f"{context.base_url}"
        f"{ENDPOINTS_LIVE['picture'].format(stream_id=context.stream_id)}"
    )
    overlay = {
        "bbox": {"showAll": "true"},
        "color": params.get("overlay_color", "red"),
        "thickness": str(params.get("overlay_thickness", 11)),
        "debug": "false",
    }
    jpg = Path(tmp_path) / "live_overlay.jpg"
    last_err = None
    for attempt in range(int(params.get("picture_attempts", 4))):
        try:
            resp = requests.get(
                url,
                params={"overlay": json.dumps(overlay)},
                headers={"streamid": context.stream_id},
                timeout=int(params.get("timeout", 30)),
                verify=context.verify_ssl,
            )
            assert resp.status_code == 200 and resp.content, (
                f"live picture failed: status={resp.status_code} body={resp.text[:200]!r}"
            )
            jpg.write_bytes(resp.content)
            context.last_picture = jpg
            context.rgb, context.jpeg_w, context.jpeg_h = jpeg_to_rgb(jpg)
            last_err = None
            # Consumer often connects on first overlay request; retry a few times.
            time.sleep(1.0)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1.0)
    if last_err is not None and context.rgb is None:
        raise last_err


@when('a live WebRTC stream is started with overlay enabled')
def live_webrtc_overlay(context):
    assert context.stream_id, "stream_id not set"
    params = context.params
    context.webrtc_frames = asyncio.run(
        capture_live_webrtc_overlay_frames(
            context.base_url,
            context.stream_id,
            collect_frames=int(params.get("webrtc_collect_frames", 30)),
            warmup_frames=int(params.get("webrtc_warmup_frames", 10)),
            signaling_timeout=float(params.get("webrtc_signaling_timeout", 60)),
            overlay_color=params.get("overlay_color", "red"),
            overlay_thickness=int(params.get("overlay_thickness", 11)),
        )
    )


@when('the replay picture is requested with overlay=true')
def replay_picture_overlay(context):
    pass


@then('the overlay is rendered for the filter')
def overlay_rendered(context):
    pass


def _expected_target(params) -> tuple:
    rgb = params.get("expected_rgb", [255, 0, 0])
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]))


@then('the JPEG contains a region of the expected bbox color')
def jpeg_has_bbox_color(context):
    assert context.rgb is not None, "no live picture RGB captured"
    assert context.spec is not None, "no LiveBoxSpec"
    params = context.params
    src_box = context.spec.pixel_box()
    box = scale_box(
        src_box,
        int(params["width"]),
        int(params["height"]),
        context.jpeg_w,
        context.jpeg_h,
    )
    assert_live_box_border(
        context.rgb,
        context.jpeg_w,
        context.jpeg_h,
        box,
        target_rgb=_expected_target(params),
        min_border=int(params.get("min_border", 200)),
        tol=int(params.get("rgb_tol", 55)),
    )


@then('sampled WebRTC frames contain a region of the expected bbox color')
def webrtc_frames_have_bbox_color(context):
    assert context.webrtc_frames, "no WebRTC RGB frames captured"
    assert context.spec is not None, "no LiveBoxSpec"
    params = context.params
    target = _expected_target(params)
    min_border = int(params.get("webrtc_min_border", params.get("min_border", 80)))
    tol = int(params.get("rgb_tol", 55))
    min_pass = int(params.get("webrtc_min_passing_samples", 2))
    src_box = context.spec.pixel_box()
    src_w, src_h = int(params["width"]), int(params["height"])

    samples = sample_first_middle_last(context.webrtc_frames)
    passed = []
    failures = []
    for idx, (rgb, w, h) in samples:
        box = scale_box(src_box, src_w, src_h, w, h)
        try:
            assert_live_box_border(
                rgb, w, h, box, target_rgb=target, min_border=min_border, tol=tol,
            )
            passed.append(idx)
        except AssertionError as exc:
            failures.append((idx, str(exc)))
            logger.warning("WebRTC sample frame[%d] bbox assert failed: %s", idx, exc)

    assert len(passed) >= min_pass, (
        f"bbox overlay not found on enough WebRTC samples: passed={passed} "
        f"need>={min_pass}; failures={failures}"
    )
    logger.info(
        "WebRTC overlay bbox OK on %d/%d samples (indices=%s)",
        len(passed), len(samples), passed,
    )
