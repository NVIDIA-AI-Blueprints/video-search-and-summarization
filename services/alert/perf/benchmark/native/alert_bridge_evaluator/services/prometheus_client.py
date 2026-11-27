"""
Prometheus client for Alert Bridge Evaluator v2 metrics.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests


class PrometheusError(Exception):
    """Raised when Prometheus queries fail or input is invalid."""


class PrometheusClient:
    """Query Alert Bridge metrics from a Prometheus server."""

    _DURATION_RE = re.compile(r"^\s*\d+(?:\.\d+)?[smhd]\s*$")

    # Per-sensor histogram metrics emitted by alert-agent.
    _LATENCY_HISTOGRAMS = {
        "upstream": "alert_bridge_upstream_duration_by_sensor_seconds",
        "kafka_lag": "alert_bridge_kafka_lag_duration_by_sensor_seconds",
        "worker_queue_wait": "alert_bridge_worker_queue_wait_duration_by_sensor_seconds",
        "vst": "alert_bridge_vst_duration_by_sensor_seconds",
        "video_length": "alert_bridge_video_length_by_sensor_seconds",
        "vlm": "alert_bridge_vlm_duration_by_sensor_seconds",
        "worker_processing": "alert_bridge_worker_processing_by_sensor_seconds",
        "e2e": "alert_bridge_e2e_duration_by_sensor_seconds",
    }
    _VERDICT_COUNTER = "alert_bridge_events_by_sensor_total"
    _AGGREGATE_LATENCY_HISTOGRAMS = {
        "upstream": "alert_bridge_upstream_duration_seconds",
        "kafka_lag": "alert_bridge_kafka_lag_duration_seconds",
        "worker_queue_wait": "alert_bridge_worker_queue_wait_duration_seconds",
        "vst": "alert_bridge_vst_duration_seconds",
        "video_length": "alert_bridge_video_length_seconds",
        "vlm": "alert_bridge_vlm_duration_seconds",
        "worker_processing": "alert_bridge_worker_processing_seconds",
        "e2e": "alert_bridge_e2e_duration_seconds",
    }
    _AGGREGATE_VERDICT_COUNTER = "alert_bridge_events_total"
    _ONDEMAND_AGGREGATE_LATENCY_HISTOGRAMS = {
        "vlm": "alert_bridge_ondemand_vlm_duration_seconds",
        "worker_processing": "alert_bridge_ondemand_processing_seconds",
        "e2e": "alert_bridge_ondemand_e2e_duration_seconds",
    }
    _ONDEMAND_VERDICT_COUNTER = "alert_bridge_ondemand_events_total"
    _ONDEMAND_REQUEST_COUNTER = "alert_bridge_ondemand_requests_total"
    _ONDEMAND_VERIFICATION_FAILURE_COUNTER = "alert_bridge_ondemand_verification_failures_total"
    _ASYNC_EXTERNAL_IO_HISTOGRAM = "alert_bridge_async_external_io_duration_seconds"
    _RTVI_CALL_HISTOGRAM = "alert_bridge_rtvi_call_duration_seconds"
    _INCIDENT_QUERY_HISTOGRAM = "alert_bridge_incident_query_duration_seconds"

    def __init__(self, settings: Any):
        self._base_url = settings.prometheus_url.rstrip("/")
        self._timeout = int(getattr(settings, "prometheus_timeout", 30))
        self._session = requests.Session()

    def health_check(self) -> Dict[str, Any]:
        """Check Prometheus liveness endpoint."""
        try:
            response = self._session.get(
                f"{self._base_url}/-/healthy",
                timeout=self._timeout,
            )
            if response.status_code == 200:
                return {"status": "healthy"}
            return {"status": "unhealthy", "error": f"HTTP {response.status_code}"}
        except requests.RequestException as exc:
            return {"status": "unhealthy", "error": str(exc)}

    def query_confirmed_events_count(
        self,
        sensor_id: str,
        *,
        start_timestamp: Optional[str] = None,
        end_timestamp: Optional[str] = None,
        duration: Optional[str] = None,
    ) -> int:
        """Count confirmed events from per-sensor counter in the target window."""
        window, eval_time = self._resolve_window(
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            duration=duration,
        )
        label_filter = self._labels(
            verdict="confirmed",
            sensorId=sensor_id,
        )
        expr = (
            "sum(increase("
            f"alert_bridge_events_by_sensor_total{{{label_filter}}}[{window}]"
            "))"
        )
        value = self._prom_query(expr, eval_time)
        return int(round(value)) if value is not None else 0

    def query_latency_metrics(
        self,
        sensor_id: str,
        *,
        start_timestamp: Optional[str] = None,
        end_timestamp: Optional[str] = None,
        duration: Optional[str] = None,
    ) -> Dict[str, Dict[str, Optional[float]]]:
        """Fetch per-stage latency stats for a single sensor."""
        window, eval_time = self._resolve_window(
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            duration=duration,
        )
        label_filter = self._labels(sensorId=sensor_id)

        stats: Dict[str, Dict[str, Optional[float]]] = {}
        for stage, metric in self._LATENCY_HISTOGRAMS.items():
            stats[stage] = {
                "avg": self._prom_avg(metric, window, label_filter, eval_time),
                "p50": self._prom_quantile(metric, 0.50, window, label_filter, eval_time),
                "p90": self._prom_quantile(metric, 0.90, window, label_filter, eval_time),
                "p99": self._prom_quantile(metric, 0.99, window, label_filter, eval_time),
                "count": float(self._prom_count(metric, window, label_filter, eval_time)),
            }
        return stats

    def query_reliability_metrics(
        self,
        sensor_id: str,
        *,
        start_timestamp: Optional[str] = None,
        end_timestamp: Optional[str] = None,
        duration: Optional[str] = None,
    ) -> Dict[str, int]:
        """Fetch per-sensor event-volume and reliability counters for a window."""
        window, eval_time = self._resolve_window(
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            duration=duration,
        )
        base_labels = {"sensorId": sensor_id}

        confirmed = self._prom_increase(
            self._VERDICT_COUNTER,
            window,
            self._labels(verdict="confirmed", **base_labels),
            eval_time,
        )
        rejected = self._prom_increase(
            self._VERDICT_COUNTER,
            window,
            self._labels(verdict="rejected", **base_labels),
            eval_time,
        )
        verification_failed = self._prom_increase(
            self._VERDICT_COUNTER,
            window,
            self._labels(verdict="verification-failed", **base_labels),
            eval_time,
        )
        unknown = self._prom_increase(
            self._VERDICT_COUNTER,
            window,
            self._labels(verdict="unknown", **base_labels),
            eval_time,
        )
        events_after_dedup = self._prom_increase(
            "alert_bridge_events_after_dedup_by_sensor_total",
            window,
            self._labels(**base_labels),
            eval_time,
        )
        dropped = self._prom_increase(
            "alert_bridge_events_dropped_by_sensor_total",
            window,
            self._labels(**base_labels),
            eval_time,
        )
        skipped_confirmed = self._prom_increase(
            "alert_bridge_events_skipped_confirmed_by_sensor_total",
            window,
            self._labels(**base_labels),
            eval_time,
        )
        verification_failures = self._prom_increase(
            "alert_bridge_verification_failures_by_sensor_total",
            window,
            self._labels(**base_labels),
            eval_time,
        )

        return {
            "total_events": confirmed + rejected + verification_failed + unknown,
            "confirmed_events": confirmed,
            "rejected_events": rejected,
            "verification_failed_events": verification_failed,
            "unknown_events": unknown,
            "events_after_dedup": events_after_dedup,
            "dropped_events": dropped,
            "skipped_confirmed_events": skipped_confirmed,
            "verification_failures": verification_failures,
        }

    def query_aggregate_summary_metrics(
        self,
        *,
        start_timestamp: str,
        end_timestamp: str,
        buffer_seconds: int = 90,
        metric_profile: str = "realtime",
        sensor_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Fetch aggregate latency + reliability metrics for a latency summary window.

        Window is resolved as:
          [start_timestamp, end_timestamp + buffer_seconds]
        and evaluated at:
          eval_time = end_timestamp + buffer_seconds
        """
        start_unix = self._iso_to_unix(start_timestamp)
        end_unix = self._iso_to_unix(end_timestamp)
        if end_unix <= start_unix:
            raise PrometheusError(
                "Invalid time range: end_timestamp must be after start_timestamp"
            )

        buffer = max(int(buffer_seconds), 0)
        eval_time = end_unix + buffer
        window_seconds = int(round(eval_time - start_unix))
        if window_seconds <= 0:
            raise PrometheusError("Invalid aggregate summary window: non-positive duration")
        window = f"{window_seconds}s"
        metric_profile = self._normalize_metric_profile(metric_profile)
        exact_sensor_ids = sorted(
            {
                str(sensor_id).strip()
                for sensor_id in (sensor_ids or [])
                if str(sensor_id).strip()
            }
        )
        use_exact_sensor_counts = bool(exact_sensor_ids) and metric_profile == "realtime"
        sensor_labels = (
            self._sensor_regex_labels(exact_sensor_ids)
            if use_exact_sensor_counts
            else ""
        )
        latency_histograms = (
            self._ONDEMAND_AGGREGATE_LATENCY_HISTOGRAMS
            if metric_profile == "ondemand"
            else (
                self._LATENCY_HISTOGRAMS
                if use_exact_sensor_counts
                else self._AGGREGATE_LATENCY_HISTOGRAMS
            )
        )
        not_applicable_stages = sorted(
            set(self._AGGREGATE_LATENCY_HISTOGRAMS) - set(latency_histograms)
        )

        stats: Dict[str, Dict[str, Optional[float]]] = {}
        for stage in self._AGGREGATE_LATENCY_HISTOGRAMS:
            metric = latency_histograms.get(stage)
            if not metric:
                stats[stage] = {
                    "avg": None,
                    "p50": None,
                    "p90": None,
                    "p99": None,
                    "count": 0.0,
                    "data_status": "not_applicable",
                }
                continue
            stats[stage] = {
                "avg": self._prom_avg(metric, window, sensor_labels, eval_time),
                "p50": self._prom_quantile(metric, 0.50, window, sensor_labels, eval_time),
                "p90": self._prom_quantile(metric, 0.90, window, sensor_labels, eval_time),
                "p99": self._prom_quantile(metric, 0.99, window, sensor_labels, eval_time),
                "count": float(
                    self._prom_counter_delta(
                        f"{metric}_count",
                        sensor_labels,
                        start_unix,
                        eval_time,
                        window,
                    )
                    if use_exact_sensor_counts
                    else self._prom_count(metric, window, "", eval_time)
                ),
            }

        verdict_counter = (
            self._ONDEMAND_VERDICT_COUNTER
            if metric_profile == "ondemand"
            else (
                self._VERDICT_COUNTER
                if use_exact_sensor_counts
                else self._AGGREGATE_VERDICT_COUNTER
            )
        )

        def counter_value(metric: str, labels: str) -> int:
            if use_exact_sensor_counts:
                return self._prom_counter_delta(
                    metric,
                    labels,
                    start_unix,
                    eval_time,
                    window,
                )
            return self._prom_increase(metric, window, labels, eval_time)

        def verdict_labels(verdict: str) -> str:
            if use_exact_sensor_counts:
                return self._sensor_regex_labels(
                    exact_sensor_ids,
                    verdict=verdict,
                )
            return self._labels(verdict=verdict)

        confirmed = counter_value(verdict_counter, verdict_labels("confirmed"))
        rejected = counter_value(verdict_counter, verdict_labels("rejected"))
        verification_failed = counter_value(
            verdict_counter,
            verdict_labels("verification-failed"),
        )
        unknown = counter_value(verdict_counter, verdict_labels("unknown"))
        if metric_profile == "ondemand":
            events_after_dedup = 0
            dropped = 0
            skipped_confirmed = 0
            verification_failures = self._prom_increase(
                self._ONDEMAND_VERIFICATION_FAILURE_COUNTER,
                window,
                "",
                eval_time,
            )
        else:
            reliability_metrics = {
                "events_after_dedup": (
                    "alert_bridge_events_after_dedup_by_sensor_total"
                    if use_exact_sensor_counts
                    else "alert_bridge_events_after_dedup_total"
                ),
                "dropped": (
                    "alert_bridge_events_dropped_by_sensor_total"
                    if use_exact_sensor_counts
                    else "alert_bridge_events_dropped_total"
                ),
                "skipped_confirmed": (
                    "alert_bridge_events_skipped_confirmed_by_sensor_total"
                    if use_exact_sensor_counts
                    else "alert_bridge_events_skipped_confirmed_total"
                ),
                "verification_failures": (
                    "alert_bridge_verification_failures_by_sensor_total"
                    if use_exact_sensor_counts
                    else "alert_bridge_verification_failures_total"
                ),
            }
            reliability_labels = sensor_labels if use_exact_sensor_counts else ""
            events_after_dedup = counter_value(
                reliability_metrics["events_after_dedup"], reliability_labels
            )
            dropped = counter_value(reliability_metrics["dropped"], reliability_labels)
            skipped_confirmed = counter_value(
                reliability_metrics["skipped_confirmed"], reliability_labels
            )
            verification_failures = counter_value(
                reliability_metrics["verification_failures"], reliability_labels
            )

        reliability = {
            "confirmed_events": confirmed,
            "rejected_events": rejected,
            "verification_failed_events": verification_failed,
            "unknown_events": unknown,
            "events_after_dedup": events_after_dedup,
            "dropped_events": dropped,
            "skipped_confirmed_events": skipped_confirmed,
            "verification_failures": verification_failures,
        }
        reliability["total_verified_events"] = (
            reliability["confirmed_events"]
            + reliability["rejected_events"]
            + reliability["verification_failed_events"]
            + reliability["unknown_events"]
        )
        # Keep the legacy field for compatibility, but expose unambiguous
        # populations for completeness and successful-verification reporting.
        reliability["terminal_outcomes"] = reliability["total_verified_events"]
        reliability["successful_verifications"] = (
            reliability["confirmed_events"] + reliability["rejected_events"]
        )

        async_external_io = {
            "avg": self._prom_avg(self._ASYNC_EXTERNAL_IO_HISTOGRAM, window, "", eval_time),
            "p50": self._prom_quantile(self._ASYNC_EXTERNAL_IO_HISTOGRAM, 0.50, window, "", eval_time),
            "p90": self._prom_quantile(self._ASYNC_EXTERNAL_IO_HISTOGRAM, 0.90, window, "", eval_time),
            "p99": self._prom_quantile(self._ASYNC_EXTERNAL_IO_HISTOGRAM, 0.99, window, "", eval_time),
            "count": float(self._prom_count(self._ASYNC_EXTERNAL_IO_HISTOGRAM, window, "", eval_time)),
        }
        rtvi_call = {
            "avg": self._prom_avg(self._RTVI_CALL_HISTOGRAM, window, "", eval_time),
            "p50": self._prom_quantile(self._RTVI_CALL_HISTOGRAM, 0.50, window, "", eval_time),
            "p90": self._prom_quantile(self._RTVI_CALL_HISTOGRAM, 0.90, window, "", eval_time),
            "p99": self._prom_quantile(self._RTVI_CALL_HISTOGRAM, 0.99, window, "", eval_time),
            "count": float(self._prom_count(self._RTVI_CALL_HISTOGRAM, window, "", eval_time)),
        }
        incident_query = {
            "avg": self._prom_avg(self._INCIDENT_QUERY_HISTOGRAM, window, "", eval_time),
            "p50": self._prom_quantile(self._INCIDENT_QUERY_HISTOGRAM, 0.50, window, "", eval_time),
            "p90": self._prom_quantile(self._INCIDENT_QUERY_HISTOGRAM, 0.90, window, "", eval_time),
            "p99": self._prom_quantile(self._INCIDENT_QUERY_HISTOGRAM, 0.99, window, "", eval_time),
            "count": float(self._prom_count(self._INCIDENT_QUERY_HISTOGRAM, window, "", eval_time)),
        }

        ondemand_requests_total = 0
        ondemand_requests_accepted_total = 0
        if metric_profile == "ondemand":
            ondemand_requests_total = self._prom_increase(
                self._ONDEMAND_REQUEST_COUNTER,
                window,
                "",
                eval_time,
            )
            ondemand_requests_accepted_total = self._prom_increase(
                self._ONDEMAND_REQUEST_COUNTER,
                window,
                self._labels(outcome="accepted"),
                eval_time,
            )

        operational = {
            "async_external_io": async_external_io,
            "async_external_io_count": int(float(async_external_io.get("count") or 0)),
            "async_external_io_fallback_total": self._prom_increase(
                "alert_bridge_async_external_io_fallback_total",
                window,
                "",
                eval_time,
            ),
            "async_sink_in_flight_avg": self._prom_gauge_avg(
                "alert_bridge_async_sink_in_flight",
                window,
                "",
                eval_time,
            ),
            "async_sink_in_flight_max": self._prom_gauge_max(
                "alert_bridge_async_sink_in_flight",
                window,
                "",
                eval_time,
            ),
            "realtime_rules_active_avg": self._prom_gauge_avg(
                "alert_bridge_realtime_rules_active",
                window,
                "",
                eval_time,
            ),
            "realtime_rules_active_max": self._prom_gauge_max(
                "alert_bridge_realtime_rules_active",
                window,
                "",
                eval_time,
            ),
            "realtime_rules_created_total": self._prom_increase(
                "alert_bridge_realtime_rules_created_total",
                window,
                "",
                eval_time,
            ),
            "realtime_rules_deleted_total": self._prom_increase(
                "alert_bridge_realtime_rules_deleted_total",
                window,
                "",
                eval_time,
            ),
            "realtime_rules_failed_total": self._prom_increase(
                "alert_bridge_realtime_rules_failed_total",
                window,
                "",
                eval_time,
            ),
            "rtvi_call": rtvi_call,
            "rtvi_call_count": int(float(rtvi_call.get("count") or 0)),
            "rtvi_call_failures_total": self._prom_increase(
                "alert_bridge_rtvi_call_failures_total",
                window,
                "",
                eval_time,
            ),
            "incident_query": incident_query,
            "incident_query_count": int(float(incident_query.get("count") or 0)),
            "incident_query_failures_total": self._prom_increase(
                "alert_bridge_incident_query_failures_total",
                window,
                "",
                eval_time,
            ),
            "replay_rule_failures_total": self._prom_increase(
                "alert_bridge_replay_rule_failures_total",
                window,
                "",
                eval_time,
            ),
            "ondemand_requests_total": ondemand_requests_total,
            "ondemand_requests_accepted_total": ondemand_requests_accepted_total,
        }

        has_latency_data = any(float(stage.get("count", 0) or 0) > 0 for stage in stats.values())
        has_reliability_data = any(int(v or 0) > 0 for v in reliability.values())
        has_operational_hist_data = any(
            float((hist.get("count") or 0)) > 0
            for hist in (
                async_external_io,
                rtvi_call,
                incident_query,
            )
        )
        has_operational_counter_data = any(
            int(operational.get(key) or 0) > 0
            for key in (
                "async_external_io_fallback_total",
                "realtime_rules_created_total",
                "realtime_rules_deleted_total",
                "realtime_rules_failed_total",
                "rtvi_call_failures_total",
                "incident_query_failures_total",
                "replay_rule_failures_total",
                "ondemand_requests_total",
                "ondemand_requests_accepted_total",
            )
        )
        has_operational_gauge_data = any(
            (operational.get(key) is not None and float(operational.get(key)) > 0)
            for key in (
                "async_sink_in_flight_avg",
                "async_sink_in_flight_max",
                "realtime_rules_active_avg",
                "realtime_rules_active_max",
            )
        )
        errors = {
            "drop_reasons": self._prom_grouped_increase(
                "alert_bridge_events_dropped_total",
                window,
                "",
                eval_time,
                ("reason",),
            ),
            "verification_failure_reasons": self._prom_grouped_increase(
                "alert_bridge_verification_failures_total",
                window,
                "",
                eval_time,
                ("reason",),
            ),
            "async_fallback_reasons": self._prom_grouped_increase(
                "alert_bridge_async_external_io_fallback_total",
                window,
                "",
                eval_time,
                ("operation", "reason"),
            ),
            "async_external_io_errors": self._prom_grouped_increase(
                f"{self._ASYNC_EXTERNAL_IO_HISTOGRAM}_count",
                window,
                'result!="success"',
                eval_time,
                ("operation", "mode", "result"),
            ),
            "realtime_rule_failure_stages": self._prom_grouped_increase(
                "alert_bridge_realtime_rules_failed_total",
                window,
                "",
                eval_time,
                ("stage",),
            ),
            "replay_rule_failures": operational["replay_rule_failures_total"],
            "rtvi_call_failure_methods": self._prom_grouped_increase(
                "alert_bridge_rtvi_call_failures_total",
                window,
                "",
                eval_time,
                ("method",),
            ),
            "incident_query_failures": operational["incident_query_failures_total"],
        }
        if metric_profile == "ondemand":
            errors["ondemand_request_outcomes"] = self._prom_grouped_increase(
                self._ONDEMAND_REQUEST_COUNTER,
                window,
                'outcome!="accepted"',
                eval_time,
                ("outcome",),
            )
            errors["ondemand_verification_failure_reasons"] = self._prom_grouped_increase(
                self._ONDEMAND_VERIFICATION_FAILURE_COUNTER,
                window,
                "",
                eval_time,
                ("reason",),
            )
        has_error_breakdown_data = any(
            int(row.get("count") or 0) > 0
            for rows in errors.values()
            if isinstance(rows, list)
            for row in rows
        ) or any(
            int(errors.get(key) or 0) > 0
            for key in ("replay_rule_failures", "incident_query_failures")
        )
        has_data = (
            has_latency_data
            or has_reliability_data
            or has_operational_hist_data
            or has_operational_counter_data
            or has_operational_gauge_data
            or has_error_breakdown_data
        )

        return {
            "status": "success" if has_data else "no_data",
            "start_ts": start_timestamp,
            "end_ts": end_timestamp,
            "eval_time_unix": float(eval_time),
            "window_seconds": window_seconds,
            "buffer_seconds": buffer,
            "stats": stats,
            "reliability": reliability,
            "operational": operational,
            "errors": errors,
            "metric_profile": metric_profile,
            "not_applicable_stages": not_applicable_stages,
            "counts_source": (
                "sensor_scoped_counter_snapshot_delta"
                if use_exact_sensor_counts
                else "prometheus_increase"
            ),
            "count_sensor_ids": exact_sensor_ids if use_exact_sensor_counts else [],
        }

    @staticmethod
    def _normalize_metric_profile(metric_profile: str) -> str:
        profile = str(metric_profile or "realtime").strip().lower()
        if profile not in {"realtime", "ondemand"}:
            raise PrometheusError(
                f"Invalid metric_profile={metric_profile!r}; expected 'realtime' or 'ondemand'"
            )
        return profile

    def _resolve_window(
        self,
        *,
        start_timestamp: Optional[str],
        end_timestamp: Optional[str],
        duration: Optional[str],
    ) -> Tuple[str, Optional[float]]:
        if start_timestamp and end_timestamp:
            start_unix = self._iso_to_unix(start_timestamp)
            end_unix = self._iso_to_unix(end_timestamp)
            seconds = int(round(end_unix - start_unix))
            if seconds <= 0:
                raise PrometheusError(
                    "Invalid time range: end_timestamp must be after start_timestamp"
                )
            return f"{seconds}s", end_unix

        if duration:
            d = duration.strip().lower()
            if not self._DURATION_RE.match(d):
                raise PrometheusError(
                    f"Invalid duration {duration!r}; expected value like '30m', '1h', or '10s'"
                )
            return d, None

        raise PrometheusError(
            "Either (start_timestamp and end_timestamp) or duration must be provided"
        )

    @staticmethod
    def _labels(**kwargs: str) -> str:
        """Build a PromQL label filter string."""
        parts = []
        for key, value in kwargs.items():
            escaped = value.replace("\\", "\\\\").replace("\"", "\\\"")
            parts.append(f'{key}="{escaped}"')
        return ",".join(parts)

    @classmethod
    def _sensor_regex_labels(cls, sensor_ids: List[str], **kwargs: str) -> str:
        """Build labels scoped to an exact set of sensor IDs."""
        cleaned = sorted(
            {
                str(sensor_id).strip()
                for sensor_id in sensor_ids
                if str(sensor_id).strip()
            }
        )
        if not cleaned:
            raise PrometheusError("At least one sensor ID is required for scoped metrics")
        regex = "|".join(
            re.escape(sensor_id).replace(r"\-", "-")
            for sensor_id in cleaned
        )
        escaped_regex = json.dumps(regex)[1:-1]
        labels = [f'sensorId=~"{escaped_regex}"']
        exact_labels = cls._labels(**kwargs)
        if exact_labels:
            labels.append(exact_labels)
        return ",".join(labels)

    def _prom_counter_delta(
        self,
        metric: str,
        label_filter: str,
        start_time: float,
        end_time: float,
        window: str,
    ) -> int:
        """Return an exact counter delta and reject windows containing resets."""
        reset_expr = f"sum(resets({metric}{{{label_filter}}}[{window}]))"
        resets = self._prom_query(reset_expr, end_time) or 0.0
        if resets > 0:
            raise PrometheusError(
                f"Counter reset detected for {metric} during the evaluation window"
            )

        value_expr = f"sum({metric}{{{label_filter}}})"
        start_value = self._prom_query(value_expr, start_time) or 0.0
        end_value = self._prom_query(value_expr, end_time) or 0.0
        delta = end_value - start_value
        if delta < -1e-9:
            raise PrometheusError(
                f"Counter decreased for {metric} during the evaluation window"
            )
        return int(round(max(0.0, delta)))

    def _prom_query(self, promql: str, eval_time: Optional[float]) -> Optional[float]:
        params: Dict[str, Any] = {"query": promql}
        if eval_time is not None:
            params["time"] = eval_time

        try:
            response = self._session.get(
                f"{self._base_url}/api/v1/query",
                params=params,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise PrometheusError(f"Prometheus request failed: {exc}") from exc
        except ValueError as exc:
            raise PrometheusError(f"Invalid Prometheus JSON response: {exc}") from exc

        if payload.get("status") != "success":
            raise PrometheusError(
                f"Prometheus query failed: {payload.get('errorType', 'unknown')} "
                f"{payload.get('error', '')}".strip()
            )

        results = (payload.get("data") or {}).get("result") or []
        if not results:
            return None

        try:
            value = float(results[0]["value"][1])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PrometheusError(f"Malformed Prometheus result: {exc}") from exc

        if math.isnan(value) or math.isinf(value):
            return None
        return value

    def _prom_quantile(
        self,
        metric: str,
        quantile: float,
        window: str,
        label_filter: str,
        eval_time: Optional[float],
    ) -> Optional[float]:
        expr = (
            f"histogram_quantile({quantile}, "
            f"sum by (le) (rate({metric}_bucket{{{label_filter}}}[{window}])))"
        )
        return self._prom_query(expr, eval_time)

    def _prom_avg(
        self,
        metric: str,
        window: str,
        label_filter: str,
        eval_time: Optional[float],
    ) -> Optional[float]:
        expr = (
            f"sum(rate({metric}_sum{{{label_filter}}}[{window}])) / "
            f"sum(rate({metric}_count{{{label_filter}}}[{window}]))"
        )
        return self._prom_query(expr, eval_time)

    def _prom_count(
        self,
        metric: str,
        window: str,
        label_filter: str,
        eval_time: Optional[float],
    ) -> int:
        expr = f"sum(increase({metric}_count{{{label_filter}}}[{window}]))"
        value = self._prom_query(expr, eval_time)
        return int(round(value)) if value is not None else 0

    def _prom_increase(
        self,
        metric: str,
        window: str,
        label_filter: str,
        eval_time: Optional[float],
    ) -> int:
        expr = f"sum(increase({metric}{{{label_filter}}}[{window}]))"
        value = self._prom_query(expr, eval_time)
        return int(round(value)) if value is not None else 0

    def _prom_grouped_increase(
        self,
        metric: str,
        window: str,
        label_filter: str,
        eval_time: Optional[float],
        group_labels: Tuple[str, ...],
    ) -> list[Dict[str, Any]]:
        group_by = ", ".join(group_labels)
        expr = f"sum by ({group_by}) (increase({metric}{{{label_filter}}}[{window}]))"
        results = self._prom_vector_query(expr, eval_time)
        rows: list[Dict[str, Any]] = []
        for result in results:
            labels = result.get("metric") or {}
            try:
                count = int(round(float(result["value"][1])))
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            if count <= 0:
                continue
            row: Dict[str, Any] = {
                label: str(labels.get(label, "")) for label in group_labels
            }
            row["count"] = count
            rows.append(row)
        rows.sort(
            key=lambda row: (
                -int(row.get("count") or 0),
                tuple(str(row.get(label, "")) for label in group_labels),
            )
        )
        return rows

    def _prom_vector_query(self, promql: str, eval_time: Optional[float]) -> list[Dict[str, Any]]:
        params: Dict[str, Any] = {"query": promql}
        if eval_time is not None:
            params["time"] = eval_time

        try:
            response = self._session.get(
                f"{self._base_url}/api/v1/query",
                params=params,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise PrometheusError(f"Prometheus request failed: {exc}") from exc
        except ValueError as exc:
            raise PrometheusError(f"Invalid Prometheus JSON response: {exc}") from exc

        if payload.get("status") != "success":
            raise PrometheusError(
                f"Prometheus query failed: {payload.get('errorType', 'unknown')} "
                f"{payload.get('error', '')}".strip()
            )

        results = (payload.get("data") or {}).get("result") or []
        if not isinstance(results, list):
            raise PrometheusError("Malformed Prometheus vector result")
        return results

    def _prom_gauge_avg(
        self,
        metric: str,
        window: str,
        label_filter: str,
        eval_time: Optional[float],
    ) -> Optional[float]:
        expr = f"avg(avg_over_time({metric}{{{label_filter}}}[{window}]))"
        return self._prom_query(expr, eval_time)

    def _prom_gauge_max(
        self,
        metric: str,
        window: str,
        label_filter: str,
        eval_time: Optional[float],
    ) -> Optional[float]:
        expr = f"max(max_over_time({metric}{{{label_filter}}}[{window}]))"
        return self._prom_query(expr, eval_time)

    @staticmethod
    def _iso_to_unix(iso_timestamp: str) -> float:
        ts = iso_timestamp.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
