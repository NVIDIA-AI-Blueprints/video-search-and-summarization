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

"""Unit tests for the C23 ``_complete_event_after_publish`` helper.

Before C23, ``_record_event_complete`` fired synchronously after
``_publish_*_with_mode`` at every exit site. In **async elastic sink
mode** the publish helper returns a ``Future`` immediately — so the
recorder ran before ES actually finished writing. ``E2E_DURATION``
silently undercounted by the async-sink queue wait + ES-write time,
and ``elasticReadyAt`` on the shared latency dict was stamped minutes
before the document was actually readable in ES.

After C23, each of the 8 VST/URL/VLM exit sites calls the new helper
``_complete_event_after_publish(publish_future, ...)``. The helper:

  * Fires the recorder **inline** when ``publish_future`` is ``None``
    (sync sink mode) — same observable behaviour as pre-C23.
  * Attaches the recorder as a **done-callback** on the future when
    async mode returns one — deferring ``elasticReadyAt`` stamp and
    ``E2E_DURATION`` observation until the async sink write actually
    completes.

These tests exercise the helper directly with a stubbed enhancer and
cover both branches plus the race where the future is already done by
the time the helper runs.

Run with: pytest test/test_publish_completion_callback.py -v
"""

import logging
import os
import sys
import threading
import types
from concurrent.futures import Future
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


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def spy_record(monkeypatch):
    """Replace ``record_event_complete`` (imported into ``eavw``) with
    a spy so we can inspect exactly when / with what arguments the
    recorder fires."""
    spy = Mock()
    monkeypatch.setattr(eavw, "record_event_complete", spy)
    return spy


def _stub_enhancer():
    """Build a stub AnomalyEnhancer wired only enough to call
    ``_complete_event_after_publish`` directly. The helper is a regular
    method so we invoke it via ``AnomalyEnhancer._complete_event_after_publish(stub, ...)``."""
    return Mock(spec=AnomalyEnhancer)


# ── Sync-mode behaviour ──────────────────────────────────────────────────


class TestSyncModeFiresInline:
    """When ``_submit_sink_operation_with_mode`` ran sync (returned
    ``None``), the publish has already finished. The helper must fire
    the recorder inline — matching the pre-C23 semantics so existing
    dashboards don't shift when this change lands."""

    def test_none_future_fires_recorder_immediately(self, spy_record):
        stub = _stub_enhancer()
        msg = {"sensorId": "cam-1", "info": {"verdict": "confirmed"}}
        lat = {}

        AnomalyEnhancer._complete_event_after_publish(
            stub,
            publish_future=None,
            worker_start_time=123.0,
            message=msg,
            latency=lat,
            failure_reason="url_validation",
        )

        spy_record.assert_called_once_with(
            123.0, msg, lat, failure_reason="url_validation",
        )

    def test_none_future_fires_with_no_failure_reason(self, spy_record):
        """Happy-path (successful publish) uses ``failure_reason=None``.
        The default must propagate unchanged."""
        stub = _stub_enhancer()
        AnomalyEnhancer._complete_event_after_publish(
            stub,
            publish_future=None,
            worker_start_time=0.0,
            message={},
            latency={},
        )
        spy_record.assert_called_once()
        assert spy_record.call_args.kwargs.get("failure_reason") is None


# ── Async-mode behaviour ─────────────────────────────────────────────────


class TestAsyncModeDefersToDoneCallback:
    """When the sink submit returned a ``Future``, the helper must NOT
    fire the recorder inline. Dashboards would see the old undercount
    again if this ever regresses — this test is the direct guard."""

    def test_recorder_does_not_fire_while_future_pending(self, spy_record):
        stub = _stub_enhancer()
        future = Future()  # created pending; we never call set_result

        AnomalyEnhancer._complete_event_after_publish(
            stub,
            publish_future=future,
            worker_start_time=0.0,
            message={"sensorId": "cam-1"},
            latency={},
            failure_reason="vlm_timeout",
        )

        # Critical: recorder has NOT fired yet — this is the C23 fix.
        spy_record.assert_not_called()

    def test_recorder_fires_when_future_resolves(self, spy_record):
        stub = _stub_enhancer()
        future = Future()

        AnomalyEnhancer._complete_event_after_publish(
            stub,
            publish_future=future,
            worker_start_time=42.0,
            message={"sensorId": "cam-2"},
            latency={},
            failure_reason="vlm_server_error",
        )

        assert spy_record.call_count == 0
        # Now simulate the async sink finishing its write.
        future.set_result(None)

        spy_record.assert_called_once()
        args, kwargs = spy_record.call_args
        assert args[0] == 42.0
        assert args[1] == {"sensorId": "cam-2"}
        assert kwargs.get("failure_reason") == "vlm_server_error"

    def test_recorder_fires_when_future_resolves_with_exception(self, spy_record):
        """The sink write may itself fail. The recorder still needs to
        fire so the VERIFICATION_FAILURES counter and the event count
        are accurate — leaving it un-called would create a silent event
        loss correlated with sink outages."""
        stub = _stub_enhancer()
        future = Future()

        AnomalyEnhancer._complete_event_after_publish(
            stub,
            publish_future=future,
            worker_start_time=1.0,
            message={},
            latency={},
            failure_reason="vlm_connection_error",
        )

        future.set_exception(RuntimeError("ES down"))

        spy_record.assert_called_once()
        assert spy_record.call_args.kwargs.get("failure_reason") == "vlm_connection_error"

    def test_already_completed_future_fires_immediately(self, spy_record):
        """Edge case: the sink future may already be done by the time
        the helper attaches the callback (rare but possible when a pool
        thread races ahead of the caller). ``add_done_callback`` runs
        the callback immediately in that case — the helper must not
        hang, must not skip, must fire exactly once."""
        stub = _stub_enhancer()
        future = Future()
        future.set_result("pre-completed")

        AnomalyEnhancer._complete_event_after_publish(
            stub,
            publish_future=future,
            worker_start_time=7.0,
            message={},
            latency={},
            failure_reason="unknown",
        )

        spy_record.assert_called_once()

    def test_recorder_fires_exactly_once_per_event(self, spy_record):
        """Regression guard: the helper must call the recorder exactly
        once, no matter how many state changes the future goes
        through. ``add_done_callback`` fires every registered callback
        exactly once on completion."""
        stub = _stub_enhancer()
        future = Future()

        AnomalyEnhancer._complete_event_after_publish(
            stub,
            publish_future=future,
            worker_start_time=0.0,
            message={},
            latency={},
            failure_reason="vst_timeout",
        )

        future.set_result(None)
        # Trying to set a result again would raise, so we don't test
        # that — the spy count is the authoritative check.
        assert spy_record.call_count == 1


# ── Latency-dict mutation safety ─────────────────────────────────────────


class TestLatencyDictSurvivesUntilCallback:
    """The closure must retain a reference to the ``latency`` dict so
    it's still mutable when the done-callback fires on the sink thread.
    This is the thread-safety story the C23 implementation comment
    describes; the test locks it down."""

    def test_latency_dict_reference_survives(self, spy_record):
        stub = _stub_enhancer()
        future = Future()
        latency = {"timestamps": {"kafkaPublishedAt": "2025-01-01T00:00:00Z"}}

        AnomalyEnhancer._complete_event_after_publish(
            stub,
            publish_future=future,
            worker_start_time=0.0,
            message={},
            latency=latency,
            failure_reason=None,
        )

        # Drop the caller's reference — the closure must still hold one.
        del latency

        future.set_result(None)

        # If the dict had been GC'd the recorder would have been called
        # with something unusable. Prove the exact reference the caller
        # passed in made it through.
        spy_record.assert_called_once()
        forwarded = spy_record.call_args.args[2]
        assert forwarded["timestamps"]["kafkaPublishedAt"] == "2025-01-01T00:00:00Z"
