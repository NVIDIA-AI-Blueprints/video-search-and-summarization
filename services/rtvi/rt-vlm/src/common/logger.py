# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Logger module"""

import logging
import logging.handlers
import os
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

LOG_COLORS = {
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
    "ERROR": "\033[91m",
    "WARNING": "\033[93m",
    "INFO": "\033[94m",
    "DEBUG": "\033[96m",
    "STATUS": "\033[94m",
    "PERF": "\033[95m",
}

LOG_PERF_LEVEL = 15
LOG_STATUS_LEVEL = 16

_LOGGED_URL_PATTERN = re.compile(
    r"(?:https?|s3|rtsp|file)://[^\s\"'<>]+", re.IGNORECASE
)


def sanitize_url_for_logging(url: str) -> str:
    """Return a URL safe to include in logs.

    URL user-info, query parameters, and fragments can all carry credentials.
    Logging only the origin and path keeps the source identifiable without
    requiring an ever-growing list of sensitive parameter names.
    """
    try:
        parsed = urlsplit(url)
        netloc = parsed.netloc.rsplit("@", 1)[-1]
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except (TypeError, ValueError):
        return "[malformed URL redacted]"


def sanitize_urls_for_logging(message: str) -> str:
    """Remove credential-bearing portions from URLs embedded in log text.

    This is a defense-in-depth fallback for call sites that have not sanitized
    their structured values.  Once a query or fragment starts, its contents
    are attacker-controlled and may contain any apparent log delimiter.  The
    only safe generic boundary is therefore the end of the message.
    """
    message = str(message)
    match = _LOGGED_URL_PATTERN.search(message)
    while match:
        url = match.group(0)
        delimiter_offsets = [
            offset for offset in (url.find("?"), url.find("#")) if offset >= 0
        ]
        if delimiter_offsets:
            delimiter = min(delimiter_offsets)
            safe_url = sanitize_url_for_logging(url[:delimiter])
            return f"{message[:match.start()]}{safe_url} [URL query redacted]"

        safe_url = sanitize_url_for_logging(url)
        message = f"{message[:match.start()]}{safe_url}{message[match.end():]}"
        next_start = match.start() + len(safe_url)
        match = _LOGGED_URL_PATTERN.search(message, next_start)
    return message


def sanitize_data_for_logging(value: Any) -> Any:
    """Return structured request data with URL credentials removed."""
    if isinstance(value, Mapping):
        sanitized = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key == "url_headers":
                sanitized[key] = "[redacted]"
            elif normalized_key.endswith("url") and isinstance(item, str):
                sanitized[key] = sanitize_url_for_logging(item)
            else:
                sanitized[key] = sanitize_data_for_logging(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_data_for_logging(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_data_for_logging(item) for item in value)
    return value


class _SensitiveURLFilter(logging.Filter):
    """Sanitize the LogRecord itself before any handler or propagation."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize_urls_for_logging(record.getMessage())
        record.args = ()
        return True


# Configure the logger
logger = logging.getLogger(__name__)

for handler in logger.handlers[:]:
    logger.removeHandler(handler)

for log_filter in logger.filters[:]:
    logger.removeFilter(log_filter)
logger.addFilter(_SensitiveURLFilter())

logging.addLevelName(LOG_PERF_LEVEL, "PERF")
logging.addLevelName(LOG_STATUS_LEVEL, "STATUS")


class LogFormatter(logging.Formatter):

    def format(self, record):
        color = LOG_COLORS.get(record.levelname, LOG_COLORS["RESET"])
        return (
            f"{self.formatTime(record)} {color}{record.levelname}{LOG_COLORS['RESET']}"
            f" {record.getMessage()}"
        )


term_out = logging.StreamHandler()
term_out.setLevel(logging.INFO)
term_out.setFormatter(LogFormatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(term_out)

log_path = os.environ.get("LOG_FILE_PATH", "/opt/nvidia/rtvi/log/rtvi/rtvi.log")

# Best-effort file-handler setup. In non-Docker test environments the default
# /opt/nvidia/rtvi/log path is typically not writable; warn and continue with
# the stream handler rather than raising at import time.
try:
    log_dir = os.path.dirname(log_path)
    os.makedirs(log_dir, exist_ok=True)

    log_file = logging.handlers.TimedRotatingFileHandler(log_path)
    log_file.setLevel(LOG_PERF_LEVEL)
    log_file.setFormatter(LogFormatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(log_file)
except OSError as ex:
    logger.warning(
        "Could not set up log file at %s (%s); continuing with stream logging only. "
        "Set LOG_FILE_PATH to a writable location to enable file logging.",
        log_path,
        ex,
    )

logger.setLevel(logging.INFO)
if os.environ.get("LOG_LEVEL"):
    logger.setLevel(os.environ.get("LOG_LEVEL").upper())
    term_out.setLevel(os.environ.get("LOG_LEVEL").upper())


class TimeMeasure:
    """Measures the execution time of a block of code. This class is used as a
    context manager.
    """

    def __init__(self, string: str, print=False) -> None:
        """Class constructor

        Args:
            string (str): A string to identify the code block while printing the execution time.
            print (bool, optional): Print the execution time. Defaults to True.
        """
        self._string = string
        self._print = print

    def __enter__(self):
        self._start_time = time.time()
        return self

    def __exit__(self, type, value, traceback):
        self._end_time = time.time()
        exec_time = self._end_time - self._start_time
        if logger.level <= LOG_PERF_LEVEL:
            if exec_time > 1:
                exec_time, unit = exec_time, "sec"
            elif exec_time > 0.001:
                exec_time, unit = exec_time * 1000.0, "millisec"
            elif exec_time > 1e-6:
                exec_time, unit = exec_time * 1e6, "usec"
            logger.log(LOG_PERF_LEVEL, "%s execution time = %.3f %s", self._string, exec_time, unit)
            logger.debug(
                "%s start=%s end=%s",
                self._string,
                str(self._start_time),
                str(self._end_time),
            )

    @property
    def execution_time(self):
        """Execution time of the code block.
        Should be used once the code block is finished executing.

        Returns:
            float: Execution time in seconds
        """
        return self._end_time - self._start_time

    @property
    def current_execution_time(self):
        """Current execution time of the code block. Can be used inside the code block.

        Returns:
            float: Execution time in seconds
        """
        return time.time() - self._start_time
