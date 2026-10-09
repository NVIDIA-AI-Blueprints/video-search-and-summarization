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

"""Ingestion completeness and timing for the webhook-driven upload flow.

``vst-direct`` uploads and stops; VIOS webhooks then drive RT-Embed, RT-VLM and
RT-CV, and none of them reports back. One embedding hit per source -- what the
old index probe asked for -- arrives ~20 s after upload, while RT-CV keeps
writing frames for minutes. Querying then scores a half-built index.

This module decides "done" from what perception actually wrote, read through
the unified ingress (read-only): Elasticsearch counts per sensor against
targets derived from the video's own duration and frame rate, and RT-CV's
stream list for whether the frame pipeline has let go of the file.

    embed     count >= ceil(duration / chunk_s)                    deterministic
    tags      count >= the same chunk count                        as observed
    raw       RT-CV listed then dropped the stream, last frame
              >= end - tolerance, count >= frames - slack          deterministic
    behavior  raw done and no count changed for quiet_s            heuristic

Indexing time is not recorded anywhere in the documents (``mdx-*`` carry the
video's own timestamps), so every timing comes from this poll log and is late
by at most one poll interval.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

from .base import (
    BEHAVIOR_INDEX,
    DEFAULT_UPLOAD_TIMESTAMP,
    EMBED_INDEX,
    RAW_INDEX,
    TAG_INDEX_PREFIX,
)

INGEST_INDEXES = ("embed", "tags", "raw", "behavior")
DEFAULT_REQUIRE = frozenset(INGEST_INDEXES)

DEFAULT_CHUNK_S = 5.0
DEFAULT_POLL_S = 2.0
DEFAULT_QUIET_S = 15.0
DEFAULT_RAW_END_TOLERANCE_S = 1.0
MIN_DEADLINE_S = 600.0

#: Frames RT-CV may drop and still count as complete: 6,297/6,300 at 30 fps
#: and 244/250 at 10 fps were observed on fully processed files.
RAW_COUNT_SLACK = 15
#: A container duration a few ms past a chunk boundary (210.033 s) does not
#: produce an extra chunk; 42 were written for that file, not 43.
CHUNK_TAIL_S = 0.1
#: Above this multiple of its target a count is flagged, not failed. Current
#: RT-Embed and RT-VLM refuse a duplicate asset, so this should not fire; it
#: is what would show a file processed twice if that guard ever regressed.
OVER_TARGET_RATIO = 1.5

POLL_TIMEOUT = 15

CONFIRMED = "confirmed"
TIMED_OUT = "timed_out"
FAILED = "failed"

_RAW_SENSOR_FIELD = "sensorId.keyword"
_SENSOR_FIELD = "sensor.id.keyword"
_RAW_TIME_FIELD = "timestamp"
_STREAM_INFO_PATH = "/rtvi-cv/api/v1/stream/get-stream-info"

#: The WEBHOOK half of the old abort message, still the usual cause when
#: RT-CV never sees an upload.
WEBHOOK_HINT = (
    "check webhooks.enabled in the deployed VIOS notification config, and that "
    "RTVI_EMBED_MODEL matches the webhook's model string -- RT-Embed answers a "
    "mismatch with 200 and inference=false."
)


class IngestPollError(ValueError):
    """A response that could not be read. Transient: the gate polls again."""


@dataclass
class ExpectedVideo:
    """One video the run will query, and what is known about its ingest."""

    #: The sensor name VST registered: the key in mdx-raw and mdx-behavior.
    name: str
    #: The VST sensor UUID: the key in the embedding index and the tag index.
    sensor_id: str | None
    duration_s: float | None
    fps: float | None
    #: False for ``--skip-existing`` sources: never uploaded, so never seen by
    #: RT-CV and carrying no timings.
    uploaded_this_run: bool
    upload_start_mono: float | None = None
    upload_start_utc: str | None = None
    video_name: str = ""
    file_size_mb: float | None = None
    upload_s: float | None = None

    @property
    def tag_index(self) -> str | None:
        return f"{TAG_INDEX_PREFIX}{self.sensor_id.replace('-', '_')}" if self.sensor_id else None


@dataclass
class Snapshot:
    """One poll's view. ``cv_active`` is None when RT-CV was not asked."""

    cv_active: set[str] | None = None
    #: name -> (count, max timestamp in epoch ms or None)
    raw: dict[str, tuple[int, float | None]] = field(default_factory=dict)
    embed: dict[str, int] = field(default_factory=dict)
    behavior: dict[str, int] = field(default_factory=dict)
    tags: dict[str, int] = field(default_factory=dict)


def anchor_epoch_ms(upload_timestamp: str = DEFAULT_UPLOAD_TIMESTAMP) -> float:
    """The upload anchor as epoch ms; an offset-less timestamp is UTC."""
    parsed = datetime.fromisoformat(upload_timestamp.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp() * 1000.0


def chunk_target(duration_s: float | None, chunk_s: float) -> int | None:
    if not duration_s:
        return None
    return max(1, math.ceil((duration_s - CHUNK_TAIL_S) / chunk_s))


def raw_count_target(duration_s: float | None, fps: float | None) -> int | None:
    if not duration_s or not fps:
        return None
    return max(1, math.ceil(duration_s * fps) - RAW_COUNT_SLACK)


def timeline_duration_s(timeline: dict[str, Any]) -> float | None:
    """Seconds between a VST timeline's start and end, as ``verify_anchor`` reads them."""
    try:
        start = datetime.fromisoformat(str(timeline["start_time"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(timeline["end_time"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    seconds = (end - start).total_seconds()
    return round(seconds, 3) if seconds > 0 else None


def default_deadline_s(expected: Iterable[ExpectedVideo]) -> float:
    total = sum(v.duration_s or 0.0 for v in expected)
    return max(MIN_DEADLINE_S, 2.0 * total)


class VideoState:
    """Completion state for one video, advanced one snapshot at a time."""

    def __init__(
        self,
        video: ExpectedVideo,
        *,
        require: Iterable[str] = DEFAULT_REQUIRE,
        chunk_s: float = DEFAULT_CHUNK_S,
        quiet_s: float = DEFAULT_QUIET_S,
        raw_end_tolerance_s: float = DEFAULT_RAW_END_TOLERANCE_S,
        anchor_ms: float | None = None,
    ) -> None:
        self.video = video
        self.require = frozenset(require)
        self.quiet_s = quiet_s
        self.anchor_ms = anchor_epoch_ms() if anchor_ms is None else anchor_ms
        chunks = chunk_target(video.duration_s, chunk_s)
        self.targets: dict[str, int | None] = {
            "embed": chunks,
            "tags": chunks,
            "raw": raw_count_target(video.duration_s, video.fps),
        }
        self.raw_end_s = (
            max(0.0, video.duration_s - raw_end_tolerance_s) if video.duration_s else None
        )
        self.counts: dict[str, int] = {}
        self.last_change: dict[str, float] = {}
        self.done_at: dict[str, float] = {}
        self.index_done: dict[str, bool] = dict.fromkeys(self.require, False)
        self.raw_last_ms: float | None = None
        self.cv_seen = False
        self.cv_listed = False
        #: Which rule completed raw: ``rtcv_released``, ``rtcv_missing`` (RT-CV
        #: never listed it, so frames alone decided) or ``existing``.
        self.raw_check: str | None = None

    @property
    def cv_dropped(self) -> bool:
        return self.cv_seen and not self.cv_listed

    @property
    def done(self) -> bool:
        return all(self.index_done.values())

    @property
    def raw_last_s(self) -> float | None:
        if self.raw_last_ms is None:
            return None
        return round((self.raw_last_ms - self.anchor_ms) / 1000.0, 3)

    def latest_change(self) -> float | None:
        return max(self.last_change.values(), default=None)

    def unverifiable(self) -> str | None:
        """Why completion can never be decided for this video, if it cannot."""
        if self.video.duration_s is None:
            return "duration unknown (ffprobe unavailable and no VST timeline)"
        if self.require & {"embed", "tags"} and not self.video.sensor_id:
            return "no VST sensor id, so its embedding and tag documents cannot be found"
        return None

    def cv_identities(self) -> set[str]:
        v = self.video
        stem = v.name.rsplit(".", 1)[0]
        ids = {v.name, stem, v.video_name}
        if v.sensor_id:
            ids.add(v.sensor_id)
        return {i for i in ids if i}

    def observe(self, snap: Snapshot, now: float) -> None:
        v = self.video
        seen: dict[str, int] = {}
        if "raw" in self.require:
            count, last_ms = snap.raw.get(v.name, (0, None))
            seen["raw"] = count
            self.raw_last_ms = last_ms
        if "embed" in self.require:
            seen["embed"] = snap.embed.get(v.sensor_id or "", 0)
        if "tags" in self.require:
            seen["tags"] = snap.tags.get(v.tag_index or "", 0)
        if "behavior" in self.require:
            seen["behavior"] = snap.behavior.get(v.name, 0)

        for index, count in seen.items():
            previous = self.counts.get(index)
            # An uploaded video started from zero -- the stale check enforces
            # that -- so its first non-zero count is a change. A skipped one
            # was already there: its first reading is the baseline.
            if previous is None and v.uploaded_this_run:
                previous = 0
            if previous is not None and count != previous:
                self.last_change[index] = now
            self.counts[index] = count

        for index in ("embed", "tags"):
            target = self.targets[index]
            if index in self.require and index not in self.done_at and target and seen[index] >= target:
                self.done_at[index] = now

        if snap.cv_active is not None and v.uploaded_this_run:
            self.cv_listed = bool(self.cv_identities() & snap.cv_active)
            self.cv_seen |= self.cv_listed

        for index in ("embed", "tags"):
            if index in self.require:
                self.index_done[index] = index in self.done_at
        if "raw" in self.require:
            self.index_done["raw"] = self._raw_complete(now)
        if "behavior" in self.require:
            latest = self.latest_change()
            quiet = latest is None or now - latest >= self.quiet_s
            self.index_done["behavior"] = self.index_done.get("raw", True) and quiet

    def _raw_last_frame_ok(self) -> bool:
        last_s = self.raw_last_s
        return last_s is not None and self.raw_end_s is not None and last_s >= self.raw_end_s

    def _raw_count_ok(self) -> bool:
        target = self.targets["raw"]
        return target is None or self.counts.get("raw", 0) >= target

    def _raw_complete(self, now: float) -> bool:
        if not (self._raw_last_frame_ok() and self._raw_count_ok()):
            self.raw_check = None
            return False
        if not self.video.uploaded_this_run:
            self.raw_check = "existing"
        elif self.cv_dropped:
            self.raw_check = "rtcv_released"
        elif not self.cv_seen and now - self.last_change.get("raw", now) >= self.quiet_s:
            # RT-CV can finish a short clip between two polls, and an
            # un-listed stream is not evidence of anything still running.
            # Frames complete and stable stand in, and the report says so.
            self.raw_check = "rtcv_missing"
        else:
            self.raw_check = None
        return self.raw_check is not None

    def per_index_done_s(self) -> dict[str, float | None]:
        """Seconds from upload start, per index. Empty for a skipped video."""
        start = self.video.upload_start_mono
        if not self.video.uploaded_this_run or start is None:
            return {}
        out: dict[str, float | None] = {}
        for index in INGEST_INDEXES:
            if index not in self.require or not self.index_done.get(index):
                continue
            at = self.done_at.get(index) if index in ("embed", "tags") else self.last_change.get(index)
            # Zero behavior objects is a legitimate outcome with no done time.
            out[index] = round(at - start, 3) if at is not None else None
        return out

    def causes(self) -> list[str]:
        """Plain-language reasons each unfinished index is not done."""
        out: list[str] = []
        v = self.video
        for index in ("embed", "tags"):
            if index in self.require and not self.index_done.get(index):
                count, target = self.counts.get(index, 0), self.targets[index]
                where = EMBED_INDEX if index == "embed" else v.tag_index
                line = f"{index}: {count}/{target} documents in {where}"
                if index == "tags" and count == 0:
                    line += " (no tag documents: index missing or empty -- drop 'tags' from --ingest-require if this profile has no RT-VLM)"
                out.append(line)
        if "raw" in self.require and not self.index_done.get("raw"):
            parts = []
            if v.uploaded_this_run and not self.cv_seen and not self.counts.get("raw"):
                parts.append("RT-CV never listed the stream")
            elif v.uploaded_this_run and not self.cv_seen:
                parts.append("RT-CV never listed the stream; waiting for frames to settle")
            elif self.cv_listed:
                parts.append("RT-CV is still processing the stream")
            if not self._raw_last_frame_ok():
                parts.append(f"last frame at {self.raw_last_s}s, needs >= {self.raw_end_s}s")
            if not self._raw_count_ok():
                parts.append(f"{self.counts.get('raw', 0)}/{self.targets['raw']} frames")
            out.append(f"raw: {'; '.join(parts) or 'not complete'}")
        if "behavior" in self.require and not self.index_done.get("behavior"):
            if not self.index_done.get("raw", True):
                out.append("behavior: waiting for raw")
            else:
                out.append(f"behavior: counts still changing within the {self.quiet_s:g}s quiet window")
        return out

    def warnings(self) -> list[str]:
        """Outcomes that pass but deserve a look in the summary."""
        out = []
        if "behavior" in self.require and self.counts.get("raw") and not self.counts.get("behavior"):
            out.append(
                "raw documents but no behavior documents (allowed: a video can have "
                "no tracked objects, but the same video has produced them before)"
            )
        if self.raw_check == "rtcv_missing":
            out.append("RT-CV never listed the stream; raw was accepted on complete, stable frames")
        return out

    def report(self, outcome: str) -> dict[str, Any]:
        v = self.video
        done_s = self.per_index_done_s()
        timed = [s for s in done_s.values() if s is not None]
        targets = {k: t for k, t in self.targets.items() if k in self.require}
        if "raw" in self.require:
            targets["raw_last_s"] = self.raw_end_s
        over = [
            k for k, t in self.targets.items()
            if k in self.require and t and self.counts.get(k, 0) > OVER_TARGET_RATIO * t
        ]
        return {
            "sensor": v.name,
            "sensor_id": v.sensor_id,
            "video_name": v.video_name or v.name,
            "uploaded_this_run": v.uploaded_this_run,
            "duration_s": v.duration_s,
            "fps": v.fps,
            "file_size_mb": v.file_size_mb,
            "upload_s": v.upload_s if v.uploaded_this_run else None,
            "upload_start_utc": v.upload_start_utc if v.uploaded_this_run else None,
            "upload_start_mono": v.upload_start_mono if v.uploaded_this_run else None,
            "per_index_done_s": done_s,
            "ingest_s": max(timed) if timed and len(done_s) == len(self.require) else None,
            "counts": dict(self.counts),
            "targets": targets,
            "raw_last_s": self.raw_last_s if "raw" in self.require else None,
            "cv_seen": self.cv_seen if v.uploaded_this_run else None,
            "raw_check": self.raw_check if "raw" in self.require else None,
            "over_target": over,
            "warnings": self.warnings(),
            "outcome": outcome,
            "causes": [] if outcome == CONFIRMED else self.causes(),
        }


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


def stream_identities(payload: Any) -> set[str]:
    """Every string RT-CV reports for its active streams.

    The entries carry the sensor name and id under keys that differ between
    RT-CV builds (``camera_id``, ``camera_name``, ...), so all string values
    are collected rather than one key trusted.
    """
    entries: Any = payload
    while isinstance(entries, dict) and "stream-info" in entries:
        entries = entries["stream-info"]
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        raise IngestPollError(f"unexpected RT-CV stream-info shape: {str(payload)[:200]}")
    out: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            out |= {str(value) for value in entry.values() if isinstance(value, str) and value}
    return out


def _terms_body(field_name: str, keys: list[str], last_ts: bool = False) -> dict[str, Any]:
    agg: dict[str, Any] = {"terms": {"field": field_name, "size": max(1, len(keys))}}
    if last_ts:
        agg["aggs"] = {"last": {"max": {"field": _RAW_TIME_FIELD}}}
    return {"size": 0, "query": {"terms": {field_name: keys}}, "aggs": {"by": agg}}


def _buckets(response: dict[str, Any], label: str) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        raise IngestPollError(f"{label}: malformed response {str(response)[:200]}")
    if response.get("error"):
        raise IngestPollError(f"{label}: {str(response['error'])[:300]}")
    buckets = ((response.get("aggregations") or {}).get("by") or {}).get("buckets") or []
    if not isinstance(buckets, list):
        raise IngestPollError(f"{label}: malformed buckets")
    return buckets


def _msearch(base: str, searches: list[tuple[str, str, dict[str, Any]]], timeout: int) -> dict[str, list]:
    """Run ``(label, index, body)`` searches in one request; buckets by label."""
    lines = []
    for _label, index, body in searches:
        lines.append(json.dumps({"index": index, "ignore_unavailable": True}))
        lines.append(json.dumps(body))
    resp = requests.post(
        f"{base}/elasticsearch/_msearch",
        data="\n".join(lines) + "\n",
        headers={"Content-Type": "application/x-ndjson"},
        timeout=timeout,
    )
    resp.raise_for_status()
    responses = (resp.json() or {}).get("responses")
    if not isinstance(responses, list) or len(responses) != len(searches):
        raise IngestPollError(f"_msearch returned {str(responses)[:200]}")
    return {label: _buckets(r, label) for (label, _i, _b), r in zip(searches, responses)}


def poll_ingest(
    ingress_url: str,
    expected: list[ExpectedVideo],
    require: Iterable[str] = DEFAULT_REQUIRE,
    timeout: int = POLL_TIMEOUT,
) -> Snapshot:
    """One snapshot in at most three requests, however many videos there are.

    Counts come from ``_search`` aggregations, never ``_cat/indices``: the
    latter counts nested documents (38,266 for 6,541 real mdx-raw documents).
    """
    require = frozenset(require)
    base = ingress_url.rstrip("/")
    names = sorted({v.name for v in expected})
    ids = sorted({v.sensor_id for v in expected if v.sensor_id})
    snap = Snapshot()

    if "raw" in require:
        resp = requests.get(f"{base}{_STREAM_INFO_PATH}", timeout=timeout)
        resp.raise_for_status()
        snap.cv_active = stream_identities(resp.json())

    searches: list[tuple[str, str, dict[str, Any]]] = []
    if "raw" in require and names:
        searches.append(("raw", RAW_INDEX, _terms_body(_RAW_SENSOR_FIELD, names, last_ts=True)))
    if "embed" in require and ids:
        searches.append(("embed", EMBED_INDEX, _terms_body(_SENSOR_FIELD, ids)))
    if "behavior" in require and names:
        searches.append(("behavior", BEHAVIOR_INDEX, _terms_body(_SENSOR_FIELD, names)))
    if searches:
        found = _msearch(base, searches, timeout)
        for bucket in found.get("raw", []):
            last = (bucket.get("last") or {}).get("value")
            snap.raw[str(bucket.get("key"))] = (int(bucket.get("doc_count", 0)), float(last) if last is not None else None)
        snap.embed = {str(b.get("key")): int(b.get("doc_count", 0)) for b in found.get("embed", [])}
        snap.behavior = {str(b.get("key")): int(b.get("doc_count", 0)) for b in found.get("behavior", [])}

    tag_indexes = sorted({v.tag_index for v in expected if v.tag_index})
    if "tags" in require and tag_indexes:
        resp = requests.post(
            f"{base}/elasticsearch/{TAG_INDEX_PREFIX}*/_search",
            params={"ignore_unavailable": "true"},
            json={
                "size": 0,
                "query": {"terms": {"_index": tag_indexes}},
                "aggs": {"by": {"terms": {"field": "_index", "size": len(tag_indexes)}}},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        snap.tags = {str(b.get("key")): int(b.get("doc_count", 0)) for b in _buckets(resp.json(), "tags")}
    return snap


def stale_documents(
    ingress_url: str,
    spellings: dict[str, list[str]],
    require: Iterable[str] = DEFAULT_REQUIRE,
    timeout: int = POLL_TIMEOUT,
) -> dict[str, dict[str, int]]:
    """Documents already indexed under a name this run is about to upload.

    ``spellings`` maps each video to every name VST might register it as. Any
    hit would satisfy the completion check before perception had started, so
    the caller must abort. Only name-keyed indexes can be checked: the
    embedding and tag indexes are keyed by a sensor UUID that does not exist
    until the upload returns.
    """
    require = frozenset(require)
    all_names = sorted({n for names in spellings.values() for n in names})
    searches = []
    if "raw" in require:
        searches.append(("raw", RAW_INDEX, _terms_body(_RAW_SENSOR_FIELD, all_names)))
    if "behavior" in require:
        searches.append(("behavior", BEHAVIOR_INDEX, _terms_body(_SENSOR_FIELD, all_names)))
    if not searches or not all_names:
        return {}
    found = _msearch(ingress_url.rstrip("/"), searches, timeout)
    per_name = {
        label: {str(b.get("key")): int(b.get("doc_count", 0)) for b in buckets}
        for label, buckets in found.items()
    }
    stale: dict[str, dict[str, int]] = {}
    for video, names in spellings.items():
        counts = {label: sum(by.get(n, 0) for n in set(names)) for label, by in per_name.items()}
        counts = {label: c for label, c in counts.items() if c}
        if counts:
            stale[video] = counts
    return stale


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class IngestWatcher:
    """Polls from before the first upload until every video is ingested.

    Started before the upload loop and fed each video as its upload returns,
    so nothing perception does between uploads is missed: a short clip that
    RT-CV lists and drops while a later file is still uploading is remembered
    as seen, and every video's timings start from its own upload rather than
    from the end of the batch. :meth:`seal` says the set is complete; only
    then can the run be confirmed, and only then does the deadline run.

    Run :meth:`start` for a background thread, or call :meth:`wait` alone to
    poll inline (what tests do, with a fake clock).
    """

    def __init__(
        self,
        ingress_url: str,
        *,
        require: Iterable[str] = DEFAULT_REQUIRE,
        chunk_s: float = DEFAULT_CHUNK_S,
        poll_s: float = DEFAULT_POLL_S,
        quiet_s: float = DEFAULT_QUIET_S,
        raw_end_tolerance_s: float = DEFAULT_RAW_END_TOLERANCE_S,
        upload_timestamp: str = DEFAULT_UPLOAD_TIMESTAMP,
        poll_fn: Callable[[list[ExpectedVideo]], Snapshot] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] | None = None,
        log: Callable[[str], None] = print,
        progress_every: int = 10,
    ) -> None:
        self.require = frozenset(require)
        unknown = self.require - DEFAULT_REQUIRE
        if unknown:
            raise ValueError(f"unknown ingest index(es): {sorted(unknown)}")
        if "behavior" in self.require and "raw" not in self.require:
            raise ValueError("'behavior' completion is defined relative to 'raw'; require both")
        self.ingress_url = ingress_url
        self.chunk_s = chunk_s
        self.poll_s = poll_s
        self.quiet_s = quiet_s
        self.raw_end_tolerance_s = raw_end_tolerance_s
        self.anchor_ms = anchor_epoch_ms(upload_timestamp)
        self._poll_fn = poll_fn or (lambda videos: poll_ingest(ingress_url, videos, self.require))
        self._clock = clock
        self._stop = threading.Event()
        self._sleep = sleep or self._stop.wait
        self._log = log
        self._progress_every = progress_every

        self._lock = threading.Lock()
        self._states: list[VideoState] = []
        self._cv_ever: set[str] = set()
        self._thread: threading.Thread | None = None
        self._result: dict[str, Any] | None = None
        self.started_at: float | None = None
        self.sealed_at: float | None = None
        self.deadline_s: float | None = None
        self.polls = 0
        self.poll_errors = 0
        self.last_error: str | None = None

    @property
    def started(self) -> bool:
        return self.started_at is not None

    def add(self, video: ExpectedVideo) -> None:
        state = VideoState(
            video, require=self.require, chunk_s=self.chunk_s, quiet_s=self.quiet_s,
            raw_end_tolerance_s=self.raw_end_tolerance_s, anchor_ms=self.anchor_ms,
        )
        with self._lock:
            if video.uploaded_this_run and state.cv_identities() & self._cv_ever:
                state.cv_seen = True
            self._states.append(state)

    def seal(self, deadline_s: float | None = None) -> None:
        with self._lock:
            if self.sealed_at is not None:
                return
            self.deadline_s = deadline_s if deadline_s is not None else default_deadline_s(
                st.video for st in self._states
            )
            self.sealed_at = self._clock()

    def start(self) -> None:
        if self._thread is None:
            self.started_at = self._clock()
            self._thread = threading.Thread(target=self._run, name="ingest-watcher", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def wait(self, deadline_s: float | None = None) -> dict[str, Any]:
        """Seal the set and block until confirmed, timed out or failed."""
        self.seal(deadline_s)
        if self._thread is not None:
            self._thread.join()
        else:
            self._run()
        assert self._result is not None
        return self._result

    def _run(self) -> None:
        if self.started_at is None:
            self.started_at = self._clock()
        try:
            while not self._stop.is_set():
                result = self._step()
                if result is not None:
                    self._result = result
                    return
                self._sleep(self.poll_s)
        except Exception as exc:  # noqa: BLE001
            # A bug here must still end the wait with a reason, not hang it.
            with self._lock:
                reports = [st.report(FAILED) for st in self._states]
            self._result = self._finish(FAILED, reports, self._clock(), error=f"{type(exc).__name__}: {exc}")
        if self._result is None:
            with self._lock:
                reports = [st.report(TIMED_OUT) for st in self._states]
            self._result = self._finish(TIMED_OUT, reports, self._clock(), error="stopped")

    def _step(self) -> dict[str, Any] | None:
        now = self._clock()
        if self.started_at is None:
            self.started_at = now
        with self._lock:
            videos = [st.video for st in self._states]
            sealed = self.sealed_at is not None
        if sealed:
            failed = self._unverifiable_result(now)
            if failed is not None:
                return failed
        try:
            snap = self._poll_fn(videos)
        except (requests.RequestException, ValueError) as exc:
            self.poll_errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            self._log(f"  ingest poll failed ({self.last_error}); retrying")
        else:
            self.polls += 1
            with self._lock:
                if snap.cv_active:
                    self._cv_ever |= snap.cv_active
                for st in self._states:
                    st.observe(snap, now)
                if sealed:
                    states = list(self._states)
                    latest = max((t for st in states if (t := st.latest_change()) is not None), default=None)
                    if all(st.done for st in states) and (latest is None or now - latest >= self.quiet_s):
                        return self._finish(CONFIRMED, [st.report(CONFIRMED) for st in states], now)
                    if self._progress_every and self.polls % self._progress_every == 1:
                        self._log(_progress_line(states, now - self.sealed_at))
        if sealed and now - self.sealed_at >= self.deadline_s:
            with self._lock:
                reports = [st.report(CONFIRMED if st.done else TIMED_OUT) for st in self._states]
            return self._finish(TIMED_OUT, reports, now, error=self.last_error)
        return None

    def _unverifiable_result(self, now: float) -> dict[str, Any] | None:
        with self._lock:
            states = list(self._states)
        unverifiable = {st.video.name: reason for st in states if (reason := st.unverifiable())}
        if not unverifiable:
            return None
        reports = []
        for st in states:
            report = st.report(FAILED)
            report["causes"] = [
                unverifiable.get(st.video.name)
                or "not polled: another video's completion cannot be decided"
            ]
            reports.append(report)
        return self._finish(FAILED, reports, now, error="completion cannot be decided for some videos")

    def _finish(
        self, outcome: str, reports: list[dict[str, Any]], now: float, error: str | None = None,
    ) -> dict[str, Any]:
        result = {
            "outcome": outcome,
            "require": sorted(self.require),
            "chunk_s": self.chunk_s,
            "poll_interval_s": self.poll_s,
            "quiet_s": self.quiet_s,
            "deadline_s": self.deadline_s,
            "raw_end_tolerance_s": self.raw_end_tolerance_s,
            "behavior_check": "quiet_window" if "behavior" in self.require else None,
            "ingress_url": self.ingress_url,
            "polls": self.polls,
            "poll_errors": self.poll_errors,
            # From the last upload returning: how long querying was held back.
            "waited_s": round(now - (self.sealed_at if self.sealed_at is not None else now), 3),
            # From before the first upload: how long the poll log covers.
            "polled_s": round(now - (self.started_at if self.started_at is not None else now), 3),
            "per_video": reports,
        }
        if error:
            result["error"] = error
        return result


def wait_for_ingest_complete(
    ingress_url: str,
    expected: list[ExpectedVideo],
    *,
    require: Iterable[str] = DEFAULT_REQUIRE,
    chunk_s: float = DEFAULT_CHUNK_S,
    poll_s: float = DEFAULT_POLL_S,
    quiet_s: float = DEFAULT_QUIET_S,
    deadline_s: float | None = None,
    raw_end_tolerance_s: float = DEFAULT_RAW_END_TOLERANCE_S,
    upload_timestamp: str = DEFAULT_UPLOAD_TIMESTAMP,
    poll_fn: Callable[[], Snapshot] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
    progress_every: int = 10,
) -> dict[str, Any]:
    """Poll inline until a known, complete set of videos is ingested.

    Returns the result rather than raising: the caller decides to abort, and
    the per-video report is what it aborts with.
    """
    watcher = IngestWatcher(
        ingress_url, require=require, chunk_s=chunk_s, poll_s=poll_s, quiet_s=quiet_s,
        raw_end_tolerance_s=raw_end_tolerance_s, upload_timestamp=upload_timestamp,
        poll_fn=(lambda _videos: poll_fn()) if poll_fn else None,
        clock=clock, sleep=sleep, log=log, progress_every=progress_every,
    )
    for video in expected:
        watcher.add(video)
    return watcher.wait(deadline_s)


def _progress_line(states: list[VideoState], elapsed: float) -> str:
    parts = []
    for st in states:
        counts = " ".join(
            f"{k}={st.counts.get(k, 0)}/{st.targets.get(k) if k != 'behavior' else '?'}"
            for k in INGEST_INDEXES if k in st.require
        )
        parts.append(f"{st.video.name}[{'done' if st.done else counts}]")
    return f"  {elapsed:6.1f}s  " + "  ".join(parts)


def format_ingest_failure(result: dict[str, Any]) -> str:
    """The abort message: each unfinished video, each index against its target."""
    bad = [r for r in result.get("per_video", []) if r.get("outcome") != CONFIRMED]
    lines = [
        (
            f"ABORTED: ingestion is not complete for {len(bad)}/{len(result.get('per_video', []))} "
            f"video(s) after {result.get('waited_s')}s (outcome: {result.get('outcome')})."
        ),
    ]
    for report in bad:
        targets = report.get("targets", {})
        counts = ", ".join(
            f"{k} {report['counts'].get(k, 0)}" + (f"/{targets[k]}" if targets.get(k) is not None else "")
            for k in INGEST_INDEXES if k in targets or k in report.get("counts", {})
        )
        lines.append(f"  {report['sensor']} [{report['outcome']}]  {counts}")
        lines.extend(f"    - {cause}" for cause in report.get("causes", []))
    if result.get("error"):
        lines.append(f"  last poll error: {result['error']}")
    lines.append(
        "  Querying now would score a half-built index as a retrieval regression. "
        "Raise --ingest-deadline-s if perception is merely slow."
    )
    if any("RT-CV never listed" in c for r in bad for c in r.get("causes", [])):
        lines.append(f"  RT-CV never saw an upload: {WEBHOOK_HINT}")
    return "\n".join(lines)
