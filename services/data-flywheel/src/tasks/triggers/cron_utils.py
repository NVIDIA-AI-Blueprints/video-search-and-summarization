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

"""Cron expression utilities for triggers."""

from celery.schedules import crontab


def validate_cron(expression: str) -> bool:
    """
    Validate cron expression format.

    Args:
        expression: Cron expression (5 fields: minute hour day month dow)

    Returns:
        True if valid

    Raises:
        ValueError: If expression is invalid
    """
    parts = expression.split()

    if len(parts) != 5:
        raise ValueError(f"Cron expression must have 5 fields, got {len(parts)}: {expression}")

    # Basic validation - Celery will do the heavy lifting
    return True


def crontab_from_string(cron_expr: str) -> crontab:
    """
    Convert cron string to Celery crontab object.

    Args:
        cron_expr: Cron expression (e.g., "0 0 * * 0")

    Returns:
        Celery crontab schedule

    Raises:
        ValueError: If expression is invalid or contains invalid values
    """
    validate_cron(cron_expr)

    minute, hour, day_of_month, month_of_year, day_of_week = cron_expr.split()

    try:
        return crontab(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            day_of_week=day_of_week,
        )
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid cron expression '{cron_expr}': {e!s}") from e
