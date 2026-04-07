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
set -e
KB_URL="${KIBANA_URL:-http://localhost:5601}"
ES_URL="${ELASTICSEARCH_URL:-http://localhost:9200}"
KB_CONNECTION_RETRY_ATTEMPTS=0
KB_CONNECTION_MAX_ATTEMPTS=30
ES_CONNECTION_RETRY_ATTEMPTS=0
ES_CONNECTION_MAX_ATTEMPTS=30

check_ES_status(){
    until curl -sf -o /dev/null -XGET "$ES_URL"; do
        if [ ${ES_CONNECTION_RETRY_ATTEMPTS} -eq ${ES_CONNECTION_MAX_ATTEMPTS} ]; then echo "Max ES connection attempts reached."; exit 1; fi
        ES_CONNECTION_RETRY_ATTEMPTS=$(($ES_CONNECTION_RETRY_ATTEMPTS+1))
        echo "Waiting for ES... ($ES_CONNECTION_RETRY_ATTEMPTS/$ES_CONNECTION_MAX_ATTEMPTS)"
        sleep 5
    done
}
check_kibana_status(){
    until curl -sf -o /dev/null -XGET "$KB_URL"; do
        if [ ${KB_CONNECTION_RETRY_ATTEMPTS} -eq ${KB_CONNECTION_MAX_ATTEMPTS} ]; then echo "Max Kibana connection attempts reached."; exit 1; fi
        KB_CONNECTION_RETRY_ATTEMPTS=$(($KB_CONNECTION_RETRY_ATTEMPTS+1))
        echo "Waiting for Kibana... ($KB_CONNECTION_RETRY_ATTEMPTS/$KB_CONNECTION_MAX_ATTEMPTS)"
        sleep 5
    done
}

check_ES_status
check_kibana_status
sleep 10
echo "Importing Kibana dashboards..."
curl -sSf -X POST "${KB_URL}/api/saved_objects/_import?overwrite=true" -H "kbn-xsrf: true" --form file=@/opt/mdx/lvs-kibana-objects.ndjson || exit 1
echo "Done."
