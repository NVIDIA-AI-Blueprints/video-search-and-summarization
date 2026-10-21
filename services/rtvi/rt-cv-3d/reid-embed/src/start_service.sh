#! /bin/bash
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

# Configuration is read entirely from environment variables by src/main.py
# (see KAFKA_CONSUMER_TOPIC and DEEPSTREAM_SCHEMA defaults inside config.py).
# Set them via your shell, .env file, or docker-compose `environment:` block.

# The script lives in src/, but main.py must be launched from the repository
# root: Python puts the script's directory on sys.path (so `import config`
# resolves), while the working directory stays at the root for relative paths.
cd "$(dirname "$0")/.." || exit 1
exec python src/main.py
