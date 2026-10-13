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

"""Corpus preparation: ingest the videos and prove the indices are populated.

Two jobs:

* :func:`prepare_corpus` uploads the videos and waits until Elasticsearch holds
  the document count the corpus is expected to produce.
* :func:`verify_corpus` gates the sweep. A kNN query against an empty index
  returns almost instantly and looks like excellent latency, so an unpopulated
  corpus has to stop the run rather than quietly produce meaningless numbers.

How a video is uploaded
-----------------------
Two hops, the universal flow::

    POST /api/v1/videos          -> {"url": ...}   # ask the agent where to put it
    POST {url}                   -> {"sensorId": ...}  # stream the bytes to VST

There is no third ``/complete`` call. VIOS raises ``camera_streaming`` on the
upload and its webhook notifier fans out to RT-CV, RT-Embed and RT-VLM by
itself; ``/complete`` makes those same calls by hand, so on a deployment with
the webhook configured it is at best redundant and at worst a second
registration (the observed symptom is ``duplicate camera id``).

It is also why completion is measured in Elasticsearch rather than read off a
response. The webhook fan-out is fire-and-forget: VIOS never reports the
outcome to the uploader, so no HTTP status here can say the corpus is indexed.

The corpus is one source video uploaded ``copies`` times, all at once by
default. Every upload is renamed ``searchbench-<timestamp>-<seq>-<stem>.mp4``,
so VST mints a fresh sensorId per request and the embed service -- which keys
on that id, not on content -- has nothing to collapse. Five uploads of one file
are five independent sensors.

Why readiness is counted per video, not per index
-------------------------------------------------
Ingestion is bursty: each video produces a burst of Kafka messages and the
pipeline then goes quiet while CV/VLM analyses the next one. That gap is longer
than any quiet window worth waiting, so a quiet window alone cannot tell a gap
from completion -- a sweep once started on 6% of its corpus that way, with the
gate reporting success.

An index total is not much better. Five videos' worth of documents belonging to
four sensors satisfies it, and nothing afterwards can detect that.

So the gate is per video: every uploaded ``sensorId`` must hold its own
document count in *every* gated index before the sweep may start. The gated
indices disagree on where the sensor id lives -- ``mdx-raw-*`` keys on
``sensorId``, ``mdx-embed-filtered-*`` on ``sensor.id`` -- so the field name is
configured per pattern rather than shared.

Where the expected count comes from
-----------------------------------
``expected_docs_per_video`` in the config, when it is set. That is the strong
case: the bar exists before a single document arrives.

Otherwise the videos check each other. They are the same file, so whichever
copy gets furthest sets the bar for all of them, and a copy that stalls is
visible against its siblings. This is weaker -- it cannot tell five finished
videos from five that stalled in step -- which is why the quiet window still
has to pass afterwards, and why the report records which of the two was used.
"""

from __future__ import annotations

import fnmatch
import http.client
import json
import logging
import ssl
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests

from .console import say
from .lock import lock_config
from .resources import capture_es_index_counts

logger = logging.getLogger(__name__)

#: Step 1 of the upload: the agent answers with the VST URL to send bytes to.
VIDEOS_PATH = "/api/v1/videos"

#: Capture time for an upload whose real one is unknown. Ground truth across
#: the VSS harnesses is expressed relative to this exact constant, and VIOS
#: reads it from the ``metadata`` field of the upload itself.
DEFAULT_UPLOAD_TIMESTAMP = "2025-01-01T00:00:00"

_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".webm": "video/webm",
}


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass
class UploadOutcome:
    """Result of streaming one video into VST through the agent."""

    source_path: str
    upload_filename: str
    size_bytes: int = 0
    started_at: str = ""
    completed_at: str = ""
    duration_sec: float = 0.0
    http_status: int | None = None
    success: bool = False
    sensor_id: str = ""
    retry_count: int = 0
    error: str = ""
    # Timed separately because the single PUT this replaced reported one opaque
    # number: a slow upload and a slow agent are different problems.
    phases: dict[str, float] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)


@dataclass
class VideoTarget:
    """What one uploaded video owes each gated index, and what it holds.

    Counting per ``sensor_id`` rather than per index is what makes "ingest
    finished" a statement about every video instead of about a total. A total
    is satisfied by five videos' worth of documents belonging to four sensors;
    this is not.

    ``expected`` and ``observed`` are keyed by index pattern, because the two
    gated indices are written by different services at different rates and a
    video is only done when both have it.

    Two keys are stored side by side because the gated indices do not agree on
    which identifier a document carries. ``mdx-raw-*`` and ``mdx-behavior-*``
    arrive via nvstreamer, which uses the uploaded filename stem as the sensor
    key; ``mdx-embed-filtered-*`` is written by the embed service, which keys
    on VST's REST UUID. The right field for each pattern is selected at query
    time from :class:`GateEntry` so neither index silently counts zero.
    """

    upload_filename: str
    sensor_id: str
    vst_uuid: str = ""
    expected: dict[str, int] = field(default_factory=dict)
    observed: dict[str, int] = field(default_factory=dict)

    @property
    def met(self) -> bool:
        # An expectation of zero is not an expectation met: it is a pattern
        # nobody has a number for, and treating it as satisfied would let the
        # gate pass on an index that was never written.
        return bool(self.expected) and all(
            count > 0 and self.observed.get(pattern, 0) >= count
            for pattern, count in self.expected.items()
        )

    def shortfall(self) -> str:
        return ", ".join(
            f"{pattern}={self.observed.get(pattern, 0):,}/{count:,}"
            for pattern, count in self.expected.items()
            if self.observed.get(pattern, 0) < count or count <= 0
        )


FILENAME_STEM = "filename_stem"
VST_UUID = "vst_uuid"
_VALID_SENSOR_KEYS = {FILENAME_STEM, VST_UUID}


@dataclass(frozen=True)
class GateEntry:
    """One gated index: which ES field holds the sensor id, and which of a
    target's two identifiers goes into it.

    ``sensor_key`` picks between ``filename_stem`` (nvstreamer-written indices
    like mdx-raw-* and mdx-behavior-*) and ``vst_uuid`` (the embed service's
    index). Guessing from the field name does not work: mdx-behavior-* uses
    ``sensor.id`` with the filename stem while mdx-embed-filtered-* uses the
    same field name with the UUID. Explicit is the only safe default.
    """

    field: str
    sensor_key: str = FILENAME_STEM


#: ``corpus.ingest.readiness.gate_indices`` when unset; matches the Helm chart.
DEFAULT_GATE_INDICES: dict[str, Any] = {
    "mdx-raw-*": "sensorId.keyword",
    "mdx-embed-filtered-*": {"field": "sensor.id.keyword", "sensor_key": VST_UUID},
}


def normalize_gate_indices(raw: dict[str, Any] | None) -> dict[str, GateEntry]:
    """Accept both the short ``pattern: field`` form and the explicit
    ``pattern: {field, sensor_key}`` form.

    The short form keeps old configs working and is right for every index
    nvstreamer writes. The explicit form is required when an index is written
    by a service that keys on VST's UUID instead of the filename -- the embed
    service is the one in-tree example.
    """
    out: dict[str, GateEntry] = {}
    for pattern, entry in (raw or {}).items():
        pattern_str = str(pattern)
        if isinstance(entry, GateEntry):
            # Idempotent, so inner callers can renormalize what the outer caller
            # already normalized without having to care which it is.
            out[pattern_str] = entry
            continue
        if isinstance(entry, dict):
            field_name = str(entry.get("field") or "").strip()
            sensor_key = str(entry.get("sensor_key") or FILENAME_STEM).strip()
            if sensor_key not in _VALID_SENSOR_KEYS:
                raise ValueError(
                    f"gate_indices[{pattern_str!r}].sensor_key = {sensor_key!r} "
                    f"must be one of {sorted(_VALID_SENSOR_KEYS)}"
                )
        else:
            field_name = str(entry).strip()
            sensor_key = FILENAME_STEM
        if not field_name:
            raise ValueError(
                f"gate_indices[{pattern_str!r}] has no 'field' set; "
                f"the gate needs a field name to query"
            )
        out[pattern_str] = GateEntry(field=field_name, sensor_key=sensor_key)
    return out


def _sensor_key_for(target: VideoTarget, entry: GateEntry) -> str:
    """Return the value of ``target`` that matches ``entry``'s key source."""
    if entry.sensor_key == VST_UUID:
        return target.vst_uuid
    return target.sensor_id


@dataclass
class CorpusState:
    """Corpus provenance for one results directory."""

    checked_at: str = ""
    ingest_enabled: bool = False
    uploads: list[UploadOutcome] = field(default_factory=list)
    doc_counts_before: dict[str, Any] = field(default_factory=dict)
    doc_counts_after: dict[str, Any] = field(default_factory=dict)
    readiness: dict[str, Any] = field(default_factory=dict)
    video_targets: list[VideoTarget] = field(default_factory=list)
    # What the index held before this sweep touched it, and what it holds that
    # this sweep did not upload. Together these make "the corpus is exactly the
    # configured videos, once each" a checked claim rather than an assumption --
    # every upload gets a fresh filename and so a fresh sensor_id, so ingesting
    # twice duplicates the corpus without duplicating any sensor id.
    purge: dict[str, Any] = field(default_factory=dict)
    foreign_docs: dict[str, Any] = field(default_factory=dict)
    gate_passed: bool = False
    gate_message: str = ""
    # Written after the sweep, not before it. doc_counts_after is captured
    # seconds before the first run, so on its own it cannot show that the index
    # the sweep measured was the index the gate approved.
    doc_counts_at_end: dict[str, Any] = field(default_factory=dict)
    corpus_drift: dict[str, Any] = field(default_factory=dict)


def _content_type(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _doc_count(counts: dict[str, Any], pattern: str) -> int | None:
    info = (counts.get("indices") or {}).get(pattern)
    if not isinstance(info, dict) or "doc_count" not in info:
        return None
    return int(info["doc_count"])


def resolve_ingest_videos(ingest_config: dict[str, Any]) -> list[Path]:
    """Expand ``video`` plus ``copies`` into the list of uploads to perform.

    A directory is read as its video files, each uploaded ``copies`` times, so
    the same config shape serves both the single-file corpus this benchmark
    defaults to and a distinct-file corpus at ``copies: 1``.
    """
    raw = str(ingest_config.get("video") or "").strip()
    if not raw:
        return []
    copies = max(1, int(ingest_config.get("copies", 1)))
    source = Path(raw).expanduser()

    if source.is_dir():
        files = sorted(
            p for p in source.iterdir()
            if p.is_file() and p.suffix.lower() in _CONTENT_TYPES
        )
    else:
        files = [source]

    return [path for path in files for _ in range(copies)]


def validate_corpus_config(config: dict[str, Any]) -> None:
    """Reject corpus settings that would make the gate silently vacuous.

    Called before anything is purged or uploaded: a gate pattern the counts
    never query is always 0, so its mismatch and growth checks can never fire.
    """
    corpus = config.get("corpus", {}) or {}
    ingest = corpus.get("ingest") or {}
    if not ingest.get("enabled"):
        return
    patterns = set(corpus.get("es_index_patterns") or [])
    readiness = ingest.get("readiness") or {}
    gate = normalize_gate_indices(readiness.get("gate_indices") or DEFAULT_GATE_INDICES)
    for pattern in gate:
        if pattern not in patterns:
            raise ValueError(
                f"corpus.ingest.readiness.gate_indices names {pattern!r}, which is "
                "not in corpus.es_index_patterns, so it would never be counted"
            )
    for pattern in readiness.get("expected_docs_per_video") or {}:
        if pattern not in gate:
            raise ValueError(
                f"corpus.ingest.readiness.expected_docs_per_video names {pattern!r}, "
                "which is not a gate index"
            )
    ratio = float(readiness.get("min_ratio", 1.0))
    if not 0 < ratio <= 1:
        raise ValueError(f"corpus.ingest.readiness.min_ratio must be in (0, 1], got {ratio}")


def build_video_targets(
    uploads: list[UploadOutcome],
    gate_indices: dict[str, Any],
    expected_docs_per_video: dict[str, Any] | None = None,
    min_ratio: float = 1.0,
) -> list[VideoTarget]:
    """One target per successfully uploaded video.

    ``min_ratio`` scales a configured count the same way it scales a converged
    one, so the setting means the same thing in both modes.

    Every copy owes the same count because the corpus is one file uploaded
    repeatedly. With ``expected_docs_per_video`` set, that count is known
    before a single document arrives; without it the targets start empty and
    :func:`wait_for_es_settled` fills them in by convergence.

    An upload with no ``sensor_id`` yields no target: there is nothing to key
    the count on. The caller reports that as an unchecked file rather than
    treating it as satisfied.

    Each target carries both identifiers the gated indices might use. The
    filename stem is what nvstreamer writes into mdx-raw-* and mdx-behavior-*;
    VST's UUID is what the embed service writes into mdx-embed-filtered-*. The
    gate reads :class:`GateEntry` to decide which one to query with, so neither
    index silently counts zero.
    """
    entries = normalize_gate_indices(gate_indices)
    configured = {
        pattern: max(1, int(int(count or 0) * min_ratio))
        for pattern, count in (expected_docs_per_video or {}).items()
        if pattern in entries and int(count or 0) > 0
    }
    complete = len(configured) == len(entries)
    return [
        VideoTarget(
            upload_filename=upload.upload_filename,
            sensor_id=Path(upload.upload_filename).stem,
            vst_uuid=upload.sensor_id,
            expected=dict(configured) if complete else {},
        )
        for upload in uploads
        if upload.success and upload.sensor_id
    ]


def find_duplicate_sensor_ids(uploads: list[UploadOutcome]) -> dict[str, list[str]]:
    """Group successful uploads that received the same ``sensor_id``.

    When two uploads come back with the same sensor id the embed service
    deduplicated them by content, not by filename -- the ``searchbench-<ts>``
    name is unique every time, so a shared id can only mean the service treated
    the two bodies as the same asset. That matters most for this benchmark,
    whose corpus *is* one file uploaded repeatedly: a collapse would leave the
    config claiming N videos while the corpus holds one, and no later check
    could tell.

    Returns ``{sensor_id: [filenames...]}`` for every id claimed by more than
    one upload, empty when every id is unique.
    """
    seen: dict[str, list[str]] = {}
    for upload in uploads:
        if not upload.success or not upload.sensor_id:
            continue
        seen.setdefault(upload.sensor_id, []).append(upload.upload_filename)
    return {sid: files for sid, files in seen.items() if len(files) > 1}


def count_docs_for_sensor(
    elasticsearch_url: str,
    index_pattern: str,
    sensor_field: str,
    sensor_id: str,
    timeout: int = 30,
) -> int | None:
    """Documents in ``index_pattern`` whose ``sensor_field`` equals ``sensor_id``.

    ``None`` on any transport or query error, which the caller treats as
    "unknown this poll" rather than zero: a 503 from Elasticsearch mid-ingest
    must not be read as the file having produced nothing.
    """
    if not elasticsearch_url or not index_pattern or not sensor_field or not sensor_id:
        return None
    url = f"{elasticsearch_url.rstrip('/')}/{quote(index_pattern, safe='*,')}/_count"
    try:
        resp = requests.post(
            url,
            json={"query": {"term": {sensor_field: sensor_id}}},
            # A pattern that matches nothing yet must answer 0, not 404: the
            # embed index does not exist until the first write.
            params={"ignore_unavailable": "true", "allow_no_indices": "true"},
            timeout=timeout,
        )
    except (requests.RequestException, ValueError) as e:
        logger.debug("per-file count failed for %s: %s", sensor_id, e)
        return None
    if resp.status_code != 200:
        logger.debug("per-file count HTTP %s for %s", resp.status_code, sensor_id)
        return None
    try:
        return int(resp.json().get("count", 0))
    except (ValueError, TypeError, AttributeError):
        return None


def count_foreign_docs(
    elasticsearch_url: str,
    index_pattern: str,
    sensor_field: str,
    known_sensor_ids: list[str],
    timeout: int = 30,
) -> int | None:
    """Count documents belonging to nobody this sweep uploaded.

    The per-file check proves our own files arrived; it says nothing about what
    else is in the index. Only this catches a Kafka backlog from an aborted run
    draining into a freshly purged index, or a second ingest against the same
    cluster. Both leave the corpus larger than configured with documents the
    sweep never asked for, and both are invisible to a per-sensor count.

    ``None`` means the question could not be answered, which is not the same as
    zero and must not be reported as a clean corpus.
    """
    if not elasticsearch_url or not index_pattern or not sensor_field:
        return None
    if not known_sensor_ids:
        return None
    url = f"{elasticsearch_url.rstrip('/')}/{quote(index_pattern, safe='*,')}/_count"
    try:
        resp = requests.post(
            url,
            json={"query": {"bool": {
                "must_not": [{"terms": {sensor_field: known_sensor_ids}}]
            }}},
            params={"ignore_unavailable": "true", "allow_no_indices": "true"},
            timeout=timeout,
        )
    except (requests.RequestException, ValueError) as e:
        logger.debug("foreign-doc count failed: %s", e)
        return None
    if resp.status_code != 200:
        logger.debug("foreign-doc count HTTP %s", resp.status_code)
        return None
    try:
        return int(resp.json().get("count", 0))
    except (ValueError, TypeError, AttributeError):
        return None


def resolve_concrete_indices(
    elasticsearch_url: str,
    index_patterns: list[str],
    timeout: int = 30,
) -> tuple[list[str], str]:
    """Expand wildcard patterns into the concrete index names behind them.

    Needed because logstash writes one index per day (``mdx-behavior-2026-10-01``)
    and because Elasticsearch 9 ships ``action.destructive_requires_name: true``,
    which rejects ``DELETE /mdx-*`` outright. Resolving first also means the log
    names every index before it goes.
    """
    if not elasticsearch_url or not index_patterns:
        return [], "no elasticsearch_url or no index patterns"
    base = elasticsearch_url.rstrip("/")
    names: list[str] = []
    for pattern in index_patterns:
        try:
            resp = requests.get(
                f"{base}/_cat/indices/{quote(pattern, safe='*,')}",
                # ILM can mark an index hidden, and a hidden index still answers
                # queries through a wildcard pattern, so it still has to go.
                params={"format": "json", "h": "index", "expand_wildcards": "all"},
                timeout=timeout,
            )
        except (requests.RequestException, ValueError) as e:
            return [], f"could not list indices for {pattern}: {e}"
        if resp.status_code == 404:
            continue
        if resp.status_code != 200:
            return [], f"could not list indices for {pattern}: HTTP {resp.status_code}"
        try:
            rows = resp.json()
        except ValueError as e:
            return [], f"could not parse index list for {pattern}: {e}"
        for row in rows:
            name = str((row or {}).get("index", "")).strip()
            if name and name not in names:
                names.append(name)
    return sorted(names), ""


def check_index_templates(
    elasticsearch_url: str,
    index_patterns: list[str],
    timeout: int = 30,
) -> tuple[bool, str]:
    """Confirm a deleted index would come back with the mapping it needs.

    Deleting an index is only safe while the chart's templates are still in the
    cluster. They are applied by the Elasticsearch init Job at deploy time, not
    by logstash, which runs with ``manage_template => false``. Without them the
    next write recreates the index under dynamic mapping, and the nested vision
    embeddings the embed path searches become ordinary objects -- a corpus that
    looks populated, answers every query with nothing, and took a purge to
    create.

    Asked through ``_simulate_index``, which reports what Elasticsearch itself
    would apply to a new index of that name, rather than guessing from template
    names or pattern strings.
    """
    if not elasticsearch_url or not index_patterns:
        return False, "no elasticsearch_url or no index patterns"
    base = elasticsearch_url.rstrip("/")
    for pattern in index_patterns:
        # A name no real index will ever have, so the simulation cannot be
        # answered from an existing index instead of from the templates.
        probe = f"{pattern.rstrip('*').rstrip('-')}-2099-01-01"
        try:
            resp = requests.post(
                f"{base}/_index_template/_simulate_index/{quote(probe, safe='')}",
                timeout=timeout,
            )
        except (requests.RequestException, ValueError) as e:
            return False, f"could not simulate {probe}: {e}"
        if resp.status_code != 200:
            return False, f"could not simulate {probe}: HTTP {resp.status_code}"
        try:
            properties = (
                (resp.json().get("template") or {}).get("mappings") or {}
            ).get("properties") or {}
        except (ValueError, AttributeError) as e:
            return False, f"could not parse simulation for {probe}: {e}"
        if not properties:
            return False, (
                f"no index template covers {pattern}: a new index would be created "
                f"with dynamic mappings instead of the chart's. Re-run the "
                f"Elasticsearch init Job (or deploy with --clean) before purging"
            )
    return True, ""


def purge_corpus_indices(
    elasticsearch_url: str,
    index_patterns: list[str],
    protected_indices: list[str] | None = None,
    require_templates: bool = True,
    settle_sec: float = 30.0,
    timeout: int = 60,
) -> dict[str, Any]:
    """Delete the corpus indices so the next ingest is the only data present.

    Needed because every upload gets a fresh filename and so a fresh
    ``sensor_id``. Ingesting twice without clearing leaves both copies under
    different sensor ids: the per-file check passes, the totals pass, and every
    search then scores against a corpus holding each video twice. The duplicates
    are not detectable after the fact from counts alone, which is why this runs
    before the upload.
    """
    result: dict[str, Any] = {
        "enabled": True,
        "attempted_at": _utc_now_iso(),
        "patterns": list(index_patterns),
        "success": False,
        "deleted": [],
        "error": "",
    }
    if not elasticsearch_url or not index_patterns:
        result["error"] = "no elasticsearch_url or no index patterns configured"
        return result

    # The run lock lives in Elasticsearch too. A pattern broad enough to match
    # it would have this delete the lock held by the very sweep doing the
    # deleting, and the next benchmark would see a free lock and start on top.
    protected = [p for p in (protected_indices or []) if p]

    def _refuse(pattern: str, name: str) -> dict[str, Any]:
        result["error"] = (
            f"refusing to purge: {pattern!r} also matches the protected index "
            f"{name!r}. Narrow corpus.es_index_patterns."
        )
        return result

    for pattern in index_patterns:
        for name in protected:
            if any(fnmatch.fnmatchcase(name, p) for p in pattern.split(",")):
                return _refuse(pattern, name)

    names, why = resolve_concrete_indices(
        elasticsearch_url, index_patterns, timeout=timeout
    )
    if why:
        result["error"] = why
        return result
    # Checked again on what the patterns resolved to, which is what is deleted.
    for name in names:
        if name in protected:
            return _refuse(",".join(index_patterns), name)
    result["resolved"] = names
    if not names:
        # Nothing to delete, so nothing has to be rebuilt from a template, so
        # the template check is not asked. It matters after a cleaning deploy,
        # which leaves Elasticsearch empty while the chart's init Job may still
        # be applying the templates -- demanding them here would fail a sweep
        # for a purge it did not need to perform.
        result["success"] = True
        result["status"] = "already_empty"
        return result

    if require_templates:
        ok, why = check_index_templates(
            elasticsearch_url, index_patterns, timeout=timeout
        )
        result["templates_ok"] = ok
        if not ok:
            result["error"] = why
            return result

    base = elasticsearch_url.rstrip("/")
    # Batched because the request line carries every name, and a cluster left
    # running for weeks accumulates one index per family per day.
    for start in range(0, len(names), 50):
        batch = names[start:start + 50]
        try:
            resp = requests.delete(
                f"{base}/{quote(','.join(batch), safe=',')}",
                params={"ignore_unavailable": "true"},
                timeout=timeout,
            )
        except (requests.RequestException, ValueError) as e:
            result["error"] = f"delete failed for {batch[0]}...: {e}"
            return result
        if resp.status_code not in (200, 404):
            result["error"] = (
                f"delete returned HTTP {resp.status_code} for {len(batch)} "
                f"index(es): {resp.text[:300]}"
            )
            return result
        result["deleted"].extend(batch)

    # Deleting is not the same as having deleted, and being empty once is not
    # the same as being empty. Logstash commits its Kafka offsets in Kafka, so
    # an earlier aborted ingest can still have messages in flight that an
    # Elasticsearch delete does nothing to stop; they would arrive during the
    # upload and be indistinguishable from it. Checking twice, with a pause,
    # turns "someone else is still writing" into a refusal here instead of a
    # corpus nobody can account for later.
    after = capture_es_index_counts(elasticsearch_url, index_patterns, timeout=timeout)
    result["doc_counts_after_purge"] = after
    if settle_sec > 0:
        time.sleep(settle_sec)
        after = capture_es_index_counts(
            elasticsearch_url, index_patterns, timeout=timeout
        )
        result["doc_counts_after_purge"] = after
        result["settle_sec"] = settle_sec

    uncounted = [p for p in index_patterns if _doc_count(after, p) is None]
    if uncounted:
        result["error"] = (
            f"purge ran but Elasticsearch could not count {', '.join(uncounted)} "
            "afterwards, so there is no evidence the indices are empty."
        )
        return result
    remaining = {
        pattern: count
        for pattern in index_patterns
        if (count := _doc_count(after, pattern) or 0) > 0
    }
    if remaining:
        result["error"] = (
            "purge ran but documents are present "
            + (f"{settle_sec:.0f}s later" if settle_sec > 0 else "immediately after")
            + ": "
            + ", ".join(f"{p}={c:,}" for p, c in remaining.items())
            + ". Something is still writing to these indices -- an earlier ingest "
              "whose Kafka backlog survived the delete, or another benchmark."
        )
        result["remaining"] = remaining
        return result

    result["success"] = True
    result["status"] = "purged"
    return result


def request_upload_url(
    agent_endpoint: str,
    filename: str,
    timeout_sec: int = 30,
) -> str:
    """Step 1: ask the agent which VST URL to send this file's bytes to."""
    resp = requests.post(
        f"{agent_endpoint.rstrip('/')}{VIDEOS_PATH}",
        json={"filename": filename},
        headers={"Content-Type": "application/json"},
        timeout=timeout_sec,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"POST {VIDEOS_PATH} returned HTTP {resp.status_code}: {resp.text[:300]}"
        )
    try:
        url = str((resp.json() or {}).get("url") or "").strip()
    except ValueError as e:
        raise RuntimeError(f"POST {VIDEOS_PATH} returned non-JSON: {e}") from e
    if not url:
        raise RuntimeError(
            f"POST {VIDEOS_PATH} returned no upload url: {resp.text[:200]}"
        )
    return url


def _multipart_parts(
    boundary: str,
    filename: str,
    content_type: str,
    fields: dict[str, str],
) -> tuple[bytes, bytes]:
    """The bytes that bracket the file in a multipart body.

    Returned rather than assembled around the file so the caller can compute
    ``Content-Length`` without reading the video: the length is
    ``len(head) + file_size + len(tail)``.
    """
    head = bytearray()
    for name, value in fields.items():
        head += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()
    head += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="mediaFile"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return bytes(head), tail


def _post_multipart_streaming(
    url: str,
    file_path: Path,
    size_bytes: int,
    content_type: str,
    filename: str,
    headers: dict[str, str],
    fields: dict[str, str],
    timeout_sec: int,
    chunk_size: int,
) -> tuple[int, dict[str, Any], str]:
    """POST a multipart form whose file part is streamed straight from disk.

    ``requests`` would do this in three lines, but it buffers a multipart body
    in memory to length it. At half a gigabyte per upload and five uploads per
    corpus that is a real cost for no benefit, so the body is assembled by hand
    and the length computed arithmetically.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise ValueError(f"URL has no host: {url}")

    boundary = f"----vssbench{uuid.uuid4().hex}"
    head, tail = _multipart_parts(boundary, filename, content_type, fields)

    if parsed.scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            parsed.hostname, port=parsed.port, timeout=timeout_sec,
            context=ssl.create_default_context(),
        )
    else:
        conn = http.client.HTTPConnection(
            parsed.hostname, port=parsed.port, timeout=timeout_sec
        )

    path_and_query = parsed.path or "/"
    if parsed.query:
        path_and_query = f"{path_and_query}?{parsed.query}"

    try:
        conn.putrequest("POST", path_and_query, skip_host=True)
        conn.putheader("Host", parsed.netloc)
        conn.putheader("Accept", "application/json")
        conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        conn.putheader("Content-Length", str(len(head) + size_bytes + len(tail)))
        for name, value in headers.items():
            conn.putheader(name, value)
        conn.endheaders()

        conn.send(head)
        with file_path.open("rb") as f:
            while True:
                chunk = f.read(max(1, chunk_size))
                if not chunk:
                    break
                conn.send(chunk)
        conn.send(tail)

        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        status = response.status
    finally:
        conn.close()

    parsed_body: dict[str, Any] = {}
    if body:
        try:
            parsed_body = json.loads(body)
        except json.JSONDecodeError:
            parsed_body = {}
    return status, parsed_body, body


def _looks_like_duplicate_asset(status: int, raw: str) -> bool:
    """Does this response say "the embed service already has this content"?

    The agent surfaces the embed service's 409 as the body of its own 502, so
    this looks for the sentinel inside the response text rather than at the
    outer HTTP status::

        HTTP 502: {"detail":"Embedding generation failed:
            Embedding generation failed with status 409:
            {\\"code\\":\\"DuplicateAsset\\"...}"}

    Treating this as "retry quickly" is how a sensor id ends up reused across
    uploads; the right handling is a longer pause so the embed service's
    in-flight state for the previous upload has time to clear, and a log line
    that names the condition rather than hiding it in a generic HTTP message.
    """
    return status in (409, 502) and "DuplicateAsset" in (raw or "")


def upload_one_video(
    agent_endpoint: str,
    file_path: Path,
    timeout_sec: int = 3600,
    url_timeout_sec: int = 30,
    chunk_size: int = 1024 * 1024,
    max_retries: int = 1,
    retry_backoff_sec: float = 5.0,
    duplicate_asset_backoff_sec: float = 30.0,
    filename_prefix: str = "searchbench",
    sequence: int = 0,
    upload_timestamp: str = DEFAULT_UPLOAD_TIMESTAMP,
) -> UploadOutcome:
    """Upload a single video through the agent to VST and report the outcome.

    ``sequence`` is part of the generated filename because this corpus uploads
    one file repeatedly: the timestamp alone has one-second resolution, and two
    uploads of the same file inside one second would otherwise collide on a
    name.
    """
    suffix = file_path.suffix.lower() or ".mp4"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    upload_filename = (
        f"{filename_prefix}-{stamp}-{sequence:03d}-{file_path.stem}{suffix}"[-120:]
    )

    outcome = UploadOutcome(
        source_path=str(file_path),
        upload_filename=upload_filename,
        size_bytes=file_path.stat().st_size if file_path.exists() else 0,
        started_at=_utc_now_iso(),
    )

    if not file_path.exists():
        outcome.error = f"video not found: {file_path}"
        outcome.completed_at = _utc_now_iso()
        return outcome

    start = time.monotonic()

    for attempt in range(max_retries + 1):
        duplicate_asset = False
        try:
            step = time.monotonic()
            url = request_upload_url(
                agent_endpoint, upload_filename, timeout_sec=url_timeout_sec
            )
            outcome.phases["request_url_sec"] = round(time.monotonic() - step, 3)

            step = time.monotonic()
            status, body, raw = _post_multipart_streaming(
                url=url,
                file_path=file_path,
                size_bytes=outcome.size_bytes,
                content_type=_content_type(file_path),
                filename=upload_filename,
                headers={
                    "nvstreamer-chunk-number": "1",
                    "nvstreamer-total-chunks": "1",
                    "nvstreamer-is-last-chunk": "true",
                    "nvstreamer-identifier": str(uuid.uuid4()),
                    "nvstreamer-file-name": upload_filename,
                },
                fields={
                    "filename": upload_filename,
                    "metadata": json.dumps({"timestamp": upload_timestamp}),
                },
                timeout_sec=timeout_sec,
                chunk_size=chunk_size,
            )
            outcome.phases["upload_sec"] = round(time.monotonic() - step, 3)
            outcome.http_status = status
            outcome.response = body

            if 200 <= status < 300:
                sensor_id = str(body.get("sensorId") or "").strip()
                if not sensor_id:
                    # Without a sensor id the per-file check has nothing to key
                    # on, so a 200 here is not enough to call the upload good.
                    outcome.error = f"VST returned no sensorId: {raw[:200]}"
                else:
                    outcome.success = True
                    outcome.sensor_id = sensor_id
                    outcome.error = ""
                    break
            else:
                outcome.error = f"HTTP {status}: {raw[:300]}"
                duplicate_asset = _looks_like_duplicate_asset(status, raw)
        except Exception as e:  # noqa: BLE001 - reported as an upload failure
            outcome.error = str(e)

        if attempt < max_retries:
            outcome.retry_count += 1
            backoff = (
                duplicate_asset_backoff_sec if duplicate_asset else retry_backoff_sec
            )
            if duplicate_asset:
                # Named rather than buried in the generic HTTP line, because the
                # retry's success tells us nothing about whether the sensor id
                # is fresh or a prior one handed back -- so this is the signal
                # the dedup check in prepare_corpus will care about.
                say(f"    [WARN] {file_path.name} rejected as DuplicateAsset by the "
                    f"embed service; the content is already present. Waiting "
                    f"{backoff:.0f}s so a retry can either land as a new sensor or "
                    f"confirm the id is reused.")
            say(f"    [RETRY] {file_path.name} attempt {attempt + 1}/"
                f"{max_retries + 1} failed ({outcome.error[:120]}); retrying in "
                f"{backoff:.0f}s")
            time.sleep(backoff)

    outcome.duration_sec = time.monotonic() - start
    outcome.completed_at = _utc_now_iso()
    return outcome


def upload_videos(
    videos: list[Path],
    concurrency: int,
    upload: Callable[[int, Path], UploadOutcome],
) -> list[UploadOutcome]:
    """Upload the corpus, up to ``concurrency`` videos at a time.

    Results come back in upload order rather than completion order, so the
    record reads the same whether this ran one at a time or all at once.

    Parallelism here buys wall-clock time on the transfer, not on the
    pipeline: perception runs at roughly real time per stream, so five
    concurrent uploads of a 30-minute video still cost about 30 minutes of
    indexing if the deployment has the capacity to run them side by side, and
    about two and a half hours if it does not. The gate below does not care
    which happened -- it waits for documents either way.
    """
    if concurrency <= 1 or len(videos) <= 1:
        return [upload(index, video) for index, video in enumerate(videos)]

    outcomes: list[UploadOutcome | None] = [None] * len(videos)
    with ThreadPoolExecutor(max_workers=min(concurrency, len(videos))) as pool:
        futures = {
            pool.submit(upload, index, video): index
            for index, video in enumerate(videos)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                outcomes[index] = future.result()
            except Exception as e:  # noqa: BLE001 - recorded, not raised
                # A thread that died still has to appear in the record, or the
                # upload count silently disagrees with the corpus size.
                outcomes[index] = UploadOutcome(
                    source_path=str(videos[index]),
                    upload_filename="",
                    error=f"upload thread failed: {type(e).__name__}: {e}",
                    completed_at=_utc_now_iso(),
                )
    return [o for o in outcomes if o is not None]


def count_video_docs(
    elasticsearch_url: str,
    targets: list[VideoTarget],
    gate_indices: dict[str, Any],
) -> None:
    """Refresh every target's ``observed`` counts in place.

    One term query per (video, index). At five videos and two indices that is
    ten queries a poll, which Elasticsearch answers in single-digit
    milliseconds. A query that fails leaves the previous count alone rather
    than writing a zero: a 503 mid-ingest is not evidence the documents went
    away.

    The query key per index is picked from the entry's ``sensor_key``: embed
    indices want VST's UUID, nvstreamer-written indices want the filename stem.
    """
    entries = normalize_gate_indices(gate_indices)
    for target in targets:
        for pattern, entry in entries.items():
            key = _sensor_key_for(target, entry)
            if not key:
                continue
            observed = count_docs_for_sensor(
                elasticsearch_url, pattern, entry.field, key
            )
            if observed is not None:
                target.observed[pattern] = observed


def converge_expected(
    targets: list[VideoTarget],
    gate_indices: dict[str, Any],
    min_ratio: float = 1.0,
) -> None:
    """Set each video's expected counts to the best any video has achieved.

    Used when the config does not say what a video owes. Every copy is the
    same file, so whatever the furthest-along copy has produced is what all of
    them must produce -- which turns the other four videos into the check on
    the fifth.

    This is weaker than a configured number and the report says so. It cannot
    distinguish "all five finished" from "all five stalled together at the
    same point", which is why the quiet window still has to pass afterwards.
    A pattern no video has written yet stays at zero, and a zero expectation
    is never met.
    """
    entries = normalize_gate_indices(gate_indices)
    for pattern in entries:
        best = max((t.observed.get(pattern, 0) for t in targets), default=0)
        required = int(best * min_ratio)
        for target in targets:
            target.expected[pattern] = required


def wait_for_es_settled(
    elasticsearch_url: str,
    index_patterns: list[str],
    baseline: dict[str, Any],
    targets: list[VideoTarget] | None = None,
    gate_indices: dict[str, Any] | None = None,
    expected_from_config: bool = False,
    min_ratio: float = 1.0,
    poll_interval_sec: float = 5.0,
    stable_for_sec: float = 300.0,
    timeout_sec: float = 14400.0,
    sensor_field_grace_sec: float = 180.0,
) -> dict[str, Any]:
    """Poll ES until every uploaded video is fully indexed, then until it stops.

    Two conditions, both required:

    ``videos``  every uploaded video holds its expected document count in
                every gated index, counted under its own ``sensor_id``. This
                is what makes "ingest finished" a statement about each video
                rather than about a total -- a total is satisfied by five
                videos' worth of documents belonging to four sensors.
    ``quiet``   the counts then stop changing for ``stable_for_sec``. Catches
                the ungated indices still finishing, and a video still
                producing past its expected count. The timer only starts once
                every video is complete, so an inter-video gap cannot feed it.

    With ``expected_from_config`` false the targets are empty on entry and get
    filled in by :func:`converge_expected` on every poll: the furthest-along
    copy sets the bar for all of them. Honest about what that is worth -- it
    cannot tell "all finished" from "all stalled together", and the quiet
    window is what covers the difference.
    """
    targets = list(targets or [])
    entries = normalize_gate_indices(gate_indices or {})

    result: dict[str, Any] = {
        "enabled": True,
        "started_at": _utc_now_iso(),
        "poll_interval_sec": poll_interval_sec,
        "stable_for_sec": stable_for_sec,
        "timeout_sec": timeout_sec,
        "polls": 0,
        "success": False,
        "status": "timeout",
        "targets_total": len(targets),
        # Serialize the normalized form (field + sensor_key per pattern) so the
        # written state records which identifier each index was queried with.
        "gate_indices": {
            pattern: {"field": entry.field, "sensor_key": entry.sensor_key}
            for pattern, entry in entries.items()
        },
        "expected_source": "config" if expected_from_config else "converged",
        "min_ratio": min_ratio,
        "video_count": len(targets),
    }

    # With no sensor ids there is nothing to count per video, and the whole
    # gate is per video. The run is not blocked -- a sweep against a
    # pre-ingested corpus is legitimate -- but the result has to say that the
    # only thing left is a quiet window, which is the check that has been
    # fooled before.
    if not targets:
        result["degraded"] = True
        result["degraded_reason"] = (
            "no successful upload reported a sensor_id, so no video could be "
            "checked individually and completion rests on the quiet window alone"
        )

    baseline_counts = {p: (_doc_count(baseline, p) or 0) for p in index_patterns}
    deadline = time.monotonic() + timeout_sec
    started = time.monotonic()
    counts: dict[str, int] = {}
    last_counts: dict[str, int] = {}
    unchanged_since: float | None = None
    ready_since: float | None = None
    grew = False
    announced_targets = False
    # One heartbeat line per minute, so the log answers "is the job still alive"
    # without burying the completion lines in poll noise. An hour of silence
    # during the quiet window is indistinguishable from a dead log stream.
    heartbeat_interval_sec = 60.0
    last_heartbeat = time.monotonic()

    def _snapshot() -> dict[str, Any]:
        return {
            "final_counts": last_counts,
            "baseline_counts": baseline_counts,
            "targets_met": sum(1 for t in targets if t.met),
            "targets": [
                {
                    "upload_filename": t.upload_filename,
                    "sensor_id": t.sensor_id,
                    "expected": dict(t.expected),
                    "observed": dict(t.observed),
                    "met": t.met,
                }
                for t in targets
            ],
            "completed_at": _utc_now_iso(),
        }

    while time.monotonic() < deadline:
        result["polls"] += 1
        current = capture_es_index_counts(elasticsearch_url, index_patterns)
        raw_counts = {p: _doc_count(current, p) for p in index_patterns}
        if any(c is None for c in raw_counts.values()):
            # An unanswered count is not a quiet one: two failed polls compare
            # equal, and the quiet window would run out on an outage.
            result["unanswered_polls"] = result.get("unanswered_polls", 0) + 1
            unchanged_since = None
            time.sleep(min(poll_interval_sec, max(0.0, deadline - time.monotonic())))
            continue
        counts = {p: int(c or 0) for p, c in raw_counts.items()}

        count_video_docs(elasticsearch_url, targets, entries)
        if not expected_from_config:
            converge_expected(targets, entries, min_ratio)

        if not grew and any(counts[p] > baseline_counts[p] for p in index_patterns):
            grew = True
            say("    Indexing started: "
                + ", ".join(f"{p}={counts[p]:,}" for p in index_patterns))

        # A sensor field that does not exist answers 0 for every video forever,
        # which is indistinguishable from "nothing indexed yet" except that the
        # index total is climbing. Say which field is wrong instead of burning
        # the full timeout to report a generic stall. Checked per pattern
        # because the two gated indices do not agree on the field name --
        # mdx-raw-* keys on sensorId, the embed index on sensor.id.
        if targets and (time.monotonic() - started) >= sensor_field_grace_sec:
            for pattern, entry in entries.items():
                total = counts.get(pattern, 0)
                # Growth, not presence: documents already there before this
                # ingest belong to no uploaded sensor and prove nothing.
                grown = total > baseline_counts.get(pattern, 0)
                if grown and all(t.observed.get(pattern, 0) == 0 for t in targets):
                    result.update(_snapshot())
                    result.update({
                        "status": "sensor_field_mismatch",
                        "error": (
                            f"{pattern} holds {total:,} documents but a term query "
                            f"on '{entry.field}' (keyed by {entry.sensor_key}) "
                            f"matched none of the "
                            f"{len(targets)} uploaded sensor_id(s) after "
                            f"{sensor_field_grace_sec:.0f}s. The field name or "
                            f"the sensor_key is probably wrong for this mapping -- "
                            f"check corpus.ingest.readiness.gate_indices."
                        ),
                    })
                    return result

        # `all` of nothing is true, which is the degraded path above: with no
        # sensor ids to check, the quiet window is all that is left, and
        # `evidence` then requires growth so it cannot pass on an untouched index.
        targets_met = all(t.met for t in targets)

        # Having watched the counts climb is one way to know ingestion happened;
        # every video holding its full count is a better one, and it does not
        # depend on when the baseline was read. Without this, a corpus that
        # finished indexing before the baseline snapshot -- a fast re-ingest, or
        # a retry whose data had already landed -- is reported as "no growth"
        # and a complete corpus gets rejected.
        evidence = grew or (targets_met and bool(targets))

        if targets_met:
            if ready_since is None:
                ready_since = time.monotonic()
                if not announced_targets:
                    announced_targets = True
                    say(
                        (f"    All {len(targets)} video(s) complete ("
                         + ", ".join(
                             f"{p}={targets[0].expected.get(p, 0):,} each"
                             for p in entries
                         )
                         + ")" if targets else
                         "    No video could be checked individually")
                        + f"; waiting {stable_for_sec:.0f}s of quiet"
                    )
            if counts == last_counts:
                if unchanged_since is None:
                    unchanged_since = time.monotonic()
                elif evidence and (time.monotonic() - unchanged_since) >= stable_for_sec:
                    result.update(_snapshot())
                    result.update({"success": True, "status": "settled"})
                    return result
            else:
                unchanged_since = None
        else:
            # Falling back out of the ready state discards the quiet timer: the
            # pipeline resumed, so whatever quiet was accumulated described a gap.
            ready_since = None
            unchanged_since = None

        if (time.monotonic() - last_heartbeat) >= heartbeat_interval_sec:
            last_heartbeat = time.monotonic()
            met = sum(1 for t in targets if t.met)
            quiet_for = (
                int(time.monotonic() - unchanged_since)
                if unchanged_since is not None else 0
            )
            # Per pattern, the slowest video against the bar, because the bar
            # is per video and a total would hide one copy stuck at zero.
            progress = ", ".join(
                f"{p}=" + (
                    f"{min((t.observed.get(p, 0) for t in targets), default=0):,}"
                    f"/{targets[0].expected.get(p, 0):,} slowest"
                    if targets else f"{counts.get(p, 0):,}"
                )
                for p in entries
            )
            say(f"    [{int(time.monotonic() - started):>5}s poll {result['polls']:>4}] "
                f"videos {met}/{len(targets)}, {progress}"
                + (f", quiet {quiet_for}/{int(stable_for_sec)}s" if ready_since else ""))

        last_counts = counts
        time.sleep(min(poll_interval_sec, max(0.0, deadline - time.monotonic())))

    unmet = [t for t in targets if not t.met]
    result.update(_snapshot())
    # "Nothing was indexed" and "indexing never finished" are different problems
    # with different fixes, so they get different statuses. Every video holding
    # its count rules out the first even when no growth was observed -- which
    # is the pre-existing-corpus case, and why `unmet` is checked rather than
    # `grew` alone. With no videos to check there is no such evidence.
    if not grew and (unmet or not targets):
        result.update({
            "status": "no_growth",
            "error": (
                f"no new documents appeared within {timeout_sec:.0f}s -- "
                "the ingestion pipeline may not be running"
            ),
        })
        return result

    reasons: list[str] = []
    if unmet:
        reasons.append(
            f"{len(unmet)}/{len(targets)} video(s) never reached their document "
            f"count ("
            + "; ".join(
                f"{t.upload_filename}: {t.shortfall()}" for t in unmet[:5]
            )
            + ")"
        )
    if not reasons:
        reasons.append(f"the totals never held still for {stable_for_sec:.0f}s")
    result.update({
        "status": "timeout",
        "error": f"ingest did not finish within {timeout_sec:.0f}s: " + "; ".join(reasons),
    })
    return result


def verify_corpus(
    elasticsearch_url: str,
    index_patterns: list[str],
    require_min_docs: dict[str, int],
    counts: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Check that the search indices hold enough documents to benchmark against.

    Returns ``(passed, message, counts)``. A kNN query against an empty index
    returns almost instantly, so running the sweep in that state would produce
    fast but meaningless latency numbers. Pass ``counts`` to judge a snapshot
    already taken, so the gate and the recorded figures are the same read.
    """
    if counts is None:
        counts = capture_es_index_counts(elasticsearch_url, index_patterns)

    if not counts.get("enabled"):
        return False, (
            "cannot verify the corpus: set services.elasticsearch_url and "
            "corpus.es_index_patterns in the config"
        ), counts

    problems: list[str] = []
    for pattern, minimum in require_min_docs.items():
        info = (counts.get("indices") or {}).get(pattern)
        if not isinstance(info, dict):
            problems.append(f"{pattern}: not queried (missing from es_index_patterns)")
        elif "error" in info:
            problems.append(f"{pattern}: query failed ({info['error']})")
        elif int(info["doc_count"]) < int(minimum):
            problems.append(
                f"{pattern}: {int(info['doc_count']):,} docs < required {int(minimum):,}"
            )

    if problems:
        return False, "; ".join(problems), counts
    if not require_min_docs:
        return True, ("corpus NOT verified: corpus.require_min_docs is empty, so "
                      "an empty index would pass"), counts
    return True, "corpus verified", counts


def _check_foreign_docs(
    es_url: str,
    targets: list[VideoTarget],
    embed_pattern: str,
    entry: GateEntry,
) -> dict[str, Any]:
    """Report embed documents belonging to no file this sweep uploaded.

    The ``known`` list must match the index's ``sensor_key`` or the exclusivity
    check is vacuous: querying the embed index with filename stems while the
    index holds UUIDs would mark every own-upload document "foreign" and refuse
    to run.
    """
    known = [
        _sensor_key_for(t, entry) for t in targets
        if _sensor_key_for(t, entry)
    ]
    report: dict[str, Any] = {
        "index_pattern": embed_pattern,
        "sensor_field": entry.field,
        "sensor_key_source": entry.sensor_key,
        "known_sensor_ids": len(known),
        "count": 0,
    }
    if not known:
        report["checked"] = False
        report["reason"] = "no sensor ids to compare against"
        return report
    found = count_foreign_docs(es_url, embed_pattern, entry.field, known)
    if found is None:
        report["checked"] = False
        report["reason"] = "Elasticsearch did not answer the exclusivity query"
        return report
    report["checked"] = True
    report["count"] = found
    return report


def _write_corpus_state(output_dir: Path, state: CorpusState) -> None:
    (output_dir / "corpus_state.json").write_text(
        json.dumps(asdict(state), indent=2, default=str) + "\n", encoding="utf-8"
    )


def prepare_corpus(
    config: dict[str, Any],
    output_dir: Path,
    skip_ingest: bool = False,
    purge: bool | None = None,
) -> CorpusState:
    """Optionally ingest videos, then gate on the resulting index state."""
    corpus_config = config.get("corpus", {}) or {}
    ingest_config = corpus_config.get("ingest", {}) or {}
    index_patterns = corpus_config.get("es_index_patterns", []) or []
    require_min_docs = corpus_config.get("require_min_docs", {}) or {}
    es_url = config.get("services", {}).get("elasticsearch_url", "")
    agent_url = config.get("services", {}).get("vss_agent_url", "")

    state = CorpusState(checked_at=_utc_now_iso())
    ingest_enabled = bool(ingest_config.get("enabled", False)) and not skip_ingest
    state.ingest_enabled = ingest_enabled

    state.doc_counts_before = capture_es_index_counts(es_url, index_patterns)
    for pattern in index_patterns:
        count = _doc_count(state.doc_counts_before, pattern)
        say(f"  ES {pattern}: {count:,} docs" if count is not None
            else f"  ES {pattern}: unavailable")

    if ingest_enabled:
        videos = resolve_ingest_videos(ingest_config)
        if not videos:
            state.gate_message = (
                "corpus.ingest.enabled is true but corpus.ingest.video is empty"
            )
            _write_corpus_state(output_dir, state)
            return state

        counts_before = {p: _doc_count(state.doc_counts_before, p) for p in index_patterns}
        docs_before = sum(c or 0 for c in counts_before.values())
        # An index ES could not count is not an empty one.
        uncounted = sorted(p for p, c in counts_before.items() if c is None)
        # Taken at the point of use, like the other --skip flags, so the config
        # written into the results directory still records what was configured.
        purge_requested = (
            bool(ingest_config.get("purge_before_ingest", True)) if purge is None
            else purge
        )
        if purge_requested:
            # The patterns are named, not counted. This is the last line before
            # a delete, so it has to say what is about to go.
            say(f"  Clearing {', '.join(index_patterns)} ({docs_before:,} documents) "
                f"so the ingest is the only data present...")
            state.purge = purge_corpus_indices(
                elasticsearch_url=es_url,
                index_patterns=index_patterns,
                protected_indices=[str(lock_config(config)["index"])],
                require_templates=bool(
                    ingest_config.get("purge_requires_templates", True)
                ),
                settle_sec=float(ingest_config.get("purge_settle_sec", 30.0)),
            )
            if not state.purge.get("success"):
                state.gate_message = (
                    f"could not clear Elasticsearch before ingesting: "
                    f"{state.purge.get('error', 'unknown error')} Ingesting now "
                    f"would add a second copy of the corpus under new sensor ids, "
                    f"which no later check can separate from the first. Set "
                    f"corpus.ingest.purge_before_ingest: false only if you intend "
                    f"to ingest on top of what is already there."
                )
                say(f"    [FAIL] {state.gate_message}")
                _write_corpus_state(output_dir, state)
                return state
            deleted = state.purge.get("deleted") or []
            say(f"    [OK] Elasticsearch cleared ({len(deleted)} index(es) deleted, "
                f"{docs_before:,} documents)")
            # The baseline the readiness wait measures growth against has to be
            # the state the ingest actually starts from. Leaving the pre-purge
            # counts here would ask the new corpus to exceed a corpus that no
            # longer exists, so nothing would ever look like growth.
            state.purge["doc_counts_before_purge"] = state.doc_counts_before
            state.doc_counts_before = state.purge.get("doc_counts_after_purge") or {}
        elif (docs_before > 0 or uncounted) and bool(
            ingest_config.get("require_empty_before_ingest", True)
        ):
            # Counts alone cannot catch this later: a second ingest arrives under
            # sensor ids of its own, so every per-file target is met and every
            # floor is cleared by a corpus holding each video twice.
            held = (
                f"could not count {', '.join(uncounted)}" if uncounted
                else f"already holds {docs_before:,} document(s)"
            )
            state.gate_message = (
                f"Elasticsearch {held} and the "
                f"purge is disabled, so this ingest would be added on top of them. "
                f"Every upload gets a new filename and so a new sensor_id, which "
                f"means the duplicate copies cannot be told apart afterwards. "
                f"Enable corpus.ingest.purge_before_ingest, sweep with "
                f"--skip-ingest against the corpus already loaded, or set "
                f"corpus.ingest.require_empty_before_ingest: false to accept a "
                f"mixed corpus."
            )
            say(f"    [FAIL] {state.gate_message}")
            _write_corpus_state(output_dir, state)
            return state

        timeout_sec = int(ingest_config.get("timeout_sec", 3600))
        chunk_size = int(ingest_config.get("upload_chunk_size_bytes", 1024 * 1024))
        max_retries = int(ingest_config.get("max_retries", 1))
        readiness_config = ingest_config.get("readiness", {}) or {}
        min_ratio = float(readiness_config.get("min_ratio", 1.0))
        # Pattern -> field + sensor_key. The two gated indices disagree on both
        # the field holding the sensor id AND on which identifier of a target
        # that field holds: mdx-raw-* uses `sensorId` with the filename stem;
        # the embed index uses `sensor.id` with the VST UUID. One shared field
        # name or one shared key source would silently count zero for one of
        # them. The default matches the shipped Helm chart.
        gate_indices = normalize_gate_indices(
            readiness_config.get("gate_indices") or DEFAULT_GATE_INDICES
        )
        configured_per_video = readiness_config.get("expected_docs_per_video") or {}
        expected_from_config = all(
            int(configured_per_video.get(p) or 0) > 0 for p in gate_indices
        )

        def _upload(index: int, video: Path) -> UploadOutcome:
            size_mib = video.stat().st_size / 1024 / 1024 if video.exists() else 0
            say(f"    Uploading {video.name} ({size_mib:,.1f} MiB) "
                f"[{index + 1}/{len(videos)}]...")
            outcome = upload_one_video(
                agent_endpoint=agent_url,
                file_path=video,
                timeout_sec=timeout_sec,
                url_timeout_sec=int(ingest_config.get("url_timeout_sec", 30)),
                chunk_size=chunk_size,
                max_retries=max_retries,
                retry_backoff_sec=float(ingest_config.get("retry_backoff_sec", 5.0)),
                duplicate_asset_backoff_sec=float(
                    ingest_config.get("duplicate_asset_backoff_sec", 30.0)
                ),
                sequence=index,
                upload_timestamp=str(
                    ingest_config.get("upload_timestamp", DEFAULT_UPLOAD_TIMESTAMP)
                ),
            )
            if outcome.success:
                say(f"    [OK] {video.name} uploaded in {outcome.duration_sec:.1f}s "
                    f"(sensorId={outcome.sensor_id})")
            else:
                say(f"    [FAIL] {video.name}: {outcome.error[:200]}")
            return outcome

        upload_concurrency = max(
            1, int(ingest_config.get("upload_concurrency", len(videos)) or 1)
        )
        say(f"  Ingesting {len(videos)} video(s) into {agent_url}"
            + (f", {upload_concurrency} at a time" if upload_concurrency > 1 else ""))
        state.uploads = upload_videos(videos, upload_concurrency, _upload)

        # Any failure, not every failure. A sweep that uploaded four of five
        # videos once passed this gate, so it measured a corpus a fifth smaller
        # than the sweep it was compared against and nothing in the report said
        # so. A partial corpus is a different experiment, not a smaller one.
        failed_uploads = [u for u in state.uploads if not u.success]
        if failed_uploads and bool(ingest_config.get("require_all_uploads", True)):
            state.gate_message = (
                f"{len(failed_uploads)}/{len(state.uploads)} upload(s) failed, so "
                f"the corpus would be smaller than configured and not comparable "
                f"with other sweeps: "
                + "; ".join(f"{u.source_path.rsplit('/', 1)[-1]}: {u.error[:120]}"
                            for u in failed_uploads[:5])
                + ". Re-run, or set corpus.ingest.require_all_uploads: false to "
                  "accept a partial corpus deliberately."
            )
            say(f"    [FAIL] {state.gate_message}")
            state.doc_counts_after = capture_es_index_counts(es_url, index_patterns)
            _write_corpus_state(output_dir, state)
            return state
        if not any(u.success for u in state.uploads):
            state.gate_message = (
                "every upload failed -- cannot benchmark against an empty corpus"
            )
            _write_corpus_state(output_dir, state)
            return state

        # Content-based dedup by the embed service. This corpus is one file
        # uploaded repeatedly, so it is exactly the case a content-keyed service
        # would collapse -- and the collapse is only visible by comparing sensor
        # ids. Once two uploads share one, the config claims N videos, the corpus
        # has fewer, and no later check can tell the difference.
        duplicates = find_duplicate_sensor_ids(state.uploads)
        if duplicates and bool(ingest_config.get("require_unique_sensor_ids", True)):
            lines = [
                f"{sid[:40]}... used by {len(files)} upload(s): {', '.join(files[:3])}"
                for sid, files in list(duplicates.items())[:5]
            ]
            state.gate_message = (
                f"the agent returned the same sensor_id to "
                f"{sum(len(f) for f in duplicates.values())} upload(s), so the embed "
                f"service deduplicated them by content and the corpus is smaller "
                f"than the {len(state.uploads)}-video config. "
                + "; ".join(lines)
                + ". Check that the VIOS webhook is disabled on this deployment, "
                  "point corpus.ingest.video at distinct files, or set "
                  "corpus.ingest.require_unique_sensor_ids: false to measure a "
                  "smaller corpus than the config claims."
            )
            say(f"    [FAIL] {state.gate_message}")
            state.doc_counts_after = capture_es_index_counts(es_url, index_patterns)
            _write_corpus_state(output_dir, state)
            return state

        embed_pattern = str(
            readiness_config.get("embed_index_pattern", "mdx-embed-filtered-*")
        )
        embed_entry = gate_indices.get(embed_pattern) or GateEntry(
            field="sensor.id.keyword", sensor_key=VST_UUID,
        )
        uploaded = sum(1 for u in state.uploads if u.success)

        state.video_targets = build_video_targets(
            uploads=state.uploads,
            gate_indices=gate_indices,
            expected_docs_per_video=configured_per_video,
            min_ratio=min_ratio,
        )
        unchecked = uploaded - len(state.video_targets)
        if unchecked > 0:
            say(f"    ! {unchecked} successful upload(s) reported no sensor_id; "
                f"their completion cannot be checked individually")

        if expected_from_config and state.video_targets:
            say("  Waiting for every video to reach "
                + ", ".join(
                    f"{p}={n:,}" for p, n in state.video_targets[0].expected.items()
                )
                + " documents under its own sensorId")
        else:
            # Said plainly, because it changes what a pass is worth. The bar is
            # set by whichever copy gets furthest, so the gate cannot tell five
            # finished videos from five that stalled in step.
            say("  Waiting for all "
                f"{len(state.video_targets)} video(s) to agree on their document "
                "counts (no expected_docs_per_video configured, so the furthest-"
                "along copy sets the bar), then for the counts to stop changing")

        state.readiness = wait_for_es_settled(
            elasticsearch_url=es_url,
            index_patterns=index_patterns,
            baseline=state.doc_counts_before,
            targets=state.video_targets,
            gate_indices=gate_indices,
            expected_from_config=expected_from_config,
            min_ratio=min_ratio,
            poll_interval_sec=float(readiness_config.get("poll_interval_sec", 5.0)),
            stable_for_sec=float(readiness_config.get("stable_for_sec", 300.0)),
            timeout_sec=float(readiness_config.get("timeout_sec", 14400.0)),
            sensor_field_grace_sec=float(
                readiness_config.get("sensor_field_grace_sec", 180.0)
            ),
        )
        if state.readiness.get("success"):
            met = state.readiness.get("targets_met", 0)
            total = state.readiness.get("targets_total", 0)
            per_video = state.video_targets[0].expected if state.video_targets else {}
            say(f"    [OK] Ingest complete after {state.readiness['polls']} poll(s): "
                f"{met}/{total} video(s) hold "
                + ", ".join(f"{p}={n:,}" for p, n in per_video.items())
                + " documents each")

            # Every copy is the same file, so every copy owes the same count.
            # One copy sitting well above the rest means its content was
            # processed twice -- the only way that happens is a stream
            # registered more than once, and no later count can separate the
            # two passes.
            for pattern in gate_indices:
                observed = [t.observed.get(pattern, 0) for t in state.video_targets]
                if observed and min(observed) > 0 and max(observed) > min(observed) * 1.05:
                    say(f"    ! {pattern} is uneven across identical videos: "
                        f"{min(observed):,} to {max(observed):,}. The copies are "
                        f"the same file, so they should produce the same count; "
                        f"a high outlier usually means that stream was "
                        f"registered twice.")
            if state.readiness.get("degraded"):
                say(f"    ! {state.readiness.get('degraded_reason', '')}")

            state.foreign_docs = _check_foreign_docs(
                es_url=es_url,
                targets=state.video_targets,
                embed_pattern=embed_pattern,
                entry=embed_entry,
            )
            exclusive = bool(ingest_config.get("require_exclusive_corpus", True))
            if exclusive and not state.foreign_docs.get("checked"):
                state.gate_message = (
                    f"could not check {embed_pattern} for documents from other "
                    f"ingests ({state.foreign_docs.get('reason', 'no answer')}), so "
                    "the corpus cannot be shown to be this sweep's alone. Set "
                    "corpus.ingest.require_exclusive_corpus: false to skip the check."
                )
                say(f"    [FAIL] {state.gate_message}")
                state.doc_counts_after = capture_es_index_counts(es_url, index_patterns)
                _write_corpus_state(output_dir, state)
                return state
            if state.foreign_docs.get("count"):
                found = state.foreign_docs["count"]
                say(f"    ! {found:,} document(s) in "
                    f"{state.foreign_docs['index_pattern']} belong to no file this "
                    f"sweep uploaded")
                if bool(ingest_config.get("require_exclusive_corpus", True)):
                    state.gate_message = (
                        f"the corpus is not exclusively this sweep's: {found:,} "
                        f"document(s) in {state.foreign_docs['index_pattern']} carry "
                        f"a {state.foreign_docs['sensor_field']} this sweep never "
                        f"uploaded. Likely a Kafka backlog from an earlier aborted "
                        f"ingest draining in, or a second ingest against the same "
                        f"cluster. Searches would score against data the report does "
                        f"not describe. Set corpus.ingest.require_exclusive_corpus: "
                        f"false to accept it."
                    )
                    say(f"    [FAIL] {state.gate_message}")
                    state.doc_counts_after = capture_es_index_counts(
                        es_url, index_patterns
                    )
                    _write_corpus_state(output_dir, state)
                    return state
        else:
            # The doc-count gate cannot stand in for this: require_min_docs is a
            # floor, so a corpus still being indexed clears it easily. Starting
            # the sweep then would measure a growing index, which is exactly the
            # drift the readiness wait exists to prevent.
            state.gate_message = (
                f"ingest did not complete ({state.readiness.get('status', 'unknown')}): "
                f"{state.readiness.get('error', 'readiness wait did not succeed')} "
                "The index was still changing, so the sweep would measure a moving "
                "corpus. Raise corpus.ingest.readiness.timeout_sec, or re-run with "
                "--skip-ingest once indexing has finished."
            )
            say(f"    [FAIL] {state.gate_message}")
            state.doc_counts_after = capture_es_index_counts(es_url, index_patterns)
            _write_corpus_state(output_dir, state)
            return state
    else:
        reason = "--skip-ingest" if skip_ingest else "corpus.ingest.enabled=false"
        say(f"  Ingest skipped ({reason}) -- using the corpus already in Elasticsearch")

    state.doc_counts_after = capture_es_index_counts(es_url, index_patterns)
    passed, message, _ = verify_corpus(
        es_url, index_patterns, require_min_docs, counts=state.doc_counts_after
    )
    state.gate_passed = passed
    state.gate_message = message

    _write_corpus_state(output_dir, state)
    return state


def record_corpus_drift(
    config: dict[str, Any],
    output_dir: Path,
    tolerance_docs: int = 0,
) -> dict[str, Any]:
    """Re-count the indices after the sweep and record any drift.

    A single sample taken before the first run cannot show that the index the
    sweep measured was the index the gate approved. One sweep ran its whole
    critic-off phase with logstash still indexing throughout, and the recorded
    fingerprint described none of it.

    Called after the runs regardless of how they ended, so an aborted sweep
    still reports what it measured against. Never raises: this is a diagnostic,
    and losing it must not lose the results.
    """
    corpus_config = config.get("corpus", {}) or {}
    index_patterns = corpus_config.get("es_index_patterns", []) or []
    es_url = config.get("services", {}).get("elasticsearch_url", "")

    drift: dict[str, Any] = {
        "checked_at": _utc_now_iso(), "drifted": False, "indices": {},
    }
    state_path = output_dir / "corpus_state.json"
    try:
        at_end = capture_es_index_counts(es_url, index_patterns)
        before: dict[str, Any] = {}
        if state_path.exists():
            before = json.loads(state_path.read_text(encoding="utf-8")) or {}
        approved = before.get("doc_counts_after") or {}

        for pattern in index_patterns:
            start = _doc_count(approved, pattern)
            end = _doc_count(at_end, pattern)
            entry: dict[str, Any] = {"at_gate": start, "at_end": end}
            if start is not None and end is not None:
                entry["delta"] = end - start
                entry["drifted"] = abs(end - start) > tolerance_docs
                if entry["drifted"]:
                    drift["drifted"] = True
            drift["indices"][pattern] = entry

        before["doc_counts_at_end"] = at_end
        before["corpus_drift"] = drift
        state_path.write_text(
            json.dumps(before, indent=2, default=str) + "\n", encoding="utf-8"
        )
    except Exception as e:  # noqa: BLE001 - a diagnostic must not fail the sweep
        logger.warning("could not record corpus drift: %s", e)
        drift["error"] = str(e)
    return drift
