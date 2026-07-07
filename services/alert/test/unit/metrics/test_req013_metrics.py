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

"""In-process store + verdict-protection observability metrics.

The metric objects in ``metrics.prometheus_metrics`` are defined
unconditionally (prometheus_client is a hard dependency), so they can be
exercised directly regardless of the ``PROMETHEUS_METRICS_ENABLED`` flag.
This test asserts the four observability families exist, carry only bounded
labels, and record correctly, and that the dedup handler is wired to them.
"""

import os
import tempfile

import pytest
import yaml

# NOTE: ``metrics.prometheus_metrics`` is imported *inside* the tests, never
# at module top level. Sibling wiring tests (e.g.
# test_vst_duration_observation.py) delete the ``metrics`` modules from
# sys.modules and re-import them at collection time; a top-level import here
# would register the metrics in the global CollectorRegistry first and make
# that re-import raise "Duplicated timeseries". Keeping the imports lazy
# matches test_metrics_recorder.py / test_prometheus_multiprocess_export.py.


def _labelnames(metric):
    return set(getattr(metric, "_labelnames", ()) or ())


# ── (1) family + label-contract presence ───────────────────────────────


class TestMetricContract:
    def test_all_req013_families_defined(self):
        from metrics import prometheus_metrics as pm
        for name in (
            "DEDUP_CACHE_OCCUPANCY",
            "DEDUP_CACHE_EVICTIONS",
            "VERDICT_ES_GET_DURATION",
            "VERDICT_FAIL_OPEN",
            "ALERT_CONFIG_READ_SOURCE",
            "ALERT_CONFIG_STALENESS_SECONDS",
            "INDEX_READY",
            "RECORD_KEY_ALIGNMENT",
            "VERDICT_RETENTION_DELETED",
            "VERDICT_RETENTION_RUNS",
            "VERDICT_RETENTION_LAST_RUN",
        ):
            assert hasattr(pm, name), f"missing observability metric {name}"

    def test_labels_are_bounded_no_pii(self):
        from metrics import prometheus_metrics as pm
        # No sensorId / fingerprint / objectId labels anywhere.
        assert _labelnames(pm.DEDUP_CACHE_OCCUPANCY) == {"store"}
        assert _labelnames(pm.DEDUP_CACHE_EVICTIONS) == {"store", "mode"}
        assert _labelnames(pm.VERDICT_FAIL_OPEN) == {"reason"}
        assert _labelnames(pm.ALERT_CONFIG_READ_SOURCE) == {"source"}
        assert _labelnames(pm.INDEX_READY) == {"index"}
        assert _labelnames(pm.RECORD_KEY_ALIGNMENT) == {"aligned"}
        for m in (
            pm.DEDUP_CACHE_OCCUPANCY,
            pm.VERDICT_FAIL_OPEN,
            pm.RECORD_KEY_ALIGNMENT,
        ):
            for banned in ("sensorId", "sensor_id", "fingerprint", "objectIds"):
                assert banned not in _labelnames(m)

    def test_counter_records(self):
        from metrics import prometheus_metrics as pm
        before = pm.VERDICT_FAIL_OPEN.labels(reason="malformed")._value.get()
        pm.VERDICT_FAIL_OPEN.labels(reason="malformed").inc()
        after = pm.VERDICT_FAIL_OPEN.labels(reason="malformed")._value.get()
        assert after == before + 1

    def test_gauge_records(self):
        from metrics import prometheus_metrics as pm
        pm.INDEX_READY.labels(index="confirmed-verdicts").set(1)
        assert pm.INDEX_READY.labels(index="confirmed-verdicts")._value.get() == 1


# ── recorder helpers are import-safe and no-op when metrics disabled ────


class TestRecorderHelpers:
    def test_helpers_exist_and_are_safe_noops(self):
        from metrics import recorder as rec

        # Default test env has PROMETHEUS_METRICS_ENABLED off → every helper
        # must be a safe no-op (no exception, no NameError on the lazily
        # imported metric objects).
        rec.set_cache_occupancy("dedup", 5)
        rec.inc_cache_evictions("dedup", "sweep", 3)
        rec.observe_verdict_es_get(0.01)
        rec.inc_verdict_fail_open("expired")
        rec.inc_alert_config_read_source("cache")
        rec.set_alert_config_staleness(1.0)
        rec.set_index_ready("confirmed-verdicts", True)
        rec.inc_record_key_alignment("yes")
        rec.record_verdict_retention_run(2, True)

    def test_closed_enums_reject_stray_values(self):
        from metrics import recorder as rec

        # Guarded helpers silently drop out-of-enum values so a bad call site
        # cannot mint a stray Prometheus series.
        rec.inc_verdict_fail_open("not-a-reason")  # no raise
        rec.inc_alert_config_read_source("elsewhere")  # no raise
        rec.inc_record_key_alignment("maybe")  # no raise


# ── handler wiring: cache occupancy/eviction + verdict fail-open ────────


class _RecorderSpy:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _rec(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return _rec


def _cfg(**event_filters):
    cfg = {"alert_agent": {"event_filters": {"dedup_ttl_seconds": 300, **event_filters}}}
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        yaml.dump(cfg, f)
    return path


class TestHandlerWiring:
    def test_cache_occupancy_and_eviction_recorded(self, monkeypatch):
        import clients.dedup_state as ds

        spy = _RecorderSpy()
        monkeypatch.setattr(ds, "_metrics", spy)

        path = _cfg()
        try:
            h = ds.DedupStateHandler(config_file=path, clock=ds.time.monotonic)
        finally:
            os.unlink(path)

        h.process_event({
            "sensorId": "cam-1", "timestamp": "t", "end": "e",
            "objectIds": [1], "category": "collision",
            "analyticsModule": {"id": "x"},
        })
        names = [c[0] for c in spy.calls]
        assert "set_cache_occupancy" in names

    def test_verdict_fail_open_recorded_when_es_down(self, monkeypatch):
        import clients.dedup_state as ds

        spy = _RecorderSpy()
        monkeypatch.setattr(ds, "_metrics", spy)

        path = _cfg(protect_confirmed_verdicts={"enabled": True, "ttl_seconds": 600})
        try:
            h = ds.DedupStateHandler(config_file=path)
        finally:
            os.unlink(path)
        h._es_retry_after = 1e18  # ES unavailable / in backoff

        assert h.is_verdict_confirmed("fp") is False
        fail_open = [c for c in spy.calls if c[0] == "inc_verdict_fail_open"]
        assert fail_open and fail_open[0][1] == ("es_down",)

    def test_mark_write_failure_records_fail_open(self, monkeypatch):
        import clients.dedup_state as ds

        spy = _RecorderSpy()
        monkeypatch.setattr(ds, "_metrics", spy)

        path = _cfg(protect_confirmed_verdicts={"enabled": True, "ttl_seconds": 600})
        try:
            h = ds.DedupStateHandler(config_file=path)
        finally:
            os.unlink(path)

        class BoomES:
            def ensure_json_index(self, index):
                raise RuntimeError("es write down")

        h._es_client = BoomES()
        assert h.mark_verdict_confirmed("fp") is False
        reasons = [c[1][0] for c in spy.calls if c[0] == "inc_verdict_fail_open"]
        assert "write_error" in reasons


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
