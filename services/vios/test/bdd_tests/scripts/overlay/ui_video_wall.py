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
Drive the VST web UI Video Wall (Playwright) and screenshot the multi-stream grid.
Doubles as a UI check: selects all sensors, starts the wall, and verifies multiple
video tiles render. Prints "RESULT: tiles=<n> -> <png>".
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

def _resolve_chrome():
    """Codec-capable browser for WebRTC capture: VIOS_SANITY_CHROME override, then system Google
    Chrome; else None (Playwright's bundled Chromium lacks H.264/H.265 -> WebRTC panel is black)."""
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:30888")
    ap.add_argument("--out", default="/tmp/vios_ui/video_wall.png")
    ap.add_argument("--out-mp4", default="/tmp/vios_ui/video_wall.mp4")
    ap.add_argument("--rec-dir", default="/tmp/vios_ui/vw_rec")
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--streams", help="comma-separated stream ids to select (else Select All)")
    a = ap.parse_args()
    Path(a.rec_dir).mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--no-sandbox", "--use-fake-ui-for-media-stream",
                                    "--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1600, "height": 900},
                            record_video_dir=a.rec_dir, record_video_size={"width": 1600, "height": 900})
        pg = ctx.new_page()
        pg.goto(f"{a.base_url}/vst/", wait_until="networkidle", timeout=30000)
        pg.get_by_text("Video Wall", exact=True).first.click()
        pg.wait_for_timeout(2500)
        if a.streams:
            combo = pg.get_by_role("combobox", name="Select Sensors")
            for s in [x.strip() for x in a.streams.split(",") if x.strip()]:
                combo.click(); combo.fill(s); pg.wait_for_timeout(700)
                pg.get_by_role("option", name=s, exact=True).first.click()
            pg.keyboard.press("Escape")
        else:
            pg.get_by_role("button", name="Select All").click()
        pg.wait_for_timeout(1000)
        pg.get_by_role("button", name="Start Video Wall").click()
        # The wall composites the selected streams into one stream, which takes
        # time to become ready. Poll for actual playback (videoWidth>0) and for
        # the "not ready" error to clear, up to ~35s.
        playing = False
        for _ in range(35):
            pg.wait_for_timeout(1000)
            try:
                vw = pg.evaluate(
                    "() => { const v=[...document.querySelectorAll('video')]"
                    ".map(e=>e.videoWidth||0); return Math.max(0, ...v); }")
            except Exception:
                vw = 0
            body = pg.inner_text("body")
            not_ready = "Streaming not ready" in body or "Playback Error" in body
            if vw > 0 and not not_ready:
                playing = True
                break
        # Enable the bbox + debug overlay on the started wall (same Analytics
        # Overlay Settings dialog the live player uses). Metadata for each tile's
        # sensor must be published concurrently for boxes to draw.
        overlay_on = False
        if playing:
            try:
                pg.get_by_role("button", name="Analytics Overlay Settings").first.click()
                pg.wait_for_timeout(1200)
                dlg = pg.get_by_role("dialog")
                dlg.get_by_text("Debug Mode", exact=True).click()
                pg.wait_for_timeout(300)
                dlg.get_by_role("button", name="Save Settings").click()
                overlay_on = True
                pg.wait_for_timeout(6000)   # let the overlay apply + metadata draw
            except Exception as e:  # noqa: BLE001
                print(f"overlay-enable warning: {e}", file=sys.stderr)
        pg.wait_for_timeout(int(a.seconds * 1000))   # record the overlaid wall
        tiles = pg.locator("video").count()
        pg.screenshot(path=a.out, full_page=True)
        video = pg.video
        ctx.close()                                  # flushes the recording
        webm = video.path() if video else None
        b.close()
    if webm and a.out_mp4:
        Path(a.out_mp4).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", f"-{int(a.seconds)}",
                        "-i", webm, "-c:v", "libx264", "-pix_fmt", "yuv420p", a.out_mp4], check=True)
    print(f"RESULT: playing={playing} overlay={overlay_on} tiles={tiles} -> {a.out} mp4={a.out_mp4}")
    return 0 if playing else 1


if __name__ == "__main__":
    sys.exit(main())
