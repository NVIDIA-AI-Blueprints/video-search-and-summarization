# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the async retry strategy builder."""

from __future__ import annotations

import pytest

from vss_core._foundation.retry import create_retry_strategy


class _RetryableError(Exception):
    pass


class _OtherError(Exception):
    pass


@pytest.mark.asyncio
async def test_stops_after_n_attempts_and_reraises():
    attempts = 0
    # delay=0 keeps the exponential wait at zero so the test runs instantly.
    with pytest.raises(_RetryableError):
        async for attempt in create_retry_strategy(retries=3, delay=0, exceptions=(_RetryableError,)):
            with attempt:
                attempts += 1
                raise _RetryableError("boom")
    assert attempts == 3


@pytest.mark.asyncio
async def test_succeeds_after_transient_failures():
    attempts = 0
    async for attempt in create_retry_strategy(retries=5, delay=0, exceptions=(_RetryableError,)):
        with attempt:
            attempts += 1
            if attempts < 3:
                raise _RetryableError("transient")
    assert attempts == 3


@pytest.mark.asyncio
async def test_only_retries_listed_exception_types():
    attempts = 0
    with pytest.raises(_OtherError):
        async for attempt in create_retry_strategy(retries=3, delay=0, exceptions=(_RetryableError,)):
            with attempt:
                attempts += 1
                raise _OtherError("not retryable")
    # An unlisted exception propagates on the first attempt (no retries).
    assert attempts == 1
