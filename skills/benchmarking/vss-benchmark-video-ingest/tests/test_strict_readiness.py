# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""A partial plateau cannot stand in for both expected indexed counts."""

from http.client import IncompleteRead
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from completion import EsReadinessMonitor, UploadContext
from es_readiness import EsReadinessConfig, expected_counts, wait_for_readiness
from corpus import VideoItem
from httpio import JsonResponse, request_json
from upload import upload_one
from vss_cli import CliResult


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def observe(counts_at, *, concurrency=1, timeout=0, through_monitor=False):
    """Run the production poller against a deterministic, local ES timeline."""
    clock = Clock()
    polls = []
    config = EsReadinessConfig(poll_interval_sec=5, timeout_override_sec=timeout)

    def indexed(*args, **kwargs):
        raw, embed = counts_at(clock.now)
        polls.append((clock.now, raw, embed))
        return JsonResponse(200, {"aggregations": {
            "pipelines": {"buckets": {
                "rt_cv": {"doc_count": raw},
                "rt_embed": {"doc_count": embed},
            }},
            "last_timestamp": {"value_as_string": "2026-10-06T00:00:10Z"},
        }}, "", 0)

    with (
        patch("es_readiness.request_json", side_effect=indexed),
        patch("es_readiness.time.monotonic", side_effect=clock.monotonic),
        patch("es_readiness.time.sleep", side_effect=clock.sleep),
        patch("es_readiness.utc_now", return_value="2026-10-06T00:00:15Z"),
    ):
        if through_monitor:
            result = EsReadinessMonitor("http://es.test", 5, config=config).confirm(
                UploadContext(
                    "clip.mp4", "test-camera", "test-sensor", {}, 101.5, 10,
                    concurrency=concurrency,
                )
            )
        else:
            result = wait_for_readiness(
                config=config,
                camera_name="test-camera",
                sensor_id="test-sensor",
                duration_sec=101.5,
                fps=10,
                concurrency=concurrency,
                request_sent_at="2026-10-06T00:00:00Z",
            )
    return result, clock.now, polls


class StrictReadinessTests(unittest.TestCase):
    def test_fractional_timeout_is_not_truncated_or_skipped(self):
        for timeout in (0.5, 1.9):
            with self.subTest(timeout=timeout):
                result, elapsed, polls = observe(lambda now: (0, 0), timeout=timeout)
                self.assertFalse(result.success)
                self.assertEqual(result.drop_reason, "readiness_timeout")
                self.assertEqual(elapsed, timeout)
                self.assertEqual(len(polls), 1)
                confirmed, _, success_polls = observe(lambda now: (1000, 21), timeout=timeout)
                self.assertTrue(confirmed.success)
                self.assertEqual(len(success_polls), 1)

    def test_preserves_existing_expected_frame_tolerance(self):
        chunks, frames = expected_counts(
            duration_sec=101.5, fps=10, config=EsReadinessConfig()
        )
        self.assertEqual((chunks, frames), (21, 1000))

    def test_partial_plateau_is_unconfirmed_and_reports_observed_coverage(self):
        for raw in (950, 999):
            with self.subTest(raw=raw):
                result, elapsed, _ = observe(lambda now: (raw, 21))
                self.assertFalse(result.success)
                self.assertEqual(result.drop_reason, "pipeline_stalled")
                self.assertEqual(elapsed, 120)
                self.assertEqual(result.es_frame_count, raw)
                self.assertEqual(result.raw_completion_ratio, raw / 1000)

    def test_late_frames_after_120_seconds_are_awaited_within_scaled_budget(self):
        for concurrency in (5, 10):
            with self.subTest(concurrency=concurrency):
                result, elapsed, polls = observe(
                    lambda now: (950 if now < 125 else 1000, 21),
                    concurrency=concurrency,
                )
                self.assertTrue(result.success)
                self.assertEqual(elapsed, 125)
                self.assertIn((120, 950, 21), polls)
                self.assertEqual(result.es_frame_count, 1000)
                self.assertTrue(result.raw_complete_exact)
                self.assertFalse(result.rt_cv_dropped)

    def test_both_counts_met_complete_immediately_without_idle_wait(self):
        for raw, embed in ((1000, 21), (1015, 22)):
            with self.subTest(raw=raw, embed=embed):
                result, elapsed, polls = observe(lambda now: (raw, embed))
                self.assertTrue(result.success)
                self.assertEqual(elapsed, 0)
                self.assertEqual(len(polls), 1)
                self.assertEqual(result.completed_at, "2026-10-06T00:00:15Z")

    def test_missing_or_incomplete_pipeline_cannot_confirm(self):
        for raw, embed in ((0, 21), (1000, 0), (1000, 20)):
            with self.subTest(raw=raw, embed=embed):
                result, elapsed, _ = observe(lambda now: (raw, embed), timeout=30)
                self.assertFalse(result.success)
                self.assertLessEqual(elapsed, 30)
                self.assertEqual(result.raw_completion_ratio, raw / 1000)

    def test_full_raw_still_waits_for_late_embed_chunks(self):
        result, elapsed, _ = observe(
            lambda now: (1000, 20 if now < 125 else 21), concurrency=5
        )
        self.assertTrue(result.success)
        self.assertEqual(elapsed, 125)
        self.assertEqual(result.es_chunk_count, 21)

    def test_monitor_preserves_partial_coverage_for_failed_upload_artifacts(self):
        result, _, _ = observe(lambda now: (950, 21), through_monitor=True)
        self.assertFalse(result.confirmed)
        self.assertEqual(result.outcome, "unconfirmed")
        self.assertEqual(result.metrics["es_frame_count"], 950)
        self.assertEqual(result.metrics["expected_frames"], 1000)
        self.assertEqual(result.metrics["raw_completion_ratio"], 0.95)

    def test_hard_deadline_still_bounds_partial_stream(self):
        result, elapsed, _ = observe(lambda now: (950, 21), concurrency=10, timeout=20)
        self.assertFalse(result.success)
        self.assertEqual(result.drop_reason, "readiness_timeout")
        self.assertEqual(elapsed, 20)
        self.assertEqual(result.raw_completion_ratio, 0.95)


class HttpResponse:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.body).encode("utf-8")


def source_video():
    return VideoItem(
        "clip", "custom", "/tmp/clip.mp4", 50_000_000, 101.5, 10,
        1920, 1080, "1920x1080", "h264", "mp4", "video/mp4",
    )


class ClientObservationTests(unittest.TestCase):
    def test_transient_es_failure_recovers_without_reupload_and_uses_client_time(self):
        self.assert_transient_read_recovers(urllib.error.URLError("temporary"))

    def test_truncated_success_body_recovers_without_reupload(self):
        response = HttpResponse({})
        response.read = Mock(side_effect=IncompleteRead(b'{"agg', 200))
        self.assert_transient_read_recovers(response)

    def test_truncated_http_error_body_recovers_without_reupload(self):
        body = Mock(read=Mock(side_effect=IncompleteRead(b'{"err', 200)))
        response = urllib.error.HTTPError("http://es.test", 503, "unavailable", {}, body)
        self.assert_transient_read_recovers(response)

    def assert_transient_read_recovers(self, first_response):
        clock = Clock()
        client_start, client_end = "2026-10-06T00:00:00Z", "2026-10-06T00:00:05Z"
        # Even an unexpected server timestamp and chunk-count field cannot
        # replace the caller's completion clock or measured chunk expectation.
        cli = Mock(call=Mock(return_value=CliResult(0, {
            "added": True, "type": "video", "sensor_id": "returned-uuid", "chunks_processed": 99999,
        })))
        response = HttpResponse({"aggregations": {
            "pipelines": {"buckets": {
                "rt_cv": {"doc_count": 1000}, "rt_embed": {"doc_count": 21},
            }},
            "last_timestamp": {"value_as_string": "2100-01-01T00:00:00Z"},
        }})
        monitor = EsReadinessMonitor("http://es.test", 5, config=EsReadinessConfig(timeout_override_sec=20))
        with (
            patch("httpio.urllib.request.urlopen", side_effect=[first_response, response]) as read,
            patch("es_readiness.time.monotonic", side_effect=clock.monotonic),
            patch("es_readiness.time.sleep", side_effect=clock.sleep),
            patch("es_readiness.utc_now", side_effect=[client_start, client_end]),
            patch("upload.utc_now", side_effect=[client_start, client_start, client_end]),
        ):
            record = upload_one(
                source_video(), worker_index=1, upload_sequence=1, concurrency=1,
                run_uuid="a" * 32, cli=cli, readiness_monitor=monitor,
            )
        self.assertEqual(record.outcome, "confirmed")
        self.assertEqual(record.request_sent_at, client_start)
        self.assertEqual(record.ingest_confirmed_at, client_end)
        self.assertEqual(record.latency_sec, 5)
        self.assertEqual(record.expected_chunks, 21)
        self.assertEqual(record.readiness_polls, 2)
        self.assertEqual(read.call_count, 2)
        cli.call.assert_called_once()
        self.assertNotIn("ingested_at", read.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("http_status", record.as_row())
        self.assertNotIn("es_indexed_latency_sec", record.as_row())

    def test_persistent_network_errors_stop_at_readiness_deadline(self):
        clock = Clock()
        with (
            patch("httpio.urllib.request.urlopen", side_effect=TimeoutError("slow ES")) as read,
            patch("es_readiness.time.monotonic", side_effect=clock.monotonic),
            patch("es_readiness.time.sleep", side_effect=clock.sleep),
        ):
            result = wait_for_readiness(
                config=EsReadinessConfig(poll_interval_sec=5, timeout_override_sec=12),
                camera_name="camera", sensor_id="uuid", duration_sec=101.5, fps=10,
            )
        self.assertFalse(result.success)
        self.assertEqual(result.drop_reason, "readiness_timeout")
        self.assertIn("ES transport request failed", result.error)
        self.assertEqual(clock.now, 12)
        self.assertEqual(read.call_count, 3)
        self.assertEqual([call.kwargs["timeout"] for call in read.call_args_list], [12, 7, 2])

    def test_only_exact_canonical_cli_sensor_id_can_start_readiness(self):
        cases = [
            {"sensorId": "other"}, {"vst_sensor_id": "other"}, {"id": "other"},
            {"sensor_id": " padded "}, {"sensor_id": ""}, {"sensor_id": None}, {"sensor_id": 123},
        ]
        for identity in cases:
            with self.subTest(identity=identity):
                cli = Mock(call=Mock(return_value=CliResult(0, {"added": True, "type": "video", **identity})))
                monitor = Mock()
                record = upload_one(
                    source_video(), worker_index=1, upload_sequence=1, concurrency=1,
                    run_uuid="a" * 32, cli=cli, readiness_monitor=monitor,
                )
                self.assertEqual(record.outcome, "unconfirmed")
                self.assertEqual(record.sensor_id, "")
                monitor.confirm.assert_not_called()
                cli.call.assert_called_once()


class HttpTransportTests(unittest.TestCase):
    def test_transport_errors_become_status_zero_without_an_internal_retry(self):
        for failure in (urllib.error.URLError("temporary DNS error"), TimeoutError("slow"), ConnectionResetError("reset")):
            with self.subTest(failure=type(failure).__name__):
                with patch("httpio.urllib.request.urlopen", side_effect=failure) as read:
                    result = request_json("http://es.test")
                self.assertEqual(result.status, 0)
                self.assertIsNone(result.body)
                read.assert_called_once()

    def test_error_response_keeps_http_status_and_parsed_body(self):
        failure = urllib.error.HTTPError(
            "http://es.test", 503, "unavailable", {}, io.BytesIO(b'{"error": "busy"}')
        )
        with patch("httpio.urllib.request.urlopen", side_effect=failure):
            result = request_json("http://es.test")
        self.assertEqual(result.status, 503)
        self.assertEqual(result.body, {"error": "busy"})

    def test_timeout_while_reading_a_response_is_also_a_transport_failure(self):
        response = HttpResponse({})
        with (
            patch("httpio.urllib.request.urlopen", return_value=response),
            patch.object(response, "read", side_effect=TimeoutError("response interrupted")),
        ):
            self.assertEqual(request_json("http://es.test").status, 0)


if __name__ == "__main__":
    unittest.main()
