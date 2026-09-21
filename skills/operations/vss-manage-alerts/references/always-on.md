# Always-On Operation (Workflow G — VLM real-time mode only)

Operational reference for **Workflow G**: checking whether always-on alerting is active, querying its incidents, and troubleshooting missing always-on alerts.

> **Operate, don't author.** This workflow never creates, edits, or deletes always-on rule configuration. Authoring `always_on_rules` entries and rule lifecycle redesign are **out of scope in this pass** — when asked, say so. Enabling/disabling the feature is a **deploy-mode choice** (`-m real-time` vs `-m verification`), not a runtime config edit.

> **`camera_remove` against a real camera is a stop, not an operation this workflow performs on its own.** It tears down that camera's always-on rules, so it is gated by the parent skill's Workflow D stop protocol: ask the yes/no question naming the camera, and send it only after an explicit "yes". The status probe below is exempt only because its `camera_id` is a sentinel that matches no camera. A "stop monitoring on `<sensor>`" request routes to **Workflow D**; it never gets served by posting `camera_remove` here. Carrying out a confirmed stop is [Stopping a camera's always-on rules](#stopping-a-cameras-always-on-rules-after-the-user-confirms) below.

## How always-on works

VIOS posts camera lifecycle events to Alert Bridge via the real-time webhook config (`notification_config_2d_vlm.json` → `POST /api/v1/realtime/always-on`). Alert Bridge fans each `camera_streaming` event out into **one realtime rule per entry** in the always-on rules YAML, targeting that camera's stream on `rtvi-vlm`:

```
VIOS webhook ──POST /api/v1/realtime/always-on──▶ Alert Bridge
  change=camera_streaming  → start every configured always_on_rule for that camera (idempotent per camera_id)
  change=camera_remove     → tear down that camera's always-on rules
```

- Event body (top level): `{source?, alert_type?, created_at?, event: {camera_id, camera_name?, camera_url?, change, …}}` with `change ∈ {camera_streaming, camera_remove}`. `camera_name` and `camera_url` are required on `camera_streaming`, ignored on `camera_remove`. VIOS sends the raw VST envelope; body templating is not used.
- Started rules are ordinary realtime rules — their incidents surface through Workflow C's `GET /api/v1/realtime/incidents` like any other.
- The rules live in an **in-memory sidecar**, not the ES-backed rules index, and **may not appear in Workflow D's rules list** — that is expected, not a bug.
- <a id="restart-gap"></a>**An Alert Bridge restart silently unmonitors every camera, and nothing replays the rules.** VIOS emits `camera_streaming` only on a sensor *status transition*, and its webhook `retry` block re-delivers an event whose delivery **failed** — it never re-sends one already delivered. So a camera that stays online across the restart gets no new event and is left unmonitored indefinitely. Only the next lifecycle transition re-arms it: an offline→online reconnect, or an operator taking the stream out of VIOS and re-registering it. Never tell an operator monitoring resumed on its own — verify with the fan-out log lines for that `camera_id`.

## Status — is always-on active?

There is **no `/always-on/health` endpoint** — never invent one. Two signals, in preference order:

1. **Config gate (zero side effects).** The feature is gated by `ALERT_AGENT_ALWAYS_ON` in the alerts profile env (substituted into `alert_agent.always_on` in the mounted verifier config). On a Docker compose deploy via `dev-profile.sh`, real-time (`MODE=2d_vlm`) sets it **true**; verification (`MODE=2d_cv`) sets it **false**. Check the env or the resolved config. On Kubernetes, skip these local-file and `docker exec` checks; ask the operator or use the endpoint probe below.
   ```bash
   if [ "${DEPLOYMENT_KIND:-docker}" != "kubernetes" ]; then
     grep -E '^ALERT_AGENT_ALWAYS_ON=' deploy/docker/developer-profiles/dev-profile-alerts/generated.env 2>/dev/null \
       || grep -E '^ALERT_AGENT_ALWAYS_ON=' deploy/docker/developer-profiles/dev-profile-alerts/overrides.env
     docker exec vss-alert-bridge sh -c 'grep -A1 -E "^\s*always_on" /app/runtime/config.yml' 2>/dev/null
   fi
   ```
2. **Endpoint probe (POST — benign only with a sentinel camera ID).** A `camera_remove` for a **nonexistent** camera is a no-op when enabled and returns the gate response when disabled. Keep the sentinel UUID below verbatim; substituting the camera you are actually asked about turns the probe into an unconfirmed teardown of that camera's monitoring. Use `$AB` from the parent skill (Kubernetes `${VSS_PUBLIC_URL}/alert-bridge`, Docker `http://${HOST_IP}:9080`):
   ```bash
   : "${AB:?Resolve AB from vss-manage-alerts Deployment prerequisite}"
   curl -s -o /tmp/ao.json -w '%{http_code}\n' -X POST "$AB/api/v1/realtime/always-on" \
     -H 'Content-Type: application/json' \
     -d '{"source":"vst","event":{"camera_id":"00000000-0000-0000-0000-0000000000aa","change":"camera_remove"}}'
   # 503 + {"reason":"ALWAYS_ON_DISABLED"} → feature off (verification / 2d_cv default)
   # 200-range / REMOVE_* reason           → feature on (real-time / 2d_vlm)
   ```

Report the state you actually observed. If the user wants it enabled on a Docker verification deploy, tell them to redeploy with `the `/vss-build-vision-ai` stock Alerts workflow in real-time mode` (do **not** hand-edit config and restart). That redeploy sets `ALERT_AGENT_ALWAYS_ON=true` and `MODE=2d_vlm`. On Kubernetes there is **no supported way to enable it**, and saying otherwise sends the operator on a dead end: `deploy/helm/services/alert/configs/config.yml` pins `always_on: false` as a literal (with an explicit "Helm keeps this disabled for the dev alerts real-time profile … do not align them" note), and no `values.yaml` key or pod env maps to `alert_agent.always_on`. The chart wires only `ALWAYS_ON_RULES_CONFIG=/app/configs/realtime-config.yml` and already ships a non-empty `always_on_rules` YAML, so the rules are never the missing piece; a config change also restarts the pod on its own through the `checksum/config` annotation. Report that always-on is off on this deployment and that the chart offers no operator switch for it — do **not** describe a set-the-gate-and-restart path.

## Response envelope & reason codes

Every response: `{"reason": "<REASON>", "status": "HTTP/1.1 <code> <phrase>", "details": [...]?}` — `details` (one entry per rule, with per-rule `status`/`result`) appears only when the service reached the fan-out; short-circuits (disabled gate, bad payload, config error, add-path dedupe) omit the key entirely. Present-but-empty is a third state, not the same as absent — see the stop table below.

| `reason` | Meaning |
|---|---|
| `ALWAYS_ON_DISABLED` | Feature gate `alert_agent.always_on` is off (HTTP 503) |
| `INVALID_PAYLOAD` | Event body failed validation (HTTP 422, custom shape — not FastAPI's `detail`) |
| `CONFIG_ERROR` | Rules YAML missing/malformed (also raised at startup validation) |
| `STREAM_ADD_SUCCESS` | All configured rules started for the camera |
| `STREAM_ADD_PARTIAL_SUCCESS` | Some rules started, some failed — see `details` |
| `STREAM_ADD_FAILED` | No rule could be started — see `details` |
| `STREAM_ADD_ALREADY_ACTIVE` | Rules already running for this `camera_id` (idempotent short-circuit) |
| `STREAM_REMOVE_SUCCESS` / `STREAM_REMOVE_FAILED` | Teardown outcome for `camera_remove` — read `details` for what actually stopped, see [Stopping a camera's always-on rules](#stopping-a-cameras-always-on-rules-after-the-user-confirms) |

## Stopping a camera's always-on rules (after the user confirms)

`DELETE /api/v1/realtime/<id>` cannot reach these rules: they are created on a *store-less* `RealtimeAlertService` instance, so no listing shows them and no rule ID addresses them. `camera_remove` is the only teardown.

1. **Resolve the `camera_id`** — the sidecar is keyed on the VST **sensor UUID** VIOS sent, never the name: `"${VSS[@]}" vios list --type stream --sensor <name>`. Cross-check it against what Alert Bridge recorded (`docker logs vss-alert-bridge 2>&1 | grep 'always-on event: camera_id'`), because an unmatched ID still answers `200` — see the table.
2. **Ground the confirmation** — those same logs (`STREAM_ADD_SUCCESS`, one `alert_rule_id` per rule) plus Workflow C incidents are the only evidence the rules are running. Nothing there → report nothing active and stop; a live `camera_remove` is never the way to find out.
3. **Send it, only after the "yes"** — `camera_name` / `camera_url` are ignored on this path:
   ```bash
   curl -s -X POST "$AB/api/v1/realtime/always-on" -H 'Content-Type: application/json' \
     -d '{"source":"vst","event":{"camera_id":"<SENSOR_UUID>","change":"camera_remove"}}' | jq .
   ```
4. **Report from `details`, not from the status code:**

   | Response | Report |
   |---|---|
   | `200`, **non-empty** `details` | Stopped — one line per entry (`rule_id`, `alert_rule_id`). A `404` entry is `result: "success"` too: the rule was already gone |
   | `200`, `details: []` | **Nothing stopped** — the `camera_id` matched no sidecar entry, so the loop never ran. Never report it as a stop: re-check step 1, else the rules never started, or Alert Bridge restarted and dropped the sidecar ([which does not re-arm itself](#restart-gap) — the camera is unmonitored, not stopped) |
   | `502` `STREAM_REMOVE_FAILED` | Partial — name which stopped and which didn't, with each entry's `error`. Failed rule IDs stay in the sidecar, so a retry re-drives only those |

5. **Say that it is not permanent** — this drops the running rules, it does not turn always-on off: the camera's next offline→online reconnect re-emits `camera_streaming` and starts them all again. Permanently off means removing the sensor from VIOS or redeploying in verification mode; say which applies.

## Rules YAML (read-only knowledge)

`ALWAYS_ON_RULES_CONFIG` is the **only** source — there is no implicit fallback. `AlwaysOnService.load_rules` reads that env var and raises `AlwaysOnRulesConfigError` when it is unset, when the path is missing, when YAML parsing fails, or when the file does not match the schema; it never falls back to `./realtime-config.yaml` or to the sample. `services/alert/realtime-config-sample.yaml` is documentation (the validation error message points at it), not a search path. In Docker, the mounted source is rendered through `env-substitute.py` to `/app/runtime/realtime-config.yml`, and `ALWAYS_ON_RULES_CONFIG` points at that rendered file. This lets a rule use `model: "${VLM_NAME}"` so it follows the deployment-selected local, alternate, or remote VLM.

Shape: top-level `always_on_rules:` — a non-empty list with unique `rule_id`s; each entry carries `rule_id`, `alert_type`, optional `description`, and `always_on_params` where `prompt` / `system_prompt` / `model` are **required** and `live_stream_url` / `alert_type` / `sensor_name` are **derived** from the camera event (setting them is a config error). Omitting `model` does not select an available model; it fails startup validation. The rendered YAML is validated at Alert Bridge startup when the gate is on.

## Troubleshooting — "why aren't always-on alerts appearing?"

Walk the chain top-down; stop at the first broken link and report it:

1. **Feature gate** — `ALERT_AGENT_ALWAYS_ON=false` / `alert_agent.always_on: false` (verification / 2d_cv) explains everything; see Status above. Real-time deploys should show `ALERT_AGENT_ALWAYS_ON=true` and `MODE=2d_vlm`.
2. **Rules YAML** — resolves via the chain above and validated at boot; a `CONFIG_ERROR` in `alert-bridge` startup logs means no rule can ever start: `docker logs vss-alert-bridge 2>&1 | grep -i "always"`.
3. **VIOS webhooks reaching Alert Bridge** — the same logs show each incoming `POST /api/v1/realtime/always-on`; no log lines = VIOS never posted the camera event (check `VST_NOTIFICATION_CONFIG_PATH` points at `notification_config_2d_vlm.json` and the sensor is online; hand off to `vss-manage-video-io-storage` if needed). A camera whose fan-out lines pre-date the container's current start time is the [restart gap](#restart-gap), not a VIOS fault — diagnose it as unmonitored and re-arm the stream.
4. **Stream registered on rtvi-vlm** — the fan-out `details` entries carry per-rule upstream errors; a 502-class `error` means `rtvi-vlm` rejected the stream.
5. **Incidents** — finally, query Workflow C (`GET /api/v1/realtime/incidents`, scope by camera/time). Empty with all links healthy = nothing matched the rule prompts yet; report that grounded, don't fabricate.

Never "fix" a broken link by authoring config or onboarding sensors the user didn't ask for — report the diagnosis and the enable path.
