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
Script to display responses from the alert agent by consuming from output Kafka topics.

Usage:
    python verify_responses.py          # Monitor Kafka topics for responses
    python verify_responses.py --test   # Test with sample responses
"""

import argparse
import json
import os
from datetime import datetime
import time
import uuid

import yaml
from confluent_kafka import Consumer, KafkaError
from google.protobuf.json_format import MessageToDict

from mdx.anomaly.protobuf import Incident as nvSchemaIncident

def load_config(config_path: str | None = None) -> dict:
    """Load configuration from config.yaml."""
    default_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', '..', '..',
        'config.yaml',
    )
    resolved_path = config_path or os.environ.get('ALERT_AGENT_CONFIG', default_path)

    with open(os.path.abspath(resolved_path), 'r') as file:
        return yaml.safe_load(file)

def create_consumer(config, topic, group_id):
    """Create Kafka consumer for a specific topic"""
    consumer_config = {
        'bootstrap.servers': config['kafka']['bootstrap_servers'],
        'group.id': group_id,
        'auto.offset.reset': 'earliest',  # Read from beginning
        'enable.auto.commit': True
    }
    
    consumer = Consumer(consumer_config)
    consumer.subscribe([topic])
    return consumer

def print_message(message_data, topic, encoding="json"):
    """Print the message"""
    print(f"\n{'='*80}")
    print(f"TOPIC: {topic}")
    print(f"TIME: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"ENCODING: {encoding}")
    print(f"{'='*80}")
    print(json.dumps(message_data, indent=2))

def monitor_topics(config_path: str | None = None, topics: list[str] | None = None):
    """Monitor output topics for responses"""
    config = load_config(config_path)

    if topics:
        topics_to_monitor = [(topic, f"consumer-{topic}") for topic in topics]
    else:
        topics_to_monitor = [
            (config['kafka']['enhanced_anomaly_topic'], 'enhanced-consumer'),
            (config['kafka']['incidents_topic'], 'incidents-consumer')
        ]
    
    consumers = []
    for topic, group_id in topics_to_monitor:
        consumer = create_consumer(config, topic, group_id)
        consumers.append((consumer, topic))
    
    print(f"Monitoring topics: {[topic for _, topic in topics_to_monitor]}")
    print("Waiting for messages... (Press Ctrl+C to stop)\n")
    
    try:
        while True:
            for consumer, topic in consumers:
                msg = consumer.poll(timeout=1.0)
                
                if msg is None:
                    continue
                    
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    else:
                        print(f"Consumer error: {msg.error()}")
                        continue
                
                value_bytes = msg.value()

                decoded = False
                if value_bytes is None:
                    continue

                try:
                    message_value = value_bytes.decode('utf-8')
                    message_data = json.loads(message_value)
                    print_message(message_data, topic, encoding="json")
                    decoded = True
                except (UnicodeDecodeError, json.JSONDecodeError):
                    decoded = False

                if decoded:
                    continue

                try:
                    proto_msg = nvSchemaIncident()
                    proto_msg.ParseFromString(value_bytes)
                    message_data = MessageToDict(proto_msg, preserving_proto_field_name=True)
                    print_message(message_data, topic, encoding="protobuf")
                    continue
                except Exception as parse_err:
                    print(f"Error decoding message from {topic}: {parse_err}")
            
            time.sleep(0.1)  # Small delay to avoid busy waiting
            
    except KeyboardInterrupt:
        print(f"\nStopping...")
    finally:
        for consumer, _ in consumers:
            consumer.close()

def generate_test_response(scenario='success'):
    """Generate test response messages"""
    base_response = {
        "eventId": str(uuid.uuid4()),
        "version": "1.0.0",
        "alertType": "wrong_way_detection",
        "sensorId": "cam_12",
        "streamId": "cam_12_stream_1",
        "timestamp": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
        "mediaFilePath": "/media/cam_12/test_video.mp4"
    }
    
    if scenario == 'success':
        base_response.update({
            "vlmEvaluation": [
                {
                    "question": "Is the vehicle going the wrong way?",
                    "response": "Yes, the vehicle is moving against traffic flow.",
                    "confidence": "93%",
                    "reasoning": "Vehicle crosses median line and enters opposite lane."
                }
            ],
            "status": {
                "code": "OK",
                "message": "Evaluation completed successfully."
            }
        })
    elif scenario == 'media_not_found':
        base_response.update({
            "mediaFilePath": "/media/cam_12/missing_file.mp4",
            "vlmEvaluation": [],
            "status": {
                "code": "MEDIA_NOT_FOUND",
                "message": "The specified media file could not be located or accessed.",
                "retriable": False,
                "origin": "Alert Bridge",
                "details": {
                    "path": "/media/cam_12/missing_file.mp4",
                    "httpStatus": 404
                }
            }
        })
    elif scenario == 'vlm_timeout':
        base_response.update({
            "vlmEvaluation": [],
            "status": {
                "code": "VLM_TIMEOUT",
                "message": "VSS did not respond within the allowed time window.",
                "retriable": True,
                "origin": "Alert Bridge",
                "details": {
                    "timeoutMs": 10000,
                    "retryWindow": "30s"
                }
            }
        })
    
    return base_response

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor Kafka topics for alert agent responses")
    parser.add_argument("--config", dest="config_path", help="Path to config.yaml")
    parser.add_argument(
        "--topic",
        dest="topics",
        action="append",
        help="Topic to monitor (can be specified multiple times)",
    )
    parser.add_argument("--test", action="store_true", help="Print sample responses and exit")

    args = parser.parse_args()

    if args.test:
        print("Test mode - Sample responses:\n")
        for scenario in ['success', 'media_not_found', 'vlm_timeout']:
            test_response = generate_test_response(scenario)
            print_message(test_response, 'test-topic')
    else:
        monitor_topics(config_path=args.config_path, topics=args.topics)
