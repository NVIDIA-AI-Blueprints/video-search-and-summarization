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

"""OTel metrics for the alert pipeline (REQ-011).

**Additive, and OTLP-only.** The 60-plus existing ``alert_bridge_*`` series stay
exactly as they are; these three run alongside them and are exported to the
collector, never scraped from this process.

That is not a preference. ``metrics/__init__.py`` auto-sets
``PROMETHEUS_MULTIPROC_DIR`` when Prometheus is enabled, so the ``:9081`` scrape
endpoint serves a ``MultiProcessCollector`` registry assembled from on-disk
shards. An OTel ``PrometheusMetricReader`` registers into the in-process default
registry instead, which that endpoint never reads -- so its series would be
invisible while appearing to be wired. The collector's own Prometheus exporter is
where these are scraped from.

The instruments are gated independently of ``PROMETHEUS_METRICS_ENABLED``:
recording one must not depend on a different subsystem being switched on, which
is the defect that made ``record_event_complete`` an unsafe place to hang
anything.

Every function here is a no-op when tracing is disabled or the SDK is absent, and
none of them raise: a metrics fault must cost a data point, never an alert.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_initialised_pid: Optional[int] = None
_provider: Any = None
_instruments: Dict[str, Any] = {}


def _views(aggregation_cls, view_cls):
    """Second-shaped bucket boundaries for the two duration histograms.

    The SDK's default boundaries are ``[0, 5, 10, 25, ... 10000]`` -- unitless
    numbers shaped for milliseconds. These instruments declare ``unit="s"`` and
    record seconds, so against the default every alert from 3ms to 1.5s lands in
    the single ``(0, 5]`` bucket and no percentile is recoverable. Verified by
    export before this existed: five observations spanning three orders of
    magnitude produced ``bucket_counts = [0, 5, 0, ...]``.

    Boundaries are set through a View rather than on the instrument, which is
    how ``services/rtvi/rt-vlm`` does it, and are chosen to mirror the Prometheus
    histogram measuring the same quantity so a reader comparing the two surfaces
    is comparing like with like. ``alert.verification.duration`` is a superset of
    ``E2E_DURATION_BUCKETS`` with finer buckets below 1.0s, which is where that
    one starts and so cannot resolve anything. ``alert.capacity.wait.duration``
    is a superset of ``WORKER_QUEUE_WAIT_BUCKETS``, which already starts at
    0.01s -- the additions there are at the bottom, not the top.
    """
    return [
        view_cls(
            instrument_name="alert.verification.duration",
            aggregation=aggregation_cls(
                boundaries=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0]
            ),
        ),
        view_cls(
            instrument_name="alert.capacity.wait.duration",
            aggregation=aggregation_cls(
                boundaries=[0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
            ),
        ),
    ]


def init_metrics(service_name: str = "vss-alert-ms") -> bool:
    """Build this process's MeterProvider. Idempotent per pid. Never raises."""
    global _initialised_pid, _provider, _instruments

    pid = os.getpid()
    if _initialised_pid == pid:
        return _provider is not None

    with _lock:
        if _initialised_pid == pid:
            return _provider is not None
        try:
            return _init_locked(pid, service_name)
        except BaseException:
            logger.warning("metrics initialisation raised; continuing", exc_info=True)
            _initialised_pid, _provider, _instruments = pid, None, {}
            return False


def _init_locked(pid: int, service_name: str) -> bool:
    global _initialised_pid, _provider, _instruments
    _provider, _instruments = None, {}
    try:
        from opentelemetry import metrics
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.metrics.view import (
            ExplicitBucketHistogramAggregation,
            View,
        )
        from opentelemetry.sdk.resources import Resource

        # Honours OTEL_METRICS_EXPORTER the way _build_exporter honours
        # OTEL_TRACES_EXPORTER. That variable is normally read by the SDK's
        # auto-configuration, and this provider is built by hand -- so without
        # this, setting OTEL_TRACES_EXPORTER=none silenced traces while metrics
        # kept trying to export. Which matters: against an unreachable collector
        # the OTLP exporter retries three times with backoff and blocks roughly
        # seven seconds at process exit, and the pipeline fleet's SIGTERM path
        # runs into docker stop's ten-second grace.
        kind = os.getenv(
            "OTEL_METRICS_EXPORTER",
            os.getenv("OTEL_TRACES_EXPORTER", "otlp"),
        ).strip().lower()
        if kind in {"none", "null", ""}:
            logger.info("OTel metrics disabled by OTEL_METRICS_EXPORTER=%r", kind)
            return False
        if kind == "console":
            from opentelemetry.sdk.metrics.export import ConsoleMetricExporter

            exporter = ConsoleMetricExporter()
        else:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )

            exporter = OTLPMetricExporter()

        resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", service_name)})
        # Bounded, so a dead collector cannot dominate shutdown.
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "60000")),
            export_timeout_millis=int(os.getenv("OTEL_METRIC_EXPORT_TIMEOUT", "5000")),
        )
        provider = MeterProvider(
            resource=resource, metric_readers=[reader], views=_views(
                ExplicitBucketHistogramAggregation, View
            ),
        )
        metrics.set_meter_provider(provider)
        meter = provider.get_meter(__name__)

        _instruments = {
            "verification_duration": meter.create_histogram(
                "alert.verification.duration", unit="s",
                description="End-to-end alert verification, per event",
            ),
            "vlm_attempts": meter.create_counter(
                "alert.vlm.attempt.count", unit="1",
                description="VLM requests, counted per attempt rather than per event",
            ),
            "capacity_wait": meter.create_histogram(
                "alert.capacity.wait.duration", unit="s",
                description="Time spent waiting for a VST or VLM capacity slot",
            ),
        }
        _provider = provider
        logger.info("OTel metrics initialised (pid=%d)", pid)
        return True
    except Exception:
        logger.info("OTel metrics unavailable; continuing without them", exc_info=True)
        _provider, _instruments = None, {}
        return False
    finally:
        # Published last, like the tracer's: a concurrent caller reading it
        # early would be told metrics were off while they were still being built.
        _initialised_pid = pid


def _record(name: str, kind: str, value: float, **attributes: Any) -> None:
    """Record one point. Silent when metrics are off; never raises."""
    instrument = _instruments.get(name)
    if instrument is None:
        return
    try:
        clean = {k: v for k, v in attributes.items() if v is not None}
        (instrument.record if kind == "histogram" else instrument.add)(value, clean)
    except Exception:
        logger.debug("could not record %s", name, exc_info=True)


def observe_verification_duration(seconds: float, pipeline_mode: Any = None,
                                  verdict: Any = None) -> None:
    _record("verification_duration", "histogram", seconds,
            pipeline_mode=pipeline_mode, verdict=verdict)


def count_vlm_attempt(success: bool, attempt: int) -> None:
    _record("vlm_attempts", "counter", 1, success=success, attempt=attempt)


def observe_capacity_wait(seconds: float, service: str) -> None:
    _record("capacity_wait", "histogram", seconds, service=service)


def shutdown(timeout_millis: int = 5000) -> None:
    """Flush and stop. Safe to call when never initialised."""
    global _provider, _instruments
    provider, _provider, _instruments = _provider, None, {}
    if provider is None:
        return
    try:
        provider.force_flush(timeout_millis)
        provider.shutdown()
    except Exception:
        logger.debug("metrics shutdown failed", exc_info=True)
