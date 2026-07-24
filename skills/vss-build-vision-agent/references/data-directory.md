# Data Directory Gate

Run this gate for every stock or delta build after writing
`_builds/<name>/override.env` and before `docker compose up`. Docker otherwise
creates missing bind sources as `root:root`, and dangling symlinks left by an
older deployment can make permission or mount setup fail unpredictably.

## Check and create

From the repository root:

```bash
REPO="$(git rev-parse --show-toplevel)"
BUILD_DIR="$REPO/_builds/<name>"
ENV_FILE="$BUILD_DIR/override.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing build override: $ENV_FILE" >&2
  exit 1
fi
if [ "$(grep -c '^VSS_DATA_DIR=' "$ENV_FILE")" -ne 1 ]; then
  echo "override.env must contain exactly one VSS_DATA_DIR" >&2
  exit 1
fi
if [ "$(grep -c '^COMPOSE_PROFILES=' "$ENV_FILE")" -ne 1 ]; then
  echo "override.env must contain exactly one COMPOSE_PROFILES" >&2
  exit 1
fi

DATA="$(sed -n 's/^VSS_DATA_DIR=//p' "$ENV_FILE")"
DATA="${DATA#\"}"
DATA="${DATA%\"}"
DATA="${DATA#\'}"
DATA="${DATA%\'}"
COMPOSE_PROFILES="$(sed -n 's/^COMPOSE_PROFILES=//p' "$ENV_FILE")"
COMPOSE_PROFILES="${COMPOSE_PROFILES#\"}"
COMPOSE_PROFILES="${COMPOSE_PROFILES%\"}"
COMPOSE_PROFILES="${COMPOSE_PROFILES#\'}"
COMPOSE_PROFILES="${COMPOSE_PROFILES%\'}"

case "$DATA" in
  /*) ;;
  *) echo "VSS_DATA_DIR must be one absolute path: $DATA" >&2; exit 1 ;;
esac

if [ -L "$DATA" ] && [ ! -e "$DATA" ]; then
  echo "VSS_DATA_DIR is a dangling symlink: $DATA" >&2
  exit 1
fi
mkdir -p "$DATA"

required=(
  data_log/analytics_cache
  data_log/calibration_toolkit
  data_log/elastic/data
  data_log/elastic/logs
  data_log/kafka
  data_log/redis/data
  data_log/redis/log
  data_log/vss_video_analytics_api
  data_log/vst/clip_storage
  data_log/nvstreamer/vst_data
  agent_eval/dataset
  agent_eval/results
  models
)

case ",$COMPOSE_PROFILES," in
  *,nvstreamer-alerts,*)
    required+=(videos/dev-profile-alerts)
    ;;
esac
case ",$COMPOSE_PROFILES," in
  *,perception-alerts,*)
    required+=(
      models/rtdetr-its
      models/gdino
    )
    ;;
esac
case ",$COMPOSE_PROFILES," in
  *,nvstreamer-lvs,*) required+=(videos/dev-profile-lvs) ;;
esac

broken_links=()
while IFS= read -r -d '' candidate; do
  [ -e "$candidate" ] || broken_links+=("$candidate")
done < <(find "$DATA" -type l -print0)
if [ "${#broken_links[@]}" -gt 0 ]; then
  printf 'Dangling symlink under VSS_DATA_DIR: %s\n' "${broken_links[@]}" >&2
  echo "Remove or repair each link before deployment." >&2
  exit 1
fi

for relative_path in "${required[@]}"; do
  path="$DATA/$relative_path"
  if [ -e "$path" ] && [ ! -d "$path" ]; then
    echo "Required data path is not a directory: $path" >&2
    exit 1
  fi
  mkdir -p "$path"
done

# Containers use different UIDs. Change only the shared data roots; never
# recursively chown VSS_DATA_DIR to the host user.
chmod -R a+rwx "$DATA/data_log" "$DATA/agent_eval" "$DATA/models"
[ ! -d "$DATA/videos" ] || chmod -R a+rwx "$DATA/videos"

for relative_path in "${required[@]}"; do
  path="$DATA/$relative_path"
  if [ ! -d "$path" ] || [ ! -w "$path" ] || [ ! -x "$path" ]; then
    echo "Required data directory is not writable and traversable: $path" >&2
    exit 1
  fi
done
```

Do not silently ignore dangling symlinks. A permission walker may skip one in
best-effort mode, but the stale path can still break a later bind mount,
cleanup, or deployment.

## Existing PostgreSQL failure

If `vss-vios-postgres` already reports corrupted or stale PGDATA, stop the
stack and remove only its resolved Compose volume:

```bash
docker logs vss-vios-postgres
docker compose -f "$BUILD_DIR/resolved.yml" down
docker volume ls -q \
  --filter label=com.docker.compose.project=mdx \
  | grep 'vios_pg_data$' \
  | xargs -r docker volume rm
```

Do not recursively `chown` the data root and do not delete unrelated Docker
volumes.
