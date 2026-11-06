# SPDX-License-Identifier: Apache-2.0
"""Exercise local integration with fixed clocks and external-service responses."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

CORE = Path(__file__).resolve().parents[1] / 'benchmark/core'
spec = importlib.util.spec_from_file_location('alerts_local', CORE / 'alerts_latency_runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_local_evaluation_preserves_native_summary(monkeypatch):
    monkeypatch.setenv('JOB_ID', 'run-1')
    service, reports = Mock(), Mock()
    service.filter_evaluable_v2_incidents.side_effect = lambda x: x
    service.evaluate_batch_v2.return_value = [{'metric_sensor_id': 'sensor-1'}]
    service.collect_prometheus_aggregate_summary.return_value = {'status': 'success'}
    service.build_prometheus_latency_summary.return_value = {'summary_status': 'success'}
    summary = {'e2e_count': 4, 'e2e_p90_sec': 1.25, 'e2e_p90_censored': True}
    reports.generate_prometheus_latency_report_suite.return_value = {
        'summary': summary, 'csv_filename': 'latency_run_summary.csv'}
    settings = SimpleNamespace(eval_mode='latency', data_source='prometheus',
                               prometheus_summary_buffer_seconds=0, latency_duration='5m')
    result = runner.evaluate(settings, service, reports, [{'video': 'sensor-1'}],
                            start='2026-01-01T00:00:00Z', end='2026-01-01T00:05:00Z',
                            injection={'submitted': 4})
    assert result['summary'] is summary
    assert result['status'] == 'completed'
    aligned = service.evaluate_batch_v2.call_args.args[0][0]
    assert aligned['start_timestamp'] == '2026-01-01T00:00:00Z'
    assert aligned['end_timestamp'] == '2026-01-01T00:05:00Z'
    service.build_prometheus_latency_summary.assert_called_once_with(
        service.evaluate_batch_v2.return_value, {'status': 'success'},
        synthetic_injection_summary={'submitted': 4})


def test_drain_and_sensor_windows_use_same_buffer(monkeypatch):
    from datetime import datetime, timezone
    settings = SimpleNamespace(eval_mode='latency', data_source='prometheus',
                               prometheus_summary_buffer_seconds=90)
    workflow = runner.LatencyWorkflow(settings, None, None)
    monkeypatch.setattr(workflow, '_utc_now', lambda: datetime(2026, 1, 1, 0, 5, 30, tzinfo=timezone.utc))
    sleep = Mock()
    monkeypatch.setattr('alert_bridge_evaluator.latency_workflow.time.sleep', sleep)
    workflow._wait_for_prometheus_summary_window('2026-01-01T00:05:00Z')
    sleep.assert_called_once_with(60.0)
    aligned = workflow._with_prometheus_sensor_query_window([{'video': 's'}],
        '2026-01-01T00:00:00Z', '2026-01-01T00:05:00Z')
    assert aligned[0]['end_timestamp'] == '2026-01-01T00:06:30Z'


def test_stream_tracker_returns_source_payload(monkeypatch):
    tracker = runner.StreamTracker('http://vst', 'http://streamer')
    tracker._running = True
    tracker._stop_requested = False
    monkeypatch.setattr('surf_trigger.stream_tracking.time.time', lambda: 1000)
    event = asyncio.run(tracker._wait_for_streaming_complete({
        'video.mp4': {'start_timestamp': 900, 'end_timestamp': 950, 'sensor_id': 'uuid-1', 'duration': 50}}, 990))
    assert event.data['sensor_data']['video.mp4']['sensor_id'] == 'uuid-1'
    payload = runner.PayloadBuilder()._build_eval_payload('job', event.data['sensor_data'], 1, event.data)
    assert payload['incidents'][0]['stream_uuid'] == 'uuid-1'
    assert payload['incidents'][0]['start_timestamp'] == '1970-01-01T00:15:00Z'


def test_namespaced_protobuf_serialization():
    from alert_bridge_evaluator.services.kafka_synthetic_producer import convert_incident_to_protobuf_incident
    from alert_bridge_evaluator.mdx.protobuf import Incident
    message = convert_incident_to_protobuf_incident({'category': 'FOV Count Violation', 'sensorId': 'sensor-1'})
    decoded = Incident.FromString(message.SerializeToString())
    assert decoded.category == 'FOV Count Violation'
    assert decoded.sensorId == 'sensor-1'
