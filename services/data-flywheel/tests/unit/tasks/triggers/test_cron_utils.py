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

"""Tests for cron utilities."""

import pytest
from celery.schedules import crontab

from src.tasks.triggers.cron_utils import crontab_from_string, validate_cron


class TestValidateCron:
    """Tests for validate_cron function."""

    def test_valid_cron_five_fields(self):
        """Test valid cron with 5 fields."""
        result = validate_cron("0 0 * * *")
        assert result is True

    def test_valid_cron_with_ranges(self):
        """Test valid cron with ranges."""
        result = validate_cron("0 */2 * * 1-5")
        assert result is True

    def test_invalid_cron_too_few_fields(self):
        """Test cron with too few fields raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_cron("0 0 *")

        assert "must have 5 fields" in str(exc_info.value)
        assert "got 3" in str(exc_info.value)

    def test_invalid_cron_too_many_fields(self):
        """Test cron with too many fields raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_cron("0 0 * * * * *")

        assert "must have 5 fields" in str(exc_info.value)
        assert "got 7" in str(exc_info.value)


class TestCrontabFromString:
    """Tests for crontab_from_string function."""

    def test_valid_cron_daily_midnight(self):
        """Test valid daily at midnight cron."""
        result = crontab_from_string("0 0 * * *")

        assert isinstance(result, crontab)
        assert 0 in result.minute
        assert 0 in result.hour

    def test_valid_cron_every_two_hours(self):
        """Test valid every 2 hours cron."""
        result = crontab_from_string("0 */2 * * *")

        assert isinstance(result, crontab)
        assert 0 in result.minute

    def test_valid_cron_monday_morning(self):
        """Test valid Monday at 8:30 AM cron."""
        result = crontab_from_string("30 8 * * 1")

        assert isinstance(result, crontab)
        assert 30 in result.minute
        assert 8 in result.hour
        assert 1 in result.day_of_week

    def test_valid_cron_first_of_month(self):
        """Test valid first day of month cron."""
        result = crontab_from_string("0 0 1 * *")

        assert isinstance(result, crontab)
        assert 1 in result.day_of_month

    def test_valid_cron_every_15_minutes(self):
        """Test valid every 15 minutes cron."""
        result = crontab_from_string("*/15 * * * *")

        assert isinstance(result, crontab)
        # */15 expands to set of values

    def test_invalid_minute_value(self):
        """Test invalid minute value (70) raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            crontab_from_string("70 0 * * *")

        assert "Invalid cron expression" in str(exc_info.value)
        assert "70 0 * * *" in str(exc_info.value)

    def test_invalid_hour_value(self):
        """Test invalid hour value (25) raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            crontab_from_string("0 25 * * *")

        assert "Invalid cron expression" in str(exc_info.value)
        assert "0 25 * * *" in str(exc_info.value)

    def test_invalid_day_of_week(self):
        """Test invalid day of week (8) raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            crontab_from_string("0 0 * * 8")

        assert "Invalid cron expression" in str(exc_info.value)

    def test_invalid_day_of_month(self):
        """Test invalid day of month (32) raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            crontab_from_string("0 0 32 * *")

        assert "Invalid cron expression" in str(exc_info.value)

    def test_invalid_day_of_month_zero(self):
        """Test day of month 0 raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            crontab_from_string("0 0 0 * *")

        assert "Invalid cron expression" in str(exc_info.value)

    def test_invalid_characters(self):
        """Test invalid characters in cron raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            crontab_from_string("abc 0 * * *")

        assert "Invalid cron expression" in str(exc_info.value)

    def test_error_message_includes_original_expression(self):
        """Test error message includes the original invalid expression."""
        test_expr = "99 99 99 99 99"

        with pytest.raises(ValueError) as exc_info:
            crontab_from_string(test_expr)

        assert test_expr in str(exc_info.value)
