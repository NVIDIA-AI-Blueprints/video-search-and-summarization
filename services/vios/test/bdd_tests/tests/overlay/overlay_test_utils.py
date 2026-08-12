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
Helpers for the VIOS overlay integration test (download / replay path).

Deterministic-timestamp strategy: we upload a synthetic clip at a chosen epoch T0,
so frame ``i`` has wall-clock epoch ``T0 + i*(1000/fps)``. The overlay metadata is
then generated at those exact epochs (see ``metadata_generator``) and served by the
fake ES, so it matches the recorded frame PTS within ``bbox_tolerance_ms`` -- no
live PTS scraping needed.

Verification uses ffmpeg only (no PyAV/numpy): a bbox overlay is drawn as a
rectangle *outline*, so we extract a frame to raw RGB and count/localize pixels
matching the overlay colour rather than sampling the box centre (which stays
background).
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Default overlay colour for the "Person" class (overlay_color_code in vst_config),
# RGB. Override per-deployment via config if needed.
PERSON_RGB = (118, 185, 0)

STORAGE_FILE = "/vst/api/v1/storage/file/{stream_id}"


def epoch_ms_to_iso_z(epoch_ms: int) -> str:
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{epoch_ms % 1000:03d}Z"


def make_solid_clip(path: Path, seconds: int, fps: int, width: int, height: int,
                    color: str = "blue") -> None:
    """Create a deterministic single-colour H.264 clip (background for the overlay)."""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c={color}:s={width}x{height}:d={seconds}:r={fps}",
        "-pix_fmt", "yuv420p", "-c:v", "libx264",
        "-profile:v", "baseline", "-preset", "ultrafast", "-movflags", "+faststart",
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, timeout=max(60, seconds + 30), check=True)
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced no clip")
    logger.info("Created overlay test clip %s (%dx%d, %dfps, %ds)", path.name, width, height, fps, seconds)


def upload_clip_at_epoch(api_base_url: str, file_path: Path, filename: str,
                         sensor_id: str, start_epoch_ms: int, verify_ssl: bool) -> Dict:
    """Upload a clip with an explicit start timestamp so frame epochs are known.

    Mirrors download_test_utils.upload_test_video but pins ``timestamp`` to T0
    instead of now(), which is what makes the metadata↔frame match deterministic.
    """
    url = f"{api_base_url}/vst/api/v1/storage/file/{filename}"
    params = {"timestamp": epoch_ms_to_iso_z(start_epoch_ms), "sensorId": sensor_id}
    with open(file_path, "rb") as f:
        content = f.read()
    logger.info("Uploading overlay clip %s at T0=%s (%d bytes)",
                filename, params["timestamp"], len(content))
    resp = requests.put(url, params=params, data=content,
                        headers={"Content-Type": "application/octet-stream"},
                        timeout=120, verify=verify_ssl)
    data = {}
    try:
        data = resp.json()
    except ValueError:
        pass
    return {
        "status_code": resp.status_code,
        "success": resp.status_code in (200, 201),
        "streamId": data.get("streamId"),
        "sensorId": data.get("sensorId"),
        "response": data,
    }


def overlay_configuration(show_all: bool = True, object_ids: Optional[List[str]] = None,
                          class_types: Optional[List[str]] = None,
                          debug: bool = True, thickness: int = 11) -> str:
    """Build the ``configuration`` JSON string enabling the bbox overlay on download.

    Uses the *new* overlay schema (a ``bbox`` object), which is what
    parseNewSchema/setOverlayOptsBasedOnJson expects. The older flat
    ``overlayBbox`` key is silently ignored by parseNewSchema and leaves
    ``m_enableBbox`` false (no metadata fetch, no boxes).
    """
    bbox: Dict[str, object] = {"showAll": "true" if show_all else "false",
                               # draw the object id (= burned-in frame number) on every box
                               "showObjId": "true", "objIdPosition": 0,
                               "objIdTextColor": "white", "objIdTextBGColor": "black"}
    if not show_all:
        if object_ids:
            bbox["objectId"] = object_ids      # array of ids
        if class_types:
            bbox["classType"] = class_types    # array of class names
    # debug + thickness are top-level overlay keys (siblings of bbox); default
    # thickness is 1px, so bump it for a bolder, easier-to-see/assert box.
    overlay: Dict[str, object] = {"bbox": bbox, "debug": "true" if debug else "false",
                                  "thickness": str(thickness)}
    return json.dumps({"overlay": overlay})


def download_overlay_clip(api_base_url: str, stream_id: str, route_key: str,
                          start_epoch_ms: int, end_epoch_ms: int, out_path: Path,
                          overlay: bool, verify_ssl: bool, timeout: int = 180) -> Dict:
    """Download the window with (or without) overlay, save to out_path.

    Uses transcode=full so the overlay is decoded and re-drawn (remux would not).
    Passing overlay=False downloads the identical window with no overlay for a
    differential comparison (control).
    """
    url = f"{api_base_url}{STORAGE_FILE.format(stream_id=stream_id)}"
    params = {
        "startTime": epoch_ms_to_iso_z(start_epoch_ms),
        "endTime": epoch_ms_to_iso_z(end_epoch_ms),
        "container": "mp4",
        "transcode": "full",
        "disableAudio": "true",
    }
    if overlay:
        params["configuration"] = overlay_configuration()
    headers = {"streamid": route_key} if route_key else None
    resp = requests.get(url, params=params, headers=headers, timeout=timeout,
                        verify=verify_ssl, stream=True)
    ok = resp.status_code == 200
    size = 0
    if ok:
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    size += len(chunk)
    logger.info("Download overlay=%s status=%d bytes=%d -> %s",
                overlay, resp.status_code, size, out_path.name)
    return {"status_code": resp.status_code, "success": ok and size > 0, "bytes": size}


def extract_frame_rgb(mp4_path: Path, at_seconds: float,
                      width: int, height: int) -> bytes:
    """Extract one frame at ``at_seconds`` as raw rgb24 bytes (len = w*h*3)."""
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{at_seconds:.3f}", "-i", str(mp4_path),
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, timeout=30, check=True).stdout
    expected = width * height * 3
    if len(out) < expected:
        raise RuntimeError(f"short frame: got {len(out)} bytes, expected {expected}")
    return out[:expected]


def count_color_pixels(rgb: bytes, width: int, height: int,
                       target: Tuple[int, int, int], tol: int = 60,
                       step: int = 1) -> Tuple[int, Optional[Tuple[int, int, int, int]]]:
    """Count pixels within L-inf ``tol`` of ``target``; return (count, bbox) where
    bbox = (minX,minY,maxX,maxY) of matches (None if no matches). Pure Python."""
    tr, tg, tb = target
    count = 0
    minx = miny = 10 ** 9
    maxx = maxy = -1
    row_bytes = width * 3
    for y in range(0, height, step):
        base = y * row_bytes
        for x in range(0, width, step):
            o = base + x * 3
            if abs(rgb[o] - tr) <= tol and abs(rgb[o + 1] - tg) <= tol and abs(rgb[o + 2] - tb) <= tol:
                count += 1
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
    bbox = (minx, miny, maxx, maxy) if maxx >= 0 else None
    return count, bbox


def diff_pixels(a: bytes, b: bytes, width: int, height: int,
                expect_box_px: Tuple[int, int, int, int], thresh: int = 40,
                margin: int = 20, step: int = 2):
    """Compare two same-size rgb24 frames. Returns (n_diff, bbox_of_diffs,
    frac_inside_expected_box). A pixel differs if the summed abs channel delta
    exceeds ``thresh`` (above re-encode noise)."""
    el, et, er, eb = expect_box_px
    el -= margin; et -= margin; er += margin; eb += margin
    row = width * 3
    n = 0
    inside = 0
    minx = miny = 10 ** 9
    maxx = maxy = -1
    limit = min(len(a), len(b))
    for y in range(0, height, step):
        base = y * row
        for x in range(0, width, step):
            o = base + x * 3
            if o + 3 > limit:
                break
            if abs(a[o] - b[o]) + abs(a[o + 1] - b[o + 1]) + abs(a[o + 2] - b[o + 2]) > thresh:
                n += 1
                if el <= x <= er and et <= y <= eb:
                    inside += 1
                minx = min(minx, x); maxx = max(maxx, x)
                miny = min(miny, y); maxy = max(maxy, y)
    bbox = (minx, miny, maxx, maxy) if maxx >= 0 else None
    frac_inside = (inside / n) if n else 0.0
    return n, bbox, frac_inside


def count_border_colorful(rgb: bytes, width: int, height: int,
                          box: Tuple[int, int, int, int], margin: int = 8,
                          sat_thresh: int = 45) -> Tuple[int, int]:
    """Count 'colorful' (saturated, max-min channel > sat_thresh) pixels on the
    box border annulus vs a same-scale off-box floor region. Used for the LIVE
    overlay assertion, which cannot use an overlay-off control (the scene moves
    between snapshots). A drawn box is a saturated outline on the (desaturated)
    floor at a known location."""
    el, et, er, eb = box

    def sat(o: int) -> bool:
        # The bbox outline for the Person class renders red; detect that
        # specifically rather than generic saturation, which colored people in
        # the live scene would trip. (sat_thresh gates the red dominance.)
        r, g, b = rgb[o], rgb[o + 1], rgb[o + 2]
        return (r - g) > sat_thresh and (r - b) > sat_thresh and r > 90

    border = 0
    for y in range(max(0, et - margin), min(height, eb + margin)):
        base = y * width * 3
        for x in range(max(0, el - margin), min(width, er + margin)):
            on_v = (abs(x - el) <= margin or abs(x - er) <= margin)
            on_h = (abs(y - et) <= margin or abs(y - eb) <= margin)
            if on_v or on_h:
                if sat(base + x * 3):
                    border += 1
    # Control: an off-box floor patch of similar area.
    fw, fh = (er - el), margin * 2
    fx, fy = max(0, el - 3 * margin - fw), et
    floor = 0
    for y in range(fy, min(height, fy + (eb - et))):
        base = y * width * 3
        for x in range(fx, min(width, fx + fw)):
            if sat(base + x * 3):
                floor += 1
    return border, floor


def assert_live_box_border(snapshot_rgb: bytes, width: int, height: int,
                           box: Tuple[int, int, int, int], min_border: int = 300,
                           sat_thresh: int = 45) -> Dict:
    """Assert a colored box outline is drawn at ``box`` in a live overlay snapshot,
    by direct detection (border colorful pixels), robust to scene motion."""
    border, floor = count_border_colorful(snapshot_rgb, width, height, box, sat_thresh=sat_thresh)
    logger.info("live box border colorful px=%d, off-box floor px=%d", border, floor)
    assert border >= min_border, (
        f"no box outline at expected border {box}: colorful px {border} < {min_border}"
    )
    assert border > floor * 5 + min_border, (
        f"box border not distinct from floor: border={border} floor={floor}"
    )
    return {"border": border, "floor": floor}


def assert_overlay_box(overlay_mp4: Path, control_mp4: Path,
                       sample_seconds: List[float], width: int, height: int,
                       expect_box_px: Tuple[int, int, int, int],
                       min_diff_pixels: int = 120, localize_frac: float = 0.7,
                       diff_thresh: int = 40, require_localized: bool = True) -> Dict:
    """Assert the overlay draws a box at the expected region.

    Deployment-agnostic: instead of matching a hard-coded class colour (which
    depends on ``overlay_color_code`` and blends over the video), it asserts that
    the overlay clip *differs* from the overlay-off control in a bounded region
    localized to the expected box -- i.e. a box was drawn there and nowhere else.

    ``require_localized=False`` only checks that enough difference lands *inside*
    the box (a box was drawn there) without requiring the rest of the frame to be
    clean -- needed when debug mode is on, which draws frame-wide info text.
    """
    results = []
    for t in sample_seconds:
        a = extract_frame_rgb(overlay_mp4, t, width, height)
        b = extract_frame_rgb(control_mp4, t, width, height)
        n, bbox, frac = diff_pixels(a, b, width, height, expect_box_px, thresh=diff_thresh)
        results.append({"t": t, "n_diff": n, "bbox": bbox, "frac_in_box": frac,
                        "n_in_box": int(n * frac)})
        logger.info("t=%.2fs overlay-vs-control diff=%d frac_in_box=%.2f n_in_box=%d bbox=%s",
                    t, n, frac, int(n * frac), bbox)

    if require_localized:
        best = max(results, key=lambda r: r["n_diff"])
        assert best["n_diff"] >= min_diff_pixels, (
            f"no overlay box drawn: max diff pixels {best['n_diff']} < {min_diff_pixels} "
            f"(samples={results})")
        assert best["frac_in_box"] >= localize_frac, (
            f"overlay differences not localized to expected box {expect_box_px}: "
            f"only {best['frac_in_box']:.2f} of {best['n_diff']} diffs inside "
            f"(bbox={best['bbox']}) -- likely global re-encode noise, not a box")
    else:
        best = max(results, key=lambda r: r["n_in_box"])
        assert best["n_in_box"] >= min_diff_pixels, (
            f"no overlay box drawn inside {expect_box_px}: max in-box diff "
            f"{best['n_in_box']} < {min_diff_pixels} (samples={results})")
    return {"samples": results, "max_diff": best["n_diff"], "frac_in_box": best["frac_in_box"]}


def assert_overlay_box_sustained(overlay_mp4: Path, control_mp4: Path,
                                 sample_seconds: List[float], width: int, height: int,
                                 expect_box_px: Tuple[int, int, int, int],
                                 min_diff_pixels: int = 120, diff_thresh: int = 40,
                                 min_present_frac: float = 0.9) -> Dict:
    """Assert the overlay box is present at (nearly) EVERY sample across a long clip.

    Unlike ``assert_overlay_box`` (which passes if ANY single sample has a box, so it only
    proves the box was drawn *somewhere*), this requires the box to be SUSTAINED and reports
    the exact timestamps where it is absent. Catches metadata dropout over time -- the box
    being proper for the first ~10-15s then flickering / vanishing once the server stops
    delivering metadata for the rest of the clip (e.g. it fetches only the first ES batch,
    ``video_metadata_query_batch_size_num_frames``). Sample the whole clip, every few seconds."""
    results = []
    for t in sample_seconds:
        a = extract_frame_rgb(overlay_mp4, t, width, height)
        b = extract_frame_rgb(control_mp4, t, width, height)
        n, bbox, frac = diff_pixels(a, b, width, height, expect_box_px, thresh=diff_thresh)
        n_in_box = int(n * frac)
        present = n_in_box >= min_diff_pixels
        results.append({"t": round(t, 1), "n_in_box": n_in_box, "present": present})
        logger.info("t=%.1fs n_in_box=%d box=%s", t, n_in_box, "yes" if present else "NO")
    have = [r for r in results if r["present"]]
    absent = [r["t"] for r in results if not r["present"]]
    present_frac = (len(have) / len(results)) if results else 0.0
    assert present_frac >= min_present_frac, (
        f"overlay box NOT sustained: present in {len(have)}/{len(results)} samples "
        f"({present_frac:.0%}) < required {min_present_frac:.0%}; box absent at t={absent}s "
        f"-- flickers / drops out over the clip (samples={results})")
    return {"present_frac": present_frac, "absent_seconds": absent, "samples": results}
