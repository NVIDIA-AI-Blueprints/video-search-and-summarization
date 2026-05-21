#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Create a timestamped tarball of 3rd-party *production* dependency source only
# (no devDependencies). Matches what is needed to build the production Docker image.
set -e

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
TMPDIR_PARENT="${TMPDIR:-/tmp}"
WORK_DIR=$(mktemp -d "$TMPDIR_PARENT/third-party-deps-src.XXXXXX")
trap 'rm -rf "$WORK_DIR"' EXIT

echo "Copying repo (excluding node_modules, .next, .git) to $WORK_DIR ..."
rsync -a \
  --exclude='node_modules' \
  --exclude='.next' \
  --exclude='.git' \
  --exclude='*.tar.gz' \
  --exclude='.turbo' \
  "$REPO_ROOT/" "$WORK_DIR/"

cd "$WORK_DIR"
echo "Running npm ci --omit=dev (production dependencies only) ..."
npm ci --omit=dev

# Only include node_modules paths that exist (npm may hoist; some workspaces may have no local node_modules)
NODE_MODULES_PATHS=(
  node_modules
  apps/nemo-agent-toolkit-ui/node_modules
  apps/nv-metropolis-bp-vss-ui/node_modules
  packages/common/node_modules
  packages/nemo-agent-toolkit-ui/node_modules
  packages/nv-metropolis-bp-vss-ui/all/node_modules
  packages/nv-metropolis-bp-vss-ui/alerts/node_modules
  packages/nv-metropolis-bp-vss-ui/dashboard/node_modules
  packages/nv-metropolis-bp-vss-ui/map/node_modules
  packages/nv-metropolis-bp-vss-ui/search/node_modules
  packages/nv-metropolis-bp-vss-ui/video-management/node_modules
)
TAR_PATHS=()
for p in "${NODE_MODULES_PATHS[@]}"; do
  [ -d "$p" ] && TAR_PATHS+=( "$p" )
done

TS=$(date +%Y%m%d-%H%M%S)
TARNAME="third-party-deps-sources-${TS}.tar.gz"
echo "Creating $TARNAME ..."
tar --exclude='.next' --exclude='*.tsbuildinfo' -czf "$TARNAME" "${TAR_PATHS[@]}"

mv "$TARNAME" "$REPO_ROOT/"
echo "Created: $REPO_ROOT/$TARNAME"
