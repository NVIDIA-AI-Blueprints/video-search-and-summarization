#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Stage the plugin's content assets next to its code:
#   skills/     every skill under <skills-src> (flat or grouped layout) whose
#               SKILL.md frontmatter declares `vss-requires:` — the operation
#               skills, which name the vss CLI command group (or "alerts") a
#               live deployment must serve for them to be usable
#   workspace/  the OpenClaw workspace instruction files from <workspace-src>
# Used by the Dockerfile (from the pinned VSS checkout) and by `npm run stage`
# (from this repo checkout) so both paths produce the same layout.
set -eu
skills_src=${1:?usage: stage-assets.sh <skills-src> <workspace-src>}
workspace_src=${2:?usage: stage-assets.sh <skills-src> <workspace-src>}
here=$(cd "$(dirname "$0")" && pwd)

rm -rf "$here/skills" "$here/workspace"
mkdir -p "$here/skills"
find "$skills_src" -name SKILL.md | sort | while read -r f; do
  # frontmatter only: the block between the leading '---' lines
  if sed -n '2,/^---$/p' "$f" | grep -qE '^[[:space:]]*vss-requires:'; then
    d=$(dirname "$f"); cp -R "$d" "$here/skills/$(basename "$d")"
  fi
done
if [ "$(ls "$here/skills" | wc -l)" -eq 0 ]; then
  echo "no skill under $skills_src declares vss-requires in its SKILL.md frontmatter;" >&2
  echo "the VSS checkout (VSS_REF) predates the operation-skill declarations" >&2
  exit 1
fi
cp -R "$workspace_src" "$here/workspace"
test -f "$here/workspace/AGENTS.md"
echo "staged $(ls "$here/skills" | wc -l) skills ($(ls "$here/skills" | tr "\n" " ")), workspace variants: $(ls -d "$here"/workspace/_*/ 2>/dev/null | xargs -n1 basename | tr '\n' ' ')"
