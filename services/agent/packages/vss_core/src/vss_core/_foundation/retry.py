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
"""Transport-neutral async retry helper."""

from __future__ import annotations

import logging

from tenacity import AsyncRetrying
from tenacity import before_sleep_log
from tenacity import retry_if_exception_type
from tenacity import stop_after_attempt
from tenacity import wait_random_exponential

logger = logging.getLogger(__name__)


def create_retry_strategy(
    retries: int,
    delay: int | float = 2,
    *,
    exceptions: tuple[type[BaseException], ...],
) -> AsyncRetrying:
    """Build an ``AsyncRetrying`` strategy with jittered exponential backoff.

    Args:
        retries: total number of attempts (including the first) before giving up.
        delay: base wait unit in seconds. Waits grow exponentially with jitter,
            each capped at ``delay * 3`` — so with the default the wait is a
            random value in ``[0, 2]`` after the first failure, then ``[0, 4]``,
            then capped at ``6``.
        exceptions: tuple of exception types that trigger a retry.
    """
    return AsyncRetrying(
        retry=retry_if_exception_type(exceptions),
        stop=stop_after_attempt(retries),
        wait=wait_random_exponential(multiplier=delay, max=delay * 3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
