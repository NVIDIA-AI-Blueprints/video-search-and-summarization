# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for the UUID-string detection helper."""

from __future__ import annotations

import pytest

from vss_core.search_core._internal.uuid_string import is_standard_uuid_string


@pytest.mark.parametrize(
    "value",
    [
        "8fce43a6-1c35-4d6a-b6e3-391c42090a87",
        "8FCE43A6-1C35-4D6A-B6E3-391C42090A87",
        "8fce43a61c354d6ab6e3391c42090a87",  # 32 hex chars, no dashes
    ],
)
def test_valid_uuid_strings(value):
    assert is_standard_uuid_string(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "camera-01",
        "not-a-uuid",
        "8fce43a6-1c35-4d6a-b6e3",  # too short
        "",
    ],
)
def test_invalid_uuid_strings(value):
    assert is_standard_uuid_string(value) is False


@pytest.mark.parametrize("value", [None, 123, ["x"], object()])
def test_non_string_values(value):
    assert is_standard_uuid_string(value) is False
