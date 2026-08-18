/**
 * @jest-environment node
 */
import {
  extractContent,
  readContent,
  toContentStream,
  toIntermediateStep,
} from '../../lib-src/server/streamTransforms';

function sseResponse(lines: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const line of lines) controller.enqueue(encoder.encode(`${line}\n`));
      controller.close();
    },
  });
  return new Response(body);
}

async function drain(stream: ReadableStream<Uint8Array>): Promise<string> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let out = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    out += decoder.decode(value, { stream: true });
  }
  return out;
}

describe('extractContent', () => {
  it('reads each backend envelope shape', () => {
    expect(extractContent({ value: 'a' })).toBe('a');
    expect(extractContent({ output: 'b' })).toBe('b');
    expect(extractContent({ answer: 'c' })).toBe('c');
    expect(extractContent({ choices: [{ message: { content: 'd' } }] })).toBe('d');
    expect(extractContent({ choices: [{ delta: { content: 'e' } }] })).toBe('e');
  });

  it('prefers a complete message over an incremental delta', () => {
    expect(
      extractContent({ choices: [{ message: { content: 'full' }, delta: { content: 'frag' } }] }),
    ).toBe('full');
  });

  it('returns null when there is nothing usable', () => {
    expect(extractContent({})).toBeNull();
    expect(extractContent(null)).toBeNull();
    expect(extractContent({ value: '' })).toBeNull();
  });
});

describe('toContentStream', () => {
  it('concatenates streamed deltas', async () => {
    const stream = toContentStream(
      sseResponse([
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        'data: [DONE]',
      ]),
    );
    expect(await drain(stream)).toBe('Hello');
  });

  it('stops at [DONE] and ignores anything after it', async () => {
    const stream = toContentStream(
      sseResponse(['data: {"value":"a"}', 'data: [DONE]', 'data: {"value":"ignored"}']),
    );
    expect(await drain(stream)).toBe('a');
  });

  it('skips malformed frames rather than failing the response', async () => {
    const stream = toContentStream(
      sseResponse(['data: {"value":"a"}', 'data: {not json', 'data: {"value":"b"}']),
    );
    expect(await drain(stream)).toBe('ab');
  });

  it('wraps intermediate steps when enabled', async () => {
    const stream = toContentStream(
      sseResponse(['intermediate_data: {"name":"Search","payload":"q"}', 'data: {"value":"done"}']),
      { enableIntermediateSteps: true },
    );
    const out = await drain(stream);
    expect(out).toContain('<intermediatestep>');
    expect(out).toContain('"name":"Search"');
    expect(out).toContain('done');
  });

  it('drops intermediate steps when disabled', async () => {
    const stream = toContentStream(
      sseResponse(['intermediate_data: {"name":"Search"}', 'data: {"value":"done"}']),
      { enableIntermediateSteps: false },
    );
    expect(await drain(stream)).toBe('done');
  });

  it('indexes successive steps', async () => {
    const stream = toContentStream(
      sseResponse([
        'intermediate_data: {"name":"One"}',
        'intermediate_data: {"name":"Two"}',
      ]),
      { enableIntermediateSteps: true },
    );
    const out = await drain(stream);
    expect(out).toContain('"index":0');
    expect(out).toContain('"index":1');
  });

  it('falls back to parsing the whole body when nothing was streamed', async () => {
    // Some backends answer a "stream" endpoint with one JSON document.
    const stream = toContentStream(new Response('{"value":"whole"}'));
    expect(await drain(stream)).toBe('whole');
  });

  it('emits nothing rather than throwing on an unparseable body', async () => {
    const stream = toContentStream(new Response('not json at all'));
    expect(await drain(stream)).toBe('');
  });

  it('handles a trailing line with no newline', async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"value":"tail"}'));
        controller.close();
      },
    });
    expect(await drain(toContentStream(new Response(body)))).toBe('tail');
  });
});

describe('toIntermediateStep', () => {
  it('defaults every field a renderer depends on', () => {
    const step = JSON.parse(
      toIntermediateStep({}, 7).replace('<intermediatestep>', '').replace('</intermediatestep>', ''),
    );
    expect(step).toMatchObject({
      status: 'in_progress',
      type: 'system_intermediate',
      parent_id: 'default',
      content: { name: 'Step', payload: 'No details' },
      index: 7,
    });
  });
});

describe('readContent', () => {
  it('extracts content from a JSON body', async () => {
    expect(await readContent(new Response('{"output":"hi"}'))).toBe('hi');
  });

  it('returns the raw body when it is not JSON', async () => {
    expect(await readContent(new Response('plain text'))).toBe('plain text');
  });
});