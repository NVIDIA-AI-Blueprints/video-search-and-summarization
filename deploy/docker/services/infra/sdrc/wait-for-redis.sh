#!/bin/sh
# Poll Redis until PONG. Host/port come from /wdm-env/wdm.env (written from config.yml).
set -e
if [ -f /wdm-env/wdm.env ]; then
  set -a
  # shellcheck disable=SC1090
  . /wdm-env/wdm.env
  set +a
fi
if [ -z "${WDM_WL_REDIS_SERVER:-}" ]; then
  echo "WDM_WL_REDIS_SERVER missing: add to config.yml under the first enable:true workload, or ensure wdm-env-from-config ran." >&2
  exit 1
fi
port="${WDM_WL_REDIS_PORT:-6379}"
max="${REDIS_WAIT_TIMEOUT_SECONDS:-120}"
i=0
while [ "$i" -lt "$max" ]; do
  if redis-cli -h "$WDM_WL_REDIS_SERVER" -p "$port" ping 2>/dev/null | grep -q PONG; then
    echo "redis is up at ${WDM_WL_REDIS_SERVER}:${port}"
    exit 0
  fi
  echo "waiting for redis at ${WDM_WL_REDIS_SERVER}:${port}..."
  sleep 1
  i=$((i + 1))
done
echo "timeout waiting for redis at ${WDM_WL_REDIS_SERVER}:${port}" >&2
exit 1
