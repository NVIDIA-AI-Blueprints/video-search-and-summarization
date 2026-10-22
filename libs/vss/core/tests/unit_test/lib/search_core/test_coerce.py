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

"""Unit tests for the shared value-coercion helpers."""

from __future__ import annotations

from vss_core.search_core._internal.coerce import _coerce_float
from vss_core.search_core._internal.coerce import _coerce_str


def test_coerce_str_absent_values_use_default():
    assert _coerce_str(None) == ""
    assert _coerce_str("") == ""
    assert _coerce_str(None, "fallback") == "fallback"
    assert _coerce_str("", "fallback") == "fallback"


def test_coerce_str_preserves_zero():
    # object_id == 0 must survive rather than collapse to the default.
    assert _coerce_str(0) == "0"


def test_coerce_str_stringifies():
    assert _coerce_str("cam1") == "cam1"
    assert _coerce_str(42) == "42"
    assert _coerce_str(3.5) == "3.5"


def test_coerce_float_none_uses_default():
    assert _coerce_float(None) == 0.0
    assert _coerce_float(None, 1.5) == 1.5


def test_coerce_float_parses_numbers_and_strings():
    assert _coerce_float("2.5") == 2.5
    assert _coerce_float(3) == 3.0
    assert _coerce_float(0) == 0.0


def test_coerce_float_bad_value_uses_default():
    assert _coerce_float("abc") == 0.0
    assert _coerce_float("abc", 9.9) == 9.9
    assert _coerce_float([1, 2]) == 0.0
