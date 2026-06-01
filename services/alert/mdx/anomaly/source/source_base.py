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
from typing import Any, List, Optional


class SourceBase(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def read(self) -> List[bytes]:
        """
        Read raw messages from the event bridge
        Returns: List of raw byte messages
        """
        pass

    @abstractmethod
    def poll(self) -> List[Any]:
        """
        Read and deserialize messages into StreamMessage format
        Returns: List of StreamMessage objects
        """
        pass
    
    @abstractmethod
    def poll_heartbeats(self) -> List[Any]:
        """
        Read heartbeat messages
        Returns: List of StreamMessage objects
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """
        Clean up resources
        """
        pass
    
    # Legacy methods for backward compatibility
    def read_data(self) -> List[Any]:
        """Legacy method - use poll() instead"""
        return self.poll()
    
    def read_heartbeats(self) -> List[Any]:
        """Legacy method - use poll_heartbeats() instead"""
        return self.poll_heartbeats()
