/**
 * @jest-environment node
 */
import {
  buildChatPayload,
  buildGeneratePayload,
  buildPayload,
  inferProtocol,
  isStreamingProtocol,
} from '../../lib-src/server/backendProtocol';

describe('inferProtocol', () => {
  it('recognises the toolkit generate endpoints', () => {
    expect(inferProtocol('http://agent:8000/generate')).toBe('nat-generate');
    expect(inferProtocol('http://agent:8000/generate/stream')).toBe('nat-generate-stream');
  });

  it('treats any other /stream endpoint as streaming OpenAI', () => {
    expect(inferProtocol('http://agent:8000/chat/stream')).toBe('openai-chat-stream');
  });

  it('defaults a bring-your-own OpenAI endpoint to the chat protocol', () => {
    // Previously fell through to a non-streaming branch because the URL
    // contained neither "generate" nor "chat/stream".
    expect(inferProtocol('https://my-agent.example.com/v1/chat/completions')).toBe('openai-chat');
  });

  it('ignores the query string when inferring', () => {
    expect(inferProtocol('http://agent:8000/generate?session=1')).toBe('nat-generate');
  });
});

describe('isStreamingProtocol', () => {
  it('is true only for the streaming variants', () => {
    expect(isStreamingProtocol('openai-chat-stream')).toBe(true);
    expect(isStreamingProtocol('nat-generate-stream')).toBe(true);
    expect(isStreamingProtocol('openai-chat')).toBe(false);
    expect(isStreamingProtocol('nat-generate')).toBe(false);
  });
});

describe('buildGeneratePayload', () => {
  it('sends only the latest user turn', () => {
    expect(
      buildGeneratePayload([
        { role: 'user', content: 'first' },
        { role: 'assistant', content: 'reply' },
        { role: 'user', content: 'second' },
      ]),
    ).toEqual({ input_message: 'second' });
  });

  it('merges custom agent params', () => {
    expect(buildGeneratePayload([{ role: 'user', content: 'hi' }], { use_critic: true })).toEqual({
      input_message: 'hi',
      use_critic: true,
    });
  });

  it('throws when there is no message to send', () => {
    expect(() => buildGeneratePayload([])).toThrow('User message not found.');
  });
});

describe('buildChatPayload', () => {
  const messages = [{ role: 'user', content: 'hi' }];

  it('sends the transcript and nothing else by default', () => {
    // Regression: the previous payload shipped Swagger placeholders
    // (model: "string", max_tokens: 0, stop: true, additionalProp1: {}) which
    // strict OpenAI-compatible servers reject.
    expect(buildChatPayload(messages)).toEqual({ messages });
  });

  it('omits sampling parameters unless supplied', () => {
    const payload = buildChatPayload(messages);
    expect(payload).not.toHaveProperty('model');
    expect(payload).not.toHaveProperty('max_tokens');
    expect(payload).not.toHaveProperty('top_p');
    expect(payload).not.toHaveProperty('stop');
  });

  it('includes caller-supplied parameters', () => {
    expect(buildChatPayload(messages, { model: 'llama-3.1-70b', temperature: 0.2 })).toEqual({
      messages,
      model: 'llama-3.1-70b',
      temperature: 0.2,
    });
  });

  it('throws when there are no messages', () => {
    expect(() => buildChatPayload([])).toThrow('User message not found.');
  });
});

describe('buildPayload', () => {
  const messages = [{ role: 'user', content: 'hi' }];

  it('routes each protocol to its payload shape', () => {
    expect(buildPayload('nat-generate', messages)).toEqual({ input_message: 'hi' });
    expect(buildPayload('nat-generate-stream', messages)).toEqual({ input_message: 'hi' });
    expect(buildPayload('openai-chat', messages)).toEqual({ messages });
    expect(buildPayload('openai-chat-stream', messages)).toEqual({ messages });
  });
});