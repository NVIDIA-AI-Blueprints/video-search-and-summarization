# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
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
