# Chat Interface

## Purpose
The chat interface provides real-time conversational interaction with AI agents through the NeMo Agent toolkit, supporting text input, voice input, and streaming responses with human-in-the-loop workflow capabilities.

## Scope
- Route(s): `/` (main page)
- Primary components: `Chat`, `ChatInput`, `ChatMessage`, `ChatHeader`, `ChatLoader`, `ChatInteractionMessage`
- External deps: WebSocket for real-time streaming, HTTP API endpoints, speech recognition API, speech synthesis API, react-markdown for message rendering

## UI Elements

| Element | Type | Location | Action/Handler | Notes |
|--------|------|----------|----------------|-------|
| Message Input | Textarea | Footer | onChange, onKeyDown | Auto-resizing, supports drag & drop |
| Send Button | Button | Input Right | onSend | Disabled while streaming |
| Stop Button | Button | Top Center | handleStopConversation | Only visible during streaming |
| Regenerate Button | Button | Top Center | onRegenerate | Only visible after assistant response |
| Voice Input | Button | Input Left | handleSpeechToText | Uses browser speech recognition |
| Scroll Down | Button | Bottom Right | onScrollDownClick | Auto-hides when at bottom |
| Message Actions | Buttons | Message Hover | Copy, Edit, Delete, Speak | Per-message actions |

## Component Tree
```
<Chat>
├─ <ChatHeader />
├─ <div className="messages-container">
│  ├─ <MemoizedChatMessage> (for each message)
│  │  ├─ <BotAvatar /> (for assistant messages)
│  │  ├─ <UserAvatar /> (for user messages)
│  │  └─ <MemoizedReactMarkdown /> (message content)
│  └─ <ChatLoader /> (when loading)
├─ <InteractionModal /> (for human-in-the-loop)
└─ <ChatInput>
   ├─ Voice Input Button
   ├─ Textarea
   └─ Send Button
```

## Behavior

**Message Processing:**
- Dual-mode operation: WebSocket streaming and HTTP API calls
- Support for 4 endpoint types: chat, chat/stream, generate, generate/stream
- Real-time message streaming with character-by-character display
- Intermediate steps visualization during AI processing
- Human-in-the-loop workflow integration with interactive modals

**Communication Modes:**
- WebSocket mode for real-time bidirectional communication
- HTTP streaming mode for server-sent events
- Automatic fallback and reconnection handling
- OAuth consent flow integration with new tab redirects

**Message Features:**
- Auto-scrolling to latest messages with manual scroll detection
- Message editing, deletion, and regeneration capabilities
- Copy-to-clipboard functionality for message content
- Voice input via browser speech recognition API
- Text-to-speech playback for accessibility
- Markdown rendering with syntax highlighting

## Source Links
- [components/Chat/Chat.tsx](../../../components/Chat/Chat.tsx)
- [components/Chat/ChatInput.tsx](../../../components/Chat/ChatInput.tsx)
- [components/Chat/ChatMessage.tsx](../../../components/Chat/ChatMessage.tsx)
- [components/Chat/ChatHeader.tsx](../../../components/Chat/ChatHeader.tsx)
- [components/Chat/ChatLoader.tsx](../../../components/Chat/ChatLoader.tsx)
- [components/Chat/ChatInteractionMessage.tsx](../../../components/Chat/ChatInteractionMessage.tsx)