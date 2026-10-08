#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# The NemoClaw resources a vss-build-vision-ai build leaves running on the
# host: the dashboard and relay ports, and the sandboxes the registry maps to
# them. Everything tied to the checkout - the harness worktree, Compose, the
# bind-mounted data dirs - stays in references/teardown.md, which also carries
# the prose for this script.
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: teardown-nemoclaw-resources.sh [-h]

Reclaims the dashboard and relay ports - killing whatever holds them and every
forward watchdog, and destroying every sandbox the registry maps to either -
then reports an unwritable relay /tmp log, which only sudo can clear.

Run it before a harness deploy, and again as the first step of teardown, ahead
of the worktree, Compose and data cleanup in references/teardown.md.

Environment:
  NEMOCLAW_DASHBOARD_PORT        default 18789
  NEMOCLAW_DASHBOARD_RELAY_PORT  default 18790

Exit codes:
  0  the ports are free, no sandbox is left, and nothing needs sudo
  1  usage error
  2  a port could not be freed; only sudo can see or clear the holder
  3  a relay log is not writable; only sudo can clear it
  4  a sandbox could not be listed or destroyed, so one may still be running

Each failure prints; when several stand at once the largest code is returned.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unexpected argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

dashboard_port="${NEMOCLAW_DASHBOARD_PORT:-18789}"
relay_port="${NEMOCLAW_DASHBOARD_RELAY_PORT:-18790}"

# A bind test is the only check that needs nothing but python3; lsof and ss
# name the holder but a host may carry neither.
port_free() {
  python3 - "$1" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("", int(sys.argv[1])))
PY
}

# One PID per line. fuser writes them space-separated and ss repeats one per
# socket, so normalise rather than trusting any single tool's shape.
port_pids() {
  local port="$1"
  {
    if command -v lsof >/dev/null 2>&1; then
      lsof -tnP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null
    elif command -v ss >/dev/null 2>&1; then
      ss -Hltnp "sport = :$port" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2
    elif command -v fuser >/dev/null 2>&1; then
      fuser -n tcp "$port" 2>/dev/null
    fi
  } | tr -s '[:space:]' '\n' | grep -E '^[0-9]+$' | sort -u
  return 0
}

# Sandboxes whose dashboard port in `nemoclaw list --json` is one of the given
# ports. The gateway never learns a local port, so that registry is the host's
# only port -> sandbox index, and being recorded state it answers for a
# forward that died or never came up. A host without the CLI has no sandboxes;
# an installed CLI that cannot answer leaves the index unread, which the bind
# checks cannot notice once the listeners are gone.
sandboxes_on() {
  local listing
  command -v nemoclaw >/dev/null 2>&1 || return 0
  if ! listing=$(nemoclaw list --json 2>/dev/null); then
    echo "ERROR: nemoclaw list --json failed; sandboxes on $* are unaccounted for" >&2
    return 1
  fi
  python3 -c '
import json, sys
ports = set(map(int, sys.argv[1:]))
for s in json.load(sys.stdin).get("sandboxes", []):
    if s.get("dashboardPort") in ports: print(s["name"])
' "$@" 2>/dev/null <<<"$listing" && return 0
  echo "ERROR: could not read sandboxes out of nemoclaw list --json" >&2
  return 1
}

# TERM, then KILL whatever survives it.
stop_pids() {
  local pid alive
  [[ "$#" -gt 0 ]] || return 0
  for pid in "$@"; do
    printf '  killing %s: ' "$pid"
    tr '\0' ' ' 2>/dev/null <"/proc/$pid/cmdline"
    echo
  done
  kill -TERM "$@" 2>/dev/null
  for _ in $(seq 50); do
    alive=0
    for pid in "$@"; do kill -0 "$pid" 2>/dev/null && alive=1; done
    [[ "$alive" == 0 ]] && return 0
    sleep 0.1
  done
  kill -KILL "$@" 2>/dev/null
  sleep 0.5
  return 0
}

# Reclaim both ports from whoever holds them and destroy any sandbox
# registered on either. Bring-up recreates the sandbox anyway, so sparing it
# would only leave one whose forward and relay this just killed. A holder
# outside this user's reach is invisible to the scan: that is the exit-2 path,
# and only sudo can clear it.
free_ports() {
  local port p listing rc=0
  local -a pids sandboxes=()

  # Watchdogs join the sweep because they hold no port, so no scan finds
  # them, yet they answer a dying forward with `nemoclaw recover`.
  mapfile -t pids < <(pgrep -f 'dashboard-forward-watchdog\.py'
                      port_pids "$dashboard_port"; port_pids "$relay_port")

  if listing=$(sandboxes_on "$dashboard_port" "$relay_port"); then
    [[ -z "$listing" ]] || mapfile -t sandboxes <<<"$listing"
  else
    (( rc = rc > 4 ? rc : 4 ))
  fi

  stop_pids "${pids[@]}"
  for p in "${sandboxes[@]}"; do
    echo "  destroying sandbox $p"
    nemoclaw "$p" destroy --yes --cleanup-gateway ||
      { echo "ERROR: destroy failed: $p" >&2; (( rc = rc > 4 ? rc : 4 )); }
  done

  for port in "$dashboard_port" "$relay_port"; do
    port_free "$port" && { echo "$port free"; continue; }
    echo "ERROR: $port still held by something this scan cannot see or clear;" \
         "ask for: sudo lsof -i :$port -sTCP:LISTEN -P -n" >&2
    (( rc = rc > 2 ? rc : 2 ))
  done
  return "$rc"
}

# The relay appends to two fixed /tmp paths. A file another user owns is
# unwritable here and only sudo clears it, and the notebook reaches it after
# the stack is up - so name it before the images pull rather than after.
report_blockers() {
  local f rc=0
  for f in /tmp/nemoclaw-dashboard-relay.log /tmp/nemoclaw-dashboard-watchdog.log; do
    [[ -e "$f" && ! -w "$f" ]] || continue
    echo "ERROR: $f belongs to $(stat -c %U "$f") and is not writable; ask for:" \
         "sudo mv -n $f $f.bak" >&2
    rc=3
  done
  return "$rc"
}



status=0
report_blockers || status=$?
free_ports || { rc=$?; (( status = status > rc ? status : rc )); }

exit "$status"
