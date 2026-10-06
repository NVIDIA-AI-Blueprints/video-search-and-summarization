# Deploy Readiness Gate

`docker compose up -d` returns when containers are *created*, not when
the processes inside have finished initialising. Cold deploys
(first-time NIM image pulls, model warmup, vLLM CUDA-graph capture)
can legitimately take 10–20 min. Use this gate before declaring a
deploy "done".

## Step 1 — wait for the compose project to settle

**Gate 0 first — confirm a non-zero, expected container count and healthy
container states together.** A state-only `ps --format json | jq ...` filter
passes *vacuously* when no services started (the missing env-file pair / unset
`COMPOSE_PROFILES` failure mode — `up -d` exits 0 with "no service selected"),
so keep the count guard in the same snippet as the state guard:

```bash
BUILD_DIR="_builds/<name>"
expected=$(docker compose -f "$BUILD_DIR/resolved.yml" config --services | wc -l)
actual=$(docker compose -f "$BUILD_DIR/resolved.yml" ps -q | wc -l)
if [ "$expected" -le 0 ] || [ "$actual" -le 0 ] || [ "$actual" -lt "$expected" ]; then
  echo "FAIL: expected $expected services, got $actual — inspect resolved.yml" >&2
  exit 1
fi

# docker compose 2.21+ emits NDJSON (one bare object per line) from
# `ps --format json`, not a JSON array — so no `.[]` here; jq's default
# input loop already iterates each line. The filter accepts only
# `running` and `exited 0`; everything else (restarting, unhealthy,
# exited with non-zero code) is a failure.
bad=$(
  docker compose -f "$BUILD_DIR/resolved.yml" ps --format json \
    | jq -r 'select((.State == "running" or (.State == "exited" and .ExitCode == 0)) | not)
             | "\(.Name)\t\(.State)\texit=\(.ExitCode // "?")\t\(.Status)"'
)
if [ -n "$bad" ]; then
  echo "FAIL: containers not running or cleanly exited:" >&2
  printf '%s\n' "$bad" >&2
  exit 1
fi
```

Every container must be either `running` or cleanly `exited 0`. One-shot init
jobs (e.g. `vss-kibana-init`) legitimately exit 0 and stay exited, which is
fine. Anything `restarting`, `unhealthy`, or `exited <N≠0>` is a deploy
failure even though `up -d` returned 0.

> **Warehouse needs a data-plane check, not just Gate 0.** Every container can
> report `Up` while zero streams are processed, and Gate 0 cannot see it. Run the
> liveness checks in [`profiles/warehouse.md`](profiles/warehouse.md) before
> declaring a warehouse deploy done.

## Step 2 — probe the profile's documented readiness endpoints

Container state alone isn't enough — the processes inside may still be
importing modules, loading models, and binding ports. The matching file under
`profiles/` lists the endpoints that must be reachable for that profile (agent
REST API, UI, inference NIMs, etc., on the ports the profile actually opens).
Run those `curl` checks with a generous deadline (15 min is reasonable for cold
NIM warmup) and only declare the deploy done once every documented endpoint
returns the expected success exit code.

**Agent gate — only when the build includes the VSS Agent.** Stock profiles run
`vss-agent`, so it must answer on `:8000/health`; a headless delta prunes it (no
`:8000` bound at all), so the probe does not apply. Gate on the resolved service
set rather than assuming every build has an agent:

```bash
if docker compose -f "$BUILD_DIR/resolved.yml" config --services | grep -qx vss-agent; then
  curl -sf --max-time 15 http://localhost:8000/health >/dev/null && echo "agent OK"
fi
```

When it applies, keep this separate from Step 1: a `running` agent container
does not mean the NAT-serve process is listening — it can be up while `:8000`
never bound (config error, unreachable model endpoint), and Step 1 would still
pass.

**Analytics gates — derive each one from the resolved graph.** Alerts,
NemoClaw, LVS, and host-side CLI selection do not imply VA-MCP. Probe only the
service keys serialized into `resolved.yml`:

```bash
services=$(docker compose -f "$BUILD_DIR/resolved.yml" config --services)

if grep -qx vss-video-analytics-api <<<"$services"; then
  curl -sf --max-time 15 \
    "http://${HOST_IP}:${VIDEO_ANALYTICS_API_HOST_PORT:-8081}/livez" \
    >/dev/null && echo "video analytics API OK"
fi
if grep -qx alert-bridge <<<"$services"; then
  curl -sf --max-time 15 \
    "http://${HOST_IP}:${ALERT_BRIDGE_HOST_PORT:-9080}/health" \
    >/dev/null && echo "alert bridge OK"
fi
if grep -qx vss-va-mcp <<<"$services"; then
  curl -sf --max-time 15 \
    "http://${HOST_IP}:${VSS_VA_MCP_HOST_PORT:-9901}/health" \
    >/dev/null && echo "legacy VA-MCP OK"
fi
```

The `:9901` probe is therefore present only for an explicitly selected legacy
MCP workflow. A CLI-based Alerts build probes the Video Analytics API and Alert
Bridge instead.

## Record the deployed version and check the Alert API contract

After the readiness endpoints answer, run the provenance gate below. Keep the
JSON alongside `resolved.yml`; for a skill eval, also copy it into
`/logs/artifacts/deployment-provenance.json` so the trace retains it even if a
later operational step fails. It records the worker checkout SHA, a hash of the
resolved model, Compose service keys, container names, actual image IDs, registry
digests, and available source/version labels. It never copies container
environments or API response bodies. An eval's `PR_HEAD_SHA`, when supplied,
must match the worker checkout.

```bash
services=$(docker compose -f "$BUILD_DIR/resolved.yml" config --services)
probe_args=()
if grep -qx alert-bridge <<<"$services"; then
  probe_args+=(--alert-direct-origin "http://${HOST_IP}:${ALERT_BRIDGE_HOST_PORT:-9080}")
  if grep -qx vss-haproxy-ingress <<<"$services"; then
    probe_args+=(--alert-ingress-origin "http://${HOST_IP}:${HAPROXY_HOST_PORT:-7777}/alert-bridge")
  fi
fi
python3 "$REPO/skills/vss-build-vision-ai/scripts/check_deployment_provenance.py" \
  --repo-root "$REPO" --resolved "$BUILD_DIR/resolved.yml" \
  --output "$BUILD_DIR/deployment-provenance.json" "${probe_args[@]}"
```

Use the host's documented HAProxy origin for this routing check; published
Brev secure-link authentication is a separate browser-access check.
For an Alert build, this gate requires `GET /api/v1/verification/config` to
return the current `{status, configs, count}` contract; an empty `configs` list
is valid. A health check alone does not prove this API exists. A direct failure
needs image provenance and service-log investigation; a direct success with an
ingress failure points to routing. Retain the report on either failure and stop
before onboarding or operational work.

A moving tag such as `develop-latest` is not proof that a running image matches
the checkout or a release. Missing source labels mean source identity is unknown;
do not infer it from the tag. Compare the recorded digest/labels with the intended
build. Likewise, `vss-behavior-analytics-alerts` is the current Alerts Compose
**service key**, while `vss-behavior-analytics` is its **container name**; that
difference does not establish an old VSS release.

## Step 3 — triage slow containers

If any probe times out, dump `docker compose ps` and
`docker compose logs --tail 100 <slow-service>` and report the slow
container. Never claim success on a half-warm stack.

## Step 4 — when a check must prove the data plane is advancing

Steps 1–3 prove services are *up*; some runtime checks additionally require
proving records are *flowing* — e.g. a topic's end offset increases once a
source is processed. Measure that with **before/after end-offset snapshots**,
not a consumer tail. Snapshot each named topic's end offset, run the workload,
snapshot again, and confirm every one increased:

```bash
# Exec into the kafka service (targeted via the build's resolved.yml) and use the
# broker's INTERNAL listener localhost:29092 — the same bootstrap the shipped
# healthcheck uses; no host port and nothing to read from resolved.yml:
docker compose -f "$BUILD_DIR/resolved.yml" exec -T kafka \
  kafka-get-offsets --bootstrap-server localhost:29092 --topic "$TOPIC" --time -1
# Fallbacks if that binary is absent: `kafka-run-class kafka.tools.GetOffsetShell`,
# or `kafka-consumer-groups --describe` and read each partition's LOG-END-OFFSET.
```

**Pick `$TOPIC` from the build's perception mode — the topic a healthy stack
advances is not the same one in every build.** `mdx-raw` is the 2D detector's
output; on a warehouse `MODE=3d` build it stays flat at `0` forever while the
stack is perfectly healthy, so snapshotting it reports a dead data plane on a
working deployment. There is no universal topic to poll:

| Build | Perception topic | Analytics topics |
|---|---|---|
| Warehouse `MODE=2d`, Alerts, Search (RT-CV 2D) | `mdx-raw` | `mdx-behavior`, `mdx-incidents` |
| Warehouse `MODE=3d` (Sparse4D / MV3DT) | `mdx-bev` | `mdx-behavior`, `mdx-incidents` |

Confirm rather than assume: `kafka-topics --bootstrap-server localhost:29092
--list` shows every topic the build created (a warehouse stack creates ~20
regardless of mode, so presence proves nothing), and only the offset delta
proves which one is advancing. When a check names specific topics, snapshot
each of those.

`kafka-console-consumer` only shows that *some* messages exist; it gives no
stable before/after end-offset delta, so it cannot prove a topic advanced for a
given run. Do the snapshot-run-snapshot comparison for every topic a check
names. Prove Elasticsearch landing at the coarsest sufficient level — the offset
delta, or a plain `_count` on the index pattern — and don't over-probe per-record
nested fields: `objects` in `mdx-raw-*` is `nested`, so a top-level
`{"exists":{"field":"objects"}}` reads 0 on healthy data.
