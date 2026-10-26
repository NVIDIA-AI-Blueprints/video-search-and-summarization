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

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=elasticsearch-connection-init.sh
source "${SCRIPT_DIR}/elasticsearch-connection-init.sh"

# ELASTICSEARCH CONNECTION VARIABLES (parameterized from docker compose)
ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS="${ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS:-20}"
ELASTICSEARCH_CONNECTION_RETRY_INTERVAL="${ELASTICSEARCH_CONNECTION_RETRY_INTERVAL:-5}"
ELASTICSEARCH_URL="${ELASTICSEARCH_URL:-http://elasticsearch:9200}"

# Retry parameters for ingest pipeline creation (kept separate from the connection
# wait above, and from ILM's/templates' own knobs, for consistency and independent tuning)
ELASTICSEARCH_INGEST_PIPELINE_CREATE_MAX_ATTEMPTS="${ELASTICSEARCH_INGEST_PIPELINE_CREATE_MAX_ATTEMPTS:-12}"
ELASTICSEARCH_INGEST_PIPELINE_CREATE_RETRY_INTERVAL="${ELASTICSEARCH_INGEST_PIPELINE_CREATE_RETRY_INTERVAL:-10}"

####################################
## function: create_ingest_pipeline
####################################
create_ingest_pipeline() {
    local pipeline_id="$1"
    local pipeline_config="$2"
    echo "Creating ingest pipeline: ${pipeline_id}"
    put_json_with_retry "ingest pipeline ${pipeline_id}" "/_ingest/pipeline/${pipeline_id}" "${pipeline_config}" \
      "200 201" "${ELASTICSEARCH_INGEST_PIPELINE_CREATE_MAX_ATTEMPTS}" "${ELASTICSEARCH_INGEST_PIPELINE_CREATE_RETRY_INTERVAL}"
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

######################
## Main
######################
main(){
    check_ES_status "Ingest pipeline creation"
    create_insertion_timestamp_ingest_pipeline
}
main "$@"
