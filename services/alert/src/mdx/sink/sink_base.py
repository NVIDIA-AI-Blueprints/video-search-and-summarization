# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from abc import ABC, abstractmethod
from typing import Any, List


class SinkBase(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def write(self, messages: List[Any]) -> None:
        """
        Write StreamMessage objects with serialization
        Args:
            messages: List of StreamMessage objects to serialize and write
        """
        pass

    @abstractmethod
    def write_msg(self, messages: List[bytes]) -> None:
        """
        Write raw byte messages
        Args:
            messages: List of raw byte messages to write
        """
        pass
    
    @abstractmethod
    def write_incidents(self, messages: List[Any]) -> None:
        """
        Write incident messages to dedicated stream/topic
        Args:
            messages: List of StreamMessage objects for incidents
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """
        Clean up resources
        """
        pass
    
    # Legacy method for backward compatibility
    def write_data(self, data: List[Any]) -> None:
        """Legacy method - use write() instead"""
        self.write(data)
