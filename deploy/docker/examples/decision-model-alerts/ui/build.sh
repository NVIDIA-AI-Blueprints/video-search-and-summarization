#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(git -C "$script_dir" rev-parse --show-toplevel)
temporary_root=$(CDPATH= cd -- "${TMPDIR:-/tmp}" && pwd -P)
build_context=$(mktemp -d "$temporary_root/vss-d1-ui.XXXXXXXX")
trap 'rm -rf -- "$build_context"' EXIT

# Use the checkout's tracked UI sources without local caches or dependencies.
git -C "$repo_root" ls-files -z -- LICENSE services/ui |
    while IFS= read -r -d '' file; do
        if [[ -e "$repo_root/$file" || -L "$repo_root/$file" ]]; then
            printf '%s\0' "$file"
        fi
    done |
    tar -C "$repo_root" --no-recursion --null -T - -cf - |
    tar -C "$build_context" -xf -

patch_file="$script_dir/home-integration.patch"
apply_home_patch() {
    # The patch omits trailing whitespace carried by a few stock context lines.
    GIT_CEILING_DIRECTORIES="$temporary_root" \
        git -C "$build_context" apply --whitespace=fix "$@" "$patch_file"
}
if ! apply_home_patch --check; then
    printf 'The D1 tab patch does not apply to this checkout. Update %s to match the VSS UI.\n' "$patch_file" >&2
    exit 1
fi
apply_home_patch
cp -a -- "$script_dir/overlay/." "$build_context/services/ui/apps/nv-metropolis-bp-vss-ui/"

image="vss-d1-cosmos-ui:${VSS_CONTAINER_TAG:-3.3.0}"
docker build "$@" --file "$build_context/services/ui/Dockerfile" --tag "$image" "$build_context"
