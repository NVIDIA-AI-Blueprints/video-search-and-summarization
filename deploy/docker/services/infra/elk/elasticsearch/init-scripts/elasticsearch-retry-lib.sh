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
#
# Shared HTTP-PUT-with-retry helper for the Elasticsearch init scripts
# (elasticsearch-ilm-policy-creation.sh, elasticsearch-template-creation.sh,
# elasticsearch-ingest-pipeline-creation.sh). Meant to be `source`d, not run
# directly.

#################################
## function: is_retryable_http_code
#################################
is_retryable_http_code() {
    [[ "$1" =~ ^(000|408|429|502|503|504)$ ]]
}

####################################
## function: put_json_with_retry
## args: description path payload [success_codes] [max_attempts] [retry_interval]
## Each caller (ILM policy, index template, ingest pipeline creation) passes
## its own dedicated max_attempts/retry_interval pair
## (ELASTICSEARCH_ILM_CREATE_*, ELASTICSEARCH_TEMPLATE_CREATE_*,
## ELASTICSEARCH_INGEST_PIPELINE_CREATE_*) so each step is independently
## tunable. If omitted, max_attempts/retry_interval fall back to the
## ELASTICSEARCH_CONNECTION_* knobs the connection-wait check uses.
####################################
put_json_with_retry() {
    local description="$1"
    local path="$2"
    local payload="$3"
    local success_codes="${4:-200}"
    local max_attempts="${5:-${ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS:-20}}"
    local retry_interval="${6:-${ELASTICSEARCH_CONNECTION_RETRY_INTERVAL:-5}}"
    local attempt=1
    local response
    local curl_exit_code
    local http_code
    local response_body

    while [ "${attempt}" -le "${max_attempts}" ]; do
        curl_exit_code=0
        response=$(curl -sS -w "\\n%{http_code}" "${ELASTICSEARCH_URL}${path}" \
          -X 'PUT' \
          -H 'Content-Type: application/json' \
          --data-raw "${payload}" \
          --compressed \
          --insecure 2>&1) || curl_exit_code=$?

        http_code=$(printf '%s\n' "${response}" | tail -n1)
        if [[ "${http_code}" =~ ^[0-9]{3}$ ]]; then
            response_body=$(printf '%s\n' "${response}" | sed '$d')
        else
            http_code="000"
            response_body="${response}"
        fi

        echo "HTTP code: ${http_code}"
        if [[ " ${success_codes} " == *" ${http_code} "* ]]; then
            echo "Successfully completed ${description}."
            return
        fi

        if is_retryable_http_code "${http_code}" && [ "${attempt}" -lt "${max_attempts}" ]; then
            if [ "${curl_exit_code}" -ne 0 ]; then
                echo "Curl exited with code ${curl_exit_code} while processing ${description}."
            fi
            echo "Elasticsearch is not ready to process ${description}; retrying in ${retry_interval}s (attempt ${attempt}/${max_attempts})."
            attempt=$((attempt+1))
            sleep "${retry_interval}"
            continue
        fi

        echo "Error response from Elasticsearch:" >&2
        echo "${response_body}" >&2
        exit_with_msg "Curl command for ${description} failed with HTTP status ${http_code}."
    done

    exit_with_msg "Exceeded max attempts for ${description}."
}
