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

set -e

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
import_calibration(){
    echo -e "Importing Calibration JSON File"
    curl -X POST localhost:8081/config/upload-file/calibration \
	--form configFiles=@"/opt/mdx/calibration/sample-data/calibration.json" || exit_with_msg "Curl command to import calibration file failed with error code $?."
}

import_road_network(){
    echo -e "Importing Road Network JSON File"
    curl -X POST localhost:8081/config/upload-file/road-network \
	--form configFiles=@"/opt/mdx/calibration/sample-data/road-network.json" || exit_with_msg "Curl command to import road-network file failed with error code $?."
}

fetchstatus() {
  curl \
    -o /dev/null \
    --silent \
    --head \
    --write-out '%{http_code}' \
    "http://localhost:8081/livez"

    echo ""
}

######################
## Main
######################
main(){

    # Wait for API initizaliztion to avoid startup raise conditions.
    sleep 10
    echo "Checking if API service is reachable"
    apistatus=$(fetchstatus)          # initialize to actual value before we sleep even once
    echo "apistatus: $apistatus"
	until [ "$apistatus" = 200 ]; do  # until our result is success...
	  sleep 2                         # wait a second...
	  apistatus=$(fetchstatus)        # then poll again.
	  echo "apistatus: $apistatus"
	done
	echo "apistatus: $apistatus"

	echo "importing calibration ..."
    import_calibration
    echo "importing road network ..."
    import_road_network
    sleep 2
    echo "done"
}
main
