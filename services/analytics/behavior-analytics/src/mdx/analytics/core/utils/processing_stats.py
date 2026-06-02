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

import time

from pydantic import BaseModel, Field, computed_field


class ProcessingStats(BaseModel):
    """Statistics tracker for processing performance metrics.
    
    Tracks the number of processed messages and calculates processing rate
    for a worker over time. Uses the current time as the start timestamp
    and provides real-time messages per second calculation.
    
    :ivar str | int worker_id: Unique identifier for the worker
    :ivar float start: Starting timestamp for tracking, defaults to current time
    :ivar int num_msgs: Initial number of processed messages, defaults to 0
    """

    worker_id: str | int = Field(frozen=True)
    start: float = Field(default_factory=time.time, frozen=True)
    num_msgs: int = 0


    def update(self, num_msgs: int) -> None:
        """Update the message count by adding new processed messages.
        
        :param int num_msgs: Number of new messages processed to add to the total
        """

        self.num_msgs += num_msgs


    @computed_field(return_type=float)
    @property
    def msgs_per_sec(self) -> float:
        """Calculate the current messages per second processing rate.
        
        :return float: Messages processed per second rounded to 2 decimal places
        """

        elapsed = time.time() - self.start
        return round(self.num_msgs / elapsed, 2)


class BatchStats(ProcessingStats):
    """Statistics tracker for batch processing performance metrics.
    
    Extends ProcessingStats to include batch-specific tracking with
    a unique batch identifier. Inherits all message counting and
    rate calculation functionality from the parent class.
    
    :ivar str | int worker_id: Unique identifier for the worker
    :ivar float start: Starting timestamp for tracking, defaults to current time
    :ivar int num_msgs: Initial number of processed messages, defaults to 0
    :ivar int batch_id: Unique identifier for the batch being processed
    """

    batch_id: int = Field(frozen=True)
