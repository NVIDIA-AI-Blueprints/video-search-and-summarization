import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from alert_bridge_evaluator.services.prometheus_client import PrometheusClient, PrometheusError


class TestPrometheusClient(unittest.TestCase):
    def setUp(self):
        settings = SimpleNamespace(
            prometheus_url="http://prometheus:9090",
            prometheus_timeout=10,
        )
        self.client = PrometheusClient(settings)

    def _mock_success_response(self, value: str = "5") -> Mock:
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "status": "success",
            "data": {
                "result": [{"value": [0, value]}],
            },
        }
        return response

    def test_query_confirmed_events_count_uses_per_sensor_metric(self):
        response = self._mock_success_response("7")
        self.client._session.get = Mock(return_value=response)

        count = self.client.query_confirmed_events_count(
            sensor_id="cam-42",
            start_timestamp="2026-05-01T00:00:00Z",
            end_timestamp="2026-05-01T00:01:00Z",
        )

        self.assertEqual(count, 7)
        _, kwargs = self.client._session.get.call_args
        query = kwargs["params"]["query"]
        self.assertIn("alert_bridge_events_by_sensor_total", query)
        self.assertIn('sensorId="cam-42"', query)
        self.assertIn('verdict="confirmed"', query)
        self.assertIn("[60s]", query)
        self.assertIn("time", kwargs["params"])

    def test_query_latency_metrics_invalid_duration_raises(self):
        with self.assertRaises(PrometheusError):
            self.client.query_latency_metrics(
                sensor_id="cam-1",
                duration="invalid-duration",
            )

    def test_query_latency_metrics_returns_all_stages(self):
        self.client._prom_avg = Mock(return_value=1.23)
        self.client._prom_quantile = Mock(side_effect=[0.5, 0.9, 1.0] * 8)
        self.client._prom_count = Mock(return_value=12)

        stats = self.client.query_latency_metrics(
            sensor_id="cam-99",
            duration="5m",
        )

        expected_stages = {
            "upstream",
            "kafka_lag",
            "worker_queue_wait",
            "vst",
            "video_length",
            "vlm",
            "worker_processing",
            "e2e",
        }
        self.assertEqual(set(stats.keys()), expected_stages)
        self.assertEqual(stats["e2e"]["avg"], 1.23)
        self.assertEqual(stats["e2e"]["count"], 12.0)

    def test_query_reliability_metrics_returns_expected_totals(self):
        self.client._prom_increase = Mock(side_effect=[3, 4, 1, 2, 10, 5, 6, 7])

        stats = self.client.query_reliability_metrics(
            sensor_id="cam-99",
            duration="5m",
        )

        self.assertEqual(stats["confirmed_events"], 3)
        self.assertEqual(stats["rejected_events"], 4)
        self.assertEqual(stats["verification_failed_events"], 1)
        self.assertEqual(stats["unknown_events"], 2)
        self.assertEqual(stats["events_after_dedup"], 10)
        self.assertEqual(stats["dropped_events"], 5)
        self.assertEqual(stats["skipped_confirmed_events"], 6)
        self.assertEqual(stats["verification_failures"], 7)
        self.assertEqual(stats["total_events"], 10)
        self.assertEqual(self.client._prom_increase.call_count, 8)

    def test_query_aggregate_summary_metrics_uses_buffered_window(self):
        self.client._prom_avg = Mock(return_value=1.5)
        self.client._prom_quantile = Mock(return_value=1.0)
        self.client._prom_count = Mock(return_value=4)
        self.client._prom_increase = Mock(
            side_effect=[
                4, 5, 1, 0, 12, 2, 1, 3,  # existing reliability
                6, 7, 8, 9, 10, 11, 12,   # operational counters
            ]
        )
        self.client._prom_gauge_avg = Mock(side_effect=[2.5, 3.5])
        self.client._prom_gauge_max = Mock(side_effect=[4.5, 5.5])
        self.client._prom_grouped_increase = Mock(return_value=[])

        result = self.client.query_aggregate_summary_metrics(
            start_timestamp="2026-05-01T00:00:00Z",
            end_timestamp="2026-05-01T00:01:00Z",
            buffer_seconds=90,
        )

        expected_eval_time = self.client._iso_to_unix("2026-05-01T00:01:00Z") + 90
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["window_seconds"], 150)
        self.assertEqual(result["buffer_seconds"], 90)
        self.assertAlmostEqual(result["eval_time_unix"], expected_eval_time)
        self.assertEqual(result["reliability"]["confirmed_events"], 4)
        self.assertEqual(result["reliability"]["rejected_events"], 5)
        self.assertEqual(result["reliability"]["verification_failed_events"], 1)
        self.assertEqual(result["reliability"]["unknown_events"], 0)
        self.assertEqual(result["reliability"]["total_verified_events"], 10)
        self.assertEqual(result["operational"]["async_external_io_fallback_total"], 6)
        self.assertEqual(result["operational"]["realtime_rules_created_total"], 7)
        self.assertEqual(result["operational"]["realtime_rules_deleted_total"], 8)
        self.assertEqual(result["operational"]["realtime_rules_failed_total"], 9)
        self.assertEqual(result["operational"]["rtvi_call_failures_total"], 10)
        self.assertEqual(result["operational"]["incident_query_failures_total"], 11)
        self.assertEqual(result["operational"]["replay_rule_failures_total"], 12)
        self.assertEqual(result["operational"]["async_sink_in_flight_avg"], 2.5)
        self.assertEqual(result["operational"]["async_sink_in_flight_max"], 4.5)
        self.assertEqual(result["operational"]["realtime_rules_active_avg"], 3.5)
        self.assertEqual(result["operational"]["realtime_rules_active_max"], 5.5)
        self.assertEqual(result["operational"]["async_external_io_count"], 4)
        self.assertEqual(result["operational"]["rtvi_call_count"], 4)
        self.assertEqual(result["operational"]["incident_query_count"], 4)

        _, window, labels, eval_time = self.client._prom_count.call_args_list[0][0]
        self.assertEqual(window, "150s")
        self.assertEqual(labels, "")
        self.assertAlmostEqual(eval_time, expected_eval_time)

    def test_query_aggregate_summary_metrics_includes_error_breakdowns(self):
        self.client._prom_avg = Mock(return_value=1.5)
        self.client._prom_quantile = Mock(return_value=1.0)
        self.client._prom_count = Mock(return_value=4)
        self.client._prom_increase = Mock(
            side_effect=[
                4, 5, 1, 0, 12, 2, 1, 3,  # reliability
                6, 7, 8, 9, 10, 11, 12,   # operational counters
            ]
        )
        self.client._prom_gauge_avg = Mock(side_effect=[2.5, 3.5])
        self.client._prom_gauge_max = Mock(side_effect=[4.5, 5.5])
        self.client._prom_grouped_increase = Mock(
            side_effect=[
                [{"reason": "rate_limit", "count": 9}, {"reason": "dedup", "count": 3}],
                [{"reason": "vlm_timeout", "count": 2}],
                [{"operation": "elastic_sink", "reason": "timeout", "count": 1}],
                [{"operation": "vst_media", "mode": "async", "result": "timeout", "count": 4}],
                [{"stage": "start_stream", "count": 5}],
                [{"method": "generate_captions", "count": 6}],
            ]
        )

        result = self.client.query_aggregate_summary_metrics(
            start_timestamp="2026-05-01T00:00:00Z",
            end_timestamp="2026-05-01T00:01:00Z",
            buffer_seconds=90,
        )

        self.assertEqual(
            result["errors"]["drop_reasons"],
            [{"reason": "rate_limit", "count": 9}, {"reason": "dedup", "count": 3}],
        )
        self.assertEqual(
            result["errors"]["verification_failure_reasons"],
            [{"reason": "vlm_timeout", "count": 2}],
        )
        self.assertEqual(
            result["errors"]["async_fallback_reasons"],
            [{"operation": "elastic_sink", "reason": "timeout", "count": 1}],
        )
        self.assertEqual(
            result["errors"]["async_external_io_errors"],
            [{"operation": "vst_media", "mode": "async", "result": "timeout", "count": 4}],
        )
        self.assertEqual(
            result["errors"]["realtime_rule_failure_stages"],
            [{"stage": "start_stream", "count": 5}],
        )
        self.assertEqual(result["errors"]["replay_rule_failures"], 12)
        self.assertEqual(
            result["errors"]["rtvi_call_failure_methods"],
            [{"method": "generate_captions", "count": 6}],
        )
        self.assertEqual(result["errors"]["incident_query_failures"], 11)
        self.assertEqual(self.client._prom_grouped_increase.call_count, 6)


    def test_query_aggregate_summary_metrics_ondemand_profile_uses_ondemand_metrics(self):
        def fake_avg(metric, *_args):
            return 1.5 if "ondemand" in metric else None

        def fake_quantile(metric, *_args):
            return 2.5 if "ondemand" in metric else None

        def fake_count(metric, *_args):
            return 20 if "ondemand" in metric else 0

        def fake_increase(metric, _window, labels, _eval_time):
            if metric == self.client._ONDEMAND_VERDICT_COUNTER and 'verdict="unknown"' in labels:
                return 20
            if metric == self.client._ONDEMAND_REQUEST_COUNTER and labels == "":
                return 20
            if metric == self.client._ONDEMAND_REQUEST_COUNTER and 'outcome="accepted"' in labels:
                return 19
            if metric == self.client._ONDEMAND_VERIFICATION_FAILURE_COUNTER:
                return 1
            return 0

        def fake_grouped(metric, _window, labels, _eval_time, _group_labels):
            if metric == self.client._ONDEMAND_REQUEST_COUNTER and 'outcome!="accepted"' in labels:
                return [{"outcome": "failed", "count": 1}]
            if metric == self.client._ONDEMAND_VERIFICATION_FAILURE_COUNTER:
                return [{"reason": "vlm_timeout", "count": 1}]
            return []

        self.client._prom_avg = Mock(side_effect=fake_avg)
        self.client._prom_quantile = Mock(side_effect=fake_quantile)
        self.client._prom_count = Mock(side_effect=fake_count)
        self.client._prom_increase = Mock(side_effect=fake_increase)
        self.client._prom_gauge_avg = Mock(return_value=None)
        self.client._prom_gauge_max = Mock(return_value=None)
        self.client._prom_grouped_increase = Mock(side_effect=fake_grouped)

        result = self.client.query_aggregate_summary_metrics(
            start_timestamp="2026-05-01T00:00:00Z",
            end_timestamp="2026-05-01T00:01:00Z",
            buffer_seconds=0,
            metric_profile="ondemand",
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["metric_profile"], "ondemand")
        self.assertIn("upstream", result["not_applicable_stages"])
        self.assertEqual(result["stats"]["upstream"]["data_status"], "not_applicable")
        self.assertEqual(result["stats"]["e2e"]["count"], 20.0)
        self.assertEqual(result["reliability"]["unknown_events"], 20)
        self.assertEqual(result["reliability"]["total_verified_events"], 20)
        self.assertEqual(result["reliability"]["verification_failures"], 1)
        self.assertEqual(result["operational"]["ondemand_requests_total"], 20)
        self.assertEqual(result["operational"]["ondemand_requests_accepted_total"], 19)
        self.assertEqual(result["errors"]["ondemand_request_outcomes"], [{"outcome": "failed", "count": 1}])
        self.assertEqual(result["errors"]["ondemand_verification_failure_reasons"], [{"reason": "vlm_timeout", "count": 1}])

    def test_query_aggregate_summary_metrics_no_data(self):
        self.client._prom_avg = Mock(return_value=None)
        self.client._prom_quantile = Mock(return_value=None)
        self.client._prom_count = Mock(return_value=0)
        self.client._prom_increase = Mock(return_value=0)
        self.client._prom_gauge_avg = Mock(return_value=None)
        self.client._prom_gauge_max = Mock(return_value=None)
        self.client._prom_grouped_increase = Mock(return_value=[])

        result = self.client.query_aggregate_summary_metrics(
            start_timestamp="2026-05-01T00:00:00Z",
            end_timestamp="2026-05-01T00:01:00Z",
            buffer_seconds=90,
        )

        self.assertEqual(result["status"], "no_data")
        self.assertEqual(result["reliability"]["total_verified_events"], 0)
        self.assertEqual(result["operational"]["async_external_io_fallback_total"], 0)
        self.assertEqual(result["errors"]["drop_reasons"], [])


    def test_prom_counter_delta_uses_raw_boundary_snapshots(self):
        self.client._prom_query = Mock(side_effect=[0.0, 100.0, 302.0])

        count = self.client._prom_counter_delta(
            "alert_bridge_events_by_sensor_total",
            'sensorId=~"cam-a|cam-b",verdict="rejected"',
            1000.0,
            1390.0,
            "390s",
        )

        self.assertEqual(count, 202)
        queries = [call.args for call in self.client._prom_query.call_args_list]
        self.assertIn("resets(", queries[0][0])
        self.assertEqual(queries[0][1], 1390.0)
        self.assertEqual(queries[1][1], 1000.0)
        self.assertEqual(queries[2][1], 1390.0)

    def test_prom_counter_delta_rejects_counter_resets(self):
        self.client._prom_query = Mock(return_value=1.0)

        with self.assertRaisesRegex(PrometheusError, "Counter reset detected"):
            self.client._prom_counter_delta(
                "alert_bridge_events_by_sensor_total",
                'sensorId="cam-a"',
                1000.0,
                1390.0,
                "390s",
            )

    def test_synthetic_aggregate_uses_exact_sensor_scoped_counter_deltas(self):
        self.client._prom_avg = Mock(return_value=1.5)
        self.client._prom_quantile = Mock(return_value=1.0)
        self.client._prom_counter_delta = Mock(
            side_effect=[
                *([250] * 8),
                48,
                202,
                0,
                0,
                250,
                0,
                0,
                0,
            ]
        )
        self.client._prom_count = Mock(return_value=0)
        self.client._prom_increase = Mock(return_value=0)
        self.client._prom_gauge_avg = Mock(return_value=None)
        self.client._prom_gauge_max = Mock(return_value=None)
        self.client._prom_grouped_increase = Mock(return_value=[])

        result = self.client.query_aggregate_summary_metrics(
            start_timestamp="2026-05-01T00:00:00Z",
            end_timestamp="2026-05-01T00:05:00Z",
            buffer_seconds=90,
            sensor_ids=["uuid-b", "uuid-a"],
        )

        self.assertEqual(result["counts_source"], "sensor_scoped_counter_snapshot_delta")
        self.assertEqual(result["count_sensor_ids"], ["uuid-a", "uuid-b"])
        self.assertEqual(result["reliability"]["confirmed_events"], 48)
        self.assertEqual(result["reliability"]["rejected_events"], 202)
        self.assertEqual(result["reliability"]["total_verified_events"], 250)
        self.assertEqual(result["stats"]["e2e"]["count"], 250.0)
        first_metric, first_labels, *_ = self.client._prom_counter_delta.call_args_list[0].args
        self.assertEqual(
            first_metric,
            "alert_bridge_upstream_duration_by_sensor_seconds_count",
        )
        self.assertIn('sensorId=~"uuid-a|uuid-b"', first_labels)


if __name__ == "__main__":
    unittest.main()
