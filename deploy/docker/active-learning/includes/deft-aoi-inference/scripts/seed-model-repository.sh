#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

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

install_acl_tools() {
  if command -v setfacl >/dev/null 2>&1; then
    return
  fi

  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends acl
  rm -rf /var/lib/apt/lists/*
}

prepare_repository_permissions() {
  chown -R "1000:${shared_gid}" "${target}"
  find "${target}" -type d -exec chmod 2775 {} +
  find "${target}" -type f -exec chmod 0664 {} +

  # The default ACL persists with the named volume. Avoid a network-bound
  # package installation on every ephemeral init-container restart; changing
  # the ACL policy requires a new versioned marker name.
  if [[ ! -e "${acl_marker}" ]]; then
    install_acl_tools
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
