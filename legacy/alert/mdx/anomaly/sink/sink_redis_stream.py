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

import logging
from typing import List, Any
import redis
import json
from mdx.anomaly.sink.sink_base import SinkBase
from mdx.anomaly.stream_message import StreamMessage

class SinkRedisStream(SinkBase):
    """Redis Streams sink implementation"""
    
    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Redis connection setup
        redis_config = config['event_bridge']['redis_sink']
        self.redis_client = redis.Redis(
            host=redis_config['host'],
            port=redis_config['port'],
            db=redis_config.get('db', 0),
            decode_responses=True
        )
        
        # Stream configuration
        self.enhanced_anomaly_stream = redis_config['streams']['enhanced_anomaly_stream']
        self.incidents_stream = redis_config['streams']['incidents_stream']
        
        # Test connection
        try:
            self.redis_client.ping()
            self.logger.info("Redis connection established successfully")
        except Exception as e:
            self.logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    def write(self, messages: List[StreamMessage]) -> None:
        """Write StreamMessage objects to Redis Stream"""
        if not messages:
            return
        
        for message in messages:
            try:
                # Convert StreamMessage to Redis Stream format
                fields = message.to_redis_fields()
                
                # Add message to enhanced anomaly stream
                message_id = self.redis_client.xadd(
                    self.enhanced_anomaly_stream,
                    fields
                )
                
                self.logger.debug(f"Written message {message.id} to {self.enhanced_anomaly_stream} with ID {message_id}")
                
            except Exception as e:
                self.logger.error(f"Failed to write message {message.id} to Redis Stream: {e}")
                continue
    
    def write_msg(self, messages: List[bytes]) -> None:
        """Write raw byte messages to Redis Stream"""
        if not messages:
            return
        
        for msg_bytes in messages:
            try:
                # Convert bytes to StreamMessage first
                json_str = msg_bytes.decode('utf-8')
                stream_msg = StreamMessage.from_json_with_schema(json_str, 'request_schema.yaml')
                
                # Write using the standard method
                self.write([stream_msg])
                
            except Exception as e:
                self.logger.error(f"Failed to write raw message to Redis Stream: {e}")
                continue
    
    def write_incidents(self, messages: List[StreamMessage]) -> None:
        """Write incident messages to dedicated stream"""
        if not messages:
            return
        
        for message in messages:
            try:
                # Convert StreamMessage to Redis Stream format
                fields = message.to_redis_fields()
                
                # Add message to incidents stream
                message_id = self.redis_client.xadd(
                    self.incidents_stream,
                    fields
                )
                
                self.logger.debug(f"Written incident {message.id} to {self.incidents_stream} with ID {message_id}")
                
            except Exception as e:
                self.logger.error(f"Failed to write incident {message.id} to Redis Stream: {e}")
                continue
    
    def close(self) -> None:
        """Clean up Redis connection"""
        try:
            if self.redis_client:
                self.redis_client.close()
                self.logger.info("Redis connection closed")
        except Exception as e:
            self.logger.error(f"Error closing Redis connection: {e}")
    
    def _batch_write(self, messages: List[StreamMessage], stream_name: str) -> None:
        """Write messages in batches for better performance"""
        if not messages:
            return
        
        batch_size = 100  # Configurable batch size
        for i in range(0, len(messages), batch_size):
            batch = messages[i:i + batch_size]
            
            try:
                # Use pipeline for batch operations
                pipe = self.redis_client.pipeline()
                
                for message in batch:
                    fields = message.to_redis_fields()
                    pipe.xadd(stream_name, fields)
                
                # Execute all operations in the batch
                results = pipe.execute()
                
                # Log successful batch
                self.logger.debug(f"Batch wrote {len(batch)} messages to {stream_name}")
                
            except Exception as e:
                self.logger.error(f"Failed to write batch to {stream_name}: {e}")
                
                # Fallback to individual writes
                for message in batch:
                    try:
                        fields = message.to_redis_fields()
                        self.redis_client.xadd(stream_name, fields)
                    except Exception as individual_error:
                        self.logger.error(f"Failed to write individual message {message.id}: {individual_error}")
                        continue
    
    def write_batch(self, messages: List[StreamMessage]) -> None:
        """Write messages in batches for better performance"""
        self._batch_write(messages, self.enhanced_anomaly_stream)
    
    def write_incidents_batch(self, messages: List[StreamMessage]) -> None:
        """Write incident messages in batches"""
        self._batch_write(messages, self.incidents_stream)
    
    def get_stream_info(self, stream_name: str) -> dict:
        """Get information about a Redis Stream"""
        try:
            info = self.redis_client.xinfo_stream(stream_name)
            return {
                'length': info.get('length', 0),
                'first_entry': info.get('first-entry'),
                'last_entry': info.get('last-entry'),
                'groups': info.get('groups', 0)
            }
        except Exception as e:
            self.logger.error(f"Failed to get info for stream {stream_name}: {e}")
            return {}
    
    def trim_stream(self, stream_name: str, max_length: int) -> None:
        """Trim stream to maximum length"""
        try:
            trimmed = self.redis_client.xtrim(stream_name, maxlen=max_length)
            self.logger.info(f"Trimmed {trimmed} messages from {stream_name}")
        except Exception as e:
            self.logger.error(f"Failed to trim stream {stream_name}: {e}") 