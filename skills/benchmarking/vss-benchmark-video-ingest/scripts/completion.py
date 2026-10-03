# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Elasticsearch readiness for one uploaded video.

The benchmark has one definition of finished: RT-CV raw frames and RT-Embed
chunks are indexed in Elasticsearch, matching ``vss_ingest_perf``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from es_readiness import EsReadinessConfig, wait_for_readiness
from httpio import parse_endpoint, request_json


class ReadinessError(RuntimeError):
    """The Elasticsearch readiness source is unusable."""


@dataclass
class ReadinessResult:
    confirmed: bool
    outcome: str
    detail: str
    polls: int = 0
    completed_at: str = ""
    metrics: dict = field(default_factory=dict)


@dataclass
class UploadContext:
    upload_filename: str
    camera_name: str
    sensor_id: str
    response_body: dict[str, Any]
    duration_sec: float = 0.0
    fps: float = 0.0
    reported_chunks: int = 0
    concurrency: int = 1


class EsReadinessMonitor:
    """Wait until both ingest pipelines have indexed their documents."""

    label = "Elasticsearch readiness (vss_ingest_perf parity)"
    stops_clock_at = "RT-CV raw frames and RT-Embed chunks indexed in Elasticsearch"

    def __init__(
        self,
        elasticsearch_url: str,
        poll_interval_sec: float = 2.0,
        request_timeout_sec: float = 60.0,
        config: EsReadinessConfig | None = None,
    ):
        self.elasticsearch_url = elasticsearch_url.rstrip("/")
        self.request_timeout_sec = float(request_timeout_sec)
        self.config = config or EsReadinessConfig()
        self.config.elasticsearch_url = self.elasticsearch_url
        self.config.request_timeout_sec = self.request_timeout_sec
        self.config.poll_interval_sec = max(0.5, float(poll_interval_sec))

    def preflight(self) -> None:
        if not self.elasticsearch_url:
            raise ReadinessError("--elasticsearch-url is required")
        parse_endpoint(self.elasticsearch_url, label="--elasticsearch-url")
        response = request_json(
            f"{self.elasticsearch_url}/_cluster/health",
            timeout_sec=self.request_timeout_sec,
        )
        if response.status != 200 or not isinstance(response.body, dict):
            raise ReadinessError(
                f"Elasticsearch did not answer /_cluster/health (HTTP {response.status}); "
                "check --elasticsearch-url"
            )
        if str(response.body.get("status") or "") == "red":
            raise ReadinessError(
                "Elasticsearch cluster health is red; refusing to benchmark against it"
            )

    def confirm(self, ctx: UploadContext) -> ReadinessResult:
        result = wait_for_readiness(
            config=self.config,
            camera_name=ctx.camera_name,
            sensor_id=ctx.sensor_id,
            duration_sec=ctx.duration_sec,
            fps=ctx.fps,
            reported_chunks=ctx.reported_chunks,
            concurrency=ctx.concurrency,
        )
        metrics = {
            "expected_frames": result.expected_frames,
            "es_frame_count": result.es_frame_count,
            "expected_chunks": result.expected_chunks,
            "es_chunk_count": result.es_chunk_count,
            "raw_completion_ratio": round(result.raw_completion_ratio, 4),
            "raw_complete_exact": result.raw_complete_exact,
            "rt_cv_dropped": result.rt_cv_dropped,
            "rt_cv_absent": result.rt_cv_absent,
            "drop_reason": result.drop_reason,
        }
        if result.success:
            note = (
                "raw complete"
                if result.raw_complete_exact
                else f"raw settled at {result.raw_completion_ratio:.1%} of expected"
            )
            return ReadinessResult(
                True,
                "confirmed",
                f"{note}; embed {result.es_chunk_count}/{result.expected_chunks}",
                result.attempts,
                completed_at=result.completed_at,
                metrics=metrics,
            )
        return ReadinessResult(
            False,
            "unconfirmed",
            result.error,
            result.attempts,
            metrics=metrics,
        )
