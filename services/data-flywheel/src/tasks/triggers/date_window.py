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

"""Data window calculator for embedding jobs."""

from datetime import datetime, timedelta
from typing import Literal


class DataWindowCalculator:
    """Calculate date ranges for data windows (embedding jobs only)."""

    @staticmethod
    def calculate_date_range(
        window: int | Literal["all"],
    ) -> dict | None:
        """
        Calculate date range for given window.

        Args:
            window: Time window specification
                - int: Number of months to look back (e.g., 1, 3, 6, 12)
                - "all": Use all available data

        Returns:
            Dict with 'from' and 'to' dates, or None for 'all'

        Raises:
            ValueError: If window is an invalid value (e.g., negative or zero months)
        """
        if window == "all":
            return None  # Use all data

        if isinstance(window, int):
            if window <= 0:
                raise ValueError(f"data_window_months must be positive, got: {window}")

            # Calculate from_date based on months (30 days per month)
            now = datetime.utcnow()
            days = window * 30
            from_date = now - timedelta(days=days)

            # Return date-only strings (YYYY-MM-DD) as required by EmbeddingDataSplitConfig
            return {
                "from": from_date.date().isoformat(),
                "to": now.date().isoformat(),
            }

        raise ValueError(
            f"Invalid data window type: {type(window).__name__}. Expected int or 'all'."
        )
