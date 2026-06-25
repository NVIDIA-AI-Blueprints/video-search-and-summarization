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
Script to display and validate responses from the alert agent by consuming from Redis Streams.

Validates responses against the new response schema and matches them with sent payloads.

Usage:
    python verify_responses.py          # Monitor Redis Streams for responses with schema validation
    python verify_responses.py --test   # Test with sample responses
    python verify_responses.py --info   # Show stream information
    python verify_responses.py --validate-last N  # Validate last N responses from output stream
"""

import json
import yaml
import os
import sys
import time
from datetime import datetime
import uuid
from typing import Dict, List, Optional, Any

try:
    import redis
except ImportError:
    print("❌ Redis package not found. Install it with: pip install redis")
    sys.exit(1)

# Track sent payloads for validation matching
sent_payloads = {}  # Dict[event_id, payload_data]
response_validations = {}  # Dict[event_id, validation_result]

def load_config():
    """Load configuration from config.yaml"""
    config_path = os.path.join(os.path.dirname(__file__), '../../../config.yaml')
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)

def validate_response_schema(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate response against the new response schema structure.
    
    Returns validation result with details.
    """
    validation_result = {
        'valid': True,
        'errors': [],
        'warnings': [],
        'validated_fields': []
    }
    
    try:
        # Required top-level fields for the new response schema (snake_case)
        required_fields = ['id', 'version', '@timestamp', 'sensor_id', 'video_path', 'alert', 'event', 'result']
        
        # Check required fields
        for field in required_fields:
            if field not in response_data:
                validation_result['errors'].append(f"Missing required field: {field}")
                validation_result['valid'] = False
            else:
                validation_result['validated_fields'].append(field)
        
        # Validate alert object structure
        if 'alert' in response_data:
            alert = response_data['alert']
            if isinstance(alert, dict):
                alert_required = ['severity', 'status', 'type', 'description']
                for field in alert_required:
                    if field not in alert:
                        validation_result['errors'].append(f"Missing alert.{field}")
                        validation_result['valid'] = False
                # Accept REVIEWED/REVIEW_FAILED per new schema; don't enforce value here
                if 'status' in alert:
                    validation_result['validated_fields'].append(f"alert.status={alert.get('status')}")
            else:
                validation_result['errors'].append("alert field must be an object")
                validation_result['valid'] = False
        
        # Validate event object structure
        if 'event' in response_data:
            event = response_data['event']
            if isinstance(event, dict):
                event_required = ['type', 'description']
                for field in event_required:
                    if field not in event:
                        validation_result['errors'].append(f"Missing event.{field}")
                        validation_result['valid'] = False
            else:
                validation_result['errors'].append("event field must be an object")
                validation_result['valid'] = False
        
        # Validate result object structure (new envelope)
        if 'result' in response_data:
            result_obj = response_data['result']
            if isinstance(result_obj, dict):
                result_required = ['status', 'review_method', 'reviewed_by', 'reviewed_at']
                for field in result_required:
                    if field not in result_obj:
                        validation_result['errors'].append(f"Missing result.{field}")
                        validation_result['valid'] = False

                # Validate status
                status = result_obj.get('status')
                if status in ['SUCCESS', 'FAILURE']:
                    validation_result['validated_fields'].append(f'result.status={status}')
                else:
                    validation_result['errors'].append(f"Invalid result.status: {status} (expected SUCCESS or FAILURE)")
                    validation_result['valid'] = False

                # Tri-state verification_result: bool or None
                if 'verification_result' in result_obj:
                    vr = result_obj.get('verification_result')
                    if vr is not None and not isinstance(vr, bool):
                        validation_result['errors'].append("result.verification_result must be boolean or null")
                        validation_result['valid'] = False

                # Optional fields
                optional_fields = ['confidence', 'description', 'reasoning', 'error_string', 'notes', 'debug', 'selected_frames_ts']
                for field in optional_fields:
                    if field in result_obj:
                        validation_result['validated_fields'].append(f'result.{field}')

                # Conditional presence: error_string only on FAILURE
                if status == 'SUCCESS' and 'error_string' in result_obj and result_obj['error_string']:
                    validation_result['warnings'].append("result.error_string present on SUCCESS; should be omitted or empty")
                if status == 'FAILURE' and 'error_string' not in result_obj:
                    validation_result['errors'].append("Missing result.error_string for FAILURE response")
                    validation_result['valid'] = False

                # Validate debug structure if present
                if isinstance(result_obj.get('debug'), dict):
                    if 'input_prompt' in result_obj['debug']:
                        validation_result['validated_fields'].append('result.debug.input_prompt')
                elif 'debug' in result_obj and result_obj['debug'] is not None:
                    validation_result['warnings'].append('result.debug should be an object when present')
            else:
                validation_result['errors'].append("result field must be an object")
                validation_result['valid'] = False
        
        # Check optional fields
        optional_fields = ['confidence', 'stream_name', 'cv_metadata_path', 'meta_labels']
        for field in optional_fields:
            if field in response_data:
                validation_result['validated_fields'].append(f'optional.{field}')
        
        # Validate metaLabels structure if present
        if 'meta_labels' in response_data:
            meta_labels = response_data['meta_labels']
            if isinstance(meta_labels, list):
                for i, label in enumerate(meta_labels):
                    if not isinstance(label, dict) or 'key' not in label or 'value' not in label:
                        validation_result['warnings'].append(f"metaLabels[{i}] should have 'key' and 'value' fields")
            else:
                validation_result['warnings'].append("metaLabels should be an array")
        
        # Version compatibility check
        if response_data.get('version', '').startswith('1.'):
            validation_result['validated_fields'].append('compatible_version')
        else:
            validation_result['warnings'].append(f"Version '{response_data.get('version')}' may not be compatible")
        
    except Exception as e:
        validation_result['valid'] = False
        validation_result['errors'].append(f"Validation exception: {str(e)}")
    
    return validation_result

def print_validation_result(validation: Dict[str, Any], event_id: str):
    """Print formatted validation results"""
    if validation['valid']:
        print(f"✅ Schema Validation: PASSED")
        print(f"   📊 Validated Fields ({len(validation['validated_fields'])}): {', '.join(validation['validated_fields'][:8])}")
        if len(validation['validated_fields']) > 8:
            print(f"       ...and {len(validation['validated_fields']) - 8} more")
    else:
        print(f"❌ Schema Validation: FAILED")
        print(f"   🚨 Errors ({len(validation['errors'])}):")
        for error in validation['errors']:
            print(f"       • {error}")
    
    if validation['warnings']:
        print(f"   ⚠️  Warnings ({len(validation['warnings'])}):")
        for warning in validation['warnings'][:3]:  # Show first 3 warnings
            print(f"       • {warning}")
        if len(validation['warnings']) > 3:
            print(f"       ...and {len(validation['warnings']) - 3} more warnings")
    
    # Store validation result
    response_validations[event_id] = validation

def print_message(message_data, stream_name, message_id):
    """Print the message in a formatted way with schema validation"""
    print(f"\n{'='*80}")
    print(f"STREAM: {stream_name}")
    print(f"MESSAGE ID: {message_id}")
    print(f"TIME: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")
    
    # Parse and validate response payload
    response_payload = None
    event_id = None
    
    # If the message contains a JSON payload, pretty print it
    # Check for 'data' field (used by StreamMessage.to_redis_fields())
    if isinstance(message_data, dict) and 'data' in message_data:
        try:
            response_payload = json.loads(message_data['data'])
            event_id = response_payload.get('id', response_payload.get('eventId', 'unknown'))
            
            print(f"📋 RESPONSE PAYLOAD (Event ID: {event_id}):")
            print(json.dumps(response_payload, indent=2))
            
            # Validate against response schema
            validation = validate_response_schema(response_payload)
            print(f"\n🔍 SCHEMA VALIDATION:")
            print_validation_result(validation, event_id)
            
            # Check if this response matches a sent payload
            if event_id in sent_payloads:
                print(f"\n🔗 PAYLOAD MATCH FOUND:")
                sent_payload = sent_payloads[event_id]
                print(f"   📤 Original Alert Type: {sent_payload.get('alert', {}).get('type', 'N/A')}")
                print(f"   📥 Response Alert Type: {response_payload.get('alert', {}).get('type', 'N/A')}")
                print(f"   📤 Original Sensor: {sent_payload.get('sensor_id', sent_payload.get('sensorId', 'N/A'))}")
                print(f"   📥 Response Sensor: {response_payload.get('sensor_id', 'N/A')}")
                
                # Compare result envelope
                result_obj = response_payload.get('result', {})
                if result_obj:
                    print(f"   🔍 Result Status: {result_obj.get('status', 'N/A')}")
                    print(f"   🔍 Verification Result: {result_obj.get('verification_result', 'N/A')}")
                    print(f"   🔍 Confidence: {result_obj.get('confidence', 'N/A')}")
            else:
                print(f"\n📭 NO MATCHING SENT PAYLOAD for Event ID: {event_id}")
            
            # Print other metadata
            print(f"\n📊 STREAM METADATA:")
            for key, value in message_data.items():
                if key != 'data':
                    print(f"   {key}: {value}")
                    
        except json.JSONDecodeError as e:
            print(f"❌ JSON Parse Error: {e}")
            print("Raw message data:")
            print(json.dumps(message_data, indent=2))
    else:
        print("Raw message data:")
        print(json.dumps(message_data, indent=2))

def load_sent_payloads():
    """
    Load sent payloads from send_payload.py test scenarios for matching.
    This helps match responses with their original requests.
    """
    global sent_payloads
    
    try:
        # Import the payload generation function
        sys.path.append(os.path.dirname(__file__))
        
        # Generate the same test payloads that send_payload.py uses
        from send_payload import get_test_payloads
        test_payloads = get_test_payloads()
        
        # Store payloads by event ID for matching
        for payload_num, payload in test_payloads.items():
            event_id = payload['id']
            sent_payloads[event_id] = payload
            
        print(f"📚 Loaded {len(sent_payloads)} known test payloads for matching")
        
    except Exception as e:
        print(f"⚠️  Could not load sent payloads: {e}")
        print("   Response validation will continue without payload matching")

def monitor_streams():
    """Monitor output streams for responses with schema validation"""
    config = load_config()
    
    # Load known sent payloads for matching
    load_sent_payloads()
    
    # Redis configuration
    redis_config = config['event_bridge']['redis_sink']
    
    # Connect to Redis
    try:
        r = redis.Redis(
            host=redis_config['host'],
            port=redis_config['port'],
            db=redis_config['db'],
            decode_responses=True
        )
        
        # Test connection
        r.ping()
        print(f"✅ Connected to Redis at {redis_config['host']}:{redis_config['port']}")
        
    except redis.ConnectionError as e:
        print(f"❌ Failed to connect to Redis: {e}")
        print("💡 Make sure Redis is running: docker compose -f test_docker-compose.yml up -d redis")
        sys.exit(1)
    
    # Get stream names to monitor (output streams)
    streams_to_monitor = [
        redis_config['streams']['enhanced_anomaly_stream'],
        redis_config['streams']['incidents_stream']
    ]
    
    print(f"📡 Monitoring Redis Output Streams: {streams_to_monitor}")
    print("   🔍 Schema Validation: ENABLED")
    print("   🔗 Payload Matching: ENABLED")
    print("Waiting for messages... (Press Ctrl+C to stop)\n")
    
    # Keep track of last message IDs for each stream
    last_ids = {stream: '0' for stream in streams_to_monitor}
    
    try:
        while True:
            messages_found = False
            
            for stream_name in streams_to_monitor:
                try:
                    # Read new messages from stream
                    messages = r.xread({stream_name: last_ids[stream_name]}, count=10, block=1000)
                    
                    for stream, msgs in messages:
                        for msg_id, fields in msgs:
                            print_message(fields, stream_name, msg_id)
                            last_ids[stream_name] = msg_id
                            messages_found = True
                            
                except redis.ResponseError as e:
                    if "NOGROUP" in str(e) or "no such key" in str(e):
                        # Stream doesn't exist yet
                        continue
                    else:
                        print(f"❌ Error reading from stream {stream_name}: {e}")
                        continue
                except Exception as e:
                    print(f"❌ Unexpected error reading from stream {stream_name}: {e}")
                    continue
            
            if not messages_found:
                time.sleep(0.1)  # Small delay to avoid busy waiting
            
    except KeyboardInterrupt:
        print(f"\n👋 Stopping monitor...")
        
        # Show validation summary
        if response_validations:
            print(f"\n📊 VALIDATION SUMMARY:")
            print(f"{'='*50}")
            total_responses = len(response_validations)
            valid_responses = sum(1 for v in response_validations.values() if v['valid'])
            
            print(f"Total Responses Validated: {total_responses}")
            print(f"Valid Responses: {valid_responses}")
            print(f"Invalid Responses: {total_responses - valid_responses}")
            print(f"Success Rate: {(valid_responses/total_responses)*100:.1f}%" if total_responses > 0 else "No responses")
    except Exception as e:
        print(f"❌ Error during monitoring: {e}")

def validate_last_responses(count: int):
    """Validate the last N responses from output streams"""
    config = load_config()
    
    # Load known sent payloads
    load_sent_payloads()
    
    # Redis configuration  
    redis_config = config['event_bridge']['redis_sink']
    
    try:
        r = redis.Redis(
            host=redis_config['host'],
            port=redis_config['port'],
            db=redis_config['db'],
            decode_responses=True
        )
        r.ping()
        print(f"✅ Connected to Redis at {redis_config['host']}:{redis_config['port']}")
        
    except redis.ConnectionError as e:
        print(f"❌ Failed to connect to Redis: {e}")
        sys.exit(1)
    
    # Get output stream
    stream_name = redis_config['streams']['incidents_stream']
    
    try:
        # Get last N messages
        messages = r.xrevrange(stream_name, count=count)
        
        if not messages:
            print(f"📭 No messages found in stream: {stream_name}")
            return
        
        print(f"🔍 Validating last {len(messages)} responses from {stream_name}")
        print(f"{'='*80}")
        
        for i, (msg_id, fields) in enumerate(messages):
            print(f"\n📋 Response {i+1}/{len(messages)} (ID: {msg_id})")
            print_message(fields, stream_name, msg_id)
            
    except Exception as e:
        print(f"❌ Error retrieving messages: {e}")

def show_stream_info():
    """Show information about Redis Streams"""
    config = load_config()
    
    # Redis configuration
    redis_config = config['event_bridge']['redis_source']
    redis_sink_config = config['event_bridge']['redis_sink']
    
    # Connect to Redis
    try:
        r = redis.Redis(
            host=redis_config['host'],
            port=redis_config['port'],
            db=redis_config['db'],
            decode_responses=True
        )
        r.ping()
        print(f"✅ Connected to Redis at {redis_config['host']}:{redis_config['port']}")
        
    except redis.ConnectionError as e:
        print(f"❌ Failed to connect to Redis: {e}")
        sys.exit(1)
    
    # Get all streams
    all_streams = [
        redis_config['streams']['anomaly_stream'],
        redis_config['streams']['heartbeat_stream'],
        redis_sink_config['streams']['incidents_stream'],
        redis_sink_config['streams']['incidents_stream']
    ]
    
    print(f"\n📊 Redis Streams Information:")
    print(f"{'='*80}")
    
    for stream_name in all_streams:
        try:
            # Check if stream exists
            if r.exists(stream_name):
                # Get stream info
                info = r.xinfo_stream(stream_name)
                
                print(f"\n🔸 Stream: {stream_name}")
                print(f"   Length: {info['length']} messages")
                print(f"   Consumer Groups: {info['groups']}")
                print(f"   First Entry: {info.get('first-entry', 'N/A')}")
                print(f"   Last Entry: {info.get('last-entry', 'N/A')}")
                
                # Get recent messages
                try:
                    recent_messages = r.xrevrange(stream_name, count=3)
                    if recent_messages:
                        print(f"   Recent Messages:")
                        for msg_id, fields in recent_messages:
                            print(f"     {msg_id}: {len(fields)} fields")
                except Exception as e:
                    print(f"   Error getting recent messages: {e}")
                
                # Show consumer groups
                try:
                    groups = r.xinfo_groups(stream_name)
                    if groups:
                        print(f"   Consumer Groups Details:")
                        for group in groups:
                            print(f"     {group['name']}: {group['consumers']} consumers, {group['pending']} pending")
                except Exception as e:
                    print(f"   Error getting consumer groups: {e}")
                    
            else:
                print(f"\n🔸 Stream: {stream_name} (doesn't exist)")
                
        except Exception as e:
            print(f"\n🔸 Stream: {stream_name} - Error: {e}")
    
    print(f"\n{'='*80}")

def generate_test_response(scenario='success'):
    """Generate test response messages"""
    base_response = {
        "id": str(uuid.uuid4()),
        "version": "1.0",
        "@timestamp": datetime.utcnow().isoformat() + "Z",
        "sensor_id": "test-sensor-v3",
        "stream_name": "Test Stream",
        "video_path": "/test/video.mp4",
        "cv_metadata_path": "/test/metadata.json",
        "confidence": 0.92,
        "alert": {
            "severity": "HIGH",
            "status": "REVIEWED",
            "type": "RESTRICTED_ACCESS",
            "description": "Vehicle detected traveling in wrong direction"
        },
        "event": {
            "type": "traffic_violation",
            "description": "Wrong-way driving detected"
        },
        "meta_labels": [
            {"key": "SHIFT", "value": "DAY"},
            {"key": "ZONE", "value": "A"}
        ]
    }
    
    if scenario == 'success':
        base_response["result"] = {
            "status": "SUCCESS",
            "error_string": "",
            "verification_result": True,
            "confidence": 0.92,
            "review_method": "VSS",
            "reviewed_by": "MVILA-15B v1.0",
            "reviewed_at": datetime.utcnow().isoformat() + "Z",
            "notes": "Alert auto-reviewed by VSS",
            "debug": {
                "input_prompt": "Is the vehicle going the wrong way?"
            },
            "description": "Yes, there is a vehicle traveling in the wrong direction",
            "reasoning": "Vehicle is clearly moving against traffic flow",
            "selected_frames_ts": [0, 1000, 2000, 3000, 4000]
        }
    elif scenario == 'verification_failure':
        base_response["alert"]["status"] = "REVIEW_FAILED"
        base_response["result"] = {
            "status": "FAILURE",
            "error_string": "VSS processing failed",
            "verification_result": False,
            "confidence": 0.0,
            "review_method": "VSS",
            "reviewed_by": "MVILA-15B v1.0",
            "reviewed_at": datetime.utcnow().isoformat() + "Z",
            "notes": "Alert verification failed",
            "debug": None,
            "description": "Verification could not be completed",
            "reasoning": None
        }
    elif scenario == 'invalid_schema':
        # Missing required fields for testing validation
        del base_response["result"]  # Missing result object
        base_response["alert"] = {"type": "test"}  # Missing required alert fields
    
    return base_response

def run_test_mode():
    """Test mode: print sample responses with validation"""
    print("🧪 Test mode - Sample responses with validation:\n")
    
    scenarios = ['success', 'verification_failure', 'invalid_schema']
    for scenario in scenarios:
        test_response = generate_test_response(scenario)
        print_message({'data': json.dumps(test_response)}, 'test-stream', f'test-{scenario}-123')

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == '--test':
            run_test_mode()
        elif sys.argv[1] == '--info':
            show_stream_info()
        elif sys.argv[1] == '--validate-last':
            if len(sys.argv) > 2:
                try:
                    count = int(sys.argv[2])
                    validate_last_responses(count)
                except ValueError:
                    print("❌ Invalid count. Usage: --validate-last N")
            else:
                print("❌ Missing count. Usage: --validate-last N")
        else:
            print("❌ Unknown option")
            print("Usage: python verify_responses.py [--test|--info|--validate-last N]")
            sys.exit(1)
    else:
        # Normal mode: monitor Redis Streams with schema validation
        monitor_streams() 