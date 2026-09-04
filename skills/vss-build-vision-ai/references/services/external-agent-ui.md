# Embedded External-Agent UI Adapter

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Use an external agent harness for VSS UI chat | `vss-ui` |

## Execution boundary

The existing VSS UI Next.js server contains the trusted adapter between browser
chat and an external agent harness. It is not a separate service or image. The
browser speaks the VSS-owned same-origin `/api/agent` run/event contract, and
the adapter selects a wire-protocol connector. OpenClaw uses its native
protocol-v4 WebSocket connection so structured tool events remain visible.
Hermes and other compatible backends can use the Responses connector.

The selected harness owns prompts, sessions, skills, tools, shell commands,
permissions, and model calls. The UI adapter only normalizes chat/run events,
keeps the harness credential out of browser code, and validates UI artifacts.
It must never execute a skill or tool on the harness's behalf. OpenClaw's native
connection requests only `operator.read` and `operator.write`; it does not use
the broader Responses compatibility endpoint.

This separates two kinds of portability:

- **Data plane:** any backend that speaks a supported wire protocol can carry a
  chat turn through the same adapter.
- **VSS capability plane:** the backend is a VSS assistant only after its
  harness has the complete recursive skill catalog, execution tools, a
  version-matched `vss` CLI runtime, endpoint configuration, network
  permissions, and a VSS capability receipt.

Do not inject a large VSS prompt from the UI as a substitute. For an existing
NemoClaw-managed OpenClaw or Hermes agent, run the additive
`deploy/docker/scripts/attach_vss_agent.py`; it preserves the agent's persona,
workspace documents, memory, provider, model, and history. Use
`deploy_nemoclaw.ipynb` only when creating a new dedicated VSS assistant. An
arbitrary Responses endpoint without capability attachment is generic chat,
not a ready external VSS agent.

Search results and alert incidents cross the boundary as versioned VSS UI
artifacts. The Responses connector offers the `vss_ui_publish_artifact` client
function for harnesses that hide backend tool output. The native OpenClaw
connector recognizes strict VSS CLI results and the common
`<vss-ui-artifact>` envelope. The adapter validates every path before emitting
`artifact.created`; do not add a harness-specific renderer.

## Required peers and pruning

- Retain `vss-ui`; no additional Compose service is added.
- Retain the Ingress owner when the UI needs the normal public VSS origin.
- Remove `vss-agent` when the external harness replaces VSS Agent
  orchestration. Harness selection does not by itself prune `phoenix` or model
  peers; normal capability composition decides whether another owner needs
  them.
- The harness API must be running before deployment generation. OpenClaw uses
  its native Gateway WebSocket and does not require `/v1/responses`; Hermes
  exposes its Responses API through its API forward.

## Single-host Compose network contract

Notebook-managed OpenClaw and Hermes API forwards are loopback-only. The
attachment script opens an additional forward bound only to Docker's private
bridge gateway. The existing `vss-ui` container reaches that private forward
through `host.docker.internal`, mapped with Docker's `host-gateway` feature.
Neither the raw harness API nor a new application listener is published on an
external interface.

Resolve the bridge address rather than assuming `172.17.0.1`:

```bash
docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
```

Do not bind the harness API to `0.0.0.0`. Kubernetes deployments instead need
an explicitly private, cluster-reachable backend URL; no new Deployment or
Service is created by this capability.

When a sandbox uses `http://host.openshell.internal:<ingress-port>` as its VSS
origin, keep `HOST_INTERNAL_ALIAS=host.openshell.internal` on HAProxy. The
ingress explicitly admits that Host header.

## Host-side capability bootstrap

Provision the selected sandbox before resolving the Compose graph. From the
exact, clean VSS source revision being deployed:

```bash
REPO="$(git rev-parse --show-toplevel)"
BUILD_DIR="$REPO/_builds/<name>"

python3 "$REPO/deploy/docker/scripts/attach_vss_agent.py" \
  --runtime <openclaw-or-hermes> \
  --sandbox <existing-sandbox> \
  --vss-origin http://host.openshell.internal:<haproxy-port> \
  --receipt-output "$BUILD_DIR/agent-capabilities.json" \
  --ui-env-output "$BUILD_DIR/agent-ui.env"
```

The script recursively installs the skill catalog, prepares the project CLI at
the exact source commit, applies the narrow network policy, probes the harness
API, verifies that identity files did not change, starts the private bridge
forward, and writes both host artifacts mode `0600`. OpenClaw attachment uses
the native Gateway health probe and operator token; Hermes checks the
authenticated model list and Responses route.

Never print, source, or commit `agent-ui.env`: it contains the harness
credential. Pass it as the final env layer only while generating `resolved.yml`,
then deploy the resolved file without env files. A repeated bootstrap is safe
for a clean installer-managed runtime and refuses dirty or foreign checkouts.

`deploy_vss_orchestrator.ipynb` is the exception when the user explicitly gives
the sandbox deployment ownership: it obtains the live credential, opens the
private forward, and resolves its own graph.

## Required configuration

The attachment writes these values to protected `agent-ui.env`:

| Environment variable | Use |
|---|---|
| `VSS_AGENT_ADAPTER_ENABLED=true` | Activates generator validation and UI build settings for the embedded adapter. |
| `VSS_AGENT_BACKEND_BIND_HOST` | Private Docker bridge address used by the harness forward. |
| `VSS_AGENT_REQUIRE_CAPABILITIES=true` | Makes the embedded adapter fail closed without a valid receipt. |
| `VSS_AGENT_CAPABILITIES_B64` | Canonical capability receipt generated by the bootstrap. |
| `VSS_AGENT_CAPABILITIES_SHA256` | Digest binding for the receipt bytes. |
| `VSS_AGENT_EXPECTED_RUNTIME_REF` | Exact VSS commit shared by the receipt and deployment. |
| `VSS_AGENT_BACKEND_PROTOCOL` | `openclaw-ws`, `responses`, or `legacy-chat`. |
| `VSS_AGENT_BACKEND_URL` | Private harness origin reachable from the UI container. |
| `VSS_AGENT_BACKEND_PATH` | `/` for OpenClaw; `/v1/responses` for Hermes. |
| `VSS_AGENT_BACKEND_TOKEN` | Server-only harness credential; never expose it to the browser. |
| `VSS_AGENT_BACKEND_MODEL` | Harness agent/model selector. |
| `VSS_AGENT_BACKEND_SESSION_FIELD` | Stable Responses field (`user` for Hermes); empty for OpenClaw. |
| `VSS_AGENT_BACKEND_SESSION_HEADER` | Optional Responses routing header. |
| `VSS_AGENT_BACKEND_HEADERS_JSON` | Optional upstream HTTP headers; treat the value as credential-bearing. |
| `VSS_AGENT_RUN_RETENTION_SECONDS` | Optional in-process replay retention override. |
| `VSS_AGENT_MAX_EVENT_CHARS_PER_RUN` | Optional per-run retained event-size limit. |
| `VSS_AGENT_MAX_THREAD_STATE_CHARS` | Optional retained thread-state size limit. |
| `NEXT_PUBLIC_ENABLE_CHAT_TAB=true` | Makes the VSS UI chat tab visible. |
| `NEXT_PUBLIC_FORCE_HTTP_CHAT_TRANSPORT=true` | Locks chat to the same-origin server route. |

The Compose service maps the `VSS_AGENT_*` build variables to server-only
`AGENT_*` environment variables inside `vss-ui`. There is no UI-to-adapter
token because the browser already reaches the trusted Next.js server through
the deployment's existing access-control boundary.

## Harness presets

| Harness | Protocol | Backend URL/path from `vss-ui` | Model | Session routing |
|---|---|---|---|---|
| OpenClaw | `openclaw-ws` | `ws://host.docker.internal:<port>/` | `openclaw/default` | Native opaque `agent:main:vss-ui-*` session key |
| Hermes | `responses` | `http://host.docker.internal:<port>/v1/responses` | `hermes-agent` | `user` plus `X-Hermes-Session-Key` |

The OpenClaw connector does not fall back to Responses. The compatibility
endpoint previously failed to stream native tool lifecycle events, shares a
broad operator credential, and can blur session isolation depending on the
harness version. The native protocol preserves tool events and creates one
opaque upstream session per VSS UI thread while requesting only
`operator.read`/`operator.write`.

## Readiness

After Compose Gate 0, require all of these:

1. Capability installation succeeded and the BYO agent's identity-file hashes
   did not change.
2. The harness contains all canonical recursive skills and its receipt names
   the selected origin, runtime commit, installed skills, and artifact
   protocol.
3. The commit-matched project CLI answers from inside the harness.
4. Required VSS routes are reachable from inside the harness.
5. The harness API is healthy through the private bridge forward.
6. `GET /api/agent/capabilities` through the deployed VSS origin reports
   protocol `1.0`, `vss.attached=true`, `vss.ready=true`, and the expected
   runtime commit.
7. Send a harmless VSS UI chat turn. For OpenClaw, verify protocol
   `openclaw-ws`, one intermediate tool event, and one Search or Alerts
   artifact from a non-destructive query.

## Sources

- `deploy/docker/services/ui/compose.yml`
- `services/ui/apps/nv-metropolis-bp-vss-ui/utils/server/agentAdapter/`
- `deploy/docker/scripts/attach_vss_agent.py`
- `deploy/docker/scripts/deploy_nemoclaw.ipynb`
- `deploy/docker/scripts/deploy_vss_orchestrator.ipynb`
