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

# This will create an RTSP source with uri rtsp://<ip>:<port>/file-stream

apt-get install -y vlc
useradd -m vlcuser
su -c  "cvlc --loop /opt/nvidia/via/streams/warehouse_82min.mp4 ":sout=#gather:rtp{sdp=rtsp://:4344/file-stream}" :network-caching=1500 :sout-all :sout-keep" vlcuser &
