#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Resolve which docs build a Fern workflow run needs.
#
# Usage: REF_NAME=<github.ref_name> [GH_TOKEN=...] resolve_fern_docs_context.sh
#
#   pull-request/<N>   copy-pr-bot mirror of PR N. Looks up the PR's base
#                      branch (needs GH_TOKEN); a base of release/X.Y.Z means
#                      the PR previews as that release.
#   release/X.Y.Z      a release branch (the last path segment is the version,
#                      so release/<product>/X.Y.Z works too).
#   anything else      the Dev build.
#
# Writes to $GITHUB_OUTPUT (stdout when unset):
#   number=<N or empty>     PR number, for the preview id and PR comment
#   release=<X.Y.Z or empty> pass as build_fern_site.py --release

set -euo pipefail

ref_name="${REF_NAME:?REF_NAME is required}"
number=""
branch="$ref_name"

if [[ "$ref_name" =~ ^pull-request/([0-9]+)$ ]]; then
  number="${BASH_REMATCH[1]}"
  branch="$(gh pr view "$number" --repo "${GITHUB_REPOSITORY:?}" --json baseRefName -q .baseRefName)"
else
  echo "::notice::${ref_name} is not a copy-pr-bot mirror branch; no PR comment will be posted"
fi

release=""
if [[ "$branch" =~ ^release/(.+/)?([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
  release="${BASH_REMATCH[2]}"
fi

echo "docs context: ref=${ref_name} pr=${number:-none} release=${release:-none (Dev)}"
{
  echo "number=${number}"
  echo "release=${release}"
} >> "${GITHUB_OUTPUT:-/dev/stdout}"
