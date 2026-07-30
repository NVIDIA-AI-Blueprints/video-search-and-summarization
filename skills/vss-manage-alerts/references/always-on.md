# Always-On Operation (Workflow G — VLM real-time mode only)

Operational reference for **Workflow G**: checking whether always-on alerting is active, querying its incidents, and troubleshooting missing always-on alerts.

> **Operate, don't author.** This workflow never creates, edits, or deletes always-on rule configuration. Authoring `always_on_rules` entries and rule lifecycle redesign are **out of scope in this pass** — when asked, say so. Enabling/disabling the feature is a **deploy-mode choice** (`-m real-time` vs `-m verification`), not a runtime config edit.

## How always-on works

SDR (sensor discovery) posts camera lifecycle events to Alert Bridge; Alert Bridge fans each `camera_streaming` event out into **one realtime rule per entry** in the always-on rules YAML, targeting that camera's stream on `rtvi-vlm`:

```
SDR event ──POST /api/v1/realtime/always-on──▶ Alert Bridge
  change=camera_streaming  → start every configured always_on_rule for that camera (idempotent per camera_id)
  change=camera_remove     → tear down that camera's always-on rules
```

- Event body (top level): `{source?, alert_type?, created_at?, event: {camera_id, camera_name?, camera_url?, change, …}}` with `change ∈ {camera_streaming, camera_remove}`. `camera_name` and `camera_url` are required on `camera_streaming`, ignored on `camera_remove`.
- Started rules are ordinary realtime rules — their incidents surface through Workflow C's `GET /api/v1/realtime/incidents` like any other.
- The rules live in an **in-memory sidecar**, not the ES-backed rules index: they do not survive a restart by themselves (SDR re-announces cameras) and **may not appear in Workflow D's rules list** — that is expected, not a bug.

## Status — is always-on active?

There is **no `/always-on/health` endpoint** — never invent one. Two signals, in preference order:

1. **Config gate (zero side effects).** The feature is gated by `ALERT_AGENT_ALWAYS_ON` in the alerts profile env (substituted into `alert_agent.always_on` in the mounted verifier config). On a Docker compose deploy via `dev-profile.sh`, real-time (`MODE=2d_vlm`) sets it **true** and uncomments `VST_NOTIFICATION_CONFIG_PATH` (alerts VIOS webhook override); verification (`MODE=2d_cv`) sets it **false** and keeps that path commented (shared VIOS default). Check the env or the resolved config. On Kubernetes, skip these local-file and `docker exec` checks; ask the operator or use the endpoint probe below.
   ```bash
   if [ "${DEPLOYMENT_KIND:-docker}" != "kubernetes" ]; then
     grep -E '^ALERT_AGENT_ALWAYS_ON=' deploy/docker/developer-profiles/dev-profile-alerts/generated.env 2>/dev/null \
       || grep -E '^ALERT_AGENT_ALWAYS_ON=' deploy/docker/developer-profiles/dev-profile-alerts/overrides.env
     # Real-time: uncommented override. Verification: line stays commented (#VST_NOTIFICATION_CONFIG_PATH=...).
     grep -E '^#?[[:space:]]*VST_NOTIFICATION_CONFIG_PATH=' deploy/docker/developer-profiles/dev-profile-alerts/generated.env 2>/dev/null \
       || grep -E '^#?[[:space:]]*VST_NOTIFICATION_CONFIG_PATH=' deploy/docker/developer-profiles/dev-profile-alerts/overrides.env
     docker exec vss-alert-bridge sh -c 'grep -A1 -E "^\s*always_on" /app/runtime/config.yml' 2>/dev/null
   fi
   ```
2. **Endpoint probe (benign POST).** A `camera_remove` for a nonexistent camera is a no-op when enabled and returns the gate response when disabled. Use `$AB` from the parent skill (Kubernetes `${VSS_PUBLIC_URL}/alert-bridge`, Docker `http://${HOST_IP}:9080`):
   ```bash
   : "${AB:?Resolve AB from vss-manage-alerts Deployment prerequisite}"
   curl -s -o /tmp/ao.json -w '%{http_code}\n' -X POST "$AB/api/v1/realtime/always-on" \
     -H 'Content-Type: application/json' \
     -d '{"source":"vst","event":{"camera_id":"00000000-0000-0000-0000-0000000000aa","change":"camera_remove"}}'
   # 503 + {"reason":"ALWAYS_ON_DISABLED"} → feature off (verification / 2d_cv default)
   # 200-range / REMOVE_* reason           → feature on (real-time / 2d_vlm)
   ```

Report the state you actually observed. If the user wants it enabled on a Docker verification deploy, tell them to redeploy with `/vss-deploy-profile -p alerts -m real-time` (do **not** hand-edit config and restart). That redeploy sets `ALERT_AGENT_ALWAYS_ON=true` and uncomments `VST_NOTIFICATION_CONFIG_PATH`. On Kubernetes, describe the operator-managed enable path as information only: set `alert_agent.always_on: true`, provide a non-empty `always_on_rules` YAML, and restart `alert-bridge` so it validates and loads both the gate and the rules; do **not** perform those changes.

## Response envelope & reason codes

Every response: `{"reason": "<REASON>", "status": "HTTP/1.1 <code> <phrase>", "details": [...]?}` — `details` (one entry per rule, with per-rule `status`/`result`) appears only when the service fanned out across rules.

| `reason` | Meaning |
|---|---|
| `ALWAYS_ON_DISABLED` | Feature gate `alert_agent.always_on` is off (HTTP 503) |
| `INVALID_PAYLOAD` | Event body failed validation (HTTP 422, custom shape — not FastAPI's `detail`) |
| `CONFIG_ERROR` | Rules YAML missing/malformed (also raised at startup validation) |
| `STREAM_ADD_SUCCESS` | All configured rules started for the camera |
| `STREAM_ADD_PARTIAL_SUCCESS` | Some rules started, some failed — see `details` |
| `STREAM_ADD_FAILED` | No rule could be started — see `details` |
| `STREAM_ADD_ALREADY_ACTIVE` | Rules already running for this `camera_id` (idempotent short-circuit) |
| `STREAM_REMOVE_SUCCESS` / `STREAM_REMOVE_FAILED` | Teardown outcome for `camera_remove` |

## Rules YAML (read-only knowledge)

Resolution order (first match wins): `$ALWAYS_ON_RULES_CONFIG` → `./realtime-config.yaml` → `./realtime-config-sample.yaml` (sample ships at `services/alert/realtime-config-sample.yaml`). In Docker, the mounted source is rendered through `env-substitute.py` to `/app/runtime/realtime-config.yml`, and `ALWAYS_ON_RULES_CONFIG` points at that rendered file. This lets a rule use `model: "${VLM_NAME}"` so it follows the deployment-selected local, alternate, or remote VLM.

Shape: top-level `always_on_rules:` — a non-empty list with unique `rule_id`s; each entry carries `rule_id`, `alert_type`, optional `description`, and `always_on_params` where `prompt` / `system_prompt` / `model` are **required** and `live_stream_url` / `alert_type` / `sensor_name` are **derived** from the camera event (setting them is a config error). Omitting `model` does not select an available model; it fails startup validation. The rendered YAML is validated at Alert Bridge startup when the gate is on.

## Troubleshooting — "why aren't always-on alerts appearing?"

Walk the chain top-down; stop at the first broken link and report it:

1. **Feature gate** — `ALERT_AGENT_ALWAYS_ON=false` / `alert_agent.always_on: false` (verification / 2d_cv) explains everything; see Status above. Real-time deploys should show `ALERT_AGENT_ALWAYS_ON=true` and an **uncommented** `VST_NOTIFICATION_CONFIG_PATH` pointing at the alerts VIOS webhook config.
2. **Rules YAML** — resolves via the chain above and validated at boot; a `CONFIG_ERROR` in `alert-bridge` startup logs means no rule can ever start: `docker logs vss-alert-bridge 2>&1 | grep -i "always"`.
3. **SDR events reaching Alert Bridge** — the same logs show each incoming `POST /api/v1/realtime/always-on`; no log lines = SDR never announced the camera (VIOS/SDR side, hand off to `vss-manage-video-io-storage`).
4. **Stream registered on rtvi-vlm** — the fan-out `details` entries carry per-rule upstream errors; a 502-class `error` means `rtvi-vlm` rejected the stream.
5. **Incidents** — finally, query Workflow C (`GET /api/v1/realtime/incidents`, scope by camera/time). Empty with all links healthy = nothing matched the rule prompts yet; report that grounded, don't fabricate.

Never "fix" a broken link by authoring config or onboarding sensors the user didn't ask for — report the diagnosis and the enable path.
