#!/usr/bin/env python3
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
Redis Stream Consumer for WebSocket Broadcasting

Consumes alerts from Alert Bridge Redis streams and provides them for WebSocket broadcasting.
Uses unique consumer groups per FastAPI instance to ensure all instances receive all alerts.
"""

import asyncio
import logging
import json
import redis.asyncio as redis
import uuid
import socket
import os
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime


class RedisStreamConsumer:
    """
    Redis stream consumer for WebSocket alert broadcasting.
    
    Reads from Alert Bridge input and enhanced streams using unique consumer groups
    per FastAPI instance to ensure proper fan-out broadcasting.
    """
    
    def __init__(self, redis_config: Dict[str, Any]):
        """
        Initialize Redis stream consumer.
        
        Args:
            redis_config: Complete Redis configuration dictionary from config.yaml
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Redis connection configuration
        self.redis_host = redis_config.get('host', 'localhost')
        self.redis_port = redis_config.get('port', 6379)
        self.redis_db = redis_config.get('db', 0)
        
        # Stream configuration from config
        streams_config = redis_config.get('streams', {})
        sink_streams_config = redis_config.get('sink_streams', {})
        
        self.input_stream = streams_config.get('anomaly_stream', 'alert-bridge-input-stream')
        self.enhanced_stream = sink_streams_config.get('enhanced_anomaly_stream', 'alert-bridge-enhanced-stream')
        
        # Consumer configuration
        consumer_config = redis_config.get('consumer_config', {})
        self.block_time = consumer_config.get('block_time', 1000)  # milliseconds
        self.count = consumer_config.get('count', 1)
        self.reconnect_delay = consumer_config.get('reconnect_delay', 5)  # seconds
        self.consumer_group_prefix = redis_config.get('consumer_group_prefix', 'websocket_instance')
        
        # Create unique consumer group for this FastAPI instance
        self.instance_id = self._generate_instance_id()
        self.consumer_group = f"{self.consumer_group_prefix}_{self.instance_id}"
        self.consumer_name = f"consumer_{self.instance_id}"
        
        # Redis client
        self.redis_client: Optional[redis.Redis] = None
        
        # Stream offsets
        self.stream_offsets = {
            self.input_stream: ">",      # Start from new messages
            self.enhanced_stream: ">"    # Start from new messages
        }
        
        # Consumer state
        self.is_running = False
        self._stop_event = asyncio.Event()
        
        self.logger.info("Redis stream consumer initialized", extra={
            "instance_id": self.instance_id,
            "consumer_group": self.consumer_group,
            "input_stream": self.input_stream,
            "enhanced_stream": self.enhanced_stream,
            "redis_host": f"{self.redis_host}:{self.redis_port}"
        })
    
    def _generate_instance_id(self) -> str:
        """
        Generate unique instance ID for this FastAPI instance.
        
        Returns:
            Unique instance identifier
        """
        hostname = socket.gethostname()
        pid = os.getpid()
        timestamp = int(datetime.utcnow().timestamp())
        unique_suffix = str(uuid.uuid4())[:8]
        
        return f"{hostname}_{pid}_{timestamp}_{unique_suffix}"
    
    async def connect(self) -> None:
        """
        Connect to Redis and set up consumer groups.
        
        Raises:
            ConnectionError: If Redis connection fails
        """
        try:
            self.redis_client = redis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                db=self.redis_db,
                decode_responses=True
            )
            
            # Test connection
            await self.redis_client.ping()
            self.logger.info("Connected to Redis successfully")
            
            # Create consumer groups for this instance
            await self._create_consumer_groups()
            
        except Exception as e:
            self.logger.error(f"Failed to connect to Redis: {e}")
            raise ConnectionError(f"Redis connection failed: {e}")
    
    async def _create_consumer_groups(self) -> None:
        """
        Create consumer groups for the streams we want to monitor.
        
        Note: Each FastAPI instance creates its own consumer group to ensure
        all instances receive all messages (fan-out pattern).
        """
        streams = [self.input_stream, self.enhanced_stream]
        
        for stream_name in streams:
            try:
                # Create consumer group (starts from latest messages)
                await self.redis_client.xgroup_create(
                    stream_name, 
                    self.consumer_group, 
                    id="$",  # Start from latest messages
                    mkstream=True  # Create stream if it doesn't exist
                )
                self.logger.info(f"Created consumer group for stream", extra={
                    "stream": stream_name,
                    "consumer_group": self.consumer_group
                })
            except redis.exceptions.ResponseError as e:
                if "BUSYGROUP" in str(e):
                    # Consumer group already exists, which is fine
                    self.logger.debug(f"Consumer group already exists for stream", extra={
                        "stream": stream_name,
                        "consumer_group": self.consumer_group
                    })
                else:
                    self.logger.warning(f"Failed to create consumer group for stream", extra={
                        "stream": stream_name,
                        "consumer_group": self.consumer_group,
                        "error": str(e)
                    })
    
    async def start_consuming(self, message_callback) -> None:
        """
        Start consuming messages from Redis streams.
        
        Args:
            message_callback: Async function to call with each message
        """
        if self.is_running:
            self.logger.warning("Consumer is already running")
            return
            
        if not self.redis_client:
            await self.connect()
            
        self.is_running = True
        self._stop_event.clear()
        
        self.logger.info("Starting Redis stream consumption", extra={
            "consumer_group": self.consumer_group,
            "consumer_name": self.consumer_name
        })
        
        try:
            while self.is_running and not self._stop_event.is_set():
                await self._consume_messages(message_callback)
                
        except Exception as e:
            self.logger.error(f"Error in consumption loop: {e}")
            raise
        finally:
            self.is_running = False
            self.logger.info("Stopped Redis stream consumption")
    
    async def _consume_messages(self, message_callback) -> None:
        """
        Consume messages from streams and call callback.
        
        Args:
            message_callback: Function to call with each message
        """
        try:
            # Read from multiple streams using consumer group
            streams = {
                self.input_stream: ">",     # Read new messages only
                self.enhanced_stream: ">"   # Read new messages only
            }
            
            messages = await self.redis_client.xreadgroup(
                self.consumer_group,
                self.consumer_name,
                streams,
                count=self.count,           # From configuration
                block=self.block_time,      # From configuration (milliseconds)
                noack=False                 # Require acknowledgment
            )
            
            # Process received messages
            for stream_name, stream_messages in messages:
                for message_id, fields in stream_messages:
                    await self._process_message(
                        stream_name, message_id, fields, message_callback
                    )
                    
        except redis.exceptions.ConnectionError as e:
            self.logger.error(f"Redis connection error during consumption: {e}")
            # Try to reconnect after configured delay
            await asyncio.sleep(self.reconnect_delay)
            await self.connect()
        except Exception as e:
            self.logger.error(f"Error consuming messages: {e}")
            await asyncio.sleep(1)  # Brief pause before retry
    
    async def _process_message(
        self, 
        stream_name: str, 
        message_id: str, 
        fields: Dict[str, str],
        message_callback
    ) -> None:
        """
        Process a single message from Redis stream.
        
        Args:
            stream_name: Name of the stream
            message_id: Redis message ID
            fields: Message fields from Redis
            message_callback: Callback function for the message
        """
        try:
            # Determine alert type based on stream
            alert_type = "original" if "input" in stream_name else "enhanced"
            
            # Create message for WebSocket broadcasting
            websocket_message = {
                "type": "alert",
                "alert_type": alert_type,
                "stream_name": stream_name,
                "message_id": message_id,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "data": fields  # Raw passthrough of Redis stream fields
            }
            
            # Call the callback with the message
            await message_callback(websocket_message)
            
            # Acknowledge the message
            await self.redis_client.xack(stream_name, self.consumer_group, message_id)
            
            self.logger.debug("Processed message from stream", extra={
                "stream": stream_name,
                "message_id": message_id,
                "alert_type": alert_type
            })
            
        except Exception as e:
            self.logger.error(f"Error processing message from stream {stream_name}: {e}")
            # Don't acknowledge failed messages - they'll be retried
    
    async def stop(self) -> None:
        """Stop the consumer."""
        self.logger.info("Stopping Redis stream consumer")
        self.is_running = False
        self._stop_event.set()
        
        if self.redis_client:
            await self.redis_client.close()
            self.redis_client = None
    
    def get_consumer_info(self) -> Dict[str, Any]:
        """
        Get consumer information.
        
        Returns:
            Dictionary with consumer information
        """
        return {
            "instance_id": self.instance_id,
            "consumer_group": self.consumer_group,
            "consumer_name": self.consumer_name,
            "is_running": self.is_running,
            "streams": [self.input_stream, self.enhanced_stream]
        } 