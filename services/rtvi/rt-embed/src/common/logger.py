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
