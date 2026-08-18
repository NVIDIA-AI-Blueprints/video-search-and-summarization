// SPDX-License-Identifier: MIT
/**
 * Edge-runtime chat proxy.
 *
 * Kept separate from `server.ts` so nothing in this module's graph reaches a
 * Node built-in: the route runs on the edge runtime, where importing
 * next-i18next (and therefore `node:fs`) fails the build.
 */
import {
  buildPayload,
  inferProtocol,
  isStreamingProtocol,
  type BackendProtocol,
  type ChatMessage,
} from './server/backendProtocol';
import { readContent, toContentStream } from './server/streamTransforms';

export {
  buildChatPayload,
  buildGeneratePayload,
  buildPayload,
  inferProtocol,
  isStreamingProtocol,
} from './server/backendProtocol';
export type {
  BackendProtocol,
  ChatMessage,
  CustomAgentParams,
} from './server/backendProtocol';
export {
  extractContent,
  readContent,
  toContentStream,
  toIntermediateStep,
} from './server/streamTransforms';

type ChatRequestBody = {
  /** Absolute URL of the agent endpoint. */
  chatCompletionURL?: string;
  /** Explicit protocol; inferred from the URL when omitted. */
  protocol?: BackendProtocol;
  messages?: ChatMessage[];
  additionalProps?: { enableIntermediateSteps?: boolean };
  /** Any remaining keys are forwarded as agent-specific parameters. */
  [key: string]: unknown;
};

/**
 * Proxies a chat turn to the configured agent backend.
 *
 * Runs server-side so the endpoint and its credentials are never exposed to the
 * browser. The response is plain text — assistant content, with intermediate
 * steps interleaved as tagged spans.
 */
export const chatApiHandler = async (req: Request): Promise<Response> => {
  let body: ChatRequestBody;
  try {
    body = (await req.json()) as ChatRequestBody;
  } catch {
    return new Response('Invalid request body.', { status: 400 });
  }

  const {
    chatCompletionURL = '',
    protocol,
    messages = [],
    additionalProps = { enableIntermediateSteps: true },
    ...customAgentParams
  } = body;

  if (!chatCompletionURL) {
    return new Response('Agent endpoint is not configured.', { status: 400 });
  }

  const resolved: BackendProtocol = protocol ?? inferProtocol(chatCompletionURL);

  let payload: Record<string, unknown>;
  try {
    payload = buildPayload(resolved, messages, customAgentParams as Record<string, unknown>);
  } catch (error) {
    return new Response(
      error instanceof Error ? error.message : 'Invalid request.',
      { status: 400 },
    );
  }

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Conversation-Id': req.headers.get('Conversation-Id') || '',
    'User-Message-ID': req.headers.get('User-Message-ID') || '',
    'X-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone || 'Etc/UTC',
  };

  // Forwarded so a bring-your-own agent behind an API key is reachable. Read
  // from the incoming request rather than the body so it is never persisted
  // into conversation history.
  const authorization = req.headers.get('Authorization');
  if (authorization) headers.Authorization = authorization;

  let response: Response;
  try {
    response = await fetch(chatCompletionURL, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
    });
  } catch (error) {
    return new Response(
      `Could not reach the agent backend: ${
        error instanceof Error ? error.message : 'unknown error'
      }`,
      { status: 502 },
    );
  }

  if (!response.ok) {
    const detail = await response.text();
    return new Response(`Error: ${detail}`, { status: 500 });
  }

  return isStreamingProtocol(resolved)
    ? new Response(toContentStream(response, additionalProps))
    : new Response(await readContent(response));
};
