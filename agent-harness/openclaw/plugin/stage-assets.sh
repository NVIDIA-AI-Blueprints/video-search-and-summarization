#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Stage the plugin's content assets next to its code:
#   skills/     one directory per SKILL.md found under <skills-src> (flat or grouped)
#   workspace/  the OpenClaw workspace instruction files from <workspace-src>
# Used by the Dockerfile (from the pinned VSS checkout) and by `npm run stage`
# (from this repo checkout) so both paths produce the same layout.
set -eu
skills_src=${1:?usage: stage-assets.sh <skills-src> <workspace-src>}
workspace_src=${2:?usage: stage-assets.sh <skills-src> <workspace-src>}
here=$(cd "$(dirname "$0")" && pwd)

rm -rf "$here/skills" "$here/workspace"
mkdir -p "$here/skills"
find "$skills_src" -name SKILL.md -exec dirname {} \; | sort | while read -r d; do
  cp -R "$d" "$here/skills/$(basename "$d")"
done
test "$(ls "$here/skills" | wc -l)" -gt 0
cp -R "$workspace_src" "$here/workspace"
test -f "$here/workspace/AGENTS.md"
echo "staged $(ls "$here/skills" | wc -l) skills, workspace variants: $(ls -d "$here"/workspace/_*/ 2>/dev/null | xargs -n1 basename | tr '\n' ' ')"
