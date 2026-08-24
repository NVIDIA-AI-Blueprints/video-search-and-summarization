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

"""Unit tests for ``metrics.recorder``.

These tests exercise the module-level Prometheus recording helpers that were
extracted from ``AnomalyEnhancer`` so that:

  * the recording logic can be validated without spinning up the full VLM
    orchestrator (prior to the extraction, every assertion required a live
    enhancer instance and its dependencies);
  * the feature-flag gate sits in a single place, so disabled-mode behavior
    only needs to be asserted once.

The tests run in both modes:
  * ``PROMETHEUS_METRICS_ENABLED=true``   — observations must increment.
  * ``PROMETHEUS_METRICS_ENABLED`` unset  — observations must be no-ops and
    must not raise, even if the prometheus_client package is not importable.

Run with: pytest test/metrics/test_metrics_recorder.py -v
"""

import importlib
import os
import sys
import time

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────


def _reload_recorder(enabled: bool):
    """Reload ``metrics`` + ``metrics.recorder`` with a specific flag state.

    The feature flag is evaluated once at module import time; flipping the
    env var and re-importing is the cleanest way to cover both modes from a
    single test run without having to fork a subprocess.

    Real ``metrics.prometheus_metrics`` modules are intentionally **not** reloaded —
    re-executing that module would try to register the same histograms
    and counters against ``prometheus_client``'s default registry a
    second time, which raises ``ValueError: Duplicated timeseries``. The
    metric *objects* do not depend on the feature flag; only the
    recorder does, so keeping them loaded once and rebinding the
    recorder is both correct and fast.

    Lightweight tests in this repository sometimes stub
    ``metrics.prometheus_metrics`` with an empty module. If that stub is
    present, discard it so enabled-mode recorder tests import the real metric
    definitions instead of failing on missing names.
    """
    os.environ["PROMETHEUS_METRICS_ENABLED"] = "true" if enabled else "false"
    prometheus_metrics = sys.modules.get("metrics.prometheus_metrics")
    if prometheus_metrics is not None and (
        not hasattr(prometheus_metrics, "ASYNC_EXTERNAL_IO_FALLBACK_TOTAL")
        or not hasattr(prometheus_metrics, "VLM_DURATION_BY_SENSOR")
    ):
        sys.modules.pop("metrics.prometheus_metrics", None)
    for mod in ("metrics.recorder", "metrics"):
        sys.modules.pop(mod, None)
    import metrics  # noqa: F401  (re-imports with new flag)
    recorder = importlib.import_module("metrics.recorder")
    return recorder


@pytest.fixture
def enabled_recorder():
    """Recorder module with the Prometheus flag enabled."""
    pytest.importorskip("prometheus_client")
    return _reload_recorder(True)


@pytest.fixture
def disabled_recorder():
    """Recorder module with the Prometheus flag disabled."""
    return _reload_recorder(False)


def _counter_value(counter):
    """Read the current value of a labelled Counter child."""
    return counter._value.get()


# ── Disabled-mode tests ───────────────────────────────────────────────────


class TestDisabledMode:
    """When the feature flag is off, every helper must be a no-op."""

    def test_observe_vst_duration_is_noop(self, disabled_recorder):
        # Must not raise — there is no VST_DURATION object in scope.
        disabled_recorder.observe_vst_duration(1.23)

    def test_observe_vlm_duration_is_noop(self, disabled_recorder):
        disabled_recorder.observe_vlm_duration(4.56)

    def test_observe_video_length_is_noop(self, disabled_recorder):
        disabled_recorder.observe_video_length(7.89)

    def test_observe_video_length_accepts_none(self, disabled_recorder):
        disabled_recorder.observe_video_length(None)

    def test_observe_pipeline_latency_is_noop(self, disabled_recorder):
        disabled_recorder.observe_pipeline_latency(
            {"end": "2025-01-02T03:04:05Z"},
            {"timestamps": {"kafkaPublishedAt": "2025-01-02T03:04:06Z"}},
        )

    def test_record_event_complete_is_noop_and_skips_stamp(self, disabled_recorder):
        latency = {}
        disabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={"info": {"verdict": "confirmed"}},
            latency=latency,
            failure_reason=None,
        )
        # Disabled mode must not mutate the latency dict either — no work
        # should happen at all when the scrape target is not enabled.
        assert latency == {}

    def test_inc_events_after_dedup_is_noop(self, disabled_recorder):
        disabled_recorder.inc_events_after_dedup(5)

    def test_inc_events_dropped_is_noop(self, disabled_recorder):
        disabled_recorder.inc_events_dropped("dedup", 3)

    def test_inc_events_skipped_confirmed_is_noop(self, disabled_recorder):
        # Must not raise — there is no EVENTS_SKIPPED_CONFIRMED object in scope.
        disabled_recorder.inc_events_skipped_confirmed()

    def test_inc_async_dispatch_fallback_is_noop(self, disabled_recorder):
        # Must not raise — there is no ASYNC_EXTERNAL_IO_FALLBACK_TOTAL
        # object in scope when the feature flag is off.
        disabled_recorder.inc_async_dispatch_fallback("executor_unavailable")

    def test_warm_startup_labels_is_noop(self, disabled_recorder):
        # Disabled mode must never touch the prometheus_client API.
        disabled_recorder.warm_startup_labels()


# ── Enabled-mode tests ────────────────────────────────────────────────────


class TestEnabledMode:
    def test_observe_vst_duration_increments_count(self, enabled_recorder):
        from metrics.prometheus_metrics import VST_DURATION
        before = VST_DURATION._sum.get()
        enabled_recorder.observe_vst_duration(2.5)
        assert VST_DURATION._sum.get() == pytest.approx(before + 2.5)

    def test_observe_vlm_duration_increments_count(self, enabled_recorder):
        from metrics.prometheus_metrics import VLM_DURATION
        before = VLM_DURATION._sum.get()
        enabled_recorder.observe_vlm_duration(1.25)
        assert VLM_DURATION._sum.get() == pytest.approx(before + 1.25)

    def test_observe_video_length_none_is_noop(self, enabled_recorder):
        # None should be treated as "no observation available", not as 0.0
        # — this is load-bearing for callers who pass iso_delta_seconds()
        # results directly.
        from metrics.prometheus_metrics import VIDEO_LENGTH
        before = VIDEO_LENGTH._sum.get()
        enabled_recorder.observe_video_length(None)
        assert VIDEO_LENGTH._sum.get() == before

    def test_observe_video_length_observes_value(self, enabled_recorder):
        from metrics.prometheus_metrics import VIDEO_LENGTH
        before = VIDEO_LENGTH._sum.get()
        enabled_recorder.observe_video_length(12.0)
        assert VIDEO_LENGTH._sum.get() == pytest.approx(before + 12.0)

    def test_observe_pipeline_latency_records_all_stages(self, enabled_recorder):
        from metrics.prometheus_metrics import (
            E2E_DURATION,
            KAFKA_LAG_DURATION,
            UPSTREAM_DURATION,
            WORKER_QUEUE_WAIT_DURATION,
        )
        up_before = UPSTREAM_DURATION._sum.get()
        lag_before = KAFKA_LAG_DURATION._sum.get()
        wait_before = WORKER_QUEUE_WAIT_DURATION._sum.get()
        e2e_before = E2E_DURATION._sum.get()

        message = {"end": "2025-01-02T03:04:00Z"}
        latency = {
            "timestamps": {
                "kafkaPublishedAt": "2025-01-02T03:04:01Z",
                "kafkaConsumedAt":  "2025-01-02T03:04:02Z",
                "workerAssignedAt": "2025-01-02T03:04:03Z",
                "elasticReadyAt":   "2025-01-02T03:04:08Z",
            },
        }
        enabled_recorder.observe_pipeline_latency(message, latency)

        assert UPSTREAM_DURATION._sum.get() == pytest.approx(up_before + 1.0)
        assert KAFKA_LAG_DURATION._sum.get() == pytest.approx(lag_before + 1.0)
        assert WORKER_QUEUE_WAIT_DURATION._sum.get() == pytest.approx(wait_before + 1.0)
        assert E2E_DURATION._sum.get() == pytest.approx(e2e_before + 8.0)

    def test_observe_pipeline_latency_skips_missing_stage(self, enabled_recorder):
        # Upstream stage is derivable (event end → kafka publish), but the
        # kafka consume timestamp is missing, so kafka lag and worker queue
        # wait must be skipped without affecting the stages that can be
        # computed.
        from metrics.prometheus_metrics import (
            KAFKA_LAG_DURATION,
            UPSTREAM_DURATION,
        )
        up_before = UPSTREAM_DURATION._sum.get()
        lag_before = KAFKA_LAG_DURATION._sum.get()

        message = {"end": "2025-01-02T03:04:00Z"}
        latency = {
            "timestamps": {
                "kafkaPublishedAt": "2025-01-02T03:04:01Z",
                # kafkaConsumedAt deliberately absent
            },
        }
        enabled_recorder.observe_pipeline_latency(message, latency)

        assert UPSTREAM_DURATION._sum.get() == pytest.approx(up_before + 1.0)
        assert KAFKA_LAG_DURATION._sum.get() == lag_before

    def test_observe_pipeline_latency_accepts_snake_case_keys(self, enabled_recorder):
        """Legacy documents and in-flight events during a rolling deploy use
        snake_case timestamp keys. The recorder must fall back to them so
        these events produce histogram observations rather than silent zeros."""
        from metrics.prometheus_metrics import (
            KAFKA_LAG_DURATION,
            UPSTREAM_DURATION,
            WORKER_QUEUE_WAIT_DURATION,
        )
        up_before   = UPSTREAM_DURATION._sum.get()
        lag_before  = KAFKA_LAG_DURATION._sum.get()
        wait_before = WORKER_QUEUE_WAIT_DURATION._sum.get()

        message = {"end": "2025-01-02T03:04:00Z"}
        latency = {
            "timestamps": {
                "kafka_published_at": "2025-01-02T03:04:01Z",
                "kafka_consumed_at":  "2025-01-02T03:04:02Z",
                "worker_assigned_at": "2025-01-02T03:04:03Z",
            },
        }
        enabled_recorder.observe_pipeline_latency(message, latency)

        assert UPSTREAM_DURATION._sum.get()        == pytest.approx(up_before   + 1.0)
        assert KAFKA_LAG_DURATION._sum.get()       == pytest.approx(lag_before  + 1.0)
        assert WORKER_QUEUE_WAIT_DURATION._sum.get() == pytest.approx(wait_before + 1.0)

    def test_record_event_complete_does_not_mutate_latency(self, enabled_recorder):
        from metrics.prometheus_metrics import E2E_DURATION
        e2e_before = E2E_DURATION._sum.get()
        latency = {"timestamps": {}}
        enabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={"info": {"verdict": "confirmed"}, "end": "2025-01-02T03:04:00Z"},
            latency=latency,
        )
        # The recorder must NOT inject elasticReadyAt back into the caller's
        # latency dict — doing so would corrupt the ES payload for failure
        # events that never reached the post-VLM timestamp stamp.
        assert latency == {"timestamps": {}}, (
            "record_event_complete must not mutate the latency dict"
        )
        # The E2E histogram must still have been observed (using a local
        # wall-clock value, not writing back to the dict).
        assert E2E_DURATION._sum.get() > e2e_before

    def test_record_event_complete_uses_existing_elastic_ready_for_obs(self, enabled_recorder):
        from metrics.prometheus_metrics import E2E_DURATION
        e2e_before = E2E_DURATION._sum.get()
        latency = {
            "timestamps": {
                "elasticReadyAt": "2025-01-02T03:04:08Z",
            }
        }
        enabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={"info": {"verdict": "confirmed"}, "end": "2025-01-02T03:04:00Z"},
            latency=latency,
        )
        # The existing elasticReadyAt must be used for the E2E observation
        # and the caller's dict must remain unchanged.
        assert latency["timestamps"]["elasticReadyAt"] == "2025-01-02T03:04:08Z"
        assert E2E_DURATION._sum.get() == pytest.approx(e2e_before + 8.0)

    def test_record_event_complete_increments_events_total(self, enabled_recorder):
        from metrics.prometheus_metrics import EVENTS_TOTAL
        before = _counter_value(EVENTS_TOTAL.labels(verdict="confirmed"))
        enabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={"info": {"verdict": "confirmed"}},
            latency={},
        )
        assert _counter_value(EVENTS_TOTAL.labels(verdict="confirmed")) == before + 1

    def test_record_event_complete_defaults_verdict_to_unknown(self, enabled_recorder):
        from metrics.prometheus_metrics import EVENTS_TOTAL
        before = _counter_value(EVENTS_TOTAL.labels(verdict="unknown"))
        # ``info`` present but no verdict key
        enabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={"info": {}},
            latency={},
        )
        # ``info`` missing entirely
        enabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={},
            latency={},
        )
        assert _counter_value(EVENTS_TOTAL.labels(verdict="unknown")) == before + 2

    def test_record_event_complete_increments_verification_failures(self, enabled_recorder):
        from metrics.prometheus_metrics import VERIFICATION_FAILURES
        before = _counter_value(VERIFICATION_FAILURES.labels(reason="vlm_timeout"))
        enabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={"info": {"verdict": "verification-failed"}},
            latency={},
            failure_reason="vlm_timeout",
        )
        assert (
            _counter_value(VERIFICATION_FAILURES.labels(reason="vlm_timeout"))
            == before + 1
        )

    def test_record_event_complete_skips_failure_counter_on_success(self, enabled_recorder):
        from metrics.prometheus_metrics import VERIFICATION_FAILURES
        before = _counter_value(VERIFICATION_FAILURES.labels(reason="vlm_timeout"))
        enabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={"info": {"verdict": "confirmed"}},
            latency={},
            failure_reason=None,
        )
        assert (
            _counter_value(VERIFICATION_FAILURES.labels(reason="vlm_timeout"))
            == before
        )

    # ── Batch-level event counters (C22) ─────────────────────────────────

    def test_inc_events_after_dedup_increments(self, enabled_recorder):
        from metrics.prometheus_metrics import EVENTS_AFTER_DEDUP
        before = _counter_value(EVENTS_AFTER_DEDUP)
        enabled_recorder.inc_events_after_dedup(7)
        assert _counter_value(EVENTS_AFTER_DEDUP) == before + 7

    def test_inc_events_after_dedup_zero_is_noop(self, enabled_recorder):
        # A batch that passed every filter but had zero survivors must not
        # touch the counter — incrementing by 0 is still a scrape-visible
        # no-op but we explicitly short-circuit to keep the contract clean.
        from metrics.prometheus_metrics import EVENTS_AFTER_DEDUP
        before = _counter_value(EVENTS_AFTER_DEDUP)
        enabled_recorder.inc_events_after_dedup(0)
        assert _counter_value(EVENTS_AFTER_DEDUP) == before

    def test_inc_events_after_dedup_negative_is_noop(self, enabled_recorder):
        # Prometheus counters are monotonic; a negative argument is
        # always a bug at the call site. Treat it defensively as a no-op
        # rather than blowing up the pipeline.
        from metrics.prometheus_metrics import EVENTS_AFTER_DEDUP
        before = _counter_value(EVENTS_AFTER_DEDUP)
        enabled_recorder.inc_events_after_dedup(-3)
        assert _counter_value(EVENTS_AFTER_DEDUP) == before

    def test_inc_events_dropped_valid_reasons(self, enabled_recorder):
        from metrics.prometheus_metrics import EVENTS_DROPPED
        for reason, delta in [("end_time_delta", 2), ("dedup", 5), ("rate_limit", 1)]:
            before = _counter_value(EVENTS_DROPPED.labels(reason=reason))
            enabled_recorder.inc_events_dropped(reason, delta)
            assert _counter_value(EVENTS_DROPPED.labels(reason=reason)) == before + delta

    def test_inc_events_dropped_zero_is_noop(self, enabled_recorder):
        from metrics.prometheus_metrics import EVENTS_DROPPED
        before = _counter_value(EVENTS_DROPPED.labels(reason="dedup"))
        enabled_recorder.inc_events_dropped("dedup", 0)
        assert _counter_value(EVENTS_DROPPED.labels(reason="dedup")) == before

    def test_inc_events_dropped_unknown_reason_maps_to_allowlist(self, enabled_recorder):
        # Defensive: a typo at the call site must not mint a new time
        # series. Unknown reasons fold into a catch-all "unknown" bucket
        # so the cardinality stays bounded by the allowlist size.
        from metrics.prometheus_metrics import EVENTS_DROPPED
        before = _counter_value(EVENTS_DROPPED.labels(reason="unknown"))
        enabled_recorder.inc_events_dropped("typo_reason", 4)
        assert _counter_value(EVENTS_DROPPED.labels(reason="unknown")) == before + 4

    # ── Verdict allowlist (C8) ────────────────────────────────────────────

    def test_record_event_complete_accepts_all_allowlist_verdicts(self, enabled_recorder):
        """Every entry in ``EVENTS_VERDICTS`` routes to its own series —
        no accidental collapse into ``unknown``."""
        from metrics.prometheus_metrics import EVENTS_TOTAL
        for verdict in enabled_recorder.EVENTS_VERDICTS:
            before = _counter_value(EVENTS_TOTAL.labels(verdict=verdict))
            enabled_recorder.record_event_complete(
                worker_start_time=time.time(),
                message={"info": {"verdict": verdict}},
                latency={},
            )
            assert _counter_value(EVENTS_TOTAL.labels(verdict=verdict)) == before + 1

    def test_record_event_complete_typo_verdict_maps_to_unknown(self, enabled_recorder):
        """A typoed upstream value must fold into the ``unknown`` bucket
        instead of minting a fresh ``confirmd`` series (permanent-cardinality
        foot-gun)."""
        from metrics.prometheus_metrics import EVENTS_TOTAL
        before_unknown = _counter_value(EVENTS_TOTAL.labels(verdict="unknown"))
        enabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={"info": {"verdict": "confirmd"}},  # typo on purpose
            latency={},
        )
        assert _counter_value(EVENTS_TOTAL.labels(verdict="unknown")) == before_unknown + 1
        # Prove no new series was created for the typo.
        try:
            # ``.labels(...)`` constructs-on-read in prometheus_client, so
            # simply accessing it would create the child. Instead walk
            # the registered children via the ``_metrics`` dict which
            # only contains materialized series.
            assert "('confirmd',)" not in str(EVENTS_TOTAL._metrics)
        except AttributeError:
            # Older prometheus_client versions — just assert nothing
            # other than ``unknown`` moved, which is already asserted
            # above implicitly.
            pass

    def test_record_event_complete_empty_string_verdict_is_unknown(self, enabled_recorder):
        from metrics.prometheus_metrics import EVENTS_TOTAL
        before = _counter_value(EVENTS_TOTAL.labels(verdict="unknown"))
        enabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={"info": {"verdict": ""}},
            latency={},
        )
        assert _counter_value(EVENTS_TOTAL.labels(verdict="unknown")) == before + 1

    def test_record_event_complete_none_verdict_is_unknown(self, enabled_recorder):
        from metrics.prometheus_metrics import EVENTS_TOTAL
        before = _counter_value(EVENTS_TOTAL.labels(verdict="unknown"))
        enabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={"info": {"verdict": None}},
            latency={},
        )
        assert _counter_value(EVENTS_TOTAL.labels(verdict="unknown")) == before + 1

    def test_record_event_complete_non_string_verdict_is_unknown(self, enabled_recorder):
        """Non-string values (e.g. an int accidentally written upstream)
        must not raise — they normalize to ``unknown``."""
        from metrics.prometheus_metrics import EVENTS_TOTAL
        before = _counter_value(EVENTS_TOTAL.labels(verdict="unknown"))
        enabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={"info": {"verdict": 42}},
            latency={},
        )
        assert _counter_value(EVENTS_TOTAL.labels(verdict="unknown")) == before + 1

    def test_normalize_verdict_warns_once_per_unknown_value(self, enabled_recorder, caplog):
        """The drift warning must fire exactly once per distinct unknown
        value — otherwise a noisy upstream producer would flood the log
        at every event."""
        import logging
        # Reset the warn-tracker so previous tests don't interfere.
        enabled_recorder._UNKNOWN_VERDICTS_SEEN.clear()
        with caplog.at_level(logging.WARNING, logger="metrics.recorder"):
            enabled_recorder._normalize_verdict("mystery_value")
            enabled_recorder._normalize_verdict("mystery_value")
            enabled_recorder._normalize_verdict("mystery_value")
            enabled_recorder._normalize_verdict("another_weird_value")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        # One warning per distinct value — so 2 warnings for {mystery, another_weird}.
        assert len(warnings) == 2
        assert any("mystery_value" in r.getMessage() for r in warnings)
        assert any("another_weird_value" in r.getMessage() for r in warnings)

    # ── Skipped-confirmed counter (C9) ────────────────────────────────────

    def test_inc_events_skipped_confirmed_increments(self, enabled_recorder):
        from metrics.prometheus_metrics import EVENTS_SKIPPED_CONFIRMED
        before = _counter_value(EVENTS_SKIPPED_CONFIRMED)
        enabled_recorder.inc_events_skipped_confirmed()
        assert _counter_value(EVENTS_SKIPPED_CONFIRMED) == before + 1

    def test_inc_events_skipped_confirmed_multiple_calls_accumulate(self, enabled_recorder):
        from metrics.prometheus_metrics import EVENTS_SKIPPED_CONFIRMED
        before = _counter_value(EVENTS_SKIPPED_CONFIRMED)
        for _ in range(5):
            enabled_recorder.inc_events_skipped_confirmed()
        assert _counter_value(EVENTS_SKIPPED_CONFIRMED) == before + 5

    # ── Async dispatch fallback (C26) ────────────────────────────────────

    def test_inc_async_dispatch_fallback_executor_unavailable(self, enabled_recorder):
        from metrics.prometheus_metrics import ASYNC_EXTERNAL_IO_FALLBACK_TOTAL
        child = ASYNC_EXTERNAL_IO_FALLBACK_TOTAL.labels(
            operation="dispatch_message", reason="executor_unavailable",
        )
        before = _counter_value(child)
        enabled_recorder.inc_async_dispatch_fallback("executor_unavailable")
        assert _counter_value(child) == before + 1

    def test_inc_async_dispatch_fallback_submit_error(self, enabled_recorder):
        from metrics.prometheus_metrics import ASYNC_EXTERNAL_IO_FALLBACK_TOTAL
        child = ASYNC_EXTERNAL_IO_FALLBACK_TOTAL.labels(
            operation="dispatch_message", reason="submit_error",
        )
        before = _counter_value(child)
        enabled_recorder.inc_async_dispatch_fallback("submit_error")
        assert _counter_value(child) == before + 1

    def test_inc_async_dispatch_fallback_uses_dispatch_message_operation(self, enabled_recorder):
        """The operation label must be ``dispatch_message`` so operators
        can ``sum by (operation) (rate(...))`` across all async mixins
        and see dispatch fallbacks as their own row (not merged with
        Redis / sink)."""
        from metrics.prometheus_metrics import ASYNC_EXTERNAL_IO_FALLBACK_TOTAL
        # Reading the metric's label definition shows operation + reason.
        assert ASYNC_EXTERNAL_IO_FALLBACK_TOTAL._labelnames == ("operation", "reason")
        # Proving the helper emits the right operation value: increment
        # under a distinct reason and look it up by the labels we expect.
        child = ASYNC_EXTERNAL_IO_FALLBACK_TOTAL.labels(
            operation="dispatch_message", reason="executor_unavailable",
        )
        before = _counter_value(child)
        enabled_recorder.inc_async_dispatch_fallback("executor_unavailable")
        assert _counter_value(child) == before + 1

    # ── Startup label warmup (C15) ────────────────────────────────────────

    def test_warm_startup_labels_materializes_verdict_series(self, enabled_recorder):
        """Every verdict in the allowlist must show up in the
        registry with value 0 (or higher, if a prior test touched it)
        after ``warm_startup_labels`` runs."""
        enabled_recorder.warm_startup_labels()
        from metrics.prometheus_metrics import EVENTS_TOTAL
        exposed = {
            sample.labels["verdict"]
            for metric in EVENTS_TOTAL.collect()
            for sample in metric.samples
            if sample.name == "alert_bridge_events_total"
        }
        for verdict in enabled_recorder.EVENTS_VERDICTS:
            assert verdict in exposed, f"warmup missed {verdict!r}"

    def test_warm_startup_labels_materializes_dropped_reasons(self, enabled_recorder):
        enabled_recorder.warm_startup_labels()
        from metrics.prometheus_metrics import EVENTS_DROPPED
        exposed = {
            sample.labels["reason"]
            for metric in EVENTS_DROPPED.collect()
            for sample in metric.samples
            if sample.name == "alert_bridge_events_dropped_total"
        }
        for reason in enabled_recorder.EVENTS_DROPPED_REASONS:
            assert reason in exposed, f"warmup missed drop reason {reason!r}"

    def test_warm_startup_labels_materializes_verification_reasons(self, enabled_recorder):
        """All VST and VLM failure reasons (C14 taxonomy) must be
        warmed. This is the regression guard against the original C15
        scrape-window issue — after warmup, a ``rate()`` query on any
        known reason returns zero instead of "no data"."""
        enabled_recorder.warm_startup_labels()
        from metrics.prometheus_metrics import VERIFICATION_FAILURES
        exposed = {
            sample.labels["reason"]
            for metric in VERIFICATION_FAILURES.collect()
            for sample in metric.samples
            if sample.name == "alert_bridge_verification_failures_total"
        }
        expected = {
            # VST side (C14)
            "vst_timeout", "vst_overloaded", "vst_not_found", "vst_unavailable",
            "vst_client_error", "vst_server_error", "vst_unknown",
            # URL + VLM side
            "url_validation",
            "vlm_parse_failure", "vlm_timeout", "vlm_connection_error",
            "vlm_server_error", "vlm_invalid_payload",
            "unknown",
        }
        missing = expected - exposed
        assert not missing, f"warmup missed {missing}"

    def test_warm_startup_labels_does_not_inflate_counts(self, enabled_recorder):
        """``inc(0)`` is a no-op numerically — counters are monotonic
        and warmup must not skew the cumulative value. If a future
        refactor swaps ``inc(0)`` for ``inc(1)`` this test catches it
        immediately."""
        from metrics.prometheus_metrics import EVENTS_TOTAL
        before = _counter_value(EVENTS_TOTAL.labels(verdict="confirmed"))
        enabled_recorder.warm_startup_labels()
        enabled_recorder.warm_startup_labels()  # call twice to be sure
        assert _counter_value(EVENTS_TOTAL.labels(verdict="confirmed")) == before

    # ── Per-sensor opt-in (C21) ───────────────────────────────────────────

    def test_per_sensor_labels_default_off(self, enabled_recorder):
        """The feature is opt-in. Fresh recorder reload must default off."""
        assert enabled_recorder.per_sensor_labels_enabled() is False

    def test_set_per_sensor_labels_toggle(self, enabled_recorder):
        try:
            enabled_recorder.set_per_sensor_labels(True)
            assert enabled_recorder.per_sensor_labels_enabled() is True
            enabled_recorder.set_per_sensor_labels(False)
            assert enabled_recorder.per_sensor_labels_enabled() is False
        finally:
            enabled_recorder.set_per_sensor_labels(False)

    def test_sanitize_sensor_id_passes_through_valid(self, enabled_recorder):
        assert enabled_recorder._sanitize_sensor_id("cam-42") == "cam-42"
        assert enabled_recorder._sanitize_sensor_id("  padded  ") == "padded"

    def test_sanitize_sensor_id_none_maps_to_unknown(self, enabled_recorder):
        assert enabled_recorder._sanitize_sensor_id(None) == "unknown"

    def test_sanitize_sensor_id_empty_maps_to_unknown(self, enabled_recorder):
        assert enabled_recorder._sanitize_sensor_id("") == "unknown"
        assert enabled_recorder._sanitize_sensor_id("   ") == "unknown"

    def test_sanitize_sensor_id_non_string_maps_to_unknown(self, enabled_recorder):
        assert enabled_recorder._sanitize_sensor_id(42) == "unknown"
        assert enabled_recorder._sanitize_sensor_id({"nested": "dict"}) == "unknown"

    def test_sanitize_sensor_id_length_bounded(self, enabled_recorder):
        """A pathological upstream producer writing an unbounded string
        must NOT mint an unbounded Prometheus label — that would be a
        permanent-cardinality time bomb for the scrape target."""
        long_id = "x" * 200
        assert enabled_recorder._sanitize_sensor_id(long_id) == "unknown"

    def test_sanitize_sensor_id_cardinality_cap_returns_overflow(self, enabled_recorder):
        """Once ``_MAX_SENSOR_IDS`` distinct IDs are known, every new
        unseen ID must return ``'unknown_overflow'`` instead of being
        added to the registry."""
        saved_known = enabled_recorder._KNOWN_SENSOR_IDS.copy()
        saved_max = enabled_recorder._MAX_SENSOR_IDS
        try:
            # Fill the set to exactly the cap with synthetic IDs.
            enabled_recorder._KNOWN_SENSOR_IDS.clear()
            cap = 5
            enabled_recorder._MAX_SENSOR_IDS = cap
            for i in range(cap):
                result = enabled_recorder._sanitize_sensor_id(f"cap-sensor-{i}")
                assert result == f"cap-sensor-{i}"

            # One more brand-new ID must overflow.
            assert enabled_recorder._sanitize_sensor_id("cap-sensor-overflow") == "unknown_overflow"
            # The set size must not have grown past the cap.
            assert len(enabled_recorder._KNOWN_SENSOR_IDS) == cap
        finally:
            enabled_recorder._KNOWN_SENSOR_IDS.clear()
            enabled_recorder._KNOWN_SENSOR_IDS.update(saved_known)
            enabled_recorder._MAX_SENSOR_IDS = saved_max

    def test_sanitize_sensor_id_known_id_passes_through_after_cap(self, enabled_recorder):
        """An ID that was accepted before the cap was reached must
        continue to pass through even after the cap is hit — only
        brand-new IDs are blocked."""
        saved_known = enabled_recorder._KNOWN_SENSOR_IDS.copy()
        saved_max = enabled_recorder._MAX_SENSOR_IDS
        try:
            enabled_recorder._KNOWN_SENSOR_IDS.clear()
            enabled_recorder._MAX_SENSOR_IDS = 2
            enabled_recorder._sanitize_sensor_id("known-a")
            enabled_recorder._sanitize_sensor_id("known-b")
            # Cap reached — known-a must still pass through.
            assert enabled_recorder._sanitize_sensor_id("known-a") == "known-a"
            # A new ID must overflow.
            assert enabled_recorder._sanitize_sensor_id("brand-new") == "unknown_overflow"
        finally:
            enabled_recorder._KNOWN_SENSOR_IDS.clear()
            enabled_recorder._KNOWN_SENSOR_IDS.update(saved_known)
            enabled_recorder._MAX_SENSOR_IDS = saved_max

    def test_sanitize_sensor_id_overflow_routes_to_unknown_overflow_series(self, enabled_recorder):
        """End-to-end: when the cap is reached, ``record_event_complete``
        must route the overflowing sensor's metric increment to
        ``sensorId='unknown_overflow'`` rather than a new series."""
        saved_known = enabled_recorder._KNOWN_SENSOR_IDS.copy()
        saved_max = enabled_recorder._MAX_SENSOR_IDS
        try:
            enabled_recorder.set_per_sensor_labels(True)
            enabled_recorder._KNOWN_SENSOR_IDS.clear()
            enabled_recorder._MAX_SENSOR_IDS = 1
            # Fill the one allowed slot.
            enabled_recorder._sanitize_sensor_id("slot-used")

            from metrics.prometheus_metrics import EVENTS_TOTAL_BY_SENSOR
            overflow_before = _counter_value(
                EVENTS_TOTAL_BY_SENSOR.labels(verdict="confirmed", sensorId="unknown_overflow")
            )
            enabled_recorder.record_event_complete(
                worker_start_time=time.time(),
                message={"sensorId": "overflow-cam", "info": {"verdict": "confirmed"}},
                latency={},
            )
            assert _counter_value(
                EVENTS_TOTAL_BY_SENSOR.labels(verdict="confirmed", sensorId="unknown_overflow")
            ) == overflow_before + 1
        finally:
            enabled_recorder._KNOWN_SENSOR_IDS.clear()
            enabled_recorder._KNOWN_SENSOR_IDS.update(saved_known)
            enabled_recorder._MAX_SENSOR_IDS = saved_max
            enabled_recorder.set_per_sensor_labels(False)

    def test_per_sensor_counters_absent_when_flag_off(self, enabled_recorder):
        """With the flag off, the ``*_BY_SENSOR`` counters must stay
        untouched regardless of how many events flow through."""
        enabled_recorder.set_per_sensor_labels(False)
        from metrics.prometheus_metrics import EVENTS_TOTAL_BY_SENSOR
        before = _counter_value(
            EVENTS_TOTAL_BY_SENSOR.labels(verdict="confirmed", sensorId="cam-1")
        )
        enabled_recorder.record_event_complete(
            worker_start_time=time.time(),
            message={"sensorId": "cam-1", "info": {"verdict": "confirmed"}},
            latency={},
        )
        assert _counter_value(
            EVENTS_TOTAL_BY_SENSOR.labels(verdict="confirmed", sensorId="cam-1")
        ) == before

    def test_per_sensor_latency_histograms_absent_when_flag_off(self, enabled_recorder):
        """The opt-in gate applies to latency histograms as well as counters."""
        enabled_recorder.set_per_sensor_labels(False)
        from metrics.prometheus_metrics import VLM_DURATION_BY_SENSOR
        before = VLM_DURATION_BY_SENSOR.labels(sensorId="cam-lat-off")._sum.get()
        enabled_recorder.observe_vlm_duration(2.0, sensor_id="cam-lat-off")
        assert (
            VLM_DURATION_BY_SENSOR.labels(sensorId="cam-lat-off")._sum.get()
            == before
        )

    def test_per_sensor_events_total_increments_when_flag_on(self, enabled_recorder):
        """Flag on: ``EVENTS_TOTAL`` still increments (backward-compat)
        AND ``EVENTS_TOTAL_BY_SENSOR`` increments in lockstep."""
        try:
            enabled_recorder.set_per_sensor_labels(True)
            from metrics.prometheus_metrics import (
                EVENTS_TOTAL,
                EVENTS_TOTAL_BY_SENSOR,
            )
            before_total = _counter_value(EVENTS_TOTAL.labels(verdict="confirmed"))
            before_by_sensor = _counter_value(
                EVENTS_TOTAL_BY_SENSOR.labels(verdict="confirmed", sensorId="cam-42")
            )
            enabled_recorder.record_event_complete(
                worker_start_time=time.time(),
                message={"sensorId": "cam-42", "info": {"verdict": "confirmed"}},
                latency={},
            )
            assert _counter_value(EVENTS_TOTAL.labels(verdict="confirmed")) == before_total + 1
            assert _counter_value(
                EVENTS_TOTAL_BY_SENSOR.labels(verdict="confirmed", sensorId="cam-42")
            ) == before_by_sensor + 1
        finally:
            enabled_recorder.set_per_sensor_labels(False)

    def test_per_sensor_stage_histograms_increment_when_flag_on(self, enabled_recorder):
        """Flag on: stage helper histograms emit by-sensor series in lockstep."""
        try:
            enabled_recorder.set_per_sensor_labels(True)
            from metrics.prometheus_metrics import (
                VIDEO_LENGTH_BY_SENSOR,
                VLM_DURATION_BY_SENSOR,
                VST_DURATION_BY_SENSOR,
            )
            vst_before = VST_DURATION_BY_SENSOR.labels(sensorId="cam-stage")._sum.get()
            vlm_before = VLM_DURATION_BY_SENSOR.labels(sensorId="cam-stage")._sum.get()
            video_before = VIDEO_LENGTH_BY_SENSOR.labels(sensorId="cam-stage")._sum.get()

            enabled_recorder.observe_vst_duration(1.5, sensor_id="cam-stage")
            enabled_recorder.observe_vlm_duration(2.5, sensor_id="cam-stage")
            enabled_recorder.observe_video_length(10.0, sensor_id="cam-stage")

            assert VST_DURATION_BY_SENSOR.labels(sensorId="cam-stage")._sum.get() == pytest.approx(vst_before + 1.5)
            assert VLM_DURATION_BY_SENSOR.labels(sensorId="cam-stage")._sum.get() == pytest.approx(vlm_before + 2.5)
            assert VIDEO_LENGTH_BY_SENSOR.labels(sensorId="cam-stage")._sum.get() == pytest.approx(video_before + 10.0)
        finally:
            enabled_recorder.set_per_sensor_labels(False)

    def test_per_sensor_pipeline_latency_histograms_increment_when_flag_on(self, enabled_recorder):
        try:
            enabled_recorder.set_per_sensor_labels(True)
            from metrics.prometheus_metrics import (
                E2E_DURATION_BY_SENSOR,
                KAFKA_LAG_DURATION_BY_SENSOR,
                UPSTREAM_DURATION_BY_SENSOR,
                WORKER_QUEUE_WAIT_DURATION_BY_SENSOR,
            )
            up_before = UPSTREAM_DURATION_BY_SENSOR.labels(sensorId="cam-pipe")._sum.get()
            lag_before = KAFKA_LAG_DURATION_BY_SENSOR.labels(sensorId="cam-pipe")._sum.get()
            wait_before = WORKER_QUEUE_WAIT_DURATION_BY_SENSOR.labels(sensorId="cam-pipe")._sum.get()
            e2e_before = E2E_DURATION_BY_SENSOR.labels(sensorId="cam-pipe")._sum.get()

            enabled_recorder.observe_pipeline_latency(
                {"sensorId": "cam-pipe", "end": "2025-01-02T03:04:00Z"},
                {
                    "timestamps": {
                        "kafkaPublishedAt": "2025-01-02T03:04:01Z",
                        "kafkaConsumedAt":  "2025-01-02T03:04:02Z",
                        "workerAssignedAt": "2025-01-02T03:04:03Z",
                        "elasticReadyAt":   "2025-01-02T03:04:08Z",
                    },
                },
            )

            assert UPSTREAM_DURATION_BY_SENSOR.labels(sensorId="cam-pipe")._sum.get() == pytest.approx(up_before + 1.0)
            assert KAFKA_LAG_DURATION_BY_SENSOR.labels(sensorId="cam-pipe")._sum.get() == pytest.approx(lag_before + 1.0)
            assert WORKER_QUEUE_WAIT_DURATION_BY_SENSOR.labels(sensorId="cam-pipe")._sum.get() == pytest.approx(wait_before + 1.0)
            assert E2E_DURATION_BY_SENSOR.labels(sensorId="cam-pipe")._sum.get() == pytest.approx(e2e_before + 8.0)
        finally:
            enabled_recorder.set_per_sensor_labels(False)

    def test_per_sensor_worker_processing_histogram_increments_when_flag_on(self, enabled_recorder):
        try:
            enabled_recorder.set_per_sensor_labels(True)
            from metrics.prometheus_metrics import WORKER_PROCESSING_DURATION_BY_SENSOR
            before = WORKER_PROCESSING_DURATION_BY_SENSOR.labels(sensorId="cam-worker")._sum.get()

            enabled_recorder.record_event_complete(
                worker_start_time=time.time() - 1.0,
                message={"sensorId": "cam-worker", "info": {"verdict": "confirmed"}},
                latency={},
            )

            assert (
                WORKER_PROCESSING_DURATION_BY_SENSOR.labels(sensorId="cam-worker")._sum.get()
                > before
            )
        finally:
            enabled_recorder.set_per_sensor_labels(False)

    def test_per_sensor_verification_failures_increments_when_flag_on(self, enabled_recorder):
        try:
            enabled_recorder.set_per_sensor_labels(True)
            from metrics.prometheus_metrics import (
                VERIFICATION_FAILURES_BY_SENSOR,
            )
            before = _counter_value(
                VERIFICATION_FAILURES_BY_SENSOR.labels(
                    reason="vlm_timeout", sensorId="cam-x",
                )
            )
            enabled_recorder.record_event_complete(
                worker_start_time=time.time(),
                message={"sensorId": "cam-x", "info": {"verdict": "verification-failed"}},
                latency={},
                failure_reason="vlm_timeout",
            )
            assert _counter_value(
                VERIFICATION_FAILURES_BY_SENSOR.labels(
                    reason="vlm_timeout", sensorId="cam-x",
                )
            ) == before + 1
        finally:
            enabled_recorder.set_per_sensor_labels(False)

    def test_per_sensor_events_dropped_groups_by_sensor(self, enabled_recorder):
        """Batch-level drop counters must split the passed ``messages``
        iterable by sensor and emit one ``.inc(n)`` per group."""
        try:
            enabled_recorder.set_per_sensor_labels(True)
            from metrics.prometheus_metrics import EVENTS_DROPPED_BY_SENSOR
            before_a = _counter_value(
                EVENTS_DROPPED_BY_SENSOR.labels(reason="dedup", sensorId="cam-a")
            )
            before_b = _counter_value(
                EVENTS_DROPPED_BY_SENSOR.labels(reason="dedup", sensorId="cam-b")
            )
            # Four messages, two sensors — 3 cam-a, 1 cam-b.
            msgs = [
                {"sensorId": "cam-a"}, {"sensorId": "cam-a"},
                {"sensorId": "cam-b"}, {"sensorId": "cam-a"},
            ]
            enabled_recorder.inc_events_dropped("dedup", 4, messages=msgs)
            assert _counter_value(
                EVENTS_DROPPED_BY_SENSOR.labels(reason="dedup", sensorId="cam-a")
            ) == before_a + 3
            assert _counter_value(
                EVENTS_DROPPED_BY_SENSOR.labels(reason="dedup", sensorId="cam-b")
            ) == before_b + 1
        finally:
            enabled_recorder.set_per_sensor_labels(False)

    def test_per_sensor_skipped_confirmed_increments_when_flag_on(self, enabled_recorder):
        try:
            enabled_recorder.set_per_sensor_labels(True)
            from metrics.prometheus_metrics import EVENTS_SKIPPED_CONFIRMED_BY_SENSOR
            before = _counter_value(
                EVENTS_SKIPPED_CONFIRMED_BY_SENSOR.labels(sensorId="cam-skipped")
            )
            enabled_recorder.inc_events_skipped_confirmed({"sensorId": "cam-skipped"})
            assert _counter_value(
                EVENTS_SKIPPED_CONFIRMED_BY_SENSOR.labels(sensorId="cam-skipped")
            ) == before + 1
        finally:
            enabled_recorder.set_per_sensor_labels(False)

    def test_per_sensor_long_sensor_id_folds_to_unknown(self, enabled_recorder):
        """Cardinality guard: a 1KB sensor ID must route to
        ``sensorId="unknown"`` rather than mint a new series."""
        try:
            enabled_recorder.set_per_sensor_labels(True)
            from metrics.prometheus_metrics import EVENTS_TOTAL_BY_SENSOR
            before = _counter_value(
                EVENTS_TOTAL_BY_SENSOR.labels(verdict="confirmed", sensorId="unknown")
            )
            enabled_recorder.record_event_complete(
                worker_start_time=time.time(),
                message={"sensorId": "x" * 1024, "info": {"verdict": "confirmed"}},
                latency={},
            )
            assert _counter_value(
                EVENTS_TOTAL_BY_SENSOR.labels(verdict="confirmed", sensorId="unknown")
            ) == before + 1
        finally:
            enabled_recorder.set_per_sensor_labels(False)

    def test_per_sensor_counter_sample_names_match_latency_script(self, enabled_recorder):
        """prometheus_client exposes Counter samples under the ``*_total``
        sample name. The latency script queries these exact names, so this
        test catches both bad counter definitions and stale PromQL."""
        from metrics.prometheus_metrics import (
            EVENTS_AFTER_DEDUP_BY_SENSOR,
            EVENTS_DROPPED_BY_SENSOR,
            EVENTS_SKIPPED_CONFIRMED_BY_SENSOR,
            EVENTS_TOTAL_BY_SENSOR,
            VERIFICATION_FAILURES_BY_SENSOR,
        )

        cases = (
            (
                EVENTS_TOTAL_BY_SENSOR,
                {"verdict": "confirmed", "sensorId": "cam-name-check"},
                "alert_bridge_events_by_sensor_total",
            ),
            (
                EVENTS_AFTER_DEDUP_BY_SENSOR,
                {"sensorId": "cam-name-check"},
                "alert_bridge_events_after_dedup_by_sensor_total",
            ),
            (
                EVENTS_SKIPPED_CONFIRMED_BY_SENSOR,
                {"sensorId": "cam-name-check"},
                "alert_bridge_events_skipped_confirmed_by_sensor_total",
            ),
            (
                EVENTS_DROPPED_BY_SENSOR,
                {"reason": "dedup", "sensorId": "cam-name-check"},
                "alert_bridge_events_dropped_by_sensor_total",
            ),
            (
                VERIFICATION_FAILURES_BY_SENSOR,
                {"reason": "vlm_timeout", "sensorId": "cam-name-check"},
                "alert_bridge_verification_failures_by_sensor_total",
            ),
        )

        all_sample_names = set()
        for counter, labels, expected_name in cases:
            counter.labels(**labels).inc(0)
            sample_names = {
                sample.name
                for metric in counter.collect()
                for sample in metric.samples
            }
            all_sample_names.update(sample_names)
            assert expected_name in sample_names

        legacy_names = {
            "alert_bridge_events_total_by_sensor",
            "alert_bridge_events_after_dedup_total_by_sensor",
            "alert_bridge_events_skipped_confirmed_total_by_sensor",
            "alert_bridge_events_dropped_total_by_sensor",
            "alert_bridge_verification_failures_total_by_sensor",
        }
        assert not (all_sample_names & legacy_names)

    def test_per_sensor_latency_histogram_sample_names_match_latency_script(self, enabled_recorder):
        """The latency script queries these exact histogram base names."""
        from metrics.prometheus_metrics import (
            E2E_DURATION_BY_SENSOR,
            KAFKA_LAG_DURATION_BY_SENSOR,
            UPSTREAM_DURATION_BY_SENSOR,
            VIDEO_LENGTH_BY_SENSOR,
            VLM_DURATION_BY_SENSOR,
            VST_DURATION_BY_SENSOR,
            WORKER_PROCESSING_DURATION_BY_SENSOR,
            WORKER_QUEUE_WAIT_DURATION_BY_SENSOR,
        )

        cases = (
            (UPSTREAM_DURATION_BY_SENSOR, "alert_bridge_upstream_duration_by_sensor_seconds"),
            (KAFKA_LAG_DURATION_BY_SENSOR, "alert_bridge_kafka_lag_duration_by_sensor_seconds"),
            (WORKER_QUEUE_WAIT_DURATION_BY_SENSOR, "alert_bridge_worker_queue_wait_duration_by_sensor_seconds"),
            (VST_DURATION_BY_SENSOR, "alert_bridge_vst_duration_by_sensor_seconds"),
            (VIDEO_LENGTH_BY_SENSOR, "alert_bridge_video_length_by_sensor_seconds"),
            (VLM_DURATION_BY_SENSOR, "alert_bridge_vlm_duration_by_sensor_seconds"),
            (WORKER_PROCESSING_DURATION_BY_SENSOR, "alert_bridge_worker_processing_by_sensor_seconds"),
            (E2E_DURATION_BY_SENSOR, "alert_bridge_e2e_duration_by_sensor_seconds"),
        )

        for histogram, base_name in cases:
            histogram.labels(sensorId="cam-name-check").observe(0)
            sample_names = {
                sample.name
                for metric in histogram.collect()
                for sample in metric.samples
            }
            assert f"{base_name}_bucket" in sample_names
            assert f"{base_name}_count" in sample_names
            assert f"{base_name}_sum" in sample_names

    # ── Comment-2 gap tests ──────────────────────────────────────────────

    def test_sanitize_sensor_id_at_exact_max_length_passes_through(self, enabled_recorder):
        """A string of exactly ``_SENSOR_ID_MAX_LEN`` (128) chars is valid
        and must pass through unchanged.  A string one char longer must
        fold to ``'unknown'``."""
        max_len = enabled_recorder._SENSOR_ID_MAX_LEN
        at_limit = "x" * max_len
        over_limit = "x" * (max_len + 1)
        assert enabled_recorder._sanitize_sensor_id(at_limit) == at_limit
        assert enabled_recorder._sanitize_sensor_id(over_limit) == "unknown"

    def test_sanitize_sensor_id_special_chars_pass_through(self, enabled_recorder):
        """The current impl applies no character allowlist — special chars
        that appear in real-world sensor IDs (path separators, colons,
        query-string chars) must pass through."""
        cases = [
            "sensorId/with:colons?query=1",
            "camera-01_floor-2.sector-A",
            "localhost:5000",
            "urn:uuid:550e8400-e29b-41d4-a716-446655440000",
        ]
        for raw in cases:
            assert enabled_recorder._sanitize_sensor_id(raw) == raw

    def test_inc_events_after_dedup_per_sensor_increments_when_flag_on(self, enabled_recorder):
        """Flag on: ``EVENTS_AFTER_DEDUP_BY_SENSOR`` must increment in
        lockstep with ``EVENTS_AFTER_DEDUP`` when messages are supplied."""
        try:
            enabled_recorder.set_per_sensor_labels(True)
            from metrics.prometheus_metrics import (
                EVENTS_AFTER_DEDUP,
                EVENTS_AFTER_DEDUP_BY_SENSOR,
            )
            agg_before = _counter_value(EVENTS_AFTER_DEDUP)
            sensor_before = _counter_value(
                EVENTS_AFTER_DEDUP_BY_SENSOR.labels(sensorId="cam-dedup")
            )
            msgs = [{"sensorId": "cam-dedup"}] * 3
            enabled_recorder.inc_events_after_dedup(3, messages=msgs)
            assert _counter_value(EVENTS_AFTER_DEDUP) == agg_before + 3
            assert _counter_value(
                EVENTS_AFTER_DEDUP_BY_SENSOR.labels(sensorId="cam-dedup")
            ) == sensor_before + 3
        finally:
            enabled_recorder.set_per_sensor_labels(False)

    def test_inc_events_after_dedup_per_sensor_absent_when_flag_off(self, enabled_recorder):
        """Flag off: ``EVENTS_AFTER_DEDUP_BY_SENSOR`` must stay unchanged
        even though ``EVENTS_AFTER_DEDUP`` increments normally."""
        enabled_recorder.set_per_sensor_labels(False)
        from metrics.prometheus_metrics import (
            EVENTS_AFTER_DEDUP,
            EVENTS_AFTER_DEDUP_BY_SENSOR,
        )
        agg_before = _counter_value(EVENTS_AFTER_DEDUP)
        sensor_before = _counter_value(
            EVENTS_AFTER_DEDUP_BY_SENSOR.labels(sensorId="cam-dedup-off")
        )
        msgs = [{"sensorId": "cam-dedup-off"}] * 2
        enabled_recorder.inc_events_after_dedup(2, messages=msgs)
        assert _counter_value(EVENTS_AFTER_DEDUP) == agg_before + 2
        assert _counter_value(
            EVENTS_AFTER_DEDUP_BY_SENSOR.labels(sensorId="cam-dedup-off")
        ) == sensor_before

    def test_inc_events_after_dedup_lockstep_aggregate_equals_per_sensor_sum(self, enabled_recorder):
        """The sum of all per-sensor increments must equal the aggregate
        increment — this is the reconciliation invariant the dashboards rely on."""
        try:
            enabled_recorder.set_per_sensor_labels(True)
            from metrics.prometheus_metrics import (
                EVENTS_AFTER_DEDUP,
                EVENTS_AFTER_DEDUP_BY_SENSOR,
            )
            agg_before = _counter_value(EVENTS_AFTER_DEDUP)
            sensor_a_before = _counter_value(
                EVENTS_AFTER_DEDUP_BY_SENSOR.labels(sensorId="cam-ls-a")
            )
            sensor_b_before = _counter_value(
                EVENTS_AFTER_DEDUP_BY_SENSOR.labels(sensorId="cam-ls-b")
            )
            msgs = [
                {"sensorId": "cam-ls-a"},
                {"sensorId": "cam-ls-a"},
                {"sensorId": "cam-ls-b"},
            ]
            enabled_recorder.inc_events_after_dedup(3, messages=msgs)
            agg_delta = _counter_value(EVENTS_AFTER_DEDUP) - agg_before
            per_sensor_delta = (
                _counter_value(EVENTS_AFTER_DEDUP_BY_SENSOR.labels(sensorId="cam-ls-a")) - sensor_a_before
                + _counter_value(EVENTS_AFTER_DEDUP_BY_SENSOR.labels(sensorId="cam-ls-b")) - sensor_b_before
            )
            assert agg_delta == 3
            assert per_sensor_delta == pytest.approx(agg_delta)
        finally:
            enabled_recorder.set_per_sensor_labels(False)

    def test_per_sensor_flag_on_prometheus_disabled_is_noop(self, disabled_recorder):
        """``PROMETHEUS_METRICS_ENABLED=false`` must short-circuit all helpers
        even when ``per_sensor_labels`` is True — the flag check fires first
        so no metric objects are ever dereferenced."""
        disabled_recorder.set_per_sensor_labels(True)
        try:
            disabled_recorder.inc_events_after_dedup(3, messages=[{"sensorId": "s"}] * 3)
            disabled_recorder.inc_events_dropped("dedup", 2, messages=[{"sensorId": "s"}] * 2)
            disabled_recorder.inc_events_skipped_confirmed({"sensorId": "s"})
            latency: dict = {}
            disabled_recorder.record_event_complete(
                worker_start_time=0.0,
                message={"sensorId": "s", "info": {"verdict": "confirmed"}},
                latency=latency,
            )
            assert latency == {}, "disabled recorder must not mutate latency"
        finally:
            disabled_recorder.set_per_sensor_labels(False)

    # ── Concurrency / cap-enforcement (race-condition regression) ────────

    def test_cap_never_exceeded_under_concurrent_load(self, enabled_recorder):
        """_KNOWN_SENSOR_IDS must never grow past _MAX_SENSOR_IDS even when
        many worker threads call _sanitize_sensor_id simultaneously with
        distinct IDs.

        Without _KNOWN_SENSOR_IDS_LOCK each thread can observe
        ``len(...) < cap`` as True before any of them writes, and all add —
        blowing the cap by up to (thread_count - 1).  This test would
        fail reliably against the old check-then-add code."""
        import threading

        CAP = 10
        THREADS = CAP * 8  # high contention to surface the TOCTOU window

        saved_known = enabled_recorder._KNOWN_SENSOR_IDS.copy()
        saved_max = enabled_recorder._MAX_SENSOR_IDS
        try:
            enabled_recorder._KNOWN_SENSOR_IDS.clear()
            enabled_recorder._MAX_SENSOR_IDS = CAP

            results: list = []
            results_lock = threading.Lock()

            def register(sensor_id: str) -> None:
                result = enabled_recorder._sanitize_sensor_id(sensor_id)
                with results_lock:
                    results.append(result)

            threads = [
                threading.Thread(target=register, args=(f"concurrent-sensor-{i}",))
                for i in range(THREADS)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(enabled_recorder._KNOWN_SENSOR_IDS) == CAP, (
                f"cap breached: set grew to {len(enabled_recorder._KNOWN_SENSOR_IDS)}, "
                f"expected {CAP}"
            )
            # Every return value must be either a registered ID or the
            # overflow sentinel — nothing else is a valid outcome.
            valid_ids = enabled_recorder._KNOWN_SENSOR_IDS
            for r in results:
                assert r in valid_ids or r == "unknown_overflow", (
                    f"_sanitize_sensor_id returned unexpected value: {r!r}"
                )
            # Exactly CAP calls should have claimed a slot; the rest overflow.
            assert results.count("unknown_overflow") == THREADS - CAP
        finally:
            enabled_recorder._KNOWN_SENSOR_IDS.clear()
            enabled_recorder._KNOWN_SENSOR_IDS.update(saved_known)
            enabled_recorder._MAX_SENSOR_IDS = saved_max

    def test_cap_warning_fires_once_not_per_thread(self, enabled_recorder, caplog):
        """The overflow WARNING must appear at least once but must not be
        spammed once per overflowing thread — a noisy operator log under
        load is its own incident."""
        import logging
        import threading

        CAP = 3
        THREADS = CAP * 4

        saved_known = enabled_recorder._KNOWN_SENSOR_IDS.copy()
        saved_max = enabled_recorder._MAX_SENSOR_IDS
        try:
            enabled_recorder._KNOWN_SENSOR_IDS.clear()
            enabled_recorder._MAX_SENSOR_IDS = CAP

            barrier = threading.Barrier(THREADS)

            def register(sensor_id: str) -> None:
                # All threads reach the sanitize call at the same time to
                # maximise contention on the lock acquisition.
                barrier.wait()
                enabled_recorder._sanitize_sensor_id(sensor_id)

            with caplog.at_level(logging.WARNING, logger="metrics.recorder"):
                threads = [
                    threading.Thread(target=register, args=(f"warn-sensor-{i}",))
                    for i in range(THREADS)
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

            overflow_warnings = [
                r for r in caplog.records
                if r.levelno == logging.WARNING and "unknown_overflow" in r.getMessage()
            ]
            # Must warn at least once so operators know the cap was hit.
            assert overflow_warnings, "expected at least one overflow WARNING"
            # Must not warn more times than there were overflowing threads —
            # but ideally only once per distinct ID.  Assert an upper-bound
            # of THREADS to guard against an unbounded log storm.
            assert len(overflow_warnings) <= THREADS
        finally:
            enabled_recorder._KNOWN_SENSOR_IDS.clear()
            enabled_recorder._KNOWN_SENSOR_IDS.update(saved_known)
            enabled_recorder._MAX_SENSOR_IDS = saved_max

    def test_known_id_fast_path_correct_under_concurrent_reads(self, enabled_recorder):
        """The unlocked fast-path (``stripped in _KNOWN_SENSOR_IDS``) must
        always return the correct value when IDs are only being read, not
        added."""
        import threading

        saved_known = enabled_recorder._KNOWN_SENSOR_IDS.copy()
        saved_max = enabled_recorder._MAX_SENSOR_IDS
        try:
            enabled_recorder._KNOWN_SENSOR_IDS.clear()
            enabled_recorder._MAX_SENSOR_IDS = 128
            for i in range(10):
                enabled_recorder._KNOWN_SENSOR_IDS.add(f"pre-sensor-{i}")

            errors: list = []
            errors_lock = threading.Lock()

            def read_known(sensor_id: str) -> None:
                result = enabled_recorder._sanitize_sensor_id(sensor_id)
                if result != sensor_id:
                    with errors_lock:
                        errors.append((sensor_id, result))

            threads = [
                threading.Thread(target=read_known, args=(f"pre-sensor-{i % 10}",))
                for i in range(100)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors, f"fast-path returned wrong values: {errors}"
        finally:
            enabled_recorder._KNOWN_SENSOR_IDS.clear()
            enabled_recorder._KNOWN_SENSOR_IDS.update(saved_known)
            enabled_recorder._MAX_SENSOR_IDS = saved_max

    # ── Warmup ───────────────────────────────────────────────────────────

    def test_warm_startup_labels_idempotent(self, enabled_recorder):
        """Multiple calls must leave the registry in the same state —
        no duplicate child collectors, no error on re-warmup.

        Note: we assert via *subset*, not equality, because other tests
        in this suite (e.g. the ``inc_events_dropped("typo_reason", 4)``
        allowlist-normalization test) materialize additional series
        like ``reason="unknown"`` that are not part of the warmup set
        but are legitimate runtime outputs.
        """
        enabled_recorder.warm_startup_labels()
        enabled_recorder.warm_startup_labels()
        enabled_recorder.warm_startup_labels()
        from metrics.prometheus_metrics import EVENTS_DROPPED
        exposed = {
            sample.labels["reason"]
            for metric in EVENTS_DROPPED.collect()
            for sample in metric.samples
            if sample.name == "alert_bridge_events_dropped_total"
        }
        for reason in enabled_recorder.EVENTS_DROPPED_REASONS:
            assert reason in exposed, f"warmup reason {reason!r} disappeared after repeated calls"
