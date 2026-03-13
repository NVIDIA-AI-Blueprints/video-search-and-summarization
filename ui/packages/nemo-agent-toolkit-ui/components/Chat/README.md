# Chat Components

This directory contains all components related to the chat interface functionality.

## Components Overview

### Core Chat Components
- **Chat.tsx** - Main chat container and message orchestration
- **ChatInput.tsx** - Message input with voice features  
- **ChatMessage.tsx** - Individual message display and interactions
- **ChatHeader.tsx** - Chat header with conversation title and actions
- **ChatLoader.tsx** - Loading state indicator during message processing

### Specialized Components  
- **ChatInteractionMessage.tsx** - Human-in-the-loop interaction modal
- **MemoizedChatMessage.tsx** - Performance-optimized message wrapper
- **ErrorMessageDiv.tsx** - Error message display component
- **Regenerate.tsx** - Message regeneration functionality

## Behavior

**Real-time Streaming:**
- WebSocket connection handles live message updates
- Messages stream character-by-character for natural conversation flow
- Loading states show when assistant is processing
- Stop button allows canceling ongoing responses

**Message Management:**
- Messages support editing, deletion, and regeneration
- Conversation history persists across sessions
- Copy functionality for sharing message content
- Text-to-speech playback for accessibility

**Human-in-the-Loop:**
- Interactive modals for user approval workflows
- OAuth consent handling with new tab redirects
- Workflow pause/resume based on user input
- Context preservation during interactions

## Key Features
- **Dual Communication Modes**: WebSocket streaming and HTTP API endpoints
- **4 API Endpoint Types**: chat, chat/stream, generate, generate/stream with automatic routing
- **Real-time Streaming**: Character-by-character message display with stop/resume controls
- **Human-in-the-Loop Workflows**: Interactive modals with OAuth consent handling via new tabs
- **Voice Integration**: Speech-to-text input and text-to-speech output
- **Intermediate Steps**: Visualization of AI reasoning process during generation
- **Markdown Rendering**: Full markdown support with syntax highlighting and custom components
- **Message Management**: Edit, delete, regenerate, copy, and organize conversations
- **Responsive Design**: Optimized for mobile and desktop with auto-scroll management

## Related Documentation
See [docs/ui/chat/chat-interface.md](../../docs/ui/chat/chat-interface.md) for detailed feature documentation.