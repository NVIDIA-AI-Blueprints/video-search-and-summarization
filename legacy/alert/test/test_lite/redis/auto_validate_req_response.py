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
Automated Test Script for Alert Bridge Response Validation

Sends all 5 test payloads sequentially and validates responses against the response schema.
Provides comprehensive end-to-end testing with detailed reporting.

Usage:
    python auto_validate_req_response.py [--timeout 60] [--verbose]
"""

import json
import yaml
import os
import sys
import time
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import threading
import queue

try:
    import redis
except ImportError:
    print("❌ Redis package not found. Install it with: pip install redis")
    sys.exit(1)

# Import payload generation and validation functions
from send_payload import get_test_payloads
from verify_responses import validate_response_schema, load_sent_payloads

class AutomatedTester:
    """
    Automated tester for Alert Bridge end-to-end validation.
    
    Sends payloads, monitors responses, validates schema, and reports results.
    """
    
    def __init__(self, timeout_seconds: int = 60, verbose: bool = False):
        """Initialize the automated tester."""
        self.timeout_seconds = timeout_seconds
        self.verbose = verbose
        self.config = self._load_config()
        
        # Redis connections
        self.redis_source = None
        self.redis_sink = None
        
        # Test tracking
        self.sent_payloads = {}  # Dict[event_id, payload_info]
        self.received_responses = {}  # Dict[event_id, response_data]
        self.validation_results = {}  # Dict[event_id, validation_result]
        
        # Response monitoring
        self.response_queue = queue.Queue()
        self.monitor_thread = None
        self.stop_monitoring = threading.Event()
        
        print(f"🤖 Automated Tester initialized")
        print(f"   ⏱️  Timeout: {timeout_seconds} seconds")
        print(f"   📢 Verbose: {verbose}")
    
    def _load_config(self):
        """Load configuration from config.yaml"""
        config_path = os.path.join(os.path.dirname(__file__), '../../../config.yaml')
        with open(config_path, 'r') as file:
            return yaml.safe_load(file)
    
    def _connect_redis(self):
        """Connect to Redis for both source and sink."""
        try:
            # Source Redis (for sending)
            source_config = self.config['event_bridge']['redis_source']
            self.redis_source = redis.Redis(
                host=source_config['host'],
                port=source_config['port'],
                db=source_config['db'],
                decode_responses=True
            )
            self.redis_source.ping()
            
            # Sink Redis (for receiving)
            sink_config = self.config['event_bridge']['redis_sink']
            self.redis_sink = redis.Redis(
                host=sink_config['host'],
                port=sink_config['port'],
                db=sink_config['db'],
                decode_responses=True
            )
            self.redis_sink.ping()
            
            print(f"✅ Connected to Redis")
            return True
            
        except redis.ConnectionError as e:
            print(f"❌ Failed to connect to Redis: {e}")
            print("💡 Make sure Redis is running: docker compose -f ../../../test_docker-compose.yml up -d redis")
            return False
    
    def _start_response_monitoring(self):
        """Start monitoring output streams for responses."""
        def monitor_responses():
            """Monitor responses in background thread."""
            sink_config = self.config['event_bridge']['redis_sink']
            # Monitor both output streams since responses could go to either
            streams_to_monitor = [
                sink_config['streams']['enhanced_anomaly_stream'],
                sink_config['streams']['incidents_stream']
            ]
            
            # Debug: Print actual stream names being monitored
            print(f"🔍 DEBUG: Monitoring streams: {streams_to_monitor}")
            last_ids = {stream: '$' for stream in streams_to_monitor}  # Start from new messages only
            
            if self.verbose:
                print(f"📡 Started monitoring streams: {streams_to_monitor}")
            
            while not self.stop_monitoring.is_set():
                try:
                    # Read new messages from all streams
                    messages = self.redis_sink.xread(last_ids, count=10, block=1000)
                    
                    # Debug: Show if any messages were found
                    if messages:
                        print(f"🔍 DEBUG: Found {len(messages)} stream(s) with messages")
                    
                    for stream, msgs in messages:
                        for msg_id, fields in msgs:
                            print(f"🔍 DEBUG: Message {msg_id} from {stream}: {list(fields.keys())}")
                            # Check for both 'payload' and 'data' fields (different message formats)
                            response_field = None
                            if 'payload' in fields:
                                response_field = 'payload'
                            elif 'data' in fields:
                                response_field = 'data'
                            
                            if response_field:
                                try:
                                    response_data = json.loads(fields[response_field])
                                    print(f"🔍 DEBUG: Parsed response from '{response_field}' field")
                                    event_id = response_data.get('id', response_data.get('eventId'))
                                    
                                    if event_id:
                                        self.response_queue.put({
                                            'event_id': event_id,
                                            'response_data': response_data,
                                            'message_id': msg_id,
                                            'timestamp': datetime.now()
                                        })
                                        
                                        if self.verbose:
                                            print(f"📥 Received response for: {event_id}")
                                    
                                except json.JSONDecodeError:
                                    continue
                            
                            last_ids[stream] = msg_id
                
                except Exception as e:
                    if not self.stop_monitoring.is_set():
                        print(f"⚠️  Monitor error: {e}")
                    time.sleep(1)
        
        self.monitor_thread = threading.Thread(target=monitor_responses, daemon=True)
        self.monitor_thread.start()
    
    def _stop_response_monitoring(self):
        """Stop monitoring responses."""
        if self.monitor_thread:
            self.stop_monitoring.set()
            self.monitor_thread.join(timeout=2)
            if self.verbose:
                print("🛑 Stopped response monitoring")
    
    def send_payload(self, payload_number: int, payload_data: Dict[str, Any]) -> bool:
        """Send a single payload to Redis stream."""
        try:
            source_config = self.config['event_bridge']['redis_source']
            stream_name = source_config['streams']['anomaly_stream']
            
            # Prepare stream data (same format as send_payload.py)
            stream_data = {
                'data': json.dumps(payload_data),
                'timestamp': payload_data['@timestamp'],
                'metadata': json.dumps({'source': 'automated_test', 'sensor_id': payload_data['sensorId']})
            }
            
            # Send to stream
            message_id = self.redis_source.xadd(stream_name, stream_data)
            
            # Track sent payload
            event_id = payload_data['id']
            self.sent_payloads[event_id] = {
                'payload_number': payload_number,
                'payload_data': payload_data,
                'message_id': message_id,
                'sent_time': datetime.now(),
                'alert_type': payload_data['alert']['type'],
                'sensor_id': payload_data['sensorId']
            }
            
            print(f"📤 Sent Payload {payload_number}: {event_id}")
            if self.verbose:
                print(f"   Alert Type: {payload_data['alert']['type']}")
                print(f"   Sensor: {payload_data['sensorId']}")
                print(f"   Message ID: {message_id}")
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to send payload {payload_number}: {e}")
            return False
    
    def wait_for_responses(self) -> int:
        """Wait for responses and process them. Returns number of responses received."""
        print(f"⏳ Waiting for responses (timeout: {self.timeout_seconds}s)...")
        
        start_time = datetime.now()
        timeout_time = start_time + timedelta(seconds=self.timeout_seconds)
        received_count = 0
        
        while datetime.now() < timeout_time and received_count < len(self.sent_payloads):
            try:
                # Check for new responses
                response_info = self.response_queue.get(timeout=1)
                event_id = response_info['event_id']
                response_data = response_info['response_data']
                
                if event_id in self.sent_payloads:
                    # Store response
                    self.received_responses[event_id] = response_data
                    
                    # Validate response schema
                    validation_result = validate_response_schema(response_data)
                    self.validation_results[event_id] = validation_result
                    
                    received_count += 1
                    
                    # Show immediate feedback
                    payload_info = self.sent_payloads[event_id]
                    status = "✅ VALID" if validation_result['valid'] else "❌ INVALID"
                    print(f"📥 Response {received_count}/{len(self.sent_payloads)}: Payload {payload_info['payload_number']} - {status}")
                    
                    if self.verbose and not validation_result['valid']:
                        print(f"   🚨 Validation Errors: {validation_result['errors']}")
                
                else:
                    if self.verbose:
                        print(f"📭 Received unknown response: {event_id}")
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"⚠️  Error processing response: {e}")
        
        return received_count
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive test report."""
        total_sent = len(self.sent_payloads)
        total_received = len(self.received_responses)
        total_valid = sum(1 for v in self.validation_results.values() if v['valid'])
        
        report = {
            'summary': {
                'total_payloads_sent': total_sent,
                'total_responses_received': total_received,
                'total_valid_responses': total_valid,
                'success_rate': (total_valid / total_sent * 100) if total_sent > 0 else 0,
                'response_rate': (total_received / total_sent * 100) if total_sent > 0 else 0
            },
            'test_cases': []
        }
        
        # Generate detailed test case results
        for event_id, payload_info in self.sent_payloads.items():
            test_case = {
                'payload_number': payload_info['payload_number'],
                'event_id': event_id,
                'alert_type': payload_info['alert_type'],
                'sensor_id': payload_info['sensor_id'],
                'sent_time': payload_info['sent_time'].isoformat(),
                'response_received': event_id in self.received_responses,
                'schema_valid': False,
                'validation_errors': [],
                'verification_status': None,
                'verification_result': None,
                'verification_confidence': None
            }
            
            # Add response details if received
            if event_id in self.received_responses:
                response_data = self.received_responses[event_id]
                
                # Schema validation
                if event_id in self.validation_results:
                    validation = self.validation_results[event_id]
                    test_case['schema_valid'] = validation['valid']
                    test_case['validation_errors'] = validation['errors']
                
                # Extract verification details
                verification = response_data.get('verification', {})
                test_case['verification_status'] = verification.get('status')
                test_case['verification_result'] = verification.get('result')
                test_case['verification_confidence'] = verification.get('confidence')
                
                # Response timing
                response_received_time = datetime.now()  # Approximate
                test_case['response_time_seconds'] = (response_received_time - payload_info['sent_time']).total_seconds()
            
            report['test_cases'].append(test_case)
        
        return report
    
    def print_report(self, report: Dict[str, Any]):
        """Print formatted test report."""
        summary = report['summary']
        
        print(f"\n{'='*80}")
        print(f"📊 AUTOMATED TEST REPORT")
        print(f"{'='*80}")
        
        # Summary
        print(f"\n📈 SUMMARY:")
        print(f"   Payloads Sent: {summary['total_payloads_sent']}")
        print(f"   Responses Received: {summary['total_responses_received']}")
        print(f"   Valid Responses: {summary['total_valid_responses']}")
        print(f"   Response Rate: {summary['response_rate']:.1f}%")
        print(f"   Success Rate: {summary['success_rate']:.1f}%")
        
        # Test cases
        print(f"\n📋 TEST CASE DETAILS:")
        for test_case in report['test_cases']:
            payload_num = test_case['payload_number']
            alert_type = test_case['alert_type']
            
            # Status indicators
            response_status = "✅" if test_case['response_received'] else "❌"
            schema_status = "✅" if test_case['schema_valid'] else "❌"
            
            print(f"\n🔸 Payload {payload_num}: {alert_type}")
            print(f"   📤 Event ID: {test_case['event_id']}")
            print(f"   📥 Response Received: {response_status}")
            print(f"   🔍 Schema Valid: {schema_status}")
            
            if test_case['response_received']:
                print(f"   🔍 Verification Status: {test_case['verification_status']}")
                print(f"   🔍 Verification Result: {test_case['verification_result']}")
                if test_case['verification_confidence'] is not None:
                    print(f"   🔍 Verification Confidence: {test_case['verification_confidence']:.2f}")
                
                if 'response_time_seconds' in test_case:
                    print(f"   ⏱️  Response Time: {test_case['response_time_seconds']:.1f}s")
            
            if test_case['validation_errors']:
                print(f"   🚨 Validation Errors:")
                for error in test_case['validation_errors'][:3]:  # Show first 3 errors
                    print(f"      • {error}")
                if len(test_case['validation_errors']) > 3:
                    print(f"      ...and {len(test_case['validation_errors']) - 3} more")
        
        # Final status
        print(f"\n{'='*80}")
        if summary['success_rate'] == 100.0:
            print(f"🎉 ALL TESTS PASSED! Perfect success rate.")
        elif summary['success_rate'] >= 80.0:
            print(f"✅ TESTS MOSTLY SUCCESSFUL ({summary['success_rate']:.1f}% success rate)")
        elif summary['success_rate'] >= 50.0:
            print(f"⚠️  TESTS PARTIALLY SUCCESSFUL ({summary['success_rate']:.1f}% success rate)")
        else:
            print(f"❌ TESTS MOSTLY FAILED ({summary['success_rate']:.1f}% success rate)")
        print(f"{'='*80}")
    
    def run_tests(self) -> bool:
        """Run the complete automated test suite."""
        print(f"🚀 Starting Automated Test Suite...")
        
        # Connect to Redis
        if not self._connect_redis():
            return False
        
        # Start response monitoring
        self._start_response_monitoring()
        
        try:
            # Get test payloads
            test_payloads = get_test_payloads()
            print(f"📋 Loaded {len(test_payloads)} test payloads")
            
            # Send all payloads
            print(f"\n📤 SENDING PAYLOADS:")
            for payload_number in sorted(test_payloads.keys()):
                payload_data = test_payloads[payload_number]
                success = self.send_payload(payload_number, payload_data)
                if not success:
                    print(f"❌ Failed to send payload {payload_number}")
                    return False
                
                # Small delay between sends
                time.sleep(0.5)
            
            # Wait for responses
            print(f"\n📥 COLLECTING RESPONSES:")
            received_count = self.wait_for_responses()
            
            # Generate and print report
            report = self.generate_report()
            self.print_report(report)
            
            # Return success if all responses were valid
            return report['summary']['success_rate'] == 100.0
            
        finally:
            self._stop_response_monitoring()

def main():
    """Main function with argument parsing."""
    parser = argparse.ArgumentParser(description='Automated Alert Bridge Testing')
    parser.add_argument('--timeout', type=int, default=60, help='Response timeout in seconds (default: 60)')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    
    args = parser.parse_args()
    
    # Create and run tester
    tester = AutomatedTester(timeout_seconds=args.timeout, verbose=args.verbose)
    success = tester.run_tests()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main() 