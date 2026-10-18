#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Publish the Fern site in the current directory (a build_fern_site.py output
# tree). Retries because Fern sometimes reports "Uploaded N files" and then
# "Missing uploaded file(s) ... Re-run register".
#
# Usage: FERN_TOKEN=... publish_fern_docs.sh [--preview [ID]]
#   (no arguments)   publish production
#   --preview ID     publish, or update in place, the preview named ID
#   --preview        publish a preview under an ID Fern generates
# Preview must be requested explicitly, so an empty ID can never fall through
# to a production publish.
#
# Fern publishes to the single instance in fern/docs.yml; it requires
# --instance only when more than one is configured.
#
# On success writes url=<published URL> to $GITHUB_OUTPUT (stdout when unset)
# and adds it to the step summary. Exits 1 if no attempt published; the caller
# decides whether that fails the job.

set -euo pipefail

ATTEMPTS=3

args=(generate --docs)
case "$#:${1:-}" in
  0:) ;;
  1:--preview) args+=(--preview) ;;
  2:--preview)
    if [[ -z "$2" ]]; then
      echo "--preview ID must not be empty; omit it to let Fern generate one" >&2
      exit 2
    fi
    args+=(--preview --id "$2")
    ;;
  *)
    echo "usage: publish_fern_docs.sh [--preview [ID]]" >&2
    exit 2
    ;;
esac

log="$(mktemp)"
trap 'rm -f "$log"' EXIT

url=""
for ((i = 1; i <= ATTEMPTS; i++)); do
  echo "::group::fern generate attempt $i/$ATTEMPTS"
  if fern "${args[@]}" 2>&1 | tee "$log"; then
    url="$(grep -oP 'Published docs to \K.*(?= \()' "$log" | head -n 1 || true)"
  fi
  echo "::endgroup::"
  if [[ -n "$url" ]]; then
    break
  fi
  echo "fern generate did not publish on attempt $i/$ATTEMPTS"
  if ((i < ATTEMPTS)); then
    sleep $((i * 20))
  fi
done

if [[ -z "$url" ]]; then
  echo "No attempt published after ${ATTEMPTS} tries; see the fern output above." >&2
  exit 1
fi

echo "url=${url}" >> "${GITHUB_OUTPUT:-/dev/stdout}"
echo "### Published: [${url}](${url})" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
