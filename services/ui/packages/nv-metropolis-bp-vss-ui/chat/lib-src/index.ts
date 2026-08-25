// SPDX-License-Identifier: MIT
/**
 * VSS chat interface.
 *
 * Replaces the NeMo Agent Toolkit chat UI for the chat tab and the docked
 * sidebar. Depends on no toolkit code: it speaks the BYO agent contract
 * (OpenAI-shaped request, SSE response) directly, so any backend implementing
 * `/chat/stream` works, including the VSS agent adapter driving OpenClaw or
 * Hermes.
 */
export { ChatPanel, default as default } from './ChatPanel';
export { useChatStream } from './useChatStream';
export type { UseChatStreamResult } from './useChatStream';
export { SseParser, extractContent } from './sse';
export type { SseEvent } from './sse';
export type {
  ChatAnswerHandler,
  ChatEndpointConfig,
  ChatMessage,
  ChatPanelProps,
  ChatRole,
  ChatStep,
} from './types';
