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
# Redis Publisher Script for Testing Error Messages
#
# Usage:
#   python3 test_redis_publisher.py [--channel CHANNEL] [--host HOST] [--port PORT]
######################################################################################################

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone

try:
    import redis
except ImportError:
    print("Error: redis library not installed. Install with: pip install redis")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Publish test error messages to Redis channel")
    parser.add_argument(
        "--channel",
        type=str,
        default="mdx-vlm-errors",
        help="Redis channel to publish to (default: mdx-vlm-errors)",
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
        "--message",
        type=str,
        default="Test error message",
        help="Error message text (default: 'Test error message')",
    )
    parser.add_argument(
        "--stream-id",
        type=str,
        default=None,
        help="Stream ID (default: auto-generated UUID)",
    )
    parser.add_argument(
        "--type",
        type=str,
        default="functional",
        choices=["functional", "critical", "warning"],
        help="Error type (default: functional)",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="test-publisher",
        help="Source identifier (default: test-publisher)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of messages to send (default: 1)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="Interval between messages in seconds (default: 0)",
    )

    args = parser.parse_args()

    print(f"Connecting to Redis at {args.host}:{args.port}...")

    try:
        # Create Redis client
        redis_client = redis.Redis(
            host=args.host,
            port=args.port,
            db=args.db,
            password=args.password,
            decode_responses=False,
            socket_connect_timeout=5,
            socket_timeout=5,
        )

        # Test connection
        redis_client.ping()
        print("Successfully connected to Redis server\n")

    except redis.ConnectionError as e:
        print(f"Error connecting to Redis: {e}")
        print("\nMake sure Redis is running and accessible.")
        sys.exit(1)
    except redis.AuthenticationError as e:
        print(f"Error authenticating to Redis: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error initializing Redis client: {e}")
        sys.exit(1)

    try:
        print(f"Publishing to channel: {args.channel}")
        print(f"Message count: {args.count}")
        if args.count > 1:
            print(f"Interval: {args.interval}s\n")
        else:
            print()

        for i in range(args.count):
            stream_id = args.stream_id or str(uuid.uuid4())
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z"

            error_message = {
                "streamId": stream_id,
                "timestamp": timestamp,
                "type": args.type,
                "source": args.source,
                "event": args.message if args.count == 1 else f"{args.message} #{i+1}",
            }

            serialized_message = json.dumps(error_message).encode("utf-8")

            # Publish to Redis channel
            subscribers = redis_client.publish(args.channel, serialized_message)

            print(f"[{i+1}/{args.count}] Published error message:")
            print(f"  Stream ID: {stream_id}")
            print(f"  Type: {args.type}")
            print(f"  Message: {error_message['event']}")
            print(f"  Active subscribers: {subscribers}")

            if i < args.count - 1 and args.interval > 0:
                import time

                time.sleep(args.interval)

        print(f"\nSuccessfully published {args.count} message(s) to channel '{args.channel}'")

    except KeyboardInterrupt:
        print("\n\nStopped by user")
    except Exception as e:
        print(f"\nError publishing messages: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        try:
            redis_client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
