# Tear down an existing VSS deployment

## Contents

- [Default teardown](#default-teardown--clean-project-volumes)
- [Cache-preserving teardown](#cache-preserving-teardown--explicit-opt-in)
- [Bind-mounted data cleanup](#bind-mounted-data-cleanup)

Always tear down **by project name**. Profiles default `COMPOSE_PROJECT_NAME`
to `vss`, and users may change it to run multiple stacks on one host. A plain
`docker compose down` leaves named volumes and the project network behind, so
target the selected project and pass `-v --remove-orphans`.

The default removes all project volumes, including model caches. Use the
cache-preserving path only when the user explicitly asks to keep model caches.

## Default teardown — clean project volumes

Removes containers, the project network, **and all named volumes** (including
multi-GB NIM/RTVI model caches).

```bash
REPO="$(git rev-parse --show-toplevel)"
BUILD_DIR="$REPO/_builds/<name>"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-vss}"
docker compose -p "${COMPOSE_PROJECT_NAME}" -f "$BUILD_DIR/resolved.yml" \
  down -v --remove-orphans

# Remove same-project volumes left by an older resolved model.
docker volume ls -q \
  --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
  | xargs -r docker volume rm
```

- **`-p "${COMPOSE_PROJECT_NAME}"`** targets the selected VSS project. `--remove-orphans` also removes
  same-project containers omitted from the current `resolved.yml`.
- **`-f "$BUILD_DIR/resolved.yml"`** supplies the exact deployed Compose model;
  project name alone is not a Compose configuration.
- **`-v`** removes named volumes — without it ES / Kafka / Postgres / Milvus data
  **and** NIM/RTVI model caches all survive.
- **`--remove-orphans`** frees the project network from leftover or host-networked
  containers so the network is deleted too.
- The label-filtered sweep removes only volumes owned by the selected Compose
  project; never sweep every dangling volume on the host.

`-v` drops NIM/RTVI model caches (multi-GB re-download next deploy). To keep them
for an immediate redeploy or profile switch, use the cache-preserving teardown below.

`-v` removes docker **volumes**, but the bind-mounted **on-disk data dirs**
(ES/Kafka/Redis data, behavior-learning, VST/nvstreamer recordings) live on the host
filesystem and survive any teardown — they poison the next run if left. After
**either** flavor, also clear them with the sudo-gated
[bind-mounted data cleanup](#bind-mounted-data-cleanup) below.

## Cache-preserving teardown — explicit opt-in

Removes containers, the project network, and *stale data* volumes (ES indices,
Kafka offsets, Postgres, nvstreamer recordings) but **keeps** model caches so the
next deploy doesn't re-download them. Do not select this path unless the user
explicitly requests cache preservation.

### Tear down while preserving model caches

Ask user to confirm to tear down the deployment before you proceed.

When cache preservation was explicitly requested, still stop every prior VSS
stack, especially when switching profiles (`base` → `search`, alerts
verification → alerts real-time). Compose profile flags only start selected
services; they do not stop services from a previous deployment.

```bash
# Tear down by the selected project name. This catches every
# same-project container/network regardless of which resolved.yml is on disk.
# NO -v here — the cache-preserving
# path keeps NIM/RTVI model caches; stale DATA volumes are removed explicitly
# below. --remove-orphans frees + deletes the project network.
REPO="$(git rev-parse --show-toplevel)"
BUILD_DIR="$REPO/_builds/<name>"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-vss}"
docker compose -p "${COMPOSE_PROJECT_NAME}" -f "$BUILD_DIR/resolved.yml" \
  down --remove-orphans

# Catch-all: remove every VSS-stack container the dev-profile compose
# files bring up. Without this, leftovers from a prior deploy linger
# (especially the *-smc set, which the alerts compose profile shares
# with the *-dev set on host networking and port 30000) and either:
#   - bind ports the new deploy needs → second sensor-ms fails to bind
#     → /sensor/list returns 502 (issue #151), or
#   - pass the new deploy's container-name health checks while serving
#     stale data from the prior deploy's DB.
# The patterns below cover everything declared under
# deploy/docker/services/ (agent, vios, rtvi, infra, nim, video-summarization, …)
# and deploy/docker/developer-profiles/dev-profile-*/compose files.
docker ps -a --format '{{.Names}}' \
  | grep -E '^(vss-|mdx-|perception-|rtvi-|alert-|nvstreamer-|sensor-ms-|vst-ingress-|vst-mcp-|vst-file-proxy|centralizedb-|storage-ms-|streamprocessing-ms-|sdr-(http|streamprocessing)-|envoy-(http|streamprocessing)-|rtspserver-ms-|recorder-ms-|replaystream-ms-|livestream-ms-|metropolis-vss-ui|phoenix)' \
  | xargs -r docker rm -f

# `down --remove-orphans` already deletes the project network (${COMPOSE_PROJECT_NAME}_default).
# Remove it explicitly only as a belt-and-suspenders, by EXACT name — `-f name=...`
# is a substring match and could catch unrelated networks.
docker network rm "${COMPOSE_PROJECT_NAME}_default" 2>/dev/null || true

# `down` (no -v) also leaves every named volume. Remove the stale DATA volumes
# that poison a fresh deploy — ES indices, Kafka offsets, Postgres, logstash
# libs — while KEEPING model caches (rtvi-*, *_cache).
# Names are <project>_<vol>; match on the volume-name suffix.
docker volume ls -q \
  --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
  | grep -E '(elastic-(data|logs)|kafka-data|logstash-libs|phoenix-data|vios_pg_data)$' \
  | xargs -r docker volume rm
```

## Bind-mounted data cleanup

Run after **either** teardown flavor above. Removing containers/volumes does **not**
clear the bind-mounted on-disk data dirs; this step does. Ask the user to confirm
before you proceed.

Use the bundled cleanup helper. It clears every directory whose stale state can poison a fresh deploy: kafka logs, elasticsearch data + logs, redis data + log, behavior-learning data, video-analytics API state, calibration toolkit, VST/nvstreamer recordings, and any blueprint-configurator backup files.

The cleaner needs **root**. Gate on sudo the same way the SKILL.md pre-flight does:
if sudo is passwordless, run it; otherwise **do not** run it under automation —
surface the command and let the user run it once, then resume.

```bash
# Use the build-specific path and data values that produced resolved.yml.
BUILD_DIR="$REPO/_builds/<name>"
ENV_FILE="$BUILD_DIR/override.env"
[ -f "$ENV_FILE" ] || {
  echo "missing build override: $ENV_FILE" >&2
  exit 1
}

# Sudo gate: passwordless sudo → run it; otherwise surface the exact command for
# the user to run once (don't run privileged cleanup under non-interactive sudo).
if sudo -n true 2>/dev/null; then
  sudo bash "$REPO/deploy/docker/scripts/cleanup_all_datalog.sh" --env-file "$ENV_FILE"
else
  echo "sudo needs a password — run this once and confirm, then resume:"
  echo "  sudo bash $REPO/deploy/docker/scripts/cleanup_all_datalog.sh --env-file $ENV_FILE"
fi
```
