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

This separates two kinds of portability:

- **Data plane:** any backend that speaks a supported wire protocol can carry a
  chat turn through the same gateway connector.
- **VSS capability plane:** the backend is a VSS assistant only after its
  harness has the complete recursive skill catalog, execution tools, a
  version-matched `vss` CLI runtime, endpoint configuration, network
  permissions, and a VSS capability receipt.

Do not inject a large VSS prompt from the gateway as a substitute. That would
neither install executable tools nor establish their security boundary, and it
would make prompt/session behavior connector-dependent. For NemoClaw-managed
OpenClaw or Hermes that already exists, run the additive
`deploy/docker/scripts/attach_vss_agent.py`; it must preserve the agent's
persona, canonical workspace documents, memory, provider, model, and chat
history. Use `deploy_nemoclaw.ipynb` only when creating a new dedicated VSS
assistant whose identity may intentionally be set by the VSS workspace overlay.
For another harness, use its native skill/runtime/policy installer to attach the
same capabilities without replacing identity. An arbitrary Responses endpoint
with no capability attachment is generic chat only and is not a ready external
VSS agent.

Search results and alert incidents cross the data-plane boundary as versioned
VSS UI artifacts. The operational skill emits the exact validated response in
a `<vss-ui-artifact>` envelope; the gateway extracts it from exposed tool output
or final text and emits `artifact.created`. The UI uses
`vss.search.results` for Search result cards and `vss.alert.incidents` for Chat
incident cards plus Alerts-tab refresh. Do not add a harness-specific renderer.

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

When a sandbox uses `http://host.openshell.internal:<ingress-port>` as its VSS
origin, keep `HOST_INTERNAL_ALIAS=host.openshell.internal` on the HAProxy
service. The ingress explicitly admits that Host header; TCP reachability alone
is insufficient because an unrecognized host intentionally returns 404.

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
| `VSS_AGENT_BACKEND_HEADERS_JSON` | Optional upstream header object; treat the entire value as credential-bearing even when it contains only routing metadata. |
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

OpenClaw is the primary production-validation preset. Hermes transport is
compatible, but NVIDIA's current NemoClaw platform-support matrix labels the
project early-preview alpha and does not assert Hermes production parity with
OpenClaw. A Hermes build therefore requires staging validation for the selected
provider, model, skills, long-running tools, restart recovery, and security
policy before it can be represented as production-ready.

## Readiness

After Compose Gate 0, require all of these:

1. The capability installer completed without a failed skill and verified that
   the BYO agent's identity-file hashes did not change. For a newly created
   dedicated VSS agent only, verify the chosen workspace overlay and archived
   first-turn `BOOTSTRAP.md` instead.
2. The harness contains all canonical `skills/**/SKILL.md` names; a one-level
   `skills/*/SKILL.md` check is invalid. Its
   `~/.vss/agent-capabilities.json` receipt names the selected origin, runtime
   commit, installed skills, and artifact protocol.
3. From inside the harness, the commit-matched project CLI answers
   `uv run --project "$VSS_REPO_ROOT/services/agent" --no-dev --extra cli vss
   --version`.
4. Required routes for the selected capability are reachable from inside the
   harness: the configured VSS origin for operations, VA-MCP for analytics, and
   VSS Orchestrator MCP only when deployment management is enabled.
5. `agent-gateway` is healthy.
6. `GET http://<VSS_AGENT_GATEWAY_BIND_HOST>:<port>/healthz` returns `200`.
7. Authenticated `GET /v1/capabilities` through the gateway returns contract
   version `1.0`.
8. The VSS UI server can reach `VSS_AGENT_GATEWAY_URL`.
9. Send one harmless chat turn through the VSS UI and confirm the response came
   from the selected harness. Then run a non-destructive VSS query and confirm
   the harness executed the skill and the corresponding Search or alert
   artifact rendered in the UI.

## Sources

- `deploy/docker/services/agent-gateway/compose.yml`
- `deploy/docker/services/ui/compose.yml`
- `services/agent-gateway/README.md`
- `deploy/docker/scripts/attach_vss_agent.py`
- `deploy/docker/scripts/deploy_nemoclaw.ipynb`
- `deploy/docker/scripts/deploy_vss_orchestrator.ipynb`
