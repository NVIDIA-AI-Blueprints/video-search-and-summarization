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

# ILM policy retention period (default: 4h)
ELASTICSEARCH_ILM_MIN_AGE="${ELASTICSEARCH_ILM_MIN_AGE:-4h}"

#################################
## function: check_ES_status
#################################
check_ES_status(){
    echo "Attempting to connect to the Elasticsearch server for ILM policy creation."

    # Wait for ES to come up
    until curl --output /dev/null --silent --head --fail -XGET "$ES_URL"; do
        if [ ${ES_CONNECTION_RETRY_ATTEMPTS} -eq ${ES_CONNECTION_MAX_ATTEMPTS} ];then
            exit_with_msg "Max attempts to connect to ES reached."
        fi

        ES_CONNECTION_RETRY_ATTEMPTS=$(($ES_CONNECTION_RETRY_ATTEMPTS+1))
        echo "Unable to connect to ES. Trying to reconnect - (attempt $ES_CONNECTION_RETRY_ATTEMPTS/$ES_CONNECTION_MAX_ATTEMPTS)"
        sleep 5
    done
}

configure_ilm_settings(){
    echo "Configuring ILM settings for faster execution."
    
    # Set ILM poll interval to 30 seconds instead of default 10 minutes
    curl -X PUT "$ES_URL/_cluster/settings" \
      -H 'Content-Type: application/json' \
      --data-raw '{
        "persistent": {
          "indices.lifecycle.poll_interval": "30s"
        }
      }' \
      --compressed \
      --insecure || exit_with_msg "Failed to configure ILM poll interval."
    
    echo "ILM poll interval set to 30 seconds."
}

####################################
## function: create_ilm_policies
####################################
create_ilm_policy() {
    local policy_name="$1"
    local policy_config="$2"
    
    echo "Creating ILM policy: ${policy_name}"
    response=$(curl -s -w "\\n%{http_code}" "${ES_URL}/_ilm/policy/${policy_name}" \
      -X 'PUT' \
      -H 'Content-Type: application/json' \
      --data-raw "${policy_config}" \
      --compressed \
      --insecure)
    
    http_code=$(echo "$response" | tail -n1)
    response_body=$(echo "$response" | sed '$d')
    echo "HTTP code: ${http_code}"
    if [ "${http_code}" -ne 200 ]; then
        echo "Error response from Elasticsearch:" >&2
        echo "${response_body}" >&2
        exit_with_msg "Curl command to create ${policy_name} in Elasticsearch failed with HTTP status ${http_code}."
    fi
    echo "Successfully created ${policy_name}."
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
