# Tear down an existing VSS deployment

Always tear down **by project name** — every profile's `overrides.env` sets
`COMPOSE_PROJECT_NAME=vss` by default, and users may change it when running
multiple profiles on one host. A plain `docker compose down` leaves named volumes
and the project network behind, so target the selected project and pass
`-v --remove-orphans`. Two flavors, depending on whether you keep model caches.

## Full teardown — reclaim the host

Removes containers, the project network, **and all named volumes** (including
multi-GB NIM/RTVI model caches). This is the canonical `dev-profile.sh` teardown.

```bash
docker compose -p "${COMPOSE_PROJECT_NAME:-vss}" down -v --remove-orphans
docker volume ls -q -f dangling=true | xargs -r docker volume rm   # sweep leftovers
```

- **`-p "${COMPOSE_PROJECT_NAME:-vss}"`** removes everything labeled with the
  selected Compose project — robust even if `resolved.yml` is stale or now
  describes a *different* profile. A file-scoped `down` only touches what that
  file currently lists, leaving the rest behind.
- **`-v`** removes named volumes — without it ES / Kafka / Postgres / Milvus data
  **and** NIM/RTVI model caches all survive.
- **`--remove-orphans`** frees the project network from leftover or host-networked
  containers so the network is deleted too.

`-v` drops NIM/RTVI model caches (multi-GB re-download next deploy). To keep them
for an immediate redeploy or profile switch, use the cache-preserving teardown below.

`-v` removes docker **volumes**, but the bind-mounted **on-disk data dirs**
(ES/Kafka/Redis data, behavior-learning, VST/nvstreamer recordings) live on the host
filesystem and survive any teardown — they poison the next run if left. After
**either** flavor, also clear them with the sudo-gated
[Step 0b — on-disk data-dir cleanup](#step-0b--cleanup-previous-stale-state-and-local-logs-data) below.

## Cache-preserving teardown — before a redeploy or profile switch

Removes containers, the project network, and *stale data* volumes (ES indices,
Kafka offsets, Postgres, nvstreamer recordings) but **keeps** model caches so the
next deploy doesn't re-download them.

### Step 0 — Tear down any existing deployment

Ask user to confirm to tear down the deployment before you proceed.

Before every deploy, **always** stop any prior VSS stack. This is
mandatory even if you think the host is clean, and especially when
switching profiles (`base` → `search`, `alerts` verification →
`alerts` real-time, etc.). Compose profile flags only *start* the
services listed under the selected profile — they do NOT stop
services from a previously-active profile, so containers from the
prior deploy linger and pass unrelated container-name checks,
contaminate results, and can bind ports the new deploy needs.

```bash
# Tear down by project name (matches the selected COMPOSE_PROJECT_NAME;
# profiles default to vss in overrides.env). This catches every project-labeled
# container/network regardless of which resolved.yml is on disk. NO -v here — the cache-preserving
# path keeps NIM/RTVI model caches; stale DATA volumes are removed explicitly
# below. --remove-orphans frees + deletes the project network.
docker compose -p "${COMPOSE_PROJECT_NAME:-vss}" down --remove-orphans

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

# `down --remove-orphans` already deletes the project network, such as vss_default.
# Remove it explicitly only as a belt-and-suspenders, by exact project name.
project="${COMPOSE_PROJECT_NAME:-vss}"
docker network rm "${project}_default" 2>/dev/null || true

# `down` (no -v) also leaves every named volume. Remove the stale DATA volumes
# that poison a fresh deploy — ES indices, Kafka offsets, Postgres, logstash
# libs — while KEEPING model caches (rtvi-*, *_cache).
# Names are <project>_<vol>; filter to the selected project and match on the volume-name suffix.
docker volume ls -q \
  --filter "label=com.docker.compose.project=${project}" \
  | grep -E '(elastic-(data|logs)|kafka-data|logstash-libs|phoenix-data|vios_pg_data)$' \
  | xargs -r docker volume rm
```

### Step 0b — Cleanup previous stale state and local logs, data.

Run after **either** teardown flavor above. Removing containers/volumes does **not**
clear the bind-mounted on-disk data dirs; this step does. Ask the user to confirm
before you proceed.

Use the bundled cleanup helper. It clears every directory whose stale state can poison a fresh deploy: kafka logs, elasticsearch data + logs, redis data + log, behavior-learning data, video-analytics API state, calibration toolkit, VST/nvstreamer recordings, and any blueprint-configurator backup files. The same logic `dev-profile.sh` runs internally between deploys.

The cleaner needs **root**. Gate on sudo the same way the SKILL.md pre-flight does:
if sudo is passwordless, run it; otherwise **do not** run it under automation —
surface the command and let the user run it once, then resume.

```bash
# Step 0 (teardown) runs BEFORE Step 1c initializes generated.env,
# so on a fresh checkout / first deploy generated.env doesn't exist
# yet — fall back to overrides.env. Once a prior deploy via this skill has
# run, generated.env carries the actually-deployed override paths.
PROFILE_DIR="$REPO/deploy/docker/developer-profiles/dev-profile-<profile>"
ENV_FILE="$PROFILE_DIR/generated.env"
[ -f "$ENV_FILE" ] || ENV_FILE="$PROFILE_DIR/overrides.env"

# Sudo gate: passwordless sudo → run it; otherwise surface the exact command for
# the user to run once (don't run privileged cleanup under non-interactive sudo).
if sudo -n true 2>/dev/null; then
  sudo bash "$REPO/deploy/docker/scripts/cleanup_all_datalog.sh" --env-file "$ENV_FILE"
else
  echo "sudo needs a password — run this once and confirm, then resume:"
  echo "  sudo bash $REPO/deploy/docker/scripts/cleanup_all_datalog.sh --env-file $ENV_FILE"
fi
```
