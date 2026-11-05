# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Bring live streams into the alerts pipeline.

pre_flight stages one video file per stream for NVStreamer, which serves each
as an RTSP stream. A stream only enters the pipeline (VST -> RTVI-CV ->
Behavior Analytics -> Alert Bridge) once it is registered as a VST sensor, so
the benchmark controls the active stream count by registering sensors.

Stream levels are swept in ascending order and sensors are only ever added,
never removed, between levels. Same approach as the alerts eval framework
(ci-vss-oss eval/scripts/tests/register_alert_streams.py).
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Set

import requests

logger = logging.getLogger(__name__)

STREAM_PREFIX = "perf_stream_"


def discover_streams(nvstreamer_url: str, expected: int, timeout_s: int = 600) -> Dict[str, str]:
    """Return {name: rtsp_url} for the staged perf streams, sorted by name."""
    deadline = time.time() + timeout_s
    streams: Dict[str, str] = {}
    while time.time() < deadline:
        try:
            resp = requests.get(f"{nvstreamer_url}/api/v1/live/streams", timeout=10)
            resp.raise_for_status()
            for group in resp.json():
                for _key, values in group.items():
                    for stream in values:
                        name, url = stream.get("name", ""), stream.get("url", "")
                        if stream.get("isMain") and name.startswith(STREAM_PREFIX) and url:
                            streams[name] = url
        except (requests.RequestException, ValueError, AttributeError) as exc:
            logger.warning("NVStreamer stream discovery failed: %s", exc)
        if len(streams) >= expected:
            break
        logger.info("waiting for NVStreamer streams: %d/%d", len(streams), expected)
        time.sleep(10)
    if len(streams) < expected:
        raise RuntimeError(f"NVStreamer serves {len(streams)} perf streams, need {expected}")
    return dict(sorted(streams.items()))


def list_vst_sensor_names(vst_url: str, timeout: int = 15) -> Set[str]:
    """Names of every sensor registered in VST (GET /vst/api/v1/sensor/list)."""
    resp = requests.get(f"{vst_url.rstrip('/')}/vst/api/v1/sensor/list", timeout=timeout)
    resp.raise_for_status()
    data = resp.json() or []
    if isinstance(data, dict):  # some VST versions wrap the list
        data = data.get("sensors", data.get("data", []))
    return {str(s.get("name", "")) for s in data if isinstance(s, dict) and s.get("name")}


class SensorRegistry:
    """Registers NVStreamer streams as VST sensors, keeping track of the count.

    Safe to re-run against a stack that is still up: perf streams already
    registered in VST (e.g. by an earlier benchmark run) are counted as active
    instead of being added again. VST answers a duplicate sensor/add with 400.
    """

    def __init__(self, vst_url: str, streams: Dict[str, str]):
        self.vst_url = vst_url.rstrip("/")
        self.streams = streams
        existing = list_vst_sensor_names(self.vst_url)
        # Keep NVStreamer order so stream N is always the same file.
        self.registered: List[str] = [n for n in streams if n in existing]
        others = sorted(n for n in existing if not n.startswith(STREAM_PREFIX))
        if self.registered:
            logger.info("%d perf sensor(s) already registered in VST", len(self.registered))
        if others:
            logger.warning(
                "%d non-perf sensor(s) are also registered in VST and add load: %s",
                len(others), ", ".join(others[:10]),
            )

    def ensure(self, count: int) -> int:
        """Register sensors until ``count`` are active. Returns the active count."""
        if len(self.registered) > count:
            raise RuntimeError(
                f"{len(self.registered)} perf streams are already active in VST but this level needs {count}. "
                "Streams are only ever added, so re-deploy (teardown.sh + deploy.sh) or remove the "
                "perf_stream_* sensors in VST before measuring a lower stream count."
            )
        pending = [n for n in self.streams if n not in self.registered][: max(0, count - len(self.registered))]
        for name in pending:
            payload = {"location": "", "name": name, "password": "", "sensorUrl": self.streams[name], "username": ""}
            try:
                resp = requests.post(f"{self.vst_url}/vst/api/v1/sensor/add", json=payload, timeout=30)
            except requests.RequestException as exc:
                raise RuntimeError(f"VST sensor/add failed for {name}: {exc}") from exc
            if resp.status_code not in (200, 201, 409):
                # A 400 can mean "already exists" (e.g. added concurrently); trust the list.
                if name not in list_vst_sensor_names(self.vst_url):
                    raise RuntimeError(
                        f"VST sensor/add failed for {name}: HTTP {resp.status_code} {resp.text[:300]}"
                    )
            self.registered.append(name)
        if pending:
            logger.info("registered %d sensor(s); %d active", len(pending), len(self.registered))
        return len(self.registered)
