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
from typing import Any, Dict, Optional
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

@dataclass
class StreamMessage:
    """Unified message format with configurable field extraction for all event bridges"""
    id: str
    timestamp: datetime
    data: Dict[str, Any]
    metadata: Dict[str, Any]
    raw_data: Optional[bytes] = None
    
    # Extracted core fields for easy access
    core_fields: Optional[Dict[str, Any]] = None
    
    @classmethod
    def from_json_with_schema(cls, json_str: str, schema_file: str = 'request_schema.yaml', message_id: Optional[str] = None) -> 'StreamMessage':
        """Create StreamMessage using schema file for field extraction"""
        try:
            from utils.field_extractor import extract_core_fields, validate_required_fields
            
            data = json.loads(json_str)
            
            # Validate required fields
            if not validate_required_fields(data, schema_file):
                logger.warning(f"Message validation failed for schema {schema_file}")
            
            # Extract core fields using schema file
            core_fields = extract_core_fields(data, schema_file)
            
            return cls(
                id=message_id or str(core_fields.get('message_id') or ''),
                timestamp=cls._parse_timestamp(core_fields.get('timestamp')),
                data=data,
                metadata={'source': 'json', 'schema_file': schema_file},
                raw_data=json_str.encode('utf-8'),
                core_fields=core_fields
            )
        except Exception as e:
            logger.error(f"Error creating StreamMessage from JSON: {e}")
            raise
    
    @classmethod
    def from_json_with_config(cls, json_str: str, config: Dict[str, Any], message_id: Optional[str] = None) -> 'StreamMessage':
        """Legacy method - use from_json_with_schema instead"""
        logger.warning("from_json_with_config is deprecated, use from_json_with_schema instead")
        return cls.from_json_with_schema(json_str, 'request_schema.yaml', message_id)
    
    @classmethod
    def from_json(cls, json_str: str, message_id: Optional[str] = None) -> 'StreamMessage':
        """Legacy method for backward compatibility"""
        try:
            data = json.loads(json_str)
            return cls(
                id=message_id or data.get('eventId', ''),
                timestamp=cls._parse_timestamp(data.get('timestamp')),
                data=data,
                metadata={'source': 'json'},
                raw_data=json_str.encode('utf-8')
            )
        except Exception as e:
            logger.error(f"Error creating StreamMessage from JSON (legacy): {e}")
            raise
    
    @classmethod
    def from_kafka_message(cls, kafka_message, schema_file: str = 'request_schema.yaml') -> 'StreamMessage':
        """Create StreamMessage from Kafka message"""
        try:
            from utils.field_extractor import extract_core_fields
            
            # Decode Kafka message
            json_str = kafka_message.value().decode('utf-8')
            data = json.loads(json_str)
            
            # Extract core fields using schema file
            core_fields = extract_core_fields(data, schema_file)
            
            return cls(
                id=kafka_message.key().decode('utf-8') if kafka_message.key() else str(core_fields.get('message_id', '')),
                timestamp=cls._parse_timestamp(core_fields.get('timestamp')),
                data=data,
                metadata={
                    'source': 'kafka',
                    'partition': kafka_message.partition(),
                    'offset': kafka_message.offset(),
                    'schema_file': schema_file
                },
                raw_data=kafka_message.value(),
                core_fields=core_fields
            )
        except Exception as e:
            logger.error(f"Error creating StreamMessage from Kafka: {e}")
            raise
    
    @classmethod
    def from_redis_stream(cls, stream_name: str, message_id: str, fields: Dict[str, str], schema_file: str = 'request_schema.yaml') -> 'StreamMessage':
        """Create StreamMessage from Redis Stream message"""
        try:
            from utils.field_extractor import extract_core_fields
            
            # Parse JSON data from Redis stream fields (consistent with to_redis_fields format)
            json_str = fields.get('data', '{}')
            json_data = json.loads(json_str)
            
            # Extract core fields using schema file
            core_fields = extract_core_fields(json_data, schema_file)
            
            return cls(
                id=message_id,
                timestamp=cls._parse_timestamp(core_fields.get('timestamp')),
                data=json_data,
                metadata={
                    'source': 'redis_stream',
                    'stream': stream_name,
                    'redis_id': message_id,
                    'schema_file': schema_file
                },
                raw_data=json_str.encode('utf-8'),
                core_fields=core_fields
            )
        except Exception as e:
            logger.error(f"Error creating StreamMessage from Redis Stream: {e}")
            raise
    
    @staticmethod
    def _parse_timestamp(timestamp_str: Optional[str]) -> datetime:
        """Parse timestamp with fallback to current time"""
        if not timestamp_str:
            return datetime.now()
        
        try:
            # Handle ISO format with Z suffix
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str.replace('Z', '+00:00')
            return datetime.fromisoformat(timestamp_str)
        except Exception as e:
            logger.debug(f"Error parsing timestamp '{timestamp_str}': {e}")
            return datetime.now()
    
    def get_field(self, field_name: str, default: Any = None) -> Any:
        """Get core field value with fallback"""
        if self.core_fields:
            return self.core_fields.get(field_name, default)
        return default
    
    def to_json(self) -> str:
        """Convert data to JSON string"""
        try:
            return json.dumps(self.data)
        except Exception as e:
            logger.error(f"Error converting to JSON: {e}")
            return "{}"
    
    def to_bytes(self) -> bytes:
        """Convert to bytes"""
        if self.raw_data:
            return self.raw_data
        return self.to_json().encode('utf-8')
    
    def to_redis_fields(self) -> Dict[str, str]:
        """Convert to Redis Stream fields format"""
        return {
            'data': self.to_json(),
            'timestamp': self.timestamp.isoformat(),
            'metadata': json.dumps(self.metadata) if self.metadata else '{}'
        }
    
    def extract_core_fields_if_needed(self, schema_file: str = 'request_schema.yaml') -> None:
        """Extract core fields if not already extracted"""
        if not self.core_fields:
            try:
                from utils.field_extractor import extract_core_fields
                self.core_fields = extract_core_fields(self.data, schema_file)
            except Exception as e:
                logger.error(f"Error extracting core fields: {e}")
                self.core_fields = {} 