#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Stage the plugin's content assets next to its code:
#   skills/     the skills listed in skills.txt, found by directory name under
#               <skills-src> (flat or grouped layout); a listed skill that is
#               missing fails the staging
#   workspace/  the OpenClaw workspace instruction files from <workspace-src>
# Used by the Dockerfile (from the pinned VSS checkout) and by `npm run stage`
# (from this repo checkout) so both paths produce the same layout.
set -eu
skills_src=${1:?usage: stage-assets.sh <skills-src> <workspace-src>}
workspace_src=${2:?usage: stage-assets.sh <skills-src> <workspace-src>}
here=$(cd "$(dirname "$0")" && pwd)

allow=$(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$here/skills.txt")
test -n "$allow"

rm -rf "$here/skills" "$here/workspace"
mkdir -p "$here/skills"
for name in $allow; do
  src=$(find "$skills_src" -type f -path "*/$name/SKILL.md" | head -n1)
  test -n "$src" || { echo "skills.txt lists '$name' but no $name/SKILL.md under $skills_src" >&2; exit 1; }
  cp -R "$(dirname "$src")" "$here/skills/$name"
done
cp -R "$workspace_src" "$here/workspace"
test -f "$here/workspace/AGENTS.md"
echo "staged $(ls "$here/skills" | wc -l) skills ($(echo $allow | tr "\n" " ")), workspace variants: $(ls -d "$here"/workspace/_*/ 2>/dev/null | xargs -n1 basename | tr '\n' ' ')"
