# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Elasticsearch readiness using the harness's completion and count conventions.

CLI success confirms VIOS media availability. VIOS webhooks dispatch inference;
completion requires the upload's RT-CV raw frames and RT-Embed chunks in ES.

Client latency includes the wait until a poll observes readiness. The separate
indexing timestamp uses matched documents' max(ingested_at), falling back to
client confirmation time when unavailable. Throughput uses that indexing time;
latency percentiles retain the client-observed wait, including polling delay.

All ES reads use the deployment's public endpoint. No internal telemetry is
collected, and the CLI/webhook path needs its own performance baseline. See
references/completion-and-accounting.md for identities and readiness tolerances.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
import math
import time
from typing import Any

from httpio import request_json

DEFAULT_ES_URL = "http://elasticsearch:9200"
DEFAULT_EMBED_INDEX = "mdx-embed-filtered-2025-01-01"
DEFAULT_RAW_INDEX = "mdx-raw-2025-01-01"
DEFAULT_EMBED_CHUNK_DURATION_SEC = 5
DEFAULT_FRAME_PROCESSING_TIME_MS = 33
DEFAULT_ENGINE_WARMUP_SEC = 300

#: Base idle window. Used directly to confirm an already near-complete raw
#: stream has settled, and multiplied by concurrency to size the give-up
#: windows, since raw indexing lag grows with simultaneous streams.
DEFAULT_RAW_DROP_GRACE_SEC = 120

#: How long embed may produce nothing while RT-CV is demonstrably writing.
DEFAULT_EMBED_DEAD_AFTER_SEC = 900

#: Fraction of expected_frames that counts as "RT-CV finished" once the raw
#: count stops advancing. RT-CV runs with live-source=1, which drops late frames
#: rather than queueing them, so the exact count is not reliably reachable.
DEFAULT_RAW_COMPLETION_RATIO = 0.95

#: How long raw may produce nothing at all, once embed has completed, before the
#: wait calls the stream refused rather than slow.
DEFAULT_RAW_ABSENT_ABORT_SEC = 600

#: RT-CV lands a few frames short of ceil(duration*fps). At a tighter tolerance
#: raw_met could never become true and readiness would only ever succeed through
#: the settle fallback.
FRAME_TOLERANCE = 15


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def duration_between(started_at: str, completed_at: str) -> float:
    """Seconds between two ISO timestamps, or 0.0 when either is unusable."""
    start, end = parse_timestamp(started_at), parse_timestamp(completed_at)
    if start is None or end is None:
        return 0.0
    return max(0.0, (end - start).total_seconds())


@dataclass
class EsReadinessConfig:
    """Resolved readiness settings. Mirrors the harness ``es_readiness`` block."""

    elasticsearch_url: str = DEFAULT_ES_URL
    embed_index: str = DEFAULT_EMBED_INDEX
    raw_index: str = DEFAULT_RAW_INDEX
    frame_processing_time_ms: int = DEFAULT_FRAME_PROCESSING_TIME_MS
    embed_chunk_duration_sec: int = DEFAULT_EMBED_CHUNK_DURATION_SEC
    poll_interval_sec: float = 2.0
    raw_drop_grace_sec: int = DEFAULT_RAW_DROP_GRACE_SEC
    raw_completion_ratio: float = DEFAULT_RAW_COMPLETION_RATIO
    raw_absent_abort_sec: int = DEFAULT_RAW_ABSENT_ABORT_SEC
    embed_dead_after_sec: int = DEFAULT_EMBED_DEAD_AFTER_SEC
    #: Hard ceiling. 0 means "use the computed per-upload budget".
    timeout_override_sec: float = 0.0
    request_timeout_sec: float = 60.0

    def indices(self) -> str:
        return f"{self.raw_index},{self.embed_index}"


@dataclass
class ReadinessResult:
    """Outcome of one readiness wait, plus the counters behind it."""

    success: bool
    started_at: str
    completed_at: str
    latency_sec: float
    attempts: int
    error: str = ""
    expected_frames: int = 0
    es_frame_count: int = 0
    expected_chunks: int = 0
    es_chunk_count: int = 0
    raw_completion_ratio: float = 0.0
    raw_complete_exact: bool = False
    rt_cv_dropped: bool = False
    rt_cv_absent: bool = False
    rt_embed_dropped: bool = False
    drop_reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


def search_url(config: EsReadinessConfig) -> str:
    """Search across raw and embed indices without failing when one is missing.

    ``ignore_unavailable`` skips an index that does not exist yet (mdx-raw
    before the first RT-CV write); ``allow_no_indices`` returns empty hits
    instead of 404 when neither exists at deploy start.
    """
    from urllib.parse import quote

    path = quote(config.indices(), safe="*,")
    return f"{config.elasticsearch_url.rstrip('/')}/{path}/_search?ignore_unavailable=true&allow_no_indices=true"


def build_query(*, camera_name: str, sensor_id: str, config: EsReadinessConfig) -> dict[str, Any]:
    """The harness query: two filtered buckets plus max(ingested_at).

    The two indices are keyed differently and this is not interchangeable:
    raw is keyed on ``sensorId.keyword`` by the *camera name*, embed on
    ``sensor.id.keyword`` by the *VST sensor UUID*.
    """
    return {
        "size": 0,
        "track_total_hits": True,
        "query": {
            "bool": {
                "should": [
                    {
                        "bool": {
                            "filter": [
                                {"wildcard": {"_index": config.raw_index}},
                                {"term": {"sensorId.keyword": camera_name}},
                            ]
                        }
                    },
                    {
                        "bool": {
                            "filter": [
                                {"wildcard": {"_index": config.embed_index}},
                                {"term": {"sensor.id.keyword": sensor_id}},
                            ]
                        }
                    },
                ],
                "minimum_should_match": 1,
            }
        },
        "aggs": {
            "pipelines": {
                "filters": {
                    "filters": {
                        "rt_cv": {"wildcard": {"_index": config.raw_index}},
                        "rt_embed": {"wildcard": {"_index": config.embed_index}},
                    }
                },
                "aggs": {"last_timestamp": {"max": {"field": "ingested_at"}}},
            },
            "last_timestamp": {"max": {"field": "ingested_at"}},
        },
    }


def _bucket_count(payload: dict[str, Any], *path: str) -> int:
    value: Any = payload.get("aggregations", {})
    for key in path:
        if not isinstance(value, dict):
            return 0
        value = value.get(key)
    if isinstance(value, dict):
        try:
            return int(value.get("doc_count") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _bucket_timestamp(payload: dict[str, Any], *path: str) -> str:
    value: Any = payload.get("aggregations", {})
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    if isinstance(value, dict):
        text = str(value.get("value_as_string") or "").strip()
        return text if parse_timestamp(text) is not None else ""
    return ""


def expected_counts(
    *,
    duration_sec: float,
    fps: float,
    reported_chunks: int,
    config: EsReadinessConfig,
) -> tuple[int, int]:
    """Return ``(expected_chunks, expected_frames)``.

    ``expected_frames`` carries the harness's 15-frame tolerance: RT-CV runs
    with live-source=1 and drops late frames rather than queueing them, so the
    exact ``ceil(duration * fps)`` is not reachable.
    """
    chunk_duration = max(1, int(config.embed_chunk_duration_sec))
    expected_chunks = int(reported_chunks) if reported_chunks > 0 else 0
    if expected_chunks <= 0 and duration_sec > 0:
        expected_chunks = int(math.ceil(duration_sec / chunk_duration))
    expected_frames = (
        max(1, int(math.ceil(duration_sec * fps)) - FRAME_TOLERANCE)
        if duration_sec > 0 and fps > 0
        else 0
    )
    return expected_chunks, expected_frames


def compute_budget(
    *, expected_chunks: int, expected_frames: int, concurrency: int, config: EsReadinessConfig
) -> dict[str, int]:
    """Per-upload timeout and give-up windows, scaled by concurrency.

    Windows scale with concurrency for the same reason the timeout does: raw
    indexing lag grows with the number of simultaneous streams, so a fixed
    window mistakes a backlog for a dead stream and fails healthy uploads --
    which then score as zero throughput.
    """
    factor = max(1, int(concurrency))
    chunk_duration = max(1, int(config.embed_chunk_duration_sec))

    frame_timeout = max(
        1,
        int(math.ceil(config.frame_processing_time_ms * expected_frames * factor / 1000))
        + DEFAULT_ENGINE_WARMUP_SEC,
    )
    embed_timeout = max(1, int(math.ceil(chunk_duration * expected_chunks * factor)) + DEFAULT_ENGINE_WARMUP_SEC)
    timeout = max(frame_timeout, embed_timeout)
    # A ceiling, never a floor: --readiness-timeout may only cut the computed
    # budget short. Raising it above the harness budget would let a stalled
    # upload poll past the point the harness would have failed it.
    if config.timeout_override_sec > 0:
        timeout = min(timeout, int(config.timeout_override_sec))

    raw_stall_grace = config.raw_drop_grace_sec * factor
    # Half the stall window, floored at the configured value, so it tracks the
    # Logstash lag concurrency creates while still reporting sooner than a stall.
    raw_absent_abort = max(config.raw_absent_abort_sec, raw_stall_grace // 2)
    # Keep the embed-dead window inside the overall budget, or at high
    # concurrency it exceeds the timeout and can never fire.
    embed_dead_after = min(config.embed_dead_after_sec * factor, max(1, timeout // 2))

    return {
        "timeout_sec": timeout,
        "frame_timeout_sec": frame_timeout,
        "embed_timeout_sec": embed_timeout,
        "raw_stall_grace_sec": raw_stall_grace,
        "raw_absent_abort_sec": raw_absent_abort,
        "embed_dead_after_sec": embed_dead_after,
    }


def wait_for_readiness(
    *,
    config: EsReadinessConfig,
    camera_name: str,
    sensor_id: str,
    duration_sec: float,
    fps: float,
    reported_chunks: int = 0,
    concurrency: int = 1,
    request_sent_at: str = "",
) -> ReadinessResult:
    """Poll Elasticsearch until both pipelines have finished, or give up.

    Success requires ``raw_complete and embed_met``. ``rt_cv_absent`` and
    ``rt_cv_dropped`` survive only as provenance on the result, never as
    independent success paths.
    """
    started_at = request_sent_at or utc_now()
    started = time.monotonic()

    expected_chunks, expected_frames = expected_counts(
        duration_sec=duration_sec, fps=fps, reported_chunks=reported_chunks, config=config
    )
    budget = compute_budget(
        expected_chunks=expected_chunks,
        expected_frames=expected_frames,
        concurrency=concurrency,
        config=config,
    )

    def fail(error: str, *, attempts: int = 0, drop_reason: str = "", **counters) -> ReadinessResult:
        completed = utc_now()
        return ReadinessResult(
            success=False,
            started_at=started_at,
            completed_at=completed,
            latency_sec=duration_between(started_at, completed),
            attempts=attempts,
            error=error,
            drop_reason=drop_reason,
            expected_frames=expected_frames,
            expected_chunks=expected_chunks,
            extra={"budget": budget},
            **counters,
        )

    if not camera_name:
        return fail("cannot query ES readiness: camera_name is empty")
    if not sensor_id:
        return fail("cannot query ES readiness: sensor_id is empty")
    if expected_chunks <= 0:
        return fail(f"cannot calculate expected chunks: duration_sec={duration_sec}")
    if expected_frames <= 0:
        return fail(f"cannot calculate expected frames: duration_sec={duration_sec}, fps={fps}")

    url = search_url(config)
    query = build_query(camera_name=camera_name, sensor_id=sensor_id, config=config)

    attempts = 0
    raw_count = embed_count = 0
    best_raw = best_embed = 0
    raw_ever_seen = False
    last_progress = last_raw_progress = started
    deadline = started + budget["timeout_sec"]
    last_error = ""

    while time.monotonic() < deadline:
        attempts += 1
        remaining = max(1.0, deadline - time.monotonic())
        response = request_json(
            url, method="POST", payload=query, timeout_sec=min(remaining, config.request_timeout_sec)
        )
        if response.status != 200 or not isinstance(response.body, dict):
            last_error = f"ES returned HTTP {response.status}"
            time.sleep(min(config.poll_interval_sec, max(0.0, deadline - time.monotonic())))
            continue

        payload = response.body
        raw_count = _bucket_count(payload, "pipelines", "buckets", "rt_cv")
        embed_count = _bucket_count(payload, "pipelines", "buckets", "rt_embed")
        last_timestamp = _bucket_timestamp(payload, "last_timestamp")
        raw_ts = _bucket_timestamp(payload, "pipelines", "buckets", "rt_cv", "last_timestamp")
        embed_ts = _bucket_timestamp(payload, "pipelines", "buckets", "rt_embed", "last_timestamp")

        if raw_count > 0:
            raw_ever_seen = True

        now = time.monotonic()
        elapsed = now - started
        if raw_count > best_raw:
            last_raw_progress = now
        if raw_count > best_raw or embed_count > best_embed:
            best_raw, best_embed = max(best_raw, raw_count), max(best_embed, embed_count)
            last_progress = now

        raw_idle = now - last_raw_progress
        stalled_sec = now - last_progress

        embed_met = embed_count >= expected_chunks
        raw_met = raw_count >= expected_frames
        raw_near_complete = expected_frames > 0 and raw_count >= int(
            math.ceil(config.raw_completion_ratio * expected_frames)
        )
        # A plateau at or above the completion ratio is treated as done, because
        # live-source=1 makes the exact count unreachable; the short grace window
        # confirms an already near-complete stream has settled.
        raw_settled = raw_ever_seen and raw_near_complete and raw_idle >= config.raw_drop_grace_sec
        raw_complete = raw_met or raw_settled

        if raw_complete and embed_met:
            # The clock stops when the last document was indexed, not when this
            # poll noticed it.
            completed = last_timestamp or utc_now()
            ratio = raw_count / expected_frames if expected_frames > 0 else 0.0
            return ReadinessResult(
                success=True,
                started_at=started_at,
                completed_at=completed,
                latency_sec=duration_between(started_at, completed),
                attempts=attempts,
                expected_frames=expected_frames,
                es_frame_count=raw_count,
                expected_chunks=expected_chunks,
                es_chunk_count=embed_count,
                raw_completion_ratio=ratio,
                raw_complete_exact=raw_met,
                rt_cv_dropped=raw_settled and not raw_met,
                extra={
                    "budget": budget,
                    "raw_last_timestamp": raw_ts,
                    "embed_last_timestamp": embed_ts,
                    "poll_elapsed_sec": round(elapsed, 3),
                },
            )

        # RT-CV never produced a frame while embedding finished: the stream was
        # most likely refused rather than merely slow.
        if (
            budget["raw_absent_abort_sec"] > 0
            and embed_met
            and not raw_ever_seen
            and elapsed >= budget["raw_absent_abort_sec"]
        ):
            reason = (
                f"RT-CV produced no raw frames in {elapsed:.0f}s while embedding completed "
                f"({embed_count}/{expected_chunks} chunks): raw_count=0/{expected_frames} "
                f"in index {config.raw_index}. The stream was most likely refused."
            )
            return fail(
                reason,
                attempts=attempts,
                drop_reason="rt_cv_stream_refused",
                es_frame_count=raw_count,
                es_chunk_count=embed_count,
                # Provenance, as the harness records it: raw really was absent,
                # not merely behind.
                rt_cv_absent=True,
            )

        # Nothing is producing the embed topic at all.
        if (
            budget["embed_dead_after_sec"] > 0
            and embed_count == 0
            and raw_ever_seen
            and elapsed >= budget["embed_dead_after_sec"]
        ):
            reason = (
                f"Embed pipeline produced no documents in {elapsed:.0f}s while RT-CV wrote "
                f"{raw_count} frame(s): embed_count=0/{expected_chunks} in index {config.embed_index}."
            )
            return fail(
                reason,
                attempts=attempts,
                drop_reason="embed_pipeline_dead",
                es_frame_count=raw_count,
                es_chunk_count=embed_count,
                rt_embed_dropped=True,
            )

        # Only a genuine stall fails here. Every success path above has already
        # returned, so reaching this point means the run is incomplete; the
        # condition is written out rather than collapsed to `if stalled` so it
        # stays correct if a success branch is ever added.
        stalled = budget["raw_stall_grace_sec"] > 0 and stalled_sec >= budget["raw_stall_grace_sec"]
        if stalled and not (raw_met and embed_met):
            reason = (
                f"Neither index advanced for {stalled_sec:.0f}s "
                f"(raw={raw_count}/{expected_frames}, embed={embed_count}/{expected_chunks})."
            )
            return fail(
                reason,
                attempts=attempts,
                drop_reason="pipeline_stalled",
                es_frame_count=raw_count,
                es_chunk_count=embed_count,
                # Both pipelines stopped producing; the harness flags both.
                rt_cv_dropped=True,
                rt_embed_dropped=True,
            )

        time.sleep(min(config.poll_interval_sec, max(0.0, deadline - time.monotonic())))

    reason = (
        f"timed out after {budget['timeout_sec']}s "
        f"(raw={raw_count}/{expected_frames}, embed={embed_count}/{expected_chunks})"
        + (f"; last ES error: {last_error}" if last_error else "")
    )
    return fail(
        reason,
        attempts=attempts,
        drop_reason="readiness_timeout",
        es_frame_count=raw_count,
        es_chunk_count=embed_count,
    )
