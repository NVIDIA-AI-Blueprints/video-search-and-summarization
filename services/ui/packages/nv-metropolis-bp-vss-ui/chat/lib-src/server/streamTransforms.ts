// SPDX-License-Identifier: MIT
/**
 * Agent response decoding.
 *
 * The proxy turns a backend's response into a plain text stream of assistant
 * content, with intermediate steps interleaved as tagged spans the client
 * renders separately. Backends differ in envelope but agree on the useful part
 * being somewhere in a small set of fields, so extraction is shared.
 */

/** Marker wrapping a serialised intermediate step in the outgoing text stream. */
export const INTERMEDIATE_STEP_OPEN = '<intermediatestep>';
export const INTERMEDIATE_STEP_CLOSE = '</intermediatestep>';

const SSE_DATA_PREFIX = 'data: ';
const INTERMEDIATE_DATA_PREFIX = 'intermediate_data: ';
const SSE_DONE = '[DONE]';

export type StreamOptions = {
  /** When false, intermediate steps are dropped rather than forwarded. */
  enableIntermediateSteps?: boolean;
};

/**
 * Pulls assistant text out of a backend envelope.
 *
 * Ordered most- to least-specific. `delta.content` is checked last so a
 * complete message wins over an incremental fragment when both are present.
 */
export function extractContent(parsed: unknown): string | null {
  if (typeof parsed === 'string') return parsed;
  if (!parsed || typeof parsed !== 'object') return null;

  const envelope = parsed as Record<string, any>;
  const candidate =
    envelope.value ??
    envelope.output ??
    envelope.answer ??
    envelope.choices?.[0]?.message?.content ??
    envelope.choices?.[0]?.delta?.content;

  return typeof candidate === 'string' && candidate.length > 0 ? candidate : null;
}

/** Normalises a raw step payload into the envelope the client renders. */
export function toIntermediateStep(payload: any, index: number): string {
  const step = {
    id: payload?.id || '',
    status: payload?.status || 'in_progress',
    error: payload?.error || '',
    type: 'system_intermediate',
    parent_id: payload?.parent_id || 'default',
    intermediate_parent_id: payload?.intermediate_parent_id || 'default',
    content: {
      name: payload?.name || 'Step',
      payload: payload?.payload || 'No details',
    },
    time_stamp: payload?.time_stamp || 'default',
    index,
  };

  return `${INTERMEDIATE_STEP_OPEN}${JSON.stringify(step)}${INTERMEDIATE_STEP_CLOSE}`;
}

type LineResult = { text: string | null; done: boolean };

/**
 * Interprets one line of the backend stream.
 *
 * Unparseable lines yield no output rather than throwing: a malformed frame
 * mid-answer should not tear down a response the user is already reading.
 */
function handleLine(
  line: string,
  options: StreamOptions,
  nextStepIndex: () => number,
): LineResult {
  if (line.startsWith(SSE_DATA_PREFIX)) {
    const data = line.slice(SSE_DATA_PREFIX.length);
    if (data.trim() === SSE_DONE) return { text: null, done: true };

    try {
      return { text: extractContent(JSON.parse(data)), done: false };
    } catch {
      return { text: null, done: false };
    }
  }

  if (line.startsWith(INTERMEDIATE_DATA_PREFIX)) {
    if (!options.enableIntermediateSteps) return { text: null, done: false };

    try {
      const payload = JSON.parse(line.slice(INTERMEDIATE_DATA_PREFIX.length));
      return { text: toIntermediateStep(payload, nextStepIndex()), done: false };
    } catch {
      return { text: null, done: false };
    }
  }

  // Already-formed step spans are forwarded untouched.
  if (
    options.enableIntermediateSteps &&
    line.includes(INTERMEDIATE_STEP_OPEN) &&
    line.includes(INTERMEDIATE_STEP_CLOSE)
  ) {
    return { text: line, done: false };
  }

  return { text: null, done: false };
}

/**
 * Streams a backend response as assistant text.
 *
 * If the backend never emitted a recognisable frame, the accumulated body is
 * parsed once as a whole — some backends answer a "stream" endpoint with a
 * single JSON document, and dropping that would show the user an empty reply.
 */
export function toContentStream(
  response: Response,
  options: StreamOptions = {},
): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const reader = response.body?.getReader();

  let buffer = '';
  let wholeBody = '';
  let emittedAny = false;
  let stepIndex = 0;
  const nextStepIndex = () => stepIndex++;

  return new ReadableStream<Uint8Array>({
    async start(controller) {
      if (!reader) {
        controller.close();
        return;
      }

      const emit = (text: string) => {
        emittedAny = true;
        controller.enqueue(encoder.encode(text));
      };

      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          buffer += chunk;
          wholeBody += chunk;

          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          for (const line of lines) {
            const { text, done: finished } = handleLine(line, options, nextStepIndex);
            if (text) emit(text);
            if (finished) {
              controller.close();
              return;
            }
          }
        }

        if (buffer.length > 0) {
          const { text } = handleLine(buffer, options, nextStepIndex);
          if (text) emit(text);
        }

        if (!emittedAny) {
          try {
            const content = extractContent(JSON.parse(wholeBody));
            if (content) emit(content.trim());
          } catch {
            // Not JSON either; the caller sees an empty response rather than an error.
          }
        }
      } finally {
        controller.close();
        reader.releaseLock();
      }
    },
  });
}

/** Reads a non-streaming response and returns the assistant text. */
export async function readContent(response: Response): Promise<string> {
  const body = await response.text();

  try {
    const content = extractContent(JSON.parse(body));
    if (content !== null) return content;
    return body;
  } catch {
    return body;
  }
}
