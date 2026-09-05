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

"""Post-ingest readiness.

A 200 from ``/complete`` is not readiness -- indexing continues afterwards.
Querying too early returns empty result sets that look exactly like a retrieval
regression, so this gate exists to stop a timing artefact being reported as an
accuracy number.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from .base import VST_LIST_TIMEOUT


def sensor_list_url(vst_url: str) -> str:
    return f"{vst_url.rstrip('/')}/vst/api/v1/sensor/list"


def list_sensor_names(vst_url: str, timeout: int = VST_LIST_TIMEOUT) -> set[str]:
    """Return every registered sensor name, lowercased.

    Raises on transport failure so callers can distinguish "VST said there are
    no sensors" from "VST could not be reached".
    """
    resp = requests.get(sensor_list_url(vst_url), timeout=timeout)
    resp.raise_for_status()
    payload = resp.json() or []
    return {str(s.get("name", "")).lower() for s in payload if isinstance(s, dict)}


def list_sensor_streams(vst_url: str, timeout: int = VST_LIST_TIMEOUT) -> dict[str, str]:
    """Return ``{stream_id: name}`` for every registered sensor.

    Same endpoint and payload shape as ``run_eval.get_indexed_videos``, but
    takes a resolved VST origin instead of re-deriving one from a port. That
    matters for destructive callers: deriving the origin twice is how you end
    up deleting from a different host than the one you listed.
    """
    resp = requests.get(f"{vst_url.rstrip('/')}/vst/api/v1/sensor/streams", timeout=timeout)
    resp.raise_for_status()
    payload = resp.json() or []  # list of {stream_id: [{name, url, ...}]}

    streams: dict[str, str] = {}
    for entry in payload:
        if not isinstance(entry, dict) or not entry:
            continue
        stream_id = next(iter(entry))
        stream_list = entry[stream_id]
        if stream_list:
            streams[stream_id] = stream_list[0].get("name", "unknown")
    return streams


def inventory_snapshot(vst_url: str) -> dict[str, Any]:
    """Record what the deployment holds right now.

    Shared deployments get wiped and reloaded by other people mid-run. When
    that happens the eval keeps producing numbers, but they describe an index
    that no longer exists -- observed on 10.86.12.161, where 17 ingested
    sources vanished between one query and the next.

    Taking this before and after the query phase turns an invisible
    correctness problem into a recorded warning.
    """
    try:
        names = sorted(list_sensor_names(vst_url))
        return {"ok": True, "count": len(names), "names": names}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "names": []}


def compare_inventory(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Diff two snapshots; ``stable`` is False when the deployment changed."""
    if not (before.get("ok") and after.get("ok")):
        return {"stable": None, "reason": "inventory unavailable at one or both ends"}

    gone = sorted(set(before["names"]) - set(after["names"]))
    added = sorted(set(after["names"]) - set(before["names"]))
    return {
        "stable": not gone and not added,
        "disappeared": gone,
        "appeared": added,
        "before_count": before["count"],
        "after_count": after["count"],
    }


def name_variants(video_name: str) -> set[str]:
    """Plausible VST spellings of one local file name.

    VST is not consistent about the extension. On the deployment probed on
    2026-08-25 the sensor list held BOTH spellings simultaneously::

        warehouse_safety_0001        <- stem only
        sample-drone-bridge.mp4      <- extension retained

    Matching on the stem alone therefore waits forever for sources that are
    already registered. Accept either.
    """
    stem = video_name.rsplit(".", 1)[0]
    return {stem.lower(), f"{stem.lower()}.mp4", f"{stem.lower()}.mkv"}


def is_registered(video_name: str, registered: set[str]) -> bool:
    """True when any plausible spelling of ``video_name`` is registered."""
    return bool(name_variants(video_name) & registered)


def wait_for_sources(
    vst_url: str,
    expected_names: list[str],
    timeout_s: int = 1200,
    poll_interval_s: int = 10,
) -> dict[str, Any]:
    """Block until VST lists every expected source, or the budget runs out.

    Only VST registration is checked. The upstream skill also checks the three
    Elasticsearch indices directly, which this deliberately does not do --
    reaching past the public origin into ES is what the skill's hard boundaries
    forbid.

    Note VST registration is not a complete readiness signal: an aborted ingest
    can leave embedding documents in Elasticsearch with no VST sensor, which
    nothing here can see.
    """
    deadline = time.time() + timeout_s
    started = time.time()
    registered: set[str] = set()
    attempts = 0

    while time.time() < deadline:
        attempts += 1
        try:
            registered = list_sensor_names(vst_url)
            missing = [n for n in expected_names if not is_registered(n, registered)]
            if not missing:
                return {
                    "ready": True,
                    "attempts": attempts,
                    "waited_s": round(time.time() - started, 1),
                    "missing": [],
                }
        except Exception as e:
            missing = list(expected_names)
            print(f"  VST sensor list not readable yet ({type(e).__name__}: {e})")

        print(f"  Waiting for {len(missing)} source(s) to register: {missing[:5]}")
        time.sleep(poll_interval_s)

    return {
        "ready": False,
        "attempts": attempts,
        "waited_s": round(time.time() - started, 1),
        "missing": [n for n in expected_names if not is_registered(n, registered)],
    }
