#!/bin/sh
# Wait until every container name in docker-workload-containers.txt is running (docker inspect).
# File is produced by wdm-env-from-config from config.yml (WDM_CLUSTER_CONTAINER_NAMES per docker workload).
set -e
LIST="${DOCKER_WORKLOAD_CONTAINERS_FILE:-/wdm-env/docker-workload-containers.txt}"
MAX="${DOCKER_WORKLOAD_WAIT_SECONDS:-300}"

if [ ! -f "$LIST" ] || [ ! -s "$LIST" ]; then
  echo "No docker workload containers listed; skipping wait."
  exit 0
fi

i=0
while [ "$i" -lt "$MAX" ]; do
  ok=true
  while IFS= read -r name || [ -n "$name" ]; do
    case "$name" in ''|\#*) continue ;; esac
    state=$(docker inspect "$name" --format '{{.State.Running}}' 2>/dev/null || echo false)
    if [ "$state" != "true" ]; then
      ok=false
      echo "waiting for container: $name (running=$state)"
      break
    fi
  done <"$LIST"
  if [ "$ok" = true ]; then
    echo "all docker workload containers are running"
    exit 0
  fi
  sleep 2
  i=$((i + 2))
done
echo "timeout after ${MAX}s waiting for docker workload containers" >&2
exit 1
