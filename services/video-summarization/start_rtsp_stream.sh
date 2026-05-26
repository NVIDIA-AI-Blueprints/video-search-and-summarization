#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

if [ $# -lt 2 ]; then
echo "Usage: $0 <file> <port>"
exit
fi
IPs=$(ifconfig | grep '\<inet\>' | awk '{print $2}')

for IP in $IPs; do
    echo "$(tput setaf 1)Starting VLC RTSP server at $(tput bold)rtsp://$IP:$2/file-stream$(tput sgr0)"
done

cvlc --loop $1 ":sout=#gather:rtp{sdp=rtsp://:$2/file-stream}" :network-caching=1500 :sout-all :sout-keep

