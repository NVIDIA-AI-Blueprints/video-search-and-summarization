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

"""Integration tests for the C24 ``worker_assigned_at`` threading.

Before C24, ``_process_single_message`` stamped ``worker_assigned_at =
datetime.now(...)`` at its own entry point. In async-dispatch mode that
entry runs on a dispatch executor thread, so the stamp included
*dispatch queue wait* in addition to batch queue wait — silently
changing what ``WORKER_QUEUE_WAIT_DURATION`` means when the
``async_io_enabled`` flag flips.

After C24, the stamp happens once at the batch scheduler's
``worker_queue.get()`` dequeue and is threaded down as a keyword arg
through ``process_batch_vlm`` → ``_process_single_message_with_mode`` →
``_process_single_message``. ``WORKER_QUEUE_WAIT_DURATION`` now means
the same thing in both modes: "time from Kafka consume to batch worker
dequeue".

These tests lock the threading down at each hop.

Run with: pytest test/test_worker_assigned_at_threading.py -v
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

# Purge any prior stubs for the dispatch mixin. Sibling test files
# (``test_events_dropped_wiring.py``, ``test_no_prompt_wiring.py``, etc.)
# replace ``handlers.async_dispatch_mixin`` with a stub class so they can
# MRO-compose a fake ``AnomalyEnhancer``. This test file needs the real
# module, so unless we pop the stub first the real submodule will never
# be imported.
for _mod in (
    "handlers.async_dispatch_mixin",
    "handlers",
    "enhance_alert_with_vlm",
):
    sys.modules.pop(_mod, None)

_stub_modules = [
    'its_redis', 'its_redis.redis_handler',
    'mdx', 'mdx.anomaly', 'mdx.anomaly.event_bridge_factory',
    'mdx.anomaly.sink', 'mdx.anomaly.sink.vlm_enhanced_sink',
    'mdx.anomaly.utils', 'mdx.anomaly.utils.elastic_ready',
    'handlers', 'handlers.enrichment', 'handlers.direct_media',
    'handlers.prompt_handler', 'handlers.prompt_handler.alert_type_config_loader',
    'handlers.async_external_io_mixin',
    'handlers.async_vlm_mode_mixin',
    'utils.schema_util',
    'vlm.warmup',
    'vss',
    'metrics', 'metrics.prometheus_metrics', 'metrics.recorder',
]
# IMPORTANT: this test exercises the real ``AsyncDispatchMixin`` — do NOT
# stub ``handlers.async_dispatch_mixin``. We still stub the other two
# mixins and ``utils.logging_config`` because the dispatch mixin only
# imports lightweight bits from the handlers package.
for mod_name in _stub_modules:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# ``handlers`` must let real submodule imports (specifically
# ``handlers.async_dispatch_mixin``) resolve — point ``__path__`` at the
# real package directory so Python's finders find the real file on disk
# instead of treating ``handlers`` as an empty package.
import os as _os
_HANDLERS_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "handlers")
sys.modules['handlers'].__path__ = [_HANDLERS_DIR]
sys.modules['handlers.prompt_handler'].__path__ = []
sys.modules['metrics'].__path__ = []

# Stub utils.logging_config ONLY if it isn't already loaded. The dispatch
# mixin imports ``get_logger`` from it at module top, so an unrelated
# prior test that replaced this module with a bare ModuleType would make
# importing the dispatch mixin fail.
if 'utils.logging_config' in sys.modules and not hasattr(sys.modules['utils.logging_config'], 'get_logger'):
    sys.modules['utils.logging_config'].get_logger = lambda name: logging.getLogger(name)
    sys.modules['utils.logging_config'].setup_logging = Mock()
    sys.modules['utils.logging_config'].enforce_log_level = Mock()
elif 'utils.logging_config' not in sys.modules:
    stub = types.ModuleType('utils.logging_config')
    stub.get_logger = lambda name: logging.getLogger(name)
    stub.setup_logging = Mock()
    stub.enforce_log_level = Mock()
    sys.modules['utils.logging_config'] = stub

sys.modules['its_redis.redis_handler'].RedisHandler = Mock
sys.modules['mdx.anomaly.event_bridge_factory'].EventBridgeFactory = Mock()
sys.modules['mdx.anomaly.sink.vlm_enhanced_sink'].build_vlm_enhanced_sink = Mock()
sys.modules['mdx.anomaly.utils.elastic_ready'].generate_alert_fingerprint = Mock(return_value='fp')
sys.modules['mdx.anomaly.utils.elastic_ready'].generate_incident_fingerprint = Mock(return_value='fp')
sys.modules['handlers.enrichment'].EnrichmentProcessor = Mock
sys.modules['handlers.direct_media'].DirectMediaHandler = Mock
sys.modules['handlers.prompt_handler.alert_type_config_loader'].AlertTypeConfig = Mock
sys.modules['handlers.prompt_handler.alert_type_config_loader'].AlertTypeConfigLoader = Mock

# Only the other two mixins get stubbed. The dispatch mixin is the
# subject under test.
class _AsyncExternalIOMixinStub: pass
class _AsyncVLMModeMixinStub: pass
sys.modules['handlers.async_external_io_mixin'].AsyncExternalIOMixin = _AsyncExternalIOMixinStub
sys.modules['handlers.async_vlm_mode_mixin'].AsyncVLMModeMixin = _AsyncVLMModeMixinStub

sys.modules['utils.schema_util'].protobuf_anomalies_to_json_string_list = Mock()
sys.modules['vlm.warmup'].warmup_vlm = Mock()
sys.modules['vlm.warmup'].WARMUP_VIDEO = '/tmp/fake.mp4'
sys.modules['vss'].VSSHandler = Mock

sys.modules['metrics'].PROMETHEUS_ENABLED = False
for name in (
    "inc_events_after_dedup", "inc_events_dropped", "inc_events_skipped_confirmed",
    "observe_pipeline_latency", "observe_video_length", "observe_vlm_duration",
    "observe_vst_duration", "record_event_complete", "set_per_sensor_labels",
    "warm_startup_labels",
):
    setattr(sys.modules['metrics.recorder'], name, Mock())

import enhance_alert_with_vlm as eavw  # noqa: E402
from enhance_alert_with_vlm import AnomalyEnhancer  # noqa: E402

# Import the REAL dispatch mixin once at module top — after the
# ``sys.modules.pop`` above — so later tests inside this file don't
# re-trigger an import that might hit a stub left by a sibling test
# file. All dispatch-mixin tests below must use ``_AsyncDispatchMixin``
# from this module-level import, not a fresh ``from handlers...``
# statement inside the test body.
from handlers.async_dispatch_mixin import AsyncDispatchMixin as _AsyncDispatchMixin  # noqa: E402


# ── Tests ────────────────────────────────────────────────────────────────


class TestProcessSingleMessageUsesSuppliedStamp:
    """The leaf function honors the threaded-down timestamp when provided
    and falls back to ``datetime.now(...)`` when it isn't. Both branches
    must populate ``latency['timestamps']['workerAssignedAt']``.
    """

    def _make_stub(self):
        stub = Mock(spec=AnomalyEnhancer)
        # Skip every downstream side-effect: early-skip path handles
        # C9 counter and then returns. We reach into the no-prompt
        # branch by flipping skip=False — simplest setup.
        stub._set_message_id_and_should_skip = Mock(return_value=True)
        stub.redis_handler = None
        return stub

    def test_supplied_stamp_reaches_latency_dict(self, monkeypatch):
        """The pre-VLM timestamp block writes ``worker_assigned_at`` into
        the shared latency dict. We spy on ``record_event_complete`` to
        capture the ``latency`` argument and verify the value."""
        supplied = "2025-01-01T00:00:05.123456+00:00"

        stub = Mock(spec=AnomalyEnhancer)
        stub._set_message_id_and_should_skip = Mock(return_value=False)
        stub.prompt_manager = Mock()
        stub.prompt_manager.get_prompts_for_message.return_value = (None, None)
        stub.redis_handler = None

        captured = {}

        def _capture(worker_start_time, message, latency, failure_reason=None):
            captured["latency"] = latency

        monkeypatch.setattr(eavw, "record_event_complete", _capture)

        AnomalyEnhancer._process_single_message(
            stub,
            worker_id=0,
            message={"sensorId": "cam-1", "end": "2025-01-01T00:00:02Z"},
            kafka_consumed_at="2025-01-01T00:00:04Z",
            kafka_published_at="2025-01-01T00:00:03Z",
            worker_assigned_at=supplied,
        )

        ts = captured["latency"]["timestamps"]
        assert ts["workerAssignedAt"] == supplied, (
            f"C24: the supplied batch-scheduler timestamp must survive "
            f"into ``latency['timestamps']['workerAssignedAt']`` instead "
            f"of being overwritten by a fresh ``datetime.now(...)``."
        )

    def test_fallback_stamps_inline_when_none_supplied(self, monkeypatch):
        """Backward-compat: if a caller does not thread the stamp (VSS
        path, test harnesses) the function stamps its own timestamp so
        the latency dict always has a value."""
        stub = Mock(spec=AnomalyEnhancer)
        stub._set_message_id_and_should_skip = Mock(return_value=False)
        stub.prompt_manager = Mock()
        stub.prompt_manager.get_prompts_for_message.return_value = (None, None)
        stub.redis_handler = None

        captured = {}

        def _capture(worker_start_time, message, latency, failure_reason=None):
            captured["latency"] = latency

        monkeypatch.setattr(eavw, "record_event_complete", _capture)

        AnomalyEnhancer._process_single_message(
            stub,
            worker_id=0,
            message={"sensorId": "cam-1", "end": "2025-01-01T00:00:02Z"},
            kafka_consumed_at="2025-01-01T00:00:01Z",
            kafka_published_at="2025-01-01T00:00:00Z",
        )

        ts = captured["latency"]["timestamps"]
        # The fallback path must still populate the key with an
        # ISO-shaped string (we can't compare to an exact value, but we
        # can assert the shape).
        assert ts["workerAssignedAt"], "fallback stamp was not written"
        assert ts["workerAssignedAt"].startswith("20"), (
            f"expected ISO-8601 stamp, got {ts['workerAssignedAt']!r}"
        )


class TestProcessBatchVlmThreadsStampDown:
    """``process_batch_vlm`` must forward ``worker_assigned_at`` to the
    per-message dispatcher. The stamp is taken at the batch scheduler
    and must NOT be overwritten inside the batch."""

    def test_stamp_forwarded_to_process_single_message_with_mode(self, monkeypatch):
        stub = Mock(spec=AnomalyEnhancer)
        stub.config = {
            'alert_agent': {'verify_only_finished_events': False},
        }
        stub.async_io_enabled = False
        stub.vst_pass_through_mode = False
        stub.redis_handler = None
        stub._vst_handler = Mock()
        stub._apply_vlm_rate_limit = lambda msgs: msgs
        stub._process_single_message_with_mode = Mock()

        # Short-circuit the parse step.
        raw_msg = {
            "sensorId": "cam-1",
            "category": "loitering",
            "timestamp": "2025-01-01T00:00:00Z",
            "end": "2025-01-01T00:00:02Z",
            "objectIds": [],
        }
        import json as _json
        monkeypatch.setattr(
            eavw,
            "protobuf_anomalies_to_json_string_list",
            lambda *a, **k: [_json.dumps(raw_msg)],
        )
        monkeypatch.setattr(eavw, "normalize_alert_message", lambda m: m)

        supplied_stamp = "2025-01-01T00:00:00.999999+00:00"
        AnomalyEnhancer.process_batch_vlm(
            stub,
            worker_id=0,
            messages=[raw_msg],
            message_type="Behavior",
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at=supplied_stamp,
        )

        # The mock should have received exactly one call, with the
        # supplied stamp threaded through as a keyword argument.
        stub._process_single_message_with_mode.assert_called_once()
        kwargs = stub._process_single_message_with_mode.call_args.kwargs
        assert kwargs.get("worker_assigned_at") == supplied_stamp


class TestDispatchMixinPreservesStamp:
    """The dispatch mixin threads the stamp into every call path
    (sync fallback, async submit, executor-unavailable fallback,
    submit-failed fallback)."""

    def _make_dispatch_stub(self, async_io_enabled=False, executor=None, submit_raises=None):
        """Return a bare ``Mock`` (no ``spec``) with the attributes the
        dispatch mixin touches. Using ``spec=AsyncDispatchMixin`` would
        reject ``async_dispatch_max_in_flight`` etc. because those are
        instance attributes set by ``AnomalyEnhancer.__init__``, not
        class-level members of the mixin."""
        stub = Mock()
        stub.async_io_enabled = async_io_enabled
        stub._message_dispatch_lock = threading.Lock()
        stub._message_dispatch_futures = set()
        stub._dispatch_backpressure_semaphore = None
        stub._message_dispatch_executor = executor
        stub.async_dispatch_max_in_flight = 16
        stub._process_single_message = Mock()
        if submit_raises is not None and executor is not None:
            executor.submit = Mock(side_effect=submit_raises)
        return stub

    def test_sync_mode_forwards_stamp(self):
        stub = self._make_dispatch_stub(async_io_enabled=False)
        _AsyncDispatchMixin._process_single_message_with_mode(
            stub,
            worker_id=0,
            message={"sensorId": "cam-1"},
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:01Z",
        )
        stub._process_single_message.assert_called_once()
        kwargs = stub._process_single_message.call_args.kwargs
        assert kwargs.get("worker_assigned_at") == "2025-01-01T00:00:01Z"

    def test_executor_unavailable_fallback_forwards_stamp(self):
        # async_io on but no executor → fallback to inline.
        stub = self._make_dispatch_stub(async_io_enabled=True, executor=None)
        _AsyncDispatchMixin._process_single_message_with_mode(
            stub,
            worker_id=0,
            message={"sensorId": "cam-1"},
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:02Z",
        )
        stub._process_single_message.assert_called_once()
        kwargs = stub._process_single_message.call_args.kwargs
        assert kwargs.get("worker_assigned_at") == "2025-01-01T00:00:02Z"

    def test_async_submit_forwards_stamp_as_positional(self):
        """Dispatch executor receives the stamp as part of the submit
        args, so when the dispatched call finally runs on a pool thread
        it sees the batch-scheduler stamp — not the later dispatch-pickup
        time."""
        executor = Mock()
        captured_args = {}

        def _fake_submit(fn, *args, **kwargs):
            captured_args["args"] = args
            captured_args["kwargs"] = kwargs
            fut = Mock()
            fut.cancelled = Mock(return_value=False)
            fut.exception = Mock(return_value=None)
            fut.add_done_callback = Mock()
            return fut

        executor.submit = _fake_submit
        stub = self._make_dispatch_stub(async_io_enabled=True, executor=executor)
        _AsyncDispatchMixin._process_single_message_with_mode(
            stub,
            worker_id=0,
            message={"sensorId": "cam-1"},
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:03Z",
        )
        # The dispatcher submits as positional args: (worker_id, message,
        # kafka_consumed_at, kafka_published_at, worker_assigned_at). Lock
        # that position down so the receiving _process_single_message sees
        # the stamp as the 5th positional argument (after self in the
        # instance call, which is shifted out here because submit gets
        # the bound-method reference).
        assert captured_args["args"][-1] == "2025-01-01T00:00:03Z", (
            f"worker_assigned_at was not the last positional submit arg; "
            f"got args={captured_args['args']!r}"
        )


# ── C26: async dispatch fallback counter wiring ──────────────────────────


class TestDispatchFallbackCounter:
    """Both of the dispatch mixin's silent-fallback-to-sync paths must
    now emit a counter increment so operators can see when the async
    pipeline can't take work (startup/shutdown race, executor shutdown,
    thread-pool overflow). Before C26 these paths only logged."""

    def _make_dispatch_stub(self, async_io_enabled=False, executor=None):
        stub = Mock()
        stub.async_io_enabled = async_io_enabled
        stub._message_dispatch_lock = threading.Lock()
        stub._message_dispatch_futures = set()
        stub._dispatch_backpressure_semaphore = None
        stub._message_dispatch_executor = executor
        stub.async_dispatch_max_in_flight = 16
        stub._process_single_message = Mock()
        return stub

    def test_executor_unavailable_fires_counter(self, monkeypatch):
        """Path: ``async_io_enabled=True`` but the executor is None.
        The mixin must call the C26 helper with
        ``reason="executor_unavailable"`` before falling back inline."""
        import handlers.async_dispatch_mixin as dispatch_mod

        spy = Mock()
        monkeypatch.setattr(dispatch_mod, "inc_async_dispatch_fallback", spy)

        stub = self._make_dispatch_stub(async_io_enabled=True, executor=None)
        _AsyncDispatchMixin._process_single_message_with_mode(
            stub,
            worker_id=0,
            message={"sensorId": "cam-1"},
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:02Z",
        )

        spy.assert_called_once_with("executor_unavailable")

    def test_submit_error_fires_counter(self, monkeypatch):
        """Path: ``executor.submit(...)`` raises (thread-pool overflow,
        executor shutdown race). Counter fires with
        ``reason="submit_error"`` before the inline fallback."""
        import handlers.async_dispatch_mixin as dispatch_mod

        spy = Mock()
        monkeypatch.setattr(dispatch_mod, "inc_async_dispatch_fallback", spy)

        executor = Mock()
        executor.submit = Mock(side_effect=RuntimeError("pool shutdown"))
        stub = self._make_dispatch_stub(async_io_enabled=True, executor=executor)

        _AsyncDispatchMixin._process_single_message_with_mode(
            stub,
            worker_id=0,
            message={"sensorId": "cam-1"},
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:03Z",
        )

        spy.assert_called_once_with("submit_error")

    def test_sync_mode_does_not_fire_counter(self, monkeypatch):
        """If async mode is off, there's no fallback — the counter
        must not move. Sanity check that we're not accidentally
        incrementing it on every call."""
        import handlers.async_dispatch_mixin as dispatch_mod

        spy = Mock()
        monkeypatch.setattr(dispatch_mod, "inc_async_dispatch_fallback", spy)

        stub = self._make_dispatch_stub(async_io_enabled=False)
        _AsyncDispatchMixin._process_single_message_with_mode(
            stub,
            worker_id=0,
            message={"sensorId": "cam-1"},
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:01Z",
        )

        spy.assert_not_called()

    def test_successful_async_submit_does_not_fire_counter(self, monkeypatch):
        """Happy path: submit succeeds → no fallback → no counter
        increment. Critical to assert because spuriously incrementing
        the counter on every async submission would flood the
        dashboard with false-positive ``submit_error`` data."""
        import handlers.async_dispatch_mixin as dispatch_mod

        spy = Mock()
        monkeypatch.setattr(dispatch_mod, "inc_async_dispatch_fallback", spy)

        executor = Mock()

        def _fake_submit(fn, *args, **kwargs):
            fut = Mock()
            fut.cancelled = Mock(return_value=False)
            fut.exception = Mock(return_value=None)
            fut.add_done_callback = Mock()
            return fut

        executor.submit = _fake_submit
        stub = self._make_dispatch_stub(async_io_enabled=True, executor=executor)

        _AsyncDispatchMixin._process_single_message_with_mode(
            stub,
            worker_id=0,
            message={"sensorId": "cam-1"},
            kafka_consumed_at="2025-01-01T00:00:00Z",
            kafka_published_at="2024-12-31T23:59:59Z",
            worker_assigned_at="2025-01-01T00:00:03Z",
        )

        spy.assert_not_called()
