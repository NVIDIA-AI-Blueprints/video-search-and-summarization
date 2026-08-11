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
Factory for creating message broker instances based on configuration.
"""
from utils.kafka_message_broker import KafkaMessageBroker
from utils.redis_message_broker import RedisMessageBroker
from utils.logger import get_logger

logger = get_logger(__name__)

class MessageBrokerFactory:
    """Factory class for creating message broker instances"""
    
    @staticmethod
    def create_message_broker(broker_type, config):
        """
        Create a message broker instance based on the specified type.
        
        Args:
            broker_type (str): Type of message broker ('kafka' or 'redis')
            config (dict): Configuration dictionary containing broker-specific settings
            
        Returns:
            MessageBroker: Instance of the appropriate message broker
            
        Raises:
            ValueError: If broker_type is not supported
        """
        logger.debug(f"MessageBrokerFactory creating broker of type: {broker_type}")
        broker_type = broker_type.lower()
        
        if broker_type == 'kafka':
            logger.info("Creating Kafka message broker")
            logger.debug(f"Kafka config: bootstrap_servers={config['WDM_KFK_BOOTSTRAP_URL']}, "
                        f"wl_id_field={config['WDM_WL_ID_FIELD']}, wl_event_field={config['WDM_WL_EVENT_FIELD']}")
            return KafkaMessageBroker(
                bootstrap_servers=config['WDM_KFK_BOOTSTRAP_URL'],
                wl_id_field=config['WDM_WL_ID_FIELD'],
                wl_event_field=config['WDM_WL_EVENT_FIELD']
            )
        elif broker_type == 'redis':
            logger.info("Creating Redis message broker")
            logger.debug(f"Redis config: host={config['WDM_REDIS_HOST']}, port={config['WDM_REDIS_PORT']}, "
                        f"db={config['REDIS_DB']}, wl_id_field={config['WDM_WL_ID_FIELD']}, "
                        f"wl_event_field={config['WDM_WL_EVENT_FIELD']}")
            return RedisMessageBroker(
                host=config['WDM_REDIS_HOST'],
                port=config['WDM_REDIS_PORT'],
                db=config['REDIS_DB'],
                wl_id_field=config['WDM_WL_ID_FIELD'],
                wl_event_field=config['WDM_WL_EVENT_FIELD']
            )
        else:
            logger.error(f"Unsupported message broker type: '{broker_type}'")
            raise ValueError(f"Unsupported message broker type: '{broker_type}'. Supported types: 'kafka', 'redis'")

