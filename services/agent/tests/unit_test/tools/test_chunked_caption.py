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
"""Unit tests for the pure dense chunked captioning helpers (_chunked_caption).

Covers the window-tiling math and the aggregation orchestrator in isolation
(no VLM/VST dependencies), using a stub caption coroutine.
"""

import asyncio
from itertools import pairwise

import pytest

from vss_agents.tools._chunked_caption import caption_in_windows
from vss_agents.tools._chunked_caption import divide_into_windows


class TestDivideIntoWindows:
    def test_exact_multiple(self):
        assert divide_into_windows(60, 30) == [(0.0, 30.0), (30.0, 60.0)]

    def test_long_video_tiles_into_seven_windows(self):
        # The 210s clip that confabulates: 7 windows of 30s at ~1 fps each.
        assert divide_into_windows(210, 30) == [
            (0.0, 30.0),
            (30.0, 60.0),
            (60.0, 90.0),
            (90.0, 120.0),
            (120.0, 150.0),
            (150.0, 180.0),
            (180.0, 210.0),
        ]

    def test_partial_last_window(self):
        assert divide_into_windows(45, 30) == [(0.0, 30.0), (30.0, 45.0)]

    def test_window_longer_than_video_yields_single_window(self):
        assert divide_into_windows(20, 30) == [(0.0, 20.0)]

    def test_zero_duration_yields_single_window(self):
        assert divide_into_windows(0, 30) == [(0.0, 0.0)]

    def test_invalid_window_seconds_raises(self):
        with pytest.raises(ValueError):
            divide_into_windows(100, 0)
        with pytest.raises(ValueError):
            divide_into_windows(100, -1)

    def test_tiling_is_gapless_and_covers_full_duration(self):
        windows = divide_into_windows(217, 30)
        assert windows[0][0] == 0.0
        assert windows[-1][1] == 217
        for (_, prev_end), (next_start, _) in pairwise(windows):
            assert prev_end == next_start  # no gaps, no overlaps


class TestCaptionInWindows:
    @pytest.mark.asyncio
    async def test_one_call_per_window_with_absolute_headers(self):
        calls = []

        async def cap(window_start, window_end):
            calls.append((window_start, window_end))
            return f"caption for {int(window_start)}"

        result = await caption_in_windows(
            windows=divide_into_windows(90, 30),
            caption_window=cap,
            max_concurrency=4,
        )
        assert calls == [(0.0, 30.0), (30.0, 60.0), (60.0, 90.0)]
        assert result == ("[0-30s] caption for 0\n\n[30-60s] caption for 30\n\n[60-90s] caption for 60")

    @pytest.mark.asyncio
    async def test_short_video_single_window(self):
        calls = []

        async def cap(window_start, window_end):
            calls.append((window_start, window_end))
            return "only"

        result = await caption_in_windows(
            windows=divide_into_windows(12, 30),
            caption_window=cap,
            max_concurrency=3,
        )
        assert calls == [(0.0, 12.0)]
        assert result == "[0-12s] only"

    @pytest.mark.asyncio
    async def test_concurrency_is_bounded_by_max_concurrency(self):
        active = 0
        peak = 0

        async def cap(window_start, window_end):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return "x"

        # 300s / 30s = 10 windows, but never more than 2 in flight at once.
        await caption_in_windows(
            windows=divide_into_windows(300, 30),
            caption_window=cap,
            max_concurrency=2,
        )
        assert peak <= 2

    @pytest.mark.asyncio
    async def test_output_order_follows_window_order_not_completion(self):
        async def cap(window_start, window_end):
            # Earlier windows finish last if order were driven by completion time.
            await asyncio.sleep((100 - window_start) * 0.001)
            return f"w{int(window_start)}"

        result = await caption_in_windows(
            windows=divide_into_windows(90, 30),
            caption_window=cap,
            max_concurrency=4,
        )
        assert result == "[0-30s] w0\n\n[30-60s] w30\n\n[60-90s] w60"

    @pytest.mark.asyncio
    async def test_caption_whitespace_is_stripped(self):
        async def cap(window_start, window_end):
            return "  padded caption  \n"

        result = await caption_in_windows(
            windows=divide_into_windows(30, 30),
            caption_window=cap,
            max_concurrency=1,
        )
        assert result == "[0-30s] padded caption"

    @pytest.mark.asyncio
    async def test_one_failed_window_keeps_the_others(self):
        async def cap(window_start, window_end):
            if window_start == 30.0:
                raise RuntimeError("VLM boom")
            return f"ok{int(window_start)}"

        # [30-60s] fails after its retries -> placeholder; the successful windows are kept in place.
        result = await caption_in_windows(
            windows=divide_into_windows(90, 30),
            caption_window=cap,
            max_concurrency=4,
        )
        assert result == "[0-30s] ok0\n\n[30-60s] [caption unavailable]\n\n[60-90s] ok60"

    @pytest.mark.asyncio
    async def test_all_windows_failed_raises(self):
        async def cap(window_start, window_end):
            raise RuntimeError("VLM down")

        # Total outage still fails atomically, like the single-pass path.
        with pytest.raises(RuntimeError):
            await caption_in_windows(
                windows=divide_into_windows(90, 30),
                caption_window=cap,
                max_concurrency=2,
            )

    @pytest.mark.asyncio
    async def test_empty_window_caption_gets_placeholder(self):
        async def cap(window_start, window_end):
            return "   " if window_start == 0.0 else "real content"

        result = await caption_in_windows(
            windows=divide_into_windows(60, 30),
            caption_window=cap,
            max_concurrency=2,
        )
        assert result == "[0-30s] [no caption returned]\n\n[30-60s] real content"
