#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Create a timestamped tarball of 3rd-party npm dependency *source* for packages whose
# JavaScript is loaded at production runtime (Node interpreter), after a full production
# build:
#
#   1. npm ci (includes devDependencies only as build tools — jest, eslint, turbo, etc.)
#   2. turbo run build bundle (same as Dockerfile builder)
#   3. Archive node_modules trees that Next.js copies into .next/standalone (file-traced
#      production server deps: dependencies used to compile the app that are still
#      required when server.js runs) plus root packages required by custom-server.js
#
# Does NOT archive the full workspace node_modules install and does NOT use
# npm ci --omit=dev + tar-all-workspaces (that set ≠ interpreted runtime deps).
#
# Runs inside the Node version from services/ui/.nvmrc (Dockerfile uses the same)
# so host OS/Python packages and unrelated repo trees are never included.
set -euo pipefail

UI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NVMRC="$UI_ROOT/.nvmrc"
if [ ! -f "$NVMRC" ]; then
  echo "ERROR: missing $NVMRC" >&2
  exit 1
fi
NODE_VERSION="$(tr -d '[:space:]' < "$NVMRC")"
NODE_VERSION="${NODE_VERSION#v}"
if [ -z "$NODE_VERSION" ]; then
  echo "ERROR: empty Node version in $NVMRC" >&2
  exit 1
fi
NODE_IMAGE="${NODE_IMAGE:-node:${NODE_VERSION}}"
TMPDIR_PARENT="${TMPDIR:-/tmp}"
WORK_DIR=$(mktemp -d "$TMPDIR_PARENT/vss-ui-third-party-deps.XXXXXX")
cleanup() {
  if [ -n "${WORK_DIR:-}" ] && [ -d "$WORK_DIR" ]; then
    docker run --rm -v "$WORK_DIR:/out" "$NODE_IMAGE" rm -rf /out/staging "/out/${TARNAME:-}" 2>/dev/null || true
    rm -rf "$WORK_DIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT

TS=$(date +%Y%m%d-%H%M%S)
TARNAME="third-party-deps-sources-${TS}.tar.gz"
TAR_PATH="$UI_ROOT/$TARNAME"

echo "Building production dependency tree in $NODE_IMAGE (UI root: $UI_ROOT) ..."

docker run --rm \
  -v "$UI_ROOT:/src:ro" \
  -v "$WORK_DIR:/out" \
  -w /work \
  "$NODE_IMAGE" \
  bash -euo pipefail -c "
    shopt -s nullglob
    cp -a /src/. /work/
    cd /work

    export NEXT_TELEMETRY_DISABLED=1
    export NEXT_BUILD_TRACES=false

    echo 'npm ci ...'
    npm ci

    echo 'turbo run build bundle ...'
    npx turbo run build bundle

    STAGING=/out/staging
    mkdir -p \"\$STAGING\"

    # Standalone node_modules = production deps traced for server execution (not dev tools).
    for app in nemo-agent-toolkit-ui nv-metropolis-bp-vss-ui; do
      standalone_root=\"apps/\${app}/.next/standalone\"
      if [ ! -d \"\$standalone_root\" ]; then
        echo \"ERROR: missing \${standalone_root} after build\" >&2
        exit 1
      fi
      while IFS= read -r -d '' nm_dir; do
        rel=\"\${nm_dir#/work/}\"
        dest=\"\${STAGING}/\${rel}\"
        mkdir -p \"\$(dirname \"\$dest\")\"
        cp -a \"\$nm_dir\" \"\$dest\"
      done < <(find \"\$standalone_root\" -type d -name node_modules -print0)
    done

    # custom-server.js require() at runtime (also copied in Dockerfile runner stage).
    for pkg in \
      next-runtime-env \
      chalk \
      ansi-styles \
      supports-color \
      color-convert \
      color-name \
      has-flag; do
      src=\"node_modules/\${pkg}\"
      if [ ! -d \"\$src\" ]; then
        echo \"ERROR: missing runtime root dependency \${src}\" >&2
        exit 1
      fi
      dest=\"\${STAGING}/\${src}\"
      mkdir -p \"\$(dirname \"\$dest\")\"
      cp -a \"\$src\" \"\$dest\"
    done

    # Fail fast if known non-UI contaminants appear (bad rsync scope, host pollution, etc.).
    if find \"\$STAGING\" \\( -iname '*jupyterlab*' -o -iname '*jupyter-lab*' \\) | grep -q .; then
      echo 'ERROR: jupyterlab found in staged npm sources; tarball scope is wrong.' >&2
      find \"\$STAGING\" \\( -iname '*jupyterlab*' -o -iname '*jupyter-lab*' \\) >&2
      exit 1
    fi

    staged_count=\$(find \"\$STAGING\" -type d -name node_modules | wc -l)
    if [ \"\$staged_count\" -lt 3 ]; then
      echo \"ERROR: expected at least 3 node_modules trees in staging, found \$staged_count\" >&2
      exit 1
    fi

    echo \"Staging \$staged_count node_modules trees under \$STAGING\"
    echo 'Creating /out/${TARNAME} ...'
    # Do not use --exclude=.next here: paths under apps/*/.next/standalone would be dropped.
    tar -czf \"/out/${TARNAME}\" -C \"\$STAGING\" .
  "

docker run --rm -v "$WORK_DIR:/out" "$NODE_IMAGE" \
  chown "$(id -u):$(id -g)" "/out/$TARNAME" 2>/dev/null || true
mv "$WORK_DIR/$TARNAME" "$TAR_PATH"
trap - EXIT
docker run --rm -v "$WORK_DIR:/out" "$NODE_IMAGE" rm -rf /out/staging 2>/dev/null || true
rm -rf "$WORK_DIR"

tar_count=$(tar -tzf "$TAR_PATH" | wc -l)
tar_size=$(du -h "$TAR_PATH" | awk '{print $1}')
echo "Created: $TAR_PATH ($tar_size, $tar_count paths)"
if [ "$tar_count" -lt 1000 ]; then
  echo "ERROR: archive looks too small; check staging paths." >&2
  exit 1
fi
