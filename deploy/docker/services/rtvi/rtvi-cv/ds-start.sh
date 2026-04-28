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

echo -e "\nds pgie configs\n"
cat ds-ppl-analytics-pgie-config.yml

# Check STREAM_TYPE and run appropriate command
if [ "$STREAM_TYPE" = "kafka" ]; then
    echo "Running metropolis_perception_app with kafka configuration..."
    echo -e "\nds main configs\n"
    cat ds-main-config.txt
    ./metropolis_perception_app -c ds-main-config.txt -m 1 -t 0 -l 5 --message-rate 1 --tracker-reid
elif [ "$STREAM_TYPE" = "redis" ]; then
    echo "Running metropolis_perception_app with redis configuration..."
    echo -e "\nds main configs\n"
    cat ds-main-redis-config.txt
    ./metropolis_perception_app -c ds-main-redis-config.txt -m 1 -t 0 -l 5 --message-rate 1 --tracker-reid
else
    echo "STREAM_TYPE not set or invalid. Defaulting to kafka configuration..."
    echo -e "\nds main configs\n"
    cat ds-main-config.txt
    ./metropolis_perception_app -c ds-main-config.txt -m 1 -t 0 -l 5 --message-rate 1 --tracker-reid
fi
