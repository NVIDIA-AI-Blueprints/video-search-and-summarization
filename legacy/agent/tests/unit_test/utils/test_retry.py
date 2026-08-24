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
"""Tests for vss_agents/utils/retry.py."""

import pytest

from vss_agents.utils.retry import create_retry_strategy


class TestCreateRetryStrategy:
    """Tests for create_retry_strategy function."""

    def test_create_retry_strategy_returns_async_retrying(self):
        """Test that function returns AsyncRetrying instance."""
        from tenacity import AsyncRetrying

        strategy = create_retry_strategy(retries=3)
        assert isinstance(strategy, AsyncRetrying)

    def test_create_retry_strategy_default_delay(self):
        """Test retry strategy with default delay."""
        strategy = create_retry_strategy(retries=3)
        # Verify the strategy was created without error
        assert strategy is not None

    def test_create_retry_strategy_custom_delay(self):
        """Test retry strategy with custom delay."""
        strategy = create_retry_strategy(retries=3, delay=5)
        assert strategy is not None

    def test_create_retry_strategy_single_retry(self):
        """Test retry strategy with single retry."""
        strategy = create_retry_strategy(retries=1)
        assert strategy is not None

    def test_create_retry_strategy_many_retries(self):
        """Test retry strategy with many retries."""
        strategy = create_retry_strategy(retries=10, delay=1)
        assert strategy is not None

    @pytest.mark.asyncio
    async def test_retry_strategy_on_success(self):
        """Test retry strategy when function succeeds."""
        call_count = 0

        async def success_func():
            nonlocal call_count
            call_count += 1
            return "success"

        strategy = create_retry_strategy(retries=3)
        async for attempt in strategy:
            with attempt:
                result = await success_func()

        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_strategy_on_other_exception(self):
        """Test that non-retryable exceptions are raised immediately."""
        strategy = create_retry_strategy(retries=3)

        async def failing_func():
            raise ValueError("Not a connection error")

        with pytest.raises(ValueError, match="Not a connection error"):
            async for attempt in strategy:
                with attempt:
                    await failing_func()
