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
Script to check timestamps of entries written to Redis Streams.
Shows only the most recent entry with request/response Redis times and event time.

Usage:
    python check_redis_timestamps.py          # Show most recent entry timing
"""

import json
import yaml
import os
import sys
from datetime import datetime
import time
from collections import defaultdict

try:
    import redis
except ImportError:
    print("❌ Redis package not found. Install it with: pip install redis")
    sys.exit(1)

def load_config():
    """Load configuration from config.yaml"""
    # Get the script's directory and navigate to project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, '../..'))
    config_path = os.path.join(project_root, 'config.yaml')
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)

def redis_timestamp_to_datetime(timestamp_str):
    """Convert Redis timestamp (milliseconds-sequence) to datetime"""
    try:
        # Redis timestamp format: "1234567890123-0"
        timestamp_ms = int(timestamp_str.split('-')[0])
        return datetime.fromtimestamp(timestamp_ms / 1000)
    except:
        return None

def format_time_diff(dt1, dt2):
    """Format time difference between two datetimes"""
    diff = abs((dt2 - dt1).total_seconds())
    if diff < 1:
        return f"{diff*1000:.0f}ms"
    elif diff < 60:
        return f"{diff:.1f}s"
    elif diff < 3600:
        return f"{diff/60:.1f}m"
    else:
        return f"{diff/3600:.1f}h"

def extract_event_info(fields):
    """Extract event information from Redis fields"""
    event_info = {
        'event_id': None,
        'timestamp': None,
    }
    
    # Try to extract from 'data' field (input format)
    if 'data' in fields:
        try:
            data = json.loads(fields['data'])
            event_info['event_id'] = data.get('eventId')
            event_info['timestamp'] = data.get('timestamp')
        except json.JSONDecodeError:
            pass
    
    # Try to extract from 'payload' field (output format)
    if 'payload' in fields:
        try:
            payload = json.loads(fields['payload'])
            event_info['event_id'] = event_info['event_id'] or payload.get('eventId') or payload.get('event_id')
            event_info['timestamp'] = event_info['timestamp'] or payload.get('timestamp') or payload.get('start')
        except json.JSONDecodeError:
            pass
    
    return event_info

def get_latest_entry_timing(r, input_stream, output_streams):
    """Get timing information for the most recent complete workflow"""
    
    # Get recent entries from input stream
    input_entries = r.xrevrange(input_stream, count=10)
    
    if not input_entries:
        print("No entries found in input stream")
        return None
    
    # Process each input entry to find matching output
    for msg_id, fields in input_entries:
        event_info = extract_event_info(fields)
        event_id = event_info['event_id']
        
        if not event_id:
            continue
            
        # Get request Redis time
        request_redis_time = redis_timestamp_to_datetime(msg_id)
        
        # Parse event time
        event_time = None
        if event_info['timestamp']:
            try:
                # Handle different timestamp formats
                for fmt in ['%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S.%f']:
                    try:
                        event_time = datetime.strptime(event_info['timestamp'].replace('Z', ''), fmt.replace('Z', ''))
                        break
                    except:
                        continue
            except:
                pass
        
        # Search for matching output entry
        for output_stream in output_streams:
            try:
                # Get recent entries from output stream
                output_entries = r.xrevrange(output_stream, count=50)
                
                for out_msg_id, out_fields in output_entries:
                    out_event_info = extract_event_info(out_fields)
                    
                    if out_event_info['event_id'] == event_id:
                        # Found matching output
                        response_redis_time = redis_timestamp_to_datetime(out_msg_id)
                        
                        return {
                            'event_id': event_id,
                            'event_time': event_time,
                            'request_redis_time': request_redis_time,
                            'response_redis_time': response_redis_time,
                            'input_msg_id': msg_id,
                            'output_msg_id': out_msg_id,
                            'output_stream': output_stream
                        }
            except:
                continue
    
    return None

def main():
    """Main function"""
    config = load_config()
    
    # Redis configuration
    redis_source = config['event_bridge']['redis_source']
    redis_sink = config['event_bridge']['redis_sink']
    
    # Connect to Redis
    try:
        r = redis.Redis(
            host=redis_source['host'],
            port=redis_source['port'],
            db=redis_source['db'],
            decode_responses=True
        )
        r.ping()
        print(f"✅ Connected to Redis at {redis_source['host']}:{redis_source['port']}")
        
    except redis.ConnectionError as e:
        print(f"❌ Failed to connect to Redis: {e}")
        print("💡 Make sure Redis is running")
        sys.exit(1)
    
    # Define streams
    input_stream = redis_source['streams']['anomaly_stream']
    output_streams = [
        redis_sink['streams']['enhanced_anomaly_stream'],
        redis_sink['streams']['incidents_stream']
    ]
    
    print("\n🔍 Finding most recent complete workflow...\n")
    
    # Get latest entry timing
    timing_info = get_latest_entry_timing(r, input_stream, output_streams)
    
    if not timing_info:
        print("❌ No complete workflow found (no matching input/output entries)")
        return
    
    # Display results
    print(f"📊 Most Recent Complete Workflow")
    print(f"{'='*60}")
    print(f"Event ID: {timing_info['event_id']}")
    print(f"{'='*60}")
    
    # Format times
    event_time_str = timing_info['event_time'].strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] if timing_info['event_time'] else 'N/A'
    request_time_str = timing_info['request_redis_time'].strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    response_time_str = timing_info['response_redis_time'].strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    
    print(f"\n⏰ Event Time:          {event_time_str}")
    print(f"📥 Request Redis Time:  {request_time_str}")
    print(f"📤 Response Redis Time: {response_time_str}")
    
    # Calculate latencies
    if timing_info['event_time']:
        client_latency = format_time_diff(timing_info['event_time'], timing_info['request_redis_time'])
        print(f"\n⏱️  Client → Redis:      {client_latency}")
    
    total_latency = format_time_diff(timing_info['request_redis_time'], timing_info['response_redis_time'])
    print(f"⏱️  Total Processing:    {total_latency}")
    
    if timing_info['event_time']:
        end_to_end = format_time_diff(timing_info['event_time'], timing_info['response_redis_time'])
        print(f"⏱️  End-to-End:          {end_to_end}")
    
    print(f"\n{'='*60}")

if __name__ == "__main__":
    main() 