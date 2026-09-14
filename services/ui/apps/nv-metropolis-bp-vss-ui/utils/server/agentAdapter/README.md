# Embedded external-agent adapter

The VSS UI's Next.js process contains the trusted adapter between browser chat
and an external agent harness. It is not a separate service or image.

The browser uses the same-origin `/api/agent` run/event API. The server keeps
the harness credential private, maps each UI thread to an isolated upstream
session, normalizes OpenClaw native WebSocket or Responses events, supports
cancellation and SSE replay, and validates VSS UI artifacts before returning
them to the browser. The existing `/api/chat` route is retained as a legacy UI
compatibility bridge.

The implementation uses only Node.js built-ins and dependencies already
required by the UI. No runtime package was added for this adapter.

## Server environment

Set `AGENT_ADAPTER_ENABLED=true` and `AGENT_BACKEND_URL` to enable the adapter.
The principal settings are:

- `AGENT_ADAPTER_ENABLED`: explicit profile-level adapter switch.
- `AGENT_INTERACTIONS_ENABLED`: enables OpenClaw structured questions. This is
  opt-in so ordinary chat remains compatible with older gateways. NemoClaw's
  pinned OpenClaw uses the Orchestrator MCP broker described below; a BYO
  OpenClaw uses native questions and requires OpenClaw 2026.8.1 or newer plus
  a token that grants `operator.questions`.
- `AGENT_INTERACTION_BROKER_DIR`: container path shared with the Orchestrator
  `ask_user_question` MCP tool. Set by the NemoClaw deployment path; leave it
  unset for a BYO OpenClaw that supports native `question.*` events.
- `AGENT_BACKEND_PROTOCOL`: `openclaw-ws`, `responses`, or `legacy-chat`.
- `AGENT_BACKEND_URL` and `AGENT_BACKEND_PATH`: private harness location.
- `AGENT_BACKEND_TOKEN`: server-only harness credential.
- `AGENT_BACKEND_MODEL`: Responses or legacy-chat model selector.
- `AGENT_BACKEND_SESSION_FIELD` and `AGENT_BACKEND_SESSION_HEADER`: optional
  Responses session routing.
- `AGENT_RUN_RETENTION_SECONDS` and `AGENT_MAX_*`: optional in-process replay
  retention and memory bounds. `AGENT_MAX_RETAINED_CHARS` bounds all retained
  run requests, events, and Responses thread state; it defaults to 64 million
  serialized characters.

The adapter connects to an already-configured harness. It does not install
Skills, provision a CLI, or modify the harness's identity, memory, or history.

Never place a backend credential in a `NEXT_PUBLIC_*` variable. In Docker, the
harness must separately be reachable from `host.docker.internal`; PR #2183
tracks the NemoClaw bridge-binding setup. The HITL broker does not alter gateway
network exposure.
The Compose deployment mounts `.orchestrator-artifacts/interactions` into the
UI for broker responses. The browser never sees or writes that directory.

## Same-origin API

- `GET /api/agent/capabilities`
- `POST /api/agent/runs`
- `GET /api/agent/runs/<run_id>`
- `GET /api/agent/runs/<run_id>/events`
- `POST /api/agent/runs/<run_id>/cancel`
- `POST /api/agent/runs/<run_id>/respond`

Run creation accepts an optional `Idempotency-Key`. Event streams support
`Last-Event-ID` replay while retained. A run can emit `interaction.required`
with one to three structured questions. Post the matching `interaction_id` and
`response: {type: "questions", answers: {<question_id>: [<answer>]}}` to its
`respond_url`; the response resumes that same run and thread.
