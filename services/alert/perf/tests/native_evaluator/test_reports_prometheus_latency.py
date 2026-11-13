import csv
import os
import tempfile
import unittest
from types import SimpleNamespace

from alert_bridge_evaluator.services.reports import ReportService


class TestReportsPrometheusLatency(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = SimpleNamespace(
            reports_dir=self.temp_dir.name,
            max_stream_concurrency=0,
            platform="test-platform",
            model="test-model",
            kafka_bootstrap_servers="",
            kafka_vlm_topic="mdx-vlm-incidents",
            eval_mode="latency",
            data_source="prometheus",
            prometheus_metric_profile="realtime",
            eval_workflow="verification",
            prometheus_url="http://prometheus:9091",
            prometheus_summary_buffer_seconds=90,
        )
        self.report_service = ReportService(self.settings)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _sample_results(self):
        return [
            {
                "eval_mode": "latency",
                "data_source": "prometheus",
                "sensor_id": "cam-1",
                "start_ts": "2026-05-01T00:00:00Z",
                "end_ts": "2026-05-01T00:05:00Z",
                "status": "success",
                "stats": {
                    "upstream": {"avg": 1.1, "p50": 1.0, "p90": 1.3, "p99": 1.4, "count": 4},
                    "kafka_lag": {"avg": 0.1, "p50": 0.1, "p90": 0.2, "p99": 0.2, "count": 4},
                    "worker_queue_wait": {"avg": 0.2, "p50": 0.2, "p90": 0.3, "p99": 0.4, "count": 4},
                    "vst": {"avg": 0.7, "p50": 0.6, "p90": 0.9, "p99": 1.0, "count": 4},
                    "video_length": {"avg": 10.0, "p50": 10.0, "p90": 11.0, "p99": 12.0, "count": 4},
                    "vlm": {"avg": 2.2, "p50": 2.1, "p90": 2.5, "p99": 2.9, "count": 4},
                    "worker_processing": {"avg": 3.3, "p50": 3.2, "p90": 3.5, "p99": 3.9, "count": 4},
                    "e2e": {"avg": 4.4, "p50": 4.3, "p90": 4.8, "p99": 5.2, "count": 4},
                },
                "reliability": {
                    "total_events": 4,
                    "confirmed_events": 3,
                    "rejected_events": 1,
                    "verification_failed_events": 0,
                    "unknown_events": 0,
                    "events_after_dedup": 4,
                    "dropped_events": 0,
                    "skipped_confirmed_events": 0,
                    "verification_failures": 0,
                },
            },
            {
                "eval_mode": "latency",
                "data_source": "prometheus",
                "sensor_id": "cam-2",
                "start_ts": "2026-05-01T00:00:00Z",
                "end_ts": "2026-05-01T00:05:00Z",
                "status": "no_data",
                "stats": {"e2e": {"avg": None, "p90": None, "p99": None, "count": 0}},
                "reliability": {
                    "total_events": 0,
                    "confirmed_events": 0,
                    "rejected_events": 0,
                    "dropped_events": 3,
                    "skipped_confirmed_events": 2,
                    "verification_failures": 0,
                },
            },
        ]

    def _sample_aggregate_summary(self):
        return {
            "status": "success",
            "start_ts": "2026-05-01T00:00:00Z",
            "end_ts": "2026-05-01T00:05:00Z",
            "window_seconds": 390,
            "buffer_seconds": 90,
            "stats": {
                "upstream": {"avg": 1.2, "p50": 1.1, "p90": 1.5, "p99": 1.8, "count": 5},
                "kafka_lag": {"avg": 0.2, "p50": 0.2, "p90": 0.3, "p99": 0.4, "count": 5},
                "worker_queue_wait": {"avg": 0.3, "p50": 0.2, "p90": 0.4, "p99": 0.5, "count": 5},
                "vst": {"avg": 0.4, "p50": 0.4, "p90": 0.6, "p99": 0.7, "count": 5},
                "video_length": {"avg": 8.5, "p50": 8.0, "p90": 9.0, "p99": 9.5, "count": 5},
                "vlm": {"avg": 0.8, "p50": 0.8, "p90": 1.0, "p99": 1.2, "count": 5},
                "worker_processing": {"avg": 1.0, "p50": 0.9, "p90": 1.2, "p99": 1.4, "count": 5},
                "e2e": {"avg": 2.3, "p50": 2.2, "p90": 2.7, "p99": 3.0, "count": 5},
            },
            "reliability": {
                "confirmed_events": 3,
                "rejected_events": 2,
                "verification_failed_events": 0,
                "unknown_events": 0,
                "events_after_dedup": 12,
                "dropped_events": 4,
                "skipped_confirmed_events": 7,
                "verification_failures": 0,
                "total_verified_events": 5,
            },
            "operational": {
                "async_external_io_fallback_total": 3,
                "realtime_rules_failed_total": 5,
                "replay_rule_failures_total": 2,
                "rtvi_call_failures_total": 6,
                "incident_query_failures_total": 1,
                "replay_rule_failures_total": 2,
            },
            "errors": {
                "drop_reasons": [
                    {"reason": "rate_limit", "count": 3},
                    {"reason": "dedup", "count": 1},
                ],
                "verification_failure_reasons": [
                    {"reason": "vlm_timeout", "count": 2},
                ],
                "async_fallback_reasons": [
                    {"operation": "elastic_sink", "reason": "timeout", "count": 3},
                ],
                "async_external_io_errors": [
                    {"operation": "vst_media", "mode": "async", "result": "timeout", "count": 4},
                ],
                "realtime_rule_failure_stages": [
                    {"stage": "start_stream", "count": 5},
                ],
                "replay_rule_failures": 2,
                "rtvi_call_failure_methods": [
                    {"method": "generate_captions", "count": 6},
                ],
                "incident_query_failures": 1,
            },
        }

    def _sample_latency_summary(self):
        return {
            "summary_source": "prometheus_aggregate",
            "summary_status": "success",
            "total_sensors": 2,
            "total_hits": 5,
            "total_usable": 5,
            "per_sensor_summary_status": "has_data",
            "prometheus_window": {
                "start_ts": "2026-05-01T00:00:00Z",
                "end_ts": "2026-05-01T00:05:00Z",
                "window_seconds": 390,
                "buffer_seconds": 90,
            },
            "e2e": {"avg": 2.3, "p50": 2.2, "p90": 2.7, "p99": 3.0, "max": None, "n": 5},
            "reliability": {
                "total_events": 5,
                "total_verified_events": 5,
                "confirmed_events": 3,
                "rejected_events": 2,
                "verification_failed_events": 0,
                "unknown_events": 0,
                "events_after_dedup": 12,
                "dropped_events": 4,
                "skipped_confirmed_events": 7,
                "verification_failures": 0,
            },
        }

    def test_generate_prometheus_latency_report_suite_writes_layered_csv_only(self):
        self.settings.synthetic_alerts_injection = True
        self.settings.synthetic_alert_submission_mode = "message_bus"
        self.settings.synthetic_alerts_per_minute = 20

        report = self.report_service.generate_prometheus_latency_report_suite(
            job_id="job-1",
            evaluation_results=self._sample_results(),
            latency_summary=self._sample_latency_summary(),
            prometheus_aggregate_summary=self._sample_aggregate_summary(),
            duration_str="10m",
        )

        self.assertEqual(report["csv_filename"], "latency_run_summary.csv")
        expected_files = {
            "run_summary_csv",
            "stage_breakdown_csv",
            "sensor_drilldown_csv",
            "error_breakdown_csv",
        }
        self.assertEqual(set(report["report_files"]), expected_files)
        for filename in report["report_files"].values():
            self.assertTrue(os.path.exists(os.path.join(self.temp_dir.name, filename)))
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir.name, "latency_run_summary.json")))
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir.name, "latency_stage_breakdown.json")))
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir.name, "latency_sensor_drilldown.json")))
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir.name, "latency_run_manifest.json")))

        with open(os.path.join(self.temp_dir.name, "latency_run_summary.csv"), newline="", encoding="utf-8") as f:
            summary_rows = list(csv.DictReader(f))
        self.assertEqual(summary_rows[0]["schema_version"], "3")
        self.assertEqual(summary_rows[0]["job_id"], "job-1")
        self.assertEqual(summary_rows[0]["data_source"], "prometheus")
        self.assertEqual(summary_rows[0]["metric_profile"], "realtime")
        self.assertEqual(summary_rows[0]["summary_status"], "success")
        self.assertEqual(summary_rows[0]["synthetic_alerts_injection"], "true")
        self.assertEqual(summary_rows[0]["synthetic_alert_submission_mode"], "message_bus")
        self.assertEqual(summary_rows[0]["synthetic_alerts_per_minute"], "20.0000")
        self.assertEqual(summary_rows[0]["synthetic_alert_event_duration_ms"], "30000")
        self.assertEqual(summary_rows[0]["synthetic_distribution_strategy"], "round_robin")
        self.assertEqual(summary_rows[0]["synthetic_target_submissions"], "200")
        self.assertEqual(summary_rows[0]["metrics_counts_source"], "")
        self.assertEqual(summary_rows[0]["sensors_reported"], "2")
        self.assertEqual(summary_rows[0]["sensors_with_latency_data"], "1")
        self.assertEqual(summary_rows[0]["sensors_no_data"], "1")
        self.assertEqual(summary_rows[0]["verified_events"], "5")
        self.assertEqual(summary_rows[0]["error_events_total"], "27")
        self.assertEqual(summary_rows[0]["top_drop_reason"], "rate_limit")
        self.assertEqual(summary_rows[0]["top_verification_failure_reason"], "vlm_timeout")
        self.assertEqual(summary_rows[0]["top_async_fallback_reason"], "elastic_sink:timeout")
        self.assertEqual(summary_rows[0]["async_external_io_errors_total"], "4")
        self.assertEqual(summary_rows[0]["realtime_rule_failures_total"], "5")
        self.assertEqual(summary_rows[0]["replay_rule_failures_total"], "2")
        self.assertEqual(summary_rows[0]["rtvi_call_failures_total"], "6")
        self.assertEqual(summary_rows[0]["incident_query_failures_total"], "1")
        self.assertEqual(summary_rows[0]["top_error_category"], "rtvi_call_failure")
        self.assertEqual(summary_rows[0]["top_error_reason"], "generate_captions")
        self.assertEqual(summary_rows[0]["error_breakdown_file"], "error_breakdown.csv")
        self.assertEqual(summary_rows[0]["e2e_p90_sec"], "2.7000")
        self.assertEqual(summary_rows[0]["top_bottleneck_stage"], "upstream")
        self.assertEqual(summary_rows[0]["top_bottleneck_avg_sec"], "1.2000")

        with open(os.path.join(self.temp_dir.name, "latency_stage_breakdown.csv"), newline="", encoding="utf-8") as f:
            stage_rows = list(csv.DictReader(f))
        stage_by_name = {row["stage"]: row for row in stage_rows}
        self.assertEqual(stage_by_name["vst_fetch"]["stage_display_name"], "VST Fetch")
        self.assertEqual(stage_by_name["video_length"]["category"], "input_context")
        self.assertEqual(stage_by_name["upstream"]["bottleneck_rank"], "1")
        self.assertEqual(stage_by_name["upstream"]["population_aligned"], "true")
        self.assertEqual(stage_by_name["upstream"]["e2e_sample_count"], "5")
        self.assertNotEqual(stage_by_name["upstream"]["share_of_e2e_avg_pct"], "")
        self.assertEqual(stage_by_name["e2e"]["category"], "end_to_end")

        with open(os.path.join(self.temp_dir.name, "error_breakdown.csv"), newline="", encoding="utf-8") as f:
            error_rows = list(csv.DictReader(f))
        self.assertEqual(
            [row["error_category"] for row in error_rows],
            [
                "drop",
                "drop",
                "verification_failure",
                "async_fallback",
                "async_external_io_error",
                "realtime_rule_failure",
                "replay_rule_failure",
                "rtvi_call_failure",
                "incident_query_failure",
            ],
        )
        self.assertEqual(error_rows[0]["reason"], "rate_limit")
        self.assertEqual(error_rows[0]["count"], "3")
        self.assertEqual(error_rows[0]["share_pct"], "75.0000")
        self.assertEqual(error_rows[3]["operation"], "elastic_sink")
        self.assertEqual(error_rows[4]["reason"], "timeout")
        self.assertEqual(error_rows[4]["operation"], "vst_media:async")
        self.assertEqual(error_rows[5]["reason"], "start_stream")
        self.assertEqual(error_rows[7]["reason"], "generate_captions")

        with open(os.path.join(self.temp_dir.name, "latency_sensor_drilldown.csv"), newline="", encoding="utf-8") as f:
            sensor_rows = list(csv.DictReader(f))
        sensor_by_id = {row["sensor_id"]: row for row in sensor_rows}
        self.assertEqual(sensor_by_id["cam-1"]["has_latency_data"], "true")
        self.assertEqual(sensor_by_id["cam-1"]["dominant_stage"], "worker_processing")
        self.assertEqual(sensor_by_id["cam-1"]["sensor_rank_by_e2e_p90"], "1")
        self.assertEqual(sensor_by_id["cam-2"]["has_latency_data"], "false")
        self.assertEqual(sensor_by_id["cam-2"]["sensor_rank_by_no_data"], "1")


    def test_prometheus_latency_suite_renders_ondemand_metric_profile(self):
        aggregate_summary = {
            "status": "success",
            "metric_profile": "ondemand",
            "not_applicable_stages": [
                "upstream",
                "kafka_lag",
                "worker_queue_wait",
                "vst",
                "video_length",
            ],
            "start_ts": "2026-05-01T00:00:00Z",
            "end_ts": "2026-05-01T00:05:00Z",
            "window_seconds": 300,
            "buffer_seconds": 0,
            "stats": {
                "upstream": {"avg": None, "p50": None, "p90": None, "p99": None, "count": 0},
                "kafka_lag": {"avg": None, "p50": None, "p90": None, "p99": None, "count": 0},
                "worker_queue_wait": {"avg": None, "p50": None, "p90": None, "p99": None, "count": 0},
                "vst": {"avg": None, "p50": None, "p90": None, "p99": None, "count": 0},
                "video_length": {"avg": None, "p50": None, "p90": None, "p99": None, "count": 0},
                "vlm": {"avg": 2.0, "p50": 1.8, "p90": 2.8, "p99": 3.0, "count": 20},
                "worker_processing": {"avg": 2.5, "p50": 2.4, "p90": 3.2, "p99": 3.5, "count": 20},
                "e2e": {"avg": 2.8, "p50": 2.7, "p90": 3.6, "p99": 3.9, "count": 20},
            },
            "reliability": {
                "confirmed_events": 0,
                "rejected_events": 0,
                "verification_failed_events": 0,
                "unknown_events": 20,
                "events_after_dedup": 0,
                "dropped_events": 0,
                "skipped_confirmed_events": 0,
                "verification_failures": 1,
                "total_verified_events": 20,
            },
            "errors": {
                "ondemand_request_outcomes": [{"outcome": "failed", "count": 2}],
                "ondemand_verification_failure_reasons": [{"reason": "vlm_timeout", "count": 1}],
            },
        }
        latency_summary = {
            "summary_source": "prometheus_aggregate",
            "summary_status": "success",
            "metric_profile": "ondemand",
            "total_sensors": 2,
            "total_hits": 20,
            "total_usable": 20,
            "reliability": dict(aggregate_summary["reliability"]),
            "e2e": {"avg": 2.8, "p50": 2.7, "p90": 3.6, "p99": 3.9, "max": None, "n": 20},
        }

        report = self.report_service.generate_prometheus_latency_report_suite(
            job_id="job-ondemand",
            evaluation_results=[
                {
                    "eval_mode": "latency",
                    "data_source": "prometheus",
                    "metric_profile": "ondemand",
                    "sensor_id": "cam-1",
                    "status": "not_applicable",
                    "stats": {},
                    "reliability": {},
                }
            ],
            latency_summary=latency_summary,
            prometheus_aggregate_summary=aggregate_summary,
            duration_str="5m",
        )

        with open(report["csv_path"], newline="", encoding="utf-8") as f:
            summary_rows = list(csv.DictReader(f))
        self.assertEqual(summary_rows[0]["metric_profile"], "ondemand")
        self.assertEqual(summary_rows[0]["verified_events"], "20")
        self.assertEqual(summary_rows[0]["top_error_category"], "ondemand_request_error")
        self.assertEqual(summary_rows[0]["top_error_reason"], "failed")

        with open(os.path.join(self.temp_dir.name, "latency_stage_breakdown.csv"), newline="", encoding="utf-8") as f:
            stage_rows = {row["stage"]: row for row in csv.DictReader(f)}
        self.assertEqual(stage_rows["upstream"]["data_status"], "not_applicable")
        self.assertEqual(stage_rows["vlm_inference"]["data_status"], "has_data")
        self.assertEqual(stage_rows["e2e"]["data_status"], "has_data")

        with open(os.path.join(self.temp_dir.name, "error_breakdown.csv"), newline="", encoding="utf-8") as f:
            error_rows = list(csv.DictReader(f))
        self.assertEqual([row["error_category"] for row in error_rows], [
            "ondemand_request_error",
            "ondemand_verification_failure",
        ])
        self.assertEqual(error_rows[0]["reason"], "failed")
        self.assertEqual(error_rows[1]["reason"], "vlm_timeout")

    def test_prometheus_latency_suite_keeps_reliability_when_latency_empty(self):
        aggregate_summary = self._sample_aggregate_summary()
        aggregate_summary["status"] = "no_data"
        aggregate_summary["stats"] = {
            "e2e": {"avg": None, "p50": None, "p90": None, "p99": None, "count": 0}
        }
        aggregate_summary["reliability"].update(
            {
                "total_verified_events": 0,
                "confirmed_events": 0,
                "rejected_events": 0,
                "events_after_dedup": 49,
                "dropped_events": 8734,
                "skipped_confirmed_events": 48,
            }
        )
        latency_summary = self._sample_latency_summary()
        latency_summary["summary_status"] = "no_data"
        latency_summary["total_hits"] = 0
        latency_summary["total_usable"] = 0
        latency_summary["e2e"] = {"avg": None, "p50": None, "p90": None, "p99": None, "max": None, "n": 0}
        latency_summary["reliability"].update(
            {
                "total_events": 0,
                "total_verified_events": 0,
                "confirmed_events": 0,
                "rejected_events": 0,
                "events_after_dedup": 49,
                "dropped_events": 8734,
                "skipped_confirmed_events": 48,
            }
        )

        report = self.report_service.generate_prometheus_latency_report_suite(
            job_id="job-empty-latency",
            evaluation_results=[],
            latency_summary=latency_summary,
            prometheus_aggregate_summary=aggregate_summary,
            duration_str="10m",
        )

        with open(report["csv_path"], newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["summary_status"], "no_data")
        self.assertEqual(rows[0]["e2e_count"], "0")
        self.assertEqual(rows[0]["e2e_avg_sec"], "")
        self.assertEqual(rows[0]["events_after_dedup"], "49")
        self.assertEqual(rows[0]["dropped_events"], "8734")
        self.assertEqual(rows[0]["skipped_confirmed_events"], "48")

    def test_synthetic_report_exposes_e2e_completeness(self):
        self.settings.synthetic_alerts_injection = True
        self.settings.synthetic_alerts_per_minute = 1.0
        report = self.report_service.generate_prometheus_latency_report_suite(
            job_id="job-partial",
            evaluation_results=self._sample_results(),
            latency_summary=self._sample_latency_summary(),
            prometheus_aggregate_summary=self._sample_aggregate_summary(),
            duration_str="10m",
        )

        with open(report["csv_path"], newline="", encoding="utf-8") as report_file:
            row = next(csv.DictReader(report_file))
        self.assertEqual(row["synthetic_target_submissions"], "10")
        self.assertEqual(row["terminal_outcomes"], "5")
        self.assertEqual(row["successful_verifications"], "5")
        self.assertEqual(row["e2e_expected_count"], "5")
        self.assertEqual(row["e2e_observed_count"], "5")
        self.assertEqual(row["e2e_missing_count"], "0")
        self.assertEqual(row["e2e_completion_ratio"], "1.0000")

    def test_prometheus_report_marks_bucket_ceiling_percentiles_as_censored(self):
        aggregate = self._sample_aggregate_summary()
        aggregate["stats"]["worker_processing"]["p99"] = 60.0
        aggregate["stats"]["e2e"]["p90"] = 120.0
        aggregate["stats"]["e2e"]["p99"] = 120.0

        report = self.report_service.generate_prometheus_latency_report_suite(
            job_id="job-censored",
            evaluation_results=self._sample_results(),
            latency_summary=self._sample_latency_summary(),
            prometheus_aggregate_summary=aggregate,
            duration_str="10m",
        )

        with open(report["csv_path"], newline="", encoding="utf-8") as report_file:
            row = next(csv.DictReader(report_file))
        self.assertEqual(row["e2e_p90_censored"], "true")
        self.assertEqual(row["e2e_p99_censored"], "true")
        self.assertEqual(row["e2e_bucket_max_sec"], "120.0000")

        with open(
            os.path.join(self.temp_dir.name, "latency_stage_breakdown.csv"),
            newline="",
            encoding="utf-8",
        ) as stage_file:
            stage_rows = {row["stage"]: row for row in csv.DictReader(stage_file)}
        self.assertEqual(stage_rows["worker_processing"]["p99_censored"], "true")
        self.assertEqual(stage_rows["worker_processing"]["finite_bucket_max_sec"], "60.0000")

    def test_stage_share_is_blank_when_sample_populations_differ(self):
        aggregate = self._sample_aggregate_summary()
        aggregate["stats"]["vlm"]["count"] = 4

        report = self.report_service.generate_prometheus_latency_report_suite(
            job_id="job-population-mismatch",
            evaluation_results=self._sample_results(),
            latency_summary=self._sample_latency_summary(),
            prometheus_aggregate_summary=aggregate,
            duration_str="10m",
        )

        with open(
            os.path.join(self.temp_dir.name, "latency_stage_breakdown.csv"),
            newline="",
            encoding="utf-8",
        ) as stage_file:
            stage_rows = {row["stage"]: row for row in csv.DictReader(stage_file)}
        self.assertEqual(stage_rows["vlm_inference"]["population_aligned"], "false")
        self.assertEqual(stage_rows["vlm_inference"]["share_of_e2e_avg_pct"], "")

    def test_generate_prometheus_latency_csv(self):
        results = [
            {
                "eval_mode": "latency",
                "data_source": "prometheus",
                "sensor_id": "cam-1",
                "start_ts": "2026-05-01T00:00:00Z",
                "end_ts": "2026-05-01T00:05:00Z",
                "status": "success",
                "stats": {
                    "upstream": {"avg": 1.1, "p50": 1.0, "p90": 1.3, "p99": 1.4, "count": 4},
                    "kafka_lag": {"avg": 0.1, "p50": 0.1, "p90": 0.2, "p99": 0.2, "count": 4},
                    "worker_queue_wait": {"avg": 0.2, "p50": 0.2, "p90": 0.3, "p99": 0.4, "count": 4},
                    "vst": {"avg": 0.7, "p50": 0.6, "p90": 0.9, "p99": 1.0, "count": 4},
                    "video_length": {"avg": 10.0, "p50": 10.0, "p90": 11.0, "p99": 12.0, "count": 4},
                    "vlm": {"avg": 2.2, "p50": 2.1, "p90": 2.5, "p99": 2.9, "count": 4},
                    "worker_processing": {"avg": 3.3, "p50": 3.2, "p90": 3.5, "p99": 3.9, "count": 4},
                    "e2e": {"avg": 4.4, "p50": 4.3, "p90": 4.8, "p99": 5.2, "count": 4},
                },
                "reliability": {
                    "total_events": 4,
                    "confirmed_events": 3,
                    "rejected_events": 1,
                    "verification_failed_events": 0,
                    "unknown_events": 0,
                    "events_after_dedup": 4,
                    "dropped_events": 0,
                    "skipped_confirmed_events": 0,
                    "verification_failures": 0,
                },
            }
        ]

        report = self.report_service.generate_csv_v2("job-1", results)
        self.assertEqual(report["csv_filename"], "latency_run_summary.csv")
        self.assertEqual(report["rows_written"], 1)
        self.assertTrue(os.path.exists(report["csv_path"]))
        self.assertEqual(report["report_files"]["sensor_drilldown_csv"], "latency_sensor_drilldown.csv")

        with open(os.path.join(self.temp_dir.name, "latency_sensor_drilldown.csv"), newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sensor_id"], "cam-1")
        self.assertEqual(rows[0]["e2e_count"], "4")
        self.assertEqual(rows[0]["confirmed_events"], "3")

    def test_generate_csv_v2_in_latency_mode_with_empty_results_still_emits_latency_schema(self):
        latency_settings = SimpleNamespace(
            reports_dir=self.temp_dir.name,
            max_stream_concurrency=0,
            platform="test-platform",
            model="test-model",
            kafka_bootstrap_servers="",
            kafka_vlm_topic="mdx-vlm-incidents",
            eval_mode="latency",
            data_source="prometheus",
        )
        report_service = ReportService(latency_settings)

        report = report_service.generate_csv_v2("job-empty", [])
        self.assertEqual(report["csv_filename"], "latency_run_summary.csv")
        self.assertEqual(report["rows_written"], 1)

        with open(report["csv_path"], newline="", encoding="utf-8") as f:
            headers = next(csv.reader(f))
        self.assertIn("schema_version", headers)
        self.assertIn("summary_status", headers)
        self.assertNotIn("upstream_avg", headers)
        self.assertNotIn("gt_events_count", headers)

    def test_append_summary_line_prometheus_without_aggregate_defaults_to_na_and_zero(self):
        results = [
            {
                "data_source": "prometheus",
                "sensor_id": "cam-1",
                "stats": {
                    "e2e": {"count": 7},
                },
                "reliability": {
                    "total_events": 7,
                    "confirmed_events": 5,
                    "rejected_events": 2,
                    "verification_failed_events": 0,
                    "unknown_events": 0,
                    "events_after_dedup": 7,
                    "dropped_events": 1,
                    "skipped_confirmed_events": 0,
                    "verification_failures": 0,
                },
            },
            {
                "data_source": "prometheus",
                "sensor_id": "cam-2",
                "stats": {
                    "e2e": {"count": 5},
                },
                "reliability": {
                    "total_events": 5,
                    "confirmed_events": 1,
                    "rejected_events": 3,
                    "verification_failed_events": 1,
                    "unknown_events": 0,
                    "events_after_dedup": 5,
                    "dropped_events": 0,
                    "skipped_confirmed_events": 1,
                    "verification_failures": 1,
                },
            },
        ]
        latency_summary = {
            "upstream": {"avg": 1.0},
            "kafka_lag": {"avg": 0.1},
            "worker_queue_wait": {"avg": 0.2},
            "vst": {"avg": 0.2},
            "video_length": {"avg": 8.0},
            "vlm": {"avg": 0.3},
            "worker_processing": {"avg": 0.4},
            "e2e": {"avg": 0.4},
        }

        output = self.report_service.append_summary_line(
            job_id="job-prom-1",
            evaluation_results=results,
            latency_summary=latency_summary,
            duration_str="10m",
        )

        with open(output["csv_path"], newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_id"], "job-prom-1")
        self.assertEqual(rows[0]["summary_status"], "unknown")
        self.assertEqual(rows[0]["verified_events"], "0")
        self.assertEqual(rows[0]["confirmed_events"], "0")
        self.assertEqual(rows[0]["dropped_events"], "0")
        self.assertEqual(rows[0]["e2e_avg_sec"], "")
        self.assertEqual(
            set(output["report_files"]),
            {"run_summary_csv", "stage_breakdown_csv", "sensor_drilldown_csv", "error_breakdown_csv"},
        )

    def test_append_summary_line_uses_aggregate_prometheus_summary_when_provided(self):
        results = [
            {
                "data_source": "prometheus",
                "sensor_id": "cam-a",
                "status": "no_data",
                "stats": {"e2e": {"count": 0}},
                "reliability": {"total_events": 0},
            },
            {
                "data_source": "prometheus",
                "sensor_id": "cam-b",
                "status": "no_data",
                "stats": {"e2e": {"count": 0}},
                "reliability": {"total_events": 0},
            },
        ]
        latency_summary = {
            "upstream": {"avg": None},
            "kafka_lag": {"avg": None},
            "worker_queue_wait": {"avg": None},
            "vst": {"avg": None},
            "video_length": {"avg": None},
            "vlm": {"avg": None},
            "worker_processing": {"avg": None},
            "e2e": {"avg": None},
        }
        aggregate_summary = {
            "status": "success",
            "stats": {
                "upstream": {"avg": 1.2},
                "kafka_lag": {"avg": 0.2},
                "worker_queue_wait": {"avg": 0.3},
                "vst": {"avg": 0.4},
                "video_length": {"avg": 8.5},
                "vlm": {"avg": 0.8},
                "worker_processing": {"avg": 1.0},
                "e2e": {"avg": 2.3},
            },
            "reliability": {
                "confirmed_events": 5,
                "rejected_events": 7,
                "verification_failed_events": 0,
                "unknown_events": 0,
                "events_after_dedup": 12,
                "dropped_events": 2,
                "skipped_confirmed_events": 1,
                "verification_failures": 0,
                "total_verified_events": 12,
            },
            "operational": {
                "async_external_io": {"avg": 0.11, "p90": 0.33},
                "async_external_io_count": 20,
                "async_external_io_fallback_total": 2,
                "async_sink_in_flight_avg": 1.5,
                "async_sink_in_flight_max": 4.0,
                "realtime_rules_active_avg": 5.0,
                "realtime_rules_active_max": 6.0,
                "realtime_rules_created_total": 1,
                "realtime_rules_deleted_total": 0,
                "realtime_rules_failed_total": 0,
                "rtvi_call": {"avg": 0.75, "p90": 1.25},
                "rtvi_call_count": 12,
                "rtvi_call_failures_total": 3,
                "incident_query": {"avg": 0.04, "p90": 0.08},
                "incident_query_count": 18,
                "incident_query_failures_total": 1,
            },
            "errors": {
                "drop_reasons": [{"reason": "dedup", "count": 2}],
                "verification_failure_reasons": [],
                "async_fallback_reasons": [{"operation": "redis", "reason": "timeout", "count": 2}],
                "async_external_io_errors": [{"operation": "vst_media", "mode": "sync", "result": "error", "count": 3}],
                "realtime_rule_failure_stages": [{"stage": "validation", "count": 4}],
                "replay_rule_failures": 2,
                "rtvi_call_failure_methods": [{"method": "start_stream", "count": 3}],
                "incident_query_failures": 1,
            },
        }

        output = self.report_service.append_summary_line(
            job_id="job-prom-agg-1",
            evaluation_results=results,
            latency_summary=latency_summary,
            duration_str="10m",
            prometheus_aggregate_summary=aggregate_summary,
        )

        with open(output["csv_path"], newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verified_events"], "12")
        self.assertEqual(rows[0]["verified_events_per_min_per_stream"], "0.6000")
        self.assertEqual(rows[0]["confirmed_events_per_min_per_stream"], "0.2500")
        self.assertEqual(rows[0]["e2e_avg_sec"], "2.3000")
        self.assertEqual(rows[0]["confirmed_events"], "5")
        self.assertEqual(rows[0]["error_events_total"], "17")
        self.assertEqual(rows[0]["top_drop_reason"], "dedup")
        self.assertEqual(rows[0]["top_async_fallback_reason"], "redis:timeout")
        self.assertEqual(rows[0]["realtime_rule_failures_total"], "4")
        self.assertEqual(rows[0]["top_error_category"], "realtime_rule_failure")
        self.assertEqual(rows[0]["top_error_reason"], "validation")
        self.assertEqual(rows[0]["top_bottleneck_stage"], "upstream")
        self.assertEqual(output["report_files"]["stage_breakdown_csv"], "latency_stage_breakdown.csv")


if __name__ == "__main__":
    unittest.main()
