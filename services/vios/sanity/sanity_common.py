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
Shared context + result model for the VIOS+NVStreamer sanity harness.

Sanity is the orchestrated, evidence-producing run: it deploys/uses a running
stack, drives each use-case (download, picture, overlay, webrtc, video-wall),
captures a snapshot/video per use-case, and emits a PDF with the snapshots and
their http links. It REUSES the verbs from the bdd_tests overlay lib (one-way
dependency: sanity -> bdd_tests), and never lives inside the bdd suite.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger("sanity")


def _default_host_ip() -> str:
    """Host IP reachable from the docker containers and from a browser opening the
    evidence links. Override with VIOS_SANITY_HOST_IP; else auto-detect the primary
    outbound interface; else localhost."""
    v = os.environ.get("VIOS_SANITY_HOST_IP")
    if v:
        return v
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"

# Repo root = .../video-search-and-summarization ; wire the bdd_tests overlay
# lib onto the path so sanity can reuse its verbs.
REPO_ROOT = Path(__file__).resolve().parents[3]
BDD_ROOT = REPO_ROOT / "services/vios/test/bdd_tests"
sys.path.insert(0, str(BDD_ROOT))


@dataclass
class SanityContext:
    base_url: str = "http://localhost:30888"
    nvstreamer_url: str = "http://localhost:31000"
    host_ip: str = field(default_factory=_default_host_ip)
    stream_id: str = "warehouse_sample"
    # metadata backends (a deployment/sanity provides these)
    es_host: str = "0.0.0.0"
    es_port: int = 19200
    es_index: str = "mdx-bev-test"
    broker: str = "redis"            # live consumer type: redis|kafka
    redis_host: str = "localhost"
    redis_port: int = 6379
    kafka_brokers: str = "172.17.0.1:9092"
    topic: str = "vst-overlay-test"
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    verify_ssl: bool = False
    # evidence sink: files copied here are served by the file server (see sanity/README.md).
    # Override the location/URL with VIOS_SANITY_SHARE_DIR / VIOS_SANITY_FILE_SERVER /
    # VIOS_SANITY_FILE_SERVER_PORT; file_server_base defaults to http://<host_ip>:18080.
    share_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("VIOS_SANITY_SHARE_DIR", "/tmp/vios_sanity/share")))
    file_server_base: str = ""
    out_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("VIOS_SANITY_OUT_DIR", "/tmp/vios_sanity")))
    # populated by provisioning (provision.py): the uniform NVStreamer RTSP copies
    # and the VST file-backed sensor derived from the single input clip.
    provisioned_streams: list = field(default_factory=list)
    file_sensor: Optional[str] = None
    # per-sensor source resolution {sensor_id: (width, height)} so overlay metadata
    # pixel coords match the actual stream (handles 640x480 / 1080p / 4K correctly).
    stream_res: dict = field(default_factory=dict)
    # {VIOS sensorId -> descriptive name} for display (RTSP sensors are UUIDs).
    stream_names: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.host_ip:
            self.host_ip = _default_host_ip()
        if not self.file_server_base:
            port = os.environ.get("VIOS_SANITY_FILE_SERVER_PORT", "18080")
            self.file_server_base = (os.environ.get("VIOS_SANITY_FILE_SERVER")
                                     or f"http://{self.host_ip}:{port}")
        self.share_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def publish(self, local_path: Path, name: Optional[str] = None) -> str:
        """Copy an artifact into the served share dir; return its http link."""
        name = name or local_path.name
        dst = self.share_dir / name
        shutil.copy(local_path, dst)
        return f"{self.file_server_base}/{name}"


@dataclass
class UseCaseResult:
    name: str
    status: str = "SKIP"            # PASS | FAIL | SKIP
    detail: str = ""
    duration_s: float = 0.0
    image: Optional[Path] = None    # a representative snapshot (embedded in PDF)
    links: List[str] = field(default_factory=list)   # http links (video/image)
    plan: str = ""                  # which plan produced this result (for the PDF)
    group: str = ""                 # optional grouping label (download/picture/webrtc/perf)
    metrics: dict = field(default_factory=dict)      # perf numbers, rendered as a table
    evidence: bool = False          # include this result in the PDF evidence gallery
    request: dict = field(default_factory=dict)      # exact REST/UI request for failure triage
    started_at: str = ""                             # UTC, ISO 8601 with Z suffix
    finished_at: str = ""                            # UTC, ISO 8601 with Z suffix


def run_usecase(name: str, fn: Callable[[SanityContext], UseCaseResult],
                ctx: SanityContext) -> UseCaseResult:
    """Execute one use-case, catching failures into a FAIL result."""
    started = datetime.now(timezone.utc)
    t0 = time.time()
    ctx.active_request = {}
    logger.info("=== use-case: %s ===", name)
    try:
        res = fn(ctx)
    except Exception as e:  # noqa: BLE001 - sanity must never abort on one case
        logger.exception("use-case %s crashed", name)
        res = UseCaseResult(name=name, status="FAIL", detail=f"exception: {e}",
                            request=dict(ctx.active_request or {}))
    finished = datetime.now(timezone.utc)
    res.name = name
    res.duration_s = time.time() - t0
    res.started_at = started.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    res.finished_at = finished.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    logger.info("--- %s: %s (%.1fs) ---", name, res.status, res.duration_s)
    return res
