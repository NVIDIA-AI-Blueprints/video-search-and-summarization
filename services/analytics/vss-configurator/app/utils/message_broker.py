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

"""
Abstract base class for message broker implementations.
This allows the VSS Configurator to support different message brokers (Kafka, Redis, etc.)
"""
from abc import ABC, abstractmethod
from utils.logger import get_logger

logger = get_logger(__name__)

class MessageBroker(ABC):
    """Abstract base class for message broker implementations"""
    
    @abstractmethod
    def send_message(self, topic, key, sensor_mapping):
        """
        Send sensor configuration messages to the message broker.
        
        Args:
            topic (str): The topic/stream to send messages to
            key (str): The message key (e.g., 'sensor')
            sensor_mapping: SensorMapping object containing sensor information
        """
        pass
    
    @abstractmethod
    def close(self):
        """Close the connection to the message broker"""
        pass

