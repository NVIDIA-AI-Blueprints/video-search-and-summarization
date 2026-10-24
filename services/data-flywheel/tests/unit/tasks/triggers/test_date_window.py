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

"""Tests for date window calculator."""

from datetime import date, datetime

import pytest

from src.tasks.triggers.date_window import DataWindowCalculator


class TestDataWindowCalculator:
    """Tests for DataWindowCalculator class."""

    def test_calculate_all_data(self):
        """Test 'all' returns None for all data."""
        calculator = DataWindowCalculator()
        result = calculator.calculate_date_range("all")

        assert result is None

    def test_calculate_one_month(self):
        """Test 1 month calculation (30 days)."""
        calculator = DataWindowCalculator()
        result = calculator.calculate_date_range(1)

        assert result is not None
        assert "from" in result
        assert "to" in result

        # Parse as date (not datetime) since we return date-only strings
        from_date = date.fromisoformat(result["from"])
        to_date = date.fromisoformat(result["to"])

        # Should be approximately 30 days
        delta = to_date - from_date
        assert 29 <= delta.days <= 31

    def test_calculate_twelve_months(self):
        """Test 12 months equals 360 days (30 * 12)."""
        calculator = DataWindowCalculator()
        result = calculator.calculate_date_range(12)

        assert result is not None

        from_date = date.fromisoformat(result["from"])
        to_date = date.fromisoformat(result["to"])

        # Should be 360 days (12 months * 30 days)
        delta = to_date - from_date
        assert 359 <= delta.days <= 361

    def test_calculate_six_months(self):
        """Test 6 months calculation (180 days)."""
        calculator = DataWindowCalculator()
        result = calculator.calculate_date_range(6)

        assert result is not None

        from_date = date.fromisoformat(result["from"])
        to_date = date.fromisoformat(result["to"])

        # Should be 180 days (6 months * 30 days)
        delta = to_date - from_date
        assert 179 <= delta.days <= 181

    def test_negative_months_raises_error(self):
        """Test negative months raises ValueError."""
        calculator = DataWindowCalculator()

        with pytest.raises(ValueError) as exc_info:
            calculator.calculate_date_range(-1)

        assert "must be positive" in str(exc_info.value)

    def test_zero_months_raises_error(self):
        """Test zero months raises ValueError."""
        calculator = DataWindowCalculator()

        with pytest.raises(ValueError) as exc_info:
            calculator.calculate_date_range(0)

        assert "must be positive" in str(exc_info.value)

    def test_invalid_type_raises_error(self):
        """Test invalid window type raises ValueError."""
        calculator = DataWindowCalculator()

        with pytest.raises(ValueError) as exc_info:
            calculator.calculate_date_range("invalid")  # type: ignore

        assert "Invalid data window type" in str(exc_info.value)

    def test_date_range_format(self):
        """Test date range is in date-only ISO format (YYYY-MM-DD)."""
        calculator = DataWindowCalculator()
        result = calculator.calculate_date_range(3)

        assert result is not None

        # Should be date-only strings (YYYY-MM-DD), not datetime strings
        assert "T" not in result["from"], "from_date should not contain time component"
        assert "T" not in result["to"], "to_date should not contain time component"

        # Should be able to parse as date (not datetime)
        from_date = date.fromisoformat(result["from"])
        to_date = date.fromisoformat(result["to"])

        assert isinstance(from_date, date)
        assert isinstance(to_date, date)
        assert not isinstance(from_date, datetime)
        assert not isinstance(to_date, datetime)

        # Verify format is YYYY-MM-DD
        assert len(result["from"]) == 10, "from_date should be YYYY-MM-DD format"
        assert len(result["to"]) == 10, "to_date should be YYYY-MM-DD format"
        assert result["from"].count("-") == 2, "from_date should have 2 dashes"
        assert result["to"].count("-") == 2, "to_date should have 2 dashes"

    def test_date_range_pydantic_compatible(self):
        """Test that returned dates are compatible with Pydantic date validation."""
        from pydantic import BaseModel

        class DateConfig(BaseModel):
            from_date: date | None
            to_date: date | None

        calculator = DataWindowCalculator()
        result = calculator.calculate_date_range(3)

        assert result is not None

        # Should be able to create Pydantic model with these date strings
        config = DateConfig(
            from_date=result["from"],
            to_date=result["to"],
        )

        assert isinstance(config.from_date, date)
        assert isinstance(config.to_date, date)

    def test_to_date_is_current_date(self):
        """Test that 'to' date is approximately today (within 1 day tolerance)."""
        calculator = DataWindowCalculator()
        result = calculator.calculate_date_range(1)

        assert result is not None

        to_date = date.fromisoformat(result["to"])
        today = date.today()

        # Should be today or yesterday (accounting for timezone differences)
        assert (
            abs((to_date - today).days) <= 1
        ), f"to_date ({to_date}) should be today or yesterday, got {today}"

    def test_from_date_is_calculated_correctly(self):
        """Test that from_date is correctly calculated as (now - window * 30 days)."""
        calculator = DataWindowCalculator()

        # Test with 3 months (90 days)
        result = calculator.calculate_date_range(3)

        assert result is not None

        from_date = date.fromisoformat(result["from"])
        to_date = date.fromisoformat(result["to"])

        # Calculate expected difference
        expected_days = 3 * 30  # 90 days

        # Allow 1 day tolerance for timing differences
        actual_days = (to_date - from_date).days
        assert (
            abs(actual_days - expected_days) <= 1
        ), f"Expected approximately {expected_days} days, got {actual_days} days"

    def test_static_method(self):
        """Test that calculate_date_range can be called as a static method."""
        # Should work without instantiating the class
        result = DataWindowCalculator.calculate_date_range(1)

        assert result is not None
        assert "from" in result
        assert "to" in result
