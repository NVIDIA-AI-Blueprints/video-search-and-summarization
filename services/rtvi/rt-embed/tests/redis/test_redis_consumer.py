#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

######################################################################################################
# Redis Consumer Script for Testing Error Messages
#
# Usage:
#   python3 test_redis_consumer.py [--channel CHANNEL] [--host HOST] [--port PORT]
######################################################################################################

import argparse
import json
import sys
from datetime import datetime

try:
    import redis
except ImportError:
    print("Error: redis library not installed. Install with: pip install redis")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Consume error messages from Redis pub/sub channel"
    )
    parser.add_argument(
        "--channel",
        type=str,
        default="mdx-vlm-errors",
        help="Redis channel to subscribe to (default: mdx-vlm-errors)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="Redis server host (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=6379,
        help="Redis server port (default: 6379)",
    )
    parser.add_argument(
        "--db",
        type=int,
        default=0,
        help="Redis database number (default: 0)",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="Redis password (optional)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed message information",
    )

    args = parser.parse_args()

    print(f"Connecting to Redis at {args.host}:{args.port}...")
    print(f"Subscribing to channel: {args.channel}")
    print("Press Ctrl+C to stop\n")

    try:
        # Create Redis client
        redis_client = redis.Redis(
            host=args.host,
            port=args.port,
            db=args.db,
            password=args.password,
            decode_responses=False,  # We'll decode manually
            socket_connect_timeout=5,
            socket_timeout=5,
        )

        # Test connection
        redis_client.ping()
        print(f"Successfully connected to Redis server at {args.host}:{args.port}\n")

    except redis.ConnectionError as e:
        print(f"Error connecting to Redis: {e}")
        print("\nMake sure Redis is running and accessible.")
        print("You can start Redis with:")
        print("  docker run -d -p 6379:6379 redis:latest")
        print("Or install locally:")
        print("  sudo apt-get install redis-server")
        sys.exit(1)
    except redis.AuthenticationError as e:
        print(f"Error authenticating to Redis: {e}")
        print("Check that your password is correct.")
        sys.exit(1)
    except Exception as e:
        print(f"Error initializing Redis client: {e}")
        sys.exit(1)

    message_count = 0
    pubsub = None

    try:
        # Create pubsub object and subscribe to channel
        pubsub = redis_client.pubsub()
        pubsub.subscribe(args.channel)

        print(f"Waiting for messages on channel '{args.channel}'...")
        print(
            '(Tip: Send a test message with: redis-cli PUBLISH {channel} \'{"test": "message"}\')\n'
        )

        # Listen for messages
        for message in pubsub.listen():
            # Skip subscription confirmation messages
            if message["type"] == "subscribe":
                print(f"Subscribed to channel: {message['channel'].decode('utf-8')}")
                continue

            if message["type"] != "message":
                continue

            message_count += 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            print(f"\n{'='*80}")
            print(f"[{timestamp}] Message #{message_count}")
            print(f"{'='*80}")
            print(f"  Channel: {message['channel'].decode('utf-8')}")

            try:
                # Decode and parse JSON message
                message_data = message["data"]
                if isinstance(message_data, bytes):
                    message_data = message_data.decode("utf-8")

                error_message = json.loads(message_data)

                print("\n  Error Message Details:")
                print(f"    Stream ID:   {error_message.get('streamId', 'N/A')}")
                print(f"    Timestamp:   {error_message.get('timestamp', 'N/A')}")
                print(f"    Type:        {error_message.get('type', 'N/A')}")
                print(f"    Source:      {error_message.get('source', 'N/A')}")
                print(f"    Event:       {error_message.get('event', 'N/A')}")

                if args.verbose:
                    print("\n  Full Message (JSON):")
                    print(json.dumps(error_message, indent=4))

            except json.JSONDecodeError as e:
                print(f"  Error: Could not parse message as JSON: {e}")
                print(f"  Raw message: {message['data']}")
            except Exception as e:
                print(f"  Error processing message: {e}")
                print(f"  Raw message: {message['data']}")

    except KeyboardInterrupt:
        print(f"\n\n{'='*80}")
        print(f"Stopped. Received {message_count} error message(s) total.")
        print(f"{'='*80}")
    except redis.ConnectionError as e:
        print(f"\n\nError: Lost connection to Redis: {e}")
        print(f"Received {message_count} message(s) before disconnect.")
    except Exception as e:
        print(f"\n\nError consuming messages: {e}")
        import traceback

        traceback.print_exc()
    finally:
        if pubsub:
            try:
                pubsub.unsubscribe()
                pubsub.close()
            except Exception:
                pass
        try:
            redis_client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
