# SPDX-License-Identifier: Apache-2.0
"""Run Alerts latency evaluation locally, without evaluator HTTP services."""
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'native'))
from alert_bridge_evaluator.config import Settings
from alert_bridge_evaluator.services.alert_bridge_client import AlertBridgeClient
from alert_bridge_evaluator.services.elasticsearch_client import ElasticsearchClient
from alert_bridge_evaluator.services.prometheus_client import PrometheusClient
from alert_bridge_evaluator.services.evaluation import EvaluationService
from alert_bridge_evaluator.services.reports import ReportService
from alert_bridge_evaluator.latency_workflow import LatencyWorkflow
from surf_trigger.stream_tracking import StreamTracker
from surf_trigger.payload import PayloadBuilder


def evaluate(settings, service, reports, incidents, *, start=None, end=None, injection=None):
    workflow = LatencyWorkflow(settings, service, reports)
    incidents = service.filter_evaluable_v2_incidents(incidents)
    workflow._wait_for_prometheus_summary_window(end)
    aligned = workflow._with_prometheus_sensor_query_window(incidents, start, end)
    results = service.evaluate_batch_v2(aligned, wait_for_results=True)
    _, summary, _, aggregate = workflow._resolve_latency_reporting_artifacts(
        os.environ['JOB_ID'], incidents, results, start,
        end or datetime.now(timezone.utc).isoformat(), injection,
    )
    artifacts = workflow._generate_v2_report_artifacts(
        os.environ['JOB_ID'], results, summary, aggregate,
    )
    return dict(status='completed', job_id=os.environ['JOB_ID'], results=results,
                summary=artifacts.get('summary') or summary,
                report_file=artifacts['csv_filename'], report_files=artifacts.get('report_files', {}))


def main():
    logging.basicConfig(level=logging.INFO)
    output = Path(os.environ['EVAL_RESULT_DIR_PATH']) / 'ab-eval'
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'alert_bridge_evaluation_status.json'
    try:
        os.environ['CONFIG_FILE'] = str(Path(os.environ['CI_VSS_OSS_DIR']) / 'eval/configs/ab_evaluator_config.yml')
        os.environ['GT_FILE_PATH'] = os.environ['EVAL_GROUND_TRUTH_PATH']
        os.environ.setdefault('SETUP_INDEXED_AT', 'true')
        settings = Settings()
        settings.eval_mode = 'latency'
        settings.data_source = 'prometheus'
        settings.reports_dir = str(output)
        settings.prometheus_host = os.environ.get('ALERT_BRIDGE_EVALUATOR_PROMETHEUS_HOST', 'localhost')
        settings.prometheus_port = int(os.environ.get('ALERT_BRIDGE_EVALUATOR_PROMETHEUS_PORT', '9090'))
        settings.prometheus_summary_buffer_seconds = int(os.environ.get('ALERT_BRIDGE_EVALUATOR_PROMETHEUS_SUMMARY_BUFFER_SECONDS', '0'))
        settings._sync_prometheus_url_from_host_port()
        settings.kafka_bootstrap_servers = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092')
        settings.vst_base_url = os.environ.get('ALERT_BRIDGE_EVALUATOR_VST_BASE_URL') or os.environ.get('VST_BASE_URL', 'http://localhost:30888')
        ab = AlertBridgeClient(settings)
        es = ElasticsearchClient(settings)
        if settings.setup_indexed_at:
            es.setup_indexed_at_pipeline()
        if settings.auto_seed_ab:
            from alert_bridge_evaluator.lifecycle import seed_alert_types_from_gt, wait_for_alert_bridge
            if wait_for_alert_bridge(ab, timeout_seconds=settings.ab_health_timeout):
                seed_alert_types_from_gt(ab, settings.gt_file_path, max_workers=settings.seed_ab_concurrency)
        service = EvaluationService(settings, ab, es, None, PrometheusClient(settings))
        reports = ReportService(settings)
        start = end = injection = None
        if os.environ.get('SYNTHETIC_ALERTS_INJECTION', '').lower() == 'true':
            sensors = json.loads(os.environ['PERF_SENSOR_IDS'])
            injection = service.inject_synthetic_latency_incidents_over_window(
                source_video_id=os.environ['LATENCY_SOURCE_VIDEO'], sensor_ids=sensors,
                start_timestamp=datetime.now(timezone.utc).isoformat(),
                latency_duration=settings.latency_duration,
                alerts_per_minute=float(os.environ['SYNTHETIC_ALERTS_PER_MINUTE']),
                event_duration_ms=int(os.environ.get('SYNTHETIC_ALERT_EVENT_DURATION_MS', '30000')),
                category=os.environ['ALERT_CATEGORY'], submission_mode='message_bus',
            )
            start, end = injection['latency_window_start_ts'], injection['latency_window_end_ts']
            streams = injection.get('runtime_streams', [])
            mapping = {s.get('requested_sensor_id'): s for s in streams}
            incidents = [dict(video=s, timestamp=end, category=os.environ['ALERT_CATEGORY'],
                              runtime_stream=mapping.get(s)) for s in sensors]
        else:
            duration = service._parse_duration_seconds(settings.latency_duration)
            if not duration:
                raise ValueError('Invalid latency duration')
            time.sleep(duration)
            event = asyncio.run(StreamTracker(settings.vst_base_url,
                os.environ.get('NVSTREAMER_BASE_URL', 'http://localhost:31000')).run_once())
            if event is None:
                raise RuntimeError('Stream tracking failed or timed out')
            incidents = PayloadBuilder()._build_eval_payload(os.environ['JOB_ID'],
                event.data['sensor_data'], event.data['total_sensors'], event.data)['incidents']
            for inc in incidents:
                inc.update(timestamp=None, category=None)
        payload = evaluate(settings, service, reports, incidents, start=start, end=end, injection=injection)
    except Exception as exc:
        payload = dict(status='failed', job_id=os.environ.get('JOB_ID'), error=str(exc))
        target.write_text(json.dumps(payload, indent=2))
        raise
    target.write_text(json.dumps(payload, indent=2))


if __name__ == '__main__':
    main()
