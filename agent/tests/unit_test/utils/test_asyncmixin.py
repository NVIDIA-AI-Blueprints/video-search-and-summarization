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
"""Tests for vss_agents/utils/asyncmixin.py."""

import pytest

from vss_agents.utils.asyncmixin import AsyncMixin


class TestAsyncMixin:
    """Tests for AsyncMixin class."""

    @pytest.mark.asyncio
    async def test_async_initialization(self):
        """Test async initialization using await."""

        class TestClass(AsyncMixin):
            async def __ainit__(self, value):
                self.value = value

        instance = await TestClass(42)
        assert instance.value == 42
        assert instance.async_initialized is True

    @pytest.mark.asyncio
    async def test_stored_args(self):
        """Test that constructor args are stored and passed to __ainit__."""

        class TestClass(AsyncMixin):
            async def __ainit__(self, a, b, c=None):
                self.a = a
                self.b = b
                self.c = c

        instance = await TestClass(1, 2, c=3)
        assert instance.a == 1
        assert instance.b == 2
        assert instance.c == 3

    @pytest.mark.asyncio
    async def test_async_initialized_flag(self):
        """Test async_initialized flag is False before await."""

        class TestClass(AsyncMixin):
            async def __ainit__(self):
                pass

        obj = TestClass()
        assert obj.async_initialized is False

        instance = await obj
        assert instance.async_initialized is True

    @pytest.mark.asyncio
    async def test_await_returns_self(self):
        """Test that awaiting returns the instance."""

        class TestClass(AsyncMixin):
            async def __ainit__(self):
                pass

        obj = TestClass()
        result = await obj
        assert result is obj

    @pytest.mark.asyncio
    async def test_async_init_with_no_params(self):
        """Test class with no parameters."""

        class TestClass(AsyncMixin):
            async def __ainit__(self):
                self.initialized = True

        instance = await TestClass()
        assert instance.initialized is True

    @pytest.mark.asyncio
    async def test_double_await_raises(self):
        """Test that awaiting twice raises assertion error."""

        class TestClass(AsyncMixin):
            async def __ainit__(self):
                pass

        instance = await TestClass()

        # Awaiting again should raise AssertionError
        with pytest.raises(AssertionError):
            await instance

    @pytest.mark.asyncio
    async def test_async_init_exception(self):
        """Test that exceptions in __ainit__ propagate."""

        class TestClass(AsyncMixin):
            async def __ainit__(self):
                raise ValueError("Init failed")

        with pytest.raises(ValueError, match="Init failed"):
            await TestClass()

    @pytest.mark.asyncio
    async def test_async_init_with_async_operations(self):
        """Test __ainit__ with actual async operations."""
        import asyncio

        class TestClass(AsyncMixin):
            async def __ainit__(self, delay):
                await asyncio.sleep(delay)
                self.completed = True

        instance = await TestClass(0.001)
        assert instance.completed is True
