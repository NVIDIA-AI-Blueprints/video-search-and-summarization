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
Standalone end-to-end driver for the VIOS download-overlay test.

Assumes VST is deployed with ``overlay.video_metadata_server`` pointed at the fake
ES address this script serves (e.g. ``172.17.0.1:19200/mdx-bev-test*``). It:

  1. adds an RTSP sensor (starts recording),
  2. records for a bit, then reads the recorded window + stream/sensor id,
  3. serves generated centered-box metadata for that window from a fake ES,
  4. downloads the window twice (overlay on, overlay off) via the storage API,
  5. asserts a box of the class colour is drawn near frame centre (and not in the
     overlay-off control).

Run:  python3 run_overlay_e2e.py --base-url http://localhost:30888 \
          --rtsp 'rtsp://<host>:31555/nvstream/.../warehouse_sample.mp4' \
          --es-host 172.17.0.1 --es-port 19200

This is the flow the pytest-bdd test wraps once proven.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

# Allow running from the bdd_tests root or the script dir.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.overlay.metadata_generator import OverlaySpec, generate_es_docs  # noqa: E402
from scripts.overlay.fake_es_server import FakeESServer  # noqa: E402
from tests.overlay.overlay_test_utils import (  # noqa: E402
    PERSON_RGB, download_overlay_clip, assert_overlay_box, epoch_ms_to_iso_z,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("overlay_e2e")

V = "/vst/api/v1"


def add_rtsp_sensor(base, rtsp, name, verify_ssl):
    r = requests.post(f"{base}{V}/sensor/add", json={"sensorUrl": rtsp, "name": name},
                      timeout=60, verify=verify_ssl)
    log.info("add sensor -> %s %s", r.status_code, r.text[:300])
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return {}


def list_streams(base, verify_ssl):
    r = requests.get(f"{base}{V}/sensor/streams", timeout=30, verify=verify_ssl)
    r.raise_for_status()
    return r.json()


def get_timelines(base, verify_ssl):
    r = requests.get(f"{base}{V}/storage/timelines", timeout=30, verify=verify_ssl)
    r.raise_for_status()
    return r.json()


def delete_sensor(base, sensor_id, verify_ssl):
    try:
        r = requests.delete(f"{base}{V}/sensor/{sensor_id}", timeout=60, verify=verify_ssl)
        log.info("delete sensor %s -> %s", sensor_id, r.status_code)
    except requests.RequestException as e:
        log.warning("delete sensor failed: %s", e)


def _iso_to_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000 + 0.5)


def pick_window(base, stream_id, window_seconds, lead_s, tail_guard_s, verify):
    """Choose a [start,end] epoch-ms window safely inside the stream's timeline."""
    tl = get_timelines(base, verify)
    ranges = tl.get(stream_id) or []
    if not ranges:
        raise SystemExit(f"no timeline for stream {stream_id}: keys={list(tl.keys())}")
    # Longest range.
    best = max(ranges, key=lambda r: _iso_to_ms(r["endTime"]) - _iso_to_ms(r["startTime"]))
    t0, t1 = _iso_to_ms(best["startTime"]), _iso_to_ms(best["endTime"])
    start = t0 + lead_s * 1000
    end = min(start + window_seconds * 1000, t1 - tail_guard_s * 1000)
    if end <= start:
        raise SystemExit(f"timeline too short: [{best['startTime']}..{best['endTime']}]")
    log.info("window [%s .. %s] within timeline [%s .. %s]",
             epoch_ms_to_iso_z(start), epoch_ms_to_iso_z(end),
             best["startTime"], best["endTime"])
    return start, end


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:30888")
    p.add_argument("--stream-id", default="warehouse_sample")
    p.add_argument("--es-host", default="172.17.0.1")
    p.add_argument("--es-port", type=int, default=19200)
    p.add_argument("--index", default="mdx-bev-test")
    p.add_argument("--window-seconds", type=int, default=10)
    p.add_argument("--lead-seconds", type=int, default=5, help="skip this much after timeline start")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--no-protobuf", action="store_true")
    p.add_argument("--outdir", default="/tmp/vios_overlay_e2e")
    args = p.parse_args()

    verify = False
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    start_ms, end_ms = pick_window(args.base_url, args.stream_id, args.window_seconds,
                                   args.lead_seconds, tail_guard_s=3, verify=verify)

    # Centered box + dense metadata across the window (+/-1s guard) so every
    # downloaded frame finds a match within bbox_tolerance_ms.
    frames = int((args.window_seconds + 2) * args.fps)
    spec = OverlaySpec(sensor_id=args.stream_id, start_epoch_ms=start_ms - 1000,
                       fps=args.fps, frame_count=frames, width=args.width, height=args.height)
    docs = generate_es_docs(spec, protobuf=not args.no_protobuf)

    es = FakeESServer(host="0.0.0.0", port=args.es_port, index_name=args.index).start()
    es.load_docs(docs)
    try:
        route = args.stream_id  # non-upload stream: route key == streamId
        ov = outdir / "overlay_on.mp4"
        ctl = outdir / "overlay_off.mp4"
        r1 = download_overlay_clip(args.base_url, args.stream_id, route, start_ms, end_ms,
                                   ov, overlay=True, verify_ssl=verify)
        r2 = download_overlay_clip(args.base_url, args.stream_id, route, start_ms, end_ms,
                                   ctl, overlay=False, verify_ssl=verify)
        if not r1["success"] or not r2["success"]:
            raise SystemExit(f"download failed: overlay={r1} control={r2}")

        lx, ty, rx, by = spec.pixel_box()
        dur = (end_ms - start_ms) / 1000.0
        samples = [1.0, dur / 2.0, max(1.0, dur - 1.0)]
        summary = assert_overlay_box(
            ov, ctl, sample_seconds=samples, width=args.width, height=args.height,
            expect_box_px=(lx, ty, rx, by), min_diff_pixels=120, localize_frac=0.7,
        )
        log.info("OVERLAY TEST PASSED: %s", summary["samples"])
        print("RESULT: PASS max_diff=%d frac_in_box=%.2f" % (summary["max_diff"], summary["frac_in_box"]))
        return 0
    finally:
        es.stop()


if __name__ == "__main__":
    sys.exit(main())
