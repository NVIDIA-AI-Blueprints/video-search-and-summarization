# VSS ⇄ BYO Agent Adapter (POC)

Reference adapter letting the VSS UI drive a **NemoClaw / OpenClaw** agent, so a user can
bring their own agent or harness instead of the built-in `vss-agent`.

```
VSS UI ──HTTP/SSE──> adapter ──WebSocket──> OpenClaw gateway ──> skills ──> VSS backends
```

Design rationale is inline below; the OpenClaw gateway's undocumented WebSocket handshake
quirks are recorded in `adapter.py`'s module docstring, which is where they are needed.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/chat/stream` | OpenAI-shaped `{messages}` in, `text/event-stream` out |
| `GET` | `/health` | liveness + resolved gateway/session config |
| `GET` | `/v1/skills` | manifest: name, path, detected `requirements` per skill |
| `GET` | `/v1/skills/<name>` | one skill's `SKILL.md` (fetched on demand by the agent) |
| `GET` | `/v1/skills/<name>/bundle.tar.gz` | one skill incl. `scripts/` and `references/` |
| `GET` | `/v1/skills/bundle.tar.gz` | all skills, version-matched to this deployment |
| `GET` | `/v1/skills/env` | resolved VSS base URLs (replaces `HOST_IP` guessing) |
| `POST` | `/v1/search` | archive search over HTTP (runs the host `vss` CLI) |

The request/response shape is not invented — it is what the VSS UI already speaks
(`packages/nemo-agent-toolkit-ui/pages/api/chat.ts`). Any backend implementing
`/chat/stream` can be swapped in by changing one env var, which is the point.

## Run

```bash
ADAPTER_PORT=9098 \
OPENCLAW_GATEWAY_TOKEN="$(nemoclaw <sandbox> gateway-token | head -1)" \
  python3 adapter.py
```

| Env | Default | Notes |
|---|---|---|
| `OPENCLAW_GATEWAY_URL` | `ws://localhost:18789/` | OpenShell forward to the sandbox gateway |
| `OPENCLAW_GATEWAY_TOKEN` | *(required)* | `nemoclaw <sandbox> gateway-token` |
| `OPENCLAW_SESSION_PREFIX` | `agent:main:vss` | one session per UI conversation |
| `ADAPTER_PORT` | `9099` | |
| `ADAPTER_TURN_TIMEOUT` | `600` | seconds |
| `VSS_SKILLS_DIR` | `~/video-search-and-summarization/skills` | source for the skills endpoints |
| `VSS_HOST_ALIAS` | `host.openshell.internal` | advertised by `/v1/skills/env` |
| `ADAPTER_BOOTSTRAP` | `1` | set `0` to disable first-turn context injection |
| `ADAPTER_PUBLIC_URL` | `http://<alias>:<port>` | base URL the agent is told to fetch from |
| `VSS_REPO_ROOT` | `~/video-search-and-summarization` | checkout used by `/v1/search` |
| `UV_BIN` | auto-detected | `uv` used to run the CLI |
| `ADAPTER_SEARCH_TIMEOUT` | `180` | seconds |
| `ADAPTER_ALLOW_CIDRS` | `127.0.0.1/32,::1/128,172.16.0.0/12` | callers allowed to reach the adapter |
| `ADAPTER_TOKEN` | *(unset)* | if set, required via `Authorization: Bearer`, `X-Adapter-Token`, or `?token=` |
| `ADAPTER_SSE_KEEPALIVE` | `15` | seconds between SSE keepalive comments |
| `AGENT_SESSION_FIELD` | `user` | how the conversation is named to the harness; empty to send nothing |
| `AGENT_SESSION_IN` | `body` | `body` or `header` |
| `AGENT_SESSION_SALT` | *(random per process)* | set to keep conversations continuous across restarts |
| `AGENT_BACKEND` | `openclaw` | `openclaw` (WebSocket gateway) or `hermes` (OpenAI-compatible API) |
| `HERMES_API_URL` | `http://127.0.0.1:8642/v1/chat/completions` | Hermes agent API |
| `HERMES_TOKEN` | *(unset)* | `nemoclaw <sandbox> gateway-token --quiet` |
| `HERMES_MODEL` | `hermes-agent` | |

## Harnesses

`AGENT_BACKEND=openclaw` drives OpenClaw's WebSocket gateway — handshake, scopes, session
creation, event translation.

`AGENT_BACKEND=hermes` drives Hermes, which already exposes an OpenAI-compatible API on
`:8642/v1`. That driver is ~60 lines and mostly passthrough: attach the bearer, prepend the
bootstrap, re-emit deltas. Both can run at once on different ports.

## Conversation identity

OpenClaw and Hermes both accept the OpenAI `user` body field and derive a stable
session from it, so the default needs no configuration and stays vendor-neutral. Both
also offer their own names, which differ (`x-openclaw-session-key` vs
`X-Hermes-Session-Key`, `user` vs `conversation`) and sit in different places — so the
name and its location are config, not code:

```bash
AGENT_SESSION_FIELD=x-openclaw-session-key AGENT_SESSION_IN=header
AGENT_SESSION_FIELD=conversation           AGENT_SESSION_IN=body
```

Verified live in all three combinations: `user` on OpenClaw, `user` on Hermes, and
`x-openclaw-session-key` as a header on OpenClaw.

**The value is derived here, never forwarded from the caller.** OpenClaw does not
validate session-key ownership ([openclaw#11793](https://github.com/openclaw/openclaw/issues/11793),
CVSS 8.1, closed as not planned) and its keys are predictable, so passing a
client-supplied conversation id straight through would let one caller land on another's
session and read or drive it. The id is HMAC'd with a process secret first, which keeps
it stable per conversation while making it unguessable.

## Access control

The adapter holds the gateway token and can drive the agent, so it must not be reachable
by anything that can route to it. It binds `0.0.0.0` by necessity (the UI container and
the sandbox both reach it over docker bridges), so the control is a **caller allowlist**
plus an **optional shared token**.

`?token=` exists because `chat.ts` sends a fixed header set with no auth header but passes
its configured URL through verbatim — a query param is the only way to authenticate
without changing UI code. It is redacted from the request log.

> **Known limitation:** setting `ADAPTER_TOKEN` also gates the agent's own callbacks
> (`/v1/skills*`, `/v1/search`), and the bootstrap hands the agent `ADAPTER_PUBLIC_URL`
> without a token — so the agent gets `403` and cannot fetch skills or search. Until that
> is resolved, rely on `ADAPTER_ALLOW_CIDRS` alone, which already restricts callers to the
> docker bridges. Resolving it means either embedding the token in the bootstrap URLs
> (putting a credential in the model's context) or requiring the token only on
> `/chat/stream` — a call worth making deliberately.

## Archive search

`POST /v1/search` with
`{"mode":"embed","query":"...","top_k":10,"source_type":"video_file"}`.

`vss-search-archive` needs `uv` and a source checkout, which a sandboxed or hosted agent
does not have. This runs the same CLI host-side and returns the same `SearchOutput`.

Requires `vss configure --base-url <origin>` once on the host, and **the adapter port must
be listed in `assets/vss_nemoclaw_policy.yaml`** or sandbox calls fail as `policy_denied`.

## Bootstrap

On a session's **first turn** the adapter prepends a deployment-context block: where VSS
is, the skills index (name + description), how to fetch a skill's full instructions, and
the VSS conventions. ~6.6 KB.

Each skill is listed as `- <name> [needs: uv, docker, ...]: <description>` so the agent
sees the cost at selection time. Without it the agent refuses for plausible-but-wrong
reasons: a skill needing `uv` or a source checkout is unrunnable inside a sandbox, and
the agent cannot tell that from the description alone.

This is what makes BYO work without an install step — it needs nothing from the harness
but the ability to accept text, and skill bodies (~345 KB across 18 skills) stay remote
until one is actually needed.

Requires `websocket-client` (`pip install websocket-client`).

## Point the UI at it

The UI reads `NEXT_PUBLIC_*` at **container start** (`next-runtime-env`, `/__ENV.js`), so
this is an env change plus a restart — no rebuild:

```yaml
NEXT_PUBLIC_SIDEBAR_CHAT_HTTP_CHAT_COMPLETION_URL: http://<docker-bridge-gw>:9098/chat/stream
```

`chat.ts` is a Next.js *edge API route*, so the fetch happens server-side from inside the
UI container — the adapter does not need to be publicly reachable.

> **Gotcha:** if the chat is in WebSocket mode the adapter is bypassed entirely and
> silently, because the WS URL still points at the old backend. `sessionStorage`
> overrides the env default, so a browser that once had WS mode on keeps using it. Fix:
> toggle WebSocket Mode off in chat settings, or
> `sessionStorage.removeItem('webSocketMode')`.

## Verify which backend served a turn

```bash
grep 'POST /chat/stream' adapter.log          # adapter received it
docker logs vss-agent | grep WebSocket        # old backend received it
```

On the gateway, only this adapter creates `agent:main:vss-<conversation-id>` sessions, so
their presence in `sessions.list` is unambiguous proof.

## Status

POC. Known gaps:

- The adapter holds a gateway token, so it relies on the caller allowlist and optional
  shared token rather than being safe to expose.
- The Responses API streams text only — no tool/step events, so intermediate-step panels
  stay empty. Use `AGENT_BACKEND=openclaw` (WebSocket) when step visibility matters.
- OpenClaw's Responses endpoint answers from one shared session, so separate UI
  conversations share memory. History is carried in the prompt instead.
- A model's chain-of-thought can leak into replies; it is not separable from the answer,
  so it is not filtered here.
