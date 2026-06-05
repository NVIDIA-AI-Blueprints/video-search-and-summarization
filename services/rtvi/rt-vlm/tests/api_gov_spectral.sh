#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

function print_red() {
    echo -e "\033[1;31m$1\033[0m"
}

if ! which spectral &>/dev/null; then
    print_red "spectral tool not found, install it with \`npm install @stoplight/spectral\` and add it to your PATH"
    exit 1
fi

curl --silent -o governance-main.tgz --etag-compare governance-main.tgz.etag --etag-save governance-main.tgz.etag https://gitlab-master.nvidia.com/api-standards/api-governance/pipeline/-/archive/main/governance-main.tgz
if [ $? -eq 6 ]; then
    echo
    print_red "failed to download api governance rules, you need to be on the vpn, proceeding with cached rules"
    echo
fi
tar xzf governance-main.tgz

mkdir -p governance-main/spectral && cd governance-main/spectral
git clone https://gitlab-master.nvidia.com/api-standards/api-governance/rulesets.git rulesets && cd rulesets
git checkout tags/latest && cd ../../..

for ruleset in $(/bin/ls -1 governance-main/spectral/rulesets/standard-*.yaml); do
    echo "Running spectral lint with ruleset $ruleset"
    spectral lint --ruleset $ruleset $1
done

