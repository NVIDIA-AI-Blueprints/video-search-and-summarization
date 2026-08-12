# Data Directory Gate

Run this gate for every stock or delta build once `_builds/<name>/override.env`
exists — a bring-up prerequisite, not a deploy step, so run it whether or not
this run deploys. Any later `docker compose up`, this agent's or hand-run, needs
it: Docker otherwise creates missing bind sources as `root:root`, and stale
dangling symlinks break permission or mount setup. It only prepares the external
`VSS_DATA_DIR`, never the repository tree.

This gate does **not** prepare build-local generated config. If a selected
service needs rendered or scratch files that are specific to this build, stage
them under `_builds/<name>/generated/` during composition, then reference that
path from `override.env` or a minimal patch. Keep `deploy/docker/` read-only:
do not create missing bind sources, rendered configs, scratch directories, or
placeholder files there.

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

## RT-CV model contents

This gate creates the model directories (including `models/rtdetr-its` and
`models/gdino` for `perception-alerts`) and makes them world-writable — the
RT-CV container runs as a non-matching UID and writes generated TensorRT
`.engine` files back into this tree, so the directories must stay `a+rwx`. The
gate does **not** download any model: when the build carries an RT-CV perception
key, the RT-CV container downloads the detector ONNX (and the Search vision
encoder) at first boot (ds-start phase 0) from its mounted `models-download.json`
into this tree and sets their file permissions itself. No host-side staging is
required.

## Existing PostgreSQL failure

If `vss-vios-postgres` already reports corrupted or stale PGDATA, stop the
stack and remove only its resolved Compose volume:

```bash
docker logs vss-vios-postgres
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-vss}"
docker compose -p "${COMPOSE_PROJECT_NAME}" -f "$BUILD_DIR/resolved.yml" down
docker volume ls -q \
  --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
  | grep 'vios_pg_data$' \
  | xargs -r docker volume rm
```

Do not recursively `chown` the data root and do not delete unrelated Docker
volumes.
