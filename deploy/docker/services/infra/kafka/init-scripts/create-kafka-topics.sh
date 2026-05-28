#!/bin/bash

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

# installing required binaries
ARCH=$(uname -m)
JQ_URL=""

if [ "$ARCH" = "x86_64" ]; then
    JQ_URL="https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-amd64"
elif [ "$ARCH" = "aarch64" ]; then
    JQ_URL="https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-arm64"
else
    echo "Unsupported architecture: $ARCH"
    exit 1
fi

mkdir -p ~/jqbin
curl -L -o ~/jqbin/jq "$JQ_URL"
chmod +x ~/jqbin/jq

export PATH="/home/appuser/jqbin:${PATH}"

# bootstrap kafka hosts
KAFKA_HOST=${BOOTSTRAP_HOST:-localhost}
KAFKA_PORT=${KAFKA_PORT:-9092}

echo 'Waiting for Kafka to come up in order to create the kafka-topics'

until kafka-broker-api-versions --bootstrap-server $KAFKA_HOST:$KAFKA_PORT > /dev/null 2>&1; do
    echo 'Waiting for Kafka services to be up'
    sleep 2
done

echo 'Kafka services are up and running'

echo "KAFKA_TOPICS: $KAFKA_TOPICS"
echo "DEFAULT_PARTITIONS: $DEFAULT_PARTITIONS"
echo "DEFAULT_RETENTION_MS: $DEFAULT_RETENTION_MS"
echo "DEFAULT_REPLICATION_FACTOR: $DEFAULT_REPLICATION_FACTOR"
echo "DEFAULT_SEGMENT_MS: $DEFAULT_SEGMENT_MS"
echo "KAFKA_HOST: $KAFKA_HOST"
echo "KAFKA_PORT: $KAFKA_PORT"

kafkaTopics=$(echo "$KAFKA_TOPICS" | jq --arg default_partitions "${DEFAULT_PARTITIONS}" \
  --arg default_retention_ms "${DEFAULT_RETENTION_MS}" \
  --arg default_replication_factor "${DEFAULT_REPLICATION_FACTOR}" \
  --arg default_segment_ms "${DEFAULT_SEGMENT_MS}" \
  --arg kafka_host "${KAFKA_HOST}:${KAFKA_PORT}" \
  -r '.[] | "kafka-topics --create --bootstrap-server \($kafka_host) --topic \(.name) --partitions \(.partitions // $default_partitions) --replication-factor \(.replication_factor // $default_replication_factor) --if-not-exists --config retention.ms=\(.retention_ms // $default_retention_ms) --config segment.ms=\(.segment_ms // $default_segment_ms)"')

echo "bootstrap-server: $KAFKA_HOST:$KAFKA_PORT"

#Check if Kafka is Up & Running
echo "Checking if $KAFKA_HOST:$KAFKA_PORT is reachable"
CON_Check=`kafka-broker-api-versions --bootstrap-server $KAFKA_HOST:$KAFKA_PORT > /dev/null 2>&1 && echo "True" || echo "False"`
if [[ $CON_Check == True ]]
then
      kafka-topics --bootstrap-server $KAFKA_HOST:$KAFKA_PORT --list

      echo -e 'Connectivity looks fine, Creating kafka topics'
      
      # Create kafka topics using the list provided

			while IFS= read -r kafkaTopics; do
			  echo "Executing: $kafkaTopics"
			  eval "$kafkaTopics"
			done <<< "$kafkaTopics"

      echo -e 'Below kafka topics created successfully created:'
      kafka-topics --bootstrap-server $KAFKA_HOST:$KAFKA_PORT --list
else 
  echo "Kafka is not healthy, Please check if Kafka is Running and $KAFKA_HOST:$KAFKA_PORT is reachable"

fi
