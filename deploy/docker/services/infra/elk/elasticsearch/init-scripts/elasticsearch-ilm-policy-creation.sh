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
# shellcheck source=lib/es-retry.sh
source "${SCRIPT_DIR}/lib/es-retry.sh"

# ELASTICSEARCH CONNECTION VARIABLES (parameterized from docker compose)
ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS="${ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS:-20}"
ELASTICSEARCH_CONNECTION_RETRY_INTERVAL="${ELASTICSEARCH_CONNECTION_RETRY_INTERVAL:-5}"
ELASTICSEARCH_HEALTH_TIMEOUT="${ELASTICSEARCH_HEALTH_TIMEOUT:-5s}"
ELASTICSEARCH_URL="${ELASTICSEARCH_URL:-${ES_URL:-http://elasticsearch:9200}}"

# ILM policy retention period (default: 4h)
ELASTICSEARCH_ILM_MIN_AGE="${ELASTICSEARCH_ILM_MIN_AGE:-4h}"
ELASTICSEARCH_ILM_CREATE_MAX_ATTEMPTS="${ELASTICSEARCH_ILM_CREATE_MAX_ATTEMPTS:-12}"
ELASTICSEARCH_ILM_CREATE_RETRY_INTERVAL="${ELASTICSEARCH_ILM_CREATE_RETRY_INTERVAL:-10}"

#################################
## function: check_ES_status
#################################
check_ES_status(){
    echo "Attempting to connect to the Elasticsearch server for ILM policy creation."

    local attempt=1
    local response
    local health_url="${ELASTICSEARCH_URL}/_cluster/health?local=false&wait_for_status=yellow&wait_for_events=normal&timeout=${ELASTICSEARCH_HEALTH_TIMEOUT}"

    while [ "${attempt}" -le "${ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS}" ]; do
        if response=$(curl -fsS "${health_url}" 2>&1); then
            if echo "${response}" | grep -Eq '"timed_out"[[:space:]]*:[[:space:]]*false'; then
                echo "Elasticsearch cluster health is ready for ILM policy creation."
                return
            fi

            echo "Elasticsearch cluster health check timed out waiting for a ready master."
        else
            echo "Unable to connect to ES: ${response}"
        fi

        echo "Trying to reconnect - (attempt ${attempt}/${ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS})"
        attempt=$((attempt+1))
        sleep "${ELASTICSEARCH_CONNECTION_RETRY_INTERVAL}"
    done

    exit_with_msg "Max attempts to connect to a ready Elasticsearch cluster reached."
}

configure_ilm_settings(){
    echo "Configuring ILM settings for faster execution."
    
    # Set ILM poll interval to 30 seconds instead of default 10 minutes
    put_json_with_retry "ILM poll interval configuration" "/_cluster/settings" '{
        "persistent": {
          "indices.lifecycle.poll_interval": "30s"
        }
      }' "200" "${ELASTICSEARCH_ILM_CREATE_MAX_ATTEMPTS}" "${ELASTICSEARCH_ILM_CREATE_RETRY_INTERVAL}"
    
    echo "ILM poll interval set to 30 seconds."
}

####################################
## function: create_ilm_policies
####################################
create_ilm_policy() {
    local policy_name="$1"
    local policy_config="$2"
    
    echo "Creating ILM policy: ${policy_name}"
    put_json_with_retry "ILM policy ${policy_name}" "/_ilm/policy/${policy_name}" "${policy_config}" \
      "200" "${ELASTICSEARCH_ILM_CREATE_MAX_ATTEMPTS}" "${ELASTICSEARCH_ILM_CREATE_RETRY_INTERVAL}"
}

create_ilm_policies(){
    echo "Creating ILM policies for indices."

    # Create all ILM policies using the configured min_age
    create_ilm_policy 'mdx-behavior-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-raw-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-frames-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-alerts-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-events-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-mtmc-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-rtls-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-amr-locations-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-amr-events-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-bev-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-space-utilization-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-vlm-alerts-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-incidents-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-vlm-incidents-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-embed-filtered-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"
    create_ilm_policy 'mdx-compressed-embeddings-ilm-policy' "{\"policy\":{\"phases\":{\"delete\":{\"min_age\":\"${ELASTICSEARCH_ILM_MIN_AGE}\",\"actions\":{\"delete\":{}}}}}}"

    echo "All ILM policies created successfully."
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
    configure_ilm_settings
    create_ilm_policies
}
main 
