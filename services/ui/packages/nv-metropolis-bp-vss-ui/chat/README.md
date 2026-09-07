<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# @nv-metropolis-bp-vss-ui/chat

VSS chat interface for the chat tab and the docked sidebar.

**No NeMo Agent Toolkit dependency** — that is the reason this package exists.
Its primary transport is the versioned VSS agent API exposed by the embedded
adapter. The original OpenAI-shaped chat-SSE transport remains available for
deployments that have not enabled that adapter.

## Source provenance

Components and helpers marked `MIT AND Apache-2.0` retain code derived from the
MIT-licensed NeMo Agent Toolkit UI and include contributions made in this
repository under Apache-2.0. Files marked only `Apache-2.0` are original work
authored for this repository.

## Usage

```tsx
import { ChatPanel } from '@nv-metropolis-bp-vss-ui/chat';
import '@nv-metropolis-bp-vss-ui/chat/lib/chat.css';

<ChatPanel
  endpoint={{
    url: '/api/agent',
    transport: 'agent-api',
    surface: 'vss-ui-sidebar',
    mediaProxyUrl: '/api/proxy',
    uploadUrlBase: vstApiBase,
  }}
  title="Vision Agent"
  theme="dark"
  storageKeyPrefix="appSidebar"
  features={{ uploadFile: true, messageCopy: false }}
  onAnswer={(answer, conversationId) => forwardToSearchTab(answer, conversationId)}
  onSubmit={() => clearStaleResults()}
  onSubmitMessageReady={(submit) => (submitRef.current = submit)}
  onAddQueryContextReady={(add) => (addContextRef.current = add)}
  onControlsReady={(handlers) => setSidebarControls(handlers)}
/>
```

## Feature parity with the toolkit chat bar

The toolkit chat bar is ~11k lines across `Chat`, `Chatbar`, `Markdown`,
`Settings` and `Sidebar`. Everything below is reproduced here; the flag column
gives the `NEXT_PUBLIC_*` variable the deployment already sets, which
`Home.tsx` maps onto the `features` prop, so an existing deployment needs no
new configuration.

| Area | Feature | Flag |
|---|---|---|
| Conversations | multiple threads, new / rename / delete / clear | — |
| | tab-scoped IndexedDB persistence, `storageKeyPrefix` | — |
| | search over names and message text | — |
| | export / import (toolkit v1–v4 files load) | — |
| | send full thread vs. latest turn | `CHAT_HISTORY_DEFAULT_ON` |
| Input | context chips + `[Context: …]` prefix | — |
| | chunked video upload, drag & drop, progress, cancel | `CHAT_UPLOAD_FILE_ENABLE` |
| | per-file upload metadata | `CHAT_UPLOAD_FILE_METADATA_ENABLED` |
| | post-upload auto-prompt | `CHAT_UPLOAD_FILE_HIDDEN_MESSAGE_TEMPLATE` |
| | agent parameter panel | `CHAT_API_CUSTOM_AGENT_PARAMS_JSON` |
| | speech-to-text | `CHAT_INPUT_MIC_ENABLED` |
| | regenerate, stop, scroll-to-latest, auto-grow textarea | — |
| Messages | streaming markdown, GFM tables, math | — |
| | images (fullscreen, download), video, charts, incidents | — |
| | code blocks (highlight, copy, download) | — |
| | `<agent-think>` reasoning traces | — |
| | nested intermediate-step tree (see caveat below) | `ENABLE_INTERMEDIATE_STEPS` |
| | copy / speak / edit / delete | `CHAT_MESSAGE_{COPY,SPEAKER,EDIT}_ENABLED` |
| | caller-info card (sanitised HTML from the host) | — |
| Shell | welcome screen with drop zone, header menu, theme toggle | `SHOW_THEME_TOGGLE_BUTTON` |
| Embedding | `onAnswer`, `onAnswerComplete`, `onSubmit` | — |
| | `onSubmitMessageReady`, `onMessageSubmitted` | — |
| | `onAddQueryContextReady`, `onChatVideoUploadComplete` | — |
| | `onBusyChange`, `onControlsReady`, `isActive` | — |

### Per-surface configuration

The chat tab and the docked sidebar are configured independently, exactly as
they were for the toolkit: the sidebar overrides any main variable by
re-declaring it under `NEXT_PUBLIC_SIDEBAR_CHAT_`, and falls back to the main
value otherwise. This deployment relies on it — the sidebar runs a different
agent from the chat tab and exposes its own parameter fields (search source
type, critic toggle). `Home.tsx` resolves both surfaces through `surfaceEnv`.

### Deliberately not ported

- **WebSocket transport** and its `webSocketSchema` / connection toggle. Both
  supported transports are HTTP + SSE, the deployment ships
  `NEXT_PUBLIC_WEB_SOCKET_DEFAULT_ON=false`, and the WebSocket path exists to
  talk to NAT core — the thing being removed.
- **Human-in-the-loop interaction responses.** Current adapter connectors
  advertise `interaction_responses: false`, so an interaction event is shown
  as unsupported instead of presenting a form that cannot submit a response.
- **Folders and prompt templates.** Present in the toolkit's Chatbar, never
  surfaced in VSS. Import still accepts and preserves both keys so a toolkit
  export round-trips.
- **`GraphPlot` charts** (`react-force-graph-2d`). Not installed in this
  workspace and not emitted by any VSS workflow; the type renders as
  "unsupported" rather than pulling in a graph engine for a case that never
  fires.

### Deliberate differences

- **Intermediate steps are a React tree, not serialised HTML.** The toolkit
  rendered steps by writing a `<details>` cascade into the message's markdown
  and re-parsing it, which is why half its `fixMalformedHtml` helpers exist. The
  tree stays structured here; the repairs are kept only for markup the *model*
  emits mid-stream (`markdown/streaming.ts`).
- **Pie slice colours are a fixed palette.** The toolkit called
  `getRandomColor()` inside render, so every streamed token recoloured the
  chart.
- **Syntax highlighting loads on demand.** Prism and its grammars are several
  hundred kilobytes and only needed once a fenced block has stopped changing.

## Wire protocol

With `transport: 'agent-api'`, a turn creates a run through `POST /runs` and then
reads the returned `events_url` as SSE. Every `data:` payload is a versioned
agent API event.

| Agent API event | UI behavior |
|---|---|
| `run.started`, `run.completed` | run status step and terminal state |
| `message.delta` | assistant text |
| `reasoning.delta` | reasoning step |
| `tool.*` | keyed tool-step updates |
| `artifact.created` | validated artifact delivered to `onAnswer`, not rendered as prose |
| `interaction.required` | unsupported-interaction error (no response UI) |
| `run.failed`, `run.cancelled` | terminal error |

Absolute `*_url` values in artifact payloads are rewritten through
`mediaProxyUrl`. Aborting a turn also posts to the run's `cancel_url`.

When `transport` is omitted or set to `chat-sse`, the compatibility parser
accepts the original line protocol:

| Legacy line | Meaning |
|---|---|
| `data: {"choices":[{"delta":{"content":"…"}}]}` | assistant text |
| `data: [DONE]` | turn complete |
| `intermediate_data: {…}` | tool/skill step (`parent_id` nests it) |
| `error_data: {…}` | turn-level failure |
| `interaction_data: {…}` | unsupported-interaction error |
| `: keepalive` | ignored |

Legacy content is also read from `choices[0].message.content` and the plain
`value` / `output` / `answer` fields because agent servers differ.

## Tests

`npm test` runs two Jest projects: `logic` (node, no DOM) for the parsers,
import/export and markdown repairs, and `components` (jsdom) which drives
`ChatPanel` against a faked SSE stream.
