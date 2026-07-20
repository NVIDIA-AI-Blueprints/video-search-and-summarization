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
Drive the VST web UI (Playwright) for live OR replay and record the stream with
the bbox + debug overlay enabled. Doubles as a UI test (exercises the Live/Recorded
Streams pages + the Analytics Overlay Settings controls). Outputs:
  * <out>.mp4      -- full-page UI recording (evidence, shows the VST UI)
  * <panel-out>.png -- screenshot of just the <video> element (clean crop for the
    box assertion)

Live: metadata from the broker (published concurrently). Replay: metadata from ES
(the caller seeds it); pass --start-time/--end-time for the recorded window.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.overlay.live_publisher import LiveBoxSpec, RedisBoxPublisher, KafkaBoxPublisher  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ui_capture")

def _resolve_chrome():
    """Path to a codec-capable browser for WebRTC capture. The WebRTC video is H.264/H.265 and
    Playwright's BUNDLED Chromium lacks those proprietary codecs -> the panel renders black.
    Prefer the VIOS_SANITY_CHROME override, then system Google Chrome; else None (bundled
    Chromium -- fine for non-WebRTC, black for WebRTC)."""
    env = os.environ.get("VIOS_SANITY_CHROME")
    if env:
        return env
    for c in ("google-chrome", "google-chrome-stable", "chrome"):
        found = shutil.which(c)
        if found:
            return found
    for p in ("/opt/google/chrome/chrome",):
        if os.path.exists(p):
            return p
    return None


CHROME = _resolve_chrome()
NAV = {"live": "Live Streams", "replay": "Recorded Streams"}


def run(base_url, stream_id, seconds, out_path, panel_out, mode, broker, rec_dir,
        enable_overlay=True, seeks=0, width=1920, height=1080, no_publish=False):
    pub = t = None
    # no_publish -> an external continuous metadata_service feeds the broker; don't self-publish
    if mode == "live" and enable_overlay and not no_publish:  # replay reads ES (external)
        spec = LiveBoxSpec(sensor_id=stream_id, width=width, height=height)
        pub = (RedisBoxPublisher("localhost", 6379, "vst-overlay-test", spec, fps=30)
               if broker == "redis" else
               KafkaBoxPublisher("172.17.0.1:9092", "vst-overlay-test", spec, fps=30))

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox", "--use-fake-ui-for-media-stream",
                                    "--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1600, "height": 900},
                            record_video_dir=rec_dir, record_video_size={"width": 1600, "height": 900})
        pg = ctx.new_page()
        pg.goto(f"{base_url}/vst/", wait_until="networkidle", timeout=30000)
        pg.get_by_text(NAV[mode], exact=True).first.click()
        pg.wait_for_timeout(2500)

        box = pg.get_by_role("combobox", name="Select Sensors")
        box.click(); box.fill(stream_id); pg.wait_for_timeout(1000)
        pg.get_by_role("option", name=stream_id).first.click()
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(5000)
        log.info("[%s] stream selected", mode)

        # Enable overlay (Debug Mode + bounding boxes) and save.
        if enable_overlay:
            pg.get_by_role("button", name="Analytics Overlay Settings").click()
            pg.wait_for_timeout(1200)
            dlg = pg.get_by_role("dialog")
            dlg.get_by_text("Debug Mode", exact=True).click()
            pg.wait_for_timeout(300)
            # Best-effort: draw the objId (= burned frame no.) on the box. Short timeout so
            # a missing/renamed toggle NEVER hangs or fails the capture (box still draws).
            for _lbl in ("Show Object ID", "Show Object Id", "Object ID"):
                try:
                    dlg.get_by_text(_lbl, exact=True).first.click(timeout=2500)
                    pg.wait_for_timeout(300)
                    break
                except Exception:  # noqa: BLE001
                    continue
            else:
                log.warning("[%s] 'Show Object ID' toggle not found; continuing", mode)
            dlg.get_by_role("button", name="Save Settings").click()
            pg.wait_for_timeout(3000)
            log.info("[%s] overlay enabled (bbox + debug)", mode)

        if pub is not None:
            t = threading.Thread(target=pub.run, args=(seconds + 6,), daemon=True)
            t.start()

        # Replay seek exercise: jump the player a few times and confirm it keeps
        # playing (tests the VIOS replay seek path).
        if mode == "replay" and seeks > 0:
            span = max(1.0, seconds / (seeks + 1))
            for i in range(seeks):
                target = round(span * (i + 1), 1)
                moved = pg.evaluate(
                    "(t) => { const v = document.querySelector('video');"
                    " if (!v) return false; v.currentTime = (v.currentTime||0) + t;"
                    " return !v.paused; }", target)
                log.info("[replay] seek #%d +%.1fs playing=%s", i + 1, target, moved)
                pg.wait_for_timeout(1500)

        pg.wait_for_timeout(int(seconds * 1000))
        if pub is not None:
            pub.stop(); t.join(timeout=5)

        # Clean crop of just the video panel for assertion.
        try:
            pg.locator("video").first.screenshot(path=panel_out)
        except Exception as e:  # noqa: BLE001
            log.warning("video-element screenshot failed: %s", e)

        video = pg.video
        ctx.close()
        webm = video.path()
        b.close()

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", f"-{int(seconds)}",
                    "-i", webm, "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path], check=True)
    log.info("saved UI capture -> %s (panel: %s)", out_path, panel_out)
    return out_path


def run_nvstreamer(base_url, stream_id, seconds, out_path, panel_out, rec_dir):
    """Drive the NVStreamer UI (:31000, 'Media Streams' page) and record a live
    WebRTC stream. Selecting the sensor auto-starts the live panel."""
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox", "--use-fake-ui-for-media-stream",
                                    "--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1600, "height": 900},
                            record_video_dir=rec_dir, record_video_size={"width": 1600, "height": 900})
        pg = ctx.new_page()
        pg.goto(base_url.rstrip("/") + "/", wait_until="networkidle", timeout=30000)
        pg.wait_for_timeout(2000)
        pg.get_by_text("Media Streams", exact=True).first.click()
        pg.wait_for_timeout(2000)
        cb = pg.get_by_role("combobox", name="Select Sensors").first
        cb.click(); pg.wait_for_timeout(1200)
        pg.get_by_role("option", name=stream_id).first.click()
        pg.keyboard.press("Escape"); pg.wait_for_timeout(3000)
        try:                              # selecting usually auto-plays; click if present
            pg.get_by_role("button", name="Start Stream", exact=True).first.click(timeout=4000)
        except Exception:  # noqa: BLE001
            pass
        pg.wait_for_timeout(int(seconds * 1000))
        for v in pg.query_selector_all("video"):
            st = pg.evaluate("(el) => ({w: el.videoWidth, h: el.videoHeight})", v)
            if st["w"] > 0:
                try:
                    v.screenshot(path=panel_out)
                except Exception as e:  # noqa: BLE001
                    log.warning("nvstreamer panel screenshot failed: %s", e)
                break
        video = pg.video
        ctx.close()
        webm = video.path()
        b.close()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", f"-{int(seconds)}",
                    "-i", webm, "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path], check=True)
    log.info("saved NVStreamer UI capture -> %s (panel: %s)", out_path, panel_out)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:30888")
    ap.add_argument("--stream-id", default="warehouse_sample")
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--mode", default="live", choices=["live", "replay"])
    ap.add_argument("--broker", default="redis", choices=["redis", "kafka"])
    ap.add_argument("--out", default="/tmp/vios_ui/vst_ui_overlay.mp4")
    ap.add_argument("--panel-out", default="/tmp/vios_ui/vst_ui_panel.png")
    ap.add_argument("--rec-dir", default="/tmp/vios_ui/rec")
    ap.add_argument("--seek", type=int, default=0, help="replay: number of seeks to exercise")
    ap.add_argument("--no-overlay", action="store_true", help="do not enable the analytics overlay")
    ap.add_argument("--ui", default="vst", choices=["vst", "nvstreamer"],
                    help="which UI to drive: vst (:30888) or nvstreamer (:31000)")
    ap.add_argument("--width", type=int, default=1920, help="source frame width (live bbox coords)")
    ap.add_argument("--height", type=int, default=1080, help="source frame height (live bbox coords)")
    ap.add_argument("--no-publish", action="store_true",
                    help="don't self-publish live metadata; an external metadata_service feeds the broker")
    a = ap.parse_args()
    Path(a.rec_dir).mkdir(parents=True, exist_ok=True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    if a.ui == "nvstreamer":
        run_nvstreamer(a.base_url, a.stream_id, a.seconds, a.out, a.panel_out, a.rec_dir)
    else:
        run(a.base_url, a.stream_id, a.seconds, a.out, a.panel_out, a.mode, a.broker, a.rec_dir,
            enable_overlay=not a.no_overlay, seeks=a.seek, width=a.width, height=a.height,
            no_publish=a.no_publish)
    print(f"RESULT: {a.out} | panel {a.panel_out}")


if __name__ == "__main__":
    main()
