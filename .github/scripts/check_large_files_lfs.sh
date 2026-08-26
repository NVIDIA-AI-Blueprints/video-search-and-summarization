#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Fail if any git blob under services/, libs/, or tools/ is larger than 5 MiB
# (GitLab Master regular-file limit for mirror-sync).
#
# Git LFS files are stored in git as ~130-byte pointers, so they pass.
# Working-tree size is ignored (smudged LFS files are large on disk).
# Paths outside those prefixes are ignored.
#
# Usage:
#   bash .github/scripts/check_large_files_lfs.sh            # staged files in scope
#   bash .github/scripts/check_large_files_lfs.sh --all      # all tracked files in scope
#   bash .github/scripts/check_large_files_lfs.sh path ...   # those paths (in-scope only)

set -euo pipefail

MAX_BYTES=$((5 * 1024 * 1024))
SCOPE_PREFIXES=(services/ libs/ tools/)

in_scope() {
  local path="$1"
  local prefix
  for prefix in "${SCOPE_PREFIXES[@]}"; do
    if [[ "$path" == "$prefix"* ]]; then
      return 0
    fi
  done
  return 1
}

ALL=0
PATHS=()
for arg in "$@"; do
  if [[ "$arg" == "--all" ]]; then
    ALL=1
  else
    PATHS+=("$arg")
  fi
done

if [[ "$ALL" -eq 0 && ${#PATHS[@]} -eq 0 ]]; then
  staged=()
  while IFS= read -r -d '' path; do
    if in_scope "$path"; then
      staged+=("$path")
    fi
  done < <(git diff --cached --name-only --diff-filter=ACMR -z || true)
  if [[ ${#staged[@]} -eq 0 ]]; then
    exit 0
  fi
  PATHS=("${staged[@]}")
fi

if [[ "$ALL" -eq 0 && ${#PATHS[@]} -gt 0 ]]; then
  scoped=()
  for path in "${PATHS[@]}"; do
    if in_scope "$path"; then
      scoped+=("$path")
    fi
  done
  if [[ ${#scoped[@]} -eq 0 ]]; then
    exit 0
  fi
  PATHS=("${scoped[@]}")
fi

raw_tmp="$(mktemp)"
sha_path_tmp="$(mktemp)"
size_map_tmp="$(mktemp)"
offenders_tmp="$(mktemp)"
trap 'rm -f "$raw_tmp" "$sha_path_tmp" "$size_map_tmp" "$offenders_tmp"' EXIT

if [[ "$ALL" -eq 1 ]]; then
  git ls-files -z -s -- "${SCOPE_PREFIXES[@]}" > "$raw_tmp"
else
  git ls-files -z -s -- "${PATHS[@]}" > "$raw_tmp"
fi

: > "$sha_path_tmp"
while IFS= read -r -d '' record; do
  meta="${record%%$'\t'*}"
  path="${record#*$'\t'}"
  mode="${meta%% *}"
  rest="${meta#* }"
  sha="${rest%% *}"
  # Skip gitlinks (submodules) and symlinks — GitLab size check is for blobs.
  case "$mode" in
    16*|12*) continue ;;
  esac
  in_scope "$path" || continue
  printf '%s\t%s\n' "$sha" "$path" >> "$sha_path_tmp"
done < "$raw_tmp"

if [[ ! -s "$sha_path_tmp" ]]; then
  exit 0
fi

cut -f1 "$sha_path_tmp" | sort -u | git cat-file --batch-check='%(objectname) %(objecttype) %(objectsize)' \
  | awk '$2 == "blob" { print $1 "\t" $3 }' > "$size_map_tmp"

awk -F '\t' -v max="$MAX_BYTES" '
  NR == FNR { size[$1] = $2; next }
  ($1 in size) && size[$1] > max { print $2 "\t" size[$1] }
' "$size_map_tmp" "$sha_path_tmp" | sort -u > "$offenders_tmp"

if [[ ! -s "$offenders_tmp" ]]; then
  exit 0
fi

awk -F '\t' '
  BEGIN {
    print "ERROR: These files under services/, libs/, or tools/ are regular git blobs larger than 5 MiB." > "/dev/stderr"
    print "Track them with Git LFS." > "/dev/stderr"
    print "" > "/dev/stderr"
  }
  {
    mib = $2 / (1024 * 1024)
    printf "  %s: %.2f MiB (%s bytes)\n", $1, mib, $2 > "/dev/stderr"
  }
  END {
    print "" > "/dev/stderr"
    print "Fix (example):" > "/dev/stderr"
    print "  git lfs install" > "/dev/stderr"
    print "  git lfs track \"<path>\"" > "/dev/stderr"
    print "  git add .gitattributes \"<path>\"" > "/dev/stderr"
    print "Then commit. Do not use git lfs migrate unless you intend to rewrite history." > "/dev/stderr"
  }
' "$offenders_tmp"
exit 1
