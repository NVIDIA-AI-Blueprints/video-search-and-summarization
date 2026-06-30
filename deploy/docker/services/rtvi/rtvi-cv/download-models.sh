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
  if command -v ngc >/dev/null 2>&1; then
    return
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq ca-certificates wget unzip python3 > /dev/null
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

  python3 - "$package_dir" "$source_rel" <<'PY'
import os
import sys

root = sys.argv[1]
source_rel = sys.argv[2]
target = source_rel.strip("/").replace("\\", "/")
target_base = os.path.basename(target)

for dirpath, dirnames, filenames in os.walk(root):
    rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
    rel_dir = "" if rel_dir == "." else rel_dir

    for name in filenames:
        rel = f"{rel_dir}/{name}".strip("/")
        if rel == target or rel.endswith("/" + target) or name == target_base:
            print(os.path.join(root, rel))
            raise SystemExit(0)

    for name in dirnames:
        rel = f"{rel_dir}/{name}".strip("/")
        if rel == target or rel.endswith("/" + target) or name == target_base:
            print(os.path.join(root, rel))
            raise SystemExit(0)

raise SystemExit(1)
PY
}

expand_manifest_to_json() {
  python3 - "$MODELS_MANIFEST_PATH" <<'PY'
import json
import os
import re
import sys

manifest_path = sys.argv[1]
raw = open(manifest_path, "r", encoding="utf-8").read()
expanded = os.path.expandvars(raw)
data = json.loads(expanded)

downloads = data["downloads"] if isinstance(data, dict) else data
if not isinstance(downloads, list):
    raise SystemExit("Manifest must be a list or object with 'downloads' list.")

for idx, item in enumerate(downloads):
    if not isinstance(item, dict):
        raise SystemExit(f"downloads[{idx}] must be an object.")
    for field in ("model", "sourcePath", "destPath"):
        if not item.get(field):
            raise SystemExit(f"downloads[{idx}] missing required field '{field}'.")
    item.setdefault("org", os.environ.get("NGC_ORG_DEFAULT", "nvidia"))

print(json.dumps(downloads))
PY
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
  downloads_count="$(python3 - "$manifest_json" <<'PY'
import json
import sys
print(len(json.loads(sys.argv[1])))
PY
)"

  if [[ "$downloads_count" == "0" ]]; then
    echo "No download entries found in ${MODELS_MANIFEST_PATH}. Nothing to do."
    exit 0
  fi

  declare -A package_cache
  local idx

  for (( idx=0; idx<downloads_count; idx++ )); do
    local model_ref source_rel dest_rel org compat_link
    model_ref="$(python3 - "$manifest_json" "$idx" <<'PY'
import json, sys
d=json.loads(sys.argv[1])[int(sys.argv[2])]
print(d["model"])
PY
)"
    source_rel="$(python3 - "$manifest_json" "$idx" <<'PY'
import json, sys
d=json.loads(sys.argv[1])[int(sys.argv[2])]
print(d["sourcePath"])
PY
)"
    dest_rel="$(python3 - "$manifest_json" "$idx" <<'PY'
import json, sys
d=json.loads(sys.argv[1])[int(sys.argv[2])]
print(d["destPath"])
PY
)"
    org="$(python3 - "$manifest_json" "$idx" <<'PY'
import json, sys
d=json.loads(sys.argv[1])[int(sys.argv[2])]
print(d.get("org","nvidia"))
PY
)"
    compat_link="$(python3 - "$manifest_json" "$idx" <<'PY'
import json, sys
d=json.loads(sys.argv[1])[int(sys.argv[2])]
print(d.get("compatSymlink",""))
PY
)"

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
