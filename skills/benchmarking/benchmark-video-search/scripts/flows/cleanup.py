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

"""Destructive cleanup operations for benchmark dataset isolation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .readiness import list_sensor_streams, name_variants


def require_same_host(agent_endpoint: str, vst_url: str) -> None:
    """Abort a delete whose listing and deleting origins are on different hosts.

    Sources are listed from VST and deleted through the agent, by stream id.
    ``--vst-url`` can point anywhere, so without this check a listing from one
    stack could be replayed as deletes against another.
    """
    agent_host = (urlparse(agent_endpoint).hostname or "").lower()
    vst_host = (urlparse(vst_url).hostname or "").lower()
    if not agent_host or agent_host != vst_host:
        raise SystemExit(
            f"ABORTED: refusing to delete -- sources are listed from {vst_url} but "
            f"deleted through {agent_endpoint}, and the hosts differ "
            f"({vst_host or '?'} vs {agent_host or '?'}).\n"
            "  Point --vst-url at the same host as --endpoint, or delete by hand."
        )


def clear_all_videos(agent_endpoint: str, vst_url: str) -> dict[str, Any]:
    """Delete every video registered on the deployment.

    Lists from ``vst_url`` and deletes through ``agent_endpoint``; aborts
    before listing if their hosts differ (:func:`require_same_host`).
    Prints the full inventory first: ``--clear`` is destructive and shared
    deployments carry other people's fixtures.
    """
    require_same_host(agent_endpoint, vst_url)
    try:
        streams = list_sensor_streams(vst_url)
    except Exception as e:
        raise SystemExit(f"ABORTED: could not list sensors at {vst_url} ({type(e).__name__}: {e})") from e

    if not streams:
        print("  No videos found to delete.")
        return {"found": 0, "deleted": 0, "names": []}

    print(f"  Deleting {len(streams)} video(s) from {agent_endpoint}:")
    for stream_id, name in streams.items():
        print(f"    - {name}  ({stream_id})")

    deleted, failed = 0, []
    for stream_id, name in streams.items():
        try:
            resp = requests.delete(f"{agent_endpoint.rstrip('/')}/api/v1/videos/{stream_id}", timeout=60)
            resp.raise_for_status()
            deleted += 1
            print(f"    deleted {name} -> {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"    FAILED {name}: {e}")

    print(f"  Deleted {deleted}/{len(streams)} video(s)")
    return {"found": len(streams), "deleted": deleted, "failed": failed, "names": list(streams.values())}


def prune_foreign_videos(agent_endpoint: str, vst_url: str, video_dir: Path) -> dict[str, Any]:
    """Delete every registered source that is NOT one of this dataset's videos.

    The narrow form of ``--clear``. A run is only interpretable if the index
    holds what the dataset says it holds: a foreign source cannot be retrieved
    for any query in the dataset, so it contributes nothing but the chance of a
    false positive and a longer ingest. Removing exactly the foreign ones keeps
    the deployment reproducible without wiping fixtures the dataset does need.

    Membership uses :func:`name_variants` -- the same normalisation
    ``--skip-existing`` uses to decide a video is already present. Sharing one
    matcher is what stops this deleting a video the very next step re-uploads.
    """
    require_same_host(agent_endpoint, vst_url)
    try:
        streams = list_sensor_streams(vst_url)
    except Exception as e:
        raise SystemExit(f"ABORTED: could not list sensors at {vst_url} ({type(e).__name__}: {e})") from e

    ours: set[str] = set()
    for video_file in sorted(video_dir.glob("*.mp4")) + sorted(video_dir.glob("*.mkv")):
        ours |= name_variants(video_file.name)
    if not ours:
        raise SystemExit(
            f"ABORTED: no video files (.mp4 or .mkv) under {video_dir}, so every source looks foreign. "
            f"Refusing to delete the whole deployment -- pass --clear if that is what you meant."
        )

    foreign = {stream_id: name for stream_id, name in streams.items() if str(name).lower() not in ours}
    kept = len(streams) - len(foreign)
    print(f"  {len(streams)} source(s) registered: {kept} belong to this dataset, {len(foreign)} do not")
    if not foreign:
        print("  Nothing to prune.")
        return {"found": len(streams), "kept": kept, "deleted": 0, "names": []}

    for stream_id, name in foreign.items():
        print(f"    - {name}  ({stream_id})")

    deleted, failed = 0, []
    for stream_id, name in foreign.items():
        try:
            resp = requests.delete(f"{agent_endpoint.rstrip('/')}/api/v1/videos/{stream_id}", timeout=60)
            resp.raise_for_status()
            deleted += 1
            print(f"    deleted {name} -> {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"    FAILED {name}: {e}")

    print(f"  Pruned {deleted}/{len(foreign)} foreign video(s); kept {kept}")
    return {
        "found": len(streams),
        "kept": kept,
        "deleted": deleted,
        "failed": failed,
        "names": list(foreign.values()),
    }
