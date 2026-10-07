# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Observe run-owned Raw and Embed document removal through public ES reads.

A settled zero count is a bounded observation, not proof that no producer can
write later or that model-serving capacity has been released. CLI deletion and
this wait run outside the ingestion measurement window; neither is retried here.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

from es_readiness import EsReadinessConfig, build_query, search_url
from httpio import request_json


@dataclass(frozen=True)
class CleanupWaitResult:
    success: bool
    detail: str
    polls: int
    elapsed_sec: float


def _count(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"ES cleanup response has an invalid {label}")
    return value


def _document_counts(payload: Any) -> tuple[int, int]:
    """Reject partial or malformed responses rather than interpreting them as zero."""
    if not isinstance(payload, dict) or payload.get("error") is not None:
        raise ValueError("ES cleanup response is not a successful search object")
    if payload.get("timed_out") is not False or payload.get("terminated_early", False) is not False:
        raise ValueError("ES cleanup search timed out, terminated early, or omitted its timeout status")
    shards = payload.get("_shards")
    if not isinstance(shards, dict):
        raise ValueError("ES cleanup response omitted shard status")
    total = _count(shards.get("total"), "total shard count")
    successful = _count(shards.get("successful"), "successful shard count")
    failed = _count(shards.get("failed"), "failed shard count")
    if failed or successful != total or shards.get("failures"):
        raise ValueError("ES cleanup search has failed or incomplete shards")
    skipped = _count(shards.get("skipped", 0), "skipped shard count")
    if skipped > total:
        raise ValueError("ES cleanup response has an invalid skipped shard count")

    hits = payload.get("hits")
    total_hits = hits.get("total") if isinstance(hits, dict) else None
    if not isinstance(total_hits, dict) or total_hits.get("relation") != "eq":
        raise ValueError("ES cleanup response omitted an exact total hit count")
    matched = _count(total_hits.get("value"), "total hit count")
    # A search that resolves no indices may omit aggregations. Only the explicit
    # no-shards, no-hits result can establish absence without pipeline buckets.
    if total == 0 and matched == 0 and "aggregations" not in payload:
        return 0, 0
    aggregations = payload.get("aggregations")
    pipelines = aggregations.get("pipelines") if isinstance(aggregations, dict) else None
    buckets = pipelines.get("buckets") if isinstance(pipelines, dict) else None
    if not isinstance(buckets, dict):
        raise ValueError("ES cleanup response omitted pipeline buckets")
    counts = []
    for name in ("rt_cv", "rt_embed"):
        bucket = buckets.get(name)
        if not isinstance(bucket, dict):
            raise ValueError(f"ES cleanup response omitted the {name} bucket")
        counts.append(_count(bucket.get("doc_count"), f"{name} document count"))
    if (matched == 0) != (sum(counts) == 0) or (total == 0 and matched):
        raise ValueError("ES cleanup response has inconsistent document counts")
    return counts[0], counts[1]


def wait_for_cleanup(
    config: EsReadinessConfig,
    identities: list[tuple[str, str]],
    *,
    timeout_sec: float = 300.0,
    settle_sec: float = 30.0,
) -> CleanupWaitResult:
    """Wait for both pipelines to stay absent across successful batch observations.

    ``identities`` contains exact, run-owned (camera name, VIOS sensor UUID)
    pairs whose CLI deletions were confirmed. A single query covers the whole
    batch each poll, so serial requests cannot treat old per-target observations
    as one current zero count. This is still sampled ES visibility, not a global
    transaction or an acknowledgement from upstream processing services.
    """
    started = time.monotonic()
    polls = 0

    def finish(success: bool, detail: str) -> CleanupWaitResult:
        return CleanupWaitResult(success, detail, polls, round(time.monotonic() - started, 3))

    settings = (timeout_sec, settle_sec, config.poll_interval_sec, config.request_timeout_sec)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or
           not math.isfinite(value) or value <= 0 for value in settings):
        return finish(False, "Cleanup timeout, settling interval, poll interval and request timeout must be positive and finite")
    if timeout_sec <= settle_sec:
        return finish(False, "Cleanup timeout must exceed the settling interval")
    targets: list[tuple[str, str]] = []
    for pair in identities:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2 or any(
            not isinstance(value, str) or not value or value != value.strip() for value in pair
        ):
            return finish(False, "Cleanup requires an exact camera name and sensor UUID for every target")
        identity = (pair[0], pair[1])
        if identity not in targets:
            targets.append(identity)
    if not targets:
        return finish(True, "No run-owned deletions require an ES cleanup wait")

    payload = build_query(camera_name=targets[0][0], sensor_id=targets[0][1], config=config)
    # Keep the readiness query's index scoping and bucket semantics, replacing
    # only its exact identity filters. Two terms queries avoid one Boolean clause
    # per upload, which would hit ES clause limits for larger video classes.
    raw_filter, embed_filter = [branch["bool"]["filter"] for branch in payload["query"]["bool"]["should"]]
    raw_filter[1] = {"terms": {"sensorId.keyword": sorted({camera for camera, _ in targets})}}
    embed_filter[1] = {"terms": {"sensor.id.keyword": sorted({sensor for _, sensor in targets})}}
    url = search_url(config)
    deadline = started + timeout_sec
    zero_since = None
    last_counts = None
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        polls += 1
        try:
            response = request_json(
                url, method="POST", payload=payload,
                timeout_sec=min(config.request_timeout_sec, remaining),
            )
            if not 200 <= response.status < 300:
                return finish(False, f"ES cleanup search returned HTTP {response.status}")
            counts = _document_counts(response.body)
        except (OSError, ValueError) as exc:
            return finish(False, f"ES cleanup verification failed: {exc}")
        observed_at = time.monotonic()
        if observed_at >= deadline:
            break
        last_counts = counts
        if counts == (0, 0):
            if zero_since is None:
                zero_since = observed_at
            if observed_at - zero_since >= settle_sec:
                return finish(True, f"Run-owned Raw and Embed documents observed absent for {settle_sec:g}s")
        else:
            zero_since = None
        time.sleep(min(config.poll_interval_sec, deadline - observed_at))

    counts_detail = (
        f"; last observed raw={last_counts[0]}, embed={last_counts[1]}" if last_counts is not None else ""
    )
    return finish(False, f"ES cleanup did not settle within {timeout_sec:g}s{counts_detail}")
