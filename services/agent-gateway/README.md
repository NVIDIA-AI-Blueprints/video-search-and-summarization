# VSS Agent Gateway

The VSS UI talks to this service through one versioned run/event protocol. Agent
harnesses sit behind protocol connectors, so the UI never branches on names such
as OpenClaw, Hermes, or VSS Agent.

```text
VSS UI  ->  /api/agent/*  ->  VSS run/event contract  ->  protocol connector  ->  agent backend
                                       |                       |
                              replay/cancel/auth         Responses or legacy chat
```

AG-UI is not required. It can be added later as one more connector if an upstream
only exposes AG-UI; it should not become the UI's internal contract.

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
Recovery `history` may contain only the messages before `input`, or a UI's full
transcript ending in the same `input`; the gateway normalizes both forms.

## Connector selection

Select a wire protocol, not a harness:

| Setting                        | Purpose                                | Default                           |
| ------------------------------ | -------------------------------------- | --------------------------------- |
| `AGENT_BACKEND_PROTOCOL`       | `responses` or `legacy-chat`           | `responses`                       |
| `AGENT_BACKEND_URL`            | Operator-configured backend origin     | required                          |
| `AGENT_BACKEND_PATH`           | Protocol endpoint                      | `/v1/responses` or `/chat/stream` |
| `AGENT_BACKEND_TOKEN`          | Upstream bearer credential             | unset                             |
| `AGENT_BACKEND_MODEL`          | Responses/chat model or agent selector | `agent`                           |
| `AGENT_BACKEND_SESSION_FIELD`  | Stable session request field           | `user`                            |
| `AGENT_BACKEND_SESSION_HEADER` | Optional stable-session header         | unset                             |
| `AGENT_BACKEND_HEADERS_JSON`   | Additional non-secret routing headers  | `{}`                              |

The Responses connector sends the full UI transcript only when establishing or
recovering a chain. On later turns it verifies that the UI transcript still
matches the saved chain, then sends only the new input with
`previous_response_id`. An edited or regenerated transcript starts a fresh chain
instead of accidentally continuing stale state.

### OpenClaw

Enable OpenClaw's Responses endpoint, then use the generic connector:

```bash
export AGENT_BACKEND_PROTOCOL=responses
export AGENT_BACKEND_URL=http://127.0.0.1:18789
export AGENT_BACKEND_MODEL=openclaw/default
export AGENT_BACKEND_TOKEN="..."
# Optional explicit routing in addition to the standard `user` field:
export AGENT_BACKEND_SESSION_HEADER=x-openclaw-session-key
```

The OpenClaw Responses endpoint runs a normal agent turn, so tools, skills, and
permissions remain owned by OpenClaw. Treat its bearer credential as operator
access and do not expose it to the browser.

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
Tool visibility is best-effort: the UI can render structured progress that the
selected upstream protocol emits, while backend-internal steps remain backend
internal. A richer native connector is appropriate when a harness exposes
approval, delegation, or tool events that Responses does not carry.

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

`deploy_nemoclaw.ipynb` enables OpenClaw's Responses endpoint. The companion
`deploy_vss_orchestrator.ipynb` obtains the selected OpenClaw/Hermes API token
without displaying it, generates an independent gateway token, verifies the
backend, selects `vss-ui` plus `agent-gateway`, and passes the settings into the
resolved Compose deployment. Generated environment and Compose artifacts are
written owner-readable (`0600`) because they contain both credentials.

Run the dependency-free test suite:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -t . -v
```

## Current storage boundary

Run events and Responses chain metadata are in memory. Reconnect works within one
gateway process and the configured retention window. This is suitable for the
single-host Compose deployment above, but not yet for an HA control plane: use
one gateway replica, and expect active replay state to be lost if it restarts.
A durable/shared `RunStore` is required before horizontal scaling or
restart-surviving replay.

This gateway is one trusted-operator boundary, not adversarial multi-tenant
isolation. Put authentication in front of the VSS UI and use separate gateway
instances and backend credentials for users or organizations that do not trust
one another.
