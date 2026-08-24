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
import os
from datetime import datetime, timezone
from typing import List, Any
import redis
from mdx.anomaly.source.source_base import SourceBase
from mdx.anomaly.stream_message import StreamMessage

class SourceRedisStream(SourceBase):
    """Redis Streams source implementation"""
    
    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Redis connection setup
        redis_config = config['event_bridge']['redis_source']
        self.redis_client = redis.Redis(
            host=redis_config['host'],
            port=redis_config['port'],
            db=redis_config.get('db', 0),
            decode_responses=True
        )
        
        # Stream configuration
        self.anomaly_stream = redis_config['streams']['anomaly_stream']
        self.heartbeat_stream = redis_config['streams']['heartbeat_stream']
        self.consumer_group = redis_config['consumer_group']
        self.consumer_name = f"consumer-{os.getpid()}"
        
        # Consumer settings
        consumer_config = redis_config.get('consumer_config', {})
        self.block_time = consumer_config.get('block_time', 1000)
        self.count = consumer_config.get('count', 10)
        self.batch_size = consumer_config.get('batch_size', 100)
        
        # Initialize consumer groups
        self._setup_consumer_groups()
    
    def _setup_consumer_groups(self):
        """Create consumer groups if they don't exist"""
        streams = [self.anomaly_stream, self.heartbeat_stream]
        for stream in streams:
            try:
                self.redis_client.xgroup_create(
                    stream, 
                    self.consumer_group, 
                    id='0-0',
                    mkstream=True
                )
                self.logger.info(f"Created consumer group {self.consumer_group} for stream {stream}")
            except redis.exceptions.ResponseError as e:
                if "BUSYGROUP" in str(e):
                    self.logger.debug(f"Consumer group {self.consumer_group} already exists for {stream}")
                else:
                    self.logger.error(f"Error creating consumer group: {e}")
                    raise
    
    def read(self) -> List[bytes]:
        """Read raw messages from Redis Stream"""
        try:
            messages = self._read_from_stream(self.anomaly_stream)
            return [msg.to_bytes() for msg in messages]
        except Exception as e:
            self.logger.error(f"Error reading raw messages: {e}")
            return []
    
    def poll(self) -> List[StreamMessage]:
        """Read and deserialize messages into StreamMessage format"""
        return self._read_from_stream(self.anomaly_stream)
    
    def poll_heartbeats(self) -> List[StreamMessage]:
        """Read heartbeat messages"""
        return self._read_from_stream(self.heartbeat_stream)
    
    def _read_from_stream(self, stream_name: str) -> List[StreamMessage]:
        """Read messages from a specific Redis Stream"""
        try:
            # Use XREADGROUP to read from consumer group
            messages = self.redis_client.xreadgroup(
                groupname=self.consumer_group,
                consumername=self.consumer_name,
                streams={stream_name: '>'},
                count=self.count,
                block=self.block_time
            )
            
            stream_messages = []
            for stream, msgs in messages:
                for msg_id, fields in msgs:
                    try:
                        # Create StreamMessage from Redis Stream message
                        stream_msg = StreamMessage.from_redis_stream(
                            stream_name, msg_id, fields, 'request_schema.yaml'
                        )
                        stream_messages.append(stream_msg)
                        
                        # Acknowledge message
                        self.redis_client.xack(stream_name, self.consumer_group, msg_id)
                        self.logger.debug(f"Processed message {msg_id} from {stream_name}")
                        
                    except Exception as e:
                        self.logger.error(f"Error processing message {msg_id}: {e}")
                        continue
            
            return stream_messages
            
        except redis.exceptions.ConnectionError:
            self.logger.error("Redis connection failed")
            return []
        except redis.exceptions.TimeoutError:
            self.logger.debug("Redis read timeout - no new messages")
            return []
        except Exception as e:
            self.logger.error(f"Error reading from Redis Stream: {e}")
            return []
    
    def read_data(self) -> List[dict]:
        """Read data and return normalized batch dictionaries.

        ``AnomalyEnhancer.process_anomalies`` expects every source to return
        batches shaped like SourceKafka.read_data(): a list of dictionaries
        with ``kind``, ``messages`` and timing keys. Redis Stream payloads are
        already JSON, so ``messages`` carries JSON strings instead of Kafka
        protobuf tuples; ``process_batch_vlm`` handles both forms.
        """
        try:
            # Get StreamMessage objects
            stream_messages = self._read_from_stream(self.anomaly_stream)
            if not stream_messages:
                return []
            
            # Convert to JSON strings like Kafka does
            json_strings = []
            for msg in stream_messages:
                json_strings.append(msg.to_json())
            
            return [{
                'kind': 'anomaly',
                'messages': json_strings,
                'kafka_consumed_at': datetime.now(timezone.utc).isoformat(),
                'kafka_published_at': None,
            }]
        except Exception as e:
            self.logger.error(f"Error reading data as JSON strings: {e}")
            return []
    
    def close(self) -> None:
        """Clean up Redis connection"""
        try:
            if self.redis_client:
                self.redis_client.close()
                self.logger.info("Redis connection closed")
        except Exception as e:
            self.logger.error(f"Error closing Redis connection: {e}")
    
    def _handle_pending_messages(self, stream_name: str) -> List[StreamMessage]:
        """Handle pending messages that were not acknowledged"""
        try:
            # Get pending messages for this consumer
            pending = self.redis_client.xpending_range(
                stream_name, 
                self.consumer_group, 
                min='-', 
                max='+', 
                count=100
            )
            
            stream_messages = []
            for msg_info in pending:
                msg_id = msg_info['message_id']
                
                # Claim the message if it's been idle for too long
                idle_time = msg_info['time_since_delivered']
                if idle_time > 60000:  # 60 seconds
                    claimed = self.redis_client.xclaim(
                        stream_name,
                        self.consumer_group,
                        self.consumer_name,
                        min_idle_time=60000,
                        message_ids=[msg_id]
                    )
                    
                    for claimed_msg_id, fields in claimed:
                        try:
                            stream_msg = StreamMessage.from_redis_stream(
                                stream_name, claimed_msg_id, fields, 'request_schema.yaml'
                            )
                            stream_messages.append(stream_msg)
                            
                            # Acknowledge the claimed message
                            self.redis_client.xack(stream_name, self.consumer_group, claimed_msg_id)
                            self.logger.info(f"Claimed and processed pending message {claimed_msg_id}")
                            
                        except Exception as e:
                            self.logger.error(f"Error processing claimed message {claimed_msg_id}: {e}")
                            continue
            
            return stream_messages
            
        except Exception as e:
            self.logger.error(f"Error handling pending messages: {e}")
            return []
