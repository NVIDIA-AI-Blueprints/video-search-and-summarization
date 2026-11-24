#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Manual distortion-overlay tool (POC).

Interactive helper to eyeball / fine-tune the radial distortion parameter(s) used
by the AMC rectification pipeline, for cases where the automatic GeoCalib /
line-search estimate is inaccurate.

Idea
----
Instead of displaying the *rectified* image for the current parameters (which is
hard to judge), this tool overlays a grid of lines that are **straight in the
rectified (undistorted) space** and **forward-distorted** into the *original*
image using the current model + parameters. When the parameters match the true
lens distortion, the overlaid curves follow the scene's truly-straight edges
(wall / floor / pillar lines, etc.). The tool starts at ``k1 = 0`` (no distortion
-> straight grid).

Supported models (GeoCalib / AMC)
---------------------------------
Forward distortion scale ``s`` applied to a point ``p`` about the principal point
``c`` as ``p_distorted = (p - c) * s + c``, with ``r^2 = |p - c|^2`` in pixels^2:

- ``pinhole``            : ``s = 1``                                   (no distortion)
- ``simple_radial``      : ``s = 1 + k1 r^2``
- ``radial``             : ``s = 1 + k1 r^2 + k2 r^4``
- ``simple_divisional``  : ``s = (1 - sqrt(1 - 4 k1 r^2)) / (2 k1 r^2)``

Convention (matches ``core/rectification/lens_distortion.py`` and
``core/camera_estimation/run_geocalib.py``)
--------------------------------------------------------------------------
- Pinhole camera with ``fx = fy = 1`` and principal point ``(W // 2, H // 2)``.
- Because ``f = 1`` and ``r`` is in pixels, these ``k1`` / ``k2`` are in AMC
  "full-resolution pixel" units, i.e. GeoCalib's values converted as
  ``k1_amc = k1_geocalib / f^2`` and ``k2_amc = k2_geocalib / f^4``. The values
  produced here are directly usable in the AMC ``distortion.yaml``.
- Note: the current AMC rectification remap (``lens_distortion.undistort_image``)
  only consumes ``k1`` (OpenCV simple-radial). ``radial`` / ``simple_divisional``
  here are for visualization / analysis of which model best fits the scene.

Usage
-----
Interactive (needs a display):
    python k1_overlay_tool.py -i /path/to/image_or_video

Headless snapshot (no GUI; dumps overlay PNGs for a few k1 values):
    python k1_overlay_tool.py -i clip.mp4 --model simple_radial --snapshot -2e-5,-1e-5,0 -o out_dir

Keys (interactive): left/right = fine k1 nudge, down/up = coarse k1 nudge,
r = reset, s = save.
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
import yaml

from distortion_models import (
    MODELS,
    auto_param_ranges,
    build_grid_lines,
    corner_r2,
    forward_distort,
    params_dict as _params_dict,
    radial_scale,
    rectify_image_bgr,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("k1_overlay_tool")


# --------------------------------------------------------------------------- #
# Input loading
# --------------------------------------------------------------------------- #
def load_frame(input_path: Path, frame_index: int = 0) -> np.ndarray:
    """Load an image, or extract a frame from a video.

    For videos, seeks to ``frame_index`` and returns the first successfully
    decoded frame at/after that index (skips undecodable leading frames).

    Args:
        input_path: Path to an image or video file.
        frame_index: For videos, the desired starting frame index.

    Returns:
        BGR image as an ``(H, W, 3)`` uint8 array (OpenCV convention).

    Raises:
        FileNotFoundError: If the path does not exist.
        ValueError: If no image/frame could be read.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input does not exist: {input_path}")

    # Try as a still image first.
    img = cv2.imread(str(input_path))
    if img is not None:
        logger.info("Loaded image: %s  (%dx%d)", input_path.name, img.shape[1], img.shape[0])
        return img

    # Fall back to video decoding.
    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open as image or video: {input_path}")

    if frame_index > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, float(frame_index))

    frame = None
    for _ in range(300):  # scan a bounded number of frames for the first valid one
        ret, f = cap.read()
        if not ret:
            break
        if f is not None:
            frame = f
            break
    cap.release()

    if frame is None:
        raise ValueError(f"Could not read any valid frame from video: {input_path}")

    logger.info(
        "Extracted frame %d from video: %s  (%dx%d)",
        frame_index, input_path.name, frame.shape[1], frame.shape[0],
    )
    return frame


# --------------------------------------------------------------------------- #
# Grid generation (build_grid_lines imported from distortion_models)
# --------------------------------------------------------------------------- #
def distort_lines(
    lines: List[np.ndarray],
    cx: float,
    cy: float,
    model: str,
    k1: float,
    k2: float,
    scale: float = 1.0,
) -> List[np.ndarray]:
    """Forward-distort a list of polylines and optionally scale for display.

    Distortion is computed at native resolution (so ``k1`` / ``k2`` keep their
    AMC full-resolution meaning); the result is then multiplied by ``scale`` for
    drawing on a possibly-downscaled display image.

    Args:
        lines: List of undistorted polylines (native-resolution pixel coords).
        cx: Principal point x (native pixels).
        cy: Principal point y (native pixels).
        model: Distortion model.
        k1: First radial coefficient.
        k2: Second radial coefficient (``radial`` only).
        scale: Display scale factor applied AFTER distortion.

    Returns:
        List of distorted (and scaled) polylines.
    """
    out: List[np.ndarray] = []
    for ln in lines:
        xd, yd = forward_distort(ln[:, 0], ln[:, 1], cx, cy, model, k1, k2)
        out.append(np.stack([xd * scale, yd * scale], axis=1))
    return out


# --------------------------------------------------------------------------- #
# Rendering / IO helpers
# --------------------------------------------------------------------------- #
def _display_scale(width: int, height: int, max_width: int) -> float:
    """Compute a downscale factor so the displayed image width <= ``max_width``."""
    if max_width <= 0 or width <= max_width:
        return 1.0
    return float(max_width) / float(width)


def rotate_lines(
    lines: List[np.ndarray],
    cx: float,
    cy: float,
    rotation_deg: float,
) -> List[np.ndarray]:
    """Rotate polylines about the principal point (visualization-only aid).

    Rotating a straight line keeps it straight; since radial distortion is
    isotropic the squared radius is preserved, so this only reorients which
    scene direction the contours align with. It never affects ``k1``/``k2`` or
    the rectification.

    Args:
        lines: Undistorted polylines (native coords).
        cx: Principal point x (native pixels).
        cy: Principal point y (native pixels).
        rotation_deg: Rotation angle in degrees.

    Returns:
        Rotated polylines (or the input list unchanged if angle is ~0).
    """
    if abs(rotation_deg) < 1e-9:
        return lines
    th = np.deg2rad(rotation_deg)
    c, s = np.cos(th), np.sin(th)
    out: List[np.ndarray] = []
    for ln in lines:
        xn = ln[:, 0] - cx
        yn = ln[:, 1] - cy
        xr = xn * c - yn * s + cx
        yr = xn * s + yn * c + cy
        out.append(np.stack([xr, yr], axis=1))
    return out


def render_overlay_bgr(
    img_bgr: np.ndarray,
    lines: List[np.ndarray],
    cx: float,
    cy: float,
    model: str,
    k1: float,
    k2: float,
    max_width: int,
    color: Tuple[int, int, int] = (255, 0, 255),  # magenta (BGR)
    rotation_deg: float = 0.0,
) -> np.ndarray:
    """Draw the distorted grid on a (possibly downscaled) copy of the image (BGR).

    Args:
        img_bgr: Source image (BGR, native resolution).
        lines: Undistorted polylines (native coords).
        cx: Principal point x (native pixels).
        cy: Principal point y (native pixels).
        model: Distortion model.
        k1: First radial coefficient.
        k2: Second radial coefficient (``radial`` only).
        max_width: Max display width for downscaling.
        color: BGR color for the grid.
        rotation_deg: Visualization-only rotation of the contours about the
            center (does not affect params/rectification).

    Returns:
        BGR image with the overlay drawn.
    """
    h, w = img_bgr.shape[:2]
    s = _display_scale(w, h, max_width)
    disp = cv2.resize(img_bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s != 1.0 else img_bgr.copy()

    lines_r = rotate_lines(lines, cx, cy, rotation_deg)
    for ln in distort_lines(lines_r, cx, cy, model, k1, k2, scale=s):
        pts = ln.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(disp, [pts], isClosed=False, color=color, thickness=2, lineType=cv2.LINE_AA)

    cc = (int(cx * s), int(cy * s))
    cv2.drawMarker(disp, cc, (0, 0, 255), markerType=cv2.MARKER_CROSS, markerSize=14, thickness=1)
    label = f"{model}  k1={k1:.3e}" + (f"  k2={k2:.3e}" if model == "radial" else "")
    if abs(rotation_deg) >= 1e-9:
        label += f"  rot={rotation_deg:g}deg"
    cv2.putText(disp, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(disp, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1, cv2.LINE_AA)
    return disp


def rectify_image(
    img_bgr: np.ndarray,
    cx: float,
    cy: float,
    model: str,
    k1: float,
    k2: float,
) -> np.ndarray:
    """Rectify via shared distortion_models remapper."""
    return rectify_image_bgr(img_bgr, model, k1, k2, cx=cx, cy=cy)


def save_params(output_dir: Path, model: str, k1: float, k2: float) -> Path:
    """Write current model + params to ``distortion.yaml`` (AMC-compatible)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "distortion.yaml"
    data = _params_dict(model, k1, k2)
    with open(out_path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    logger.info("Saved %s -> %s", data, out_path)
    return out_path


def save_bundle(
    output_dir: Path,
    img_bgr: np.ndarray,
    lines: List[np.ndarray],
    cx: float,
    cy: float,
    model: str,
    k1: float,
    k2: float,
    rotation_deg: float = 0.0,
) -> Path:
    """Save the full result bundle for the current selection.

    Writes, into a timestamped subfolder of ``output_dir`` (non-destructive):
        1. ``distortion.yaml``      - selected model + params (AMC-compatible)
        2. ``overlay.png``          - original image with axis-aligned contours
        3. ``rectified.png``        - rectified image using the selected params
        4. ``overlay_rotated.png``  - overlay with the visualization rotation
           applied (only when ``rotation_deg`` != 0). This reflects the actual
           on-screen alignment used during tuning. The rotation is NOT written
           to ``distortion.yaml`` (it is a visualization aid only).

    Args:
        output_dir: Base output directory.
        img_bgr: Source (distorted) image, BGR, native resolution.
        lines: Undistorted grid polylines (native coords).
        cx: Principal point x (native pixels).
        cy: Principal point y (native pixels).
        model: Distortion model.
        k1: First radial coefficient.
        k2: Second radial coefficient (``radial`` only).
        rotation_deg: On-screen contour rotation (degrees) to bake into the
            extra ``overlay_rotated.png`` only.

    Returns:
        Path to the created bundle subfolder.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_dir = output_dir / f"{stamp}_{model}"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # 1. params (rotation intentionally excluded)
    with open(bundle_dir / "distortion.yaml", "w") as f:
        yaml.safe_dump(_params_dict(model, k1, k2), f, sort_keys=False)

    # 2. overlay at native resolution (max_width=0 -> no downscale), axis-aligned
    overlay = render_overlay_bgr(img_bgr, lines, cx, cy, model, k1, k2, max_width=0)
    cv2.imwrite(str(bundle_dir / "overlay.png"), overlay)

    # 3. rectified image at native resolution
    rectified = rectify_image(img_bgr, cx, cy, model, k1, k2)
    cv2.imwrite(str(bundle_dir / "rectified.png"), rectified)

    saved = ["distortion.yaml", "overlay.png", "rectified.png"]

    # 4. rotated overlay (only meaningful when a rotation was set on screen)
    if abs(rotation_deg) >= 1e-9:
        overlay_rot = render_overlay_bgr(
            img_bgr, lines, cx, cy, model, k1, k2, max_width=0, rotation_deg=rotation_deg
        )
        cv2.imwrite(str(bundle_dir / "overlay_rotated.png"), overlay_rot)
        saved.append(f"overlay_rotated.png (rot={rotation_deg:g}deg)")

    logger.info("Saved bundle -> %s  (%s)  %s",
                bundle_dir, ", ".join(saved), _params_dict(model, k1, k2))
    return bundle_dir


# --------------------------------------------------------------------------- #
# Snapshot (headless) mode
# --------------------------------------------------------------------------- #
def run_snapshot(
    img_bgr: np.ndarray,
    lines: List[np.ndarray],
    cx: float,
    cy: float,
    model: str,
    k1_values: List[float],
    k2: float,
    output_dir: Path,
    max_width: int,
) -> None:
    """Render and save overlay + rectified PNGs for a set of ``k1`` values (no GUI)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for k1 in k1_values:
        overlay = render_overlay_bgr(img_bgr, lines, cx, cy, model, k1, k2, max_width)
        overlay_path = output_dir / f"overlay_{model}_k1_{k1:+.3e}.png"
        cv2.imwrite(str(overlay_path), overlay)

        rectified = rectify_image(img_bgr, cx, cy, model, k1, k2)
        rectified_path = output_dir / f"rectified_{model}_k1_{k1:+.3e}.png"
        cv2.imwrite(str(rectified_path), rectified)

        logger.info("Wrote %s and %s", overlay_path.name, rectified_path.name)


# --------------------------------------------------------------------------- #
# Interactive (browser) mode  -- works headless / over SSH, no X display needed
# --------------------------------------------------------------------------- #
_BROWSER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Manual K1 tuner</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #111; color: #eee; font-family: system-ui, sans-serif; }
  header { padding: 8px 12px; background: #1b1b1b; border-bottom: 1px solid #333; font-size: 13px; }
  header b { color: #9f9; }
  #wrap { display: flex; gap: 16px; padding: 12px; align-items: flex-start; flex-wrap: wrap; }
  #stage { position: relative; line-height: 0; border: 1px solid #333; }
  #base { display: block; max-width: 100%; height: auto; }
  #overlay { position: absolute; left: 0; top: 0; width: 100%; height: 100%; pointer-events: none; }
  #panel { min-width: 320px; max-width: 380px; font-size: 14px; }
  .row { margin: 10px 0; }
  .row label { display: inline-block; min-width: 92px; color: #bbb; }
  input[type=range] { width: 100%; }
  input[type=number] { width: 160px; background: #222; color: #eee; border: 1px solid #444; padding: 3px; }
  select { background: #222; color: #eee; border: 1px solid #444; padding: 4px; }
  button { background: #2a2a2a; color: #eee; border: 1px solid #555; padding: 7px 14px; margin-right: 8px; cursor: pointer; border-radius: 4px; }
  button:hover { background: #383838; }
  .val { color: #9f9; font-variant-numeric: tabular-nums; }
  #status { margin-top: 10px; color: #8bf; min-height: 1.2em; white-space: pre-wrap; }
  .hint { color: #888; font-size: 12px; }
</style>
</head>
<body>
<header>
  Manual distortion tuner &mdash; drag <b>k1</b> until the magenta grid hugs the scene's straight edges,
  then <b>Save</b>. Toggle <b>Show rectified</b> to preview the correction.
</header>
<div id="wrap">
  <div id="stage">
    <img id="base" src="/frame.jpg"/>
    <canvas id="overlay"></canvas>
  </div>
  <div id="panel">
    <div class="row">
      <label for="model">model</label>
      <select id="model"></select>
    </div>
    <div class="row">
      <label for="k1">k1</label> <span class="val" id="k1val"></span><br/>
      <input type="range" id="k1"/>
      <div><input type="number" id="k1num" step="1e-9"/></div>
    </div>
    <div class="row" id="k2row">
      <label for="k2">k2 (radial)</label> <span class="val" id="k2val"></span><br/>
      <input type="range" id="k2"/>
      <div><input type="number" id="k2num" step="1e-15"/></div>
    </div>
    <div class="row">
      <label for="rot">rotate (deg)</label>
      <input type="number" id="rot" value="0" step="0.5"/>
      <span class="hint">contours only (visual align aid)</span>
    </div>
    <div class="row">
      <label><input type="checkbox" id="rect"/> Show rectified</label>
    </div>
    <div class="row">
      <button id="reset">Reset</button>
      <button id="save">Save</button>
    </div>
    <div id="status"></div>
    <div class="hint">Arrow keys on a slider nudge the value. Ctrl+C in the terminal stops the server.</div>
  </div>
</div>
<script>
let CFG = null;

function radialScale(r2, model, k1, k2) {
  if (model === 'pinhole') return 1.0;
  if (model === 'simple_radial') return 1.0 + k1 * r2;
  if (model === 'radial') return 1.0 + k1 * r2 + k2 * r2 * r2;
  if (model === 'simple_divisional') {
    const denom = 2.0 * k1 * r2;
    if (Math.abs(denom) < 1e-30) return 1.0;
    let inside = 1.0 - 4.0 * k1 * r2;
    if (inside < 0) inside = 0;
    return (1.0 - Math.sqrt(inside)) / denom;
  }
  return 1.0;
}

function drawOverlay() {
  const cv = document.getElementById('overlay');
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (document.getElementById('rect').checked) return;  // rectified view: no grid

  const model = document.getElementById('model').value;
  const k1 = parseFloat(document.getElementById('k1num').value);
  const k2 = parseFloat(document.getElementById('k2num').value);
  const rot = (parseFloat(document.getElementById('rot').value) || 0) * Math.PI / 180.0;
  const cr = Math.cos(rot), sr = Math.sin(rot);
  const s = CFG.scale, cx = CFG.cx, cy = CFG.cy;

  ctx.strokeStyle = 'rgba(255,0,255,0.9)';
  ctx.lineWidth = 2;
  for (const line of CFG.lines) {
    ctx.beginPath();
    for (let i = 0; i < line.length; i++) {
      const xu = line[i][0], yu = line[i][1];
      // rotate the (straight) line about the center, then distort. Radial
      // distortion is isotropic so r2 is unchanged by rotation.
      const xn0 = xu - cx, yn0 = yu - cy;
      const xn = xn0 * cr - yn0 * sr;
      const yn = xn0 * sr + yn0 * cr;
      const r2 = xn * xn + yn * yn;
      const sc = radialScale(r2, model, k1, k2);
      const xd = (xn * sc + cx) * s;
      const yd = (yn * sc + cy) * s;
      if (i === 0) ctx.moveTo(xd, yd); else ctx.lineTo(xd, yd);
    }
    ctx.stroke();
  }
  // center marker
  ctx.strokeStyle = 'rgba(255,0,0,0.9)';
  const mx = cx * s, my = cy * s;
  ctx.beginPath(); ctx.moveTo(mx - 8, my); ctx.lineTo(mx + 8, my);
  ctx.moveTo(mx, my - 8); ctx.lineTo(mx, my + 8); ctx.stroke();
}

function fmt(v) { return Number(v).toExponential(3); }

function syncLabels() {
  document.getElementById('k1val').textContent = fmt(document.getElementById('k1num').value);
  document.getElementById('k2val').textContent = fmt(document.getElementById('k2num').value);
  const isRadial = document.getElementById('model').value === 'radial';
  document.getElementById('k2row').style.opacity = isRadial ? '1' : '0.4';
}

let rectTimer = null;
function refreshView() {
  const base = document.getElementById('base');
  if (document.getElementById('rect').checked) {
    const model = document.getElementById('model').value;
    const k1 = document.getElementById('k1num').value;
    const k2 = document.getElementById('k2num').value;
    clearTimeout(rectTimer);
    rectTimer = setTimeout(() => {
      base.src = `/rectified.jpg?model=${model}&k1=${k1}&k2=${k2}&t=${Date.now()}`;
    }, 120);
  } else {
    if (!base.src.endsWith('/frame.jpg')) base.src = '/frame.jpg';
    drawOverlay();
  }
}

function bindPair(rangeId, numId) {
  const r = document.getElementById(rangeId), n = document.getElementById(numId);
  r.addEventListener('input', () => { n.value = r.value; syncLabels(); drawOverlay(); });
  r.addEventListener('change', () => { refreshView(); });
  n.addEventListener('input', () => { r.value = n.value; syncLabels(); refreshView(); });
}

async function save() {
  const model = document.getElementById('model').value;
  const k1 = document.getElementById('k1num').value;
  const k2 = document.getElementById('k2num').value;
  const rot = document.getElementById('rot').value || 0;
  const st = document.getElementById('status');
  st.textContent = 'Saving...';
  try {
    const res = await fetch(`/save?model=${model}&k1=${k1}&k2=${k2}&rot=${rot}`);
    const j = await res.json();
    const extra = (parseFloat(rot) ? ', overlay_rotated.png' : '');
    st.textContent = 'Saved bundle -> ' + j.dir +
      '\n(distortion.yaml, overlay.png, rectified.png' + extra + ')';
  } catch (e) { st.textContent = 'Save failed: ' + e; }
}

async function init() {
  CFG = await (await fetch('/config.json')).json();
  const img = document.getElementById('base');
  const cv = document.getElementById('overlay');

  function sizeCanvas() {
    // Backing store stays at display resolution; drawOverlay() draws in those
    // pixel coords. The canvas CSS size is 100% of #stage (which is sized by the
    // <img>), so the browser scales the canvas in lockstep with the image on any
    // resize. (Identity transform: setting cv.width already resets it.)
    cv.width = CFG.disp_w; cv.height = CFG.disp_h;
    drawOverlay();
  }
  img.onload = sizeCanvas;
  if (img.complete) sizeCanvas();
  window.addEventListener('resize', sizeCanvas);

  const sel = document.getElementById('model');
  for (const m of CFG.models) {
    const o = document.createElement('option'); o.value = m; o.textContent = m;
    if (m === CFG.model) o.selected = true; sel.appendChild(o);
  }
  const k1 = document.getElementById('k1'), k1n = document.getElementById('k1num');
  const k2 = document.getElementById('k2'), k2n = document.getElementById('k2num');
  const k1step = (CFG.k1_max - CFG.k1_min) / 1000.0;
  const k2step = (CFG.k2_max - CFG.k2_min) / 1000.0;
  k1.min = CFG.k1_min; k1.max = CFG.k1_max; k1.step = k1step; k1.value = CFG.k1; k1n.value = CFG.k1;
  k2.min = CFG.k2_min; k2.max = CFG.k2_max; k2.step = k2step; k2.value = CFG.k2; k2n.value = CFG.k2;

  bindPair('k1', 'k1num');
  bindPair('k2', 'k2num');
  sel.addEventListener('change', () => { syncLabels(); refreshView(); });
  document.getElementById('rect').addEventListener('change', refreshView);
  document.getElementById('rot').addEventListener('input', drawOverlay);
  document.getElementById('reset').addEventListener('click', () => {
    k1.value = 0; k1n.value = 0; k2.value = 0; k2n.value = 0;
    document.getElementById('rot').value = 0; syncLabels(); refreshView();
  });
  document.getElementById('save').addEventListener('click', save);

  syncLabels();
  drawOverlay();
}
init();
</script>
</body>
</html>
"""


def run_browser(
    img_bgr: np.ndarray,
    lines: List[np.ndarray],
    cx: float,
    cy: float,
    model_init: str,
    k1_min: float,
    k1_max: float,
    k1_init: float,
    k2_min: float,
    k2_max: float,
    k2_init: float,
    output_dir: Path,
    max_width: int,
    host: str = "127.0.0.1",
    port: int = 8000,
    open_browser: bool = True,
) -> None:
    """Serve the interactive tuner as a local browser UI (no X display needed).

    The overlay grid is redrawn client-side in JS (smooth), while rectified
    previews and saving are computed server-side with OpenCV.

    Args:
        img_bgr: Source (distorted) image, BGR, native resolution.
        lines: Undistorted grid polylines (native coords).
        cx: Principal point x (native pixels).
        cy: Principal point y (native pixels).
        model_init: Initial model.
        k1_min: k1 slider minimum.
        k1_max: k1 slider maximum.
        k1_init: Initial k1.
        k2_min: k2 slider minimum.
        k2_max: k2 slider maximum.
        k2_init: Initial k2.
        output_dir: Base directory for saved bundles.
        max_width: Max display width (downscale); k1/k2 stay native units.
        host: Bind host.
        port: Bind port.
        open_browser: Best-effort auto-open of the default browser.
    """
    h, w = img_bgr.shape[:2]
    s = _display_scale(w, h, max_width)
    disp = cv2.resize(img_bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s != 1.0 else img_bgr
    dh, dw = disp.shape[:2]
    ok, frame_jpg = cv2.imencode(".jpg", disp, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError("Failed to encode display frame.")
    frame_bytes = frame_jpg.tobytes()

    config = {
        "models": list(MODELS),
        "model": model_init,
        "k1": k1_init, "k1_min": k1_min, "k1_max": k1_max,
        "k2": k2_init, "k2_min": k2_min, "k2_max": k2_max,
        "cx": float(cx), "cy": float(cy),
        "scale": s, "disp_w": dw, "disp_h": dh,
        "native_w": w, "native_h": h,
        "lines": [np.round(ln, 1).tolist() for ln in lines],
    }
    config_bytes = json.dumps(config).encode("utf-8")

    def _clamp(val: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, val))

    def _query_params(qs: Dict[str, List[str]]) -> Tuple[str, float, float]:
        model = qs.get("model", [model_init])[0]
        if model not in MODELS:
            model = model_init
        k1 = float(qs.get("k1", ["0"])[0])
        k2 = float(qs.get("k2", ["0"])[0])
        return model, k1, k2

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # silence per-request noise
            pass

        def _send(self, body: bytes, content_type: str, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            parsed = urlparse(self.path)
            route = parsed.path
            qs = parse_qs(parsed.query)
            try:
                if route == "/" or route == "/index.html":
                    self._send(_BROWSER_HTML.encode("utf-8"), "text/html; charset=utf-8")
                elif route == "/config.json":
                    self._send(config_bytes, "application/json")
                elif route == "/frame.jpg":
                    self._send(frame_bytes, "image/jpeg")
                elif route == "/rectified.jpg":
                    model, k1, k2 = _query_params(qs)
                    k1 = _clamp(k1, k1_min, k1_max)
                    rect = rectify_image(img_bgr, cx, cy, model, k1, k2)
                    rect_disp = cv2.resize(rect, (dw, dh), interpolation=cv2.INTER_AREA)
                    ok2, buf = cv2.imencode(".jpg", rect_disp, [cv2.IMWRITE_JPEG_QUALITY, 90])
                    self._send(buf.tobytes(), "image/jpeg")
                elif route == "/save":
                    model, k1, k2 = _query_params(qs)
                    rot = float(qs.get("rot", ["0"])[0])
                    bundle = save_bundle(output_dir, img_bgr, lines, cx, cy, model, k1, k2,
                                         rotation_deg=rot)
                    self._send(json.dumps({"dir": str(bundle.resolve())}).encode(), "application/json")
                else:
                    self._send(b"not found", "text/plain", code=404)
            except Exception as exc:  # keep server alive on per-request errors
                logger.exception("Request error for %s", self.path)
                self._send(json.dumps({"error": str(exc)}).encode(), "application/json", code=500)

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    logger.info("Serving manual K1 tuner at %s", url)
    logger.info("Open that URL in a browser (Ctrl+C to stop).")
    if open_browser:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
    finally:
        server.server_close()


# --------------------------------------------------------------------------- #
# Interactive (matplotlib) mode
# --------------------------------------------------------------------------- #
def run_interactive(
    img_bgr: np.ndarray,
    lines: List[np.ndarray],
    cx: float,
    cy: float,
    model_init: str,
    k1_min: float,
    k1_max: float,
    k1_init: float,
    k2_min: float,
    k2_max: float,
    k2_init: float,
    output_dir: Path,
    max_width: int,
) -> None:
    """Launch the interactive UI (model radio buttons + k1/k2 sliders).

    The background image is drawn once; only the grid ``Line2D`` artists are
    updated on any change (efficient, no full-image redraw).
    """
    import matplotlib

    try:
        import matplotlib.pyplot as plt
        from matplotlib.widgets import Button, RadioButtons, Slider
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "matplotlib is required for interactive mode. "
            "Install it, or use --snapshot for headless rendering."
        ) from exc

    if matplotlib.get_backend().lower() == "agg":
        raise RuntimeError(
            "matplotlib is using a non-interactive backend (Agg); no display available. "
            "Use --snapshot for headless rendering instead."
        )

    h, w = img_bgr.shape[:2]
    s = _display_scale(w, h, max_width)
    disp = cv2.resize(img_bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s != 1.0 else img_bgr
    disp_rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
    dh, dw = disp_rgb.shape[:2]

    # Mutable current state (closured by callbacks).
    state = {"model": model_init, "k1": k1_init, "k2": k2_init}

    fig, ax = plt.subplots(figsize=(min(15, dw / 100 + 2), min(9.5, dh / 100 + 2.5)))
    plt.subplots_adjust(left=0.04, right=0.98, top=0.95, bottom=0.24)
    ax.imshow(disp_rgb, extent=(0, dw, dh, 0))
    ax.set_xlim(0, dw)
    ax.set_ylim(dh, 0)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.plot([cx * s], [cy * s], marker="+", color="red", markersize=12, mew=1.5)

    artists = []
    for ln in distort_lines(lines, cx, cy, model_init, k1_init, k2_init, scale=s):
        (artist,) = ax.plot(ln[:, 0], ln[:, 1], color="magenta", linewidth=2.0, alpha=0.9)
        artists.append(artist)

    def _title() -> str:
        m, k1, k2 = state["model"], state["k1"], state["k2"]
        txt = f"model={m}   k1={k1:.3e}"
        if m == "radial":
            txt += f"   k2={k2:.3e}"
        return txt + "   (overlay should hug straight scene edges)"

    title = ax.set_title(_title())

    def _redraw() -> None:
        new_lines = distort_lines(lines, cx, cy, state["model"], state["k1"], state["k2"], scale=s)
        for artist, ln in zip(artists, new_lines):
            artist.set_data(ln[:, 0], ln[:, 1])
        title.set_text(_title())
        fig.canvas.draw_idle()

    # Model selector (radio buttons).
    ax_radio = plt.axes([0.03, 0.02, 0.16, 0.17])
    ax_radio.set_title("model", fontsize=9)
    radio = RadioButtons(ax_radio, MODELS, active=MODELS.index(model_init))

    # Sliders.
    ax_k1 = plt.axes([0.30, 0.12, 0.46, 0.03])
    ax_k2 = plt.axes([0.30, 0.06, 0.46, 0.03])
    slider_k1 = Slider(ax_k1, "k1", k1_min, k1_max, valinit=k1_init, valfmt="%.3e")
    slider_k2 = Slider(ax_k2, "k2 (radial)", k2_min, k2_max, valinit=k2_init, valfmt="%.3e")

    # Buttons.
    ax_reset = plt.axes([0.80, 0.10, 0.08, 0.045])
    ax_save = plt.axes([0.89, 0.10, 0.08, 0.045])
    btn_reset = Button(ax_reset, "Reset")
    btn_save = Button(ax_save, "Save")

    def _on_model(label: str) -> None:
        state["model"] = label
        _redraw()

    def _on_k1(val: float) -> None:
        state["k1"] = float(val)
        _redraw()

    def _on_k2(val: float) -> None:
        state["k2"] = float(val)
        _redraw()

    def _reset(_event: object) -> None:
        slider_k1.set_val(0.0)
        slider_k2.set_val(0.0)

    def _save(_event: object) -> None:
        save_bundle(output_dir, img_bgr, lines, cx, cy,
                    state["model"], state["k1"], state["k2"])

    def _on_key(event: object) -> None:
        step_fine = (k1_max - k1_min) / 1000.0
        step_coarse = (k1_max - k1_min) / 100.0
        key = getattr(event, "key", None)
        cur = float(slider_k1.val)
        if key == "left":
            slider_k1.set_val(float(np.clip(cur - step_fine, k1_min, k1_max)))
        elif key == "right":
            slider_k1.set_val(float(np.clip(cur + step_fine, k1_min, k1_max)))
        elif key == "down":
            slider_k1.set_val(float(np.clip(cur - step_coarse, k1_min, k1_max)))
        elif key == "up":
            slider_k1.set_val(float(np.clip(cur + step_coarse, k1_min, k1_max)))
        elif key == "r":
            _reset(event)
        elif key == "s":
            _save(event)

    radio.on_clicked(_on_model)
    slider_k1.on_changed(_on_k1)
    slider_k2.on_changed(_on_k2)
    btn_reset.on_clicked(_reset)
    btn_save.on_clicked(_save)
    fig.canvas.mpl_connect("key_press_event", _on_key)

    logger.info("Interactive mode. Keys: left/right=fine k1, down/up=coarse k1, r=reset, s=save.")
    plt.show()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_float_list(text: str) -> List[float]:
    """Parse a comma-separated list of floats (for --snapshot)."""
    return [float(v.strip()) for v in text.split(",") if v.strip()]


def main() -> None:
    """Entry point for the manual distortion overlay tool."""
    parser = argparse.ArgumentParser(
        description="Manual distortion-overlay tuning tool (POC).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-i", "--input", type=str, required=True,
                        help="Path to an input image OR video (first valid frame is used).")
    parser.add_argument("-o", "--output", type=str, default="./k1_tuner_output",
                        help="Output directory for saved distortion.yaml / snapshots.")
    parser.add_argument("--frame-index", type=int, default=0,
                        help="Starting frame index when the input is a video.")
    parser.add_argument("--model", type=str, default="simple_radial", choices=MODELS,
                        help="Initial distortion model.")
    parser.add_argument("--k1-min", type=float, default=None,
                        help="Slider minimum for k1 (default: auto from image size).")
    parser.add_argument("--k1-max", type=float, default=None,
                        help="Slider maximum for k1 (default: auto from image size).")
    parser.add_argument("--k1-init", type=float, default=0.0,
                        help="Initial k1 (default 0 = no distortion).")
    parser.add_argument("--k2-min", type=float, default=None,
                        help="Slider minimum for k2, radial (default: auto from image size).")
    parser.add_argument("--k2-max", type=float, default=None,
                        help="Slider maximum for k2, radial (default: auto from image size).")
    parser.add_argument("--k2-init", type=float, default=0.0, help="Initial k2 (radial).")
    parser.add_argument("--grid", type=str, default="14x9",
                        help="Grid density as 'VxH' (vertical x horizontal line counts).")
    parser.add_argument("--max-width", type=int, default=1600,
                        help="Downscale display if image is wider than this (0 = no scaling). "
                             "k1/k2 stay in native-resolution units regardless.")
    parser.add_argument("--backend", type=str, default="browser", choices=("browser", "mpl"),
                        help="Interactive backend: 'browser' (headless-friendly, default) or "
                             "'mpl' (matplotlib window, needs an X display).")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Browser UI bind host.")
    parser.add_argument("--port", type=int, default=8000, help="Browser UI bind port.")
    parser.add_argument("--no-open", action="store_true",
                        help="Do not auto-open the browser (just print the URL).")
    parser.add_argument("--snapshot", type=str, default=None,
                        help="Headless mode: comma-separated k1 values to render as PNGs, e.g. "
                             "'-2e-5,-1e-5,0'. Uses --model and --k2-init. No GUI is opened.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)

    img_bgr = load_frame(input_path, frame_index=args.frame_index)
    height, width = img_bgr.shape[:2]

    # Principal point matches AMC (integer division), see lens_distortion.py.
    cx = width // 2
    cy = height // 2

    try:
        n_v_str, n_h_str = args.grid.lower().split("x")
        n_vertical, n_horizontal = int(n_v_str), int(n_h_str)
    except Exception:
        raise ValueError(f"Invalid --grid '{args.grid}'. Expected 'VxH', e.g. '14x9'.")

    lines = build_grid_lines(width, height, n_vertical, n_horizontal)

    # Derive sensible slider ranges from the image size unless overridden.
    k1_limit, k2_limit = auto_param_ranges(width, height)
    k1_min = args.k1_min if args.k1_min is not None else -k1_limit
    k1_max = args.k1_max if args.k1_max is not None else k1_limit
    k2_min = args.k2_min if args.k2_min is not None else -k2_limit
    k2_max = args.k2_max if args.k2_max is not None else k2_limit
    logger.info("k1 range: [%.3e, %.3e]   k2 range: [%.3e, %.3e]  (corner r^2=%.0f)",
                k1_min, k1_max, k2_min, k2_max, corner_r2(width, height))

    if args.snapshot is not None:
        k1_values = _parse_float_list(args.snapshot)
        if not k1_values:
            raise ValueError("--snapshot given but no valid k1 values parsed.")
        run_snapshot(img_bgr, lines, cx, cy, args.model, k1_values, args.k2_init,
                     output_dir, args.max_width)
        return

    if args.backend == "browser":
        run_browser(
            img_bgr=img_bgr,
            lines=lines,
            cx=cx,
            cy=cy,
            model_init=args.model,
            k1_min=k1_min,
            k1_max=k1_max,
            k1_init=args.k1_init,
            k2_min=k2_min,
            k2_max=k2_max,
            k2_init=args.k2_init,
            output_dir=output_dir,
            max_width=args.max_width,
            host=args.host,
            port=args.port,
            open_browser=not args.no_open,
        )
        return

    run_interactive(
        img_bgr=img_bgr,
        lines=lines,
        cx=cx,
        cy=cy,
        model_init=args.model,
        k1_min=k1_min,
        k1_max=k1_max,
        k1_init=args.k1_init,
        k2_min=k2_min,
        k2_max=k2_max,
        k2_init=args.k2_init,
        output_dir=output_dir,
        max_width=args.max_width,
    )


if __name__ == "__main__":
    main()
