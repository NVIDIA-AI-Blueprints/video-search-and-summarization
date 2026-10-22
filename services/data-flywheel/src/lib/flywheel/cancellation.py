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

"""Cancellation utilities for flywheel tasks."""

from src.api.db_manager import get_db_manager
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.cancellation")


class FlywheelCancelledError(Exception):
    """Exception raised when a flywheel run is cancelled."""

    def __init__(self, flywheel_run_id: str, message: str = "Flywheel run was cancelled"):
        self.flywheel_run_id = flywheel_run_id
        self.message = message
        super().__init__(self.message)


def check_cancellation(flywheel_run_id: str) -> None:
    """
    Check if a flywheel run has been cancelled and raise an exception if so.

    Args:
        flywheel_run_id: ID of the flywheel run to check
    Raises:
        FlywheelCancelledError: If the flywheel run is cancelled
    """
    db_manager = get_db_manager()
    if db_manager.is_flywheel_run_cancelled(flywheel_run_id):
        message = f"Flywheel run {flywheel_run_id} was cancelled"
        logger.info(message)
        raise FlywheelCancelledError(flywheel_run_id, message)
