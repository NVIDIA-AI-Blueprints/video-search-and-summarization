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

import io
import json
import logging

import pytest

from lib.logging.wdm_logging import (
    ContextAndTraceFilter,
    JsonFormatter,
    RateLimitedLogger,
    TextFormatter,
    WlObjectNameFilter,
    bind_context,
    clear_context,
    configure_root_logging,
    log_event,
    log_rate_limited,
    parse_log_level,
    reset_context,
)


def test_parse_log_level():
    assert parse_log_level("DEBUG") == logging.DEBUG
    assert parse_log_level("info") == logging.INFO
    assert parse_log_level(None) == logging.INFO
    assert parse_log_level("") == logging.INFO


def test_text_and_json_formatters_include_event_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("WDM_LOG_LEVEL", "INFO")
    monkeypatch.setenv("WDM_LOG_FORMAT", "text")
    monkeypatch.setenv("WDM_LOG_TO_FILE", "0")
    configure_root_logging("wl-test", str(tmp_path), component="workload")

    logger = logging.getLogger("sdrc.test.logging")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(TextFormatter())
    handler.addFilter(WlObjectNameFilter("workload:wl-test"))
    from lib.logging.wdm_logging import ContextAndTraceFilter

    handler.addFilter(ContextAndTraceFilter())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_event(
        logger,
        logging.INFO,
        "stream.provision.finished",
        "Finished provisioning stream",
        stream_id="cam-1",
        outcome="ok",
        elapsed_s=0.042,
    )
    text_line = stream.getvalue().strip()
    assert "stream.provision.finished" in text_line
    assert "stream_id=cam-1" in text_line
    assert "outcome=ok" in text_line
    assert "[workload:wl-test]" in text_line

    stream.truncate(0)
    stream.seek(0)
    handler.setFormatter(JsonFormatter())
    log_event(
        logger,
        logging.INFO,
        "bus.message.committed",
        "Committing message",
        message_id="1-0",
        outcome="ok",
    )
    payload = json.loads(stream.getvalue().strip())
    assert payload["event"] == "bus.message.committed"
    assert payload["message_id"] == "1-0"
    assert payload["severity"] == "INFO"
    assert payload["workload"] == "wl-test"
    assert payload["component"] == "workload"


def test_configure_root_logging_respects_level_and_silences_redis_lock(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WDM_LOG_LEVEL", "INFO")
    monkeypatch.setenv("WDM_LOG_FORMAT", "text")
    monkeypatch.setenv("WDM_LOG_TO_FILE", "0")
    configure_root_logging("wl-test", str(tmp_path))

    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("redis_lock.acquire").level == logging.WARNING

    monkeypatch.setenv("WDM_LOG_LEVEL", "DEBUG")
    configure_root_logging("wl-test", str(tmp_path))
    assert logging.getLogger().level == logging.DEBUG


def test_configure_root_logging_sets_component_display_name(tmp_path, monkeypatch):
    monkeypatch.setenv("WDM_LOG_LEVEL", "INFO")
    monkeypatch.setenv("WDM_LOG_FORMAT", "text")
    monkeypatch.setenv("WDM_LOG_TO_FILE", "0")

    configure_root_logging("vss-rtvi-cv", str(tmp_path), component="workload")
    from lib.logging.wdm_logging import get_context

    assert get_context().get("component") == "workload"
    assert get_context().get("workload") == "vss-rtvi-cv"

    logger = logging.getLogger("sdrc.test.source")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(TextFormatter())
    handler.addFilter(WlObjectNameFilter("workload:vss-rtvi-cv"))
    from lib.logging.wdm_logging import ContextAndTraceFilter

    handler.addFilter(ContextAndTraceFilter())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("hello")
    line = stream.getvalue()
    assert "[workload:vss-rtvi-cv]" in line
    assert "component=workload" in line

    configure_root_logging("run-workloads", str(tmp_path), component="router")
    assert get_context().get("component") == "router"

    clear_context()
    token = bind_context(stream_id="s1", bus="redis")
    try:
        from lib.logging.wdm_logging import get_context

        assert get_context()["stream_id"] == "s1"
    finally:
        reset_context(token)
        clear_context()


def test_rate_limited_logger_suppresses_bursts():
    limiter = RateLimitedLogger(interval_s=60.0)
    ok1, suppressed1 = limiter.should_log("k")
    ok2, suppressed2 = limiter.should_log("k")
    assert ok1 is True and suppressed1 == 0
    assert ok2 is False and suppressed2 == 0

    logger = logging.getLogger("sdrc.test.rate")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.ERROR)
    # Should not raise
    log_rate_limited(logger, logging.ERROR, "burst-key", "boom %s", "x", interval_s=60.0)
    log_rate_limited(logger, logging.ERROR, "burst-key", "boom %s", "x", interval_s=60.0)


def test_rate_limited_logger_logs_first_event_when_uptime_below_interval(monkeypatch):
    """time.monotonic() is boot-relative, so a fresh host must not suppress the first line."""
    from lib.logging import wdm_logging

    class _FakeClock:
        def __init__(self, value: float):
            self.value = value

        def monotonic(self) -> float:
            return self.value

    clock = _FakeClock(3.0)
    monkeypatch.setattr(wdm_logging, "time", clock)

    limiter = RateLimitedLogger(interval_s=60.0)
    assert limiter.should_log("boot-key") == (True, 0)
    assert limiter.should_log("boot-key") == (False, 0)

    clock.value += 61.0
    assert limiter.should_log("boot-key") == (True, 1)


def test_log_rate_limited_custom_interval_retains_state():
    """Custom interval_s must reuse the same limiter (Greptile P2)."""
    logger = logging.getLogger("sdrc.test.rate.custom")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    logger.propagate = False

    key = "custom-interval-unique-key"
    log_rate_limited(logger, logging.ERROR, key, "first", interval_s=5.0)
    log_rate_limited(logger, logging.ERROR, key, "second", interval_s=5.0)
    log_rate_limited(logger, logging.ERROR, key, "third", interval_s=5.0)
    lines = [ln for ln in stream.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0] == "first"


def test_component_context_overrides_handler_source_bracket():
    """Router-configured handlers must still show [controller] when context is bound."""
    clear_context()
    logger = logging.getLogger("sdrc.test.component.override")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(TextFormatter())
    # Simulate run_workloads installing a router bracket filter.
    handler.addFilter(WlObjectNameFilter("router"))
    handler.addFilter(ContextAndTraceFilter())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    bind_context(component="router")
    logger.info("from router")
    router_line = stream.getvalue()
    assert "[router]" in router_line
    assert "component=router" in router_line

    stream.truncate(0)
    stream.seek(0)
    bind_context(component="controller")
    logger.info("from controller")
    controller_line = stream.getvalue()
    assert "[controller]" in controller_line
    assert "component=controller" in controller_line
    assert "[router]" not in controller_line

    stream.truncate(0)
    stream.seek(0)
    bind_context(component="workload", workload="vss-rtvi-cv")
    logger.info("from workload")
    wl_line = stream.getvalue()
    assert "[workload:vss-rtvi-cv]" in wl_line
    assert "component=workload" in wl_line
    clear_context()


def test_controller_context_bind_does_not_leak_to_parent_thread():
    """Watcher-thread bind_context(component=controller) must not flip parent router tag."""
    import threading

    from lib.logging import get_context

    clear_context()
    bind_context(component="router")
    seen = {}

    def _run_as_controller(fn):
        def _wrapped(*args, **kwargs):
            bind_context(component="controller")
            return fn(*args, **kwargs)

        return _wrapped

    def worker():
        seen.update(get_context())

    t = threading.Thread(target=_run_as_controller(worker))
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
    assert seen.get("component") == "controller"
    assert get_context().get("component") == "router"
    clear_context()
