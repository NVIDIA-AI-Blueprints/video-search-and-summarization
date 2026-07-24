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
#       "org": "nvidia"
#     }
#   ]
# }

set -euo pipefail

MODELS_MANIFEST_PATH="${MODELS_MANIFEST_PATH:-/opt/config/models-download.json}"
MODELS_DEST_ROOT="${MODELS_DEST_ROOT:-/opt/storage}"
STORAGE_UID="${STORAGE_UID:-1001}"
STORAGE_GID="${STORAGE_GID:-1001}"
NGC_ORG_DEFAULT="${NGC_ORG_DEFAULT:-nvidia}"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "${TMP_ROOT}"' EXIT

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "ERROR: Required file not found: ${path}" >&2
    exit 1
  fi
}

# NGC ships arch-specific CLI builds and the init image matches the host arch, so on ARM
# hosts (e.g. DGX-Spark/Thor) we must fetch the arm64 build, not the amd64 one. Historically
# the download step ran on the host and inherited whatever CLI the operator installed; moving
# it into the container means we must select the arch ourselves.
ngc_cli_zip_for_arch() {
  case "${1:-$(uname -m)}" in
    aarch64|arm64) echo "ngccli_arm64.zip" ;;
    *)             echo "ngccli_linux.zip" ;;
  esac
}

ensure_ngc_cli() {
  if command -v ngc >/dev/null 2>&1 && command -v jq >/dev/null 2>&1 && command -v envsubst >/dev/null 2>&1; then
    return
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq ca-certificates wget unzip jq gettext-base > /dev/null
  cd /tmp
  local ngc_cli_zip
  ngc_cli_zip="$(ngc_cli_zip_for_arch)"
  wget -q "https://ngc.nvidia.com/downloads/${ngc_cli_zip}" -O ngccli.zip
  unzip -q ngccli.zip && chmod +x ngc-cli/ngc
  export PATH="/tmp/ngc-cli:${PATH}"
  ngc --version
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

  echo "ERROR: Unable to resolve sourcePath '${source_rel}' under '${package_dir}'." >&2
  exit 1
}

expand_manifest_to_json() {
  local expanded_manifest downloads_json
  expanded_manifest="$(envsubst < "$MODELS_MANIFEST_PATH")"

  if ! echo "$expanded_manifest" | jq -e 'type == "array" or (type == "object" and (.downloads | type == "array"))' >/dev/null; then
    echo "ERROR: Manifest must be a JSON array or an object with a 'downloads' array." >&2
    exit 1
  fi

  downloads_json="$(echo "$expanded_manifest" | jq -c 'if type == "array" then . else .downloads end')"

  if ! echo "$downloads_json" | jq -e 'all(.[]; (type == "object") and (.model | type == "string" and length > 0) and (.sourcePath | type == "string" and length > 0) and (.destPath | type == "string" and length > 0))' >/dev/null; then
    echo "ERROR: Each manifest entry must be an object with non-empty model/sourcePath/destPath." >&2
    exit 1
  fi

  echo "$downloads_json" | jq -c --arg default_org "$NGC_ORG_DEFAULT" 'map(.org = (.org // $default_org))'
}

# RT-CV developer and warehouse profiles acquire individual NGC *model* packages
# (nvidia/tao/*) through this manifest-driven path.
# Whole-tree NGC *resource* bundles (e.g. vss-warehouse-app-data) are intentionally NOT
# handled here: warehouse non-model app data is delivered separately.
download_package() {
  local package_ref="$1"
  local org="$2"
  local package_key="${package_ref//[^A-Za-z0-9._-]/_}"
  local package_dir="${TMP_ROOT}/${package_key}"

  # The temp dir is a pure function of the package ref, so its presence means we already
  # pulled this package earlier in the run -- reuse it instead of re-downloading (this is
  # the per-run cache; avoids an associative array so the script runs on bash 3.2+ too).
  if [[ -d "$package_dir" ]]; then
    echo "$package_dir"
    return
  fi

  mkdir -p "$package_dir"
  # This function is called through command substitution. Keep NGC's progress and
  # completion output out of stdout so package_dir contains only the path below.
  ngc registry model download-version "$package_ref" --org "$org" --dest "$package_dir" >&2

  echo "$package_dir"
}

# Marker is derived from the full destination path (path separators flattened) so two
# entries that share a basename but differ by directory never collide on one marker.
tuple_marker() {
  local dest_rel="$1"
  local key="${dest_rel//\//__}"
  echo "${MODELS_DEST_ROOT}/.${key}.done"
}

# Apply the model-tree permission contract (Contract #4) to a single written artifact
# only -- never recursively across the whole shared volume, so engine plans and any
# pre-staged/unrelated files keep their ownership and mode.
apply_artifact_perms() {
  local path="$1"
  chown -R "${STORAGE_UID}:${STORAGE_GID}" "$path"
  if [[ -d "$path" ]]; then
    find "$path" -type d -exec chmod 0777 {} +
    find "$path" -type f -exec chmod 0644 {} +
  else
    chmod 0644 "$path"
    # TensorRT writes engine plans next to the model, so the containing dir needs 0777.
    local parent
    parent="$(dirname "$path")"
    chown "${STORAGE_UID}:${STORAGE_GID}" "$parent"
    chmod 0777 "$parent"
  fi
}

main() {
  require_file "$MODELS_MANIFEST_PATH"
  ensure_ngc_cli
  mkdir -p "$MODELS_DEST_ROOT"
  # Root dir must stay writable by the fixed perception UID (engine plans land here too);
  # scoped to the root node only, not a recursive sweep of pre-existing contents.
  chown "${STORAGE_UID}:${STORAGE_GID}" "$MODELS_DEST_ROOT" 2>/dev/null || true
  chmod 0777 "$MODELS_DEST_ROOT" 2>/dev/null || true

  local manifest_json
  manifest_json="$(expand_manifest_to_json)"

  local downloads_count
  downloads_count="$(echo "$manifest_json" | jq 'length')"

  if [[ "$downloads_count" == "0" ]]; then
    echo "No download entries found in ${MODELS_MANIFEST_PATH}. Nothing to do."
    exit 0
  fi

  local idx

  for (( idx=0; idx<downloads_count; idx++ )); do
    local entry model_ref source_rel dest_rel org
    entry="$(echo "$manifest_json" | jq -c ".[$idx]")"
    model_ref="$(echo "$entry" | jq -r '.model')"
    source_rel="$(echo "$entry" | jq -r '.sourcePath')"
    dest_rel="$(echo "$entry" | jq -r '.destPath')"
    org="$(echo "$entry" | jq -r '.org')"

    local marker dest_abs
    marker="$(tuple_marker "$dest_rel")"
    dest_abs="${MODELS_DEST_ROOT}/${dest_rel}"

    if [[ -f "$marker" && -e "$dest_abs" ]]; then
      echo "Skipping ${model_ref} -> ${dest_rel}; marker present (${marker})."
      continue
    fi

    local package_dir source_abs
    package_dir="$(download_package "$model_ref" "$org")"
    source_abs="$(resolve_source_path "$package_dir" "$source_rel")"

    mkdir -p "$(dirname "$dest_abs")"
    if [[ -d "$source_abs" ]]; then
      mkdir -p "$dest_abs"
      cp -a "${source_abs}/." "${dest_abs}/"
    else
      cp -a "$source_abs" "$dest_abs"
    fi

    apply_artifact_perms "$dest_abs"

    touch "$marker"
    chown "${STORAGE_UID}:${STORAGE_GID}" "$marker"
  done

  echo "Model download init completed for ${downloads_count} manifest entries."
}

main "$@"
