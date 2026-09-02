# External Agent Gateway Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Use an external agent harness for VSS UI chat | `agent-gateway`, `vss-ui` |

## Execution boundary

The VSS UI speaks the VSS-owned run/event contract to `agent-gateway`. The
gateway selects a wire-protocol connector, not a harness-specific code path.
OpenClaw and Hermes both use the `responses` connector; another backend that
implements the same protocol uses the same configuration.

The selected harness owns prompts, sessions, skills, tools, shell commands,
permissions, and model calls. The gateway normalizes chat/run events and keeps
the harness credential out of browser code. It must never execute a skill or
tool on the harness's behalf. Structured tool progress is limited to events the
upstream protocol exposes; use a richer protocol connector later if interactive
approvals or backend-private trajectory events are required.

This owner is reached only by an explicit request to connect an external agent
harness to VSS UI. It makes the build a Delta even when the underlying vision
services otherwise match a stock profile.

## Required peers and pruning

- Retain `vss-ui` and add `agent-gateway`.
- Retain the Ingress owner when the UI needs the normal public VSS origin.
- `agent-gateway` does not require `vss-agent`. When the external harness
  replaces VSS Agent orchestration and no selected owner requires `vss-agent`,
  prune `vss-agent`, `phoenix`, and model peers reachable only through it.
  Keep them when another selected capability (for example an LVS workflow)
  explicitly requires them.
- The harness API must be running before deployment generation. OpenClaw's
  `/v1/responses` endpoint must be enabled; NemoHermes exposes its Responses API
  through its API forward.

## Single-host Compose network contract

Notebook-managed OpenClaw/Hermes API forwards are loopback-only. On Linux,
`agent-gateway` therefore uses host networking to reach the backend at
`127.0.0.1`, but binds its own listener only to Docker's private bridge gateway.
The `vss-ui` container reaches it through `host.docker.internal`, mapped with
Docker's `host-gateway` feature.

Resolve the bind address rather than assuming `172.17.0.1`:

```bash
docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
```

Do not bind the gateway or raw harness API to `0.0.0.0`. This owner supports a
single Linux Docker host; an HA/multi-node deployment requires a durable run
store and an explicit private service network instead of this host-loopback
bridge.

The orchestrator's resolved gateway-mode graph builds both `agent-gateway` and
the compatible `vss-agent-ui` from the pinned checkout. Do not replace the UI
with an older registry image: it lacks the same-origin gateway routes and HTTP
transport lock.

## Required configuration

Write these values to the build's sensitive `override.env`; the resolved
Compose artifact contains them too, so both files must remain local and mode
`0600`:

| Environment variable | Use |
|---|---|
| `VSS_AGENT_GATEWAY_ENABLED=true` | Activates generator validation/defaults. |
| `VSS_AGENT_GATEWAY_BIND_HOST` | Private IPv4 gateway returned by `docker network inspect bridge`. |
| `VSS_AGENT_GATEWAY_PORT` | Host-network listener port; default `18090`. |
| `VSS_AGENT_GATEWAY_URL` | UI-server URL; default `http://host.docker.internal:18090`. |
| `VSS_AGENT_GATEWAY_TOKEN` | Independent random bearer between VSS UI and the gateway. |
| `VSS_AGENT_BACKEND_PROTOCOL` | `responses` for OpenClaw/Hermes. |
| `VSS_AGENT_BACKEND_URL` | Harness origin on host loopback. |
| `VSS_AGENT_BACKEND_TOKEN` | Harness operator/API bearer; never expose it to the browser. |
| `VSS_AGENT_BACKEND_MODEL` | Harness agent/model selector. |
| `VSS_AGENT_BACKEND_SESSION_FIELD` | Stable Responses field; use `user`. |
| `VSS_AGENT_BACKEND_SESSION_HEADER` | Optional harness-specific stable-session header. |
| `NEXT_PUBLIC_ENABLE_CHAT_TAB=true` | Makes the VSS UI chat tab visible. |
| `NEXT_PUBLIC_FORCE_HTTP_CHAT_TRANSPORT=true` | Locks all chat surfaces to same-origin HTTP so a saved WebSocket preference cannot bypass the gateway. Set automatically by the generator. |

Generate `VSS_AGENT_GATEWAY_TOKEN` independently from the harness token. Obtain
the backend token with the selected sandbox CLI's `gateway-token --quiet` path
and capture it directly into the protected artifact-generation process; never
print it in chat, logs, or notebook output.

## Harness presets

| Harness | Backend URL | Model | Session header |
|---|---|---|---|
| OpenClaw | `http://127.0.0.1:18789` (or `NEMOCLAW_DASHBOARD_PORT`) | `openclaw/default` | `x-openclaw-session-key` |
| Hermes | `http://127.0.0.1:8642` (or `NEMOHERMES_API_PORT`) | `hermes-agent` | `X-Hermes-Session-Key` |

Both use `/v1/responses`. For OpenClaw, enable
`gateway.http.endpoints.responses.enabled=true` and restart the sandbox gateway
before probing. Probe authenticated `/v1/models` before resolution; a failed
probe is a blocker.

## Readiness

After Compose Gate 0, require all of these:

1. `agent-gateway` is healthy.
2. `GET http://<VSS_AGENT_GATEWAY_BIND_HOST>:<port>/healthz` returns `200`.
3. Authenticated `GET /v1/capabilities` through the gateway returns contract
   version `1.0`.
4. The VSS UI server can reach `VSS_AGENT_GATEWAY_URL`.
5. Send one harmless chat turn through the VSS UI and confirm the response came
   from the selected harness. Tool/skill execution should be verified with a
   separately approved, non-destructive harness task.

## Sources

- `deploy/docker/services/agent-gateway/compose.yml`
- `deploy/docker/services/ui/compose.yml`
- `services/agent-gateway/README.md`
- `deploy/docker/scripts/deploy_nemoclaw.ipynb`
- `deploy/docker/scripts/deploy_vss_orchestrator.ipynb`
