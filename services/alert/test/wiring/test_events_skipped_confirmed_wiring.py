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

"""Integration tests for the C9 ``EVENTS_SKIPPED_CONFIRMED`` counter wiring.

Verifies that ``_set_message_id_and_should_skip`` calls
``inc_events_skipped_confirmed()`` exactly when — and only when — it
returns ``True`` after detecting an already-confirmed verdict in Redis.
Other early-return paths (missing fingerprint, no redis handler, Redis
failure, no-confirmed-verdict) must NOT increment the counter, otherwise
the C2 reconciliation identity would be violated in the opposite
direction (over-counting skips).

Run with: pytest test/test_events_skipped_confirmed_wiring.py -v
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

# Provide Mock spies for every recorder helper the production module imports.
sys.modules['metrics'].PROMETHEUS_ENABLED = False
# The production module imports every one of these names at startup;
# the stub must expose each as a Mock or the import fails.
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
def spy_skipped_confirmed(monkeypatch):
    """Replace the recorder helper with a spy so we can assert on calls
    without running the real Prometheus increment path."""
    spy = Mock()
    monkeypatch.setattr(eavw, "inc_events_skipped_confirmed", spy)
    return spy


def _make_stub():
    stub = Mock(spec=AnomalyEnhancer)
    stub.redis_handler = Mock()
    stub._compute_fingerprint = Mock(return_value="fp-abc")
    stub._run_redis_operation_with_mode = Mock()
    return stub


def _msg():
    return {
        "sensorId": "cam-1",
        "category": "loitering",
        "timestamp": "2025-01-01T00:00:00Z",
        "end": "2025-01-01T00:00:02Z",
    }


# ── Tests ────────────────────────────────────────────────────────────────


class TestCounterFiresOnConfirmedSkip:
    """The one path that should increment the counter: Redis reports a
    confirmed verdict already exists for this fingerprint."""

    def test_confirmed_verdict_triggers_counter(self, spy_skipped_confirmed):
        stub = _make_stub()
        stub._run_redis_operation_with_mode.return_value = True  # confirmed
        msg = _msg()

        result = AnomalyEnhancer._set_message_id_and_should_skip(
            stub, message=msg, sensor_id="cam-1",
        )

        assert result is True
        # Post-C21: the helper now takes the message for per-sensor
        # routing. We accept any positional signature here; the
        # per-sensor test coverage lives in ``test/metrics/test_metrics_recorder.py``.
        assert spy_skipped_confirmed.call_count == 1
        # Fingerprint must still be stamped onto the message before
        # the early return.
        assert msg["Id"] == "fp-abc"

    def test_multiple_confirmed_skips_each_increment(self, spy_skipped_confirmed):
        stub = _make_stub()
        stub._run_redis_operation_with_mode.return_value = True

        for _ in range(3):
            AnomalyEnhancer._set_message_id_and_should_skip(
                stub, message=_msg(), sensor_id="cam-1",
            )
        assert spy_skipped_confirmed.call_count == 3


class TestCounterDoesNotFireOnOtherPaths:
    """Every non-skip early return must leave the counter untouched —
    otherwise we'd over-count skips and silently break the C2
    reconciliation identity in the opposite direction."""

    def test_no_counter_when_no_fingerprint(self, spy_skipped_confirmed):
        stub = _make_stub()
        stub._compute_fingerprint.return_value = None  # no fingerprint
        result = AnomalyEnhancer._set_message_id_and_should_skip(
            stub, message=_msg(), sensor_id="cam-1",
        )
        assert result is False
        spy_skipped_confirmed.assert_not_called()

    def test_no_counter_when_redis_handler_missing(self, spy_skipped_confirmed):
        stub = _make_stub()
        stub.redis_handler = None
        result = AnomalyEnhancer._set_message_id_and_should_skip(
            stub, message=_msg(), sensor_id="cam-1",
        )
        assert result is False
        spy_skipped_confirmed.assert_not_called()

    def test_no_counter_when_redis_check_raises(self, spy_skipped_confirmed):
        """Redis outage on the confirmed-verdict check: the helper logs
        and continues processing (treating the event as new). The
        skipped counter must NOT increment — the event is about to run
        the full pipeline, so counting it as skipped would double-count."""
        stub = _make_stub()
        stub._run_redis_operation_with_mode.side_effect = RuntimeError("redis down")
        result = AnomalyEnhancer._set_message_id_and_should_skip(
            stub, message=_msg(), sensor_id="cam-1",
        )
        assert result is False
        spy_skipped_confirmed.assert_not_called()

    def test_no_counter_when_verdict_not_confirmed(self, spy_skipped_confirmed):
        """The common happy path: new event, Redis says no confirmed
        verdict exists yet. Event will proceed into the pipeline and
        be counted in ``EVENTS_TOTAL`` on completion; nothing to count
        as skipped."""
        stub = _make_stub()
        stub._run_redis_operation_with_mode.return_value = False
        result = AnomalyEnhancer._set_message_id_and_should_skip(
            stub, message=_msg(), sensor_id="cam-1",
        )
        assert result is False
        spy_skipped_confirmed.assert_not_called()


class TestReconciliationInvariantHolds:
    """End-to-end: for a mixed batch, the number of skip-counter calls
    equals the number of events whose fingerprint had a confirmed
    verdict. This directly supports the C2 identity
    ``EVENTS_AFTER_DEDUP = EVENTS_TOTAL + EVENTS_SKIPPED_CONFIRMED + ...``
    """

    def test_mixed_batch_skip_count_matches_confirmed_hits(self, spy_skipped_confirmed):
        stub = _make_stub()
        # 5 events: 2 are confirmed-skips, 3 are new.
        stub._run_redis_operation_with_mode.side_effect = [True, False, True, False, False]

        skips = 0
        for i in range(5):
            was_skipped = AnomalyEnhancer._set_message_id_and_should_skip(
                stub, message=_msg(), sensor_id=f"cam-{i}",
            )
            if was_skipped:
                skips += 1

        assert skips == 2
        assert spy_skipped_confirmed.call_count == 2
