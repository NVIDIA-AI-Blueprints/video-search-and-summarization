# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the ISO 8601 <-> datetime helpers."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from vss_core._foundation.time import datetime_to_iso8601
from vss_core._foundation.time import iso8601_to_datetime
from vss_core._foundation.time import safe_iso8601_to_datetime


def test_round_trip_utc():
    original = datetime(2025, 8, 25, 3, 5, 55, 752000, tzinfo=UTC)
    text = datetime_to_iso8601(original)
    assert text == "2025-08-25T03:05:55.752000Z"
    assert iso8601_to_datetime(text) == original


def test_naive_datetime_treated_as_utc():
    naive = datetime(2025, 8, 25, 3, 5, 55)
    text = datetime_to_iso8601(naive)
    assert text.endswith("Z")
    assert text == "2025-08-25T03:05:55Z"


def test_aware_non_utc_converted_to_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    aware = datetime(2025, 8, 25, 8, 35, 55, tzinfo=ist)
    text = datetime_to_iso8601(aware)
    # 08:35:55 +05:30 == 03:05:55 UTC
    assert text == "2025-08-25T03:05:55Z"


def test_microsecond_zero_has_no_fractional_seconds():
    dt = datetime(2025, 8, 25, 3, 5, 55, 0, tzinfo=UTC)
    text = datetime_to_iso8601(dt)
    assert text == "2025-08-25T03:05:55Z"
    assert "." not in text


def test_fractional_seconds_preserved():
    dt = datetime(2025, 8, 25, 3, 5, 55, 123456, tzinfo=UTC)
    text = datetime_to_iso8601(dt)
    assert text == "2025-08-25T03:05:55.123456Z"


def test_iso8601_to_datetime_z_suffix():
    dt = iso8601_to_datetime("2025-08-25T03:05:55.752Z")
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timedelta(0)


def test_iso8601_to_datetime_naive_gets_utc():
    dt = iso8601_to_datetime("2025-08-25T03:05:55")
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timedelta(0)


def test_safe_iso8601_none_empty_and_garbage_return_none():
    assert safe_iso8601_to_datetime(None) is None
    assert safe_iso8601_to_datetime("") is None
    assert safe_iso8601_to_datetime("not-a-date") is None


def test_safe_iso8601_valid_parses():
    dt = safe_iso8601_to_datetime("2025-08-25T03:05:55Z")
    assert dt == datetime(2025, 8, 25, 3, 5, 55, tzinfo=UTC)
