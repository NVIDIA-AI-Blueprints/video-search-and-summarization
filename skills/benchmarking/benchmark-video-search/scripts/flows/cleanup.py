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

"""The pre-ingest wipe: the only code in this skill that deletes anything.

A default run deletes every uploaded **file** source on the deployment, then
waits until the searched indexes are empty, so the fresh ingest is the only
thing search can return. RTSP cameras are never deleted. ``--skip-ingest`` is
the only way to run without it. Deletion is irreversible: the agent removes the
source's VST storage along with it.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import requests

from .ingest_readiness import SEARCHED_INDEXES, indexed_counts
from .readiness import list_sensor_sources

RTSP_SCHEME = "rtsp://"

#: How long deleted sources' documents get to disappear before the wipe gives up.
WIPE_TIMEOUT_S = 180.0
WIPE_POLL_S = 5.0
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_rtsp(source: dict[str, Any]) -> bool:
    return source["url"].startswith(RTSP_SCHEME)


def file_sources(vst_url: str) -> tuple[dict[str, str], list[str]]:
    """``({stream_id: name}`` for every uploaded file source``, [rtsp names])``.

    Only file sources are this eval's to delete. An RTSP camera indexes into
    dated indexes a ``video_file`` search never reads, and deleting one
    through the agent also deletes its VST storage.
    """
    try:
        sources = list_sensor_sources(vst_url)
    except Exception as e:
        raise SystemExit(f"ABORTED: could not list sensors at {vst_url} ({type(e).__name__}: {e})") from e
    files = {sid: s["name"] for sid, s in sources.items() if not is_rtsp(s)}
    rtsp = sorted(s["name"] for s in sources.values() if is_rtsp(s))
    return files, rtsp


def clear_all_videos(agent_endpoint: str, vst_url: str) -> dict[str, Any]:
    """Delete every uploaded video file registered on the deployment; RTSP cameras stay.

    Lists and deletes against the SAME resolved origins the rest of the run
    uses, so ``--vst-url`` cannot list one host and delete from another.
    Prints the full inventory first, so the log shows what the wipe removed.
    """
    streams, rtsp = file_sources(vst_url)
    if rtsp:
        print(f"  Keeping {len(rtsp)} RTSP source(s); this eval never reads their indexes: {rtsp[:10]}")

    if not streams:
        print("  No videos found to delete.")
        return {"found": 0, "deleted": 0, "names": [], "kept_rtsp": rtsp}

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
    return {
        "found": len(streams), "deleted": deleted, "failed": failed,
        "names": list(streams.values()), "kept_rtsp": rtsp,
    }


def _host(url: str) -> str | None:
    """The URL's hostname, with every loopback spelling as one host."""
    host = urlparse(url).hostname
    return "localhost" if host in LOOPBACK_HOSTS else host


def check_cleanup_hosts(agent_endpoint: str, vst_url: str, ingress_url: str) -> None:
    """Abort unless VST, the ingress and the agent are on one host.

    Sources are listed from VST and deleted through the agent, and the ingress
    confirms their documents are gone. On two hosts, what was read from one
    stack would be deleted from, or checked against, another. Two URLs on one
    host are not proven to be one deployment; this catches only a host mismatch.
    """
    host = _host(agent_endpoint)
    for flag, url in (("--vst-url", vst_url), ("--ingress-url", ingress_url)):
        if _host(url) != host:
            raise SystemExit(
                f"ABORTED: {flag} ({url}) and --endpoint ({agent_endpoint}) are on different hosts, "
                "so the wipe could delete from the wrong deployment. Pass a "
                f"{flag} on the endpoint's host."
            )


def _index_state(vst_url: str, ingress_url: str) -> tuple[dict[str, str], dict[str, int]]:
    """The file sources and the searched-index counts; RTSP cameras are not counted."""
    sources, _rtsp = file_sources(vst_url)
    try:
        counts = indexed_counts(ingress_url)
    except (requests.RequestException, ValueError) as e:
        raise SystemExit(
            f"ABORTED: could not count documents through the ingress at {ingress_url} "
            f"({type(e).__name__}: {e}). Pass --ingress-url."
        ) from e
    return sources, counts


def wipe_index(
    agent_endpoint: str,
    vst_url: str,
    ingress_url: str,
    timeout_s: float = WIPE_TIMEOUT_S,
    poll_s: float = WIPE_POLL_S,
    clock: Any = time.monotonic,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Leave no VST file source and no searched document, so the ingest is fresh.

    Clean means both: VST alone misses documents an aborted ingest left with
    no source, and search returns those. RTSP cameras are left registered.
    Only sources can be deleted from
    here -- the ingress refuses ``_delete_by_query`` -- and each deletion's
    cleanup webhooks empty that source's documents. The state is re-read until
    it is empty on two polls in a row: one empty read can land between a late
    write and its cleanup.
    """
    sources, counts = _index_state(vst_url, ingress_url)
    record: dict[str, Any] = {"clean_before": not sources and not any(counts.values()),
                              "sources_before": len(sources), "documents_before": counts}
    if record["clean_before"]:
        print("  Index is clean: no file sources, no documents. Ingesting fresh.")
        return {**record, "deleted": 0, "waited_s": 0.0}

    detail = "  ".join(f"{k}={v}" for k, v in counts.items())
    if not sources:
        raise SystemExit(_orphans_message(counts, "no VST file source owns them"))

    print(f"  Not clean: {len(sources)} file source(s); documents {detail}. Deleting every file source.")
    cleared = clear_all_videos(agent_endpoint, vst_url)
    if cleared.get("failed"):
        raise SystemExit(
            f"ABORTED: {len(cleared['failed'])} source(s) could not be deleted: {cleared['failed'][:10]}\n"
            "  They would stay searchable next to the fresh ingest. Delete them by hand and rerun."
        )

    started = clock()
    empty_reads = 0
    while True:
        sources, counts = _index_state(vst_url, ingress_url)
        empty_reads = empty_reads + 1 if not sources and not any(counts.values()) else 0
        if empty_reads >= 2:
            break
        if clock() - started >= timeout_s:
            if sources:
                raise SystemExit(
                    f"ABORTED: {len(sources)} file source(s) still registered {timeout_s:.0f}s after "
                    f"deleting every file source: {sorted(sources.values())[:10]}\n"
                    "  Something is uploading videos. Stop it and rerun."
                )
            raise SystemExit(_orphans_message(counts, f"still there {timeout_s:.0f}s after deleting every file source"))
        sleep(poll_s)

    waited = round(clock() - started, 1)
    print(f"  Index empty after {waited}s; deleted {cleared['deleted']} file source(s).")
    return {
        **record, "deleted": cleared["deleted"], "names": cleared.get("names", []),
        "kept_rtsp": cleared.get("kept_rtsp", []), "waited_s": waited,
    }


def _orphans_message(counts: dict[str, int], why: str) -> str:
    detail = "  ".join(f"{k}={v}" for k, v in counts.items() if v)
    indexes = " ".join(SEARCHED_INDEXES.values())
    return (
        f"ABORTED: documents with no source are left in the searched indexes ({detail}); {why}.\n"
        "  Search would return them next to the fresh ingest, and the ingress refuses deletes.\n"
        "  A file source was deleted without its cleanup webhook emptying them, or an upload is\n"
        "  still being processed. If something is still writing, wait and rerun; otherwise\n"
        f"  delete them on the Elasticsearch host (_delete_by_query, match_all) from: {indexes}"
    )
