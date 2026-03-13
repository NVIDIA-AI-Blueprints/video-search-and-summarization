# NeMo Agent Toolkit UI Documentation

## Overview
This directory contains comprehensive documentation for the NeMo Agent toolkit UI, a React/Next.js application that provides a modern chat interface for AI agent interactions.

## Documentation Structure

### Feature Documentation
- **[Chat Interface](./chat/chat-interface.md)** - Real-time conversational interface with streaming and voice input
- **[Sidebar Navigation](./sidebar/conversation-management.md)** - Conversation organization, search, and folder management
- **[Configuration Management](./settings/configuration-management.md)** - API configuration, import/export, and application settings
- **[Button Reference](./button-reference.md)** - Comprehensive guide to all interactive buttons in the UI

### Component Documentation
Each component directory contains a README.md with detailed behavior and integration information:

- **[Chat Components](../../components/Chat/README.md)** - Core chat functionality and message handling
- **[Chatbar Components](../../components/Chatbar/README.md)** - Conversation management and organization
- **[Sidebar Components](../../components/Sidebar/README.md)** - Generic sidebar layout and controls
- **[Folder Components](../../components/Folder/README.md)** - Collapsible organization containers

## Key Features
- **Real-time Chat Streaming** via WebSocket connections and HTTP streaming
- **Multiple API Endpoints** supporting both chat and generate modes (4 total: chat, chat/stream, generate, generate/stream)
- **Human-in-the-Loop Workflows** with interactive modals and OAuth consent handling via new tabs
- **Intermediate Steps Visualization** showing AI reasoning process
- **Conversation Organization** with folders and search functionality
- **Data Import/Export** for conversation backup and migration
- **Voice Input/Output** with speech recognition and text-to-speech
- **Dark/Light Theme** support with system detection
- **Markdown Rendering** with syntax highlighting and custom components

## API Endpoints
The application supports 4 distinct API endpoint modes:
- **chat** - Standard chat completion (HTTP)
- **chat/stream** - Streaming chat with SSE (HTTP)
- **generate** - AI generation tasks (HTTP)  
- **generate/stream** - Streaming generation with intermediate steps (HTTP)

## WebSocket Message Types
- **system_response_message** - Assistant responses with streaming content
- **system_intermediate_message** - AI reasoning steps and workflow progress
- **system_interaction_message** - Human-in-the-loop prompts and OAuth flows
- **error** - Error handling and validation messages

## Tech Stack
- **Framework:** Next.js 13+ with React 18
- **Language:** TypeScript for type safety
- **Styling:** Tailwind CSS for responsive design
- **State:** React Context + useReducer pattern
- **Real-time:** WebSocket for streaming responses
- **Markdown:** react-markdown with custom components
- **Charts:** Recharts for data visualization
- **Icons:** Tabler Icons for consistent iconography
- **i18n:** next-i18next for internationalization