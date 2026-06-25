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
"""Async retry helper used by the VST helpers.

Ported byte-identical from vss_agents/utils/retry.py. Default exception set
remains aiohttp-specific because VST helpers use aiohttp; callers that use
httpx pass their own exception tuple.
"""

from __future__ import annotations

import logging

from aiohttp import ClientConnectorError
from aiohttp import ConnectionTimeoutError
from tenacity import AsyncRetrying
from tenacity import before_sleep_log
from tenacity import retry_if_exception_type
from tenacity import stop_after_attempt
from tenacity import wait_random

logger = logging.getLogger(__name__)


def create_retry_strategy(
    retries: int,
    delay: int | float = 2,
    exceptions: tuple = (ClientConnectorError, ConnectionTimeoutError),
) -> AsyncRetrying:
    """Build an AsyncRetrying strategy.

    Args:
        retries: number of attempts before giving up.
        delay: base delay in seconds; actual wait is random in [delay, delay*3].
        exceptions: tuple of exception types that trigger a retry.
    """
    return AsyncRetrying(
        retry=retry_if_exception_type(exceptions),
        stop=stop_after_attempt(retries),
        wait=wait_random(min=delay, max=delay * 3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
