# External Agent Gateway Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Use an external agent harness for VSS UI chat | `agent-gateway`, `vss-ui` |

## Execution boundary

The VSS UI speaks the VSS-owned run/event contract to `agent-gateway`. The
gateway selects a wire-protocol connector, not a harness-specific code path.
OpenClaw uses its native protocol-v4 WebSocket connector so its structured tool
events remain visible. Hermes uses the generic `responses` connector; another
backend that implements Responses uses the same configuration.

The selected harness owns prompts, sessions, skills, tools, shell commands,
permissions, and model calls. The gateway normalizes chat/run events and keeps
the harness credential out of browser code. It must never execute a skill or
tool on the harness's behalf. The native OpenClaw connector requests
`tool-events`, but neither its raw arguments nor command output cross into the
browser. Use another protocol connector when a harness exposes interactive
approvals or richer events through a different wire contract.

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
VSS UI artifacts. The Responses connector offers one protocol-level
`vss_ui_publish_artifact` client function for harnesses that hide backend tool
output. For native OpenClaw search, the connector privately recognizes the
exact strict VSS `SearchOutput` plus its matching successful
`vss_job_completed` marker from a completed exec result, so the model need not
reconstruct JSON. A completed tool result can also carry a
`<vss-ui-artifact>` envelope; other harnesses can use the same envelope in tool
output, with final text as a fallback. The gateway validates and normalizes
every path to `artifact.created`. The UI uses `vss.search.results` for Search result cards and
`vss.alert.incidents` for Chat incident cards plus Alerts-tab refresh. Do not
add a harness-specific renderer.

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
- The harness API must be running before deployment generation. OpenClaw uses
  its Gateway WebSocket and does not require `/v1/responses`; NemoHermes exposes
  its Responses API through its API forward.

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

## Host-side capability bootstrap

Provision an existing OpenClaw or Hermes sandbox through its host CLI before
resolving the gateway-enabled Compose graph. Do not add a privileged Compose
initializer: it would need the host Docker socket and the operator's NemoClaw
state, crossing the harness security boundary. The native CLI is the supported
owner of sandbox mutation and preserves the agent's history and identity.

From the exact, clean VSS source revision being deployed:

```bash
REPO="$(git rev-parse --show-toplevel)"
BUILD_DIR="$REPO/_builds/<name>"

python3 "$REPO/deploy/docker/scripts/attach_vss_agent.py" \
  --runtime <openclaw-or-hermes> \
  --sandbox <existing-sandbox> \
  --vss-origin http://host.openshell.internal:<haproxy-port> \
  --receipt-output "$BUILD_DIR/agent-capabilities.json" \
  --gateway-env-output "$BUILD_DIR/agent-gateway.env"
```

The origin may be the planned origin of the graph about to be deployed; the
bootstrap configures it but deployment readiness must prove it is reachable.
The script recursively installs the complete skill catalog, prepares the
project CLI at the exact source commit, applies the narrowly scoped network
policy, probes the harness API, verifies that identity files did not change,
and writes both host artifacts mode `0600`. For OpenClaw, the probe checks the
native Gateway's documented `/health` endpoint and obtains the operator token;
for Hermes, it checks the authenticated model list and Responses route.
The bootstrap binds the capability receipt to that immutable commit. OpenClaw attachment promotes
the official catalog into its highest-precedence workspace skill root, refuses
an operator-owned same-name collision, and aligns the `~` path in OpenClaw's
skill cards with NemoClaw's durable sandbox state. Never print, source, or
commit `agent-gateway.env`: it contains both gateway and harness credentials.

Pass `agent-gateway.env` as the final env layer only while generating
`resolved.yml`; deploy the resolved file with no env files. A repeated bootstrap
is safe for a clean installer-managed runtime and advances it to the requested
immutable commit. It refuses dirty or foreign checkouts.

The dedicated-agent notebook path performs the equivalent recursive install
and receipt creation itself. The Harbor NemoClaw evaluation adapter executes
those checked-in notebooks in order, so it uses that same provisioning path;
Harbor's task-scoped `/skills` staging is test input, not a production install.

## Required configuration

For a BYO sandbox, `attach_vss_agent.py` writes these values to the build's
sensitive `agent-gateway.env`. The resolved Compose artifact contains them too,
so both files must remain local and mode `0600`. The notebook path passes the
same values directly to the resolver without displaying them:

| Environment variable | Use |
|---|---|
| `VSS_AGENT_GATEWAY_ENABLED=true` | Activates generator validation/defaults. |
| `VSS_AGENT_GATEWAY_BIND_HOST` | Private IPv4 gateway returned by `docker network inspect bridge`. |
| `VSS_AGENT_GATEWAY_PORT` | Host-network listener port; default `18090`. |
| `VSS_AGENT_GATEWAY_URL` | UI-server URL; default `http://host.docker.internal:18090`. |
| `VSS_AGENT_GATEWAY_TOKEN` | Independent random bearer between VSS UI and the gateway. |
| `VSS_AGENT_GATEWAY_REQUIRE_CAPABILITIES=true` | Makes gateway startup fail closed without a valid receipt. |
| `VSS_AGENT_GATEWAY_CAPABILITIES_B64` | Canonical capability receipt; generated by the bootstrap. |
| `VSS_AGENT_GATEWAY_CAPABILITIES_SHA256` | Digest binding for the receipt bytes. |
| `VSS_AGENT_GATEWAY_EXPECTED_RUNTIME_REF` | Exact full VSS commit the receipt and gateway deployment must share. |
| `VSS_AGENT_BACKEND_PROTOCOL` | `openclaw-ws` for OpenClaw; `responses` for Hermes or another compatible backend. |
| `VSS_AGENT_BACKEND_URL` | Harness origin on host loopback. |
| `VSS_AGENT_BACKEND_PATH` | `/` for OpenClaw; `/v1/responses` for Hermes. |
| `VSS_AGENT_BACKEND_TOKEN` | Harness operator/API bearer; never expose it to the browser. |
| `VSS_AGENT_BACKEND_MODEL` | Harness agent/model selector. |
| `VSS_AGENT_BACKEND_SESSION_FIELD` | Stable Responses field (`user` for Hermes); empty for OpenClaw. |
| `VSS_AGENT_BACKEND_SESSION_HEADER` | Optional protocol routing header (`X-Hermes-Session-Key` for Hermes); empty for OpenClaw. |
| `VSS_AGENT_BACKEND_HEADERS_JSON` | Optional upstream header object; treat the entire value as credential-bearing even when it contains only routing metadata. |
| `NEXT_PUBLIC_ENABLE_CHAT_TAB=true` | Makes the VSS UI chat tab visible. |
| `NEXT_PUBLIC_FORCE_HTTP_CHAT_TRANSPORT=true` | Locks all chat surfaces to same-origin HTTP so a saved WebSocket preference cannot bypass the gateway. Set automatically by the generator. |

Generate `VSS_AGENT_GATEWAY_TOKEN` independently from the harness token. Obtain
the backend token with the selected sandbox CLI's `gateway-token --quiet` path
and capture it directly into the protected artifact-generation process; never
print it in chat, logs, or notebook output.

## Harness presets

| Harness | Protocol | Backend URL/path | Model | Session routing |
|---|---|---|---|---|
| OpenClaw | `openclaw-ws` | `ws://127.0.0.1:18789/` (or `NEMOCLAW_DASHBOARD_PORT`) | `openclaw/default` | Native opaque `agent:main:vss-ui-*` session key |
| Hermes | `responses` | `http://127.0.0.1:8642/v1/responses` (or `NEMOHERMES_API_PORT`) | `hermes-agent` | `user` plus `X-Hermes-Session-Key` |

The OpenClaw connector does not fall back to Responses. It challenge-signs with
a device identity persisted in the Compose `agent-gateway-state` volume. The
NemoClaw host-isolated trust path accepts this client directly. If a hardened
OpenClaw instance requests device pairing, approve the exact request ID it
reports, then retry; do not disable device authentication. Probe OpenClaw's
native `/health` endpoint before resolution; `/v1/models` is not required and
the Responses endpoint remains disabled. A failed probe is a blocker.

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
   `/sandbox/.vss/agent-capabilities.json` receipt names the selected origin, runtime
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
   version `1.0`, `vss.attached=true`, and `vss.ready=true`. The reported
   `vss.runtime_commit` must equal the commit used to resolve the deployment.
   This proves capability attachment, not live service reachability; item 4 and
   the end-to-end checks remain mandatory. A dedicated notebook agent may have
   an empty receipt origin before it deploys its first stack.
8. The VSS UI server can reach `VSS_AGENT_GATEWAY_URL`.
9. Send one harmless chat turn through the VSS UI and confirm the response came
   from the selected harness. For OpenClaw, confirm `connector.protocol` is
   `openclaw-ws` and at least one deliberate tool call renders as an
   intermediate step. Then run a non-destructive VSS query and confirm the
   harness executed the skill and the corresponding Search or alert artifact
   rendered in the UI.

## Sources

- `deploy/docker/services/agent-gateway/compose.yml`
- `deploy/docker/services/ui/compose.yml`
- `services/agent-gateway/README.md`
- `deploy/docker/scripts/attach_vss_agent.py`
- `deploy/docker/scripts/deploy_nemoclaw.ipynb`
- `deploy/docker/scripts/deploy_vss_orchestrator.ipynb`
