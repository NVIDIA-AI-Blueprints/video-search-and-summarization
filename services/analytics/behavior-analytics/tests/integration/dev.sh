#!/usr/bin/env bash
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

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source $SCRIPT_DIR/generate_env.sh
. "$SCRIPT_DIR/docker_compose/infra/.env"

cd "$PROJ_ROOT_DIR"

# Playback data file path and mode
DATA_FILEPATH=${1:-tests/integration/docker_compose/apps_data/playback/warehouse_2d_playback_data.txt}
PLAYBACK_MODE=${2:-}

# Start the infra services
docker compose -f $SCRIPT_DIR/docker_compose/infra/compose.yml up -d --build --force-recreate

# Run the playback app
docker run -it --rm --network host \
  -v $PROJ_ROOT_DIR/configs/frame_playback_config.json:/resources/frame_playback_config.json \
  -v $PROJ_ROOT_DIR/${DATA_FILEPATH}:/opt/mdx/playback/playback_data.txt \
  nvcr.io/nv-metropolis-dev/metropolis-analytic/mdx-behavior-analytics:3.2 \
  python3 apps/playback/playback_frames.py \
  --config /resources/frame_playback_config.json \
  --playback-filepath /opt/mdx/playback/playback_data.txt \
  $PLAYBACK_MODE

# Run main app to be tested separately...
