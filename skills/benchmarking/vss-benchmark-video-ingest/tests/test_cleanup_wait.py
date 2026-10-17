# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Virtual-clock checks for delayed cleanup, reappearing documents and partial reads."""

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cleanup import wait_for_cleanup
from es_readiness import EsReadinessConfig
from httpio import JsonResponse
from upload import delete_asset
from vss_cli import CliResult


def search_response(raw=0, embed=0):
    return JsonResponse(200, {
        "timed_out": False,
        "_shards": {"total": 2, "successful": 2, "skipped": 0, "failed": 0},
        "hits": {"total": {"value": raw + embed, "relation": "eq"}},
        "aggregations": {"pipelines": {"buckets": {
            "rt_cv": {"doc_count": raw}, "rt_embed": {"doc_count": embed},
        }}},
    }, "", 0)


class VirtualClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


class CleanupWaitTests(unittest.TestCase):
    def setUp(self):
        self.config = EsReadinessConfig(
            elasticsearch_url="http://es.test", poll_interval_sec=5, request_timeout_sec=20,
        )
        self.targets = [("point-00001", "sensor-1"), ("point-00002", "sensor-2")]

    def wait(self, responses, *, timeout=30, settle=10, targets=None, request_delay=0):
        clock = VirtualClock()
        sequence = iter(responses)

        def request(*args, **kwargs):
            clock.now += request_delay
            response = next(sequence)
            if isinstance(response, Exception):
                raise response
            return response

        with (patch("cleanup.time.monotonic", side_effect=clock.monotonic),
              patch("cleanup.time.sleep", side_effect=clock.sleep),
              patch("cleanup.request_json", side_effect=request) as read):
            result = wait_for_cleanup(
                self.config, self.targets if targets is None else targets,
                timeout_sec=timeout, settle_sec=settle,
            )
        return result, read, clock

    def test_waits_for_both_raw_and_embed_then_consecutive_zero_observations(self):
        result, read, _ = self.wait([
            search_response(9, 4), search_response(0, 1),
            search_response(), search_response(), search_response(),
        ])
        self.assertTrue(result.success)
        self.assertEqual((result.polls, result.elapsed_sec), (5, 20))
        self.assertEqual(read.call_count, 5)

    def test_reappearing_documents_reset_settling_interval(self):
        result, _, _ = self.wait([
            search_response(), search_response(), search_response(1, 0),
            search_response(), search_response(), search_response(),
        ])
        self.assertTrue(result.success)
        self.assertEqual((result.polls, result.elapsed_sec), (6, 25))

    def test_each_poll_checks_whole_batch_with_exact_separate_pipeline_identities(self):
        result, read, _ = self.wait([search_response()] * 3, targets=self.targets + [self.targets[0]])
        self.assertTrue(result.success)
        args, kwargs = read.call_args
        self.assertEqual(args[0], "http://es.test/mdx-raw-2025-01-01,mdx-embed-filtered-2025-01-01/_search?ignore_unavailable=false&allow_no_indices=false")
        self.assertEqual(kwargs["method"], "POST")
        payload = kwargs["payload"]
        self.assertEqual(payload["size"], 0)
        terms = [clause["bool"]["filter"][1]["terms"] for clause in payload["query"]["bool"]["should"]]
        self.assertEqual(terms, [
            {"sensorId.keyword": ["point-00001", "point-00002"]},
            {"sensor.id.keyword": ["sensor-1", "sensor-2"]},
        ])
        self.assertEqual(read.call_count, 3)  # one request per poll, not one per target

    def test_never_settling_times_out_without_success_and_clamps_http_budget(self):
        result, read, clock = self.wait([search_response(1, 0)] * 3, timeout=12, settle=5)
        self.assertFalse(result.success)
        self.assertEqual((result.polls, result.elapsed_sec), (3, 12))
        self.assertEqual([call.kwargs["timeout_sec"] for call in read.call_args_list], [12, 7, 2])
        self.assertEqual(clock.sleeps, [5, 5, 2])
        self.assertIn("raw=1, embed=0", result.detail)

    def test_response_arriving_at_deadline_cannot_confirm_cleanup(self):
        result, read, _ = self.wait([search_response()] * 2, timeout=15, settle=1, request_delay=5)
        self.assertFalse(result.success)
        self.assertEqual(result.elapsed_sec, 15)
        self.assertEqual(read.call_count, 2)

    def test_zero_shards_cannot_confirm_cleanup_with_or_without_buckets(self):
        for with_buckets in (False, True):
            with self.subTest(with_buckets=with_buckets):
                missing = search_response()
                missing.body["_shards"] = {"total": 0, "successful": 0, "skipped": 0, "failed": 0}
                if not with_buckets:
                    missing.body.pop("aggregations")
                result, read, clock = self.wait([missing])
                self.assertFalse(result.success)
                self.assertIn("resolved no shards", result.detail)
                self.assertEqual(read.call_count, 1)
                self.assertEqual(clock.sleeps, [])

    def test_read_errors_fail_closed_without_retry(self):
        for response in [JsonResponse(503, {}, "unavailable", 0), JsonResponse(404, {}, "missing", 0), OSError("connection refused")]:
            with self.subTest(response=response):
                result, read, clock = self.wait([response])
                self.assertFalse(result.success)
                self.assertEqual(read.call_count, 1)
                self.assertEqual(clock.sleeps, [])

    def test_incomplete_or_malformed_search_cannot_be_misread_as_absence(self):
        cases = []
        for field, value in [("timed_out", True), ("timed_out", None), ("terminated_early", True),
                             ("error", {"type": "search_phase_execution_exception"}),
                             ("_shards", None), ("aggregations", None), ("hits", None)]:
            response = search_response()
            response.body[field] = value
            cases.append(response)
        for field, value in [("failed", 1), ("successful", 1), ("total", -1), ("total", True),
                             ("failures", [{"reason": "unavailable"}]), ("skipped", 3)]:
            response = search_response()
            response.body["_shards"][field] = value
            cases.append(response)
        for count in [None, -1, True, "0", 0.5]:
            response = search_response()
            response.body["aggregations"]["pipelines"]["buckets"]["rt_cv"]["doc_count"] = count
            cases.append(response)
        response = search_response()
        response.body["aggregations"]["pipelines"]["buckets"].pop("rt_embed")
        cases.append(response)
        for total in [{"value": 0, "relation": "gte"}, {"value": -1, "relation": "eq"}, {"value": 2, "relation": "eq"}]:
            response = search_response()
            response.body["hits"]["total"] = total
            cases.append(response)
        cases.append(JsonResponse(200, [], "[]", 0))
        for response in cases:
            with self.subTest(body=response.body):
                result, read, _ = self.wait([response])
                self.assertFalse(result.success)
                self.assertEqual(read.call_count, 1)

    def test_empty_batch_does_not_query_es(self):
        result, read, _ = self.wait([], targets=[])
        self.assertTrue(result.success)
        self.assertEqual(result.polls, 0)
        read.assert_not_called()

    def test_invalid_identity_or_timing_is_rejected_before_any_read(self):
        for targets in [[("camera", "")], [(" camera", "uuid")], [("camera", None)], ["camera"], [("camera",)]]:
            with self.subTest(targets=targets):
                result, read, _ = self.wait([], targets=targets)
                self.assertFalse(result.success)
                read.assert_not_called()
        for timeout, settle in [(0, 10), (float("nan"), 10), (float("inf"), 10), (10, 10), (5, 10), (30, 0)]:
            with self.subTest(timeout=timeout, settle=settle):
                result, read, _ = self.wait([], timeout=timeout, settle=settle)
                self.assertFalse(result.success)
                read.assert_not_called()
        for name in ("poll_interval_sec", "request_timeout_sec"):
            original = getattr(self.config, name)
            for value in (0, -1, float("nan"), float("inf")):
                setattr(self.config, name, value)
                self.assertFalse(self.wait([])[0].success)
            setattr(self.config, name, original)


class DeleteConfirmationTests(unittest.TestCase):
    def test_only_confirmed_storage_removal_counts_as_cleanup(self):
        for body in [
            {}, {"confirmed": False, "recordings": "unconfirmed"},
            {"confirmed": True, "recordings": "kept"}, {"confirmed": True, "recordings": "none"},
            {"confirmed": "true", "recordings": "removed"},
        ]:
            with self.subTest(body=body):
                cli = Mock(call=Mock(return_value=CliResult(0, body, "VIOS diagnostic")))
                ok, detail = delete_asset(cli, "owned-uuid")
                self.assertFalse(ok)
                self.assertIn("VIOS diagnostic", detail)
                cli.call.assert_called_once_with("vios", "delete", "--type", "video", "--sensor", "owned-uuid")
        cli = Mock(call=Mock(return_value=CliResult(0, {"confirmed": True, "recordings": "removed"})))
        self.assertTrue(delete_asset(cli, "owned-uuid")[0])

    def test_cli_failure_retains_diagnostic_and_does_not_retry(self):
        cli = Mock(call=Mock(return_value=CliResult(7, {}, "VIOS delete timed out")))
        self.assertEqual(delete_asset(cli, "owned-uuid"), (False, "VIOS delete timed out"))
        cli.call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
