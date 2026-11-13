"""
Report generation and management service

Handles creating, saving, and listing evaluation reports.
"""

import csv
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime
from typing import List, Dict, Any, Union, Optional
from ..config import Settings
try:
    from confluent_kafka import Consumer, TopicPartition
    _KAFKA_AVAILABLE = True
except ImportError:
    Consumer = None  # type: ignore
    TopicPartition = None  # type: ignore
    _KAFKA_AVAILABLE = False

logger = logging.getLogger(__name__)


class ReportService:
    """
    Service for managing evaluation reports.

    Provides functionality to:
    - Generate evaluation reports with statistics
    - Save reports to filesystem
    - List and retrieve existing reports
    """

    def __init__(self, settings: Settings):
        """
        Initialize ReportService with settings.

        Args:
            settings: Application settings containing reports directory
        """
        self._settings = settings
        self._ensure_reports_dir()

    PROMETHEUS_REPORT_SCHEMA_VERSION = 3

    # Highest finite buckets defined by Alert Bridge. A percentile returned at
    # one of these ceilings is a censored lower bound, not an exact value.
    PROMETHEUS_STAGE_BUCKET_MAX_SECONDS = {
        "upstream": 60.0,
        "kafka_lag": 5.0,
        "worker_queue_wait": 10.0,
        "vst": 10.0,
        "video_length": 60.0,
        "vlm": 30.0,
        "worker_processing": 60.0,
        "e2e": 120.0,
    }

    PROMETHEUS_STAGE_DEFINITIONS = [
        {
            "metric_key": "upstream",
            "stage": "upstream",
            "display": "Upstream (CV+Analytics)",
            "category": "latency_stage",
            "metric_name": "alert_bridge_upstream_duration_seconds",
        },
        {
            "metric_key": "kafka_lag",
            "stage": "kafka_lag",
            "display": "AB Consumer Lag",
            "category": "latency_stage",
            "metric_name": "alert_bridge_kafka_lag_duration_seconds",
        },
        {
            "metric_key": "worker_queue_wait",
            "stage": "worker_queue_wait",
            "display": "Worker Queue Wait",
            "category": "latency_stage",
            "metric_name": "alert_bridge_worker_queue_wait_duration_seconds",
        },
        {
            "metric_key": "vst",
            "stage": "vst_fetch",
            "display": "VST Fetch",
            "category": "latency_stage",
            "metric_name": "alert_bridge_vst_duration_seconds",
        },
        {
            "metric_key": "vlm",
            "stage": "vlm_inference",
            "display": "VLM Inference",
            "category": "latency_stage",
            "metric_name": "alert_bridge_vlm_duration_seconds",
        },
        {
            "metric_key": "worker_processing",
            "stage": "worker_processing",
            "display": "Worker Processing",
            "category": "latency_stage",
            "metric_name": "alert_bridge_worker_processing_seconds",
        },
        {
            "metric_key": "e2e",
            "stage": "e2e",
            "display": "End-to-End",
            "category": "end_to_end",
            "metric_name": "alert_bridge_e2e_duration_seconds",
        },
        {
            "metric_key": "video_length",
            "stage": "video_length",
            "display": "Video Length",
            "category": "input_context",
            "metric_name": "alert_bridge_video_length_seconds",
        },
    ]

    RUN_SUMMARY_FIELDNAMES = [
        "schema_version",
        "job_id",
        "generated_at",
        "data_source",
        "metric_profile",
        "eval_mode",
        "alerts_mode",
        "platform",
        "model",
        "hardware_profile",
        "prometheus_url",
        "window_start_ts",
        "window_end_ts",
        "window_seconds",
        "buffer_seconds",
        "configured_latency_duration_seconds",
        "observed_incident_start_ts",
        "observed_incident_end_ts",
        "observed_incident_window_seconds",
        "incident_coverage_ratio",
        "summary_status",
        "synthetic_alerts_injection",
        "synthetic_alert_submission_mode",
        "synthetic_alerts_per_minute",
        "synthetic_alert_event_duration_ms",
        "synthetic_distribution_strategy",
        "synthetic_target_submissions",
        "synthetic_attempted_submissions",
        "synthetic_submitted",
        "synthetic_failed_submissions",
        "synthetic_missed_submissions",
        "submission_coverage_ratio",
        "terminal_coverage_ratio",
        "e2e_min_coverage_ratio",
        "metrics_counts_source",
        "sensors_requested",
        "sensors_reported",
        "sensors_with_latency_data",
        "sensors_no_data",
        "sensors_error",
        "events_after_dedup",
        "verified_events",
        "terminal_outcomes",
        "successful_verifications",
        "confirmed_events",
        "rejected_events",
        "verification_failed_events",
        "unknown_events",
        "dropped_events",
        "skipped_confirmed_events",
        "verification_failures",
        "error_events_total",
        "drop_events_total",
        "top_drop_reason",
        "verification_failures_total",
        "top_verification_failure_reason",
        "async_fallbacks_total",
        "top_async_fallback_reason",
        "async_external_io_errors_total",
        "realtime_rule_failures_total",
        "replay_rule_failures_total",
        "rtvi_call_failures_total",
        "incident_query_failures_total",
        "top_error_category",
        "top_error_reason",
        "error_breakdown_file",
        "e2e_avg_sec",
        "e2e_p50_sec",
        "e2e_p90_sec",
        "e2e_p99_sec",
        "e2e_p50_censored",
        "e2e_p90_censored",
        "e2e_p99_censored",
        "e2e_bucket_max_sec",
        "e2e_count",
        "e2e_expected_count",
        "e2e_observed_count",
        "e2e_missing_count",
        "e2e_completion_ratio",
        "vlm_p90_sec",
        "vst_p90_sec",
        "queue_wait_p90_sec",
        "kafka_lag_p90_sec",
        "top_bottleneck_stage",
        "top_bottleneck_avg_sec",
        "verified_events_per_min_per_stream",
        "confirmed_events_per_min_per_stream",
        "kafka_vlm_messages",
        "kafka_msg_per_sec_avg",
        "kafka_msg_per_sec_peak",
    ]

    STAGE_BREAKDOWN_FIELDNAMES = [
        "job_id",
        "stage",
        "stage_display_name",
        "category",
        "avg_sec",
        "p50_sec",
        "p90_sec",
        "p99_sec",
        "p50_censored",
        "p90_censored",
        "p99_censored",
        "finite_bucket_max_sec",
        "count",
        "e2e_sample_count",
        "population_aligned",
        "share_of_e2e_avg_pct",
        "bottleneck_rank",
        "data_status",
    ]

    ERROR_BREAKDOWN_FIELDNAMES = [
        "job_id",
        "window_start_ts",
        "window_end_ts",
        "error_category",
        "reason",
        "operation",
        "count",
        "share_pct",
    ]

    SENSOR_DRILLDOWN_FIELDNAMES = [
        "job_id",
        "sensor_id",
        "window_start_ts",
        "window_end_ts",
        "status",
        "error",
        "has_latency_data",
        "dominant_stage",
        "e2e_avg_sec",
        "e2e_p90_sec",
        "e2e_p99_sec",
        "e2e_count",
        "vlm_avg_sec",
        "vlm_p90_sec",
        "vst_avg_sec",
        "vst_p90_sec",
        "queue_wait_avg_sec",
        "queue_wait_p90_sec",
        "kafka_lag_avg_sec",
        "kafka_lag_p90_sec",
        "worker_processing_avg_sec",
        "worker_processing_p90_sec",
        "video_length_avg_sec",
        "events_after_dedup",
        "verified_events",
        "confirmed_events",
        "rejected_events",
        "dropped_events",
        "skipped_confirmed_events",
        "verification_failures",
        "sensor_rank_by_e2e_p90",
        "sensor_rank_by_vlm_p90",
        "sensor_rank_by_drop_count",
        "sensor_rank_by_no_data",
    ]

    PROMETHEUS_REPORT_FILES = {
        "run_summary_csv": "latency_run_summary.csv",
        "stage_breakdown_csv": "latency_stage_breakdown.csv",
        "sensor_drilldown_csv": "latency_sensor_drilldown.csv",
        "error_breakdown_csv": "error_breakdown.csv",
    }

    @staticmethod
    def _num(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _int_num(cls, value: Any) -> int:
        numeric = cls._num(value)
        if numeric is None:
            return 0
        return int(round(numeric))

    @classmethod
    def _float_num(cls, value: Any) -> float:
        numeric = cls._num(value)
        if numeric is None:
            return 0.0
        return round(numeric, 4)

    @classmethod
    def _csv_value(cls, value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            return f"{value:.4f}"
        return value

    @classmethod
    def _write_csv_rows(cls, path: str, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: cls._csv_value(row.get(key)) for key in fieldnames})

    @staticmethod
    def _write_json(path: str, payload: Any) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)

    def generate_prometheus_latency_report_suite(
        self,
        job_id: str,
        evaluation_results: List[Dict[str, Any]],
        latency_summary: Dict[str, Any],
        prometheus_aggregate_summary: Optional[Dict[str, Any]],
        duration_str: str = "1h",
    ) -> Dict[str, Any]:
        """Generate the Prometheus alerts latency v2 report suite."""
        self._ensure_reports_dir()
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        aggregate = prometheus_aggregate_summary if isinstance(prometheus_aggregate_summary, dict) else {}
        kafka_stats = self._count_kafka_vlm_messages(duration_str)

        stage_rows = self._build_prometheus_stage_rows(job_id, aggregate)
        sensor_rows = self._build_prometheus_sensor_rows(job_id, evaluation_results)
        error_rows = self._build_prometheus_error_rows(job_id, aggregate)
        run_summary = self._build_prometheus_run_summary(
            job_id=job_id,
            generated_at=generated_at,
            evaluation_results=evaluation_results,
            latency_summary=latency_summary or {},
            aggregate=aggregate,
            stage_rows=stage_rows,
            sensor_rows=sensor_rows,
            error_rows=error_rows,
            kafka_stats=kafka_stats,
            duration_str=duration_str,
        )
        report_paths = {
            key: os.path.join(self._settings.reports_dir, filename)
            for key, filename in self.PROMETHEUS_REPORT_FILES.items()
        }
        self._write_csv_rows(
            report_paths["run_summary_csv"],
            self.RUN_SUMMARY_FIELDNAMES,
            [run_summary],
        )
        self._write_csv_rows(
            report_paths["stage_breakdown_csv"],
            self.STAGE_BREAKDOWN_FIELDNAMES,
            stage_rows,
        )
        self._write_csv_rows(
            report_paths["sensor_drilldown_csv"],
            self.SENSOR_DRILLDOWN_FIELDNAMES,
            sensor_rows,
        )
        self._write_csv_rows(
            report_paths["error_breakdown_csv"],
            self.ERROR_BREAKDOWN_FIELDNAMES,
            error_rows,
        )

        return {
            "csv_filename": self.PROMETHEUS_REPORT_FILES["run_summary_csv"],
            "csv_path": report_paths["run_summary_csv"],
            "rows_written": 1,
            "report_files": dict(self.PROMETHEUS_REPORT_FILES),
            "report_paths": report_paths,
            "summary": run_summary,
        }

    def _build_prometheus_error_rows(
        self,
        job_id: str,
        aggregate: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        errors = aggregate.get("errors") or {}
        start_ts = aggregate.get("start_ts") or ""
        end_ts = aggregate.get("end_ts") or ""
        specs = [
            ("drop", errors.get("drop_reasons") or []),
            ("verification_failure", errors.get("verification_failure_reasons") or []),
            ("async_fallback", errors.get("async_fallback_reasons") or []),
            ("async_external_io_error", errors.get("async_external_io_errors") or []),
            ("realtime_rule_failure", errors.get("realtime_rule_failure_stages") or []),
            ("ondemand_request_error", errors.get("ondemand_request_outcomes") or []),
            ("ondemand_verification_failure", errors.get("ondemand_verification_failure_reasons") or []),
        ]
        rows: List[Dict[str, Any]] = []
        for category, entries in specs:
            total = sum(self._int_num(entry.get("count")) for entry in entries)
            for entry in entries:
                count = self._int_num(entry.get("count"))
                if count <= 0:
                    continue
                rows.append(
                    {
                        "job_id": job_id,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "error_category": category,
                        "reason": self._error_row_reason(category, entry),
                        "operation": self._error_row_operation(category, entry),
                        "count": count,
                        "share_pct": round(count / total * 100, 4) if total else None,
                    }
                )
        for category, count in (
            ("replay_rule_failure", self._int_num(errors.get("replay_rule_failures"))),
        ):
            if count <= 0:
                continue
            rows.append(
                {
                    "job_id": job_id,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "error_category": category,
                    "reason": "",
                    "operation": "",
                    "count": count,
                    "share_pct": 100.0,
                }
            )

        for category, entries in (("rtvi_call_failure", errors.get("rtvi_call_failure_methods") or []),):
            total = sum(self._int_num(entry.get("count")) for entry in entries)
            for entry in entries:
                count = self._int_num(entry.get("count"))
                if count <= 0:
                    continue
                rows.append(
                    {
                        "job_id": job_id,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "error_category": category,
                        "reason": self._error_row_reason(category, entry),
                        "operation": self._error_row_operation(category, entry),
                        "count": count,
                        "share_pct": round(count / total * 100, 4) if total else None,
                    }
                )

        for category, count in (
            ("incident_query_failure", self._int_num(errors.get("incident_query_failures"))),
        ):
            if count <= 0:
                continue
            rows.append(
                {
                    "job_id": job_id,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "error_category": category,
                    "reason": "",
                    "operation": "",
                    "count": count,
                    "share_pct": 100.0,
                }
            )
        return rows

    @staticmethod
    def _error_row_reason(category: str, entry: Dict[str, Any]) -> str:
        if category == "async_external_io_error":
            return str(entry.get("result") or "")
        if category == "realtime_rule_failure":
            return str(entry.get("stage") or "")
        if category == "rtvi_call_failure":
            return str(entry.get("method") or "")
        if category == "ondemand_request_error":
            return str(entry.get("outcome") or "")
        return str(entry.get("reason") or "")

    @staticmethod
    def _error_row_operation(category: str, entry: Dict[str, Any]) -> str:
        operation = str(entry.get("operation") or "")
        if category == "async_external_io_error" and entry.get("mode"):
            return f"{operation}:{entry.get('mode')}" if operation else str(entry.get("mode") or "")
        return operation

    @classmethod
    def _summarize_error_rows(cls, error_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        summary = {
            "error_events_total": 0,
            "drop_events_total": 0,
            "top_drop_reason": "",
            "verification_failures_total": 0,
            "top_verification_failure_reason": "",
            "async_fallbacks_total": 0,
            "top_async_fallback_reason": "",
            "async_external_io_errors_total": 0,
            "realtime_rule_failures_total": 0,
            "replay_rule_failures_total": 0,
            "rtvi_call_failures_total": 0,
            "incident_query_failures_total": 0,
            "top_error_category": "",
            "top_error_reason": "",
        }
        by_category: Dict[str, List[Dict[str, Any]]] = {
            "drop": [],
            "verification_failure": [],
            "async_fallback": [],
            "async_external_io_error": [],
            "realtime_rule_failure": [],
            "replay_rule_failure": [],
            "rtvi_call_failure": [],
            "incident_query_failure": [],
            "ondemand_request_error": [],
            "ondemand_verification_failure": [],
        }
        for row in error_rows:
            category = str(row.get("error_category") or "")
            count = cls._int_num(row.get("count"))
            if count <= 0 or category not in by_category:
                continue
            copied = dict(row)
            copied["count"] = count
            by_category[category].append(copied)
            summary["error_events_total"] += count

        def top_reason(rows: List[Dict[str, Any]], *, include_operation: bool = False) -> str:
            if not rows:
                return ""
            rows = sorted(
                rows,
                key=lambda item: (
                    -cls._int_num(item.get("count")),
                    str(item.get("operation") or ""),
                    str(item.get("reason") or ""),
                ),
            )
            first = rows[0]
            reason = str(first.get("reason") or "")
            if include_operation and first.get("operation"):
                return f"{first.get('operation')}:{reason}" if reason else str(first.get("operation"))
            return reason

        summary["drop_events_total"] = sum(
            cls._int_num(row.get("count")) for row in by_category["drop"]
        )
        summary["top_drop_reason"] = top_reason(by_category["drop"])
        summary["verification_failures_total"] = sum(
            cls._int_num(row.get("count")) for row in by_category["verification_failure"]
        )
        summary["top_verification_failure_reason"] = top_reason(by_category["verification_failure"])
        summary["async_fallbacks_total"] = sum(
            cls._int_num(row.get("count")) for row in by_category["async_fallback"]
        )
        summary["top_async_fallback_reason"] = top_reason(
            by_category["async_fallback"], include_operation=True
        )
        summary["async_external_io_errors_total"] = sum(
            cls._int_num(row.get("count")) for row in by_category["async_external_io_error"]
        )
        summary["realtime_rule_failures_total"] = sum(
            cls._int_num(row.get("count")) for row in by_category["realtime_rule_failure"]
        )
        summary["replay_rule_failures_total"] = sum(
            cls._int_num(row.get("count")) for row in by_category["replay_rule_failure"]
        )
        summary["rtvi_call_failures_total"] = sum(
            cls._int_num(row.get("count")) for row in by_category["rtvi_call_failure"]
        )
        summary["incident_query_failures_total"] = sum(
            cls._int_num(row.get("count")) for row in by_category["incident_query_failure"]
        )
        top_rows = sorted(
            (row for rows in by_category.values() for row in rows),
            key=lambda item: (
                -cls._int_num(item.get("count")),
                str(item.get("error_category") or ""),
                str(item.get("operation") or ""),
                str(item.get("reason") or ""),
            ),
        )
        if top_rows:
            summary["top_error_category"] = str(top_rows[0].get("error_category") or "")
            summary["top_error_reason"] = top_reason([top_rows[0]], include_operation=False)
        return summary

    def _build_prometheus_stage_rows(
        self,
        job_id: str,
        aggregate: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        stats = aggregate.get("stats") or {}
        not_applicable_stages = set(aggregate.get("not_applicable_stages") or [])
        e2e_stats = stats.get("e2e") or {}
        e2e_avg = self._num(e2e_stats.get("avg"))
        e2e_count = self._int_num(e2e_stats.get("count"))
        rows: List[Dict[str, Any]] = []
        for definition in self.PROMETHEUS_STAGE_DEFINITIONS:
            metric_key = definition["metric_key"]
            stage_stats = stats.get(metric_key) or {}
            avg = self._num(stage_stats.get("avg"))
            count = self._int_num(stage_stats.get("count"))
            population_aligned = bool(
                count > 0 and e2e_count > 0 and count == e2e_count
            )
            share = None
            if (
                population_aligned
                and avg is not None
                and e2e_avg
                and definition["category"] != "input_context"
            ):
                # This is a comparison of aligned-population averages, not an
                # additive decomposition of E2E latency.
                share = round(avg / e2e_avg * 100, 4)
            bucket_max = self.PROMETHEUS_STAGE_BUCKET_MAX_SECONDS.get(metric_key)
            p50 = self._num(stage_stats.get("p50"))
            p90 = self._num(stage_stats.get("p90"))
            p99 = self._num(stage_stats.get("p99"))
            if definition["metric_key"] in not_applicable_stages:
                data_status = "not_applicable"
            else:
                data_status = "has_data" if count > 0 else "no_data"
            rows.append(
                {
                    "job_id": job_id,
                    "stage": definition["stage"],
                    "stage_display_name": definition["display"],
                    "category": definition["category"],
                    "avg_sec": avg,
                    "p50_sec": p50,
                    "p90_sec": p90,
                    "p99_sec": p99,
                    "p50_censored": bool(bucket_max is not None and p50 is not None and p50 >= bucket_max),
                    "p90_censored": bool(bucket_max is not None and p90 is not None and p90 >= bucket_max),
                    "p99_censored": bool(bucket_max is not None and p99 is not None and p99 >= bucket_max),
                    "finite_bucket_max_sec": bucket_max,
                    "count": count,
                    "e2e_sample_count": e2e_count,
                    "population_aligned": population_aligned,
                    "share_of_e2e_avg_pct": share,
                    "bottleneck_rank": "",
                    "data_status": data_status,
                }
            )

        ranked = [
            row for row in rows
            if row["category"] == "latency_stage" and row["avg_sec"] is not None
        ]
        ranked.sort(key=lambda row: row["avg_sec"], reverse=True)
        for rank, row in enumerate(ranked, start=1):
            row["bottleneck_rank"] = rank
        return rows

    def _build_prometheus_sensor_rows(
        self,
        job_id: str,
        evaluation_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for result in evaluation_results:
            stats = result.get("stats") or {}
            reliability = result.get("reliability") or {}
            e2e = stats.get("e2e") or {}
            has_latency_data = any(
                self._int_num((stats.get(key) or {}).get("count")) > 0
                for key in ("upstream", "kafka_lag", "worker_queue_wait", "vst", "vlm", "worker_processing", "e2e")
            )
            dominant_stage = self._dominant_sensor_stage(stats)
            verified_events = self._int_num(reliability.get("total_events"))
            if verified_events <= 0:
                verified_events = (
                    self._int_num(reliability.get("confirmed_events"))
                    + self._int_num(reliability.get("rejected_events"))
                    + self._int_num(reliability.get("verification_failed_events"))
                    + self._int_num(reliability.get("unknown_events"))
                )
            rows.append(
                {
                    "job_id": job_id,
                    "sensor_id": result.get("sensor_id") or result.get("video") or result.get("video_id") or "",
                    "window_start_ts": result.get("start_ts", ""),
                    "window_end_ts": result.get("end_ts", ""),
                    "status": result.get("status", ""),
                    "error": result.get("error", ""),
                    "has_latency_data": has_latency_data,
                    "dominant_stage": dominant_stage,
                    "e2e_avg_sec": self._num(e2e.get("avg")),
                    "e2e_p90_sec": self._num(e2e.get("p90")),
                    "e2e_p99_sec": self._num(e2e.get("p99")),
                    "e2e_count": self._int_num(e2e.get("count")),
                    "vlm_avg_sec": self._num((stats.get("vlm") or {}).get("avg")),
                    "vlm_p90_sec": self._num((stats.get("vlm") or {}).get("p90")),
                    "vst_avg_sec": self._num((stats.get("vst") or {}).get("avg")),
                    "vst_p90_sec": self._num((stats.get("vst") or {}).get("p90")),
                    "queue_wait_avg_sec": self._num((stats.get("worker_queue_wait") or {}).get("avg")),
                    "queue_wait_p90_sec": self._num((stats.get("worker_queue_wait") or {}).get("p90")),
                    "kafka_lag_avg_sec": self._num((stats.get("kafka_lag") or {}).get("avg")),
                    "kafka_lag_p90_sec": self._num((stats.get("kafka_lag") or {}).get("p90")),
                    "worker_processing_avg_sec": self._num((stats.get("worker_processing") or {}).get("avg")),
                    "worker_processing_p90_sec": self._num((stats.get("worker_processing") or {}).get("p90")),
                    "video_length_avg_sec": self._num((stats.get("video_length") or {}).get("avg")),
                    "events_after_dedup": self._int_num(reliability.get("events_after_dedup")),
                    "verified_events": verified_events,
                    "confirmed_events": self._int_num(reliability.get("confirmed_events")),
                    "rejected_events": self._int_num(reliability.get("rejected_events")),
                    "dropped_events": self._int_num(reliability.get("dropped_events")),
                    "skipped_confirmed_events": self._int_num(reliability.get("skipped_confirmed_events")),
                    "verification_failures": self._int_num(reliability.get("verification_failures")),
                    "sensor_rank_by_e2e_p90": 0,
                    "sensor_rank_by_vlm_p90": 0,
                    "sensor_rank_by_drop_count": 0,
                    "sensor_rank_by_no_data": 0,
                }
            )

        self._assign_descending_rank(rows, "e2e_p90_sec", "sensor_rank_by_e2e_p90")
        self._assign_descending_rank(rows, "vlm_p90_sec", "sensor_rank_by_vlm_p90")
        self._assign_descending_rank(rows, "dropped_events", "sensor_rank_by_drop_count")
        no_data_rows = [row for row in rows if not row["has_latency_data"]]
        for rank, row in enumerate(no_data_rows, start=1):
            row["sensor_rank_by_no_data"] = rank
        return rows

    def _dominant_sensor_stage(self, stats: Dict[str, Any]) -> str:
        candidates = []
        for definition in self.PROMETHEUS_STAGE_DEFINITIONS:
            if definition["category"] != "latency_stage":
                continue
            avg = self._num((stats.get(definition["metric_key"]) or {}).get("avg"))
            if avg is not None:
                candidates.append((avg, definition["stage"]))
        if not candidates:
            return ""
        candidates.sort(reverse=True)
        return candidates[0][1]

    @staticmethod
    def _assign_descending_rank(rows: List[Dict[str, Any]], value_key: str, rank_key: str) -> None:
        ranked = [row for row in rows if row.get(value_key) is not None]
        ranked.sort(key=lambda row: row[value_key], reverse=True)
        for rank, row in enumerate(ranked, start=1):
            row[rank_key] = rank

    def _build_prometheus_run_summary(
        self,
        *,
        job_id: str,
        generated_at: str,
        evaluation_results: List[Dict[str, Any]],
        latency_summary: Dict[str, Any],
        aggregate: Dict[str, Any],
        stage_rows: List[Dict[str, Any]],
        sensor_rows: List[Dict[str, Any]],
        error_rows: List[Dict[str, Any]],
        kafka_stats: Dict[str, Any],
        duration_str: str,
    ) -> Dict[str, Any]:
        reliability = latency_summary.get("reliability") or aggregate.get("reliability") or {}
        e2e_stats = (aggregate.get("stats") or {}).get("e2e") or {}
        sensors_reported = len({row["sensor_id"] for row in sensor_rows if row.get("sensor_id")})
        sensors_with_latency = sum(1 for row in sensor_rows if row.get("has_latency_data"))
        sensors_error = sum(1 for row in sensor_rows if row.get("status") == "error")
        sensors_requested = int(getattr(self._settings, "max_stream_concurrency", 0) or 0) or sensors_reported
        verified_events = self._int_num(reliability.get("total_verified_events"))
        if verified_events <= 0:
            verified_events = self._int_num(reliability.get("total_events"))
        if verified_events <= 0:
            verified_events = (
                self._int_num(reliability.get("confirmed_events"))
                + self._int_num(reliability.get("rejected_events"))
                + self._int_num(reliability.get("verification_failed_events"))
                + self._int_num(reliability.get("unknown_events"))
            )

        duration_minutes = self._parse_duration_to_minutes(duration_str)
        rate_denominator = duration_minutes * sensors_requested if duration_minutes > 0 and sensors_requested > 0 else 0
        verified_rate = round(verified_events / rate_denominator, 4) if rate_denominator else None
        confirmed_rate = (
            round(self._int_num(reliability.get("confirmed_events")) / rate_denominator, 4)
            if rate_denominator else None
        )
        top_bottleneck = next(
            (row for row in stage_rows if row.get("bottleneck_rank") == 1),
            None,
        )
        error_summary = self._summarize_error_rows(error_rows)
        synthetic_enabled = bool(getattr(self._settings, "synthetic_alerts_injection", False))
        synthetic_rate = self._num(getattr(self._settings, "synthetic_alerts_per_minute", None))
        if synthetic_enabled and synthetic_rate is None and sensors_requested > 0:
            synthetic_rate = float(sensors_requested)
        synthetic_event_duration_ms = getattr(
            self._settings, "synthetic_alert_event_duration_ms", None
        )
        if synthetic_enabled and synthetic_event_duration_ms is None:
            synthetic_event_duration_ms = int(
                getattr(self._settings, "vst_media_window_seconds", 30) or 30
            ) * 1000
        completeness = latency_summary.get("synthetic_completeness") or {}
        synthetic_target = self._int_num(completeness.get("target_submissions"))
        if (
            synthetic_enabled
            and synthetic_target <= 0
            and synthetic_rate is not None
            and duration_minutes > 0
        ):
            synthetic_target = max(1, int(synthetic_rate * duration_minutes))
        synthetic_attempted = self._int_num(completeness.get("attempted"))
        synthetic_submitted = self._int_num(completeness.get("submitted"))
        synthetic_failed = self._int_num(completeness.get("failed"))
        synthetic_missed = self._int_num(completeness.get("missed"))
        terminal_outcomes = self._int_num(
            completeness.get("terminal_outcomes")
            if "terminal_outcomes" in completeness
            else reliability.get("terminal_outcomes", verified_events)
        )
        successful_verifications = self._int_num(
            completeness.get("successful_verifications")
            if "successful_verifications" in completeness
            else reliability.get("successful_verifications")
        )
        if successful_verifications <= 0:
            successful_verifications = (
                self._int_num(reliability.get("confirmed_events"))
                + self._int_num(reliability.get("rejected_events"))
            )
        e2e_observed = self._int_num(
            e2e_stats.get("count") if "count" in e2e_stats else e2e_stats.get("n")
        )
        e2e_expected = terminal_outcomes if synthetic_enabled else ""
        e2e_missing = max(0, int(e2e_expected) - e2e_observed) if e2e_expected != "" else ""
        e2e_completion_ratio = (
            round(e2e_observed / int(e2e_expected), 4)
            if e2e_expected not in ("", 0)
            else ""
        )
        submission_coverage = completeness.get("submission_coverage_ratio", "")
        terminal_coverage = completeness.get("terminal_coverage_ratio", "")
        e2e_min_coverage = completeness.get(
            "e2e_min_coverage_ratio",
            getattr(self._settings, "synthetic_e2e_min_coverage_ratio", 0.99),
        )
        e2e_bucket_max = self.PROMETHEUS_STAGE_BUCKET_MAX_SECONDS["e2e"]
        e2e_p50 = self._num(e2e_stats.get("p50"))
        e2e_p90 = self._num(e2e_stats.get("p90"))
        e2e_p99 = self._num(e2e_stats.get("p99"))
        if e2e_missing not in ("", 0):
            logger.warning(
                "E2E latency coverage is incomplete: expected=%s observed=%s missing=%s",
                e2e_expected, e2e_observed, e2e_missing,
            )

        return {
            "schema_version": self.PROMETHEUS_REPORT_SCHEMA_VERSION,
            "job_id": job_id,
            "generated_at": generated_at,
            "data_source": getattr(self._settings, "data_source", "prometheus") or "prometheus",
            "metric_profile": aggregate.get("metric_profile") or getattr(self._settings, "prometheus_metric_profile", "realtime") or "realtime",
            "eval_mode": getattr(self._settings, "eval_mode", "latency") or "latency",
            "alerts_mode": getattr(self._settings, "eval_workflow", "") or "verification",
            "platform": getattr(self._settings, "platform", "") or "",
            "model": getattr(self._settings, "model", "") or "",
            "hardware_profile": getattr(self._settings, "hardware_profile", "") or getattr(self._settings, "platform", "") or "",
            "prometheus_url": getattr(self._settings, "prometheus_url", "") or "",
            "window_start_ts": aggregate.get("start_ts") or (latency_summary.get("prometheus_window") or {}).get("start_ts") or "",
            "window_end_ts": aggregate.get("end_ts") or (latency_summary.get("prometheus_window") or {}).get("end_ts") or "",
            "window_seconds": self._int_num(aggregate.get("window_seconds") or (latency_summary.get("prometheus_window") or {}).get("window_seconds")),
            "buffer_seconds": self._int_num(aggregate.get("buffer_seconds") or (latency_summary.get("prometheus_window") or {}).get("buffer_seconds")),
            "configured_latency_duration_seconds": self._int_num(aggregate.get("configured_latency_duration_seconds") or (latency_summary.get("prometheus_window") or {}).get("configured_latency_duration_seconds")),
            "observed_incident_start_ts": aggregate.get("observed_incident_start_ts") or (latency_summary.get("prometheus_window") or {}).get("observed_incident_start_ts") or "",
            "observed_incident_end_ts": aggregate.get("observed_incident_end_ts") or (latency_summary.get("prometheus_window") or {}).get("observed_incident_end_ts") or "",
            "observed_incident_window_seconds": self._int_num(aggregate.get("observed_incident_window_seconds") or (latency_summary.get("prometheus_window") or {}).get("observed_incident_window_seconds")),
            "incident_coverage_ratio": self._float_num(aggregate.get("incident_coverage_ratio") or (latency_summary.get("prometheus_window") or {}).get("incident_coverage_ratio")),
            "summary_status": latency_summary.get("summary_status") or aggregate.get("status") or "unknown",
            "synthetic_alerts_injection": synthetic_enabled,
            "synthetic_alert_submission_mode": (
                getattr(self._settings, "synthetic_alert_submission_mode", "") if synthetic_enabled else ""
            ),
            "synthetic_alerts_per_minute": synthetic_rate if synthetic_enabled else "",
            "synthetic_alert_event_duration_ms": synthetic_event_duration_ms if synthetic_enabled else "",
            "synthetic_distribution_strategy": "round_robin" if synthetic_enabled else "",
            "synthetic_target_submissions": synthetic_target if synthetic_enabled else "",
            "synthetic_attempted_submissions": synthetic_attempted if synthetic_enabled else "",
            "synthetic_submitted": synthetic_submitted if synthetic_enabled else "",
            "synthetic_failed_submissions": synthetic_failed if synthetic_enabled else "",
            "synthetic_missed_submissions": synthetic_missed if synthetic_enabled else "",
            "submission_coverage_ratio": submission_coverage if synthetic_enabled else "",
            "terminal_coverage_ratio": terminal_coverage if synthetic_enabled else "",
            "e2e_min_coverage_ratio": e2e_min_coverage if synthetic_enabled else "",
            "metrics_counts_source": (
                completeness.get("metrics_counts_source", "")
                if synthetic_enabled
                else ""
            ),
            "sensors_requested": sensors_requested,
            "sensors_reported": sensors_reported,
            "sensors_with_latency_data": sensors_with_latency,
            "sensors_no_data": max(0, sensors_reported - sensors_with_latency - sensors_error),
            "sensors_error": sensors_error,
            "events_after_dedup": self._int_num(reliability.get("events_after_dedup")),
            "verified_events": verified_events,
            "terminal_outcomes": terminal_outcomes,
            "successful_verifications": successful_verifications,
            "confirmed_events": self._int_num(reliability.get("confirmed_events")),
            "rejected_events": self._int_num(reliability.get("rejected_events")),
            "verification_failed_events": self._int_num(reliability.get("verification_failed_events")),
            "unknown_events": self._int_num(reliability.get("unknown_events")),
            "dropped_events": self._int_num(reliability.get("dropped_events")),
            "skipped_confirmed_events": self._int_num(reliability.get("skipped_confirmed_events")),
            "verification_failures": self._int_num(reliability.get("verification_failures")),
            "error_events_total": error_summary["error_events_total"],
            "drop_events_total": error_summary["drop_events_total"],
            "top_drop_reason": error_summary["top_drop_reason"],
            "verification_failures_total": error_summary["verification_failures_total"],
            "top_verification_failure_reason": error_summary["top_verification_failure_reason"],
            "async_fallbacks_total": error_summary["async_fallbacks_total"],
            "top_async_fallback_reason": error_summary["top_async_fallback_reason"],
            "async_external_io_errors_total": error_summary["async_external_io_errors_total"],
            "realtime_rule_failures_total": error_summary["realtime_rule_failures_total"],
            "replay_rule_failures_total": error_summary["replay_rule_failures_total"],
            "rtvi_call_failures_total": error_summary["rtvi_call_failures_total"],
            "incident_query_failures_total": error_summary["incident_query_failures_total"],
            "top_error_category": error_summary["top_error_category"],
            "top_error_reason": error_summary["top_error_reason"],
            "error_breakdown_file": self.PROMETHEUS_REPORT_FILES["error_breakdown_csv"],
            "e2e_avg_sec": self._num(e2e_stats.get("avg")),
            "e2e_p50_sec": e2e_p50,
            "e2e_p90_sec": e2e_p90,
            "e2e_p99_sec": e2e_p99,
            "e2e_p50_censored": bool(e2e_p50 is not None and e2e_p50 >= e2e_bucket_max),
            "e2e_p90_censored": bool(e2e_p90 is not None and e2e_p90 >= e2e_bucket_max),
            "e2e_p99_censored": bool(e2e_p99 is not None and e2e_p99 >= e2e_bucket_max),
            "e2e_bucket_max_sec": e2e_bucket_max,
            "e2e_count": e2e_observed,
            "e2e_expected_count": e2e_expected,
            "e2e_observed_count": e2e_observed if synthetic_enabled else "",
            "e2e_missing_count": e2e_missing,
            "e2e_completion_ratio": e2e_completion_ratio,
            "vlm_p90_sec": self._stage_value(aggregate, "vlm", "p90"),
            "vst_p90_sec": self._stage_value(aggregate, "vst", "p90"),
            "queue_wait_p90_sec": self._stage_value(aggregate, "worker_queue_wait", "p90"),
            "kafka_lag_p90_sec": self._stage_value(aggregate, "kafka_lag", "p90"),
            "top_bottleneck_stage": top_bottleneck["stage"] if top_bottleneck else "",
            "top_bottleneck_avg_sec": top_bottleneck["avg_sec"] if top_bottleneck else None,
            "verified_events_per_min_per_stream": verified_rate,
            "confirmed_events_per_min_per_stream": confirmed_rate,
            "kafka_vlm_messages": kafka_stats["total"] if kafka_stats.get("available") else "",
            "kafka_msg_per_sec_avg": kafka_stats["per_second_avg"] if kafka_stats.get("available") else "",
            "kafka_msg_per_sec_peak": kafka_stats["per_second_peak"] if kafka_stats.get("available") else "",
        }

    @staticmethod
    def _stage_value(aggregate: Dict[str, Any], stage_key: str, stat_key: str) -> Optional[float]:
        try:
            value = ((aggregate.get("stats") or {}).get(stage_key) or {}).get(stat_key)
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _ensure_reports_dir(self) -> None:
        """Create reports directory if it doesn't exist"""
        os.makedirs(self._settings.reports_dir, exist_ok=True)

    def generate(
        self,
        job_id: str,
        evaluation_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generate evaluation report with statistics.

        Args:
            job_id: Unique job identifier
            evaluation_results: List of evaluation result dictionaries

        Returns:
            Report information dictionary with:
            - report_filename: str
            - report_path: str
            - summary: dict (statistics)

        The report is saved to the reports directory as a JSON file.
        """
        self._ensure_reports_dir()

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_filename = f'report_{job_id}_{timestamp}.json'
        report_path = os.path.join(self._settings.reports_dir, report_filename)

        # Calculate statistics
        summary = self.calculate_summary(evaluation_results)

        # Generate full report
        report = {
            'job_id': job_id,
            'timestamp': timestamp,
            'summary': summary,
            'detailed_results': evaluation_results
        }

        # Save report to file
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)

        return {
            'report_filename': report_filename,
            'report_path': report_path,
            'summary': summary
        }

    def generate_csv(
        self,
        job_id: str,
        evaluation_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generate CSV evaluation report.

        Format:
        timestamp, video_name, system_prompt, question, actual_answer, expected_answer, eval_result, additional_info

        Args:
            job_id: Unique job identifier
            evaluation_results: List of evaluation result dictionaries

        Returns:
            Report information dictionary with:
            - csv_filename: str
            - csv_path: str
            - rows_written: int
        """
        self._ensure_reports_dir()

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_filename = f'report_{job_id}_{timestamp}.csv'
        csv_path = os.path.join(self._settings.reports_dir, csv_filename)

        # Define CSV columns
        fieldnames = [
            'timestamp',
            'video_name',
            'system_prompt',
            'question',
            'actual_answer',
            'expected_answer',
            'eval_result',
            'additional_info'
        ]

        rows_written = 0
        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for result in evaluation_results:
                # Extract data from result
                timestamp_val = result.get('timestamp', '')
                video_name = result.get('video', '')
                system_prompt = result.get('gt_system_prompt', '')
                question = result.get('gt_question', '')
                expected_answer = result.get('gt_answer', '')

                # Get verdict from result
                verdict = result.get('verdict', '')

                # Map verdict to actual answer
                # "confirmed" -> "Yes", "rejected" -> "No"
                # All other cases (unverified, verification-failed, timeout, error, etc.) -> "Unverified"
                if verdict == 'confirmed':
                    actual_answer = 'Yes'
                elif verdict == 'rejected':
                    actual_answer = 'No'
                else:
                    # Any other verdict (unverified, verification-failed, timeout, error, empty, etc.) = Unverified
                    actual_answer = 'Unverified'

                # Determine eval result based on match field
                match = result.get('match')
                if match is True:
                    eval_result = 'success'
                elif match is False:
                    eval_result = 'failure'
                else:
                    eval_result = 'no_ground_truth'

                # Build additional info for non-standard verdicts
                additional_info = ''

                # If verdict is not confirmed/rejected, save the full raw_response
                if verdict not in ['confirmed', 'rejected']:
                    raw_response = result.get('raw_response', {})
                    if raw_response:
                        # Convert raw_response to JSON string for CSV storage
                        try:
                            additional_info = json.dumps(raw_response, ensure_ascii=False)
                        except Exception:
                            # Fallback to string representation if JSON serialization fails
                            additional_info = str(raw_response)
                    else:
                        # If no raw_response, include basic info
                        info_parts = []
                        info_parts.append(f"verdict={verdict}")

                        reasoning = result.get('reasoning')
                        if reasoning:
                            info_parts.append(f"reasoning={reasoning}")

                        verification_code = result.get('verification_code')
                        if verification_code:
                            info_parts.append(f"code={verification_code}")

                        verification_status = result.get('verification_status')
                        if verification_status:
                            info_parts.append(f"status={verification_status}")

                        additional_info = '; '.join(info_parts)

                # Write row
                writer.writerow({
                    'timestamp': timestamp_val,
                    'video_name': video_name,
                    'system_prompt': system_prompt,
                    'question': question,
                    'actual_answer': actual_answer,
                    'expected_answer': expected_answer,
                    'eval_result': eval_result,
                    'additional_info': additional_info
                })
                rows_written += 1

        return {
            'csv_filename': csv_filename,
            'csv_path': csv_path,
            'rows_written': rows_written
        }

    def generate_csv_synthetic(
        self,
        job_id: str,
        evaluation_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generate CSV report for synthetic (BCQ) eval.
        Same columns as V1 accuracy CSV; filename prefixed with 'report_synthetic_'.
        """
        self._ensure_reports_dir()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_filename = f'report_synthetic_{job_id}_{timestamp}.csv'
        csv_path = os.path.join(self._settings.reports_dir, csv_filename)

        fieldnames = [
            'timestamp',
            'video_name',
            'system_prompt',
            'question',
            'actual_answer',
            'expected_answer',
            'eval_result',
            'additional_info',
        ]

        rows_written = 0
        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for result in evaluation_results:
                verdict = result.get('verdict', '')
                if verdict == 'confirmed':
                    actual_answer = 'Yes'
                elif verdict == 'rejected':
                    actual_answer = 'No'
                else:
                    actual_answer = 'Unverified'

                match = result.get('match')
                if match is True:
                    eval_result = 'success'
                elif match is False:
                    eval_result = 'failure'
                else:
                    eval_result = 'no_ground_truth'

                additional_info = ''
                if verdict not in ['confirmed', 'rejected']:
                    raw_response = result.get('raw_response', {})
                    if raw_response:
                        try:
                            additional_info = json.dumps(raw_response, ensure_ascii=False)
                        except Exception:
                            additional_info = str(raw_response)
                    else:
                        info_parts = [f"verdict={verdict}"]
                        if result.get('reasoning'):
                            info_parts.append(f"reasoning={result['reasoning']}")
                        if result.get('verification_code'):
                            info_parts.append(f"code={result['verification_code']}")
                        if result.get('verification_status'):
                            info_parts.append(f"status={result['verification_status']}")
                        additional_info = '; '.join(info_parts)

                writer.writerow({
                    'timestamp': result.get('timestamp', ''),
                    'video_name': result.get('video', ''),
                    'system_prompt': result.get('gt_system_prompt', ''),
                    'question': result.get('gt_question', ''),
                    'actual_answer': actual_answer,
                    'expected_answer': result.get('gt_answer', ''),
                    'eval_result': eval_result,
                    'additional_info': additional_info,
                })
                rows_written += 1

        return {
            'csv_filename': csv_filename,
            'csv_path': csv_path,
            'rows_written': rows_written,
        }

    def generate_csv_v2(
        self,
        job_id: str,
        evaluation_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generate CSV evaluation report for eval v2.

        Automatically detects latency mode by checking the first result for
        eval_mode == 'latency'.  When in latency mode the CSV contains
        per-incident latency breakdowns; otherwise the standard accuracy
        columns are written.
        """
        # Enforce latency-only report shape when evaluator is configured in latency mode,
        # even if results are empty or malformed.
        if getattr(self._settings, "eval_mode", "").lower() == "latency":
            if getattr(self._settings, "data_source", "").lower() == "prometheus":
                return self.generate_prometheus_latency_report_suite(
                    job_id=job_id,
                    evaluation_results=evaluation_results,
                    latency_summary={},
                    prometheus_aggregate_summary=None,
                    duration_str=getattr(self._settings, "latency_duration", "1h"),
                )
            return self._generate_latency_csv(job_id, evaluation_results)

        if not evaluation_results:
            return self._generate_accuracy_csv_v2(job_id, evaluation_results)
        first = evaluation_results[0]
        if first.get("eval_mode") == "latency":
            if first.get("data_source") == "prometheus":
                return self.generate_prometheus_latency_report_suite(
                    job_id=job_id,
                    evaluation_results=evaluation_results,
                    latency_summary={},
                    prometheus_aggregate_summary=None,
                    duration_str=getattr(self._settings, "latency_duration", "1h"),
                )
            return self._generate_latency_csv(job_id, evaluation_results)
        if "verdict" in first:
            return self.generate_csv_synthetic(job_id, evaluation_results)
        return self._generate_accuracy_csv_v2(job_id, evaluation_results)

    def _generate_accuracy_csv_v2(
        self,
        job_id: str,
        evaluation_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Standard accuracy CSV for eval v2 (events-based)."""
        self._ensure_reports_dir()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_filename = f'report_v2_{job_id}_{timestamp}.csv'
        csv_path = os.path.join(self._settings.reports_dir, csv_filename)
        fieldnames = [
            'timestamp',
            'sensor_id',
            'start_ts',
            'end_ts',
            'gt_events_count',
            'actual_events_count',
            'eval_result',
            'error'
        ]
        rows_written = 0
        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for result in evaluation_results:
                pass_val = 'pass' if result.get('pass') is True else 'fail'
                gt_count = result.get('gt_events_count')
                es_count = result.get('actual_events_count')
                writer.writerow({
                    'timestamp': result.get('timestamp', ''),
                    'sensor_id': result.get('video', '') or result.get('sensor_id', ''),
                    'start_ts': result.get('start_ts', ''),
                    'end_ts': result.get('end_ts', ''),
                    'gt_events_count': gt_count if gt_count is not None else '',
                    'actual_events_count': es_count if es_count is not None else '',
                    'eval_result': pass_val,
                    'error': result.get('error', '')
                })
                rows_written += 1
        return {
            'csv_filename': csv_filename,
            'csv_path': csv_path,
            'rows_written': rows_written
        }

    def _generate_latency_csv(
        self,
        job_id: str,
        evaluation_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generate latency CSV report (mirrors latency.sh output).

        Per-row columns:
            #, sensor_id, Upstream (CV+Analytics), AB Consumer Lag,
            VST Fetch, VLM Inference, Idx-End(E2E)

        A summary section is appended at the bottom with aggregate stats.
        """
        self._ensure_reports_dir()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_filename = f'report_latency_{job_id}_{timestamp}.csv'
        csv_path = os.path.join(self._settings.reports_dir, csv_filename)

        fieldnames = [
            '#',
            'sensor_id',
            'Upstream (CV+Analytics)',
            'AB Consumer Lag',
            'VST Fetch',
            'VLM Inference',
            'Idx-End(E2E)',
        ]

        rows_written = 0
        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for sensor_result in evaluation_results:
                for row in sensor_result.get('rows', []):
                    writer.writerow({
                        '#': row.get('#', ''),
                        'sensor_id': row.get('sensor_id', ''),
                        'Upstream (CV+Analytics)': row.get('upstream_fmt', 'N/A'),
                        'AB Consumer Lag': row.get('kafka_lag_fmt', 'N/A'),
                        'VST Fetch': row.get('vst_fmt', 'N/A'),
                        'VLM Inference': row.get('vlm_fmt', 'N/A'),
                        'Idx-End(E2E)': row.get('e2e_fmt', 'N/A'),
                    })
                    rows_written += 1

            writer.writerow({})

            stats_all = self._aggregate_latency_stats(evaluation_results)
            for metric_name, metric_key in [
                ('Upstream (CV+Analytics)', 'upstream'),
                ('AB Consumer Lag', 'kafka_lag'),
                ('VST Fetch', 'vst'),
                ('VLM Inference', 'vlm'),
                ('Idx-End(E2E)', 'e2e'),
            ]:
                s = stats_all.get(metric_key, {})
                if not s or s.get('n', 0) == 0:
                    continue
                writer.writerow({
                    '#': 'SUMMARY',
                    'sensor_id': metric_name,
                    'Upstream (CV+Analytics)': f"avg={self._fmt_stat(s.get('avg'))}",
                    'AB Consumer Lag': f"p50={self._fmt_stat(s.get('p50'))}",
                    'VST Fetch': f"p90={self._fmt_stat(s.get('p90'))}",
                    'VLM Inference': f"p99={self._fmt_stat(s.get('p99'))}",
                    'Idx-End(E2E)': f"max={self._fmt_stat(s.get('max'))}",
                })

        return {
            'csv_filename': csv_filename,
            'csv_path': csv_path,
            'rows_written': rows_written,
        }

    def _generate_prometheus_latency_csv(
        self,
        job_id: str,
        evaluation_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Generate CSV for Prometheus-backed latency stats (one row per sensor window)."""
        self._ensure_reports_dir()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_filename = f'report_prom_latency_{job_id}_{timestamp}.csv'
        csv_path = os.path.join(self._settings.reports_dir, csv_filename)

        stages = [
            "upstream",
            "kafka_lag",
            "worker_queue_wait",
            "vst",
            "video_length",
            "vlm",
            "worker_processing",
            "e2e",
        ]
        reliability_fields = [
            "total_events",
            "confirmed_events",
            "rejected_events",
            "verification_failed_events",
            "unknown_events",
            "events_after_dedup",
            "dropped_events",
            "skipped_confirmed_events",
            "verification_failures",
        ]

        fieldnames = ["sensor_id", "start_ts", "end_ts", "status", "error"]
        for stage in stages:
            fieldnames.extend([
                f"{stage}_avg",
                f"{stage}_p50",
                f"{stage}_p90",
                f"{stage}_p99",
                f"{stage}_count",
            ])
        fieldnames.extend(reliability_fields)

        def _fmt_metric(value: Any) -> str:
            if value is None:
                return ""
            try:
                return f"{float(value):.4f}"
            except (TypeError, ValueError):
                return ""

        rows_written = 0
        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for result in evaluation_results:
                stats = result.get("stats") or {}
                reliability = result.get("reliability") or {}
                row: Dict[str, Any] = {
                    "sensor_id": result.get("sensor_id") or result.get("video", ""),
                    "start_ts": result.get("start_ts", ""),
                    "end_ts": result.get("end_ts", ""),
                    "status": result.get("status", ""),
                    "error": result.get("error", ""),
                }
                for stage in stages:
                    stage_stats = stats.get(stage) or {}
                    row[f"{stage}_avg"] = _fmt_metric(stage_stats.get("avg"))
                    row[f"{stage}_p50"] = _fmt_metric(stage_stats.get("p50"))
                    row[f"{stage}_p90"] = _fmt_metric(stage_stats.get("p90"))
                    row[f"{stage}_p99"] = _fmt_metric(stage_stats.get("p99"))
                    row[f"{stage}_count"] = int(float(stage_stats.get("count") or 0))
                for field in reliability_fields:
                    row[field] = int(float(reliability.get(field) or 0))
                writer.writerow(row)
                rows_written += 1

        return {
            "csv_filename": csv_filename,
            "csv_path": csv_path,
            "rows_written": rows_written,
        }

    @staticmethod
    def _fmt_stat(v) -> str:
        if v is None:
            return "N/A"
        return f"{v:.2f}s"

    @staticmethod
    def _aggregate_latency_stats(
        results: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Merge per-sensor stats rows into single aggregate lists, then compute stats."""
        from .latency import _compute_metric_stats
        buckets: Dict[str, List[float]] = {
            'upstream': [], 'kafka_lag': [], 'vst': [], 'vlm': [], 'e2e': []
        }
        for r in results:
            for row in r.get('rows', []):
                for key in buckets:
                    val = row.get(key)
                    if val is not None:
                        buckets[key].append(val)
        return {k: _compute_metric_stats(v) for k, v in buckets.items()}

    @staticmethod
    def calculate_summary(evaluation_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate summary statistics from evaluation results.

        Args:
            evaluation_results: List of evaluation result dictionaries

        Returns:
            Summary statistics dictionary with:
            - total_evaluations: int
            - exact_matches: int
            - false_matches: int
            - no_ground_truth: int
            - errors: int
            - accuracy: str (formatted percentage)
            - match_rate: float (0-1)
        """
        total = len(evaluation_results)
        exact_matches = sum(1 for r in evaluation_results if r.get('status') == 'exact')
        false_matches = sum(1 for r in evaluation_results if r.get('status') == 'false')
        no_gt = sum(1 for r in evaluation_results if r.get('status') == 'no_ground_truth')
        errors = sum(1 for r in evaluation_results if r.get('status') == 'error')

        return {
            'total_evaluations': total,
            'exact_matches': exact_matches,
            'false_matches': false_matches,
            'no_ground_truth': no_gt,
            'errors': errors,
            'accuracy': f"{(exact_matches / total * 100):.2f}%" if total > 0 else "0%",
            'match_rate': exact_matches / total if total > 0 else 0
        }

    def list_files(self, format: str = 'all') -> List[str]:
        """
        List all report files in the reports directory.

        Args:
            format: File format to list ('json', 'csv', or 'all')

        Returns:
            List of report filenames, sorted by date (most recent first)
        """
        if not os.path.exists(self._settings.reports_dir):
            return []

        report_files = []

        for f in os.listdir(self._settings.reports_dir):
            if not f.startswith('report_'):
                continue

            if format == 'json' and f.endswith('.json'):
                report_files.append(f)
            elif format == 'csv' and f.endswith('.csv'):
                report_files.append(f)
            elif format == 'all' and (f.endswith('.json') or f.endswith('.csv')):
                report_files.append(f)

        # Sort by filename (which includes timestamp) - most recent first
        report_files.sort(reverse=True)

        return report_files

    def get_content(self, filename: str) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Read and return report content.

        Args:
            filename: Report filename (JSON or CSV)

        Returns:
            Report content as dictionary (JSON) or list of dicts (CSV)

        Raises:
            FileNotFoundError: If report file doesn't exist
            ValueError: If filename is invalid
        """
        if not (filename.endswith('.json') or filename.endswith('.csv')):
            raise ValueError('Invalid report filename - must be .json or .csv file')

        report_path = os.path.join(self._settings.reports_dir, filename)

        if not os.path.exists(report_path):
            raise FileNotFoundError(f'Report not found: {filename}')

        if filename.endswith('.json'):
            with open(report_path, 'r') as f:
                return json.load(f)
        else:  # CSV
            with open(report_path, 'r', newline='', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                return list(reader)

    def delete(self, filename: str) -> bool:
        """
        Delete a report file.

        Args:
            filename: Report filename to delete (JSON or CSV)

        Returns:
            True if deleted successfully, False if file not found
        """
        if not (filename.endswith('.json') or filename.endswith('.csv')):
            return False

        report_path = os.path.join(self._settings.reports_dir, filename)

        if not os.path.exists(report_path):
            return False

        os.remove(report_path)
        return True

    def combine_reports(
        self,
        job_ids: List[str],
        job_service: Any,
        output_name: str = None
    ) -> Dict[str, Any]:
        """
        Combine multiple CSV reports into a single unified report.

        Saves combined report to: {reports_dir}/combines/

        Args:
            job_ids: List of job IDs whose reports should be combined
            job_service: JobService instance for validating jobs
            output_name: Optional custom name for the combined report

        Returns:
            Dictionary with combined report metadata

        Raises:
            ValueError: If any job_id is invalid, not completed, or missing report file
        """
        # Step 1: Validate all jobs and collect report files
        invalid_jobs = []
        report_files = []

        for job_id in job_ids:
            job = job_service.get(job_id)

            if not job:
                invalid_jobs.append({
                    'job_id': job_id,
                    'reason': 'Job not found'
                })
                continue

            if job['status'] != 'completed':
                invalid_jobs.append({
                    'job_id': job_id,
                    'status': job['status'],
                    'reason': 'Job not completed'
                })
                continue

            report_file = job.get('report_file')
            if not report_file:
                invalid_jobs.append({
                    'job_id': job_id,
                    'reason': 'No report file found'
                })
                continue

            # Verify file exists
            report_path = os.path.join(self._settings.reports_dir, report_file)
            if not os.path.exists(report_path):
                invalid_jobs.append({
                    'job_id': job_id,
                    'reason': f'Report file not found: {report_file}'
                })
                continue

            report_files.append(report_file)

        # If any invalid jobs, raise error with details
        if invalid_jobs:
            raise ValueError(f"Some jobs cannot be combined: {json.dumps(invalid_jobs)}")

        # Step 2: Ensure reports directory and combines subdirectory exist
        self._ensure_reports_dir()
        combines_dir = os.path.join(self._settings.reports_dir, 'combines')
        os.makedirs(combines_dir, exist_ok=True)

        # Step 3: Generate output filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if output_name:
            # Sanitize custom name (remove special characters)
            safe_name = re.sub(r'[^\w\-]', '_', output_name)
            filename = f"report_combined_{safe_name}_{timestamp}.csv"
        else:
            filename = f"report_combined_{timestamp}.csv"

        # Save combined report to combines subdirectory
        output_path = os.path.join(combines_dir, filename)

        # Step 4: Combine CSV files
        combined_rows = []
        header = None
        total_data_rows = 0

        for i, report_file in enumerate(report_files):
            report_path = os.path.join(self._settings.reports_dir, report_file)

            with open(report_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)

                if i == 0:
                    # First file: save header
                    header = next(reader)
                    combined_rows.append(header)
                else:
                    # Skip header in subsequent files
                    next(reader)

                # Append all data rows
                for row in reader:
                    combined_rows.append(row)
                    total_data_rows += 1

        # Step 5: Write combined CSV
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(combined_rows)

        # Step 6: Return metadata
        return {
            'filename': filename,
            'path': f"combines/{filename}",
            'full_path': output_path,
            'combined_jobs': len(job_ids),
            'total_rows': total_data_rows,
            'job_ids': job_ids,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    SUMMARY_CSV_FILENAME = "report_latency_summary.csv"
    SUMMARY_FIELDNAMES = [
        "Timestamp",
        "Platform",
        "Max Stream Concurrency",
        "Configured Streams",
        "Model",
        "Alert verification rate",
        "Confirmed alert rate",
        "Alert rate (Kafka-based)",
        "Kafka VLM Messages",
        "Kafka msg/sec (avg)",
        "Kafka msg/sec (peak)",
        "E2E Latency - Avg (sec)",
        "VLM-NIM Latency Avg (sec)",
        "VST Latency - Avg (sec)",
        "CV+BA Latency",
        "AB Consumer Lag - Avg (sec)",
        "Worker Queue Wait - Avg (sec)",
        "Worker Processing - Avg (sec)",
        "Video Length - Avg (sec)",
        "Confirmed Events",
        "Rejected Events",
        "Verification-failed Events",
        "Unknown Verdict Events",
        "Events After Dedup",
        "Dropped Events",
        "Skipped Confirmed Events",
        "Verification Failures",
        "Async External IO Latency Avg (sec)",
        "Async External IO Latency p90 (sec)",
        "Async External IO Ops",
        "Async External IO Fallbacks",
        "Async Sink In-Flight Avg",
        "Async Sink In-Flight Max",
        "Realtime Rules Active Avg",
        "Realtime Rules Active Max",
        "Realtime Rules Created",
        "Realtime Rules Deleted",
        "Realtime Rules Failed",
        "RTVI Call Latency Avg (sec)",
        "RTVI Call Latency p90 (sec)",
        "RTVI Calls",
        "RTVI Call Failures",
        "Incident Query Latency Avg (sec)",
        "Incident Query Latency p90 (sec)",
        "Incident Queries",
        "Incident Query Failures",
        "Num Events",
        "Job ID",
    ]

    def append_summary_line(
        self,
        job_id: str,
        evaluation_results: List[Dict[str, Any]],
        latency_summary: Dict[str, Any],
        duration_str: str = "1h",
        prometheus_aggregate_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Append a single summary row to the persistent report_summary.csv.

        Each latency evaluation run produces one row containing platform
        metadata, aggregate latency averages, and Kafka-based throughput
        metrics (when KAFKA_BOOTSTRAP_SERVERS is configured).

        Args:
            job_id: The evaluation job identifier
            evaluation_results: Raw per-sensor result dicts (contain 'rows')
            latency_summary: Aggregated stats from LatencyService.calculate_latency_summary
            duration_str: ES duration notation used for the run (e.g. '1h')

        Returns:
            Dict with summary_file path and the row data written
        """
        self._ensure_reports_dir()
        csv_path = os.path.join(self._settings.reports_dir, self.SUMMARY_CSV_FILENAME)

        settings_data_source = str(getattr(self._settings, "data_source", "") or "").strip().lower()
        has_aggregate_payload = isinstance(prometheus_aggregate_summary, dict)
        is_prometheus = (
            settings_data_source == "prometheus"
            or any((r.get("data_source") == "prometheus") for r in evaluation_results)
            or has_aggregate_payload
        )
        if is_prometheus:
            return self.generate_prometheus_latency_report_suite(
                job_id=job_id,
                evaluation_results=evaluation_results,
                latency_summary=latency_summary,
                prometheus_aggregate_summary=prometheus_aggregate_summary,
                duration_str=duration_str,
            )

        unique_sensors: set = set()
        total_events = 0
        reliability_totals = {
            "total_events": 0,
            "total_verified_events": 0,
            "confirmed_events": 0,
            "rejected_events": 0,
            "verification_failed_events": 0,
            "unknown_events": 0,
            "events_after_dedup": 0,
            "dropped_events": 0,
            "skipped_confirmed_events": 0,
            "verification_failures": 0,
        }
        aggregate_stats: Dict[str, Any] = {}
        aggregate_reliability: Dict[str, Any] = {}
        aggregate_operational: Dict[str, Any] = {}
        aggregate_ready = (
            is_prometheus
            and has_aggregate_payload
            and prometheus_aggregate_summary.get("status") in {"success", "no_data"}
        )
        if is_prometheus:
            for result in evaluation_results:
                sid = (
                    result.get("sensor_id")
                    or result.get("video")
                    or result.get("video_id")
                    or ""
                )
                if sid:
                    unique_sensors.add(sid)
            if aggregate_ready:
                aggregate_stats = prometheus_aggregate_summary.get("stats") or {}
                aggregate_reliability = prometheus_aggregate_summary.get("reliability") or {}
                aggregate_operational = prometheus_aggregate_summary.get("operational") or {}
                for key in reliability_totals:
                    reliability_totals[key] = int(
                        float(aggregate_reliability.get(key) or 0)
                    )
                if reliability_totals["total_verified_events"] <= 0:
                    reliability_totals["total_verified_events"] = (
                        reliability_totals["confirmed_events"]
                        + reliability_totals["rejected_events"]
                        + reliability_totals["verification_failed_events"]
                        + reliability_totals["unknown_events"]
                    )
                total_events = reliability_totals["total_verified_events"]
            else:
                logger.warning(
                    "Prometheus aggregate summary unavailable for job_id=%s; "
                    "summary rate/count/latency fields will default to N/A/0",
                    job_id,
                )
        else:
            for result in evaluation_results:
                for row in result.get("rows", []):
                    sid = row.get("sensor_id", "")
                    if sid:
                        unique_sensors.add(sid)
                total_events += result.get("usable_hits", 0)

        es_stream_count = len(unique_sensors)

        configured_streams = self._settings.max_stream_concurrency
        max_concurrency = configured_streams if configured_streams > 0 else es_stream_count

        duration_minutes = self._parse_duration_to_minutes(duration_str)
        if max_concurrency > 0 and duration_minutes > 0:
            alert_rate = round(total_events / duration_minutes / max_concurrency, 2)
            alert_rate_str = f"{alert_rate} alert/min/stream"
        else:
            alert_rate_str = "N/A"
        if is_prometheus and max_concurrency > 0 and duration_minutes > 0:
            confirmed_rate = round(
                reliability_totals["confirmed_events"] / duration_minutes / max_concurrency,
                2,
            )
            confirmed_rate_str = f"{confirmed_rate} alert/min/stream"
        else:
            confirmed_rate_str = "N/A"

        kafka_stats = self._count_kafka_vlm_messages(duration_str)
        if kafka_stats["available"] and kafka_stats["total"] > 0 and max_concurrency > 0 and duration_minutes > 0:
            kafka_rate = round(kafka_stats["total"] / duration_minutes / max_concurrency, 2)
            kafka_rate_str = f"{kafka_rate} alert/min/stream"
        else:
            kafka_rate_str = "N/A"

        def _avg(key: str) -> str:
            if is_prometheus:
                if not aggregate_ready:
                    return "N/A"
                val = (aggregate_stats.get(key) or {}).get("avg")
            else:
                val = latency_summary.get(key, {}).get("avg")
            if val is None:
                return "N/A"
            return f"{val:.2f}"

        def _op_hist(stat_key: str, metric_key: str = "avg") -> str:
            if not is_prometheus:
                return "N/A"
            if not aggregate_ready:
                return "N/A"
            value = (aggregate_operational.get(stat_key) or {}).get(metric_key)
            if value is None:
                return "N/A"
            try:
                return f"{float(value):.2f}"
            except (TypeError, ValueError):
                return "N/A"

        def _op_gauge(key: str) -> str:
            if not is_prometheus:
                return "N/A"
            if not aggregate_ready:
                return "N/A"
            value = aggregate_operational.get(key)
            if value is None:
                return "N/A"
            try:
                return f"{float(value):.2f}"
            except (TypeError, ValueError):
                return "N/A"

        def _op_count(key: str) -> Any:
            if not is_prometheus:
                return "N/A"
            if not aggregate_ready:
                return 0
            try:
                return int(float(aggregate_operational.get(key) or 0))
            except (TypeError, ValueError):
                return 0

        row_data = {
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Platform": self._settings.platform or "N/A",
            "Max Stream Concurrency": es_stream_count,
            "Configured Streams": configured_streams if configured_streams > 0 else "N/A",
            "Model": self._settings.model or "N/A",
            "Alert verification rate": alert_rate_str,
            "Confirmed alert rate": confirmed_rate_str,
            "Alert rate (Kafka-based)": kafka_rate_str,
            "Kafka VLM Messages": kafka_stats["total"] if kafka_stats["available"] else "N/A",
            "Kafka msg/sec (avg)": kafka_stats["per_second_avg"] if kafka_stats["available"] else "N/A",
            "Kafka msg/sec (peak)": kafka_stats["per_second_peak"] if kafka_stats["available"] else "N/A",
            "E2E Latency - Avg (sec)": _avg("e2e"),
            "VLM-NIM Latency Avg (sec)": _avg("vlm"),
            "VST Latency - Avg (sec)": _avg("vst"),
            "CV+BA Latency": _avg("upstream"),
            "AB Consumer Lag - Avg (sec)": _avg("kafka_lag"),
            "Worker Queue Wait - Avg (sec)": _avg("worker_queue_wait"),
            "Worker Processing - Avg (sec)": _avg("worker_processing"),
            "Video Length - Avg (sec)": _avg("video_length"),
            "Confirmed Events": reliability_totals["confirmed_events"] if is_prometheus else "N/A",
            "Rejected Events": reliability_totals["rejected_events"] if is_prometheus else "N/A",
            "Verification-failed Events": reliability_totals["verification_failed_events"] if is_prometheus else "N/A",
            "Unknown Verdict Events": reliability_totals["unknown_events"] if is_prometheus else "N/A",
            "Events After Dedup": reliability_totals["events_after_dedup"] if is_prometheus else "N/A",
            "Dropped Events": reliability_totals["dropped_events"] if is_prometheus else "N/A",
            "Skipped Confirmed Events": reliability_totals["skipped_confirmed_events"] if is_prometheus else "N/A",
            "Verification Failures": reliability_totals["verification_failures"] if is_prometheus else "N/A",
            "Async External IO Latency Avg (sec)": _op_hist("async_external_io", "avg"),
            "Async External IO Latency p90 (sec)": _op_hist("async_external_io", "p90"),
            "Async External IO Ops": _op_count("async_external_io_count"),
            "Async External IO Fallbacks": _op_count("async_external_io_fallback_total"),
            "Async Sink In-Flight Avg": _op_gauge("async_sink_in_flight_avg"),
            "Async Sink In-Flight Max": _op_gauge("async_sink_in_flight_max"),
            "Realtime Rules Active Avg": _op_gauge("realtime_rules_active_avg"),
            "Realtime Rules Active Max": _op_gauge("realtime_rules_active_max"),
            "Realtime Rules Created": _op_count("realtime_rules_created_total"),
            "Realtime Rules Deleted": _op_count("realtime_rules_deleted_total"),
            "Realtime Rules Failed": _op_count("realtime_rules_failed_total"),
            "RTVI Call Latency Avg (sec)": _op_hist("rtvi_call", "avg"),
            "RTVI Call Latency p90 (sec)": _op_hist("rtvi_call", "p90"),
            "RTVI Calls": _op_count("rtvi_call_count"),
            "RTVI Call Failures": _op_count("rtvi_call_failures_total"),
            "Incident Query Latency Avg (sec)": _op_hist("incident_query", "avg"),
            "Incident Query Latency p90 (sec)": _op_hist("incident_query", "p90"),
            "Incident Queries": _op_count("incident_query_count"),
            "Incident Query Failures": _op_count("incident_query_failures_total"),
            "Num Events": total_events,
            "Job ID": job_id,
        }

        write_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.SUMMARY_FIELDNAMES)
            if write_header:
                writer.writeheader()
            writer.writerow(row_data)

        return {
            "summary_file": self.SUMMARY_CSV_FILENAME,
            "summary_path": csv_path,
            "row": row_data,
        }

    def _count_kafka_vlm_messages(self, duration_str: str) -> Dict[str, Any]:
        """
        Count messages on the Kafka VLM output topic within the evaluation
        time window.  Returns total count and per-second throughput stats.

        Disabled gracefully when KAFKA_BOOTSTRAP_SERVERS is not set or
        confluent-kafka is not installed.
        """
        empty = {"total": 0, "per_second_avg": 0, "per_second_peak": 0, "available": False}

        if not self._settings.kafka_bootstrap_servers:
            return empty

        if not _KAFKA_AVAILABLE:
            logger.warning("confluent-kafka not installed; Kafka message counting disabled")
            return empty

        duration_seconds = self._parse_duration_to_minutes(duration_str) * 60
        if duration_seconds <= 0:
            return empty

        cutoff_ms = int((time.time() - duration_seconds) * 1000)
        topic = self._settings.kafka_vlm_topic
        group_id = f"evaluator-counter-{int(time.time())}"

        try:
            consumer = Consumer({
                "bootstrap.servers": self._settings.kafka_bootstrap_servers,
                "group.id": group_id,
                "auto.offset.reset": "latest",
                "enable.auto.commit": False,
            })

            cluster_meta = consumer.list_topics(topic, timeout=10)
            topic_meta = cluster_meta.topics.get(topic)
            if not topic_meta or topic_meta.error is not None:
                logger.warning("Kafka topic %s not found", topic)
                consumer.close()
                return empty

            partition_ids = list(topic_meta.partitions.keys())
            tps_for_time = [TopicPartition(topic, pid, cutoff_ms) for pid in partition_ids]
            start_offsets = consumer.offsets_for_times(tps_for_time, timeout=10)

            # Offset-based total: O(1) memory, no message consumption
            total = 0
            valid_tps = []
            for tp in start_offsets:
                if tp.offset < 0:
                    continue
                _, hi = consumer.get_watermark_offsets(
                    TopicPartition(topic, tp.partition), timeout=10
                )
                total += max(0, hi - tp.offset)
                valid_tps.append(tp)

            # Capped sampling for per-second peak throughput
            _MAX_MSGS = 100_000
            _DEADLINE = time.time() + 30
            now_ms = int(time.time() * 1000)
            timestamps_ms: list = []
            for tp in valid_tps:
                if len(timestamps_ms) >= _MAX_MSGS or time.time() >= _DEADLINE:
                    break
                consumer.assign([tp])
                while len(timestamps_ms) < _MAX_MSGS and time.time() < _DEADLINE:
                    msg = consumer.poll(timeout=1.0)
                    if msg is None or msg.error():
                        break
                    _, ts_ms = msg.timestamp()
                    if ts_ms > now_ms:
                        break
                    if ts_ms >= cutoff_ms:
                        timestamps_ms.append(ts_ms)

            consumer.close()
        except Exception as exc:
            logger.warning("Kafka message counting failed: %s", exc)
            return empty

        if total == 0:
            return {"total": 0, "per_second_avg": 0, "per_second_peak": 0, "available": True}

        per_second_avg = round(total / duration_seconds, 2)
        if timestamps_ms:
            second_buckets = Counter(int(ts / 1000) for ts in timestamps_ms)
            per_second_peak = max(second_buckets.values())
        else:
            per_second_peak = 0

        return {
            "total": total,
            "per_second_avg": per_second_avg,
            "per_second_peak": per_second_peak,
            "available": True,
        }

    @staticmethod
    def _parse_duration_to_minutes(duration_str: str) -> float:
        """Convert ES duration notation (e.g. '30m', '1h', '2d') to minutes."""
        duration_str = duration_str.strip().lower()
        try:
            if duration_str.endswith("d"):
                return float(duration_str[:-1]) * 1440
            elif duration_str.endswith("h"):
                return float(duration_str[:-1]) * 60
            elif duration_str.endswith("m"):
                return float(duration_str[:-1])
            elif duration_str.endswith("s"):
                return float(duration_str[:-1]) / 60
            return float(duration_str)
        except (ValueError, IndexError):
            return 0.0
