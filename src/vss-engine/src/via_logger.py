######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################
"""VIA logger module"""

import logging
import logging.handlers
import os
import sys
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
term_out.setLevel(LOG_PERF_LEVEL)
term_out.setFormatter(LogFormatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(term_out)

log_file = logging.handlers.TimedRotatingFileHandler("/tmp/via-logs/via_engine.log")
log_file.setLevel(LOG_PERF_LEVEL)
log_file.setFormatter(LogFormatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(log_file)

logger.setLevel(logging.INFO)
if os.environ.get("VSS_LOG_LEVEL"):
    logger.setLevel(os.environ.get("VSS_LOG_LEVEL").upper())


class TimeMeasure:
    """Measures the execution time of a block of code. This class is used as a
    context manager.
    """

    def __init__(self, string: str, print=True) -> None:
        """Class constructor

        Args:
            string (str): A string to identify the code block while printing the execution time.
            print (bool, optional): Print the execution time. Defaults to True.
        """
        self._string = string
        self._print = print

    def __enter__(self):
        self._start_time = time.time()
        logger.debug("[START] " + self._string)
        return self

    def __exit__(self, type, value, traceback):
        self._end_time = time.time()
        logger.debug("[END]   " + self._string)
        if self._print:
            exec_time = self._end_time - self._start_time
            if exec_time > 1:
                exec_time, unit = exec_time, "sec"
            elif exec_time > 0.001:
                exec_time, unit = exec_time * 1000.0, "millisec"
            elif exec_time > 1e-6:
                exec_time, unit = exec_time * 1e6, "usec"
            logger.log(
                LOG_PERF_LEVEL,
                "{:s} execution time = {:.3f} {:s}".format(self._string, exec_time, unit),
            )
            print(
                "{:s} execution time = {:.3f} {:s}".format(self._string, exec_time, unit),
                file=sys.stderr,
            )
            logger.debug(f"{self._string} start={str(self._start_time)} end={str(self._end_time)}")

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
