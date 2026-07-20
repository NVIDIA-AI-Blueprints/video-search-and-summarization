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
BDD steps for the download-overlay test (features/overlay/overlay_download.feature).

Preconditions (env, not created by this test):
  * VST is deployed and recording the configured stream.
  * VST overlay.video_metadata_server points at this test's fake ES
    (host:port must match ``es_host``/``es_port`` below, default 172.17.0.1:19200).

The test seeds the fake ES with a synthetic centered box across the recorded
window, downloads that window with and without the bbox overlay, and asserts the
overlaid clip differs from the control only in a region localized to the box.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from scripts.overlay.metadata_generator import OverlaySpec, generate_es_docs
from scripts.overlay.fake_es_server import FakeESServer
from tests.overlay.overlay_test_utils import download_overlay_clip, assert_overlay_box

logger = logging.getLogger(__name__)

scenarios("../../features/overlay/overlay_download.feature")

# Defaults; override via config.json tests.overlay_tests.test_parameters.
_DEFAULTS = {
    "stream_id": "warehouse_sample",
    "es_host": "172.17.0.1",
    "es_port": 19200,
    "index": "mdx-bev-test",
    "window_seconds": 10,
    "lead_seconds": 5,
    "tail_guard_seconds": 3,
    "width": 1920,
    "height": 1080,
    "fps": 30.0,
    "min_diff_pixels": 120,
    "localize_frac": 0.7,
}

V = "/vst/api/v1"


def _iso_to_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000 + 0.5)


class _Ctx:
    pass


@pytest.fixture
def params(config):
    p = dict(_DEFAULTS)
    try:
        p.update(config["tests"]["overlay_tests"]["test_parameters"])
    except (KeyError, TypeError):
        pass
    return p


@pytest.fixture
def ctx():
    c = _Ctx()
    c.fake_es = None
    yield c
    if c.fake_es is not None:
        c.fake_es.stop()


@given("a recorded stream and a fake metadata store seeded with a centered box")
def seed_metadata(ctx, params, api_config, tmp_path):
    base = api_config["base_url"]
    verify = api_config.get("verify_ssl", False)
    stream_id = params["stream_id"]
    ctx.base = base
    ctx.verify = verify
    ctx.params = params
    ctx.outdir = tmp_path

    # Resolve the recorded window from the timeline.
    r = requests.get(f"{base}{V}/storage/timelines", timeout=30, verify=verify)
    r.raise_for_status()
    ranges = (r.json() or {}).get(stream_id) or []
    if not ranges:
        pytest.skip(f"no recording timeline for stream '{stream_id}'")
    best = max(ranges, key=lambda x: _iso_to_ms(x["endTime"]) - _iso_to_ms(x["startTime"]))
    t0, t1 = _iso_to_ms(best["startTime"]), _iso_to_ms(best["endTime"])
    start = t0 + params["lead_seconds"] * 1000
    end = min(start + params["window_seconds"] * 1000, t1 - params["tail_guard_seconds"] * 1000)
    if end <= start:
        pytest.skip(f"recording too short for stream '{stream_id}'")
    ctx.start_ms, ctx.end_ms = start, end

    # Generate a centered box for the window (+/-1s guard) and serve it.
    frames = int((params["window_seconds"] + 2) * params["fps"])
    ctx.spec = OverlaySpec(sensor_id=stream_id, start_epoch_ms=start - 1000,
                           fps=params["fps"], frame_count=frames,
                           width=params["width"], height=params["height"])
    docs = generate_es_docs(ctx.spec, protobuf=True)
    ctx.fake_es = FakeESServer(host="0.0.0.0", port=params["es_port"],
                               index_name=params["index"]).start()
    ctx.fake_es.load_docs(docs)
    logger.info("seeded %d docs for window [%d..%d]", len(docs), start, end)


@when("I download the recorded window with bbox overlay enabled")
def download_overlay(ctx):
    ctx.overlay_mp4 = ctx.outdir / "overlay_on.mp4"
    res = download_overlay_clip(ctx.base, ctx.params["stream_id"], ctx.params["stream_id"],
                                ctx.start_ms, ctx.end_ms, ctx.overlay_mp4,
                                overlay=True, verify_ssl=ctx.verify)
    assert res["success"], f"overlay download failed: {res}"


@when("I download the same window without overlay as a control")
def download_control(ctx):
    ctx.control_mp4 = ctx.outdir / "overlay_off.mp4"
    res = download_overlay_clip(ctx.base, ctx.params["stream_id"], ctx.params["stream_id"],
                                ctx.start_ms, ctx.end_ms, ctx.control_mp4,
                                overlay=False, verify_ssl=ctx.verify)
    assert res["success"], f"control download failed: {res}"


@then("the overlaid video differs from the control in a box at the seeded location")
def assert_box(ctx):
    lx, ty, rx, by = ctx.spec.pixel_box()
    dur = (ctx.end_ms - ctx.start_ms) / 1000.0
    samples = [1.0, dur / 2.0, max(1.0, dur - 1.0)]
    summary = assert_overlay_box(
        ctx.overlay_mp4, ctx.control_mp4, sample_seconds=samples,
        width=ctx.params["width"], height=ctx.params["height"],
        expect_box_px=(lx, ty, rx, by),
        min_diff_pixels=ctx.params["min_diff_pixels"],
        localize_frac=ctx.params["localize_frac"],
    )
    logger.info("overlay assertion summary: %s", summary)
