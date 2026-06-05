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
# Kafka Consumer Script for Testing VisionLLM Protobuf Messages
#
# Usage:
#   python3 test_kafka_consumer.py [--topic TOPIC] [--bootstrap-servers SERVERS]
######################################################################################################

import argparse
import json
import os
import sys
import types as _types
from datetime import datetime

from google.protobuf.json_format import MessageToJson

# Resolve the directory containing nv_pb2.py and ext_pb2.py.
# Supports two layouts:
#   1. Running from the rtvi-microservices repo (files at src/server/protos/)
#   2. Standalone: README instructs users to download test_kafka_consumer.py,
#      nv_pb2.py, ext_pb2.py into the same directory and run it.
script_dir = os.path.dirname(os.path.abspath(__file__))
repo_proto_dir = os.path.join(script_dir, "../../src/server/protos")

if os.path.exists(os.path.join(repo_proto_dir, "nv_pb2.py")):
    proto_dir = os.path.abspath(repo_proto_dir)
elif os.path.exists(os.path.join(script_dir, "nv_pb2.py")):
    proto_dir = script_dir
else:
    proto_dir = script_dir  # let imports fail with a clear error below

# The generated ext_pb2.py has a hardcoded `from server.protos import nv_pb2`,
# so make that package path resolvable regardless of layout by registering a
if "server" not in sys.modules:
    _server_pkg = _types.ModuleType("server")
    _server_pkg.__path__ = []
    sys.modules["server"] = _server_pkg
if "server.protos" not in sys.modules:
    _protos_pkg = _types.ModuleType("server.protos")
    _protos_pkg.__path__ = [proto_dir]
    sys.modules["server.protos"] = _protos_pkg

# Also add proto_dir to sys.path so `import nv_pb2` works as a plain module.
if proto_dir not in sys.path:
    sys.path.insert(0, proto_dir)

try:
    from kafka import KafkaConsumer
    from kafka.errors import KafkaError
except ImportError:
    print("Error: kafka-python not installed. Install with: pip install kafka-python")
    sys.exit(1)

# Check for protobuf dependency first
try:
    __import__("google.protobuf")
except ImportError:
    print("Error: protobuf library not installed.")
    print("Install with: pip install protobuf")
    print("Or if using a virtual environment, activate it first and then install.")
    sys.exit(1)

try:
    from server.protos import ext_pb2, nv_pb2
except ImportError as e:
    print(f"Error: Could not import protobuf modules: {e}")
    print(f"Script dir : {script_dir}")
    print(f"Proto dir  : {proto_dir}")
    print(f"Python path: {sys.path}")
    print("\nTroubleshooting:")
    print("1. Make sure protobuf is installed: pip install protobuf")
    print("2. Place nv_pb2.py and ext_pb2.py next to this script, or run")
    print("   from inside the rtvi-microservices repo.")
    print("3. If using a virtual environment, make sure it's activated.")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Consume VisionLLM protobuf messages from Kafka")
    parser.add_argument(
        "--topic",
        type=str,
        default="mdx-vlm-captions",
        help="Kafka topic to consume from (default: mdx-vlm-captions)",
    )
    parser.add_argument(
        "--bootstrap-servers",
        type=str,
        default="localhost:9094",
        help="Kafka bootstrap servers (default: localhost:9094)",
    )
    parser.add_argument(
        "--group-id",
        type=str,
        default="vision-llm-test-consumer",
        help="Consumer group ID (default: vision-llm-test-consumer)",
    )
    parser.add_argument(
        "--incident-topic",
        type=str,
        default=os.environ.get("KAFKA_INCIDENT_TOPIC", "mdx-vlm-incidents"),
        help=(
            "Kafka topic to consume incidents from "
            "(default: mdx-vlm-incidents; set to '' to disable)"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed message information",
    )
    parser.add_argument(
        "--disable-messages",
        action="store_true",
        help="Disable processing of VisionLLM messages (only process incidents and errors)",
    )
    parser.add_argument(
        "--incident-file",
        type=str,
        default=None,
        help="File path to dump incident responses (default: None, no file output)",
    )
    parser.add_argument(
        "--message-file",
        type=str,
        default=None,
        help="File path to dump VisionLLM message responses (default: None, no file output)",
    )
    parser.add_argument(
        "--json-format",
        action="store_true",
        help="Output responses in JSON format",
    )

    args = parser.parse_args()

    print(f"Connecting to Kafka at {args.bootstrap_servers}...")
    topics = [args.topic]
    if args.incident_topic:
        topics.append(args.incident_topic)

    print(f"Consuming from topic(s): {', '.join(topics)}")
    print(f"Consumer group: {args.group_id}")
    print("Press Ctrl+C to stop\n")

    try:
        consumer = KafkaConsumer(
            *topics,
            bootstrap_servers=args.bootstrap_servers.split(","),
            group_id=args.group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            value_deserializer=lambda m: m,  # Keep as bytes for protobuf
        )
    except KafkaError as e:
        print(f"Error connecting to Kafka: {e}")
        print("\nMake sure Kafka is running. You can start it with:")
        print("  bash test_kafka_setup.sh")
        sys.exit(1)

    message_count = 0
    incident_count = 0
    output_file_handles = {}  # Dictionary to store file handles by request ID
    message_file_handles = {}  # Dictionary to store message file handles by request/stream ID
    incident_file_base = args.incident_file
    message_file_base = args.message_file

    def sanitize_filename(text):
        """Sanitize text for use in filenames"""
        # Replace invalid filename characters with underscores
        invalid_chars = '<>:"/\\|?*'
        sanitized = text
        for char in invalid_chars:
            sanitized = sanitized.replace(char, "_")
        # Remove leading/trailing spaces and dots
        sanitized = sanitized.strip(" .")
        return sanitized if sanitized else "unknown"

    def get_output_file_handle(request_id):
        """Get or create a file handle for the given request ID"""
        if not incident_file_base:
            return None

        # Sanitize request ID for filename
        sanitized_request_id = sanitize_filename(str(request_id))

        if request_id not in output_file_handles:
            # Create filename based on request ID
            base, ext = os.path.splitext(incident_file_base)
            if ext:
                filename = f"{base}_{sanitized_request_id}{ext}"
            else:
                filename = f"{incident_file_base}_{sanitized_request_id}"

            output_file_handles[request_id] = open(filename, "a", encoding="utf-8")
            print(f"Created output file for Request ID {request_id}: {filename}")

        return output_file_handles[request_id]

    def get_message_file_handle(request_id):
        """Get or create a file handle for VisionLLM messages for the given request/stream ID"""
        if not message_file_base:
            return None

        # Sanitize request ID for filename
        sanitized_request_id = sanitize_filename(str(request_id))

        if request_id not in message_file_handles:
            # Create filename based on request ID
            base, ext = os.path.splitext(message_file_base)
            if ext:
                filename = f"{base}_{sanitized_request_id}{ext}"
            else:
                filename = f"{message_file_base}_{sanitized_request_id}"

            message_file_handles[request_id] = open(filename, "a", encoding="utf-8")
            print(f"Created message file for Request/Stream ID {request_id}: {filename}")

        return message_file_handles[request_id]

    if incident_file_base:
        print("Writing incident responses to files based on Request ID\n")
    if message_file_base:
        print("Writing VisionLLM message responses to files based on Request/Stream ID\n")

    try:
        for message in consumer:
            message_count += 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            headers = {k: v for k, v in (message.headers or [])}
            message_type = headers.get("message_type", b"vision_llm").decode("utf-8", "ignore")

            if args.disable_messages and message_type != "vision_llm":
                print(f"\n[{timestamp}] Message #{message_count}")
                print(f"  Topic: {message.topic}")
                print(f"  Partition: {message.partition}, Offset: {message.offset}")
                print(f"  Key: {message.key.decode('utf-8') if message.key else 'None'}")
                print(f"  Headers: {', '.join(f'{k}={v!r}' for k, v in headers.items()) or 'None'}")
                print(f"  Message Type: {message_type}")

            try:
                if message_type == "incident":
                    incident_count += 1
                    incident = ext_pb2.Incident()
                    incident.ParseFromString(message.value)

                    print("  Incident Summary:")
                    print(f"    Incident #{incident_count}")
                    print(f"    Sensor ID: {incident.sensorId}")
                    print(f"    Object IDs: {list(incident.objectIds)}")
                    print(f"    Category: {incident.category or 'N/A'}")
                    print(f"    Is Anomaly: {incident.isAnomaly}")
                    print(f"    Alert: {incident.info.get('incidentDetected', 'true')}")
                    print(f"    Request ID: {incident.info.get('requestId', 'N/A')}")
                    print(f"    Chunk IDX: {incident.info.get('chunkIdx', 'N/A')}")

                    if incident.HasField("llm") and incident.llm.queries:
                        query = incident.llm.queries[0]
                        print("    LLM Query Response:")
                        preview = query.response[:200]
                        suffix = "..." if len(query.response) > 200 else ""
                        print(f"      {preview}{suffix}")

                    if args.verbose:
                        print("\n    Incident Info Map:")
                        for key, value in incident.info.items():
                            print(f"      {key}: {value}")

                    # Dump incident to file if output file is specified
                    request_id = incident.info.get("requestId", "N/A")
                    output_file_handle = get_output_file_handle(request_id)
                    if output_file_handle:
                        if args.json_format:
                            # Convert incident protobuf to JSON using native protobuf JSON schema
                            incident_json_str = MessageToJson(
                                incident, preserving_proto_field_name=True
                            )
                            incident_data = json.loads(incident_json_str)

                            # Add metadata fields (not part of protobuf schema)
                            incident_data["IncidentCount"] = incident_count
                            incident_data["kafka"] = {
                                "timestamp": timestamp,
                                "topic": message.topic,
                                "partition": message.partition,
                                "offset": message.offset,
                                "key": message.key.decode("utf-8") if message.key else None,
                            }

                            # Add metadata comment/documentation for custom fields
                            incident_data["_metadata"] = {
                                "_comment": (
                                    "Fields added by test_kafka_consumer.py "
                                    "(not part of original protobuf schema)"
                                ),
                                "customFields": [
                                    "IncidentCount - Sequential incident number added by consumer",
                                    (
                                        "kafka - Kafka message metadata "
                                        "(timestamp, topic, partition, offset, key)"
                                    ),
                                    "_metadata - This metadata field documenting custom additions",
                                ],
                            }

                            # Write JSON to file
                            json.dump(
                                incident_data, output_file_handle, indent=2, ensure_ascii=False
                            )
                            output_file_handle.write("\n\n")
                        else:
                            # Original text format
                            output_file_handle.write(f"IncidentCount: {incident_count}\n")
                            output_file_handle.write(
                                f"chunkIdx: {incident.info.get('chunkIdx', 'N/A')}\n"
                            )

                            if incident.HasField("llm") and incident.llm.queries:
                                query = incident.llm.queries[0]
                                output_file_handle.write(f"LLM Query Response: {query.response}\n")
                            else:
                                output_file_handle.write("LLM Query Response: N/A\n")

                            # Add verbose parameters in text format
                            if args.verbose:
                                output_file_handle.write(f"timestamp: {timestamp}\n")
                                output_file_handle.write(f"topic: {message.topic}\n")
                                output_file_handle.write(f"partition: {message.partition}\n")
                                output_file_handle.write(f"offset: {message.offset}\n")
                                output_file_handle.write(
                                    f"key: {message.key.decode('utf-8') if message.key else 'None'}\n"
                                )
                                output_file_handle.write(f"sensorId: {incident.sensorId}\n")
                                output_file_handle.write(f"objectIds: {list(incident.objectIds)}\n")
                                output_file_handle.write(
                                    f"category: {incident.category or 'N/A'}\n"
                                )
                                output_file_handle.write(f"isAnomaly: {incident.isAnomaly}\n")
                                output_file_handle.write(
                                    f"alert: {incident.info.get('incidentDetected', 'true')}\n"
                                )
                                output_file_handle.write(
                                    f"requestId: {incident.info.get('requestId', 'N/A')}\n"
                                )
                                output_file_handle.write("\nIncident Info Map:\n")
                                for key, value in incident.info.items():
                                    output_file_handle.write(f"  {key}: {value}\n")
                                if incident.HasField("llm") and incident.llm.queries:
                                    for i, q in enumerate(incident.llm.queries):
                                        output_file_handle.write(f"\nLLM Query #{i+1}:\n")
                                        output_file_handle.write(f"  Response: {q.response}\n")
                                        if hasattr(q, "query"):
                                            output_file_handle.write(f"  Query: {q.query}\n")

                            output_file_handle.write("\n")

                        output_file_handle.flush()
                elif message_type == "error":
                    try:
                        error_json = json.dumps(json.loads(message.value.decode("utf-8")), indent=4)
                        print(f"\n  Error Message: {error_json}")
                    except Exception as e:
                        print(f"\n  Error decoding error message as JSON: {e}")
                        print(f"\n  Raw Error Message: {message.value.decode('utf-8')}")
                else:
                    if args.disable_messages:
                        continue
                    vision_llm = nv_pb2.VisionLLM()
                    vision_llm.ParseFromString(message.value)

                    print("  VisionLLM Summary:")
                    print(f"    Version: {vision_llm.version}")
                    print(f"    Start Frame ID: {vision_llm.startFrameId}")
                    print(f"    End Frame ID: {vision_llm.endFrameId}")
                    print(f"    Frame Count: {vision_llm.info.get('frameCount', 'N/A')}")
                    print(f"    Request ID: {vision_llm.info.get('requestId', 'N/A')}")
                    print(f"    Chunk IDX: {vision_llm.info.get('chunkIdx', 'N/A')}")
                    print(f"    Stream ID: {vision_llm.info.get('streamId', 'N/A')}")
                    print(f"    Timestamp: {vision_llm.timestamp.ToDatetime().isoformat()}")
                    print(f"    End Timestamp: {vision_llm.end.ToDatetime().isoformat()}")

                    if vision_llm.HasField("sensor"):
                        print(f"    Sensor ID: {vision_llm.sensor.id}")
                        print(f"    Sensor Type: {vision_llm.sensor.type}")
                        if vision_llm.sensor.info.get("url"):
                            print(f"    Sensor URL: {vision_llm.sensor.info.get('url')}")
                        if vision_llm.sensor.info.get("path"):
                            print(f"    Sensor Path: {vision_llm.sensor.info.get('path')}")

                    if vision_llm.HasField("llm") and vision_llm.llm.queries:
                        query = vision_llm.llm.queries[0]
                        if query.response:
                            response_preview = (
                                query.response[:100] + "..."
                                if len(query.response) > 100
                                else query.response
                            )
                            print(f"    VLM Response: {response_preview}")

                    if vision_llm.HasField("llm") and vision_llm.llm.visionEmbeddings:
                        print(f"  Vision Embeddings: {len(vision_llm.llm.visionEmbeddings)}")
                        for embedding in vision_llm.llm.visionEmbeddings:
                            print(f"    Embedding: {embedding.vector[:10]}...")

                    if args.verbose:
                        print("\n    Full Message Info:")
                        for key, value in vision_llm.info.items():
                            print(f"      {key}: {value}")

                        if vision_llm.frames:
                            print(f"    Frames: {len(vision_llm.frames)} frame(s)")
                            for i, frame in enumerate(vision_llm.frames[:3]):  # Show first 3 frames
                                print(
                                    f"      Frame {i+1}: ID={frame.id}, Objects={len(frame.objects)}"
                                )
                                print(
                                    f"      Frame {i+1}: Timestamp={frame.timestamp.ToDatetime().isoformat()}"
                                )
                            if len(vision_llm.frames) > 3:
                                print(f"      ... and {len(vision_llm.frames) - 3} more frames")

                    # Dump VisionLLM message to file if output file is specified
                    message_request_id = vision_llm.info.get("requestId") or vision_llm.info.get(
                        "streamId", "N/A"
                    )
                    message_file_handle = get_message_file_handle(message_request_id)
                    if message_file_handle:
                        if args.json_format:
                            # Convert VisionLLM protobuf to JSON using native protobuf JSON schema
                            vision_llm_json_str = MessageToJson(
                                vision_llm, preserving_proto_field_name=True
                            )
                            vision_llm_data = json.loads(vision_llm_json_str)

                            # Add metadata fields (not part of protobuf schema)
                            vision_llm_data["MessageCount"] = message_count
                            vision_llm_data["kafka"] = {
                                "timestamp": timestamp,
                                "topic": message.topic,
                                "partition": message.partition,
                                "offset": message.offset,
                                "key": message.key.decode("utf-8") if message.key else None,
                            }

                            # Add metadata comment/documentation for custom fields
                            vision_llm_data["_metadata"] = {
                                "_comment": (
                                    "Fields added by test_kafka_consumer.py "
                                    "(not part of original protobuf schema)"
                                ),
                                "customFields": [
                                    "MessageCount - Sequential message number added by consumer",
                                    (
                                        "kafka - Kafka message metadata "
                                        "(timestamp, topic, partition, offset, key)"
                                    ),
                                    "_metadata - This metadata field documenting custom additions",
                                ],
                            }

                            # Write JSON to file
                            json.dump(
                                vision_llm_data, message_file_handle, indent=2, ensure_ascii=False
                            )
                            message_file_handle.write("\n\n")
                        else:
                            # Text format
                            message_file_handle.write(f"MessageCount: {message_count}\n")
                            message_file_handle.write(
                                f"chunkIdx: {vision_llm.info.get('chunkIdx', 'N/A')}\n"
                            )
                            message_file_handle.write(
                                f"requestId: {vision_llm.info.get('requestId', 'N/A')}\n"
                            )
                            message_file_handle.write(
                                f"streamId: {vision_llm.info.get('streamId', 'N/A')}\n"
                            )

                            if vision_llm.HasField("llm") and vision_llm.llm.queries:
                                query = vision_llm.llm.queries[0]
                                if query.response:
                                    message_file_handle.write(f"VLM Response: {query.response}\n")

                            if args.verbose:
                                message_file_handle.write("\nFull Message Info:\n")
                                for key, value in vision_llm.info.items():
                                    message_file_handle.write(f"  {key}: {value}\n")

                            message_file_handle.write("\n")

                        message_file_handle.flush()

            except Exception as e:
                print(f"  Error parsing protobuf message: {e}")
                print(f"  Raw message size: {len(message.value)} bytes")
                if args.verbose:
                    import traceback

                    traceback.print_exc()

    except KeyboardInterrupt:
        print(
            f"\n\nStopped. Received {message_count} message(s) total, {incident_count} incident(s)."
        )
    except Exception as e:
        print(f"\nError consuming messages: {e}")
        import traceback

        traceback.print_exc()
    finally:
        consumer.close()
        if output_file_handles:
            for request_id, file_handle in output_file_handles.items():
                file_handle.close()
            print(
                f"\nIncident responses written to {len(output_file_handles)} file(s) based on Request IDs:"
            )
            for request_id in output_file_handles.keys():
                sanitized_request_id = sanitize_filename(str(request_id))
                base, ext = os.path.splitext(incident_file_base)
                if ext:
                    filename = f"{base}_{sanitized_request_id}{ext}"
                else:
                    filename = f"{incident_file_base}_{sanitized_request_id}"
                print(f"  Request ID {request_id}: {filename}")
        if message_file_handles:
            for request_id, file_handle in message_file_handles.items():
                file_handle.close()
            print(
                "\nVisionLLM message responses written to "
                f"{len(message_file_handles)} file(s) based on Request/Stream IDs:"
            )
            for request_id in message_file_handles.keys():
                sanitized_request_id = sanitize_filename(str(request_id))
                base, ext = os.path.splitext(message_file_base)
                if ext:
                    filename = f"{base}_{sanitized_request_id}{ext}"
                else:
                    filename = f"{message_file_base}_{sanitized_request_id}"
                print(f"  Request/Stream ID {request_id}: {filename}")


if __name__ == "__main__":
    main()
