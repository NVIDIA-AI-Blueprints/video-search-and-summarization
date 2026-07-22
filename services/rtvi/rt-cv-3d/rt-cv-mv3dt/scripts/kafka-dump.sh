#!/usr/bin/env bash
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

#
# kafka-dump.sh — dump the Kafka metadata topics to verify the pipeline output:
# per-sensor frames on mdx-raw (perception) or fused BEV tracks on mdx-bev
# (bev-fusion). Parses each nv.Frame protobuf IN-PROCESS (confluent-kafka +
# utils/schema_pb2.py) and prints (frame timestamp, sensorId, frame id) — no
# protoc needed. Also handy for checking that timestamps are synchronized
# across cameras.
#
# Usage:
#   ./scripts/kafka-dump.sh                    # stream mdx-raw until Ctrl-C
#   ./scripts/kafka-dump.sh --topic mdx-bev    # fused output instead
#   ./scripts/kafka-dump.sh --count 20         # dump 20 messages and stop
#   ./scripts/kafka-dump.sh --from-beginning --count 50
#   ./scripts/kafka-dump.sh --raw --count 1    # also print the full decoded nv.Frame
#
# Env: KAFKA_BOOTSTRAP (default localhost:9092).
# Python deps auto-install into utils/venv on first run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/utils${PYTHONPATH:+:$PYTHONPATH}"   # so `import schema_pb2` resolves

# shellcheck disable=SC1091
source "$ROOT/scripts/ensure-venv.sh"
ensure_venv || { echo "ERROR: could not set up utils/venv" >&2; exit 1; }

# our --bootstrap default first; any user-supplied --bootstrap in "$@" wins (argparse last-wins)
exec "$VENV_PY" "$ROOT/utils/kafka-dump.py" --bootstrap "${KAFKA_BOOTSTRAP:-localhost:9092}" "$@"
