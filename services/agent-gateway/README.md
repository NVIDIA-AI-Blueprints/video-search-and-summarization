# VSS Agent Gateway

The VSS UI talks to this service through one versioned run/event protocol. Agent
harnesses sit behind protocol connectors, so the UI never branches on names such
as OpenClaw, Hermes, or VSS Agent.

```text
VSS UI  ->  /api/agent/*  ->  VSS run/event contract  ->  protocol connector  ->  agent backend
                                       |                       |
                              replay/cancel/auth      OpenClaw WS, Responses, legacy chat
```

AG-UI is not required. It can be added later as one more connector if an upstream
only exposes AG-UI; it should not become the UI's internal contract.

## Runtime protocol versus VSS capability attachment

Backend-agnostic chat and backend-agnostic VSS operation are separate
contracts. Speaking a supported wire protocol is enough to connect a backend
to the UI, but it does not turn a generic model endpoint into a VSS agent.

Before an external harness is advertised as VSS-ready, attach the VSS
capabilities that its existing agent needs:

- every `skills/**/SKILL.md` directory, discovered recursively;
- the harness-native execution tools those skills use (shell/exec, HTTP, and
  MCP where applicable);
- a commit-matched, pre-warmed project `vss` CLI runtime for operational skills;
- the VSS/OpenShell network policy and the deployment origin or service routes;
- a capability receipt at `/sandbox/.vss/agent-capabilities.json`, including the VSS
  origin, CLI revision, installed skills, and supported UI artifact version.

Capability attachment must preserve the agent the operator brought: do not
replace its persona, canonical workspace documents, memory, provider, model, or
conversation history. This is the external-harness equivalent of granting the
built-in VSS Agent its tools; it is not a new agent identity and is not a prompt
prepended to each turn. An operator may separately choose the repository's VSS
persona overlay when creating a new, dedicated VSS assistant.

For an existing NemoClaw-managed OpenClaw or Hermes agent, use the additive
installer:

```bash
python3 deploy/docker/scripts/attach_vss_agent.py \
  --runtime openclaw \
  --sandbox my-agent \
  --vss-origin http://host.openshell.internal:7777 \
  --receipt-output _builds/my-build/agent-capabilities.json \
  --gateway-env-output _builds/my-build/agent-gateway.env
```

The installer hashes the canonical identity files before and after attachment
and fails if they changed. The two host artifacts are written mode `0600`; the
gateway overlay includes independent credentials plus a digest-bound receipt
and the exact expected VSS source commit. Pass it last when resolving Compose,
never print/source/commit it, and do not pass it again when deploying the
standalone `resolved.yml`. For OpenClaw, attachment resolves the selected
default agent's workspace and promotes the validated managed install into its
`skills/` directory, OpenClaw's documented highest-precedence catalog, so an
older skill bundled with the harness cannot shadow the commit-bound copy. A
management marker permits safe updates; a different operator-owned same-name
workspace skill makes attachment fail rather than being overwritten. The
installer also merge-adds
`/sandbox/.local/bin` to `tools.exec.pathPrepend` and
`/sandbox/.openclaw/skills` to `skills.load.extraDirs`, retaining existing
operator entries. When the workspace lives below OpenClaw's state directory, a
guarded `/sandbox/vss-openclaw-workspace` alias points to the same bytes and is
recorded on the default agent. This prevents OpenClaw from shortening skill-card
paths to a `~` that the gateway's exec child resolves differently. Together
these let the agent invoke the pre-warmed `uv`/VSS runtime and read the exact
installed skill. Attachment also supplies the receipt, repository, and VSS
origin as explicit non-secret runtime environment values and selects Bash for
the Bash-based VSS skill recipes.
`deploy_nemoclaw.ipynb` applies the same runtime setup for a dedicated agent
and intentionally installs the optional VSS persona. Another harness needs a
capability installer for its
skill/runtime/policy locations, but no new chat connector when it already
speaks a supported wire protocol.

The gateway deliberately neither executes skills nor claims that a connected
backend has them. An unprovisioned Responses endpoint is valid generic chat, but
must fail the VSS readiness gate and must not be presented as a VSS-capable
assistant.

OpenClaw is the primary production-validation target. Hermes has the compatible
Responses surface, but NVIDIA's current platform-support documentation labels
NemoClaw early-preview alpha and does not yet assert Hermes production parity;
stage and qualify that preset for the selected provider and workload before
calling it production-ready.

## Contract

The first protocol version is `1.0`:

- `GET /v1/capabilities`
- `POST /v1/runs` with a new `input` turn and optional recovery `history`
- `GET /v1/runs/{run_id}`
- `GET /v1/runs/{run_id}/events`, with `Last-Event-ID` replay
- `POST /v1/runs/{run_id}/cancel`
- `POST /v1/runs/{run_id}/respond` (advertised only when a future connector supports it)

Run creation accepts `Idempotency-Key`. A repeated key with the same body returns
the original run; the same key with a different body returns `409`.

Normalized events include:

- `run.started`, `run.completed`, `run.failed`, `run.cancelled`
- `message.delta` and `reasoning.delta`
- `tool.started`, `tool.arguments.delta`, `tool.requested`, `tool.completed`, `tool.failed`
- `artifact.created` and `interaction.required`

Errors are terminal `run.failed` events, never assistant prose. Event IDs are
monotonic per run and can be replayed during the configured retention window.
If a consumer falls behind that window after its SSE response has started, the
gateway sends a connection-local `run.failed` event with code `events_expired`;
it does not fail or cancel the backend run for other consumers.
Recovery `history` may contain only the messages before `input`, or a UI's full
transcript ending in the same `input`; the gateway normalizes both forms.

## Connector selection

Select a wire protocol, not a harness:

| Setting                        | Purpose                                | Default                           |
| ------------------------------ | -------------------------------------- | --------------------------------- |
| `AGENT_BACKEND_PROTOCOL`       | `openclaw-ws`, `responses`, or `legacy-chat` | `responses`                  |
| `AGENT_BACKEND_URL`            | Operator-configured backend origin     | required                          |
| `AGENT_BACKEND_PATH`           | Protocol endpoint                      | `/`, `/v1/responses`, or `/chat/stream` |
| `AGENT_BACKEND_TOKEN`          | Upstream bearer credential             | unset                             |
| `AGENT_BACKEND_MODEL`          | Responses/chat model or agent selector | `agent`                           |
| `AGENT_BACKEND_SESSION_FIELD`  | Stable session request field           | `user`                            |
| `AGENT_BACKEND_SESSION_HEADER` | Optional stable-session header         | unset                             |
| `AGENT_BACKEND_HEADERS_JSON`   | Additional upstream headers (secret)   | `{}`                              |
| `AGENT_BACKEND_STATE_DIR`      | Private connector identity state       | `/var/lib/vss-agent-gateway`      |

Production gateway mode also sets `AGENT_REQUIRE_VSS_CAPABILITIES=true` and
supplies `AGENT_VSS_CAPABILITIES_B64`, `AGENT_VSS_CAPABILITIES_SHA256`, and
`AGENT_EXPECTED_VSS_RUNTIME_REF`. Startup fails closed if the receipt is
missing, malformed, incomplete, digest-mismatched, or from another VSS commit.
`GET /v1/capabilities` then exposes only its non-secret readiness summary.
That summary reports verified capability attachment; because the gateway does
not cross the harness policy boundary to probe VSS, deployment readiness must
still test the configured routes from inside the harness.

The Responses connector sends the full UI transcript only when establishing or
recovering a chain. On later turns it verifies that the UI transcript still
matches the saved chain, then sends only the new input with
`previous_response_id`. An edited or regenerated transcript starts a fresh chain
instead of accidentally continuing stale state.

### OpenClaw

Use OpenClaw's native protocol-v4 Gateway WebSocket:

```bash
export AGENT_BACKEND_PROTOCOL=openclaw-ws
export AGENT_BACKEND_URL=ws://127.0.0.1:18789
export AGENT_BACKEND_PATH=/
export AGENT_BACKEND_TOKEN="..."
```

The connector advertises OpenClaw's `tool-events` capability, maps native
`agent`/`session.tool` phases into normalized tool events, and maps `chat`
events into message deltas. Tools, skills, permissions, and execution remain
owned by OpenClaw. The connector never forwards raw tool arguments, partial
command output, or tool results to the browser. It privately inspects completed
results for validated VSS presentation data and emits only the normalized
artifact.

Each UI thread maps to a stable, opaque OpenClaw session key. Cancellation calls
native `chat.abort`. The connector does not fall back to OpenClaw's Responses
endpoint, and the endpoint does not need to be enabled.

The connector signs every `connect.challenge` with an Ed25519 device identity.
Compose persists that private identity in the `agent-gateway-state` volume.
NemoClaw's host-isolated Control UI trust path accepts it immediately. A
hardened standalone OpenClaw Gateway can instead return a one-time pairing
request; approve the reported request ID with `openclaw devices approve
<requestId>`, then retry the run. Keep the OpenClaw bearer credential and the
gateway state volume server-side.

### Hermes

Enable the Hermes API server, then point the same connector at it:

```bash
export AGENT_BACKEND_PROTOCOL=responses
export AGENT_BACKEND_URL=http://127.0.0.1:8642
export AGENT_BACKEND_MODEL=hermes-agent
export AGENT_BACKEND_TOKEN="..."
# Optional stable long-term-memory scope:
export AGENT_BACKEND_SESSION_HEADER=X-Hermes-Session-Key
```

Hermes also has a richer native Runs API with approval events. That can be a
future protocol connector when approval UI is needed; it is not necessary for
portable chat and tool execution.

Skills are installed and governed in the selected backend. The gateway does not
copy skills into prompts or execute shell/tool calls on behalf of the harness.
The OpenClaw connector provides native tool visibility; other connectors render
the structured progress their selected protocol exposes. Backend-internal steps
remain backend internal. Another native protocol can be added as a connector
when a harness exposes approvals, delegation, or tool events that Responses
does not carry.

### How VSS search runs

For OpenClaw and Hermes, the harness matches a search request to
`vss-search-archive`, reads its complete instructions, and invokes the
project-local `vss search run ...` command through its own exec tool. The CLI
talks to the VSS routes recorded by `vss configure`; the gateway sees the
agent's protocol events and normalizes them without taking ownership of the
command.
Analytics requests similarly use `vss-query-analytics` against VA-MCP, while
deployment lifecycle requests use the VSS Orchestrator MCP.

Search and incident-query skills preserve their validated service response as
VSS presentation data. The Responses connector advertises
`vss_ui_publish_artifact` as a standard client function and completes the
function-call round trip when the upstream supports client tools. For native
OpenClaw search, the gateway reads the exact structured exec result: a strict
VSS `SearchOutput` object becomes a result artifact only when the same tool
result also contains its matching successful `vss_job_completed` marker. The
model therefore does not have to reconstruct JSON. A completed tool result can
also carry a versioned `<vss-ui-artifact>` envelope without exposing surrounding
command output. Harnesses such as Hermes that surface backend tool output can
use the same envelope, with final streamed text as the portable fallback. The
gateway validates and deduplicates every path and emits the same
`artifact.created` event.

The Search tab consumes search artifacts as result cards; Chat consumes the
same search and incident artifacts, and an incident artifact also refreshes the
Alerts tab. The publisher is a Responses-protocol extension, while native tool
result extraction belongs to the OpenClaw protocol connector. The envelope is
a VSS presentation contract rather than a harness API. Malformed or unsupported
envelopes remain ordinary text when no valid artifact exists. After a tool has
already supplied a validated artifact, a malformed model-copied envelope is
suppressed as duplicate transport noise. Invalid publisher calls return a tool
error to the agent so it can correct them.

`deploy_nemoclaw.ipynb` installs all nested skills and prepares the exact VSS
CLI revision before chat is exposed. Do not defer a mutable `git clone develop`
or dependency installation to the first search turn.

### VSS Agent

The compatibility connector keeps the existing backend usable while it migrates
to the run/event contract:

```bash
export AGENT_BACKEND_PROTOCOL=legacy-chat
export AGENT_BACKEND_URL=http://vss-agent:8000
export AGENT_BACKEND_PATH=/chat/stream
```

## Run locally

The default bind is loopback. A non-loopback bind fails closed unless a gateway
token is configured (or `AGENT_GATEWAY_ALLOW_INSECURE=true` is explicitly set for
an isolated network).

```bash
export AGENT_BACKEND_URL=http://127.0.0.1:8642
export AGENT_GATEWAY_TOKEN="$(openssl rand -hex 32)"
python3 -m vss_agent_gateway
```

Point the VSS UI server—not browser code—at it:

```bash
export AGENT_GATEWAY_URL=http://127.0.0.1:8090
export AGENT_GATEWAY_TOKEN="..."
```

When `AGENT_GATEWAY_URL` is absent, the existing `/api/chat` path remains active.
When present, `/api/chat` uses the gateway and `/api/agent/*` exposes the structured
same-origin contract for the native renderer migration. Upstream and gateway
tokens never appear in `NEXT_PUBLIC_*` variables.

For a single-host Linux Compose deployment, select the opt-in profile and bind
the gateway to Docker's private bridge address. The service uses host networking
only so it can reach a notebook-managed harness API on host loopback; the UI
reaches the private bind through Docker's `host-gateway` alias:

```bash
export VSS_AGENT_GATEWAY_ENABLED=true
export VSS_AGENT_GATEWAY_BIND_HOST="$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')"
export VSS_AGENT_GATEWAY_PORT=18090
export VSS_AGENT_GATEWAY_URL=http://host.docker.internal:18090
export VSS_AGENT_GATEWAY_TOKEN="$(openssl rand -hex 32)"
export VSS_AGENT_BACKEND_URL=http://127.0.0.1:8642
export VSS_AGENT_BACKEND_TOKEN="..."
export VSS_AGENT_BACKEND_MODEL=hermes-agent
export NEXT_PUBLIC_FORCE_HTTP_CHAT_TRANSPORT=true
export COMPOSE_PROFILES="${COMPOSE_PROFILES:+${COMPOSE_PROFILES},}agent-gateway"
```

The generator sets the HTTP transport lock and disables both WebSocket defaults
whenever the gateway is enabled. This applies to the main Chat tab and the
app-wide chat sidebar, including browsers with a previously saved WebSocket
preference.

The generated gateway-mode Compose graph includes local source builds for both
the gateway and the compatible VSS UI, so the standard deployment lifecycle
(`pull --ignore-buildable`, then `up -d --build`) works from a pinned source
checkout before registry images are published. The image variables name/tag
those builds. A later registry-only deployment may omit the `build:` entries
only when it pins released gateway and UI images containing this contract.

`deploy_nemoclaw.ipynb` prepares OpenClaw's native agent/tool runtime without
enabling its Responses endpoint. The companion
`deploy_vss_orchestrator.ipynb` obtains the selected OpenClaw/Hermes API token
without displaying it, generates an independent gateway token, verifies the
backend, selects `vss-ui` plus `agent-gateway`, and passes the settings into the
resolved Compose deployment. Generated environment and Compose artifacts are
written owner-readable (`0600`) because they contain both credentials.

A successful OpenClaw `/health`, Hermes `/v1/models`, or chat probe proves
transport, not VSS capability.
Before exposing the UI, verify inside the harness that the capability receipt
matches the selected VSS origin and source revision, every canonical skill is
installed, the project CLI answers `vss --version`, and the routes required by
the selected deployment are reachable. For an attached BYO agent, also verify
that the identity hashes did not change. Only the optional dedicated-agent path
uses VSS workspace identity documents and archives its first-turn
`BOOTSTRAP.md`.

Run replay is bounded by `AGENT_GATEWAY_MAX_EVENT_CHARS_PER_RUN` (20 million
characters by default) as well as the event-count limit. Responses continuity
state is kept in memory and bounded by
`AGENT_GATEWAY_MAX_THREAD_STATE_CHARS` (20 million characters by default) and
the configured maximum run count. Eviction safely falls back to the recovery
history supplied by the UI. A gateway restart also uses that recovery path.

Install the pinned runtime dependencies, then run the test suite:

```bash
python3 -m pip install -r requirements.txt
PYTHONPATH=. python3 -m unittest discover -s tests -t . -v
```

## Current storage boundary

Run events and Responses chain metadata are in memory. The OpenClaw device
identity is the exception: Compose persists it in `agent-gateway-state` so
pairing survives container replacement. Run replay works within one gateway
process and the configured retention window. This is suitable for the
single-host Compose deployment above, but not yet for an HA control plane: use
one gateway replica, and expect active replay state to be lost if it restarts.
A durable/shared `RunStore` is required before horizontal scaling or
restart-surviving replay.

This gateway is one trusted-operator boundary, not adversarial multi-tenant
isolation. Put authentication in front of the VSS UI and use separate gateway
instances and backend credentials for users or organizations that do not trust
one another.
