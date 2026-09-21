# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Guest-side docker prep used by OpenShell Harbor ``start()``.

Isolation still kills every container and user-defined network so a prior
spec cannot hold ports. Model/apt cache volumes and images stay, so the
next deploy is a weight reload (~55 s) instead of an NGC/HF download
(~20 min) plus a registry pull.
"""

from __future__ import annotations

# Compose names these ``<project>_<vol>``. Match the volume-name suffix so a
# renamed project still keeps the caches. Data volumes (elastic-*, kafka-*,
# phoenix-*, postgres) do not match and are still dropped.
MODEL_CACHE_VOLUME_RE = r"hf-cache|ngc-model-cache|_cache$"

DOCKER_RESET_SCRIPT = rf"""set -uo pipefail
CACHE_RE='{MODEL_CACHE_VOLUME_RE}'
COLD="${{SKILL_EVAL_COLD_DOCKER_RESET:-0}}"
docker info >/dev/null 2>&1 || {{ echo "docker daemon unreachable" >&2; exit 1; }}
cids=$(docker ps -aq); [ -n "$cids" ] && docker rm -f $cids >/dev/null 2>&1 || true
vols=$(docker volume ls -q)
if [ -n "$vols" ]; then
  if [ "$COLD" = "1" ]; then
    docker volume rm -f $vols >/dev/null 2>&1 || true
  else
    drop=$(printf '%s\n' $vols | grep -vE "$CACHE_RE" || true)
    [ -n "$drop" ] && docker volume rm -f $drop >/dev/null 2>&1 || true
  fi
fi
docker network prune -f >/dev/null 2>&1 || true
docker info >/dev/null 2>&1 || {{ echo "docker daemon died during reset" >&2; exit 1; }}
rc=$(docker ps -aq | wc -l | tr -d ' ')
if [ "$COLD" = "1" ]; then
  rv_drop=$(docker volume ls -q | wc -l | tr -d ' ')
else
  rv_drop=$(docker volume ls -q | grep -vE "$CACHE_RE" | wc -l | tr -d ' ')
fi
rn=$(docker network ls --filter type=custom -q | wc -l | tr -d ' ')
if [ "$rc" != "0" ] || [ "$rv_drop" != "0" ] || [ "$rn" != "0" ]; then
  echo "docker runtime reset incomplete: ${{rc}} containers, ${{rv_drop}} non-cache volumes, ${{rn}} user-defined networks remain" >&2
  exit 1
fi
kept=$(docker volume ls -q | grep -E "$CACHE_RE" | wc -l | tr -d ' ')
echo "docker runtime reset OK; images preserved ($(docker images -q | wc -l | tr -d ' ') layers); cache volumes kept ($kept)"
"""

DOCKER_PREWARM_SCRIPT = r"""set -uo pipefail
[ -f "$HOME/.eval_env" ] && . "$HOME/.eval_env"
GOLDEN="$HOME/video-search-and-summarization/deploy/docker/test-scripts/compose-images.golden"
if [ ! -f "$GOLDEN" ]; then
  echo "prewarm skip: $GOLDEN missing"
  exit 0
fi
if [ -n "${NGC_CLI_API_KEY:-}" ]; then
  printf '%s\n' "$NGC_CLI_API_KEY" | docker login nvcr.io -u '$oauthtoken' --password-stdin >/dev/null 2>&1 || true
fi
images=$(awk 'NF>=2 && $1 !~ /industry-profiles\// { print $2 }' "$GOLDEN" \
  | grep '/' \
  | grep -v '[${}]' \
  | grep -v '^nvcr.io/nim/' \
  | sort -u)
[ -n "$images" ] || { echo "prewarm skip: no pullable images"; exit 0; }
missing=""
n_local=0
n_all=0
for img in $images; do
  n_all=$((n_all + 1))
  if docker image inspect "$img" >/dev/null 2>&1; then
    n_local=$((n_local + 1))
  else
    missing="$missing $img"
  fi
done
if [ -z "${missing## }" ]; then
  echo "prewarm OK: $n_all images already local"
  exit 0
fi
n_miss=0
for img in $missing; do
  n_miss=$((n_miss + 1))
done
echo "prewarm: pulling $n_miss missing of $n_all images ($n_local already local)"
# Best-effort: a registry blip must not fail start(); compose can still pull.
printf '%s\n' $missing | xargs -r -P 8 -n 1 docker pull || \
  echo "prewarm: some pulls failed; compose may retry"
echo "prewarm done"
"""
