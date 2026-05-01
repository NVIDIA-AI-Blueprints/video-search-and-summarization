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

readonly MAX_IMAGE_UPLOAD_COUNT=20
video_analytics_api_url="http://localhost:8081"

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
    curl -X POST "${video_analytics_api_url}/config/upload-file/calibration" \
	--form configFiles=@"/opt/vss/calibration.json" || exit_with_msg "Curl command to import calibration file failed with error code $?."
}

import_images(){
    echo -e "Importing Images and Meta"
    local images_dir="/opt/vss/images"
    local metadata_file="${images_dir}/imageMetadata.json"

    if [ ! -f "$metadata_file" ]; then
        exit_with_msg "imageMetadata.json not found at ${metadata_file}"
    fi

    local image_count
    image_count=$(jq '.images | length' "$metadata_file")
    if [ "$image_count" -gt "$MAX_IMAGE_UPLOAD_COUNT" ]; then
        exit_with_msg "imageMetadata.json has ${image_count} images; maximum allowed is ${MAX_IMAGE_UPLOAD_COUNT}."
    fi

    local form_args=()
    local fileName
    while IFS= read -r fileName; do
        if [ -n "$fileName" ]; then
            local filepath="${images_dir}/${fileName}"
            if [ -f "$filepath" ]; then
                form_args+=(--form "images=@${filepath}")
            else
                echo "Warning: ${fileName} listed in imageMetadata.json not found at ${filepath}, skipping."
            fi
        fi
    done < <(jq -r '.images[].fileName' "$metadata_file")

    if [ ${#form_args[@]} -eq 0 ]; then
        echo "No image files from imageMetadata.json found in ${images_dir}"
    else
        form_args+=(--form "imageMetadata=@${metadata_file}")
        curl -X POST "${video_analytics_api_url}/config/calibration/images" "${form_args[@]}" || exit_with_msg "Curl command to import images failed with error code $?."
    fi
}

fetchstatus() {
  curl \
    -o /dev/null \
    --silent \
    --head \
    --write-out '%{http_code}' \
    "${video_analytics_api_url}/livez"
    
    echo ""
}

######################
## Main
######################
main(){

    # Wait for API initialization to avoid startup raise conditions.  
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
    sleep 2
    echo "importing images ..."
    import_images
    echo "done"
}
main
