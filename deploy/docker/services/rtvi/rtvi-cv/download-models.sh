#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Canonical manifest-driven model download init script for RTVI CV.
# Manifest schema (JSON):
# {
#   "downloads": [
#     {
#       "model": "nvidia/tao/rtdetr_2d_warehouse:deployable_rn50_v1.0.2",
#       "sourcePath": "rtdetr_2d_warehouse_vdeployable_rn50_v1.0.2/rtdetr_warehouse_v1.0.2.fp16.onnx",
#       "destPath": "rtdetr_warehouse_v1.0.2.fp16.onnx",
#       "org": "nvidia",
#       "compatSymlink": "optional-link-name"
#     }
#   ]
# }

set -euo pipefail

MODELS_MANIFEST_PATH="${MODELS_MANIFEST_PATH:-/opt/config/models-download.yaml}"
MODELS_DEST_ROOT="${MODELS_DEST_ROOT:-/opt/storage}"
STORAGE_UID="${STORAGE_UID:-1001}"
STORAGE_GID="${STORAGE_GID:-1001}"
NGC_ORG_DEFAULT="${NGC_ORG_DEFAULT:-nvidia}"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "${TMP_ROOT}"' EXIT

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "ERROR: Required file not found: ${path}"
    exit 1
  fi
}

ensure_ngc_cli() {
  if command -v ngc >/dev/null 2>&1 && command -v jq >/dev/null 2>&1 && command -v envsubst >/dev/null 2>&1; then
    return
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq ca-certificates wget unzip jq gettext-base > /dev/null
  cd /tmp
  wget -q https://ngc.nvidia.com/downloads/ngccli_linux.zip -O ngccli_linux.zip
  unzip -q ngccli_linux.zip && chmod +x ngc-cli/ngc
  export PATH="/tmp/ngc-cli:${PATH}"
  ngc --version
}

detect_ref_kind() {
  local package_ref="$1"
  if [[ "$package_ref" == nvidia/vss-warehouse/* ]]; then
    echo "resource"
  else
    echo "model"
  fi
}

resolve_source_path() {
  local package_dir="$1"
  local source_rel="$2"

  if [[ -e "${package_dir}/${source_rel}" ]]; then
    echo "${package_dir}/${source_rel}"
    return
  fi

  local target_base candidate
  target_base="$(basename "$source_rel")"
  candidate="$(find "$package_dir" -mindepth 1 \( -path "*/${source_rel}" -o -name "${target_base}" \) | head -n1)"
  if [[ -n "$candidate" ]]; then
    echo "$candidate"
    return
  fi

  echo "ERROR: Unable to resolve sourcePath '${source_rel}' under '${package_dir}'."
  exit 1
}

expand_manifest_to_json() {
  local expanded_manifest downloads_json
  expanded_manifest="$(envsubst < "$MODELS_MANIFEST_PATH")"

  if ! echo "$expanded_manifest" | jq -e 'type == "array" or (type == "object" and (.downloads | type == "array"))' >/dev/null; then
    echo "ERROR: Manifest must be a JSON array or an object with a 'downloads' array."
    exit 1
  fi

  downloads_json="$(echo "$expanded_manifest" | jq -c 'if type == "array" then . else .downloads end')"

  if ! echo "$downloads_json" | jq -e 'all(.[]; (type == "object") and (.model | type == "string" and length > 0) and (.sourcePath | type == "string" and length > 0) and (.destPath | type == "string" and length > 0))' >/dev/null; then
    echo "ERROR: Each manifest entry must be an object with non-empty model/sourcePath/destPath."
    exit 1
  fi

  echo "$downloads_json" | jq -c 'map(.org = (.org // env.NGC_ORG_DEFAULT))'
}

download_package() {
  local package_ref="$1"
  local org="$2"
  local package_key="${package_ref//[^A-Za-z0-9._-]/_}"
  local package_dir="${TMP_ROOT}/${package_key}"
  mkdir -p "$package_dir"

  local kind
  kind="$(detect_ref_kind "$package_ref")"

  if [[ "$kind" == "resource" ]]; then
    ngc registry resource download-version "$package_ref" --org "$org" --dest "$package_dir"
    shopt -s nullglob globstar
    for archive in "${package_dir}"/**/*.tar.gz "${package_dir}"/**/*.tgz; do
      tar -xzf "$archive" -C "$package_dir"
    done
    shopt -u nullglob globstar
  else
    ngc registry model download-version "$package_ref" --org "$org" --dest "$package_dir"
  fi

  echo "$package_dir"
}

tuple_marker() {
  local dest_rel="$1"
  local base
  base="$(basename "$dest_rel")"
  if [[ "$base" == *.* ]]; then
    base="${base%.*}"
  fi
  echo "${MODELS_DEST_ROOT}/.${base}.done"
}

main() {
  require_file "$MODELS_MANIFEST_PATH"
  ensure_ngc_cli
  mkdir -p "$MODELS_DEST_ROOT"

  local manifest_json
  manifest_json="$(expand_manifest_to_json)"

  local downloads_count
  downloads_count="$(echo "$manifest_json" | jq 'length')"

  if [[ "$downloads_count" == "0" ]]; then
    echo "No download entries found in ${MODELS_MANIFEST_PATH}. Nothing to do."
    exit 0
  fi

  declare -A package_cache
  local idx

  for (( idx=0; idx<downloads_count; idx++ )); do
    local entry model_ref source_rel dest_rel org compat_link
    entry="$(echo "$manifest_json" | jq -c ".[$idx]")"
    model_ref="$(echo "$entry" | jq -r '.model')"
    source_rel="$(echo "$entry" | jq -r '.sourcePath')"
    dest_rel="$(echo "$entry" | jq -r '.destPath')"
    org="$(echo "$entry" | jq -r '.org')"
    compat_link="$(echo "$entry" | jq -r '.compatSymlink // empty')"

    local marker dest_abs
    marker="$(tuple_marker "$dest_rel")"
    dest_abs="${MODELS_DEST_ROOT}/${dest_rel}"

    if [[ -f "$marker" && -e "$dest_abs" ]]; then
      echo "Skipping ${model_ref} -> ${dest_rel}; marker present (${marker})."
      continue
    fi

    if [[ -z "${package_cache[$model_ref]:-}" ]]; then
      package_cache[$model_ref]="$(download_package "$model_ref" "$org")"
    fi
    local package_dir source_abs
    package_dir="${package_cache[$model_ref]}"
    source_abs="$(resolve_source_path "$package_dir" "$source_rel")"

    mkdir -p "$(dirname "$dest_abs")"
    if [[ -d "$source_abs" ]]; then
      mkdir -p "$dest_abs"
      cp -a "${source_abs}/." "${dest_abs}/"
    else
      cp -a "$source_abs" "$dest_abs"
    fi

    if [[ -n "$compat_link" ]]; then
      local compat_path
      compat_path="${MODELS_DEST_ROOT}/${compat_link}"
      if [[ ! -e "$compat_path" ]]; then
        ln -s "$(basename "$dest_rel")" "$compat_path"
      fi
    fi

    touch "$marker"
  done

  chown -R "${STORAGE_UID}:${STORAGE_GID}" "${MODELS_DEST_ROOT}"
  find "${MODELS_DEST_ROOT}" -type d -exec chmod 0777 {} +
  find "${MODELS_DEST_ROOT}" -type f -exec chmod 0644 {} +

  echo "Model download init completed for ${downloads_count} manifest entries."
}

main "$@"
