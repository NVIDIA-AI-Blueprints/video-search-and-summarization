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

"""Unit tests for the error-handling / exit-code behavior of the
``test/latency/prometheus_latency.py`` reporting script.

Locks down the contracts established by review comments C17, C18, C19,
and C20:

  * C17 — ``prom_query`` raises :class:`PromQueryError` on transport-level
    failures instead of calling ``sys.exit(1)`` from inside a low-level
    helper. A single transient hiccup on one of ~30 PromQL queries must
    not kill the entire report.
  * C18 — ``run_prometheus`` and ``run_elasticsearch`` return ``bool`` so
    ``main()`` can decide the process exit code after every section has
    had a chance to run. Exit code 3 signals "report may be incomplete";
    exit code 0 covers both "happy path" and "endpoint healthy, window
    empty".
  * C19 — the Verification-failures table always prints, even when every
    counter is zero. Downstream automation parsing the output expects a
    stable section shape regardless of whether the window had failures.
  * C20 — the Elasticsearch latency report understands Alert Bridge's
    ``info.latency`` contract: a JSON string containing camelCase keys.
    Prior versions queried nested latency fields directly and silently
    returned zero ES hits.

Run with: pytest test/latency/test_prometheus_latency_script.py -v
"""

import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


# The script lives under ``test/latency/`` and is intended to be executed
# directly, not imported as a package. Put that directory on sys.path so
# we can load it as a module.
_SCRIPT_DIR = os.path.join(os.path.dirname(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# ``requests`` is a hard runtime dep of the script itself — if the test
# environment doesn't have it, the script import would fail with a
# ``sys.exit(1)`` at module load time. Skip the whole file in that case
# rather than blowing up collection.
pytest.importorskip("requests")

prometheus_latency = importlib.import_module("prometheus_latency")


# ── C17: PromQueryError + prom_query ─────────────────────────────────────


class TestPromQueryRaisesPromQueryError:
    """``prom_query`` must raise a typed exception on transport failure
    so callers can distinguish "the endpoint is unreachable" from "the
    endpoint replied with no samples for this query"."""

    def test_raises_on_connection_error(self):
        import requests  # noqa: F401  (imported for the exception class)

        with patch.object(prometheus_latency.requests, "get",
                          side_effect=prometheus_latency.requests.ConnectionError("refused")):
            with pytest.raises(prometheus_latency.PromQueryError) as excinfo:
                prometheus_latency.prom_query("http://fake", "up")
            assert "Prometheus request failed" in str(excinfo.value)

    def test_raises_on_timeout(self):
        with patch.object(prometheus_latency.requests, "get",
                          side_effect=prometheus_latency.requests.Timeout("slow")):
            with pytest.raises(prometheus_latency.PromQueryError):
                prometheus_latency.prom_query("http://fake", "up")

    def test_raises_on_non_2xx_response(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = prometheus_latency.requests.HTTPError("500")
        with patch.object(prometheus_latency.requests, "get", return_value=mock_response):
            with pytest.raises(prometheus_latency.PromQueryError):
                prometheus_latency.prom_query("http://fake", "up")

    def test_chains_original_exception(self):
        """The typed error should preserve the underlying cause via
        ``raise ... from e`` so debuggers / loggers see the full chain."""
        original = prometheus_latency.requests.ConnectionError("refused")
        with patch.object(prometheus_latency.requests, "get", side_effect=original):
            try:
                prometheus_latency.prom_query("http://fake", "up")
            except prometheus_latency.PromQueryError as exc:
                assert exc.__cause__ is original


class TestPromQueryReturnValues:
    """Successful PromQL queries return ``float`` on hit, ``None`` on
    empty result. Neither path should raise."""

    def test_empty_result_returns_none(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"status": "success", "data": {"result": []}}
        with patch.object(prometheus_latency.requests, "get", return_value=mock_response):
            assert prometheus_latency.prom_query("http://fake", "up") is None

    def test_single_result_returns_float(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "status": "success",
            "data": {"result": [{"metric": {}, "value": [1234567890, "42.5"]}]},
        }
        with patch.object(prometheus_latency.requests, "get", return_value=mock_response):
            assert prometheus_latency.prom_query("http://fake", "up") == 42.5

    def test_malformed_value_returns_none(self):
        """Preserves the existing "graceful degradation" behavior for
        malformed PromQL responses — those should not crash the report,
        only produce a missing row."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "status": "success",
            "data": {"result": [{"value": ["timestamp-not-a-float"]}]},
        }
        with patch.object(prometheus_latency.requests, "get", return_value=mock_response):
            assert prometheus_latency.prom_query("http://fake", "up") is None


# ── C18: run_prometheus return value ─────────────────────────────────────


class TestRunPrometheusReturnsBool:
    def test_returns_false_when_health_check_fails(self):
        # Health check is the first thing ``run_prometheus`` does; if it
        # fails, no query should even be attempted.
        with patch.object(prometheus_latency.requests, "get",
                          side_effect=prometheus_latency.requests.ConnectionError("refused")):
            result = prometheus_latency.run_prometheus("http://fake", "1h", "now")
        assert result is False

    def test_returns_false_when_a_query_raises(self):
        """PromQueryError raised mid-report must bubble up to `main()` as
        a False return so the exit code can reflect the partial failure."""
        # Health check succeeds; the first PromQL query raises.
        calls = {"n": 0}

        def fake_get(url, **kwargs):
            calls["n"] += 1
            mock = MagicMock()
            mock.raise_for_status.return_value = None
            if url.endswith("/-/healthy"):
                mock.json.return_value = {}
                return mock
            # All instant queries raise ConnectionError
            raise prometheus_latency.requests.ConnectionError("refused mid-report")

        with patch.object(prometheus_latency.requests, "get", side_effect=fake_get):
            result = prometheus_latency.run_prometheus("http://fake", "1h", "now")

        assert result is False
        assert calls["n"] >= 2  # at least the health check + one query attempt

    def test_returns_true_on_empty_window(self):
        """Endpoint healthy, every query returns an empty result set —
        a legitimate "nothing happened in the window" outcome that must
        NOT be flagged as an endpoint failure."""
        def fake_get(url, **kwargs):
            mock = MagicMock()
            mock.raise_for_status.return_value = None
            mock.status_code = 200
            mock.json.return_value = {"status": "success", "data": {"result": []}}
            return mock

        with patch.object(prometheus_latency.requests, "get", side_effect=fake_get):
            result = prometheus_latency.run_prometheus("http://fake", "1h", "now")

        assert result is True

    def test_returns_true_on_populated_window(self):
        def fake_get(url, **kwargs):
            mock = MagicMock()
            mock.raise_for_status.return_value = None
            mock.status_code = 200
            if url.endswith("/-/healthy"):
                mock.json.return_value = {}
            else:
                mock.json.return_value = {
                    "status": "success",
                    "data": {"result": [{"metric": {}, "value": [0, "1.0"]}]},
                }
            return mock

        with patch.object(prometheus_latency.requests, "get", side_effect=fake_get):
            result = prometheus_latency.run_prometheus("http://fake", "1h", "now")

        assert result is True


# ── C18: run_elasticsearch return value ──────────────────────────────────


class TestRunElasticsearchReturnsBool:
    def test_returns_false_on_connection_error(self):
        with patch.object(prometheus_latency.requests, "post",
                          side_effect=prometheus_latency.requests.ConnectionError("refused")):
            result = prometheus_latency.run_elasticsearch("es", 9200, "1h", "now")
        assert result is False

    def test_returns_false_on_non_200_response(self):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "ES internal error"
        with patch.object(prometheus_latency.requests, "post", return_value=mock_response):
            result = prometheus_latency.run_elasticsearch("es", 9200, "1h", "now")
        assert result is False

    def test_returns_false_on_error_payload(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": {"type": "parsing_exception"}}
        with patch.object(prometheus_latency.requests, "post", return_value=mock_response):
            result = prometheus_latency.run_elasticsearch("es", 9200, "1h", "now")
        assert result is False

    def test_returns_true_on_empty_hits(self):
        """Endpoint healthy, window has no matching documents — this is a
        clean run, not a failure. Exit code must stay 0."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"hits": {"hits": []}}
        with patch.object(prometheus_latency.requests, "post", return_value=mock_response):
            result = prometheus_latency.run_elasticsearch("es", 9200, "1h", "now")
        assert result is True


# ── C18: main() exit code aggregation ────────────────────────────────────


class TestMainExitCode:
    def _run_main(self, argv, prom_result=True, es_result=True):
        with patch.object(sys, "argv", argv), \
             patch.object(prometheus_latency, "run_prometheus", return_value=prom_result), \
             patch.object(prometheus_latency, "run_elasticsearch", return_value=es_result):
            try:
                prometheus_latency.main()
                return 0
            except SystemExit as e:
                return e.code

    def test_exits_zero_when_both_sections_succeed(self):
        assert self._run_main(
            ["prometheus_latency.py", "1h", "localhost", "--es-host", "eshost"],
            prom_result=True, es_result=True,
        ) == 0

    def test_exits_zero_when_no_es_host_and_prom_ok(self):
        assert self._run_main(
            ["prometheus_latency.py", "1h", "localhost"],
            prom_result=True,
        ) == 0

    def test_exits_three_when_prometheus_fails(self):
        assert self._run_main(
            ["prometheus_latency.py", "1h", "localhost"],
            prom_result=False,
        ) == 3

    def test_exits_three_when_elasticsearch_fails(self):
        assert self._run_main(
            ["prometheus_latency.py", "1h", "localhost", "--es-host", "eshost"],
            prom_result=True, es_result=False,
        ) == 3

    def test_exits_three_when_both_fail(self):
        assert self._run_main(
            ["prometheus_latency.py", "1h", "localhost", "--es-host", "eshost"],
            prom_result=False, es_result=False,
        ) == 3

    def test_exits_two_on_bad_duration(self):
        """Pre-existing CLI validation contract — bad duration format
        exits with code 2, not 3. Locked down so C18 refactors can't
        accidentally collapse the two error categories."""
        with patch.object(sys, "argv",
                          ["prometheus_latency.py", "not-a-duration", "localhost"]):
            with pytest.raises(SystemExit) as excinfo:
                prometheus_latency.main()
        assert excinfo.value.code == 2


class TestElasticsearchStillRunsAfterPrometheusFails:
    """Regression test for the original C17 complaint: a failing
    Prometheus section must NOT short-circuit the ES section. Both
    sections get a chance to produce output; only the exit code
    reflects the combined status."""

    def test_both_sections_called_even_on_prom_failure(self):
        with patch.object(sys, "argv",
                          ["prometheus_latency.py", "1h", "localhost", "--es-host", "eshost"]), \
             patch.object(prometheus_latency, "run_prometheus", return_value=False) as mock_prom, \
             patch.object(prometheus_latency, "run_elasticsearch", return_value=True) as mock_es:
            try:
                prometheus_latency.main()
            except SystemExit:
                pass  # expected: exit 3
        assert mock_prom.called, "run_prometheus should have been invoked"
        assert mock_es.called, "run_elasticsearch must still run after Prometheus fails"


# ── C19: failure section always prints ───────────────────────────────────


class TestFailureSectionAlwaysPrints:
    """The Verification-failures table is part of the script's stable
    output contract. Before C19 it was silently omitted when every
    counter was zero, making "window was clean" indistinguishable from
    "the script broke" on the consumer side."""

    def _run_with_all_zeros(self, capsys):
        """Run ``run_prometheus`` against a fake Prometheus that
        returns zero for every counter query. Returns captured stdout."""
        def fake_get(url, **kwargs):
            mock = MagicMock()
            mock.raise_for_status.return_value = None
            mock.status_code = 200
            mock.json.return_value = {"status": "success", "data": {"result": []}}
            return mock

        with patch.object(prometheus_latency.requests, "get", side_effect=fake_get):
            ok = prometheus_latency.run_prometheus("http://fake", "1h", "now")
        assert ok is True
        return capsys.readouterr().out

    def test_failure_header_present_when_window_is_clean(self, capsys):
        """Even with zero failures the ``Verification failures`` header
        must appear, showing ``[total: 0]``. Downstream automation
        uses this line as an anchor."""
        output = self._run_with_all_zeros(capsys)
        assert "Verification failures" in output
        assert "[total: 0]" in output

    def test_every_reason_row_emitted_when_all_zero(self, capsys):
        """Every reason row must be present in the table even when its
        count is zero. Parsers rely on a stable set of rows; silently
        omitting rows for inactive reasons breaks column layout."""
        output = self._run_with_all_zeros(capsys)
        expected_reasons = [
            "vst_timeout", "vst_overloaded", "vst_not_found", "vst_unavailable",
            "vst_client_error", "vst_server_error", "vst_unknown",
            "url_validation", "vlm_parse_failure", "vlm_timeout",
            "vlm_connection_error", "vlm_server_error", "vlm_invalid_payload",
            "no_prompt", "unknown",
        ]
        for reason in expected_reasons:
            assert reason in output, f"row for {reason!r} missing from output"

    def test_percentage_is_na_when_no_failures(self, capsys):
        """``100 * count / total_failures`` would divide by zero when
        the window is clean. The script must print ``N/A`` instead."""
        output = self._run_with_all_zeros(capsys)
        # At least one row should carry the N/A marker (every row
        # does, actually) — asserting once is enough to lock the
        # zero-division guard down.
        assert "N/A" in output


# ── C20: ES latency payload contract ─────────────────────────────────────


class TestElasticsearchLatencyPayloadContract:
    """The ES writer in ``enhance_alert_with_vlm.py`` stamps keys like
    ``kafkaPublishedAt`` / ``vlmRequest`` / ``getVideoStreamUrlWithOverlay``
    into a JSON string stored at ``info.latency``. Querying nested
    ``info.latency.*`` fields misses those documents because ES sees
    ``info.latency`` as a string field."""

    @pytest.fixture
    def captured_body(self):
        """Run ``run_elasticsearch`` against a mocked ES endpoint and
        capture the exact JSON body the script sent."""
        captured = {}

        def fake_post(url, data=None, **kwargs):
            import json as _json
            captured["body"] = _json.loads(data) if data else {}
            mock = MagicMock()
            mock.status_code = 200
            mock.json.return_value = {"hits": {"hits": []}}
            return mock

        with patch.object(prometheus_latency.requests, "post", side_effect=fake_post):
            prometheus_latency.run_elasticsearch("es", 9200, "1h", "now")
        return captured["body"]

    def test_exists_filter_uses_latency_parent_field(self, captured_body):
        """Real 0.0.61 documents require parent-field selection."""
        filters = captured_body["query"]["bool"]["filter"]
        exists_fields = {
            f["exists"]["field"]
            for f in filters
            if "exists" in f
        }
        assert exists_fields == {"info.latency"}

    def test_no_nested_latency_fields_remain(self, captured_body):
        """Nested latency paths do not match JSON-string payloads."""
        body_str = str(captured_body)
        forbidden = [
            "info.latency.vlmRequest.duration",
            "info.latency.timestamps.kafkaPublishedAt",
            "info.latency.getVideoStreamUrlWithOverlay.duration",
            "vlm_request",
            "kafka_published_at",
            "kafka_consumed_at",
            "get_video_stream_url_with_overlay",
        ]
        for bad in forbidden:
            assert bad not in body_str, (
                f"latency field {bad!r} still present in ES query body; "
                "the report must fetch and parse the parent info.latency field"
            )

    def test_source_list_fetches_latency_payload(self, captured_body):
        """The full payload is needed because parsing happens client-side."""
        source_fields = set(captured_body["_source"])
        assert "info.latency" in source_fields
        assert "info.indexedAt" in source_fields
        # Explicit negative assertion — the old ``indexed_at`` top-level
        # field is no longer in the projection.
        assert "indexed_at" not in source_fields

    def test_parse_loop_reads_json_string_camelcase_payload(self, capsys, monkeypatch, tmp_path):
        """Exercise a synthetic ES hit shaped like Alert Bridge 0.0.61."""
        monkeypatch.chdir(tmp_path)
        hit = {
            "_source": {
                "end": "2025-01-01T00:00:00Z",
                "sensorId": "cam-1",
                "info": {
                    "indexedAt": "2025-01-01T00:00:10Z",
                    "latency": json.dumps({
                        "timestamps": {
                            "kafkaPublishedAt": "2025-01-01T00:00:01Z",
                            "kafkaConsumedAt":  "2025-01-01T00:00:02Z",
                        },
                        "vlmRequest": {"duration": 1.5},
                        "getVideoStreamUrlWithOverlay": {"duration": 0.5},
                    }, separators=(",", ":")),
                },
            }
        }

        def fake_post(url, data=None, **kwargs):
            mock = MagicMock()
            mock.status_code = 200
            mock.json.return_value = {"hits": {"hits": [hit]}}
            return mock

        with patch.object(prometheus_latency.requests, "post", side_effect=fake_post):
            ok = prometheus_latency.run_elasticsearch("es", 9200, "1h", "now")

        assert ok is True
        output = capsys.readouterr().out
        assert "usable=1" in output
        assert "VLM Inference" in output

    def test_parse_loop_keeps_nested_camelcase_backcompat(self, capsys, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        hit = {
            "_source": {
                "end": "2025-01-01T00:00:00Z",
                "sensorId": "cam-1",
                "info": {
                    "indexedAt": "2025-01-01T00:00:10Z",
                    "latency": {
                        "timestamps": {
                            "kafkaPublishedAt": "2025-01-01T00:00:01Z",
                            "kafkaConsumedAt": "2025-01-01T00:00:02Z",
                        },
                        "vlmRequest": {"duration": 1.5},
                        "getVideoStreamUrlWithOverlay": {"duration": 0.5},
                    },
                },
            }
        }

        def fake_post(url, data=None, **kwargs):
            mock = MagicMock()
            mock.status_code = 200
            mock.json.return_value = {"hits": {"hits": [hit]}}
            return mock

        with patch.object(prometheus_latency.requests, "post", side_effect=fake_post):
            ok = prometheus_latency.run_elasticsearch("es", 9200, "1h", "now")

        assert ok is True
        assert "usable=1" in capsys.readouterr().out

    def test_parse_loop_keeps_legacy_snakecase_backcompat(self, capsys, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        hit = {
            "_source": {
                "end": "2025-01-01T00:00:00Z",
                "sensorId": "cam-1",
                "info": {
                    "indexedAt": "2025-01-01T00:00:10Z",
                    "latency": {
                        "timestamps": {
                            "kafka_published_at": "2025-01-01T00:00:01Z",
                            "kafka_consumed_at": "2025-01-01T00:00:02Z",
                        },
                        "vlm_request": {"duration": 1.5},
                        "get_video_stream_url_with_overlay": {"duration": 0.5},
                    },
                },
            }
        }

        def fake_post(url, data=None, **kwargs):
            mock = MagicMock()
            mock.status_code = 200
            mock.json.return_value = {"hits": {"hits": [hit]}}
            return mock

        with patch.object(prometheus_latency.requests, "post", side_effect=fake_post):
            ok = prometheus_latency.run_elasticsearch("es", 9200, "1h", "now")

        assert ok is True
        assert "usable=1" in capsys.readouterr().out

    def test_malformed_latency_json_is_skipped(self, capsys):
        hit = {
            "_source": {
                "end": "2025-01-01T00:00:00Z",
                "sensorId": "cam-1",
                "info": {
                    "indexedAt": "2025-01-01T00:00:10Z",
                    "latency": "{\"timestamps\":",
                },
            }
        }

        def fake_post(url, data=None, **kwargs):
            mock = MagicMock()
            mock.status_code = 200
            mock.json.return_value = {"hits": {"hits": [hit]}}
            return mock

        with patch.object(prometheus_latency.requests, "post", side_effect=fake_post):
            ok = prometheus_latency.run_elasticsearch("es", 9200, "1h", "now")

        assert ok is True
        captured = capsys.readouterr()
        assert "No usable rows" in captured.out
        assert "JSONDecodeError" in captured.err


# ── C21 `--sensor-id` filter tests ────────────────────────────────────────


class TestPromqlEscape:
    """The ``_promql_escape`` helper is security-sensitive — a sensor
    ID containing a quote or backslash must NOT be able to inject
    arbitrary PromQL. These tests pin the escaping rules so a future
    refactor can't silently regress."""

    def test_plain_id_passthrough(self):
        assert prometheus_latency._promql_escape("cam-42") == "cam-42"

    def test_double_quote_escaped(self):
        assert prometheus_latency._promql_escape('bad"id') == 'bad\\"id'

    def test_backslash_escaped(self):
        assert prometheus_latency._promql_escape("bad\\id") == "bad\\\\id"

    def test_backslash_and_quote_both_escaped(self):
        # Order matters: escape backslash first, else ``\"`` would
        # become ``\\"`` (a legitimate escaped quote) after the
        # backslash pass, corrupting the intent.
        assert prometheus_latency._promql_escape('a\\b"c') == 'a\\\\b\\"c'

    def test_unicode_passthrough(self):
        # Non-ASCII characters are legal in PromQL label values —
        # no escaping needed.
        assert prometheus_latency._promql_escape("камера-1") == "камера-1"


class TestPerSensorBreakdown:
    """The all-sensors view must query the actual counter sample names
    emitted by prometheus_client. Counter names ending in ``_total`` are
    especially easy to get wrong here, so lock the PromQL down directly."""

    def _run(self, monkeypatch, fake_result_values=None):
        captured = []
        values = iter(fake_result_values or [])

        def _fake(base_url, promql):
            captured.append(promql)
            try:
                return next(values)
            except StopIteration:
                return []

        monkeypatch.setattr(prometheus_latency, "_prom_range_query", _fake)
        prometheus_latency.run_per_sensor_breakdown("http://fake", "1h")
        return captured

    def test_queries_use_exposed_counter_names(self, monkeypatch):
        queries = self._run(monkeypatch)
        joined = "\n".join(queries)

        assert "alert_bridge_events_by_sensor_total" in joined
        assert "alert_bridge_events_dropped_by_sensor_total" in joined

        legacy_names = (
            "alert_bridge_events_total_by_sensor",
            "alert_bridge_events_dropped_total_by_sensor",
        )
        for legacy in legacy_names:
            assert legacy not in joined, f"legacy metric name still queried: {legacy}"

    def test_merges_rows_from_each_counter_by_sensor(self, monkeypatch, capsys):
        self._run(
            monkeypatch,
            fake_result_values=[
                [{"metric": {"sensorId": "cam-1"}, "value": [0, "2"]}],
                [{"metric": {"sensorId": "cam-1"}, "value": [0, "1"]}],
                [{"metric": {"sensorId": "cam-2"}, "value": [0, "3"]}],
                [{"metric": {"sensorId": "cam-2"}, "value": [0, "4"]}],
            ],
        )

        output = capsys.readouterr().out
        assert "cam-1" in output
        assert "cam-2" in output
        assert "confirmed" in output
        assert "dropped" in output


class TestSingleSensorBreakdown:
    """Legacy detailed counter-only single-sensor helper.

    We mock ``_prom_range_query`` to capture the exact PromQL emitted
    so we can assert the label filter is present and escaped."""

    def _run(self, monkeypatch, sensor_id, fake_result_values=None):
        """Drive the function with a mocked ``_prom_range_query`` that
        returns ``fake_result_values`` for every query (default: zero
        results). Returns the list of queries actually sent."""
        captured = []
        values = iter(fake_result_values or [])

        def _fake(base_url, promql):
            captured.append(promql)
            try:
                return next(values)
            except StopIteration:
                return []

        monkeypatch.setattr(prometheus_latency, "_prom_range_query", _fake)
        prometheus_latency.run_single_sensor_breakdown(
            "http://fake", "1h", sensor_id,
        )
        return captured

    def test_label_filter_present_in_every_query(self, monkeypatch):
        queries = self._run(monkeypatch, "cam-42")
        assert queries, "expected at least one PromQL query to be sent"
        for q in queries:
            assert 'sensorId="cam-42"' in q, (
                f"query did not carry the sensorId filter: {q}"
            )

    def test_special_chars_in_sensor_id_are_escaped(self, monkeypatch):
        """If a sensor ID contained a raw ``"``, the query body would
        be syntactically broken — and worse, a malicious operator
        could attempt to inject a PromQL fragment. The escaping must
        neutralize that."""
        # Hypothetical PromQL-injection attempt
        malicious = 'x"} or up{foo="bar'
        queries = self._run(monkeypatch, malicious)
        # Every query must contain the escaped form, never the raw one.
        for q in queries:
            assert 'sensorId="x\\"}' in q, (
                f"escaped quote not found in query: {q}"
            )
            # Critical: the raw unescaped form must never appear.
            assert 'sensorId="x"}' not in q, (
                f"sensor ID was NOT escaped, PromQL injection is possible: {q}"
            )

    def test_queries_cover_every_event_counter(self, monkeypatch):
        """The single-sensor view must not silently drop counters.
        We assert that each of the five per-sensor metrics is queried
        at least once."""
        queries = self._run(monkeypatch, "cam-1")
        joined = "\n".join(queries)
        for metric in (
            "alert_bridge_events_by_sensor_total",
            "alert_bridge_events_after_dedup_by_sensor_total",
            "alert_bridge_events_skipped_confirmed_by_sensor_total",
            "alert_bridge_events_dropped_by_sensor_total",
            "alert_bridge_verification_failures_by_sensor_total",
        ):
            assert metric in joined, f"query set missing {metric}"

    def test_does_not_query_legacy_counter_names(self, monkeypatch):
        queries = self._run(monkeypatch, "cam-1")
        joined = "\n".join(queries)
        legacy_names = (
            "alert_bridge_events_total_by_sensor",
            "alert_bridge_events_after_dedup_total_by_sensor",
            "alert_bridge_events_skipped_confirmed_total_by_sensor",
            "alert_bridge_events_dropped_total_by_sensor",
            "alert_bridge_verification_failures_total_by_sensor",
        )
        for legacy in legacy_names:
            assert legacy not in joined, f"legacy metric name still queried: {legacy}"

    def test_all_three_drop_reasons_queried(self, monkeypatch):
        queries = self._run(monkeypatch, "cam-1")
        joined = "\n".join(queries)
        for reason in ("end_time_delta", "dedup", "rate_limit"):
            assert f'reason="{reason}"' in joined, (
                f"drop reason {reason!r} missing from per-sensor query set"
            )

    def test_full_failure_reason_taxonomy_queried(self, monkeypatch):
        """The triage view lives or dies by whether operators can
        see **which** failure reason hit a given sensor. Regression
        guard: if someone removes a reason from the query list, this
        test fails and forces a comments.md update."""
        queries = self._run(monkeypatch, "cam-1")
        joined = "\n".join(queries)
        expected_reasons = {
            "vst_timeout", "vst_overloaded", "vst_not_found",
            "vst_unavailable", "vst_client_error", "vst_server_error",
            "vst_unknown",
            "url_validation",
            "vlm_parse_failure", "vlm_timeout", "vlm_connection_error",
            "vlm_server_error", "vlm_invalid_payload",
            "no_prompt", "redis_unavailable", "unknown",
        }
        for reason in expected_reasons:
            assert f'reason="{reason}"' in joined, (
                f"failure reason {reason!r} missing from per-sensor query set"
            )

    def test_no_data_prints_hint_not_error(self, monkeypatch, capsys):
        """If the alert-agent opt-in is off (every per-sensor counter
        returns 0), the function must print a hint and return cleanly
        — NOT raise, NOT return non-zero."""
        self._run(monkeypatch, "cam-42")
        out = capsys.readouterr().out
        assert "no data" in out.lower()
        assert "per_sensor_labels" in out


class TestSensorSpecificPrometheusReport:
    """``--sensor-id`` makes the main Prometheus report sensor-specific.

    The top latency table must query the opt-in ``*_by_sensor_seconds``
    histograms, not the global histograms, and every counter query in
    the same report must carry the same ``sensorId`` filter.
    """

    def _run(self, sensor_id=None):
        captured = []

        def fake_get(url, **kwargs):
            mock = MagicMock()
            mock.raise_for_status.return_value = None
            mock.status_code = 200
            if url.endswith("/-/healthy"):
                mock.json.return_value = {}
            else:
                captured.append(kwargs["params"]["query"])
                mock.json.return_value = {
                    "status": "success",
                    "data": {"result": [{"metric": {}, "value": [0, "1.0"]}]},
                }
            return mock

        with patch.object(prometheus_latency.requests, "get", side_effect=fake_get):
            ok = prometheus_latency.run_prometheus(
                "http://fake", "1h", "now", sensor_id=sensor_id,
            )

        assert ok is True
        return captured

    def test_sensor_id_filters_every_main_report_query(self):
        queries = self._run(sensor_id="cam-42")

        assert queries, "expected PromQL queries from run_prometheus"
        for query in queries:
            assert 'sensorId="cam-42"' in query

    def test_sensor_id_uses_by_sensor_latency_histograms(self):
        joined = "\n".join(self._run(sensor_id="cam-42"))

        for metric in (
            "alert_bridge_upstream_duration_by_sensor_seconds",
            "alert_bridge_kafka_lag_duration_by_sensor_seconds",
            "alert_bridge_worker_queue_wait_duration_by_sensor_seconds",
            "alert_bridge_vst_duration_by_sensor_seconds",
            "alert_bridge_video_length_by_sensor_seconds",
            "alert_bridge_vlm_duration_by_sensor_seconds",
            "alert_bridge_worker_processing_by_sensor_seconds",
            "alert_bridge_e2e_duration_by_sensor_seconds",
        ):
            assert metric in joined

        for global_metric in (
            "alert_bridge_upstream_duration_seconds",
            "alert_bridge_kafka_lag_duration_seconds",
            "alert_bridge_worker_queue_wait_duration_seconds",
            "alert_bridge_vst_duration_seconds",
            "alert_bridge_video_length_seconds",
            "alert_bridge_vlm_duration_seconds",
            "alert_bridge_worker_processing_seconds",
            "alert_bridge_e2e_duration_seconds",
        ):
            assert global_metric not in joined

    def test_sensor_id_uses_by_sensor_event_counters(self):
        joined = "\n".join(self._run(sensor_id="cam-42"))

        for metric in (
            "alert_bridge_events_by_sensor_total",
            "alert_bridge_events_after_dedup_by_sensor_total",
            "alert_bridge_events_skipped_confirmed_by_sensor_total",
            "alert_bridge_events_dropped_by_sensor_total",
            "alert_bridge_verification_failures_by_sensor_total",
        ):
            assert metric in joined

    def test_sensor_id_is_escaped_in_main_report_queries(self):
        queries = self._run(sensor_id='x"} or up{foo="bar')

        for query in queries:
            assert 'sensorId="x\\"}' in query
            assert 'sensorId="x"}' not in query

    def test_without_sensor_id_global_report_stays_global(self):
        joined = "\n".join(self._run())

        assert "alert_bridge_vlm_duration_seconds" in joined
        assert "alert_bridge_events_total" in joined
        assert "sensorId=" not in joined
        assert "_by_sensor" not in joined


class TestSensorIdCliFlag:
    """End-to-end CLI wiring for ``--sensor-id``."""

    def test_sensor_id_flag_passes_sensor_to_prometheus_report(self, monkeypatch):
        called_with = {}

        def _fake(base_url, window, now_str, sensor_id=None):
            called_with["base_url"] = base_url
            called_with["window"] = window
            called_with["sensor_id"] = sensor_id
            return True

        monkeypatch.setattr(prometheus_latency, "run_prometheus", _fake)
        monkeypatch.setattr(sys, "argv",
                            ["prometheus_latency.py", "1h", "localhost", "--sensor-id", "cam-42"])

        prometheus_latency.main()

        assert called_with["sensor_id"] == "cam-42"
        assert called_with["window"] == "1h"

    def test_no_sensor_id_flag_keeps_prometheus_report_global(self, monkeypatch):
        called_with = {}

        def _fake(base_url, window, now_str, sensor_id=None):
            called_with["sensor_id"] = sensor_id
            return True

        monkeypatch.setattr(prometheus_latency, "run_prometheus", _fake)
        monkeypatch.setattr(sys, "argv",
                            ["prometheus_latency.py", "1h", "localhost"])

        prometheus_latency.main()

        assert called_with["sensor_id"] is None

    def test_sensor_id_suppresses_all_sensors_breakdown(self, monkeypatch):
        calls = {"per_sensor": 0}
        prom_call = {}

        def _fake_prom(base_url, window, now_str, sensor_id=None):
            prom_call["sensor_id"] = sensor_id
            return True

        monkeypatch.setattr(prometheus_latency, "run_prometheus", _fake_prom)
        monkeypatch.setattr(
            prometheus_latency, "run_per_sensor_breakdown",
            lambda *a, **kw: calls.__setitem__("per_sensor", calls["per_sensor"] + 1),
        )
        monkeypatch.setattr(sys, "argv",
                            ["prometheus_latency.py", "1h", "localhost",
                             "--per-sensor", "--sensor-id", "cam-1"])

        prometheus_latency.main()

        assert prom_call["sensor_id"] == "cam-1"
        assert calls["per_sensor"] == 0

    def test_per_sensor_without_sensor_id_still_prints_all_sensors_breakdown(self, monkeypatch):
        calls = {"per_sensor": 0}
        monkeypatch.setattr(prometheus_latency, "run_prometheus", lambda *a, **kw: True)
        monkeypatch.setattr(
            prometheus_latency, "run_per_sensor_breakdown",
            lambda *a, **kw: calls.__setitem__("per_sensor", calls["per_sensor"] + 1),
        )
        monkeypatch.setattr(sys, "argv",
                            ["prometheus_latency.py", "1h", "localhost", "--per-sensor"])

        prometheus_latency.main()

        assert calls["per_sensor"] == 1

    def test_sensor_id_skips_elasticsearch_section_even_when_requested(self, monkeypatch):
        calls = {"es": 0}
        monkeypatch.setattr(prometheus_latency, "run_prometheus", lambda *a, **kw: True)
        monkeypatch.setattr(
            prometheus_latency, "run_elasticsearch",
            lambda *a, **kw: calls.__setitem__("es", calls["es"] + 1),
        )
        monkeypatch.setattr(sys, "argv", [
            "prometheus_latency.py", "1h", "localhost",
            "--sensor-id", "cam-1", "--es-host", "eshost",
        ])

        prometheus_latency.main()

        assert calls["es"] == 0

    def test_all_sensor_breakdown_skipped_when_prometheus_section_fails(self, monkeypatch):
        """If ``run_prometheus`` returns ``False`` (endpoint down), the
        per-sensor queries are pointless — they would hit the same
        endpoint. Skip them so the error output stays focused on the
        root cause."""
        called = {"count": 0}

        def _fake(*args, **kwargs):
            called["count"] += 1

        monkeypatch.setattr(prometheus_latency, "run_prometheus", lambda *a, **kw: False)
        monkeypatch.setattr(prometheus_latency, "run_per_sensor_breakdown", _fake)
        monkeypatch.setattr(sys, "argv",
                            ["prometheus_latency.py", "1h", "localhost", "--per-sensor"])

        with pytest.raises(SystemExit):
            prometheus_latency.main()

        assert called["count"] == 0
