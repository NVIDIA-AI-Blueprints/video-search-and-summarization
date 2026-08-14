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
    configure_root_logging("wl-test", str(tmp_path))

    logger = logging.getLogger("sdrc.test.logging")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(TextFormatter())
    handler.addFilter(WlObjectNameFilter("wl-test"))
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


def test_bind_context_and_clear():
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
