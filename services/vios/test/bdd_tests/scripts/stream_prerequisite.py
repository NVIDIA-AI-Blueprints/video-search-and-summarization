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

"""BDD session prerequisite: make sure NVStreamer has at least one stream.

The sample clips are baked into the BDD test image under ``/app/test_videos``
(see the project Dockerfile). When NVStreamer reports no file-backed sensors,
this module uploads those clips to NVStreamer (PUT v2) and then triggers a VST
sensor scan so VIOS imports the resulting RTSP streams.

It is best-effort: a missing baked directory, an unreachable NVStreamer, or a
failed scan only logs a warning -- it never raises into the session. Suites
that don't need live streams (e.g. file-upload, unit tests) still run.
"""
import logging
import os
import time
from pathlib import Path
from typing import List

import requests

logger = logging.getLogger(__name__)

# ISO 8601 UTC timestamp stamped on uploaded clips when none is configured.
DEFAULT_UPLOAD_TIMESTAMP = "2025-01-01T00:00:00.000Z"
# Supported test-clip containers (codec is validated server-side on upload).
VIDEO_SUFFIXES = (".mp4", ".mkv", ".ts")
# VST endpoints the first BDD tests (live/replay WebRTC) read -- the prerequisite
# waits for these to be populated so freshly-seeded streams have warmed up.
LIVE_STREAMS_PATH = "/vst/api/v1/live/streams"
REPLAY_STREAMS_PATH = "/vst/api/v1/replay/streams"
# Default readiness timeouts (seconds). Recordings (replay) take longer to appear
# than live streams. Overridable via the `nvstreamer` config block.
DEFAULT_LIVE_READY_TIMEOUT_SEC = 150
DEFAULT_REPLAY_READY_TIMEOUT_SEC = 300
# Candidate locations for the baked clips, in priority order.
_DEFAULT_BAKED_DIRS = ("/app/test_videos",)


def baked_videos_dir() -> Path:
    """Resolve the directory holding the baked sample clips.

    Priority: ``TEST_VIDEOS_DIR`` env var, then ``/app/test_videos`` (the path
    baked into the container), then the repo copy under ``test/bdd_tests``.
    """
    env_dir = os.environ.get("TEST_VIDEOS_DIR")
    candidates = []
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend(Path(d) for d in _DEFAULT_BAKED_DIRS)
    # Repo-relative fallback for local (non-container) runs.
    candidates.append(Path(__file__).resolve().parent.parent / "test_videos")
    for path in candidates:
        if path.is_dir() and any(
            p.suffix.lower() in VIDEO_SUFFIXES for p in path.iterdir()
        ):
            return path
    # Return the first preference so callers can log a useful "not found" path.
    return candidates[0]


def _list_videos(directory: Path) -> List[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES
    )


def nvstreamer_endpoints(config: dict) -> List[str]:
    """Resolve the NVStreamer REST endpoint(s) to seed.

    Priority: ``NVSTREAMER_ENDPOINTS`` env var (comma-separated full URLs),
    then the ``nvstreamer`` block in config.json (host + port_base + instances),
    then a single default of ``http://localhost:31000`` (the BDD container is
    host-networked, so NVStreamer's host ports are reachable as localhost).
    """
    env_eps = os.environ.get("NVSTREAMER_ENDPOINTS")
    if env_eps:
        return [e.strip().rstrip("/") for e in env_eps.split(",") if e.strip()]

    ns = config.get("nvstreamer", {}) if isinstance(config, dict) else {}
    host = ns.get("host", "localhost")
    port_base = int(ns.get("port_base", 31000))
    instances = int(ns.get("instances", 1))
    return [f"http://{host}:{port_base + i}" for i in range(max(instances, 1))]


def nvstreamer_stream_count(endpoint: str, timeout: int = 10) -> int:
    """Return the number of sensors NVStreamer is currently serving.

    Raises on transport error so the caller can treat the endpoint as
    unreachable and skip it.
    """
    resp = requests.get(
        f"{endpoint}/vst/api/v1/sensor/list", timeout=timeout
    )
    resp.raise_for_status()
    data = resp.json()
    # NVStreamer returns either a bare list or {"sensors":[...]} depending on
    # version -- handle both.
    if isinstance(data, dict):
        data = data.get("sensors", data.get("data", []))
    return len(data) if isinstance(data, list) else 0


def upload_video(endpoint: str, video: Path, timestamp: str, timeout: int = 120) -> bool:
    """Upload one clip to NVStreamer via PUT v2. Returns True on success."""
    payload = video.read_bytes()
    # Filenames must not contain whitespace (NVStreamer rejects with 400).
    name = video.name.replace(" ", "_")
    url = f"{endpoint}/vst/api/v1/storage/file/{name}?timestamp={timestamp}"
    resp = requests.put(
        url,
        data=payload,
        headers={"Content-Type": "application/octet-stream"},
        timeout=timeout,
    )
    if resp.status_code == 200:
        logger.info("Uploaded %s to %s", name, endpoint)
        return True
    # 409 = already present; treat as success (the stream exists).
    if resp.status_code == 409:
        logger.info("%s already present on %s (409)", name, endpoint)
        return True
    logger.warning(
        "Upload of %s to %s failed: HTTP %s %s",
        name, endpoint, resp.status_code, resp.text[:200],
    )
    return False


def vst_scan(base_url: str, verify_ssl: bool = False, timeout: int = 60) -> bool:
    """Trigger a VST sensor scan so VIOS imports NVStreamer RTSP streams."""
    url = f"{base_url}/vst/api/v1/sensor/scan"
    resp = requests.post(url, timeout=timeout, verify=verify_ssl)
    if resp.status_code == 200:
        logger.info("VST sensor scan triggered (%s)", url)
        return True
    logger.warning("VST sensor scan failed: HTTP %s %s", resp.status_code, resp.text[:200])
    return False


def _endpoint_list_count(base_url: str, path: str, verify_ssl: bool = False, timeout: int = 15) -> int:
    """Count the list items returned by a VST list endpoint (sensor/live/replay)."""
    resp = requests.get(f"{base_url}{path}", timeout=timeout, verify=verify_ssl)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        data = data.get("streams", data.get("sensors", data.get("data", [])))
    return len(data) if isinstance(data, list) else 0


def _wait_for_count(base_url: str, path: str, verify_ssl: bool,
                    timeout_s: int, interval_s: int, label: str) -> bool:
    """Poll a VST list endpoint until it returns >0 items or the timeout elapses."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            n = _endpoint_list_count(base_url, path, verify_ssl)
            if n > 0:
                logger.info("%s ready: %d", label, n)
                return True
        except Exception as exc:
            # Transient while the endpoint warms up; keep polling but surface it.
            logger.debug("%s readiness check failed: %s", label, exc)
        time.sleep(interval_s)
    logger.warning("%s not ready after %ds", label, timeout_s)
    return False


def ensure_streams(base_url: str, verify_ssl: bool, config: dict) -> dict:
    """Ensure NVStreamer has streams, seeding the baked clips if it is empty.

    Returns a summary dict: ``{"seeded": bool, "uploaded": int, "scanned": bool}``.
    Best-effort -- logs and returns instead of raising on any failure.
    """
    summary = {"seeded": False, "uploaded": 0, "scanned": False}
    timestamp = config.get("nvstreamer", {}).get(
        "upload_timestamp", DEFAULT_UPLOAD_TIMESTAMP
    )

    endpoints = nvstreamer_endpoints(config)
    videos = _list_videos(baked_videos_dir())

    seeded_any = False
    reachable_any = False
    for endpoint in endpoints:
        try:
            count = nvstreamer_stream_count(endpoint)
        except Exception as exc:  # unreachable / not deployed
            logger.info("NVStreamer %s not reachable (%s) -- skipping", endpoint, exc)
            continue
        reachable_any = True
        if count > 0:
            logger.info("NVStreamer %s already has %d stream(s)", endpoint, count)
            continue
        if not videos:
            logger.warning(
                "NVStreamer %s has no streams but no baked clips were found in %s",
                endpoint, baked_videos_dir(),
            )
            continue
        logger.info("NVStreamer %s has no streams -- uploading %d baked clip(s)", endpoint, len(videos))
        for video in videos:
            try:
                if upload_video(endpoint, video, timestamp):
                    summary["uploaded"] += 1
                    seeded_any = True
            except Exception as exc:
                logger.warning("Upload of %s to %s raised: %s", video.name, endpoint, exc)

    if not reachable_any:
        logger.warning("No NVStreamer endpoint reachable %s -- stream prerequisite skipped", endpoints)
        return summary

    if seeded_any:
        summary["seeded"] = True
        # Give NVStreamer's discovery cycle a moment to register the files,
        # then ask VST to scan and import the RTSP streams.
        time.sleep(5)
        try:
            summary["scanned"] = vst_scan(base_url, verify_ssl)
        except Exception as exc:
            logger.warning("VST scan raised: %s", exc)

        ns = config.get("nvstreamer", {}) if isinstance(config, dict) else {}
        live_timeout = int(ns.get("live_ready_timeout_sec", DEFAULT_LIVE_READY_TIMEOUT_SEC))
        replay_timeout = int(ns.get("replay_ready_timeout_sec", DEFAULT_REPLAY_READY_TIMEOUT_SEC))

        # Wait for the streams to actually warm up before tests run. Seeding at
        # test-time (vs at deploy-time) means the first stream-dependent tests
        # (live/replay WebRTC) would otherwise race the pipeline coming up:
        #   1. sensors imported into VST,
        #   2. live streams reachable (/live/streams populated),
        #   3. recordings replayable (/replay/streams populated).
        _wait_for_count(base_url, "/vst/api/v1/sensor/list", verify_ssl, 60, 5, "VST sensors")
        summary["live_ready"] = _wait_for_count(
            base_url, LIVE_STREAMS_PATH, verify_ssl, live_timeout, 5, "VST live streams"
        )
        summary["replay_ready"] = _wait_for_count(
            base_url, REPLAY_STREAMS_PATH, verify_ssl, replay_timeout, 10, "VST replay streams"
        )

    return summary
