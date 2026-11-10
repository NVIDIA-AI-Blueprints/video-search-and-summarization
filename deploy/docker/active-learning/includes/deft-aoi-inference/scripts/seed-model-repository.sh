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

# Seed the shared Triton model repository volume from the inference image, once.
#
# The volume is seeded only when it is empty or incomplete. Later image upgrades
# do NOT refresh the baseline model in it, because the volume also holds the
# versions the flywheel has promoted since. See README.md ("Model repository").

set -euo pipefail

target=/seed-target
marker="${target}/.aoi-model-repository-initialized"
acl_marker="${target}/.aoi-model-repository-acl-v1"
shared_gid="${AOI_MODEL_REPOSITORY_GID:-2000}"
required_files=(
  "${target}/changenet_classify/config.pbtxt"
  "${target}/changenet_classify/1/model.onnx"
  "${target}/changenet_classify/1/model.onnx.data"
  "${target}/changenet_classify/1/model.pth"
)

ensure_acl_tools() {
  # Use setfacl from the inference image when it is there. Otherwise install the
  # 'acl' package, which needs network access to the image's apt repositories.
  if command -v setfacl >/dev/null 2>&1; then
    return
  fi

  echo "setfacl not found in the inference image; installing the 'acl' package." >&2
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Cannot install 'acl': apt-get is not available. Use an AOI_INFERENCE_IMAGE that includes setfacl." >&2
    exit 1
  fi

  export DEBIAN_FRONTEND=noninteractive
  if ! { apt-get update && apt-get install -y --no-install-recommends acl; }; then
    echo "Failed to install 'acl' (no network access to the apt repositories?). Use an AOI_INFERENCE_IMAGE that includes setfacl." >&2
    exit 1
  fi
  rm -rf /var/lib/apt/lists/*
}

prepare_repository_permissions() {
  chown -R "1000:${shared_gid}" "${target}"
  find "${target}" -type d -exec chmod 2775 {} +
  find "${target}" -type f -exec chmod 0664 {} +

  # The default ACL persists with the named volume, so it is applied (and acl
  # installed, if needed) once, not on every init-container restart. Changing
  # the ACL policy requires a new versioned marker name.
  if [[ ! -e "${acl_marker}" ]]; then
    ensure_acl_tools
    setfacl -R -m "g:${shared_gid}:rwX" "${target}"
    find "${target}" -type d -exec setfacl -m "d:g:${shared_gid}:rwx" {} +
    touch "${acl_marker}"
  fi

  touch "${marker}"
  chown "1000:${shared_gid}" "${marker}" "${acl_marker}"
  chmod 0664 "${marker}" "${acl_marker}"
}

repository_complete=true
for required_file in "${required_files[@]}"; do
  if [[ ! -s "${required_file}" ]]; then
    repository_complete=false
    break
  fi
done

if [[ -e "${marker}" && -e "${acl_marker}" && "${repository_complete}" == "true" ]]; then
  exit 0
fi

if [[ "${repository_complete}" == "true" ]]; then
  prepare_repository_permissions
  exit 0
fi

cp -a /app/microservice/triton_model_repo/. "${target}/"

for required_file in "${required_files[@]}"; do
  if [[ ! -s "${required_file}" ]]; then
    echo "AOI model repository seed is incomplete: ${required_file}" >&2
    exit 1
  fi
done

prepare_repository_permissions
