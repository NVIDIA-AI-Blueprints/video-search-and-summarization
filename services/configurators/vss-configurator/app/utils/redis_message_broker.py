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
Redis implementation of the MessageBroker interface.
Uses Redis Streams to publish sensor configuration messages.
"""
import json
import redis
import os
from datetime import datetime, timezone
from utils.message_broker import MessageBroker
from utils.logger import get_logger

logger = get_logger(__name__)

class RedisMessageBroker(MessageBroker):
    """Redis implementation of MessageBroker using Redis Streams"""
    
    def __init__(self, host, port, db, wl_id_field, wl_event_field, **kwargs):
        """
        Initialize Redis message broker.
        
        Args:
            host (str): Redis host
            port (int): Redis port
            db (int): Redis database number
            wl_id_field (str): Field name for workload ID (e.g., 'camera_id')
            wl_event_field (str): Field name for event (e.g., 'event')
            **kwargs: Additional Redis client configuration parameters
        """
        logger.debug(f"Initializing Redis message broker with host={host}, port={port}, db={db}")
        logger.debug(f"Fields: wl_id_field={wl_id_field}, wl_event_field={wl_event_field}")
        self.wl_id_field = wl_id_field
        self.wl_event_field = wl_event_field
        
        try:
            logger.debug("Creating Redis client connection")
            self.redis_client = redis.StrictRedis(
                host=host,
                port=port,
                db=db,
                decode_responses=False,
                socket_timeout=10,
                socket_connect_timeout=5,
                retry_on_timeout=True,
                **kwargs
            )
            # Test connection
            logger.debug("Testing Redis connection with PING")
            self.redis_client.ping()
            logger.info(f"Redis message broker initialized with host={host}, port={port}, db={db}")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            logger.debug(f"Exception details: {repr(e)}")
            raise
    
    def _generate_timestamp(self):
        """Generate UTC timestamp in ISO 8601 format"""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    def _create_redis_messages(self, sensor_mapping):
        """
        Create Redis messages from sensor mapping.
        
        Args:
            sensor_mapping: SensorMapping object containing sensor information
            
        Returns:
            list: List of dictionaries containing message data
        """
        logger.debug(f"Creating Redis messages for {len(sensor_mapping.sensors)} sensors")
        redis_msgs = []
        for sensor_id, sensor_data in sensor_mapping.sensors.items():
            logger.debug(f"Creating Redis message for sensor: {sensor_id}")
            timestamp = self._generate_timestamp()
            region = sensor_data.region if sensor_data.region else ""
            group = sensor_data.group_id if sensor_data.group_id else ""
            name = f"{region}|{group}" if region and group else sensor_id
            redis_msg = {
                "alert_type": "config",
                "created_at": timestamp,
                "txn_id": "",
                self.wl_event_field: {
                    self.wl_id_field: sensor_id,
                    "name": name,
                    "change": "config",
                    "metadata": {
                        "messagingbus": "redis",
                        "region": region,
                        "group": group,
                        "topic-prefix": os.environ.get("CONFIG_TOPIC_PREFIX", "mdx-bev"),
                        "create-topic": "true",
                        "topic-partition": int(os.environ.get("CONFIG_TOPIC_PARTITION", 10))
                    },
                    "headers": {
                        "source": "vst",
                        "created_at": timestamp
                    }
                }
            }
            
            # Add camera_url if available
            if hasattr(sensor_data, 'url') and sensor_data.url:
                redis_msg[self.wl_event_field]["camera_url"] = sensor_data.url
                logger.debug(f"Added camera_url to message: {sensor_data.url}")
            
            logger.info(f"Redis message created: {redis_msg}")
            redis_msgs.append(redis_msg)
        
        logger.debug(f"Created {len(redis_msgs)} Redis messages")
        return redis_msgs
    
    def send_message(self, topic, key, sensor_mapping):
        """
        Send sensor configuration messages to Redis Stream.
        
        Args:
            topic (str): Redis stream name to send messages to (Eg: sensor)
            key (str): Message key (used as field name in Redis stream) (Eg: sensor.id)
            sensor_mapping: SensorMapping object containing sensor information
        """
        logger.debug(f"Sending messages to Redis stream: {topic} with key: {key}")
        try:
            messages = self._create_redis_messages(sensor_mapping)
            
            for i, msg in enumerate(messages):
                logger.debug(f"Sending message {i+1}/{len(messages)} to Redis stream")
                # Convert message to JSON string and encode to bytes
                msg_json = json.dumps(msg)
                
                # Add message to Redis stream
                self.redis_client.xadd(topic, {key.encode('utf-8'): msg_json.encode('utf-8')})
                logger.info(f"Message sent to Redis stream {topic} with key {key}")
            
            logger.info(f"Successfully sent {len(messages)} messages to Redis stream {topic}")
        except redis.RedisError as e:
            logger.error(f"Redis error while sending message to stream {topic}: {e}")
            logger.debug(f"Exception details: {repr(e)}")
            raise
        except Exception as e:
            logger.error(f"Error sending message to Redis stream {topic}: {e}")
            logger.debug(f"Exception details: {repr(e)}")
            raise
    
    def close(self):
        """Close Redis client connection"""
        logger.debug("Closing Redis client connection")
        if self.redis_client:
            try:
                self.redis_client.close()
                logger.info("Redis client connection closed")
            except Exception as e:
                logger.error(f"Error closing Redis client: {e}")
                logger.debug(f"Exception details: {repr(e)}")

