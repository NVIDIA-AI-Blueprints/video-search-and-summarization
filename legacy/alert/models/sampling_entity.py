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

from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime

@dataclass
class SamplingEntity:
    """
    Data class representing a sampling entity.
    Only sensorName and timeStamp are required fields.
    """
    sensorName: str
    timeStamp: datetime
    streamId: Optional[str] = None
    imageUrl: Optional[str] = None
    prompt: Optional[str] = None
    vlmResponse: Optional[Dict[str, Any]] = None
    sampledImage: Optional[bytes] = None  # Binary JPEG data
    sensorLocation: Optional[str] = None  # New field for sensor location

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SamplingEntity':
        """
        Create a SamplingEntity instance from a dictionary
        
        Args:
            data: Dictionary containing sampling data
            
        Returns:
            SamplingEntity instance
            
        Raises:
            ValueError: If required fields are missing
        """
        if 'sensorName' not in data or 'timeStamp' not in data:
            raise ValueError("Required fields 'sensorName' and 'timeStamp' must be present")
            
        timeStamp = datetime.strptime(data['timeStamp'], '%Y-%m-%dT%H:%M:%S.%fZ') if isinstance(data['timeStamp'], str) else data['timeStamp']
        
        return cls(
            sensorName=data['sensorName'],
            timeStamp=timeStamp,
            streamId=data.get('streamId'),
            imageUrl=data.get('imageUrl'),
            prompt=data.get('prompt'),
            vlmResponse=data.get('vlmResponse'),
            sampledImage=data.get('sampledImage'),
            sensorLocation=data.get('sensorLocation')  # Parse sensor location
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the SamplingEntity to a dictionary
        
        Returns:
            Dictionary representation of the entity
        """
        return {
            'sensorName': self.sensorName,
            'timeStamp': self.timeStamp.isoformat() + 'Z',
            'streamId': self.streamId,
            'imageUrl': self.imageUrl,
            'prompt': self.prompt,
            'vlmResponse': self.vlmResponse,
            'sensorLocation': self.sensorLocation  # Include sensor location
            # Note: sampledImage is binary data, typically not included in dict representation
            # unless specifically needed for serialization
        } 