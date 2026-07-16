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

import os
from kafka import KafkaProducer
from datetime import datetime, timezone
from utils.logger import get_logger

logger = get_logger(__name__)
class KfkProducer:
    def __init__(self, **config):
        logger.debug(f"Initializing KafkaProducer with config keys: {list(config.keys())}")
        # Extract custom fields before passing to KafkaProducer
        self.wl_id_field = config.pop('wl_id_field', None)
        self.wl_event_field = config.pop('wl_event_field', None)
        logger.debug(f"Custom fields: wl_id_field={self.wl_id_field}, wl_event_field={self.wl_event_field}")
        # Now pass only valid Kafka configs
        logger.debug(f"Creating Kafka producer with bootstrap_servers={config.get('bootstrap_servers')}")
        self.producer = KafkaProducer(**config)
        logger.info("Kafka producer initialized successfully")
    
    def _generate_timestamp(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    def _create_kfk_msgs(self, sensor_mapping):
        logger.debug(f"Creating Kafka messages for {len(sensor_mapping.sensors)} sensors")
        kfk_msgs = []
        for sensor_id, sensor_data in sensor_mapping.sensors.items():
            logger.debug(f"Creating Kafka message for sensor: {sensor_id}")
            timeStamp = str(self._generate_timestamp())            
            region = sensor_data.region if sensor_data.region else ""
            group = sensor_data.group_id if sensor_data.group_id else ""
            name = f"{region}|{group}" if region and group else sensor_id
            kfk_msg = {
                    "alert_type": "config",
                    "created_at": timeStamp, 
                    "txn_id":  "",  
                    self.wl_event_field: {
                        self.wl_id_field: sensor_id,
                        "name": name,
                        # "tags": f"{region_name}|{group_name}",
                        "change": "config",
                        "metadata": {
                                        "messagingbus": "kafka", 
                                        "region": region,
                                        "group": group, 
                                        "topic-prefix": os.environ.get("CONFIG_TOPIC_PREFIX", "mdx-bev"),
                                        "create-topic": "true", 
                                        "topic-partition": int(os.environ.get("CONFIG_TOPIC_PARTITION", 10))
                        },
                        "headers ": {
                            "source ": "vst",
                            "created_at": timeStamp 
                        }
            }
            }
            
            if hasattr(sensor_data, 'url') and sensor_data.url:
                kfk_msg[self.wl_event_field]["camera_url"] = sensor_data.url
                logger.debug(f"Added camera_url to message: {sensor_data.url}")
            
            logger.info(f"Kafka message created: {kfk_msg}")
            kfk_msgs.append(kfk_msg)
        logger.debug(f"Created {len(kfk_msgs)} Kafka messages")
        return kfk_msgs
    
    def send_message(self, topic, key, sensor_mapping):
        logger.debug(f"Sending messages to Kafka topic: {topic} with key: {key}")
        messages = self._create_kfk_msgs(sensor_mapping)
        for i, kfk_msg in enumerate(messages):
            logger.debug(f"Sending message {i+1}/{len(messages)} to Kafka")
            self.producer.send(topic=topic, key=key, value=kfk_msg)
        logger.info(f"Sent {len(messages)} messages to Kafka topic {topic}")
