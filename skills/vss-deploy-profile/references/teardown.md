# Tear down an existing VSS deployment

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
# If a resolved.yml from a prior deploy exists, prefer it — it
# knows about all compose-profile services that were brought up.
if [ -f "$REPO/deploy/docker/resolved.yml" ]; then
  docker compose -f "$REPO/deploy/docker/resolved.yml" down --remove-orphans
fi

# Catch-all for leftovers a `down` can miss (compose-profile services
# sometimes start under a different project name, or get orphaned).
# Scope by the compose project label FIRST — every VSS container carries
# `com.docker.compose.project=mdx` (the stack sets COMPOSE_PROJECT_NAME=mdx),
# so this is project-scoped and cannot touch unrelated containers:
docker ps -aq --filter "label=com.docker.compose.project=mdx" | xargs -r docker rm -f

# Fallback ONLY for known VSS leftovers that lost the project label (rare —
# e.g. the *-smc set the alerts profile shares on host networking / port 30000,
# which can bind ports the new deploy needs → /sensor/list 502, issue #151).
# Name-patterns reach beyond the project scope, so prefer the label filter
# above; run this only if `docker ps` still shows known VSS leftovers. No
# generic names (e.g. `phoenix`) here — the label filter already catches them,
# and a bare name match risks hitting unrelated containers.
docker ps -a --format '{{.Names}}' \
  | grep -E '^(vss-|mdx-|perception-|rtvi-|alert-|nvstreamer-|sensor-ms-|vst-ingress-|vst-mcp-|vst-file-proxy|centralizedb-|storage-ms-|streamprocessing-ms-|sdr-(http|streamprocessing)-|envoy-(http|streamprocessing)-|rtspserver-ms-|recorder-ms-|replaystream-ms-|livestream-ms-|metropolis-vss-ui)' \
  | xargs -r docker rm -f
```

# Step 0b - Cleanup previous stale state and local logs, data.

Ask user to confirm to clean up before you proceed.

Use the bundled cleanup helper. It clears every directory whose stale state can poison a fresh deploy: kafka logs, elasticsearch data + logs, redis data + log, behavior-learning data, video-analytics API state, calibration toolkit, VST/nvstreamer recordings, and any blueprint-configurator backup files. The same logic `dev-profile.sh` runs internally between deploys.

```bash
# Step 0 (teardown) runs BEFORE Step 1c initializes generated.env,
# so on a fresh checkout / first deploy generated.env doesn't exist
# yet — fall back to the source .env. Once a prior deploy via this
# skill has run, generated.env carries the actually-deployed paths.
PROFILE_DIR="$REPO/deploy/docker/developer-profiles/dev-profile-<profile>"
ENV_FILE="$PROFILE_DIR/generated.env"
[ -f "$ENV_FILE" ] || ENV_FILE="$PROFILE_DIR/.env"

sudo bash "$REPO/deploy/docker/scripts/cleanup_all_datalog.sh" \
    --env-file "$ENV_FILE"
```
