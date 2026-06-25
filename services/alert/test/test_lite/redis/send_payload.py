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
Script to send Alert Bridge Event test payloads to Redis Streams.
Tests mandatory/optional fields validation and config defaults.

Usage: python send_payload.py [1|2|3|4|5|--heartbeat]
"""

import json
import sys
import yaml
import os
from datetime import datetime
import uuid

try:
    import redis
except ImportError:
    print("❌ Redis package not found. Install it with: pip install redis")
    sys.exit(1)

def load_config():
    """Load configuration from config.yaml"""
    config_path = os.path.join(os.path.dirname(__file__), '../../../config.yaml')
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)

def get_test_payloads():
    """Define test payloads following Alert Bridge Event schema"""
    
    # Get current timestamp in ISO 8601 format (no microseconds, ends with 'Z')
    timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    # Use actual test asset path
    test_asset_path = "/home/user/vss/media/vlm_as_judge_benchmark/traffic/driving_conditions_clear.mp4"
    payloads = {
        1: {
            "id": str(uuid.uuid4()),
            "@timestamp": timestamp,
            "sensorId": "sensor-cam-12",
            "videoPath": test_asset_path,
            "alert": {
                "severity": "HIGH",
                "status": "VERIFICATION_PENDING",
                "type": "TRAFFIC_VIOLATION",
                "description": "Vehicle detected moving against traffic flow"
            },
            "event": {
                "type": "WRONG_WAY_DETECTION",
                "description": "Vehicle traveling in opposite direction on highway"
            },
            "vssParams": {
                "vlmParams": {
                    "prompt": "Is there a vehicle going the wrong way on this road?"
                }
            }
        },
        2: {
            "id": str(uuid.uuid4()),
            "@timestamp": timestamp,
            "sensorId": "sensor-cam-15",
            "videoPath": test_asset_path,
            "alert": {
                "severity": "MEDIUM",
                "status": "VERIFICATION_PENDING",
                "type": "SAFETY_VIOLATION",
                "description": "Worker on ladder without proper safety equipment"
            },
            "event": {
                "type": "SAFETY_INCIDENT",
                "description": "Unsafe work conditions detected"
            },
            "vssParams": {
                "vlmParams": {
                    "prompt": "Is the person on the ladder wearing proper safety equipment?",
                    "maxTokens": 200,
                    "temperature": 0.3
                }
            },
            "confidence": 0.875,
            "metaLabels": [
                {"key": "type", "value": "validation"},
                {"key": "category", "value": "test"}
            ]
        },
        3: {
            "id": str(uuid.uuid4()),
            "@timestamp": timestamp,
            "sensorId": "sensor-cam-20",
            "videoPath": test_asset_path,
            "alert": {
                "severity": "LOW",
                "status": "VERIFICATION_PENDING",
                "type": "WEATHER_CONDITIONS",
                "description": "Heavy fog reducing visibility on roadway"
            },
            "event": {
                "type": "ENVIRONMENTAL_HAZARD",
                "description": "Poor visibility conditions detected"
            },
            "vssParams": {
                "vlmParams": {
                    "prompt": "Are the driving conditions foggy or misty?"
                },
                "chunkDuration": 30,
                "cvMetadataOverlay": True
            },
            "confidence": 0.92,
            "cvMetadataPath": "/path/to/cv_metadata.json",
            "metaLabels": [
                {"key": "type", "value": "validation"},
                {"key": "category", "value": "test"}
            ]
        },
        4: {
            "id": str(uuid.uuid4()),
            "@timestamp": timestamp,
            "sensorId": "sensor-cam-25",
            "videoPath": test_asset_path,
            "alert": {
                "severity": "HIGH",
                "status": "VERIFICATION_PENDING",
                "type": "INVALID_ALERT_TYPE_XYZ",
                "description": "Test case for invalid alert type validation"
            },
            "event": {
                "type": "VALIDATION_TEST",
                "description": "Testing validation with invalid alert type and missing prompt"
            },
            "vssParams": {
                "vlmParams": {
                    "temperature": 0.5,
                    "maxTokens": 100
                }
            },
            "confidence": 0.50
        },
        5: {
            "id": str(uuid.uuid4()),
            "@timestamp": timestamp,
            "sensorId": "sensor-cam-30",
            "videoPath": test_asset_path,
            "alert": {
                "severity": "MEDIUM",
                "status": "VERIFICATION_PENDING",
                "type": "TRAFFIC",
                "description": "Test case for auto-prompt generation with valid alert type"
            },
            "event": {
                "type": "TRAFFIC_VIOLATION",
                "description": "Testing auto-prompt generation from alert type"
            },
            "vssParams": {
                "vlmParams": {
                    "temperature": 0.4,
                    "maxTokens": 512,
                    "topP": 1.0
                }
            },
            "confidence": 0.75,
            "metaLabels": [
                {"key": "type", "value": "validation"},
                {"key": "category", "value": "test"}
            ]
        }
    }
    return payloads

def send_test_message(payload_number=1):
    """Send test message to Redis Stream"""
    config = load_config()
    
    # Get Redis connection
    redis_config = config['event_bridge']['redis_source']
    r = redis.Redis(
        host=redis_config['host'],
        port=redis_config['port'],
        db=redis_config['db'],
        decode_responses=True
    )
    
    # Get test payloads
    payloads = get_test_payloads()
    
    if payload_number not in payloads:
        print(f"❌ Invalid payload number. Available: {list(payloads.keys())}")
        return False
    
    payload = payloads[payload_number]
    
    print(f"🚀 Sending test payload {payload_number}...")
    print(f"📋 Event ID: {payload['id']}")
    print(f"📋 Alert Type: {payload['alert']['type']}")
    print(f"📋 Sensor ID: {payload['sensorId']}")
    
    # Show mandatory vs optional fields
    mandatory_fields = ["id", "version", "@timestamp", "sensorId", "videoPath", "alert", "event", "vlmParams"]
    optional_fields = ["confidence", "cvMetadataPath", "vssParams", "metaLabels"]
    
    provided_mandatory = [f for f in mandatory_fields if f in payload]
    provided_optional = [f for f in optional_fields if f in payload]
    missing_optional = [f for f in optional_fields if f not in payload]
    
    print(f"✅ Mandatory fields provided: {provided_mandatory}")
    print(f"📦 Optional fields provided: {provided_optional}")
    print(f"🎯 Optional fields missing (will get defaults): {missing_optional}")
    
    try:
        # Get stream name - use anomaly stream for input
        stream_name = config['event_bridge']['redis_source']['streams']['anomaly_stream']
        
        # Convert payload to Redis Stream format (consistent with existing format)
        # Use 'data' field like StreamMessage.to_redis_fields() expects
        stream_data = {
            'data': json.dumps(payload),
            'timestamp': payload['@timestamp'],
            'metadata': json.dumps({'source': 'test_client', 'sensor_id': payload['sensorId']})
        }
        
        # Send message to Redis Stream
        message_id = r.xadd(stream_name, stream_data)  # type: ignore
        
        print(f"✅ Message sent successfully!")
        print(f"📨 Stream: {stream_name}")
        print(f"🆔 Message ID: {message_id}")
        print(f"📄 Payload preview:")
        print(json.dumps(payload, indent=2)[:500] + "...")
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to send message: {e}")
        return False

def send_heartbeat():
    """Send heartbeat message"""
    config = load_config()
    redis_config = config['event_bridge']['redis']
    r = redis.Redis(
        host=redis_config['host'],
        port=redis_config['port'],
        db=redis_config['db'],
        decode_responses=True
    )
    
    heartbeat = {
        "type": "heartbeat",
        "timestamp": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
        "component": "redis_test_client"
    }
    
    try:
        stream_name = config['event_bridge']['redis_source']['streams']['anomaly_stream']
        message_id = r.xadd(stream_name, heartbeat)  # type: ignore
        print(f"💓 Heartbeat sent to {stream_name} with ID: {message_id}")
        return True
    except Exception as e:
        print(f"❌ Failed to send heartbeat: {e}")
        return False

def main():
    """Main function"""
    if len(sys.argv) < 2:
        print("Usage: python send_payload.py [1|2|3|4|5|--heartbeat]")
        print("\n📋 Available test payloads:")
        print("  1: Wrong way detection (minimal optional fields)")
        print("  2: Safety violation (some optional fields)")  
        print("  3: Weather conditions (all optional fields)")
        print("  4: ❌ VALIDATION TEST: Invalid alert.type")
        print("  5: 🔄 AUTO-PROMPT TEST: Valid alert.type + auto-generated prompt") 
        print("  --heartbeat: Send heartbeat message")
        return
    
    arg = sys.argv[1]
    
    if arg == "--heartbeat":
        send_heartbeat()
    else:
        try:
            payload_number = int(arg)
            send_test_message(payload_number)
        except ValueError:
            print(f"❌ Invalid argument: {arg}")
            print("Use: 1, 2, 3, 4, 5, or --heartbeat")

if __name__ == "__main__":
    main() 