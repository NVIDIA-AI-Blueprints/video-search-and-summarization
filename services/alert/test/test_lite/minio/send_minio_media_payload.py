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
Upload media files to MinIO and send payloads to Kafka.

This script:
1. Uploads media files from a folder to MinIO bucket
2. Sends Kafka payloads with MinIO URLs for Mode 3 processing

Prerequisites:
    pip install minio confluent-kafka pyyaml
"""

import argparse
import json
import mimetypes
import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))  # alert_agent root

try:
    from minio import Minio
    from minio.error import S3Error
except ImportError:
    print("Error: minio not installed. Run: pip install minio")
    sys.exit(1)

try:
    from confluent_kafka import Producer
except ImportError:
    print("Error: confluent_kafka not installed. Run: pip install confluent-kafka")
    sys.exit(1)

import yaml
from utils.schema_util import convert_incident_to_protobuf_incident


# Default MinIO configuration
DEFAULT_MINIO_CONFIG = {
    'host': 'localhost',
    'port': 9000,
    'access_key': 'minioadmin',
    'secret_key': 'minioadmin123',
    'bucket': 'alert-media',
    'secure': False,
}


def load_config():
    """Load alert_agent config.yaml (for Kafka settings)"""
    config_path = os.path.join(os.path.dirname(__file__), '../../../config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def load_minio_config():
    """Load MinIO config from minio_config.yaml (same folder)"""
    minio_config_path = os.path.join(os.path.dirname(__file__), 'minio_config.yaml')
    if os.path.exists(minio_config_path):
        with open(minio_config_path, 'r') as f:
            return yaml.safe_load(f)
    return {}


def get_minio_config(args) -> dict:
    """Get MinIO config from minio_config.yaml or command line args"""
    # Start with defaults
    minio_cfg = DEFAULT_MINIO_CONFIG.copy()
    
    # Override from minio_config.yaml if present
    yaml_config = load_minio_config()
    yaml_minio = yaml_config.get('minio', {})
    if yaml_minio:
        minio_cfg['host'] = yaml_minio.get('host', minio_cfg['host'])
        minio_cfg['port'] = yaml_minio.get('port', minio_cfg['port'])
        minio_cfg['access_key'] = yaml_minio.get('access_key', minio_cfg['access_key'])
        minio_cfg['secret_key'] = yaml_minio.get('secret_key', minio_cfg['secret_key'])
        minio_cfg['bucket'] = yaml_minio.get('bucket', minio_cfg['bucket'])
        minio_cfg['secure'] = yaml_minio.get('secure', minio_cfg['secure'])
    
    # Override from command line args (highest priority)
    if args.minio_host:
        minio_cfg['host'] = args.minio_host
    if args.minio_port:
        minio_cfg['port'] = args.minio_port
    if args.minio_bucket:
        minio_cfg['bucket'] = args.minio_bucket
    if args.minio_access_key:
        minio_cfg['access_key'] = args.minio_access_key
    if args.minio_secret_key:
        minio_cfg['secret_key'] = args.minio_secret_key
    
    return minio_cfg


def create_minio_client(minio_cfg: dict) -> Minio:
    """Create MinIO client"""
    endpoint = f"{minio_cfg['host']}:{minio_cfg['port']}"
    return Minio(
        endpoint,
        access_key=minio_cfg['access_key'],
        secret_key=minio_cfg['secret_key'],
        secure=minio_cfg['secure'],
    )


def ensure_bucket(client: Minio, bucket: str):
    """Ensure bucket exists, create if not"""
    if not client.bucket_exists(bucket):
        print(f"Creating bucket: {bucket}")
        client.make_bucket(bucket)
    else:
        print(f"Using existing bucket: {bucket}")


def detect_media_type(file_path: str) -> str:
    """Detect if file is image or video"""
    mime, _ = mimetypes.guess_type(file_path)
    if mime:
        if mime.startswith('image/'):
            return 'image'
        if mime.startswith('video/'):
            return 'video'
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff']:
        return 'image'
    return 'video'


def is_media_file(file_path: str) -> bool:
    """Check if file is a supported media file"""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in [
        '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff',
        '.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'
    ]


def scan_folder_for_media(folder_path: str) -> list:
    """Scan folder for media files"""
    if not os.path.isdir(folder_path):
        return []
    media_files = []
    for filename in sorted(os.listdir(folder_path)):
        file_path = os.path.join(folder_path, filename)
        if os.path.isfile(file_path) and is_media_file(file_path):
            media_files.append(file_path)
    return media_files


def upload_to_minio(client: Minio, bucket: str, file_path: str, object_name: str = None) -> str:
    """
    Upload file to MinIO and return the object URL.
    
    Returns:
        URL to access the uploaded file
    """
    if object_name is None:
        object_name = os.path.basename(file_path)
    
    # Get content type
    content_type, _ = mimetypes.guess_type(file_path)
    if content_type is None:
        content_type = 'application/octet-stream'
    
    # Upload file
    client.fput_object(
        bucket,
        object_name,
        file_path,
        content_type=content_type,
    )
    
    return object_name


def get_minio_url(minio_cfg: dict, bucket: str, object_name: str) -> str:
    """Generate public URL for MinIO object"""
    protocol = 'https' if minio_cfg['secure'] else 'http'
    return f"{protocol}://{minio_cfg['host']}:{minio_cfg['port']}/{bucket}/{object_name}"


def create_payload(file_urls: list, media_type: str = "image", category: str = "Barcode Scanner Module") -> dict:
    """
    Create Kafka payload for Mode 3 processing.
    
    Args:
        file_urls: List of media URLs (1 or more)
        media_type: Type of media - 'image', 'images', or 'video'
        category: Alert category
    """
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    return {
        "id": str(uuid.uuid4()),
        "sensorId": f"minio-test-{uuid.uuid4().hex[:6]}",
        "category": category,
        "timestamp": ts,
        "end": ts,
        "info": {
            "media_urls": file_urls,
            "media_type": media_type
        }
    }


def send_payload(payload: dict, config: dict) -> bool:
    """Send payload to Kafka"""
    topic = config['event_bridge']['kafka_source']['topics'].get('incident', 'mdx-incidents')
    producer = Producer({
        'bootstrap.servers': config['kafka']['bootstrap_servers'],
        'client.id': 'minio-media-test'
    })
    try:
        proto_message = convert_incident_to_protobuf_incident(payload)
        producer.produce(
            topic=topic,
            key=payload['sensorId'],
            value=proto_message.SerializeToString()
        )
        producer.flush()
        return True
    except Exception as e:
        print(f"  Error sending to Kafka: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('folder', type=str, help='Folder containing media files')
    parser.add_argument('--minio-host', type=str, help='MinIO host (default: localhost)')
    parser.add_argument('--minio-port', type=int, help='MinIO port (default: 9000)')
    parser.add_argument('--minio-bucket', type=str, help='MinIO bucket (default: alert-media)')
    parser.add_argument('--minio-access-key', type=str, help='MinIO access key')
    parser.add_argument('--minio-secret-key', type=str, help='MinIO secret key')
    parser.add_argument('--category', type=str, default='Barcode Scanner Module',
                       help='Alert category for payload')
    parser.add_argument('--upload-only', action='store_true',
                       help='Only upload to MinIO, do not send Kafka payloads')
    parser.add_argument('--list-bucket', action='store_true',
                       help='List files in MinIO bucket and exit')
    parser.add_argument('--batch', action='store_true',
                       help='Send all images as a single multi-image payload (uses media_urls)')
    parser.add_argument('--images-only', action='store_true',
                       help='Only process image files (skip videos) - useful with --batch')
    args = parser.parse_args()
    
    config = load_config()
    minio_cfg = get_minio_config(args)
    
    print("=" * 60)
    print("MinIO Media Upload & Kafka Sender")
    print("=" * 60)
    print(f"MinIO endpoint: {minio_cfg['host']}:{minio_cfg['port']}")
    print(f"MinIO bucket:   {minio_cfg['bucket']}")
    print("-" * 60)
    
    # Create MinIO client
    try:
        client = create_minio_client(minio_cfg)
        # Test connection
        client.list_buckets()
        print("✓ MinIO connection OK")
    except Exception as e:
        print(f"✗ Failed to connect to MinIO: {e}")
        print("\nMake sure MinIO is running:")
        print("  cd test/test_lite/minio && docker compose up -d")
        sys.exit(1)
    
    # List bucket contents if requested
    if args.list_bucket:
        print(f"\nFiles in bucket '{minio_cfg['bucket']}':")
        try:
            ensure_bucket(client, minio_cfg['bucket'])
            objects = client.list_objects(minio_cfg['bucket'])
            for obj in objects:
                url = get_minio_url(minio_cfg, minio_cfg['bucket'], obj.object_name)
                print(f"  - {obj.object_name} ({obj.size} bytes)")
                print(f"    URL: {url}")
        except Exception as e:
            print(f"  Error: {e}")
        return
    
    # Scan folder for media files
    folder_path = os.path.abspath(args.folder)
    if not os.path.isdir(folder_path):
        print(f"Error: Folder not found: {folder_path}")
        sys.exit(1)
    
    media_files = scan_folder_for_media(folder_path)
    if not media_files:
        print(f"No media files found in: {folder_path}")
        sys.exit(1)
    
    print(f"\nFound {len(media_files)} media file(s):")
    for f in media_files:
        print(f"  - {os.path.basename(f)}")
    print("-" * 60)
    
    # Ensure bucket exists
    ensure_bucket(client, minio_cfg['bucket'])
    
    # Filter to images only if requested
    if args.images_only:
        media_files = [f for f in media_files if detect_media_type(f) == 'image']
        if not media_files:
            print("No image files found after filtering")
            sys.exit(1)
        print(f"Filtered to {len(media_files)} image file(s)")
    
    # Upload files and send payloads
    uploaded = []
    for i, file_path in enumerate(media_files, 1):
        filename = os.path.basename(file_path)
        media_type = detect_media_type(file_path)
        
        print(f"\n[{i}/{len(media_files)}] {filename} ({media_type})")
        
        # Upload to MinIO
        try:
            object_name = upload_to_minio(client, minio_cfg['bucket'], file_path)
            file_url = get_minio_url(minio_cfg, minio_cfg['bucket'], object_name)
            print(f"  ✓ Uploaded to MinIO: {file_url}")
            uploaded.append({
                'file': filename,
                'url': file_url,
                'type': media_type
            })
        except S3Error as e:
            print(f"  ✗ Upload failed: {e}")
            continue
        
        # Send individual Kafka payload (unless upload-only or batch mode)
        if not args.upload_only and not args.batch:
            payload = create_payload([file_url], media_type, args.category)
            if send_payload(payload, config):
                print(f"  ✓ Sent Kafka payload (id={payload['id'][:8]}...)")
            else:
                print(f"  ✗ Failed to send Kafka payload")
    
    # Batch mode: send all images as a single payload
    if args.batch and not args.upload_only and uploaded:
        # Filter to images only for batch mode
        image_urls = [item['url'] for item in uploaded if item['type'] == 'image']
        if image_urls:
            print(f"\n--- Batch Mode: Sending {len(image_urls)} images as single payload ---")
            payload = create_payload(image_urls, "images", args.category)
            if send_payload(payload, config):
                print(f"  ✓ Sent batch Kafka payload (id={payload['id'][:8]}...)")
                print(f"    Images in payload: {len(image_urls)}")
            else:
                print(f"  ✗ Failed to send batch Kafka payload")
        else:
            print("\n  No images to batch (only videos found)")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Uploaded: {len(uploaded)}/{len(media_files)} files")
    
    if uploaded:
        print("\nMinIO URLs:")
        for item in uploaded:
            print(f"  {item['type']:5s} | {item['url']}")
    
    if not args.upload_only:
        if args.batch:
            image_count = len([u for u in uploaded if u['type'] == 'image'])
            print(f"\nBatch mode: Sent 1 multi-image payload with {image_count} images")
        print(f"Kafka topic: {config['event_bridge']['kafka_source']['topics'].get('incident', 'mdx-incidents')}")
    
    print("\nTo view files in MinIO Console:")
    print(f"  http://{minio_cfg['host']}:9001")
    print(f"  Login: {minio_cfg['access_key']} / {minio_cfg['secret_key']}")


if __name__ == "__main__":
    main()
