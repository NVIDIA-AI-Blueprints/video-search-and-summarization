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
Simple MQTT to Kafka Bridge with Reliable Delivery
Ensures no messages are lost between MQTT and Kafka
"""

import logging
import time
import sys
import threading

from paho.mqtt.client import Client, ConnectFlags, MQTTMessage, CONNACK_ACCEPTED, MQTT_ERR_SUCCESS
from paho.mqtt.enums import CallbackAPIVersion, MQTTProtocolVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode
from typing import Any

try:
    from confluent_kafka import Producer  # type: ignore
except ImportError:
    print("Error: confluent-kafka is not installed. Run: pip install confluent-kafka")
    sys.exit(1)

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s.%(msecs)03d %(levelname)s [ThreadName:%(threadName)s] (LineNo:%(lineno)d) - %(message)s",
    datefmt="%y/%m/%d %H:%M:%S",
    level=logging.INFO
)


class SimpleMQTTKafkaBridge:

    _DEFAULT_CONF = {
        'mqtt_host': 'localhost',
        'mqtt_port': 1883,
        'kafka_brokers': 'localhost:9092',
        'kafka_topics': 'mdx-raw'
    }

    def __init__(self, *, mqtt_host: str, mqtt_port: int, kafka_brokers: str, kafka_topics: str):

        self.config = self._DEFAULT_CONF | {
            'base_client_id': 'mqtt-kafka-bridge',
            'mqtt_host': mqtt_host,
            'mqtt_port': mqtt_port,
            'kafka_brokers': kafka_brokers,
            'kafka_topics': kafka_topics
        }

        # Kafka producer configuration
        self.producer = Producer({
            'bootstrap.servers': self.config['kafka_brokers'],
            'linger.ms': 100,
            'delivery.timeout.ms': 30000,
            'request.timeout.ms': 10000,
            'enable.idempotence': True,
            'acks': 'all'  # Wait for all replicas
        })
        
        self.kafka_topics = [t.strip() for t in self.config['kafka_topics'].split(',') if t.strip()]
        # Statistics
        self.stats = {
            'read': 0,
            'sent': 0,
            'failed': 0
        }


    def process_stream(self, topic: str) -> None:
        """Process MQTT stream"""

        client_id = f"{self.config['base_client_id']}_{topic}"

        def _on_connect(client: Client, userdata: Any, conn_flags: ConnectFlags, rc: ReasonCode, props: Properties | None) -> None:
            if rc == CONNACK_ACCEPTED:
                logger.debug(f"Consumer client {client_id} connected to MQTT broker.")
            else:
                raise Exception(f"MQTT Connection Error in client {client_id} - [code:{rc.value}] {rc!s}")

        def _on_message(client: Client, userdata: Any, msg: MQTTMessage) -> None:

            if not msg.retain:
                self.stats['read'] += 1
                self._process_message(msg)


        _client = Client(
            callback_api_version = CallbackAPIVersion.VERSION2,
            client_id = client_id,
            protocol = MQTTProtocolVersion.MQTTv5
        )

        _client.on_connect = _on_connect
        _client.on_message = _on_message

        _client.connect(host = self.config['mqtt_host'], port = self.config['mqtt_port'], keepalive = 60, clean_start = False)

        rc, _ = _client.subscribe(topic, qos = 1)

        if rc == MQTT_ERR_SUCCESS:
            logger.debug(f"Subscribed client {client_id} to MQTT topic {topic}")
        else:
            raise Exception(f"Failed to subscribe client {client_id} to topic {topic} - [code:{rc.value}] {rc!s}")

        _client.loop_forever()


    def _process_message(self, msg: MQTTMessage):

        try:
            topic = msg.topic
            headers: dict[str, bytes] = {}

            if msg.properties and (user_props := msg.properties.json().get('UserProperty')):
                headers = { k: v.encode('utf-8') for k, v in user_props }

            try:
                self.producer.produce(
                    topic = topic,
                    value = msg.payload,
                    key = headers.get('key', b'')
                )
                self.stats['sent'] += 1
                self.producer.poll(0)

            except Exception as e:
                logger.error(f"Failed to produce {msg.payload}: {e}")
                self.stats['failed'] += 1

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

    bridge = SimpleMQTTKafkaBridge(
        mqtt_host=os.getenv('MQTT_HOST', 'localhost'),
        mqtt_port=int(os.getenv('MQTT_PORT', '1883')),
        kafka_brokers=os.getenv('KAFKA_BROKERS', 'localhost:9092'),
        kafka_topics=os.getenv('KAFKA_TOPICS', 'mdx-raw')
    )
    
    bridge.run()
