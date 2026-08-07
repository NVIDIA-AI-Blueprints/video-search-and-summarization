#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Resolve the source ref for the daily skills evaluation.
#
# Usage: resolve-skills-eval-ref.sh [YYYYMMDD]
#
# Environment overrides keep the policy easy to adjust without coupling it to
# the workflow:
#   SKILLS_EVAL_REMOTE          Git remote or URL (default: origin)
#   SKILLS_EVAL_TAG_PREFIX      Daily tag prefix (default: nightly-)
#   SKILLS_EVAL_FALLBACK_BRANCH Fallback branch (default: develop)
#   SKILLS_EVAL_TIMEZONE        Timezone used for today's date
#                               (default: UTC, matching the tag publisher)
#
# Prints the commit SHA behind either the exact daily tag or fallback branch.

set -euo pipefail

remote="${SKILLS_EVAL_REMOTE:-origin}"
tag_prefix="${SKILLS_EVAL_TAG_PREFIX:-nightly-}"
fallback_branch="${SKILLS_EVAL_FALLBACK_BRANCH:-develop}"
timezone="${SKILLS_EVAL_TIMEZONE:-UTC}"
date_suffix="${1:-$(TZ="$timezone" date +%Y%m%d)}"

if [[ ! "$date_suffix" =~ ^[0-9]{8}$ ]]; then
  printf 'error: expected date in YYYYMMDD form, got %q\n' "$date_suffix" >&2
  exit 64
fi

tag="${tag_prefix}${date_suffix}"
tag_ref="refs/tags/${tag}"

# Resolve a remote ref to a commit SHA. For an annotated tag, ls-remote returns
# both the tag-object SHA and a ^{} entry; prefer the latter (peeled commit).
resolve_remote_commit() {
  local ref="$1"
  local result direct_sha="" peeled_sha="" sha remote_ref resolved_sha

  result="$(git ls-remote --exit-code "$remote" "$ref" "${ref}^{}")" || return $?
  while read -r sha remote_ref; do
    if [[ "$remote_ref" == "${ref}^{}" ]]; then
      peeled_sha="$sha"
    elif [[ "$remote_ref" == "$ref" ]]; then
      direct_sha="$sha"
    fi
  done <<< "$result"

  resolved_sha="${peeled_sha:-$direct_sha}"
  if [[ ! "$resolved_sha" =~ ^[0-9a-fA-F]{40}$ ]]; then
    printf 'error: remote returned an invalid SHA for %s: %q\n' \
      "$ref" "$resolved_sha" >&2
    return 65
  fi
  printf '%s\n' "$resolved_sha"
}

if resolved_ref="$(resolve_remote_commit "$tag_ref")"; then
  printf 'skills eval ref: using daily tag %s commit %s\n' \
    "$tag" "$resolved_ref" >&2
else
  status=$?
  # git-ls-remote reserves status 2 for a successful remote query with no
  # matching ref. Authentication, transport, and other failures must fail the
  # workflow rather than silently evaluating a different revision.
  if [[ "$status" -ne 2 ]]; then
    printf 'error: could not query %s for %s (git ls-remote exit %d)\n' \
      "$remote" "$tag_ref" "$status" >&2
    exit "$status"
  fi

  fallback_ref="refs/heads/${fallback_branch}"
  if resolved_ref="$(resolve_remote_commit "$fallback_ref")"; then
    :
  else
    status=$?
    printf 'error: could not resolve fallback branch %s from %s (git ls-remote exit %d)\n' \
      "$fallback_branch" "$remote" "$status" >&2
    exit "$status"
  fi

  printf 'skills eval ref: %s is unavailable; using %s commit %s\n' \
    "$tag" "$fallback_branch" "$resolved_ref" >&2
fi

# Both successful paths converge here and emit exactly one final ref.
printf '%s\n' "$resolved_ref"
