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

"""Unit tests for ``utils.logging_config``.

Two properties matter operationally:

* Log lines stay on a single line. Multi-line VLM responses would otherwise
  break line-oriented log shipping.
* Base64 data URLs are truncated. Mode 3 (direct media) embeds whole videos
  as ``data:video/mp4;base64,...``; logging one unabridged floods the log
  pipeline.

``setup_logging`` and ``enforce_log_level`` mutate global logging state, so
every test here runs behind the ``restore_logging`` fixture that snapshots
and restores the root logger.
"""

import logging
import os
from unittest.mock import patch

import pytest

from utils.logging_config import (
    _BASE64_TRUNCATE_LENGTH,
    _SingleLineFormatter,
    _truncate_base64,
    enforce_log_level,
    get_logger,
    setup_logging,
)


@pytest.fixture
def restore_logging():
    """Snapshot and restore global logging state around a test."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_formatters = [(h, h.formatter) for h in saved_handlers]
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for handler, formatter in saved_formatters:
        handler.setFormatter(formatter)


def make_record(msg, args=()):
    return logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=args, exc_info=None,
    )


class TestTruncateBase64:
    def test_long_data_url_is_truncated(self):
        payload = "A" * 500
        result = _truncate_base64(f"data:video/mp4;base64,{payload}")

        assert result.startswith("data:video/mp4;base64," + "A" * _BASE64_TRUNCATE_LENGTH)
        assert f"[truncated {500 - _BASE64_TRUNCATE_LENGTH} chars]" in result
        assert len(result) < 200

    def test_data_url_below_the_regex_threshold_is_untouched(self):
        """The pattern only matches payloads of 100+ chars."""
        text = "data:image/png;base64," + "A" * 40
        assert _truncate_base64(text) == text

    def test_custom_max_length_is_honoured(self):
        result = _truncate_base64("data:image/png;base64," + "A" * 200, max_length=10)
        assert "data:image/png;base64," + "A" * 10 + "...[truncated 190 chars]" == result

    def test_payload_matching_the_pattern_but_under_max_length_is_kept(self):
        """100 chars matches the regex but does not exceed a 150-char budget."""
        text = "data:image/png;base64," + "A" * 100
        assert _truncate_base64(text, max_length=150) == text

    def test_text_without_a_data_url_is_untouched(self):
        text = "plain log line with no payload"
        assert _truncate_base64(text) == text

    def test_multiple_data_urls_are_each_truncated(self):
        text = f"first data:image/png;base64,{'A' * 200} second data:video/mp4;base64,{'B' * 200}"
        result = _truncate_base64(text)
        assert result.count("[truncated 150 chars]") == 2

    def test_surrounding_text_is_preserved(self):
        result = _truncate_base64(f"before data:image/png;base64,{'A' * 200} after")
        assert result.startswith("before ")
        assert result.endswith(" after")

    def test_mime_type_is_matched_case_insensitively(self):
        result = _truncate_base64(f"DATA:IMAGE/PNG;BASE64,{'A' * 200}")
        assert "[truncated" in result


class TestSingleLineFormatter:
    def test_newlines_in_the_message_become_spaces(self):
        formatter = _SingleLineFormatter("%(message)s")
        assert formatter.format(make_record("line1\nline2")) == "line1 line2"

    def test_carriage_returns_become_spaces(self):
        formatter = _SingleLineFormatter("%(message)s")
        assert formatter.format(make_record("line1\r\nline2")) == "line1  line2"

    def test_formatted_output_never_contains_a_newline(self):
        formatter = _SingleLineFormatter("%(levelname)s - %(message)s")
        result = formatter.format(make_record("a\nb\nc"))
        assert "\n" not in result
        assert result == "INFO - a b c"

    def test_percent_style_args_are_interpolated(self):
        formatter = _SingleLineFormatter("%(message)s")
        assert formatter.format(make_record("value=%s", ("x",))) == "value=x"

    def test_base64_is_truncated_by_default(self):
        formatter = _SingleLineFormatter("%(message)s")
        result = formatter.format(make_record(f"data:video/mp4;base64,{'A' * 500}"))
        assert "[truncated" in result

    def test_base64_truncation_can_be_disabled(self):
        formatter = _SingleLineFormatter("%(message)s", truncate_base64=False)
        payload = "A" * 500
        result = formatter.format(make_record(f"data:video/mp4;base64,{payload}"))
        assert "[truncated" not in result
        assert payload in result

    def test_exception_traceback_is_collapsed_to_one_line(self):
        formatter = _SingleLineFormatter("%(message)s")
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname=__file__, lineno=1,
                msg="failed", args=(), exc_info=sys.exc_info(),
            )
        result = formatter.format(record)
        assert "\n" not in result
        assert "ValueError: boom" in result

    def test_datefmt_is_passed_through(self):
        formatter = _SingleLineFormatter("%(asctime)s|%(message)s", datefmt="%Y")
        result = formatter.format(make_record("hello"))
        assert result.endswith("|hello")
        assert len(result.split("|")[0]) == 4


class TestGetLogger:
    def test_returns_a_logger_with_the_requested_name(self):
        assert get_logger("alert.test").name == "alert.test"

    def test_repeated_calls_return_the_same_instance(self):
        assert get_logger("alert.test") is get_logger("alert.test")


class TestSetupLogging:
    def test_applies_level_from_config(self, restore_logging):
        with patch("utils.config.load_config", return_value={"logging": {"level": "DEBUG"}}):
            setup_logging("config.yaml")
        assert logging.getLogger().level == logging.DEBUG

    def test_defaults_to_info_when_config_has_no_logging_section(self, restore_logging):
        with patch("utils.config.load_config", return_value={}):
            setup_logging("config.yaml")
        assert logging.getLogger().level == logging.INFO

    def test_env_var_overrides_config_level(self, restore_logging):
        with patch("utils.config.load_config", return_value={"logging": {"level": "ERROR"}}), \
             patch.dict(os.environ, {"LOG_LEVEL_ROOT": "warning"}):
            setup_logging("config.yaml")
        assert logging.getLogger().level == logging.WARNING

    def test_invalid_level_is_reported_and_does_not_propagate(self, restore_logging, caplog):
        """The ValueError is swallowed by the outer handler and logged.

        The fallback ``basicConfig`` call omits ``force=True``, so when the
        root logger already has handlers it is a no-op and the level is left
        untouched — only the diagnostic is guaranteed.
        """
        with patch("utils.config.load_config", return_value={"logging": {"level": "LOUD"}}), \
             caplog.at_level(logging.ERROR, logger="utils.logging_config"):
            setup_logging("config.yaml")

        assert "Invalid log level 'LOUD'" in caplog.text

    def test_single_line_formatter_is_installed(self, restore_logging):
        with patch("utils.config.load_config", return_value={"logging": {"level": "INFO"}}):
            setup_logging("config.yaml")
        assert any(
            isinstance(h.formatter, _SingleLineFormatter) for h in logging.getLogger().handlers
        )

    def test_third_party_loggers_are_demoted(self, restore_logging):
        with patch(
            "utils.config.load_config",
            return_value={"logging": {"level": "DEBUG", "third_party_level": "ERROR"}},
        ):
            setup_logging("config.yaml")
        assert logging.getLogger("urllib3").level == logging.ERROR
        assert logging.getLogger("elasticsearch").level == logging.ERROR

    def test_third_party_level_env_override(self, restore_logging):
        with patch("utils.config.load_config", return_value={}), \
             patch.dict(os.environ, {"LOG_LEVEL_3P": "critical"}):
            setup_logging("config.yaml")
        assert logging.getLogger("httpx").level == logging.CRITICAL

    @pytest.mark.parametrize("flag", ["false", "0", "no"])
    def test_base64_truncation_can_be_disabled_by_env(self, restore_logging, flag):
        with patch("utils.config.load_config", return_value={}), \
             patch.dict(os.environ, {"LOG_TRUNCATE_BASE64": flag, "LOG_SINGLE_LINE": "true"}):
            setup_logging("config.yaml")
        formatters = [
            h.formatter for h in logging.getLogger().handlers
            if isinstance(h.formatter, _SingleLineFormatter)
        ]
        assert formatters and all(f.truncate_base64 is False for f in formatters)

    def test_missing_config_file_is_reported_and_does_not_propagate(self, restore_logging, caplog):
        with patch("utils.config.load_config", side_effect=FileNotFoundError), \
             caplog.at_level(logging.WARNING, logger="utils.logging_config"):
            setup_logging("nope.yaml")

        assert "Config file 'nope.yaml' not found" in caplog.text

    def test_unexpected_config_error_is_reported_and_does_not_propagate(
        self, restore_logging, caplog
    ):
        with patch("utils.config.load_config", side_effect=RuntimeError("bad yaml")), \
             caplog.at_level(logging.ERROR, logger="utils.logging_config"):
            setup_logging("config.yaml")

        assert "bad yaml" in caplog.text


class TestEnforceLogLevel:
    def test_reapplies_configured_level_to_existing_loggers(self, restore_logging):
        app_logger = logging.getLogger("alert.enforce.app")
        app_logger.setLevel(logging.CRITICAL)  # simulate a hardcoded setLevel()

        with patch("utils.config.load_config", return_value={"logging": {"level": "DEBUG"}}):
            enforce_log_level("config.yaml")

        assert app_logger.level == logging.DEBUG
        assert logging.getLogger().level == logging.DEBUG

    def test_third_party_loggers_keep_the_demoted_level(self, restore_logging):
        logging.getLogger("urllib3.connectionpool")

        with patch(
            "utils.config.load_config",
            return_value={"logging": {"level": "DEBUG", "third_party_level": "ERROR"}},
        ):
            enforce_log_level("config.yaml")

        assert logging.getLogger("urllib3.connectionpool").level == logging.ERROR

    def test_env_overrides_are_honoured(self, restore_logging):
        with patch("utils.config.load_config", return_value={}), \
             patch.dict(os.environ, {"LOG_LEVEL_ROOT": "warning"}):
            enforce_log_level("config.yaml")
        assert logging.getLogger().level == logging.WARNING

    def test_unknown_level_name_falls_back_to_info(self, restore_logging):
        with patch("utils.config.load_config", return_value={"logging": {"level": "LOUD"}}):
            enforce_log_level("config.yaml")
        assert logging.getLogger().level == logging.INFO

    def test_config_failure_is_swallowed(self, restore_logging):
        before = logging.getLogger().level
        with patch("utils.config.load_config", side_effect=RuntimeError("bad yaml")):
            enforce_log_level("config.yaml")
        assert logging.getLogger().level == before
