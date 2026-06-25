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
Script to create required Kafka topics for testing.
"""

import yaml
from confluent_kafka.admin import AdminClient, NewTopic

def load_config():
    """Load configuration from config.yaml"""
    with open('../../../config.yaml', 'r') as file:
        return yaml.safe_load(file)

def create_topics():
    """Create required Kafka topics"""
    config = load_config()
    
    # Kafka admin client configuration
    admin_config = {
        'bootstrap.servers': config['kafka']['bootstrap_servers']
    }
    
    admin_client = AdminClient(admin_config)
    
    # Define topics to create
    topics = [
        NewTopic(config['kafka']['anomalyTopic'], num_partitions=1, replication_factor=1),
        NewTopic(config['kafka']['enhanced_anomaly_topic'], num_partitions=1, replication_factor=1),
        NewTopic(config['kafka']['incidents_topic'], num_partitions=1, replication_factor=1)
    ]
    
    # Create topics
    fs = admin_client.create_topics(topics)
    
    # Wait for operation to complete
    for topic, f in fs.items():
        try:
            f.result()  # The result itself is None
            print(f"✅ Topic '{topic}' created successfully")
        except Exception as e:
            if "already exists" in str(e):
                print(f"ℹ️  Topic '{topic}' already exists")
            else:
                print(f"❌ Failed to create topic '{topic}': {e}")

if __name__ == "__main__":
    create_topics() 