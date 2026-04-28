#!/bin/sh
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

set -e
rep="${SDR_HTTP_REPLAYSTREAM_PORT:-4004}"
liv="${SDR_HTTP_LIVESTREAM_PORT:-4005}"
max="${SENSOR_BP_WAIT_SDR_STREAM_MAX_SEC:-300}"
echo "[sensor-bp-wait-sdr-streaming] waiting for 127.0.0.1:${rep} and :${liv} (${max} s max)"
for _ in $(seq 1 "$max"); do
  if nc -z -w1 127.0.0.1 "$rep" && nc -z -w1 127.0.0.1 "$liv"; then
    echo "[sensor-bp-wait-sdr-streaming] ok"
    exit 0
  fi
  sleep 1
done
echo "[sensor-bp-wait-sdr-streaming] timeout" >&2
exit 1
