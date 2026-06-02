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
Flexible script to send Alert Bridge Event test payloads to Kafka.
Usage: python send_payload.py [1|2]
"""

import json
import sys
from confluent_kafka import Producer
import yaml
from datetime import datetime
import uuid

def load_config():
    """Load configuration from config.yaml"""
    with open('../../../config.yaml', 'r') as file:
        return yaml.safe_load(file)

def get_test_payloads():
    """Define test payloads following Alert Bridge Event schema"""
    
    # Get current timestamp
    timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    
    # Base path for test videos
    base_path = "/home/user/alert_agent/test/test_lite/kafka/test_videos"
    
    payloads = {
        1: {
            "eventId": str(uuid.uuid4()),
            "version": "1.0.0",
            "alertType": "wrong_way_detection",
            "sensorId": "cam_12",
            "streamId": "cam_12_stream_1",
            "timestamp": timestamp,
            "mediaFilePath": f"{base_path}/traffic/heavy_traffic.mov",
            "prompts": [
                { "question": "Is the vehicle going the wrong way?", "expectedAnswer": "yes" },
                { "question": "Is the vehicle turning left?", "expectedAnswer": "no" }
            ]
        },
        2: {
            "eventId": str(uuid.uuid4()),
            "version": "1.0.0",
            "alertType": "safety_violation",
            "sensorId": "cam_12",
            "streamId": "cam_12_stream_1",
            "timestamp": timestamp,
            "mediaFilePath": f"{base_path}/warehouse/on_ladder.mp4",
            "prompts": [
                { "question": "Is someone on a ladder?", "expectedAnswer": "yes" },
                { "question": "Is the person wearing safety equipment?", "expectedAnswer": "no" }
            ]
        }
    }
    
    return payloads

def send_test_message(payload_number=1):
    """Send test message to Kafka"""
    config = load_config()
    
    # Get test payloads
    payloads = get_test_payloads()
    
    if payload_number not in payloads:
        print(f"❌ Error: Payload {payload_number} not defined!")
        print(f"Available payloads: {list(payloads.keys())}")
        sys.exit(1)
    
    test_payload = payloads[payload_number]
    
    # Kafka producer configuration
    producer_config = {
        'bootstrap.servers': config['kafka']['bootstrap_servers'],
        'client.id': f'alert-bridge-test-producer-{payload_number}'
    }
    
    producer = Producer(producer_config)
    
    try:
        # Get topic from config
        topic = config['kafka']['anomalyTopic']
        
        # Convert to JSON string
        message = json.dumps(test_payload, indent=2)
        
        # Send message
        producer.produce(
            topic=topic,
            key=test_payload['sensorId'],
            value=message
        )
        
        producer.flush()
        
        print(f"✅ Alert Bridge Event payload {payload_number} sent to topic '{topic}':")
        print(f"   Event ID: {test_payload['eventId']}")
        print(f"   Alert Type: {test_payload['alertType']}")
        print(f"   Sensor: {test_payload['sensorId']}")
        print(f"   Timestamp: {test_payload['timestamp']}")
        print(f"   Media: {test_payload['mediaFilePath']}")
        print(f"   Prompts: {len(test_payload.get('prompts', []))} questions")
        
        print("\n📋 Payload Details:")
        for i, prompt in enumerate(test_payload.get('prompts', []), 1):
            print(f"   Q{i}: {prompt['question']}")
            print(f"   Expected: {prompt['expectedAnswer']}")
        
        print(f"\nℹ️  Expected output topics:")
        print(f"   Enhanced alerts: {config['kafka']['enhanced_anomaly_topic']}")
        print(f"   Incidents: {config['kafka']['incidents_topic']}")
        
        print("\n💡 This payload simulates:")
        if payload_number == 1:
            print("   A heavy traffic scenario to test wrong-way detection")
        elif payload_number == 2:
            print("   A warehouse safety violation scenario with someone on a ladder")
        
    except Exception as e:
        print(f"❌ Error sending message: {str(e)}")
        sys.exit(1)

def save_payload_files():
    """Save test payloads to JSON files for reference"""
    payloads = get_test_payloads()
    
    for num, payload in payloads.items():
        filename = f'alert_bridge_payload_{num}.json'
        with open(filename, 'w') as f:
            json.dump(payload, f, indent=2)
        print(f"📄 Saved {filename}")

if __name__ == "__main__":
    # Default to payload 1
    payload_num = 1
    
    # Check command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == '--save':
            save_payload_files()
            sys.exit(0)
        
        try:
            payload_num = int(sys.argv[1])
            if payload_num not in [1, 2]:
                raise ValueError()
        except ValueError:
            print("❌ Error: Please specify payload number 1 or 2")
            print("Usage: python send_payload.py [1|2]")
            print("       python send_payload.py --save  (to save payload files)")
            sys.exit(1)
    
    send_test_message(payload_num) 