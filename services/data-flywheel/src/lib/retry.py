# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Neutral transient-error retry helper (no backend SDK imports).

This is the single backoff policy shared by every HTTP client in DFW. It lives
in ``src/lib`` (generic) so backend-agnostic code — and the DEFT backend, which
must run on images without the NMP SDK — can use it without pulling in
``nemo_platform``. ``src/backends/nmp/retry_utils.py`` re-exports these names
for existing NMP importers.

IMPORTANT: only retry idempotent operations. Retrying a resource-creating POST
after a timeout can double-submit (e.g. duplicate a training run) because the
server may have accepted the first attempt. Callers gate this via their own
``retryable`` flags; this helper never inspects the HTTP method.
"""

from collections.abc import Callable
from time import sleep
from typing import TypeVar

import requests

from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.lib.retry")

# Retry configuration for transient connection errors
API_MAX_RETRIES = 3
API_RETRY_BACKOFF_FACTOR = 2.0  # seconds: 2, 4, 8

T = TypeVar("T")


def is_transient_error(error: Exception) -> bool:
    """Check if an error is a transient connection error that should be retried."""
    return (
        isinstance(error, requests.exceptions.ConnectionError)
        or isinstance(error, requests.exceptions.Timeout)
        or isinstance(error, TimeoutError)
        # nemo_platform.APITimeoutError, matched by name so this module never
        # imports the NMP SDK (deft-only images don't ship it).
        or type(error).__name__ == "APITimeoutError"
        or "RemoteDisconnected" in str(error)
        or "Connection aborted" in str(error)
        or "Remote end closed connection" in str(error)
        or "ProtocolError" in str(error)
        or "Connection reset" in str(error)
        or "timed out" in str(error).lower()
    )


def retry_on_transient_error(
    func: Callable[..., T],
    operation_name: str,
    *args,
    max_retries: int = API_MAX_RETRIES,
    backoff_factor: float = API_RETRY_BACKOFF_FACTOR,
    **kwargs,
) -> T:
    """
    Execute a function with retry logic for transient connection errors.

    Args:
        func: The function to execute
        operation_name: Name of the operation for logging
        *args: Positional arguments to pass to the function
        max_retries: Maximum number of retry attempts (keyword-only)
        backoff_factor: Exponential backoff factor in seconds (keyword-only)
        **kwargs: Keyword arguments to pass to the function

    Returns:
        The result of the function call

    Raises:
        ValueError: If max_retries < 1 or backoff_factor <= 0
        The last exception if all retries are exhausted
    """
    # Guard the loop bounds: max_retries=0 would skip the loop entirely and
    # silently return None instead of the callable's result.
    if max_retries < 1:
        raise ValueError("max_retries must be >= 1")
    if backoff_factor <= 0:
        raise ValueError("backoff_factor must be > 0")

    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # Check if this is a transient connection error that should be retried
            if is_transient_error(e) and attempt < max_retries:
                wait_time = backoff_factor**attempt
                logger.warning(
                    f"Transient connection error in {operation_name} "
                    f"(attempt {attempt}/{max_retries}): {e}. "
                    f"Retrying in {wait_time:.0f}s..."
                )
                sleep(wait_time)
                continue
            else:
                # Non-retryable error or max retries reached
                raise e
