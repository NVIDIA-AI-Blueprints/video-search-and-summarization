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

"""Integration tests for the C25 pre-processing failure wiring.

Before C25, ``_set_message_id_and_should_skip`` ran OUTSIDE the outer
``try/except`` envelope in ``_process_single_message``. A Redis
failure on the confirmed-verdict check bubbled straight out of the
per-event function into ``process_batch_vlm``'s generic exception
handler, which logs but never touches Prometheus. Events silently
vanished from ``EVENTS_TOTAL`` during any Redis incident — exactly
the "dashboard goes quiet and nobody knows why" failure mode the
reviewer flagged.

After C25:

  * A small targeted ``try/except`` wraps the skip check.
  * Redis-shaped exceptions (class name contains "redis") route to
    ``failure_reason="redis_unavailable"``.
  * Any other exception (defensive fallback) folds into ``"unknown"``.
  * The event is counted in ``EVENTS_TOTAL{verdict="unknown"}`` plus
    ``VERIFICATION_FAILURES{reason=…}`` — restoring the C2
    reconciliation identity even during Redis outages.

These tests cover both the classifier in isolation (``_classify_pre_processing_failure``)
and the end-to-end wiring via ``_process_single_message``.

Run with: pytest test/test_pre_processing_failure_wiring.py -v
"""

import logging
import os
import sys
import threading
import types
from unittest.mock import Mock

import pytest


# ── Module-level setup ───────────────────────────────────────────────────
os.environ.setdefault("PROMETHEUS_METRICS_ENABLED", "false")

_stub_modules = [
    'its_redis', 'its_redis.redis_handler',
    'mdx', 'mdx.anomaly', 'mdx.anomaly.event_bridge_factory',
    'mdx.anomaly.sink', 'mdx.anomaly.sink.vlm_enhanced_sink',
    'mdx.anomaly.utils', 'mdx.anomaly.utils.elastic_ready',
    'handlers', 'handlers.enrichment', 'handlers.direct_media',
    'handlers.prompt_handler', 'handlers.prompt_handler.alert_type_config_loader',
    'handlers.async_dispatch_mixin',
    'handlers.async_external_io_mixin',
    'handlers.async_vlm_mode_mixin',
    'utils.logging_config',
    'utils.schema_util',
    'vlm.warmup',
    'vss',
    'metrics', 'metrics.prometheus_metrics', 'metrics.recorder',
]
for mod_name in _stub_modules:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

sys.modules['handlers'].__path__ = []
sys.modules['handlers.prompt_handler'].__path__ = []
sys.modules['metrics'].__path__ = []

sys.modules['its_redis.redis_handler'].RedisHandler = Mock
sys.modules['mdx.anomaly.event_bridge_factory'].EventBridgeFactory = Mock()
sys.modules['mdx.anomaly.sink.vlm_enhanced_sink'].build_vlm_enhanced_sink = Mock()
sys.modules['mdx.anomaly.utils.elastic_ready'].generate_alert_fingerprint = Mock(return_value='fp')
sys.modules['mdx.anomaly.utils.elastic_ready'].generate_incident_fingerprint = Mock(return_value='fp')
sys.modules['handlers.enrichment'].EnrichmentProcessor = Mock
sys.modules['handlers.direct_media'].DirectMediaHandler = Mock
sys.modules['handlers.prompt_handler.alert_type_config_loader'].AlertTypeConfig = Mock
sys.modules['handlers.prompt_handler.alert_type_config_loader'].AlertTypeConfigLoader = Mock

class _AsyncDispatchMixinStub: pass
class _AsyncExternalIOMixinStub: pass
class _AsyncVLMModeMixinStub: pass
sys.modules['handlers.async_dispatch_mixin'].AsyncDispatchMixin = _AsyncDispatchMixinStub
sys.modules['handlers.async_external_io_mixin'].AsyncExternalIOMixin = _AsyncExternalIOMixinStub
sys.modules['handlers.async_vlm_mode_mixin'].AsyncVLMModeMixin = _AsyncVLMModeMixinStub

sys.modules['utils.logging_config'].setup_logging = Mock()
sys.modules['utils.logging_config'].get_logger = lambda name: logging.getLogger(name)
sys.modules['utils.logging_config'].enforce_log_level = Mock()
sys.modules['utils.schema_util'].protobuf_anomalies_to_json_string_list = Mock()
sys.modules['vlm.warmup'].warmup_vlm = Mock()
sys.modules['vlm.warmup'].WARMUP_VIDEO = '/tmp/fake.mp4'
sys.modules['vss'].VSSHandler = Mock

sys.modules['metrics'].PROMETHEUS_ENABLED = False
for _recorder_attr in (
    "inc_events_after_dedup",
    "inc_events_dropped",
    "inc_events_skipped_confirmed",
    "observe_pipeline_latency",
    "observe_video_length",
    "observe_vlm_duration",
    "observe_vst_duration",
    "record_event_complete",
    "set_per_sensor_labels",
    "warm_startup_labels",
):
    setattr(sys.modules['metrics.recorder'], _recorder_attr, Mock())

import enhance_alert_with_vlm as eavw  # noqa: E402
from enhance_alert_with_vlm import AnomalyEnhancer  # noqa: E402


# ── Classifier tests ─────────────────────────────────────────────────────


class _RedisConnectionError(Exception):
    """Fake Redis-shaped exception — class name contains ``Redis``.

    The classifier duck-types on the MRO rather than importing the
    actual ``redis`` package, so this reflects the real-world
    matching rules."""


class _RedisTimeoutError(RuntimeError):
    """Another Redis-shaped shape but with a non-Exception base."""


class TestClassifierMapsRedisExceptions:
    def test_direct_redis_class_maps_to_redis_unavailable(self):
        reason = AnomalyEnhancer._classify_pre_processing_failure(
            _RedisConnectionError("cannot connect"),
        )
        assert reason == "redis_unavailable"

    def test_redis_class_with_different_base_maps_to_redis_unavailable(self):
        reason = AnomalyEnhancer._classify_pre_processing_failure(
            _RedisTimeoutError("timed out"),
        )
        assert reason == "redis_unavailable"

    def test_nested_in_mro_still_matches(self):
        """A subclass whose parent contains 'redis' must still classify
        — operators who subclass Redis exceptions shouldn't silently
        fall through to ``unknown``."""
        class WrappedRedisError(_RedisConnectionError):
            pass
        reason = AnomalyEnhancer._classify_pre_processing_failure(WrappedRedisError())
        assert reason == "redis_unavailable"

    def test_case_insensitive_match(self):
        """Class name match is case-insensitive — ``Redis`` and
        ``redis`` and ``REDISClientError`` all route to the same
        reason."""
        class REDISError(Exception):
            pass
        assert AnomalyEnhancer._classify_pre_processing_failure(REDISError()) == "redis_unavailable"


class TestClassifierFallsBackToUnknown:
    def test_non_redis_runtime_error_maps_to_unknown(self):
        assert AnomalyEnhancer._classify_pre_processing_failure(RuntimeError("boom")) == "unknown"

    def test_value_error_maps_to_unknown(self):
        assert AnomalyEnhancer._classify_pre_processing_failure(ValueError("bad")) == "unknown"

    def test_attribute_error_maps_to_unknown(self):
        """A hypothetical bug in ``_compute_fingerprint`` would surface
        as AttributeError. Folding to ``unknown`` keeps the event
        counted without lying about the cause."""
        assert AnomalyEnhancer._classify_pre_processing_failure(AttributeError()) == "unknown"


# ── Wiring tests ─────────────────────────────────────────────────────────


@pytest.fixture
def spy_record(monkeypatch):
    spy = Mock()
    monkeypatch.setattr(eavw, "record_event_complete", spy)
    return spy


def _make_stub():
    stub = Mock(spec=AnomalyEnhancer)
    stub._set_message_id_and_should_skip = Mock()
    stub._classify_pre_processing_failure = AnomalyEnhancer._classify_pre_processing_failure
    stub.redis_handler = None
    stub.prompt_manager = Mock()
    stub.prompt_manager.get_prompts_for_message.return_value = (None, None)
    return stub


def _msg():
    return {
        "sensorId": "cam-1",
        "category": "loitering",
        "timestamp": "2025-01-01T00:00:00Z",
        "end": "2025-01-01T00:00:02Z",
    }


class TestSkipCheckExceptionTriggersRedisUnavailable:
    def test_redis_error_in_skip_check_fires_failure_counter(self, spy_record):
        """The whole point of C25: a Redis failure on the skip check
        must not disappear silently. The recorder spy sees exactly one
        call with ``failure_reason="redis_unavailable"``."""
        stub = _make_stub()
        stub._set_message_id_and_should_skip.side_effect = _RedisConnectionError("down")

        AnomalyEnhancer._process_single_message(
            stub,
            worker_id=0,
            message=_msg(),
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:01Z",
        )

        spy_record.assert_called_once()
        assert spy_record.call_args.kwargs.get("failure_reason") == "redis_unavailable"

    def test_skip_exception_does_not_reach_downstream(self, spy_record):
        """Sanity: if the skip-check fires, no VLM/VST work runs.
        The exception was handled; the function returned. Calling a
        downstream method (e.g. ``get_prompts_for_message``) would
        indicate the exception was not actually caught."""
        stub = _make_stub()
        stub._set_message_id_and_should_skip.side_effect = _RedisConnectionError("down")

        AnomalyEnhancer._process_single_message(
            stub,
            worker_id=0,
            message=_msg(),
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:01Z",
        )

        stub.prompt_manager.get_prompts_for_message.assert_not_called()

    def test_non_redis_exception_in_skip_check_folds_to_unknown(self, spy_record):
        """A defensive-fallback test: a hypothetical RuntimeError in
        ``_set_message_id_and_should_skip`` should still produce a
        counter — just with the ``unknown`` reason."""
        stub = _make_stub()
        stub._set_message_id_and_should_skip.side_effect = RuntimeError("weird")

        AnomalyEnhancer._process_single_message(
            stub,
            worker_id=0,
            message=_msg(),
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:01Z",
        )

        spy_record.assert_called_once()
        assert spy_record.call_args.kwargs.get("failure_reason") == "unknown"


class TestSkipCheckHappyPathsUnaffected:
    """C25's wrapper must not break either of the two non-exception
    outcomes — normal pass-through and normal skip."""

    def test_skip_check_returns_true_no_failure_counter(self, spy_record):
        """When the fingerprint is already confirmed, the function
        returns normally. No failure counter should fire."""
        stub = _make_stub()
        stub._set_message_id_and_should_skip.return_value = True

        AnomalyEnhancer._process_single_message(
            stub,
            worker_id=0,
            message=_msg(),
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:01Z",
        )

        # The ``_set_message_id_and_should_skip`` helper itself calls
        # ``inc_events_skipped_confirmed`` via the recorder (C9). The
        # spy on ``record_event_complete`` must NOT have fired — a
        # confirmed-skip event never reaches it.
        spy_record.assert_not_called()

    def test_skip_check_returns_false_proceeds_to_prompt_lookup(self, spy_record):
        """The normal "new event" path: skip check returns False,
        function proceeds to ``get_prompts_for_message``. No failure
        counter on this branch — the no-prompt case below has its own
        counter via C10."""
        stub = _make_stub()
        stub._set_message_id_and_should_skip.return_value = False
        # Flow through to the no_prompt branch which WILL fire record.

        AnomalyEnhancer._process_single_message(
            stub,
            worker_id=0,
            message=_msg(),
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:01Z",
        )

        stub.prompt_manager.get_prompts_for_message.assert_called_once()
        # The no-prompt branch did call the recorder, but with
        # ``no_prompt`` not ``redis_unavailable``.
        assert spy_record.call_count == 1
        assert spy_record.call_args.kwargs.get("failure_reason") == "no_prompt"


class TestLatencyDictAvailableOnEarlyExit:
    """The C25 wrapper needs a valid ``latency`` dict to pass to the
    recorder. Before the fix, ``latency`` was initialized AFTER the
    skip check — this test locks in that the reordering stuck."""

    def test_latency_dict_has_timestamps_on_redis_failure(self, spy_record):
        stub = _make_stub()
        stub._set_message_id_and_should_skip.side_effect = _RedisConnectionError("down")

        AnomalyEnhancer._process_single_message(
            stub,
            worker_id=0,
            message=_msg(),
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:01Z",
        )

        spy_record.assert_called_once()
        forwarded_latency = spy_record.call_args.args[2]
        assert "timestamps" in forwarded_latency
        ts = forwarded_latency["timestamps"]
        assert ts["kafkaPublishedAt"] == "2024-12-31T23:59:59Z"
        assert ts["kafkaConsumedAt"] == "2025-01-01T00:00:00Z"
        assert ts["workerAssignedAt"] == "2025-01-01T00:00:01Z"
