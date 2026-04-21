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
import logging
import time

logger = logging.getLogger(__name__)

LOG_PERF_LEVEL = 15
LOG_STATUS_LEVEL = 16

logging.addLevelName(LOG_PERF_LEVEL, "PERF")
logging.addLevelName(LOG_STATUS_LEVEL, "STATUS")


class TimeMeasure:
    """Measures the execution time of a block of code. This class is used as a
    context manager.
    """

    def __init__(self, string: str, print: bool = True) -> None:
        """Class constructor

        Args:
            string (str): A string to identify the code block while printing the execution time.
            print (bool, optional): Print the execution time. Defaults to True.
        """
        self._string = string
        self._print = print

    def __enter__(self) -> "TimeMeasure":
        self._start_time = time.perf_counter()
        logger.debug("[START] " + self._string)
        return self

    def __exit__(self, type: type[BaseException] | None, value: BaseException | None, traceback: object) -> None:
        self._end_time = time.perf_counter()
        logger.debug("[END]   " + self._string)
        if self._print:
            exec_time = self._end_time - self._start_time
            if exec_time > 1:
                exec_time, unit = exec_time, "sec"
            elif exec_time > 0.001:
                exec_time, unit = exec_time * 1000.0, "millisec"
            elif exec_time > 1e-6:
                exec_time, unit = exec_time * 1e6, "usec"
            else:
                exec_time, unit = exec_time * 1e9, "nanosec"
            logger.log(
                LOG_PERF_LEVEL,
                f"{self._string:s} execution time = {exec_time:.3f} {unit:s}",
            )
            logger.debug(f"{self._string} start={self._start_time!s} end={self._end_time!s}")

    @property
    def execution_time(self) -> float:
        """Execution time of the code block.
        Should be used once the code block is finished executing.

        Returns:
            float: Execution time in seconds
        """
        return self._end_time - self._start_time

    @property
    def current_execution_time(self) -> float:
        """Current execution time of the code block. Can be used inside the code block.

        Returns:
            float: Execution time in seconds
        """
        return time.perf_counter() - self._start_time
