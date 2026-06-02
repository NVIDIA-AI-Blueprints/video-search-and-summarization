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
Script to setup Redis Streams for testing.
This script initializes the streams and consumer groups needed for testing.
"""

import yaml
import sys
import os

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

def setup_redis_streams():
    """Setup Redis Streams and consumer groups"""
    config = load_config()
    
    # Redis configuration
    redis_config = config['event_bridge']['redis_source']
    
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
    
    # Get stream names
    streams = redis_config['streams']
    consumer_group = redis_config['consumer_group']
    
    # Setup input streams and consumer groups
    input_streams = [
        streams['anomaly_stream'],
        streams['heartbeat_stream']
    ]
    
    # Get output streams from sink configuration
    redis_sink_config = config['event_bridge']['redis_sink']
    output_streams = [
        redis_sink_config['streams']['enhanced_anomaly_stream'],
        redis_sink_config['streams']['incidents_stream']
    ]
    
    all_streams = input_streams + output_streams
    
    print(f"\n📋 Setting up streams:")
    for stream in all_streams:
        print(f"   - {stream}")
    
    print(f"\n👥 Consumer group: {consumer_group}")
    
    # Create consumer groups for input streams
    for stream in input_streams:
        try:
            # Create consumer group (will fail if already exists)
            r.xgroup_create(stream, consumer_group, id='0', mkstream=True)
            print(f"✅ Created consumer group '{consumer_group}' for stream '{stream}'")
        except redis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                print(f"ℹ️  Consumer group '{consumer_group}' already exists for stream '{stream}'")
            else:
                print(f"❌ Error creating consumer group for '{stream}': {e}")
    
    # Initialize output streams (just add a dummy message to create them)
    for stream in output_streams:
        try:
            # Check if stream exists
            if r.exists(stream):
                print(f"ℹ️  Stream '{stream}' already exists")
            else:
                # Create stream with a dummy message
                r.xadd(stream, {'init': 'stream_initialized'})
                print(f"✅ Created stream '{stream}'")
        except redis.ResponseError as e:
            print(f"❌ Error creating stream '{stream}': {e}")
    
    # Show stream info
    print(f"\n📊 Stream Information:")
    for stream in all_streams:
        try:
            info = r.xinfo_stream(stream)
            print(f"   {stream}: {info['length']} messages, {info['groups']} consumer groups")
        except redis.ResponseError:
            print(f"   {stream}: Stream doesn't exist yet")
    
    print(f"\n🎉 Redis Streams setup complete!")
    print(f"\n💡 Usage:")
    print(f"   - Send messages: python send_payload.py")
    print(f"   - Monitor responses: python verify_responses.py")
    print(f"   - Check stream status: redis-cli XINFO STREAM <stream_name>")

def cleanup_streams():
    """Clean up streams and consumer groups"""
    config = load_config()
    redis_config = config['event_bridge']['redis_source']
    
    r = redis.Redis(
        host=redis_config['host'],
        port=redis_config['port'],
        db=redis_config['db'],
        decode_responses=True
    )
    
    # Get all streams
    streams = redis_config['streams']
    consumer_group = redis_config['consumer_group']
    
    redis_sink_config = config['event_bridge']['redis_sink']
    
    all_streams = [
        streams['anomaly_stream'],
        streams['heartbeat_stream'],
        redis_sink_config['streams']['enhanced_anomaly_stream'],
        redis_sink_config['streams']['incidents_stream']
    ]
    
    print(f"🧹 Cleaning up Redis Streams...")
    
    for stream in all_streams:
        try:
            # Delete stream
            r.delete(stream)
            print(f"✅ Deleted stream '{stream}'")
        except redis.ResponseError as e:
            print(f"ℹ️  Stream '{stream}' doesn't exist: {e}")
    
    print(f"🎉 Cleanup complete!")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == '--cleanup':
        cleanup_streams()
    else:
        setup_redis_streams() 