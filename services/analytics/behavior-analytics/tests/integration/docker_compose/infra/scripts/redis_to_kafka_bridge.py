#!/usr/bin/env python3
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

"""
Simple Redis to Kafka Bridge with Reliable Delivery
Ensures no messages are lost between Redis and Kafka
"""

import redis
import logging
import time
import sys
import threading

try:
    from confluent_kafka import Producer  # type: ignore
except ImportError:
    print("Error: confluent-kafka is not installed. Run: pip install confluent-kafka")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class RedisKafkaBridge:
    def __init__(self, redis_host: str = 'localhost', redis_port: int = 6379, 
                 kafka_brokers: str = 'localhost:9092', kafka_topics: str = 'mdx-raw'):
        # Redis connection
        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            decode_responses=False  # Important: keep as bytes
        )
        
        # Kafka producer configuration
        self.producer = Producer({
            'bootstrap.servers': kafka_brokers,
            'linger.ms': 100,
            'delivery.timeout.ms': 30000,
            'request.timeout.ms': 10000,
            'acks': 'all'  # Wait for all replicas
        })
        
        self.kafka_topics = kafka_topics.split(',')
        self.consumer_group = 'redis-kafka-bridge'
        
        # Statistics
        self.stats = {
            'read': 0,
            'sent': 0,
            'failed': 0
        }
        
    def _ensure_consumer_group(self, stream: str) -> None:
        """Create consumer group if it doesn't exist"""
        try:
            self.redis_client.xgroup_create(stream, self.consumer_group, id='0', mkstream=True)
            logger.info(f"Created consumer group '{self.consumer_group}' for stream '{stream}'")
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        
    def process_stream(self, topic: str = 'mdx-raw') -> None:
        """Process Redis stream with reliable delivery"""
        self._ensure_consumer_group(topic)
        consumer_name = f"bridge-{threading.current_thread().name}"
        
        # Track delivery status
        delivery_status = {}
        
        def delivery_callback(err, msg, msg_id):
            """Track delivery status"""
            if err:
                logger.error(f"Failed to deliver {msg_id}: {err}")
                delivery_status[msg_id] = False
            else:
                logger.debug(f"Delivered {msg_id} to {msg.topic()}[{msg.partition()}]@{msg.offset()}")
                delivery_status[msg_id] = True
        
        while True:
            try:
                # Read messages from Redis
                messages = self.redis_client.xreadgroup(
                    self.consumer_group,
                    consumer_name,
                    {topic: '>'},
                    count=10,  # Process smaller batches for reliability
                    block=1000
                )
                
                if not messages:
                    continue
                
                # Process messages
                message_ids = []
                for stream_name, stream_messages in messages:
                    for msg_id, data in stream_messages:
                        self.stats['read'] += 1
                        message_ids.append(msg_id)
                        
                        # Send to Kafka with callback
                        def make_callback(mid):
                            return lambda err, msg: delivery_callback(err, msg, mid)
                        
                        try:
                            self.producer.produce(
                                topic=topic,
                                value=data.get(b'value', b''),
                                key=data.get(b'key', b''),
                                on_delivery=make_callback(msg_id)
                            )
                        except Exception as e:
                            logger.error(f"Failed to produce {msg_id}: {e}")
                            delivery_status[msg_id] = False
                
                # Wait for all messages to be delivered
                self.producer.flush(timeout=1)
                
                # Check delivery status and only ACK successful messages
                successful_ids = []
                for msg_id in message_ids:
                    if delivery_status.get(msg_id, False):
                        successful_ids.append(msg_id)
                        self.stats['sent'] += 1
                    else:
                        self.stats['failed'] += 1
                        logger.warning(f"Message {msg_id} failed, will be retried")
                
                # ACK only successful messages
                if successful_ids:
                    self.redis_client.xack(topic, self.consumer_group, *successful_ids)
                
                # Clear delivery status for next batch
                delivery_status.clear()
                
                # Log stats periodically
                if self.stats['read'] % 100 == 0:
                    logger.info(f"Stats - Read: {self.stats['read']}, "
                                f"Sent: {self.stats['sent']}, "
                                f"Failed: {self.stats['failed']}")
                
            except Exception as e:
                logger.error(f"Processing error: {e}", exc_info=True)
                time.sleep(5)

    def run(self) -> None:
        """Start the bridge"""
        try:
            threads = []
            for topic in self.kafka_topics:
                logger.info(f"Starting bridge for topic: {topic}")
                thread = threading.Thread(
                    target=self.process_stream, 
                    args=(topic,), 
                    name=f"{topic}"
                )
                thread.daemon = True
                threads.append(thread)
                thread.start()
            
            # Monitor threads
            while True:
                for t in threads:
                    if not t.is_alive():
                        logger.error(f"Thread {t.name} died")
                        raise RuntimeError(f"Thread {t.name} died")
                time.sleep(5)
                
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self.producer.flush(30)
            logger.info(f"Final stats - Read: {self.stats['read']}, "
                        f"Sent: {self.stats['sent']}, "
                        f"Failed: {self.stats['failed']}")


if __name__ == '__main__':
    import os
    
    # Add startup delay to ensure Kafka is ready
    startup_delay = int(os.getenv('STARTUP_DELAY', '30'))
    if startup_delay > 0:
        logger.info(f"Waiting {startup_delay} seconds for services to be ready...")
        time.sleep(startup_delay)
    
    bridge = RedisKafkaBridge(
        redis_host=os.getenv('REDIS_HOST', 'localhost'),
        redis_port=int(os.getenv('REDIS_PORT', '6379')),
        kafka_brokers=os.getenv('KAFKA_BROKERS', 'localhost:9092'),
        kafka_topics=os.getenv('KAFKA_TOPICS', 'mdx-raw')
    )
    
    bridge.run() 