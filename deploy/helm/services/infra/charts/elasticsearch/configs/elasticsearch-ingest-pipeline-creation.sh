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

#!/bin/bash
set -euo pipefail

# ES CONNECTION VARIABLES
ES_CONNECTION_RETRY_ATTEMPTS=0
ES_CONNECTION_MAX_ATTEMPTS=10
ES_URL="${ELASTICSEARCH_URL:-http://localhost:9200}"

#################################
## function: check_ES_status
#################################
check_ES_status(){
    echo "Attempting to connect to the Elasticsearch server for ingest pipeline creation."
    until curl --output /dev/null --silent --head --fail -XGET "$ES_URL"; do
        if [ ${ES_CONNECTION_RETRY_ATTEMPTS} -eq ${ES_CONNECTION_MAX_ATTEMPTS} ];then
            exit_with_msg "Max attempts to connect to ES reached."
        fi
        ES_CONNECTION_RETRY_ATTEMPTS=$(($ES_CONNECTION_RETRY_ATTEMPTS+1))
        echo "Unable to connect to ES. Trying to reconnect - (attempt $ES_CONNECTION_RETRY_ATTEMPTS/$ES_CONNECTION_MAX_ATTEMPTS)"
        sleep 5
    done
}

####################################
## function: create_ingest_pipeline
####################################
create_ingest_pipeline() {
    local pipeline_id="$1"
    local pipeline_config="$2"
    echo "Creating ingest pipeline: ${pipeline_id}"
    response=$(curl -s -w "\\n%{http_code}" "${ES_URL}/_ingest/pipeline/${pipeline_id}" \
      -X 'PUT' \
      -H 'Content-Type: application/json' \
      --data-raw "${pipeline_config}" \
      --compressed \
      --insecure)

    http_code=$(echo "$response" | tail -n1)
    response_body=$(echo "$response" | sed '$d')
    echo "HTTP code: ${http_code}"
    if [ "${http_code}" -ne 200 ] && [ "${http_code}" -ne 201 ]; then
        echo "Error response from Elasticsearch:" >&2
        echo "${response_body}" >&2
        exit_with_msg "Curl command to create ${pipeline_id} in Elasticsearch failed with HTTP status ${http_code}."
    fi
    echo "Successfully created ${pipeline_id}."
}

####################################
## function: create_insertion_timestamp_ingest_pipeline
####################################
create_insertion_timestamp_ingest_pipeline() {
    local pipeline_id="insertion-timestamp-pipeline"
    local pipeline_config=$(cat <<'EOF'
{
  "description": "Adds dynamic timestamp field to documents based on targetFieldName in document body",
  "processors": [
    {
      "set": {
        "field": "_ingest_timestamp",
        "value": "{{_ingest.timestamp}}"
      }
    },
    {
      "date": {
        "field": "_ingest_timestamp",
        "target_field": "_ingest_timestamp",
        "timezone": "UTC",
        "formats" : ["ISO8601"]
      }
    },
    {
      "script": {
        "lang": "painless",
        "source": "ctx[ctx.targetFieldName] = ctx._ingest_timestamp;"
      }
    },
    {
      "remove": {
        "field": "_ingest_timestamp",
        "ignore_missing": true
      }
    },
    {
      "remove": {
        "field": "targetFieldName",
        "ignore_missing": true
      }
    }
  ]
}
EOF
)
    create_ingest_pipeline "${pipeline_id}" "${pipeline_config}"
}

############################
## function: exit_with_msg
############################
exit_with_msg(){
    echo -e "$1 \nExiting Script."
    exit 1
}

######################
## Main
######################
main(){
    check_ES_status
    create_insertion_timestamp_ingest_pipeline
}
main "$@"
