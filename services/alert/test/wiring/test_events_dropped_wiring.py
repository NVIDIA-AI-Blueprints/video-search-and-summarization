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

"""Integration tests for the C22 drop-counter wiring.

Verifies that ``process_batch_vlm`` and ``_apply_vlm_rate_limit`` call
``inc_events_dropped(reason, count)`` at each of the three filter sites
with the correct arithmetic:

  * ``filter_by_end_time_delta`` → ``reason="end_time_delta"``
  * ``filter_new_events``        → ``reason="dedup"``  (scoped to dedup
                                    alone, not conflated with end-time-delta
                                    drops — this covers the latent bug
                                    C22 fixed as a side-effect)
  * ``_apply_vlm_rate_limit``    → ``reason="rate_limit"``

The unit tests in ``test/metrics/test_metrics_recorder.py`` already exercise the
``inc_events_dropped`` helper itself; these tests prove the call sites
in ``enhance_alert_with_vlm`` are wired up correctly and scoped to the
right filter.

Run with: pytest test/test_events_dropped_wiring.py -v
"""

import logging
import json
import os
import sys
import threading
import types
from unittest.mock import Mock

import pytest


# ── Module-level setup ───────────────────────────────────────────────────
# Mirrors the stubbing pattern used by ``test_vst_error_reporting.py`` so
# we can import ``enhance_alert_with_vlm`` without bringing in the heavy
# Redis / Kafka / VLM clients. We intercept ``inc_events_dropped`` and
# ``inc_events_after_dedup`` inside the production module namespace so we
# do not need the real Prometheus registry for these assertions — the
# helpers themselves are covered by ``test/metrics/test_metrics_recorder.py``.

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
_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.modules['mdx'].__path__ = [os.path.join(_REPO_ROOT, 'mdx')]
sys.modules['mdx.anomaly'].__path__ = [os.path.join(_REPO_ROOT, 'mdx', 'anomaly')]

sys.modules['its_redis.redis_handler'].RedisHandler = Mock
sys.modules['mdx.anomaly.event_bridge_factory'].EventBridgeFactory = Mock()
sys.modules['mdx.anomaly.sink.vlm_enhanced_sink'].build_vlm_enhanced_sink = Mock()
sys.modules['mdx.anomaly.utils.elastic_ready'].generate_alert_fingerprint = Mock(return_value='fp')
sys.modules['mdx.anomaly.utils.elastic_ready'].generate_incident_fingerprint = Mock(return_value='fp')
sys.modules['handlers.enrichment'].EnrichmentProcessor = Mock
sys.modules['handlers.direct_media'].DirectMediaHandler = Mock
sys.modules['handlers.prompt_handler.alert_type_config_loader'].AlertTypeConfig = Mock
sys.modules['handlers.prompt_handler.alert_type_config_loader'].AlertTypeConfigLoader = Mock

# The three async mixins are subclassed by AnomalyEnhancer, so we need
# real (empty) class objects here — a ``Mock`` instance cannot be used
# as a base class. Each mixin must be a **distinct** class because
# Python rejects duplicate base classes in a MRO.
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

# Stub the metrics package so enhance_alert_with_vlm imports them as
# plain Mocks. We then swap the production module's references for
# spies that capture every call.
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
def spy_counters(monkeypatch):
    """Replace the two batch-level counter helpers with spies scoped to
    the ``enhance_alert_with_vlm`` module namespace. Using monkeypatch
    means each test starts from a clean slate regardless of ordering.
    """
    inc_dropped = Mock()
    inc_after_dedup = Mock()
    monkeypatch.setattr(eavw, "inc_events_dropped", inc_dropped)
    monkeypatch.setattr(eavw, "inc_events_after_dedup", inc_after_dedup)
    return inc_dropped, inc_after_dedup


def _make_enhancer():
    """Build a stub with just enough state to let ``process_batch_vlm``
    run end-to-end through the three filter sites."""
    stub = Mock(spec=AnomalyEnhancer)
    stub.config = {
        'alert_agent': {'verify_only_finished_events': False},
    }
    stub.source_type = 'kafka'
    stub.async_io_enabled = False
    stub.vst_pass_through_mode = False
    stub._vst_handler = Mock()
    stub._vlm_rate_limit_enabled = False

    # The three filter stubs. Each test reconfigures ``_run_redis_operation_with_mode``
    # via ``side_effect`` so we can shape each filter's output independently.
    stub.redis_handler = Mock()
    stub._run_redis_operation_with_mode = Mock()

    # ``_process_single_message_with_mode`` is a no-op for this test —
    # we only care about what happened BEFORE per-message dispatch.
    stub._process_single_message_with_mode = Mock()
    stub._apply_vlm_rate_limit = lambda msgs: msgs  # pass-through by default
    return stub


def _msg(idx):
    """Build a minimal Behavior-shaped message the pipeline will accept."""
    return {
        'sensorId': f'cam-{idx}',
        'category': 'loitering',
        'timestamp': f'2025-01-01T00:00:{idx:02d}Z',
        'end': f'2025-01-01T00:00:{idx + 2:02d}Z',
        'objectIds': [],
    }


def _patch_parse(monkeypatch, messages):
    """Short-circuit the protobuf/JSON decode step so the test controls
    the exact message list ``process_batch_vlm`` sees after parsing."""
    monkeypatch.setattr(
        eavw,
        "protobuf_anomalies_to_json_string_list",
        lambda *args, **kwargs: [__import__('json').dumps(m) for m in messages],
    )
    # normalize_alert_message is idempotent for our shapes; stub to identity.
    monkeypatch.setattr(eavw, "normalize_alert_message", lambda m: m)


# ── C22 wiring tests ─────────────────────────────────────────────────────


class TestEndTimeDeltaDropCount:
    def test_counts_events_dropped_by_end_time_delta(self, spy_counters, monkeypatch):
        inc_dropped, inc_after_dedup = spy_counters
        stub = _make_enhancer()
        raw = [_msg(i) for i in range(5)]
        _patch_parse(monkeypatch, raw)

        # end_time_delta drops 2 → 3 remain; dedup keeps all 3.
        stub._run_redis_operation_with_mode.side_effect = [
            raw[:3],   # filter_by_end_time_delta result (2 dropped)
            raw[:3],   # filter_new_events result (0 dropped)
        ]

        AnomalyEnhancer.process_batch_vlm(stub, worker_id=0, messages=raw, message_type='Behavior')

        # Every reason must be called exactly once (zero-count calls
        # still happen — the helper short-circuits internally).
        end_time_calls = [c for c in inc_dropped.call_args_list if c.args[0] == "end_time_delta"]
        assert len(end_time_calls) == 1
        assert end_time_calls[0].args[1] == 2

    def test_zero_drops_still_reports_zero(self, spy_counters, monkeypatch):
        """Filter ran but dropped nothing — still records a 0-count call
        so operators can see the filter is healthy (inc_events_dropped
        no-ops internally on 0, but the call-site contract is to always
        call it)."""
        inc_dropped, _ = spy_counters
        stub = _make_enhancer()
        raw = [_msg(i) for i in range(3)]
        _patch_parse(monkeypatch, raw)

        stub._run_redis_operation_with_mode.side_effect = [
            raw,  # filter_by_end_time_delta kept everything
            raw,  # filter_new_events kept everything
        ]

        AnomalyEnhancer.process_batch_vlm(stub, worker_id=0, messages=raw, message_type='Behavior')

        end_time_calls = [c for c in inc_dropped.call_args_list if c.args[0] == "end_time_delta"]
        assert len(end_time_calls) == 1
        assert end_time_calls[0].args[1] == 0


class TestDedupDropCount:
    def test_counts_events_dropped_by_dedup_only(self, spy_counters, monkeypatch):
        """Scoped to the dedup filter — this test is the regression guard
        for the latent bug C22 also fixed. Previously ``dedup_dropped``
        was computed as ``len(parsed_messages) - len(dedup_filtered)``,
        which conflated end-time-delta drops with dedup drops.
        """
        inc_dropped, _ = spy_counters
        stub = _make_enhancer()
        raw = [_msg(i) for i in range(10)]
        _patch_parse(monkeypatch, raw)

        # end_time_delta drops 3 → 7 remain.
        # dedup drops 2 of the 7 remaining → 5 remain.
        # Correct behavior: EVENTS_DROPPED{reason="dedup"} increments by 2, NOT by 5.
        stub._run_redis_operation_with_mode.side_effect = [
            raw[:7],   # after end_time_delta
            raw[:5],   # after dedup
        ]

        AnomalyEnhancer.process_batch_vlm(stub, worker_id=0, messages=raw, message_type='Behavior')

        dedup_calls = [c for c in inc_dropped.call_args_list if c.args[0] == "dedup"]
        assert len(dedup_calls) == 1
        assert dedup_calls[0].args[1] == 2, (
            "dedup drop count must be scoped to the dedup filter only, "
            "not the raw pre-filter count"
        )


class TestRateLimitDropCount:
    def test_apply_vlm_rate_limit_records_drops(self, spy_counters):
        """Call ``_apply_vlm_rate_limit`` directly: easier to isolate
        than driving it through ``process_batch_vlm``."""
        inc_dropped, _ = spy_counters

        stub = Mock(spec=AnomalyEnhancer)
        stub.config = {'alert_agent': {'verify_only_finished_events': False}}
        stub._vlm_rate_limit_enabled = True
        stub.redis_handler = Mock()
        # Rate limiter drops 3 of 10 messages.
        stub._run_redis_operation_with_mode = Mock(return_value=[_msg(i) for i in range(7)])

        result = AnomalyEnhancer._apply_vlm_rate_limit(stub, [_msg(i) for i in range(10)])
        assert len(result) == 7

        rate_calls = [c for c in inc_dropped.call_args_list if c.args[0] == "rate_limit"]
        assert len(rate_calls) == 1
        assert rate_calls[0].args[1] == 3

    def test_rate_limit_disabled_no_call(self, spy_counters):
        """When the feature flag is off, the helper short-circuits
        before doing any work. No ``rate_limit`` drop call should fire."""
        inc_dropped, _ = spy_counters

        stub = Mock(spec=AnomalyEnhancer)
        stub.config = {'alert_agent': {'verify_only_finished_events': False}}
        stub._vlm_rate_limit_enabled = False
        stub.redis_handler = Mock()
        stub._run_redis_operation_with_mode = Mock()  # should not be called

        input_msgs = [_msg(i) for i in range(3)]
        result = AnomalyEnhancer._apply_vlm_rate_limit(stub, input_msgs)
        assert result is input_msgs  # pass-through unchanged

        rate_calls = [c for c in inc_dropped.call_args_list if c.args[0] == "rate_limit"]
        assert len(rate_calls) == 0

    def test_rate_limit_error_fallback_no_drop_counted(self, spy_counters):
        """If the Redis call raises, ``_apply_vlm_rate_limit`` returns
        the input unchanged. No drops happened, so no counter should
        move — otherwise transient Redis errors would be attributed to
        rate-limit policy in the dashboards."""
        inc_dropped, _ = spy_counters

        stub = Mock(spec=AnomalyEnhancer)
        stub.config = {'alert_agent': {'verify_only_finished_events': False}}
        stub._vlm_rate_limit_enabled = True
        stub.redis_handler = Mock()
        stub._run_redis_operation_with_mode = Mock(side_effect=RuntimeError("redis down"))

        input_msgs = [_msg(i) for i in range(3)]
        result = AnomalyEnhancer._apply_vlm_rate_limit(stub, input_msgs)
        assert result == input_msgs

        rate_calls = [c for c in inc_dropped.call_args_list if c.args[0] == "rate_limit"]
        assert len(rate_calls) == 0


class TestEventsAfterDedupCount:
    def test_counts_events_that_survive_all_filters(self, spy_counters, monkeypatch):
        _, inc_after_dedup = spy_counters
        stub = _make_enhancer()
        raw = [_msg(i) for i in range(8)]
        _patch_parse(monkeypatch, raw)

        # end_time_delta keeps 6; dedup keeps 4; rate-limit disabled.
        stub._run_redis_operation_with_mode.side_effect = [
            raw[:6],   # after end_time_delta
            raw[:4],   # after dedup
        ]

        AnomalyEnhancer.process_batch_vlm(stub, worker_id=0, messages=raw, message_type='Behavior')

        # inc_events_after_dedup is now called with the surviving message
        # list too (C21 per-sensor support). We assert the count and
        # tolerate any additional kwargs rather than over-specifying.
        assert inc_after_dedup.call_count == 1
        call = inc_after_dedup.call_args
        assert call.args[0] == 4
        assert "messages" in call.kwargs

    def test_reconciliation_identity_holds(self, spy_counters, monkeypatch):
        """The C2 invariant: ``raw = dropped + after_dedup``.

        Walking the filter chain: 10 raw → end_time_delta drops 2 → 8
        → dedup drops 3 → 5 → rate-limit disabled → 5.

        Expected counters: EVENTS_DROPPED{end_time_delta}=2,
        EVENTS_DROPPED{dedup}=3, EVENTS_DROPPED{rate_limit}=0,
        EVENTS_AFTER_DEDUP=5. Sum of all drops + after_dedup = 10 = raw.
        """
        inc_dropped, inc_after_dedup = spy_counters
        stub = _make_enhancer()
        raw = [_msg(i) for i in range(10)]
        _patch_parse(monkeypatch, raw)

        stub._run_redis_operation_with_mode.side_effect = [
            raw[:8],   # after end_time_delta
            raw[:5],   # after dedup
        ]

        AnomalyEnhancer.process_batch_vlm(stub, worker_id=0, messages=raw, message_type='Behavior')

        totals = {"end_time_delta": 0, "dedup": 0, "rate_limit": 0}
        for call in inc_dropped.call_args_list:
            totals[call.args[0]] += call.args[1]
        # rate_limit never fires because _apply_vlm_rate_limit is stubbed
        # as pass-through. That's fine — in production it fires from
        # inside the helper (covered by TestRateLimitDropCount).
        assert totals == {"end_time_delta": 2, "dedup": 3, "rate_limit": 0}

        assert inc_after_dedup.call_count == 1
        assert inc_after_dedup.call_args.args[0] == 5
        assert totals["end_time_delta"] + totals["dedup"] + inc_after_dedup.call_args.args[0] == len(raw)


class TestJsonBatchInput:
    def test_dict_batches_bypass_protobuf_decode(self, spy_counters, monkeypatch):
        """Direct JSON/dict callers should keep working after adding Redis
        Stream JSON-string support. Dict lists are already decoded and must
        not be routed through the protobuf decoder."""
        _, inc_after_dedup = spy_counters
        stub = _make_enhancer()
        parsed = [_msg(i) for i in range(2)]

        monkeypatch.setattr(
            eavw,
            "protobuf_anomalies_to_json_string_list",
            Mock(side_effect=AssertionError("protobuf decoder should not run")),
        )
        monkeypatch.setattr(eavw, "normalize_alert_message", lambda m: m)
        stub._run_redis_operation_with_mode.side_effect = [
            parsed,
            parsed,
        ]

        AnomalyEnhancer.process_batch_vlm(
            stub,
            worker_id=0,
            messages=parsed,
            message_type='Behavior',
        )

        assert inc_after_dedup.call_args.args[0] == 2
        assert stub._process_single_message_with_mode.call_count == 2

    def test_json_string_batches_bypass_protobuf_decode(self, spy_counters, monkeypatch):
        """Redis Stream read_data returns JSON strings inside normalized
        batch dictionaries. process_batch_vlm must parse those directly,
        not hand them to the protobuf decoder that Kafka tuple batches use.
        """
        _, inc_after_dedup = spy_counters
        stub = _make_enhancer()
        parsed = [_msg(i) for i in range(2)]
        json_messages = [json.dumps(m) for m in parsed]

        monkeypatch.setattr(
            eavw,
            "protobuf_anomalies_to_json_string_list",
            Mock(side_effect=AssertionError("protobuf decoder should not run")),
        )
        monkeypatch.setattr(eavw, "normalize_alert_message", lambda m: m)
        stub._run_redis_operation_with_mode.side_effect = [
            parsed,
            parsed,
        ]

        AnomalyEnhancer.process_batch_vlm(
            stub,
            worker_id=0,
            messages=json_messages,
            message_type='Behavior',
        )

        assert inc_after_dedup.call_args.args[0] == 2
        assert stub._process_single_message_with_mode.call_count == 2


class TestPrometheusServerStartup:
    def test_start_prometheus_uses_multiprocess_collector_when_dir_is_set(self, monkeypatch):
        registry = object()
        collector_registry = Mock(return_value=registry)
        multiprocess_mod = Mock()
        start_server = Mock()

        monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus-shards")
        monkeypatch.setattr(eavw, "CollectorRegistry", collector_registry, raising=False)
        monkeypatch.setattr(eavw, "prometheus_multiprocess", multiprocess_mod, raising=False)
        monkeypatch.setattr(eavw, "start_prometheus_server", start_server, raising=False)

        eavw._start_prometheus_metrics_server(9081)

        collector_registry.assert_called_once_with()
        multiprocess_mod.MultiProcessCollector.assert_called_once_with(registry)
        start_server.assert_called_once_with(9081, registry=registry)

    def test_start_prometheus_uses_default_registry_when_no_multiprocess_dir(self, monkeypatch):
        start_server = Mock()

        monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
        monkeypatch.setattr(eavw, "start_prometheus_server", start_server, raising=False)

        eavw._start_prometheus_metrics_server(9081)

        start_server.assert_called_once_with(9081)

    def test_mark_process_dead_notifies_prometheus_client(self, monkeypatch):
        process = Mock(pid=12345)
        multiprocess_mod = Mock()

        monkeypatch.setattr(eavw, "PROMETHEUS_ENABLED", True)
        monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus-shards")
        monkeypatch.setattr(eavw, "prometheus_multiprocess", multiprocess_mod, raising=False)

        eavw._mark_prometheus_process_dead(process)

        multiprocess_mod.mark_process_dead.assert_called_once_with(12345)

    def test_mark_process_dead_skips_when_multiprocess_dir_is_unset(self, monkeypatch):
        process = Mock(pid=12345)
        multiprocess_mod = Mock()

        monkeypatch.setattr(eavw, "PROMETHEUS_ENABLED", True)
        monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
        monkeypatch.setattr(eavw, "prometheus_multiprocess", multiprocess_mod, raising=False)

        eavw._mark_prometheus_process_dead(process)

        multiprocess_mod.mark_process_dead.assert_not_called()
