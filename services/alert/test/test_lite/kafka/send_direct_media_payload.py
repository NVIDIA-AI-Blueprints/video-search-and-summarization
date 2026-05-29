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
Send media files from a folder to Kafka (Protobuf format).

Usage:
    python send_direct_media_payload.py /path/to/media_folder
"""

import json
import sys
import os
import socket
import threading
import time
import mimetypes
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from functools import partial
from urllib.parse import unquote
import uuid
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

try:
    from confluent_kafka import Producer
except ImportError:
    print("Error: confluent_kafka not installed. Run: pip install confluent-kafka")
    sys.exit(1)

from utils.schema_util import convert_incident_to_protobuf_incident
import yaml


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), '../../../config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, **kwargs):
        self._serve_directory = directory
        super().__init__(*args, **kwargs)
    
    def translate_path(self, path):
        # Extract filename from URL path (handle URL encoding)
        filename = unquote(os.path.basename(path))
        if self._serve_directory:
            full_path = os.path.join(self._serve_directory, filename)
        else:
            full_path = os.path.join(os.getcwd(), filename)
        print(f"  [DEBUG] translate_path: {path} -> {full_path} (exists: {os.path.exists(full_path)})")
        return full_path
    
    def log_message(self, format, *args):
        # Log requests to help debug
        print(f"  [HTTP] {format % args}")


def detect_media_type(file_path: str) -> str:
    mime, _ = mimetypes.guess_type(file_path)
    if mime:
        if mime.startswith('image/'):
            return 'image'
        if mime.startswith('video/'):
            return 'video'
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
        return 'image'
    return 'video'


def is_media_file(file_path: str) -> bool:
    ext = os.path.splitext(file_path)[1].lower()
    return ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv']


def scan_folder_for_media(folder_path: str) -> list:
    if not os.path.isdir(folder_path):
        return []
    media_files = []
    for filename in sorted(os.listdir(folder_path)):
        file_path = os.path.join(folder_path, filename)
        if os.path.isfile(file_path) and is_media_file(file_path):
            media_files.append(file_path)
    return media_files


def create_payload(file_url: str, media_type: str):
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    return {
        "id": str(uuid.uuid4()),
        "sensorId": f"test-cam-{uuid.uuid4().hex[:6]}",
        "category": "Barcode Scanner Module",
        "timestamp": ts,
        "end": ts,
        "info": {
            "media_url": file_url,
            "media_type": media_type
        }
    }


def send_payload(payload: dict, config: dict):
    topic = config['event_bridge']['kafka_source']['topics'].get('incident', 'mdx-incidents')
    producer = Producer({
        'bootstrap.servers': config['kafka']['bootstrap_servers'],
        'client.id': 'direct-media-test'
    })
    try:
        # Convert JSON to Protobuf format
        proto_message = convert_incident_to_protobuf_incident(payload)
        producer.produce(topic=topic, key=payload['sensorId'], value=proto_message.SerializeToString())
        producer.flush()
        url = payload['info']['media_url']
        display_url = url[:70] + "..." if len(url) > 70 else url
        print(f"  Sent: {display_url}")
        return True
    except Exception as e:
        print(f"  Error: {e}")
        return False


def send_folder(folder_path: str):
    folder_path = os.path.abspath(folder_path)
    
    if not os.path.isdir(folder_path):
        print(f"Error: Folder not found: {folder_path}")
        return False
    
    media_files = scan_folder_for_media(folder_path)
    if not media_files:
        print(f"No media files found in: {folder_path}")
        return False
    
    config = load_config()
    
    print(f"Found {len(media_files)} media file(s) in: {folder_path}")
    print("Files to serve:")
    for f in media_files:
        print(f"  - {os.path.basename(f)}")
    print("-" * 60)
    
    # Start HTTP server
    port = get_free_port()
    print(f"Serving from directory: {folder_path}")
    handler = partial(QuietHandler, directory=folder_path)
    server = HTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    ip = get_local_ip()
    base_url = f"http://{ip}:{port}"
    print(f"HTTP server: {base_url}")
    
    # Verify server is working with first file
    first_file = os.path.basename(media_files[0])
    test_url = f"{base_url}/{first_file}"
    try:
        resp = requests.head(test_url, timeout=5)
        if resp.status_code == 200:
            print(f"Server verified OK: {test_url}")
        else:
            print(f"WARNING: Server returned {resp.status_code} for {test_url}")
    except Exception as e:
        print(f"WARNING: Cannot reach server: {e}")
    
    print("-" * 60)
    
    # Send payloads
    success_count = 0
    for i, file_path in enumerate(media_files, 1):
        filename = os.path.basename(file_path)
        media_type = detect_media_type(file_path)
        file_url = f"{base_url}/{filename}"
        
        print(f"[{i}/{len(media_files)}] {filename} ({media_type})")
        payload = create_payload(file_url, media_type)
        if send_payload(payload, config):
            success_count += 1
    
    print("-" * 60)
    print(f"Sent {success_count}/{len(media_files)} payloads")
    
    print("\nServer running (Ctrl+C to stop)...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    
    server.shutdown()
    print("Done.")
    return success_count > 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('folder', type=str, help='Folder containing media files')
    args = parser.parse_args()
    
    send_folder(args.folder)
