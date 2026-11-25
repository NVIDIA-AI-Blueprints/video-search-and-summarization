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

if [ ! $1 ] 
then
	echo "Usage: bash $0 <output_foler>"
	echo "Environment variables:"
	echo "  LOCAL_MS_MODE   Set to 'true' to run deepstream-app in Docker container when running locally"
	echo "               (default: 'false', running from auto calib container)"
	echo ""
	echo "Examples:"
	echo "  $0 /path/to/output_dir"
	echo "  LOCAL_MS_MODE=true $0 /path/to/output_dir"
	exit 1
fi

echo $1

SCRIPT_ROOT="$(dirname "$0")"

mkdir $SCRIPT_ROOT/../configs/config_DeepStream/kitti_detector 

echo "Running deepstream app to detect people..."

if [ "${LOCAL_MS_MODE:-false}" = "true" ]; then
    docker run -it --rm --security-opt=no-new-privileges --ipc=host --network host --gpus all \
        -e DISPLAY=$DISPLAY -e P4ROOT=$P4ROOT -v /tmp/.X11-unix/:/tmp/.X11-unix \
        -v $HOME:$HOME -w $PWD --cap-add=SYS_PTRACE --security-opt seccomp=unconfined \
        --shm-size="8g" -v /tmp/.X11-unix/:/tmp/.X11-unix \
        nvcr.io/nvidia/deepstream:9.1-triton-multiarch \
        deepstream-app -c $SCRIPT_ROOT/../configs/config_DeepStream/conf_2d.txt
else
    deepstream-app -c $SCRIPT_ROOT/../configs/config_DeepStream/conf_2d.txt
fi


mv $SCRIPT_ROOT/../configs/config_DeepStream/kitti_detector $1 -f
mv peoplenet_out_2d.mp4 $1 -f
