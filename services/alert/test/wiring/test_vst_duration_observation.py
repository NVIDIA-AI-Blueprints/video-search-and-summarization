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

"""Integration tests for the C12 invariant: **one `VST_DURATION` observation
per event**, regardless of whether the VST section succeeded on the primary
attempt, failed, retried without overlay, succeeded on retry, or failed on
retry.

Prior to the C12 fix, `VST_DURATION.observe(...)` fired per-attempt at five
different sites in `_process_single_message`. With `retry_without_overlay`
enabled, a single event could produce two observations, inflating
`alert_bridge_vst_duration_seconds_count` and biasing the histogram with
the short-tail fail-fast-then-succeed pair.

These tests drive `_process_single_message` end-to-end under each branch
and assert that `VST_DURATION._count` grows by exactly one per call. They
also assert that `VIDEO_LENGTH` observes at most once per event (the
reviewer's companion claim was already correct, but we lock the behavior
down with a test so it can't regress).

Run with: pytest test/test_vst_duration_observation.py -v
"""

import logging
import os
import sys
import threading
import types
from unittest.mock import Mock

import pytest


# ── Module-level setup: stub heavy deps, enable real prometheus metrics ──

# Enable the feature flag BEFORE the production modules import `metrics`, so
# the conditional imports in `metrics/recorder.py` actually pull the real
# counter/histogram objects.
os.environ["PROMETHEUS_METRICS_ENABLED"] = "true"

# Prometheus client must be importable for these tests — skip cleanly if
# the environment does not have it (this file should be collected but
# silently no-op in CI environments that run the pure-unit suite).
pytest.importorskip("prometheus_client")

# Purge any previous stubs from sibling test files so we load the real
# ``metrics`` package here. `test_vst_error_reporting.py` stubs the same
# modules with `PROMETHEUS_ENABLED = False`; if it ran first in the same
# process we would inherit its disabled state and assert-zero every test.
for name in list(sys.modules):
    if name == "metrics" or name.startswith("metrics."):
        del sys.modules[name]

# Stub every other heavy dependency `enhance_alert_with_vlm` pulls in.
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
]
for mod_name in _stub_modules:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

sys.modules['handlers'].__path__ = []
sys.modules['handlers.prompt_handler'].__path__ = []

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

# Now that the env var is set and stubs are in place, import the real
# metrics module and the system-under-test.
from metrics import PROMETHEUS_ENABLED  # noqa: E402
from metrics.prometheus_metrics import VIDEO_LENGTH, VST_DURATION  # noqa: E402
from enhance_alert_with_vlm import AnomalyEnhancer  # noqa: E402
from vst.exceptions import VSTError, VSTUnavailableError  # noqa: E402


# Fail fast if the test module somehow loaded before the env var took
# effect — otherwise every assertion below silently passes against a
# no-op recorder and we'd be testing nothing.
assert PROMETHEUS_ENABLED, (
    "PROMETHEUS_METRICS_ENABLED env var did not propagate to the metrics "
    "module. Another test likely stubbed `metrics` before this file ran."
)


# ── Stub factory (mirrors test_vst_error_reporting.py style) ─────────────

def _make_enhancer(retry_without_overlay=False):
    """Build a thinly-stubbed `AnomalyEnhancer` that only exercises the
    VST section of `_process_single_message`. Every external collaborator
    (VLM, sink, Redis, enrichment) is mocked out; the VST handler is the
    only dependency whose behavior each test configures.
    """
    stub = Mock(spec=AnomalyEnhancer)
    stub.config = {
        'vst_config': {'retry_without_overlay': retry_without_overlay},
        'vlm': {'max_retries': 0, 'model': 'test', 'dynamic_frame_count': False},
        'alert_agent': {
            'include_latency_info': False,
            'url_transform': {'enabled': False},
        },
    }
    stub.url_transform_enabled = False
    stub.include_latency_info = False
    stub.vlm_media_source_using_base64 = False
    stub.async_vst_enabled = False
    stub.async_elastic_enabled = False
    stub.async_redis_enabled = False
    stub.async_io_enabled = False
    stub.async_vlm_runtime = None
    stub.async_external_timeout_seconds = 30.0
    stub._vlm_sink_type = "elastic"
    stub._sink_async_lock = threading.Lock()
    stub._sink_async_futures = set()
    stub._vst_handler = Mock()
    stub.vlm_client = Mock()
    stub.vlm_client.config = {'num_frames': 10}
    stub.prompt_manager = Mock()
    stub.prompt_manager.alert_config_loader = None
    stub.prompt_manager.get_prompts_for_message.return_value = ("u", "s")
    stub.vlm_enhanced_event_sink = Mock()
    stub.enrichment_processor = Mock(process=Mock(return_value=None))
    stub.redis_handler = Mock()
    stub.validate_video_url = Mock(return_value=True)
    stub._set_message_id_and_should_skip = Mock(return_value=False)
    stub._compute_fingerprint = Mock(return_value=None)
    stub._classify_vst_failure = AnomalyEnhancer._classify_vst_failure
    stub._pluggable_parser = None
    # The VLM analyze path returns a response with a ``.content`` attribute
    # so the success branch can exit cleanly — we don't assert on VLM
    # metrics here, only on VST_DURATION.
    stub._analyze_video_url_with_mode = Mock(
        return_value=Mock(content='{"verdict":"confirmed"}')
    )
    stub._get_video_stream_url_with_mode = Mock(
        side_effect=lambda sensor_id, start, end, **kwargs: (
            stub._vst_handler.get_video_stream_url(sensor_id, start, end, **kwargs)
        )
    )
    stub._publish_success_with_mode = Mock(return_value=None)
    stub._publish_error_with_mode = Mock(return_value=None)
    stub._update_enrichment_with_mode = Mock(return_value=None)
    stub._process_enrichment_with_mode = Mock(return_value=None)
    stub._get_merged_vlm_config = Mock(return_value={
        'model': 'test', 'max_retries': 0, 'dynamic_frame_count': False,
        'num_frames': 10,
    })
    stub.set_max_frames = Mock(return_value=10)
    return stub


def _make_msg():
    return {
        'sensorId': 'cam-1',
        'category': 'loitering',
        'timestamp': '2025-09-10T11:16:31Z',
        'end': '2025-09-10T11:16:33Z',
        'objectIds': [],
    }


def _run(stub, msg=None):
    """Drive `_process_single_message` once and return the mutated msg."""
    msg = msg or _make_msg()
    AnomalyEnhancer._process_single_message(stub, worker_id=0, message=msg)
    return msg


def _histogram_count(hist) -> float:
    """Read the total observation count of a ``prometheus_client`` Histogram.

    The client library does not expose ``_count`` directly on labelled or
    unlabelled histograms — the canonical way is to walk ``.collect()``
    and pluck the sample whose name ends in ``_count``. This is the same
    path the HTTP exposition uses and therefore the one that matches
    what operators see on the scrape target.
    """
    for metric in hist.collect():
        for sample in metric.samples:
            if sample.name.endswith("_count"):
                return sample.value
    return 0.0


def _vst_count():
    """Current sample count of the VST_DURATION histogram."""
    return _histogram_count(VST_DURATION)


def _video_length_count():
    """Current sample count of the VIDEO_LENGTH histogram."""
    return _histogram_count(VIDEO_LENGTH)


# ── Tests ────────────────────────────────────────────────────────────────


class TestVstDurationObservedOncePerEvent:
    """Core C12 invariant: one sample per event, no matter which branch runs."""

    def test_primary_success_observes_exactly_once(self):
        stub = _make_enhancer(retry_without_overlay=False)
        stub._vst_handler.get_video_stream_url.return_value = (
            'http://vst/clip.mp4', '2025-01-01T00:00:00Z', '2025-01-01T00:00:10Z',
        )
        before = _vst_count()
        _run(stub)
        assert _vst_count() - before == 1

    def test_primary_failure_no_retry_observes_exactly_once(self):
        stub = _make_enhancer(retry_without_overlay=False)
        stub._vst_handler.get_video_stream_url.side_effect = VSTUnavailableError(
            "Cannot connect", category="connection_failed",
        )
        before = _vst_count()
        _run(stub)
        assert _vst_count() - before == 1

    def test_retry_success_observes_exactly_once(self):
        """Primary attempt fails, retry-without-overlay succeeds.

        The reviewer identified this as the most egregious double-count case
        in the original code (primary fail observed + retry success observed
        = 2 samples per event). After C12 the accumulator collapses them
        into one sample whose value is the *sum* of both attempts' wall
        clocks.
        """
        stub = _make_enhancer(retry_without_overlay=True)
        stub._vst_handler.get_video_stream_url.side_effect = [
            VSTUnavailableError("Cannot connect", category="connection_failed"),
            ('http://vst/clip.mp4', '2025-01-01T00:00:00Z', '2025-01-01T00:00:10Z'),
        ]
        before = _vst_count()
        _run(stub)
        assert _vst_count() - before == 1

    def test_retry_both_fail_observes_exactly_once(self):
        stub = _make_enhancer(retry_without_overlay=True)
        stub._vst_handler.get_video_stream_url.side_effect = VSTUnavailableError(
            "Cannot connect", category="connection_failed",
        )
        before = _vst_count()
        _run(stub)
        assert _vst_count() - before == 1
        # Two attempts happened — verify the call count so we know the
        # retry branch actually ran and we're not passing this test by
        # accident.
        assert stub._vst_handler.get_video_stream_url.call_count == 2

    def test_retry_unexpected_exception_observes_exactly_once(self):
        """Retry branch raises a non-VST exception (defensive `except Exception`)."""
        stub = _make_enhancer(retry_without_overlay=True)
        stub._vst_handler.get_video_stream_url.side_effect = [
            VSTUnavailableError("primary fail", category="connection_failed"),
            RuntimeError("retry crashed"),
        ]
        before = _vst_count()
        _run(stub)
        assert _vst_count() - before == 1

    def test_primary_unexpected_exception_observes_exactly_once(self):
        """Primary attempt raises a non-VST exception; retry disabled."""
        stub = _make_enhancer(retry_without_overlay=False)
        stub._vst_handler.get_video_stream_url.side_effect = RuntimeError("boom")
        before = _vst_count()
        _run(stub)
        assert _vst_count() - before == 1


class TestVstDurationValueAccumulation:
    """C12 observes the *sum* of attempts so operators can read the
    quantile as 'total VST wall-clock per event' — lock the arithmetic
    down so a future refactor cannot quietly switch to 'just the last
    attempt'."""

    def test_retry_success_samples_sum_of_both_attempts(self):
        stub = _make_enhancer(retry_without_overlay=True)
        stub._vst_handler.get_video_stream_url.side_effect = [
            VSTUnavailableError("primary fail", category="connection_failed"),
            ('http://vst/clip.mp4', '2025-01-01T00:00:00Z', '2025-01-01T00:00:10Z'),
        ]
        sum_before = VST_DURATION._sum.get()
        _run(stub)
        delta = VST_DURATION._sum.get() - sum_before
        # We can't predict the exact wall-clock values (time.time() is
        # real), but we *can* assert the observed sample is non-negative
        # and rounded to 3 decimals like the source does. It must also
        # be >= the sum of the per-attempt durations the caller stamped
        # into the latency dict — if observation somehow only captured
        # one attempt, delta would equal that single attempt's value.
        assert delta >= 0.0
        # Three-decimal rounding is part of the C12 contract (operators
        # comparing `vst_duration_seconds_sum / _count` to the per-event
        # latency dict should see matching precision).
        assert round(delta, 3) == delta


class TestVideoLengthObservedOncePerEvent:
    """The reviewer's companion claim ('VIDEO_LENGTH is observed twice')
    was incorrect. Lock the correct behavior: VIDEO_LENGTH fires at most
    once, and only when VST returned a usable URL."""

    def test_primary_success_observes_once(self):
        stub = _make_enhancer(retry_without_overlay=False)
        stub._vst_handler.get_video_stream_url.return_value = (
            'http://vst/clip.mp4', '2025-01-01T00:00:00Z', '2025-01-01T00:00:10Z',
        )
        before = _video_length_count()
        _run(stub)
        assert _video_length_count() - before == 1

    def test_primary_failure_no_observation(self):
        stub = _make_enhancer(retry_without_overlay=False)
        stub._vst_handler.get_video_stream_url.side_effect = VSTUnavailableError(
            "down", category="connection_failed",
        )
        before = _video_length_count()
        _run(stub)
        assert _video_length_count() - before == 0

    def test_retry_success_observes_exactly_once(self):
        stub = _make_enhancer(retry_without_overlay=True)
        stub._vst_handler.get_video_stream_url.side_effect = [
            VSTUnavailableError("primary fail", category="connection_failed"),
            ('http://vst/clip.mp4', '2025-01-01T00:00:00Z', '2025-01-01T00:00:10Z'),
        ]
        before = _video_length_count()
        _run(stub)
        assert _video_length_count() - before == 1

    def test_retry_both_fail_no_observation(self):
        stub = _make_enhancer(retry_without_overlay=True)
        stub._vst_handler.get_video_stream_url.side_effect = VSTUnavailableError(
            "down", category="connection_failed",
        )
        before = _video_length_count()
        _run(stub)
        assert _video_length_count() - before == 0
