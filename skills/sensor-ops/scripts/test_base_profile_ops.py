#!/usr/bin/env python3
"""End-to-end probe for the sensor-ops skill on a deployed VSS base profile.

Reads the query/check definitions from `base_profile_ops.json` (same dir
or overridable via --spec). For each query block, runs the VIOS API
calls the skill would make and validates every check against the live
response.

Assumes VSS base is already deployed on the target host (the coordinator
handles deployment — Harbor doesn't chain tasks).

Exits 0 if every check passes, non-zero otherwise. Prints per-check
pass/fail for the generator's shell verifier to tally.

Usage:
    python3 test_base_profile_ops.py \
        --vst-url http://localhost:30888 \
        --video-path /path/to/warehouse.mp4 \
        [--spec base_profile_ops.json] \
        [--brev-link-prefix 77770] \
        [--brev-env-id $BREV_ENV_ID]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# ----------------------------------------------------------------------
# HTTP helpers (stdlib only — no requests)
# ----------------------------------------------------------------------

def http(method: str, url: str, *, data: bytes | None = None,
         headers: dict | None = None, timeout: int = 60) -> tuple[int, dict, bytes]:
    """Return (status, headers_dict, body_bytes) or raise on transport error."""
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() if e.fp else b""


def get_json(url: str, timeout: int = 30) -> tuple[int, object]:
    code, _, body = http("GET", url, timeout=timeout)
    if code >= 400:
        return code, None
    try:
        return code, json.loads(body)
    except Exception:
        return code, None


def head(url: str, timeout: int = 30) -> tuple[int, dict]:
    """HEAD request; returns (status, headers). Falls back to GET if HEAD
    isn't supported by the endpoint (some CDNs 405 on HEAD)."""
    code, h, _ = http("HEAD", url, timeout=timeout)
    if code == 405:
        code, h, _ = http("GET", url, timeout=timeout)
    return code, h


# ----------------------------------------------------------------------
# Upload + lookup
# ----------------------------------------------------------------------

def upload_video(vst_url: str, video_path: str, timestamp: str,
                 sensor_id: str | None = None) -> dict:
    """PUT a local mp4 to /vst/api/v1/storage/file/<filename>."""
    fname = os.path.basename(video_path)
    url = f"{vst_url}/vst/api/v1/storage/file/{fname}?timestamp={timestamp}"
    if sensor_id:
        url += f"&sensorId={sensor_id}"
    size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        data = f.read()
    code, _, body = http(
        "PUT", url, data=data, timeout=600,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(size),
        },
    )
    if code >= 400:
        raise RuntimeError(f"upload failed: HTTP {code}: {body[:300]!r}")
    return json.loads(body)


def find_sensor(vst_url: str, name_stem: str) -> dict | None:
    code, data = get_json(f"{vst_url}/vst/api/v1/sensor/list")
    if code != 200 or not isinstance(data, list):
        return None
    for s in data:
        if (s.get("name") or "").startswith(name_stem):
            return s
    return None


def list_streams(vst_url: str, sensor_id: str) -> list:
    code, data = get_json(f"{vst_url}/vst/api/v1/sensor/{sensor_id}/streams")
    return data if (code == 200 and isinstance(data, list)) else []


def get_timeline(vst_url: str, stream_id: str) -> list:
    code, data = get_json(f"{vst_url}/vst/api/v1/storage/{stream_id}/timelines")
    if code != 200:
        return []
    # Response is typically list[{startTime, endTime}] OR {<streamId>: [...]}
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get(stream_id, [])
    return []


def get_snapshot_url(vst_url: str, stream_id: str, start_time: str) -> dict | None:
    url = (f"{vst_url}/vst/api/v1/replay/stream/{stream_id}/picture/url"
           f"?startTime={start_time}")
    code, _, body = http("GET", url, headers={"streamId": stream_id})
    if code >= 400:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def get_clip_url(vst_url: str, stream_id: str, start_time: str,
                 end_time: str) -> dict | None:
    url = (f"{vst_url}/vst/api/v1/storage/file/{stream_id}/url"
           f"?startTime={start_time}&endTime={end_time}"
           f"&container=mp4&disableAudio=true")
    code, data = get_json(url, timeout=60)
    return data if code == 200 else None


# ----------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------

_BREV_URL_RE = re.compile(r"^https://[A-Za-z0-9]+-[A-Za-z0-9\-]+\.brevlab\.com/")


def is_brev_link(url: str, link_prefix: str | None, env_id: str | None) -> bool:
    if not url:
        return False
    if not _BREV_URL_RE.match(url):
        return False
    if link_prefix and env_id:
        expected_host = f"{link_prefix}-{env_id}.brevlab.com"
        return expected_host in url
    return True


def check_result(ok: bool, label: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    return ok


# ----------------------------------------------------------------------
# Main orchestration
# ----------------------------------------------------------------------

def run(args) -> int:
    vst_url = args.vst_url.rstrip("/")
    link_prefix = args.brev_link_prefix or os.environ.get("BREV_LINK_PREFIX")
    env_id = args.brev_env_id or os.environ.get("BREV_ENV_ID")
    enforce_brev = bool(link_prefix and env_id)

    total = 0
    passed = 0

    def tally(ok: bool, label: str) -> None:
        nonlocal total, passed
        total += 1
        if check_result(ok, label):
            passed += 1

    # -- Pre-flight: VST reachable --
    print("=== VST availability ===")
    code, _ = get_json(f"{vst_url}/vst/api/v1/sensor/version", timeout=10)
    tally(code == 200, f"VST /sensor/version reachable (HTTP {code})")
    if code != 200:
        print(f"\nResults: {passed} passed, {total - passed} failed (of {total})")
        return 1

    # -- Query 1: upload --
    print("\n=== Query 1: upload ===")
    stem = os.path.splitext(os.path.basename(args.video_path))[0]
    try:
        resp = upload_video(vst_url, args.video_path, args.timestamp)
    except Exception as e:
        tally(False, f"upload raised: {e}")
        print(f"\nResults: {passed} passed, {total - passed} failed (of {total})")
        return 1
    tally(bool(resp.get("sensorId")) and bool(resp.get("streamId")),
          "upload response has sensorId and streamId")

    sensor_id = resp["sensorId"]
    stream_id = resp["streamId"]

    # Wait briefly for the sensor to register
    time.sleep(2)
    sensor = find_sensor(vst_url, stem)
    tally(sensor is not None,
          f"/sensor/list contains sensor whose name starts with '{stem}'")

    streams = list_streams(vst_url, sensor_id)
    main = next((s for s in streams if s.get("isMain")), streams[0] if streams else None)
    tally(main is not None, "/sensor/<id>/streams returns a main stream")
    stream_url = (main or {}).get("url") or ""
    tally(not stream_url.startswith("rtsp://") and stream_url != "",
          f"main stream url is a local file path (got: {stream_url[:80]})")

    # -- Query 2: snapshot --
    print("\n=== Query 2: snapshot URL at 5 s ===")
    snap_start = args.timestamp.replace("00.000Z", "05.000Z")
    snap = get_snapshot_url(vst_url, stream_id, snap_start)
    tally(bool(snap and snap.get("imageUrl")),
          "/replay/.../picture/url returns non-empty imageUrl")
    image_url = (snap or {}).get("imageUrl") or ""
    if enforce_brev:
        tally(is_brev_link(image_url, link_prefix, env_id),
              "imageUrl matches Brev secure-link pattern "
              f"https://{link_prefix}-{env_id}.brevlab.com/...")
    else:
        tally(True, "Brev-link check skipped (no BREV_ENV_ID/BREV_LINK_PREFIX set)")
    if image_url:
        code, hdrs = head(image_url)
        tally(code == 200, f"HEAD <imageUrl> -> HTTP {code}")
        ct = hdrs.get("Content-Type", "")
        tally(ct.startswith("image/"), f"Content-Type starts with image/ (got {ct!r})")
        try:
            clen = int(hdrs.get("Content-Length", "0") or 0)
        except ValueError:
            clen = 0
        tally(clen > 2000, f"Content-Length > 2000 (got {clen})")
    else:
        tally(False, "imageUrl empty — skipping HEAD/CT/Length checks")
        tally(False, "")
        tally(False, "")

    # -- Query 3: video clip (3s → 5s) --
    print("\n=== Query 3: clip URL 3s -> 5s ===")
    clip_start = args.timestamp.replace("00.000Z", "03.000Z")
    clip_end = args.timestamp.replace("00.000Z", "05.000Z")
    clip = get_clip_url(vst_url, stream_id, clip_start, clip_end)
    tally(bool(clip and clip.get("videoUrl")),
          "/storage/file/.../url returns non-empty videoUrl")
    video_url = (clip or {}).get("videoUrl") or ""
    if enforce_brev:
        tally(is_brev_link(video_url, link_prefix, env_id),
              f"videoUrl matches Brev secure-link pattern "
              f"https://{link_prefix}-{env_id}.brevlab.com/...")
    else:
        tally(True, "Brev-link check skipped (no BREV_ENV_ID/BREV_LINK_PREFIX set)")
    if video_url:
        code, hdrs = head(video_url)
        tally(code == 200, f"HEAD <videoUrl> -> HTTP {code}")
        ct = hdrs.get("Content-Type", "")
        tally(ct.startswith("video/"), f"Content-Type starts with video/ (got {ct!r})")
        try:
            clen = int(hdrs.get("Content-Length", "0") or 0)
        except ValueError:
            clen = 0
        tally(clen > 10000, f"Content-Length > 10000 (got {clen})")
    else:
        tally(False, "videoUrl empty — skipping HEAD/CT/Length checks")
        tally(False, "")
        tally(False, "")

    # expiry sanity
    tally(bool(clip and clip.get("expiryISO")),
          "clip response has an expiryISO field")

    print(f"\nResults: {passed} passed, {total - passed} failed (of {total})")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vst-url", default=os.environ.get("VST_URL", "http://localhost:30888"))
    ap.add_argument("--video-path", required=True)
    ap.add_argument("--timestamp", default="2025-01-01T00:00:00.000Z")
    ap.add_argument("--spec", default=str(Path(__file__).resolve().parents[1]
                                         / "eval" / "base_profile_ops.json"))
    ap.add_argument("--brev-link-prefix")
    ap.add_argument("--brev-env-id")
    args = ap.parse_args()
    # `spec` is currently advisory — checks are hardcoded to mirror the JSON.
    # It's passed so future iterations can drive checks from the JSON.
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
