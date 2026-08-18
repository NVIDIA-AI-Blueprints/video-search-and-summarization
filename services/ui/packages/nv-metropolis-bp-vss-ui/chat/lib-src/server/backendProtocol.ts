// SPDX-License-Identifier: MIT
/**
 * Agent backend protocols.
 *
 * Backends are described by the shape of their wire protocol, not by vendor.
 * `openai-chat` covers vss-agent and any OpenAI-compatible agent a customer
 * brings; `nat-generate` is the toolkit's single-input form.
 */

export type BackendProtocol =
  | 'openai-chat'
  | 'openai-chat-stream'
  | 'nat-generate'
  | 'nat-generate-stream';

export type ChatMessage = {
  role: string;
  content: string;
  [key: string]: unknown;
};

/** Extra body keys are forwarded verbatim as agent-specific parameters. */
export type CustomAgentParams = Record<string, unknown>;

/**
 * Infers the protocol from the endpoint path.
 *
 * Callers should send `protocol` explicitly; this exists so an endpoint
 * configured only as a URL still works. Inference is a fallback, not the
 * contract — a bring-your-own agent on `/v1/chat/completions` gets the
 * OpenAI shape rather than silently falling through to a non-streaming path.
 */
export function inferProtocol(endpoint: string): BackendProtocol {
  const path = endpoint.split('?')[0];

  if (/\/generate\/stream\/?$/.test(path)) return 'nat-generate-stream';
  if (/\/generate\/?$/.test(path)) return 'nat-generate';
  if (/\/stream\/?$/.test(path)) return 'openai-chat-stream';

  return 'openai-chat';
}

export function isStreamingProtocol(protocol: BackendProtocol): boolean {
  return protocol === 'openai-chat-stream' || protocol === 'nat-generate-stream';
}

/**
 * Body for the toolkit's generate endpoints, which take the latest user turn
 * rather than a transcript.
 */
export function buildGeneratePayload(
  messages: ChatMessage[],
  customAgentParams: CustomAgentParams = {},
): Record<string, unknown> {
  const latest = messages[messages.length - 1]?.content;
  if (!latest) {
    throw new Error('User message not found.');
  }

  return { input_message: latest, ...customAgentParams };
}

/**
 * Body for OpenAI-compatible chat endpoints.
 *
 * Only `messages` is always sent. Sampling parameters are included solely when
 * the caller supplies them: the previous implementation shipped Swagger
 * placeholders (`model: "string"`, `max_tokens: 0`, `stop: true`) which a
 * lenient backend ignored but a strict one rejects outright.
 */
export function buildChatPayload(
  messages: ChatMessage[],
  customAgentParams: CustomAgentParams = {},
): Record<string, unknown> {
  if (!Array.isArray(messages) || messages.length === 0) {
    throw new Error('User message not found.');
  }

  return { messages, ...customAgentParams };
}

export function buildPayload(
  protocol: BackendProtocol,
  messages: ChatMessage[],
  customAgentParams: CustomAgentParams = {},
): Record<string, unknown> {
  return protocol === 'nat-generate' || protocol === 'nat-generate-stream'
    ? buildGeneratePayload(messages, customAgentParams)
    : buildChatPayload(messages, customAgentParams);
}
