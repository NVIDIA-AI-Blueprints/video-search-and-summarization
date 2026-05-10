# Tear down an existing VSS deployment

### Step 0 — Tear down any existing deployment

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
if [ -f "$REPO/deployments/resolved.yml" ]; then
  docker compose -f "$REPO/deployments/resolved.yml" down --remove-orphans
fi

# Catch-all: remove every VSS-stack container the dev-profile compose
# files bring up. Without this, leftovers from a prior deploy linger
# (especially the *-smc set, which the alerts compose profile shares
# with the *-dev set on host networking and port 30000) and either:
#   - bind ports the new deploy needs → second sensor-ms fails to bind
#     → /sensor/list returns 502 (issue #151), or
#   - pass the new deploy's container-name health checks while serving
#     stale data from the prior deploy's DB.
# The patterns below cover everything declared in
# deployments/vst/{2d,3d,smc,developer,ps}/, deployments/foundational/,
# deployments/agents/, deployments/proxy/, and the dev-profile-*
# compose files.
docker ps -a --format '{{.Names}}' \
  | grep -E '^(vss-|mdx-|perception-|rtvi-|alert-|nvstreamer-|sensor-ms-|vst-ingress-|vst-mcp-|vst-file-proxy|centralizedb-|storage-ms-|streamprocessing-ms-|sdr-(http|streamprocessing)-|envoy-(http|streamprocessing)-|rtspserver-ms-|recorder-ms-|replaystream-ms-|livestream-ms-|metropolis-vss-ui|phoenix)' \
  | xargs -r docker rm -f
```

If this is the host's first deploy, the `docker compose down`
line is a no-op (exit 0 with no containers to stop) — safe to run
unconditionally.
