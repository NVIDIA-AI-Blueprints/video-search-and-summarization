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

set -euo pipefail

if (($# != 1)); then
    echo "Usage: $0 <image:tag>" >&2
    exit 2
fi

image=$1
container=
cleanup() {
    if [[ -n "$container" ]]; then
        docker rm -f "$container" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# docker export/import collapses the final root filesystem into a new layer, so
# files deleted from an inherited base layer are not present in the distributed
# artifact. Reapply the runtime configuration because export contains only the
# filesystem.
declare -a changes=()
while IFS= read -r -d '' change; do
    changes+=(--change "$change")
done < <(
    docker image inspect "$image" | python3 -c '
import json
import sys

config = json.load(sys.stdin)[0]["Config"]

def emit(value):
    if value:
        sys.stdout.buffer.write(value.encode() + b"\0")

for value in config.get("Env") or []:
    key, separator, item = value.partition("=")
    if separator:
        emit("ENV " + key + "=" + json.dumps(item))
for key, value in (config.get("Labels") or {}).items():
    emit("LABEL " + key + "=" + json.dumps(value))
for port in (config.get("ExposedPorts") or {}):
    emit("EXPOSE " + port)
for volume in (config.get("Volumes") or {}):
    emit("VOLUME " + json.dumps([volume]))
for command in config.get("OnBuild") or []:
    emit("ONBUILD " + command)
emit("ENTRYPOINT " + json.dumps(config.get("Entrypoint")) if config.get("Entrypoint") else "")
emit("CMD " + json.dumps(config.get("Cmd")) if config.get("Cmd") else "")
emit("USER " + config["User"] if config.get("User") else "")
emit("WORKDIR " + config["WorkingDir"] if config.get("WorkingDir") else "")
emit("STOPSIGNAL " + config["StopSignal"] if config.get("StopSignal") else "")
'
)

container=$(docker create "$image")
flattened_id=$(
    docker export "$container" \
        | docker import "${changes[@]}" -
)
docker tag "$flattened_id" "$image"

echo "Flattened $image as $flattened_id"
