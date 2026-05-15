#!/usr/bin/env bash
# Clean up root-owned bind-mount artefacts (./log, ./.wdm-env) without needing
# host sudo. Runs a one-shot Alpine container as root and removes the contents.
#
# Usage:
#   ./clean.sh           # wipe log/ and .wdm-env/ contents (dirs are kept)
#   ./clean.sh --purge   # remove the directories themselves too
#   ./clean.sh --logs    # only clean log/
#   ./clean.sh --env     # only clean .wdm-env/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DO_LOGS=1
DO_ENV=1
PURGE=0
for arg in "$@"; do
  case "$arg" in
    --logs) DO_ENV=0 ;;
    --env)  DO_LOGS=0 ;;
    --purge) PURGE=1 ;;
    -h|--help)
      sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "clean.sh: unknown arg: $arg" >&2; exit 2 ;;
  esac
done

TARGETS=()
[ "$DO_LOGS" = 1 ] && TARGETS+=("/mnt/log")
[ "$DO_ENV"  = 1 ] && TARGETS+=("/mnt/wdm-env")

if [ "$PURGE" = 1 ]; then
  ACTION='rm -rf "$d"'
else
  # Keep the dir, remove children (including dotfiles).
  ACTION='rm -rf "$d"/* "$d"/.[!.]* "$d"/..?* 2>/dev/null || true'
fi

mkdir -p ./log ./.wdm-env

docker run --rm \
  -v "$SCRIPT_DIR/log:/mnt/log" \
  -v "$SCRIPT_DIR/.wdm-env:/mnt/wdm-env" \
  --user 0:0 \
  alpine:3 sh -c "
    set -e
    for d in ${TARGETS[*]}; do
      $ACTION
    done
    echo 'clean.sh: removed contents of: ${TARGETS[*]}'
  "
