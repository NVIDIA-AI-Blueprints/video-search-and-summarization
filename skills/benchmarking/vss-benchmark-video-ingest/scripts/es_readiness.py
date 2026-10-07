# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Client-observed Elasticsearch readiness requiring both expected counts.

CLI success confirms VIOS media availability. VIOS webhooks dispatch inference;
completion requires the upload's RT-CV raw frames and RT-Embed chunks in ES.

Completion is observed on the caller's clock when a successful poll sees both
counts. The polling interval and response latency affect the observed completion;
this check cannot identify the instant documents were indexed inside the server.

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

#: Base failure window, multiplied by concurrency before giving up on stalled
#: indexing. An idle stream never establishes successful completion.
DEFAULT_RAW_DROP_GRACE_SEC = 120

#: How long embed may produce nothing while RT-CV is demonstrably writing.
DEFAULT_EMBED_DEAD_AFTER_SEC = 900

#: How long raw may produce nothing at all, once embed has completed, before the
#: wait calls the stream refused rather than slow.
DEFAULT_RAW_ABSENT_ABORT_SEC = 600

#: Preserve the existing allowance for a small raw-frame shortfall when
#: calculating the expected count. Readiness must reach that adjusted count.
FRAME_TOLERANCE = 15


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class EsReadinessConfig:
    """Resolved settings for expected counts and bounded readiness polling."""

    elasticsearch_url: str = DEFAULT_ES_URL
    embed_index: str = DEFAULT_EMBED_INDEX
    raw_index: str = DEFAULT_RAW_INDEX
    frame_processing_time_ms: int = DEFAULT_FRAME_PROCESSING_TIME_MS
    embed_chunk_duration_sec: int = DEFAULT_EMBED_CHUNK_DURATION_SEC
    poll_interval_sec: float = 2.0
    raw_drop_grace_sec: int = DEFAULT_RAW_DROP_GRACE_SEC
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
    """Count the upload's documents in two independently keyed pipelines.

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
            },
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


def expected_counts(
    *,
    duration_sec: float,
    fps: float,
    config: EsReadinessConfig,
) -> tuple[int, int]:
    """Return ``(expected_chunks, expected_frames)``.

    ``expected_frames`` retains the existing 15-frame allowance for the raw
    processing tail. Completion still requires this adjusted count in full.
    """
    chunk_duration = max(1, int(config.embed_chunk_duration_sec))
    expected_chunks = int(math.ceil(duration_sec / chunk_duration)) if duration_sec > 0 else 0
    expected_frames = (
        max(1, int(math.ceil(duration_sec * fps)) - FRAME_TOLERANCE)
        if duration_sec > 0 and fps > 0
        else 0
    )
    return expected_chunks, expected_frames


def compute_budget(
    *, expected_chunks: int, expected_frames: int, concurrency: int, config: EsReadinessConfig
) -> dict[str, int | float]:
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
    # budget short. The computed concurrency-scaled timeout remains the upper
    # bound even when a caller supplies a larger value.
    if config.timeout_override_sec > 0:
        timeout = min(timeout, config.timeout_override_sec)

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
    concurrency: int = 1,
    request_sent_at: str = "",
) -> ReadinessResult:
    """Poll Elasticsearch until both pipelines have finished, or give up.

    Success requires ``raw_met and embed_met``. ``rt_cv_absent`` and
    ``rt_cv_dropped`` survive only as provenance on the result, never as
    independent success paths.
    """
    started_at = request_sent_at or utc_now()
    started = time.monotonic()

    expected_chunks, expected_frames = expected_counts(
        duration_sec=duration_sec, fps=fps, config=config
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
            latency_sec=round(time.monotonic() - started, 3),
            attempts=attempts,
            error=error,
            drop_reason=drop_reason,
            expected_frames=expected_frames,
            expected_chunks=expected_chunks,
            raw_completion_ratio=(
                counters.get("es_frame_count", 0) / expected_frames if expected_frames > 0 else 0.0
            ),
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
    last_progress = started
    deadline = started + budget["timeout_sec"]
    last_error = ""

    while time.monotonic() < deadline:
        attempts += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        response = request_json(
            url, method="POST", payload=query, timeout_sec=min(remaining, config.request_timeout_sec)
        )
        if response.status != 200 or not isinstance(response.body, dict):
            last_error = "ES transport request failed" if response.status == 0 else f"ES returned HTTP {response.status}"
            time.sleep(min(config.poll_interval_sec, max(0.0, deadline - time.monotonic())))
            continue

        payload = response.body
        raw_count = _bucket_count(payload, "pipelines", "buckets", "rt_cv")
        embed_count = _bucket_count(payload, "pipelines", "buckets", "rt_embed")

        if raw_count > 0:
            raw_ever_seen = True

        now = time.monotonic()
        elapsed = now - started
        if now >= deadline:
            break
        if raw_count > best_raw or embed_count > best_embed:
            best_raw, best_embed = max(best_raw, raw_count), max(best_embed, embed_count)
            last_progress = now

        stalled_sec = now - last_progress

        embed_met = embed_count >= expected_chunks
        raw_met = raw_count >= expected_frames
        # A quiet partial stream may still have documents queued upstream.
        # Only reaching both expected counts can confirm this upload.
        if raw_met and embed_met:
            # Both endpoints of a reported upload window belong to the client.
            # A server timestamp cannot remove polling delay without clock skew.
            completed = utc_now()
            ratio = raw_count / expected_frames if expected_frames > 0 else 0.0
            return ReadinessResult(
                success=True,
                started_at=started_at,
                completed_at=completed,
                latency_sec=round(time.monotonic() - started, 3),
                attempts=attempts,
                expected_frames=expected_frames,
                es_frame_count=raw_count,
                expected_chunks=expected_chunks,
                es_chunk_count=embed_count,
                raw_completion_ratio=ratio,
                raw_complete_exact=True,
                extra={
                    "budget": budget,
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
                # Raw remained absent in every successful observation.
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
                # Both observed document counts stopped advancing.
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
