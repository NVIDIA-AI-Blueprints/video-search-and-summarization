<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

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
An OpenClaw harness must provide the isolated `vss-ui` agent because the
adapter routes every UI thread there and never falls back to `main`.

Never place a backend credential in a `NEXT_PUBLIC_*` variable. In Docker, the
harness forward binds to Docker's private bridge address and the UI connects to
`host.docker.internal`; the port is not published on an external interface.

## Follow-up questions

Structured interaction responses are deliberately unsupported in the launch
configuration. An OpenClaw agent must ask a required question in its ordinary
assistant response and finish that turn. The user's reply creates a new run on
the same UI thread, which the connector maps back to the same OpenClaw session.

Set `NEXT_PUBLIC_ENABLE_HITL=false` for an adapter-backed chat surface. The
sidebar-specific `NEXT_PUBLIC_SIDEBAR_CHAT_ENABLE_HITL` takes precedence when
set. Both default to false, and the UI suppresses the legacy response modal
whenever the adapter is enabled even if a public flag is accidentally true. Set
the flags to true only when a legacy `vss-agent` chat-SSE surface has also
explicitly enabled its structured interaction tools.

## Same-origin API

- `GET /api/agent/capabilities`
- `POST /api/agent/runs`
- `GET /api/agent/runs/<run_id>`
- `GET /api/agent/runs/<run_id>/events`
- `POST /api/agent/runs/<run_id>/cancel`

Run creation accepts an optional `Idempotency-Key`. Event streams support
`Last-Event-ID` replay while retained. Interaction responses remain
unsupported and return a conflict response.

## Snapshot artifacts

Snapshot tool results are normalized at the adapter boundary, independently of
the selected harness protocol. The adapter recognizes the VSS CLI
`kind: "snapshot"`/`media_url` result, native agent `snapshot_urls`, and
`image_url` tool results. OpenClaw image-read results are also accepted when
they contain a bounded base64 payload with an allowlisted raster MIME type;
SVG and malformed payloads are rejected. The adapter emits a version 1.0
`vss.media.image` artifact, which the shared chat renderer displays with
fullscreen and download controls.

VIOS container hostnames and bare `/storage` paths are rewritten to the UI's
same-origin `/vst/...` route. Sandbox-local paths such as `/tmp/image.jpg` are
not browser-accessible and are rejected instead of being presented as a broken
image. When OpenClaw reads such a file as an image attachment, the validated
bytes are carried in the artifact instead of exposing the sandbox path.
