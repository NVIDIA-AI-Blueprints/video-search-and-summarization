#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

set -Ee

function check_vst_running() {
VST_STATUS=`curl -s -o /dev/null -w "%{http_code}" localhost:30000`
 echo $VST_STATUS
  if [[ $VST_STATUS != '200' ]]; then
	  echo "VST is not running, Please make sure VST is installed to be collect the data"
	  exit 1

  else
      echo "VST is Running and is healthy..."
  fi
}

function import_vst_data() {
sudo apt-get update && sudo apt-get install jq -y
IMPORT_VST_DATA_LOCAL_PATH=`docker inspect mdx-vst | jq -r .[].GraphDriver.Data.MergedDir`
if [[ $IMPORT_VST_DATA_LOCAL_PATH == '' && $VST_DATA_STORE_LOCAL_PATH == '' ]]; then
	echo "#######    VST Volume is not found for Importing the VST DATA... VST_DATA_LOCAL_PATH = $IMPORT_VST_DATA_LOCAL_PATH can be found from docker inspect command....  #######"
	exit 0

else
    echo "Importing VST Data for MDX LLM App Testing, VST DATA file will be stored at PATH = $VST_DATA_STORE_LOCAL_PATH for data from PATH = $IMPORT_VST_DATA_LOCAL_PATH"
    sudo docker cp $VST_DATA_STORE_LOCAL_PATH/vst_data mdx-vst:/home/vst/vst_release
    sudo docker cp $VST_DATA_STORE_LOCAL_PATH/vst_video mdx-vst:/home/vst/vst_release
    sudo docker restart mdx-vst
fi
}

check_vst_running
import_vst_data
