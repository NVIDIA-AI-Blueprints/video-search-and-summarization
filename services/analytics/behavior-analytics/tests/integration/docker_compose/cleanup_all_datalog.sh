#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

. ./infra/.env

# Use sudo only when not root and sudo is available (CI often runs as root with no sudo)
run_rm() {
  if [ "$(id -u)" -eq 0 ] || ! command -v sudo >/dev/null 2>&1; then
    rm "$@"
  else
    sudo rm "$@"
  fi
}

echo "Cleaning up DIR: $MDX_DATA_DIR"

if [ -d "$MDX_DATA_DIR/data_log/zookeeper/data/" ]; then
    run_rm -rf $MDX_DATA_DIR/data_log/zookeeper/data/*
fi

if [ -d "$MDX_DATA_DIR/data_log/zookeeper/log/" ]; then
    run_rm -rf $MDX_DATA_DIR/data_log/zookeeper/log/*
fi

if [ -d "$MDX_DATA_DIR/data_log/kafka/" ]; then
    run_rm -rf $MDX_DATA_DIR/data_log/kafka/*
fi

if [ -d "$MDX_DATA_DIR/data_log/elastic/" ]; then
    run_rm -rf $MDX_DATA_DIR/data_log/elastic/data/*
    run_rm -rf $MDX_DATA_DIR/data_log/elastic/logs/*
fi

if [ -d "$MDX_DATA_DIR/data_log/tmp/" ]; then
    run_rm -rf $MDX_DATA_DIR/data_log/tmp/*
fi

if [ -d "$MDX_DATA_DIR/data_log/redis/data" ]; then
    run_rm -rf $MDX_DATA_DIR/data_log/redis/data/*
fi

if [ -d "$MDX_DATA_DIR/data_log/redis/log" ]; then
    run_rm -rf $MDX_DATA_DIR/data_log/redis/log/*
fi

if [ -d "$MDX_DATA_DIR/data_log/emqx/data" ]; then
    run_rm -rf $MDX_DATA_DIR/data_log/emqx/data/*
fi

if [ -d "$MDX_DATA_DIR/data_log/emqx/log" ]; then
    run_rm -rf $MDX_DATA_DIR/data_log/emqx/log/*
fi
