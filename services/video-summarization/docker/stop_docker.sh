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

SCRIPT_DIR=$(dirname $(realpath $0))
export REPO_DIR=$(dirname $SCRIPT_DIR)

if [ -z "${OVERRIDE_USER_NAME}" ]; then
    echo "OVERRIDE_USER_NAME is not set; using $USER"
else
    USER=$OVERRIDE_USER_NAME
    echo "OVERRIDE_USER_NAME is set to $USER"
fi

if docker ps | grep "\<via-engine-$USER\>\$" > /dev/null; then
    docker compose -f deploy/compose.yaml -f deploy/compose.dev.yaml \
        --project-directory=${REPO_DIR} -p via-engine-$USER down
fi
