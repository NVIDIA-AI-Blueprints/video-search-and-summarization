# @nv-metropolis-bp-vss-ui/chat

VSS chat interface for the chat tab and the docked sidebar.

**No NeMo Agent Toolkit dependency** — that is the reason this package exists.
It speaks the BYO agent contract directly (OpenAI-shaped request in,
Server-Sent Events out), so any backend implementing `/chat/stream` works,
including the VSS agent adapter driving OpenClaw or Hermes.

## Usage

```tsx
import { ChatPanel } from '@nv-metropolis-bp-vss-ui/chat';
import '@nv-metropolis-bp-vss-ui/chat/lib/chat.css';

<ChatPanel
  endpoint={{ url: process.env.NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL! }}
  title="Vision Agent"
  theme="dark"
  onAnswer={(answer) => forwardToSearchTab(answer)}
  onSubmit={() => clearStaleResults()}
/>
```

`onAnswer` / `onSubmit` are how feature tabs (search, alerts) receive results
and clear stale state, replacing `registerChatAnswerHandler` and
`registerSidebarChatEventSubscriber` without the chat package needing to know
those tabs exist.

## What it renders

- streaming assistant text, markdown with GFM tables
- collapsed tool/skill progress from `intermediate_data:` frames
- per-message errors, and a Stop button that aborts the in-flight turn

## Wire protocol

| Line | Meaning |
|---|---|
| `data: {"choices":[{"delta":{"content":"…"}}]}` | assistant text |
| `data: [DONE]` | turn complete |
| `intermediate_data: {…}` | tool/skill step |
| `: keepalive` | ignored |

Content is also read from `choices[0].message.content` and the plain
`value` / `output` / `answer` fields, because agent servers differ.

## Status

Foundation complete and unit-tested (`SseParser`, 14 assertions). Not yet wired
into `Home.tsx`, and does not yet cover the toolkit's video modal, chunked file
upload, or conversation history — see DECISIONS.md for the migration plan.
