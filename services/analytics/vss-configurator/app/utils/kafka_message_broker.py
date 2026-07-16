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
Kafka implementation of the MessageBroker interface.
"""
import json
from utils.message_broker import MessageBroker
from utils.kafka_producer import KfkProducer
from utils.logger import get_logger

logger = get_logger(__name__)


class KafkaMessageBroker(MessageBroker):
    """Kafka implementation of MessageBroker"""
    
    def __init__(self, bootstrap_servers, wl_id_field, wl_event_field, **kwargs):
        """
        Initialize Kafka message broker.
        
        Args:
            bootstrap_servers (str): Kafka bootstrap servers
            wl_id_field (str): Field name for workload ID (e.g., 'camera_id')
            wl_event_field (str): Field name for event (e.g., 'event')
            **kwargs: Additional KafkaProducer configuration parameters
        """
        logger.debug(f"Initializing Kafka message broker with bootstrap_servers={bootstrap_servers}")
        logger.debug(f"Fields: wl_id_field={wl_id_field}, wl_event_field={wl_event_field}")
        self.producer = KfkProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=str.encode,
            wl_id_field=wl_id_field,
            wl_event_field=wl_event_field,
            **kwargs
        )
        logger.info(f"Kafka message broker initialized with bootstrap_servers={bootstrap_servers}")
    
    def send_message(self, topic, key, sensor_mapping):
        """
        Send sensor configuration messages to Kafka topic.
        
        Args:
            topic (str): Kafka topic to send messages to
            key (str): Message key
            sensor_mapping: SensorMapping object containing sensor information
        """
        logger.debug(f"KafkaMessageBroker sending {len(sensor_mapping.sensors)} messages to topic {topic}")
        try:
            self.producer.send_message(topic=topic, key=key, sensor_mapping=sensor_mapping)
            logger.info(f"Successfully sent message to Kafka topic {topic}")
        except Exception as e:
            logger.error(f"Error sending message to Kafka topic {topic}: {e}")
            logger.debug(f"Exception details: {repr(e)}")
            raise
    
    def close(self):
        """Close Kafka producer connection"""
        logger.debug("Closing Kafka producer connection")
        if hasattr(self.producer, 'producer'):
            try:
                logger.debug("Flushing Kafka producer")
                self.producer.producer.flush()
                self.producer.producer.close()
                logger.info("Kafka producer connection closed")
            except Exception as e:
                logger.error(f"Error closing Kafka producer: {e}")
                logger.debug(f"Exception details: {repr(e)}")

