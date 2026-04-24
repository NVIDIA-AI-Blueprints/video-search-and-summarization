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

set -e

# KIBANA CONNECTION VARIABLES
KB_CONNECTION_RETRY_ATTEMPTS=0
KB_CONNECTION_MAX_ATTEMPTS=10
KB_URL="http://localhost:5601"


# ES CONNECTION VARIABLES
ES_CONNECTION_RETRY_ATTEMPTS=0
ES_CONNECTION_MAX_ATTEMPTS=10
ES_URL="http://localhost:9200"

#################################
## function: check_ES_status
#################################
check_ES_status(){

    echo "Attempting to connect to the Elasticsearch server."

    # Wait for ES to come up
    until $(curl --output /dev/null --silent --head --fail -XGET $ES_URL); do
        if [ ${ES_CONNECTION_RETRY_ATTEMPTS} -eq ${ES_CONNECTION_MAX_ATTEMPTS} ];then
            exit_with_msg "Max attempts to connect to ES reached."
        fi

        ES_CONNECTION_RETRY_ATTEMPTS=$(($ES_CONNECTION_RETRY_ATTEMPTS+1))
        echo "Unable to connect to ES. Trying to reconnect - (attempt $ES_CONNECTION_RETRY_ATTEMPTS/$ES_CONNECTION_MAX_ATTEMPTS)"
        sleep 5
    done
}

#################################
## function: check_kibana_status
#################################
check_kibana_status(){

    echo "Attempting to connect to the Kibana."

    # Wait for ES to come up
    until $(curl --output /dev/null --silent --head --fail -XGET $KB_URL); do
        if [ ${KB_CONNECTION_RETRY_ATTEMPTS} -eq ${KB_CONNECTION_MAX_ATTEMPTS} ];then
            exit_with_msg "Max attempts to connect to Kibana reached."
        fi

        KB_CONNECTION_RETRY_ATTEMPTS=$(($KB_CONNECTION_RETRY_ATTEMPTS+1))
        echo "Unable to connect to Kibana. Trying to reconnect - (attempt $KB_CONNECTION_RETRY_ATTEMPTS/$KB_CONNECTION_MAX_ATTEMPTS)."
        sleep 5
    done
}


############################
## function: exit_with_msg
############################
exit_with_msg(){
    echo -e "$1 \nExiting Script."
    exit 1
}

##############################
## function: import_dashboard
##############################
import_dashboard(){
    echo -e "Importing Dashboards"
    curl -X POST localhost:5601/api/saved_objects/_import?overwrite=true \
    -H "kbn-xsrf: true" \
    --form file=@"/opt/mdx/its-kibana-objects.ndjson" || exit_with_msg "Curl command to import kibana dashboard failed with failed with error code $?."
}


######################
## Main
######################
main(){
    check_ES_status
    check_kibana_status

    # Wait for ES and Kibana initizaliztion to avoid startup raise conditions.  
    sleep 10

    import_dashboard
}
main