"""Latency-only orchestration extracted from the source evaluation router."""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
logger = logging.getLogger(__name__)
class LatencyWorkflow:
    def __init__(self, settings, evaluation, reports):
        self._settings = settings
        self._eval_service = evaluation
        self._report_service = reports

    @staticmethod
    def _utc_now() -> datetime:
        """Return the current UTC time; isolated to keep drain tests deterministic."""
        return datetime.now(timezone.utc)


    def _wait_for_prometheus_summary_window(
        self,
        latency_window_end_ts: Optional[str],
    ) -> float:
        """Wait until the configured post-window Prometheus drain has elapsed."""
        if (
            getattr(self._settings, "data_source", "") != "prometheus"
            or getattr(self._settings, "eval_mode", "") != "latency"
            or not latency_window_end_ts
        ):
            return 0.0

        try:
            buffer_seconds = max(
                int(getattr(self._settings, "prometheus_summary_buffer_seconds", 0)),
                0,
            )
        except (TypeError, ValueError):
            logger.warning(
                "Ignoring invalid Prometheus summary buffer: %r",
                getattr(self._settings, "prometheus_summary_buffer_seconds", None),
            )
            return 0.0
        if buffer_seconds == 0:
            return 0.0

        try:
            normalized_end = str(latency_window_end_ts).strip()
            if normalized_end.endswith("Z"):
                normalized_end = normalized_end[:-1] + "+00:00"
            window_end = datetime.fromisoformat(normalized_end)
            if window_end.tzinfo is None:
                window_end = window_end.replace(tzinfo=timezone.utc)
            else:
                window_end = window_end.astimezone(timezone.utc)
        except (TypeError, ValueError):
            logger.warning(
                "Could not apply Prometheus summary drain to invalid window end %r",
                latency_window_end_ts,
            )
            return 0.0

        deadline = window_end + timedelta(seconds=buffer_seconds)
        remaining_seconds = max(
            0.0,
            (deadline - self._utc_now()).total_seconds(),
        )
        if remaining_seconds <= 0:
            return 0.0

        logger.info(
            "Waiting %.3fs for post-injection Prometheus drain "
            "(window_end=%s, buffer=%ss, deadline=%s)",
            remaining_seconds,
            latency_window_end_ts,
            buffer_seconds,
            deadline.isoformat().replace("+00:00", "Z"),
        )
        time.sleep(remaining_seconds)
        logger.info("Post-injection Prometheus drain complete")
        return remaining_seconds


    def _with_prometheus_sensor_query_window(
        self,
        incidents_params: list,
        latency_window_start_ts: Optional[str],
        latency_window_end_ts: Optional[str],
    ) -> list:
        """Align per-sensor Prometheus queries with the aggregate report window.

        The batch runs after the post-injection drain. Without explicit bounds,
        per-sensor evaluation uses ``latency_duration`` relative to that later
        time and drops the beginning of the injection window. The aggregate
        query already spans from the injection start through end + buffer, so
        apply those same absolute bounds to incidents that do not provide an
        explicit per-incident window.
        """
        if (
            getattr(self._settings, "data_source", "") != "prometheus"
            or getattr(self._settings, "eval_mode", "") != "latency"
            or not latency_window_start_ts
            or not latency_window_end_ts
        ):
            return incidents_params

        try:
            normalized_end = str(latency_window_end_ts).strip()
            if normalized_end.endswith("Z"):
                normalized_end = normalized_end[:-1] + "+00:00"
            window_end = datetime.fromisoformat(normalized_end)
            if window_end.tzinfo is None:
                window_end = window_end.replace(tzinfo=timezone.utc)
            else:
                window_end = window_end.astimezone(timezone.utc)
            buffer_seconds = max(
                int(getattr(self._settings, "prometheus_summary_buffer_seconds", 0)),
                0,
            )
        except (TypeError, ValueError):
            logger.warning(
                "Could not align per-sensor Prometheus queries to window %r - %r",
                latency_window_start_ts,
                latency_window_end_ts,
            )
            return incidents_params

        buffered_end = (
            window_end + timedelta(seconds=buffer_seconds)
        ).isoformat().replace("+00:00", "Z")
        aligned_incidents = []
        for incident in incidents_params:
            aligned = dict(incident)
            if (
                not aligned.get("start_timestamp")
                and not aligned.get("end_timestamp")
            ):
                aligned["start_timestamp"] = latency_window_start_ts
                aligned["end_timestamp"] = buffered_end
            aligned_incidents.append(aligned)
        return aligned_incidents


    def _generate_v2_report_artifacts(
        self,
        job_id: str,
        results_list: list,
        summary: dict,
        prometheus_aggregate_summary: dict = None,
    ) -> dict:
        """Generate report artifacts for v2 evals and return primary report metadata."""
        if (
            self._settings.eval_mode == "latency"
            and self._settings.data_source == "prometheus"
        ):
            return self._report_service.generate_prometheus_latency_report_suite(
                job_id=job_id,
                evaluation_results=results_list,
                latency_summary=summary,
                prometheus_aggregate_summary=prometheus_aggregate_summary,
                duration_str=self._settings.latency_duration,
            )

        report_info = self._report_service.generate_csv_v2(job_id, results_list)
        self._append_latency_summary(job_id, results_list, summary)
        return report_info


    def _resolve_latency_reporting_artifacts(
        self,
        job_id: str,
        incidents_params: list,
        primary_results: list,
        latency_window_start_ts: str = None,
        latency_window_end_ts: str = None,
        synthetic_injection_summary: dict = None,
    ):
        """Resolve latency data used for summary and optional secondary report."""
        latency_results = primary_results
        latency_summary = None
        latency_report_file = None
        prometheus_aggregate_summary = None

        if self._settings.eval_mode != "latency":
            return latency_results, latency_summary, latency_report_file, prometheus_aggregate_summary

        if self._settings.data_source == "prometheus":
            try:
                metric_sensor_ids = None
                if synthetic_injection_summary is not None:
                    metric_sensor_ids = [
                        str(
                            result.get("metric_sensor_id")
                            or result.get("submitted_sensor_id")
                            or ""
                        ).strip()
                        for result in (latency_results or [])
                        if str(
                            result.get("metric_sensor_id")
                            or result.get("submitted_sensor_id")
                            or ""
                        ).strip()
                    ]
                prometheus_aggregate_summary = self._eval_service.collect_prometheus_aggregate_summary(
                    incidents_params,
                    window_start_ts=latency_window_start_ts,
                    window_end_ts=latency_window_end_ts,
                    sensor_ids=metric_sensor_ids,
                )
            except Exception as e:
                logger.warning(f"Failed to collect aggregate Prometheus summary metrics: {e}")
                prometheus_aggregate_summary = {"status": "error", "error": str(e)}
            try:
                summary_kwargs = {}
                if synthetic_injection_summary is not None:
                    summary_kwargs["synthetic_injection_summary"] = synthetic_injection_summary
                latency_summary = self._eval_service.build_prometheus_latency_summary(
                    latency_results or [],
                    prometheus_aggregate_summary,
                    **summary_kwargs,
                )
            except Exception as e:
                logger.warning(f"Failed to build aggregate Prometheus latency summary: {e}")
                latency_summary = {
                    "total_sensors": len(latency_results or []),
                    "total_hits": 0,
                    "total_usable": 0,
                    "summary_source": "prometheus_aggregate",
                    "summary_status": "error",
                    "per_sensor_summary_status": "error",
                    "summary_error": str(e),
                }
        elif latency_results:
            try:
                latency_summary = self._eval_service.calculate_summary_v2(latency_results)
            except Exception as e:
                logger.warning(f"Failed to compute latency summary from latency results: {e}")

        return (
            latency_results,
            latency_summary,
            latency_report_file,
            prometheus_aggregate_summary,
        )
