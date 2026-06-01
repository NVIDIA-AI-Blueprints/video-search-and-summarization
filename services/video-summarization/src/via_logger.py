# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""VIA logger module"""

import logging
import logging.handlers
import os
import re
import time

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

# Configure the logger
logger = logging.getLogger(__name__)

for handler in logger.handlers[:]:
    logger.removeHandler(handler)

logging.addLevelName(LOG_PERF_LEVEL, "PERF")
logging.addLevelName(LOG_STATUS_LEVEL, "STATUS")


class SecureLogFilter(logging.Filter):
    """Filter to mask sensitive environment variable values in log messages."""

    # Environment variable names whose values should never appear in logs
    _SENSITIVE_ENV_VARS = [
        "NVIDIA_API_KEY",
        "OPENAI_API_KEY",
        "NGC_API_KEY",
        "NGC_CLI_KEY",
        "NGC_CLI_API_KEY",
        "GRAPH_DB_PASSWORD",
        "GRAPH_DB_USERNAME",
        "ARANGO_DB_PASSWORD",
        "ARANGO_DB_USERNAME",
        "MINIO_PASSWORD",
        "MINIO_USERNAME",
    ]

    _API_KEY_PATTERNS = [
        re.compile(r"nvapi-[A-Za-z0-9_-]+"),  # NVIDIA API keys
        re.compile(r"sk-[A-Za-z0-9_-]+"),  # OpenAI API keys
    ]

    def __init__(self):
        super().__init__()

    @staticmethod
    def mask_api_key_patterns(msg: str) -> str:
        """Mask strings matching API key patterns (nvapi-..., sk-...)."""
        masked = msg
        for pattern in SecureLogFilter._API_KEY_PATTERNS:
            masked = pattern.sub("***MASKED***", masked)
        return masked

    def filter(self, record):
        current_secrets = [os.getenv(var) for var in self._SENSITIVE_ENV_VARS if os.getenv(var)]

        full_msg = record.getMessage()
        masked_msg = full_msg
        for secret in current_secrets:
            if secret and secret in masked_msg:
                masked_msg = masked_msg.replace(secret, "***MASKED***")

        masked_msg = self.mask_api_key_patterns(masked_msg)

        # If we replaced anything, overwrite the record
        # so downstream formatters cannot recover the secret
        if masked_msg != full_msg:
            record.msg = masked_msg
            record.args = ()
        return True


class LogFormatter(logging.Formatter):

    def format(self, record):
        color = LOG_COLORS.get(record.levelname, LOG_COLORS["RESET"])
        return (
            f"{self.formatTime(record)} {color}{record.levelname}{LOG_COLORS['RESET']}"
            f" {record.getMessage()}"
        )


class SafeStreamHandler(logging.StreamHandler):
    """StreamHandler that silently ignores closed-stream errors during shutdown."""

    def emit(self, record):
        if hasattr(self.stream, "closed") and self.stream.closed:
            return
        super().emit(record)

    def handleError(self, record):
        import sys

        _, exc, _ = sys.exc_info()
        if isinstance(exc, (ValueError, OSError)):
            return
        super().handleError(record)


term_out = SafeStreamHandler()
term_out.setLevel(logging.INFO)
term_out.setFormatter(LogFormatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(term_out)

logger.addFilter(SecureLogFilter())

log_file = logging.handlers.TimedRotatingFileHandler("/tmp/via-logs/via_engine.log")
log_file.setLevel(LOG_PERF_LEVEL)
log_file.setFormatter(LogFormatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(log_file)

logger.setLevel(logging.INFO)
if os.environ.get("VSS_LOG_LEVEL"):
    logger.setLevel(os.environ.get("VSS_LOG_LEVEL").upper())
    term_out.setLevel(os.environ.get("VSS_LOG_LEVEL").upper())

# Replace root logger's StreamHandlers with SafeStreamHandler so third-party
# loggers (e.g. uvicorn) don't crash on closed streams during shutdown.
_root = logging.getLogger()
for _h in _root.handlers[:]:
    if isinstance(_h, logging.StreamHandler) and not isinstance(_h, SafeStreamHandler):
        safe_h = SafeStreamHandler(_h.stream)
        safe_h.setLevel(_h.level)
        safe_h.setFormatter(_h.formatter)
        for _f in _h.filters:
            safe_h.addFilter(_f)
        _root.removeHandler(_h)
        _root.addHandler(safe_h)


def safe_log(log_instance, level: str, msg: str, *args, **kwargs):
    """Deprecated: SafeStreamHandler now handles closed-stream errors at the handler level.
    Kept for backwards compatibility but simply delegates to the logger."""
    getattr(log_instance, level)(msg, *args, **kwargs)


def patch_logger_handlers(logger_name=None):
    """Replace StreamHandlers with SafeStreamHandler on the given logger (or root if None).
    Call this to protect third-party loggers (e.g. uvicorn) that create handlers lazily."""
    target = logging.getLogger(logger_name)
    for h in target.handlers[:]:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, SafeStreamHandler):
            safe_h = SafeStreamHandler(h.stream)
            safe_h.setLevel(h.level)
            safe_h.setFormatter(h.formatter)
            for f in h.filters:
                safe_h.addFilter(f)
            target.removeHandler(h)
            target.addHandler(safe_h)


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
            else:
                exec_time, unit = exec_time * 1e9, "nsec"
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
