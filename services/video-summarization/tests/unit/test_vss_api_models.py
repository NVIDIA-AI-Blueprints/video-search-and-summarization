# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for cross-field validation in vss_api_models.

NVBug 6542937: /summarize must reject reversed media offsets and
min_tokens > max_tokens with 422 Unprocessable Entity instead of
silently returning 200 OK with empty choices.
"""

import pytest
from pydantic import ValidationError

from vss_api_models import MediaInfoOffset, SummarizationQuery


# ---------------------------------------------------------------------------
# MediaInfoOffset — reversed offset rejection
# ---------------------------------------------------------------------------


class TestMediaInfoOffsetValidation:
    def test_valid_offsets_accepted(self):
        m = MediaInfoOffset(type="offset", start_offset=0, end_offset=60)
        assert m.start_offset == 0
        assert m.end_offset == 60

    def test_reversed_offsets_rejected(self):
        with pytest.raises(ValidationError, match="start_offset.*must be less than.*end_offset"):
            MediaInfoOffset(type="offset", start_offset=50, end_offset=20)

    def test_equal_offsets_rejected(self):
        with pytest.raises(ValidationError, match="start_offset.*must be less than.*end_offset"):
            MediaInfoOffset(type="offset", start_offset=30, end_offset=30)

    def test_only_start_offset_no_error(self):
        m = MediaInfoOffset(type="offset", start_offset=10, end_offset=None)
        assert m.start_offset == 10

    def test_only_end_offset_no_error(self):
        m = MediaInfoOffset(type="offset", start_offset=None, end_offset=60)
        assert m.end_offset == 60

    def test_both_none_no_error(self):
        m = MediaInfoOffset(type="offset", start_offset=None, end_offset=None)
        assert m.start_offset is None
        assert m.end_offset is None


# ---------------------------------------------------------------------------
# SummarizationQuery — min_tokens > max_tokens rejection
# ---------------------------------------------------------------------------


def _base_query(**overrides):
    """Minimal valid SummarizationQuery kwargs."""
    defaults = dict(
        url="http://media-server/1min.mp4",
        model="cosmos-reason1",
        scenario="warehouse",
        events=["forklift", "crash"],
    )
    defaults.update(overrides)
    return defaults


class TestSummarizationQueryTokenConstraints:
    def test_valid_min_less_than_max(self):
        q = SummarizationQuery(**_base_query(min_tokens=10, max_tokens=100))
        assert q.min_tokens == 10
        assert q.max_tokens == 100

    def test_min_tokens_greater_than_max_rejected(self):
        with pytest.raises(ValidationError, match="min_tokens.*must not exceed.*max_tokens"):
            SummarizationQuery(**_base_query(min_tokens=100, max_tokens=10))

    def test_min_tokens_equal_to_max_accepted(self):
        q = SummarizationQuery(**_base_query(min_tokens=50, max_tokens=50))
        assert q.min_tokens == 50

    def test_only_max_tokens_no_error(self):
        q = SummarizationQuery(**_base_query(max_tokens=512))
        assert q.max_tokens == 512
        assert q.min_tokens is None

    def test_only_min_tokens_no_error(self):
        q = SummarizationQuery(**_base_query(min_tokens=10))
        assert q.min_tokens == 10

    def test_neither_token_field_set_no_error(self):
        q = SummarizationQuery(**_base_query())
        assert q.min_tokens is None
