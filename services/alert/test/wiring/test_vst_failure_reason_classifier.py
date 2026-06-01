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

"""Unit tests for the C14 VST failure-reason classifier.

``AnomalyEnhancer._classify_vst_failure_reason`` replaces the old blanket
``failure_reason="vst_failure"`` label on ``VERIFICATION_FAILURES`` with
a structured set that mirrors the VLM-side taxonomy
(``vlm_timeout`` / ``vlm_connection_error`` / ``vlm_server_error`` /
``vlm_invalid_payload``). Without this mapping, operators triaging an
alert on ``reason="vst_failure"`` would have to read logs just to find
out whether to page the VST team or Alert-Agent itself.

These tests pin each exception type's mapping so a future refactor can't
silently merge two buckets or reintroduce the old blanket value.

Run with: pytest test/test_vst_failure_reason_classifier.py -v
"""

import logging
import sys
import types
from unittest.mock import Mock

import pytest


# ── Module-level setup ───────────────────────────────────────────────────
# Same stub pattern as ``test_vst_error_reporting.py``. We only need to
# import ``AnomalyEnhancer`` far enough to call the static classifier;
# nothing else runs.

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
# Provide no-op spies so enhance_alert_with_vlm imports cleanly.
for name in (
    "inc_events_after_dedup", "inc_events_dropped", "inc_events_skipped_confirmed",
    "observe_pipeline_latency", "observe_video_length", "observe_vlm_duration",
    "observe_vst_duration", "record_event_complete",
):
    setattr(sys.modules['metrics.recorder'], name, Mock())

from enhance_alert_with_vlm import AnomalyEnhancer  # noqa: E402
from vst.exceptions import (  # noqa: E402
    VSTClientError,
    VSTError,
    VSTOverloadedError,
    VSTRecordingNotFoundError,
    VSTTimeoutError,
    VSTUnavailableError,
)


classify = AnomalyEnhancer._classify_vst_failure_reason


# ── Per-exception-type tests ─────────────────────────────────────────────


class TestKnownExceptionMappings:
    """Each VST exception type must pin to exactly one reason label.
    These mappings are what the operator's dashboard rows depend on."""

    def test_vst_timeout_error(self):
        assert classify(VSTTimeoutError("timed out", category="timeout")) == "vst_timeout"

    def test_vst_overloaded_error(self):
        assert classify(VSTOverloadedError("overloaded", category="overloaded")) == "vst_overloaded"

    def test_vst_recording_not_found_error(self):
        assert classify(VSTRecordingNotFoundError("not found", category="not_found")) == "vst_not_found"

    def test_vst_unavailable_error(self):
        assert classify(VSTUnavailableError("unreachable", category="connection_failed")) == "vst_unavailable"

    def test_vst_client_error(self):
        assert classify(VSTClientError("bad request", category="client_error")) == "vst_client_error"

    def test_bare_vst_error(self):
        """The catch-all parent type maps to the generic server bucket.
        Covers any future VSTError subclass we haven't given its own
        branch yet — ``isinstance`` check order ensures specific
        subclasses still win their own label."""
        assert classify(VSTError("generic server failure", category="server_error")) == "vst_server_error"

    def test_missing_video_url_category(self):
        """VST returned 200 OK but without a playable URL — distinct
        enough from a transport failure to deserve its own diagnostic
        trail. Still maps to ``vst_server_error`` because the wire-level
        response was valid from VST's perspective."""
        err = VSTError("missing url", category="missing_video_url")
        assert classify(err) == "vst_server_error"


class TestNoneAndFallback:
    """Edge cases where the captured exception does not describe what
    actually happened (None after a URL-less success, or a non-VSTError
    that slipped through the broader ``except Exception`` in the retry
    branch)."""

    def test_none_maps_to_not_found(self):
        """None means the VST call succeeded HTTP-wise but returned no
        usable URL for the requested time range. Treating this as
        ``vst_not_found`` keeps it in the same dashboard row as
        VSTRecordingNotFoundError so operators don't have to know the
        internal distinction."""
        assert classify(None) == "vst_not_found"

    def test_non_vst_exception_maps_to_unknown(self):
        """A stray ``RuntimeError`` from the retry-without-overlay
        path's broad ``except Exception:`` must not crash triage —
        folds into the ``vst_unknown`` catch-all so the event still
        contributes to the total failure count."""
        assert classify(RuntimeError("something weird happened")) == "vst_unknown"

    def test_keyboard_interrupt_does_not_get_swallowed_at_classify_time(self):
        """The classifier is pure and side-effect-free: passing a
        KeyboardInterrupt should just classify (it IS a BaseException
        subclass but not a VSTError / Exception path). Locking this
        down protects against a future contributor adding side effects
        that might propagate signal-handler exceptions."""
        # KeyboardInterrupt is a BaseException subclass, not Exception.
        # Our classifier only special-cases VSTError subclasses, so
        # it should fall through to the final ``vst_unknown`` return.
        assert classify(KeyboardInterrupt()) == "vst_unknown"


class TestTaxonomySymmetryWithVlm:
    """Meta-tests: every returned label follows the ``vst_*`` naming
    convention used by the dashboard row layout, matching the ``vlm_*``
    prefix the VLM side already uses."""

    KNOWN_REASONS = {
        "vst_timeout", "vst_overloaded", "vst_not_found",
        "vst_unavailable", "vst_client_error", "vst_server_error",
        "vst_unknown",
    }

    def test_every_reason_starts_with_vst_prefix(self):
        for err in [
            None,
            VSTTimeoutError("t"),
            VSTOverloadedError("o"),
            VSTRecordingNotFoundError("n"),
            VSTUnavailableError("u"),
            VSTClientError("c"),
            VSTError("g"),
            RuntimeError("boom"),
        ]:
            reason = classify(err)
            assert reason.startswith("vst_"), reason
            assert reason in self.KNOWN_REASONS, (
                f"{reason!r} is not in the documented allowlist; "
                f"update metrics/prometheus_metrics.py and the "
                f"latency script's row list if this is intentional."
            )

    def test_old_vst_failure_label_is_never_emitted(self):
        """Regression guard for the C14 migration: the pre-C14
        ``vst_failure`` label must not be returned by the classifier.
        Dashboards relying on it have already been notified to switch
        to ``reason=~"vst_.*"``."""
        for err in [
            None,
            VSTTimeoutError("t"),
            VSTError("g"),
            RuntimeError("boom"),
        ]:
            assert classify(err) != "vst_failure"
